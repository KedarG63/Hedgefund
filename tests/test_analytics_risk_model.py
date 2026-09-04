"""
Offline tests for the momentum/size/low-vol factor model's math. size_factor()/
low_vol_factor()/build_factor_model() themselves need a live warehouse (same
reasoning as test_analytics_momentum.py); this mirrors the identical formulas
-- sign conventions, missing-factor handling, composite weighting -- against
synthetic data instead.
"""
import numpy as np
import pandas as pd
import pytest

from analytics.risk_model import COMPOSITE_WEIGHTS


# ------------------------------------------------------------------ size_score
def _size_score(market_cap: pd.Series) -> pd.Series:
    """Mirror of size_factor()'s -zscore(log market cap)."""
    log_mcap = np.log(market_cap)
    return -(log_mcap - log_mcap.mean()) / log_mcap.std()


def test_size_score_ranks_small_cap_above_large_cap():
    """SMB convention: SMALLER companies score HIGHER, not lower."""
    mcap = pd.Series({"MEGA_CAP": 500_000.0, "MID_CAP": 5_000.0, "SMALL_CAP": 50.0})
    z = _size_score(mcap)
    assert z["SMALL_CAP"] > z["MID_CAP"] > z["MEGA_CAP"]


def test_size_score_of_the_mean_is_zero():
    mcap = pd.Series([100.0, 1000.0, 10000.0])
    z = _size_score(mcap)
    assert z.mean() == pytest.approx(0.0, abs=1e-9)


def test_size_factor_excludes_unmatched_symbols_rather_than_zeroing_them():
    """
    An ISIN with no BSE market-cap match must end up null, not zero -- a
    zero z-score would misrepresent it as "average size" instead of "unknown".
    """
    df = pd.DataFrame({"symbol": ["A", "B", "C"], "market_cap_inr_cr": [100.0, np.nan, 500.0]})
    matched = df["market_cap_inr_cr"].notna() & (df["market_cap_inr_cr"] > 0)
    df["size_score"] = np.nan
    log_mcap = np.log(df.loc[matched, "market_cap_inr_cr"])
    df.loc[matched, "size_score"] = -(log_mcap - log_mcap.mean()) / log_mcap.std()
    assert pd.isna(df.loc[df["symbol"] == "B", "size_score"]).all()
    assert df.loc[df["symbol"] == "A", "size_score"].notna().all()


# ------------------------------------------------------------------ low_vol_score
def _low_vol_score(realized_vol: pd.Series) -> pd.Series:
    """Mirror of low_vol_factor()'s -zscore(realized_vol)."""
    return -(realized_vol - realized_vol.mean()) / realized_vol.std()


def test_low_vol_score_ranks_calm_stock_above_volatile_stock():
    """Low-volatility-anomaly convention: CALMER stocks score HIGHER, not lower."""
    vol = pd.Series({"CALM": 0.10, "AVERAGE": 0.25, "VOLATILE": 0.60})
    z = _low_vol_score(vol)
    assert z["CALM"] > z["AVERAGE"] > z["VOLATILE"]


def test_low_vol_score_of_the_mean_is_zero():
    vol = pd.Series([0.15, 0.25, 0.35])
    z = _low_vol_score(vol)
    assert z.mean() == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------ composite
def _composite(momentum_z, size_z, low_vol_z) -> float:
    """Mirror of build_factor_model()'s weighted sum + clip, one row at a time."""
    raw = (COMPOSITE_WEIGHTS["momentum_zscore"] * (momentum_z if momentum_z == momentum_z else 0.0)
           + COMPOSITE_WEIGHTS["size_score"] * (size_z if size_z == size_z else 0.0)
           + COMPOSITE_WEIGHTS["low_vol_score"] * (low_vol_z if low_vol_z == low_vol_z else 0.0))
    return max(-1.0, min(1.0, raw))


def test_composite_weights_sum_to_one():
    """Not required by the math (fillna(0.0) tolerates any weights), but a
    silent typo dropping a weight to e.g. 0.04 should be caught here."""
    assert sum(COMPOSITE_WEIGHTS.values()) == pytest.approx(1.0)


def test_composite_score_missing_factor_contributes_zero_not_a_penalty():
    """
    A symbol missing size_score (e.g. no BSE market-cap match) must be scored
    as if that factor were neutral (0), not as if it were the worst possible
    value -- fillna(0.0), not fillna(-1) or fillna(min).
    """
    with_size = _composite(momentum_z=1.0, size_z=1.0, low_vol_z=1.0)
    without_size = _composite(momentum_z=1.0, size_z=float("nan"), low_vol_z=1.0)
    assert without_size < with_size
    assert without_size == pytest.approx(
        COMPOSITE_WEIGHTS["momentum_zscore"] * 1.0 + COMPOSITE_WEIGHTS["low_vol_score"] * 1.0
    )


def test_composite_score_clips_to_unit_range():
    extreme = _composite(momentum_z=100.0, size_z=100.0, low_vol_z=100.0)
    assert extreme == 1.0
    extreme_negative = _composite(momentum_z=-100.0, size_z=-100.0, low_vol_z=-100.0)
    assert extreme_negative == -1.0


def test_composite_score_all_factors_agreeing_beats_mixed_signal():
    all_positive = _composite(momentum_z=0.5, size_z=0.5, low_vol_z=0.5)
    mixed = _composite(momentum_z=0.5, size_z=-0.5, low_vol_z=0.0)
    assert all_positive > mixed
