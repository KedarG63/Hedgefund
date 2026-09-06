"""
Where a number came from: view -> upstream views -> archived bytes.

This is what rule 1 was paying for. Every fetch writes unchanged bytes to
data/raw/ with a sidecar recording the URL, the fetch timestamp and a sha256,
BEFORE anything parses them -- so unlike almost any other research stack, a
figure on screen can be walked back to the exact bytes a server returned and
the moment they arrived.

TWO KINDS OF EDGE, and they are not interchangeable:

  RAW_DATASETS  a connector-sourced view -> the raw dataset(s) it parses.
                Extracted from the connectors' own save_raw()/write_table()
                literals, not guessed.

  UPSTREAM      a computed view -> the views it was calculated from. Also read
                out of analytics/, from core.asof calls and FROM clauses.

WHY DECLARED RATHER THAN INFERRED AT RUNTIME. A wrong provenance link is worse
than a missing one: it points a reader at bytes that did not produce the number
in front of them, which is a confident lie rather than an absence. Declared
here, a bad edge is a reviewable diff.

WHERE THIS IS DELIBERATELY IMPRECISE, and says so. Several analytics modules
write more than one table and read a shared set of views, so the upstream list
is per-MODULE, not per-table -- an over-approximation. Over-inclusion is the
safe direction: it can send someone to bytes that were not used, never hide
bytes that were. `approximate` marks those.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.storage import PARQUET, RAW

# --------------------------------------------------------------- raw datasets
# view -> raw dataset paths, from connectors' save_raw()/write_table() literals.
# Only entries whose names DIFFER from the view need listing; a matching name
# resolves automatically in raw_datasets_for().
RAW_DATASETS: dict[str, list[str]] = {
    "care_rating_actions": ["care/rating_rationale_listing"],
    "screener_financial_facts": ["screener/export"],
    "rbi_forex_reserves": ["rbi/dbie_foreignExchangeReserves"],
    "rbi_key_indicators": ["rbi/nsdp_policy_rates", "rbi/policy_rates"],
    "rbi_money_market_operations": ["rbi/press_release"],
    "rbi_press_index": ["rbi/press_release_index"],
    "sec_13f_holdings": ["sec/13f_information_table"],
    "sec_13f_notices": ["sec/13f_information_table"],
    "sec_13d_filings": ["sec/13d"],
    "sec_13d_persons": ["sec/13d"],
    "sec_13g_filings": ["sec/13g"],
    "sec_13g_persons": ["sec/13g"],
    "sec_8k_items": ["sec/filing_index"],
    "sec_insider_transactions": ["sec/insider_bulk"],
    "sec_us_fundamentals": ["sec/companyfacts", "sec/companyfacts_bulk"],
    "sec_companyfacts_restatements": ["sec/companyfacts", "sec/companyfacts_bulk"],
    "sec_sp500_constituents": ["sec/ticker_map"],
}

# Views whose raw dataset this repo has NOT established. Listed explicitly so
# the API can say "not resolved" instead of implying there are no bytes.
UNRESOLVED_HINT = (
    "No raw dataset is declared for this view. It is either computed from "
    "other views, or its connector splits fetch and parse across functions so "
    "the link could not be read from the code. Nothing is being guessed."
)

# ------------------------------------------------------------------- upstream
# computed view -> views it was calculated from. Read out of analytics/.
UPSTREAM: dict[str, list[str]] = {
    "derived_capm_beta": ["nse_bhavcopy"],
    "derived_correlation": ["nse_bhavcopy"],
    "derived_pairs_candidates": ["nse_bhavcopy"],
    "derived_momentum_zscore": ["nse_bhavcopy"],
    "derived_variance_ratio": ["nse_bhavcopy"],
    "derived_corporate_events": ["bse_announcements", "bse_scrip_master"],
    "derived_fii_source_divergence": ["nse_participant_oi", "upstox_fii_activity"],
    "derived_option_greeks": ["nse_optchain", "rbi_key_indicators"],
    "derived_factor_model": ["derived_momentum_zscore", "derived_size_factor",
                             "derived_low_vol_factor"],
    "derived_size_factor": ["bse_scrip_master", "nse_bhavcopy"],
    "derived_low_vol_factor": ["nse_bhavcopy"],
    "derived_digest": ["derived_capm_beta", "derived_momentum_zscore",
                       "derived_bulk_block_anomaly"],
    "derived_multivariate_outliers": ["derived_capm_beta", "derived_momentum_zscore",
                                      "derived_bulk_block_anomaly",
                                      "derived_promoter_holding_change"],
    "derived_india_gold_premium": ["ibja_rates", "cme_settlements", "mcx_bhavcopy"],
    "analytics_chokepoint_zscore": ["portwatch_chokepoints"],
    "analytics_current_regime": ["portwatch_chokepoints"],
    "analytics_regime_breaks": ["portwatch_chokepoints"],
    "analytics_reroute_balance": ["portwatch_chokepoints"],
    "analytics_monsoon_departure": ["cpc_india_rainfall", "imd_gridded_rainfall"],
    "analytics_monsoon_progress": ["cpc_india_rainfall"],
    "analytics_imd_normals": ["imd_gridded_rainfall"],
    "analytics_enso_state": ["noaa_oni"],
}

# These four share analytics/events.py, which reads a common set of views, so
# the upstream list is the MODULE's inputs rather than each table's. Marked so
# the panel can say so rather than overstating precision.
APPROXIMATE = {
    "derived_bulk_block_anomaly", "derived_fii_dii_divergence",
    "derived_insider_filing_frequency", "derived_promoter_holding_change",
}
for _v in APPROXIMATE:
    UPSTREAM.setdefault(_v, ["nse_bulk_deals", "nse_block_deals",
                             "nse_insider_trading", "nse_participant_oi",
                             "nse_shareholding_pattern"])


def raw_datasets_for(view: str) -> list[str]:
    """Raw dataset paths behind a view: declared first, then a name match."""
    if view in RAW_DATASETS:
        return RAW_DATASETS[view]
    source, _, dataset = view.partition("_")
    if source in ("derived", "analytics"):
        return []
    if (RAW / source / dataset).is_dir():
        return [f"{source}/{dataset}"]
    return []


def raw_files(view: str, limit: int = 25) -> list[dict]:
    """
    Archived files behind a view, newest first, each with its sidecar.

    The sidecar is the point: fetched_at_utc says WHEN a server said this, and
    sha256 lets a reader prove the bytes on disk are the bytes that arrived.
    """
    out: list[dict] = []
    for rel in raw_datasets_for(view):
        folder = RAW / rel
        if not folder.is_dir():
            continue
        files = [p for p in folder.rglob("*")
                 if p.is_file() and not p.name.endswith(".meta.json")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:limit]:
            meta = {}
            side = path.with_suffix(path.suffix + ".meta.json")
            if side.exists():
                try:
                    meta = json.loads(side.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
            out.append({
                "raw_dataset": rel,
                "path": str(path.relative_to(RAW)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "fetched_at_utc": meta.get("fetched_at_utc"),
                "sha256": meta.get("sha256"),
                "source_url": meta.get("url") or meta.get("source_url"),
                "meta": {k: v for k, v in meta.items()
                         if k not in ("sha256", "fetched_at_utc", "bytes")},
            })
    return out[:limit]


def parquet_vintages(view: str, limit: int = 25) -> list[dict]:
    """The parsed vintages of a view -- the middle of the chain."""
    source, _, dataset = view.partition("_")
    folder = PARQUET / source / dataset
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.parquet"), key=lambda p: p.name, reverse=True)
    return [{"file": p.name, "bytes": p.stat().st_size} for p in files[:limit]]


def trace(view: str, _seen: set[str] | None = None, depth: int = 0) -> dict:
    """
    The full chain from a view back to archived bytes.

    Depth-first over UPSTREAM, cycle-guarded. A computed view resolves through
    its inputs; a connector-sourced view terminates in raw files.
    """
    seen = _seen if _seen is not None else set()
    node = {
        "view": view,
        "depth": depth,
        "computed": view.startswith(("derived_", "analytics_")),
        "approximate": view in APPROXIMATE,
        # `repeated`, not `cycle`. This graph is a DAG with diamonds --
        # derived_factor_model reaches nse_bhavcopy through three different
        # children -- so a revisit is normal and expected, not a defect. It is
        # marked and not re-expanded purely to keep the tree from blowing up
        # exponentially; the panel renders it as a back-reference.
        "repeated": view in seen,
        "truncated": depth > 6,
        "vintages": [],
        "raw": [],
        "upstream": [],
    }
    if node["repeated"] or node["truncated"]:
        # Same SHAPE as every other node. A consumer that has to branch on
        # which keys exist is a consumer that will crash on the branch nobody
        # tested -- which is exactly how this was found.
        return node

    seen.add(view)
    node["vintages"] = parquet_vintages(view, limit=5)
    node["raw"] = raw_files(view, limit=5)
    node["upstream"] = [trace(u, seen, depth + 1) for u in UPSTREAM.get(view, [])]
    return node


def read_raw(rel_path: str, max_bytes: int = 4096) -> dict:
    """
    A bounded preview of one archived file.

    Bounded because the archive holds a 1.4 GB SEC bulk file, and because the
    point is to SEE the bytes, not to move them. Path is resolved against the
    archive root and checked to still be inside it -- these names reach the API
    from a URL.
    """
    root = RAW.resolve()
    path = (root / rel_path).resolve()
    if not str(path).startswith(str(root)):
        raise ValueError("path escapes the raw archive")
    if not path.is_file():
        raise FileNotFoundError(rel_path)

    size = path.stat().st_size
    head = path.read_bytes()[:max_bytes] if size else b""
    try:
        text, binary = head.decode("utf-8"), False
    except UnicodeDecodeError:
        text, binary = head.hex(), True

    return {"path": rel_path, "bytes": size, "truncated": size > max_bytes,
            "binary": binary, "preview": text}
