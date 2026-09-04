"""
Environment / secrets handling.

Everything sensitive -- broker API keys, session cookies, contact emails -- lives
in .env, which is gitignored. Nothing sensitive is ever hardcoded, and nothing
sensitive is ever written to the raw archive sidecars.

Usage:
    from core.config import require, get

    email = require("SEC_CONTACT_EMAIL")   # raises with a useful message
    root  = get("QUANTDATA_ROOT", "./data")
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

_loaded = False

# Keys whose values must never be printed, logged, or written to a sidecar.
SECRET_KEYS = {
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "NSE_COOKIE",
    "BSE_COOKIE",
    "RBI_COOKIE",
    "MCA_COOKIE",
    "ALERT_WEBHOOK_URL",
    "SMTP_PASSWORD",
    "DEEPSEEK_API_KEY",
    "SCREENER_CSRFTOKEN",
    "SCREENER_SESSIONID",
    # Bearer token for the terminal API (api/main.py). It gates read access to
    # the entire warehouse, so it is a credential even though nothing external
    # issued it.
    "TERMINAL_TOKEN",
}


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Minimal .env parser. Deliberately dependency-free and boring:
    KEY=value, # comments, blank lines, optional surrounding quotes.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def load_env(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """
    Load .env into os.environ. Real environment variables win by default --
    that is what lets systemd/cron/CI override the file without editing it.
    Idempotent: safe to call from every module.
    """
    global _loaded
    target = path or ENV_PATH
    values = _parse_env_file(target)
    for key, val in values.items():
        if override or key not in os.environ:
            os.environ[key] = val
    _loaded = True
    return values


def get(key: str, default: str | None = None) -> str | None:
    if not _loaded:
        load_env()
    return os.environ.get(key, default)


def require(key: str) -> str:
    """
    Fetch a required setting or raise. Never returns a placeholder -- a job that
    runs with a fake credential and fails downstream is harder to debug than one
    that refuses to start.
    """
    val = get(key)
    if not val or val.startswith("CHANGEME"):
        raise RuntimeError(
            f"Required setting {key!r} is not set. "
            f"Add it to {ENV_PATH} (copy .env.example) or export it in the environment."
        )
    return val


def redact(key: str, value: str | None) -> str:
    """Render a setting for logs. Secrets become a length-only fingerprint."""
    if value is None:
        return "<unset>"
    if value == "":
        return "<empty>"
    if key in SECRET_KEYS or any(t in key.upper() for t in ("SECRET", "TOKEN", "PASSWORD", "COOKIE", "KEY")):
        return f"<set, {len(value)} chars>"
    return value


def describe() -> dict[str, str]:
    """Safe-to-print snapshot of quantdata settings, for --dry-run and startup logs."""
    if not _loaded:
        load_env()
    keys = sorted(
        k for k in os.environ
        if k.startswith(("QUANTDATA_", "SEC_", "KITE_", "REDIS_", "ALERT_", "SMTP_"))
        or k in SECRET_KEYS
    )
    return {k: redact(k, os.environ.get(k)) for k in keys}
