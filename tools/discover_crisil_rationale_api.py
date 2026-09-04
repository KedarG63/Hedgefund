"""
DISCOVERY ONLY -- find CRISIL's real rating-rationale search/list endpoint.

Not a connector. Per CLAUDE.md: never invent an endpoint, never guess. The
"Latest Rating Rationales" page (rating-rationale.html) renders a search form
(date range, company name, title) plus a "Load More" button that pages through
results -- classic sign of a background XHR/fetch call, per the SPA pattern
documented in BUILD_GUIDE.md. This script drives that page with Playwright and
logs every network response that looks like data (JSON, or XHR/fetch resource
type) so the real endpoint, its params, and its response shape can be read
off directly instead of guessed.

Output: crisil_rationale_api_capture.json in the repo root -- request url,
method, resource type, response status/content-type, and a truncated body
sample for anything that looks like data.

    python tools/discover_crisil_rationale_api.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from playwright.sync_api import sync_playwright

URL = "https://www.crisilratings.com/en/home/our-business/ratings/rating-rationale.html"
OUT = Path(__file__).resolve().parent.parent / "crisil_rationale_api_capture.json"

DATA_TYPES = {"xhr", "fetch"}


def main():
    captures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        def on_response(response):
            req = response.request
            if req.resource_type not in DATA_TYPES:
                return
            entry = {
                "url": response.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "post_data": req.post_data,
            }
            try:
                body = response.text()
                entry["body_sample"] = body[:2000]
            except Exception as exc:  # noqa: BLE001
                entry["body_sample"] = f"<unreadable: {exc}>"
            captures.append(entry)
            print(f"[{response.status}] {req.method} {response.url}")

        page.on("response", on_response)

        print(f"Navigating to {URL} ...")
        page.goto(URL, wait_until="networkidle", timeout=45000)

        # The page shows a form (date range, company name, rationale title)
        # plus a list with a "Load More" button. Try triggering both a
        # default listing fetch (already happened on goto) and pagination.
        try:
            load_more = page.get_by_text("Load More", exact=False)
            if load_more.count() > 0:
                print("Clicking 'Load More' ...")
                load_more.first.click()
                page.wait_for_timeout(4000)
        except Exception as exc:  # noqa: BLE001
            print(f"Load More click failed: {exc}")

        # Try a company-name search too, if the field exists.
        try:
            name_input = page.locator("input[type=text]").first
            if name_input.count() > 0:
                print("Typing a sample company name into the first text input ...")
                name_input.fill("Reliance")
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
        except Exception as exc:  # noqa: BLE001
            print(f"Search interaction failed: {exc}")

        browser.close()

    OUT.write_text(json.dumps(captures, indent=2), encoding="utf-8")
    print(f"\n{len(captures)} XHR/fetch responses captured -> {OUT}")


if __name__ == "__main__":
    main()
