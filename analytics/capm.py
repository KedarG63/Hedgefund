"""
CAPM beta/alpha: OLS of each stock's daily log return against NIFTYBEES'
daily log return (the market-return proxy), trailing window.

NIFTY 50 the INDEX has no ClsPric row in nse_bhavcopy -- it isn't a listed
security, it's an index. NIFTYBEES (Nippon India ETF Nifty BeES) IS a real,
liquid, already-collected row every trading day and tracks the index closely
enough for a beta estimate; its tracking error (typically a few bps/day) is
immaterial here -- this is a documented simplification, not an oversight.
Fall back to JUNIORBEES/BANKBEES only if a sector-specific beta is wanted
later; not built in Phase 1.
"""
from __future__ import annotations

import pandas as pd
from scipy import stats

from analytics.returns import log_returns
from analytics.universe import equity_universe
from core.storage import db, register_views, write_table

MARKET_PROXY_SYMBOL = "NIFTYBEES"
DEFAULT_WINDOW_DAYS = 252
# Fewer observations than this and a beta estimate is closer to noise than
# signal. Also the guard that turns "warehouse doesn't have enough bhavcopy
# history yet" into a loud, clear failure instead of a garbage regression.
MIN_OBS = 20


def _price_history(symbols: list[str], as_of: str | None = None) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        placeholders = ", ".join(f"'{s}'" for s in symbols)
        where_date = f"AND TradDt <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT TckrSymb AS symbol, TradDt AS trade_date, any_value(ClsPric) AS close
            FROM nse_bhavcopy
            WHERE TckrSymb IN ({placeholders}) {where_date}
            GROUP BY TckrSymb, TradDt
            ORDER BY symbol, trade_date
        """).df()
    finally:
        con.close()


def compute_betas(window_days: int = DEFAULT_WINDOW_DAYS, min_obs: int = MIN_OBS,
                  as_of: str | None = None, min_turnover: float | None = None,
                  persist: bool = True) -> pd.DataFrame:
    """
    OLS beta/alpha per symbol vs NIFTYBEES, trailing `window_days`
    observations (as many as are actually available, down to `min_obs`).

    Raises if NIFTYBEES itself has fewer than `min_obs` return observations
    -- that means the warehouse doesn't have enough bhavcopy history yet for
    ANY beta in this run (expected on a freshly started pipeline, not a
    per-symbol data gap), and returning an empty frame would hide that
    rather than surface it, which the connector contract explicitly forbids.

    min_turnover, passed through to equity_universe(), is an opt-in liquidity
    floor -- default None keeps today's behavior (illiquid names included).
    """
    universe = equity_universe(as_of=as_of, min_turnover=min_turnover)
    symbols = universe["symbol"].tolist()
    if MARKET_PROXY_SYMBOL not in symbols:
        raise RuntimeError(
            f"{MARKET_PROXY_SYMBOL} not found in the current equity universe -- "
            "cannot compute CAPM beta without the market proxy."
        )

    hist = _price_history(symbols, as_of=as_of)
    hist = log_returns(hist, price_col="close", symbol_col="symbol", date_col="trade_date")

    market = hist[hist["symbol"] == MARKET_PROXY_SYMBOL][["trade_date", "log_return"]] \
        .rename(columns={"log_return": "market_return"}).dropna()
    if len(market) < min_obs:
        raise RuntimeError(
            f"Only {len(market)} {MARKET_PROXY_SYMBOL} return observation(s) in the "
            f"warehouse; need at least {min_obs}. Beta needs daily nse_bhavcopy history "
            "to accumulate -- this is expected on a freshly started pipeline, not a bug."
        )
    market_tail = market.tail(window_days)

    rows = []
    for symbol, grp in hist.groupby("symbol"):
        if symbol == MARKET_PROXY_SYMBOL:
            continue
        merged = grp[["trade_date", "log_return"]].dropna().merge(
            market_tail, on="trade_date", how="inner")
        if len(merged) < min_obs:
            continue
        slope, intercept, r, _, _ = stats.linregress(merged["market_return"], merged["log_return"])
        rows.append({
            "symbol": symbol, "as_of_date": merged["trade_date"].max(),
            "beta": slope, "alpha": intercept, "r_squared": r ** 2,
            "n_obs": len(merged), "market_proxy": MARKET_PROXY_SYMBOL,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(
            "compute_betas: no symbol had enough overlapping history with "
            f"{MARKET_PROXY_SYMBOL} ({min_obs}+ obs) to fit a regression."
        )
    if persist:
        write_table(df, "derived", "capm_beta")
    return df
