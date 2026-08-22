"""
NSE + BSE: corporate filings, results, flows, derivatives positioning.

Neither exchange documents these endpoints, but they are the same ones the
public websites call, so they are as real as it gets. They DO change without
notice -- hence _check_schema() below and the endpoint notes in each
docstring. Every endpoint in this file was hit live and verified on
2026-08-22; see PHASE4.md for the discovery trail (which JS bundle, which
line) so a future break can be re-diagnosed fast instead of re-discovered
from scratch.

LEGAL NOTE: exchange terms restrict bulk extraction and REDISTRIBUTION.
Internal research use is standard practice; publishing or reselling this data
needs a licence from NSE/BSE. As a fund, get that clarified before anything
touches a client-facing document.
"""
import io
import zipfile
from datetime import date

import pandas as pd
from core.http import nse_session, bse_session
from core.storage import save_raw, write_table


def _check_schema(df: pd.DataFrame, expected: set, source: str, dataset: str) -> None:
    """
    Raise if a column this parser depends on has silently disappeared.

    New columns are not an error -- NSE/BSE add fields often and that is
    harmless. A MISSING expected column means the schema changed underneath
    us; every downstream user of that column is now silently wrong, which is
    worse than a loud failure here. This is what the connector contract's
    "raise on failure, never return empty" rule means for a file whose
    columns just moved instead of a request that just failed.
    """
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{source}.{dataset}: schema drift -- expected column(s) "
            f"{sorted(missing)} not found. Got: {sorted(df.columns)}. "
            f"The site's schema changed; update the parser before trusting this data."
        )


# ---------------------------------------------------------------- NSE

NSE_API = "https://www.nseindia.com/api"
NSE_ARCHIVE = "https://nsearchives.nseindia.com"


def nse_corporate_announcements(index="equities", frm=None, to=None) -> pd.DataFrame:
    """Real-time corporate announcement feed. Poll every 60s during market hours."""
    f = nse_session()
    params = {"index": index}
    if frm and to:
        params |= {"from_date": frm, "to_date": to}
    r = f.get(f"{NSE_API}/corporate-announcements", params=params)
    save_raw("nse", "announcements", r.content, "json")
    df = pd.DataFrame(r.json())
    if not df.empty:
        write_table(df, "nse", "announcements")
    return df


def nse_financial_results(period="Quarterly", index="equities") -> pd.DataFrame:
    """
    Quarterly/annual results as filed. Each row carries an XBRL link and a PDF
    link -- grab the XBRL, it is machine-readable and needs no OCR.
    """
    f = nse_session()
    r = f.get(f"{NSE_API}/corporates-financial-results",
              params={"index": index, "period": period})
    save_raw("nse", f"results_{period}", r.content, "json")
    df = pd.DataFrame(r.json())
    write_table(df, "nse", f"results_{period}")
    return df


def nse_fii_dii() -> pd.DataFrame:
    """
    Daily FII and DII cash-market buy/sell. Published after market close.

    NSE serves this endpoint brotli-compressed (content-encoding: br). httpx
    only auto-decodes that if the `brotli` package is installed -- it is in
    requirements.txt, but if this raises a UnicodeDecodeError, that package is
    missing from the current environment: `pip install brotli`.
    """
    f = nse_session()
    r = f.get(f"{NSE_API}/fiidiiTradeReact")
    save_raw("nse", "fii_dii", r.content, "json")
    return pd.DataFrame(r.json())


def _nifty_expiry_dates(symbol: str) -> list:
    """
    First step of the option-chain flow: NSE's option-chain-v3 endpoint
    requires an explicit `expiry` param (as of 2026-08-22 -- it used to accept
    a bare symbol and pick the near month itself, and silently returns `{}`
    with the old call shape). This is the only place that gets the valid
    expiry list.
    """
    f = nse_session()
    r = f.get(f"{NSE_API}/option-chain-contract-info", params={"symbol": symbol})
    return r.json()["expiryDates"]


def nse_option_chain(symbol="NIFTY", index=True, expiry: str | None = None) -> dict:
    """
    Live option chain with OI, change in OI, IV, per strike.
    Near-real-time (refreshes ~every 3 min on NSE's side).

    Two-step call, discovered from option-chain-v3.js on 2026-08-22:
    `option-chain-indices` (the old single-call endpoint) now 404s outright.
    Its replacement, `option-chain-v3`, requires `type` (Indices/Equity) AND
    an explicit `expiry` date string ("25-Aug-2026" style) -- omit expiry and
    it returns `{}` rather than an error, which looks like "no data" instead
    of "wrong call shape". Default here is the nearest expiry.
    """
    f = nse_session()
    if expiry is None:
        expiry = _nifty_expiry_dates(symbol)[0]
    params = {"type": "Indices" if index else "Equity", "symbol": symbol, "expiry": expiry}
    r = f.get(f"{NSE_API}/option-chain-v3", params=params)
    save_raw("nse", f"optchain_{symbol}", r.content, "json", {"expiry": expiry})
    d = r.json()
    if not d.get("records", {}).get("data"):
        raise RuntimeError(f"option_chain({symbol}, expiry={expiry}) returned no rows -- "
                            f"bad expiry, market holiday, or the endpoint moved again.")
    return d


