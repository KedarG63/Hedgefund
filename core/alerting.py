"""
Failure alerting.

WHY THIS IS NOT OPTIONAL DECORATION
    BUILD_GUIDE: "a job that silently stops running looks exactly like a market
    with no news." With 30+ jobs you will not notice by eye which one stopped,
    and the dashboard only shows what was collected -- it cannot show what a
    dead job would have collected.

DESIGN
    Alerting must never take the pipeline down with it. A webhook timeout at
    23:05 is not a reason to lose the run's results, so every send failure is
    caught and reported rather than raised.

    Configured entirely through .env, and silent when unconfigured -- a laptop
    run should not nag about a missing webhook. `configured()` says which
    channels are live so the caller can tell "nothing failed" from "nothing was
    sent".
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from core.config import get


def configured() -> dict[str, bool]:
    """Which alert channels have credentials. Nothing here is required."""
    return {
        "webhook": bool(get("ALERT_WEBHOOK_URL")),
        "email": bool(get("ALERT_EMAIL_TO") and get("SMTP_HOST")),
    }


def format_report(results: dict[str, str], as_of: str | None = None) -> str:
    """
    Human-readable run summary, failures first.

    Includes the SUCCESS count as well as failures: "3 failed" alone does not
    say whether the other 28 ran or whether the whole process died after job 3.
    """
    failed = {k: v for k, v in results.items() if str(v).startswith("FAIL")}
    skipped = [k for k, v in results.items() if v == "SKIP"]
    ok = [k for k, v in results.items() if v == "OK"]

    stamp = as_of or datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [f"quantdata run {stamp}",
             f"{len(ok)} ok / {len(failed)} failed / {len(skipped)} skipped"]
    if failed:
        lines.append("")
        lines.append("FAILED:")
        lines.extend(f"  {name}: {msg}" for name, msg in sorted(failed.items()))
    return "\n".join(lines)


def send_webhook(text: str, url: str | None = None, timeout: float = 10.0) -> bool:
    """
    POST a plain-text alert. Slack-compatible ({"text": ...}).

    Returns success rather than raising: alerting failing must not fail the run.
    """
    url = url or get("ALERT_WEBHOOK_URL")
    if not url:
        return False
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  alert: webhook send failed ({type(e).__name__}: {str(e)[:80]})")
        return False


def alert_failures(results: dict[str, str]) -> dict:
    """
    Send an alert if anything failed. Returns what happened, so run_daily can
    report "3 failed, alert sent" or "3 failed, NO CHANNEL CONFIGURED" -- the
    second being much worse and worth saying out loud.
    """
    failed = {k: v for k, v in results.items() if str(v).startswith("FAIL")}
    channels = configured()
    out = {"failures": len(failed), "channels": channels, "sent": False}
    if not failed:
        return out
    text = format_report(results)
    if channels["webhook"]:
        out["sent"] = send_webhook(text)
    return out
