"""
Offline tests for the proxy / pay-versus-performance connector.

The arithmetic is simple; the risk is misreading it. These pin the two
interpretations that matter: what pay_premium means, and when it means nothing.
"""
import pandas as pd
import pytest

from connectors.sec_proxy import PROXY_FORMS, PVP_CONCEPTS, pay_vs_tsr


def pvp_rows(rows):
    """Build a long pay-versus-performance frame the way the connector emits it."""
    return pd.DataFrame([
        {"cik": r.get("cik", 1), "entity": r.get("entity", "ACME"),
         "fiscal_year": r["fy"], "concept": r["concept"], "value": r["value"],
         "meaning": PVP_CONCEPTS.get(r["concept"]), "unit": "USD",
         "start": None, "end": f"{r['fy']}-12-31", "form": "DEF 14A",
         "filed": f"{int(r['fy']) + 1}-04-01", "accn": "A-1"}
        for r in rows
    ])


# ------------------------------------------------------------------ concepts
def test_pvp_concepts_cover_pay_and_return_sides():
    """A pay-for-performance screen needs both halves or it says nothing."""
    assert "PeoActuallyPaidCompAmt" in PVP_CONCEPTS      # what the CEO earned
    assert "PeoTotalCompAmt" in PVP_CONCEPTS
    assert "TotalShareholderRtnAmt" in PVP_CONCEPTS      # what holders earned
    assert "PeerGroupTotalShareholderRtnAmt" in PVP_CONCEPTS


def test_proxy_forms_include_definitive_and_preliminary():
    assert "DEF 14A" in PROXY_FORMS and "PRE 14A" in PROXY_FORMS


# --------------------------------------------------------------- derivations
def test_tsr_vs_peer_and_pay_premium():
    df = pay_vs_tsr(pvp_rows([
        {"fy": "2024", "concept": "PeoTotalCompAmt", "value": 10_000_000.0},
        {"fy": "2024", "concept": "PeoActuallyPaidCompAmt", "value": 25_000_000.0},
        {"fy": "2024", "concept": "TotalShareholderRtnAmt", "value": 90.0},
        {"fy": "2024", "concept": "PeerGroupTotalShareholderRtnAmt", "value": 120.0},
    ]), persist=False)
    row = df.iloc[0]
    assert row["pay_premium"] == pytest.approx(2.5)
    assert row["tsr_vs_peer"] == pytest.approx(-30.0)
    assert bool(row["pay_up_performance_down"]) is True


def test_outperforming_peers_is_not_flagged():
    df = pay_vs_tsr(pvp_rows([
        {"fy": "2024", "concept": "PeoTotalCompAmt", "value": 10_000_000.0},
        {"fy": "2024", "concept": "PeoActuallyPaidCompAmt", "value": 25_000_000.0},
        {"fy": "2024", "concept": "TotalShareholderRtnAmt", "value": 150.0},
        {"fy": "2024", "concept": "PeerGroupTotalShareholderRtnAmt", "value": 120.0},
    ]), persist=False)
    assert bool(df.iloc[0]["pay_up_performance_down"]) is False


def test_pay_below_disclosed_is_not_flagged():
    """
    Actually-paid BELOW disclosed means unvested equity lost value -- pay fell
    with the stock, which is alignment working, not a governance failure.
    """
    df = pay_vs_tsr(pvp_rows([
        {"fy": "2024", "concept": "PeoTotalCompAmt", "value": 10_000_000.0},
        {"fy": "2024", "concept": "PeoActuallyPaidCompAmt", "value": 4_000_000.0},
        {"fy": "2024", "concept": "TotalShareholderRtnAmt", "value": 70.0},
        {"fy": "2024", "concept": "PeerGroupTotalShareholderRtnAmt", "value": 120.0},
    ]), persist=False)
    assert df.iloc[0]["pay_premium"] == pytest.approx(0.4)
    assert bool(df.iloc[0]["pay_up_performance_down"]) is False


def test_founder_equity_revaluation_produces_an_extreme_premium():
    """
    Coinbase FY2021: $3.27m disclosed, $2,118m actually paid -- founder stock
    revalued at IPO, not a pay award. The metric is correct; the governance
    READING is not. The premium must stay visible so the caller can see 648x
    and go and look rather than trusting the flag.
    """
    df = pay_vs_tsr(pvp_rows([
        {"fy": "2021", "entity": "Coinbase Global, Inc.",
         "concept": "PeoTotalCompAmt", "value": 3_267_027.0},
        {"fy": "2021", "entity": "Coinbase Global, Inc.",
         "concept": "PeoActuallyPaidCompAmt", "value": 2_118_756_064.0},
        {"fy": "2021", "entity": "Coinbase Global, Inc.",
         "concept": "TotalShareholderRtnAmt", "value": 77.0},
        {"fy": "2021", "entity": "Coinbase Global, Inc.",
         "concept": "PeerGroupTotalShareholderRtnAmt", "value": 115.0},
    ]), persist=False)
    row = df.iloc[0]
    assert row["pay_premium"] > 600
    assert row["ceo_pay_disclosed"] == pytest.approx(3_267_027.0)


# ------------------------------------------------------------- missing data
def test_missing_ceo_concepts_yield_nan_not_zero():
    """
    Only 22% of the S&P 500 tag PeoTotalCompAmt. Absent must read as unknown --
    a zero would make every untagged company look unpaid.
    """
    df = pay_vs_tsr(pvp_rows([
        {"fy": "2024", "concept": "TotalShareholderRtnAmt", "value": 90.0},
        {"fy": "2024", "concept": "PeerGroupTotalShareholderRtnAmt", "value": 120.0},
    ]), persist=False)
    row = df.iloc[0]
    assert pd.isna(row["ceo_pay_disclosed"])
    assert pd.isna(row["pay_premium"])
    assert bool(row["pay_up_performance_down"]) is False
    assert row["tsr_vs_peer"] == pytest.approx(-30.0), "the TSR half still works"


def test_empty_input_returns_empty():
    assert pay_vs_tsr(pd.DataFrame(), persist=False).empty


def test_one_row_per_company_year():
    df = pay_vs_tsr(pvp_rows([
        {"fy": "2023", "concept": "PeoTotalCompAmt", "value": 1.0},
        {"fy": "2023", "concept": "PeoActuallyPaidCompAmt", "value": 2.0},
        {"fy": "2024", "concept": "PeoTotalCompAmt", "value": 3.0},
        {"fy": "2024", "concept": "PeoActuallyPaidCompAmt", "value": 4.0},
    ]), persist=False)
    assert len(df) == 2
    assert sorted(df["fiscal_year"]) == ["2023", "2024"]
