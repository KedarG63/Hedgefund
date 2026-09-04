"""
NIFTY/BANKNIFTY option chain: persistence fix + Black-Scholes/IV/Greeks.

PART A fixes a Phase 4 gap, not a broken endpoint: connectors.nse_bse.
nse_option_chain() already fetches live and calls save_raw() correctly (rule
1 is satisfied there); PHASE4.md records it as deliberately "Persisted: no
-- live/dashboard use only" at the time. flatten_option_chain() and
capture_and_persist_option_chain() close that gap without touching the
connector -- no new endpoint is invented.

This is a once-daily INTRADAY snapshot (whatever time run_daily.py runs),
not a continuous feed or an EOD settlement tape -- NSE's option-chain-v3
refreshes roughly every 3 minutes on their side. NSE's own
`impliedVolatility` per strike is persisted directly; that alone is a useful
daily IV-skew snapshot independent of whether Black-Scholes is computed on
top of it.

PART B prices with two documented simplifications:
  - dividend yield q=0 (no dividend/corporate-actions feed exists anywhere
    in this warehouse)
  - risk-free rate = RBI's overnight POLICY REPO RATE (rbi_key_indicators),
    not a term-matched G-Sec yield curve
Both distortions are smaller for INDEX options than single-stock options,
which is why Phase 1 options coverage is NIFTY/BANKNIFTY only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from connectors.nse_bse import nse_option_chain
from core.storage import db, register_views, write_table

DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY")
DEFAULT_DIVIDEND_YIELD = 0.0


# ------------------------------------------------------------- Part A: persist
def flatten_option_chain(raw: dict, symbol: str, fetched_at: datetime | None = None) -> pd.DataFrame:
    """
    records.data[] -> long format, one row per (strike, option_type). Each
    entry in the raw payload carries a top-level strikePrice plus a CE
    and/or PE sub-dict -- deep/illiquid strikes sometimes quote only one leg
    -- so both legs, when present, become separate output rows.
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)
    records = raw.get("records", {})
    underlying_value = records.get("underlyingValue")
    rows = []
    for entry in records.get("data", []):
        strike = entry.get("strikePrice")
        for leg in ("CE", "PE"):
            leg_data = entry.get(leg)
            if not leg_data:
                continue
            rows.append({
                "symbol": symbol,
                "strike_price": strike,
                "option_type": leg,
                "expiry_date": leg_data.get("expiryDate"),
                "open_interest": leg_data.get("openInterest"),
                "change_in_oi": leg_data.get("changeinOpenInterest"),
                "total_traded_volume": leg_data.get("totalTradedVolume"),
                "implied_volatility_nse": leg_data.get("impliedVolatility"),
                "last_price": leg_data.get("lastPrice"),
                "price_change": leg_data.get("change"),
                "bid_price": leg_data.get("buyPrice1"),
                "bid_qty": leg_data.get("buyQuantity1"),
                "ask_price": leg_data.get("sellPrice1"),
                "ask_qty": leg_data.get("sellQuantity1"),
                "underlying_value": leg_data.get("underlyingValue", underlying_value),
                "fetched_at_utc": fetched_at.isoformat(),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"flatten_option_chain({symbol}): no CE/PE rows in the payload.")
    return df


def capture_and_persist_option_chain(symbols=DEFAULT_SYMBOLS, persist: bool = True) -> pd.DataFrame:
    """
    Fetch each symbol's nearest-expiry chain (save_raw already happens
    inside nse_option_chain -- rule 1 is satisfied there), flatten,
    concatenate, write_table under nse/optchain.
    """
    fetched_at = datetime.now(timezone.utc)
    frames = []
    for symbol in symbols:
        raw = nse_option_chain(symbol=symbol, index=True)
        frames.append(flatten_option_chain(raw, symbol, fetched_at=fetched_at))
    df = pd.concat(frames, ignore_index=True)
    if persist:
        write_table(df, "nse", "optchain")
    return df


# ------------------------------------------------------- Part B: Black-Scholes
def black_scholes_price(S: float, K: float, T: float, r: float, sigma: float,
                        q: float = DEFAULT_DIVIDEND_YIELD, option_type: str = "C") -> float:
    """Standard Black-Scholes-Merton price with continuous dividend yield q."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if option_type == "C" else (K - S))
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "C":
        return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


def implied_vol_solve(price: float, S: float, K: float, T: float, r: float,
                      q: float = DEFAULT_DIVIDEND_YIELD, option_type: str = "C") -> float:
    """
    Solve for sigma via bracketed bisection (scipy.optimize.brentq) -- not
    Newton-Raphson, which can diverge near expiry or deep ITM/OTM. Bounded to
    [1e-4, 5.0] annualized vol, which comfortably covers observed India index
    option IVs (verified sample: 40-70+ for far OTM near-dated strikes).
    """
    if T <= 0:
        raise ValueError("implied_vol_solve: T must be > 0")

    def f(sigma):
        return black_scholes_price(S, K, T, r, sigma, q, option_type) - price

    lo, hi = 1e-4, 5.0
    if f(lo) > 0 or f(hi) < 0:
        raise RuntimeError(
            f"implied_vol_solve: price {price} is outside the achievable range for "
            f"S={S}, K={K}, T={T}, r={r} within sigma in [{lo}, {hi}]."
        )
    return brentq(f, lo, hi)


def greeks(S: float, K: float, T: float, r: float, sigma: float,
          q: float = DEFAULT_DIVIDEND_YIELD, option_type: str = "C") -> dict:
    """Closed-form delta/gamma/vega/theta/rho. vega is per 1 vol point, theta per day."""
    if T <= 0 or sigma <= 0:
        return {"delta": None, "gamma": None, "vega": None, "theta": None, "rho": None}
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    pdf_d1 = norm.pdf(d1)
    gamma = np.exp(-q * T) * pdf_d1 / (S * sigma * np.sqrt(T))
    vega = S * np.exp(-q * T) * pdf_d1 * np.sqrt(T) / 100
    if option_type == "C":
        delta = np.exp(-q * T) * norm.cdf(d1)
        theta = (-S * np.exp(-q * T) * pdf_d1 * sigma / (2 * np.sqrt(T))
                 - r * K * np.exp(-r * T) * norm.cdf(d2)
                 + q * S * np.exp(-q * T) * norm.cdf(d1)) / 365
        rho = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
    else:
        delta = -np.exp(-q * T) * norm.cdf(-d1)
        theta = (-S * np.exp(-q * T) * pdf_d1 * sigma / (2 * np.sqrt(T))
                 + r * K * np.exp(-r * T) * norm.cdf(-d2)
                 - q * S * np.exp(-q * T) * norm.cdf(-d1)) / 365
        rho = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


def _risk_free_rate() -> float:
    """
    RBI policy repo rate, as a decimal (e.g. 0.0525 for 5.25%). See the
    module docstring: this is the overnight policy rate, not a term-matched
    curve -- an acceptable simplification for near-dated index options.
    """
    con = db(read_only=True)
    try:
        register_views(con)
        row = con.execute("""
            SELECT value FROM rbi_key_indicators
            WHERE indicator = 'Policy Repo Rate' ORDER BY as_of DESC LIMIT 1
        """).fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError(
            "compute_greeks_for_chain needs a 'Policy Repo Rate' row in "
            "rbi_key_indicators -- run the rbi_key_indicators job first."
        )
    return float(row[0]) / 100.0


def compute_greeks_for_chain(persist: bool = True) -> pd.DataFrame:
    """
    Reads the most recently persisted nse_optchain snapshot, computes T from
    expiry_date vs fetched_at_utc, S from underlying_value, sigma from NSE's
    own impliedVolatility -- also cross-checked against a locally solved IV
    off last_price. A material divergence between the two IVs is itself a
    data-quality signal worth surfacing, not just noise.
    """
    r = _risk_free_rate()
    con = db(read_only=True)
    try:
        register_views(con)
        chain = con.execute("""
            SELECT * FROM nse_optchain
            WHERE fetched_at_utc = (SELECT max(fetched_at_utc) FROM nse_optchain)
        """).df()
    finally:
        con.close()
    if chain.empty:
        raise RuntimeError(
            "compute_greeks_for_chain: nse_optchain is empty -- run "
            "capture_and_persist_option_chain() first."
        )

    # fetched_at_utc is stored as a tz-aware isoformat string; expiry_date
    # has no time component and parses naive. Strip tz before subtracting --
    # mixing a tz-aware and a tz-naive timestamp raises in pandas.
    fetched_at = pd.to_datetime(chain["fetched_at_utc"].iloc[0]).tz_localize(None)
    expiry = pd.to_datetime(chain["expiry_date"], format="%d-%m-%Y")
    chain = chain.assign(T_years=(expiry - fetched_at).dt.total_seconds() / (365.0 * 24 * 3600))
    chain = chain[chain["T_years"] > 0]

    rows = []
    for _, row in chain.iterrows():
        S, K, T = row["underlying_value"], row["strike_price"], row["T_years"]
        opt_type = "C" if row["option_type"] == "CE" else "P"
        sigma_nse = (row["implied_volatility_nse"] or 0) / 100.0
        out = {
            "symbol": row["symbol"], "strike_price": K, "option_type": row["option_type"],
            "expiry_date": row["expiry_date"], "as_of_date": fetched_at.date().isoformat(),
            "underlying_value": S, "risk_free_rate": r,
        }
        if sigma_nse > 0:
            out.update({f"nse_{k}": v for k, v in
                       greeks(S, K, T, r, sigma_nse, option_type=opt_type).items()})
            out["theoretical_price_at_nse_iv"] = black_scholes_price(
                S, K, T, r, sigma_nse, option_type=opt_type)
        if row.get("last_price"):
            try:
                solved = implied_vol_solve(row["last_price"], S, K, T, r, option_type=opt_type) * 100
                out["implied_volatility_solved"] = solved
                out["iv_divergence_vs_nse"] = solved - (row["implied_volatility_nse"] or 0)
            except (RuntimeError, ValueError):
                pass
        rows.append(out)

    df = pd.DataFrame(rows)
    if persist and not df.empty:
        write_table(df, "derived", "option_greeks")
    return df
