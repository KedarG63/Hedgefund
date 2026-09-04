"""
Offline tests for the Indian credit-rating-agency connectors (CRISIL, ICRA,
CARE). Sample payloads are shaped exactly like each agency's live endpoint
capture (see the endpoint contract docstrings in connectors/credit_ratings.py)
-- same keys/HTML structure, same date formats, same duplicate-key shape a
real multi-page walk can produce at a window boundary.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

import connectors.credit_ratings as cr
from connectors.credit_ratings import (
    CARE_DOC_BASE_URL,
    CRISIL_DOC_BASE_URL,
    CRISIL_PAGE_WINDOW,
    _care_parse_listing,
    _crisil_parse_date,
    _crisil_parse_listing,
    _icra_parse_date,
    _icra_parse_rows,
    classify_action,
)


# ================================================================== shared
@pytest.mark.parametrize("heading,expected", [
    ("Ratings migrated to 'Crisil BB-/Stable Issuer not cooperating'", "issuer_not_cooperating"),
    ("Continues to remain under issuer non-cooperating category", "issuer_not_cooperating"),
    ("Ratings moved to Issuer Non-Cooperating Category", "issuer_not_cooperating"),
    ("Rating downgraded to 'Crisil BBB/Negative'", "downgrade"),
    ("Long-term rating upgraded to 'Crisil BBB-/Stable'", "upgrade"),
    ("Rating placed on Watch with Developing Implications", "watch"),
    ("Ratings continues on 'Watch Developing'", "watch"),
    ("Rating migrated to 'Crisil BB+ / Stable'", "migrated"),
    ("Ratings withdrawn on request of the company", "withdrawn"),
    ("Ratings reaffirmed at 'Crisil AAA/Stable'", "reaffirmed"),
    ("'Crisil AA/Stable' assigned to Non Convertible Debentures", "assigned"),
    ("Some unrelated corporate action headline", "other"),
])
def test_classify_action_matches_real_headline_shapes(heading, expected):
    assert classify_action(heading) == expected


def test_classify_action_issuer_not_cooperating_beats_migration_wording():
    """
    A real headline is "Ratings migrated to '...Issuer not cooperating'" --
    the access/data-quality flag must win over reading this as a plain
    migration, since "not cooperating" is the tradeable signal (the company
    stopped sharing information with the rater).
    """
    assert classify_action(
        "Issuer not cooperating, based on best-available information; "
        "Ratings migrated to 'Crisil BB-/Stable/Crisil A4+ Issuer not cooperating'"
    ) == "issuer_not_cooperating"


def test_classify_action_handles_missing_heading():
    assert classify_action(None) == "other"
    assert classify_action("") == "other"


# ================================================================== CRISIL
def _crisil_doc(company_name="Sample Industries Limited", heading="Ratings reaffirmed at 'Crisil A/Stable'",
                rating_date="Aug 28, 2026", trans_date="Aug 28, 2026", pr_id="1", **extra):
    return {
        "companyCode": "SMPL",
        "industryName": "",
        "ratingDate": rating_date,
        "heading": f"{company_name}:{heading}" if ":" not in heading else heading,
        "companyName": company_name,
        "ratingFileName": f"SampleIndustriesLimited_August 28_ 2026_RR_{pr_id}.html",
        "transDate": trans_date,
        "prId": pr_id,
        "showAbstarct": "0",
        "abstractTemp": "A",
        **extra,
    }


def test_crisil_parse_listing_maps_and_derives_columns():
    df = _crisil_parse_listing([_crisil_doc()])
    row = df.iloc[0]
    assert row["company_name"] == "Sample Industries Limited"
    assert row["rating_date"] == "2026-08-28"
    assert row["action_type"] == "reaffirmed"
    assert row["document_url"] == CRISIL_DOC_BASE_URL + row["rating_file_name"].replace(" ", "%20")


def test_crisil_parse_listing_deduplicates_on_pr_id():
    """Widening the (start, end) window on the next call can re-return a row
    already seen at the previous boundary -- prId is the stable dedup key."""
    docs = [_crisil_doc(pr_id="1"), _crisil_doc(pr_id="1"), _crisil_doc(pr_id="2")]
    assert len(_crisil_parse_listing(docs)) == 2


def test_crisil_parse_listing_empty_input_returns_empty_frame_with_expected_columns():
    df = _crisil_parse_listing([])
    assert df.empty
    assert "action_type" in df.columns


def test_crisil_parse_listing_raises_on_schema_drift():
    broken = [_crisil_doc()]
    del broken[0]["ratingDate"]
    with pytest.raises(RuntimeError, match="schema drift"):
        _crisil_parse_listing(broken)


def test_crisil_parse_listing_sorted_newest_first():
    docs = [_crisil_doc(rating_date="Aug 20, 2026", pr_id="1"), _crisil_doc(rating_date="Aug 28, 2026", pr_id="2")]
    df = _crisil_parse_listing(docs)
    assert list(df["rating_date"]) == ["2026-08-28", "2026-08-20"]


def test_crisil_parse_date_format():
    assert _crisil_parse_date("Aug 28, 2026").isoformat() == "2026-08-28"


def test_crisil_rating_actions_stops_when_oldest_row_crosses_cutoff(monkeypatch):
    """
    Two windows are enough to cross a 3-day cutoff: the first page's oldest
    row is still inside the window, so a second (wider) page must be fetched;
    the second page's oldest row falls outside it, so a third must not be.

    Dates are computed relative to the real date.today() rather than a fixed
    2026 date -- date is a built-in immutable type and cannot be monkeypatched.
    """
    today = date.today()
    d = lambda days_ago: (today - timedelta(days=days_ago)).strftime(cr.CRISIL_DATE_FMT)
    calls = []

    def fake_fetch_page(session, start, end, filters=None):
        calls.append((start, end))
        if start == 0:
            docs = [_crisil_doc(rating_date=d(0), pr_id="1"), _crisil_doc(rating_date=d(1), pr_id="2")]
        else:
            docs = [_crisil_doc(rating_date=d(2), pr_id="3"),
                    _crisil_doc(rating_date=d(20), pr_id="4")]  # outside a 3-day window
        return {"docs": docs, "numFound": 999999}

    monkeypatch.setattr(cr, "_crisil_fetch_page", fake_fetch_page)
    monkeypatch.setattr(cr, "crisil_session", lambda: object())

    df = cr.crisil_rating_actions(days_back=3, persist=False)

    assert calls == [(0, CRISIL_PAGE_WINDOW), (CRISIL_PAGE_WINDOW, CRISIL_PAGE_WINDOW * 2)], \
        "must stop after the page that crosses the cutoff, not keep widening"
    iso = lambda days_ago: (today - timedelta(days=days_ago)).isoformat()
    assert set(df["rating_date"]) == {iso(0), iso(1), iso(2)}


def test_crisil_rating_actions_stops_when_num_found_exhausted(monkeypatch):
    """A small corpus (numFound < CRISIL_PAGE_WINDOW) must not spin past what exists."""
    calls = []

    def fake_fetch_page(session, start, end, filters=None):
        calls.append((start, end))
        return {"docs": [_crisil_doc(rating_date="Aug 28, 2026", pr_id="1")], "numFound": 1}

    monkeypatch.setattr(cr, "_crisil_fetch_page", fake_fetch_page)
    monkeypatch.setattr(cr, "crisil_session", lambda: object())

    cr.crisil_rating_actions(days_back=30, persist=False)
    assert len(calls) == 1


def test_crisil_rating_actions_empty_feed_returns_empty_frame(monkeypatch):
    monkeypatch.setattr(cr, "_crisil_fetch_page", lambda *a, **k: {"docs": [], "numFound": 0})
    monkeypatch.setattr(cr, "crisil_session", lambda: object())

    assert cr.crisil_rating_actions(days_back=7, persist=False).empty


# ================================================================== ICRA
def _icra_row_html(rid="145344", company_id="20790", company_name="Mahindra Accelo Limited",
                   heading="Ratings reaffirmed; rated amount enhanced", rating_date="28 Aug 2026",
                   category="Corporate Debt Rating"):
    """One <tr> shaped like ICRA's real GetAllRatingRational response."""
    return f"""
    <tr>
      <td>{rating_date}</td>
      <td><span class="ratingCategoryName">{category}</span></td>
      <td><a href="/Rationale/ShowRationaleReport?Id={rid}">{company_name}: {heading}</a></td>
      <td><a href="/Rating/BankFacilities?CompanyId={company_id}&CompanyName={company_name}">Lender-wise facilities</a></td>
      <td><a href="/Rating/GetRationalReportFilePdf?Id={rid}">PDF</a></td>
    </tr>
    """


