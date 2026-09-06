"""
REAL-TIME LAYER -- broker WebSocket -> Redis -> dashboard.

This is the legitimate answer to "I want real-time". You license nothing extra;
your broker relationship already includes the feed.

INDIA: Zerodha Kite Connect
  pip install kiteconnect
  ~Rs.2,000/month, WebSocket, up to 3,000 instruments, covers NSE equity + F&O,
  BSE, MCX commodities (gold, silver, crude, natgas, copper, zinc, cotton).
  Alternatives with the same shape: Dhan, Upstox, Fyers, Angel One SmartAPI.

US/GLOBAL: Interactive Brokers (ib_insync), or Databento / Polygon.io for
  exchange-direct feeds without a broker.

ARCHITECTURE
  WebSocket tick -> Redis (hot, last-value cache + pub/sub)
                 -> ring buffer -> flush to parquet every 60s (cold, permanent)
  Dashboard reads Redis for live, DuckDB/parquet for history.
  Never write every tick straight to disk -- you will melt the disk and the
  dashboard will crawl.
"""
import json
import time
from collections import deque

from core.redis_client import LTP_HASH, TICKS_CHANNEL, redis_client


class TickBuffer:
    """Batches ticks in memory, flushes to parquet periodically."""

    def __init__(self, flush_seconds: int = 60, maxlen: int = 500_000):
        self.buf = deque(maxlen=maxlen)
        self.flush_seconds = flush_seconds
        self._last_flush = time.time()

    def add(self, tick: dict):
        self.buf.append(tick)
        if time.time() - self._last_flush > self.flush_seconds:
            self.flush()

    def flush(self):
        if not self.buf:
            return
        import pandas as pd
        from core.storage import write_table
        df = pd.DataFrame(list(self.buf))
        write_table(df, "stream", "ticks")
        self.buf.clear()
        self._last_flush = time.time()


def kite_stream(api_key: str, access_token: str, instrument_tokens: list[int]):
    """
    Live India ticks -- equities, F&O and MCX commodities in one socket.

    Get instrument_tokens from the Kite instruments dump:
        kite.instruments("MCX")  -> gold, silver, crude, natgas contracts
        kite.instruments("NSE")  -> equities
    """
    from kiteconnect import KiteTicker

    # core.redis_client, not redis.Redis(): the bare constructor hardcodes
    # localhost db 0 and ignores REDIS_URL, which put this feed in the same
    # database as an unrelated project's job queue.
    r = redis_client()
    buf = TickBuffer()

    kws = KiteTicker(api_key, access_token)

    def on_ticks(ws, ticks):
        for t in ticks:
            rec = {
                "token": t["instrument_token"],
                "ltp": t.get("last_price"),
                "volume": t.get("volume_traded"),
                "oi": t.get("oi"),
                "bid": (t.get("depth", {}).get("buy") or [{}])[0].get("price"),
                "ask": (t.get("depth", {}).get("sell") or [{}])[0].get("price"),
                "ts": time.time(),
            }
            buf.add(rec)
            if r:
                r.hset(LTP_HASH, rec["token"], json.dumps(rec))   # hot last-value
                r.publish(TICKS_CHANNEL, json.dumps(rec))         # fan-out

    def on_connect(ws, response):
        ws.subscribe(instrument_tokens)
        ws.set_mode(ws.MODE_FULL, instrument_tokens)  # FULL = depth + OI

    def on_close(ws, code, reason):
        buf.flush()

    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close
    kws.connect(threaded=False)
