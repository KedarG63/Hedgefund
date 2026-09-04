"""
Indian credit-rating agencies -- market-wide rating-action feeds (upgrades,
downgrades, new assignments, withdrawals, "issuer not cooperating" flags),
lender/facility detail where an agency exposes it structured, and the
underlying rationale documents. One module for three agencies (CRISIL, ICRA,
CARE/CareEdge) because they are the same domain with a uniform-ish output
shape, the same relationship the nse_bse.py module has to NSE + BSE -- but
their endpoint MECHANICS differ enough that every function below is prefixed
by agency, not shared, except classify_action().

WHY THIS MATTERS
    connectors/mca_charges.py names credit-rating rationales as "the single
    best substitute" for the CAPTCHA-gated MCA charge register: these
    agencies itemise every facility, limit and lender in (or alongside) the
    rationale, updated on every rating action -- often more current than a
    quarterly filing -- and cover unlisted subsidiaries a listed parent's
    consolidated accounts can hide behind. This module is that substitute.

HOW EACH ENDPOINT WAS FOUND
    Every endpoint below was found by driving the agency's own live search
    page with Playwright and reading the resulting XHR/fetch call --
    tools/discover_crisil_rationale_api.py, discover_icra_rationale_api.py,
    discover_care_rationale_api.py -- then re-verified against a bare httpx
    client. None were guessed. Re-run the relevant discovery script if any of
    this ever breaks; do not re-guess.

COVERAGE SUMMARY (verified 2026-08-28)
    CRISIL  Full market-wide daily feed + on-demand company search. Stateless
            JSON, no session needed. Rationale documents archived as HTML;
            the itemised facility/lender detail lives in that document's
            prose and is NOT parsed out (a future pass).
    ICRA    Full market-wide daily feed + on-demand company search. Needs an
            ASP.NET anti-forgery token (session-based POST). UNIQUELY exposes
            lender-wise facility/amount as its own structured HTML table
            (icra_bank_facilities()) -- no prose-parsing needed, the closest
            free substitute this repo has found yet to MCA's charge register.
    CARE    On-demand company search ONLY. No verified market-wide feed was
            found -- see care_search_by_company()'s docstring for why. Do not
            build a daily job on top of this until one is found.
"""
from __future__ import annotations

import io
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta

import pandas as pd
from lxml import html as lxml_html

from core.http import care_session, crisil_session, icra_session
from core.storage import save_raw, write_table

# ============================================================ shared
# First match wins -- headline actions are effectively mutually exclusive.
# Verified against both CRISIL and ICRA headline phrasing, which turned out
# to use near-identical vocabulary ("reaffirmed", "placed on Watch", "issuer
# not cooperating", ...) -- not assumed, checked against live samples from
# both agencies before treating this as shared rather than per-agency.
ACTION_PATTERNS = [
    # CRISIL says "issuer not cooperating"; ICRA says "issuer non-cooperating"
    # (also seen without the hyphen) -- both catch the same regulatory flag.
    ("issuer_not_cooperating", r"issuer non-?cooperating|issuer not cooperating"),
    ("downgrade", r"downgrad"),
    ("upgrade", r"upgrad"),
    ("watch", r"\bwatch\b"),  # not "placed on watch" -- "continues on 'Watch Developing'"
                              # has a quote between "on" and "watch" and would not match that
    ("migrated", r"migrat"),  # scale/category migration with no up/down direction stated
    ("withdrawn", r"withdraw"),
    ("reaffirmed", r"reaffirm"),
    ("assigned", r"assign"),
]


def classify_action(heading: str) -> str:
    """Category over a rating-agency headline. A screen to rank reading order, not a verdict."""
    low = (heading or "").lower()
    for name, pattern in ACTION_PATTERNS:
        if re.search(pattern, low):
            return name
    return "other"


