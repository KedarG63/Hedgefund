"""
Contract tests for the terminal API, against a fixture warehouse.

Hermetic on purpose: these build their own parquet files in tmp_path rather
than reading data/, so they pass on a clean checkout and cannot start passing
or failing because someone ran run_daily.py. That mirrors the per-connector
smoke-test convention -- prove the contract, not the contents.

What is pinned here is what a client depends on and cannot see for itself:
that auth is on by default, that ?as_of= actually filters, that the two wire
formats carry the same table, and that a missing dataset is a 404 rather than
a 500 from a DuckDB Binder Error.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

TOKEN = "test-terminal-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over a two-view fixture warehouse."""
    monkeypatch.setenv("TERMINAL_TOKEN", TOKEN)

    root = tmp_path / "parquet"
    (root / "nse" / "bhavcopy").mkdir(parents=True)
    (root / "derived" / "factor_model").mkdir(parents=True)

    # Two vintages so the as-of bound has something to bite on.
    pd.DataFrame([
        {"TradDt": "2026-08-20", "TckrSymb": "RELIANCE", "SctySrs": "EQ",
         "OpnPric": 1400.0, "HghPric": 1420.0, "LwPric": 1390.0, "ClsPric": 1410.0,
         "PrvsClsgPric": 1395.0, "TtlTradgVol": 1_000_000, "TtlTrfVal": 1.4e9,
         "TtlNbOfTxsExctd": 50_000, "ISIN": "INE002A01018", "FinInstrmNm": "RELIANCE",
         "knowledge_date": "2026-08-20"},
    ]).to_parquet(root / "nse" / "bhavcopy" / "20260820T090000_000001.parquet", index=False)

    pd.DataFrame([
        {"TradDt": "2026-08-21", "TckrSymb": "RELIANCE", "SctySrs": "EQ",
         "OpnPric": 1410.0, "HghPric": 1430.0, "LwPric": 1405.0, "ClsPric": 1425.0,
         "PrvsClsgPric": 1410.0, "TtlTradgVol": 1_100_000, "TtlTrfVal": 1.5e9,
         "TtlNbOfTxsExctd": 52_000, "ISIN": "INE002A01018", "FinInstrmNm": "RELIANCE",
         "knowledge_date": "2026-08-21"},
    ]).to_parquet(root / "nse" / "bhavcopy" / "20260821T090000_000001.parquet", index=False)

    pd.DataFrame([
        {"symbol": "RELIANCE", "momentum_zscore": 1.5, "size_score": 0.9,
         "low_vol_score": -0.2, "composite_score": 0.77, "as_of_date": "2026-08-21",
         "knowledge_date": "2026-08-21"},
        {"symbol": "TCS", "momentum_zscore": -0.4, "size_score": 0.8,
         "low_vol_score": 0.3, "composite_score": -0.31, "as_of_date": "2026-08-21",
         "knowledge_date": "2026-08-21"},
    ]).to_parquet(root / "derived" / "factor_model" / "20260821T090000_000001.parquet",
                  index=False)

    import core.quality as quality
    import core.storage as storage
    monkeypatch.setattr(storage, "PARQUET", root)
    monkeypatch.setattr(quality, "PARQUET", root)
    # RAW as well, and this one is not optional. quality_report() ->
    # fetch_freshness() walks the RAW archive, and startup warms the quality
    # snapshot -- so leaving RAW pointing at the real 4.7 GB / 32k-file archive
    # makes every test in this file pay a ~16s filesystem walk.
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(storage, "RAW", raw)
    monkeypatch.setattr(quality, "RAW", raw)

    # The quality snapshot is a module-level singleton, so a previous test's
    # instance would still be bound to that test's tmp_path.
    import api.background as background
    monkeypatch.setattr(background, "_quality", None)

    from fastapi.testclient import TestClient

    from api.main import app
    with TestClient(app) as c:
        yield c


def rows(response) -> pd.DataFrame:
    return pd.DataFrame(response.json()["rows"])


# ------------------------------------------------------------------------ auth

def test_health_is_open_so_a_probe_needs_no_credential(client):
    r = client.get("/api/ops/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("path", [
    "/api/universe", "/api/watchlist", "/api/macro/snapshot",
    "/api/instrument/RELIANCE/price", "/api/ops/views",
])
def test_every_data_route_requires_a_token(client, path):
    """
    The failure this prevents: exchange terms restrict redistribution, and this
    API serves the whole warehouse. A route added without auth would not be
    visibly different in dev, where everything is localhost anyway.
    """
    assert client.get(path).status_code == 401


