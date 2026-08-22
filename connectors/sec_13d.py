"""
SEC Schedule 13D -- activist stakes.

WHY THIS IS THE BEST SIGNAL-TO-COST DATASET ON EDGAR
    A 13D is filed when an investor crosses 5% of a class WITH intent to
    influence control. Since the 2024 amendments it is due within 5 business
    days, so it lands far ahead of the 13F that will eventually show the same
    position. Item 4 states the purpose in the filer's own words: board seats,
    strategic alternatives, sale of the company.

    Volume is tiny -- 2,711 filings per quarter market-wide, about 5 minutes at
    SEC's rate ceiling. Compare 90,116 insider filings.

DEFAULT UNIVERSE IS THE WHOLE MARKET, DELIBERATELY
    Restricting to the S&P 500 cuts this to 67 filings/quarter, and would throw
    away most of the signal: activists target small and mid caps, which is
    exactly where a 5% stake buys influence. The whole market is affordable
    here, so take it.

STRUCTURED XML, NOT SCRAPED HTML
    The 2024 rules mandated machine-readable filing, and it is live: every 13D
    carries primary_doc.xml under
    http://www.sec.gov/edgar/schedule13D, with the cover page, every reporting
    person's voting and dispositive power, and the Item 1-7 text as elements.
    No HTML parsing, no regex over prose for the numbers.

TWO TABLES, ON PURPOSE
    A filing carries MANY reporting persons -- the Tredegar example has eight
    (the GAMCO/Gabelli complex). Flattening persons onto the filing would
    repeat the Item text per person and double-count any sum over stakes, which
    is exactly the fan-out bug that inflated the insider and 13F datasets before
    it was caught. So:

        sec_13d_filings   one row per filing  (cover page + item text)
        sec_13d_persons   one row per (filing, reporting person)

AMENDMENTS DOMINATE, AND THAT IS THE POINT
    4,320 of 5,422 filings over two quarters are 13D/A. An amendment usually
    means the stake changed or the intent did -- escalation is the tradeable
    event, so amendmentNo and dateOfEvent are carried through.
"""
from __future__ import annotations

import re

import pandas as pd
from lxml import etree

from core.config import require
from core.http import cached_sec_session
from core.storage import db, register_views, save_raw, write_table
from connectors.sec_edgar import filing_index

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
FORMS = ("SCHEDULE 13D", "SCHEDULE 13D/A")

# Item 4 intent classifiers. These are a SCREEN, not a conclusion: the full text
# is always retained so a human reads the filing before anyone trades it.
PURPOSE_PATTERNS = {
    "seeks_sale_or_merger": r"strategic alternatives|sale of the (company|issuer)|"
                            r"merger|acquisition of the issuer|business combination|"
                            r"take[- ]private|tender offer",
    "board_representation": r"nominat\w+|board of directors|board seat|"
                            r"director candidate|proxy contest|consent solicitation",
    "engage_management":    r"engage\w* (?:in )?(?:discussions|dialogue)|"
                            r"communicat\w+ with (?:the )?(?:issuer|management)|"
                            r"discussions with (?:the )?(?:issuer|management|board)",
    "capital_return":       r"share repurchase|buyback|special dividend|return of capital",
    "opposes_transaction":  r"oppose|vote against|inadequate|undervalue\w*",
    "control_intent":       r"acquire control|controlling interest|majority of the",
}


def _s():
    return cached_sec_session(require("SEC_CONTACT_EMAIL"))


def _txt(el) -> str | None:
    if el is None:
        return None
    text = re.sub(r"\s+", " ", " ".join(el.itertext())).strip()
    return text or None


def _num(el) -> float | None:
    text = _txt(el)
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def discover(year: int, quarter: int, ciks: set[int] | None = None) -> pd.DataFrame:
    """
    Every distinct 13D/13D-A in a quarter, from SEC's own index (no per-filing
    cost).

    master.idx indexes a filing ONCE PER ASSOCIATED CIK, and a 13D is associated
    with both the reporting person and the subject issuer. So the raw index
    carries each 13D twice: 2,786 rows for 1,393 actual filings in 2025Q3.
    Without deduplication every document is fetched twice -- half the requests
    wasted -- and each reporting person is stored twice, double-counting any sum
    over stakes.

    Dedupe on accession, keeping the first CIK seen, since the accession alone
    identifies the document.
    """
    idx = filing_index(year, quarter)
    out = idx[idx["form"].isin(FORMS)].copy()
    if ciks is not None:
        # filter BEFORE dedup: either associated CIK may be the one of interest
        keep = set(out.loc[out["cik"].isin(ciks), "accn"])
        out = out[out["accn"].isin(keep)]
    out = out.drop_duplicates("accn", keep="first")
    out["amendment"] = out["form"].str.endswith("/A")
    return out.sort_values("filed", ignore_index=True)


def classify_purpose(item4: str | None) -> dict:
    """Keyword flags over Item 4. A screen to rank reading order, not a verdict."""
    flags = {k: False for k in PURPOSE_PATTERNS}
    if not item4:
        return flags
    low = item4.lower()
    for name, pattern in PURPOSE_PATTERNS.items():
        flags[name] = bool(re.search(pattern, low))
    return flags


