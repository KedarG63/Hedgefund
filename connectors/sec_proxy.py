"""
SEC DEF 14A -- proxy statements: pay versus performance, and the proxy corpus.

TWO ROUTES, AND THE CHEAP ONE COMES FIRST
    A proxy statement is a long unstructured HTML document. But since the 2022
    pay-versus-performance rules, part of it is tagged in Inline XBRL under the
    `ecd` taxonomy -- and that tagged part flows into SEC's companyfacts bulk
    archive. So the most valuable numbers in the proxy are already sitting on
    disk from the companyfacts download, at ZERO additional request cost:

        PeoTotalCompAmt                  CEO total compensation as disclosed
        PeoActuallyPaidCompAmt           CEO compensation ACTUALLY PAID --
                                         equity marked to market, so it moves
                                         with the share price
        NonPeoNeoAvgTotalCompAmt         other named executives, average
        NonPeoNeoAvgCompActuallyPaidAmt  same, actually paid
        TotalShareholderRtnAmt           the company's TSR (indexed, $100 base)
        PeerGroupTotalShareholderRtnAmt  the peer group's TSR, same basis
        CoSelectedMeasureAmt             the performance measure the company
                                         itself chose to be judged on

    That is a complete pay-for-performance panel: what the CEO actually earned
    against what shareholders actually earned, on the company's own peer basis.

COVERAGE IS PARTIAL, AND THE REASON MATTERS
    Measured across the S&P 500: 168 of 492 companies (34%) expose an `ecd`
    block, and only 108 (22%) tag PeoTotalCompAmt. That is NOT because the rest
    failed to disclose -- every one of them files a pay-versus-performance
    table. It is because many tag it DIMENSIONALLY (per year, per executive, as
    XBRL members), and companyfacts carries only non-dimensional facts.

    So treat this as a large free sample, not a census. A screen built on it is
    biased toward filers with simpler tagging, and any conclusion drawn across
    the whole index needs the missing two-thirds from the documents themselves.

WHAT STILL NEEDS THE DOCUMENT
    Board composition, director independence, related-party transactions and the
    full summary-compensation table are not tagged anywhere. They require
    parsing proxy HTML, which is a separate job with materially lower
    reliability than anything else in this pipeline. proxy_filings() builds the
    corpus and document URLs so that work can start from a real index rather
    than a search.

SAY-ON-PAY IS ALREADY COVERED ELSEWHERE
    Vote results arrive as 8-K Item 5.07 (Submission of Matters to a Vote of
    Security Holders), which connectors/sec_8k.py already captures -- 4,736
    occurrences in the S&P 500 history. Cross-reference there rather than
    re-parsing proxies for it.
"""
from __future__ import annotations

import pandas as pd

from core.storage import write_table
from connectors.sec_companyfacts import _archive, facts_for, facts_to_frame
from connectors.sec_edgar import submissions

PROXY_FORMS = ("DEF 14A", "DEFA14A", "DEFR14A", "PRE 14A", "PREC14A", "DEFC14A")

# The pay-versus-performance concepts, with what each actually means.
PVP_CONCEPTS = {
    "PeoTotalCompAmt": "CEO total compensation (as disclosed)",
    "PeoActuallyPaidCompAmt": "CEO compensation actually paid (equity marked to market)",
    "NonPeoNeoAvgTotalCompAmt": "Other named executives, average total compensation",
    "NonPeoNeoAvgCompActuallyPaidAmt": "Other named executives, average actually paid",
    "TotalShareholderRtnAmt": "Company total shareholder return (indexed)",
    "PeerGroupTotalShareholderRtnAmt": "Peer group total shareholder return (indexed)",
    "CoSelectedMeasureAmt": "Company-selected performance measure",
}


def pay_versus_performance(ciks, persist: bool = True) -> pd.DataFrame:
    """
    Pay-versus-performance panel, read from the companyfacts archive offline.

    One row per (company, fiscal year, concept). Long rather than wide because
    concept coverage is uneven -- a wide frame would imply the missing ones were
    zero rather than untagged.
    """
    zf = _archive()
    frames, missing = [], []
    try:
        for cik in ciks:
            try:
                facts = facts_for(cik, zf)
            except KeyError:
                missing.append(int(cik))
                continue
            if not (facts.get("facts") or {}).get("ecd"):
                continue
            df = facts_to_frame(facts)
            df = df[(df["taxonomy"] == "ecd") & (df["concept"].isin(PVP_CONCEPTS))]
            if not df.empty:
                frames.append(df)
    finally:
        zf.close()

    if not frames:
        raise RuntimeError(f"No ecd pay-versus-performance facts for {len(list(ciks))} CIKs")

    out = pd.concat(frames, ignore_index=True)
    out["fiscal_year"] = out["end"].astype(str).str[:4]
    out["meaning"] = out["concept"].map(PVP_CONCEPTS)
    out = out[["cik", "entity", "fiscal_year", "concept", "meaning", "value",
               "unit", "start", "end", "form", "filed", "accn"]]
    out = out.sort_values(["cik", "fiscal_year", "concept"], ignore_index=True)
    out.attrs["missing_ciks"] = missing

    if persist:
        write_table(out, "sec", "pay_versus_performance")
    return out


