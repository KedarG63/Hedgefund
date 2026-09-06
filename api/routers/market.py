"""
Cross-instrument views: the symbol universe, the ranked watchlist, and the
macro strip.

These back the panels that are on screen before anyone picks a symbol, so they
are the ones whose latency the shell actually feels.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from api.deps import AsOf, cursor, require_view, views
from api.responses import frame

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/universe")
def universe(as_of: AsOf = None, fmt: str = "arrow"):
    """
    Every tradable equity symbol, with name and industry where known.

    Backs the command bar's fuzzy index. Built from bhavcopy rather than a
    master list so it only ever contains symbols that actually have price
    history in this warehouse -- a command bar that offers a symbol with no
    data is worse than one that omits it.
    """
    from core.asof import asof_sql

    require_view("nse_bhavcopy")
    px = asof_sql("nse_bhavcopy", as_of,
                  columns="TckrSymb AS symbol, ISIN AS isin, FinInstrmNm AS name",
                  where="SctySrs = 'EQ'")
    sql = f"SELECT DISTINCT symbol, isin, name FROM ({px})"

    if "nse_index_constituents" in views():
        idx = asof_sql("nse_index_constituents", as_of,
                       columns='"Symbol" AS symbol, "Industry" AS industry, "index" AS idx')
        sql = (f"SELECT u.symbol, u.isin, u.name, i.industry, i.idx AS index_membership "
               f"FROM ({sql}) u LEFT JOIN ({idx}) i USING (symbol)")

    return frame(cursor().execute(f"SELECT * FROM ({sql}) ORDER BY symbol"), fmt)


@router.get("/watchlist")
def watchlist(as_of: AsOf = None, limit: int = Query(200, le=5000), fmt: str = "arrow"):
    """
    Instruments ranked by composite factor score, with the digest's flag where
    one exists.

    This is the left-rail grid. derived_digest covers only the shortlist the
    LLM phrased a flag for, so it is a LEFT JOIN -- the ranking comes from
    derived_factor_model, which is deterministic and covers the whole scanned
    universe.
    """
    from core.asof import latest_per

    require_view("derived_factor_model")
    factors = latest_per("derived_factor_model", ["symbol"], as_of,
                         columns="symbol, momentum_zscore, size_score, low_vol_score, "
                                 "composite_score, as_of_date")
    sql = f"SELECT * FROM ({factors})"

    if "derived_digest" in views():
        digest = latest_per("derived_digest", ["instrument"], as_of,
                            columns="instrument AS symbol, flag, tier, "
                                    "contributing_signals")
        sql = (f"SELECT f.*, d.flag, d.tier, d.contributing_signals "
               f"FROM ({sql}) f LEFT JOIN ({digest}) d USING (symbol)")

    sql = f"SELECT * FROM ({sql}) ORDER BY abs(composite_score) DESC LIMIT {int(limit)}"
    return frame(cursor().execute(sql), fmt)


@router.get("/correlation")
def correlation(symbols: str = Query(..., description="Comma-separated symbols."),
                as_of: AsOf = None, fmt: str = "arrow"):
    """
    Pairwise correlation across the requested symbols.

    derived_correlation is 9.08 MILLION rows -- a full pairwise matrix over the
    NSE equity universe across vintages. This filters on symbol_a/symbol_b
    inside DuckDB and never materialises the table; pulling it into pandas to
    subset afterwards would move gigabytes to answer a question about six names.

    Two steps, deliberately not folded into one ORDER BY: pit() collapses
    vintages of the same (pair, as_of_date), then the outer window applies the
    business rule -- of the surviving computation dates, show the most recent.
    """
    from core.asof import asof_sql

    # Validate the REQUEST before probing warehouse state: a correlation of one
    # name is malformed whether or not the dataset exists, and answering 404
    # would blame the warehouse for the caller's argument.
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()][:40]
    if len(wanted) < 2:
        return frame(pd.DataFrame(columns=["symbol_a", "symbol_b", "correlation",
                                           "n_obs", "as_of_date"]), fmt)
    require_view("derived_correlation")
    listed = ", ".join("'" + s.replace("'", "''") + "'" for s in wanted)

    inner = asof_sql("derived_correlation", as_of,
                     columns="symbol_a, symbol_b, correlation, n_obs, as_of_date",
                     where=f"symbol_a IN ({listed}) AND symbol_b IN ({listed})")
    sql = f"""SELECT * FROM ({inner})
        QUALIFY row_number() OVER (
            PARTITION BY symbol_a, symbol_b ORDER BY as_of_date DESC
        ) = 1"""
    return frame(cursor().execute(sql), fmt, {"symbols": len(wanted)})


@router.get("/macro/snapshot")
def macro_snapshot(as_of: AsOf = None, fmt: str = "arrow"):
    """
    The status-strip macro row: policy corridor, gold premium, chokepoint stress.

    Deliberately one call rather than three. These are read together, always,
    and three round trips to paint one strip is the kind of chattiness that
    makes a terminal feel slow before it has any real data in it.

    Long form ({group, label, value, unit, as_of}) because the strip renders
    heterogeneous units side by side and a wide row would need a schema change
    every time an indicator is added.
    """
    from core.asof import latest_per

    # latest_per, not asof_sql: each of these tables keys on (thing, date) so a
    # plain point-in-time read returns the whole history. A status strip wants
    # the most recent observation per thing, and latest_per derives that
    # ordering from the registry -- as_of DESC here, rate_date DESC for gold --
    # rather than each call site naming it.
    parts = []

    if "rbi_key_indicators" in views():
        parts.append(f"""SELECT 'policy' AS "group", indicator AS label,
            CAST(value AS DOUBLE) AS value, currencyDesc AS unit, CAST(as_of AS VARCHAR) AS as_of
            FROM ({latest_per("rbi_key_indicators", ["indicator"], as_of,
                              columns="indicator, value, currencyDesc, as_of")})""")

    if "derived_india_gold_premium" in views():
        parts.append(f"""SELECT 'gold' AS "group",
            'India gold premium (' || session || ')' AS label,
            CAST(premium_pct AS DOUBLE) AS value, '%' AS unit,
            CAST(rate_date AS VARCHAR) AS as_of
            FROM ({latest_per("derived_india_gold_premium", ["session"], as_of,
                              columns="session, premium_pct, rate_date")})""")

    if "analytics_chokepoint_zscore" in views():
        # Only the stressed routes belong on a status strip; the full 28-route
        # table is its own panel.
        parts.append(f"""SELECT 'chokepoint' AS "group",
            portname || ' (' || metric || ')' AS label,
            CAST(zscore AS DOUBLE) AS value, 'z' AS unit, CAST(as_of AS VARCHAR) AS as_of
            FROM ({latest_per("analytics_chokepoint_zscore", ["portname", "metric"], as_of,
                              columns="portname, metric, zscore, as_of")})
            WHERE abs(zscore) >= 2""")

    if not parts:
        return frame(pd.DataFrame(columns=["group", "label", "value", "unit", "as_of"]), fmt)

    sql = f"""SELECT * FROM ({" UNION ALL ".join(parts)}) ORDER BY "group", label"""
    return frame(cursor().execute(sql), fmt)
