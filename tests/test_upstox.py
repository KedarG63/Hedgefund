"""
Offline tests for the Upstox connector and its OAuth helper.

The feed is silent outside market hours, so decode_feed() is deliberately split
from the socket loop: these build real protobuf frames and decode them, which
would otherwise be untestable on any evening or weekend.
"""
import pandas as pd
import pytest

from connectors.upstox import (
    FEED_MODES,
    FII_SEGMENTS,
    OPEN_STATUSES,
    decode_feed,
    open_segments,
)
from connectors.upstox_auth import extract_code, hours_remaining, login_url, write_env

pb = pytest.importorskip("upstox_client.feeder.proto.MarketDataFeedV3_pb2")


# ------------------------------------------------------------------ constants
def test_feed_modes_match_the_documented_set():
    assert FEED_MODES == ("ltpc", "full", "option_greeks", "full_d30")


def test_fii_segments_cover_cash_and_all_four_derivative_books():
    assert "NSE_EQ|CASH" in FII_SEGMENTS
    for book in ("INDEX_FUTURES", "STOCK_FUTURES", "INDEX_OPTIONS", "STOCK_OPTIONS"):
        assert f"NSE_FO|{book}" in FII_SEGMENTS


# --------------------------------------------------------- market open/closed
def test_closing_end_is_not_open():
    """
    The trap: "CLOSING_END" does not contain the substring "CLOSE", so a naive
    `"CLOSE" not in status` test reports a closed segment as open. Observed
    live -- it claimed 4 of 9 segments were open on a Sunday.
    """
    info = {"NSE_EQ": "CLOSING_END", "NSE_FO": "NORMAL_CLOSE",
            "BSE_EQ": "CLOSING_END", "MCX_FO": "NORMAL_CLOSE"}
    assert open_segments(info) == []
    assert any("CLOSE" not in s for s in info.values()), "the naive test would pass here"


def test_open_segments_detects_trading():
    info = {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_CLOSE",
            "BSE_EQ": "PRE_OPEN_START"}
    assert open_segments(info) == ["BSE_EQ", "NSE_EQ"]


def test_open_statuses_are_explicit():
    assert "NORMAL_CLOSE" not in OPEN_STATUSES
    assert "CLOSING_END" not in OPEN_STATUSES
    assert "NORMAL_OPEN" in OPEN_STATUSES


# ------------------------------------------------------------ protobuf decode
def build_market_info(**statuses) -> bytes:
    resp = pb.FeedResponse()
    resp.type = pb.Type.Value("market_info")
    for segment, status in statuses.items():
        resp.marketInfo.segmentStatus[segment] = pb.MarketStatus.Value(status)
    return resp.SerializeToString()


def build_ltpc_feed(key: str, ltp: float, ltq: int = 5, cp: float = 100.0) -> bytes:
    resp = pb.FeedResponse()
    resp.type = pb.Type.Value("live_feed")
    feed = resp.feeds[key]
    feed.ltpc.ltp = ltp
    feed.ltpc.ltq = ltq
    feed.ltpc.cp = cp
    feed.ltpc.ltt = 1787467078487
    return resp.SerializeToString()


def test_decode_market_info():
    kind, records, info = decode_feed(
        build_market_info(NSE_EQ="NORMAL_CLOSE", NSE_FO="NORMAL_OPEN"))
    assert kind == "market_info"
    assert records == []
    assert info == {"NSE_EQ": "NORMAL_CLOSE", "NSE_FO": "NORMAL_OPEN"}


def test_decode_ltpc_tick():
    kind, records, _ = decode_feed(build_ltpc_feed("NSE_INDEX|Nifty 50", 24252.0))
    assert kind == "live_feed"
    assert len(records) == 1
    rec = records[0]
    assert rec["instrument_key"] == "NSE_INDEX|Nifty 50"
    assert rec["ltp"] == pytest.approx(24252.0)
    assert rec["ltq"] == 5
    assert rec["ts"] > 0, "every tick needs a capture timestamp"


