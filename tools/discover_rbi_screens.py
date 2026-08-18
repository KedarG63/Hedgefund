"""
Enumerate every DBIE SCREEN from the site's own navigation service.

Complements discover_rbi_services.py, which enumerates the API surface. This
enumerates the *content* surface -- what data screens exist -- by calling
dbie_menuMappingList, which needs no parameters beyond a session token.

WHY IT MATTERS
    The screen catalog tells you where each series lives, which is what you need
    to navigate to before capturing a cURL. Verified 2026-08-18: the four series
    that DBIE hides behind its encrypted query engine live at

        /dbie/indicators  > Financial Sector Indicators > Daily LAF Operation
        /dbie/indicators  > Financial Sector Indicators > Key Rates
        /dbie/indicators  > Financial Sector Indicators > Business of Scheduled Banks
        /dbie/statistics  > Financial Sector > Banking - Sectoral Statistics

WHAT IT DOES NOT GIVE YOU
    Leaf nodes carry ONLY {title, titleHi} -- no id, no url, no element code.
    The title -> elementCodes mapping is resolved at click time, so this catalog
    cannot be turned into working requests on its own. That is why a captured
    cURL is still required for those screens: the encrypted elementCodes in the
    request body cannot be derived from anything served up-front.

    python tools/discover_rbi_screens.py
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from connectors.rbi_dbie import RBIClient
from core.storage import save_raw

OUT = Path(__file__).resolve().parent.parent / "rbi_screen_catalog.json"


def flatten(nodes: list, path: tuple = ()) -> list[dict]:
    """Depth-first walk to leaf screens, keeping the breadcrumb."""
    rows = []
    for n in nodes:
        title = n.get("title", "?")
        here = path + (title,)
        kids = n.get("subTitle")
        if kids:
            rows.extend(flatten(kids, here))
        else:
            rows.append({
                "screen": title,
                "breadcrumb": " > ".join(here),
                "section": here[0] if here else None,
                "route": None,          # leaves carry no route; see module docstring
            })
    return rows


def main():
    rbi = RBIClient()
    payload = rbi.call("dbie_menuMappingList", {})
    menu = payload["body"]["menuList"]

    # Section-level nodes DO carry id + url; attach them to their descendants.
    routes = {n.get("title"): n.get("url") for n in menu}

    rows = flatten(menu)
    for r in rows:
        r["route"] = routes.get(r["section"])

    catalog = {
        "sections": [{"title": n.get("title"), "id": n.get("id"), "url": n.get("url")}
                     for n in menu],
        "screens": rows,
    }
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{len(rows)} screens across {len(menu)} sections -> {OUT.name}\n")
    current = None
    for r in rows:
        top = " > ".join(r["breadcrumb"].split(" > ")[:2])
        if top != current:
            current = top
            print(f"  {top}   [{r['route']}]")
        print(f"      {r['screen']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
