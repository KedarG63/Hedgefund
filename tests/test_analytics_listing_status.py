"""
Offline tests for the NSE/BSE listing-status matcher.

normalize_name/is_listed/tag_listing_status are pure functions, tested
directly. listed_company_names() needs a real warehouse, so it uses the same
throwaway-tmp_path fixture as test_quality.py.
"""
import importlib

import pandas as pd
import pytest


# ------------------------------------------------------------------ normalize_name
def test_normalize_name_uppercases_and_collapses_whitespace():
    from analytics.listing_status import normalize_name
    assert normalize_name("  tata power   company limited ") == "TATA POWER COMPANY LIMITED"


def test_normalize_name_strips_leading_the():
    """
    The real bug this module was built to fix: CRISIL's own "The Tata Power
    Company Limited" must match nse_shareholding_pattern's "Tata Power
    Company Limited" (no "The").
    """
    from analytics.listing_status import normalize_name
    assert normalize_name("The Tata Power Company Limited") == normalize_name("Tata Power Company Limited")


def test_normalize_name_strips_parenthetical_suffix():
    """ICRA carries "(Erstwhile ...)"/"(Formerly known as ...)" suffixes that
    a listing source's current name would never carry."""
    from analytics.listing_status import normalize_name
    assert normalize_name("Subam Papers Limited (Erstwhile Subam Papers Private Limited)") \
        == normalize_name("Subam Papers Limited")


def test_normalize_name_normalizes_ampersand():
    from analytics.listing_status import normalize_name
    assert normalize_name("Bansal & Sons Limited") == normalize_name("Bansal and Sons Limited")


def test_normalize_name_strips_punctuation():
    from analytics.listing_status import normalize_name
    assert normalize_name("Clio Infotech Ltd.") == normalize_name("Clio Infotech Ltd")


def test_normalize_name_none_is_none():
    from analytics.listing_status import normalize_name
    assert normalize_name(None) is None


def test_normalize_name_empty_after_stripping_is_none():
    from analytics.listing_status import normalize_name
    assert normalize_name("   ") is None


# ------------------------------------------------------------------ is_listed
def test_is_listed_true_for_a_normalized_match():
    from analytics.listing_status import is_listed
    listed = {"RELIANCE INDUSTRIES LIMITED"}
    assert is_listed("Reliance Industries Limited", listed)


def test_is_listed_false_for_an_unlisted_subsidiary():
    """
    JM Financial Credit Solutions Limited is a real, verified example: an
    unlisted NBFC whose only listed relative is its parent, JM Financial
    Limited. A "Limited" name must NOT be treated as a listing signal.
    """
    from analytics.listing_status import is_listed
    listed = {"JM FINANCIAL LIMITED"}
    assert not is_listed("JM Financial Credit Solutions Limited", listed)


def test_is_listed_false_for_none():
    from analytics.listing_status import is_listed
    assert not is_listed(None, {"ANYTHING"})


# ------------------------------------------------------------------ tag_listing_status
def test_tag_listing_status_labels_correctly():
    from analytics.listing_status import tag_listing_status
    df = pd.DataFrame({"company_name": ["Reliance Industries Limited", "Some Obscure Pvt Ltd"]})
    listed = {"RELIANCE INDUSTRIES LIMITED"}
    out = tag_listing_status(df, "company_name", listed)
    assert out["listing_status"].tolist() == ["Public (listed)", "Private/unlisted"]


def test_tag_listing_status_does_not_mutate_the_input():
    from analytics.listing_status import tag_listing_status
    df = pd.DataFrame({"company_name": ["A"]})
    tag_listing_status(df, "company_name", set())
    assert "listing_status" not in df.columns


# ------------------------------------------------------------------ listed_company_names (live warehouse)
@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTDATA_ROOT", str(tmp_path))
    import core.storage as storage
    importlib.reload(storage)
    import analytics.listing_status as listing_status
    importlib.reload(listing_status)
    yield storage, listing_status
    importlib.reload(storage)
    importlib.reload(listing_status)


def test_listed_company_names_unions_all_three_sources(s):
    storage, listing_status = s
    storage.write_table(pd.DataFrame({"Issuer_Name": ["Alpha Limited"]}), "bse", "scrip_master")
    storage.write_table(pd.DataFrame({"name": ["Beta Limited"]}), "nse", "shareholding_pattern")
    storage.write_table(pd.DataFrame({"companyName": ["Gamma Limited"]}), "nse", "results_Quarterly")

    names = listing_status.listed_company_names()
    assert names == {"ALPHA LIMITED", "BETA LIMITED", "GAMMA LIMITED"}


def test_listed_company_names_skips_a_source_not_yet_ingested(s):
    """Partial warehouse coverage is expected on a fresh pipeline, not an error."""
    storage, listing_status = s
    storage.write_table(pd.DataFrame({"Issuer_Name": ["Alpha Limited"]}), "bse", "scrip_master")
    names = listing_status.listed_company_names()
    assert names == {"ALPHA LIMITED"}


def test_listed_company_names_empty_warehouse_returns_empty_set(s):
    _, listing_status = s
    assert listing_status.listed_company_names() == set()
