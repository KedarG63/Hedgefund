"""
Request-scoped plumbing: the DuckDB handle, the as-of bound, and auth.

CONNECTION MODEL. One module-level connection is opened at startup and every
request gets its own `con.cursor()`. That is not style -- a DuckDB connection
parks its result set ON the connection, so two threads calling execute()/df()
against the same handle race, and the loser silently receives None rather than
raising. FastAPI runs sync endpoints in a threadpool, so this is the default
case, not an edge case. dashboard/app.py hit exactly this and fixed it the same
way; tests/test_dashboard.py pins it there.

The connection is in-memory and read-only (core.storage.db(read_only=True)):
the data lives in the parquet files and register_views() recreates the view
definitions in milliseconds, so no lock is taken on warehouse.duckdb and
run_daily.py can keep writing while the terminal is open. Preserving that
property is the whole reason this tier does not open a read-write handle.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query

from core.config import get, require
from core.storage import db, register_views

_con = None
_views: set[str] = set()


def startup() -> int:
    """Open the connection and register every parquet dataset as a view."""
    global _con, _views
    _con = db(read_only=True)
    _views = set(register_views(_con))
    return len(_views)


def shutdown() -> None:
    global _con
    if _con is not None:
        _con.close()
        _con = None


def reload_views() -> int:
    """
    Re-register views so datasets created since startup become visible.

    register_views() runs once at import, so a dataset written by a later
    run_daily.py is invisible until something calls this. run_daily.py should
    hit POST /api/ops/reload-views at the end of its run.
    """
    global _views
    if _con is None:
        raise RuntimeError("api.deps.startup() has not run")
    _views = set(register_views(_con))
    return len(_views)


def cursor():
    """A fresh cursor over the shared database. One per request."""
    if _con is None:
        raise RuntimeError("api.deps.startup() has not run")
    return _con.cursor()


def views() -> set[str]:
    return _views


def require_view(name: str) -> None:
    """404 rather than a 500 from a Binder Error when nothing is ingested."""
    if name not in _views:
        raise HTTPException(
            status_code=404,
            detail=f"{name!r} has no data in the warehouse yet -- run the job that writes it.",
        )


# ------------------------------------------------------------------------ auth
# The terminal is internal. Exchange terms restrict REDISTRIBUTION (see
# CLAUDE.md), so this must not become reachable off the machine without a
# licensing conversation first: uvicorn binds 127.0.0.1 and every data route
# carries a bearer token. Startup FAILS if TERMINAL_TOKEN is unset rather than
# defaulting to open -- a research terminal that silently serves the whole
# warehouse unauthenticated is the failure worth preventing.

def auth(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = require("TERMINAL_TOKEN")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required.")
    if authorization[len("Bearer "):].strip() != expected:
        raise HTTPException(status_code=401, detail="Bad token.")


def token_is_configured() -> bool:
    value = get("TERMINAL_TOKEN")
    return bool(value) and not value.startswith("CHANGEME")


# ----------------------------------------------------------------------- as-of

def as_of_param(
    as_of: Annotated[
        str | None,
        Query(description="ISO date. Show the warehouse as it was known on this "
                          "date. Omit for everything known now."),
    ] = None,
) -> date | None:
    """Parse and validate once, here, so no route inlines a raw string into SQL."""
    if as_of in (None, ""):
        return None
    try:
        return date.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"as_of must be an ISO date (YYYY-MM-DD), got {as_of!r}.",
        ) from None


AsOf = Annotated["date | None", Depends(as_of_param)]
Authed = Depends(auth)
