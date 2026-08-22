"""
SEC Schedule 13G -- passive 5% holders, and the 13G -> 13D transition.

WHY 13G MATTERS DESPITE BEING THE "BORING" FILING
    A 13G says an investor crossed 5% WITHOUT intent to influence control. On
    its own that is weak: it is what index funds and custodians file by the
    thousand, and BlackRock owning 6% of something is not news.

    Its value is as the BASELINE against which 13D means something. An investor
    who has filed 13G on a name for years and then switches to 13D has publicly
    declared they stopped being passive -- that switch is a stronger signal than
    a fresh 13D from a stranger, because the position already existed and only
    the intent changed. transitions() below finds exactly those.

THE FILING RULE IS THE INTERESTING FIELD
    designateRulePursuantThisScheduleFiled says which exemption was used:

        Rule 13d-1(b)   qualified institutional investor -- index funds,
                        banks, insurers. Highest volume, lowest signal.
        Rule 13d-1(c)   passive investor: under 20% and explicitly not held to
                        influence control. A hedge fund filing 13d-1(c) is
                        worth more attention than a custodian filing 13d-1(b).
        Rule 13d-1(d)   exempt.

DIFFERENT SCHEMA FROM 13D, DESPITE THE FAMILY RESEMBLANCE
    Verified against live filings -- the two are NOT interchangeable:

        13D                                   13G
        namespace .../schedule13D             .../schedule13g   (lowercase)
        reportingPersons/reportingPersonInfo  coverPageHeaderReportingPersonDetails
        percentOfClass                        classPercent
        aggregateAmountOwned                  reportingPersonBeneficiallyOwned...
        dateOfEvent                           eventDateRequiresFilingThisStatement
        items1To7 (item1..item5)              items (item1..item10)

    Element lookups use {*} wildcards so the namespace difference is irrelevant,
    but the element NAMES had to be read off real filings.

    There is no Item 4 "purpose" narrative here. Item 10 is the certification
    that the securities were not acquired to influence control -- which is the
    whole point of filing 13G rather than 13D.

SAME TWO STRUCTURAL TRAPS AS 13D, HANDLED THE SAME WAY
    - The filing index lists each filing once per ASSOCIATED CIK, so 13G appears
      twice (reporting person and issuer): 11,656 index rows for 5,828 real
      filings in 2025Q3. Deduplicate on accession or fetch everything twice.
    - A filing carries many reporting persons (Citadel files seven), so persons
      go in their own table rather than being flattened onto the filing.
"""
from __future__ import annotations

import pandas as pd
from lxml import etree

from core.config import require
from core.http import cached_sec_session
from core.storage import db, register_views, save_raw, write_table
from connectors.sec_edgar import filing_index
from connectors.sec_13d import _num, _txt

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
FORMS = ("SCHEDULE 13G", "SCHEDULE 13G/A")

FILING_RULES = {
    "Rule 13d-1(b)": "qualified institutional investor",
    "Rule 13d-1(c)": "passive investor",
    "Rule 13d-1(d)": "exempt",
}


def _s():
    return cached_sec_session(require("SEC_CONTACT_EMAIL"))


def discover(year: int, quarter: int, ciks: set[int] | None = None) -> pd.DataFrame:
    """Every distinct 13G/13G-A in a quarter. See the dedup note in the docstring."""
    idx = filing_index(year, quarter)
    out = idx[idx["form"].isin(FORMS)].copy()
    if ciks is not None:
        keep = set(out.loc[out["cik"].isin(ciks), "accn"])
        out = out[out["accn"].isin(keep)]
    out = out.drop_duplicates("accn", keep="first")
    out["amendment"] = out["form"].str.endswith("/A")
    return out.sort_values("filed", ignore_index=True)


def _first(node, *names):
    """
    First matching child among several spellings.

    Necessary because 13G and 13D disagree on CAPITALISATION for the same
    fields: 13G writes <issuerCik>/<issuerCusip>, 13D writes
    <issuerCIK>/<issuerCUSIP>. XML element names are case-sensitive, so using
    one spelling against the other yields None rather than an error -- the whole
    issuer column came back NULL before this was caught, which in turn made the
    13G->13D join match nothing and report zero transitions.
    """
    if node is None:
        return None
    for name in names:
        found = node.find("{*}" + name)
        if found is not None:
            return found
    return None


