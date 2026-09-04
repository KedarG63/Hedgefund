"""
NSE quarterly/annual results, XBRL instance -> tidy point-in-time facts.

nse_bse.nse_financial_results() indexes every filed result and hands back a
link per row (the `xbrl` column) to the raw instance document. This module
downloads that document and flattens it -- this is the actual income
statement / balance sheet / cash flow data, the thing screener.in shows on a
company page, built from the same primary filing screener itself parses.

VERIFIED LIVE 2026-08-30 against two real filings:
  - VSTTILLERS, Q3 FY25 quarterly (consolidated) -- P&L + EPS only
  - SIEMENS, FY24 annual (consolidated) -- full P&L + balance sheet + cash flow

Both use namespace `in-bse-fin` (schemaRef Ind-AS_entry_point_2020-03-31.xsd)
-- SEBI mandates one common Ind-AS "fin" taxonomy, authored by BSE, and NSE
serves instances tagged against it too. One concept list covers filings from
either exchange's own XBRL, once BSE's results-XBRL endpoint is verified and
wired up (BSE's `AnnSubCategoryGetData` "Result" category, checked live,
carries only a PDF attachment -- its XBRL link lives behind a different,
not-yet-verified endpoint; NSE-only for now, per CLAUDE.md's "do not invent
endpoints").

QUARTERLY VS ANNUAL COVERAGE -- CORRECTED TWICE, first after the Nifty 50
backfill (the VST Tillers Q3 sample above got it wrong by generalizing from
one quarter that turned out to be the ONE combination -- Q1/Q3 -- with
neither), then again after actually rendering balance_sheet() in the
dashboard: SEBI LODR Reg. 33 requires a full balance sheet AND cash flow
statement at HALF-YEAR (Q2) as well as YEAR-END (Q4), not year-end alone.
NSE files both under `period="Quarterly"` (`relatingTo="Second Quarter"` /
`"Fourth Quarter"`) -- NOT under a separately labeled "Annual" period, which
instead appears specific to non-April-March fiscal-year filers (SIEMENS,
calendar-year FY, files a standalone "Annual" entry) and does not reliably
cover April-March filers. Confirmed live against RELIANCE: balance_sheet()
returns two real columns, "As of Sep 2024" (Q2) and "As of Mar 2024" (Q4);
cash_flow() returns "H1 FY24-25" and "FY23-24 (Full Year)". Q1/Q3 carry
neither. Reconcile "annual" against `relatingTo`, not `period`.
Reconcile a caller's "latest annual" against `relatingTo`, not `period`.

DATA-CURRENCY CEILING -- RESOLVED, not a bug: no company anywhere in the
ENTIRE market-wide unfiltered index reports a quarter with toDate after
2024-12-31 (verified across all 3,816 rows), even though broadCastDate
(when the filing was submitted) runs through 2026-07-30. The two are not
the same thing -- a late or revised filing can broadcast years after the
period it covers. VST Tillers' own most-recent-by-broadcast-date filing
(2026-07-30) is a REFILE of its Q3 FY24-25 result (toDate 2024-12-31,
period-end unchanged from an on-time filing broadcast 2025-02-11) -- not a
new quarter. Filter/sort by toDate for "how current," never by
broadCastDate. Net: the Nifty 50 backfill's Q3 FY24-25 ceiling IS the
current state of the market in this environment, not a gap in how it was
fetched -- there is nothing more recent to find via this endpoint right
now. Two smaller-cap names (HDFCLIFE, SBILIFE) returned nothing in any
window; unexplained, and separate from the currency question.

THE CONTEXT-PERIOD LANDMINE (why _resolve_reporting_period exists):
VST Tillers' Q3 filing declares context "FourD" with xbrli:period
2024-10-01/2024-12-31 -- identical to context "OneD" -- yet OneD's
RevenueFromOperations is Rs.219cr and FourD's is Rs.693cr for the "same"
quarter. The instance also tags, AS DATA under each context,
DateOfStartOfReportingPeriod/DateOfEndOfReportingPeriod facts; FourD's own
say 2024-04-01/2024-12-31 -- the true 9-month YTD period, contradicting the
context's own declared dates. Filers mislabel context periods; the
self-reported period facts are the reliable source. Trusting the raw
xbrli:context dates alone would silently mix a quarter figure and a YTD
figure into what looks like two comparable quarterly columns.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from lxml import etree

from core.http import nse_session
from core.storage import find_raw, save_raw, write_table


def _qname(el) -> tuple[str, str | None]:
    q = etree.QName(el)
    return q.localname, el.prefix


def _parse_contexts(root) -> dict:
    contexts = {}
    for el in root:
        local, _ = _qname(el)
        if local != "context":
            continue
        entity = start = end = instant = None
        dims = []
        for child in el.iter():
            cl, _ = _qname(child)
            if cl == "identifier":
                entity = (child.text or "").strip()
            elif cl == "startDate":
                start = (child.text or "").strip()
            elif cl == "endDate":
                end = (child.text or "").strip()
            elif cl == "instant":
                instant = (child.text or "").strip()
            elif cl == "explicitMember":
                axis = child.get("dimension")
                if axis:
                    dims.append(f"{axis}={(child.text or '').strip()}")
        contexts[el.get("id")] = {
            "entity": entity, "start": start, "end": end, "instant": instant,
            "dimensions": ";".join(dims),
        }
    return contexts


def _parse_units(root) -> dict:
    units = {}
    for el in root:
        local, _ = _qname(el)
        if local != "unit":
            continue
        measures = []
        for child in el.iter():
            cl, _ = _qname(child)
            if cl == "measure":
                measures.append((child.text or "").strip().split(":")[-1])
        units[el.get("id")] = "/".join(measures) if measures else el.get("id")
    return units


def _parse_xbrl(content: bytes) -> pd.DataFrame:
    """
    One instance document -> tidy long-format facts. Pure and offline: no
    network, so this is unit-testable directly against a saved sample.

    Every element that is not schemaRef/context/unit and carries a
    contextRef is a fact -- true for both numeric (unitRef present) and
    textual/date/boolean facts (unitRef absent), which this taxonomy mixes
    in the same flat instance. `value` is float-or-NaN (numeric facts only);
    `value_text` is the raw string always, so a textual fact like
    NatureOfReportStandaloneConsolidated="Consolidated" is never silently
    dropped just because it has no unit.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(content, parser=parser)
    contexts = _parse_contexts(root)
    units = _parse_units(root)

    rows = []
    for el in root:
        local, prefix = _qname(el)
        if local in ("schemaRef", "context", "unit"):
            continue
        context_ref = el.get("contextRef")
        if context_ref is None:
            continue
        ctx = contexts.get(context_ref, {})
        unit_ref = el.get("unitRef")
        text = (el.text or "").strip()
        value = None
        if unit_ref is not None and text:
            try:
                value = float(text)
            except ValueError:
                value = None
        rows.append({
            "concept": local,
            "taxonomy": prefix,
            "context_id": context_ref,
            "entity": ctx.get("entity"),
            "period_start": ctx.get("start"),
            "period_end": ctx.get("end") or ctx.get("instant"),
            "instant": ctx.get("instant") is not None,
            "has_dimensions": bool(ctx.get("dimensions")),
            "dimensions": ctx.get("dimensions", ""),
            "unit": units.get(unit_ref, unit_ref),
            "decimals": el.get("decimals"),
            "value_text": text,
            "value": value,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("_parse_xbrl found zero facts -- empty or malformed XBRL instance")
    return df


def _resolve_reporting_period(df: pd.DataFrame) -> pd.DataFrame:
    """
    Override each context's period with its own self-reported
    DateOfStartOfReportingPeriod/DateOfEndOfReportingPeriod facts where
    present -- see the module docstring's VST Tillers example. Instant
    (balance-sheet) contexts are left alone: the landmine is specific to
    duration contexts carrying quarter-vs-YTD ambiguity.
    """
    df = df.copy()
    reported_start = (df[df["concept"] == "DateOfStartOfReportingPeriod"]
                       .set_index("context_id")["value_text"])
    reported_end = (df[df["concept"] == "DateOfEndOfReportingPeriod"]
                     .set_index("context_id")["value_text"])
    mapped_start = df["context_id"].map(reported_start)
    mapped_end = df["context_id"].map(reported_end)
    df["period_start"] = mapped_start.where(mapped_start.notna(), df["period_start"])
    df["period_end"] = df["period_end"].where(df["instant"] | mapped_end.isna(), mapped_end)
    return df


def fetch_xbrl(url: str, refetch: bool = False) -> Path:
    """
    Download one filed XBRL instance, or reuse the archived copy.

    A filed result is never edited after broadcast, so caching by url means
    a re-run only pays NSE's rate-limit budget for genuinely new filings --
    the same reasoning as find_raw's use in sec_companyfacts, at the
    single-file scale instead of the bulk-archive scale.
    """
    cached = None if refetch else find_raw("nse", "xbrl_instance", url=url)
    if cached is not None:
        return cached
    f = nse_session()
    r = f.get(url)
    return save_raw("nse", "xbrl_instance", r.content, "xml", {"url": url})


def xbrl_facts(url: str, refetch: bool = False) -> pd.DataFrame:
    """One filing's XBRL, fetched (or reused) and flattened to tidy facts."""
    path = fetch_xbrl(url, refetch=refetch)
    df = _parse_xbrl(path.read_bytes())
    df = _resolve_reporting_period(df)
    df["source_url"] = url
    return df


# Columns carried from the filing index (nse_financial_results() output) onto
# every fact row, so a caller never has to join back to the index to know
# whose number, which period-type, and which filing a fact came from.
FILING_META_COLS = [
    "symbol", "companyName", "isin", "consolidated", "period", "cumulative",
    "financialYear", "fromDate", "toDate", "filingDate", "audited",
    "relatingTo", "seqNumber",
]


def new_filings(filings: pd.DataFrame) -> pd.DataFrame:
    """
    Filings whose XBRL is not already in the raw archive.

    Exists because nse_financial_results()'s date filter is unreliable near
    the current date (see its docstring), which rules out a cheap trailing
    N-day window for daily incremental pulls. This lets a caller instead
    re-scan a wide or unfiltered index every time and pay only for what's
    genuinely new -- fetch_xbrl() already caches by url via find_raw(), this
    just avoids re-parsing and re-persisting the ones it would skip anyway.
    """
    filings = filings.dropna(subset=["xbrl"])
    is_new = ~filings["xbrl"].apply(lambda u: find_raw("nse", "xbrl_instance", url=u) is not None)
    return filings[is_new]


def financial_facts(filings: pd.DataFrame, persist: bool = True, refetch: bool = False) -> pd.DataFrame:
    """
    Turn a filing index into a tidy, point-in-time long-format fact table --
    one row per (filing, concept, context).

    `filings` is whatever the caller already filtered from
    nse_financial_results() (by symbol, date range, consolidated/standalone,
    ...). This function fetches everything it is given and nothing more --
    filtering by universe before spending requests is the caller's job, per
    ROADMAP.md rule 5, not this function's.

    One dead link or malformed instance does not lose every filing that DID
    parse: failures collect into the result's .attrs["failed"] instead of
    aborting the batch, mirroring sec_companyfacts.universe_restatements()'s
    missing_ciks pattern. Only total failure -- zero filings yielded any
    facts -- raises, per the connector contract: never return empty on error
    and call it success.
    """
    if filings.empty:
        raise RuntimeError(
            "financial_facts: no filings given -- caller must pre-filter "
            "nse_financial_results() (by symbol, date range, ...) first")

    filings = filings.dropna(subset=["xbrl"]).drop_duplicates(subset=["xbrl"])
    frames, failed = [], []
    for _, row in filings.iterrows():
        try:
            facts = xbrl_facts(row["xbrl"], refetch=refetch)
        except Exception as e:  # noqa -- one bad filing must not sink the batch
            failed.append({"xbrl": row["xbrl"], "symbol": row.get("symbol"), "error": str(e)})
            continue
        for col in FILING_META_COLS:
            facts[col] = row.get(col)
        frames.append(facts)

    if not frames:
        raise RuntimeError(
            f"financial_facts: all {len(filings)} filing(s) failed to fetch/parse -- "
            f"first error: {failed[0]['error'] if failed else 'unknown'}")

    df = pd.concat(frames, ignore_index=True)
    df.attrs["failed"] = failed
    if persist:
        write_table(df, "nse", "xbrl_facts")
    return df


def concept_panel(facts: pd.DataFrame, concepts: tuple[str, ...]) -> pd.DataFrame:
    """
    Wide, screener-style view: one row per filing period, one column per
    concept. Restricted to headline facts (has_dimensions=False) so a
    segment or other-expense breakdown never collides with the consolidated
    total it is a breakdown of.
    """
    sub = facts[facts["concept"].isin(concepts) & ~facts["has_dimensions"] & facts["value"].notna()]
    if sub.empty:
        return pd.DataFrame()
    idx = [c for c in ("symbol", "consolidated", "period_start", "period_end", "filingDate")
           if c in sub.columns]
    sub = sub.copy()
    # Balance-sheet facts are instant (period_start is NaN); pivot_table's
    # underlying groupby drops any row whose grouping key is NaN, which would
    # silently empty out every balance-sheet concept_panel() call. "" groups
    # correctly and reads as "instant, no start date" -- distinct from an
    # actual missing value, which value.notna() above already filtered out.
    sub["period_start"] = sub["period_start"].fillna("")
    wide = sub.pivot_table(index=idx, columns="concept", values="value", aggfunc="first")
    return wide.reset_index()


# Exactly the tags observed live in the two filings named in the module
# docstring -- nothing guessed. A company that tags a line item under a
# different (also-valid) in-bse-fin concept simply won't populate that
# column; the tidy fact table above is the complete, lossless source, this
# is a curated convenience view of it.
INCOME_STATEMENT_CONCEPTS = (
    "RevenueFromOperations", "OtherIncome", "Income", "CostOfMaterialsConsumed",
    "PurchasesOfStockInTrade", "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade",
    "EmployeeBenefitExpense", "FinanceCosts", "DepreciationDepletionAndAmortisationExpense",
    "OtherExpenses", "Expenses", "ProfitBeforeExceptionalItemsAndTax", "ExceptionalItemsBeforeTax",
    "ProfitBeforeTax", "CurrentTax", "DeferredTax", "TaxExpense",
    "ProfitLossForPeriodFromContinuingOperations", "ProfitLossFromDiscontinuedOperationsBeforeTax",
    "ProfitLossFromDiscontinuedOperationsAfterTax", "ProfitLossForPeriod",
    "OtherComprehensiveIncomeNetOfTaxes", "ComprehensiveIncomeForThePeriod",
    "ProfitOrLossAttributableToOwnersOfParent", "ProfitOrLossAttributableToNonControllingInterests",
    "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
    "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
)

BALANCE_SHEET_CONCEPTS = (
    "Assets", "CurrentAssets", "NoncurrentAssets", "PropertyPlantAndEquipment",
    "CapitalWorkInProgress", "Goodwill", "OtherIntangibleAssets", "InvestmentProperty",
    "NoncurrentInvestments", "CurrentInvestments", "InvestmentsAccountedForUsingEquityMethod",
    "TradeReceivablesCurrent", "TradeReceivablesNoncurrent", "CashAndCashEquivalents",
    "BankBalanceOtherThanCashAndCashEquivalents", "Inventories", "LoansCurrent", "LoansNoncurrent",
    "OtherCurrentAssets", "OtherNoncurrentAssets", "CurrentTaxAssets", "DeferredTaxAssetsNet",
    "Equity", "EquityShareCapital", "OtherEquity", "EquityAttributableToOwnersOfParent",
    "NonControllingInterest", "Liabilities", "CurrentLiabilities", "NoncurrentLiabilities",
    "BorrowingsCurrent", "BorrowingsNoncurrent", "TradePayablesCurrent", "TradePayablesNoncurrent",
    "ProvisionsCurrent", "ProvisionsNoncurrent", "CurrentTaxLiabilities", "DeferredTaxLiabilitiesNet",
    "EquityAndLiabilities",
)

CASH_FLOW_CONCEPTS = (
    "CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInInvestingActivities",
    "CashFlowsFromUsedInFinancingActivities", "IncreaseDecreaseInCashAndCashEquivalents",
    "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges",
    "EffectOfExchangeRateChangesOnCashAndCashEquivalents", "CashAndCashEquivalentsCashFlowStatement",
)


def income_statement(facts: pd.DataFrame) -> pd.DataFrame:
    return concept_panel(facts, INCOME_STATEMENT_CONCEPTS)


def balance_sheet(facts: pd.DataFrame) -> pd.DataFrame:
    return concept_panel(facts, BALANCE_SHEET_CONCEPTS)


def cash_flow(facts: pd.DataFrame) -> pd.DataFrame:
    return concept_panel(facts, CASH_FLOW_CONCEPTS)
