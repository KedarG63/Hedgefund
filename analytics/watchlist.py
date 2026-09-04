"""
Hand-maintained NIFTY 50 heavyweight watchlist -- NOT a live/verified feed.

No connector in this repo fetches actual index-constituent weights (checked
connectors/nse_bse.py and connectors/upstox.py -- neither has one, and no
verified URL exists for NIFTYBEES/BANKBEES's SEBI portfolio-disclosure page
either, which would be the Indian analogue of connectors/sec_edgar.py's
sp500_constituents() deriving S&P 500 membership from the SPDR ETF's N-PORT
filing). This list is a documented placeholder, same provisional character
as BSE_ANNOUNCEMENT_CATEGORIES / DEFAULT_IMPORT_DUTY_PCT / FII_METRIC_MAP
elsewhere in this codebase -- update it by hand when constituents change,
and replace it with a real connector the moment a verified source is found.
Every consumer (analytics/digest.py, dashboard/app.py) is written against
this same WATCHLIST_SYMBOLS name, so swapping the source later needs no
downstream change.

Last reviewed: 2026-08 (Phase 8). Not weighted -- membership only.
Every symbol below was verified live against a real TckrSymb in the current
nse_bhavcopy vintage before being added -- TATAMOTORS was dropped because it
no longer matches any ticker in the live universe (likely a post-demerger
symbol rename, e.g. into separate commercial/passenger-vehicle listings --
unverified which, so not guessed at; re-add once the current ticker is
confirmed live).
"""

WATCHLIST_SYMBOLS = (
    "RELIANCE", "HDFCBANK", "ICICIBANK", "TCS", "INFY", "BHARTIARTL",
    "ITC", "LT", "KOTAKBANK", "AXISBANK", "SBIN", "HINDUNILVR",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO",
    "WIPRO", "NESTLEIND", "ADANIENT", "ONGC", "NTPC", "POWERGRID",
    "M&M", "TATASTEEL", "JSWSTEEL", "HCLTECH", "TECHM",
)
