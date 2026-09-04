"""
The XBRL/screener merge, pinned.

These properties were previously enforced only by whatever dashboard/app.py
happened to do at its single call site. Now that two surfaces will assemble
statements from api.serializers.fundamentals, the merge rule and the
fiscal-year labelling are a contract and need to fail loudly when broken.

All fixtures are hand-built and offline -- no warehouse, no network.
"""
import pandas as pd
import pytest

from api.serializers.fundamentals import (
    BALANCE_CHECK_LABEL,
    INCOME_STATEMENT_PER_SHARE,
    balance_sheet,
    cash_flow,
    fy_label,
    fy_quarter_label,
    income_statement,
    label_with_units,
)

CR = 1e7  # XBRL reports rupees; a crore is 1e7 of them.


def _xbrl(rows) -> pd.DataFrame:
    """Long XBRL fact table in the shape connectors.nse_fundamentals produces."""
    return pd.DataFrame(
        [{"symbol": "RELIANCE", "consolidated": "Consolidated", "filingDate": "2025-07-18",
          "has_dimensions": False, **r} for r in rows]
    )


def _screener(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ------------------------------------------------------------ fiscal labelling

@pytest.mark.parametrize("period_end,expected", [
    ("2025-06-30", "Q1 FY25-26"),   # Apr-Jun is Q1, not Q2 of a calendar year
    ("2025-09-30", "Q2 FY25-26"),
    ("2025-12-31", "Q3 FY25-26"),
    ("2026-03-31", "Q4 FY25-26"),   # Jan-Mar belongs to the PREVIOUS fiscal year
])
def test_quarter_labels_follow_the_april_march_fiscal_year(period_end, expected):
    assert fy_quarter_label(period_end) == expected


def test_march_belongs_to_the_fiscal_year_that_started_the_previous_april():
    assert fy_label("2025-03-31") == "FY24-25"
    assert fy_label("2025-04-01") == "FY25-26"


# ------------------------------------------------------------------ merge rule

def test_xbrl_wins_where_both_sources_cover_a_period_and_screener_only_extends():
    """
    The single most important property in this module. XBRL exhibits are the
    filed figures; screener.in is a convenience source that is usually a
    quarter ahead. Where both publish a period, the filed number must survive
    -- and screener must still be able to add periods XBRL has not reached.
    """
    shown = _xbrl([
        {"period_start": "2025-04-01", "period_end": "2025-06-30",
         "concept": "RevenueFromOperations", "value": 240_000 * CR},
        {"period_start": "2025-04-01", "period_end": "2025-06-30",
         "concept": "ProfitLossForPeriod", "value": 18_000 * CR},
    ])
    screener_long = _screener([
        # Same period as XBRL, deliberately disagreeing -- XBRL must win.
        {"statement": "income_statement", "period_type": "quarterly",
         "concept": "Sales", "period_end": "2025-06-30", "value": 999_999.0},
        # A period XBRL does not have yet -- screener must extend into it.
        {"statement": "income_statement", "period_type": "quarterly",
         "concept": "Sales", "period_end": "2025-09-30", "value": 245_000.0},
    ])

    df = income_statement(shown, screener_long)

    assert df.loc["Revenue", "Q1 FY25-26"] == 240_000, "screener overwrote a filed XBRL figure"
    assert df.loc["Revenue", "Q2 FY25-26"] == 245_000, "screener failed to extend the timeline"
    assert df.loc["Net Profit", "Q1 FY25-26"] == 18_000


def test_columns_are_most_recent_first_and_rows_in_statement_order():
    shown = _xbrl([
        {"period_start": "2025-04-01", "period_end": "2025-06-30",
         "concept": "RevenueFromOperations", "value": 240_000 * CR},
        {"period_start": "2025-07-01", "period_end": "2025-09-30",
         "concept": "RevenueFromOperations", "value": 245_000 * CR},
    ])
    df = income_statement(shown)
    assert list(df.columns) == ["Q2 FY25-26", "Q1 FY25-26"]
    # Natural reading order of a P&L: revenue above profit, not alphabetical.
    assert list(df.index).index("Revenue") < list(df.index).index("Net Profit")


# ----------------------------------------------------------------------- units

def test_rupee_figures_are_scaled_to_crore_but_per_share_figures_are_not():
    shown = _xbrl([
        {"period_start": "2025-04-01", "period_end": "2025-06-30",
         "concept": "RevenueFromOperations", "value": 240_000 * CR},
        {"period_start": "2025-04-01", "period_end": "2025-06-30",
         "concept": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
         "value": 26.5},
    ])
    df = income_statement(shown)
    assert df.loc["Revenue", "Q1 FY25-26"] == 240_000
    assert df.loc["EPS (Basic)", "Q1 FY25-26"] == 26.5, "EPS was crore-scaled"


def test_label_with_units_marks_per_share_rows_differently():
    df = pd.DataFrame({"Q1 FY25-26": [240_000.0, 26.5]}, index=["Revenue", "EPS (Basic)"])
    out = label_with_units(df, INCOME_STATEMENT_PER_SHARE)
    assert list(out.index) == ["Revenue (Rs Cr)", "EPS (Basic) (Rs/share)"]


# --------------------------------------------------------------- balance sheet

def test_balance_check_row_is_appended_and_reads_zero_when_the_sheet_balances():
    """The check row is part of the statement, not a dashboard flourish -- an
    API response that omitted it would drop the only built-in signal that the
    merge picked up two different period definitions under one label."""
    shown = _xbrl([
        # Balance-sheet facts are instant: no period_start.
        {"period_start": None, "period_end": "2025-03-31",
         "concept": "Assets", "value": 1_750_000 * CR},
        {"period_start": None, "period_end": "2025-03-31",
         "concept": "EquityAndLiabilities", "value": 1_750_000 * CR},
    ])
    df = balance_sheet(shown)
    assert BALANCE_CHECK_LABEL in df.index
    assert df.loc[BALANCE_CHECK_LABEL, "FY24-25"] == 0


def test_half_year_snapshot_does_not_collide_with_the_fiscal_year_end():
    """
    REGRESSION: fy_label() only asks which fiscal year a date falls in, so the
    Sep (H1) balance-sheet snapshot and the Mar fiscal-year-end snapshot both
    label as "FY24-25". Before full_year_only= filtered on month, that produced
    two differently-valued FY24-25 columns in one table.
    """
    shown = _xbrl([
        {"period_start": None, "period_end": "2024-09-30",
         "concept": "Assets", "value": 1_600_000 * CR},   # H1 -- must be dropped
        {"period_start": None, "period_end": "2025-03-31",
         "concept": "Assets", "value": 1_750_000 * CR},   # FY end -- must survive
    ])
    df = balance_sheet(shown)
    assert list(df.columns).count("FY24-25") == 1, f"duplicate period columns: {list(df.columns)}"
    assert df.loc["Total Assets", "FY24-25"] == 1_750_000, "kept the half-year snapshot"


# ------------------------------------------------------------------- cash flow

def test_cash_flow_keeps_the_full_year_and_drops_the_half_year():
    """Cash-flow facts carry a real duration, so the full year is separated
    from H1 by span (>200 days) rather than by month."""
    shown = _xbrl([
        {"period_start": "2024-04-01", "period_end": "2024-09-30",
         "concept": "CashFlowsFromUsedInOperatingActivities", "value": 60_000 * CR},
        {"period_start": "2024-04-01", "period_end": "2025-03-31",
         "concept": "CashFlowsFromUsedInOperatingActivities", "value": 145_000 * CR},
    ])
    df = cash_flow(shown)
    assert list(df.columns) == ["FY24-25"]
    assert df.loc["Cash from Operating Activity", "FY24-25"] == 145_000


def test_annual_facts_never_appear_as_a_quarter():
    """A full-year fact filed alongside the Q4 exhibit would otherwise show up
    as a quarterly column worth four quarters of revenue."""
    shown = _xbrl([
        {"period_start": "2024-04-01", "period_end": "2025-03-31",
         "concept": "RevenueFromOperations", "value": 960_000 * CR},   # annual
        {"period_start": "2025-01-01", "period_end": "2025-03-31",
         "concept": "RevenueFromOperations", "value": 245_000 * CR},   # Q4
    ])
    df = income_statement(shown)
    assert df.loc["Revenue", "Q4 FY24-25"] == 245_000


# ------------------------------------------------------------------ empty input

def test_no_facts_returns_an_empty_frame_rather_than_raising():
    """Every caller branches on `.empty`; raising here would take down the tab."""
    empty = _xbrl([]).reindex(columns=["symbol", "consolidated", "period_start",
                                       "period_end", "filingDate", "concept",
                                       "has_dimensions", "value"])
    for fn in (income_statement, balance_sheet, cash_flow):
        out = fn(empty, None)
        assert isinstance(out, pd.DataFrame) and out.empty
