"""
Upstox -- broker-licensed India market data: FII/DII positioning and live ticks.

WHY A BROKER SITS IN A NO-VENDOR PIPELINE
    Real-time exchange prices are a licensed product; there is no free feed, and
    anyone offering one is redistributing without a licence. But the broker
    relationship you already pay for INCLUDES a licensed feed. So the broker is
    not a data vendor -- it is the exchange's own distribution, billed as part of
    a trading account.

TWO THINGS LIVE HERE

  fii_activity()   FII derivatives and cash positioning over a documented REST
                   endpoint. This is the same shape as NSE's participant-wise
                   OI file, but served as JSON instead of scraped from a CSV.

  stream()         The live tick layer: WebSocket -> Redis (hot last-value)
                   -> parquet every 60s (cold permanent history).

THE FEED IS PROTOBUF, AND v2 IS GONE
    Confirmed by connecting and reading the bytes, not by assumption. The first
    frame off the socket begins:

        08 02 18 84 ae ce ea 82 34 22 72 0a 0a 0a 06 4e 43 44 5f 46 4f

    which is protobuf wire format (field 1 varint, field 4 length-delimited),
    not JSON. Decoding uses MarketDataFeedV3_pb2 from the official
    upstox-python-sdk -- the authoritative schema, maintained by the vendor,
    rather than a hand-rolled decoder that would drift.

    The v2 authorize endpoint now returns HTTP 410:
        UDAPI1153 "This v2 endpoint has been discontinued.
                   Please use the new endpoint /v3/feed/market-data-feed."
    so v3 is the only path.

    The websocket URL is SINGLE-USE -- it carries a one-shot `code` query
    parameter -- so every reconnect must re-authorize. It cannot be cached.

TOKENS DIE DAILY AT 03:30 IST
    See connectors/upstox_auth.py. A long-running streamer WILL be killed by
    that, so stream() checks validity up front and says how long it has rather
    than dying opaquely at 3am.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from core.config import require
from core.storage import save_raw, write_table

API = "https://api.upstox.com"
FEED_AUTHORIZE_V3 = f"{API}/v3/feed/market-data-feed/authorize"
FII_URL = f"{API}/v2/market/fii"

# Segments the FII endpoint serves. Cash plus the four derivative books is the
# whole institutional footprint NSE publishes.
FII_SEGMENTS = (
    "NSE_FO|INDEX_FUTURES",
    "NSE_FO|STOCK_FUTURES",
    "NSE_FO|INDEX_OPTIONS",
    "NSE_FO|STOCK_OPTIONS",
    "NSE_EQ|CASH",
)

# Feed modes, from the streamer docs. full_d30 needs an Upstox Plus subscription.
FEED_MODES = ("ltpc", "full", "option_greeks", "full_d30")


def _headers() -> dict:
    return {"Authorization": f"Bearer {require('UPSTOX_ACCESS_TOKEN')}",
            "Accept": "application/json"}


def _check_token():
    """Fail early and legibly rather than at 3am with a 401."""
    from connectors.upstox_auth import hours_remaining
    left = hours_remaining()
    if left is None:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is missing or malformed. "
                           "Run: python -m connectors.upstox_auth")
    if left <= 0:
        raise RuntimeError(f"UPSTOX_ACCESS_TOKEN expired {abs(left):.1f}h ago. "
                           "Run: python -m connectors.upstox_auth")
    return left


# --------------------------------------------------------------- FII activity
def fii_activity(interval: str = "1D", segments=FII_SEGMENTS,
                 since: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    FII buy/sell, open interest and long/short positioning by segment.

    Why it matters: this is institutional positioning in the derivatives book --
    the same signal as NSE's participant-wise OI, which BUILD_GUIDE calls the
    single most valuable free dataset in Indian markets. Having it over an API
    means no CSV scrape and no schema drift from a changing file layout.

    interval "1D" gives up to 30 trading days, "1M" up to 12 months.

    NOTE ON HISTORY: Upstox documents this as available from 1 April 2026
    onwards, so there is no deep back-history here. The NSE participant-OI
    connector remains the route to anything older.
    """
    _check_token()
    if interval not in ("1D", "1M"):
        raise ValueError(f"interval must be '1D' or '1M', got {interval!r}")

    params = {"data_type": ",".join(segments), "interval": interval}
    if since:
        params["from"] = since

    r = httpx.get(FII_URL, headers=_headers(), params=params, timeout=60)
    save_raw("upstox", "fii_activity", r.content, "json",
             {"interval": interval, "segments": list(segments), "from": since,
              "url": FII_URL})
    if r.status_code != 200:
        raise RuntimeError(f"fii_activity failed ({r.status_code}): {r.text[:250]}")

    payload = r.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"fii_activity returned status={payload.get('status')}: "
                           f"{json.dumps(payload)[:250]}")

    rows = []
    for segment, entries in (payload.get("data") or {}).items():
        for e in entries or []:
            rec = dict(e)
            rec["segment"] = segment
            rows.append(rec)
    if not rows:
        raise RuntimeError(f"fii_activity returned no rows for {segments}")

    df = pd.DataFrame(rows)
    # time_stamp is epoch milliseconds in IST terms; keep both the raw value and
    # a readable trade date so joins against NSE data are straightforward.
    if "time_stamp" in df.columns:
        df["trade_date"] = (pd.to_datetime(df["time_stamp"], unit="ms", utc=True)
                              .dt.tz_convert("Asia/Kolkata").dt.date.astype(str))
    df["interval"] = interval
    if persist:
        write_table(df, "upstox", "fii_activity")
    return df


