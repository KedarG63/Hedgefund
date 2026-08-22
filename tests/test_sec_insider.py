"""
Offline tests for the insider (Forms 3/4/5) connector.

Every test here corresponds to a bug that actually occurred while building it.
All three inflated the headline signal without raising anything.
"""
import pandas as pd
import pytest

from connectors.sec_insider import (
    HIGH_SIGNAL_CODES,
    TRANSACTION_CODES,
    _classify_role,
    cluster_buys,
    latest_available_quarter,
)


def make_tx(**over):
    """One clean open-market common-stock buy, overridable per test."""
    base = dict(
        issuer_cik=1, issuer_name="ACME", ticker="ACME", owner_cik=10,
        owner_name="A", role="officer", security_title="Common Stock",
        trans_code="P", trans_date="2025-05-01", filed="2025-05-02",
        shares=1000.0, price_per_share=100.0, value_usd=100_000.0,
        is_open_market_buy=True, is_common_stock=True,
    )
    base.update(over)
    return base


# ------------------------------------------------------------------ codes/roles
def test_only_open_market_purchase_is_high_signal():
    """Grants and option exercises are compensation, not conviction."""
    assert HIGH_SIGNAL_CODES == ("P",)
    for compensation_code in ("A", "M", "F"):
        assert compensation_code not in HIGH_SIGNAL_CODES
        assert compensation_code in TRANSACTION_CODES


@pytest.mark.parametrize("relationship,expected", [
    ("Officer", "officer"),
    ("Director", "director"),
    ("TenPercentOwner", "tenpercentowner"),
    ("Director|Officer", "officer"),          # most informative role wins
    ("", "other"),
    (float("nan"), "other"),
])
def test_classify_role(relationship, expected):
    assert _classify_role(relationship) == expected


# ------------------------------------------- cross-filer duplicate transactions
def test_same_trade_reported_by_two_filers_counts_once():
    """
    TKO Group: a director and the 10% holder he is affiliated with each reported
    the SAME 1,579,080 shares at $158.32, doubling a $250m purchase to $500m.
    Identical (issuer, date, size, price) is one economic event.
    """
    dup = [
        make_tx(owner_cik=10, owner_name="Durban", role="director",
                shares=1_579_080.0, price_per_share=158.32, value_usd=250_000_000.0),
        make_tx(owner_cik=11, owner_name="Endeavor", role="tenpercentowner",
                shares=1_579_080.0, price_per_share=158.32, value_usd=250_000_000.0),
        make_tx(owner_cik=12, owner_name="Bynoe", role="director",
                shares=980.0, price_per_share=169.59, value_usd=166_198.0),
    ]
    out = cluster_buys(pd.DataFrame(dup), min_insiders=2, persist=False)
    assert len(out) == 1
    assert out.iloc[0]["total_value_usd"] == pytest.approx(250_166_198.0)
    assert out.iloc[0]["n_insiders"] == 2, "the duplicated pair must collapse to one"


# ------------------------------------------------------ preferred-stock filter
def test_preferred_stock_placement_is_excluded_by_default():
    """
    Palatin Technologies: 'Series D Preferred Stock' at $150,000/share coded P
    -- a private financing, not a view on the listed equity. It produced $454m
    of apparent insider buying in a micro-cap.
    """
    # distinct sizes, so this isolates the security-type filter rather than
    # tripping the identical-trade dedup
    rows = [
        make_tx(owner_cik=cik, security_title="Series D Preferred Stock",
                is_common_stock=False, shares=shares, price_per_share=150_000.0,
                value_usd=shares * 150_000.0)
        for cik, shares in [(10, 1500.0), (11, 1200.0), (12, 200.0)]
    ]
    assert cluster_buys(pd.DataFrame(rows), min_insiders=3, persist=False).empty
    kept = cluster_buys(pd.DataFrame(rows), min_insiders=3,
                        common_stock_only=False, persist=False)
    assert len(kept) == 1, "still available when explicitly requested"


# --------------------------------------------------------------- cluster logic
def test_cluster_counts_distinct_people_not_transactions():
    """One insider buying five times is one insider, not a cluster."""
    rows = [make_tx(owner_cik=10, trans_date=f"2025-05-0{d}", shares=100.0 * d)
            for d in range(1, 6)]
    assert cluster_buys(pd.DataFrame(rows), min_insiders=2, persist=False).empty


def test_cluster_requires_the_window():
    """Buys months apart are not a cluster."""
    rows = [make_tx(owner_cik=10, trans_date="2025-01-02"),
            make_tx(owner_cik=11, trans_date="2025-06-02")]
    assert cluster_buys(pd.DataFrame(rows), window_days=30, min_insiders=2,
                        persist=False).empty


def test_cluster_detected_within_window():
    rows = [make_tx(owner_cik=10, trans_date="2025-05-01"),
            make_tx(owner_cik=11, trans_date="2025-05-20", shares=500.0)]
    out = cluster_buys(pd.DataFrame(rows), window_days=30, min_insiders=2, persist=False)
    assert len(out) == 1 and out.iloc[0]["n_insiders"] == 2


def test_role_counts_are_distinct_people():
    rows = [make_tx(owner_cik=10, role="director", trans_date="2025-05-01"),
            make_tx(owner_cik=10, role="director", trans_date="2025-05-02", shares=7.0),
            make_tx(owner_cik=11, role="officer", trans_date="2025-05-03", shares=9.0)]
    out = cluster_buys(pd.DataFrame(rows), min_insiders=2, persist=False)
    assert out.iloc[0]["n_directors"] == 1 and out.iloc[0]["n_officers"] == 1


def test_min_value_filters_token_purchases():
    rows = [make_tx(owner_cik=10, value_usd=100.0),
            make_tx(owner_cik=11, value_usd=100.0)]
    assert cluster_buys(pd.DataFrame(rows), min_insiders=2, min_value=50_000,
                        persist=False).empty


def test_sales_are_never_clusters():
    rows = [make_tx(owner_cik=n, trans_code="S", is_open_market_buy=False)
            for n in (10, 11, 12)]
    assert cluster_buys(pd.DataFrame(rows), min_insiders=2, persist=False).empty


@pytest.mark.parametrize("today,expected", [
    ((2026, 8, 22), (2026, 2)),
    ((2026, 2, 1),  (2025, 4)),
])
def test_latest_available_quarter(today, expected):
    from datetime import date
    assert latest_available_quarter(date(*today)) == expected
