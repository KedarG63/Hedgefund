"""
SEC companyfacts bulk -- every XBRL fact every filer ever reported, one download.

WHAT IT REPLACES
    data.sec.gov/api/xbrl/companyfacts/CIK##########.json answers for one
    company per request. The bulk archive carries all of them: ~1.4 GB covering
    every filer, replacing ~10,000 requests. After it lands, ANY company's
    complete fact history is free and offline.

WHAT IT UNLOCKS THAT frames() CANNOT
    frames() is cross-sectional: one concept, one period, every filer. Excellent
    for building a panel, but it answers nothing about a single company's
    history and it only sees standard us-gaap concepts.

    companyfacts is the transpose -- one company, every concept, every period,
    every filing that ever stated them. That gives three things frames cannot:

      1. NON-CORE STANDARD TAXONOMIES. Beyond us-gaap and dei, filers report
         under ecd (executive compensation), ffd, srt, invest, cef, vip, spac
         and ifrs-full. frames() only serves the taxonomy you name, so these are
         easy to miss entirely.

         VERIFIED LIMIT, worth stating because it is widely misreported:
         companyfacts contains NO company-defined extension tags. Sampling 400
         companies yields exactly ten prefixes, all SEC-standard. There is no
         "nvda:DataCenterRevenue" here. Company extension concepts exist only in
         the raw XBRL instance documents attached to individual filings, so
         anything built on custom KPIs must parse those, not this archive.

      2. RESTATEMENT DETECTION. Every fact carries the accession and `filed`
         date of the filing that stated it, so the SAME (concept, period)
         appearing twice with different values and different filed dates is a
         revision. This is the "silent revision" problem the dashboard phase is
         meant to catch, except here the data ships with the evidence.

         WHAT THE BIG ONES ACTUALLY ARE, once scale changes are excluded: mostly
         TAGGING CORRECTIONS, not accounting restatements. Corteva reported
         NetIncomeLossAvailableToCommonStockholdersBasic for FY2021 as 2.39 USD
         in both its 2022 and 2023 10-Ks, then as 1,759,000,000 USD in the 2024
         one -- it had been tagging earnings per share into the concept for
         total net income, under the same USD unit, for two years. Anyone
         reading that figure from the original filing is off by nine orders of
         magnitude, and nothing about the value looks wrong in isolation.

      3. FULL HISTORY at no marginal request cost, so a factor model can widen
         beyond a handful of fields without another fetch.

COST AND SHAPE
    The archive is one JSON per company, named CIK##########.json. Do NOT parse
    it whole: ~10,000 companies at ~25,000 facts each is a quarter-billion rows.
    Read the members you need -- zipfile does that without extracting the rest.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from core.config import require
from core.http import cached_sec_session
from core.storage import find_raw, save_raw_stream, write_table

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"


def _s():
    return cached_sec_session(require("SEC_CONTACT_EMAIL"))


def fetch_bulk(refetch: bool = False) -> Path:
    """
    Download and archive the bulk file, streaming so 1.4 GB never sits in memory.

    Returns the archived path. An existing copy is reused unless refetch=True --
    this is a monthly-cadence artefact, not something to pull on every run.
    """
    cached = find_raw("sec", "companyfacts_bulk", url=BULK_URL)
    if cached is not None and not refetch:
        return cached

    with _s().client.stream("GET", BULK_URL, timeout=None) as r:
        r.raise_for_status()
        return save_raw_stream("sec", "companyfacts_bulk", r.iter_bytes(chunk_size=1 << 20),
                               ext="zip", meta={"url": BULK_URL})


def _archive(path: Path | None = None) -> zipfile.ZipFile:
    path = path or fetch_bulk()
    return zipfile.ZipFile(path)


def member_name(cik: int | str) -> str:
    return f"CIK{str(int(cik)).zfill(10)}.json"


def facts_for(cik: int | str, zf: zipfile.ZipFile | None = None) -> dict:
    """One company's complete fact set, read from the archive without extracting."""
    close = zf is None
    zf = zf or _archive()
    try:
        with zf.open(member_name(cik)) as fh:
            return json.load(fh)
    finally:
        if close:
            zf.close()