# ============================================================ CRISIL
# ENDPOINT CONTRACT (verified 2026-08-28)
#   GET https://www.crisilratings.com/content/crisilratings/en/home/
#       our-business/ratings/rating-rationale/_jcr_content/wrapper_100_par/
#       ratingresultlisting.results.json?cmd=RR&start=<int>&limit=<int>&filters=<json>
#
#   start/limit are NOT page/page-size. The full corpus (numFound -- ~39,500
#   rationales at capture time) is sorted newest-first, and a request returns
#   that list SLICED as list[start:limit] -- limit is an absolute end index,
#   not a count. Confirmed by direct probing: start=100&limit=200 returned
#   exactly 100 rows (limit - start); start=0&limit=2000 returned exactly
#   2000. To walk the feed you therefore widen the window (0,500), (500,1000),
#   ... rather than incrementing a page number.
#
#   filters={"company_name": "<substring>"} does a substring search over
#   company name (confirmed: "Tata" -> 43 rows spanning every Tata-group
#   entity). No working date-range filter was found: the visible from/to
#   date-picker inputs did not change results when round-tripped as filter
#   keys, so date windowing here is done by walking the (already
#   date-sorted) feed and stopping once ratingDate falls outside the window.
#
#   Individual rationale document:
#   GET https://www.crisilratings.com/mnt/winshare/Ratings/RatingList/RatingDocs/<ratingFileName>
#   (base path taken verbatim from the search page's own hidden
#   #service-file-base-path field, not guessed.) Returns a self-contained
#   HTML "Credit Bulletin" report -- the itemised facility/lender/limit detail
#   lives in there, not in the listing row. Archived raw on request; parsing
#   the facility table out of it is a separate future pass.
CRISIL_LISTING_URL = (
    "https://www.crisilratings.com/content/crisilratings/en/home/our-business/"
    "ratings/rating-rationale/_jcr_content/wrapper_100_par/ratingresultlisting.results.json"
)
CRISIL_DOC_BASE_URL = "https://www.crisilratings.com/mnt/winshare/Ratings/RatingList/RatingDocs/"
CRISIL_DATE_FMT = "%b %d, %Y"
CRISIL_PAGE_WINDOW = 500
CRISIL_EXPECTED_COLUMNS = {"companyCode", "companyName", "ratingDate", "transDate", "heading",
                          "ratingFileName", "prId"}


def _crisil_parse_date(s: str) -> date:
    return datetime.strptime(s, CRISIL_DATE_FMT).date()


def _crisil_fetch_page(session, start: int, end: int, filters: dict | None = None) -> dict:
    params = {"cmd": "RR", "start": str(start), "limit": str(end), "filters": json.dumps(filters or {})}
    r = session.get(CRISIL_LISTING_URL, params=params)
    save_raw("crisil", "rating_rationale_listing", r.content, "json",
             {"start": start, "end": end, "filters": filters or {}, "url": str(r.url)})
    return r.json()


def _crisil_parse_listing(docs: list[dict]) -> pd.DataFrame:
    if not docs:
        return pd.DataFrame(columns=[
            "company_code", "company_name", "industry_name", "rating_date", "trans_date",
            "heading", "rating_file_name", "document_url", "pr_id", "action_type",
        ])

    df = pd.DataFrame(docs)
    missing = CRISIL_EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(
            f"crisil.rating_rationale_listing: schema drift -- expected column(s) "
            f"{sorted(missing)} not found. Got: {sorted(df.columns)}. "
            f"The site's schema changed; update the parser before trusting this data."
        )

    df = df.drop_duplicates(subset=["prId"]).reset_index(drop=True)
    out = pd.DataFrame({
        "company_code": df["companyCode"],
        "company_name": df["companyName"],
        "industry_name": df.get("industryName", ""),
        "rating_date": df["ratingDate"].apply(lambda s: _crisil_parse_date(s).isoformat()),
        "trans_date": df["transDate"].apply(lambda s: _crisil_parse_date(s).isoformat()),
        "heading": df["heading"],
        "rating_file_name": df["ratingFileName"],
        "document_url": df["ratingFileName"].apply(lambda f: CRISIL_DOC_BASE_URL + urllib.parse.quote(f)),
        "pr_id": df["prId"],
        "action_type": df["heading"].apply(classify_action),
    })
    return out.sort_values("rating_date", ascending=False, ignore_index=True)


def crisil_fetch_rationale_document(rating_file_name: str, session=None) -> bytes:
    """
    Archive one rationale's full HTML "Credit Bulletin" report -- the itemised
    facility/lender/limit detail lives here, not in the listing row. This only
    fetches and archives raw bytes (rule 1); parsing the facility table out of
    it is a separate future pass.
    """
    session = session or crisil_session()
    url = CRISIL_DOC_BASE_URL + urllib.parse.quote(rating_file_name)
    r = session.get(url)
    save_raw("crisil", "rationale_document", r.content, "html",
             {"rating_file_name": rating_file_name, "url": url})
    return r.content


