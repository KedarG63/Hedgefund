"""
MCA21 -- CHG-1 CHARGE REGISTER (the hidden-leverage dataset)

WHAT IT IS
  Every time an Indian company pledges an asset for a secured loan, it must
  file Form CHG-1 with the Registrar of Companies. The MCA portal exposes the
  resulting "Index of Charges" per company: charge holder (which bank/NBFC),
  amount, date created, date modified, date satisfied, and status.

WHY IT IS THE BEST UNDEREXPLOITED DATASET IN INDIA
  1. It covers UNLISTED SUBSIDIARIES. A listed parent's consolidated accounts
     can look clean while an unlisted subsidiary is quietly pledging assets.
     Charges show up here months before they show up in any financial statement.
  2. A NEW charge is a bank extending credit -- a private credit-quality signal.
     A charge SATISFIED early is deleveraging. A flurry of MODIFICATIONS is
     usually refinancing under stress.
  3. Charge holder identity tells you which lender has exposure to whom. You can
     rebuild the private-credit network graph of corporate India from this.
  4. It is filed on a statutory deadline, so it is far more timely than
     quarterly results.

ACCESS REALITY -- READ THIS
  Index of Charges is FREE to view on the MCA V3 portal, no login required.
  Only full document downloads cost money (~Rs.100/doc).

  BUT it is CAPTCHA-gated. I am not going to help you defeat the CAPTCHA --
  it explicitly violates MCA's terms of use, and for a fund the downside is
  not a blocked IP, it is a regulatory conversation you do not want. It is
  also unnecessary, because of this:

  *** THE UNIVERSE IS SMALL ENOUGH TO DO BY HAND. ***
  NIFTY 500 + their material subsidiaries is roughly 1,500-2,000 CINs. At ~20
  seconds each with the workflow below, that is one focused day of work, and
  a couple of hours per quarter to refresh. You get a dataset almost nobody
  else has, precisely BECAUSE it resists automation.

THE HUMAN-IN-THE-LOOP PATTERN (fully legitimate, ~10x faster than clicking)
  A Playwright script that:
    - opens the page and pre-fills the CIN from your queue
    - PAUSES for you to type the CAPTCHA (one keystroke burst)
    - on submit, auto-scrapes the results table, saves raw HTML, advances to
      the next CIN
  You are the CAPTCHA solver; the bot does the other 95% of the work.
  See fetch_charges_assisted() below.

FREE ROUTES THAT NEED NO CAPTCHA AT ALL -- start here
  a) ANNUAL REPORT NOTES. Secured-borrowing notes itemise charges, lender and
     collateral. PDFs on company IR pages + exchanges. Parse with pdfplumber.
     Gets you ~80% of the signal for listed names.
  b) CREDIT RATING RATIONALES. CRISIL / ICRA / CARE / India Ratings publish
     free rationales that itemise every facility, limit and lender, updated on
     every rating action -- often MORE current than MCA. Highly scrapeable.
     This is the single best substitute.
  c) CERSAI (cersai.org.in). The central security-interest registry. Separate
     public search covering charges on property and receivables.
  d) MCA BULK DATA + data.gov.in. MCA publishes company master data in bulk and
     Monthly Information Bulletins. Check here before scraping anything.
  e) DRT / NCLT / IBBI ORDERS. When a charge goes bad it becomes a court
     document, and those are free, full-text, and name every lender.
"""
from pathlib import Path

MCA_CHARGES_URL = "https://www.mca.gov.in/mcafoportal/viewIndexOfCharges.do"
MCA_V3_SEARCH = "https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS/company-master-info.html"


def fetch_charges_assisted(cin_list: list[str], out_dir: str = "data/raw/mca/charges"):
    """
    Semi-automated Index of Charges collection.
    YOU solve the CAPTCHA; the bot does navigation, extraction and archiving.

    Run with:  python -m connectors.mca_charges
    Requires:  pip install playwright && playwright install chromium
    """
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # headless=False on purpose -- you need to SEE the captcha
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        for i, cin in enumerate(cin_list, 1):
            target = out / f"{cin}.html"
            if target.exists():
                print(f"[{i}/{len(cin_list)}] {cin} already collected, skipping")
                continue

            page.goto(MCA_CHARGES_URL, wait_until="domcontentloaded")

            # Pre-fill the CIN so you only have to handle the captcha
            for sel in ["#companyID", "input[name='companyID']", "#cinNumber"]:
                try:
                    page.fill(sel, cin, timeout=2000)
                    break
                except Exception:
                    continue

            print(f"\n[{i}/{len(cin_list)}] {cin}")
            print("  -> type the CAPTCHA in the browser and press Submit")
            input("  -> then press ENTER here to capture the result... ")

            html = page.content()
            target.write_text(html, encoding="utf-8")
            print(f"  saved {target}")

        browser.close()


def parse_charges_html(html: str):
    """Turn a saved Index of Charges page into rows."""
    import pandas as pd
    from io import StringIO
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return pd.DataFrame()
    # The charges grid is normally the widest table on the page
    if not tables:
        return pd.DataFrame()
    return max(tables, key=lambda t: t.shape[1])


if __name__ == "__main__":
    # Put your CIN queue here (get CINs from BSE/NSE listings or MCA master data)
    cins = ["L17110MH1973PLC019786"]  # example: Reliance Industries
    fetch_charges_assisted(cins)