def parse_xml(content: bytes, accn: str, cik: int, form: str | None = None,
              filed: str | None = None, url: str | None = None
              ) -> tuple[dict, list[dict]]:
    """Parse 13G XML bytes. Split out so archived files can be re-parsed offline."""
    root = etree.fromstring(content)
    header = root.find("{*}headerData")
    cover = root.find("{*}formData/{*}coverPageHeader")
    issuer = root.find("{*}formData/{*}coverPageHeader/{*}issuerInfo")
    items = root.find("{*}formData/{*}items")

    rules = [_txt(n) for n in root.findall(
        "{*}formData/{*}coverPageHeader/{*}designateRulesPursuantThisScheduleFiled/"
        "{*}designateRulePursuantThisScheduleFiled")]
    rules = [x for x in rules if x]

    item_text = {}
    if items is not None:
        for child in items:
            item_text[etree.QName(child).localname] = _txt(child)

    filing = {
        "accn": accn,
        "filer_cik": int(cik),
        "form": form or (_txt(header.find("{*}submissionType")) if header is not None else None),
        "filed": filed,
        "event_date": _txt(cover.find("{*}eventDateRequiresFilingThisStatement")) if cover is not None else None,
        "security_class": _txt(cover.find("{*}securitiesClassTitle")) if cover is not None else None,
        "issuer_cik": _txt(_first(issuer, "issuerCik", "issuerCIK")),
        "issuer_name": _txt(_first(issuer, "issuerName")),
        "issuer_cusip": _txt(_first(issuer, "issuerCusip", "issuerCUSIP")),
        "filing_rule": rules[0] if rules else None,
        "filing_rule_desc": FILING_RULES.get(rules[0]) if rules else None,
        "item1_issuer": item_text.get("item1"),
        "item2_identity": item_text.get("item2"),
        "item4_ownership": item_text.get("item4"),
        # Item 10 is the certification that the stake is NOT held to influence
        # control -- the substantive difference from a 13D.
        "item10_certification": item_text.get("item10"),
        "source_url": url,
    }
    filing["filer_cik"] = int(cik)

    persons = []
    for p in root.findall("{*}formData/{*}coverPageHeaderReportingPersonDetails"):
        owned = p.find("{*}reportingPersonBeneficiallyOwnedNumberOfShares")
        persons.append({
            "accn": accn,
            "issuer_cik": filing["issuer_cik"],
            "issuer_name": filing["issuer_name"],
            "filed": filed,
            "person_name": _txt(p.find("{*}reportingPersonName")),
            "citizenship": _txt(p.find("{*}citizenshipOrOrganization")),
            "sole_voting": _num(owned.find("{*}soleVotingPower")) if owned is not None else None,
            "shared_voting": _num(owned.find("{*}sharedVotingPower")) if owned is not None else None,
            "sole_dispositive": _num(owned.find("{*}soleDispositivePower")) if owned is not None else None,
            "shared_dispositive": _num(owned.find("{*}sharedDispositivePower")) if owned is not None else None,
            "aggregate_owned": _num(p.find("{*}reportingPersonBeneficiallyOwnedAggregateNumberOfShares")),
            "percent_of_class": _num(p.find("{*}classPercent")),
            "excludes_shares": _txt(p.find("{*}aggregateAmountExcludesCertainSharesFlag")),
            "person_type": "|".join(
                t for t in (_txt(x) for x in p.findall("{*}typeOfReportingPerson")) if t),
        })

    return filing, persons


def parse_filing(cik: int, accn: str, form: str | None = None,
                 filed: str | None = None) -> tuple[dict, list[dict]]:
    """Fetch one 13G, archive it (rule 1), then parse from those bytes."""
    folder = accn.replace("-", "")
    url = f"{ARCHIVES}/{int(cik)}/{folder}/primary_doc.xml"
    r = _s().get(url)
    # Record form and filed in the sidecar: neither appears inside the XML, and
    # without them an offline re-parse cannot reconstruct the filing date.
    save_raw("sec", "13g", r.content, "xml",
             {"cik": int(cik), "accn": accn, "url": url,
              "form": form, "filed": filed})
    return parse_xml(r.content, accn, cik, form=form, filed=filed, url=url)