def _icra_table_html(*rows):
    header = """
    <tr><th>Date</th><th>Sector</th><th>Reports</th><th></th><th>Action</th></tr>
    """
    return f"<table>{header}{''.join(rows)}</table>"


def test_icra_parse_rows_extracts_all_fields():
    html = _icra_table_html(_icra_row_html())
    df = _icra_parse_rows(html)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["rationale_id"] == "145344"
    assert row["company_id"] == "20790"
    assert row["company_name"] == "Mahindra Accelo Limited"
    assert row["rating_date"] == "2026-08-28"
    assert row["rating_category"] == "Corporate Debt Rating"
    assert row["action_type"] == "reaffirmed"
    assert "145344" in row["document_url"]
    assert "145344" in row["pdf_url"]


def test_icra_parse_rows_skips_header_row():
    """The header <tr> has <th>, not <td> with a ShowRationaleReport link -- must not appear as a data row."""
    html = _icra_table_html(_icra_row_html())
    df = _icra_parse_rows(html)
    assert len(df) == 1  # not 2 -- the header row must not be counted


def test_icra_parse_rows_handles_row_without_bank_facilities_link():
    """Structured-finance-style rows may not carry a Lender-wise facilities link."""
    row = """
    <tr>
      <td>28 Aug 2026</td>
      <td><span class="ratingCategoryName">Structured Finance</span></td>
      <td><a href="/Rationale/ShowRationaleReport?Id=999">Some Trust: Rating reaffirmed</a></td>
      <td></td>
      <td><a href="/Rating/GetRationalReportFilePdf?Id=999">PDF</a></td>
    </tr>
    """
    df = _icra_parse_rows(_icra_table_html(row))
    assert len(df) == 1
    assert df.iloc[0]["company_id"] is None


