"""
screener.in -- income statement / balance sheet / cash flow / quarterly
results, current and consolidated, for one company at a time.

DELIBERATE, SCOPED EXCEPTION to CLAUDE.md's "no third-party data vendors"
rule. Screener.in aggregates and computes these numbers from company
filings; it is not the exchange, the regulator, or a broker. Built anyway
on the user's explicit instruction 2026-08-30, after being shown the
conflict directly: NSE's own quarterly-results feed
(connectors/nse_fundamentals.py) lags ~18 months for large caps, and BSE's
per-company summary (connectors/nse_bse.py's bse_quarterly_results_summary)
turned out to return STANDALONE figures, not consolidated -- no
primary-source path to current CONSOLIDATED detail was found despite real
effort (see ROADMAP.md's Phase 4b entries). This connector is the fallback
for current consolidated data, not the default -- prefer the XBRL/BSE
paths for anything they actually cover, and don't reach for this pattern
elsewhere without the same explicit conversation.

MECHANISM, verified live 2026-08-30 against RELIANCE/TCS/INFY:
  1. GET https://www.screener.in/company/<SYMBOL>/consolidated/ -- the page
     HTML embeds both a numeric company id (in the export form's
     action="/user/company/export/<id>/") and a fresh Django CSRF token
     (a csrfmiddlewaretoken hidden input). Both must be scraped fresh each
     call: the id is stable, but the CSRF token is masked per response and
     screener may reject a stale one.
  2. POST https://www.screener.in/user/company/export/<id>/ with
     {csrfmiddlewaretoken, next: "/company/<SYMBOL>/consolidated/"}, same
     session (cookies) -- returns the raw .xlsx bytes directly
     (Content-Disposition: attachment), not a redirect to one.

WHAT'S IN THE FILE: a "Data Sheet" tab is screener's real, native export --
the other tabs (Profit & Loss / Quarters / Balance Sheet / Cash Flow /
Customization) are pre-formatted views of the same numbers, not additional
data. Data Sheet has four financial-statement blocks this module parses
(PROFIT & LOSS annual, Quarters, BALANCE SHEET annual, CASH FLOW annual),
plus a META block (price/shares/market cap) and PRICE/DERIVED blocks this
module does not parse. Verified genuinely current: RELIANCE's Quarters
block includes Jun-2026 with Sales Rs 3,09,468 Cr, matching screener's own
page display and NOT matching either NSE's XBRL feed (stuck at Dec-2024)
or BSE's summary endpoint (standalone, Rs 1,66,013 Cr for the same period).
Consolidated vs standalone is a URL choice (/consolidated/ vs bare
/company/<SYMBOL>/), not a request parameter.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import openpyxl
import pandas as pd

from core.http import screener_session
from core.storage import find_raw, save_raw, write_table

EXPORT_ID_RE = re.compile(r'/user/company/export/(\d+)/')
CSRF_RE = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"')

# Data Sheet section header (column A label) -> (statement, period_type).
# Only the four financial-statement blocks; META/PRICE/DERIVED are skipped.
SECTIONS = {
    "PROFIT & LOSS": ("income_statement", "annual"),
    "Quarters": ("income_statement", "quarterly"),
    "BALANCE SHEET": ("balance_sheet", "annual"),
    "CASH FLOW:": ("cash_flow", "annual"),
}


def _company_path(symbol: str, consolidated: bool) -> str:
    return f"/company/{symbol}/consolidated/" if consolidated else f"/company/{symbol}/"


def _export_id_and_token(f, symbol: str, consolidated: bool) -> tuple[str, str]:
    r = f.get(f"https://www.screener.in{_company_path(symbol, consolidated)}")
    id_m = EXPORT_ID_RE.search(r.text)
    token_m = CSRF_RE.search(r.text)
    if not id_m or not token_m:
        raise RuntimeError(
            f"screener.export_company({symbol!r}): couldn't find the export form "
            f"on the company page -- SCREENER_CSRFTOKEN/SCREENER_SESSIONID have "
            f"likely expired (re-capture per .env.example), or screener changed "
            f"its page layout."
        )
    return id_m.group(1), token_m.group(1)


def export_company(symbol: str, consolidated: bool = True, refetch: bool = False) -> Path:
    """
    Download one company's screener.in export and archive the raw bytes
    (rule 1: archive before parsing). Returns the archived path.

    Reuses today's archived copy unless refetch=True -- this is a quarterly
    per-company pull, not a bulk sweep, so there is no reason to re-spend a
    login-gated request against the same day's data twice.
    """
    cached = None if refetch else find_raw("screener", "export", symbol=symbol, consolidated=consolidated)
    if cached is not None:
        return cached

    f = screener_session()
    export_id, token = _export_id_and_token(f, symbol, consolidated)
    r = f.post(
        f"https://www.screener.in/user/company/export/{export_id}/",
        data={"csrfmiddlewaretoken": token, "next": _company_path(symbol, consolidated)},
    )
    if "spreadsheet" not in r.headers.get("content-type", ""):
        raise RuntimeError(
            f"screener.export_company({symbol!r}): expected an .xlsx response, got "
            f"content-type={r.headers.get('content-type')!r} -- almost always means "
            f"the session cookie expired and screener served a login page instead."
        )
    return save_raw("screener", "export", r.content, "xlsx",
                     {"symbol": symbol, "consolidated": consolidated})


def _parse_data_sheet(content: bytes) -> pd.DataFrame:
    """
    Data Sheet tab -> tidy long facts. Pure and offline-testable: no network.

    Each of the four SECTIONS starts with a header row (column A matches a
    SECTIONS key), followed immediately by a "Report Date" row (period-end
    dates across the columns), followed by concept rows until a blank row
    ends the section. A concept label can repeat within one section -- the
    Balance Sheet's "Total" appears exactly once for the liabilities+equity
    side (right after "Other Liabilities") and once for the assets side
    (right after "Other Assets"), a fixed, verified position in screener's
    own layout, so those two are renamed outright rather than left as
    "Total"/"Total_2" for the dashboard to guess at. Any OTHER unexpected
    duplicate elsewhere still gets the generic numeric-suffix fallback.
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb["Data Sheet"]
    rows = list(ws.iter_rows(values_only=True))

    BALANCE_SHEET_TOTAL_LABELS = ["Total Equity and Liabilities", "Total Assets"]

    facts = []
    i = 0
    while i < len(rows):
        label = rows[i][0] if rows[i] else None
        if label in SECTIONS:
            statement, period_type = SECTIONS[label]
            header = rows[i + 1]
            periods = header[1:]
            j = i + 2
            seen = {}
            while j < len(rows) and rows[j] and rows[j][0] not in (None, ""):
                concept = rows[j][0]
                seen[concept] = seen.get(concept, 0) + 1
                if statement == "balance_sheet" and concept == "Total":
                    concept = BALANCE_SHEET_TOTAL_LABELS[seen[concept] - 1]
                elif seen[concept] > 1:
                    concept = f"{concept}_{seen[concept]}"
                for period_end, value in zip(periods, rows[j][1:]):
                    if period_end is None or not isinstance(value, (int, float)):
                        continue
                    facts.append({
                        "statement": statement, "period_type": period_type,
                        "concept": concept, "period_end": period_end, "value": value,
                    })
                j += 1
            i = j
        else:
            i += 1

    df = pd.DataFrame(facts)
    if df.empty:
        raise RuntimeError("screener._parse_data_sheet: found none of the expected "
                            "section headers -- Data Sheet layout may have changed")
    return df


def financial_facts(symbol: str, consolidated: bool = True, persist: bool = True,
                     refetch: bool = False) -> pd.DataFrame:
    """One company's screener.in export, fetched (or reused) and flattened to tidy facts."""
    path = export_company(symbol, consolidated=consolidated, refetch=refetch)
    df = _parse_data_sheet(path.read_bytes())
    df["symbol"] = symbol
    df["consolidated"] = consolidated
    if persist:
        write_table(df, "screener", "financial_facts")
    return df
