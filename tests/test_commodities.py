"""
Offline tests for the commodities layer and the India gold premium.

The premium is the point of the module, so its arithmetic and its unit
conversion get the most attention -- a wrong constant there produces a number
that looks like a spread and is not one.
"""
import pandas as pd
import pytest

from connectors.commodities import (
    CME_PRODUCTS,
    DEFAULT_GST_PCT,
    DEFAULT_IMPORT_DUTY_PCT,
    TROY_OZ_GRAMS,
    IBJA_PURITIES,
)


# ------------------------------------------------------------------ constants
def test_troy_ounce_constant():
    """
    The whole premium hangs off this. A troy ounce is 31.1035 g, NOT the 28.35 g
    avoirdupois ounce -- confusing them misprices gold by ~9%, which is larger
    than the spread being measured.
    """
    assert TROY_OZ_GRAMS == pytest.approx(31.1034768)
    assert TROY_OZ_GRAMS != pytest.approx(28.349523, abs=0.01)


def test_ibja_purities_are_gold_finenesses():
    assert IBJA_PURITIES == ("999", "995", "916", "750", "585")


def test_cme_products_have_integer_ids():
    assert all(isinstance(v, int) for v in CME_PRODUCTS.values())
    assert "gold" in CME_PRODUCTS and "silver" in CME_PRODUCTS


# ----------------------------------------------------------- premium mechanics
def landed(comex_usd_oz, usdinr, duty=DEFAULT_IMPORT_DUTY_PCT, gst=DEFAULT_GST_PCT):
    """Mirror of the production calculation, for arithmetic assertions."""
    inr_per_10g = comex_usd_oz * usdinr / TROY_OZ_GRAMS * 10.0
    return inr_per_10g * (1 + duty / 100) * (1 + gst / 100)


def test_landed_cost_matches_live_numbers():
    """
    Real observation 2026-08-21: COMEX front month 4624.1 $/oz, RBI USDINR
    95.7467 -> 142,345 Rs/10g before levies, 155,412 after 6% duty and 3% GST.
    """
    raw = 4624.1 * 95.7467 / TROY_OZ_GRAMS * 10.0
    assert raw == pytest.approx(142_344.96, abs=1.0)
    assert landed(4624.1, 95.7467) == pytest.approx(155_412.23, abs=1.0)


def test_premium_sign_and_magnitude():
    """IBJA 999 PM 160,620 against that landed cost is a ~3.35% premium."""
    l = landed(4624.1, 95.7467)
    premium = 160_620 - l
    assert premium > 0
    assert premium / l * 100 == pytest.approx(3.35, abs=0.05)


def test_discount_is_representable():
    """
    India has traded BELOW import parity in weak-demand periods. The series must
    be able to go negative rather than being clipped at zero.
    """
    l = landed(4624.1, 95.7467)
    assert 150_000 - l < 0


def test_premium_level_is_sensitive_to_the_duty_assumption():
    """
    Duty is a POLICY variable, not market data. Changing it moves the LEVEL
    materially, which is why the docstring calls the level provisional and the
    time series the signal.
    """
    at_6 = 160_620 - landed(4624.1, 95.7467, duty=6.0)
    at_15 = 160_620 - landed(4624.1, 95.7467, duty=15.0)
    assert at_6 > at_15
    assert abs(at_6 - at_15) > 10_000, "duty choice must visibly move the level"


def test_premium_changes_survive_a_wrong_duty_assumption():
    """
    The reassuring half: a constant duty error shifts every observation by
    nearly the same amount, so DAY-ON-DAY CHANGES stay usable even if the
    absolute level is wrong.
    """
    d1 = (160_620 - landed(4624.1, 95.7467, duty=6.0)) - \
         (159_499 - landed(4624.1, 95.7467, duty=6.0))
    d2 = (160_620 - landed(4624.1, 95.7467, duty=15.0)) - \
         (159_499 - landed(4624.1, 95.7467, duty=15.0))
    assert d1 == pytest.approx(d2, abs=1e-6)
    assert d1 == pytest.approx(1121.0, abs=1.0)


# --------------------------------------------------- CME response peculiarities
def test_total_row_is_identified_not_priced():
    """
    CME appends a 'Total' row whose settle is '-' but whose volume and open
    interest are curve-wide sums. Treating it as a contract double counts the
    entire curve.
    """
    df = pd.DataFrame({"month": ["AUG 26", "SEP 26", "Total"],
                       "settle": ["4624.1", "4628.3", "-"],
                       "volume": ["311", "5,249", "239,861"]})
    df["is_total"] = df["month"].str.strip().str.lower().eq("total")
    assert df["is_total"].tolist() == [False, False, True]
    real = df[~df["is_total"]]
    assert len(real) == 2
    assert pd.to_numeric(real["volume"].str.replace(",", ""),
                         errors="coerce").sum() == 5560


def test_front_month_comes_from_order_not_price():
    """
    Gold was in contango on the day this was built, so min(settle) happened to
    be the front month. In backwardation that picks the BACK month -- a silent
    error that only appears when the curve inverts.
    """
    backwardation = pd.DataFrame({
        "month": ["AUG 26", "SEP 26", "DEC 26"],
        "settle_num": [4700.0, 4650.0, 4600.0],   # falling with maturity
        "is_total": [False, False, False],
    })
    backwardation["contract_order"] = range(len(backwardation))
    by_order = backwardation.loc[backwardation["contract_order"].idxmin(), "month"]
    by_price = backwardation.loc[backwardation["settle_num"].idxmin(), "month"]
    assert by_order == "AUG 26"
    assert by_price == "DEC 26", "price-based selection is wrong under backwardation"


def test_contract_order_skips_the_total_row():
    df = pd.DataFrame({"month": ["AUG 26", "SEP 26", "Total"]})
    df["is_total"] = df["month"].str.strip().str.lower().eq("total")
    order = (~df["is_total"]).cumsum() - 1
    order[df["is_total"]] = pd.NA
    assert order.tolist()[:2] == [0, 1]
    assert pd.isna(order.iloc[2])


# ------------------------------------------------------------------ MCX shape
def test_mcx_futures_must_be_filtered_from_options():
    """
    A typical MCX day is ~16,700 rows of which ~145 are futures. Reading GOLD
    without filtering InstrumentName returns option premiums (0.5, 40,850)
    alongside the real futures price (162,438).
    """
    df = pd.DataFrame({
        "Symbol": ["GOLD"] * 4,
        "InstrumentName": ["OPTFUT", "OPTFUT", "FUTCOM", "OPTFUT"],
        "Close": [0.5, 40850.5, 162438.0, 34861.5],
    })
    fut = df[df["InstrumentName"] == "FUTCOM"]
    assert len(fut) == 1
    assert fut["Close"].iloc[0] == 162438.0

    # Unfiltered, the same Symbol carries values three orders of magnitude
    # apart, none of which except the FUTCOM row is a gold price. Any
    # aggregate over them -- mean, min, first -- is meaningless.
    assert df["Close"].min() == 0.5
    assert df["Close"].mean() < 100_000
    assert df[df["InstrumentName"] != "FUTCOM"]["Close"].max() == 40850.5
