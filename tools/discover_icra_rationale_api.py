"""
DISCOVERY ONLY -- find ICRA's real rating-rationale search/list endpoint.

Not a connector. Same rationale as tools/discover_crisil_rationale_api.py:
never invent an endpoint. icra.in/Rating/AllRatingRationales renders a search
form (entity, sector, date range) over two paginated tables (corporate/
financial-sector rationales, and structured-finance rationales) -- classic
sign of a background XHR/fetch call. This script drives that page with
Playwright and logs every network response that looks like data so the real
endpoint, its params, and its response shape can be read off directly.

    python tools/discover_icra_rationale_api.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from playwright.sync_api import sync_playwright

URL = "https://www.icra.in/Rating/AllRatingRationales"
OUT = Path(__file__).resolve().parent.parent / "icra_rationale_api_capture.json"

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
        page.wait_for_timeout(2000)

        # Try pagination (page 2) if a numbered link exists.
        try:
            page2 = page.get_by_text("2", exact=True)
            if page2.count() > 0:
                print("Clicking page '2' ...")
                page2.first.click()
                page.wait_for_timeout(3000)
        except Exception as exc:  # noqa: BLE001
            print(f"Pagination click failed: {exc}")

        # Try an entity search.
        try:
            entity_input = page.locator("input[type=text]").first
            if entity_input.count() > 0:
                print("Typing a sample entity name ...")
                entity_input.fill("Tata")
                page.wait_for_timeout(1500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)
        except Exception as exc:  # noqa: BLE001
            print(f"Entity search failed: {exc}")

        browser.close()

    OUT.write_text(json.dumps(captures, indent=2), encoding="utf-8")
    print(f"\n{len(captures)} XHR/fetch responses captured -> {OUT}")


if __name__ == "__main__":
    main()