def crisil_rating_actions(days_back: int = 7, fetch_documents: bool = False, persist: bool = True) -> pd.DataFrame:
    """
    Market-wide rating actions from the trailing `days_back` window.

    No universe list needed: unlike MCA (CIN-gated, no bulk index), CRISIL's
    listing endpoint is itself a global feed sorted newest-first, so pulling
    the trailing window catches every rated Indian company's new rating
    actions in a handful of requests -- the same "bulk index, not N per-name
    requests" shape as sec_daily_index and nse_corporate_announcements.
    """
    cutoff = date.today() - timedelta(days=days_back)
    session = crisil_session()
    rows: list[dict] = []
    start = 0
    while True:
        end = start + CRISIL_PAGE_WINDOW
        payload = _crisil_fetch_page(session, start, end)
        docs = payload.get("docs", [])
        if not docs:
            break
        rows.extend(docs)
        oldest = _crisil_parse_date(docs[-1]["ratingDate"])
        num_found = payload.get("numFound")
        if oldest < cutoff or (num_found is not None and end >= num_found):
            break
        start = end

    df = _crisil_parse_listing(rows)
    if not df.empty:
        df = df[df["rating_date"] >= cutoff.isoformat()].reset_index(drop=True)

    if fetch_documents:
        for fname in df["rating_file_name"]:
            crisil_fetch_rationale_document(fname, session=session)

    if persist and not df.empty:
        write_table(df, "crisil", "rating_actions")
    return df


def crisil_search_by_company(company_name: str, max_docs: int = 2000,
                             fetch_documents: bool = False, persist: bool = True) -> pd.DataFrame:
    """
    On-demand lookup: every rationale CRISIL has published whose company name
    contains `company_name` (substring match, confirmed live: "Tata" returns
    all 43 Tata-group entities). Use this for a specific-name deep dive; use
    crisil_rating_actions() for the daily market-wide feed.
    """
    session = crisil_session()
    payload = _crisil_fetch_page(session, 0, max_docs, filters={"company_name": company_name})
    df = _crisil_parse_listing(payload.get("docs", []))

    if fetch_documents:
        for fname in df["rating_file_name"]:
            crisil_fetch_rationale_document(fname, session=session)

    if persist and not df.empty:
        write_table(df, "crisil", "rating_actions")
    return df


# ============================================================ ICRA
# ENDPOINT CONTRACT (verified 2026-08-28)
#   Search page: GET https://www.icra.in/Rating/AllRatingRationales
#   Listing:     POST https://www.icra.in/Rating/GetAllRatingRational
#                    ?page=<int>&type=IsESR
#                body: __RequestVerificationToken=<token>&CompanyName=<substr>&
#                      FromDate=&ToDate=&RatingCategoryName=
#
#   Unlike CRISIL, ICRA requires an ASP.NET anti-forgery token: GET the search
#   page once for a __RequestVerificationToken hidden-input value (NOT just
#   the same-named cookie ASP.NET also sets -- the FORM value is what the
#   server checks) and send it back on every POST. type=IsESR was captured
#   off the site's own pagination click and returns the combined
#   "Corporate & Financial Sector" table (both categories appear together in
#   results) -- the separate "Structured Finance Rating Rationales" tab's
#   type value was not captured and is not used here.
#
#   CompanyName does a substring match (confirmed live: "Tata" -> 10 Tata-
#   group rows on page 1). FromDate/ToDate were sent empty in every capture
#   and their format was never observed -- NOT used here; date windowing
#   walks pages instead, same reasoning as CRISIL.
#
#   Response is a server-rendered HTML *partial* (a table fragment), not
#   JSON -- ICRA_PAGE_SIZE (10, observed live; undocumented) rows per page,
#   no total-count field found anywhere in the markup, so pagination stops
#   on a cutoff crossing or an empty page, not a known total.
#
#   Each row links to /Rationale/ShowRationaleReport?Id=<id> (rationale text),
#   /Rating/GetRationalReportFilePdf?Id=<id> (PDF), and -- for rows that carry
#   one -- /Rating/BankFacilities?CompanyId=<id>&CompanyName=<name>, ICRA's
#   OWN structured lender-wise facility table (verified: a sample company
#   returned 12 rows of Name of Lender / Facility-Instrument / Amount (Rs.
#   Crore), e.g. "HDFC Bank Limited | Cash Credit | 5"). This is the single
#   best find in this whole module for the "hidden leverage" thesis: no prose
#   parsing needed, it is already a clean table.
ICRA_SEARCH_PAGE = "https://www.icra.in/Rating/AllRatingRationales"
ICRA_LIST_URL = "https://www.icra.in/Rating/GetAllRatingRational"
ICRA_DOC_URL = "https://www.icra.in/Rationale/ShowRationaleReport?Id={id}"
ICRA_PDF_URL = "https://www.icra.in/Rating/GetRationalReportFilePdf?Id={id}"
ICRA_FACILITIES_URL = "https://www.icra.in/Rating/BankFacilities"
ICRA_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
ICRA_DATE_FMT = "%d %b %Y"


