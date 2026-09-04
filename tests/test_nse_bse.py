"""
Offline tests for the NSE/BSE connector's pure logic.

No network: these pin the schema-drift guard and the endpoint-shape decisions
that live testing on 2026-08-22 got wrong on the first try (participant_oi's
trailing-whitespace columns, BSE's strCat=-1 refusal for market-wide queries)
so those cannot silently regress.

    python -m pytest tests/test_nse_bse.py -v
"""
import json

import pandas as pd
import pytest

from connectors.nse_bse import (
    BSE_ANNOUNCEMENT_CATEGORIES,
    _check_schema,
    bse_announcements,
    bse_quarterly_results_summary,
)


def test_check_schema_passes_when_all_expected_columns_present():
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    _check_schema(df, {"a", "b"}, "src", "ds")  # extra column "c" is fine


def test_check_schema_raises_on_missing_column():
    """
    REGRESSION: nse_participant_oi's raw CSV ships columns with trailing
    whitespace ("Total Long Contracts      "), which silently fails a naive
    `"Total Long Contracts" in df.columns` check. The connector strips column
    names before this check runs -- this test locks in that the check itself
    is strict enough to have caught it before the strip was added.
    """
    df = pd.DataFrame({"Client Type": ["FII"], "Total Long Contracts      ": [1]})
    with pytest.raises(RuntimeError, match="schema drift"):
        _check_schema(df, {"Client Type", "Total Long Contracts"}, "nse", "participant_oi")


def test_check_schema_error_names_the_missing_column():
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(RuntimeError, match=r"\['b'\]"):
        _check_schema(df, {"a", "b"}, "src", "ds")


def test_bse_announcement_categories_are_never_the_all_sentinel():
    """
    REGRESSION: strCat=-1 (BSE's "all categories" sentinel) returns {} rather
    than the full market feed once no scrip narrows the query -- confirmed
    live, not a param-formatting bug. The default sweep list must never
    include "-1", or a silent no-op replaces real data.
    """
    assert "-1" not in BSE_ANNOUNCEMENT_CATEGORIES
    assert len(BSE_ANNOUNCEMENT_CATEGORIES) > 0


def test_bse_announcements_scrip_query_uses_all_categories_sentinel(monkeypatch):
    """
    Per-company mode (scrip given) should use strCat=-1 in a single call --
    that combination IS verified to work -- rather than sweeping the category
    list, which would be redundant and multiply the request count for no gain.
    """
    seen_cats = []

    class FakeResp:
        content = b"{}"

        def json(self):
            return {"Table": [{"NEWSSUB": "x"}]}

    class FakeSession:
        def get(self, url, params=None):
            seen_cats.append(params["strCat"])
            return FakeResp()

    monkeypatch.setattr("connectors.nse_bse.bse_session", lambda: FakeSession())
    monkeypatch.setattr("connectors.nse_bse.save_raw", lambda *a, **k: None)
    monkeypatch.setattr("connectors.nse_bse.write_table", lambda *a, **k: None)

    from datetime import date
    bse_announcements(date(2026, 8, 1), date(2026, 8, 22), scrip="500325")

    assert seen_cats == ["-1"]


def test_bse_quarterly_results_summary_reshapes_and_parses_commas(monkeypatch):
    """
    BSE double-encodes this payload (a JSON string containing another JSON
    string) and formats every rupee figure with Indian-style comma grouping
    ("1,66,013.00") -- both must survive the reshape to tidy long format.
    """
    # A real (trimmed) shape: BSE's response body, when passed through
    # r.json(), is itself a JSON-encoded STRING -- str(...) below mimics that.
    outer = json.dumps({
        "col1": "(in Cr.)", "col2": "Jun-26", "col3": "Mar-26", "col4": "FY25-26",
        "resultinCr": [
            {"title": "Revenue", "v1": "1,66,013.00", "v2": "1,46,385.00", "v3": "5,24,105.00"},
            {"title": "OPM %", "v1": "14.24", "v2": "10.46", "v3": "14.90"},
        ],
    })

    class FakeResp:
        content = b"whatever"

        def json(self):
            return outer

    monkeypatch.setattr("connectors.nse_bse.bse_session", lambda: type(
        "F", (), {"get": staticmethod(lambda url, params=None: FakeResp())})())
    monkeypatch.setattr("connectors.nse_bse.save_raw", lambda *a, **k: None)
    monkeypatch.setattr("connectors.nse_bse.write_table", lambda *a, **k: None)

    df = bse_quarterly_results_summary("500325")

    revenue_jun = df[(df["concept"] == "Revenue") & (df["period"] == "Jun-26")]
    assert revenue_jun["value"].iloc[0] == 166013.00
    opm_fy = df[(df["concept"] == "OPM %") & (df["period"] == "FY25-26")]
    assert opm_fy["value"].iloc[0] == 14.90
    assert len(df) == 6  # 2 concepts x 3 periods


def test_bse_quarterly_results_summary_raises_on_empty_result(monkeypatch):
    class FakeResp:
        content = b"whatever"

        def json(self):
            return json.dumps({"col2": "Jun-26", "col3": "Mar-26", "col4": "FY25-26", "resultinCr": []})

    monkeypatch.setattr("connectors.nse_bse.bse_session", lambda: type(
        "F", (), {"get": staticmethod(lambda url, params=None: FakeResp())})())
    monkeypatch.setattr("connectors.nse_bse.save_raw", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="no resultinCr rows"):
        bse_quarterly_results_summary("999999")
