"""
RBI DBIE (data.rbi.org.in) -- CIMS Gateway client.

WHAT THE cURL + BUNDLE ANALYSIS REVEALED
----------------------------------------
Endpoint pattern:
    POST https://data.rbi.org.in/CIMS_Gateway_<GW>/GATEWAY/SERVICES/<serviceName>
    body: {"body": { ...service-specific params... }}

Five gateways exist, not one: DBIE, LOGIN, SAARC, RBIDATA, FRBS. Every service
name is a string literal in the JS bundles -- see tools/discover_rbi_services.py,
which enumerates 118 of them into rbi_service_catalog.json.

AUTHORIZATION: A SERVER-ISSUED SESSION TOKEN (corrected 2026-08-16)
-------------------------------------------------------------------
An earlier reading of this API assumed the token was minted client-side, because
it looks like <random prefix> + <epoch microseconds>:

    authorization: 2n3ze01786881491969196

It is NOT client-minted. The SERVER mints it in that format and returns it in a
*response* header. From main.js:

    getSession() -> POST /CIMS_Gateway_LOGIN/GATEWAY/SERVICES/security_generateSessionToken
    this.sessionId = response.headers.get("authorization")
    store.saveInSession("sessionId", this.sessionId)

Later calls echo that value back as the `authorization` request header. The
give-away that it is server-side is that every response's `transactionid` has
the identical shape -- the format is simply how RBI's gateway builds ids.

Verified live: a locally minted token is rejected (errorCode 4302), and omitting
the header is rejected (8706). The handshake returns data. mint_token() is kept
only to document the dead end.

TWO TRAPS
---------
1. HTTP 200 DOES NOT MEAN SUCCESS. Application errors come back as 200 with
   {"header":{"status":"error","errorCode":...}}. Always check the payload.
2. The JSON is HTML-ENTITY-ENCODED (an F5 WAF response filter), so it is not
   directly json.loads-able. decode() unescapes it first.

THE COOKIES ARE F5 BIG-IP WAF COOKIES
-------------------------------------
    TS01eeff17..., TSe532997b027...
Set by RBI's firewall. Never hardcode them -- they expire and are session-bound.
GET the homepage once and the client collects them. bootstrap() does this.

*** DO NOT COMMIT COOKIE OR TOKEN VALUES. *** They are session credentials.
"""
from __future__ import annotations

import html
import json
import random
import string
import time
from datetime import datetime, timedelta

import pandas as pd

from core.http import Fetcher
from core.rbi_crypto import encrypt as dbie_encrypt
from core.storage import save_raw, write_table

BASE = "https://data.rbi.org.in"
GATEWAY = f"{BASE}/CIMS_Gateway_DBIE/GATEWAY/SERVICES"
LOGIN_GATEWAY = f"{BASE}/CIMS_Gateway_LOGIN/GATEWAY/SERVICES"

# Reserve components, confirmed by probing the live service. The numbering in
# fxReservesDescription (1, 1.1 .. 1.4) matches WSS Table 2, so this is the
# complete breakdown -- TR is the sum of the other four.
RESERVE_CODES = {
    "TR": "Total Reserves",
    "FCA": "Foreign Currency Assets",
    "GOLD": "Gold",
    "SDR": "SDRs",
    "IMF": "Reserve Position in the IMF",
}


class RBIGatewayError(RuntimeError):
    """The gateway returned status=error. Raised so a job fails loudly (rule 4)."""


def mint_token(prefix_len: int = 6) -> str:
    """
    DEAD END, kept as documentation. Reproduces the token's *shape* (random
    prefix + epoch microseconds) but not its provenance -- the server has never
    issued it, so the gateway rejects it with errorCode 4302. Use
    RBIClient._handshake() instead.
    """
    alphabet = string.ascii_lowercase + string.digits
    prefix = "".join(random.choice(alphabet) for _ in range(prefix_len))
    return f"{prefix}{int(time.time() * 1_000_000)}"


