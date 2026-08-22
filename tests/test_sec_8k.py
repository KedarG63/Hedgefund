"""
Offline tests for the 8-K connector.

The parsing here is trivial; the failure modes are semantic. Both tested cases
are ones that produced plausible-looking but wrong output.
"""
import pandas as pd
import pytest

from connectors.sec_8k import (
    ITEM_CODES,
    RED_FLAG_ITEMS,
    item_description,
    item_tier,
    primary_document_url,
    red_flags,
)


# ------------------------------------------------------------------ item codes
def test_every_red_flag_is_a_known_critical_item():
    for code in RED_FLAG_ITEMS:
        assert code in ITEM_CODES, code
        assert item_tier(code) == "critical", code


def test_routine_items_are_not_red_flags():
    """
    9.01 appears on most 8-Ks and means only "exhibits attached"; 7.01 is Reg FD.
    Treating volume as signal would drown the screen.
    """
    for code in ("9.01", "7.01", "5.03", "5.07"):
        assert code not in RED_FLAG_ITEMS
        assert item_tier(code) == "low"


def test_officer_departure_is_high_but_not_a_red_flag():
    """5.02 is frequent and mostly routine rotation -- a screen, not an alert."""
    assert item_tier("5.02") == "high"
    assert "5.02" not in RED_FLAG_ITEMS


def test_unknown_item_degrades_gracefully():
    assert item_description("99.99") == "Unknown item"
    assert item_tier("99.99") == "unknown"


def test_tiers_are_from_a_closed_set():
    assert {t for _, t in ITEM_CODES.values()} <= {"critical", "high", "medium", "low"}


# ------------------------------------------------------------------ urls
def test_primary_document_url_strips_dashes_from_accession():
    url = primary_document_url(320193, "0000320193-26-000018", "aapl-20260730.htm")
    assert url.endswith("/320193/000032019326000018/aapl-20260730.htm")


# ------------------------------------------------------------------ red flags
def make_row(**over):
    base = dict(cik=1, accn="A-1", form="8-K", item="4.02",
                item_description="Non-Reliance", tier="critical",
                is_red_flag=True, is_amendment=False,
                report_date="2026-01-01", filed="2026-01-05", days_to_file=4)
    base.update(over)
    return base


def test_red_flags_selects_only_flagged_rows():
    df = pd.DataFrame([
        make_row(item="4.02", is_red_flag=True),
        make_row(item="9.01", is_red_flag=False, tier="low"),
    ])
    out = red_flags(df)
    assert len(out) == 1 and out.iloc[0]["item"] == "4.02"


def test_red_flags_since_filter():
    df = pd.DataFrame([
        make_row(accn="old", filed="2020-01-01"),
        make_row(accn="new", filed="2026-01-05"),
    ])
    out = red_flags(df, since="2025-01-01")
    assert list(out["accn"]) == ["new"]


def test_red_flags_newest_first():
    df = pd.DataFrame([
        make_row(accn="a", filed="2024-01-01"),
        make_row(accn="b", filed="2026-01-01"),
    ])
    assert list(red_flags(df)["accn"]) == ["b", "a"]


# ------------------------------------------------- amendment lag vs filing lag
def test_amendment_lag_is_kept_out_of_days_to_file():
    """
    An 8-K/A carries the ORIGINAL event's reportDate. Corteva filed an amendment
    947 days after the event it amends; counting that as disclosure lag would
    label a routine amendment as a governance failure.

    This mirrors the production computation in eight_k_filings().
    """
    df = pd.DataFrame([
        {"form": "8-K",   "report_date": "2026-01-01", "filed": "2026-01-05"},
        {"form": "8-K/A", "report_date": "2023-11-08", "filed": "2026-06-12"},
    ])
    df["is_amendment"] = df["form"].str.endswith("/A")
    gap = (pd.to_datetime(df["filed"]) - pd.to_datetime(df["report_date"])).dt.days
    df["days_to_file"] = gap.where(~df["is_amendment"])
    df["amendment_lag_days"] = gap.where(df["is_amendment"])

    assert df.loc[0, "days_to_file"] == 4
    assert pd.isna(df.loc[1, "days_to_file"]), "amendment must not pollute filing lag"
    assert df.loc[1, "amendment_lag_days"] == 947
    assert pd.isna(df.loc[0, "amendment_lag_days"])


def test_items_string_explodes_to_one_row_each():
    """
    `items` arrives as "2.02,9.01". Keeping the string forces substring matching
    downstream, which cannot be grouped or joined reliably.
    """
    df = pd.DataFrame([{"accn": "A-1", "items": "2.02,9.01"}])
    df["item_list"] = df["items"].str.split(",")
    out = df.explode("item_list")
    out["item"] = out["item_list"].str.strip()
    assert sorted(out["item"]) == ["2.02", "9.01"]
    assert len(out) == 2
