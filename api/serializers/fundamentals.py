"""
Canonical income statement / balance sheet / cash flow for an Indian listed
company, merged from two sources that disagree about coverage but not about
numbers.

THE MERGE RULE: XBRL wins on any period both sources cover; screener.in only
ever EXTENDS the timeline into periods XBRL does not have yet. That is a
CURRENCY extension, not a reconciliation between disagreeing numbers for the
same period -- NSE's XBRL exhibits are the filed, audited figures and are
authoritative wherever they exist, while screener.in is usually a quarter or
two ahead of the XBRL attachment appearing. Never "average" or "prefer
non-null" across the two; that would silently mint a number neither source
published.

Every concept mapping below was observed live in both connectors. Nothing is
guessed -- a plausible-looking wrong mapping produces a number that is wrong
in a way no one notices, which is worse than a blank cell.

WHY THIS LIVES IN api/ AND NOT dashboard/: the maps and the fiscal-year
labelling ARE the domain knowledge, not the view. They moved out of
dashboard/app.py so that a second surface (the React terminal's
/api/instrument/{symbol}/fundamentals) consumes the same assembly rather than
reimplementing it in another language. The Streamlit dashboard imports from
here and behaves identically.

Units: everything is Rs Crore except the per-share entries listed in
INCOME_STATEMENT_PER_SHARE. XBRL reports in rupees, so it is divided by 1e7;
screener.in already publishes Rs Cr and is passed through untouched.
"""
from __future__ import annotations

import pandas as pd

from connectors.nse_fundamentals import concept_panel

# ---------------------------------------------------------------- concept maps
# Canonical, natural-statement-order line items. Each entry:
# (label, {"xbrl": concept_or_None, "screener": concept_or_None}). A None on
# either side means that source does not publish the line item under any
# concept this repo has actually seen -- the column simply stays unfilled from
# that source rather than being approximated from a different one.

INCOME_STATEMENT_MAP = [
    ("Revenue", {"xbrl": "RevenueFromOperations", "screener": "Sales"}),
    ("Other Income", {"xbrl": "OtherIncome", "screener": "Other Income"}),
    ("Total Expenses", {"xbrl": "Expenses", "screener": "Expenses"}),
    ("Operating Profit", {"xbrl": None, "screener": "Operating Profit"}),
    ("Depreciation", {"xbrl": "DepreciationDepletionAndAmortisationExpense", "screener": "Depreciation"}),
    ("Interest", {"xbrl": "FinanceCosts", "screener": "Interest"}),
    ("Profit Before Tax", {"xbrl": "ProfitBeforeTax", "screener": "Profit before tax"}),
    ("Tax", {"xbrl": "TaxExpense", "screener": "Tax"}),
    ("Net Profit", {"xbrl": "ProfitLossForPeriod", "screener": "Net profit"}),
    ("EPS (Basic)", {"xbrl": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "screener": None}),
]
INCOME_STATEMENT_PER_SHARE = frozenset({"EPS (Basic)"})

BALANCE_SHEET_MAP = [
    ("Equity Share Capital", {"xbrl": "EquityShareCapital", "screener": "Equity Share Capital"}),
    ("Reserves", {"xbrl": "OtherEquity", "screener": "Reserves"}),
    ("Borrowings", {"xbrl": None, "screener": "Borrowings"}),
    ("Other Liabilities", {"xbrl": None, "screener": "Other Liabilities"}),
    ("Total Equity and Liabilities", {"xbrl": "EquityAndLiabilities", "screener": "Total Equity and Liabilities"}),
    ("Net Block", {"xbrl": "PropertyPlantAndEquipment", "screener": "Net Block"}),
    ("Capital Work in Progress", {"xbrl": "CapitalWorkInProgress", "screener": "Capital Work in Progress"}),
    ("Investments", {"xbrl": None, "screener": "Investments"}),
    ("Other Assets", {"xbrl": None, "screener": "Other Assets"}),
    ("Total Assets", {"xbrl": "Assets", "screener": "Total Assets"}),
    ("Receivables", {"xbrl": "TradeReceivablesCurrent", "screener": "Receivables"}),
    ("Inventory", {"xbrl": "Inventories", "screener": "Inventory"}),
    ("Cash & Bank", {"xbrl": "CashAndCashEquivalents", "screener": "Cash & Bank"}),
]