def reparse_archive(quarters=((2025, 3),), persist: bool = True) -> dict:
    """
    Rebuild the parsed tables from ARCHIVED bytes, with no network at all.

    This is exactly what rule 1 exists for. When the issuerCik capitalisation
    bug was found, every 13G had already been fetched -- 5,828 documents over
    40 minutes -- and re-parsing them offline took minutes. Had only the parsed
    output been kept, that quarter would have had to be fetched again.

    Filing dates come from the quarterly index rather than the XML, because the
    document does not carry them and older sidecars predate recording them.
    """
    import json as _json
    from core.storage import RAW

    dates, forms = {}, {}
    for year, q in quarters:
        idx = discover(year, q)
        dates.update(dict(zip(idx["accn"], idx["filed"])))
        forms.update(dict(zip(idx["accn"], idx["form"])))

    folder = RAW / "sec" / "13g"
    filings, persons, failures = [], [], []
    for meta_path in folder.rglob("*.meta.json"):
        try:
            meta = _json.loads(meta_path.read_text())
            data_path = meta_path.with_name(meta_path.name[: -len(".meta.json")])
            if not data_path.exists():
                continue
            accn = meta["accn"]
            f, ps = parse_xml(data_path.read_bytes(), accn, int(meta["cik"]),
                              form=meta.get("form") or forms.get(accn),
                              filed=meta.get("filed") or dates.get(accn),
                              url=meta.get("url"))
        except Exception as e:                       # noqa
            failures.append(f"{meta_path.name}: {type(e).__name__}: {str(e)[:80]}")
            continue
        filings.append(f)
        persons.extend(ps)

    summary = {"reparsed": len(filings), "reporting_persons": len(persons),
               "n_failures": len(failures), "failures": failures[:5]}
    if filings and persist:
        write_table(pd.DataFrame(filings), "sec", "13g_filings")
        write_table(pd.DataFrame(persons), "sec", "13g_persons")
    return summary


def _ingested() -> set[str]:
    con = db()
    try:
        register_views(con)
        try:
            return {r[0] for r in con.execute(
                "SELECT DISTINCT accn FROM sec_13g_filings").fetchall()}
        except Exception:
            return set()
    finally:
        con.close()


def ingest(year: int, quarter: int, ciks: set[int] | None = None,
           limit: int | None = None, persist: bool = True) -> dict:
    """Fetch and parse every 13G in a quarter not already stored."""
    found = discover(year, quarter, ciks)
    done = _ingested()
    todo = found[~found["accn"].isin(done)]
    if limit:
        todo = todo.head(limit)

    filings, persons, failures = [], [], []
    for _, row in todo.iterrows():
        try:
            f, ps = parse_filing(int(row["cik"]), row["accn"],
                                 form=row["form"], filed=row["filed"])
        except Exception as e:                       # noqa -- collect, keep going
            failures.append({"accn": row["accn"], "cik": int(row["cik"]),
                             "error": f"{type(e).__name__}: {str(e)[:110]}"})
            continue
        filings.append(f)
        persons.extend(ps)

    summary = {
        "quarter": f"{year}Q{quarter}",
        "found": len(found),
        "already_ingested": int(found["accn"].isin(done).sum()),
        "parsed": len(filings),
        "reporting_persons": len(persons),
        "n_failures": len(failures),
        "failures": failures[:10],
    }
    if filings:
        fdf = pd.DataFrame(filings)
        summary["new_13g"] = int((~fdf["form"].str.endswith("/A")).sum())
        summary["amendments"] = int(fdf["form"].str.endswith("/A").sum())
        summary["filing_rules"] = fdf["filing_rule"].value_counts(dropna=False).to_dict()
        if persist:
            write_table(fdf, "sec", "13g_filings")
            write_table(pd.DataFrame(persons), "sec", "13g_persons")
    return summary


