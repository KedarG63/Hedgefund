"""
SEC Form 13F -- institutional positioning, disclosed.

WHAT IT IS
    Every institutional investment manager with at least $100m in 13(f)
    qualifying US equities must file Form 13F-HR quarterly, within 45 days of
    quarter end, listing every position: issuer, CUSIP, market value, share
    count and voting authority.

WHY IT MATTERS FOR RESEARCH
    This is the US analogue of NSE's participant-wise open interest -- the one
    dataset that tells you what institutions actually own rather than what they
    say. It supports crowding and consensus-ownership factors, and quarter-on-
    quarter deltas give entries and exits per manager.

    Treat it as a POSITIONING factor, never a timing signal. The 45-day lag
    means a position you read today was held up to 4.5 months ago. Every row
    carries `filed` alongside `report_period` so a backtest can respect that;
    core.storage adds knowledge_date on top.

FOUR TRAPS, ALL HANDLED HERE
    1. 13F-NT is a NOTICE, not a portfolio. It says "my holdings are reported
       by another manager" and contains no holdings at all. Six of the largest
       names on the watchlist file both HR and NT across their entity trees.
       Treating an NT as an empty portfolio reads as a manager liquidating
       everything. NTs are recorded in a separate dataset so the absence is
       explained rather than silent.

    2. The `value` unit changed. Filings under the amended rules report whole
       dollars; older ones report THOUSANDS. Getting this wrong is a silent
       1000x error that still looks like a plausible portfolio. Rather than
       trusting a cutover date, the scale is inferred per filing from implied
       price per share and recorded in `value_scale`.

    3. Duplicate line items. The same security appears more than once when
       separate internal managers report it (see `other_manager`). Apple appears
       twice in Berkshire's table. Rows are kept AS FILED -- aggregation is a
       query-time concern -- so always GROUP BY cusip before ranking holdings.

    4. Amendments. 13F-HR/A restates a prior filing. Both are retained; use
       `filed` and `amendment` to pick a vintage.

A FIFTH TRAP, IN CONFIGURATION RATHER THAN PARSING
    Firms file under whole entity trees, and the entity whose NAME looks most
    like the firm is often not the one holding the portfolio. Resolving CIKs by
    name alone produced: Apple -> APPLETON PARTNERS INC/MA, Millennium ->
    WORLDQUANT MILLENNIUM ADVISORS, Geode -> the Trust Company (13F-NT only),
    Man Group -> Neuberger BerMAN GROUP. Every CIK in config/filers_13f.json is
    therefore verified against the filing index by tools/resolve_13f_filers.py.
    Use that tool when adding a firm; do not hand-enter a CIK.

SCHEDULING: POLL, DO NOT CRON QUARTERLY
    Filing dates scatter across the 45-day window -- observed for one quarter:
    Apple 07-22, Geode 08-08, most 08-12..08-14, Wellington 08-18, Man Group
    09-24. A quarterly cron either fires early and misses filers or late and
    delays the rest. ingest() instead polls SEC's own filing index and picks up
    anything not already in the warehouse, which is self-healing and catches
    amendments for free.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from lxml import etree

from core.http import cached_sec_session
from core.config import require
from core.storage import db, register_views, save_raw, write_table
from connectors.sec_edgar import filing_index

CONFIG = Path(__file__).resolve().parent.parent / "config" / "filers_13f.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def _s():
    return cached_sec_session(require("SEC_CONTACT_EMAIL"))


def watchlist() -> pd.DataFrame:
    """The tracked filers, from the committed config."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    df = pd.DataFrame(cfg["filers"])
    df["cik_int"] = df["cik"].astype(int)
    return df


# ------------------------------------------------------------------ discovery
def discover_filings(year: int, quarter: int,
                     ciks: set[int] | None = None) -> pd.DataFrame:
    """
    Every 13F filing in one quarter's index, restricted to the watchlist.

    Uses SEC's own market-wide quarterly index rather than polling each filer,
    so a new filing is found the moment it is indexed, whoever filed it.
    """
    idx = filing_index(year, quarter)
    f13 = idx[idx["form"].str.startswith("13F")].copy()
    if ciks is None:
        ciks = set(watchlist()["cik_int"])
    f13 = f13[f13["cik"].isin(ciks)].copy()

    f13["holdings_report"] = f13["form"].str.startswith("13F-HR")
    f13["amendment"] = f13["form"].str.endswith("/A")
    f13["index_year"] = year
    f13["index_quarter"] = quarter
    return f13.sort_values(["cik", "filed"], ignore_index=True)