def test_icra_parse_rows_empty_html_returns_empty_frame():
    df = _icra_parse_rows("<table></table>")
    assert df.empty
    assert "action_type" in df.columns


def test_icra_parse_date_format():
    assert _icra_parse_date("28 Aug 2026").isoformat() == "2026-08-28"


def test_icra_rating_actions_stops_on_empty_page(monkeypatch):
    calls = []

    def fake_fetch_page(session, token, page, company_name=""):
        calls.append(page)
        if page == 1:
            return _icra_table_html(_icra_row_html(rid="1", rating_date="28 Aug 2026"))
        return _icra_table_html()  # page 2: no more data

    monkeypatch.setattr(cr, "_icra_fetch_page", fake_fetch_page)
    monkeypatch.setattr(cr, "icra_session", lambda: object())
    monkeypatch.setattr(cr, "_icra_token", lambda session: "fake-token")

    df = cr.icra_rating_actions(days_back=30, persist=False)
    assert calls == [1, 2]
    assert len(df) == 1


def test_icra_rating_actions_stops_when_oldest_row_crosses_cutoff(monkeypatch):
    today = date.today()
    d = lambda days_ago: (today - timedelta(days=days_ago)).strftime(cr.ICRA_DATE_FMT)
    calls = []

    def fake_fetch_page(session, token, page, company_name=""):
        calls.append(page)
        if page == 1:
            return _icra_table_html(_icra_row_html(rid="1", rating_date=d(0)))
        return _icra_table_html(_icra_row_html(rid="2", rating_date=d(20)))  # outside a 3-day window

    monkeypatch.setattr(cr, "_icra_fetch_page", fake_fetch_page)
    monkeypatch.setattr(cr, "icra_session", lambda: object())
    monkeypatch.setattr(cr, "_icra_token", lambda session: "fake-token")

    df = cr.icra_rating_actions(days_back=3, persist=False)
    assert calls == [1, 2], "must stop after the page that crosses the cutoff"
    assert len(df) == 1  # only page 1's row survives the cutoff filter