def nse_participant_oi(d: date) -> pd.DataFrame:
    """
    ***THE UNDERRATED ONE***
    Daily FII / DII / proprietary / retail long-short open interest across index
    futures, index options, stock futures, stock options.

    No other major market gives you institutional positioning for free, daily.
    Treat this as a crowding factor, not a directional signal.
    """
    f = nse_session()
    ds = d.strftime("%d%m%Y")
    r = f.get(f"{NSE_ARCHIVE}/content/nsccl/fao_participant_oi_{ds}.csv")
    save_raw("nse", "participant_oi", r.content, "csv", {"date": d.isoformat()})
    df = pd.read_csv(io.BytesIO(r.content), skiprows=1)
    df.columns = df.columns.str.strip()  # source ships trailing spaces, e.g. "Total Long Contracts      "
    _check_schema(df, {"Client Type", "Total Long Contracts", "Total Short Contracts"},
                  "nse", "participant_oi")
    df["trade_date"] = d
    write_table(df, "nse", "participant_oi")
    return df


def nse_bhavcopy(d: date) -> pd.DataFrame:
    """
    Official EOD prices for every listed security and instrument type (CM, F&O
    -- everything in one file). Archive format has changed over the years;
    keep both patterns.

    Does NOT carry delivery quantity/percentage -- this is the UDiFF format,
    which dropped those columns. Use nse_bhavcopy_delivery() for that; it is a
    separate NSE file, equities-only.
    """
    f = nse_session()
    ds = d.strftime("%Y%m%d")
    url = f"{NSE_ARCHIVE}/content/cm/BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    r = f.get(url)
    save_raw("nse", "bhavcopy", r.content, "zip", {"date": d.isoformat()})
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name))
    _check_schema(df, {"TradDt", "ISIN", "TckrSymb", "ClsPric", "TtlTradgVol"},
                  "nse", "bhavcopy")
    write_table(df, "nse", "bhavcopy")
    return df


def nse_bhavcopy_delivery(d: date) -> pd.DataFrame:
    """
    Equities-only EOD file that DOES carry DELIV_QTY / DELIV_PER -- the
    speculative-vs-genuine-volume split that's an India-specific edge
    (BUILD_GUIDE.md #5). A high delivery percentage on a volume spike reads
    very differently from a low one on the same spike.

    Separate file from nse_bhavcopy() -- NSE split delivery data out of the
    main UDiFF bhavcopy some years back and never merged it back in.
    """
    f = nse_session()
    ds = d.strftime("%d%m%Y")
    url = f"{NSE_ARCHIVE}/products/content/sec_bhavdata_full_{ds}.csv"
    r = f.get(url)
    save_raw("nse", "bhavcopy_delivery", r.content, "csv", {"date": d.isoformat()})
    df = pd.read_csv(io.BytesIO(r.content))
    df.columns = df.columns.str.strip()  # source file ships " SERIES", " DELIV_PER" etc.
    _check_schema(df, {"SYMBOL", "SERIES", "DELIV_QTY", "DELIV_PER"},
                  "nse", "bhavcopy_delivery")
    write_table(df, "nse", "bhavcopy_delivery")
    return df


def nse_bulk_deals(frm: date, to: date) -> pd.DataFrame:
    """
    Trades >0.5% of a company's equity in a single day, single broker. Same
    endpoint backs both bulk and block deals (`optionType` switches it) --
    found in bulk-block-deals-short-selling.js, since the page's default
    on-load fetch pulls from a static JSON snapshot and only calls this API
    once a date range is submitted.
    """
    f = nse_session()
    r = f.get(f"{NSE_API}/historicalOR/bulk-block-short-deals", params={
        "optionType": "bulk_deals",
        "from": frm.strftime("%d-%m-%Y"), "to": to.strftime("%d-%m-%Y"),
    })
    save_raw("nse", "bulk_deals", r.content, "json", {"from": frm.isoformat(), "to": to.isoformat()})
    df = pd.DataFrame(r.json().get("data", []))
    if not df.empty:
        write_table(df, "nse", "bulk_deals")
    return df


def nse_block_deals(frm: date, to: date) -> pd.DataFrame:
    """
    Trades >=₹25 Cr, negotiated off the order book with 100% delivery
    mandated -- the highest-conviction institutional print NSE publishes.
    Same endpoint as nse_bulk_deals(), `optionType=block_deals`.
    """
    f = nse_session()
    r = f.get(f"{NSE_API}/historicalOR/bulk-block-short-deals", params={
        "optionType": "block_deals",
        "from": frm.strftime("%d-%m-%Y"), "to": to.strftime("%d-%m-%Y"),
    })
    save_raw("nse", "block_deals", r.content, "json", {"from": frm.isoformat(), "to": to.isoformat()})
    df = pd.DataFrame(r.json().get("data", []))
    if not df.empty:
        write_table(df, "nse", "block_deals")
    return df


