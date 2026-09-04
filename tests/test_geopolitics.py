"""
Offline tests for connectors/geopolitics.py.

The GDELT fixture is a real 58-column export row (from 20260831.export.CSV)
with its SQLDATE/DATEADDED mismatch preserved, because that mismatch is the
single most consequential trap in this feed.
"""
import io
import zipfile

import pandas as pd
import pytest

import connectors.geopolitics as gp
from connectors.geopolitics import (
    CONFLICT_ROOT_CODES,
    ENERGY_COUNTRIES,
    ENERGY_COUNTRIES_CAMEO,
    ENERGY_COUNTRIES_FIPS,
    GDELT_COLUMNS,
    gdacs_alerts,
    gdelt_daily_events,
)


def _gdelt_row(event_id="1320656121", sqldate="20250831", dateadded="20260831",
               a1="IRN", a2="USA", geo="IR", root="19", quad="4",
               goldstein="-10.0", tone="-4.81"):
    """One 58-column GDELT export row."""
    row = [""] * 58
    row[0] = event_id
    row[1] = sqldate
    row[2] = sqldate[:6]
    row[3] = sqldate[:4]
    row[7] = a1          # Actor1CountryCode
    row[17] = a2         # Actor2CountryCode
    row[26] = root + "0"  # EventCode
    row[28] = root       # EventRootCode
    row[29] = quad       # QuadClass
    row[30] = goldstein
    row[31] = "10"       # NumMentions
    row[32] = "5"        # NumSources
    row[33] = "10"       # NumArticles
    row[34] = tone
    row[51] = geo        # ActionGeo_CountryCode
    row[56] = dateadded
    row[57] = "http://example.com/story"
    return "\t".join(row)


