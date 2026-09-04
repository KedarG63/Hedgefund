"""
Offline tests for the NSE XBRL fundamentals connector.

No network: these pin the parsing logic and, most importantly, the
context-period landmine documented in connectors/nse_fundamentals.py's module
docstring -- a real filing (VST Tillers Q3 FY25) tags a context's declared
xbrli:period with the WRONG dates, and only the self-reported
DateOfStartOfReportingPeriod/DateOfEndOfReportingPeriod facts reveal the true
period. SAMPLE_XBRL below reproduces that exact shape (trimmed to what the
tests need) so a future refactor can't silently regress it.

    python -m pytest tests/test_nse_fundamentals.py -v
"""
import pandas as pd
import pytest

from connectors.nse_fundamentals import (
    FILING_META_COLS,
    _parse_xbrl,
    _resolve_reporting_period,
    concept_panel,
    financial_facts,
    income_statement,
    new_filings,
)

# Trimmed but structurally real: two duration contexts sharing one declared
# period (OneD, FourD -- the landmine), one instant context (OneI, balance
# sheet), one dimensional context (OneOperatingExpenses01D, an OtherExpenses
# breakdown that must NOT collide with the headline OtherExpenses under
# OneD), and one non-numeric fact (NatureOfReportStandaloneConsolidated).
SAMPLE_XBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin"
            xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
            xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<link:schemaRef xmlns:link="http://www.xbrl.org/2003/linkbase"
                 xmlns:xlink="http://www.w3.org/1999/xlink"
                 xlink:type="simple" xlink:href="Ind-AS_entry_point_2020-03-31.xsd"/>
<xbrli:context id="OneD">
  <xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com/NSESymbol">VSTTILLERS</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>
</xbrli:context>
<xbrli:context id="FourD">
  <xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com/NSESymbol">VSTTILLERS</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>
</xbrli:context>
<xbrli:context id="OneI">
  <xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com/NSESymbol">VSTTILLERS</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period>
</xbrli:context>
<xbrli:context id="OneOperatingExpenses01D">
  <xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com/NSESymbol">VSTTILLERS</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>
  <xbrli:scenario><xbrldi:explicitMember dimension="in-bse-fin:DetailsOfOtherExpensesAxis">in-bse-fin:OneOperatingExpenses01Member</xbrldi:explicitMember></xbrli:scenario>
