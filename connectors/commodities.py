"""
COMMODITIES -- the settlement layer, and the India gold premium.

THE HONEST PICTURE ON "REAL-TIME":
  Real-time exchange prices are a licensed product. Every exchange on earth
  sells them. There is no free real-time feed, and anyone offering one is
  redistributing without a licence.

  BUT: you are going to have a broker anyway. Your broker's API IS a licensed
  real-time feed, and it costs almost nothing (Kite Connect ~Rs.2,000/month for
  NSE/BSE/MCX; Interactive Brokers free with an account for US/global).

  So: broker WebSocket = real-time layer (stream.py).
      Exchange EOD files = authoritative settlement layer (this module).
  Reconcile nightly. When they diverge, trust the exchange file.

WHY THIS MODULE IS THE POINT OF THE PROJECT
  Individually these are three ordinary price feeds. Together they produce a
  series nobody sells: the INDIA GOLD PREMIUM -- what physical gold actually
  clears at in Mumbai versus what it costs to land an ounce there.

      premium = IBJA 999  -  (COMEX settle x USDINR, converted to Rs/10g,
                              grossed up by import duty and GST)

  It widens on import restrictions, festival demand, and shifts in the
  smuggling channel. You can compute it only because you own all four legs:
  COMEX from CME, the rupee from RBI, the physical price from IBJA, and the
  duty from CBIC.

ENDPOINT STATE, VERIFIED LIVE 2026-08-22
  Two of the four scaffolded endpoints had drifted. Both were found by reading
  the sites' own JavaScript, not by guessing variants:

  CME   the settlements service now REQUIRES tradeDate. Without it the response
        is a 400 that says so: "Required parameter 'tradeDate' is not present."
        The old scaffold treated it as optional, so every call failed.

  MCX   backpage.aspx/GetDateWiseBhavCopy is dead -- and it fails SILENTLY,
        returning HTTP 200 whose body is a 404 HTML page. The live path comes
        from BhavCopy.js via a GetData() helper that builds
        origin + pathname + method:
            /market-data/bhavcopy/GetDateWiseBhavCopy?InstrumentName=ALL&fromDate=DD/MM/YYYY
        Historical dates work, so backfill is available.

  IBJA  works. Rates are keyed off the tab-am / tab-pm container ids rather
        than table position, because position is the first thing to move.

  CBIC  the notifications index returns a 3 KB shell. NOT wired up -- see
        import_duty_note().
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime

import pandas as pd
from lxml import html as lxml_html

from core.http import Fetcher
from core.storage import db, register_views, save_raw, write_table

TROY_OZ_GRAMS = 31.1034768

# ---------------------------------------------------------- CME / COMEX / NYMEX
# Free daily settlement, volume and open interest, from the web service
# cmegroup.com itself calls. Product ids come from the product page URL.
CME_PRODUCTS = {
    "gold":        437,   # GC
    "silver":      458,   # SI
    "copper":      438,   # HG
    "wti_crude":   425,   # CL
    "natgas":      444,   # NG
    "corn":        300,
    "wheat":       323,
    "soybeans":    320,
}
CME_URL = "https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/{pid}/FUT"


def _cme_session() -> Fetcher:
    return Fetcher(base_headers={
        "Referer": "https://www.cmegroup.com/",
        "Accept": "application/json, text/plain, */*",
    })


def cme_settlements(product: str, trade_date: date | None = None,
                    persist: bool = True) -> pd.DataFrame:
    """
    Official settlement prices straight from the exchange -- the authoritative
    number, what contracts actually mark against.

    trade_date is REQUIRED by the service. It defaults to today here, but note
    that asking for a non-trading day returns an empty settlement list rather
    than an error, so an empty frame means "no session", not "broken".
    """
    if product not in CME_PRODUCTS:
        raise KeyError(f"unknown product {product!r}. Known: {', '.join(CME_PRODUCTS)}")

    d = trade_date or date.today()
    url = CME_URL.format(pid=CME_PRODUCTS[product])
    r = _cme_session().get(url, params={"tradeDate": d.strftime("%m/%d/%Y")})
    save_raw("cme", f"settle_{product}", r.content, "json",
             {"product": product, "trade_date": d.isoformat(), "url": url})

    payload = json.loads(r.content)
    rows = payload.get("settlements", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["product"] = product
    df["trade_date_requested"] = d.isoformat()
    # CME echoes the session it actually served, which may differ from the ask.
    df["trade_date_served"] = payload.get("tradeDate")

    # The last row is a "Total" aggregate: settle is "-", but volume and open
    # interest are curve-wide sums. Summing volume without excluding it double
    # counts the entire curve.
    df["is_total"] = df["month"].astype(str).str.strip().str.lower().eq("total")

    # Response order is chronological by expiry, so position identifies the
    # front month. Do NOT infer it from price: the curve here is in contango, so
    # min(settle) happens to be the front month today and would be the BACK
    # month the moment gold flips to backwardation.
    df["contract_order"] = (~df["is_total"]).cumsum() - 1
    df.loc[df["is_total"], "contract_order"] = pd.NA

    for col in ("open", "high", "low", "settle", "change", "volume", "openInterest"):
        if col in df.columns:
            df[f"{col}_num"] = pd.to_numeric(
                df[col].astype(str).str.replace(r"[^0-9.\-]", "", regex=True),
                errors="coerce")
    if persist:
        write_table(df, "cme", "settlements")
    return df


# ---------------------------------------------------------- MCX (India)
MCX_BHAVCOPY_URL = "https://www.mcxindia.com/market-data/bhavcopy/GetDateWiseBhavCopy"


def mcx_bhavcopy(d: date, instrument: str = "ALL", persist: bool = True) -> pd.DataFrame:
    """
    MCX daily bhavcopy -- gold, silver, crude, natgas, copper, zinc, cotton,
    mentha, CPO. India's own settlement truth.

    The response is {"IsSuccess": bool, "Message": str, "Data": [...]}, and
    IsSuccess is checked: this host answers HTTP 200 for dead paths, so status
    alone proves nothing.

    MOSTLY OPTIONS. A typical day is ~16,700 rows of which only ~145 are
    futures: filter InstrumentName == 'FUTCOM' (commodity futures) or 'FUTIDX'
    (index futures) before reading prices. The option rows carry zero OHLC and a
    Close that is a premium, not a commodity price -- reading GOLD without
    filtering returns values like 0.5 and 40,850 alongside the real 162,438.
    """
    f = Fetcher(base_headers={
        "Referer": "https://www.mcxindia.com/market-data/bhavcopy",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })
    r = f.get(MCX_BHAVCOPY_URL,
              params={"InstrumentName": instrument, "fromDate": d.strftime("%d/%m/%Y")})
    save_raw("mcx", "bhavcopy", r.content, "json",
             {"date": d.isoformat(), "instrument": instrument, "url": MCX_BHAVCOPY_URL})

    payload = json.loads(r.content)
    if not payload.get("IsSuccess"):
        raise RuntimeError(
            f"MCX bhavcopy {d}: IsSuccess=false, Message={payload.get('Message')!r}")

    df = pd.DataFrame(payload.get("Data") or [])
    if df.empty:
        raise RuntimeError(f"MCX bhavcopy {d}: no rows (holiday, or the path moved again)")

    df["trade_date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y", errors="coerce").dt.date.astype(str)
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    if persist:
        write_table(df, "mcx", "bhavcopy")
    return df


# ---------------------------------------------------------- IBJA (India physical)
IBJA_URL = "https://ibjarates.com/"
IBJA_PURITIES = ("999", "995", "916", "750", "585")


def ibja_rates(persist: bool = True) -> pd.DataFrame:
    """
    India Bullion & Jewellers Association rates: gold at each purity (999, 995,
    916, 750, 585) plus silver and platinum 999, for both daily sessions.

    IBJA states it publishes "opening (AM) and closing (PM)" rates, and the two
    tables live in containers with ids tab-am and tab-pm -- which is what this
    keys off. Selecting by table POSITION would be one layout tweak away from
    silently swapping the sessions.

    WHY IT MATTERS: IBJA 999 is the reference India's own institutions use --
    RBI struck Sovereign Gold Bond issue prices off IBJA 999 averages. It is the
    physical-market truth, and the leg the India premium is measured from.

    Rates are Rs per 10 grams.
    """
    r = Fetcher().get(IBJA_URL)
    save_raw("ibja", "rates", r.content, "html", {"url": IBJA_URL})

    doc = lxml_html.fromstring(r.content)
    frames = []
    for session in ("am", "pm"):
        nodes = doc.xpath(f'//*[@id="tab-{session}"]//table')
        if not nodes:
            raise RuntimeError(
                f"IBJA: no table under #tab-{session} -- the page layout changed")
        table = pd.read_html(io.StringIO(lxml_html.tostring(nodes[0], encoding="unicode")))[0]
        table = table.rename(columns={table.columns[0]: "rate_date"})
        long = table.melt(id_vars="rate_date", var_name="metal_purity", value_name="rate_inr_per_10g")
        long["session"] = session.upper()
        frames.append(long)

    out = pd.concat(frames, ignore_index=True)
    out["rate_date"] = pd.to_datetime(out["rate_date"], format="%d/%m/%Y",
                                      errors="coerce").dt.date.astype(str)
    out["rate_inr_per_10g"] = pd.to_numeric(out["rate_inr_per_10g"], errors="coerce")
    out = out.dropna(subset=["rate_inr_per_10g"])
    out["metal"] = out["metal_purity"].apply(
        lambda x: "gold" if str(x) in IBJA_PURITIES else str(x).split()[0].lower())
    if out.empty:
        raise RuntimeError("IBJA: tables parsed but no numeric rates survived")

    if persist:
        write_table(out, "ibja", "rates")
    return out


# ---------------------------------------------------------- the derived series
# India's gold import levies. These are POLICY VARIABLES, not market data: they
# change in budgets and notifications, and the premium is highly sensitive to
# them. Defaults reflect the post-2024-budget basic customs duty plus AIDC, and
# the GST charged on landed value.
#
# VERIFY THESE AGAINST THE CURRENT CBIC NOTIFICATION BEFORE TRUSTING A LEVEL.
# The premium's LEVEL moves with the assumption; its CHANGES over time are
# robust to it, so treat the time series as the signal and the level as
# provisional until cbic_tariff_notifications() is wired up.
DEFAULT_IMPORT_DUTY_PCT = 6.0     # basic customs duty + AIDC
DEFAULT_GST_PCT = 3.0             # GST on gold


def usdinr_reference_rate() -> pd.DataFrame:
    """
    RBI's USD/INR reference rate, read from the warehouse (rbi_key_indicators).

    Primary source, already collected by the RBI connector -- no extra fetch,
    and it is the rate Indian institutions actually mark against.
    """
    con = db()
    try:
        register_views(con)
        return con.execute("""
            SELECT as_of AS rate_date, value AS usdinr
            FROM rbi_key_indicators
            WHERE lower(indicator) LIKE '%exchange rate%'
            ORDER BY as_of
        """).df()
    finally:
        con.close()


def india_gold_premium(import_duty_pct: float = DEFAULT_IMPORT_DUTY_PCT,
                       gst_pct: float = DEFAULT_GST_PCT,
                       persist: bool = True) -> pd.DataFrame:
    """
    THE derived series: what physical gold clears at in India versus what it
    costs to land it.

        landed Rs/10g = COMEX $/oz x USDINR / 31.1035 x 10
                        x (1 + duty) x (1 + GST)
        premium       = IBJA 999 - landed

    Positive means India is paying above import parity -- tight physical supply,
    festival demand, or import friction. Negative means the domestic market is
    discounting, which historically accompanies weak demand or unofficial
    inflows.

    Joins on date across three sources, so a row appears only where all three
    legs exist for the same day. That is deliberate: interpolating any leg would
    manufacture a spread that was never observable.
    """
    con = db()
    try:
        register_views(con)
        ibja = con.execute("""
            SELECT rate_date, session, rate_inr_per_10g AS ibja_999
            FROM ibja_rates
            WHERE metal_purity = '999' AND metal = 'gold'
        """).df()
        # Front month by CONTRACT ORDER, never by price -- see cme_settlements.
        # The Total row is excluded explicitly rather than relying on its NaN
        # settle, so a future format change cannot let an aggregate through.
        comex = con.execute("""
            SELECT trade_date_requested AS rate_date,
                   arg_min(settle_num, contract_order) AS comex_usd_per_oz,
                   arg_min(month, contract_order)      AS comex_contract
            FROM cme_settlements
            WHERE product = 'gold'
              AND NOT is_total
              AND settle_num IS NOT NULL
              AND contract_order IS NOT NULL
            GROUP BY 1
        """).df()
    finally:
        con.close()

    fx = usdinr_reference_rate()
    if ibja.empty or comex.empty or fx.empty:
        raise RuntimeError(
            "india_gold_premium needs all three legs. Have "
            f"ibja={len(ibja)}, comex={len(comex)}, usdinr={len(fx)} rows. "
            "Run ibja_rates(), cme_settlements('gold') and the RBI key-indicators job."
        )

    df = ibja.merge(comex, on="rate_date", how="inner").merge(fx, on="rate_date", how="inner")
    if df.empty:
        raise RuntimeError(
            "No date where IBJA, COMEX and USDINR all exist. The legs are "
            "collected on different schedules; run them on the same day, or "
            "backfill COMEX for the IBJA dates already held."
        )

    df["comex_inr_per_10g"] = (
        df["comex_usd_per_oz"] * df["usdinr"] / TROY_OZ_GRAMS * 10.0)
    df["landed_inr_per_10g"] = (
        df["comex_inr_per_10g"] * (1 + import_duty_pct / 100) * (1 + gst_pct / 100))
    df["premium_inr_per_10g"] = df["ibja_999"] - df["landed_inr_per_10g"]
    df["premium_pct"] = df["premium_inr_per_10g"] / df["landed_inr_per_10g"] * 100

    df["import_duty_pct"] = import_duty_pct
    df["gst_pct"] = gst_pct
    df = df.sort_values(["rate_date", "session"], ignore_index=True)

    if persist:
        write_table(df, "derived", "india_gold_premium")
    return df


# ---------------------------------------------------------- CBIC tariff values
def cbic_tariff_notifications():
    """
    NOT WIRED UP -- the authoritative source for the duty assumption above.

    CBIC notifies an official customs valuation for gold, silver, crude palm oil
    and brass scrap roughly fortnightly in the Gazette. That is the Government
    of India's own stated view of the world price, and it is what duty is
    actually computed on.

    Status: https://www.cbic.gov.in/entities/view-sticky-posts returns a 3 KB
    shell page, not the notification list -- the content arrives some other way.
    Rather than guess a URL, capture the real request from DevTools (Network ->
    Fetch/XHR while opening the notifications list) and wire it here. Until
    then india_gold_premium() takes the duty as a parameter and says so.
    """
    raise NotImplementedError(cbic_tariff_notifications.__doc__)


def parse_cbic_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract the tariff-value table out of a CBIC notification PDF."""
    import pdfplumber
    rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                rows.extend(table)
    return pd.DataFrame(rows)


# ---------------------------------------------------------- DGCI&S trade data
def dgcis_note():
    """
    Ministry of Commerce Tradestat (tradestat.commerce.gov.in) gives export and
    import value + quantity at 8-DIGIT HS CODE level, monthly.

    For gold: HS 7108. For crude: 2709. For edible oil: 1511/1507. Eight-digit
    granularity means you can track a single listed company's product category.

    It is an ASP.NET form needing __VIEWSTATE round-tripping, so this is one of
    the few places Playwright genuinely earns its keep. Monthly cadence, so
    slowness does not matter.
    """
    return dgcis_note.__doc__


if __name__ == "__main__":
    print(cme_settlements("gold", date(2026, 8, 21), persist=False).head().to_string())
    print(ibja_rates(persist=False).head().to_string())
