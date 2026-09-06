"""
Institutional positioning.

India publishes participant-wise open interest daily and free; no other major
market does. The US analogue is 13F, on a 45-day lag. That asymmetry is why
this gets its own panel rather than a row on the macro strip.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from api.deps import AsOf, cursor, require_view, views
from api.responses import frame

router = APIRouter(prefix="/api/flows", tags=["flows"])


@router.get("/participants")
def participants(as_of: AsOf = None, limit: int = Query(200, le=5000), fmt: str = "arrow"):
    """
    NSE participant-wise OI by client type, newest first.

    Long form ({trade_date, client_type, book, contracts}) rather than the
    source's 13 wide columns: the panel stacks Future/Option x Long/Short per
    client type, and a wide row would need a schema change every time NSE adds
    a segment.
    """
    from core.asof import asof_sql

    require_view("nse_participant_oi")
    books = [
        ("Future Index Long", "future_index", "long"),
        ("Future Index Short", "future_index", "short"),
        ("Future Stock Long", "future_stock", "long"),
        ("Future Stock Short", "future_stock", "short"),
        ("Total Long Contracts", "total", "long"),
        ("Total Short Contracts", "total", "short"),
    ]
    inner = asof_sql("nse_participant_oi", as_of)
    parts = [
        f'''SELECT trade_date, "Client Type" AS client_type,
            '{book}' AS book, '{side}' AS side,
            CAST("{col}" AS DOUBLE) AS contracts
            FROM ({inner})'''
        for col, book, side in books
    ]
    sql = (f"SELECT * FROM ({' UNION ALL '.join(parts)}) "
           f"ORDER BY trade_date DESC, client_type, book LIMIT {int(limit)}")
    return frame(cursor().execute(sql), fmt)


@router.get("/fii-dii")
def fii_dii(as_of: AsOf = None, limit: int = Query(120, le=2000), fmt: str = "arrow"):
    """FII/DII cash-market buy/sell/net by category and date."""
    from core.asof import asof_sql

    require_view("nse_fii_dii")
    return frame(cursor().execute(asof_sql(
        "nse_fii_dii", as_of,
        columns="category, date, try_cast(buyValue AS DOUBLE) AS buy_value, "
                "try_cast(sellValue AS DOUBLE) AS sell_value, "
                "try_cast(netValue AS DOUBLE) AS net_value",
        order_by="strptime(date, '%d-%b-%Y') DESC", limit=limit,
    )), fmt)


@router.get("/divergence")
def divergence(as_of: AsOf = None, fmt: str = "arrow"):
    """
    Where two independent sources disagree about the same FII number.

    NSE's participant OI versus Upstox's separately-polled feed. These should
    read identically, so a flagged row means one source is stale, lagging or
    wrong -- it is a data-quality check, not two institutions taking opposite
    sides. Worth a panel precisely because the failure is invisible otherwise.
    """
    from core.asof import latest_per

    if "derived_fii_source_divergence" not in views():
        return frame(pd.DataFrame(columns=["metric", "nse_value", "upstox_value",
                                           "pct_diff", "diverges", "trade_date"]), fmt)
    return frame(cursor().execute(latest_per(
        "derived_fii_source_divergence", ["metric"], as_of,
        columns="metric, nse_value, upstox_value, abs_diff, pct_diff, diverges, trade_date",
    )), fmt)
