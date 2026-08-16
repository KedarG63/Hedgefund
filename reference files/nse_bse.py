"""
NSE + BSE: corporate filings, results, flows, derivatives positioning.

Neither exchange documents these endpoints, but they are the same ones the
public websites call, so they are as real as it gets. They DO change without
notice -- hence the schema-check in every function.

LEGAL NOTE: exchange terms restrict bulk extraction and REDISTRIBUTION.
Internal research use is standard practice; publishing or reselling this data
needs a licence from NSE/BSE. As a fund, get that clarified before anything
touches a client-facing document.
"""
import io
import zipfile
from datetime import date, datetime

import pandas as pd
from core.http import nse_session, bse_session
from core.storage import save_raw, write_table

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
    return pd.DataFrame(r.json())


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
    return write_table(df, "nse", f"results_{period}") and df


def nse_fii_dii() -> pd.DataFrame:
    """Daily FII and DII cash-market buy/sell. Published after market close."""
    f = nse_session()
    r = f.get(f"{NSE_API}/fiidiiTradeReact")
    save_raw("nse", "fii_dii", r.content, "json")
    return pd.DataFrame(r.json())


def nse_option_chain(symbol="NIFTY", index=True) -> dict:
    """
    Live option chain with OI, change in OI, IV, per strike.
    Near-real-time (refreshes ~every 3 min on NSE's side).
    """
    f = nse_session()
    ep = "option-chain-indices" if index else "option-chain-equities"
    r = f.get(f"{NSE_API}/{ep}", params={"symbol": symbol})
    save_raw("nse", f"optchain_{symbol}", r.content, "json")
    return r.json()


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
    df["trade_date"] = d
    return write_table(df, "nse", "participant_oi") and df


def nse_bhavcopy(d: date) -> pd.DataFrame:
    """
    Official EOD prices for every listed security, including DELIVERY QUANTITY
    (speculative vs genuine volume -- an India-specific edge).
    Archive format has changed over the years; keep both patterns.
    """
    f = nse_session()
    ds = d.strftime("%Y%m%d")
    url = f"{NSE_ARCHIVE}/content/cm/BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    r = f.get(url)
    save_raw("nse", "bhavcopy", r.content, "zip", {"date": d.isoformat()})
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name))
    return write_table(df, "nse", "bhavcopy") and df


# ---------------------------------------------------------------- BSE

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"


def bse_announcements(frm: date, to: date, scrip: str = "") -> pd.DataFrame:
    """
    BSE's announcement API is friendlier than NSE's. Covers ~5,000 listed
    companies vs NSE's ~2,000 -- for smallcap coverage, BSE is the better feed.
    """
    f = bse_session()
    params = {
        "strCat": "-1", "strPrevDate": frm.strftime("%Y%m%d"),
        "strScrip": scrip, "strSearch": "P",
        "strToDate": to.strftime("%Y%m%d"), "strType": "C",
        "pageno": "1",
    }
    r = f.get(f"{BSE_API}/AnnGetData/w", params=params)
    save_raw("bse", "announcements", r.content, "json")
    payload = r.json()
    return pd.DataFrame(payload.get("Table", []))


def bse_scrip_master() -> pd.DataFrame:
    """BSE scrip code <-> ISIN <-> name. Half of your India security master."""
    f = bse_session()
    r = f.get(f"{BSE_API}/ListofScripData/w", params={"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"})
    save_raw("bse", "scrip_master", r.content, "json")
    return pd.DataFrame(r.json())
