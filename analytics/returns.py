"""
Return/volatility building blocks shared by capm.py, correlation.py,
momentum.py and digest.py.

Library functions only -- no write_table() here. This mirrors how
connectors.commodities.usdinr_reference_rate() is a helper other functions
call, not something run_daily.py schedules directly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def log_returns(prices: pd.DataFrame, price_col: str = "close",
                symbol_col: str = "symbol", date_col: str = "trade_date") -> pd.DataFrame:
    """
    ln(P_t / P_{t-1}) per symbol, sorted by date. Adds a `log_return` column.
    Input must have at most one row per (symbol, date); duplicate rows for
    the same key will produce a wrong ratio for that step.
    """
    df = prices.sort_values([symbol_col, date_col]).copy()
    df["log_return"] = df.groupby(symbol_col)[price_col].transform(
        lambda s: np.log(s / s.shift(1)))
    return df


def realized_vol(returns: pd.Series, window: int = 20, annualize: bool = True) -> pd.Series:
    """Rolling stdev of log returns. Annualized by sqrt(252) unless annualize=False."""
    vol = returns.rolling(window, min_periods=window).std()
    return vol * np.sqrt(TRADING_DAYS_PER_YEAR) if annualize else vol


def sharpe_ratio(returns: pd.Series, rf_annual: float, window: int = 60) -> pd.Series:
    """
    Rolling annualized Sharpe: (mean daily return * 252 - rf_annual) / annualized vol.
    rf_annual is a decimal (0.0525 for 5.25%), not a percentage.
    """
    mean_ann = returns.rolling(window, min_periods=window).mean() * TRADING_DAYS_PER_YEAR
    vol_ann = realized_vol(returns, window=window, annualize=True)
    return (mean_ann - rf_annual) / vol_ann
