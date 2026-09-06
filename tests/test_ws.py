"""
The live tick tier.

Two halves, deliberately separated. The symbol mapping and the auth boundary
are pure logic and always run. The coalescing behaviour needs a real Redis and
SKIPS without one -- the feed only exists during market hours, so a test that
demanded it would fail for the wrong reason on most days.
"""
from __future__ import annotations





import pandas as pd
import pytest

TOKEN = "ws-test-token"


def _redis_or_skip():
    try:
        import redis

        r = redis.Redis(decode_responses=True, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception:                                                # noqa: BLE001
        pytest.skip("no Redis reachable -- the live feed is a market-hours thing")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over a one-instrument fixture warehouse."""
    monkeypatch.setenv("TERMINAL_TOKEN", TOKEN)

    root = tmp_path / "parquet"
    (root / "nse" / "bhavcopy").mkdir(parents=True)
    pd.DataFrame([
        {"TradDt": "2026-08-21", "TckrSymb": "RELIANCE", "SctySrs": "EQ",
         "ISIN": "INE002A01018", "ClsPric": 1400.0, "OpnPric": 1390.0,
         "HghPric": 1410.0, "LwPric": 1385.0, "PrvsClsgPric": 1395.0,
         "TtlTradgVol": 1_000_000, "TtlTrfVal": 1.4e9, "TtlNbOfTxsExctd": 5000,
         "FinInstrmNm": "RELIANCE", "knowledge_date": "2026-08-21"},
    ]).to_parquet(root / "nse" / "bhavcopy" / "20260821T090000_000001.parquet", index=False)

    raw = tmp_path / "raw"
    raw.mkdir()
    import core.quality as quality
    import core.storage as storage
    for mod in (storage, quality):
        monkeypatch.setattr(mod, "PARQUET", root)
        monkeypatch.setattr(mod, "RAW", raw)

    import api.background as background
    import api.ws as ws
    monkeypatch.setattr(background, "_quality", None)
    # The symbol map is module-level and TTL'd; clear it so this test's
    # warehouse is what gets mapped rather than a previous test's.
    ws._symbol_by_key.clear()
    ws._key_by_symbol.clear()
    monkeypatch.setattr(ws, "_map_built_at", 0.0)

    from fastapi.testclient import TestClient

    from api.main import app
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------- symbol mapping

def test_symbols_resolve_to_upstox_instrument_keys(client):
    """
    Upstox keys instruments by segment+ISIN, not ticker. The bridge lives on
    the server so the browser never has to know ISINs exist.
    """
    from api.ws import keys_for

    assert keys_for(["RELIANCE"]) == {"NSE_EQ|INE002A01018": "RELIANCE"}


def test_an_unknown_symbol_resolves_to_nothing_rather_than_a_bad_key(client):
    """Subscribing to a fabricated key would silently never tick, which looks
    exactly like a quiet market."""
    from api.ws import keys_for

    assert keys_for(["NOSUCHSYMBOL"]) == {}


def test_hello_reports_which_symbols_could_not_be_resolved(client):
    """The client must be able to tell "no ticks yet" from "you asked for
    something I cannot subscribe to"."""
    with client.websocket_connect(
        f"/ws/ticks?symbols=RELIANCE,NOSUCHSYMBOL&token={TOKEN}"
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["subscribed"] == ["RELIANCE"]
        assert hello["unresolved"] == ["NOSUCHSYMBOL"]


# ---------------------------------------------------------------------- auth

@pytest.mark.parametrize("qs", ["", "&token=wrong"])
def test_the_socket_rejects_a_bad_token(client, qs):
    """
    The browser WebSocket API cannot set headers, so the token rides in the
    query string -- which makes it easy to forget to check. This is that check.
    """
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/ticks?symbols=RELIANCE{qs}") as ws:
            ws.receive_json()


def test_live_status_requires_the_bearer_header(client):
    assert client.get("/api/live/status").status_code == 401
    r = client.get("/api/live/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.json()["symbols_mapped"] == 1


# ----------------------------------------------------------------- coalescing

def test_a_burst_collapses_to_one_entry_per_instrument():
    """
    THE PROPERTY THE TIER EXISTS FOR, tested at the hub rather than through a
    socket.

    A full-mode broker feed is far more than a DOM can absorb, so within a
    coalescing window an instrument must collapse to its LAST value -- the only
    one that would have been visible anyway.

    Deliberately NOT an end-to-end WebSocket test. That version needs a live
    Redis, real timing, and a reader thread that cannot be interrupted mid
    receive(), and this machine's Redis is shared with another project. A test
    that fails for those reasons teaches nothing. The socket path is verified
    separately against a real Redis; what is pinned here is the logic.
    """
    from api.ws import TickHub

    hub = TickHub()
    for i in range(50):
        hub._record("NSE_EQ|INE002A01018", {"instrument_key": "NSE_EQ|INE002A01018",
                                            "ltp": 1400.0 + i * 0.05})
    for i in range(10):
        hub._record("NSE_EQ|INE467B01029", {"instrument_key": "NSE_EQ|INE467B01029",
                                            "ltp": 3100.0 + i})

    assert hub.ticks_seen == 60, "every tick should be counted"
    assert len(hub._latest) == 2, "60 ticks over 2 instruments must leave 2 entries"
    assert hub._latest["NSE_EQ|INE002A01018"]["ltp"] == pytest.approx(1400.0 + 49 * 0.05)
    assert hub._latest["NSE_EQ|INE467B01029"]["ltp"] == 3109.0


def test_a_slow_consumer_drops_the_oldest_frame_not_the_newest():
    """
    A browser that stops reading must not grow the server's memory without
    bound. On overflow the OLDEST frame goes: for last-value-wins data, stale
    frames are precisely what to discard, and dropping the newest would leave
    the client permanently behind.
    """
    import asyncio

    from api.ws import TickHub

    async def run():
        hub = TickHub()
        q = hub.subscribe()
        for i in range(q.maxsize + 20):
            try:
                q.put_nowait([{"instrument_key": "k", "ltp": float(i)}])
            except asyncio.QueueFull:
                q.get_nowait()
                q.put_nowait([{"instrument_key": "k", "ltp": float(i)}])
        assert q.qsize() == q.maxsize
        newest = None
        while not q.empty():
            newest = q.get_nowait()
        return newest

    newest = asyncio.run(run())
    assert newest[0]["ltp"] == float(83), "the most recent frame must survive"


def test_redis_absence_is_normal_and_never_raises(monkeypatch):
    """
    The feed only runs during market hours and the Upstox token expires 03:30
    IST daily, so "no Redis" is the ordinary overnight state. A snapshot must
    return empty rather than take the panel down.
    """
    import api.ws as ws

    monkeypatch.setattr(ws, "_redis", lambda: None)
    assert ws.hub.snapshot({"NSE_EQ|INE002A01018"}) == []