def test_a_wrong_token_is_rejected(client):
    assert client.get("/api/watchlist",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401


# ------------------------------------------------------------------- as-of

def test_as_of_bounds_what_the_api_returns(client):
    """Two vintages on disk; as-of before the second must not see it."""
    assert len(rows(client.get("/api/instrument/RELIANCE/price?fmt=json", headers=H))) == 2
    assert len(rows(client.get("/api/instrument/RELIANCE/price?fmt=json&as_of=2026-08-20",
                               headers=H))) == 1
    assert rows(client.get("/api/instrument/RELIANCE/price?fmt=json&as_of=2026-08-19",
                           headers=H)).empty


def test_as_of_rolls_back_derived_signals_not_just_prices(client):
    """The property the migration exists for: a signal computed later must not
    leak into a back-dated view."""
    assert len(rows(client.get("/api/watchlist?fmt=json", headers=H))) == 2
    assert rows(client.get("/api/watchlist?fmt=json&as_of=2026-08-20", headers=H)).empty


@pytest.mark.parametrize("bad", ["garbage", "2026-13-01", "2026-02-30",
                                 "2026-08-21'; DROP TABLE x--"])
def test_a_non_date_as_of_is_a_422_not_a_500(client, bad):
    """as_of reaches SQL, so it is parsed as a date at the boundary. A client
    sending junk should learn that from the status code, and an injection
    attempt should never reach DuckDB at all."""
    r = client.get(f"/api/instrument/RELIANCE/price?as_of={bad}", headers=H)
    assert r.status_code == 422


def test_the_compact_iso_date_form_is_accepted(client):
    """date.fromisoformat() takes YYYYMMDD as well as YYYY-MM-DD on 3.11+, so
    both spellings mean the same day rather than one of them 422-ing."""
    a = client.get("/api/instrument/RELIANCE/price?fmt=json&as_of=20260820", headers=H)
    b = client.get("/api/instrument/RELIANCE/price?fmt=json&as_of=2026-08-20", headers=H)
    assert a.status_code == b.status_code == 200
    assert a.json()["count"] == b.json()["count"] == 1


# ------------------------------------------------------------- wire formats

def test_arrow_and_json_carry_the_same_table(client):
    import pyarrow as pa

    j = rows(client.get("/api/watchlist?fmt=json", headers=H))
    raw = client.get("/api/watchlist", headers=H)
    assert raw.headers["content-type"].startswith("application/vnd.apache.arrow")
    a = pa.ipc.open_stream(io.BytesIO(raw.content)).read_all().to_pandas()
    assert a.shape == j.shape
    assert set(a.columns) == set(j.columns)
    assert sorted(a["symbol"]) == sorted(j["symbol"])


def test_arrow_is_the_default_format(client):
    r = client.get("/api/universe", headers=H)
    assert r.headers["content-type"].startswith("application/vnd.apache.arrow")


def test_json_never_emits_bare_nan(client):
    """json.loads accepts NaN; browsers do not. A null column must round-trip
    as null rather than taking the whole response down in the client."""
    import json

    body = client.get("/api/watchlist?fmt=json", headers=H).text
    assert "NaN" not in body and "Infinity" not in body
    json.loads(body)


# ------------------------------------------------------------------- routes

def test_an_unknown_symbol_is_an_empty_table_not_an_error(client):
    """A command-bar typo must not look like an outage."""
    r = client.get("/api/instrument/NOSUCHSYMBOL/price?fmt=json", headers=H)
    assert r.status_code == 200 and r.json()["count"] == 0


def test_a_dataset_that_was_never_ingested_is_a_404(client):
    """Rather than a 500 from a DuckDB Binder Error, which reads like a bug in
    the API instead of a job that has not run."""
    r = client.get("/api/instrument/RELIANCE/fundamentals", headers=H)
    assert r.status_code == 404
    assert "nse_xbrl_facts" in r.json()["detail"]


def test_ops_views_reports_which_views_can_be_read_point_in_time(client):
    df = rows(client.get("/api/ops/views?fmt=json", headers=H))
    assert set(df["view"]) == {"nse_bhavcopy", "derived_factor_model"}
    assert df["point_in_time"].all()
    assert df.set_index("view").loc["nse_bhavcopy", "natural_key"] == "TradDt, TckrSymb, SctySrs"


def test_reload_views_picks_up_a_dataset_written_after_startup(client, tmp_path):
    """register_views() runs once at startup, so run_daily.py's output is
    invisible until this is called -- that is why the endpoint exists."""
    new = tmp_path / "parquet" / "derived" / "digest"
    new.mkdir(parents=True)
    pd.DataFrame([{"instrument": "RELIANCE", "composite_score": 0.5, "flag": "x",
                   "contributing_signals": ["a"], "as_of_date": "2026-08-21",
                   "tier": "watchlist", "knowledge_date": "2026-08-21"}]
                 ).to_parquet(new / "20260821T100000_000001.parquet", index=False)

    before = len(rows(client.get("/api/ops/views?fmt=json", headers=H)))
    assert client.post("/api/ops/reload-views", headers=H).json()["registered"] == before + 1


# ------------------------------------------------------------- panel routes
# The fixture warehouse holds only nse_bhavcopy and derived_factor_model, so
# these pin how the wave-1 routes behave when their dataset has NOT been
# ingested -- which is the normal state on a fresh checkout and the state most
# likely to be got wrong.

EMPTY_OK = [
    "/api/flows/divergence",       # returns an empty frame: optional dataset
    "/api/climate/enso",
]
NEEDS_DATA = [
    "/api/flows/participants",     # 404s: the panel's whole subject is missing
    "/api/flows/fii-dii",
    "/api/supply-chain/regimes",
    "/api/supply-chain/breaks",
    "/api/supply-chain/transits",
    "/api/climate/monsoon",
    "/api/instrument/NIFTY/options",
]


@pytest.mark.parametrize("path", EMPTY_OK)
def test_optional_datasets_return_an_empty_table_not_an_error(client, path):
    """A panel whose SUPPORTING dataset is absent should still render its
    frame; only a missing primary subject is a 404."""
    r = client.get(f"{path}?fmt=json", headers=H)
    assert r.status_code == 200
    assert r.json()["count"] == 0


@pytest.mark.parametrize("path", NEEDS_DATA)
def test_a_missing_primary_dataset_is_a_404(client, path):
    r = client.get(path, headers=H)
    assert r.status_code == 404, f"{path} returned {r.status_code}"


def test_credit_unions_agencies_and_returns_empty_when_none_ingested(client):
    """Three agencies, one shape. With none present the union is empty rather
    than a Binder Error on the first missing view."""
    r = client.get("/api/credit/actions?fmt=json", headers=H)
    assert r.status_code == 200 and r.json()["count"] == 0


def test_correlation_refuses_a_single_symbol(client):
    """A correlation of one name is not a question. Returning an empty frame
    keeps the panel simple and avoids a pointless scan of a 9M-row table."""
    r = client.get("/api/correlation?symbols=RELIANCE&fmt=json", headers=H)
    assert r.status_code == 200 and r.json()["count"] == 0


def test_every_panel_route_requires_a_token(client):
    for path in EMPTY_OK + NEEDS_DATA + ["/api/correlation?symbols=A,B",
                                         "/api/credit/actions"]:
        assert client.get(path).status_code == 401, path


# ---------------------------------------------------------- quality snapshot

def test_quality_answers_immediately_while_still_computing(client):
    """
    quality_report() walks the whole raw archive (~16s on the real warehouse).
    The status dot polls this, so the route must read a snapshot rather than
    recompute: a cold snapshot reports computing rather than blocking.
    """
    import time

    t = time.time()
    r = client.get("/api/ops/quality?fmt=json", headers=H)
    assert r.status_code == 200
    assert time.time() - t < 5, "the route recomputed instead of reading a snapshot"
    assert r.headers["x-qd-computing"] in ("0", "1")
    assert "x-qd-stale" in r.headers


def test_quality_wait_forces_a_synchronous_recompute(client):
    """The escape hatch for Data Ops and run_daily.py, which want a definitive
    answer and can afford to wait."""
    r = client.get("/api/ops/quality?fmt=json&wait=true", headers=H)
    assert r.status_code == 200
    assert r.headers["x-qd-computing"] == "0"
    assert r.headers["x-qd-age-seconds"] != ""
    assert r.json()["count"] >= 1


def test_a_broken_refresh_surfaces_rather_than_going_quiet():
    """
    Monitoring that silently stops reporting when it breaks is the exact
    failure core.quality exists to catch, so a failed refresh must appear in
    the payload instead of leaving the last good value in place unlabelled.
    """
    import asyncio

    from api.background import Snapshot

    def boom():
        raise RuntimeError("archive unreadable")

    snap = Snapshot(boom, ttl_seconds=60, name="test")
    asyncio.run(snap.refresh())
    state = snap.read()
    assert state["error"] and "archive unreadable" in state["error"]
    assert state["computing"] is False
    assert state["computed_at"] is not None


def test_the_sql_console_refuses_writes(client):
    """The connection is read-only in-memory so a write cannot land anywhere;
    this check exists to return a clear 400 instead of a confusing DuckDB
    error, not as the security boundary."""
    assert client.get("/api/ops/sql?q=DROP TABLE x", headers=H).status_code == 400
    assert client.get("/api/ops/sql?q=SELECT 1 AS x&fmt=json",
                      headers=H).status_code == 200
