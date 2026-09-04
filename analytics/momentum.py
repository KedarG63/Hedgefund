"""
Cross-sectional momentum z-score, and the Lo-MacKinlay variance-ratio test as
an EMH-adjacent diagnostic: VR > 1 implies trending/momentum, VR < 1 implies
mean-reversion, VR ~= 1 is consistent with a random walk. Implemented from
the closed-form asymptotic statistic directly -- nothing in the current
dependency set needs an external package for this.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.returns import log_returns
from analytics.universe import equity_universe
from core.storage import db, register_views, write_table

DEFAULT_LOOKBACK_DAYS = 20
DEFAULT_VR_WINDOW = 60
DEFAULT_VR_LAG = 5
MIN_OBS = 20


def _price_panel(symbols: list[str], as_of: str | None = None) -> pd.DataFrame:
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


def momentum_zscore(lookback_days: int = DEFAULT_LOOKBACK_DAYS, as_of: str | None = None,
                    min_turnover: float | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Trailing `lookback_days` simple return per symbol, z-scored against the
    cross-section of the universe on the same as_of date:
        z_i = (ret_i - mean(ret_universe)) / std(ret_universe)

    min_turnover, passed through to equity_universe(), is an opt-in liquidity
    floor -- default None keeps today's behavior (illiquid names included).
    """
    universe = equity_universe(as_of=as_of, min_turnover=min_turnover)
    symbols = universe["symbol"].tolist()
    hist = _price_panel(symbols, as_of=as_of)
    panel = hist.pivot(index="trade_date", columns="symbol", values="close")
    if len(panel) <= lookback_days:
        raise RuntimeError(
            f"momentum_zscore: only {len(panel)} trading day(s) of bhavcopy history "
            f"available; need at least {lookback_days + 1}. This accumulates as "
            "nse_bhavcopy runs daily -- expected on a freshly started warehouse, not a bug."
        )
    trail_ret = panel.iloc[-1] / panel.iloc[-1 - lookback_days] - 1.0
    trail_ret = trail_ret.dropna()
    if len(trail_ret) < 2:
        raise RuntimeError("momentum_zscore: fewer than 2 symbols have a full lookback window.")

    mean_, std_ = trail_ret.mean(), trail_ret.std()
    z = (trail_ret - mean_) / std_ if std_ else trail_ret * 0.0

    df = pd.DataFrame({
        "symbol": trail_ret.index, "trailing_return": trail_ret.values,
        "momentum_zscore": z.values, "as_of_date": panel.index.max(),
        "lookback_days": lookback_days,
    })
    if persist:
        write_table(df, "derived", "momentum_zscore")
    return df


def variance_ratio_test(lag: int = DEFAULT_VR_LAG, window_days: int = DEFAULT_VR_WINDOW,
                        as_of: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Lo-MacKinlay variance ratio per symbol:
        VR(lag) = Var(r_t summed over `lag` days) / (lag * Var(r_t over 1 day))
    computed on log returns over the trailing `window_days`. Also reports the
    z-statistic against the homoskedastic random-walk null (VR=1) using the
    closed-form asymptotic standard error -- no external stats package needed.
    """
    universe = equity_universe(as_of=as_of)
    symbols = universe["symbol"].tolist()
    hist = _price_panel(symbols, as_of=as_of)
    hist = log_returns(hist, price_col="close", symbol_col="symbol", date_col="trade_date")

    rows = []
    for symbol, grp in hist.groupby("symbol"):
        r = grp["log_return"].dropna().tail(window_days)
        n = len(r)
        if n < max(MIN_OBS, lag * 4):
            continue
        var_1 = r.var(ddof=1)
        if not var_1:
            continue
        var_lag = r.rolling(lag).sum().dropna().var(ddof=1)
        vr = var_lag / (lag * var_1)
        # Asymptotic std error of VR under the homoskedastic random-walk null.
        se = np.sqrt(2 * (2 * lag - 1) * (lag - 1) / (3 * lag * n))
        z_stat = (vr - 1) / se if se else np.nan
        rows.append({
            "symbol": symbol, "as_of_date": grp["trade_date"].max(),
            "variance_ratio": vr, "z_stat": z_stat, "lag": lag, "n_obs": n,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(
            f"variance_ratio_test: no symbol had {max(MIN_OBS, lag * 4)}+ return "
            "observations in the trailing window yet."
        )
    if persist:
        write_table(df, "derived", "variance_ratio")
    return df
