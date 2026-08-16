"""
RBI DBIE (data.rbi.org.in) -- CIMS Gateway client.

WHAT YOUR cURL REVEALED
-----------------------
Endpoint pattern:
    POST https://data.rbi.org.in/CIMS_Gateway_DBIE/GATEWAY/SERVICES/<serviceName>
    body: {"body": { ...service-specific params... }}

So EVERY dataset on DBIE is one POST to a named service. Find the service
names and you have the whole site. See discover_rbi_services.py.

THE AUTHORIZATION HEADER IS NOT A SECRET
----------------------------------------
    authorization: 2n3ze01786881491969196
                   ^^^^^^ ^^^^^^^^^^^^^^^^
                   random  epoch MICROSECONDS

    1786881491969196 us -> 2026-08-16T11:58:11 UTC

It is minted in the browser's JavaScript on every request: a short random
prefix concatenated with the current timestamp. There is no server-issued
credential and nothing to expire. mint_token() below reproduces it.

If RBI ever changes the scheme, fall back to harvest_token() (Playwright reads
the header off a real page load) or PUBLICATIONS (fixed-URL Excel/PDF).

THE COOKIES ARE F5 BIG-IP WAF COOKIES
-------------------------------------
    TS01eeff17..., TSe532997b027...
These are set by RBI's web application firewall. You do not hardcode them --
they expire and they are tied to your session. Instead, GET the homepage once
and your HTTP client collects them automatically. bootstrap() does this.

*** DO NOT COMMIT YOUR OWN COOKIE VALUES TO GIT. *** They are session
credentials. The ones in your cURL should be treated as burned.
"""
from __future__ import annotations

import json
import random
import string
import time
from datetime import datetime, timedelta

import pandas as pd

from core.http import Fetcher
from core.storage import save_raw, write_table

BASE = "https://data.rbi.org.in"
GATEWAY = f"{BASE}/CIMS_Gateway_DBIE/GATEWAY/SERVICES"


def mint_token(prefix_len: int = 6) -> str:
    """Reproduce the client-side authorization token: random prefix + epoch microseconds."""
    alphabet = string.ascii_lowercase + string.digits
    prefix = "".join(random.choice(alphabet) for _ in range(prefix_len))
    micros = int(time.time() * 1_000_000)
    return f"{prefix}{micros}"


class RBIClient:
    """
    Thin, well-behaved client for the DBIE gateway.

        rbi = RBIClient()
        df  = rbi.forex_reserves(weeks=52)
        raw = rbi.call("dbie_someOtherService", {"param": "value"})
    """

    def __init__(self, throttle: float = 1.0, token: str | None = None):
        self.http = Fetcher(
            base_headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": BASE,
                "Referer": f"{BASE}/",
                "channelkey": "key2",
                "datatype": "application/json",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            min_delay=throttle,
        )
        self._fixed_token = token
        self.bootstrap()

    def bootstrap(self):
        """Hit the homepage so the F5 WAF hands us its TS* cookies."""
        self.http.client.get(BASE + "/", timeout=30)
        time.sleep(0.5)

    def _headers(self):
        return {"authorization": self._fixed_token or mint_token()}

    def call(self, service: str, body: dict, archive: bool = True) -> dict:
        """POST one gateway service. Archives the raw response before parsing."""
        url = f"{GATEWAY}/{service}"
        r = self.http.post(url, json={"body": body}, headers=self._headers())
        if archive:
            save_raw("rbi", service, r.content, "json", {"request_body": body})
        return r.json()

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def to_frame(payload: dict) -> pd.DataFrame:
        """
        Normalise whatever the gateway returns into a DataFrame.
        Response shapes vary by service, so we probe rather than assume.
        """
        if payload is None:
            return pd.DataFrame()
        if isinstance(payload, list):
            return pd.DataFrame(payload)

        # common wrappers: {"body": {...}}, {"data": [...]}, {"result": [...]}
        for key in ("body", "data", "result", "records", "response", "Data"):
            if key in payload:
                return RBIClient.to_frame(payload[key])

        # a dict whose values are lists of records -> take the longest list
        list_vals = {k: v for k, v in payload.items() if isinstance(v, list)}
        if list_vals:
            best = max(list_vals.values(), key=len)
            return pd.DataFrame(best)

        return pd.json_normalize(payload)

    # ---------------------------------------------------------------- services

    def forex_reserves(self, weeks: int = 52, currency: str = "USD",
                       reserve: str = "TR", frequency: str = "Weekly") -> pd.DataFrame:
        """
        Weekly forex reserves -- RBI's highest-frequency release (every Friday).
        reserve codes seen: TR = total reserves. Others exist (FCA, gold, SDR,
        IMF reserve position) -- read them off the dropdown in DevTools.
        """
        to_dt = datetime.now()
        frm = to_dt - timedelta(weeks=weeks)
        payload = self.call("dbie_foreignExchangeReserves", {
            "currencyCode": currency,
            "reserveCode": reserve,
            "fromDate": frm.strftime("%Y-%m-%d 00:00:00"),
            "toDate": to_dt.strftime("%Y-%m-%d 00:00:00"),
            "frequency": frequency,
        })
        df = self.to_frame(payload)
        if not df.empty:
            write_table(df, "rbi", "forex_reserves")
        return df


# ---------------------------------------------------------------------------
# SERVICE CATALOG
# Populate this by running discover_rbi_services.py -- it greps RBI's own
# JavaScript bundle and returns every service name on the site at once.
# ---------------------------------------------------------------------------
SERVICES: dict[str, dict] = {
    "dbie_foreignExchangeReserves": {
        "desc": "Weekly forex reserves",
        "body": {"currencyCode": "USD", "reserveCode": "TR",
                 "fromDate": "", "toDate": "", "frequency": "Weekly"},
    },
    # ... add discovered services here
}


# ---------------------------------------------------------------------------
# Fallback A: harvest a real token from a live page load (needs Playwright)
# ---------------------------------------------------------------------------
def harvest_token(timeout_ms: int = 30_000) -> str | None:
    """Load DBIE headlessly and read the authorization header off a real XHR."""
    from playwright.sync_api import sync_playwright

    found = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_request(req):
            if "GATEWAY/SERVICES" in req.url and "authorization" in req.headers:
                found.setdefault("token", req.headers["authorization"])
                found.setdefault("url", req.url)

        page.on("request", on_request)
        page.goto(BASE, wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_timeout(3000)
        browser.close()

    return found.get("token")


# ---------------------------------------------------------------------------
# Fallback B: fixed-URL publications. No reverse engineering, never breaks.
# ---------------------------------------------------------------------------
PUBLICATIONS = {
    "wss": "https://rbi.org.in/Scripts/WSSView.aspx",
    "bulletin": "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx",
    "handbook": "https://rbi.org.in/Scripts/AnnualPublications.aspx?head=Handbook%20of%20Statistics%20on%20Indian%20Economy",
    "press_releases": "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx",
}


def fetch_publication(key: str) -> str:
    r = Fetcher().get(PUBLICATIONS[key])
    save_raw("rbi", f"pub_{key}", r.content, "html")
    return r.text


if __name__ == "__main__":
    rbi = RBIClient()
    df = rbi.forex_reserves(weeks=52)
    print(df.head(20))
    print(f"\n{len(df)} rows")
