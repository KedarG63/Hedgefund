"""
SEC EDGAR -- the easiest primary source on earth. Free, documented, no key.
Works fine from India. Only requirement: a User-Agent with your email.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
import json
import pandas as pd
from core.http import sec_session
from core.storage import save_raw, write_table

EMAIL = "research@yourfund.in"  # <-- CHANGE THIS. SEC 403s you otherwise.


def _s():
    return sec_session(EMAIL)


def ticker_map() -> pd.DataFrame:
    """Every SEC-registered ticker -> CIK. Your US security master starting point."""
    r = _s().get("https://www.sec.gov/files/company_tickers.json")
    save_raw("sec", "ticker_map", r.content, "json")
    df = pd.DataFrame(json.loads(r.content).values())
    df["cik_padded"] = df["cik_str"].astype(str).str.zfill(10)
    return write_table(df, "sec", "ticker_map") and df


def company_facts(cik: str | int) -> dict:
    """
    EVERY XBRL fact a company ever filed, in one JSON. Revenue, assets, EPS,
    segment tags -- all of it, with the filing date attached (= point-in-time).
    """
    cik10 = str(cik).zfill(10)
    r = _s().get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")
    save_raw("sec", "companyfacts", r.content, "json", {"cik": cik10})
    return json.loads(r.content)


def facts_to_frame(facts: dict) -> pd.DataFrame:
    """Flatten companyfacts JSON into a tidy dataframe you can actually query."""
    rows = []
    for taxonomy, concepts in facts.get("facts", {}).items():
        for concept, body in concepts.items():
            for unit, points in body.get("units", {}).items():
                for p in points:
                    rows.append({
                        "cik": facts.get("cik"),
                        "entity": facts.get("entityName"),
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "label": body.get("label"),
                        "unit": unit,
                        "start": p.get("start"),
                        "end": p.get("end"),
                        "value": p.get("val"),
                        "fy": p.get("fy"),
                        "fp": p.get("fp"),
                        "form": p.get("form"),
                        # THIS is the field that makes it point-in-time:
                        "filed": p.get("filed"),
                        "accn": p.get("accn"),
                    })
    return pd.DataFrame(rows)


def frames(concept: str, period: str, taxonomy: str = "us-gaap", unit: str = "USD") -> pd.DataFrame:
    """
    Cross-sectional: one concept, one period, EVERY company at once.
    e.g. frames("Revenues", "CY2025Q1") -> revenue for all US filers.
    This is how you build a fundamentals panel in minutes instead of weeks.
    """
    url = f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/{period}.json"
    r = _s().get(url)
    save_raw("sec", "frames", r.content, "json", {"concept": concept, "period": period})
    return pd.DataFrame(json.loads(r.content).get("data", []))


def submissions(cik: str | int) -> pd.DataFrame:
    """Every filing a company has made, with accession numbers and dates."""
    cik10 = str(cik).zfill(10)
    r = _s().get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    save_raw("sec", "submissions", r.content, "json", {"cik": cik10})
    recent = json.loads(r.content)["filings"]["recent"]
    return pd.DataFrame(recent)


def daily_index(date: str) -> str:
    """
    Every filing made on a given day, market-wide. date = 'YYYYMMDD'.
    Poll this each evening and you have a complete US filings feed.
    """
    y, q = date[:4], (int(date[4:6]) - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{date}.idx"
    r = _s().get(url)
    save_raw("sec", "daily_index", r.content, "idx", {"date": date})
    return r.text


def full_text_search(query: str, forms: str = "", start: str = "", end: str = "") -> dict:
    """
    Undocumented backend of EDGAR full-text search. Searches filing BODIES,
    not just metadata. Use it to find every mention of a phrase across filings.
    """
    params = {"q": query}
    if forms:
        params["forms"] = forms
    if start:
        params["startdt"], params["enddt"] = start, end
    r = _s().get("https://efts.sec.gov/LATEST/search-index", params=params)
    return json.loads(r.content)
