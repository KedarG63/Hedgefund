"""
Cross-source FII positioning reconciliation: NSE's participant-OI disclosure
vs Upstox's independently-polled FII activity feed, for the same date and
F&O segment.

HONEST FRAMING -- read before trusting this as an "independent second
opinion": verified live against 2026-08-21 data, every comparable metric
matched NSE and Upstox almost exactly (e.g. index-futures FII long/short:
26,060 / 235,915 from BOTH paths). Upstox's FII segment numbers appear to
mirror NSE's own participant-OI disclosure rather than being derived from
an unrelated methodology (run_daily.py's upstox_fii job docstring already
notes this cross-validation). So a divergence here is primarily a
RECONCILIATION/STALENESS signal -- one source lagging the other, a fetch
gap, a genuine transcription error -- not two independently-formed
institutional views disagreeing. It is still worth having: it is exactly
the kind of cross-source check nobody does by eye, and a real mismatch
means today's FII read should not be trusted at face value from either
source alone.
"""
from __future__ import annotations

import pandas as pd

from core.asof import asof_sql
from core.storage import db, register_views, write_table

# (metric label, nse_participant_oi column, upstox segment, upstox column)
# -- mapping verified live: each pair should read the same number when both
# sources are current, per the module docstring above.
FII_METRIC_MAP = [
    ("index_futures_long", "Future Index Long", "NSE_FO|INDEX_FUTURES", "total_long_contracts"),
    ("index_futures_short", "Future Index Short", "NSE_FO|INDEX_FUTURES", "total_short_contracts"),
    ("stock_futures_long", "Future Stock Long", "NSE_FO|STOCK_FUTURES", "total_long_contracts"),
    ("stock_futures_short", "Future Stock Short", "NSE_FO|STOCK_FUTURES", "total_short_contracts"),
    ("index_options_call_long", "Option Index Call Long", "NSE_FO|INDEX_OPTIONS", "total_call_long_contracts"),
    ("index_options_put_long", "Option Index Put Long", "NSE_FO|INDEX_OPTIONS", "total_put_long_contracts"),
    ("index_options_call_short", "Option Index Call Short", "NSE_FO|INDEX_OPTIONS", "total_call_short_contracts"),
    ("index_options_put_short", "Option Index Put Short", "NSE_FO|INDEX_OPTIONS", "total_put_short_contracts"),
    ("stock_options_call_long", "Option Stock Call Long", "NSE_FO|STOCK_OPTIONS", "total_call_long_contracts"),
    ("stock_options_put_long", "Option Stock Put Long", "NSE_FO|STOCK_OPTIONS", "total_put_long_contracts"),
    ("stock_options_call_short", "Option Stock Call Short", "NSE_FO|STOCK_OPTIONS", "total_call_short_contracts"),
    ("stock_options_put_short", "Option Stock Put Short", "NSE_FO|STOCK_OPTIONS", "total_put_short_contracts"),
]

# Documented tolerance -- exact match is the expectation (see module
# docstring), so anything past this small a gap is worth a look, not noise.
DIVERGENCE_THRESHOLD_PCT = 1.0


def _nse_fii_by_date() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        cols = {label: nse_col for label, nse_col, _, _ in FII_METRIC_MAP}
        # nse_participant_oi's declared key is (trade_date, "Client Type"), and
        # this filters to a single Client Type, so the point-in-time read is
        # already one row per trade_date -- no GROUP BY, and no per-column
        # arg_max that could pull two metrics from different vintages.
        select = ", ".join(f'"{nse_col}"' for nse_col in dict.fromkeys(cols.values()))
        return con.execute(asof_sql(
            "nse_participant_oi",
            columns=f"trade_date::VARCHAR AS trade_date, {select}",
            where="\"Client Type\" = 'FII'",
        )).df()
    finally:
        con.close()


def _upstox_fii_by_date_segment() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        # (trade_date, segment) is upstox_fii_activity's declared key, which is
        # exactly what the old GROUP BY was, so the collapse comes for free.
        return con.execute(asof_sql(
            "upstox_fii_activity",
            columns="trade_date, segment, total_long_contracts, total_short_contracts, "
                    "total_call_long_contracts, total_put_long_contracts, "
                    "total_call_short_contracts, total_put_short_contracts",
        )).df()
    finally:
        con.close()


def fii_source_divergence(threshold_pct: float = DIVERGENCE_THRESHOLD_PCT,
                          persist: bool = True) -> pd.DataFrame:
    """
    Long format: one row per (trade_date, metric) where both sources have a
    reading for that date. pct_diff is relative to whichever side is larger
    (clipped at a denominator of 1 so a 0-vs-0 pair reads as 0% not NaN).
    """
    nse = _nse_fii_by_date()
    upstox = _upstox_fii_by_date_segment()
    if nse.empty or upstox.empty:
        raise RuntimeError(
            "fii_source_divergence needs both nse_participant_oi (Client Type='FII') "
            "and upstox_fii_activity populated -- run those jobs first."
        )

    frames = []
    for metric, nse_col, segment, upstox_col in FII_METRIC_MAP:
        seg = upstox[upstox["segment"] == segment][["trade_date", upstox_col]] \
            .rename(columns={upstox_col: "upstox_value"})
        merged = nse[["trade_date", nse_col]].rename(columns={nse_col: "nse_value"}) \
            .merge(seg, on="trade_date", how="inner")
        if merged.empty:
            continue
        merged["metric"] = metric
        merged["abs_diff"] = (merged["nse_value"] - merged["upstox_value"]).abs()
        denom = merged[["nse_value", "upstox_value"]].abs().max(axis=1).clip(lower=1)
        merged["pct_diff"] = merged["abs_diff"] / denom * 100
        merged["diverges"] = merged["pct_diff"] > threshold_pct
        frames.append(merged)

    if not frames:
        raise RuntimeError(
            "fii_source_divergence: no (date, metric) pair had data from both sources."
        )
    df = pd.concat(frames, ignore_index=True)
    if persist:
        write_table(df, "derived", "fii_source_divergence")
    return df
