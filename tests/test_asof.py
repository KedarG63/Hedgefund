"""
Point-in-time reads, pinned.

The properties here are the ones that fail SILENTLY in production if broken:
a duplicated row inflates every join off it, and an unbounded knowledge_date
lets a backtest read figures nobody could have known at the time. Both look
like ordinary data.

All offline -- fixtures are parquet files written into tmp_path, no warehouse.
"""
import duckdb
import pandas as pd
import pytest

from core import asof as asof_mod
from core.asof import UnregisteredView, asof_sql, latest_per, natural_key

VIEW = "zzz_test_view"


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    """A fake one-view warehouse. Returns a writer for named vintages."""
    d = tmp_path / VIEW
    d.mkdir()
    monkeypatch.setitem(asof_mod.DATASET_KEYS, VIEW, ["symbol", "trade_date"])
    monkeypatch.setattr(asof_mod, "view_paths", lambda: {VIEW: d})

    def write(vintage: str, rows: list[dict]):
        # write_table() names files YYYYmmddTHHMMSS_ffffff.parquet; the name is
        # what orders same-day writes, so the fixture must use real-shaped names.
        pd.DataFrame(rows).to_parquet(d / f"{vintage}.parquet", index=False)

    return write


def run(sql: str) -> pd.DataFrame:
    return duckdb.connect(":memory:").execute(sql).df()


# ------------------------------------------------------------ vintage collapse

