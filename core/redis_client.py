"""
The one place a Redis client is constructed.

WHY THIS EXISTS. Four call sites built their own client and three of them
ignored REDIS_URL entirely -- `redis.Redis()` hardcodes localhost db 0 no
matter what .env says. So the setting existed, looked configured, and did
nothing: exactly the shape of a config bug that survives for years because
everything appears to work.

TWO DEFENCES, because the failure is silent. This machine's db 0 already holds
another project's BullMQ queue (`bull:backtest-jobs:*`), and a FLUSHDB from
either side would wipe the other's live state mid-session.

  1. A DEDICATED DATABASE. The default is now db 1, not db 0.
  2. A KEY PREFIX. Every key we write is `qd:`-prefixed, so even if someone
     points REDIS_URL back at a shared database the two apps cannot collide.

Either alone would fix today's problem; both together mean it cannot come back
by way of a misedited .env.

DEGRADING IS NORMAL, NOT EXCEPTIONAL. The tick feed only runs during market
hours and the broker token expires 03:30 IST daily, so "no Redis" is the
ordinary overnight state. Callers get None and carry on; nothing here raises
unless a caller explicitly asks it to.
"""
from __future__ import annotations

from core.config import get

# db 1, deliberately not 0. Anything that ships defaulting to 0 will sooner or
# later share a database with something else on a developer's machine.
DEFAULT_URL = "redis://localhost:6379/1"

PREFIX = "qd:"
LTP_HASH = f"{PREFIX}ltp"          # instrument_key -> latest tick JSON
TICKS_CHANNEL = f"{PREFIX}ticks"   # pub/sub fan-out of every decoded tick


def redis_url() -> str:
    return get("REDIS_URL") or DEFAULT_URL


def redis_client(*, required: bool = False, connect_timeout: float = 2.0):
    """
    A connected client, or None.

    The ping is deliberate: constructing a client does not connect, so without
    it every caller would discover the outage later and somewhere less useful.

    required=True raises instead, for jobs whose whole purpose is the stream
    (connectors/upstox.py's live feed) where silently running without a cache
    would mean throwing ticks away.
    """
    try:
        import redis
    except ImportError:
        if required:
            raise RuntimeError("redis-py is not installed: pip install redis") from None
        return None

    try:
        client = redis.Redis.from_url(
            redis_url(), decode_responses=True, socket_connect_timeout=connect_timeout,
        )
        client.ping()
        return client
    except Exception as exc:                                          # noqa: BLE001
        if required:
            raise RuntimeError(
                f"Redis is not reachable at {redis_url()} -- the live feed needs it. "
                f"({type(exc).__name__})"
            ) from exc
        return None


def namespace_report(client=None) -> dict:
    """
    Who else is in this database.

    Surfaced rather than assumed. Sharing a Redis db is not itself an error --
    it is only dangerous when nobody knows about it, which is precisely how
    this was found. `foreign_keys` counts keys that are not ours, so the API
    can say so at startup instead of waiting for a FLUSHDB to explain it.
    """
    client = client or redis_client()
    if client is None:
        return {"reachable": False, "url": redis_url(), "ours": 0, "foreign_keys": 0}

    ours = foreign = 0
    try:
        for k in client.scan_iter(count=500):
            if k.startswith(PREFIX):
                ours += 1
            else:
                foreign += 1
    except Exception:                                                 # noqa: BLE001
        pass

    db = client.connection_pool.connection_kwargs.get("db", 0)
    return {"reachable": True, "url": redis_url(), "db": db,
            "ours": ours, "foreign_keys": foreign}