def facts_to_frame(facts: dict) -> pd.DataFrame:
    """
    Flatten one company's facts to tidy rows.

    Mirrors sec_edgar.facts_to_frame but additionally keeps `frame`, SEC's own
    label for the period a fact belongs to, which is what lets a caller line up
    comparable quarters without re-deriving them from start/end dates.
    """
    rows = []
    for taxonomy, concepts in (facts.get("facts") or {}).items():
        for concept, body in concepts.items():
            for unit, points in (body.get("units") or {}).items():
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
                        "filed": p.get("filed"),       # makes it point-in-time
                        "accn": p.get("accn"),
                        "frame": p.get("frame"),
                    })
    df = pd.DataFrame(rows)
    if not df.empty:
        # companyfacts returns `cik` as an int for most companies and a
        # zero-padded string for some ("0001858681"). Mixed types make the
        # column object dtype, which fails the parquet write with a type error
        # only once enough companies are concatenated -- so it passes on a small
        # sample and breaks on the full universe.
        df["cik"] = pd.to_numeric(df["cik"], errors="coerce").astype("Int64")
    return df


# Every taxonomy prefix present in the bulk archive, confirmed by sampling 400
# companies. All are SEC-standard; none is company-defined. CORE are the two
# almost every filer uses, so "non-core" means the rest, not "custom".
CORE_TAXONOMIES = ("us-gaap", "dei")
STANDARD_TAXONOMIES = CORE_TAXONOMIES + (
    "srt", "ifrs-full", "invest", "ecd", "ffd", "cef", "vip", "spac")


def non_core_taxonomies(facts: dict) -> pd.DataFrame:
    """
    Concepts a company reports OUTSIDE us-gaap/dei -- executive compensation
    (ecd), fund data (invest, cef), and so on.

    This is deliberately NOT called custom_tags: companyfacts carries no
    company-defined extension concepts, only the ten standard taxonomies. See
    the module docstring.
    """
    rows = []
    for taxonomy, concepts in (facts.get("facts") or {}).items():
        if taxonomy in CORE_TAXONOMIES:
            continue
        for concept, body in concepts.items():
            n = sum(len(v) for v in (body.get("units") or {}).values())
            rows.append({
                "cik": facts.get("cik"),
                "entity": facts.get("entityName"),
                "taxonomy": taxonomy,
                "concept": concept,
                "label": body.get("label"),
                "n_facts": n,
            })
    return pd.DataFrame(rows).sort_values("n_facts", ascending=False, ignore_index=True) \
        if rows else pd.DataFrame()


def restatements(frame: pd.DataFrame, min_pct_change: float = 0.0) -> pd.DataFrame:
    """
    Facts that were later reported differently for the SAME concept and period.

    A revision is (taxonomy, concept, unit, start, end) appearing more than once
    with different values across filings. Returns the first and latest reported
    values with the change between them.

    Why it matters: a backtest that reads the latest value silently uses numbers
    nobody had at the time. This identifies exactly where that bites, and the
    `filed` dates say when each version became knowable.
    """
    key = ["taxonomy", "concept", "unit", "start", "end"]
    df = frame.dropna(subset=["value", "filed"]).copy()
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values("filed")
    grouped = df.groupby(key, dropna=False)

    revised = grouped["value"].nunique()
    revised = revised[revised > 1]
    if revised.empty:
        return pd.DataFrame()

    first = grouped.first()
    last = grouped.last()
    out = pd.DataFrame({
        "first_value": first["value"],
        "first_filed": first["filed"],
        "first_accn": first["accn"],
        "latest_value": last["value"],
        "latest_filed": last["filed"],
        "latest_accn": last["accn"],
        "n_versions": grouped["value"].nunique(),
    }).loc[revised.index].reset_index()

    # Use numpy NaN, not pd.NA: pd.NA in an arithmetic chain yields an OBJECT
    # dtype column, which compares and sorts as text. That passes a sort with an
    # explicit key but breaks nlargest and any downstream numeric filter -- a
    # silent failure, since the values still LOOK like numbers.
    import numpy as np

    first = pd.to_numeric(out["first_value"], errors="coerce")
    latest = pd.to_numeric(out["latest_value"], errors="coerce")
    out["first_value"] = first
    out["latest_value"] = latest

    out["abs_change"] = latest - first
    denom = first.abs().replace(0, np.nan)
    out["pct_change"] = ((out["abs_change"] / denom) * 100).astype("float64")

    # A filer switching reporting scale restates every affected line by a power
    # of 1000. Two real shapes, both of which dominate any list sorted by size:
    #   thousands -> dollars    Tesla 2016 debt: 7,511,760 -> 7,511,760,000
    #   billions  -> dollars    McDonald's 2020 cash: 3.4 -> 3,449,100,000
    #
    # The second is why an exact 1000x test is not enough: "3.4 billion" was
    # rounded before rescaling, so the ratio is 1.0144e9 rather than 1e9. Test
    # the ORDER OF MAGNITUDE instead -- log10 within ~7% of 3, 6 or 9. A genuine
    # restatement (Apple's 20m -> -1,068m, ratio 53) sits nowhere near those.
    ratio = (latest / first.replace(0, np.nan)).abs().astype("float64")
    out["value_ratio"] = ratio

    with np.errstate(divide="ignore", invalid="ignore"):
        magnitude = np.log10(ratio.where(ratio > 0))
    out["looks_like_scale_change"] = (
        pd.Series(magnitude, index=out.index)
          .abs()
          .apply(lambda m: any(abs(m - p) < 0.03 for p in (3, 6, 9))
                 if pd.notna(m) else False)
    )

    if min_pct_change:
        out = out[out["pct_change"].abs() >= min_pct_change]

    return out.sort_values("pct_change", key=lambda s: s.abs(),
                           ascending=False, ignore_index=True)


