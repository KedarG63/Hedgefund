"""
Cross-asset correlation: equity index proxy vs commodities (daily) and vs
RBI macro series (monthly-resampled). Sibling to analytics/correlation.py's
rolling_correlation() -- same persist/as_of conventions, different asset
classes, one function per frequency regime (see the note below on why they
are NOT the same function).

METHODOLOGY: RBI macro series update monthly-or-slower (policy repo rate
changes only at MPC meetings, ~6x/year; forex reserves weekly). Correlating
their raw daily change against equity daily returns is dominated by zero-
change days and produces an unstable, near-meaningless coefficient. So
equity_macro_correlation() resamples equities to MONTH-END closes first.
Commodity legs (IBJA/MCX/CME) are all genuinely daily-liquid, so
equity_commodity_correlation() stays at daily frequency in its OWN function
and OWN table -- mixing the two frequency regimes into one function/table
would force one of them into the wrong cadence.

HISTORY WARNING (as of this warehouse today): mcx_bhavcopy and
cme_settlements each have exactly 1 day collected, ibja_rates has 8, and
rbi_key_indicators' USDINR series has 1. Both functions below evaluate each
candidate series INDEPENDENTLY and skip (not fail) any series lacking
min_obs/min_months -- only raising if NONE of the candidate series have
enough history, same "accumulating, not broken" contract as
analytics/capm.py's MIN_OBS guard. Expect equity_macro_correlation() in
particular to raise for weeks after this ships; that is expected on a young
pipeline, not a bug.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.returns import log_returns
from connectors.commodities import usdinr_reference_rate
from core.asof import asof_sql
from core.storage import db, register_views, write_table

EQUITY_PROXY = "NIFTYBEES"              # same choice as analytics/capm.py, documented there
DEFAULT_WINDOW_DAYS = 60                 # mirrors analytics/correlation.py's DEFAULT_WINDOW_DAYS

# Hand-set Phase-3 floors given today's thin MCX/CME/IBJA/RBI history -- NOT
# backtested, same provisional character as analytics/universe.py's
# MIN_TURNOVER_FLOOR. Revisit once these connectors have real daily/weekly
# runs behind them.
COMMODITY_MIN_OBS = 10
MACRO_MIN_MONTHS = 6


def _niftybees_daily(as_of: str | None = None) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        where_date = f"AND TradDt <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT TradDt AS trade_date, any_value(ClsPric) AS close
            FROM nse_bhavcopy
            WHERE TckrSymb = '{EQUITY_PROXY}' {where_date}
            GROUP BY TradDt
            ORDER BY trade_date
        """).df()
    finally:
        con.close()


def _niftybees_daily_returns(as_of: str | None = None) -> pd.DataFrame:
    df = _niftybees_daily(as_of)
    df["symbol"] = EQUITY_PROXY
    df = log_returns(df, price_col="close", symbol_col="symbol", date_col="trade_date")
    return df[["trade_date", "log_return"]].dropna()


def _log_return_series(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """
    (trade_date, value_col) price-level frame -> (trade_date, commodity_value)
    log-return frame. Renamed away from "log_return" deliberately: the
    caller merges this against the equity return frame, which already owns
    a column called log_return -- an unrenamed collision would make pandas
    auto-suffix BOTH sides on merge (same class of bug analytics/
    correlation.py's sector_correlation() had before its fix).
    """
    d = df.rename(columns={value_col: "close"}).copy()
    d["symbol"] = "_commodity_leg"
    d = log_returns(d, price_col="close", symbol_col="symbol", date_col="trade_date")
    return (d[["trade_date", "log_return"]]
            .rename(columns={"log_return": "commodity_value"})
            .dropna())


def _ibja_gold_999_daily(as_of: str | None = None) -> pd.DataFrame:
    """PM session only -- IBJA's own site calls PM the closing rate (AM is opening)."""
    con = db(read_only=True)
    try:
        register_views(con)
        where_date = f"AND rate_date <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT rate_date AS trade_date, rate_inr_per_10g AS close
            FROM ibja_rates
            WHERE metal_purity = '999' AND metal = 'gold' AND session = 'PM' {where_date}
            ORDER BY trade_date
        """).df()
    finally:
        con.close()


def _mcx_gold_front_daily(as_of: str | None = None) -> pd.DataFrame:
    """Front month by nearest unexpired ExpiryDate per trade_date -- never by price (see
    connectors/commodities.py's cme_settlements() docstring on why price-based inference
    is wrong the moment a curve is in backwardation)."""
    con = db(read_only=True)
    try:
        register_views(con)
        where_date = f"AND trade_date <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT trade_date, Close AS close
            FROM mcx_bhavcopy
            WHERE Symbol = 'GOLD' AND InstrumentName = 'FUTCOM' {where_date}
            QUALIFY row_number() OVER (
                PARTITION BY trade_date ORDER BY strptime(ExpiryDate, '%d%b%Y') ASC
            ) = 1
            ORDER BY trade_date
        """).df()
    finally:
        con.close()


