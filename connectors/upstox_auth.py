"""
Upstox OAuth -- the daily token ritual, made one command.

WHY THIS EXISTS
    Upstox access tokens expire at 03:30 IST EVERY DAY. There is no refresh
    token and no headless path: the flow needs an interactive browser login, so
    it cannot be fully automated. Kite is the same. That is a property of Indian
    broker APIs, not something to engineer around.

    What CAN be removed is the friction. This prints the login URL, takes the
    code you paste back, exchanges it, and writes the token to .env with its
    expiry -- so the daily step is one command rather than a chore you abandon
    by Thursday.

THE REDIRECT URL, WHICH IS WHERE THIS USUALLY GOES WRONG
    It must be HTTPS. An http:// redirect registered against an app produces
    `INVALID_APIKEY_URL` at the authorization dialog -- documented as
    UDAPI100068, "Check your 'client_id' and 'redirect_uri'; one or both are
    incorrect" -- which reads like a bad key and is not. Verified against live
    apps: http:// fails, https:// returns the login page.

    It must also match the registered value EXACTLY (scheme, host, port, path),
    and the docs warn that URLs ending in .php or similar may be blocked.

    NOTHING NEEDS TO LISTEN ON IT. This is standard authorization-code flow:
    after login the BROWSER is redirected to
    https://127.0.0.1:5000/callback?code=XXXX. The browser will show a
    connection error and the code is still in the address bar. So no local
    HTTPS server, no self-signed certificate, no callback handler.

    python -m connectors.upstox_auth          # prints the URL, waits for the code
    python -m connectors.upstox_auth --check  # just report token validity
"""
from __future__ import annotations

import base64
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.config import ENV_PATH, get, require

AUTH_DIALOG = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
PROFILE_URL = "https://api.upstox.com/v2/user/profile"


def login_url(state: str = "quantdata") -> str:
    """The browser URL that starts the flow."""
    return AUTH_DIALOG + "?" + urllib.parse.urlencode({
        "client_id": require("UPSTOX_API_KEY"),
        "redirect_uri": require("UPSTOX_REDIRECT_URI"),
        "response_type": "code",
        "state": state,
    })


def extract_code(pasted: str) -> str:
    """
    Accept either a bare code or the whole redirected URL.

    People paste the address bar, which is the natural thing to do when the
    page failed to load -- so handle both rather than making them edit it.
    """
    pasted = pasted.strip()
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        values = urllib.parse.parse_qs(query).get("code")
        if values:
            return values[0]
    return pasted


def exchange(code: str) -> dict:
    """
    Swap the single-use authorization code for an access token.

    The code is valid for ONE use "regardless of whether the access token
    generation succeeds or fails", so a failed exchange means starting over
    with a fresh login rather than retrying with the same code.
    """
    r = httpx.post(TOKEN_URL, timeout=45,
                   headers={"Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json"},
                   data={
                       "code": code,
                       "client_id": require("UPSTOX_API_KEY"),
                       "client_secret": require("UPSTOX_API_SECRET"),
                       "redirect_uri": require("UPSTOX_REDIRECT_URI"),
                       "grant_type": "authorization_code",
                   })
    if r.status_code != 200:
        raise RuntimeError(f"token exchange failed ({r.status_code}): {r.text[:300]}")
    payload = r.json()
    if "access_token" not in payload:
        raise RuntimeError(f"no access_token in response: {json.dumps(payload)[:300]}")
    return payload


def token_expiry(token: str | None = None) -> datetime | None:
    """Expiry from the JWT's own `exp` claim -- no API call needed."""
    token = token or get("UPSTOX_ACCESS_TOKEN")
    if not token or token.count(".") != 2:
        return None
    try:
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return datetime.fromtimestamp(claims["exp"], timezone.utc)
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def hours_remaining(token: str | None = None) -> float | None:
    exp = token_expiry(token)
    if exp is None:
        return None
    return (exp - datetime.now(timezone.utc)).total_seconds() / 3600


def is_valid(token: str | None = None) -> bool:
    left = hours_remaining(token)
    return left is not None and left > 0


def write_env(token: str, path: Path | None = None) -> Path:
    """
    Replace UPSTOX_ACCESS_TOKEN in .env, leaving every other line untouched.

    Rewrites in place rather than appending: a second UPSTOX_ACCESS_TOKEN line
    would be silently shadowed by whichever the parser reads last, and a token
    that looks written but is not used is exactly the kind of failure that
    wastes an afternoon.
    """
    path = path or ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith("UPSTOX_ACCESS_TOKEN"):
            out.append(f"UPSTOX_ACCESS_TOKEN={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"UPSTOX_ACCESS_TOKEN={token}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def verify(token: str | None = None) -> dict:
    """Confirm the token works, by asking Upstox rather than trusting the JWT."""
    token = token or require("UPSTOX_ACCESS_TOKEN")
    r = httpx.get(PROFILE_URL, timeout=45,
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"token rejected ({r.status_code}): {r.text[:200]}")
    return r.json().get("data", {})


def _report():
    left = hours_remaining()
    if left is None:
        print("UPSTOX_ACCESS_TOKEN is missing or not a JWT.")
        return 1
    if left <= 0:
        print(f"Token EXPIRED {abs(left):.1f} hours ago ({token_expiry().isoformat()}).")
        return 1
    print(f"Token valid for {left:.1f} more hours (expires {token_expiry().isoformat()}).")
    try:
        who = verify()
        print(f"  verified with Upstox: {who.get('user_name')} ({who.get('user_id')}), "
              f"exchanges={who.get('exchanges')}")
    except Exception as e:                                   # noqa
        print(f"  but the API rejected it: {e}")
        return 1
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Upstox daily token refresh")
    ap.add_argument("--check", action="store_true", help="report token validity and exit")
    args = ap.parse_args(argv)

    if args.check:
        return _report()

    left = hours_remaining()
    if left and left > 0:
        print(f"Current token is still valid for {left:.1f} hours. "
              "Continuing will replace it.\n")

    print("1. Open this in a browser and log in:\n")
    print(f"   {login_url()}\n")
    print("2. You will land on your redirect URL and the page will FAIL to load.")
    print("   That is expected -- nothing is listening there.")
    print("   Copy the whole address bar (or just the code= value).\n")

    pasted = input("Paste it here: ").strip()
    if not pasted:
        print("Nothing pasted.")
        return 1

    payload = exchange(extract_code(pasted))
    token = payload["access_token"]
    write_env(token)
    print(f"\nWrote UPSTOX_ACCESS_TOKEN to {ENV_PATH}")
    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