# ------------------------------------------------------------------- live feed
def authorize_feed() -> str:
    """
    Get a websocket URL for the market feed.

    SINGLE USE. The returned URL embeds a one-shot `code`, so every reconnect
    needs a fresh call -- caching it produces a connection that is refused for
    reasons that look like an auth problem.
    """
    _check_token()
    r = httpx.get(FEED_AUTHORIZE_V3, headers=_headers(), timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"feed authorize failed ({r.status_code}): {r.text[:250]}")
    uri = (r.json().get("data") or {}).get("authorized_redirect_uri")
    if not uri:
        raise RuntimeError(f"no authorized_redirect_uri in response: {r.text[:250]}")
    return uri


def _ltpc_to_rec(key: str, ltpc, now: float) -> dict:
    return {
        "instrument_key": key,
        "ltp": ltpc.ltp,
        "ltt": ltpc.ltt,
        "ltq": ltpc.ltq,
        "close_price": ltpc.cp,
        "ts": now,
    }


def decode_feed(raw: bytes) -> tuple[str, list[dict], dict]:
    """
    Decode one protobuf frame into (message_type, tick_records, market_info).

    Split out from the socket loop so it can be tested without a live market --
    the feed is silent outside trading hours, which would otherwise make this
    untestable on the day you write it.
    """
    from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb

    response = pb.FeedResponse()
    response.ParseFromString(raw)
    # The enums are MODULE-level in this schema (pb.Type), not nested under the
    # message -- pb.FeedResponse.Type raises AttributeError.
    kind = pb.Type.Name(response.type)

    if kind == "market_info":
        status_enum = (pb.MarketInfo.DESCRIPTOR
                       .fields_by_name["segmentStatus"].message_type
                       .fields_by_name["value"].enum_type)
        return kind, [], {
            segment: status_enum.values_by_number[status].name
            for segment, status in response.marketInfo.segmentStatus.items()
        }

    now = time.time()
    records = []
    for key, feed in response.feeds.items():
        which = feed.WhichOneof("FeedUnion")
        if which == "ltpc":
            records.append(_ltpc_to_rec(key, feed.ltpc, now))
        elif which == "fullFeed":
            full = feed.fullFeed
            inner = full.WhichOneof("FullFeedUnion")
            if inner == "marketFF":
                m = full.marketFF
                rec = _ltpc_to_rec(key, m.ltpc, now)
                rec.update({"atp": m.atp, "volume": m.vtt, "oi": m.oi, "iv": m.iv,
                            "total_buy_qty": m.tbq, "total_sell_qty": m.tsq})
                records.append(rec)
            elif inner == "indexFF":
                records.append(_ltpc_to_rec(key, full.indexFF.ltpc, now))
    return kind, records, {}