def _icra_token(session) -> str:
    """One priming GET for the anti-forgery token. Not archived -- the shell
    page carries no dataset content, same as nse_session()'s priming GETs."""
    r = session.get(ICRA_SEARCH_PAGE)
    m = ICRA_TOKEN_RE.search(r.text)
    if not m:
        raise RuntimeError(
            "icra: __RequestVerificationToken not found on the search page -- "
            "the site's form markup changed; re-run tools/discover_icra_rationale_api.py"
        )
    return m.group(1)


def _icra_parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), ICRA_DATE_FMT).date()


def _icra_fetch_page(session, token: str, page: int, company_name: str = "") -> str:
    r = session.post(ICRA_LIST_URL, params={"page": str(page), "type": "IsESR"},
                     data={"__RequestVerificationToken": token, "CompanyName": company_name,
                           "FromDate": "", "ToDate": "", "RatingCategoryName": ""},
                     headers={"X-Requested-With": "XMLHttpRequest"})
    save_raw("icra", "rating_rationale_listing", r.content, "html",
             {"page": page, "company_name": company_name, "url": str(r.url)})
    return r.text


def _icra_parse_rows(html_text: str) -> pd.DataFrame:
    cols = ["rationale_id", "company_name", "company_id", "rating_date", "rating_category",
            "heading", "document_url", "pdf_url", "action_type"]
    doc = lxml_html.fromstring(html_text or "<html></html>")
    rows = []
    for tr in doc.xpath('//table//tr[td]'):
        link = tr.xpath('.//a[contains(@href,"ShowRationaleReport")]')
        if not link:
            continue  # header or pagination-only row, not a data row
        rid = link[0].get("href").rsplit("Id=", 1)[-1]
        heading = link[0].text_content().strip()
        tds = tr.xpath("./td")
        rating_date_txt = tds[0].text_content().strip() if tds else ""
        cat = tr.xpath('.//span[@class="ratingCategoryName"]')
        company_id = company_name = None
        fac = tr.xpath('.//a[contains(@href,"BankFacilities")]')
        if fac:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(fac[0].get("href")).query)
            company_id = qs.get("CompanyId", [None])[0]
            company_name = qs.get("CompanyName", [None])[0]
        rows.append({
            "rationale_id": rid,
            "company_name": company_name,
            "company_id": company_id,
            "rating_date": rating_date_txt,
            "rating_category": cat[0].text_content().strip() if cat else "",
            "heading": heading,
        })

    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    df["rating_date"] = df["rating_date"].apply(lambda s: _icra_parse_date(s).isoformat())
    df["document_url"] = df["rationale_id"].apply(lambda i: ICRA_DOC_URL.format(id=i))
    df["pdf_url"] = df["rationale_id"].apply(lambda i: ICRA_PDF_URL.format(id=i))
    df["action_type"] = df["heading"].apply(classify_action)
    return df[cols].sort_values("rating_date", ascending=False, ignore_index=True)


def icra_fetch_rationale_pdf(rationale_id: str, session=None) -> bytes:
    """Archive one rationale's PDF, raw. ICRA's HTML rationale view was not
    probed as a separate archival target -- the PDF is the canonical document."""
    session = session or icra_session()
    url = ICRA_PDF_URL.format(id=rationale_id)
    r = session.get(url)
    save_raw("icra", "rationale_document", r.content, "pdf", {"rationale_id": rationale_id, "url": url})
    return r.content


