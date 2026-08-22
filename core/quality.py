"""
Data quality: is the pipeline still telling the truth?

THE FAILURE THIS EXISTS TO CATCH
    A scraped pipeline rarely dies loudly. It stops, or a source quietly rewrites
    history, and the output still looks like data. BUILD_GUIDE puts it exactly
    right: a job that silently stops running looks identical to a market with no
    news. So three questions, none of which a connector can answer about itself:

      1. FRESHNESS   when did each source last actually deliver bytes?
      2. VOLUME      is the row count per run stable, or did it collapse?
      3. REVISION    did a value we already recorded change underneath us?

WHY REVISIONS ARE THE DANGEROUS ONE
    Freshness and volume are visible if you look. A silent revision is not: the
    number is present, plausible, and different from what you had. A backtest
    reading today's file gets figures nobody could have known at the time, and
    nothing errors.

    The archive makes this detectable. write_table() never overwrites, so each
    run leaves its own parquet vintage. Comparing vintages on a stable key shows
    exactly which (key, field) values moved and when.

    That is also why this compares PARQUET FILES rather than knowledge_date:
    several runs can share a date, and the interesting comparison is between
    successive writes.

KEYS ARE DECLARED, NOT GUESSED
    Revision detection needs to know what identifies a row. Inferring that from
    column names is the kind of plausible guess that produces confident nonsense,
    so DATASET_KEYS is an explicit registry built from real schemas. A dataset
    absent from it still gets freshness and volume monitoring; it just cannot be
    revision-checked until someone declares its key.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.storage import PARQUET, RAW

# Row identity per dataset, taken from the live schemas. Add a dataset here only
# after checking its columns -- a wrong key reports revisions that are really
# just two different rows colliding.
DATASET_KEYS: dict[str, list[str]] = {
    "rbi_forex_reserves":         ["week_ended", "component_code"],
    "ibja_rates":                 ["rate_date", "session", "metal_purity"],
    "cme_settlements":            ["product", "trade_date_requested", "month"],
    "derived_india_gold_premium": ["rate_date", "session"],
    "sec_us_fundamentals":        ["cik", "field", "period"],
    "nse_bhavcopy_delivery":      ["SYMBOL", "SERIES", "DATE1"],
    "nse_participant_oi":         ["trade_date", "Client Type"],
    "mcx_bhavcopy":               ["Symbol", "ExpiryDate", "InstrumentName",
                                   "StrikePrice", "OptionType", "trade_date"],
    "sec_sp500_constituents":     ["cusip"],
    "rbi_policy_rates":           ["rate_name"],
}

# Columns that are metadata about the fetch, not the observation itself. A
# change in these is not a revision.
NON_VALUE_COLUMNS = {"knowledge_date", "_vintage", "index_fetched_at",
                     "fetched", "source_url"}


def _utcnow():
    return datetime.now(timezone.utc)


def view_paths() -> dict[str, Path]:
    """Map each DuckDB view name back to its parquet directory."""
    out: dict[str, Path] = {}
    if not PARQUET.exists():
        return out
    for source_dir in PARQUET.iterdir():
        if not source_dir.is_dir():
            continue
        for ds_dir in source_dir.iterdir():
            if not ds_dir.is_dir() or not any(ds_dir.glob("*.parquet")):
                continue
            out[f"{source_dir.name}_{ds_dir.name}".replace("-", "_")] = ds_dir
    return out


# ------------------------------------------------------------------ freshness
def fetch_freshness() -> pd.DataFrame:
    """
    When each source last delivered bytes, read from the raw archive's sidecars.

    Deliberately measured on the RAW archive, not on parquet: parsing can succeed
    against a stale file, so "we have recent rows" does not prove "the source
    answered recently". Only the archive knows when bytes last arrived.
    """
    rows = []
    if not RAW.exists():
        return pd.DataFrame()

    for source_dir in RAW.iterdir():
        if not source_dir.is_dir():
            continue
        for ds_dir in source_dir.iterdir():
            if not ds_dir.is_dir():
                continue
            newest, n_files, total_bytes = None, 0, 0
            for meta_path in ds_dir.rglob("*.meta.json"):
                try:
                    meta = json.loads(meta_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                n_files += 1
                total_bytes += int(meta.get("bytes") or 0)
                ts = meta.get("fetched_at_utc")
                if ts and (newest is None or ts > newest):
                    newest = ts
            if not n_files:
                continue
            age = None
            if newest:
                try:
                    age = (_utcnow() - datetime.fromisoformat(newest)).total_seconds() / 3600
                except ValueError:
                    age = None
            rows.append({
                "source": source_dir.name,
                "dataset": ds_dir.name,
                "last_fetch_utc": newest,
                "age_hours": round(age, 1) if age is not None else None,
                "raw_files": n_files,
                "raw_mb": round(total_bytes / 1e6, 1),
            })

    df = pd.DataFrame(rows)
    return df.sort_values("age_hours", ascending=False, ignore_index=True) if not df.empty else df


# --------------------------------------------------------------------- volume
def row_count_trend(view: str, paths: dict[str, Path] | None = None) -> pd.DataFrame:
    """
    Rows written per run for one dataset, oldest first.

    The filename carries the write timestamp, so this is the per-run volume
    history without needing any extra bookkeeping.
    """
    paths = paths or view_paths()
    if view not in paths:
        return pd.DataFrame()
    import pyarrow.parquet as pq

    rows = []
    for f in sorted(paths[view].glob("*.parquet")):
        try:
            # Read the row count from parquet metadata rather than loading the
            # file. read_parquet(columns=[]) returns an EMPTY frame, so len()
            # on it reports 0 rows for every dataset -- plausible-looking and
            # completely wrong.
            n = pq.ParquetFile(f).metadata.num_rows
        except Exception:                                  # noqa -- unreadable vintage
            n = None
        rows.append({"vintage": f.name, "written": f.stat().st_mtime, "rows": n})
    df = pd.DataFrame(rows).sort_values("written", ignore_index=True)
    if not df.empty:
        df["written_utc"] = pd.to_datetime(df["written"], unit="s", utc=True)
        df["row_change"] = df["rows"].diff()
        # Flag a collapse rather than any decrease: legitimate variation is
        # large for daily files (holidays, half-days), so only a fall of more
        # than half against the previous run is worth surfacing.
        df["collapsed"] = (df["rows"] < df["rows"].shift() * 0.5).fillna(False)
    return df.drop(columns=["written"])


# ------------------------------------------------------------------ revisions
def detect_revisions(view: str, keys: list[str] | None = None,
                     paths: dict[str, Path] | None = None,
                     max_examples: int = 50) -> pd.DataFrame:
    """
    Keys whose recorded values CHANGED between runs -- silent revision.

    Compares successive parquet vintages on the declared key. Returns one row
    per changed key with the first and latest values of whichever fields moved,
    so the change is auditable rather than just counted.

    Returns an empty frame when there is only one vintage: a revision needs two
    observations, and reporting "no revisions" from a single file would be
    misleading rather than reassuring.

    WHAT A HIT ACTUALLY MEANS, and why it needs a human. This compares OUR
    successive writes, so a difference has two possible causes and the data
    cannot tell them apart:

        the SOURCE revised the value underneath us      -- the thing we fear
        OUR PARSER changed between the two runs         -- our own doing

    Both matter and both should be seen, but only the first is a data-integrity
    problem. In practice: check whether the connector changed between the two
    vintage timestamps before treating a hit as the source rewriting history.
    """
    paths = paths or view_paths()
    keys = keys or DATASET_KEYS.get(view)
    if view not in paths or not keys:
        return pd.DataFrame()

    files = sorted(paths[view].glob("*.parquet"), key=lambda p: p.stat().st_mtime)
    if len(files) < 2:
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception:                                  # noqa
            continue
        df["_vintage"] = f.name
        frames.append(df)
    if len(frames) < 2:
        return pd.DataFrame()

    allv = pd.concat(frames, ignore_index=True)
    missing = [k for k in keys if k not in allv.columns]
    if missing:
        raise KeyError(f"{view}: declared key column(s) {missing} not in the data")

    value_cols = [c for c in allv.columns
                  if c not in set(keys) | NON_VALUE_COLUMNS]
    if not value_cols:
        return pd.DataFrame()

    # Compare on a stable text rendering. Built column by column with
    # astype("string"): a plain astype(str) leaves nullable dtypes as floats and
    # the row-wise join then raises. A sentinel keeps NULL distinguishable from
    # the empty string, which matters -- "went missing" is a revision too.
    fingerprint = None
    for col in value_cols:
        part = allv[col].astype("string").fillna("\x00NULL")
        fingerprint = part if fingerprint is None else fingerprint.str.cat(part, sep="|")
    allv["_fingerprint"] = fingerprint

    grouped = allv.groupby(keys, dropna=False)
    versions = grouped["_fingerprint"].nunique()
    changed_keys = versions[versions > 1]
    if changed_keys.empty:
        return pd.DataFrame()

    changed = allv.set_index(keys).loc[changed_keys.index].reset_index()
    out = []
    for key_vals, grp in changed.groupby(keys, dropna=False):
        grp = grp.sort_values("_vintage")
        first, last = grp.iloc[0], grp.iloc[-1]
        moved = {c: (first[c], last[c]) for c in value_cols
                 if str(first[c]) != str(last[c])}
        if not moved:
            continue
        key_dict = dict(zip(keys, key_vals if isinstance(key_vals, tuple) else (key_vals,)))
        out.append({
            **key_dict,
            "fields_changed": ", ".join(sorted(moved)),
            "n_fields": len(moved),
            "first_vintage": first["_vintage"],
            "latest_vintage": last["_vintage"],
            "example_field": sorted(moved)[0],
            "example_before": moved[sorted(moved)[0]][0],
            "example_after": moved[sorted(moved)[0]][1],
        })
        if len(out) >= max_examples:
            break

    return pd.DataFrame(out)


# ------------------------------------------------------------------ the report
def quality_report(stale_after_hours: float = 48.0) -> pd.DataFrame:
    """
    One row per dataset: freshness, volume trend, revision status.

    `status` is the summary a human should read first:
        STALE      no bytes for longer than stale_after_hours
        COLLAPSED  the latest run wrote less than half the previous one
        REVISED    a previously recorded value changed between runs
        OK         none of the above
        (single vintage datasets report revisions as "n/a", not "none")
    """
    paths = view_paths()
    fresh = fetch_freshness()

    # Raw dataset names do not always match parquet dataset names -- CME
    # archives per product (cme/settle_gold) but writes one table
    # (cme/settlements). So: exact view match first, then fall back to the most
    # recent fetch for that SOURCE, which still answers "did this source
    # deliver anything recently".
    fresh_lookup, source_newest = {}, {}
    if not fresh.empty:
        for _, r in fresh.iterrows():
            view = f"{r['source']}_{r['dataset']}".replace("-", "_")
            fresh_lookup[view] = r
            prev = source_newest.get(r["source"])
            if prev is None or (r["last_fetch_utc"] or "") > (prev["last_fetch_utc"] or ""):
                source_newest[r["source"]] = r

    rows = []
    for view in sorted(paths):
        trend = row_count_trend(view, paths)
        n_vintages = len(trend)
        latest_rows = int(trend["rows"].iloc[-1]) if n_vintages and pd.notna(trend["rows"].iloc[-1]) else None
        collapsed = bool(trend["collapsed"].iloc[-1]) if n_vintages else False

        try:
            revs = detect_revisions(view, paths=paths)
            n_revised = len(revs)
            revision_state = "n/a" if n_vintages < 2 else str(n_revised)
        except KeyError as e:                              # declared key missing
            n_revised, revision_state = 0, f"key error: {e}"

        f = fresh_lookup.get(view)
        freshness_basis = "dataset"
        if f is None:
            f = source_newest.get(view.split("_", 1)[0])
            freshness_basis = "source" if f is not None else "none"
        age = float(f["age_hours"]) if f is not None and f["age_hours"] is not None else None

        if age is not None and age > stale_after_hours:
            status = "STALE"
        elif collapsed:
            status = "COLLAPSED"
        elif n_revised:
            status = "REVISED"
        else:
            status = "OK"

        rows.append({
            "view": view,
            "status": status,
            "rows_latest": latest_rows,
            "vintages": n_vintages,
            "row_change": (None if n_vintages < 2 or pd.isna(trend["row_change"].iloc[-1])
                           else int(trend["row_change"].iloc[-1])),
            "revisions": revision_state,
            "last_fetch_utc": None if f is None else f["last_fetch_utc"],
            "age_hours": age,
            "freshness_basis": freshness_basis,
            "revision_key": ", ".join(DATASET_KEYS.get(view, [])) or "(not declared)",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    order = {"STALE": 0, "COLLAPSED": 1, "REVISED": 2, "OK": 3}
    return df.sort_values(
        ["status", "view"], key=lambda s: s.map(order) if s.name == "status" else s,
        ignore_index=True)
