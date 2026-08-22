"""
SEC Forms 3/4/5 -- insider transactions, via SEC's own bulk data sets.

WHY BULK RATHER THAN PER-FILING
    Insider filings are the fastest signal on EDGAR (Form 4 is due within 2
    business days of the trade, against 13F's 45-day lag) and also the most
    numerous: 90,116 per quarter market-wide. Fetching them individually costs
    ~3 hours at SEC's 10 req/sec ceiling.

    SEC's DERA group publishes the same data, already parsed, as one ~10 MB
    quarterly ZIP. One request replaces ninety thousand. Verified live.

WHAT THE SIGNAL ACTUALLY IS
    Only TRANS_CODE 'P' -- an open-market purchase with the insider's own money
    -- carries real information. Grants ('A'), option exercises ('M') and
    tax-withholding dispositions ('F') are compensation mechanics and say
    nothing about a view. Most naive "insider buying" screens fail precisely
    here, by counting grants as buys.

    Sales are weaker in the other direction: insiders sell for diversification,
    tax and liquidity reasons constantly. The `plan_10b5_1` flag matters most on
    the sell side -- a sale pre-scheduled under a 10b5-1 plan was decided months
    earlier and carries almost no information, while an unplanned sale by a CEO
    into strength does.

DATASET LAYOUT (DERA form345 ZIP)
    SUBMISSION.tsv       one row per filing: accession, dates, issuer, 10b5-1
    REPORTINGOWNER.tsv   one row per insider on the filing: CIK, name, role
    NONDERIV_TRANS.tsv   the actual trades in ordinary shares
    DERIV_TRANS.tsv      options and other derivatives (archived, not parsed here)
    FOOTNOTES.tsv        free text (archived, not parsed here)

    A filing can carry several owners and several transactions, so the joined
    output is one row per (filing, owner, transaction).
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd

from core.config import require
from core.http import cached_sec_session
from core.storage import find_raw, save_raw, write_table

BULK_URL = ("https://www.sec.gov/files/structureddata/data/"
            "insider-transactions-data-sets/{year}q{quarter}_form345.zip")

# Transaction codes. Only P is a genuine open-market purchase decision.
TRANSACTION_CODES = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant/award",
    "D": "disposition to issuer",
    "F": "tax withholding",
    "M": "option exercise",
    "G": "gift",
    "C": "conversion",
    "X": "option expiration/exercise",
    "J": "other acquisition/disposition",
    "V": "voluntary early report",
}
HIGH_SIGNAL_CODES = ("P",)

# Roles, most informative first. An executive with P&L responsibility knows more
# than an outside director, who knows more than a passive 10% holder.
ROLE_RANK = {"officer": 0, "director": 1, "tenpercentowner": 2, "other": 3}


def _s():
    return cached_sec_session(require("SEC_CONTACT_EMAIL"))


def fetch_bulk(year: int, quarter: int, refetch: bool = False) -> bytes:
    """
    The quarterly Form 3/4/5 data set, archived before parsing (rule 1).

    A completed quarter's file does not change, so an already-archived copy is
    reused -- parsing from the archive is what rule 1 asks for anyway.
    """
    cached = find_raw("sec", "insider_bulk", year=year, quarter=quarter)
    if cached is not None and not refetch:
        return cached.read_bytes()

    url = BULK_URL.format(year=year, quarter=quarter)
    r = _s().get(url)
    save_raw("sec", "insider_bulk", r.content, "zip",
             {"year": year, "quarter": quarter, "url": url})
    return r.content


def _read(z: zipfile.ZipFile, member: str) -> pd.DataFrame:
    with z.open(member) as fh:
        return pd.read_csv(fh, sep="\t", dtype=str, low_memory=False)


def _classify_role(relationship: str | float) -> str:
    """
    RPTOWNER_RELATIONSHIP carries one or more roles. Rank by how much the person
    plausibly knows, keeping the most informative when someone is both.
    """
    if not isinstance(relationship, str):
        return "other"
    text = relationship.lower()
    for role in ("officer", "director", "tenpercentowner"):
        if role in text:
            return role
    return "other"


def insider_transactions(year: int, quarter: int, persist: bool = True) -> pd.DataFrame:
    """
    One row per (filing, insider, non-derivative transaction) for a quarter.

    Derived columns that carry the research value:
      value_usd       shares x price. Dollar size, not share count, is what
                      separates a token purchase from a conviction one.
      is_open_market_buy  TRANS_CODE == 'P'
      days_to_file    filing date minus transaction date. Form 4 allows 2
                      business days; larger gaps are late filings, which are
                      themselves a governance signal.
      plan_10b5_1     the trade was pre-scheduled, so it reflects a decision
                      taken months earlier rather than a current view.
      role            officer / director / tenpercentowner / other
    """
    raw = fetch_bulk(year, quarter)
    z = zipfile.ZipFile(io.BytesIO(raw))

    sub = _read(z, "SUBMISSION.tsv")
    own = _read(z, "REPORTINGOWNER.tsv")
    trn = _read(z, "NONDERIV_TRANS.tsv")

    # REPORTINGOWNER has one row PER OWNER, and 2.7% of filings are joint (up to
    # 10 owners). Joining it straight onto transactions multiplies every trade by
    # the owner count -- 1.14x rows overall, but 6.8x on the joint filings, which
    # are exactly the ones a cluster screen surfaces. Lions Gate read $184m
    # against a true $27m before this was fixed.
    #
    # So: collapse owners to one primary per filing, and carry the owner count.
    # A joint filing is one decision by related parties, so counting it as one
    # insider is also the more defensible reading for cluster detection.
    n_owners = own.groupby("ACCESSION_NUMBER").size().rename("n_owners_on_filing")
    primary = (own.sort_values(["ACCESSION_NUMBER", "RPTOWNERCIK"])
                  .drop_duplicates("ACCESSION_NUMBER", keep="first"))

    before = len(trn)
    df = (trn.merge(sub, on="ACCESSION_NUMBER", how="left", validate="many_to_one")
             .merge(primary, on="ACCESSION_NUMBER", how="left", validate="many_to_one")
             .merge(n_owners, on="ACCESSION_NUMBER", how="left"))
    if len(df) != before:
        raise RuntimeError(
            f"insider join changed row count {before} -> {len(df)}; "
            "one row per transaction is required or values double-count."
        )

    out = pd.DataFrame({
        "accn": df["ACCESSION_NUMBER"],
        "form_type": df["DOCUMENT_TYPE"],
        "issuer_cik": pd.to_numeric(df["ISSUERCIK"], errors="coerce").astype("Int64"),
        "issuer_name": df["ISSUERNAME"],
        "ticker": df["ISSUERTRADINGSYMBOL"],
        "owner_cik": pd.to_numeric(df["RPTOWNERCIK"], errors="coerce").astype("Int64"),
        "owner_name": df["RPTOWNERNAME"],
        "relationship": df["RPTOWNER_RELATIONSHIP"],
        "owner_title": df["RPTOWNER_TITLE"],
        "security_title": df["SECURITY_TITLE"],
        "trans_code": df["TRANS_CODE"],
        "acquired_disposed": df["TRANS_ACQUIRED_DISP_CD"],
        "shares": pd.to_numeric(df["TRANS_SHARES"], errors="coerce"),
        "price_per_share": pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce"),
        "shares_owned_after": pd.to_numeric(df["SHRS_OWND_FOLWNG_TRANS"], errors="coerce"),
        "direct_indirect": df["DIRECT_INDIRECT_OWNERSHIP"],
        "n_owners_on_filing": pd.to_numeric(df["n_owners_on_filing"], errors="coerce").astype("Int64"),
        "trans_sk": df["NONDERIV_TRANS_SK"],
    })

    # DERA writes dates as DD-MON-YYYY.
    trans_date = pd.to_datetime(df["TRANS_DATE"], format="%d-%b-%Y", errors="coerce")
    filing_date = pd.to_datetime(df["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
    out["trans_date"] = trans_date.dt.date.astype(str)
    out["filed"] = filing_date.dt.date.astype(str)
    out["days_to_file"] = (filing_date - trans_date).dt.days

    out["value_usd"] = out["shares"] * out["price_per_share"]
    out["trans_desc"] = out["trans_code"].map(TRANSACTION_CODES).fillna("unknown")
    # Code P also covers private placements of PREFERRED stock, which are
    # financing events rather than views on the traded equity. Palatin
    # Technologies shows "Series D Preferred Stock" at $150,000/share coded P --
    # $454m of apparent insider buying in a micro-cap. Flag the security type so
    # equity signals can restrict to the listed common shares.
    title = out["security_title"].fillna("").str.lower()
    out["is_common_stock"] = title.str.contains("common") & ~title.str.contains("preferred")

    out["is_open_market_buy"] = out["trans_code"].isin(HIGH_SIGNAL_CODES)
    out["is_sale"] = out["trans_code"].eq("S")
    out["plan_10b5_1"] = df["AFF10B5ONE"].isin(["1", "true", "TRUE"])
    out["role"] = out["relationship"].map(_classify_role)
    out["quarter"] = f"{year}Q{quarter}"

    out = out[out["accn"].notna()].reset_index(drop=True)
    if out.empty:
        raise RuntimeError(f"insider_transactions({year}Q{quarter}) produced no rows")

    if persist:
        write_table(out, "sec", "insider_transactions")
    return out


def cluster_buys(transactions: pd.DataFrame, window_days: int = 30,
                 min_insiders: int = 2, min_value: float = 50_000,
                 common_stock_only: bool = True, persist: bool = True) -> pd.DataFrame:
    """
    Issuers where several DIFFERENT insiders made open-market purchases inside a
    rolling window -- the pattern the literature treats as the strongest
    insider signal, and much harder to explain away than a single buy.

    Counts distinct owners, not transactions: one insider splitting a purchase
    across five days is one decision, not five.

    min_value filters token purchases; the default is deliberately low so that
    a director buying a modest but real amount still counts.
    """
    buys = transactions[
        transactions["is_open_market_buy"]
        & (transactions["value_usd"] >= min_value)
    ].copy()
    if common_stock_only and "is_common_stock" in buys.columns:
        buys = buys[buys["is_common_stock"]]
    if buys.empty:
        return pd.DataFrame()

    # ONE trade can be reported by SEVERAL related filers on separate
    # accessions: TKO Group's 1,579,080 shares at $158.32 appear once from the
    # director and again from the 10% holder he is affiliated with, doubling a
    # $250m purchase to $500m. Identical (issuer, date, size, price) is one
    # economic event, so keep it once.
    buys = buys.drop_duplicates(
        subset=["issuer_cik", "trans_date", "shares", "price_per_share"], keep="first")

    buys["trans_dt"] = pd.to_datetime(buys["trans_date"], errors="coerce")
    buys = buys.dropna(subset=["trans_dt", "issuer_cik"])

    rows = []
    for cik, grp in buys.groupby("issuer_cik"):
        grp = grp.sort_values("trans_dt")
        for _, anchor in grp.iterrows():
            lo = anchor["trans_dt"]
            win = grp[(grp["trans_dt"] >= lo)
                      & (grp["trans_dt"] <= lo + pd.Timedelta(days=window_days))]
            n_insiders = win["owner_cik"].nunique()
            if n_insiders < min_insiders:
                continue
            rows.append({
                "issuer_cik": cik,
                "issuer_name": anchor["issuer_name"],
                "ticker": anchor["ticker"],
                "window_start": lo.date().isoformat(),
                "window_end": (lo + pd.Timedelta(days=window_days)).date().isoformat(),
                "n_insiders": int(n_insiders),
                "n_transactions": int(len(win)),
                "total_value_usd": float(win["value_usd"].sum()),
                # distinct PEOPLE per role, not transaction counts -- one
                # director buying eight times is one director
                "n_officers": int(win.loc[win["role"] == "officer", "owner_cik"].nunique()),
                "n_directors": int(win.loc[win["role"] == "director", "owner_cik"].nunique()),
                "first_filed": win["filed"].min(),
            })

    if not rows:
        return pd.DataFrame()

    out = (pd.DataFrame(rows)
             .sort_values(["issuer_cik", "window_start"])
             .drop_duplicates("issuer_cik", keep="first")     # earliest cluster per issuer
             .sort_values("total_value_usd", ascending=False, ignore_index=True))
    out["window_days"] = window_days
    out["min_insiders"] = min_insiders

    if persist:
        write_table(out, "sec", "insider_cluster_buys")
    return out


def latest_available_quarter(today=None) -> tuple[int, int]:
    """
    Most recent quarter whose DERA data set should be published. DERA publishes
    a few weeks after quarter end, so lag by one quarter.
    """
    from datetime import date
    d = today or date.today()
    q = (d.month - 1) // 3 + 1
    return (d.year, q - 1) if q > 1 else (d.year - 1, 4)


if __name__ == "__main__":
    y, q = latest_available_quarter()
    tx = insider_transactions(y, q, persist=False)
    print(f"{len(tx):,} transactions in {y}Q{q}")
    print(tx["trans_desc"].value_counts().head(8).to_string())