def icra_bank_facilities(company_id: str, company_name: str, session=None, persist: bool = True) -> pd.DataFrame:
    """
    Lender-wise facility table for one company -- ICRA exposes this directly
    as a structured HTML table (verified live: 12 rows of lender / facility /
    amount for a sample company), unlike CRISIL where the same class of
    detail is prose inside the rationale document and is not parsed here.
    This is the closest free substitute this repo has found yet to the MCA
    CHG-1 charge register's lender/amount detail.
    """
    session = session or icra_session()
    r = session.get(ICRA_FACILITIES_URL, params={"CompanyId": company_id, "CompanyName": company_name})
    save_raw("icra", "bank_facilities", r.content, "html",
             {"company_id": company_id, "company_name": company_name, "url": str(r.url)})

    doc = lxml_html.fromstring(r.content)
    tables = doc.xpath("//table")
    cols = ["company_id", "company_name", "lender", "facility", "amount_inr_crore"]
    if not tables:
        return pd.DataFrame(columns=cols)

    table = pd.read_html(io.StringIO(lxml_html.tostring(tables[0], encoding="unicode")))[0]
    if table.shape[1] != 3:
        raise RuntimeError(
            f"icra.bank_facilities: expected 3 columns (lender, facility, amount), "
            f"got {list(table.columns)} for CompanyId={company_id} -- page layout changed"
        )
    table.columns = ["lender", "facility", "amount_inr_crore"]
    table.insert(0, "company_name", company_name)
    table.insert(0, "company_id", company_id)
    table["amount_inr_crore"] = pd.to_numeric(table["amount_inr_crore"], errors="coerce")

    if persist and not table.empty:
        write_table(table, "icra", "bank_facilities")
    return table


def icra_rating_actions(days_back: int = 7, max_pages: int = 50,
                        fetch_documents: bool = False, persist: bool = True) -> pd.DataFrame:
    """
    Market-wide feed, ICRA's equivalent of crisil_rating_actions(). Pages are
    walked forward (empty CompanyName = combined corporate + financial-sector
    table, newest first) until either the oldest row on a page crosses the
    cutoff or a page comes back with no rationale rows -- there is no
    numFound-style total to check against here, unlike CRISIL, so max_pages
    is a hard safety cap against an API change that stops terminating cleanly.
    """
    cutoff = date.today() - timedelta(days=days_back)
    session = icra_session()
    token = _icra_token(session)

    frames = []
    for page in range(1, max_pages + 1):
        df_page = _icra_parse_rows(_icra_fetch_page(session, token, page))
        if df_page.empty:
            break
        frames.append(df_page)
        if df_page["rating_date"].min() < cutoff.isoformat():
            break

    df = pd.concat(frames, ignore_index=True) if frames else _icra_parse_rows("")
    if not df.empty:
        df = df.drop_duplicates(subset=["rationale_id"]).reset_index(drop=True)
        df = df[df["rating_date"] >= cutoff.isoformat()].reset_index(drop=True)

    if fetch_documents:
        for rid in df["rationale_id"]:
            icra_fetch_rationale_pdf(rid, session=session)

    if persist and not df.empty:
        write_table(df, "icra", "rating_actions")
    return df


def icra_search_by_company(company_name: str, max_pages: int = 20,
                           fetch_documents: bool = False, persist: bool = True) -> pd.DataFrame:
    """On-demand lookup by company-name substring. Use icra_rating_actions() for the daily feed."""
    session = icra_session()
    token = _icra_token(session)

    frames = []
    for page in range(1, max_pages + 1):
        df_page = _icra_parse_rows(_icra_fetch_page(session, token, page, company_name=company_name))
        if df_page.empty:
            break
        frames.append(df_page)

    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["rationale_id"]).reset_index(drop=True) \
        if frames else _icra_parse_rows("")

    if fetch_documents:
        for rid in df["rationale_id"]:
            icra_fetch_rationale_pdf(rid, session=session)

    if persist and not df.empty:
        write_table(df, "icra", "rating_actions")
    return df