def test_decode_full_feed_carries_oi_and_volume():
    """full mode is what makes the feed worth having over ltpc -- OI and volume."""
    resp = pb.FeedResponse()
    resp.type = pb.Type.Value("live_feed")
    m = resp.feeds["NSE_FO|12345"].fullFeed.marketFF
    m.ltpc.ltp = 500.5
    m.oi = 261975
    m.vtt = 1_234_567
    m.atp = 499.2
    kind, records, _ = decode_feed(resp.SerializeToString())

    assert kind == "live_feed" and len(records) == 1
    rec = records[0]
    assert rec["oi"] == 261975 and rec["volume"] == 1_234_567
    assert rec["atp"] == pytest.approx(499.2)


def test_decode_index_full_feed():
    """Index instruments carry indexFF, not marketFF -- a different oneof branch."""
    resp = pb.FeedResponse()
    resp.type = pb.Type.Value("live_feed")
    resp.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF.ltpc.ltp = 24252.0
    kind, records, _ = decode_feed(resp.SerializeToString())
    assert kind == "live_feed"
    assert records[0]["ltp"] == pytest.approx(24252.0)


def test_decode_handles_many_instruments_in_one_frame():
    resp = pb.FeedResponse()
    resp.type = pb.Type.Value("live_feed")
    for i in range(25):
        resp.feeds[f"NSE_EQ|K{i}"].ltpc.ltp = float(i)
    _, records, _ = decode_feed(resp.SerializeToString())
    assert len(records) == 25


# ------------------------------------------------------------------ oauth
def test_login_url_has_every_required_parameter(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "test-key")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1:5000/callback")
    url = login_url()
    assert url.startswith("https://api.upstox.com/v2/login/authorization/dialog?")
    for required in ("client_id=test-key", "response_type=code", "redirect_uri="):
        assert required in url


def test_redirect_uri_is_https(monkeypatch):
    """
    An http:// redirect produces INVALID_APIKEY_URL at the dialog -- documented
    as UDAPI100068 ("client_id and/or redirect_uri incorrect"), which reads like
    a bad key and is not. Verified live: http fails, https returns the login page.
    """
    from core.config import get
    uri = get("UPSTOX_REDIRECT_URI")
    if uri:
        assert uri.startswith("https://"), f"redirect must be https, got {uri!r}"


@pytest.mark.parametrize("pasted,expected", [
    ("https://127.0.0.1:5000/callback?code=ABC123&state=quantdata", "ABC123"),
    ("?code=XYZ", "XYZ"),
    ("BARECODE", "BARECODE"),
    ("  spaced  ", "spaced"),
])
def test_extract_code_accepts_a_url_or_a_bare_code(pasted, expected):
    """People paste the address bar, because the page failed to load."""
    assert extract_code(pasted) == expected


def test_hours_remaining_reads_the_jwt_not_the_api():
    import base64, json
    from datetime import datetime, timedelta, timezone
    exp = int((datetime.now(timezone.utc) + timedelta(hours=5)).timestamp())
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    assert hours_remaining(f"header.{body}.sig") == pytest.approx(5, abs=0.1)


def test_hours_remaining_handles_a_non_jwt():
    assert hours_remaining("not-a-jwt") is None


def test_write_env_replaces_rather_than_appends(tmp_path):
    """
    A duplicate UPSTOX_ACCESS_TOKEN line would be shadowed by whichever the
    parser reads last -- a token that looks written and is not used.
    """
    env = tmp_path / ".env"
    env.write_text("A=1\nUPSTOX_ACCESS_TOKEN=old\nB=2\n", encoding="utf-8")
    write_env("new", env)
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines.count("UPSTOX_ACCESS_TOKEN=new") == 1
    assert not any(line.endswith("=old") for line in lines)
    assert "A=1" in lines and "B=2" in lines, "other settings must survive"


def test_write_env_adds_the_key_when_absent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    write_env("fresh", env)
    assert "UPSTOX_ACCESS_TOKEN=fresh" in env.read_text(encoding="utf-8").splitlines()
