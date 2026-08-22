"""
Offline tests for the companyfacts bulk connector.

Restatement detection gets the attention: it is the piece that decides whether a
backtest reads numbers that existed at the time, and it fails silently if wrong.
"""
import pandas as pd
import pytest

from connectors.sec_companyfacts import (
    non_core_taxonomies,
    facts_to_frame,
    member_name,
    restatements,
)


SAMPLE = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "units": {"USD": [
                    # same period, reported twice with different values
                    {"start": "2025-01-01", "end": "2025-03-31", "val": 100.0,
                     "fy": 2025, "fp": "Q1", "form": "10-Q",
                     "filed": "2025-05-01", "accn": "A-1", "frame": "CY2025Q1"},
                    {"start": "2025-01-01", "end": "2025-03-31", "val": 110.0,
                     "fy": 2025, "fp": "Q1", "form": "10-Q/A",
                     "filed": "2025-08-01", "accn": "A-2", "frame": "CY2025Q1"},
                    # a different period, reported once
                    {"start": "2025-04-01", "end": "2025-06-30", "val": 200.0,
                     "fy": 2025, "fp": "Q2", "form": "10-Q",
                     "filed": "2025-08-01", "accn": "A-3", "frame": "CY2025Q2"},
                ]},
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "label": "Shares Outstanding",
                "units": {"shares": [
                    {"end": "2025-03-31", "val": 1000.0, "form": "10-Q",
                     "filed": "2025-05-01", "accn": "A-1"},
                ]},
            },
        },
        "aapl": {
            "SegmentRevenueServices": {
                "label": "Services Revenue",
                "units": {"USD": [
                    {"start": "2025-01-01", "end": "2025-03-31", "val": 25.0,
                     "form": "10-Q", "filed": "2025-05-01", "accn": "A-1"},
                ]},
            },
        },
    },
}


# ------------------------------------------------------------------ plumbing
@pytest.mark.parametrize("cik,expected", [
    (320193, "CIK0000320193.json"),
    ("320193", "CIK0000320193.json"),
    (1067983, "CIK0001067983.json"),
])
def test_member_name_is_zero_padded(cik, expected):
    assert member_name(cik) == expected


# ------------------------------------------------------------------ flatten
def test_facts_to_frame_flattens_all_taxonomies():
    df = facts_to_frame(SAMPLE)
    assert len(df) == 5
    assert set(df["taxonomy"]) == {"us-gaap", "dei", "aapl"}


def test_filed_and_accn_are_preserved():
    """Without these the data is not point-in-time and revisions are invisible."""
    df = facts_to_frame(SAMPLE)
    assert df["filed"].notna().all()
    assert df["accn"].notna().all()


def test_frame_label_is_kept():
    df = facts_to_frame(SAMPLE)
    assert "CY2025Q1" in set(df["frame"].dropna())


def test_facts_to_frame_handles_empty():
    assert facts_to_frame({}).empty
    assert facts_to_frame({"facts": {}}).empty


# ------------------------------------------------------- non-core taxonomies
def test_non_core_excludes_us_gaap_and_dei():
    tags = non_core_taxonomies(SAMPLE)
    assert "us-gaap" not in set(tags["taxonomy"])
    assert "dei" not in set(tags["taxonomy"])


def test_non_core_empty_when_only_core():
    only_core = {"cik": 1, "entityName": "X",
                 "facts": {"us-gaap": SAMPLE["facts"]["us-gaap"],
                           "dei": SAMPLE["facts"]["dei"]}}
    assert non_core_taxonomies(only_core).empty


def test_standard_taxonomy_list_matches_what_the_archive_contains():
    """
    Sampling 400 companies in the bulk archive yields exactly these ten
    prefixes, all SEC-standard. companyfacts carries NO company-defined
    extension tags -- a claim often made and not true. If this list needs
    widening, re-sample rather than guessing.
    """
    from connectors.sec_companyfacts import CORE_TAXONOMIES, STANDARD_TAXONOMIES
    assert set(CORE_TAXONOMIES) == {"us-gaap", "dei"}
    assert set(STANDARD_TAXONOMIES) == {
        "us-gaap", "dei", "srt", "ifrs-full", "invest",
        "ecd", "ffd", "cef", "vip", "spac"}


# ------------------------------------------------------------- restatements
def test_restatement_detected_with_both_vintages():
    out = restatements(facts_to_frame(SAMPLE))
    assert len(out) == 1
    row = out.iloc[0]
    assert row["concept"] == "Revenues"
    assert row["first_value"] == 100.0 and row["latest_value"] == 110.0
    assert row["first_filed"] == "2025-05-01" and row["latest_filed"] == "2025-08-01"
    assert row["n_versions"] == 2


def test_restatement_change_is_computed():
    row = restatements(facts_to_frame(SAMPLE)).iloc[0]
    assert row["abs_change"] == pytest.approx(10.0)
    assert row["pct_change"] == pytest.approx(10.0)


def test_unrevised_periods_are_excluded():
    """Q2 was reported once; it must not appear as a revision."""
    out = restatements(facts_to_frame(SAMPLE))
    assert "2025-04-01" not in set(out["start"])


