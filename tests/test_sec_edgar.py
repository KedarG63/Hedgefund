"""
Offline tests for the SEC EDGAR connector's pure logic.

Deliberately no network: these cover the parts that silently corrupt a
fundamentals panel when they drift -- company-name matching (a bad match
attributes one company's numbers to another) and period selection.
"""
from datetime import date

import pandas as pd
import pytest

from connectors.sec_edgar import (
    FUNDAMENTAL_FIELDS,
    _normalise_name,
    latest_completed_quarter,
)


def norm(s: str) -> str:
    return _normalise_name(pd.Series([s])).iloc[0]


@pytest.mark.parametrize("nport,edgar", [
    # the real N-PORT vs EDGAR pairs that broke earlier revisions
    ("Air Products and Chemicals Inc", "AIR PRODUCTS & CHEMICALS INC"),
    ("Bank of America Corp", "BANK OF AMERICA CORP /DE/"),
    ("Clorox Co/The", "CLOROX CO /DE/"),
    ("Costco Wholesale Corp", "COSTCO WHOLESALE CORP /NEW"),
    ("US Bancorp", "US BANCORP \\DE\\"),
    ("Lowe's Cos Inc", "LOWES COMPANIES INC"),
    ("Moody's Corp", "MOODYS CORP /DE/"),
    ("AT&T Inc", "AT&T INC."),
    # EDGAR writes ".com" as a separate word; gluing it strands the name
    ("Amazon.com Inc", "AMAZON COM INC"),
    ("McDonald's Corp", "MCDONALDS CORP"),
    ("NXP Semiconductors NV", "NXP Semiconductors N.V."),
])
def test_name_normalisation_aligns_nport_with_edgar(nport, edgar):
    assert norm(nport) == norm(edgar), f"{nport!r} should match {edgar!r}"


def test_the_suffix_is_stripped_before_state_code():
    """
    Ordering regression: a "/DE/"-style rule applied first consumes "/TH" out of
    "/The" and leaves a stray "E", which silently breaks every "Co/The" name.
    """
    assert "E" not in norm("Boeing Co/The").split()
    assert norm("Boeing Co/The") == "BOEING"


def test_distinct_companies_do_not_collide():
    """Guard the loose keys: normalisation must not merge unrelated issuers."""
    names = ["Southern Co", "W R Berkley Corp", "Exxon Mobil Corp",
             "Alico Inc", "Equifax Inc", "Moody's Corp", "Bank of Hawaii Corp",
             "WW Grainger Inc"]
    keys = [norm(n) for n in names]
    assert len(set(keys)) == len(names), f"collision among {list(zip(names, keys))}"


def test_sorted_token_key_matches_inverted_names():
    """EDGAR files surnames first; the sorted-token fallback must bridge that."""
    a = " ".join(sorted(norm("W R Berkley Corp").split()))
    b = " ".join(sorted(norm("BERKLEY W R CORP").split()))
    assert a == b


@pytest.mark.parametrize("today,expected", [
    (date(2026, 8, 22), "CY2026Q1"),   # in Q3 -> back two -> Q1 same year
    (date(2026, 1, 15), "CY2025Q3"),   # in Q1 -> back two -> prior year Q3
    (date(2026, 5, 2),  "CY2025Q4"),   # in Q2 -> back two -> prior year Q4
    (date(2026, 11, 30), "CY2026Q2"),  # in Q4 -> back two -> Q2 same year
])
def test_latest_completed_quarter_lags_two_quarters(today, expected):
    assert latest_completed_quarter(today) == expected


def test_fundamental_fields_are_well_formed():
    """Each field needs a unit, an instant flag, and at least one concept."""
    for name, spec in FUNDAMENTAL_FIELDS.items():
        assert isinstance(spec["instant"], bool), name
        assert spec["unit"] in ("USD", "shares"), name
        assert spec["concepts"], name
        for taxonomy, concept in spec["concepts"]:
            assert taxonomy in ("us-gaap", "dei"), (name, taxonomy)
            assert concept and concept[0].isupper(), (name, concept)


def test_flow_and_stock_fields_are_classified_correctly():
    """
    Revenue and net income are measured OVER a quarter; assets, equity and
    share count are measured AT its end. Getting this backwards asks SEC for a
    frame that does not exist and silently drops the field.
    """
    assert FUNDAMENTAL_FIELDS["revenue"]["instant"] is False
    assert FUNDAMENTAL_FIELDS["net_income"]["instant"] is False
    assert FUNDAMENTAL_FIELDS["assets"]["instant"] is True
    assert FUNDAMENTAL_FIELDS["equity"]["instant"] is True
    assert FUNDAMENTAL_FIELDS["shares_outstanding"]["instant"] is True
