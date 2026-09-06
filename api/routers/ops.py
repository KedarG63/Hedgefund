"""
Warehouse operations: is the data worth believing, and what is registered.

These back the status strip's health dot and the retained Streamlit Data Ops
surface. They are the one part of the API that reports on the pipeline rather
than serving it.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.deps import cursor, reload_views, views
from api.responses import frame

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/quality")
async def quality(wait: bool = Query(False, description="Force a synchronous "
                                     "recompute instead of reading the snapshot."),
                  fmt: str = "arrow"):
    """
    Freshness, volume and revision status per dataset -- from a snapshot.

    This does NOT walk the archive per request. quality_report() takes ~16s
    against a 32k-file, 4.7 GB archive, which would both make the status dot
    useless and tie up a threadpool worker long enough for a few concurrent
    polls to starve every other endpoint. api.background refreshes it on a
    10-minute timer instead and this returns the last result immediately.

    Freshness of the ANSWER travels with it, in x-qd-age-seconds and
    x-qd-stale, because a health indicator that cannot say how old it is is
    worse than no indicator. A cold snapshot reports computing=1 with an empty
    table rather than blocking -- "not measured yet" is a real state and the
    shell should render it as such.

    ?wait=true forces a synchronous recompute. That is for the retained
    Streamlit Data Ops page and for run_daily.py, which want a definitive
    answer now and can afford to wait for it; the terminal should never use it.
    """
    import pandas as pd

    from api.background import quality_snapshot

    snap = quality_snapshot()
    if wait:
        await snap.refresh()
    state = snap.read()

    value = state["value"]
    table = value if isinstance(value, pd.DataFrame) else pd.DataFrame()
    meta = {
        "age-seconds": state["age_seconds"] if state["age_seconds"] is not None else "",
        "computing": int(state["computing"]),
        "stale": int(state["stale"]),
    }
    if state["error"]:
        # Surfaced, not swallowed: monitoring that goes quiet when it breaks is
        # exactly the failure this table exists to catch.
        meta["error"] = state["error"][:200].replace("\n", " ")
    return frame(table, fmt, meta)


@router.get("/views")
def registered(fmt: str = "arrow"):
    """
    What is queryable right now, and which of it can be read point-in-time.

    `point_in_time` is false for a view with no declared natural key. Those
    still serve freshness monitoring; they just cannot be vintage-collapsed or
    back-dated, and core.asof refuses them loudly rather than silently
    returning duplicate rows.
    """
    import pandas as pd

    from core.quality import DATASET_KEYS

    rows = [{"view": v,
             "point_in_time": v in DATASET_KEYS,
             "natural_key": ", ".join(DATASET_KEYS.get(v, [])) or None}
            for v in sorted(views())]
    return frame(pd.DataFrame(rows), fmt)


@router.post("/reload-views")
def reload():
    """
    Re-register parquet datasets created since startup.

    register_views() runs once at startup, so a dataset written by a later
    run_daily.py stays invisible until this is called. Wire it into the tail
    of the daily run rather than restarting the service.
    """
    return {"registered": reload_views()}


# NOTE: /api/ops/health is defined in api/main.py, NOT here. This router has
# auth applied to every route, and Starlette matches the FIRST registered
# route -- a copy here would shadow the open one and make the liveness probe
# require a credential.


@router.get("/sql")
def sql_passthrough(q: str = Query(..., min_length=1), limit: int = Query(1000, le=100000),
                    fmt: str = "arrow"):
    """
    Read-only ad-hoc query, for the retained Streamlit SQL console.

    The connection is opened read-only against an in-memory database whose
    only contents are views over parquet, so a write cannot land anywhere even
    if one were submitted -- the guard below is a clearer error, not the
    security boundary. The boundary is the connection mode plus the bearer
    token on this route.
    """
    from fastapi import HTTPException

    lowered = " ".join(q.lower().split())
    banned = ("insert ", "update ", "delete ", "drop ", "create ", "alter ",
              "attach ", "copy ", "export ", "install ", "load ")
    if any(b in f" {lowered} " for b in banned):
        raise HTTPException(status_code=400,
                            detail="This endpoint is read-only; use SELECT.")
    return frame(cursor().execute(f"SELECT * FROM ({q}) LIMIT {int(limit)}"), fmt)
