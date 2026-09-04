"""
Offline tests for the FII cross-source reconciliation arithmetic.
fii_source_divergence() itself needs a live warehouse (both nse_participant_oi
and upstox_fii_activity); this exercises the identical pct-diff/threshold
logic against small synthetic frames, mirroring tests/test_commodities.py's
"mirror of the production calculation" style.
"""
import pandas as pd
import pytest

from analytics.fii_divergence import DIVERGENCE_THRESHOLD_PCT, FII_METRIC_MAP


def _pct_diff(nse_value: float, upstox_value: float) -> float:
    """Mirror of fii_source_divergence()'s per-row arithmetic."""
    abs_diff = abs(nse_value - upstox_value)
    denom = max(abs(nse_value), abs(upstox_value), 1.0)
    return abs_diff / denom * 100


def test_identical_values_do_not_diverge():
    """The documented expectation: both sources reading the same number is normal."""
    assert _pct_diff(26060, 26060) == 0.0
    assert _pct_diff(26060, 26060) <= DIVERGENCE_THRESHOLD_PCT


def test_both_sides_zero_does_not_divide_by_zero():
    assert _pct_diff(0, 0) == 0.0


def test_a_real_mismatch_exceeds_the_threshold():
    # 26,060 vs 24,000 is a ~7.9% gap -- well past the 1% documented tolerance.
    pct = _pct_diff(26060, 24000)
    assert pct == pytest.approx(7.906, abs=0.01)
    assert pct > DIVERGENCE_THRESHOLD_PCT


def test_a_small_reporting_lag_gap_stays_under_threshold():
    # A 0.5% gap should read as noise, not a flagged divergence.
    pct = _pct_diff(1_000_000, 1_005_000)
    assert pct < DIVERGENCE_THRESHOLD_PCT


def test_metric_map_columns_are_internally_consistent():
    """
    Every metric must map to real, distinct participant_oi/upstox column
    pairs -- a duplicate NSE column mapped to two different Upstox columns
    would silently double-count that source's contribution.
    """
    nse_cols = [m[1] for m in FII_METRIC_MAP]
    assert len(nse_cols) == len(set(nse_cols)), "duplicate NSE column in FII_METRIC_MAP"
    for _, _, segment, _ in FII_METRIC_MAP:
        assert segment.startswith("NSE_FO|")


def test_divergence_flag_matches_the_documented_threshold():
    df = pd.DataFrame({
        "nse_value": [26060, 3655491, 100],
        "upstox_value": [26060, 2973732, 100],
    })
    df["pct_diff"] = (df["nse_value"] - df["upstox_value"]).abs() / \
        df[["nse_value", "upstox_value"]].abs().max(axis=1).clip(lower=1) * 100
    df["diverges"] = df["pct_diff"] > DIVERGENCE_THRESHOLD_PCT
    assert df["diverges"].tolist() == [False, True, False]
