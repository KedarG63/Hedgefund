"""
ENUMERATE THE ENTIRE DBIE API IN ONE SHOT.

You found ONE service by clicking. There are hundreds. Rather than clicking
through every page on the site, read RBI's own JavaScript.

WHY THIS WORKS
    DBIE is an Angular single-page app. The whole app -- every screen, every
    API call it can make -- ships to your browser as a handful of .js bundles.
    Every service name is a plain string literal inside those files.

    So: download the bundles, regex for the service names, done. You get the
    complete API surface, including screens you have never visited, in about
    thirty seconds. No browser, no clicking.

    This is the single highest-leverage move in the whole exercise.

    Nearby string literals usually reveal the request-body parameters too
    (currencyCode, reserveCode, frequency...), which is why we print context.

    python discover_rbi_services.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.http import Fetcher
from core.storage import save_raw

BASE = "https://data.rbi.org.in"

SERVICE_RE = re.compile(r"GATEWAY/SERVICES/([A-Za-z0-9_\-]+)")
# DBIE service names are camelCase and mostly prefixed dbie_
NAME_RE = re.compile(r"[\"'`](dbie_[A-Za-z0-9_]+)[\"'`]")
ASSET_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+\.js)["']""", re.I)
CHUNK_RE = re.compile(r"""["']([\w\-.]+\.js)["']""")


def fetch_bundles(f: Fetcher) -> dict[str, str]:
    """Pull index.html, then every .js bundle it references."""
    idx = f.get(BASE + "/").text
    save_raw("rbi", "index_html", idx.encode(), "html")

    paths, bundles = set(), {}
    for m in ASSET_RE.finditer(idx):
        paths.add(m.group(1))
    for m in CHUNK_RE.finditer(idx):
        paths.add(m.group(1))

    if not paths:
        print("No .js references in index.html. The shell may load them via a")
        print("runtime loader -- grab the bundle URLs from DevTools > Sources.")
        return {}

    for p in sorted(paths):
        url = p if p.startswith("http") else f"{BASE}/{p.lstrip('/')}"
        try:
            js = f.get(url).text
            bundles[url] = js
            print(f"  fetched {url}  ({len(js):,} bytes)")
            # Angular lazy-loads more chunks referenced inside main.js
            for m in CHUNK_RE.finditer(js):
                sub = m.group(1)
                suburl = f"{BASE}/{sub.lstrip('/')}"
                if suburl not in bundles:
                    try:
                        subjs = f.get(suburl).text
                        bundles[suburl] = subjs
                        print(f"  fetched {suburl}  ({len(subjs):,} bytes)")
                    except Exception:
                        pass
        except Exception as e:
            print(f"  skip {url}: {e}")
    return bundles


def extract_services(bundles: dict[str, str]) -> dict[str, dict]:
    """Find every service name plus the surrounding code as a parameter hint."""
    found: dict[str, dict] = {}
    for url, js in bundles.items():
        for rx in (SERVICE_RE, NAME_RE):
            for m in rx.finditer(js):
                name = m.group(1)
                if name in found:
                    continue
                lo, hi = max(0, m.start() - 400), min(len(js), m.end() + 400)
                found[name] = {"bundle": url, "context": js[lo:hi]}
    return found


def guess_params(context: str) -> list[str]:
    """Pull likely request-body keys out of the code around a service name."""
    keys = set(re.findall(r"[\"']?([a-z][A-Za-z0-9]{2,25})[\"']?\s*:", context))
    noise = {
        "function", "return", "this", "var", "let", "const", "type", "default",
        "value", "class", "style", "id", "name", "key", "then", "catch", "url",
        "headers", "method", "data", "body", "next", "error", "subscribe",
    }
    return sorted(k for k in keys if k not in noise)


def main():
    f = Fetcher(base_headers={"Referer": BASE + "/"}, min_delay=0.5)

    print("Fetching JS bundles...")
    bundles = fetch_bundles(f)
    if not bundles:
        return

    print("\nExtracting service names...")
    services = extract_services(bundles)

    catalog = {}
    for name, info in sorted(services.items()):
        catalog[name] = {
            "endpoint": f"/CIMS_Gateway_DBIE/GATEWAY/SERVICES/{name}",
            "likely_params": guess_params(info["context"]),
            "bundle": info["bundle"],
        }

    out = Path("rbi_service_catalog.json")
    out.write_text(json.dumps(catalog, indent=2))

    print(f"\nFound {len(catalog)} services -> {out}\n")
    for name, meta in list(catalog.items())[:40]:
        params = ", ".join(meta["likely_params"][:6])
        print(f"  {name:55s} {params}")
    if len(catalog) > 40:
        print(f"  ... and {len(catalog) - 40} more in {out}")

    print("\nNEXT: pick the services you want, add them to SERVICES in")
    print("connectors/rbi_dbie.py, and call them with RBIClient.call().")


if __name__ == "__main__":
    main()
