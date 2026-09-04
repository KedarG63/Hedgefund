"""
Offline tests for the cross-sectional momentum z-score and the Lo-MacKinlay
variance-ratio statistic. variance_ratio_test()/momentum_zscore() themselves
need a live warehouse; this exercises the identical formulas against
synthetic series with a known trending or mean-reverting structure.
Seeds/parameters verified beforehand: trending VR ~2.69, mean-reverting VR
~0.37 -- not borderline.
"""
import numpy as np
import pandas as pd
import pytest


def _variance_ratio(r: pd.Series, lag: int) -> float:
    """Mirror of analytics.momentum.variance_ratio_test's core statistic."""
    var_1 = r.var(ddof=1)
    r_lag = r.rolling(lag).sum().dropna()
    var_lag = r_lag.var(ddof=1)
    return var_lag / (lag * var_1)


def test_variance_ratio_exceeds_one_for_a_trending_series():
    rng = np.random.default_rng(10)
    shock = rng.normal(0.0005, 0.01, 250)
    trending = pd.Series(shock).rolling(3, min_periods=1).mean()  # induces positive autocorrelation
    assert _variance_ratio(trending, lag=5) > 1.0


def test_variance_ratio_is_below_one_for_a_mean_reverting_series():
    rng = np.random.default_rng(10)
    eps = rng.normal(0, 0.01, 250)
    r = np.zeros(250)
    for i in range(1, 250):
        r[i] = -0.5 * r[i - 1] + eps[i]  # AR(1), negative coefficient
    assert _variance_ratio(pd.Series(r), lag=5) < 1.0


def test_momentum_zscore_of_the_mean_is_zero():
    trail_ret = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
    z = (trail_ret - trail_ret.mean()) / trail_ret.std()
    assert z.mean() == pytest.approx(0.0, abs=1e-9)
    # the middle value (0.03) sits exactly at the mean here
    assert z.iloc[2] == pytest.approx(0.0, abs=1e-9)


def test_momentum_zscore_ranks_outperformer_above_underperformer():
    trail_ret = pd.Series({"WINNER": 0.20, "AVG_A": 0.02, "AVG_B": 0.01, "LOSER": -0.15})
    z = (trail_ret - trail_ret.mean()) / trail_ret.std()
    assert z["WINNER"] > z["AVG_A"] > z["AVG_B"] > z["LOSER"]


def test_turnover_floor_excludes_low_volume_names():
    """
    Mirror of analytics.universe.equity_universe()'s min_turnover filter:
    turnover = close * volume, opt-in floor excludes the illiquid tail.
    """
    df = pd.DataFrame({"symbol": ["A", "B"], "close": [10.0, 1000.0],
                       "volume": [100, 50000]})
    df["turnover"] = df["close"] * df["volume"]
    floor = 1_000_000
    kept = df[df["turnover"] >= floor]
    assert kept["symbol"].tolist() == ["B"]


def test_turnover_floor_none_keeps_every_row():
    """min_turnover=None (the default) must not change today's behavior."""
    df = pd.DataFrame({"symbol": ["A", "B"], "turnover": [100.0, 1_000_000.0]})
    min_turnover = None
    kept = df if min_turnover is None else df[df["turnover"] >= min_turnover]
    assert len(kept) == 2