def _gdelt_zip(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20260831.export.CSV", "\n".join(rows) + "\n")
    return buf.getvalue()


GDACS_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:gdacs="http://www.gdacs.org"
     xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
  <channel>
    <item>
      <title>Orange flood alert in India</title>
      <link>https://www.gdacs.org/report.aspx?eventid=1</link>
      <pubDate>Sun, 09 Aug 2026 01:00:00 GMT</pubDate>
      <gdacs:eventtype>FL</gdacs:eventtype>
      <gdacs:alertlevel>Orange</gdacs:alertlevel>
      <gdacs:severity>Severe</gdacs:severity>
      <gdacs:country>India</gdacs:country>
      <gdacs:fromdate>Sun, 09 Aug 2026 01:00:00 GMT</gdacs:fromdate>
      <gdacs:todate>Tue, 11 Aug 2026 01:00:00 GMT</gdacs:todate>
      <gdacs:population>120000</gdacs:population>
      <geo:lat>22.5</geo:lat>
      <geo:long>88.3</geo:long>
    </item>
  </channel>
</rss>
"""


class _FakeResponse:
    def __init__(self, content):
        self.content = content


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    monkeypatch.setattr(gp, "save_raw", lambda *a, **k: None)
    monkeypatch.setattr(gp, "write_table", lambda *a, **k: None)


def _fake_fetcher(content):
    class F:
        def get(self, url, **kw):
            return _FakeResponse(content)
    return lambda *a, **k: F()


# =================================================================== GDELT
def test_gdelt_column_count_is_58():
    """
    Verified by downloading a real export and counting, not from the codebook.
    A drift here shifts every column silently.
    """
    assert len(GDELT_COLUMNS) == 58


def test_gdelt_keeps_both_dates_separately(monkeypatch):
    """
    THE IMPORTANT ONE. The daily file is keyed on DATEADDED (ingestion), not
    SQLDATE (occurrence). Measured on real data, 2.2% of rows differ, trailing
    back a decade. Collapsing them backdates retrospective coverage into
    today's signal.
    """
    monkeypatch.setattr(gp, "Fetcher",
                        _fake_fetcher(_gdelt_zip([_gdelt_row(sqldate="20250831",
                                                             dateadded="20260831")])))
    df = gdelt_daily_events("2026-08-31", persist=False)
    assert df.iloc[0]["event_date"] == "2025-08-31"
    assert df.iloc[0]["ingested_date"] == "2026-08-31"


def test_gdelt_universe_filter_matches_on_any_of_three_fields(monkeypatch):
    """
    An Iranian action in Yemeni waters must survive whichever field carries
    the code -- actor 1, actor 2, or the action geography.
    """
    rows = [
        _gdelt_row(event_id="1", a1="IRN", a2="", geo=""),    # actor1
        _gdelt_row(event_id="2", a1="", a2="SAU", geo=""),    # actor2
        _gdelt_row(event_id="3", a1="", a2="", geo="IN"),     # action geo
        _gdelt_row(event_id="4", a1="BRA", a2="ARG", geo="BR"),  # out of universe
    ]
    monkeypatch.setattr(gp, "Fetcher", _fake_fetcher(_gdelt_zip(rows)))
    df = gdelt_daily_events("2026-08-31", persist=False)
    assert set(df["GlobalEventID"]) == {"1", "2", "3"}


def test_gdelt_conflict_only_filters_to_root_codes(monkeypatch):
    rows = [
        _gdelt_row(event_id="1", root="19"),  # material conflict
        _gdelt_row(event_id="2", root="04"),  # consult -- cooperative
    ]
    monkeypatch.setattr(gp, "Fetcher", _fake_fetcher(_gdelt_zip(rows)))
    df = gdelt_daily_events("2026-08-31", conflict_only=True, persist=False)
    assert set(df["GlobalEventID"]) == {"1"}
    assert "19" in CONFLICT_ROOT_CODES and "04" not in CONFLICT_ROOT_CODES


def test_gdelt_raises_when_universe_filter_empties_the_frame(monkeypatch):
    """Contract rule 4: raise on failure, never return empty."""
    monkeypatch.setattr(gp, "Fetcher",
                        _fake_fetcher(_gdelt_zip([_gdelt_row(a1="BRA", a2="ARG", geo="BR")])))
    with pytest.raises(RuntimeError, match="no rows survived"):
        gdelt_daily_events("2026-08-31", persist=False)


def test_gdelt_quad_class_is_labelled(monkeypatch):
    monkeypatch.setattr(gp, "Fetcher", _fake_fetcher(_gdelt_zip([_gdelt_row(quad="4")])))
    df = gdelt_daily_events("2026-08-31", persist=False)
    assert df.iloc[0]["quad_class_label"] == "material_conflict"


def test_energy_universe_covers_gulf_transit_and_india():
    """The universe is the point of the filter -- guard it against edits."""
    for code in ("IND", "IRN", "SAU", "YEM", "EGY"):
        assert code in ENERGY_COUNTRIES_CAMEO


def test_actor_and_geo_universes_use_different_code_systems():
    """
    REGRESSION. Actors are CAMEO 3-letter, action geography is FIPS 10-4
    2-letter. One set for both silently kills the geography term while still
    returning plenty of actor-matched rows, so live output looks fine.
    """
    assert all(len(c) == 3 for c in ENERGY_COUNTRIES_CAMEO)
    assert all(len(c) == 2 for c in ENERGY_COUNTRIES_FIPS)
    # FIPS is not ISO: these four are the ones that actually bite.
    for fips in ("IZ", "MU", "RS", "CH"):   # Iraq, Oman, Russia, China
        assert fips in ENERGY_COUNTRIES_FIPS


# =================================================================== GDACS
def test_gdacs_parses_namespaced_fields(monkeypatch):
    monkeypatch.setattr(gp, "Fetcher", _fake_fetcher(GDACS_FIXTURE))
    df = gdacs_alerts(persist=False)
    row = df.iloc[0]
    assert row["event_type"] == "FL"
    assert row["alert_level"] == "Orange"
    assert row["country"] == "India"
    assert row["lat"] == 22.5 and row["lon"] == 88.3


def test_gdacs_raises_if_namespace_changes(monkeypatch):
    """
    If gdacs: elements vanish, every alert silently becomes null rather than
    the feed failing -- which looks like "no disasters anywhere".
    """
    broken = GDACS_FIXTURE.replace(b"gdacs:eventtype", b"gdacs:kindofthing")
    monkeypatch.setattr(gp, "Fetcher", _fake_fetcher(broken))
    with pytest.raises(RuntimeError, match="namespace or"):
        gdacs_alerts(persist=False)


def test_gdacs_raises_on_empty_feed(monkeypatch):
    empty = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    monkeypatch.setattr(gp, "Fetcher", _fake_fetcher(empty))
    with pytest.raises(RuntimeError, match="no <item>"):
        gdacs_alerts(persist=False)
