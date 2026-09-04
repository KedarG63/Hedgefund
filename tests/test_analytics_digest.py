"""
Offline tests for the DeepSeek digest's deterministic scaffolding. No live
API call -- _composite_score() and _validate_and_truncate() are pure
functions and are exactly the part that matters for "minimal, structured,
LLM-never-computes-a-number" output: composite_score is decided here, before
core.llm.call_tool is ever invoked.
"""
import pandas as pd
import pytest

from analytics.digest import (
    FLAG_MAX_CHARS, MAX_CONTRIBUTING_SIGNALS, _composite_score,
    _select_two_tiers, _validate_and_truncate,
)
from analytics.watchlist import WATCHLIST_SYMBOLS


def test_composite_score_is_clipped_to_unit_range():
    df = pd.DataFrame({
        "symbol": ["A", "B", "C"],
        "beta": [0.5, 1.0, 5.0],          # C is a big beta outlier
        "momentum_zscore": [0.1, -0.2, 10.0],  # C also a big momentum outlier
    })
    out = _composite_score(df)
    assert (out["composite_score"] <= 1.0).all()
    assert (out["composite_score"] >= -1.0).all()


def test_composite_score_ranks_the_expected_direction():
    df = pd.DataFrame({
        "symbol": ["STRONG_UP", "FLAT", "STRONG_DOWN"],
        "beta": [1.0, 1.0, 1.0],  # equal beta -> beta_deviation_z is 0 for all
        "momentum_zscore": [3.0, 0.0, -3.0],
    })
    out = _composite_score(df)
    ordered = out.sort_values("composite_score", ascending=False)["symbol"].tolist()
    assert ordered == ["STRONG_UP", "FLAT", "STRONG_DOWN"]


def test_composite_score_handles_zero_beta_variance_without_crashing():
    """When every symbol has an identical beta, beta_deviation_z must be 0, not NaN/inf."""
    df = pd.DataFrame({
        "symbol": ["A", "B"], "beta": [1.0, 1.0], "momentum_zscore": [1.0, -1.0],
    })
    out = _composite_score(df)
    assert out["composite_score"].notna().all()


def test_validate_and_truncate_caps_an_overlong_flag():
    out = _validate_and_truncate({
        "instrument": "RELIANCE", "flag": "x" * (FLAG_MAX_CHARS + 50),
        "contributing_signals": ["a"],
    })
    assert len(out["flag"]) <= FLAG_MAX_CHARS
    assert out["flag"].endswith("...")


def test_validate_and_truncate_caps_the_signal_list():
    out = _validate_and_truncate({
        "instrument": "RELIANCE", "flag": "ok",
        "contributing_signals": [f"sig{i}" for i in range(10)],
    })
    assert len(out["contributing_signals"]) == MAX_CONTRIBUTING_SIGNALS


def test_validate_and_truncate_raises_when_instrument_is_missing():
    with pytest.raises(ValueError):
        _validate_and_truncate({"flag": "ok", "contributing_signals": []})


def test_validate_and_truncate_passes_through_a_well_formed_response():
    out = _validate_and_truncate({
        "instrument": "TCS", "flag": "momentum overbought",
        "contributing_signals": ["momentum:+2.1sigma", "beta:1.3"],
    })
    assert out == {
        "instrument": "TCS", "flag": "momentum overbought",
        "contributing_signals": ["momentum:+2.1sigma", "beta:1.3"],
    }


def _scored_frame():
    watchlist_symbol = WATCHLIST_SYMBOLS[0]
    non_watchlist_extreme = "SOME_MICROCAP"
    non_watchlist_boring = "SOME_OTHER_MICROCAP"
    return pd.DataFrame({
        "symbol": [watchlist_symbol, non_watchlist_extreme, non_watchlist_boring],
        "beta": [1.2, 3.5, 1.0],
        "momentum_zscore": [0.05, 6.0, 0.02],
        "composite_score": [0.02, 1.0, 0.01],
    })


def test_watchlist_member_appears_regardless_of_near_zero_score():
    scored = _scored_frame()
    tiers = _select_two_tiers(scored, shortlist_size=1)
    watchlist_symbol = WATCHLIST_SYMBOLS[0]
    row = tiers[tiers["symbol"] == watchlist_symbol]
    assert len(row) == 1
    assert row["tier"].iloc[0] == "watchlist"


def test_non_watchlist_extreme_score_appears_in_broad_scan():
    scored = _scored_frame()
    tiers = _select_two_tiers(scored, shortlist_size=1)
    row = tiers[tiers["symbol"] == "SOME_MICROCAP"]
    assert len(row) == 1
    assert row["tier"].iloc[0] == "broad_scan"


def test_no_symbol_appears_in_both_tiers():
    scored = _scored_frame()
    tiers = _select_two_tiers(scored, shortlist_size=5)
    assert tiers["symbol"].duplicated().sum() == 0


def test_broad_scan_never_includes_a_watchlist_member():
    """
    Even if a watchlist member's score would otherwise rank in the top-N,
    it must not double-appear under the broad_scan framing -- a name is
    narrated once, under one tier.
    """
    watchlist_symbol = WATCHLIST_SYMBOLS[0]
    scored = pd.DataFrame({
        "symbol": [watchlist_symbol, "MICROCAP_A", "MICROCAP_B"],
        "beta": [1.2, 3.5, -3.0],
        "momentum_zscore": [5.0, 6.0, -6.0],  # watchlist member ALSO happens to be extreme
        "composite_score": [1.0, 1.0, -1.0],
    })
    tiers = _select_two_tiers(scored, shortlist_size=5)
    broad_scan = tiers[tiers["tier"] == "broad_scan"]
    assert watchlist_symbol not in broad_scan["symbol"].tolist()
