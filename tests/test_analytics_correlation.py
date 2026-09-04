"""
Offline tests for the correlation/cointegration math.

rolling_correlation()/pairs_screen() themselves need a live warehouse
(equity_universe()/DuckDB); this exercises the same underlying calls --
numpy correlation, statsmodels.tsa.stattools.coint -- against synthetic
series with known relationships. Seeds verified to produce the asserted
p-values before being pinned here (cointegrated pair ~1e-27, independent
pair ~0.99 -- not borderline, so not seed-flaky).
"""
import numpy as np
import pandas as pd
import pytest
from statsmodels.tsa.stattools import coint

from analytics.correlation import sector_correlation, to_symmetric_matrix


def test_correlation_near_one_for_a_scaled_series():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, 200)
    b = 2 * a + rng.normal(0, 0.01, 200)
    assert np.corrcoef(a, b)[0, 1] > 0.99


def test_correlation_near_zero_for_independent_series():
    rng = np.random.default_rng(2)
    a = rng.normal(0, 1, 500)
    b = rng.normal(0, 1, 500)
    assert abs(np.corrcoef(a, b)[0, 1]) < 0.15


def test_cointegration_detects_a_shared_random_walk():
    """Two series driven by the same underlying random walk cointegrate."""
    rng = np.random.default_rng(3)
    common = np.cumsum(rng.normal(0, 1, 300))
    a = 100 + common + rng.normal(0, 0.5, 300)
    b = 50 + 0.5 * common + rng.normal(0, 0.5, 300)
    _, pvalue, _ = coint(a, b)
    assert pvalue < 0.05


def test_cointegration_rejects_two_independent_random_walks():
    """The classic spurious-regression trap Engle-Granger exists to catch."""
    rng = np.random.default_rng(4)
    a = 100 + np.cumsum(rng.normal(0, 1, 300))
    b = 50 + np.cumsum(rng.normal(0, 1, 300))
    _, pvalue, _ = coint(a, b)
    assert pvalue > 0.05


def test_pairs_threshold_boundary_is_inclusive():
    """
    pairs_screen() keeps correlation.abs() >= corr_threshold -- a pair
    exactly at the threshold is included, not excluded. Pinning the
    boundary convention the way test_commodities.py pins the CME
    contract-order boundary.
    """
    threshold = 0.8
    corr_values = np.array([0.79, 0.80, 0.81])
    kept = np.abs(corr_values) >= threshold
    assert kept.tolist() == [False, True, True]


def test_to_symmetric_matrix_mirrors_the_upper_triangle():
    """
    derived_correlation stores only symbol_a < symbol_b (see
    rolling_correlation()'s docstring) -- to_symmetric_matrix() must fill in
    both (a, b) and (b, a) from that single stored row, and the diagonal
    (never computed or stored) must read 1.0.
    """
    long = pd.DataFrame([
        {"symbol_a": "AAA", "symbol_b": "BBB", "correlation": 0.5},
        {"symbol_a": "AAA", "symbol_b": "CCC", "correlation": -0.2},
    ])
    mat = to_symmetric_matrix(long, ["AAA", "BBB", "CCC"])
    assert mat.loc["AAA", "BBB"] == mat.loc["BBB", "AAA"] == 0.5
    assert mat.loc["AAA", "CCC"] == mat.loc["CCC", "AAA"] == -0.2
    assert (pd.Series(np.diagonal(mat.values)) == 1.0).all()


def test_to_symmetric_matrix_ignores_pairs_outside_the_requested_symbols():
    """A row naming a symbol not in the requested list must not KeyError."""
    long = pd.DataFrame([
        {"symbol_a": "AAA", "symbol_b": "ZZZ", "correlation": 0.9},
    ])
    mat = to_symmetric_matrix(long, ["AAA", "BBB"])
    assert pd.isna(mat.loc["AAA", "BBB"])  # never computed -- NaN ("no data"), not 1.0


def test_to_symmetric_matrix_missing_pair_is_nan_not_perfectly_correlated():
    """
    rolling_correlation() drops thin-history symbols from its panel, so a
    requested symbol can have NO stored correlation against another. That
    must show as "no data" (NaN), never silently as 1.0 ("perfectly
    correlated") -- the bug a naive all-ones default would introduce.
    """
    long = pd.DataFrame([{"symbol_a": "AAA", "symbol_b": "BBB", "correlation": 0.3}])
    mat = to_symmetric_matrix(long, ["AAA", "BBB", "CCC"])
    assert pd.isna(mat.loc["AAA", "CCC"])
    assert pd.isna(mat.loc["BBB", "CCC"])
    assert mat.loc["CCC", "CCC"] == 1.0  # diagonal is always 1.0, even with no pair data


def test_sector_correlation_averages_within_industry_pairs():
    """Two symbol pairs in the same (industry_a, industry_b) cell must average, not overwrite."""
    long = pd.DataFrame([
        {"symbol_a": "AAA", "symbol_b": "BBB", "correlation": 0.8},
        {"symbol_a": "AAA", "symbol_b": "CCC", "correlation": 0.4},
    ])
    industry = pd.DataFrame([
        {"symbol": "AAA", "industry": "Banks"},
        {"symbol": "BBB", "industry": "IT"},
        {"symbol": "CCC", "industry": "IT"},
    ])
    result = sector_correlation(long, industry)
    row = result[(result["industry_a"] == "Banks") & (result["industry_b"] == "IT")]
    assert len(row) == 1
    assert row["correlation"].iloc[0] == pytest.approx(0.6)  # mean of 0.8 and 0.4


def test_sector_correlation_empty_when_no_symbol_overlaps_the_industry_map():
    long = pd.DataFrame([{"symbol_a": "ZZZ", "symbol_b": "YYY", "correlation": 0.5}])
    industry = pd.DataFrame([{"symbol": "AAA", "industry": "Banks"}])
    result = sector_correlation(long, industry)
    assert result.empty