def _cme_gold_front_daily(as_of: str | None = None) -> pd.DataFrame:
    """Front month by contract_order = 0, same convention as connectors/commodities.py."""
    con = db(read_only=True)
    try:
        register_views(con)
        where_date = f"AND trade_date_requested <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT trade_date_requested AS trade_date, settle_num AS close
            FROM cme_settlements
            WHERE product = 'gold' AND NOT is_total AND contract_order = 0
              AND settle_num IS NOT NULL {where_date}
            ORDER BY trade_date
        """).df()
    finally:
        con.close()


def _gold_premium_pct_daily(as_of: str | None = None) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        where_date = f"AND rate_date <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT rate_date AS trade_date, avg(premium_pct) AS premium_pct
            FROM derived_india_gold_premium
            WHERE 1=1 {where_date}
            GROUP BY rate_date ORDER BY trade_date
        """).df()
    finally:
        con.close()


# name -> (fetch_fn, value_col, transform). "log_return" for price levels;
# "diff" for india_gold_premium_pct, which is already a spread (percentage
# points) -- taking a log return of a spread that can sit near/below zero is
# not meaningful, so this uses the raw day-over-day point change instead.
_COMMODITY_LEGS = {
    "ibja_gold_999":         (_ibja_gold_999_daily, "close", "log_return"),
    "mcx_gold_front":        (_mcx_gold_front_daily, "close", "log_return"),
    "cme_gold_front":        (_cme_gold_front_daily, "close", "log_return"),
    "india_gold_premium_pct": (_gold_premium_pct_daily, "premium_pct", "diff"),
}


def equity_commodity_correlation(window_days: int = DEFAULT_WINDOW_DAYS,
                                  min_obs: int = COMMODITY_MIN_OBS,
                                  as_of: str | None = None,
                                  persist: bool = True) -> pd.DataFrame:
    """
    Daily-return Pearson correlation between NIFTYBEES and each available
    daily commodity series. Long format (asset_a=NIFTYBEES, asset_b,
    correlation, n_obs, window_days, as_of_date) -> derived/equity_commodity_correlation.

    Each commodity leg is evaluated independently and SKIPPED (not fatal) if
    it has fewer than min_obs overlapping observations with the equity
    return series -- raises only if every leg is skipped.
    """
    equity = _niftybees_daily_returns(as_of).tail(window_days)
    if len(equity) < min_obs:
        raise RuntimeError(
            f"equity_commodity_correlation: only {len(equity)} {EQUITY_PROXY} return "
            f"observation(s) in the window; need at least {min_obs}. Needs daily "
            "nse_bhavcopy history to accumulate -- expected on a freshly started "
            "pipeline, not a bug."
        )

    rows = []
    for name, (fetch_fn, value_col, transform) in _COMMODITY_LEGS.items():
        raw = fetch_fn(as_of)
        if raw.empty:
            continue
        if transform == "log_return":
            series = _log_return_series(raw, value_col)
        else:
            series = raw[["trade_date", value_col]].copy()
            series["commodity_value"] = series[value_col].diff()
            series = series[["trade_date", "commodity_value"]].dropna()
        series = series.tail(window_days)

        merged = equity.merge(series, on="trade_date", how="inner").dropna()
        if len(merged) < min_obs:
            continue
        corr = merged["log_return"].corr(merged["commodity_value"])
        if pd.isna(corr):
            continue
        rows.append({
            "asset_a": EQUITY_PROXY, "asset_b": name, "correlation": corr,
            "n_obs": len(merged), "window_days": window_days,
            "as_of_date": merged["trade_date"].max(),
        })

    if not rows:
        raise RuntimeError(
            "equity_commodity_correlation: no commodity series had enough overlapping "
            f"history with {EQUITY_PROXY} ({min_obs}+ obs) -- expected while "
            "mcx_bhavcopy/cme_settlements/ibja_rates are still accumulating daily "
            "history, not a bug."
        )
    df = pd.DataFrame(rows)
    if persist:
        write_table(df, "derived", "equity_commodity_correlation")
    return df


def _monthly_last(df: pd.DataFrame, date_col: str, value_col: str) -> pd.Series:
    s = df.copy()
    s[date_col] = pd.to_datetime(s[date_col])
    s = s.set_index(date_col)[value_col].sort_index()
    return s.resample("ME").last().dropna()


def _niftybees_monthly_return(as_of: str | None = None) -> pd.Series:
    monthly = _monthly_last(_niftybees_daily(as_of), "trade_date", "close")
    return (np.log(monthly / monthly.shift(1))).dropna()