def test_same_day_rerun_collapses_to_one_row_per_key(warehouse):
    """
    write_table() never overwrites, so a rerun leaves a second vintage of the
    same logical row. Confirmed live: three run_daily.py runs on 2026-08-22
    tripled every nse_bhavcopy row for that day.
    """
    row = {"symbol": "RELIANCE", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22"}
    warehouse("20260822T090000_000001", [{**row, "close": 1400.0}])
    warehouse("20260822T170000_000002", [{**row, "close": 1410.0}])

    out = run(asof_sql(VIEW))
    assert len(out) == 1, "both vintages survived -- every join off this doubles"


def test_the_later_write_wins_when_knowledge_date_ties(warehouse):
    """
    THE TIEBREAK. knowledge_date is stamped as a DATE, so same-day reruns tie
    and ORDER BY knowledge_date DESC alone picks arbitrarily. The parquet
    filename carries microsecond write order, which makes it a total order.
    """
    row = {"symbol": "RELIANCE", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22"}
    warehouse("20260822T090000_000001", [{**row, "close": 1400.0}])
    warehouse("20260822T170000_000002", [{**row, "close": 1410.0}])   # corrected, later

    out = run(asof_sql(VIEW))
    assert out["close"].iloc[0] == 1410.0, "picked an earlier vintage over a later one"


def test_a_backfill_of_many_days_in_one_run_is_not_treated_as_duplication(warehouse):
    """
    Several files can share one knowledge_date without being reruns -- a
    backfill loop writes one file per trading day, all on the same calendar
    day. Those are distinct natural keys and must all survive. (nse_bhavcopy
    has exactly this: 61 files stamped 2026-08-26.)
    """
    warehouse("20260826T075546_523996", [
        {"symbol": "RELIANCE", "trade_date": "2026-08-20", "knowledge_date": "2026-08-26", "close": 1390.0}])
    warehouse("20260826T075605_024444", [
        {"symbol": "RELIANCE", "trade_date": "2026-08-21", "knowledge_date": "2026-08-26", "close": 1395.0}])

    out = run(asof_sql(VIEW))
    assert len(out) == 2, "collapsed two different trading days into one"


def test_the_collapse_is_deterministic_across_repeated_executions(warehouse):
    """
    REGRESSION, measured on the real warehouse: the arg_max(col, knowledge_date)
    form this replaced returned FOUR different results across eight identical
    executions of the same query on derived_digest, because 12 of its 103
    instruments carry more than one row under a single knowledge_date and the
    tie was broken arbitrarily. A signal table that answers differently each
    time it is read is worse than one that is merely stale.
    """
    row = {"symbol": "RELIANCE", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22"}
    for i, close in enumerate([1400.0, 1405.0, 1410.0], start=1):
        warehouse(f"20260822T09000{i}_00000{i}", [{**row, "close": close}])

    seen = {tuple(run(asof_sql(VIEW))["close"]) for _ in range(8)}
    assert seen == {(1410.0,)}, f"unstable or wrong winner across executions: {seen}"


def test_the_filename_column_does_not_leak_into_results(warehouse):
    """filename=true is an implementation detail of the tiebreak. A caller
    doing SELECT * must not suddenly get an extra column."""
    warehouse("20260822T090000_000001", [
        {"symbol": "RELIANCE", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22", "close": 1400.0}])
    assert "filename" not in run(asof_sql(VIEW)).columns


# --------------------------------------------------------------- the as-of bound

def test_as_of_hides_rows_written_after_that_date(warehouse):
    """The whole point: a backtest must not see figures that did not exist yet."""
    warehouse("20260822T090000_000001", [
        {"symbol": "RELIANCE", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22", "close": 1400.0}])
    warehouse("20260828T090000_000002", [
        {"symbol": "TCS", "trade_date": "2026-08-28", "knowledge_date": "2026-08-28", "close": 3100.0}])

    assert set(run(asof_sql(VIEW))["symbol"]) == {"RELIANCE", "TCS"}
    assert set(run(asof_sql(VIEW, "2026-08-25"))["symbol"]) == {"RELIANCE"}
    assert run(asof_sql(VIEW, "2026-08-21")).empty


def test_as_of_returns_the_value_known_then_not_the_corrected_one(warehouse):
    """
    A revision is the dangerous case. As of the earlier date the ORIGINAL
    figure must come back, even though a corrected vintage exists on disk.
    """
    row = {"symbol": "RELIANCE", "trade_date": "2026-08-22"}
    warehouse("20260822T090000_000001", [{**row, "knowledge_date": "2026-08-22", "close": 1400.0}])
    warehouse("20260828T090000_000002", [{**row, "knowledge_date": "2026-08-28", "close": 1405.0}])

    assert run(asof_sql(VIEW, "2026-08-25"))["close"].iloc[0] == 1400.0
    assert run(asof_sql(VIEW))["close"].iloc[0] == 1405.0


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-01", "2026-08-22'; DROP TABLE x--", ""])
def test_a_non_date_as_of_is_refused(warehouse, bad):
    """as_of is inlined into SQL, so it is parsed as a date rather than trusted."""
    with pytest.raises(ValueError):
        asof_sql(VIEW, bad)


# ------------------------------------------------------------------ the registry

def test_an_undeclared_view_raises_instead_of_silently_skipping_dedup(warehouse):
    """
    Connector contract rule 4, applied here: returning duplicates for an
    unknown view produces a wrong answer that looks entirely normal.
    """
    with pytest.raises(UnregisteredView):
        asof_sql("some_view_nobody_declared")


def test_a_declared_view_with_no_data_on_disk_raises(monkeypatch):
    monkeypatch.setitem(asof_mod.DATASET_KEYS, VIEW, ["symbol"])
    monkeypatch.setattr(asof_mod, "view_paths", lambda: {})
    with pytest.raises(UnregisteredView):
        asof_sql(VIEW)


# ------------------------------------------------------------------ latest_per

@pytest.fixture
def signals(tmp_path, monkeypatch):
    """A derived_* shaped view keyed (symbol, as_of_date)."""
    d = tmp_path / "zzz_signal"
    d.mkdir()
    monkeypatch.setitem(asof_mod.DATASET_KEYS, "zzz_signal", ["symbol", "as_of_date"])
    monkeypatch.setattr(asof_mod, "view_paths", lambda: {"zzz_signal": d})

    def write(vintage, rows):
        pd.DataFrame(rows).to_parquet(d / f"{vintage}.parquet", index=False)

    return write


def test_latest_per_ranks_on_the_collapsed_key_not_on_write_time(signals):
    """
    THE BACKFILL CASE. A row computed for an old as_of_date but written today
    carries the newest knowledge_date in the table. Ordering by knowledge_date
    -- which the arg_max form this replaced effectively did -- would present a
    stale signal as the current one, silently.
    """
    signals("20260903T090000_000001", [
        {"symbol": "RELIANCE", "as_of_date": "2026-09-03",
         "knowledge_date": "2026-09-03", "beta": 1.11}])
    signals("20260904T090000_000002", [                      # backfill, written later
        {"symbol": "RELIANCE", "as_of_date": "2026-06-30",
         "knowledge_date": "2026-09-04", "beta": 0.77}])

    out = run(latest_per("zzz_signal", ["symbol"], columns="symbol, beta, as_of_date"))
    assert len(out) == 1
    assert out["as_of_date"].iloc[0] == "2026-09-03", "a backfill displaced the current signal"
    assert out["beta"].iloc[0] == 1.11


def test_latest_per_still_collapses_same_day_reruns_of_one_computation(signals):
    """Within one as_of_date, the later write wins -- the vintage collapse
    still applies underneath the ranking."""
    row = {"symbol": "RELIANCE", "as_of_date": "2026-09-03", "knowledge_date": "2026-09-03"}
    signals("20260903T090000_000001", [{**row, "beta": 1.11}])
    signals("20260903T170000_000002", [{**row, "beta": 1.22}])   # corrected rerun

    out = run(latest_per("zzz_signal", ["symbol"], columns="symbol, beta"))
    assert len(out) == 1 and out["beta"].iloc[0] == 1.22


def test_latest_per_refuses_a_grouping_outside_the_declared_key(signals):
    """per must be a subset of the natural key; anything else is not a
    well-defined collapse, and silently returning a plain GROUP BY would hide
    that the key is wrong."""
    with pytest.raises(UnregisteredView):
        latest_per("zzz_signal", ["sector"])


def test_natural_key_returns_the_declared_key():
    assert natural_key("derived_capm_beta") == ["symbol", "as_of_date"]


def test_every_declared_key_is_a_non_empty_list_of_strings():
    """Guards the registry itself -- a typo'd entry here breaks reads, not just
    revision monitoring, now that core.asof shares it."""
    for view, keys in asof_mod.DATASET_KEYS.items():
        assert isinstance(keys, list) and keys, f"{view} has an empty key"
        assert all(isinstance(k, str) and k for k in keys), f"{view} has a non-string key"


# --------------------------------------------------------------- query assembly

def test_columns_where_order_and_limit_compose(warehouse):
    warehouse("20260822T090000_000001", [
        {"symbol": "RELIANCE", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22", "close": 1400.0},
        {"symbol": "TCS", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22", "close": 3100.0},
        {"symbol": "INFY", "trade_date": "2026-08-22", "knowledge_date": "2026-08-22", "close": 1600.0},
    ])
    out = run(asof_sql(VIEW, columns="symbol, close", where="close > 1500",
                       order_by="close DESC", limit=1))
    assert list(out.columns) == ["symbol", "close"]
    assert out["symbol"].tolist() == ["TCS"]