def universe_restatements(ciks, min_pct_change: float = 1.0,
                          exclude_scale_changes: bool = True,
                          persist: bool = True) -> pd.DataFrame:
    """
    Revisions across a whole universe, computed company by company.

    Deliberately NOT "flatten everything then group": 492 companies at ~25,000
    facts each is 12M rows and several GB held at once. Revisions are a tiny
    fraction of that, so each company is reduced before moving to the next.

    This is the point-in-time audit the dashboard phase needs -- it says exactly
    which (company, concept, period) figures changed after first publication,
    and when each version became knowable.
    """
    zf = _archive()
    out, missing = [], []
    try:
        for cik in ciks:
            try:
                facts = facts_for(cik, zf)
            except KeyError:
                missing.append(int(cik))
                continue
            rs = restatements(facts_to_frame(facts), min_pct_change=min_pct_change)
            if rs.empty:
                continue
            if exclude_scale_changes:
                rs = rs[~rs["looks_like_scale_change"]]
            if rs.empty:
                continue
            rs["cik"] = int(cik)
            rs["entity"] = facts.get("entityName")
            out.append(rs)
    finally:
        zf.close()

    if not out:
        return pd.DataFrame()

    df = pd.concat(out, ignore_index=True)
    df.attrs["missing_ciks"] = missing
    if persist:
        write_table(df, "sec", "companyfacts_restatements")
    return df


def concept_panel(ciks, concepts: list[tuple[str, str]], persist: bool = True,
                  zf: zipfile.ZipFile | None = None) -> pd.DataFrame:
    """
    Long panel of chosen (taxonomy, concept) pairs across many companies,
    entirely from the archive -- no network, so widening the concept list is free.

    concepts: [("us-gaap", "Revenues"), ("us-gaap", "Assets"), ...]
    """
    close = zf is None
    zf = zf or _archive()
    wanted = set(concepts)
    try:
        frames, missing = [], []
        for cik in ciks:
            try:
                facts = facts_for(cik, zf)
            except KeyError:
                missing.append(int(cik))
                continue
            df = facts_to_frame(facts)
            if df.empty:
                continue
            df = df[[(t, c) in wanted for t, c in zip(df["taxonomy"], df["concept"])]]
            if not df.empty:
                frames.append(df)
    finally:
        if close:
            zf.close()

    if not frames:
        raise RuntimeError(
            f"concept_panel matched no facts for {len(list(ciks))} CIKs "
            f"({len(missing)} absent from the archive)"
        )

    out = pd.concat(frames, ignore_index=True)
    out.attrs["missing_ciks"] = missing
    if persist:
        write_table(out, "sec", "companyfacts_panel")
    return out


if __name__ == "__main__":
    path = fetch_bulk()
    print(f"archive: {path.name}  {path.stat().st_size/1e9:.2f} GB")
    with _archive(path) as zf:
        print(f"members: {len(zf.namelist()):,}")
        facts = facts_for(320193, zf)
    df = facts_to_frame(facts)
    print(f"AAPL: {len(df):,} facts, {df.concept.nunique()} concepts")
