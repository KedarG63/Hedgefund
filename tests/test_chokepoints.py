"""
Offline tests for connectors/chokepoints.py.

Fixtures are shaped exactly like real PortWatch ArcGIS responses (captured
2026-09-01/02): the same field list, the same paging envelope, and BOTH date
representations the layer actually emits.
"""
import json

import pandas as pd
import pytest

import connectors.chokepoints as cp
from connectors.chokepoints import (
    EXPECTED_COLUMNS,
    INDIA_RELEVANT,
    _check_schema,
    _coerce_date,
    portwatch_chokepoints,
)


def _attrs(date_value, portname="Strait of Hormuz", n_total=3, n_tanker=1):
    """One feature's attributes, with every column the parser depends on."""
    return {
        "date": date_value, "year": 2026, "month": 8, "day": 23,
        "portid": "chokepoint6", "portname": portname,
        "n_container": 0, "n_dry_bulk": 2, "n_general_cargo": 0, "n_roro": 0,
        "n_tanker": n_tanker, "n_cargo": 2, "n_total": n_total,
        "capacity_container": 0, "capacity_dry_bulk": 32472,
        "capacity_general_cargo": 0, "capacity_roro": 0, "capacity_tanker": 0,
        "capacity_cargo": 32472, "capacity": 32472, "ObjectId": 16853,
    }


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


class _FakeFetcher:
    """Returns queued payloads in order, recording the params it was called with."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(kw.get("params", {}))
        return _FakeResponse(self._payloads.pop(0))


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    """Never touch the archive or warehouse from a unit test."""
    monkeypatch.setattr(cp, "save_raw", lambda *a, **k: None)
    monkeypatch.setattr(cp, "write_table", lambda *a, **k: None)


# ============================================================ date coercion
def test_coerce_date_handles_iso_strings():
    """
    outFields naming `date` explicitly returns "YYYY-MM-DD".
    """
    out = _coerce_date(pd.Series(["2026-08-23", "2026-08-22"]))
    assert list(out) == ["2026-08-23", "2026-08-22"]


def test_coerce_date_handles_epoch_milliseconds():
    """
    outFields=* returns the SAME field as epoch ms. This is undocumented and
    was found by running both queries; a parser that assumed one representation
    silently produced 1970 dates or crashed on str/int division.
    """
    # 1787443200000 == 2026-08-23T00:00:00Z, 1787356800000 == 2026-08-22.
    # Computed, not eyeballed -- an earlier draft of this test hardcoded a
    # value four days off and "failed" against a parser that was correct.
    out = _coerce_date(pd.Series([1787443200000, 1787356800000]))
    assert list(out) == ["2026-08-23", "2026-08-22"]


def test_coerce_date_mixed_falls_back_to_string_parsing():
    out = _coerce_date(pd.Series(["2026-08-23", None]))
    assert out.iloc[0] == "2026-08-23"


# ================================================================== schema
def test_check_schema_passes_when_extra_columns_appear():
    """New columns are harmless -- only a MISSING one is drift."""
    df = pd.DataFrame([{**_attrs("2026-08-23"), "brand_new_field": 1}])
    _check_schema(df, EXPECTED_COLUMNS, "portwatch", "chokepoints")


def test_check_schema_raises_on_missing_column():
    attrs = _attrs("2026-08-23")
    del attrs["n_tanker"]
    df = pd.DataFrame([attrs])
    with pytest.raises(RuntimeError, match="schema drift"):
        _check_schema(df, EXPECTED_COLUMNS, "portwatch", "chokepoints")


# =================================================================== paging
def test_follows_exceeded_transfer_limit_across_pages(monkeypatch):
    page1 = {
        "fields": [{"name": "date"}],
        "features": [{"attributes": _attrs("2026-08-2%d" % i)} for i in range(1, 4)],
        "exceededTransferLimit": True,
    }
    page2 = {
        "fields": [{"name": "date"}],
        "features": [{"attributes": _attrs("2026-08-24")}],
    }
    fake = _FakeFetcher([page1, page2])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)

    df = portwatch_chokepoints(persist=False)
    assert len(df) == 4
    # Second page must ask for the next offset, not repeat the first.
    assert fake.calls[0]["resultOffset"] == 0
    assert fake.calls[1]["resultOffset"] == 3


def test_stops_when_transfer_limit_not_exceeded(monkeypatch):
    page = {"features": [{"attributes": _attrs("2026-08-23")}]}
    fake = _FakeFetcher([page])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)
    portwatch_chokepoints(persist=False)
    assert len(fake.calls) == 1


# ============================================================ error handling
def test_arcgis_error_inside_a_200_is_raised(monkeypatch):
    """
    THE IMPORTANT ONE. ArcGIS reports quota rejection INSIDE a 200 body. A
    caller that only checks the HTTP status treats it as an empty result and
    archives a valid-looking file with no data -- silent loss, which is
    exactly what rule 1 exists to prevent.
    """
    payload = {"error": {"code": 429, "message": "Unable to perform query. Too many requests.",
                         "details": ["API calls quota exceeded (6003 request units)!"]}}
    fake = _FakeFetcher([payload])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)

    with pytest.raises(RuntimeError, match="429"):
        portwatch_chokepoints(persist=False)


def test_empty_result_raises_rather_than_returning_empty(monkeypatch):
    """Connector contract rule 4: raise on failure, never return empty."""
    fake = _FakeFetcher([{"features": []}])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)
    with pytest.raises(RuntimeError, match="no rows"):
        portwatch_chokepoints(persist=False)


# ================================================================ filtering
def test_since_uses_a_date_literal_not_a_string_compare(monkeypatch):
    """
    `date` is a real date column. A string comparison silently matches
    nothing, which reads as "no data" instead of "wrong query".
    """
    fake = _FakeFetcher([{"features": [{"attributes": _attrs("2026-08-23")}]}])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)
    portwatch_chokepoints(since="2026-01-01", persist=False)
    assert "DATE '2026-01-01'" in fake.calls[0]["where"]


def test_portname_filter_escapes_single_quotes(monkeypatch):
    """A quote in a name must not terminate the SQL string literal."""
    fake = _FakeFetcher([{"features": [{"attributes": _attrs("2026-08-23")}]}])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)
    portwatch_chokepoints(portnames=("O'Hara Strait",), persist=False)
    assert "'O''Hara Strait'" in fake.calls[0]["where"]


def test_india_relevant_includes_the_substitute_route():
    """
    Cape of Good Hope is not an India chokepoint -- it is the SUBSTITUTE route
    that absorbs Suez/Bab el-Mandeb traffic. Dropping it would make the
    reroute mass-balance in analytics/supply_chain.py uncomputable.
    """
    assert "Cape of Good Hope" in INDIA_RELEVANT
    assert "Strait of Hormuz" in INDIA_RELEVANT


def test_objectid_is_dropped(monkeypatch):
    """ObjectId is an ArcGIS row id -- it is not data and changes across pulls."""
    fake = _FakeFetcher([{"features": [{"attributes": _attrs("2026-08-23")}]}])
    monkeypatch.setattr(cp, "Fetcher", lambda *a, **k: fake)
    df = portwatch_chokepoints(persist=False)
    assert "ObjectId" not in df.columns
