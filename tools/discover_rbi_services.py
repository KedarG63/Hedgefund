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

    Nearby string literals usually reveal the request-body parameters too
    (currencyCode, reserveCode, frequency...), which is why we keep context.

NOTES FROM THE LIVE RUN (2026-08-16)
    - index.html references exactly 4 bundles; main.js is ~9.4 MB.
    - Service names are NOT quoted in the minified output. An earlier regex
      required surrounding quotes and therefore found 1 name instead of 118.
    - Angular lazy chunks are "<chunkId><hash>.js", where the shared hash comes
      from `r.u=e=>e+".<hash>.js"` in runtime.js and the ids appear as `r.e(<id>)`
      in main.js.

    python tools/discover_rbi_services.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from core.http import Fetcher
from core.storage import save_raw

BASE = "https://data.rbi.org.in"
OUT = Path(__file__).resolve().parent.parent / "rbi_service_catalog.json"

SCRIPT_SRC_RE = re.compile(r"""<script[^>]*\ssrc\s*=\s*["']([^"']+\.js)["']""", re.I)
# Service names in minified code are bare identifiers inside string concats --
# do NOT require surrounding quotes.
NAME_RE = re.compile(r"\b(dbie_[A-Za-z0-9_]{2,})\b")
SERVICE_RE = re.compile(r"GATEWAY/SERVICES/([A-Za-z0-9_\-]+)")
CHUNK_HASH_RE = re.compile(r"""\.u\s*=\s*\w+\s*=>\s*\w+\s*\+\s*["'](\.[0-9a-f]{8,}\.js)["']""")
CHUNK_ID_RE = re.compile(r"\.e\((\d{2,6})\)")


def fetch_bundles(f: Fetcher) -> dict[str, str]:
    """index.html -> its <script src> bundles -> any lazy-loaded chunks."""
    idx = f.get(BASE + "/").text
    save_raw("rbi", "index_html", idx.encode(), "html", {"url": BASE + "/"})

    names = SCRIPT_SRC_RE.findall(idx)
    if not names:
        print("No <script src> in index.html -- the shell may use a runtime")
        print("loader. Grab bundle URLs from DevTools > Sources and add here.")
        return {}

    bundles: dict[str, str] = {}
    for n in names:
        url = n if n.startswith("http") else f"{BASE}/{n.lstrip('/')}"
        try:
            js = f.get(url).text
        except Exception as e:
            print(f"  skip    {n}: {e}")
            continue
        bundles[url] = js
        save_raw("rbi", "js_bundle", js.encode(), "js", {"url": url})
        print(f"  fetched {n:44s} {len(js):>10,} bytes")

    # Angular lazy chunks: shared hash from runtime.js, ids referenced in main.js
    joined = "\n".join(bundles.values())
    hashes = CHUNK_HASH_RE.findall(joined)
    if hashes:
        suffix = hashes[0]
        ids = sorted(set(CHUNK_ID_RE.findall(joined)))
        print(f"\n  lazy chunk pattern <id>{suffix} -- {len(ids)} candidate id(s)")
        for cid in ids:
            url = f"{BASE}/{cid}{suffix}"
            if url in bundles:
                continue
            try:
                js = f.get(url).text          # 404s now fail fast, no backoff
            except Exception:
                continue
            bundles[url] = js
            save_raw("rbi", "js_bundle", js.encode(), "js", {"url": url})
            print(f"  fetched chunk {cid:>8s} {len(js):>10,} bytes")

    return bundles


def extract_services(bundles: dict[str, str]) -> dict[str, dict]:
    """Every service name plus surrounding code, kept as a parameter hint."""
    found: dict[str, dict] = {}
    for url, js in bundles.items():
        for rx in (SERVICE_RE, NAME_RE):
            for m in rx.finditer(js):
                name = m.group(1)
                if name in found:
                    continue
                lo, hi = max(0, m.start() - 600), min(len(js), m.end() + 600)
                found[name] = {"bundle": url.rsplit("/", 1)[-1], "context": js[lo:hi]}
    return found


def guess_params(context: str) -> list[str]:
    """Likely request-body keys from the code around a service name."""
    keys = set(re.findall(r"[\"']?([a-z][A-Za-z0-9]{2,25})[\"']?\s*:", context))
    noise = {
        "function", "return", "this", "var", "let", "const", "type", "default",
        "value", "class", "style", "id", "name", "key", "then", "catch", "url",
        "headers", "method", "data", "body", "next", "error", "subscribe",
        "push", "length", "prototype", "call", "apply", "map", "filter", "get",
        "set", "new", "for", "if", "else", "case", "break", "null", "true",
        "false", "void", "typeof", "async", "await", "import", "export",
    }
    return sorted(k for k in keys if k not in noise)


def classify(name: str) -> str:
    """Rough bucket so the catalog is skimmable."""
    n = name.lower()
    if any(t in n for t in ("download", "csv", "excel", "pdf", "file", "export")):
        return "file/export"
    if any(t in n for t in ("faq", "aboutus", "contactus", "feedback", "alert",
                            "subscri", "user", "login", "otp", "captcha", "email")):
        return "site/account"
    if any(t in n for t in ("dashboard", "graph", "chart", "popular", "whatsnew",
                            "search", "menu", "ddl", "dropdown", "tab")):
        return "ui/navigation"
    return "data"


def main():
    f = Fetcher(base_headers={"Referer": BASE + "/"}, min_delay=0.3)

    print("Fetching JS bundles...")
    bundles = fetch_bundles(f)
    if not bundles:
        return 1

    print("\nExtracting service names...")
    services = extract_services(bundles)
    if not services:
        print("EMPTY -- no service names in the bundles. Do NOT guess names;")
        print("capture fresh cURLs from DevTools > Network > Fetch/XHR instead.")
        return 1

    catalog = {}
    for name, info in sorted(services.items()):
        catalog[name] = {
            "endpoint": f"/CIMS_Gateway_DBIE/GATEWAY/SERVICES/{name}",
            "kind": classify(name),
            "likely_params": guess_params(info["context"]),
            "bundle": info["bundle"],
        }

    OUT.write_text(json.dumps(catalog, indent=2))

    buckets: dict[str, list[str]] = {}
    for name, meta in catalog.items():
        buckets.setdefault(meta["kind"], []).append(name)

    print(f"\nFound {len(catalog)} services -> {OUT.name}\n")
    for kind in ("data", "ui/navigation", "file/export", "site/account"):
        names = buckets.get(kind, [])
        print(f"  {kind:16s} {len(names)}")
    print()
    for name in buckets.get("data", []):
        params = ", ".join(catalog[name]["likely_params"][:7])
        print(f"  {name:52s} {params[:70]}")

    print("\nNEXT: pick services, add them to SERVICES in connectors/rbi_dbie.py,")
    print("and call them with RBIClient.call().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