def test_icra_rating_actions_respects_max_pages_safety_cap(monkeypatch):
    """If the API never returns an empty page or an old-enough row, max_pages must still bound the walk."""
    calls = []

    def fake_fetch_page(session, token, page, company_name=""):
        calls.append(page)
        return _icra_table_html(_icra_row_html(rid=str(page), rating_date="28 Aug 2026"))

    monkeypatch.setattr(cr, "_icra_fetch_page", fake_fetch_page)
    monkeypatch.setattr(cr, "icra_session", lambda: object())
    monkeypatch.setattr(cr, "_icra_token", lambda session: "fake-token")

    cr.icra_rating_actions(days_back=9999, max_pages=5, persist=False)
    assert len(calls) == 5


# ================================================================== CARE
def _care_row(company_name="Sample Industries Limited", file_title="Sample Industries Limited",
             file_url="202608280000_Sample_Industries_Limited.pdf", published_date="2026-08-28 00:00:00.000"):
    return {
        "CompanyID": "opaque-encrypted-id==",
        "CompanyName": company_name,
        "FileTitle": file_title,
        "FileType": "PR",
        "FileURL": file_url,
        "PublishedDate": published_date,
    }


def test_care_parse_listing_maps_columns_and_builds_document_url():
    df = _care_parse_listing([_care_row()])
    row = df.iloc[0]
    assert row["company_name"] == "Sample Industries Limited"
    assert row["published_date"] == "2026-08-28"
    assert row["document_url"] == CARE_DOC_BASE_URL + row["file_url"]


def test_care_parse_listing_empty_input_returns_empty_frame():
    df = _care_parse_listing([])
    assert df.empty
    assert "document_url" in df.columns


def test_care_parse_listing_raises_on_schema_drift():
    broken = [_care_row()]
    del broken[0]["FileURL"]
    with pytest.raises(RuntimeError, match="schema drift"):
        _care_parse_listing(broken)


def test_care_parse_listing_sorted_newest_first():
    rows = [_care_row(published_date="2026-08-20 00:00:00.000"), _care_row(published_date="2026-08-28 00:00:00.000")]
    df = _care_parse_listing(rows)
    assert list(df["published_date"]) == ["2026-08-28", "2026-08-20"]


def test_care_search_by_company_raises_when_response_is_not_a_list(monkeypatch):
    """
    care.rrcompany returns a different ('data': int) shape for an empty/near-
    empty companyName -- observed live, not a bug in this parser. Must raise,
    not silently return nothing (connector contract: never return empty on
    a response the parser doesn't understand).
    """
    class FakeResponse:
        content = b'{"data": 0}'
        url = "https://www.careratings.com/rrcompany?companyName=&YearID=2026"

        def json(self):
            return {"data": 0}

    class FakeSession:
        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(cr, "care_session", lambda: FakeSession())

    with pytest.raises(RuntimeError, match="expected a list"):
        cr.care_search_by_company("", persist=False)


def test_care_search_by_company_parses_a_real_shaped_response(monkeypatch):
    class FakeResponse:
        content = b"{}"
        url = "https://www.careratings.com/rrcompany?companyName=Reliance&YearID=2026"

        def json(self):
            return {"data": [_care_row(company_name="Reliance Industries Limited")]}

    class FakeSession:
        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(cr, "care_session", lambda: FakeSession())

    df = cr.care_search_by_company("Reliance", persist=False)
    assert len(df) == 1
    assert df.iloc[0]["company_name"] == "Reliance Industries Limited"