def decode(text: str) -> str:
    """Undo the WAF's HTML-entity encoding so the body can be parsed as JSON."""
    return html.unescape(text).replace("\xa0", " ")


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
        self._token = token
        self.bootstrap()
        if not self._token:
            self._token = self._handshake()

    def bootstrap(self):
        """Hit the homepage so the F5 WAF hands us its TS* cookies."""
        self.http.client.get(BASE + "/", timeout=30)
        time.sleep(0.5)

    def _handshake(self) -> str:
        """
        Ask the LOGIN gateway for a session token. It arrives as the
        `authorization` RESPONSE header, not in the body.
        """
        r = self.http.post(f"{LOGIN_GATEWAY}/security_generateSessionToken",
                           json={"body": {}})
        token = r.headers.get("authorization")
        if not token:
            raise RBIGatewayError(
                "security_generateSessionToken returned no authorization header. "
                f"status={r.status_code} body={decode(r.text)[:200]}"
            )
        return token

    def call(self, service: str, body: dict, archive: bool = True,
             gateway: str = GATEWAY) -> dict:
        """
        POST one gateway service. Archives the raw response BEFORE parsing
        (rule 1), then raises if the payload reports an error (rule 4).
        """
        r = self.http.post(f"{gateway}/{service}", json={"body": body},
                           headers={"authorization": self._token})
        if archive:
            save_raw("rbi", service, r.content, "json",
                     {"request_body": body, "url": f"{gateway}/{service}"})

        payload = json.loads(decode(r.text))
        header = payload.get("header", {}) if isinstance(payload, dict) else {}
        if header.get("status") == "error":
            raise RBIGatewayError(
                f"{service} failed: code={header.get('errorCode')} "
                f"{header.get('errorMessage')}"
            )
        return payload

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

        for key in ("resultList", "body", "data", "result", "records", "response", "Data"):
            if key in payload:
                return RBIClient.to_frame(payload[key])

        list_vals = {k: v for k, v in payload.items() if isinstance(v, list)}
        if list_vals:
            return pd.DataFrame(max(list_vals.values(), key=len))

        return pd.json_normalize(payload)

    # ---------------------------------------------------------------- catalog

    def sap_token(self) -> dict:
        """
        RBI's own key-material service. Returns {sapToken, sapLogonToken, token}.

        Observed 2026-08-18: `token` is 32 bytes of base64 key material handed
        out with no credential, while sapToken/sapLogonToken come back null for
        an anonymous caller -- which is why report *rendering* (SAP
        BusinessObjects) stays out of reach while the catalog does not.
        """
        payload = self.call("login_getSapToken",
                            {"portalCode": "DBIE", "user": "", "code": ""},
                            gateway=LOGIN_GATEWAY)
        return (payload.get("body") or {}).get("status") or {}

    def reports_for(self, function: str, department: str, menu: str) -> list[dict]:
        """
        Every report behind one DBIE screen, with its reportId, frequency and
        the full date range RBI holds.

        Parameters mirror the app's own getReportspub(token, menu, function,
        departments) -- i.e. the three levels of the navigation tree:
            function   "Indicators" | "Statistics" | "Publication"
            department "Financial Sector Indicators"
            menu       "Daily LAF Operation"
        All four values go over the wire AES-encrypted; see core.rbi_crypto.
        """
        payload = self.call("dbie_getReportsDbie", {
            "departments": [dbie_encrypt(department)],
            "menu": dbie_encrypt(menu),
            "portal": dbie_encrypt("DBIE"),
            "function": dbie_encrypt(function),
        })
        out = []
        for group in (payload.get("body") or {}).get("reports") or []:
            for sub in group.get("subs") or []:
                out.append({
                    "report_id": sub.get("reportId"),
                    "report_name": sub.get("reportName"),
                    "frequency": sub.get("reportFreq"),
                    "from_date": sub.get("fromdate"),
                    "to_date": sub.get("todate"),
                    "function": function,
                    "department": department,
                    "menu": menu,
                })
        return out

    def economy_watch_sections(self) -> list[str]:
        """Top-level Economy Watch categories (unencrypted call)."""
        payload = self.call("dbie_getEcoHeader", {})
        rows = (payload.get("body") or {}).get("listEconomyWatchVO") or []
        return [r.get("economyWatchHeader") for r in rows if r.get("economyWatchHeader")]

    def economy_watch_reports(self, section: str) -> list[dict]:
        """Reports under one Economy Watch category. Takes an encrypted param."""
        payload = self.call("dbie_getEcoSubHeader",
                            {"defaultSelection": dbie_encrypt(section)})
        rows = (payload.get("body") or {}).get("subSelection") or []
        return [{"report_id": r.get("reportId"),
                 "report_name": r.get("economyWatchSubheader"),
                 "section": section} for r in rows]

    def key_indicators(self, persist: bool = True) -> pd.DataFrame:
        """
        RBI's headline indicator ticker: policy repo rate, CRR, SLR, CPI
        inflation and the reference exchange rate, each with its as-of date.

        Why it matters: this is the only DBIE route to current policy settings
        that needs no SAP BusinessObjects session, so it works today and gives a
        daily point-in-time stamp of the policy corridor. It is a snapshot, not
        history -- the long series (Key Rates runs from 1935) sits behind the
        report renderer.

        NOTE: dbie_getPublicationDataImpala accepts a reportId but IGNORES it --
        verified by requesting five different ids and getting byte-identical
        responses. Do not mistake it for a per-report data service.
        """
        payload = self.call("dbie_getPublicationDataImpala", {})
        rows = (payload.get("body") or {}).get("result") or []
        if not rows:
            raise RBIGatewayError("key_indicators returned no rows")

        df = pd.DataFrame(rows).rename(columns={"name": "indicator", "rate": "value"})
        df["as_of"] = (pd.to_datetime(df["timeDate"], unit="ms", utc=True)
                         .dt.tz_convert("Asia/Kolkata").dt.date.astype(str))
        df = df[["as_of", "indicator", "value", "currencyDesc"]].sort_values(
            "indicator", ignore_index=True)
        if persist:
            write_table(df, "rbi", "key_indicators")
        return df

    # ---------------------------------------------------------------- services

    def forex_reserves(self, weeks: int = 52, currency: str = "USD",
                       components: tuple[str, ...] = tuple(RESERVE_CODES),
                       frequency: str = "Weekly", persist: bool = True) -> pd.DataFrame:
        """
        Weekly foreign exchange reserves -- RBI's highest-frequency release,
        published every Friday.

        Why it matters for research: reserves are the RBI's intervention
        war-chest. Week-on-week changes in FCA net of valuation effects are the
        cleanest available read on whether the RBI is defending the rupee, which
        drives USDINR carry and is a direct input to the gold-import premium in
        connectors/commodities.py.

        Returns one tidy row per (week_ended, component) rather than a wide
        frame, so new components never change the schema.
        """
        to_dt = datetime.now()
        frm = to_dt - timedelta(weeks=weeks)

        frames = []
        for code in components:
            payload = self.call("dbie_foreignExchangeReserves", {
                "currencyCode": currency,
                "reserveCode": code,
                "fromDate": frm.strftime("%Y-%m-%d 00:00:00"),
                "toDate": to_dt.strftime("%Y-%m-%d 00:00:00"),
                "frequency": frequency,
            })
            df = self.to_frame(payload)
            if df.empty:
                continue
            frames.append(df)

        if not frames:
            raise RBIGatewayError(
                f"forex_reserves returned no rows for components={components}. "
                "The service answered but the result list was empty -- check the "
                "date window and reserveCode values."
            )

        out = pd.concat(frames, ignore_index=True)

        # timeDate is epoch milliseconds in IST; RBI reports as-of Friday.
        out["week_ended"] = (
            pd.to_datetime(out["timeDate"], unit="ms", utc=True)
              .dt.tz_convert("Asia/Kolkata").dt.date.astype(str)
        )
        out = out.rename(columns={
            "fxReservesCode": "component_code",
            "fxReservesDescription": "component",
            "amount": "amount_usd",
            "currencyCode": "currency",
            "timeFisYear": "fiscal_year",
        })
        out["fiscal_year"] = out["fiscal_year"].astype(str).str.strip()
        out["frequency"] = frequency

        cols = ["week_ended", "component_code", "component", "amount_usd",
                "currency", "unit", "unitDescription", "fiscal_year", "frequency"]
        out = out[[c for c in cols if c in out.columns]].sort_values(
            ["week_ended", "component_code"], ignore_index=True)

        if persist:
            write_table(out, "rbi", "forex_reserves")
        return out


