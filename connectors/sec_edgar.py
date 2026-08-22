"""
SEC EDGAR -- the easiest primary source on earth. Free, documented, no key.
Works fine from India. Only requirement: a User-Agent with your email.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
import json
import pandas as pd
from core.config import require
from core.http import cached_sec_session
from core.storage import find_raw, save_raw, write_table

# SEC requires a User-Agent naming a real contact. Read it from the environment
# rather than hardcoding: require() raises with a useful message if it is unset
# or still a placeholder, which is far easier to debug than a bare 403.
def _email() -> str:
    return require("SEC_CONTACT_EMAIL")


def _s():
    return cached_sec_session(_email())


def ticker_map() -> pd.DataFrame:
    """Every SEC-registered ticker -> CIK. Your US security master starting point."""
    r = _s().get("https://www.sec.gov/files/company_tickers.json")
    save_raw("sec", "ticker_map", r.content, "json")
    df = pd.DataFrame(json.loads(r.content).values())
    df["cik_padded"] = df["cik_str"].astype(str).str.zfill(10)
    write_table(df, "sec", "ticker_map")
    return df


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


def filing_index(year: int, quarter: int) -> pd.DataFrame:
    """
    Every filing made in one quarter, market-wide: CIK, form, DATE FILED and
    accession number.

    Why it matters: the frames() endpoint returns an `accn` but NOT a filed
    date, so a frames panel on its own is not point-in-time -- you would know
    what a company reported for Q2 but not when the market learned it. This
    index supplies the missing date for a whole quarter in one request, instead
    of one submissions() call per company.
    """
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"

    # A completed quarter's index never changes and is ~55 MB. If we already
    # archived it, parse the archive -- which is what rule 1 asks for anyway.
    cached = find_raw("sec", "filing_index", year=year, quarter=quarter)
    if cached is not None:
        text = cached.read_text(encoding="utf-8", errors="replace")
    else:
        r = _s().get(url)
        save_raw("sec", "filing_index", r.content, "idx",
                 {"year": year, "quarter": quarter, "url": url})
        text = r.text

    rows = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue                       # header/preamble lines
        cik, name, form, filed, path = (p.strip() for p in parts)
        accn = path.rsplit("/", 1)[-1].replace(".txt", "")
        rows.append({"cik": int(cik), "entity_idx": name, "form": form,
                     "filed": filed, "accn": accn})
    if not rows:
        raise RuntimeError(f"filing_index parsed 0 rows from {url} -- format changed?")
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- universe
# SPDR S&P 500 ETF Trust. Its quarterly NPORT-P filing lists every constituent.
# This is the primary-source route to the index membership: S&P's constituent
# list is itself proprietary and is not published by any regulator, but the ETF
# that tracks it must disclose its holdings to the SEC.
SP500_ETF_CIK = "0000884394"


def latest_nport(cik: str = SP500_ETF_CIK) -> tuple[str, str]:
    """Accession number and filing date of the most recent NPORT-P."""
    subs = submissions(cik)
    nport = subs[subs["form"].astype(str).str.startswith("NPORT-P")]
    if nport.empty:
        raise RuntimeError(f"No NPORT-P filing found for CIK {cik}")
    row = nport.iloc[0]
    return str(row["accessionNumber"]), str(row["filingDate"])


def etf_holdings(cik: str = SP500_ETF_CIK, accn: str | None = None) -> pd.DataFrame:
    """
    Parse an ETF's N-PORT holdings into a frame: name, LEI, CUSIP, ISIN, market
    value and portfolio weight.

    N-PORT identifies holdings by CUSIP/LEI/ISIN and never by CIK or ticker --
    CUSIP is proprietary to CGS, so no regulator publishes a CUSIP->CIK map.
    sp500_constituents() bridges to CIK by normalised company name.
    """
    from lxml import etree

    if accn is None:
        accn, filed = latest_nport(cik)
    else:
        filed = None

    folder = accn.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/primary_doc.xml")
    r = _s().get(url)
    save_raw("sec", "nport", r.content, "xml", {"cik": cik, "accn": accn, "url": url})

    root = etree.fromstring(r.content)

    # Match any namespace or none: filers vary between a default namespace and
    # a prefixed one for the same schema, and keying off nsmap[None] silently
    # parses zero rows for the prefixed form.
    def tag(t: str) -> str:
        return "{*}" + t

    def child(el, name):
        found = el.find(tag(name))
        return found.text if found is not None else None

    rows = []
    for h in root.findall(f".//{tag('invstOrSec')}"):
        ident = h.find(tag("identifiers"))
        isin = None
        if ident is not None:
            node = ident.find(tag("isin"))
            if node is not None:
                isin = node.get("value")
        rows.append({
            "name": child(h, "name"),
            "lei": child(h, "lei"),
            "cusip": child(h, "cusip"),
            "isin": isin,
            "value_usd": pd.to_numeric(child(h, "valUSD"), errors="coerce"),
            "pct_of_fund": pd.to_numeric(child(h, "pctVal"), errors="coerce"),
            "asset_category": child(h, "assetCat"),
        })
    if not rows:
        raise RuntimeError(f"N-PORT {accn} parsed 0 holdings -- schema changed?")

    df = pd.DataFrame(rows)
    df["accn"] = accn
    df["nport_filed"] = filed
    return df


def _normalise_name(s: pd.Series) -> pd.Series:
    """
    Squash company-name variants so N-PORT and EDGAR titles line up.

    The differences that actually bite, found by inspecting failed matches:
      "Air Products and Chemicals" vs EDGAR "AIR PRODUCTS & CHEMICALS"
      "Bank of America Corp"       vs EDGAR "BANK OF AMERICA CORP /DE/"
      "Clorox Co/The"              vs EDGAR "CLOROX CO /DE/"
    So: fold & into AND, strip EDGAR's /XX/ state-of-incorporation suffix and
    N-PORT's "/The" style tail, then drop legal-form words and connectives.
    """
    out = s.astype(str).str.upper()
    out = out.str.replace("&", " AND ", regex=False)
    # Order matters: strip "/THE" BEFORE the two-letter state pattern, or
    # "/DE/"-style matching consumes "/TH" and leaves a stray "E".
    out = out.str.replace(r"[/\\]THE\b", " ", regex=True)        # N-PORT "Clorox Co/The"
    out = out.str.replace(r"[/\\][A-Z]{2,3}\b[/\\]?", " ", regex=True)  # "/DE/", "\DE\", "/NEW"
    # ".com" first: EDGAR writes "AMAZON COM INC" (COM as its own word, dropped
    # below), so gluing it into "AMAZONCOM" would strand it.
    out = out.str.replace(r"\.COM\b", " ", regex=True)
    # DELETE apostrophes and dots rather than blanking them: "Lowe's" must
    # become LOWES to match EDGAR's "LOWES", not "LOWE S"; likewise "N.V." -> NV.
    out = out.str.replace(r"['.’]", "", regex=True)
    out = out.str.replace(r"[^A-Z0-9 ]", " ", regex=True)
    # COS is the plural of CO ("Lowe's Cos Inc", "TJX Cos Inc") -- strip it too.
    out = out.str.replace(
        r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|COMPANIES|COS|CO|LIMITED|LTD|"
        r"PLC|LLC|LP|HOLDINGS|HOLDING|GROUP|THE|AND|OF|CLASS [A-C]|COM|NEW|SA|NV)\b",
        " ", regex=True)
    return out.str.replace(r"\s+", " ", regex=True).str.strip()


def sp500_constituents(persist: bool = True) -> pd.DataFrame:
    """
    S&P 500 membership with CIKs, from the SPDR ETF's own N-PORT filing.

    Why this route: index membership is S&P Dow Jones IP and no regulator
    publishes it, but the ETF tracking the index must disclose holdings. That
    makes this a genuine primary source rather than a scraped list, and it
    arrives with portfolio weights attached -- useful on its own for
    index-flow work.

    Expect ~503 holdings for ~500 companies: dual share classes (GOOG/GOOGL,
    FOX/FOXA) appear twice. Rows that fail to match a CIK are KEPT with a null
    cik rather than dropped, so coverage gaps stay visible.
    """
    holdings = etf_holdings()
    equities = holdings[holdings["asset_category"] == "EC"].copy()

    tickers = ticker_map()
    tickers = tickers.sort_values("cik_str")

    def keys(names: pd.Series) -> pd.DataFrame:
        norm = _normalise_name(names)
        return pd.DataFrame({
            "k_exact": norm,
            # EDGAR files surnames first -- "BERKLEY W R" vs "W R Berkley",
            # "HORTON D R" vs "DR Horton". Sorting the tokens makes those equal.
            "k_sorted": norm.map(lambda s: " ".join(sorted(s.split()))),
            # "ExxonMobil" vs "Exxon Mobil"
            "k_nospace": norm.str.replace(" ", "", regex=False),
        })

    # Reset BEFORE deriving keys: keys() inherits the caller's index, and
    # concatenating a gapped index against a reset one silently scrambles rows
    # against their keys -- which mismatches companies rather than failing.
    tickers = tickers.reset_index(drop=True)
    equities = equities.reset_index(drop=True)
    tk = pd.concat([tickers, keys(tickers["title"])], axis=1)
    eq = pd.concat([equities, keys(equities["name"])], axis=1)

    # Try keys strictly-to-loosely; record which one hit so matches stay auditable.
    out = eq
    out["cik"] = pd.NA
    out["ticker"] = pd.NA
    out["edgar_title"] = pd.NA
    out["match_method"] = pd.NA

    for key in ("k_exact", "k_sorted", "k_nospace"):
        lookup = (tk.drop_duplicates(key)[[key, "cik_padded", "ticker", "title"]]
                    .rename(columns={"cik_padded": "_cik", "ticker": "_tkr", "title": "_title"}))
        merged = out.merge(lookup, on=key, how="left")
        fill = out["cik"].isna() & merged["_cik"].notna()
        out.loc[fill, "cik"] = merged.loc[fill, "_cik"]
        out.loc[fill, "ticker"] = merged.loc[fill, "_tkr"]
        out.loc[fill, "edgar_title"] = merged.loc[fill, "_title"]
        out.loc[fill, "match_method"] = key

    out = out.drop(columns=["k_exact", "k_sorted", "k_nospace"])

    if persist:
        write_table(out, "sec", "sp500_constituents")
    return out


# ------------------------------------------------------------------ fundamentals
# One logical field maps to several XBRL concepts: filers choose different tags
# for the same economic quantity, and no single concept covers everyone. Probed
# for CY2025Q2: Revenues alone reaches 1,919 filers, while
# RevenueFromContractWithCustomerExcludingAssessedTax reaches 2,546 -- different
# companies. Aliases are tried in order and the first hit per company wins.
#
# "period" is the SEC's frame notation: CY2025Q2 for flows measured over the
# quarter, CY2025Q2I for stocks measured at the instant it ends.
FUNDAMENTAL_FIELDS: dict[str, dict] = {
    "revenue": {
        "instant": False, "unit": "USD",
        "concepts": [("us-gaap", "Revenues"),
                     ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                     ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax")],
    },
    "net_income": {
        "instant": False, "unit": "USD",
        "concepts": [("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss")],
    },
    "assets": {
        "instant": True, "unit": "USD",
        "concepts": [("us-gaap", "Assets")],
    },
    "equity": {
        "instant": True, "unit": "USD",
        "concepts": [("us-gaap", "StockholdersEquity"),
                     ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")],
    },
    "shares_outstanding": {
        "instant": True, "unit": "shares",
        "concepts": [("dei", "EntityCommonStockSharesOutstanding"),
                     ("us-gaap", "CommonStockSharesOutstanding"),
                     ("us-gaap", "CommonStockSharesIssued")],
    },
}


def latest_completed_quarter(today=None) -> str:
    """
    Most recent calendar quarter whose figures are likely filed, in SEC frame
    notation ("CY2025Q2").

    Lags by two quarters deliberately: 10-Qs land 40-45 days after quarter end,
    and frames() only publishes a period once enough filers have reported. Ask
    too early and the panel is thin rather than wrong -- which is worse, because
    thinness is easy to mistake for a market with few filers.
    """
    from datetime import date
    d = today or date.today()
    q = (d.month - 1) // 3 + 1
    year, q = (d.year, q - 2) if q > 2 else (d.year - 1, q + 2)
    return f"CY{year}Q{q}"


def filing_dates(ciks) -> pd.DataFrame:
    """
    Map accession number -> filing date for a set of companies.

    Why per-company rather than the quarterly full-index: frames() reports the
    MOST RECENT filing that stated a figure, so a CY2025Q2 value is often
    carried by a 2026 filing showing prior-year comparatives (accession
    0001628280-26-050134 for a 2025 period). Covering that with quarterly index
    files means downloading ~55 MB per quarter across several years; each
    company's submissions feed answers exactly, in one small request each.
    """
    rows = []
    for cik in ciks:
        try:
            subs = submissions(cik)
        except Exception as e:                          # noqa -- keep going
            print(f"    submissions({cik}) failed: {type(e).__name__}")
            continue
        if "accessionNumber" not in subs.columns:
            continue
        rows.append(subs[["accessionNumber", "filingDate", "form"]]
                    .rename(columns={"accessionNumber": "accn", "filingDate": "filed"}))
    if not rows:
        raise RuntimeError("filing_dates resolved no filings at all")
    return pd.concat(rows, ignore_index=True).drop_duplicates("accn")


def fundamentals_panel(period: str, universe: pd.DataFrame | None = None,
                       fields: dict | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Cross-sectional fundamentals panel, point-in-time.

    period  e.g. "CY2025Q2" -- the "I" suffix for instantaneous concepts is
            added automatically per field, so pass the duration form.
    universe  frame with a `cik` column (default: S&P 500 via sp500_constituents).
              Pass None-equivalent empty to panel every US filer.

    Point-in-time is the whole point: frames() gives a value and an accession
    number but NO filed date, so on its own it silently mixes in figures that
    were not public at the time. Every row here carries `filed`, joined from the
    quarterly filing index, plus `filed_source` so you can see which rows have a
    real date and which could not be matched. Rows are never dropped for want of
    a date -- an invisible gap is worse than a visible null.
    """
    fields = fields or FUNDAMENTAL_FIELDS

    frames_out = []
    for field, spec in fields.items():
        want = period + ("I" if spec["instant"] else "")
        seen: set[int] = set()
        for taxonomy, concept in spec["concepts"]:
            try:
                df = frames(concept, want, taxonomy=taxonomy, unit=spec["unit"])
            except Exception as e:                      # noqa -- concept may not exist
                print(f"    skip {taxonomy}:{concept} {want}: {type(e).__name__}")
                continue
            if df.empty:
                continue
            df = df[~df["cik"].isin(seen)].copy()       # first alias wins per company
            seen.update(df["cik"].tolist())
            df["field"] = field
            df["concept"] = concept
            df["taxonomy"] = taxonomy
            df["period"] = want
            frames_out.append(df)

    if not frames_out:
        raise RuntimeError(f"fundamentals_panel({period}) retrieved no frames at all.")

    panel = pd.concat(frames_out, ignore_index=True)
    panel = panel.rename(columns={"val": "value", "entityName": "entity"})

    # restrict to the universe
    if universe is None:
        universe = sp500_constituents(persist=False)
    if universe is not None and not universe.empty and "cik" in universe.columns:
        keep = set(pd.to_numeric(universe["cik"], errors="coerce").dropna().astype(int))
        panel = panel[panel["cik"].isin(keep)].copy()

    # attach the filed date -- what makes this genuinely point-in-time
    panel = panel.merge(filing_dates(sorted(panel["cik"].unique())),
                        on="accn", how="left")
    panel["filed_source"] = panel["filed"].notna().map(
        {True: "submissions", False: "unmatched"})

    cols = ["cik", "entity", "field", "value", "period", "end", "filed",
            "filed_source", "form", "concept", "taxonomy", "accn"]
    panel = panel[[c for c in cols if c in panel.columns]].sort_values(
        ["cik", "field"], ignore_index=True)

    if persist:
        write_table(panel, "sec", "us_fundamentals")
    return panel


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
