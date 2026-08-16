"""
Verify the minted-token hypothesis against the live gateway.

I decoded your token as: <random prefix> + <epoch microseconds>. If that is the
whole story, you can generate tokens forever and never touch a browser.

This tries four variants, in order of how good the outcome is for you:

  1. Minted token          -> best case, fully self-contained
  2. No authorization      -> even better, header is decorative
  3. Your captured token   -> proves the request shape is right
  4. Playwright harvest    -> fallback if RBI validates server-side

    python test_rbi_token.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.http import Fetcher
from connectors.rbi_dbie import BASE, GATEWAY, mint_token

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


def attempt(label: str, headers: dict):
    f = make_client()
    try:
        r = f.client.post(f"{GATEWAY}/{SERVICE}", json={"body": BODY},
                          headers=headers, timeout=30)
        ok = r.status_code == 200 and len(r.content) > 50
        print(f"\n[{label}]  HTTP {r.status_code}  {len(r.content):,} bytes")
        if ok:
            print("  SUCCESS")
            print("  " + r.text[:400].replace("\n", " "))
        else:
            print("  " + r.text[:250].replace("\n", " "))
        return ok
    except Exception as e:
        print(f"\n[{label}]  FAILED: {e}")
        return False


def main():
    print("Testing RBI DBIE gateway auth variants")
    print("=" * 60)

    results = {}
    results["minted token"] = attempt("1. minted token", {"authorization": mint_token()})
    results["no auth header"] = attempt("2. no authorization header", {})
    if CAPTURED_TOKEN:
        results["captured token"] = attempt("3. your captured token",
                                            {"authorization": CAPTURED_TOKEN})

    print("\n" + "=" * 60)
    if results.get("no auth header"):
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
