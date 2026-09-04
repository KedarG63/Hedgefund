"""
DISCOVERY ONLY -- find CARE/CareEdge's real rating list/search endpoint.

Not a connector. Same rationale as the CRISIL/ICRA discovery scripts: never
invent an endpoint. careratings.com's "Find Ratings" page and its Press
Release listing were located by hand (WebFetch on careratings.com's nav) --
this script drives both with Playwright and logs network responses so the
real endpoint(s), params, and response shape can be read off directly instead
of guessed. careratingtracker.com (a separate, apparently paid product behind
login) is deliberately NOT probed here.

    python tools/discover_care_rationale_api.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from playwright.sync_api import sync_playwright

URLS = [
    "https://www.careratings.com/find-ratings",
    "https://www.careratings.com/industry/press-release/24",
]
OUT = Path(__file__).resolve().parent.parent / "care_rationale_api_capture.json"

DATA_TYPES = {"xhr", "fetch"}


def capture(page, url, captures):
    def on_response(response):
        req = response.request
        if req.resource_type not in DATA_TYPES:
            return
        entry = {
            "page": url,
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
    print(f"Navigating to {url} ...")
    page.goto(url, wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(2500)
    page.remove_listener("response", on_response)


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
        for url in URLS:
            try:
                capture(page, url, captures)
            except Exception as exc:  # noqa: BLE001
                print(f"Failed on {url}: {exc}")

        # On find-ratings, try a company-name search if a text input exists.
        try:
            page.goto(URLS[0], wait_until="networkidle", timeout=45000)
            inp = page.locator("input[type=text], input[type=search]").first
            if inp.count() > 0:
                print("Typing a sample company name into find-ratings search ...")
                inp.fill("Tata")
                page.wait_for_timeout(1500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)
        except Exception as exc:  # noqa: BLE001
            print(f"find-ratings search interaction failed: {exc}")

        browser.close()

    OUT.write_text(json.dumps(captures, indent=2), encoding="utf-8")
    print(f"\n{len(captures)} XHR/fetch responses captured -> {OUT}")


if __name__ == "__main__":
    main()
