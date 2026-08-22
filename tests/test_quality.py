"""
Tests for the data-quality layer.

These run against a throwaway warehouse so the assertions are exact. The cases
mirror the three failures the module exists to catch, plus the ones that made it
report confident nonsense during development.
"""
import importlib
import time

import pandas as pd
import pytest


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTDATA_ROOT", str(tmp_path))
    import core.storage as storage
    importlib.reload(storage)
    import core.quality as quality
    importlib.reload(quality)
    yield storage, quality
    importlib.reload(storage)
    importlib.reload(quality)


def write(storage, df, source="nse", dataset="bhavcopy"):
    """Write a vintage, with a pause so mtime ordering is unambiguous."""
    path = storage.write_table(df, source, dataset)
    time.sleep(0.01)
    return path


# ------------------------------------------------------------------ row counts
def test_row_count_trend_reports_real_counts(s):
    """
    read_parquet(columns=[]) returns an EMPTY frame, so len() on it reports 0
    rows for every dataset -- plausible-looking and completely wrong. The count
    must come from parquet metadata.
    """
    storage, quality = s
    write(storage, pd.DataFrame({"a": range(10)}))
    write(storage, pd.DataFrame({"a": range(7)}))

    trend = quality.row_count_trend("nse_bhavcopy")
    assert trend["rows"].tolist() == [10, 7]
    assert trend["row_change"].tolist()[1] == -3


def test_collapse_flags_only_a_halving(s):
    """
    Daily row counts vary legitimately (holidays, half-days). Only a fall of
    more than half is worth surfacing, or the signal is all noise.
    """
    storage, quality = s
    write(storage, pd.DataFrame({"a": range(100)}))
    write(storage, pd.DataFrame({"a": range(80)}))     # -20%, normal
    write(storage, pd.DataFrame({"a": range(10)}))     # -87%, a collapse

    trend = quality.row_count_trend("nse_bhavcopy")
    assert trend["collapsed"].tolist() == [False, False, True]


# ------------------------------------------------------------------ freshness
def test_freshness_reads_the_raw_archive_not_parquet(s):
    """
    Parsing can succeed against a stale file, so recent ROWS do not prove a
    recent FETCH. Only the archive sidecars know when bytes arrived.
    """
    storage, quality = s
    storage.save_raw("nse", "bhavcopy", b"payload", ext="csv")
    fresh = quality.fetch_freshness()
    assert len(fresh) == 1
    row = fresh.iloc[0]
    assert row["source"] == "nse" and row["dataset"] == "bhavcopy"
    assert row["age_hours"] is not None and row["age_hours"] < 1
    assert row["raw_files"] == 1


def test_freshness_empty_when_nothing_archived(s):
    _, quality = s
    assert quality.fetch_freshness().empty


# ------------------------------------------------------------------ revisions
def test_revision_detected_when_a_value_changes(s):
    storage, quality = s
    keys = ["sym"]
    write(storage, pd.DataFrame({"sym": ["A", "B"], "close": [10.0, 20.0]}))
    write(storage, pd.DataFrame({"sym": ["A", "B"], "close": [10.0, 25.0]}))

    revs = quality.detect_revisions("nse_bhavcopy", keys=keys)
    assert len(revs) == 1
    row = revs.iloc[0]
    assert row["sym"] == "B"
    assert row["example_field"] == "close"
    assert float(row["example_before"]) == 20.0
    assert float(row["example_after"]) == 25.0


def test_no_revision_when_values_are_stable(s):
    storage, quality = s
    df = pd.DataFrame({"sym": ["A", "B"], "close": [10.0, 20.0]})
    write(storage, df)
    write(storage, df)
    assert quality.detect_revisions("nse_bhavcopy", keys=["sym"]).empty


def test_single_vintage_cannot_be_revision_checked(s):
    """
    Reporting "no revisions" from one file would be misleading, not reassuring
    -- a revision needs two observations.
    """
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A"], "close": [10.0]}))
    assert quality.detect_revisions("nse_bhavcopy", keys=["sym"]).empty


def test_knowledge_date_change_alone_is_not_a_revision(s):
    """
    knowledge_date differs by construction between runs. Counting it as a value
    would make EVERY row look revised on every rerun.
    """
    storage, quality = s
    df = pd.DataFrame({"sym": ["A"], "close": [10.0]})
    write(storage, df)
    write(storage, df)
    revs = quality.detect_revisions("nse_bhavcopy", keys=["sym"])
    assert revs.empty
    assert "knowledge_date" in quality.NON_VALUE_COLUMNS


