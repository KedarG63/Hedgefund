"""
The provenance chain.

What this protects is the claim the whole archive exists to support: that a
number on screen can be walked back to the bytes a server actually returned.
A BROKEN chain is obvious. A WRONG chain is not -- it points a reader at bytes
that did not produce their number, with a timestamp and a hash making it look
authoritative. So the tests below care most about the edges being declared and
honest, not merely present.
"""
from __future__ import annotations

import json

import pytest

from core import provenance as prov


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A miniature raw archive plus parquet, in the real layout."""
    raw = tmp_path / "raw"
    pq = tmp_path / "parquet"
    (raw / "nse" / "bhavcopy" / "2026-08-28").mkdir(parents=True)
    (pq / "nse" / "bhavcopy").mkdir(parents=True)

    payload = raw / "nse" / "bhavcopy" / "2026-08-28" / "122117_05c6583a.zip"
    payload.write_bytes(b"PK\x03\x04 not really a zip")
    payload.with_suffix(".zip.meta.json").write_text(json.dumps({
        "source": "nse", "dataset": "bhavcopy",
        "fetched_at_utc": "2026-08-28T12:21:17.473284+00:00",
        "sha256": "05c6583a4337fe8301a70bb0deadbeef", "bytes": 21,
        "date": "2026-08-28",
    }))
    (pq / "nse" / "bhavcopy" / "20260828T122117_000001.parquet").write_bytes(b"x")

    monkeypatch.setattr(prov, "RAW", raw)
    monkeypatch.setattr(prov, "PARQUET", pq)
    return raw


# -------------------------------------------------------------- raw resolution

def test_a_collected_view_resolves_to_its_archived_bytes(archive):
    files = prov.raw_files("nse_bhavcopy")
    assert len(files) == 1
    f = files[0]
    assert f["path"] == "nse/bhavcopy/2026-08-28/122117_05c6583a.zip"
    # The sidecar is the point: when it arrived, and proof of what arrived.
    assert f["fetched_at_utc"].startswith("2026-08-28")
    assert f["sha256"].startswith("05c6583a")


def test_a_computed_view_has_no_raw_bytes_of_its_own(archive):
    """derived_* is calculated, never fetched. Claiming raw bytes for it would
    be a category error."""
    assert prov.raw_datasets_for("derived_factor_model") == []
    assert prov.raw_files("derived_factor_model") == []


def test_a_view_whose_raw_dataset_is_not_declared_returns_nothing(archive):
    """Rather than falling back to a plausible-looking guess. A wrong link is
    worse than an absent one -- this is CLAUDE.md's do-not-invent rule applied
    to lineage."""
    assert prov.raw_datasets_for("rbi_wss_extract") == []


# --------------------------------------------------------------------- tracing

def test_the_chain_reaches_archived_bytes_from_a_computed_score(archive):
    """
    THE EXIT CRITERION. derived_factor_model is a composite score; it must be
    walkable to the bytes NSE returned.
    """
    tree = prov.trace("derived_factor_model")

    def any_raw(n):
        return bool(n["raw"]) or any(any_raw(u) for u in n["upstream"])

    assert tree["computed"] is True
    assert any_raw(tree), "a composite score must reach raw bytes"


def test_every_node_in_the_trace_has_the_same_shape(archive):
    """
    A revisited node used to return a DIFFERENT dict, and the first consumer
    that walked the tree crashed on the branch nobody had exercised. Every node
    carries every key.
    """
    required = {"view", "depth", "computed", "approximate", "repeated",
                "truncated", "vintages", "raw", "upstream"}

    def check(n):
        assert required <= set(n), f"{n.get('view')} is missing {required - set(n)}"
        for u in n["upstream"]:
            check(u)

    check(prov.trace("derived_factor_model"))


def test_a_diamond_is_marked_repeated_not_cycle(archive):
    """
    derived_factor_model reaches nse_bhavcopy through three different children.
    That is an ordinary DAG diamond -- normal, not a defect -- and the second
    visit is collapsed only to stop the tree growing exponentially.
    """
    tree = prov.trace("derived_factor_model")
    seen = []

    def walk(n):
        seen.append((n["view"], n["repeated"]))
        for u in n["upstream"]:
            walk(u)

    walk(tree)
    bhav = [r for v, r in seen if v == "nse_bhavcopy"]
    assert len(bhav) > 1, "expected nse_bhavcopy to appear on several branches"
    assert bhav.count(False) == 1, "it should be expanded exactly once"
    assert any(bhav), "later visits must be marked repeated"


def test_module_level_approximations_are_labelled(archive):
    """These share an analytics module and a set of inputs, so their upstream
    list is the module's. Over-inclusive is the safe direction -- but it has to
    say so rather than imply per-table precision."""
    tree = prov.trace("derived_bulk_block_anomaly")
    assert tree["approximate"] is True


# ---------------------------------------------------------------- byte preview

def test_reading_an_archived_file_returns_bounded_bytes(archive):
    out = prov.read_raw("nse/bhavcopy/2026-08-28/122117_05c6583a.zip", max_bytes=8)
    assert out["truncated"] is True
    assert out["bytes"] == 21


def test_a_path_escaping_the_archive_is_refused(archive):
    """
    These names arrive from a URL. Without the containment check the endpoint
    would read any file the process can see.
    """
    with pytest.raises(ValueError):
        prov.read_raw("../../../../etc/passwd")


def test_a_missing_file_raises_rather_than_returning_empty(archive):
    with pytest.raises(FileNotFoundError):
        prov.read_raw("nse/bhavcopy/2026-08-28/nope.zip")


# ------------------------------------------------------------------ the registry

def test_declared_upstream_views_are_plausible_view_names():
    """A typo here would silently truncate a chain -- the node would resolve to
    nothing and the drill would just stop early."""
    for view, ups in prov.UPSTREAM.items():
        assert "_" in view, view
        for u in ups:
            assert "_" in u and u != view, f"{view} -> {u}"


def test_no_computed_view_claims_a_raw_dataset():
    """derived_/analytics_ tables are calculated. An entry in RAW_DATASETS for
    one would assert bytes that never existed."""
    bad = [v for v in prov.RAW_DATASETS if v.startswith(("derived_", "analytics_"))]
    assert not bad, bad
