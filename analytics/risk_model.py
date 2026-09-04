"""
Multi-factor cross-sectional risk model over the NSE equity universe --
momentum, size, and low-volatility. NOT a portfolio/positions model: this
repo tracks no actual holdings, so "risk model" here means factor EXPOSURE
per stock -- the input a portfolio-level model would need once positions
exist, not a portfolio VaR/attribution engine itself.

WHY ONLY THREE FACTORS
    A real multi-factor model (Fama-French, Barra-style) wants value and
    quality too -- book value, earnings, debt/equity. None of that is parsed
    anywhere in this warehouse yet: nse_results_Quarterly only INDEXES
    quarterly filings and links to their XBRL documents, it does not parse
    them (unlike sec_companyfacts.py on the US side, which parses every XBRL
    fact from one bulk download). Parsing India's XBRL taxonomy is a
    separate, connector-tier project, not built here. Value and quality are
    deliberately absent rather than faked from a proxy.

FACTOR SOURCES
    momentum   analytics/momentum.py::momentum_zscore(), reused as-is from
               its persisted table -- not recomputed here
    size       bse_scrip_master.Mktcap, joined to the NSE universe by ISIN
               (verified live: 2,377 of 2,629 NSE EQ-series ISINs matched a
               BSE market-cap row -- ~90% coverage, not 100%; an unmatched
               symbol gets a null size_score, not a zero, so it is NOT
               silently scored as "average size")
    low_vol    realized volatility (analytics/returns.py::realized_vol) over
               nse_bhavcopy close prices, z-scored and sign-flipped so a
               CALMER stock gets a HIGHER score, matching the low-volatility-
               anomaly convention

SIGN CONVENTIONS -- read before using composite_score
    Every factor is oriented so higher = more attractive by that factor's own
    academic convention: momentum_zscore is unchanged (already high=strong
    trailing return); size_score = -zscore(log market cap) (SMB-style, long
    small caps); low_vol_score = -zscore(realized_vol) (low-vol anomaly,
    long calm stocks). composite_score is an equal-weighted, hand-set,
    NOT-BACKTESTED sum of the three (COMPOSITE_WEIGHTS), clipped to [-1, 1]
    -- same documented-not-validated posture as digest.py's COMPOSITE_WEIGHTS
    and outliers.py's CONTAMINATION. A symbol missing a factor contributes
    0 (neutral, not a penalty) to the composite for that factor only -- the
    same fillna(0.0) treatment digest.py's _composite_score uses for a
    missing event_zscore.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.returns import log_returns, realized_vol
from analytics.universe import equity_universe
from core.asof import asof_sql, latest_per
from core.storage import db, register_views, write_table

VOL_WINDOW_DAYS = 20
MIN_SYMBOLS = 30

# Hand-set, documented Phase 1 starting point -- NOT backtested or tuned.
# Revisit once enough history has accumulated to validate these weights.
COMPOSITE_WEIGHTS = {
    "momentum_zscore": 0.4,
    "size_score": 0.3,
    "low_vol_score": 0.3,
}


def _price_panel(symbols: list[str], as_of: str | None = None) -> pd.DataFrame:
    # Same query as capm.py's _price_history / momentum.py's _price_panel --
    # duplicated deliberately, matching how those two already duplicate it
    # rather than share a helper.
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


def _market_cap(isins: list[str]) -> pd.DataFrame:
    """
    Latest BSE Mktcap (Rs Crore) per ISIN. bse_scrip_master accumulates a new
    parquet vintage every weekly_monday run (write_table never overwrites),
    so this collapses to the latest snapshot per ISIN the same way
    outliers.py/digest.py collapse derived_* tables to their latest vintage.
    Mktcap ships as a string in the source file (some rows are '' -- likely
    unrated/inactive ISINs) and is coerced with errors="coerce" in pandas,
    not cast inside the SQL -- casting '' to a number there raises.
    """
    con = db(read_only=True)
    try:
        register_views(con)
        placeholders = ", ".join(f"'{i}'" for i in isins)
        # ISIN_NUMBER is bse_scrip_master's declared key, so the point-in-time
        # read is already one row per ISIN.
        df = con.execute(asof_sql(
            "bse_scrip_master",
            columns="ISIN_NUMBER AS isin, Mktcap AS market_cap_inr_cr",
            where=f"ISIN_NUMBER IN ({placeholders})",
        )).df()
    finally:
        con.close()
    df["market_cap_inr_cr"] = pd.to_numeric(df["market_cap_inr_cr"], errors="coerce")
    return df


def size_factor(as_of: str | None = None, min_turnover: float | None = None,
                persist: bool = True) -> pd.DataFrame:
    """
    Cross-sectional size score: -zscore(log market cap), so SMALLER
    companies score HIGHER -- the SMB convention (long small, short big).

    Coverage is NOT universal (~90% of NSE EQ-series ISINs matched a BSE
    market-cap row in verification). An unmatched symbol keeps its row with
    market_cap_inr_cr/size_score as null -- excluded from the z-score fit and
    left for the caller to handle explicitly, not defaulted to "average".
    """
    universe = equity_universe(as_of=as_of, min_turnover=min_turnover)
    mcap = _market_cap(universe["isin"].dropna().unique().tolist())
    df = universe[["symbol", "isin", "trade_date"]].merge(mcap, on="isin", how="left") \
        .rename(columns={"trade_date": "as_of_date"})

    matched = df["market_cap_inr_cr"].notna() & (df["market_cap_inr_cr"] > 0)
    if matched.sum() < MIN_SYMBOLS:
        raise RuntimeError(
            f"size_factor: only {int(matched.sum())} symbol(s) matched a BSE market-cap "
            f"row; need at least {MIN_SYMBOLS}. Check bse_scrip_master has a recent vintage."
        )

    log_mcap = np.log(df.loc[matched, "market_cap_inr_cr"])
    std_ = log_mcap.std()
    z = (log_mcap - log_mcap.mean()) / std_ if std_ else log_mcap * 0.0
    df["size_score"] = np.nan
    df.loc[matched, "size_score"] = -z

    if persist:
        write_table(df, "derived", "size_factor")
    return df


def low_vol_factor(window_days: int = VOL_WINDOW_DAYS, as_of: str | None = None,
                   min_turnover: float | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Cross-sectional low-volatility score: -zscore(realized_vol) over the
    trailing `window_days`, so CALMER stocks score HIGHER -- the
    low-volatility-anomaly convention.
    """
    universe = equity_universe(as_of=as_of, min_turnover=min_turnover)
    hist = _price_panel(universe["symbol"].tolist(), as_of=as_of)
    hist = log_returns(hist, price_col="close", symbol_col="symbol", date_col="trade_date")

    rows = []
    for symbol, grp in hist.groupby("symbol"):
        vol = realized_vol(grp["log_return"], window=window_days).dropna()
        if vol.empty:
            continue
        rows.append({"symbol": symbol, "as_of_date": grp["trade_date"].max(),
                     "realized_vol": vol.iloc[-1]})

    df = pd.DataFrame(rows)
    if len(df) < MIN_SYMBOLS:
        raise RuntimeError(
            f"low_vol_factor: only {len(df)} symbol(s) have a full {window_days}-day "
            f"volatility window; need at least {MIN_SYMBOLS}."
        )

    std_ = df["realized_vol"].std()
    z = (df["realized_vol"] - df["realized_vol"].mean()) / std_ if std_ else df["realized_vol"] * 0.0
    df["low_vol_score"] = -z

    if persist:
        write_table(df, "derived", "low_vol_factor")
    return df


