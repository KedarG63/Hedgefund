"""
Is a company name one this warehouse has actually seen traded on NSE/BSE?

WHY THIS EXISTS
    CRISIL/ICRA/CARE rate companies of every size, listed or not -- most of
    what they cover is privately held or an unlisted subsidiary (this is
    exactly the "hidden leverage" signal BUILD_GUIDE.md frames as the whole
    point of using rating rationales as an MCA substitute). Mixing that in
    with the small slice that is actually tradeable makes every ratings
    table read as noise. This module answers, per company name, which side
    of that line a row falls on.

WHAT "LISTED" MEANS HERE, AND WHAT IT DOES NOT
    Indian company law lets a company carry "Limited" in its name (public
    limited company status) without ever listing a single share -- "Limited"
    is a legal structure choice, not a listing fact. JM Financial Credit
    Solutions Limited, for instance, is an unlisted NBFC; only its parent,
    JM Financial Limited, is listed. So this does NOT parse the name for
    "Private"/"Pvt"/"LLP" vs plain "Limited" -- that is a different,
    legal-structure question this module was explicitly asked not to
    conflate with "listed". It checks company NAME against three
    NSE/BSE-listing-derived sources already in the warehouse:
        bse_scrip_master.Issuer_Name           ~5,700 distinct names, union'd
        nse_shareholding_pattern.name          (SEBI LODR filing => listed)
        nse_results_Quarterly.companyName      (quarterly-results filing => listed)

VERIFIED MATCH RATE (2026-08-29)
    Against live crisil_rating_actions / icra_rating_actions data: ~10% of
    distinct company names matched. That is the true number, not a matching
    failure -- spot-checking the "Limited"-named misses (JM Financial Credit
    Solutions Limited, Earth Minerals Company Limited, Ozone Pharmaceuticals
    Limited, ...) turned up genuinely unlisted public-limited companies and
    unlisted subsidiaries of listed parents, not formatting mismatches. One
    miss WAS a real bug and is fixed here: CRISIL's own "The Tata Power
    Company Limited" failed to match nse_shareholding_pattern's "Tata Power
    Company Limited" before the leading "THE " strip below.

THIS IS NAME MATCHING, NOT AN ISIN JOIN
    CRISIL/ICRA/CARE listing rows carry no ISIN, so this is exact matching on
    a normalized name string, not the ISIN join analytics/risk_model.py uses
    for market cap. A name spelled differently on either side is a real,
    uncaught false negative -- silently missing a company, not a wrong
    classification. Treat the result as "known-listed" vs "not known to be
    listed", not "confirmed private".
"""
from __future__ import annotations

import re

import pandas as pd

from core.storage import db, register_views

_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")
_PUNCT_RE = re.compile(r"[.,]")
_LEADING_THE_RE = re.compile(r"^THE\s+")
_AMPERSAND_RE = re.compile(r"\s*&\s*")
_WS_RE = re.compile(r"\s+")

LISTED_NAME_SOURCES = [
    ("bse_scrip_master", "Issuer_Name"),
    ("nse_shareholding_pattern", "name"),
    ("nse_results_Quarterly", "companyName"),
]


def normalize_name(name: str | None) -> str | None:
    """
    Uppercase, strip parenthetical suffixes ("(Erstwhile ...)", "(Formerly
    known as ...)"), punctuation, a leading "THE ", normalize "&" to "AND",
    and collapse whitespace. Applied identically to both sides of every
    comparison in this module -- a normalization applied to only one side is
    a silent source of false negatives.
    """
    if name is None:
        return None
    s = str(name).upper().strip()
    s = _PAREN_RE.sub(" ", s)
    s = _PUNCT_RE.sub("", s)
    s = _LEADING_THE_RE.sub("", s)
    s = _AMPERSAND_RE.sub(" AND ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s or None


def listed_company_names() -> set[str]:
    """
    Normalized names of every company this warehouse has directly observed
    on NSE/BSE -- the union of the three sources in LISTED_NAME_SOURCES.
    A source missing from the warehouse (not yet ingested) is skipped, not
    an error -- partial coverage is expected on a freshly started pipeline.
    """
    con = db(read_only=True)
    try:
        register_views(con)
        available = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        names: set[str] = set()
        for view, col in LISTED_NAME_SOURCES:
            if view not in available:
                continue
            rows = con.execute(f"SELECT DISTINCT {col} FROM {view} WHERE {col} IS NOT NULL").fetchall()
            for (raw,) in rows:
                n = normalize_name(raw)
                if n:
                    names.add(n)
        return names
    finally:
        con.close()


def is_listed(company_name: str | None, listed_names: set[str]) -> bool:
    n = normalize_name(company_name)
    return n is not None and n in listed_names


def tag_listing_status(df: pd.DataFrame, name_col: str, listed_names: set[str]) -> pd.DataFrame:
    """Adds a `listing_status` column: 'Public (listed)' or 'Private/unlisted'."""
    df = df.copy()
    df["listing_status"] = df[name_col].apply(
        lambda n: "Public (listed)" if is_listed(n, listed_names) else "Private/unlisted")
    return df