# ---------------------------------------------------------------------------
# SERVICE CATALOG
# Populated by tools/discover_rbi_services.py -> rbi_service_catalog.json
# (118 services). Only a handful are per-series endpoints like the one below;
# most data flows through a generic Impala query engine
# (dbie_getImpalaDQAction / dbie_getElementDetailsActionEnhanced) addressed by
# element code, and those codes are ENCRYPTED client-side before transmission
# (encryptPipe.transform("encrypt", ...)). Driving that engine needs either the
# encryption scheme lifted from the bundle or a captured cURL -- do not guess.
# ---------------------------------------------------------------------------
SERVICES: dict[str, dict] = {
    "dbie_foreignExchangeReserves": {
        "desc": "Weekly forex reserves, by component",
        "gateway": "DBIE",
        "body": {"currencyCode": "USD", "reserveCode": "TR",
                 "fromDate": "", "toDate": "", "frequency": "Weekly"},
    },
    "security_generateSessionToken": {
        "desc": "Issues the session token used as the authorization header",
        "gateway": "LOGIN",
        "body": {},
    },
}


# ---------------------------------------------------------------------------
# Fallback A: harvest a real token from a live page load (needs Playwright).
# No longer required now that the handshake is understood -- kept for the case
# where RBI starts validating something the plain handshake does not satisfy.
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

        page.on("request", on_request)
        page.goto(BASE, wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_timeout(3000)
        browser.close()

    return found.get("token")


if __name__ == "__main__":
    rbi = RBIClient()
    df = rbi.forex_reserves(weeks=12)
    print(df.to_string(max_rows=30))
    print(f"\n{len(df)} rows")
