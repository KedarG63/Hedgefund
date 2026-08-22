"""
Immutable raw archive + parquet warehouse.

THE ONE RULE: every byte you fetch is written to the raw archive, unchanged,
with a fetch timestamp, BEFORE anything parses it. You parse from the archive,
never from the live web.

Why: when you find a parser bug in 2028 (you will), you re-parse 2026 history.
If you only kept parsed output, that history is gone forever -- government
portals overwrite themselves.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(os.environ.get("QUANTDATA_ROOT", Path(__file__).resolve().parent.parent / "data"))
RAW = ROOT / "raw"
PARQUET = ROOT / "parquet"
DB_PATH = ROOT / "warehouse.duckdb"


def _utcnow():
    return datetime.now(timezone.utc)


def _unique_path(folder: Path, stem: str, ext: str) -> Path:
    """
    A path that does not already exist. Timestamps alone are not unique -- a
    backfill loop issues many writes inside one second, and an overwrite in this
    layer is silent, permanent data loss. Suffix -1, -2, ... until free.
    """
    path = folder / f"{stem}.{ext}"
    n = 0
    while path.exists():
        n += 1
        path = folder / f"{stem}-{n}.{ext}"
    return path


def save_raw(source: str, dataset: str, content: bytes, ext: str = "bin", meta: dict | None = None) -> Path:
    """
    Write raw bytes to  raw/<source>/<dataset>/<YYYY-MM-DD>/<HHMMSS>_<sha8>.<ext>
    Returns the path. Never overwrites. Sidecar .meta.json records provenance.
    """
    now = _utcnow()
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M%S")
    sha8 = hashlib.sha256(content).hexdigest()[:8]

    folder = RAW / source / dataset / day
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique_path(folder, f"{stamp}_{sha8}", ext)
    path.write_bytes(content)

    sidecar = {
        "source": source,
        "dataset": dataset,
        "fetched_at_utc": now.isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        **(meta or {}),
    }
    path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(sidecar, indent=2))
    return path


def latest_raw(source: str, dataset: str) -> Path | None:
    """Most recent raw file for a dataset."""
    folder = RAW / source / dataset
    if not folder.exists():
        return None
    files = [p for p in folder.rglob("*") if p.is_file() and not p.name.endswith(".meta.json")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def save_raw_stream(source: str, dataset: str, chunks, ext: str = "bin",
                    meta: dict | None = None) -> Path:
    """
    Streaming variant of save_raw for payloads too large to hold in memory.

    `chunks` is any iterable of bytes (e.g. httpx's iter_bytes()). SEC's
    companyfacts bulk file is 1.4 GB; buffering that just to hash it wastes a
    gigabyte for nothing. The sha256 is computed incrementally as bytes land.

    Same guarantees as save_raw: never overwrites, writes a provenance sidecar.
    """
    now = _utcnow()
    folder = RAW / source / dataset / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    # Write to a .part file first: a half-downloaded file that looks complete is
    # worse than no file, because the archive is meant to be trustworthy.
    tmp = folder / f".{now.strftime('%H%M%S')}.part"
    with tmp.open("wb") as fh:
        for chunk in chunks:
            if not chunk:
                continue
            fh.write(chunk)
            digest.update(chunk)
            size += len(chunk)

    path = _unique_path(folder, f"{now.strftime('%H%M%S')}_{digest.hexdigest()[:8]}", ext)
    tmp.replace(path)

    sidecar = {
        "source": source,
        "dataset": dataset,
        "fetched_at_utc": now.isoformat(),
        "sha256": digest.hexdigest(),
        "bytes": size,
        **(meta or {}),
    }
    path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(sidecar, indent=2))
    return path


def find_raw(source: str, dataset: str, **match) -> Path | None:
    """
    Most recent archived file whose sidecar matches every key=value in `match`.

    Lets a connector parse from the archive instead of refetching bytes it
    already holds -- which is rule 1's intent, not just a nicety: SEC's
    quarterly filing index is ~55 MB, and a completed quarter never changes.
    """
    folder = RAW / source / dataset
    if not folder.exists():
        return None
    best = None
    for meta_path in folder.rglob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if any(meta.get(k) != v for k, v in match.items()):
            continue
        data_path = meta_path.with_name(meta_path.name[: -len(".meta.json")])
        if not data_path.exists():
            continue
        if best is None or data_path.stat().st_mtime > best.stat().st_mtime:
            best = data_path
    return best


def write_table(df, source: str, dataset: str, partition_col: str | None = None):
    """
    Write a parsed DataFrame to the parquet warehouse.
    Always stamps knowledge_date = now. This is what makes your data point-in-time.
    """
    import pandas as pd  # noqa

    df = df.copy()
    df["knowledge_date"] = _utcnow().date().isoformat()
    out = PARQUET / source / dataset
    out.mkdir(parents=True, exist_ok=True)
    # Microseconds + collision guard: a backfill loop writes many vintages per
    # second, and second-resolution names would silently overwrite each other.
    path = _unique_path(out, _utcnow().strftime("%Y%m%dT%H%M%S_%f"), "parquet")
    df.to_parquet(path, index=False)
    return path


def db():
    """DuckDB connection. Query parquet directly -- no ETL into tables needed."""
    ROOT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    return con


def register_views(con):
    """Expose every parquet dataset as a DuckDB view: <source>_<dataset>."""
    if not PARQUET.exists():
        return []
    made = []
    for source_dir in PARQUET.iterdir():
        if not source_dir.is_dir():
            continue
        for ds_dir in source_dir.iterdir():
            if not ds_dir.is_dir():
                continue
            view = f"{source_dir.name}_{ds_dir.name}".replace("-", "_")
            con.execute(
                f"CREATE OR REPLACE VIEW {view} AS "
                f"SELECT * FROM read_parquet('{ds_dir}/*.parquet', union_by_name=true)"
            )
            made.append(view)
    return made
