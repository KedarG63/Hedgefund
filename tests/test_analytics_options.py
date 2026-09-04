"""
Offline tests for Black-Scholes pricing, IV solving, Greeks, and the
option-chain flattening logic. black_scholes_price/implied_vol_solve/
greeks/flatten_option_chain are pure functions -- no network or DB call.
"""
import numpy as np
import pytest

from analytics.options import (
    black_scholes_price, flatten_option_chain, greeks, implied_vol_solve,
)


def test_black_scholes_matches_textbook_reference():
    # S=100, K=100, T=1, r=0.05, sigma=0.2 -> the standard textbook call
    # price ~10.4506 (verified independently via scipy.stats.norm above).
    price = black_scholes_price(S=100, K=100, T=1, r=0.05, sigma=0.2, option_type="C")
    assert price == pytest.approx(10.4506, abs=0.01)


def test_put_call_parity_holds():
    """C - P = S*e^(-qT) - K*e^(-rT), for any consistent set of inputs."""
    S, K, T, r, sigma, q = 24000.0, 24200.0, 0.05, 0.0525, 0.15, 0.0
    call = black_scholes_price(S, K, T, r, sigma, q, "C")
    put = black_scholes_price(S, K, T, r, sigma, q, "P")
    expected = S * np.exp(-q * T) - K * np.exp(-r * T)
    assert (call - put) == pytest.approx(expected, abs=1e-6)


def test_implied_vol_round_trips_a_known_sigma():
    S, K, T, r = 24000.0, 24200.0, 0.05, 0.0525
    true_sigma = 0.18
    price = black_scholes_price(S, K, T, r, true_sigma, option_type="C")
    solved = implied_vol_solve(price, S, K, T, r, option_type="C")
    assert solved == pytest.approx(true_sigma, abs=1e-4)


def test_expired_option_prices_at_intrinsic_value():
    assert black_scholes_price(S=100, K=90, T=0, r=0.05, sigma=0.3, option_type="C") == 10
    assert black_scholes_price(S=100, K=110, T=0, r=0.05, sigma=0.3, option_type="P") == 10
    assert black_scholes_price(S=100, K=110, T=0, r=0.05, sigma=0.3, option_type="C") == 0


def test_greeks_call_delta_is_between_zero_and_one():
    g = greeks(S=24000, K=24200, T=0.05, r=0.0525, sigma=0.15, option_type="C")
    assert 0 < g["delta"] < 1
    assert g["gamma"] > 0
    assert g["vega"] > 0


def test_greeks_put_delta_is_between_minus_one_and_zero():
    g = greeks(S=24000, K=24200, T=0.05, r=0.0525, sigma=0.15, option_type="P")
    assert -1 < g["delta"] < 0


def test_flatten_option_chain_matches_the_actual_captured_shape():
    """
    Mirrors the real captured payload -- see
    data/raw/nse/optchain_NIFTY/2026-08-22/134721_3840e5c8.json, one strike
    (21200) with both CE and PE legs, field names copied verbatim.
    """
    raw = {
        "records": {
            "underlyingValue": 24252,
            "data": [{
                "strikePrice": 21200,
                "expiryDates": "25-Aug-2026",
                "CE": {
                    "expiryDate": "25-08-2026", "openInterest": 116,
                    "changeinOpenInterest": -8, "totalTradedVolume": 10,
                    "impliedVolatility": 72.18, "lastPrice": 3100, "change": -20.8,
                    "buyPrice1": 3042.15, "buyQuantity1": 65,
                    "sellPrice1": 3101.3, "sellQuantity1": 130,
                    "underlyingValue": 24252,
                },
                "PE": {
                    "expiryDate": "25-08-2026", "openInterest": 42036,
                    "changeinOpenInterest": -2774, "totalTradedVolume": 49395,
                    "impliedVolatility": 43.71, "lastPrice": 0.45, "change": -0.3,
                    "buyPrice1": 0.4, "buyQuantity1": 24050,
                    "sellPrice1": 0.45, "sellQuantity1": 40495,
                    "underlyingValue": 24252,
                },
            }],
        },
    }
    df = flatten_option_chain(raw, "NIFTY")
    assert len(df) == 2
    assert set(df["option_type"]) == {"CE", "PE"}
    assert df.loc[df["option_type"] == "CE", "implied_volatility_nse"].iloc[0] == 72.18
    assert df.loc[df["option_type"] == "PE", "last_price"].iloc[0] == 0.45
    assert df.loc[df["option_type"] == "CE", "price_change"].iloc[0] == -20.8


def test_flatten_option_chain_handles_a_single_quoted_leg():
    """Deep/illiquid strikes sometimes carry only CE or only PE."""
    raw = {"records": {"underlyingValue": 100, "data": [
        {"strikePrice": 500, "CE": {"expiryDate": "25-08-2026", "lastPrice": 1.0}},
    ]}}
    df = flatten_option_chain(raw, "NIFTY")
    assert len(df) == 1
    assert df["option_type"].iloc[0] == "CE"


def test_flatten_option_chain_raises_on_an_empty_payload():
    with pytest.raises(RuntimeError):
        flatten_option_chain({"records": {"data": []}}, "NIFTY")
