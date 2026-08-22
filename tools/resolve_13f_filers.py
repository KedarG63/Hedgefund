"""
Resolve firm names to the CIKs that actually file 13F-HR.

WHY THIS EXISTS
    Matching a firm name to a single CIK by hand is wrong often enough to be
    dangerous. Large managers file under whole entity trees, and the entity
    whose name looks most like the firm is frequently NOT the one holding the
    portfolio:

        GEODE CAPITAL MANAGEMENT TRUST COMPANY, LLC  -> 13F-NT only
        GEODE CAPITAL MANAGEMENT, LLC                -> 13F-HR   <- the portfolio
        Two Sigma Blazar Portfolio, LLC              -> 13F-NT only
        TWO SIGMA INVESTMENTS, LP                    -> 13F-HR   <- the portfolio
        PERSHING SQUARE HOLDCO, L.P.                 -> HR, but a holdco wrapper
        Pershing Square Capital Management, L.P.     -> 13F-HR   <- the manager

    Picking an NT filer yields a silent empty portfolio, which reads as a
    manager holding nothing rather than as a configuration error.

WHAT IT DOES
    Scans SEC's own quarterly filing index (already archived locally) for every
    13F-HR filer whose name matches each pattern, and reports them ranked by
    filing count so the real portfolio entity is obvious. It PRINTS candidates
    for a human to curate -- it does not silently rewrite the config, because
    choosing which legal entity represents a firm is a judgement call.

    python tools/resolve_13f_filers.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import pandas as pd

from connectors.sec_edgar import filing_index

# Firm -> substring to look for in the index's entity name (upper-cased).
FIRMS = {
    "BlackRock": "BLACKROCK",
    "Vanguard": "VANGUARD",
    "State Street": "STATE STREET",
    "FMR (Fidelity)": "FMR ",
    "Capital Research": "CAPITAL RESEARCH",
    "T. Rowe Price": "PRICE T ROWE",
    "JPMorgan": "JPMORGAN",
    "Geode": "GEODE",
    "Morgan Stanley": "MORGAN STANLEY",
    "Wellington": "WELLINGTON",
    "Invesco": "INVESCO",
    "Northern Trust": "NORTHERN TRUST",
    "Bridgewater": "BRIDGEWATER",
    "Citadel": "CITADEL",
    "Millennium": "MILLENNIUM",
    "Elliott": "ELLIOTT",
    "Renaissance": "RENAISSANCE TECH",
    "Two Sigma": "TWO SIGMA",
    "D. E. Shaw": "SHAW",
    "Man Group": "MAN GROUP",
    "AQR": "AQR",
    "Tiger Global": "TIGER GLOBAL",
    "Viking Global": "VIKING GLOBAL",
    "Lone Pine": "LONE PINE",
    "Pershing Square": "PERSHING SQUARE",
    "Berkshire Hathaway": "BERKSHIRE",
    "NVIDIA": "NVIDIA",
    "Alphabet": "ALPHABET",
    "Apple": "APPLE INC",
}

QUARTERS = [(2025, 2), (2025, 3)]


def main():
    frames = []
    for year, q in QUARTERS:
        try:
            frames.append(filing_index(year, q))
        except Exception as e:                       # noqa
            print(f"  skip {year}Q{q}: {type(e).__name__}")
    if not frames:
        print("No filing index available.")
        return 1

    idx = pd.concat(frames, ignore_index=True)
    f13 = idx[idx["form"].str.startswith("13F")].copy()
    f13["upper"] = f13["entity_idx"].str.upper()

    for firm, pattern in FIRMS.items():
        m = f13[f13["upper"].str.contains(pattern, regex=False, na=False)]
        print(f"\n=== {firm} ===")
        if m.empty:
            print("   NO 13F filings found")
            continue

        summary = (m.groupby(["cik", "entity_idx"])
                    .agg(forms=("form", lambda s: ",".join(sorted(set(s)))),
                         n=("accn", "nunique"),
                         last=("filed", "max"))
                    .reset_index())
        summary["has_hr"] = summary["forms"].str.contains("13F-HR")
        summary = summary.sort_values(["has_hr", "n"], ascending=[False, False])

        for _, r in summary.head(6).iterrows():
            flag = "HR" if r["has_hr"] else "NT-only  <-- carries NO holdings"
            print(f"   {'*' if r['has_hr'] else ' '} CIK {r['cik']:<10} "
                  f"{str(r['entity_idx'])[:44]:46s} {r['forms']:24s} n={r['n']} {flag}")
    print("\n* = files 13F-HR. Curate config/filers_13f.json from these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
