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


def test_revision_examples_survive_arrow_when_changed_fields_differ_in_type(s):
    """
    example_before/after carry a value lifted from whichever column moved, so
    across rows they mix types: here row A moves a float and row B moves a UUID
    string. Left as objects, pyarrow infers double from the first row and the
    dashboard dies with ArrowInvalid ("tried to convert to double") the moment it
    reaches the string -- the frame must be renderable, not just correct.
    """
    import pyarrow as pa

    storage, quality = s
    before = pd.DataFrame({
        "sym": ["A", "B"],
        "close": [10.0, 20.0],
        "run_id": ["7f5adefc-a814-41ee-bb35-8cc2a476dc10", "1c0ffee5-dead-4bee-9f00-0d15ea5e0000"],
    })
    after = before.copy()
    after.loc[0, "close"] = 11.5                                   # float revision
    after.loc[1, "run_id"] = "9a11dead-beef-4cab-b055-facade000001"  # string revision
    write(storage, before)
    write(storage, after)

    revs = quality.detect_revisions("nse_bhavcopy", keys=["sym"])
    assert len(revs) == 2
    assert set(revs["example_field"]) == {"close", "run_id"}

    pa.Table.from_pandas(revs, preserve_index=False)   # the actual regression

    a = revs.set_index("sym").loc["A"]
    assert a["example_before"] == "10.0" and a["example_after"] == "11.5"


def test_revision_example_marks_a_value_that_went_missing(s):
    """"Went missing" is a revision too, so it must render as something a reader
    can tell apart from the string "nan"."""
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A"], "note": ["filed"]}))
    write(storage, pd.DataFrame({"sym": ["A"], "note": [None]}))

    revs = quality.detect_revisions("nse_bhavcopy", keys=["sym"])
    assert len(revs) == 1
    assert revs.iloc[0]["example_after"] == "(missing)"


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


# ------------------------------------------------------------- revision cap
def test_detect_revisions_skips_over_the_row_cap(s):
    """
    derived_correlation (an O(n^2) pairwise matrix, ~3M rows/vintage) measured
    at 40+ seconds to fingerprint-compare -- most of quality_report()'s entire
    runtime for one dataset out of 60+. Over the cap, this must raise instead
    of paying for the comparison.
    """
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A", "B", "C"], "close": [1.0, 2.0, 3.0]}))
    write(storage, pd.DataFrame({"sym": ["A", "B", "C"], "close": [1.0, 2.0, 4.0]}))
    with pytest.raises(quality.RevisionCheckSkipped, match="exceeds"):
        quality.detect_revisions("nse_bhavcopy", keys=["sym"], max_total_rows=2)


def test_detect_revisions_row_cap_checked_from_metadata_before_any_read(s):
    """
    The point of the cap is to avoid PAYING for the read -- so the row count
    must come from parquet metadata, not from loading the files and counting
    afterwards (which would defeat it entirely).
    """
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A"] * 100, "close": range(100)}))
    write(storage, pd.DataFrame({"sym": ["A"] * 100, "close": range(100)}))
    with pytest.raises(quality.RevisionCheckSkipped):
        quality.detect_revisions("nse_bhavcopy", keys=["sym"], max_total_rows=50)


def test_detect_revisions_default_cap_does_not_affect_normal_sized_datasets(s):
    """Every real dataset except derived_correlation is far under the default
    cap -- this must keep working exactly as before for them."""
    storage, quality = s
    n = 1000
    write(storage, pd.DataFrame({"sym": [f"S{i}" for i in range(n)], "close": range(n)}))
    write(storage, pd.DataFrame({"sym": [f"S{i}" for i in range(n)], "close": range(n)}))
    assert quality.detect_revisions("nse_bhavcopy", keys=["sym"]).empty


def test_report_reports_skipped_revision_check_without_claiming_zero(s, monkeypatch):
    """
    A skipped comparison must not report revision_state as "0" -- that would
    claim the dataset was checked and clean, when it was not checked at all.
    Same "visible-but-partial beats silently-absent" reasoning as the
    existing key-error branch this mirrors.
    """
    storage, quality = s
    write(storage, pd.DataFrame({"sym": ["A"], "close": [1.0]}), dataset="huge")
    write(storage, pd.DataFrame({"sym": ["A"], "close": [1.0]}), dataset="huge")

    def fake_detect_revisions(view, paths=None):
        if view == "nse_huge":
            raise quality.RevisionCheckSkipped("skipped: 9,000,000 rows across 3 vintages "
                                              "exceeds the 500,000-row revision-check cap")
        return pd.DataFrame()

    monkeypatch.setattr(quality, "detect_revisions", fake_detect_revisions)
    rep = quality.quality_report()
    row = rep[rep["view"] == "nse_huge"].iloc[0]
    assert "skipped" in row["revisions"]
    assert row["revisions"] != "0"


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
