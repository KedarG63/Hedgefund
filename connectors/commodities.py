"""
COMMODITIES -- gold, silver, crude, natgas, base metals, agri.

THE HONEST PICTURE ON "REAL-TIME":
  Real-time exchange prices are a licensed product. Every exchange on earth
  sells them. There is no free real-time feed, and anyone offering one is
  redistributing without a licence.

  BUT: you are going to have a broker anyway. Your broker's API IS a licensed
  real-time feed, and it costs almost nothing.
    - India (NSE/BSE/MCX): Zerodha Kite Connect ~Rs.2,000/month, WebSocket
      ticks, up to 3,000 instruments. Also Dhan, Upstox, Fyers, Angel One.
    - US/global: Interactive Brokers API (free with account), or Databento /
      Polygon.io if you want exchange-direct without a broker relationship.

  So: broker WebSocket = your real-time layer.
      Exchange EOD files = your authoritative settlement layer.
  Reconcile the two nightly. If they diverge, trust the exchange file.

This module covers the free EOD/settlement layer. Real-time lives in stream.py.
"""
import io
import json
from datetime import date

import pandas as pd
from core.http import Fetcher
from core.storage import save_raw, write_table


# ---------------------------------------------------------- CME / COMEX / NYMEX
# Free daily settlement, volume and open interest. Undocumented but stable
# web-service used by cmegroup.com itself.
# Product IDs (find more: cmegroup.com -> product page -> URL contains the id)
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


def cme_settlements(product: str, trade_date: date | None = None) -> pd.DataFrame:
    """
    Official settlement prices straight from the exchange. This is the
    authoritative number -- what contracts actually mark against.
    """
    pid = CME_PRODUCTS[product]
    f = Fetcher(base_headers={"Referer": "https://www.cmegroup.com/"})
    url = f"https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/{pid}/FUT"
    params = {"tradeDate": trade_date.strftime("%m/%d/%Y")} if trade_date else {}
    r = f.get(url, params=params)
    save_raw("cme", f"settle_{product}", r.content, "json")
    payload = json.loads(r.content)
    df = pd.DataFrame(payload.get("settlements", []))
    df["product"] = product
    return write_table(df, "cme", "settlements") and df


# ---------------------------------------------------------- MCX (India)
def mcx_bhavcopy(d: date) -> pd.DataFrame:
    """
    MCX daily bhavcopy -- gold, silver, crude, natgas, copper, zinc, cotton,
    mentha, CPO. India's own settlement truth.

    MCX changes its download path periodically. Use the DevTools recipe:
    mcxindia.com -> Market Data -> Bhavcopy -> F12 Network -> copy the POST.
    """
    f = Fetcher(base_headers={"Referer": "https://www.mcxindia.com/"})
    r = f.post(
        "https://www.mcxindia.com/backpage.aspx/GetDateWiseBhavCopy",
        json={"Date": d.strftime("%Y-%m-%d")},
        headers={"Content-Type": "application/json"},
    )
    save_raw("mcx", "bhavcopy", r.content, "json", {"date": d.isoformat()})
    data = r.json().get("d", [])
    return pd.DataFrame(data)


# ---------------------------------------------------------- IBJA (India physical gold)
def ibja_rates() -> pd.DataFrame:
    """
    India Bullion & Jewellers Association publishes AM/PM rates for gold at each
    purity (999, 995, 916, 750, 585) and silver 999.

    WHY THIS MATTERS: IBJA 999 is the reference India's own institutions use --
    RBI struck Sovereign Gold Bond issue prices off IBJA 999 closing averages.
    It is the physical-market truth, and the spread between IBJA and (MCX or
    COMEX + import duty + rupee) is a real, tradeable India premium/discount.
    """
    f = Fetcher()
    r = f.get("https://ibjarates.com/")
    save_raw("ibja", "rates", r.content, "html")
    tables = pd.read_html(io.BytesIO(r.content))
    df = tables[0] if tables else pd.DataFrame()
    df["fetched"] = pd.Timestamp.utcnow()
    return write_table(df, "ibja", "rates") and df


# ---------------------------------------------------------- CBIC tariff values
def cbic_tariff_notifications() -> pd.DataFrame:
    """
    ***THE "500-PAGE FINE PRINT" ONE***
    CBIC notifies an OFFICIAL customs valuation for gold, silver, crude palm
    oil, brass scrap etc., roughly fortnightly, in the Gazette.

    This is the Government of India's own stated view of the world price, used
    to compute duty. It is buried in notification PDFs that nobody parses.
    Non-tariff notifications list is at cbic.gov.in; each is a small PDF with a
    table you can extract with pdfplumber/camelot.
    """
    f = Fetcher()
    r = f.get("https://www.cbic.gov.in/entities/view-sticky-posts")
    save_raw("cbic", "notifications_index", r.content, "html")
    # Parse the listing, then pull each notification PDF and table-extract it.
    return pd.DataFrame()  # see parse_cbic_pdf() below


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

    For gold: HS 7108. For crude: 2709. For edible oil: 1511/1507.
    8-digit granularity means you can track a single listed company's product
    category. Almost nobody mines this properly.

    It is an ASP.NET form -- needs __VIEWSTATE round-tripping, so this is one of
    the few places Playwright genuinely earns its keep. Run it monthly, not
    intraday, so slowness does not matter.
    """
    return dgcis_note.__doc__