# ------------------------------------------------------- the 13G -> 13D switch
def _norm(s: pd.Series) -> pd.Series:
    """
    Loose name key: 13G and 13D spell the same filer differently.

    Dots and apostrophes are DELETED, not blanked, before anything else. "L.P."
    must collapse to "LP" so the legal-form stopword strips it; blanking gives
    "L P", which survives as two tokens and makes
    "Elliott Investment Management L.P." fail to match "ELLIOTT INVESTMENT
    MANAGEMENT LP". That failure is silent -- the transition simply never
    appears.
    """
    out = s.fillna("").astype(str).str.upper()
    out = out.str.replace(r"[.'’]", "", regex=True)
    out = out.str.replace(r"[^A-Z0-9 ]", " ", regex=True)
    out = out.str.replace(
        r"\b(INC|INCORPORATED|LLC|LP|LTD|CORP|CORPORATION|CO|COMPANY|THE|"
        r"HOLDINGS|HOLDING|GROUP|MANAGEMENT|CAPITAL|PARTNERS|ADVISORS|ADVISERS)\b",
        " ", regex=True)
    return out.str.replace(r"\s+", " ", regex=True).str.strip()


def transitions(persist: bool = True) -> pd.DataFrame:
    """
    Investors who filed 13G on an issuer and LATER filed 13D on the same issuer.

    This is the signal 13G exists to enable. A fresh 13D from a stranger says
    someone built a stake; a 13G -> 13D switch says someone who already held the
    stake, and had formally certified they were passive, has changed their mind.
    The position is already there, so only intent moved.

    Matched on (issuer, normalised filer name) because 13G person blocks carry
    no CIK -- only 13D's do -- so a CIK join would find nothing. Name matching
    is loose by design here and the output is a SHORTLIST TO READ, not a
    conclusion: both filings are linked so the pair can be checked by eye.
    """
    con = db()
    try:
        register_views(con)
        try:
            g = con.execute("""
                SELECT issuer_cik, issuer_name, person_name, min(filed) AS first_13g,
                       max(filed) AS last_13g, count(*) AS n_13g
                FROM sec_13g_persons
                WHERE issuer_cik IS NOT NULL AND person_name IS NOT NULL
                GROUP BY 1, 2, 3
            """).df()
            d = con.execute("""
                SELECT p.issuer_cik, p.person_name, min(p.filed) AS first_13d,
                       count(*) AS n_13d
                FROM sec_13d_persons p
                WHERE p.issuer_cik IS NOT NULL AND p.person_name IS NOT NULL
                GROUP BY 1, 2
            """).df()
        except Exception as e:
            raise RuntimeError(
                "transitions() needs both sec_13g_persons and sec_13d_persons. "
                f"Ingest both before calling it. ({type(e).__name__})"
            ) from e
    finally:
        con.close()

    if g.empty or d.empty:
        return pd.DataFrame()

    g["key"] = _norm(g["person_name"])
    d["key"] = _norm(d["person_name"])

    merged = g.merge(d, on=["issuer_cik", "key"], how="inner",
                     suffixes=("_13g", "_13d"))
    if merged.empty:
        return pd.DataFrame()

    # Only a switch if the 13D came AFTER the 13G. The reverse ordering happens
    # too (an activist winding down to passive) and is a different event.
    out = merged[merged["first_13d"] > merged["first_13g"]].copy()
    if out.empty:
        return pd.DataFrame()

    out["days_passive_first"] = (
        pd.to_datetime(out["first_13d"], errors="coerce")
        - pd.to_datetime(out["first_13g"], errors="coerce")
    ).dt.days

    out = out[["issuer_cik", "issuer_name", "person_name_13g", "person_name_13d",
               "first_13g", "last_13g", "n_13g", "first_13d", "n_13d",
               "days_passive_first"]].sort_values(
        "first_13d", ascending=False, ignore_index=True)

    if persist:
        write_table(out, "sec", "13g_to_13d_transitions")
    return out


def latest_quarter(today=None) -> tuple[int, int]:
    from datetime import date
    d = today or date.today()
    q = (d.month - 1) // 3 + 1
    return (d.year, q - 1) if q > 1 else (d.year - 1, 4)


if __name__ == "__main__":
    import json
    y, q = latest_quarter()
    print(json.dumps(ingest(y, q, limit=10, persist=False), indent=2, default=str))
