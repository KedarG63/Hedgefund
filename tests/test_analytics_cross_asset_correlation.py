"""
Offline tests for analytics/cross_asset_correlation.py.

The public functions (equity_commodity_correlation/equity_macro_correlation)
need a live warehouse for their equity leg -- same as analytics/capm.py and
analytics/correlation.py's public functions, which is why THEIR test files
exercise the underlying math on synthetic data rather than the DB-touching
functions directly. This file does the same for the pure transforms
(_log_return_series, _monthly_last), and additionally monkeypatches the
per-leg fetch functions to exercise the real skip-vs-raise orchestration in
equity_commodity_correlation()/equity_macro_correlation() without touching
DuckDB at all.
"""
import numpy as np
import pandas as pd
import pytest

from analytics import cross_asset_correlation as cac


def _synthetic_price_frame(n_days: int, seed: int, start: str = "2026-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n_days).strftime("%Y-%m-%d")
    rng = np.random.default_rng(seed)
    prices = 100 + np.cumsum(rng.normal(0, 1, n_days))
    return pd.DataFrame({"trade_date": dates, "close": prices})


def test_log_return_series_drops_the_first_row_and_renames_the_value_column():
    """
    Renamed to commodity_value, not log_return: the caller merges this
    against the equity return frame, which already owns a column literally
    called log_return -- an unrenamed collision would make pandas
    auto-suffix both sides (the same class of bug analytics/correlation.py's
    sector_correlation() had before its fix).
    """
    prices = _synthetic_price_frame(10, seed=1)
    out = cac._log_return_series(prices, "close")
    assert len(out) == 9  # first day has no prior close to compute a return against
    assert list(out.columns) == ["trade_date", "commodity_value"]
    assert not out["commodity_value"].isna().any()


def test_monthly_last_keeps_one_observation_per_calendar_month():
    dates = pd.date_range("2026-01-01", periods=90, freq="D")  # spans Jan, Feb, Mar 2026
    df = pd.DataFrame({"d": dates, "v": np.arange(90)})
    monthly = cac._monthly_last(df, "d", "v")
    assert len(monthly) == 3
    assert monthly.iloc[0] == 30  # index of Jan 31 (2026 Jan has 31 days, 0-indexed)
    assert monthly.iloc[-1] == 89  # index of Mar 31


def test_equity_commodity_correlation_skips_short_and_empty_legs(monkeypatch):
    equity = pd.DataFrame({"trade_date": pd.bdate_range("2026-01-01", periods=30).strftime("%Y-%m-%d")})
    equity["log_return"] = np.random.default_rng(2).normal(0, 0.01, 30)
    monkeypatch.setattr(cac, "_niftybees_daily_returns", lambda as_of=None: equity)

    good = _synthetic_price_frame(30, seed=3, start="2026-01-01")
    too_short = _synthetic_price_frame(2, seed=4, start="2026-01-01")
    empty = pd.DataFrame(columns=["trade_date", "close"])
    monkeypatch.setattr(cac, "_COMMODITY_LEGS", {
        "good_leg": (lambda as_of=None: good, "close", "log_return"),
        "too_short_leg": (lambda as_of=None: too_short, "close", "log_return"),
        "empty_leg": (lambda as_of=None: empty, "close", "log_return"),
    })

    result = cac.equity_commodity_correlation(min_obs=5, persist=False)
    assert list(result["asset_b"]) == ["good_leg"]


def test_equity_commodity_correlation_raises_when_every_leg_is_skipped(monkeypatch):
    equity = pd.DataFrame({"trade_date": pd.bdate_range("2026-01-01", periods=30).strftime("%Y-%m-%d")})
    equity["log_return"] = 0.001
    monkeypatch.setattr(cac, "_niftybees_daily_returns", lambda as_of=None: equity)
    too_short = _synthetic_price_frame(2, seed=5, start="2026-01-01")
    monkeypatch.setattr(cac, "_COMMODITY_LEGS", {
        "too_short_leg": (lambda as_of=None: too_short, "close", "log_return"),
    })
    with pytest.raises(RuntimeError):
        cac.equity_commodity_correlation(min_obs=5, persist=False)


def test_equity_macro_correlation_skips_short_and_empty_legs(monkeypatch):
    months = pd.period_range("2026-01", periods=12, freq="M").to_timestamp("M")
    rng = np.random.default_rng(6)
    equity_monthly = pd.Series(rng.normal(0, 0.02, 12), index=months)
    monkeypatch.setattr(cac, "_niftybees_monthly_return", lambda as_of=None: equity_monthly)

    good_macro = pd.Series(rng.normal(0, 1, 12), index=months)
    short_macro = pd.Series([0.1, 0.2], index=months[:2])
    empty_macro = pd.Series(dtype=float)
    monkeypatch.setattr(cac, "_MACRO_LEGS", {
        "good_macro": lambda as_of=None: good_macro,
        "short_macro": lambda as_of=None: short_macro,
        "empty_macro": lambda as_of=None: empty_macro,
    })

    result = cac.equity_macro_correlation(min_months=6, persist=False)
    assert list(result["macro_series"]) == ["good_macro"]


def test_equity_macro_correlation_raises_when_every_leg_is_skipped(monkeypatch):
    months = pd.period_range("2026-01", periods=12, freq="M").to_timestamp("M")
    equity_monthly = pd.Series(np.full(12, 0.01), index=months)
    monkeypatch.setattr(cac, "_niftybees_monthly_return", lambda as_of=None: equity_monthly)
    short_macro = pd.Series([0.1, 0.2], index=months[:2])
    monkeypatch.setattr(cac, "_MACRO_LEGS", {"short_macro": lambda as_of=None: short_macro})
    with pytest.raises(RuntimeError):
        cac.equity_macro_correlation(min_months=6, persist=False)