def _policy_repo_rate_monthly_change_bps(as_of: str | None = None) -> pd.Series:
    con = db(read_only=True)
    try:
        register_views(con)
        # observed_at is a full ISO timestamp ("2026-08-18T21:50:18"), not a
        # bare date -- a plain `<= '{as_of}'` lexicographic compare would
        # exclude same-day observations (the timestamp sorts AFTER a
        # date-only string), so the cutoff is padded to end-of-day.
        where_date = f"AND observed_at <= '{as_of}T23:59:59'" if as_of else ""
        df = con.execute(f"""
            SELECT observed_at, rate_pct
            FROM rbi_policy_rates
            WHERE rate_name = 'Policy Repo Rate' {where_date}
            QUALIFY row_number() OVER (
                PARTITION BY observed_at ORDER BY knowledge_date DESC
            ) = 1
            ORDER BY observed_at
        """).df()
    finally:
        con.close()
    if df.empty:
        return pd.Series(dtype=float)
    monthly = _monthly_last(df, "observed_at", "rate_pct")
    return (monthly.diff() * 100).dropna()  # percentage points -> bps


def _forex_reserves_monthly_pct_change(as_of: str | None = None) -> pd.Series:
    con = db(read_only=True)
    try:
        register_views(con)
        # TWO DIFFERENT SENSES OF "as of", and they are not interchangeable.
        # This module's `as_of` bounds the OBSERVATION date (week_ended) -- it
        # is the lookback window the correlation is computed over. core.asof's
        # bounds the KNOWLEDGE date -- what we had actually collected by then.
        # Only the first is wanted here, so it stays a WHERE clause and no
        # knowledge-date bound is passed; asof_sql() is used purely for the
        # vintage collapse, replacing the per-column arg_max.
        where = "component_code = 'TR'" + (f" AND week_ended <= '{as_of}'" if as_of else "")
        df = con.execute(asof_sql(
            "rbi_forex_reserves", columns="week_ended, amount_usd",
            where=where, order_by="week_ended",
        )).df()
    finally:
        con.close()
    if df.empty:
        return pd.Series(dtype=float)
    monthly = _monthly_last(df, "week_ended", "amount_usd")
    return monthly.pct_change().dropna()


def _usdinr_monthly_pct_change(as_of: str | None = None) -> pd.Series:
    fx = usdinr_reference_rate()
    if as_of:
        fx = fx[fx["rate_date"] <= as_of]
    if fx.empty:
        return pd.Series(dtype=float)
    monthly = _monthly_last(fx, "rate_date", "usdinr")
    return monthly.pct_change().dropna()


_MACRO_LEGS = {
    "policy_repo_rate_change_bps": _policy_repo_rate_monthly_change_bps,
    "forex_reserves_pct_change": _forex_reserves_monthly_pct_change,
    "usdinr_pct_change": _usdinr_monthly_pct_change,
}


def equity_macro_correlation(min_months: int = MACRO_MIN_MONTHS,
                              as_of: str | None = None,
                              persist: bool = True) -> pd.DataFrame:
    """
    Month-end resampled correlation between NIFTYBEES' monthly log return and
    each available RBI macro series' monthly change. Long format
    (equity_symbol=NIFTYBEES, macro_series, correlation, n_obs, as_of_date)
    -> derived/equity_macro_correlation.

    Each macro leg is evaluated independently and SKIPPED (not fatal) if it
    has fewer than min_months overlapping monthly observations -- raises
    only if every leg is skipped. Expect this on a warehouse only weeks old:
    the policy repo rate changes ~6x/year and this warehouse currently holds
    very few observed_at snapshots.
    """
    equity_monthly = _niftybees_monthly_return(as_of)
    if len(equity_monthly) < min_months:
        raise RuntimeError(
            f"equity_macro_correlation: only {len(equity_monthly)} monthly "
            f"{EQUITY_PROXY} return observation(s); need at least {min_months}. "
            "Needs many months of daily nse_bhavcopy history to accumulate -- "
            "expected on a freshly started pipeline, not a bug."
        )

    rows = []
    for name, fetch_fn in _MACRO_LEGS.items():
        macro_series = fetch_fn(as_of)
        if macro_series.empty:
            continue
        merged = pd.DataFrame({"equity": equity_monthly, "macro": macro_series}).dropna()
        if len(merged) < min_months:
            continue
        corr = merged["equity"].corr(merged["macro"])
        if pd.isna(corr):
            continue
        rows.append({
            "equity_symbol": EQUITY_PROXY, "macro_series": name, "correlation": corr,
            "n_obs": len(merged), "as_of_date": merged.index.max().date().isoformat(),
        })

    if not rows:
        raise RuntimeError(
            "equity_macro_correlation: no macro series had enough overlapping monthly "
            f"history with {EQUITY_PROXY} ({min_months}+ months) -- expected on a young "
            "pipeline (RBI policy rate/forex reserves/USDINR history accumulates slowly), "
            "not a bug."
        )
    df = pd.DataFrame(rows)
    if persist:
        write_table(df, "derived", "equity_macro_correlation")
    return df
