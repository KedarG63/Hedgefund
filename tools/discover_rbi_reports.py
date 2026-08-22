"""
Enumerate every REPORT behind every DBIE screen.

This is the payoff from reproducing the payload cipher (core/rbi_crypto.py).
dbie_getReportsDbie takes AES-encrypted parameters, so before the cipher was
reproduced this service could not be called at all; now it can, and it yields
for each of the 65 screens the reports RBI holds -- with reportId, publication
frequency, and the FULL date range of available history.

That range is the useful part. It tells you what a backfill is worth before you
write a line of parser: Key Rates runs from 1935, Daily LAF Operation from 2001.

    python tools/discover_rbi_reports.py

WHAT THIS DOES NOT DO
    It does not fetch report DATA. Report rendering goes through SAP
    BusinessObjects (/BOE/OpenDocument/), and dbie_getReportLink returns
    errorCode 10000 for an anonymous caller because login_getSapToken hands back
    sapLogonToken: null. Getting the series themselves needs that session --
    see the note in connectors/rbi_dbie.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import pandas as pd

from connectors.rbi_dbie import RBIClient
from core.storage import write_table

REPO = Path(__file__).resolve().parent.parent
SCREENS = REPO / "rbi_screen_catalog.json"
OUT = REPO / "rbi_report_catalog.json"


def main():
    if not SCREENS.exists():
        print(f"{SCREENS.name} missing -- run tools/discover_rbi_screens.py first.")
        return 1

    screens = json.loads(SCREENS.read_text(encoding="utf-8"))["screens"]
    rbi = RBIClient()

    rows, failed = [], []
    for s in screens:
        parts = s["breadcrumb"].split(" > ")
        if len(parts) < 3:
            continue
        function, department, menu = parts[0], parts[1], parts[-1]
        try:
            found = rbi.reports_for(function, department, menu)
        except Exception as e:                      # noqa -- record, keep walking
            failed.append({"breadcrumb": s["breadcrumb"], "error": str(e)[:120]})
            continue
        rows.extend(found)
        print(f"  {len(found):>3} reports   {s['breadcrumb'][:78]}")

    if not rows:
        print("\nNo reports found. Check that core.rbi_crypto.cipher().verify() passes.")
        return 1

    df = pd.DataFrame(rows).drop_duplicates(subset=["report_id"])
    OUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    write_table(df, "rbi", "report_catalog")

    print(f"\n{len(df)} distinct reports across {len(screens)} screens -> {OUT.name}")
    if failed:
        print(f"{len(failed)} screen(s) errored:")
        for x in failed[:5]:
            print(f"   {x['breadcrumb'][:60]}: {x['error'][:70]}")

    print("\nLongest histories:")
    dated = df[df["from_date"].notna()].copy()
    dated["_y"] = dated["from_date"].astype(str).str[-4:]
    for _, r in dated.sort_values("_y").head(15).iterrows():
        print(f"   {str(r['report_id']):>6}  {str(r['frequency'] or ''):12s} "
              f"{r['from_date']}..{r['to_date']}  {str(r['report_name'])[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
