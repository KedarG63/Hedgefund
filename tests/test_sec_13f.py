"""
Offline tests for the 13F connector.

The value-scale inference gets the most attention because it is the one piece
whose failure is silent and catastrophic: a mis-scaled filing is off by 1000x
but still looks like a plausible portfolio.
"""
import json

import pandas as pd
import pytest

from connectors.sec_13f import (
    CONFIG,
    _infer_value_scale,
    latest_reported_quarter,
    watchlist,
)


# ------------------------------------------------------------------ watchlist
def test_watchlist_loads_and_is_unique():
    wl = watchlist()
    assert len(wl) == 28, "expected 28 filers (Microsoft and Apple file no 13F)"
    assert wl["cik"].is_unique
    assert wl["cik_int"].is_unique


def test_watchlist_ciks_are_zero_padded_ten_digits():
    for cik in watchlist()["cik"]:
        assert len(cik) == 10 and cik.isdigit(), cik


@pytest.mark.parametrize("absent", ["Microsoft", "Apple"])
def test_non_filers_stay_absent_and_documented(absent):
    """
    Neither files a 13F. Their absence must stay deliberate: 'Apple Inc' was
    once matched to APPLETON PARTNERS INC/MA, a Boston advisor, which silently
    published a stranger's portfolio under Apple's name.
    """
    wl = watchlist()
    assert not wl["name"].str.contains(absent, case=False).any()
    assert absent in " ".join(json.loads(CONFIG.read_text(encoding="utf-8"))["_comment"])


def test_appleton_partners_cik_is_not_in_the_watchlist():
    """Regression guard on the specific mismatch above."""
    assert 1055290 not in set(watchlist()["cik_int"])


@pytest.mark.parametrize("name,cik", [
    ("Berkshire Hathaway Inc", 1067983),
    ("Millennium Management", 1273087),      # NOT WorldQuant Millennium (1745981)
    ("Geode Capital Management", 1214717),   # NOT the Trust Company (NT-only)
    ("Two Sigma Investments", 1179392),      # NOT Blazar Portfolio (NT-only)
    ("Man Group plc", 1637460),              # NOT Neuberger BerMAN GROUP
])
def test_verified_ciks_are_the_holdings_filers(name, cik):
    """Pin the entities that were wrong when resolved by name alone."""
    assert watchlist().query("name == @name").iloc[0]["cik_int"] == cik


def test_filer_types_are_known():
    assert set(watchlist()["type"]) <= {"asset_manager", "hedge_fund", "corporate"}


# ------------------------------------------------------------- value scaling
def test_infer_scale_detects_dollars():
    """State Street's real numbers: MSFT at $375.39/share."""
    values = pd.Series([111126793295.0, 132395203402.0, 70193397490.0])
    shares = pd.Series([296030244.0, 596025766.0, 368934077.0])
    factor, scale = _infer_value_scale(values, shares)
    assert (factor, scale) == (1.0, "dollars")


def test_infer_scale_detects_thousands():
    """
    T. Rowe Price's real numbers for the SAME quarter: it still reports in
    thousands, so implied price reads as $0.375 until scaled.
    """
    values = pd.Series([45273719.0, 42816412.0, 36763033.0])
    shares = pd.Series([120604488.0, 192753846.0, 161698873.0])
    factor, scale = _infer_value_scale(values, shares)
    assert (factor, scale) == (1000.0, "thousands")


def test_scaling_reconciles_two_filers_on_the_same_security():
    """
    The strongest check available: two managers holding MSFT in one quarter must
    imply the same price once scaled. They agree at $375.39.
    """
    tr_factor, _ = _infer_value_scale(pd.Series([45273719.0]), pd.Series([120604488.0]))
    ss_factor, _ = _infer_value_scale(pd.Series([111126793295.0]), pd.Series([296030244.0]))
    tr_price = 45273719.0 * tr_factor / 120604488.0
    ss_price = 111126793295.0 * ss_factor / 296030244.0
    assert abs(tr_price - ss_price) < 0.01, (tr_price, ss_price)


def test_infer_scale_handles_no_usable_rows():
    """Principal-amount-only filings have no share counts; must not divide by zero."""
    factor, scale = _infer_value_scale(pd.Series([100.0, 200.0]), pd.Series([0.0, 0.0]))
    assert (factor, scale) == (1.0, "unknown")


def test_infer_scale_tolerates_a_few_penny_stocks():
    """One sub-$1 holding must not flip a dollar-denominated filing to thousands."""
    values = pd.Series([111126793295.0, 132395203402.0, 500.0])
    shares = pd.Series([296030244.0, 596025766.0, 100000.0])
    assert _infer_value_scale(values, shares)[1] == "dollars"


# ------------------------------------------------------------------ scheduling
@pytest.mark.parametrize("today,expected", [
    ((2026, 8, 22), (2026, 2)),
    ((2026, 1, 5),  (2025, 4)),
    ((2026, 4, 30), (2026, 1)),
    ((2026, 11, 1), (2026, 3)),
])
def test_latest_reported_quarter(today, expected):
    from datetime import date
    assert latest_reported_quarter(date(*today)) == expected
