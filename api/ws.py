"""
Live ticks: Redis pub/sub -> one process-wide listener -> N browsers.

THE SHAPE OF THE PROBLEM. connectors/upstox.py publishes every decoded tick to
the Redis `ticks` channel. A full-mode broker feed on a few thousand
instruments is far more than a DOM can absorb, and forwarding per-tick would
melt the browser long before it melted anything else. connectors/stream.py's
TickBuffer already establishes the discipline for the disk side -- never write
every tick -- and this applies the same rule to the wire.

So: ONE Redis subscription for the whole process, not one per browser, and a
100ms coalescing timer. Within a window the same instrument may tick fifty
times; only the last value is sent, because that is the only one that would
have been visible anyway. A quiet window sends nothing at all rather than an
empty frame.

WHY instrument_key AND symbol BOTH TRAVEL. Upstox keys instruments as
"NSE_EQ|INE002A01018" -- segment plus ISIN, not ticker. The terminal's context
is a ticker, so something has to bridge the two, and doing it here means the
browser never has to know ISINs exist. The map is built from nse_bhavcopy,
which is the same table the universe endpoint draws on, so a symbol that can be
charted can also be subscribed.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from core.config import get
from core.redis_client import (
    LTP_HASH, TICKS_CHANNEL, namespace_report, redis_client, redis_url,
)

# Two routers on purpose. The WebSocket authenticates from a query parameter
# because the browser WebSocket API cannot set headers; everything else keeps
# the same bearer-header contract the REST routes use, so "every data route
# carries a token" stays true without an exception to remember.
router = APIRouter(tags=["live"])
status_router = APIRouter(prefix="/api/live", tags=["live"])

# 100ms: fast enough that a price looks live to a human, slow enough that a
# busy instrument collapses fifty ticks into one message.
COALESCE_MS = 100

# Keys and connection both come from core.redis_client, which is the single
# place that honours REDIS_URL and applies the qd: prefix. Reconstructing a
# client here is how the publishers and this reader drifted onto different
# databases in the first place.
CHANNEL = TICKS_CHANNEL


def _redis():
    """
    A Redis client, or None.

    Redis absence is NORMAL here -- the feed only runs during market hours and
    the Upstox token expires 03:30 IST daily -- so this never raises.
    """
    return redis_client()


# --------------------------------------------------------------- symbol map

_symbol_by_key: dict[str, str] = {}
_key_by_symbol: dict[str, str] = {}
_map_built_at: float = 0.0
_MAP_TTL = 900


def _build_symbol_map() -> None:
    """
    instrument_key <-> symbol, via the ISIN both sides carry.

    Rebuilt on a TTL rather than cached forever: new listings appear in
    bhavcopy, and a terminal that cannot subscribe to a symbol it can chart is
    a confusing kind of broken.
    """
    global _map_built_at
    from api.deps import cursor, views
    from core.asof import asof_sql

    if "nse_bhavcopy" not in views():
        return
    rows = cursor().execute(asof_sql(
        "nse_bhavcopy", None, columns="DISTINCT ISIN, TckrSymb",
        where="SctySrs = 'EQ' AND ISIN IS NOT NULL",
    )).fetchall()

    _symbol_by_key.clear()
    _key_by_symbol.clear()
    for isin, symbol in rows:
        key = f"NSE_EQ|{isin}"
        _symbol_by_key[key] = symbol
        _key_by_symbol[symbol] = key
    _map_built_at = time.time()


def _ensure_map() -> None:
    if not _symbol_by_key or time.time() - _map_built_at > _MAP_TTL:
        _build_symbol_map()


def keys_for(symbols: list[str]) -> dict[str, str]:
    """{instrument_key: symbol} for the symbols we can actually resolve."""
    _ensure_map()
    out = {}
    for s in symbols:
        key = _key_by_symbol.get(s.upper())
        if key:
            out[key] = s.upper()
    return out


# ------------------------------------------------------------------ the hub

class TickHub:
    """
    One Redis subscription, many browsers.

    A subscription per connection would multiply the same firehose by the
    number of open tabs for no benefit -- every connection would decode every
    tick and then discard almost all of them. Here the decode happens once and
    each connection is handed only what it asked for.
    """

    def __init__(self) -> None:
        self._latest: dict[str, dict] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._pump: asyncio.Task | None = None
        # asyncio.to_thread CANNOT be cancelled: cancelling the awaiting task
        # leaves the worker thread running. So the listener thread has to be
        # asked to stop, and has to be in a position to notice -- hence the
        # polling loop below rather than a blocking pubsub.listen(). Without
        # this the process never exits: uvicorn hangs on Ctrl+C and a
        # TestClient context never closes.
        self._stopping = threading.Event()
        self.connected = False
        self.last_tick_at: float | None = None
        self.ticks_seen = 0

    def subscribe(self) -> asyncio.Queue:
        # Bounded: a browser that stops reading must not grow the server's
        # memory without limit. On overflow the OLDEST frame is dropped --
        # for last-value-wins data, stale frames are exactly what to discard.
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def snapshot(self, keys: set[str]) -> list[dict]:
        """Last known value for each key, so a new connection paints
        immediately instead of waiting for the next tick."""
        client = _redis()
        if client is None:
            return []
        out = []
        try:
            for key in keys:
                raw = client.hget(LTP_HASH, key)
                if raw:
                    out.append(json.loads(raw))
        except Exception:                                             # noqa: BLE001
            return out
        return out

    async def start(self) -> None:
        self._stopping.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._listen(), name="tickhub:listen")
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._flush_loop(), name="tickhub:flush")

    async def stop(self) -> None:
        # Signal the listener thread FIRST; cancelling the task alone would
        # leave it blocked on the socket and the process would not exit.
        self._stopping.set()
        for t in (self._task, self._pump):
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):           # noqa: BLE001
                    pass
        self._task = self._pump = None

    async def _listen(self) -> None:
        """Blocking redis-py runs in a thread; the queue crosses back."""
        loop = asyncio.get_running_loop()
        while not self._stopping.is_set():
            client = await asyncio.to_thread(_redis)
            if client is None:
                self.connected = False
                await asyncio.sleep(5)     # feed is down or out of hours
                continue
            self.connected = True
            try:
                await asyncio.to_thread(self._blocking_listen, client, loop)
            except Exception:                                         # noqa: BLE001
                pass
            self.connected = False
            if not self._stopping.is_set():
                await asyncio.sleep(2)

    def _blocking_listen(self, client, loop) -> None:
        """
        Poll rather than block on pubsub.listen().

        listen() blocks until a message arrives, which on a quiet channel is
        forever -- and since this runs in a to_thread worker that cannot be
        cancelled, "forever" means the process never shuts down. get_message
        with a timeout returns control often enough to notice the stop flag.
        """
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        try:
            while not self._stopping.is_set():
                message = pubsub.get_message(timeout=0.5)
                if not message or message.get("type") != "message":
                    continue
                try:
                    rec = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
                key = rec.get("instrument_key")
                if not key:
                    continue
                # LAST VALUE WINS. This is the coalescing: fifty ticks in one
                # window leave one entry, which is all the browser could have
                # rendered anyway.
                loop.call_soon_threadsafe(self._record, key, rec)
        finally:
            try:
                pubsub.close()
            except Exception:                                         # noqa: BLE001
                pass

    def _record(self, key: str, rec: dict) -> None:
        self._latest[key] = rec
        self.ticks_seen += 1
        self.last_tick_at = time.time()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(COALESCE_MS / 1000)
            if not self._latest or not self._subscribers:
                # A quiet window sends nothing rather than an empty frame --
                # heartbeats belong in the protocol, not in the data path.
                self._latest.clear()
                continue
            batch = list(self._latest.values())
            self._latest.clear()
            for q in list(self._subscribers):
                try:
                    q.put_nowait(batch)
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()          # drop the oldest, keep the newest
                        q.put_nowait(batch)
                    except Exception:           # noqa: BLE001
                        pass


hub = TickHub()


@router.websocket("/ws/ticks")
async def ticks(ws: WebSocket, symbols: str = Query(""), token: str = Query("")):
    """
    Live ticks for the requested symbols.

    The token arrives as a query parameter because the browser WebSocket API
    cannot set headers. In dev the Vite proxy appends it, so the page still
    holds no credential; the check is the same one the REST routes use.
    """
    expected = get("TERMINAL_TOKEN")
    if not expected or token != expected:
        await ws.close(code=1008, reason="bad token")
        return

    await ws.accept()
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    key_map = keys_for(wanted)
    keys = set(key_map)

    await hub.start()
    q = hub.subscribe()

    try:
        await ws.send_json({
            "type": "hello",
            "subscribed": sorted(key_map.values()),
            "unresolved": sorted(set(wanted) - set(key_map.values())),
            "feed_connected": hub.connected,
            "coalesce_ms": COALESCE_MS,
        })
        # Last known value first, so the grid paints immediately instead of
        # sitting blank until the next tick -- which outside market hours is
        # never.
        snapshot = [r for r in hub.snapshot(keys) if r.get("instrument_key") in keys]
        if snapshot:
            await ws.send_json({"type": "snapshot", "ticks": _label(snapshot)})

        while True:
            batch = await q.get()
            mine = [r for r in batch if r.get("instrument_key") in keys]
            if mine:
                await ws.send_json({"type": "ticks", "ticks": _label(mine)})
    except WebSocketDisconnect:
        pass
    except Exception:                                                 # noqa: BLE001
        pass
    finally:
        hub.unsubscribe(q)


def _label(records: list[dict]) -> list[dict[str, Any]]:
    """Attach the ticker so the browser never has to know ISINs exist."""
    out = []
    for r in records:
        out.append({
            "symbol": _symbol_by_key.get(r.get("instrument_key", ""), ""),
            "instrument_key": r.get("instrument_key"),
            "ltp": r.get("ltp"),
            "volume": r.get("volume"),
            "oi": r.get("oi"),
            "close_price": r.get("close_price"),
            "ts": r.get("ts"),
        })
    return out


@status_router.get("/status")
def live_status():
    """
    Whether the feed is actually delivering, for the status strip.

    `feed_connected` is about Redis; `last_tick_age_seconds` is about the
    broker. Both matter and they fail independently -- a healthy Redis with no
    ticks means the stream job is not running, which looks identical to a quiet
    market unless the age is shown.
    """
    _ensure_map()
    age = None if hub.last_tick_at is None else round(time.time() - hub.last_tick_at, 1)
    ns = namespace_report()
    return {
        "redis_reachable": ns["reachable"],
        "redis_url": redis_url(),
        "redis_db": ns.get("db"),
        # Surfaced, not hidden. Our keys are qd:-prefixed so a shared database
        # cannot corrupt us, but whoever else is in there can still FLUSHDB the
        # live cache out from under a trading session -- worth knowing before
        # that happens rather than after.
        "foreign_keys_in_db": ns.get("foreign_keys", 0),
        "feed_connected": hub.connected,
        "ticks_seen": hub.ticks_seen,
        "last_tick_age_seconds": age,
        "symbols_mapped": len(_key_by_symbol),
        "coalesce_ms": COALESCE_MS,
    }
