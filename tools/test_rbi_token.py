"""
Verify how the DBIE gateway actually authenticates.

RESOLVED 2026-08-16 -- the minted-token hypothesis was WRONG.
------------------------------------------------------------
The token looks like <random prefix> + <epoch microseconds>, which suggested it
was minted client-side. It is not. The SERVER mints it in that format and hands
it back in a *response* header. Reading main.js:

    getSession() -> POST /CIMS_Gateway_LOGIN/GATEWAY/SERVICES/security_generateSessionToken
                    body {"body":{}}, header channelkey: key1|key2
    this.sessionId = response.headers.get("authorization")   <-- issued here
    store.saveInSession("sessionId", this.sessionId)

Every later gateway call echoes that value back as the `authorization` REQUEST
header. Minting one locally yields a well-formed string the server has never
issued, so it is rejected.

Variants tried, best outcome first:

  0. Handshake token       -> WORKS. Self-contained, no browser. <-- use this
  1. Minted token          -> rejected (errorCode 4302)
  2. No authorization      -> rejected (errorCode 8706)
  3. Your captured token   -> works until that session expires
  4. Playwright harvest    -> unnecessary now

NOTE ON JUDGING SUCCESS: the gateway returns HTTP 200 for application errors,
with an HTML-entity-encoded body carrying {"header":{"status":"error"}}. Never
judge these endpoints by status code alone.

    python tools/test_rbi_token.py
"""
import html
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from core.http import Fetcher
from connectors.rbi_dbie import BASE, GATEWAY, mint_token

LOGIN_GATEWAY = f"{BASE}/CIMS_Gateway_LOGIN/GATEWAY/SERVICES"

SERVICE = "dbie_foreignExchangeReserves"
TO = datetime.now()
FRM = TO - timedelta(weeks=8)
BODY = {
    "currencyCode": "USD",
    "reserveCode": "TR",
    "fromDate": FRM.strftime("%Y-%m-%d 00:00:00"),
    "toDate": TO.strftime("%Y-%m-%d 00:00:00"),
    "frequency": "Weekly",
}

# Optional: paste your captured token to run variant 3.
# Treat it as burned once shared -- rotate by reloading the site.
CAPTURED_TOKEN = ""


def make_client():
    f = Fetcher(base_headers={
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BASE,
        "Referer": f"{BASE}/",
        "channelkey": "key2",
        "datatype": "application/json",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }, min_delay=1.0)
    # collect the F5 WAF cookies
    f.client.get(BASE + "/", timeout=30)
    return f


def decode(text: str) -> str:
    """
    The gateway HTML-entity-encodes its JSON (an F5 WAF response filter), so the
    body is not directly json.loads-able. Unescape before inspecting.
    """
    return html.unescape(text).replace("\xa0", " ")


def attempt(label: str, headers: dict):
    """
    HTTP 200 is NOT success here. The gateway returns 200 with
    {"header":{"status":"error",...}} for application-level failures, so an
    earlier version of this script reported every variant as working. Judge on
    the decoded payload, never the status code.
    """
    f = make_client()
    try:
        r = f.client.post(f"{GATEWAY}/{SERVICE}", json={"body": BODY},
                          headers=headers, timeout=30)
        body = decode(r.text)
        print(f"\n[{label}]  HTTP {r.status_code}  {len(r.content):,} bytes")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            print("  UNPARSEABLE: " + body[:250].replace("\n", " "))
            return False

        header = payload.get("header", {}) if isinstance(payload, dict) else {}
        if header.get("status") == "error":
            print(f"  ERROR  code={header.get('errorCode')}  {header.get('errorMessage')}")
            return False

        has_data = bool(payload.get("body") or payload.get("data"))
        print("  SUCCESS -- payload carries data" if has_data
              else "  NO ERROR, but no data either")
        print("  " + body[:400].replace("\n", " "))
        return has_data
    except Exception as e:
        print(f"\n[{label}]  FAILED: {e}")
        return False


def handshake() -> str | None:
    """Ask the LOGIN gateway for a session token; it arrives as a response header."""
    f = make_client()
    r = f.client.post(f"{LOGIN_GATEWAY}/security_generateSessionToken",
                      json={"body": {}}, headers={"channelkey": "key2"}, timeout=30)
    tok = r.headers.get("authorization")
    print(f"\n[0a. handshake]  HTTP {r.status_code}  token={'yes' if tok else 'NONE'}")
    return tok


def main():
    print("Testing RBI DBIE gateway auth variants")
    print("=" * 60)

    results = {}
    tok = handshake()
    results["handshake token"] = attempt("0b. handshake token",
                                         {"authorization": tok}) if tok else False
    results["minted token"] = attempt("1. minted token", {"authorization": mint_token()})
    results["no auth header"] = attempt("2. no authorization header", {})
    if CAPTURED_TOKEN:
        results["captured token"] = attempt("3. your captured token",
                                            {"authorization": CAPTURED_TOKEN})

    print("\n" + "=" * 60)
    if results.get("handshake token"):
        print("RESOLVED: POST security_generateSessionToken on the LOGIN gateway,")
        print("then echo the response's `authorization` header on every call.")
        print("Fully self-contained -- no browser, no captured credential.")
    elif results.get("no auth header"):
        print("BEST OUTCOME: the header is decorative. Drop it entirely.")
    elif results.get("minted token"):
        print("GREAT: minted tokens work. Fully self-contained, no browser ever.")
    elif results.get("captured token"):
        print("Request shape is correct, but the token is validated server-side.")
        print("Use harvest_token() (Playwright) once per session and cache it.")
    else:
        print("Nothing worked. Likely causes, in order:")
        print("  - WAF cookies not collected: confirm GET / returned 200")
        print("  - Payload date format changed: re-capture a fresh cURL")
        print("  - IP-level geo/rate blocking")
        print("  Fall back to PUBLICATIONS (fixed-URL Excel/PDF) meanwhile.")


if __name__ == "__main__":
    main()