def _information_table_url(cik: int, accn: str) -> str | None:
    """
    Locate the information-table XML inside an accession.

    Its filename is arbitrary (Berkshire's is "56757.xml"), so the accession is
    listed and each candidate XML probed -- primary_doc.xml is the cover page,
    never the holdings.
    """
    folder = accn.replace("-", "")
    listing = json.loads(_s().get(f"{ARCHIVES}/{int(cik)}/{folder}/index.json").content)
    names = [i["name"] for i in listing["directory"]["item"]
             if i["name"].lower().endswith(".xml")]
    candidates = [n for n in names if n.lower() != "primary_doc.xml"] + \
                 [n for n in names if n.lower() == "primary_doc.xml"]
    for name in candidates:
        url = f"{ARCHIVES}/{int(cik)}/{folder}/{name}"
        try:
            head = _s().get(url).content
        except Exception:
            continue
        if b"informationTable" in head or b"infoTable" in head:
            return url
    return None


def _infer_value_scale(values: pd.Series, shares: pd.Series) -> tuple[float, str]:
    """
    Decide whether `value` is dollars or thousands, from the data itself.

    A US equity trading below $1 is possible; a whole PORTFOLIO whose median
    implied price is below $1 is not -- that means values are in thousands.
    Inferring beats hardcoding the rule-change date, and it is auditable.
    """
    ok = (shares > 0) & values.notna()
    if not ok.any():
        return 1.0, "unknown"
    implied = (values[ok] / shares[ok]).median()
    if implied < 1.0:
        return 1000.0, "thousands"
    return 1.0, "dollars"


def holdings(cik: int, accn: str, report_period: str | None = None,
             filed: str | None = None) -> pd.DataFrame:
    """
    Parse one 13F-HR information table into tidy holding rows.

    Rows are returned AS FILED, including duplicate securities reported by
    different internal managers -- see trap 3 in the module docstring.
    """
    url = _information_table_url(cik, accn)
    if url is None:
        raise RuntimeError(f"No information table found in accession {accn} (CIK {cik})")

    r = _s().get(url)
    save_raw("sec", "13f_information_table", r.content, "xml",
             {"cik": int(cik), "accn": accn, "url": url})

    root = etree.fromstring(r.content)

    # Filers split between a DEFAULT namespace (<infoTable>) and a PREFIXED one
    # (<ns1:infoTable>) for the identical schema. Keying off nsmap[None] finds
    # nothing for the prefixed form and yields a silent zero-holding parse --
    # which is how Viking, Tiger Global and Millennium first came back empty.
    # The {*} wildcard matches any namespace, or none.
    def tag(t: str) -> str:
        return "{*}" + t

    def txt(el, name):
        if el is None:
            return None
        node = el.find(tag(name))
        return node.text.strip() if node is not None and node.text else None

    rows = []
    for t in root.findall(f".//{tag('infoTable')}"):
        sh = t.find(tag("shrsOrPrnAmt"))
        va = t.find(tag("votingAuthority"))
        rows.append({
            "issuer": txt(t, "nameOfIssuer"),
            "title_of_class": txt(t, "titleOfClass"),
            "cusip": txt(t, "cusip"),
            "value_reported": pd.to_numeric(txt(t, "value"), errors="coerce"),
            "shares_or_principal": pd.to_numeric(txt(sh, "sshPrnamt"), errors="coerce"),
            "share_type": txt(sh, "sshPrnamtType"),
            "put_call": txt(t, "putCall"),
            "investment_discretion": txt(t, "investmentDiscretion"),
            "other_manager": txt(t, "otherManager"),
            "voting_sole": pd.to_numeric(txt(va, "Sole"), errors="coerce"),
            "voting_shared": pd.to_numeric(txt(va, "Shared"), errors="coerce"),
            "voting_none": pd.to_numeric(txt(va, "None"), errors="coerce"),
        })

    if not rows:
        raise RuntimeError(
            f"13F information table {accn} (CIK {cik}) parsed 0 holdings. "
            "If this is a 13F-NT it carries no holdings by design -- NTs must "
            "not be routed here."
        )

    df = pd.DataFrame(rows)
    factor, scale = _infer_value_scale(df["value_reported"], df["shares_or_principal"])
    df["value_usd"] = df["value_reported"] * factor
    df["value_scale"] = scale
    df["cik"] = int(cik)
    df["accn"] = accn
    df["report_period"] = report_period
    df["filed"] = filed
    df["source_url"] = url
    return df


