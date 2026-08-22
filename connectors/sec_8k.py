"""
SEC Form 8-K -- material events, item-coded.

WHY 8-K IS THE MOST PRICE-RELEVANT FILING CLASS
    An 8-K is due within 4 business days of a material event, and every one is
    tagged with item numbers that classify what happened. That makes the
    classification free: no NLP, no document parsing, just a code. A restatement
    (4.02), an auditor change (4.01) and a CFO departure (5.02) are all
    identifiable from metadata alone.

ONE REQUEST PER COMPANY, NOT PER FILING
    The submissions API returns `items` alongside every filing, so a single call
    per issuer yields its entire recent 8-K history already item-coded. For the
    S&P 500 that is ~492 requests covering tens of thousands of 8-Ks, against
    ~1,638 document fetches per quarter for the same universe -- and it comes
    with reportDate (when the event happened) as well as filingDate.

    This is the same lesson as the insider bulk file: find the endpoint that
    answers in aggregate before looping over documents.

WHAT THIS MODULE DOES NOT DO
    It does not fetch the 8-K documents themselves. Item codes say WHAT
    happened, not the detail -- an 8-K 2.02 tells you earnings were released,
    not what they were. Fetching bodies is worth it for a shortlist (say every
    4.02 and 5.02), and `primary_document_url()` builds those URLs, but pulling
    all of them is a document-scale job that should be filtered first.

    Note also that `filings.recent` covers roughly the last 1,000 filings per
    company. Deeper history lives in the `filings.files` shards, which are not
    wired up yet -- for the S&P 500 the recent window still spans many years.
"""
from __future__ import annotations

import json

import pandas as pd

from core.storage import db, register_views, write_table
from connectors.sec_edgar import submissions

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

# Form 8-K item codes. `tier` reflects how much a code moves a thesis, not how
# common it is -- 9.01 appears on most filings and means only "exhibits attached".
ITEM_CODES: dict[str, tuple[str, str]] = {
    "1.01": ("Entry into a Material Definitive Agreement", "high"),
    "1.02": ("Termination of a Material Definitive Agreement", "high"),
    "1.03": ("Bankruptcy or Receivership", "critical"),
    "1.04": ("Mine Safety - Reporting of Shutdowns", "low"),
    "1.05": ("Material Cybersecurity Incident", "critical"),
    "2.01": ("Completion of Acquisition or Disposition of Assets", "high"),
    "2.02": ("Results of Operations and Financial Condition", "high"),
    "2.03": ("Creation of a Direct Financial Obligation", "medium"),
    "2.04": ("Triggering Events That Accelerate a Financial Obligation", "critical"),
    "2.05": ("Costs Associated with Exit or Disposal Activities", "medium"),
    "2.06": ("Material Impairments", "critical"),
    "3.01": ("Notice of Delisting or Failure to Satisfy a Listing Rule", "critical"),
    "3.02": ("Unregistered Sales of Equity Securities", "medium"),
    "3.03": ("Material Modification to Rights of Security Holders", "medium"),
    "4.01": ("Changes in Registrant's Certifying Accountant", "critical"),
    "4.02": ("Non-Reliance on Previously Issued Financial Statements", "critical"),
    "5.01": ("Changes in Control of Registrant", "critical"),
    "5.02": ("Departure or Election of Directors or Officers", "high"),
    "5.03": ("Amendments to Articles or Bylaws; Change in Fiscal Year", "low"),
    "5.04": ("Temporary Suspension of Trading Under Employee Benefit Plans", "medium"),
    "5.05": ("Amendment to Code of Ethics", "low"),
    "5.06": ("Change in Shell Company Status", "medium"),
    "5.07": ("Submission of Matters to a Vote of Security Holders", "low"),
    "5.08": ("Shareholder Director Nominations", "medium"),
    "6.01": ("ABS Informational and Computational Material", "low"),
    "6.02": ("Change of Servicer or Trustee", "low"),
    "6.03": ("Change in Credit Enhancement or Other External Support", "medium"),
    "6.04": ("Failure to Make a Required Distribution", "critical"),
    "6.05": ("Securities Act Updating Disclosure", "low"),
    "7.01": ("Regulation FD Disclosure", "low"),
    "8.01": ("Other Events", "low"),
    "9.01": ("Financial Statements and Exhibits", "low"),
}

# The short-side triggers worth alerting on the day they appear.
RED_FLAG_ITEMS = ("1.03", "2.04", "2.06", "3.01", "4.01", "4.02", "6.04")


def item_description(code: str) -> str:
    return ITEM_CODES.get(code, ("Unknown item", "unknown"))[0]


def item_tier(code: str) -> str:
    return ITEM_CODES.get(code, ("Unknown item", "unknown"))[1]


