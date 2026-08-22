"""
Smoke tests for the storage layer -- the two rules that govern everything.

    RULE 1  every fetched byte is archived, unchanged, before anything parses it
    RULE 2  every parsed row carries knowledge_date

Plus the property that makes both rules mean anything: this layer NEVER
overwrites. The raw archive cannot be rebuilt from anywhere, so a silent
clobber is permanent loss of a vintage.

    python -m pytest tests/ -v
"""
import importlib
import json

import pandas as pd
import pytest


@pytest.fixture
def s(tmp_path, monkeypatch):
    """core.storage rebound to a throwaway root (it reads ROOT at import time)."""
    monkeypatch.setenv("QUANTDATA_ROOT", str(tmp_path))
    import core.storage as storage
    importlib.reload(storage)
    yield storage
    importlib.reload(storage)


@pytest.fixture
def con(s):
    c = s.db()
    yield c
    c.close()


# ------------------------------------------------------------ RULE 1: raw archive
def test_save_raw_roundtrips_bytes_unchanged(s):
    blob = b'\x00\x01binary\xff{"not":"utf8 safe"}'
    p = s.save_raw("rbi", "wss", blob, ext="bin")
    assert p.read_bytes() == blob, "archive must return the exact bytes fetched"


def test_save_raw_writes_provenance_sidecar(s):
    blob = b"payload"
    p = s.save_raw("nse", "bhavcopy", blob, ext="csv", meta={"url": "https://x.invalid/a.csv"})
    side = json.loads(p.with_suffix(p.suffix + ".meta.json").read_text())

    assert side["source"] == "nse"
    assert side["dataset"] == "bhavcopy"
    assert side["bytes"] == len(blob)
    assert side["url"] == "https://x.invalid/a.csv", "caller meta must survive"
    # the fetch timestamp is what makes the archive a point-in-time record
    assert side["fetched_at_utc"].endswith("+00:00"), "timestamp must be tz-aware UTC"
    import hashlib
    assert side["sha256"] == hashlib.sha256(blob).hexdigest()


def test_save_raw_partitions_by_source_dataset_day(s):
    p = s.save_raw("cme", "settlements", b"x", ext="json")
    rel = p.relative_to(s.RAW).parts
    assert rel[0] == "cme" and rel[1] == "settlements"
    assert len(rel[2]) == len("YYYY-MM-DD")


def test_save_raw_never_overwrites(s):
    """Identical bytes in the same second must still produce distinct files."""
    paths = [s.save_raw("nse", "probe", b"same", ext="txt", meta={"attempt": i}) for i in range(5)]
    assert len(set(paths)) == 5, "archive clobbered a vintage"
    assert all(p.exists() for p in paths)
    # each keeps its own provenance
    attempts = [json.loads(p.with_suffix(p.suffix + ".meta.json").read_text())["attempt"] for p in paths]
    assert sorted(attempts) == [0, 1, 2, 3, 4]


def test_latest_raw(s):
    assert s.latest_raw("nse", "never_fetched") is None
    s.save_raw("nse", "bhavcopy", b"old", ext="csv")
    newest = s.save_raw("nse", "bhavcopy", b"new", ext="csv")
    assert s.latest_raw("nse", "bhavcopy") == newest


def test_latest_raw_ignores_sidecars(s):
    s.save_raw("nse", "bhavcopy", b"data", ext="csv")
    assert not s.latest_raw("nse", "bhavcopy").name.endswith(".meta.json")


# ------------------------------------------------------------ RULE 2: knowledge_date
def test_write_table_stamps_knowledge_date(s):
    from datetime import datetime, timezone
    df = pd.DataFrame({"symbol": ["RELIANCE", "TCS"], "close": [1400.0, 3200.0]})
    back = pd.read_parquet(s.write_table(df, "nse", "bhavcopy"))

    assert "knowledge_date" in back.columns, "RULE 2: unstamped rows are not point-in-time"
    assert back["knowledge_date"].nunique() == 1
    assert back["knowledge_date"].iloc[0] == datetime.now(timezone.utc).date().isoformat()


def test_write_table_does_not_mutate_caller_frame(s):
    df = pd.DataFrame({"a": [1]})
    s.write_table(df, "test", "ds")
    assert "knowledge_date" not in df.columns, "connector's own frame was mutated"


def test_write_table_preserves_data(s):
    df = pd.DataFrame({"symbol": ["A", "B"], "close": [1.5, 2.5], "vol": [100, 200]})
    back = pd.read_parquet(s.write_table(df, "nse", "bhavcopy"))
    pd.testing.assert_frame_equal(back.drop(columns=["knowledge_date"]), df)


def test_write_table_never_overwrites(s):
    """A backfill loop issues many writes per second. None may be lost."""
    paths = [s.write_table(pd.DataFrame({"d": [f"2010-01-{i:02d}"]}), "nse", "bhavcopy")
             for i in range(1, 11)]
    assert len(set(paths)) == 10, "a vintage was silently overwritten"
    total = sum(len(pd.read_parquet(p)) for p in paths)
    assert total == 10


