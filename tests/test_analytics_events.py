"""
Offline tests for the event-signal arithmetic and date parsing.

insider_filing_frequency()/bulk_block_deal_anomaly()/promoter_holding_change()
themselves need a live warehouse; this verifies the date-format parsing
against the real observed formats ("22-Aug-2026 17:50:32", "18-AUG-2026",
"30-JUN-2026" -- confirmed live against the actual parquet columns) and the
notional/z-score arithmetic by hand, mirroring
tests/test_commodities.py's style.
"""
import pandas as pd
import pytest


def test_broadcast_datetime_format_parses_the_real_observed_string():
    parsed = pd.to_datetime("22-Aug-2026 17:50:32", format="%d-%b-%Y %H:%M:%S")
    assert parsed == pd.Timestamp("2026-08-22 17:50:32")


def test_bd_dt_date_format_parses_and_sorts_correctly_across_months():
    """
    BD_DT_DATE is text like '18-AUG-2026' -- sorted as a raw string,
    '18-AUG-2026' comes before '30-JUN-2026' alphabetically (A < J), which is
    the wrong chronological order. Parsed dates fix this, the same trap
    connectors.commodities documents for MCX's ExpiryDate.
    """
    raw = ["18-AUG-2026", "30-JUN-2026", "05-JUL-2026"]
    as_string_sorted = sorted(raw)
    as_date_sorted = sorted(raw, key=lambda s: pd.to_datetime(s, format="%d-%b-%Y"))
    # Raw string sort compares the DAY digit first ('0' < '1' < '3'), giving
    # an order with no relation to chronology.
    assert as_string_sorted == ["05-JUL-2026", "18-AUG-2026", "30-JUN-2026"]
    assert as_date_sorted == ["30-JUN-2026", "05-JUL-2026", "18-AUG-2026"]
    assert as_string_sorted != as_date_sorted


def test_bulk_deal_signed_notional_arithmetic():
    """Mirror of analytics.events.bulk_block_deal_anomaly's notional calc."""
    deals = pd.DataFrame({
        "BD_QTY_TRD": [1_000_000, 500_000],
        "BD_TP_WATP": [46.19, 46.37],
        "BD_BUY_SELL": ["BUY", "SELL"],
    })
    notional = deals["BD_QTY_TRD"] * deals["BD_TP_WATP"]
    signed = notional * deals["BD_BUY_SELL"].str.upper().map({"BUY": 1, "SELL": -1})
    assert notional.iloc[0] == pytest.approx(46_190_000.0)
    assert signed.iloc[0] == pytest.approx(46_190_000.0)
    assert signed.iloc[1] == pytest.approx(-23_185_000.0)


def test_notional_zscore_flags_an_outlier_day():
    history = pd.Series([1_000_000.0] * 19 + [10_000_000.0])  # one spike day
    mean_, std_ = history.mean(), history.std()
    z = (history.iloc[-1] - mean_) / std_
    assert z > 2.0, "a 10x spike against a flat history should score as a clear outlier"


def test_promoter_holding_change_direction():
    """Mirror of analytics.events.promoter_holding_change's QoQ delta."""
    quarters = pd.DataFrame({
        "quarter_end": pd.to_datetime(["31-MAR-2026", "30-JUN-2026"], format="%d-%b-%Y"),
        "pr_and_prgrp": [55.0, 48.5],
    }).sort_values("quarter_end")
    change = quarters["pr_and_prgrp"].diff().iloc[-1]
    assert change == pytest.approx(-6.5)
    assert change < 0, "a falling promoter holding should show as a negative change"
