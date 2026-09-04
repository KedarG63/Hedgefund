"""
Offline tests for connectors/flights.py.

The credential-spelling tests are the important ones: os.environ is
case-insensitive on Windows and case-SENSITIVE on Linux, so a literal lookup
against the .env spelling in use works on the dev laptop and fails silently
on any server this moves to.
"""
import json

import pytest

import connectors.flights as fl
from connectors.flights import BOXES, STATE_FIELDS, _credential, opensky_states


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.headers = headers or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_disk_no_token(monkeypatch):
    monkeypatch.setattr(fl, "save_raw", lambda *a, **k: None)
    monkeypatch.setattr(fl, "write_table", lambda *a, **k: None)
    monkeypatch.setattr(fl, "_access_token", lambda force=False: "fake-token")
    # A cached token from another test must never leak in.
    fl._TOKEN.update({"value": None, "expires_at": 0.0})


def _fake_fetcher(payload, headers=None):
    class F:
        def get(self, url, **kw):
            return _FakeResponse(payload, headers)
    return lambda *a, **k: F()


def _state(icao="801687", callsign="VTXIC  ", lon=71.4, lat=22.6):
    return [icao, callsign, "India", 1788281760, 1788281760, lon, lat,
            2895.6, False, 120.0, 90.0, 0.0, None, 2900.0, "1000", False, 0, 1]


# ============================================================== credentials
def test_credential_is_case_insensitive(monkeypatch):
    """
    The .env in use spells these `Opensky_clientId` / `Opensky_clientSecret`.
    Both that and the repo-conventional UPPER_SNAKE spelling must resolve.
    """
    monkeypatch.setattr(fl, "load_env", lambda: None)
    monkeypatch.setenv("Opensky_clientId", "abc123")
    assert _credential("OPENSKY_CLIENT_ID", "Opensky_clientId") == "abc123"


def test_credential_prefers_the_canonical_spelling(monkeypatch):
    monkeypatch.setattr(fl, "load_env", lambda: None)
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "canonical")
    monkeypatch.setenv("Opensky_clientId", "legacy")
    assert _credential("OPENSKY_CLIENT_ID", "Opensky_clientId") == "canonical"


def test_credential_raises_naming_every_spelling_tried(monkeypatch):
    monkeypatch.setattr(fl, "load_env", lambda: None)
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("Opensky_clientId", raising=False)
    with pytest.raises(RuntimeError, match="OPENSKY_CLIENT_ID"):
        _credential("OPENSKY_CLIENT_ID", "Opensky_clientId")


# =================================================================== states
def test_parses_state_vectors(monkeypatch):
    monkeypatch.setattr(fl, "Fetcher",
                        _fake_fetcher({"time": 1788281935, "states": [_state()]}))
    df = opensky_states("jamnagar", persist=False)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["icao24"] == "801687"
    assert row["callsign"] == "VTXIC"      # trailing padding stripped
    assert row["box"] == "jamnagar"
    assert row["latitude"] == 22.6


def test_short_state_vector_is_padded_not_zip_truncated(monkeypatch):
    """
    THE IMPORTANT ONE. `category` (index 17) is absent on some responses.
    zip() would silently truncate, but a row that is short in the MIDDLE
    would shift every subsequent column -- turning longitude into latitude.
    Padding keeps positions anchored.
    """
    short = _state()[:15]   # drop spi, position_source, category
    monkeypatch.setattr(fl, "Fetcher", _fake_fetcher({"time": 1, "states": [short]}))
    df = opensky_states("jamnagar", persist=False)
    row = df.iloc[0]
    assert row["longitude"] == 71.4 and row["latitude"] == 22.6
    assert row["category"] is None


def test_empty_box_is_not_an_error(monkeypatch):
    """
    Unlike most connectors here, an empty box is a legitimate observation --
    night, no coverage, no traffic. Raising would turn a real reading into a
    false alarm.
    """
    monkeypatch.setattr(fl, "Fetcher", _fake_fetcher({"time": 1, "states": []}))
    df = opensky_states("jamnagar", persist=False)
    assert df.empty
    assert list(df.columns)[:3] == STATE_FIELDS[:3]


def test_missing_states_key_is_an_error(monkeypatch):
    """A shape change must NOT be read as 'no aircraft'."""
    monkeypatch.setattr(fl, "Fetcher", _fake_fetcher({"time": 1}))
    with pytest.raises(RuntimeError, match="no 'states' key"):
        opensky_states("jamnagar", persist=False)


def test_unknown_box_rejected():
    with pytest.raises(ValueError, match="Unknown box"):
        opensky_states("atlantis", persist=False)


def test_jamnagar_box_covers_the_reliance_refinery():
    """
    Jamnagar is the position-relevant box: RELIANCE's refinery complex plus
    the Sikka/Vadinar crude terminals. Guard the bounds against a careless edit.
    """
    lamin, lomin, lamax, lomax = BOXES["jamnagar"]
    # Jamnagar airport is 22.47N, 70.01E.
    assert lamin < 22.47 < lamax
    assert lomin < 70.01 < lomax
