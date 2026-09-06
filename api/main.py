"""
The service tier: DuckDB over parquet, served to the terminal.

    uvicorn api.main:app --reload --port 8787 --host 127.0.0.1

WHAT THIS IS AND IS NOT. It reads. It never fetches, parses or writes -- those
belong to connectors/ and core.storage.write_table(), and nothing here bypasses
them. Every route goes through core.asof, so the whole API is point-in-time by
construction rather than per-endpoint discipline: add ?as_of=2026-06-30 and the
prices AND the derived signals come back as they stood that day.

BINDING AND EXPOSURE. Bind to 127.0.0.1. Exchange terms restrict
redistribution (CLAUDE.md), and this serves the entire warehouse behind one
token -- treat any request to expose it as a licensing question first, not a
networking one. Startup refuses to run without TERMINAL_TOKEN set rather than
defaulting to open.

IN PRODUCTION there is one process: `npm --prefix web run build` emits
web/dist, which this mounts at /, so the terminal and its API share an origin
and there is no Node runtime to keep alive. In dev, Vite serves the bundle and
proxies /api here.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Match dashboard/app.py: allow `python -m api.main` and `uvicorn api.main:app`
# from any working directory without the repo root already on sys.path.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import Depends, FastAPI                                  # noqa: E402
from fastapi.responses import JSONResponse                            # noqa: E402
from fastapi.staticfiles import StaticFiles                           # noqa: E402

from api import deps                                                  # noqa: E402
from api.background import quality_snapshot                           # noqa: E402
from api.routers import (                                              # noqa: E402
    credit, flows, instrument, market, ops, provenance, world,
)
from api.ws import (                                                   # noqa: E402
    hub as tick_hub, router as ws_router, status_router as live_status_router,
)

WEB_DIST = Path(_REPO_ROOT) / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not deps.token_is_configured():
        raise RuntimeError(
            "TERMINAL_TOKEN is not set. This API serves the whole warehouse; "
            "it does not start unauthenticated. Add a random value to .env "
            "(python -c \"import secrets; print(secrets.token_urlsafe(32))\") "
            "and keep it out of git -- .env is already gitignored."
        )
    n = deps.startup()
    # Start warming the quality snapshot immediately rather than on first
    # request -- it takes ~16s, and the shell asks for it while it paints.
    snap = quality_snapshot()
    snap.start()
    print(f"[api] {n} datasets registered; quality snapshot warming")
    yield
    await snap.stop()
    await tick_hub.stop()
    deps.shutdown()


app = FastAPI(
    title="quantdata terminal API",
    summary="Point-in-time reads over the parquet warehouse.",
    lifespan=lifespan,
)

# Auth is applied per-router rather than globally so /api/ops/health stays open
# for liveness probes. Every route that returns DATA carries it.
app.include_router(instrument.router, dependencies=[Depends(deps.auth)])
app.include_router(market.router, dependencies=[Depends(deps.auth)])
app.include_router(flows.router, dependencies=[Depends(deps.auth)])
app.include_router(world.router, dependencies=[Depends(deps.auth)])
app.include_router(credit.router, dependencies=[Depends(deps.auth)])
app.include_router(provenance.router, dependencies=[Depends(deps.auth)])
app.include_router(ops.router, dependencies=[Depends(deps.auth)])
app.include_router(live_status_router, dependencies=[Depends(deps.auth)])
# The WebSocket alone carries no header dependency: the browser WebSocket API
# cannot set headers, so it checks the same token from a query parameter.
app.include_router(ws_router)


@app.get("/api/ops/health", tags=["ops"])
def health():
    """
    Open liveness probe -- the one route without a token.

    Says nothing about the data, only that the process is up and how many
    views registered. ops.py deliberately does NOT define this: its router
    carries auth on every route, and Starlette matches the first registration,
    so a copy there would shadow this one and break the probe.
    """
    return {"status": "ok", "views": len(deps.views())}


@app.exception_handler(Exception)
async def unhandled(request, exc):
    """
    A DuckDB Binder Error usually means a view is missing or a column was
    renamed upstream -- surface the actual message rather than a bare 500,
    because that is the difference between a five-second fix and an hour.
    Internal only, so leaking the query text costs nothing.
    """
    return JSONResponse(status_code=500,
                        content={"error": type(exc).__name__, "detail": str(exc)[:2000]})


# Mounted last: StaticFiles at "/" would otherwise shadow every /api route.
# Absent until `npm run build` has been run, which is the normal state in dev.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