def pay_vs_tsr(pvp: pd.DataFrame, persist: bool = True) -> pd.DataFrame:
    """
    The governance screen: CEO pay actually paid against shareholder return.

    Pivots the long panel to one row per (company, fiscal year) and derives:

      tsr_vs_peer      company TSR minus peer TSR, same indexed basis. Positive
                       means the company beat the peer set the company itself
                       chose.
      pay_premium      CEO actually-paid divided by disclosed total. Above 1
                       means unvested equity gained value during the year;
                       below 1 means it lost value. This is the number that
                       makes "actually paid" worth having -- disclosed total
                       cannot fall when the stock does.

    A company with pay_premium above 1 while tsr_vs_peer is negative is paying
    more for less, which is the classic governance short candidate. That is a
    SCREEN: proxies are annual and the sample is biased (see module docstring),
    so it ranks names to read, not names to trade.

    THE FOUNDER-EQUITY CAVEAT, which the top of this screen is full of.
    "Actually paid" includes the change in fair value of ALL unvested equity,
    including grants made years earlier. At a founder-led company that term
    swamps everything else and reflects the share price, not a pay decision:

        Coinbase   FY2021   disclosed $3.27m   actually paid $2,118m   premium 648x
        Palantir   FY2023   disclosed $3.50m   actually paid $1,100m   premium 314x

    Nobody awarded those sums that year; existing founder stock was revalued.
    So a large pay_premium is a question, not a verdict -- read it as "equity
    revaluation dominated", and reserve the governance reading for cases where
    DISCLOSED pay is also high. Trade Desk FY2021 is the version that does
    qualify: $835m disclosed, a real mega-grant, against a below-peer TSR.
    """
    if pvp.empty:
        return pd.DataFrame()

    wide = pvp.pivot_table(index=["cik", "entity", "fiscal_year"],
                           columns="concept", values="value",
                           aggfunc="first").reset_index()
    wide.columns.name = None

    def col(name):
        return wide[name] if name in wide.columns else pd.Series(pd.NA, index=wide.index)

    tsr = pd.to_numeric(col("TotalShareholderRtnAmt"), errors="coerce")
    peer = pd.to_numeric(col("PeerGroupTotalShareholderRtnAmt"), errors="coerce")
    paid = pd.to_numeric(col("PeoActuallyPaidCompAmt"), errors="coerce")
    total = pd.to_numeric(col("PeoTotalCompAmt"), errors="coerce")

    wide["tsr_vs_peer"] = tsr - peer
    wide["pay_premium"] = paid / total.replace(0, pd.NA).astype("float64")
    wide["ceo_pay_actually_paid"] = paid
    wide["ceo_pay_disclosed"] = total
    wide["company_tsr"] = tsr
    wide["peer_tsr"] = peer

    # Paying more while underperforming the company's own chosen peer set.
    wide["pay_up_performance_down"] = (
        (wide["pay_premium"] > 1) & (wide["tsr_vs_peer"] < 0)
    ).fillna(False)

    out = wide[["cik", "entity", "fiscal_year", "ceo_pay_disclosed",
                "ceo_pay_actually_paid", "pay_premium", "company_tsr",
                "peer_tsr", "tsr_vs_peer", "pay_up_performance_down"]]
    out = out.sort_values(["fiscal_year", "cik"], ignore_index=True)

    if persist:
        write_table(out, "sec", "pay_vs_tsr")
    return out


def proxy_filings(ciks, persist: bool = True) -> pd.DataFrame:
    """
    Every proxy filing for a universe, with document URLs.

    One request per company via the submissions API, same pattern as the 8-K
    connector. This is the CORPUS -- it does not parse the documents, it makes
    them addressable so that board composition and related-party work can start
    from a real index.
    """
    from connectors.sec_8k import primary_document_url

    frames, failures = [], []
    for cik in ciks:
        try:
            subs = submissions(cik)
        except Exception as e:                       # noqa
            failures.append((int(cik), f"{type(e).__name__}: {str(e)[:70]}"))
            continue
        if subs.empty or "form" not in subs.columns:
            continue
        p = subs[subs["form"].isin(PROXY_FORMS)].copy()
        if p.empty:
            continue
        p["cik"] = int(cik)
        frames.append(p)

    if not frames:
        raise RuntimeError(f"No proxy filings found across {len(list(ciks))} CIKs")

    df = pd.concat(frames, ignore_index=True).rename(columns={
        "accessionNumber": "accn", "filingDate": "filed",
        "reportDate": "report_date", "primaryDocument": "primary_document",
    })
    df["document_url"] = [
        primary_document_url(c, a, d) if isinstance(d, str) and d else None
        for c, a, d in zip(df["cik"], df["accn"], df["primary_document"])
    ]
    cols = ["cik", "accn", "form", "filed", "report_date",
            "primary_document", "document_url"]
    df = df[[c for c in cols if c in df.columns]].sort_values(
        ["filed", "cik"], ascending=[False, True], ignore_index=True)

    if persist:
        write_table(df, "sec", "proxy_filings")
    return df


if __name__ == "__main__":
    from connectors.sec_8k import sp500_ciks
    pvp = pay_versus_performance(sorted(sp500_ciks()), persist=False)
    print(f"{len(pvp):,} pay-vs-performance facts, {pvp.cik.nunique()} companies")
    print(pay_vs_tsr(pvp, persist=False).head(10).to_string(index=False))