# Only these mean a segment is actually trading. Substring tests do not work:
# "CLOSING_END" does not contain "CLOSE", so a naive check counts a closed
# segment as open.
OPEN_STATUSES = {"NORMAL_OPEN", "PRE_OPEN_START", "PRE_OPEN_END"}


def open_segments(market_info: dict) -> list[str]:
    """Segments currently trading, by explicit status rather than string matching."""
    return sorted(s for s, status in market_info.items() if status in OPEN_STATUSES)


async def _stream(instrument_keys: list[str], mode: str, buffer, redis_client,
                  max_messages: int | None, on_record):
    import websockets

    url = authorize_feed()
    async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "guid": "quantdata",
            "method": "sub",
            "data": {"mode": mode, "instrumentKeys": instrument_keys},
        }))
        print(f"  subscribed: {len(instrument_keys)} instruments, mode={mode}")

        seen = 0
        while max_messages is None or seen < max_messages:
            try:
                raw = await ws.recv()
            except websockets.exceptions.ConnectionClosed as e:
                # Upstox drops idle sockets, which is routine outside market
                # hours. Flush what we have rather than losing the buffer to an
                # exception -- ticks already received are still real ticks.
                print(f"  connection closed by server ({e.__class__.__name__}); "
                      f"flushing {len(buffer.buf)} buffered tick(s)")
                break
            seen += 1
            kind, records, info = decode_feed(raw)
            if kind == "market_info":
                live = open_segments(info)
                print(f"  market_info: {len(info)} segments, "
                      f"{', '.join(live) if live else 'none'} open")
                continue
            for rec in records:
                buffer.add(rec)
                if redis_client:
                    payload = json.dumps(rec)
                    redis_client.hset("ltp", rec["instrument_key"], payload)
                    redis_client.publish("ticks", payload)
                if on_record:
                    on_record(rec)
        buffer.flush()


def stream(instrument_keys: list[str], mode: str = "full",
           max_messages: int | None = None, flush_seconds: int = 60,
           use_redis: bool = True, on_record=None):
    """
    Live ticks: WebSocket -> Redis (hot) -> parquet every flush_seconds (cold).

    Never writes every tick straight to disk -- that melts the disk and makes
    the dashboard crawl. Redis holds the last value for the live view; parquet
    holds the permanent history.

    instrument_keys look like "NSE_INDEX|Nifty 50" or "NSE_EQ|INE002A01018".
    max_messages bounds the run, which is what makes this testable outside
    market hours.
    """
    if mode not in FEED_MODES:
        raise ValueError(f"mode must be one of {FEED_MODES}, got {mode!r}")
    left = _check_token()
    print(f"  token valid for {left:.1f}h (expires 03:30 IST -- the stream dies with it)")

    from connectors.stream import TickBuffer

    redis_client = None
    if use_redis:
        try:
            import redis
            redis_client = redis.Redis(decode_responses=True)
            redis_client.ping()
        except Exception as e:                                # noqa
            print(f"  Redis unavailable ({type(e).__name__}); ticks still go to parquet")
            redis_client = None

    buffer = TickBuffer(flush_seconds=flush_seconds)
    try:
        asyncio.run(_stream(instrument_keys, mode, buffer, redis_client,
                            max_messages, on_record))
    except KeyboardInterrupt:
        print("\n  stopped; flushing buffer")
        buffer.flush()
    return buffer


if __name__ == "__main__":
    print(fii_activity(persist=False).head(10).to_string(index=False))