def test_value_becoming_null_is_a_revision(s):
    """
    A field going missing is a revision too. The fingerprint uses a sentinel so
    NULL stays distinguishable from the empty string.
    """
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A"], "close": [10.0]}))
    write(storage, pd.DataFrame({"sym": ["A"], "close": [None]}))
    revs = quality.detect_revisions("nse_bhavcopy", keys=["sym"])
    assert len(revs) == 1


def test_mixed_dtypes_do_not_break_the_fingerprint(s):
    """
    A plain astype(str) leaves nullable dtypes as floats and the row-wise join
    then raises TypeError -- which took out the whole report.
    """
    storage, quality = s
    a = pd.DataFrame({"sym": ["A"], "n": pd.array([1], dtype="Int64"),
                      "f": [1.5], "t": ["x"], "b": [True]})
    b = a.copy()
    b.loc[0, "f"] = 2.5
    write(storage, a)
    write(storage, b)
    revs = quality.detect_revisions("nse_bhavcopy", keys=["sym"])
    assert len(revs) == 1 and revs.iloc[0]["example_field"] == "f"


def test_undeclared_key_column_raises_rather_than_guessing(s):
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A"], "close": [1.0]}))
    write(storage, pd.DataFrame({"sym": ["A"], "close": [2.0]}))
    with pytest.raises(KeyError):
        quality.detect_revisions("nse_bhavcopy", keys=["not_a_column"])


def test_dataset_without_declared_key_is_skipped_not_failed(s):
    """Unknown datasets still get freshness and volume; they just skip revisions."""
    storage, quality = s
    write(storage, pd.DataFrame({"a": [1]}), source="zzz", dataset="unknown")
    assert "zzz_unknown" not in quality.DATASET_KEYS
    assert quality.detect_revisions("zzz_unknown").empty


# ------------------------------------------------------------------ the report
def test_quality_report_statuses(s):
    storage, quality = s
    # stable dataset -> OK
    stable = pd.DataFrame({"sym": ["A"], "close": [1.0]})
    write(storage, stable, dataset="stable")
    write(storage, stable, dataset="stable")
    # collapsing dataset
    write(storage, pd.DataFrame({"a": range(100)}), dataset="collapsing")
    write(storage, pd.DataFrame({"a": range(5)}), dataset="collapsing")

    rep = quality.quality_report()
    by_view = dict(zip(rep["view"], rep["status"]))
    assert by_view["nse_collapsing"] == "COLLAPSED"
    assert by_view["nse_stable"] == "OK"


def test_report_marks_single_vintage_revisions_as_not_applicable(s):
    storage, quality = s
    write(storage, pd.DataFrame({"a": [1]}), dataset="once")
    rep = quality.quality_report()
    assert rep.loc[rep["view"] == "nse_once", "revisions"].iloc[0] == "n/a"


def test_report_orders_problems_first(s):
    storage, quality = s
    write(storage, pd.DataFrame({"a": range(100)}), dataset="collapsing")
    write(storage, pd.DataFrame({"a": range(2)}), dataset="collapsing")
    write(storage, pd.DataFrame({"a": [1]}), dataset="fine")
    rep = quality.quality_report()
    assert rep.iloc[0]["status"] in ("STALE", "COLLAPSED", "REVISED")


def test_report_is_empty_for_an_empty_warehouse(s):
    _, quality = s
    assert quality.quality_report().empty


# ------------------------------------------------------------------ alerting
def test_alert_report_counts_successes_not_just_failures():
    """
    "3 failed" alone does not say whether the other 28 ran or whether the
    process died after job 3.
    """
    from core.alerting import format_report
    text = format_report({"a": "OK", "b": "FAIL: boom", "c": "SKIP", "d": "OK"})
    assert "2 ok / 1 failed / 1 skipped" in text
    assert "b: FAIL: boom" in text


def test_alert_not_sent_when_nothing_failed():
    from core.alerting import alert_failures
    out = alert_failures({"a": "OK", "b": "SKIP"})
    assert out["failures"] == 0 and out["sent"] is False


def test_unconfigured_alerting_is_reported_not_silent(monkeypatch):
    """
    Unconfigured alerting looks exactly like healthy alerting until the day it
    matters, so the caller must be able to tell the difference.
    """
    import core.alerting as alerting
    monkeypatch.setattr(alerting, "get", lambda key, default=None: None)
    out = alerting.alert_failures({"a": "FAIL: boom"})
    assert out["failures"] == 1
    assert out["sent"] is False
    assert not any(out["channels"].values())


def test_webhook_failure_does_not_raise(monkeypatch):
    """A webhook timeout must not take down a run that already collected data."""
    import core.alerting as alerting
    monkeypatch.setattr(alerting, "get", lambda key, default=None:
                        "https://example.invalid/hook" if key == "ALERT_WEBHOOK_URL" else None)
    assert alerting.send_webhook("hello") is False