CASH_FLOW_MAP = [
    ("Cash from Operating Activity",
     {"xbrl": "CashFlowsFromUsedInOperatingActivities", "screener": "Cash from Operating Activity"}),
    ("Cash from Investing Activity",
     {"xbrl": "CashFlowsFromUsedInInvestingActivities", "screener": "Cash from Investing Activity"}),
    ("Cash from Financing Activity",
     {"xbrl": "CashFlowsFromUsedInFinancingActivities", "screener": "Cash from Financing Activity"}),
    ("Net Cash Flow", {"xbrl": "IncreaseDecreaseInCashAndCashEquivalents", "screener": "Net Cash Flow"}),
]

# The balance sheet's own arithmetic identity, surfaced as a row rather than
# asserted: Assets and Equity+Liabilities are two views of the same total, so
# the difference should read ~0. A non-zero value means the merge picked up
# two different period definitions under one label, which is exactly the class
# of bug full_year_only= below exists to prevent.
BALANCE_CHECK_LABEL = "Balance check (Assets - Equity&Liab)"


# ----------------------------------------------------------- period labelling
# India's fiscal year runs April-March, so calendar-year labelling would put
# Q4 of FY24-25 (Jan-Mar 2025) in the wrong year on every chart axis.

def fy_quarter_label(period_end) -> str:
    ts = pd.Timestamp(period_end)
    fy_start_year = ts.year if ts.month >= 4 else ts.year - 1
    fy = f"FY{str(fy_start_year)[2:]}-{str(fy_start_year + 1)[2:]}"
    q = {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[ts.month]
    return f"Q{q} {fy}"


def fy_label(period_end) -> str:
    ts = pd.Timestamp(period_end)
    fy_start_year = ts.year if ts.month >= 4 else ts.year - 1
    return f"FY{str(fy_start_year)[2:]}-{str(fy_start_year + 1)[2:]}"


# ------------------------------------------------------------ building blocks

def xbrl_canonical_wide(shown: pd.DataFrame, concept_map, per_share: frozenset = frozenset(),
                        quarters_only: bool = False, full_year_only: bool = False) -> dict:
    """period_end (Timestamp) -> {canonical_label: value}, Rs-Cr-scaled (except per_share)."""
    xbrl_concepts = tuple(v["xbrl"] for _, v in concept_map if v.get("xbrl"))
    panel = concept_panel(shown, xbrl_concepts) if xbrl_concepts else pd.DataFrame()
    if panel.empty:
        return {}
    if quarters_only:
        span = (pd.to_datetime(panel["period_end"]) - pd.to_datetime(panel["period_start"], errors="coerce"))
        panel = panel[span.dt.days <= 100]
    if full_year_only:
        # REGRESSION GUARD: the Balance Sheet's half-year (Q2, ~Sep) snapshot
        # and screener's fiscal-year-end (~Mar) snapshot both land in the
        # same "FY24-25" label once fy_label() only looks at which fiscal
        # year a date falls in -- caught by seeing two differently-valued
        # "FY24-25" columns in a live merge test, not by inspection. Balance
        # sheet facts are instant (period_start is "" from concept_panel's
        # NaN-fill), so duration can't distinguish them; month can (Q4 always
        # falls in March for the Apr-Mar filers this warehouse currently
        # covers). Cash flow facts DO have a duration -- H1 is ~183 days,
        # full year ~365 -- so span works there instead.
        has_start = panel["period_start"].astype(str).str.len() > 0
        span_days = (pd.to_datetime(panel["period_end"])
                      - pd.to_datetime(panel["period_start"], errors="coerce")).dt.days
        keep_duration = has_start & (span_days > 200)
        keep_instant = (~has_start) & (pd.to_datetime(panel["period_end"]).dt.month == 3)
        panel = panel[keep_duration | keep_instant]
    canon_by_xbrl = {v["xbrl"]: k for k, v in concept_map if v.get("xbrl")}
    out = {}
    for _, row in panel.iterrows():
        pe = pd.Timestamp(row["period_end"]).normalize()
        d = out.setdefault(pe, {})
        for xcol, canon in canon_by_xbrl.items():
            if xcol in panel.columns and pd.notna(row.get(xcol)):
                v = row[xcol]
                d[canon] = v if canon in per_share else v / 1e7
    return out


def screener_canonical_wide(screener_long, concept_map, statement: str, period_type: str) -> dict:
    """period_end (Timestamp) -> {canonical_label: value} from a screener.in fetch -- already Rs Cr."""
    if screener_long is None or screener_long.empty:
        return {}
    sub = screener_long[(screener_long["statement"] == statement) & (screener_long["period_type"] == period_type)]
    canon_by_screener = {v["screener"]: k for k, v in concept_map if v.get("screener")}
    out = {}
    for _, row in sub.iterrows():
        canon = canon_by_screener.get(row["concept"])
        if not canon or pd.isna(row["value"]):
            continue
        pe = pd.Timestamp(row["period_end"]).normalize()
        out.setdefault(pe, {})[canon] = row["value"]
    return out


def merge_canonical(xbrl_wide: dict, screener_wide: dict, concept_map, label_fn) -> pd.DataFrame:
    """
    XBRL wins on any period both sides cover; screener only ever ADDS periods
    XBRL doesn't have. Rows in canonical (natural statement) order, columns
    most-recent-first.
    """
    order = [c for c, _ in concept_map]
    all_periods = set(xbrl_wide) | set(screener_wide)
    if not all_periods:
        return pd.DataFrame()
    merged = {pe: {**screener_wide.get(pe, {}), **xbrl_wide.get(pe, {})} for pe in all_periods}
    df = pd.DataFrame(merged).reindex(order)
    df = df[sorted(df.columns, reverse=True)]
    df.columns = [label_fn(c) for c in df.columns]
    return df.round(2)


# -------------------------------------------------------- composed statements
# The three functions a caller actually wants. Each takes the same two inputs
# -- `shown` is the XBRL fact table already filtered to one symbol and one
# consolidation basis; `screener_long` is an optional screener.in fetch in its
# native long shape (None when nobody has fetched one) -- and returns a table
# whose rows are canonical line items and whose columns are periods,
# most-recent-first.

def income_statement(shown: pd.DataFrame, screener_long=None) -> pd.DataFrame:
    """Quarterly P&L. Quarters only: an annual XBRL fact filed alongside the
    Q4 exhibit would otherwise appear as a fourth-quarter column worth four
    quarters of revenue."""
    return merge_canonical(
        xbrl_canonical_wide(shown, INCOME_STATEMENT_MAP, per_share=INCOME_STATEMENT_PER_SHARE,
                            quarters_only=True),
        screener_canonical_wide(screener_long, INCOME_STATEMENT_MAP, "income_statement", "quarterly"),
        INCOME_STATEMENT_MAP, fy_quarter_label,
    )


def balance_sheet(shown: pd.DataFrame, screener_long=None) -> pd.DataFrame:
    """Annual snapshots, with the Assets vs Equity+Liabilities identity
    appended as its own row. The check row is part of the statement, not a
    display flourish -- a surface that renders this table without it loses the
    only built-in signal that the merge went wrong."""
    df = merge_canonical(
        xbrl_canonical_wide(shown, BALANCE_SHEET_MAP, full_year_only=True),
        screener_canonical_wide(screener_long, BALANCE_SHEET_MAP, "balance_sheet", "annual"),
        BALANCE_SHEET_MAP, fy_label,
    )
    if {"Total Assets", "Total Equity and Liabilities"} <= set(df.index):
        df.loc[BALANCE_CHECK_LABEL] = df.loc["Total Assets"] - df.loc["Total Equity and Liabilities"]
    return df


def cash_flow(shown: pd.DataFrame, screener_long=None) -> pd.DataFrame:
    """Annual cash flow. Unlike the balance sheet these facts carry a real
    duration, so full_year_only= separates the full year from H1 by span
    rather than by month."""
    return merge_canonical(
        xbrl_canonical_wide(shown, CASH_FLOW_MAP, full_year_only=True),
        screener_canonical_wide(screener_long, CASH_FLOW_MAP, "cash_flow", "annual"),
        CASH_FLOW_MAP, fy_label,
    )


# -------------------------------------------------------------------- display
# Kept separate from the composed statements above on purpose: the units belong
# in the row LABEL for a rendered table, but an API response is better served
# by numbers plus a units field. A caller opts in.

def label_with_units(df: pd.DataFrame, per_share: frozenset = frozenset()) -> pd.DataFrame:
    df = df.copy()
    df.index = [f"{i} (Rs/share)" if i in per_share else f"{i} (Rs Cr)" for i in df.index]
    return df