def parse_filing(cik: int, accn: str, form: str | None = None,
                 filed: str | None = None) -> tuple[dict, list[dict]]:
    """
    Parse one 13D into (filing_row, person_rows).

    Archives primary_doc.xml before parsing, per rule 1.
    """
    folder = accn.replace("-", "")
    url = f"{ARCHIVES}/{int(cik)}/{folder}/primary_doc.xml"
    r = _s().get(url)
    save_raw("sec", "13d", r.content, "xml",
             {"cik": int(cik), "accn": accn, "url": url})

    root = etree.fromstring(r.content)
    # Wildcard namespace: filers vary between default and prefixed namespaces
    # for the same schema, and keying off nsmap[None] silently parses nothing.
    def find(path, node=root):
        return node.find(path)

    header = find("{*}headerData")
    cover = find("{*}formData/{*}coverPageHeader")
    issuer = find("{*}formData/{*}coverPageHeader/{*}issuerInfo")
    items = find("{*}formData/{*}items1To7")

    item_text = {}
    if items is not None:
        for child in items:
            item_text[etree.QName(child).localname] = _txt(child)

    filing = {
        "accn": accn,
        "filer_cik": int(cik),
        "form": form or _txt(find("{*}submissionType", header)),
        "filed": filed,
        "amendment_no": _txt(find("{*}amendmentNo", cover)) if cover is not None else None,
        "previous_accn": _txt(find("{*}previousAccessionNumber", header)) if header is not None else None,
        "date_of_event": _txt(find("{*}dateOfEvent", cover)) if cover is not None else None,
        "security_class": _txt(find("{*}securitiesClassTitle", cover)) if cover is not None else None,
        "issuer_cik": _txt(find("{*}issuerCIK", issuer)) if issuer is not None else None,
        "issuer_name": _txt(find("{*}issuerName", issuer)) if issuer is not None else None,
        "issuer_cusip": _txt(find("{*}issuerCUSIP", issuer)) if issuer is not None else None,
        "item1_security": item_text.get("item1"),
        "item2_identity": item_text.get("item2"),
        "item3_source_of_funds": item_text.get("item3"),
        "item4_purpose": item_text.get("item4"),
        "item5_interest": item_text.get("item5"),
        "item6_contracts": item_text.get("item6"),
        "item7_exhibits": item_text.get("item7"),
        "source_url": url,
    }
    filing.update(classify_purpose(filing["item4_purpose"]))

    persons = []
    for p in root.findall("{*}formData/{*}reportingPersons/{*}reportingPersonInfo"):
        persons.append({
            "accn": accn,
            "issuer_cik": filing["issuer_cik"],
            "issuer_name": filing["issuer_name"],
            "filed": filed,
            "person_cik": _txt(find("{*}reportingPersonCIK", p)),
            "person_name": _txt(find("{*}reportingPersonName", p)),
            "fund_type": _txt(find("{*}fundType", p)),
            "citizenship": _txt(find("{*}citizenshipOrOrganization", p)),
            "sole_voting": _num(find("{*}soleVotingPower", p)),
            "shared_voting": _num(find("{*}sharedVotingPower", p)),
            "sole_dispositive": _num(find("{*}soleDispositivePower", p)),
            "shared_dispositive": _num(find("{*}sharedDispositivePower", p)),
            "aggregate_owned": _num(find("{*}aggregateAmountOwned", p)),
            "percent_of_class": _num(find("{*}percentOfClass", p)),
            "excludes_shares": _txt(find("{*}isAggregateExcludeShares", p)),
            "person_type": "|".join(
                t for t in (_txt(x) for x in p.findall("{*}typeOfReportingPerson")) if t),
        })

    return filing, persons


def _ingested() -> set[str]:
    con = db()
    try:
        register_views(con)
        try:
            return {r[0] for r in con.execute("SELECT DISTINCT accn FROM sec_13d_filings").fetchall()}
        except Exception:
            return set()
    finally:
        con.close()


def ingest(year: int, quarter: int, ciks: set[int] | None = None,
           limit: int | None = None, persist: bool = True) -> dict:
    """
    Fetch and parse every 13D in a quarter that is not already stored.

    Individual parse failures are collected, not raised: one malformed filing
    must not block the other 2,700.
    """
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
        "failures": failures[:10],
        "n_failures": len(failures),
    }

    if filings:
        fdf = pd.DataFrame(filings)
        summary["new_13d"] = int((~fdf["form"].str.endswith("/A")).sum())
        summary["amendments"] = int(fdf["form"].str.endswith("/A").sum())
        summary["purpose_flags"] = {
            k: int(fdf[k].sum()) for k in PURPOSE_PATTERNS if k in fdf.columns
        }
        if persist:
            write_table(fdf, "sec", "13d_filings")
            write_table(pd.DataFrame(persons), "sec", "13d_persons")

    return summary


def latest_quarter(today=None) -> tuple[int, int]:
    """13Ds are filed within 5 business days, so the previous quarter is complete."""
    from datetime import date
    d = today or date.today()
    q = (d.month - 1) // 3 + 1
    return (d.year, q - 1) if q > 1 else (d.year - 1, 4)


if __name__ == "__main__":
    import json
    y, q = latest_quarter()
    print(json.dumps(ingest(y, q, limit=10, persist=False), indent=2, default=str))