</xbrli:context>
<xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
<in-bse-fin:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</in-bse-fin:NatureOfReportStandaloneConsolidated>
<in-bse-fin:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-5">2191000000.00</in-bse-fin:RevenueFromOperations>
<in-bse-fin:RevenueFromOperations contextRef="FourD" unitRef="INR" decimals="-5">6931200000.00</in-bse-fin:RevenueFromOperations>
<in-bse-fin:DateOfStartOfReportingPeriod contextRef="FourD">2024-04-01</in-bse-fin:DateOfStartOfReportingPeriod>
<in-bse-fin:DateOfEndOfReportingPeriod contextRef="FourD">2024-12-31</in-bse-fin:DateOfEndOfReportingPeriod>
<in-bse-fin:Assets contextRef="OneI" unitRef="INR" decimals="-5">5000000000.00</in-bse-fin:Assets>
<in-bse-fin:OtherExpenses contextRef="OneD" unitRef="INR" decimals="-5">223000000.00</in-bse-fin:OtherExpenses>
<in-bse-fin:OtherExpenses contextRef="OneOperatingExpenses01D" unitRef="INR" decimals="-5">223000000.00</in-bse-fin:OtherExpenses>
</xbrli:xbrl>
"""


def _parsed():
    return _resolve_reporting_period(_parse_xbrl(SAMPLE_XBRL))


def test_parse_xbrl_extracts_numeric_and_textual_facts():
    df = _parse_xbrl(SAMPLE_XBRL)
    revenue_oned = df[(df["concept"] == "RevenueFromOperations") & (df["context_id"] == "OneD")]
    assert revenue_oned["value"].iloc[0] == 2191000000.00
    assert revenue_oned["unit"].iloc[0] == "INR"

    # non-numeric fact: no unitRef, so value is NaN but value_text is preserved
    nature = df[df["concept"] == "NatureOfReportStandaloneConsolidated"]
    assert nature["value_text"].iloc[0] == "Consolidated"
    assert pd.isna(nature["value"].iloc[0])


def test_parse_xbrl_flags_dimensional_contexts():
    df = _parse_xbrl(SAMPLE_XBRL)
    oned = df[(df["concept"] == "OtherExpenses") & (df["context_id"] == "OneD")]
    dimensional = df[(df["concept"] == "OtherExpenses") & (df["context_id"] == "OneOperatingExpenses01D")]
    assert not oned["has_dimensions"].iloc[0]
    assert dimensional["has_dimensions"].iloc[0]
    assert "DetailsOfOtherExpensesAxis" in dimensional["dimensions"].iloc[0]


def test_parse_xbrl_instant_context_has_no_start_date():
    df = _parse_xbrl(SAMPLE_XBRL)
    assets = df[df["concept"] == "Assets"]
    assert assets["instant"].iloc[0]
    assert assets["period_end"].iloc[0] == "2024-12-31"
    assert pd.isna(assets["period_start"].iloc[0]) or assets["period_start"].iloc[0] is None


def test_parse_xbrl_raises_on_zero_facts():
    empty = b'<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"/>'
    with pytest.raises(RuntimeError, match="zero facts"):
        _parse_xbrl(empty)


def test_resolve_reporting_period_fixes_the_context_landmine():
    """
    REGRESSION: FourD's declared xbrli:period (2024-10-01/2024-12-31) is
    identical to OneD's, but its RevenueFromOperations (693cr) is a YTD
    figure, not a quarterly one -- confirmed by FourD's own
    DateOfStartOfReportingPeriod fact (2024-04-01). Trusting the raw context
    dates would make a 9-month cumulative number look like a second quarterly
    column. After resolution, FourD's period_start must reflect the
    self-reported 2024-04-01, not the context's own 2024-10-01.
    """
    df = _parsed()
    four_d = df[(df["concept"] == "RevenueFromOperations") & (df["context_id"] == "FourD")]
    one_d = df[(df["concept"] == "RevenueFromOperations") & (df["context_id"] == "OneD")]

    assert four_d["period_start"].iloc[0] == "2024-04-01"
    assert four_d["period_end"].iloc[0] == "2024-12-31"
    # OneD carries no self-reported override facts, so its raw context dates stand.
    assert one_d["period_start"].iloc[0] == "2024-10-01"


def test_resolve_reporting_period_does_not_touch_instant_contexts():
    df = _parsed()
    assets = df[df["concept"] == "Assets"]
    assert assets["period_end"].iloc[0] == "2024-12-31"
    assert assets["instant"].iloc[0]


def test_concept_panel_excludes_dimensional_breakdown():
    """
    Both OneD's headline OtherExpenses and OneOperatingExpenses01D's
    breakdown OtherExpenses carry the same value here (223000000) by
    construction -- concept_panel must keep exactly the headline one, not
    both, or a naive pivot would raise on a duplicate index/column pair.
    """
    df = _parsed()
    df["symbol"] = "VSTTILLERS"
    df["consolidated"] = "Consolidated"
    df["filingDate"] = "30-Jul-2026"
    panel = concept_panel(df, ("OtherExpenses",))
    assert len(panel) == 1
    assert panel["OtherExpenses"].iloc[0] == 223000000.00


def test_concept_panel_keeps_instant_facts_with_no_period_start():
    """
    REGRESSION: balance-sheet facts are instant contexts, so period_start is
    NaN. pivot_table's underlying groupby drops any row whose grouping key
    is NaN by default -- first version of concept_panel() silently returned
    an EMPTY DataFrame for every balance-sheet concept, discovered only by
    running a real annual filing (Siemens FY24) end to end, not by this
    synthetic fixture. Locking it in here now that it's fixed.
    """
    df = _parsed()
    df["symbol"] = "VSTTILLERS"
    df["consolidated"] = "Consolidated"
    df["filingDate"] = "30-Jul-2026"
    panel = concept_panel(df, ("Assets",))
    assert len(panel) == 1
    assert panel["Assets"].iloc[0] == 5000000000.00


def test_income_statement_uses_resolved_periods():
    df = _parsed()
    df["symbol"] = "VSTTILLERS"
    df["consolidated"] = "Consolidated"
    df["filingDate"] = "30-Jul-2026"
    stmt = income_statement(df)
    ytd_row = stmt[stmt["period_start"] == "2024-04-01"]
    assert ytd_row["RevenueFromOperations"].iloc[0] == 6931200000.00


def test_new_filings_drops_already_archived_urls(monkeypatch):
    """
    REGRESSION: nse_financial_results()'s from_date/to_date filter collapses
    to near-zero rows for any range reaching into the current year (verified
    live 2026-08-30 -- a 2-day trailing window returned 0), so the daily job
    re-scans the unfiltered index instead and relies on new_filings() to
    skip what it already has. Confirming the skip logic itself here since
    the live 404-vs-0-rows landmine can't be exercised offline.
    """
    filings = pd.DataFrame([
        {"xbrl": "https://seen", "symbol": "OLD"},
        {"xbrl": "https://unseen", "symbol": "NEW"},
        {"xbrl": None, "symbol": "NOXBRL"},
    ])
    monkeypatch.setattr(
        "connectors.nse_fundamentals.find_raw",
        lambda source, dataset, url: object() if url == "https://seen" else None,
    )
    result = new_filings(filings)
    assert result["symbol"].tolist() == ["NEW"]


def test_financial_facts_raises_on_empty_filings():
    with pytest.raises(RuntimeError, match="no filings given"):
        financial_facts(pd.DataFrame())


def test_financial_facts_tolerates_partial_failure(monkeypatch):
    filings = pd.DataFrame([
        {"xbrl": "https://good", "symbol": "GOOD", "companyName": "Good Ltd", "isin": "X1",
         "consolidated": "Consolidated", "period": "Quarterly", "cumulative": "Non-cumulative",
         "financialYear": "FY25", "fromDate": "01-Oct-2024", "toDate": "31-Dec-2024",
         "filingDate": "30-Jul-2026", "audited": "Un-Audited", "relatingTo": "Q3", "seqNumber": "1"},
        {"xbrl": "https://bad", "symbol": "BAD", "companyName": "Bad Ltd", "isin": "X2",
         "consolidated": "Consolidated", "period": "Quarterly", "cumulative": "Non-cumulative",
         "financialYear": "FY25", "fromDate": "01-Oct-2024", "toDate": "31-Dec-2024",
         "filingDate": "30-Jul-2026", "audited": "Un-Audited", "relatingTo": "Q3", "seqNumber": "2"},
    ])

    def fake_xbrl_facts(url, refetch=False):
        if url == "https://bad":
            raise RuntimeError("404 for https://bad")
        return _parsed()

    monkeypatch.setattr("connectors.nse_fundamentals.xbrl_facts", fake_xbrl_facts)
    monkeypatch.setattr("connectors.nse_fundamentals.write_table", lambda *a, **k: None)

    result = financial_facts(filings, persist=True)

    assert (result["symbol"] == "GOOD").all()
    assert len(result.attrs["failed"]) == 1
    assert result.attrs["failed"][0]["symbol"] == "BAD"
    for col in FILING_META_COLS:
        assert col in result.columns


def test_financial_facts_raises_when_every_filing_fails(monkeypatch):
    filings = pd.DataFrame([{"xbrl": "https://bad", "symbol": "BAD"}])
    monkeypatch.setattr(
        "connectors.nse_fundamentals.xbrl_facts",
        lambda url, refetch=False: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="all 1 filing"):
        financial_facts(filings)
