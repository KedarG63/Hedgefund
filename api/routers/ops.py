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
def quality(stale_after_hours: float = Query(48.0, gt=0), fmt: str = "arrow"):
    """
    Freshness, volume and revision status per dataset.

    Walks the raw archive, so it is the slowest call in this API by a wide
    margin (~16s against a 32k-file archive). The shell should poll it on a
    timer and cache, never block a panel on it.
    """
    from core.quality import quality_report

    return frame(quality_report(stale_after_hours=stale_after_hours), fmt)


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