def sp500_ciks() -> set[int]:
    """S&P 500 CIKs from the stored constituent table (see sec_edgar)."""
    con = db()
    try:
        register_views(con)
        rows = con.execute(
            "SELECT DISTINCT cik FROM sec_sp500_constituents WHERE cik IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {int(r[0]) for r in rows if r[0]}


def primary_document_url(cik: int, accn: str, primary_document: str) -> str:
    """URL of the 8-K body, for the shortlist worth reading."""
    return f"{ARCHIVES}/{int(cik)}/{accn.replace('-', '')}/{primary_document}"


def eight_k_filings(ciks=None, since: str | None = None,
                    persist: bool = True) -> pd.DataFrame:
    """
    Every 8-K for a universe, exploded to ONE ROW PER (filing, item).

    Why exploded: `items` arrives as "2.02,9.01". Keeping the comma string
    forces every downstream query into substring matching, which silently
    matches "5.02" inside "15.02"-style values and cannot be joined or grouped.
    One row per item is the natural grain.

    Columns worth knowing:
      report_date   when the EVENT happened (8-K reportDate)
      filed         when it was disclosed
      days_to_file  the gap. 8-K allows 4 business days; a longer gap is itself
                    a governance signal.
      tier          critical / high / medium / low -- see ITEM_CODES
    """
    if ciks is None:
        ciks = sp500_ciks()
    ciks = sorted(int(c) for c in ciks)

    frames, failures = [], []
    for cik in ciks:
        try:
            subs = submissions(cik)
        except Exception as e:                       # noqa -- collect, keep going
            failures.append((cik, f"{type(e).__name__}: {str(e)[:80]}"))
            continue
        if subs.empty or "form" not in subs.columns:
            continue
        k = subs[subs["form"].astype(str).str.startswith("8-K")].copy()
        if k.empty:
            continue
        k["cik"] = cik
        frames.append(k)

    if not frames:
        raise RuntimeError(
            f"eight_k_filings found no 8-Ks across {len(ciks)} CIKs "
            f"({len(failures)} request failures)"
        )

    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={
        "accessionNumber": "accn",
        "filingDate": "filed",
        "reportDate": "report_date",
        "primaryDocument": "primary_document",
    })

    if since:
        df = df[df["filed"] >= since]

    # explode "2.02,9.01" -> one row per item
    df["items"] = df["items"].fillna("")
    df["item_list"] = df["items"].str.split(",")
    out = df.explode("item_list").copy()
    out["item"] = out["item_list"].astype(str).str.strip()
    out = out[out["item"] != ""]

    out["item_description"] = out["item"].map(item_description)
    out["tier"] = out["item"].map(item_tier)
    out["is_red_flag"] = out["item"].isin(RED_FLAG_ITEMS)

    # An 8-K/A carries the ORIGINAL event's reportDate, so its gap measures how
    # long the amendment took, not how long disclosure took -- Corteva shows 947
    # days. Leaving that in days_to_file makes every amendment look like a
    # governance failure, so the gap is only computed for original filings.
    out["is_amendment"] = out["form"].astype(str).str.endswith("/A")

    filed = pd.to_datetime(out["filed"], errors="coerce")
    reported = pd.to_datetime(out["report_date"], errors="coerce")
    gap = (filed - reported).dt.days
    out["days_to_file"] = gap.where(~out["is_amendment"])
    out["amendment_lag_days"] = gap.where(out["is_amendment"])

    out["document_url"] = [
        primary_document_url(c, a, p) if isinstance(p, str) and p else None
        for c, a, p in zip(out["cik"], out["accn"], out["primary_document"])
    ]

    cols = ["cik", "accn", "form", "item", "item_description", "tier",
            "is_red_flag", "is_amendment", "report_date", "filed",
            "days_to_file", "amendment_lag_days",
            "primary_document", "document_url"]
    out = out[[c for c in cols if c in out.columns]].sort_values(
        ["filed", "cik", "item"], ascending=[False, True, True], ignore_index=True)

    if persist:
        write_table(out, "sec", "8k_items")
    return out


def red_flags(filings: pd.DataFrame, since: str | None = None) -> pd.DataFrame:
    """
    The short-side subset: restatements, auditor changes, impairments,
    delisting notices, bankruptcy and debt-acceleration triggers.

    Deliberately excludes 5.02 (officer departures): those are frequent and
    mostly routine retirements and board rotations, so they belong in a
    screen you read, not an alert that pages you.
    """
    out = filings[filings["is_red_flag"]].copy()
    if since:
        out = out[out["filed"] >= since]
    return out.sort_values(["filed", "cik"], ascending=[False, True], ignore_index=True)


if __name__ == "__main__":
    df = eight_k_filings(persist=False)
    print(f"{len(df):,} (filing, item) rows for {df.cik.nunique()} companies")
    print(df["tier"].value_counts().to_string())