def _registered_views(con) -> set[str]:
    return set(con.execute("SELECT table_name FROM information_schema.tables").df()["table_name"])


def build_factor_model(persist: bool = True) -> pd.DataFrame:
    """
    Joins momentum + size + low-vol into one cross-sectional table with a
    hand-set composite_score (COMPOSITE_WEIGHTS) -- see the module docstring
    for the sign conventions and the fillna(0.0)-is-neutral composite rule.

    Reads momentum from the already-persisted derived_momentum_zscore table
    (run analytics_momentum first) rather than recomputing it; size and
    low_vol are computed fresh each call.
    """
    con = db(read_only=True)
    try:
        register_views(con)
        if "derived_momentum_zscore" not in _registered_views(con):
            raise RuntimeError(
                "build_factor_model needs derived_momentum_zscore first -- "
                "run the analytics_momentum job before analytics_risk_model."
            )
        momentum = con.execute(latest_per(
            "derived_momentum_zscore", ["symbol"],
            columns="symbol, momentum_zscore, as_of_date",
        )).df()
    finally:
        con.close()

    size = size_factor(persist=persist)[["symbol", "size_score"]]
    low_vol = low_vol_factor(persist=persist)[["symbol", "low_vol_score"]]
    df = momentum.merge(size, on="symbol", how="left").merge(low_vol, on="symbol", how="left")

    momentum_z = df["momentum_zscore"].fillna(0.0)
    size_z = df["size_score"].fillna(0.0)
    low_vol_z = df["low_vol_score"].fillna(0.0)
    raw = (COMPOSITE_WEIGHTS["momentum_zscore"] * momentum_z
           + COMPOSITE_WEIGHTS["size_score"] * size_z
           + COMPOSITE_WEIGHTS["low_vol_score"] * low_vol_z)
    df["composite_score"] = raw.clip(-1.0, 1.0)

    out = df.sort_values("composite_score", ascending=False, ignore_index=True)
    if persist:
        write_table(out, "derived", "factor_model")
    return out
