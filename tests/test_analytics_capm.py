"""
Offline tests for the CAPM beta/alpha regression math.

compute_betas() itself needs a live warehouse (equity_universe() and
_price_history() both query DuckDB); this exercises the exact regression
path it calls internally -- analytics.returns.log_returns feeding
scipy.stats.linregress -- against synthetic return series with a known
linear relationship. Mirrors how tests/test_commodities.py verifies the
gold premium's arithmetic without a live fetch.
"""
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from analytics.returns import log_returns


def _synthetic_returns(true_beta: float, true_alpha: float, n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    market_ret = rng.normal(0, 0.01, n)
    noise = rng.normal(0, 0.001, n)
    stock_ret = true_alpha + true_beta * market_ret + noise
    dates = pd.date_range("2026-01-01", periods=n + 1, freq="D").strftime("%Y-%m-%d")
    market_price = 100 * np.exp(np.concatenate([[0], np.cumsum(market_ret)]))
    stock_price = 100 * np.exp(np.concatenate([[0], np.cumsum(stock_ret)]))
    return pd.concat([
        pd.DataFrame({"symbol": "NIFTYBEES", "trade_date": dates, "close": market_price}),
        pd.DataFrame({"symbol": "TESTSTOCK", "trade_date": dates, "close": stock_price}),
    ], ignore_index=True)


def _fit_beta(prices: pd.DataFrame):
    hist = log_returns(prices)
    market = hist[hist["symbol"] == "NIFTYBEES"][["trade_date", "log_return"]].dropna()
    stock = hist[hist["symbol"] == "TESTSTOCK"][["trade_date", "log_return"]].dropna()
    merged = stock.merge(market, on="trade_date", suffixes=("_stock", "_market"))
    return stats.linregress(merged["log_return_market"], merged["log_return_stock"])


def test_beta_recovers_a_known_linear_relationship():
    prices = _synthetic_returns(true_beta=1.3, true_alpha=0.0)
    slope, intercept, r, _, _ = _fit_beta(prices)
    assert slope == pytest.approx(1.3, abs=0.15)
    assert r ** 2 > 0.8


def test_beta_is_one_and_r_squared_is_one_for_an_identical_series():
    prices = _synthetic_returns(true_beta=1.0, true_alpha=0.0, seed=7)
    hist = log_returns(prices)
    market = hist[hist["symbol"] == "NIFTYBEES"].set_index("trade_date")["log_return"]
    merged = pd.DataFrame({"m": market, "s": market}).dropna()  # stock == market exactly
    slope, intercept, r, _, _ = stats.linregress(merged["m"], merged["s"])
    assert slope == pytest.approx(1.0, abs=1e-9)
    assert r ** 2 == pytest.approx(1.0, abs=1e-9)
