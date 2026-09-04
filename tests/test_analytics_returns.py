"""
Offline tests for the return/volatility building blocks shared across the
analytics/ package. Pure functions -- no DB, no network.
"""
import numpy as np
import pandas as pd
import pytest

from analytics.returns import TRADING_DAYS_PER_YEAR, log_returns, realized_vol, sharpe_ratio


def test_log_returns_matches_hand_computation():
    prices = pd.DataFrame({
        "symbol": ["A", "A", "A", "B", "B", "B"],
        "trade_date": ["2026-01-01", "2026-01-02", "2026-01-03"] * 2,
        "close": [100.0, 110.0, 121.0, 50.0, 45.0, 49.5],
    })
    out = log_returns(prices)
    a = out[out["symbol"] == "A"].sort_values("trade_date")
    assert pd.isna(a["log_return"].iloc[0])
    assert a["log_return"].iloc[1] == pytest.approx(np.log(110 / 100))
    assert a["log_return"].iloc[2] == pytest.approx(np.log(121 / 110))


def test_log_returns_does_not_leak_across_symbols():
    """B's first close must not compute a return off A's last close."""
    prices = pd.DataFrame({
        "symbol": ["A", "A", "B", "B"],
        "trade_date": ["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"],
        "close": [100.0, 200.0, 50.0, 55.0],
    })
    out = log_returns(prices)
    b = out[out["symbol"] == "B"].sort_values("trade_date")
    assert pd.isna(b["log_return"].iloc[0])
    assert b["log_return"].iloc[1] == pytest.approx(np.log(55 / 50))


def test_realized_vol_annualization_factor():
    alt = pd.Series([0.01, -0.01] * 15)
    raw = realized_vol(alt, window=20, annualize=False).dropna()
    ann = realized_vol(alt, window=20, annualize=True).dropna()
    assert (ann / raw).round(4).eq(round(np.sqrt(TRADING_DAYS_PER_YEAR), 4)).all()


def test_sharpe_ratio_orders_correctly_against_risk_free_rate():
    # Mean daily return here annualizes to ~378%, so rf must clear that to
    # flip the sign -- 0.50 (50%) is not enough, 5.0 (500%) is.
    returns = pd.Series([0.02, 0.01] * 30)
    low_rf = sharpe_ratio(returns, rf_annual=0.01, window=60).dropna()
    high_rf = sharpe_ratio(returns, rf_annual=5.0, window=60).dropna()
    assert (low_rf > high_rf).all()
    assert (low_rf > 0).all()
    assert (high_rf < 0).all()
