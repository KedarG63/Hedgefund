"""
Offline tests for the NSE/BSE connector's pure logic.

No network: these pin the schema-drift guard and the endpoint-shape decisions
that live testing on 2026-08-22 got wrong on the first try (participant_oi's
trailing-whitespace columns, BSE's strCat=-1 refusal for market-wide queries)
so those cannot silently regress.

    python -m pytest tests/test_nse_bse.py -v
"""
import pandas as pd
import pytest

from connectors.nse_bse import (
    BSE_ANNOUNCEMENT_CATEGORIES,
    _check_schema,
    bse_announcements,
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