def test_min_pct_change_filters():
    frame = facts_to_frame(SAMPLE)
    assert restatements(frame, min_pct_change=50).empty
    assert len(restatements(frame, min_pct_change=5)) == 1


def test_restatements_empty_when_nothing_revised():
    single = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {"Assets": {
        "label": "Assets", "units": {"USD": [
            {"end": "2025-03-31", "val": 5.0, "form": "10-Q",
             "filed": "2025-05-01", "accn": "A-1"}]}}}}}
    assert restatements(facts_to_frame(single)).empty


def test_restatement_ignores_repeats_of_the_same_value():
    """
    A company restating the SAME number in a later filing is not a revision --
    only a changed value is. Otherwise every carried-forward comparative would
    read as a restatement.
    """
    repeated = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {"Assets": {
        "label": "Assets", "units": {"USD": [
            {"end": "2025-03-31", "val": 5.0, "form": "10-Q",
             "filed": "2025-05-01", "accn": "A-1"},
            {"end": "2025-03-31", "val": 5.0, "form": "10-K",
             "filed": "2026-02-01", "accn": "A-9"}]}}}}}
    assert restatements(facts_to_frame(repeated)).empty


def test_scale_change_is_flagged_not_reported_as_a_restatement():
    """
    Tesla reported 2016 debt as 7,511,760 in its 2017 filing and 7,511,760,000 in
    its 2018 one -- a switch from thousands to dollars, exactly 1000x. That is a
    units change, not a revision, and it dominates any list sorted by magnitude.
    """
    units = {"cik": 1318605, "entityName": "Tesla", "facts": {"us-gaap": {
        "LongTermDebt": {"label": "Long Term Debt", "units": {"USD": [
            {"end": "2016-12-31", "val": 5892016.0, "form": "10-K",
             "filed": "2017-03-01", "accn": "T-1"},
            {"end": "2016-12-31", "val": 5892016000.0, "form": "10-K",
             "filed": "2018-02-23", "accn": "T-2"}]}}}}}
    out = restatements(facts_to_frame(units))
    assert len(out) == 1
    assert bool(out.iloc[0]["looks_like_scale_change"]) is True
    assert out.iloc[0]["value_ratio"] == pytest.approx(1000.0)


def test_genuine_restatement_is_not_flagged_as_a_scale_change():
    out = restatements(facts_to_frame(SAMPLE))
    assert bool(out.iloc[0]["looks_like_scale_change"]) is False


def test_numeric_columns_are_float_not_object():
    """
    pd.NA in an arithmetic chain silently yields an OBJECT column that sorts and
    compares as text. The values still look numeric, so it passes casual
    inspection and then breaks nlargest and every numeric filter downstream.
    """
    out = restatements(facts_to_frame(SAMPLE))
    for col in ("first_value", "latest_value", "abs_change", "pct_change", "value_ratio"):
        assert out[col].dtype.kind == "f", f"{col} is {out[col].dtype}, expected float"


def test_zero_first_value_does_not_raise_or_poison_dtype():
    """A revision from zero has undefined pct_change -- NaN, not an exception."""
    from_zero = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {"Assets": {
        "label": "Assets", "units": {"USD": [
            {"end": "2025-03-31", "val": 0.0, "form": "10-Q",
             "filed": "2025-05-01", "accn": "A-1"},
            {"end": "2025-03-31", "val": 50.0, "form": "10-K",
             "filed": "2026-02-01", "accn": "A-2"}]}}}}}
    out = restatements(facts_to_frame(from_zero))
    assert len(out) == 1
    assert out["pct_change"].dtype.kind == "f"
    assert pd.isna(out.iloc[0]["pct_change"])
    assert out.iloc[0]["abs_change"] == 50.0


def test_billions_to_dollars_rescaling_is_flagged():
    """
    McDonald's 2020 cash reads 3.4 in the 2021 filing and 3,449,100,000 in the
    2024 one -- billions rescaled to dollars. Because "3.4 billion" was rounded
    first, the ratio is 1.0144e9, not exactly 1e9, so an exact power-of-1000
    test misses it. Order of magnitude is the right test.
    """
    mcd = {"cik": 63908, "entityName": "MCD", "facts": {"us-gaap": {"Cash": {
        "label": "Cash", "units": {"USD": [
            {"end": "2020-12-31", "val": 3.4, "form": "10-K",
             "filed": "2021-02-23", "accn": "M-1"},
            {"end": "2020-12-31", "val": 3449100000.0, "form": "10-K",
             "filed": "2024-02-22", "accn": "M-2"}]}}}}}
    out = restatements(facts_to_frame(mcd))
    assert bool(out.iloc[0]["looks_like_scale_change"]) is True


def test_moderate_revision_is_not_mistaken_for_rescaling():
    """A 53x revision is large but nowhere near a power of 1000."""
    big = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {"Other": {
        "label": "Other", "units": {"USD": [
            {"end": "2022-03-26", "val": 20000000.0, "form": "10-Q",
             "filed": "2022-04-29", "accn": "A-1"},
            {"end": "2022-03-26", "val": -1068000000.0, "form": "10-Q",
             "filed": "2023-05-05", "accn": "A-2"}]}}}}}
    out = restatements(facts_to_frame(big))
    assert bool(out.iloc[0]["looks_like_scale_change"]) is False