# ------------------------------------------------------------------ ingestion
def _ingested_accessions() -> set[str]:
    """Accessions already in the warehouse, so polling is idempotent."""
    con = db()
    try:
        register_views(con)
        try:
            rows = con.execute("SELECT DISTINCT accn FROM sec_13f_holdings").fetchall()
        except Exception:
            return set()                     # dataset does not exist yet
        return {r[0] for r in rows}
    finally:
        con.close()


def ingest(year: int, quarter: int, limit: int | None = None,
           persist: bool = True) -> dict:
    """
    Poll one quarter's index and ingest any watchlist 13F not already stored.

    Returns a summary dict. Failures on individual filings are collected rather
    than raised, so one malformed accession cannot block the other 28 managers;
    the summary reports them and the job surfaces them.
    """
    wl = watchlist()
    names = dict(zip(wl["cik_int"], wl["name"]))
    kinds = dict(zip(wl["cik_int"], wl["type"]))

    found = discover_filings(year, quarter, set(wl["cik_int"]))
    done = _ingested_accessions()

    hr = found[found["holdings_report"]].copy()
    nt = found[~found["holdings_report"]].copy()

    todo = hr[~hr["accn"].isin(done)]
    if limit:
        todo = todo.head(limit)

    frames, failures = [], []
    for _, row in todo.iterrows():
        try:
            df = holdings(int(row["cik"]), row["accn"], filed=row["filed"])
        except Exception as e:                       # noqa -- collect, keep going
            failures.append({"cik": int(row["cik"]), "accn": row["accn"],
                             "error": f"{type(e).__name__}: {str(e)[:120]}"})
            print(f"    FAIL {names.get(int(row['cik']), row['cik'])}: {str(e)[:90]}")
            continue
        df["filer_name"] = names.get(int(row["cik"]))
        df["filer_type"] = kinds.get(int(row["cik"]))
        df["form"] = row["form"]
        df["amendment"] = bool(row["amendment"])
        frames.append(df)
        print(f"    {len(df):>5} holdings  {names.get(int(row['cik']))} "
              f"({row['form']}, filed {row['filed']})")

    summary = {
        "quarter": f"{year}Q{quarter}",
        "filings_found": len(found),
        "holdings_reports": len(hr),
        "notices_13f_nt": len(nt),
        "already_ingested": int(hr["accn"].isin(done).sum()),
        "ingested_now": len(frames),
        "failures": failures,
        "rows": 0,
    }

    if frames:
        all_rows = pd.concat(frames, ignore_index=True)
        summary["rows"] = len(all_rows)
        summary["value_scales"] = all_rows["value_scale"].value_counts().to_dict()
        if persist:
            write_table(all_rows, "sec", "13f_holdings")

    # Record notices too: an NT explains why a large filer shows no holdings.
    if len(nt) and persist:
        nt = nt.copy()
        nt["filer_name"] = nt["cik"].map(names)
        write_table(nt[["cik", "filer_name", "form", "filed", "accn"]],
                    "sec", "13f_notices")

    return summary


def latest_reported_quarter(today=None) -> tuple[int, int]:
    """
    Quarter whose 13Fs should now be on file: one quarter back, since managers
    have 45 days after quarter end and stragglers run later still.
    """
    from datetime import date
    d = today or date.today()
    q = (d.month - 1) // 3 + 1
    return (d.year, q - 1) if q > 1 else (d.year - 1, 4)


if __name__ == "__main__":
    y, q = latest_reported_quarter()
    print(f"ingesting 13F for {y}Q{q}")
    print(json.dumps(ingest(y, q, limit=5, persist=False), indent=2, default=str))
