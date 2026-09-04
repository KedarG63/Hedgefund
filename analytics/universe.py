"""
Shared universe definition for the India equities analytics engine.

Every other analytics/ module scores instruments against this list, unless
it has its own natural universe (options.py is scoped to whatever's in the
option-chain snapshot -- NIFTY/BANKNIFTY only in Phase 1).

There is no NIFTY-500-membership connector anywhere in this repo, and
building one (scraping an index-constituents page) is out of scope for this
phase -- it would need the same verified-endpoint discipline as every other
connector. So the honest Phase 1 universe is "every ordinary equity/ETF
series NSE actually printed a bhavcopy row for", not an index membership
list. This can be narrowed later (NIFTY 500, NIFTY 200) once a verified
constituents source exists.
"""
from __future__ import annotations

import pandas as pd

from core.storage import db, register_views

# NSE's SctySrs column is not just 'EQ' -- 'GB'/'SG' are sovereign gold
# bonds, 'GS'/'ST'/'SM' are other instrument classes riding the same file.
# 'EQ' is the value that means "ordinary equity or ETF share".
EQUITY_SERIES = "EQ"

# Opt-in liquidity floor (Rs turnover/day) for callers that pass min_turnover.
# Documented, unvalidated Phase 8 starting point -- same "hand-set, not
# backtested" character as digest.py's COMPOSITE_WEIGHTS. Not enabled by
# default anywhere yet; decide the real threshold after checking how many
# symbols it actually excludes on live data, not by guessing one number now.
MIN_TURNOVER_FLOOR = 10_00_000  # Rs 10 lakh/day


def equity_universe(as_of: str | None = None, min_turnover: float | None = None) -> pd.DataFrame:
    """
    Distinct NSE main-board equity/ETF symbols from the most recent
    nse_bhavcopy vintage on or before `as_of` (latest available if None),
    filtered to SctySrs == 'EQ'.

    One row per (symbol, ISIN) with its close/volume/turnover for that date.
    Deduplicates via any_value() -- the source file occasionally carries more
    than one identical row per symbol per date.

    min_turnover, if given, drops rows below that Rs turnover (close*volume)
    floor -- an opt-in liquidity filter so illiquid names don't dominate a
    cross-sectional z-score just because they're erratic. Default None keeps
    today's behavior unchanged for every existing caller.
    """
    con = db(read_only=True)
    try:
        register_views(con)
        where_date = f"TradDt = '{as_of}'" if as_of else \
            "TradDt = (SELECT max(TradDt) FROM nse_bhavcopy)"
        df = con.execute(f"""
            SELECT TckrSymb AS symbol, ISIN AS isin, TradDt AS trade_date,
                   any_value(ClsPric) AS close, any_value(TtlTradgVol) AS volume
            FROM nse_bhavcopy
            WHERE {where_date} AND SctySrs = '{EQUITY_SERIES}'
            GROUP BY TckrSymb, ISIN, TradDt
            ORDER BY symbol
        """).df()
    finally:
        con.close()
    if df.empty:
        raise RuntimeError(
            "equity_universe: no EQ-series rows found in nse_bhavcopy. Run the "
            "nse_bhavcopy job first, or check `as_of` matches a real TradDt."
        )
    df["turnover"] = df["close"] * df["volume"]
    if min_turnover is not None:
        df = df[df["turnover"] >= min_turnover]
    return df