# ------------------------------------------------------------ DuckDB layer
def test_register_views_empty_warehouse(s, con):
    assert s.register_views(con) == []


def test_register_views_exposes_datasets(s, con):
    s.write_table(pd.DataFrame({"a": [1]}), "nse", "bhavcopy")
    s.write_table(pd.DataFrame({"b": [2]}), "rbi", "forex_reserves")
    assert set(s.register_views(con)) == {"nse_bhavcopy", "rbi_forex_reserves"}


def test_register_views_sanitises_hyphens(s, con):
    s.write_table(pd.DataFrame({"a": [1]}), "cme", "gold-settlements")
    views = s.register_views(con)
    assert "cme_gold_settlements" in views
    assert con.execute("SELECT count(*) FROM cme_gold_settlements").fetchone()[0] == 1


def test_view_unions_vintages_and_tolerates_schema_drift(s, con):
    """
    A later vintage gaining a column must not break the view or drop history --
    union_by_name backfills the older rows with NULL.
    """
    s.write_table(pd.DataFrame({"week": ["2026-08-07"], "reserves": [704885.0]}),
                  "rbi", "forex_reserves")
    s.write_table(pd.DataFrame({"week": ["2026-08-14"], "reserves": [706100.0], "gold": [98000.0]}),
                  "rbi", "forex_reserves")
    s.register_views(con)

    rows = con.execute("SELECT week, reserves, gold FROM rbi_forex_reserves ORDER BY week").fetchall()
    assert rows == [("2026-08-07", 704885.0, None), ("2026-08-14", 706100.0, 98000.0)]


def test_register_views_is_idempotent(s, con):
    s.write_table(pd.DataFrame({"a": [1]}), "nse", "bhavcopy")
    assert s.register_views(con) == s.register_views(con)


# ------------------------------------------------------------ the whole path
def test_end_to_end_fetch_archive_parse_query(s, con):
    """The shape every connector must follow: archive bytes, then parse the archive."""
    payload = b"symbol,close\nRELIANCE,1400.5\nTCS,3200.0\n"

    raw_path = s.save_raw("nse", "bhavcopy", payload, ext="csv", meta={"url": "https://x.invalid"})
    df = pd.read_csv(raw_path)                      # parse FROM the archive, not the response
    s.write_table(df, "nse", "bhavcopy")
    s.register_views(con)

    got = con.execute(
        "SELECT symbol, close FROM nse_bhavcopy ORDER BY symbol"
    ).fetchall()
    assert got == [("RELIANCE", 1400.5), ("TCS", 3200.0)]
    assert con.execute(
        "SELECT count(DISTINCT knowledge_date) FROM nse_bhavcopy"
    ).fetchone()[0] == 1


def test_register_views_skips_empty_dataset_dirs(s, con):
    """
    A failed or interrupted write leaves an empty dataset folder. read_parquet
    raises on a pattern matching nothing, which would break registration for
    EVERY dataset -- one partial failure poisoning the whole warehouse.
    """
    s.write_table(pd.DataFrame({"a": [1]}), "nse", "good")
    (s.PARQUET / "nse" / "half_written").mkdir(parents=True, exist_ok=True)

    views = s.register_views(con)
    assert "nse_good" in views
    assert "nse_half_written" not in views
    assert con.execute("SELECT count(*) FROM nse_good").fetchone()[0] == 1


# -------------------------------------------------- warehouse root resolution
def test_relative_root_resolves_against_the_repo_not_cwd(tmp_path, monkeypatch):
    """
    .env ships QUANTDATA_ROOT=./data. Read relative to the CWD, anything launched
    from elsewhere silently points at a different, usually empty warehouse --
    the dashboard opens, registers 0 views, and looks exactly like a pipeline
    that collected nothing. Nothing errors.
    """
    import importlib
    monkeypatch.setenv("QUANTDATA_ROOT", "./data")
    monkeypatch.chdir(tmp_path)
    import core.storage as storage
    importlib.reload(storage)
    try:
        assert storage.ROOT.is_absolute()
        assert storage.ROOT == storage.REPO_ROOT / "data"
        assert tmp_path not in storage.ROOT.parents
    finally:
        importlib.reload(storage)


def test_absolute_root_is_honoured_as_given(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("QUANTDATA_ROOT", str(tmp_path / "warehouse"))
    import core.storage as storage
    importlib.reload(storage)
    try:
        assert storage.ROOT == tmp_path / "warehouse"
    finally:
        importlib.reload(storage)


def test_unset_root_defaults_beside_the_repo(monkeypatch):
    import importlib
    monkeypatch.delenv("QUANTDATA_ROOT", raising=False)
    import core.storage as storage
    importlib.reload(storage)
    try:
        assert storage.ROOT == storage.REPO_ROOT / "data"
    finally:
        importlib.reload(storage)