def nse_insider_trading(index="equities", frm: str | None = None, to: str | None = None) -> pd.DataFrame:
    """
    SEBI PIT (Prohibition of Insider Trading) disclosures: promoter/director/
    employee buys and sells, T+2. Endpoint name ("pit-gg") is NSE's own
    shorthand for the regulation, found in corporate-filings.js.

    frm/to, if given, are "DD-MM-YYYY" strings (NSE's own date format for
    this family of endpoints).
    """
    f = nse_session()
    params = {"index": index}
    if frm and to:
        params |= {"from_date": frm, "to_date": to}
    r = f.get(f"{NSE_API}/corporates-pit-gg", params=params)
    save_raw("nse", "insider_trading", r.content, "json")
    df = pd.DataFrame(r.json().get("data", []))
    if not df.empty:
        write_table(df, "nse", "insider_trading")
    return df


def nse_shareholding_pattern(index="equities", frm: str | None = None, to: str | None = None) -> pd.DataFrame:
    """
    Promoter/public shareholding split (`pr_and_prgrp` / `public_val`),
    disclosed quarterly under SEBI LODR Regulation 31. A rising promoter
    pledge or a falling `pr_and_prgrp` between quarters is the signal;
    this endpoint gives the raw disclosures, not a pledge-specific field --
    pledge detail lives in the linked filing, not this JSON.

    frm/to, if given, are "DD-MM-YYYY" strings.
    """
    f = nse_session()
    params = {"index": index}
    if frm and to:
        params |= {"from_date": frm, "to_date": to}
    r = f.get(f"{NSE_API}/corporate-share-holdings-master", params=params)
    save_raw("nse", "shareholding_pattern", r.content, "json")
    df = pd.DataFrame(r.json())
    if not df.empty:
        write_table(df, "nse", "shareholding_pattern")
    return df


# ---------------------------------------------------------------- BSE

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"

# Live-verified 2026-08-22 against AnnSubCategoryGetData. BSE's backend
# refuses strCat=-1 (all categories) once no scrip narrows the query -- it
# returns {} rather than an error, so a market-wide sweep has to name real
# categories. These four are individually confirmed to return rows; the true
# category list is longer (SEBI LODR defines more), but each addition needs
# its own live check against the endpoint, so this stays short on purpose.
BSE_ANNOUNCEMENT_CATEGORIES = ("Company Update", "Result", "Board Meeting", "AGM/EGM")


def bse_announcements(frm: date, to: date, scrip: str = "",
                       categories=BSE_ANNOUNCEMENT_CATEGORIES) -> pd.DataFrame:
    """
    BSE's announcement API is friendlier than NSE's. Covers ~5,000 listed
    companies vs NSE's ~2,000 -- for smallcap coverage, BSE is the better feed.

    Endpoint is AnnSubCategoryGetData, not AnnGetData -- the latter is a dead
    path on BSE's current site that answers every query with the literal JSON
    string "No Record Found!" (not an error status, just wrong data forever).

    Market-wide (scrip="") sweeps `categories` and concatenates, because
    strCat=-1 with no scrip returns {} rather than "everything" -- confirmed
    live, not a param-formatting bug. Pass `scrip` to query one company across
    all categories instead (strCat=-1 works there). Each page caps at 50 rows;
    only the first page is fetched per category.
    """
    f = bse_session()
    frames = []
    cats = ("-1",) if scrip else categories
    for cat in cats:
        params = {
            "strCat": cat, "strPrevDate": frm.strftime("%Y%m%d"),
            "strScrip": scrip, "strSearch": "P",
            "strToDate": to.strftime("%Y%m%d"), "strType": "C",
            "subcategory": "-1", "pageno": "1",
        }
        r = f.get(f"{BSE_API}/AnnSubCategoryGetData/w", params=params)
        save_raw("bse", "announcements", r.content, "json", {"category": cat, "scrip": scrip})
        payload = r.json()
        if isinstance(payload, dict) and payload.get("Table"):
            frames.append(pd.DataFrame(payload["Table"]))
    frames = [df for df in frames if not df.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    write_table(df, "bse", "announcements")
    return df


def bse_scrip_master() -> pd.DataFrame:
    """BSE scrip code <-> ISIN <-> name. Half of your India security master."""
    f = bse_session()
    r = f.get(f"{BSE_API}/ListofScripData/w", params={"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"})
    save_raw("bse", "scrip_master", r.content, "json")
    df = pd.DataFrame(r.json())
    _check_schema(df, {"SCRIP_CD", "Scrip_Name", "ISIN_NUMBER"}, "bse", "scrip_master")
    write_table(df, "bse", "scrip_master")
    return df