# ============================================================ CARE / CareEdge
# ENDPOINT CONTRACT (verified 2026-08-28)
#   GET https://www.careratings.com/rrcompany
#       ?companyName=<substr>&YearID=<int>&fdate=&tdate=
#
#   companyName filtering IS real for a distinctive legal-entity name --
#   confirmed: "ZZZNONEXISTENTCOMPANY999" -> 0 rows; "Tata" -> 668 rows, all
#   Tata-group entities; "Reliance Industries" -> 34 rows, all Reliance
#   Industries Limited across years. But "IDBI" (a common bank/lender name,
#   not just an issuer) returned 1000 rows (a hard cap) that were NOT
#   IDBI-specific -- identical, date-sorted-only results regardless of
#   YearID. Most likely explanation: the match runs over more than the
#   company-name field (e.g. lender mentions inside other companies'
#   releases), so a common term returns an apparently-unfiltered feed capped
#   at 1000 and sorted by recency, not relevance. YearID/fdate/tdate did not
#   visibly change results in any test run here and are sent empty/as-observed
#   rather than relied on.
#
#   THIS IS WHY THERE IS NO care_rating_actions() MARKET-WIDE DAILY FEED: an
#   empty companyName returns a different response shape entirely ('data' as
#   an int, not a list -- observed, not guessed around), and there is no
#   other endpoint found here that lists CARE's rating actions market-wide by
#   date the way CRISIL's and ICRA's do. Do not build one on an assumption;
#   re-run tools/discover_care_rationale_api.py and verify first.
#
#   Response items: CompanyID (opaque, encrypted-looking, not otherwise
#   usable), CompanyName, FileTitle, FileType, FileURL, PublishedDate.
#   Document: https://www.careratings.com/upload/CompanyFiles/PR/<FileURL>
#   (verified: fetched one, got back a real 200KB application/pdf). No
#   headline text is provided at the listing level, so classify_action() is
#   NOT applied here -- CARE gives no text to classify from without opening
#   the PDF itself, unlike CRISIL/ICRA's headline-carrying rows.
CARE_SEARCH_URL = "https://www.careratings.com/rrcompany"
CARE_DOC_BASE_URL = "https://www.careratings.com/upload/CompanyFiles/PR/"
CARE_EXPECTED_COLUMNS = {"CompanyName", "FileTitle", "FileType", "FileURL", "PublishedDate"}


def _care_parse_listing(rows: list[dict]) -> pd.DataFrame:
    cols = ["company_name", "file_title", "file_type", "published_date", "file_url", "document_url"]
    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    missing = CARE_EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(
            f"care.rrcompany: schema drift -- expected column(s) {sorted(missing)} not found. "
            f"Got: {sorted(df.columns)}."
        )

    out = pd.DataFrame({
        "company_name": df["CompanyName"],
        "file_title": df["FileTitle"],
        "file_type": df["FileType"],
        "published_date": pd.to_datetime(df["PublishedDate"]).dt.date.astype(str),
        "file_url": df["FileURL"],
        "document_url": CARE_DOC_BASE_URL + df["FileURL"],
    })
    return out.sort_values("published_date", ascending=False, ignore_index=True)


def care_fetch_rationale_document(file_url: str, session=None) -> bytes:
    """Archive one CARE press-release/rationale PDF, raw."""
    session = session or care_session()
    url = CARE_DOC_BASE_URL + file_url
    r = session.get(url)
    save_raw("care", "rationale_document", r.content, "pdf", {"file_url": file_url, "url": url})
    return r.content


def care_search_by_company(company_name: str, year_id: int | None = None,
                           fetch_documents: bool = False, persist: bool = True) -> pd.DataFrame:
    """
    On-demand company lookup ONLY -- see the module-level CARE contract note
    above for why there is no market-wide feed here. Prefer a specific legal
    entity name ("Reliance Industries Limited") over an abbreviation or a
    common financial term -- the latter can return an effectively-unfiltered,
    capped, date-sorted feed instead of a real match set.
    """
    session = care_session()
    r = session.get(CARE_SEARCH_URL, params={
        "companyName": company_name,
        "YearID": str(year_id or date.today().year),
        "fdate": "", "tdate": "",
    })
    save_raw("care", "rating_rationale_listing", r.content, "json",
             {"company_name": company_name, "year_id": year_id, "url": str(r.url)})

    payload = r.json()
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(
            f"care.rrcompany: expected a list under 'data' for company_name={company_name!r}, "
            f"got {type(rows).__name__} instead -- an empty/near-empty query returns a "
            f"different response shape here (observed, not a bug in this parser)"
        )

    df = _care_parse_listing(rows)
    if fetch_documents:
        for fname in df["file_url"]:
            care_fetch_rationale_document(fname, session=session)
    if persist and not df.empty:
        write_table(df, "care", "rating_actions")
    return df
