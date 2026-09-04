"""
Regression test for a real bug found while building analytics/outliers.py:
write_table() never overwrites, so a job rerun on the same calendar day
leaves a SECOND vintage sharing the same as_of_date as the first. A join
that filters on "as_of_date = max(as_of_date)" does NOT dedupe in that case
-- both same-day vintages pass the filter, silently doubling the joined
row (and anything computed off it, e.g. IsolationForest fit on a feature
matrix with duplicate points).

The original fix was `arg_max(col, knowledge_date) GROUP BY symbol`, which
collapses to the latest-written vintage per key regardless of how many
same-day vintages exist. Those modules now go through core.asof instead --
arg_max resolves each column independently and breaks knowledge_date ties
arbitrarily (measured: four different results across eight identical
executions), so see tests/test_asof.py for the properties that hold today.
The tests below still document the underlying DuckDB behaviour and the
original bug. This test proves that pattern actually works, against an in-memory
DuckDB table shaped like the real bug (two vintages, identical as_of_date,
different knowledge_date and different values) -- no warehouse needed.
"""
import duckdb
import pandas as pd


def test_naive_as_of_date_filter_does_not_dedupe_same_day_reruns():
    """Reproduce the bug: this is what the code looked like before the fix."""
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE derived_x (symbol VARCHAR, value DOUBLE, as_of_date VARCHAR, knowledge_date VARCHAR)
    """)
    con.execute("""
        INSERT INTO derived_x VALUES
            ('AAA', 1.0, '2026-08-25', '2026-08-24'),
            ('AAA', 2.0, '2026-08-25', '2026-08-26')
    """)
    buggy = con.execute("""
        SELECT symbol, value FROM derived_x
        WHERE as_of_date = (SELECT max(as_of_date) FROM derived_x)
    """).df()
    assert len(buggy) == 2, "the bug: both same-day vintages survive the naive filter"
    con.close()


def test_arg_max_knowledge_date_dedupes_to_the_latest_vintage():
    """The fix: exactly one row per symbol, holding the newest vintage's value."""
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE derived_x (symbol VARCHAR, value DOUBLE, as_of_date VARCHAR, knowledge_date VARCHAR)
    """)
    con.execute("""
        INSERT INTO derived_x VALUES
            ('AAA', 1.0, '2026-08-25', '2026-08-24'),
            ('AAA', 2.0, '2026-08-25', '2026-08-26'),
            ('BBB', 5.0, '2026-08-25', '2026-08-24')
    """)
    fixed = con.execute("""
        SELECT symbol, arg_max(value, knowledge_date) AS value FROM derived_x GROUP BY symbol
    """).df().sort_values("symbol", ignore_index=True)
    con.close()

    assert len(fixed) == 2, "one row per symbol, no duplicates"
    assert fixed.loc[fixed["symbol"] == "AAA", "value"].iloc[0] == 2.0, \
        "must hold the value from the newer knowledge_date (2026-08-26), not the older one"
    assert fixed.loc[fixed["symbol"] == "BBB", "value"].iloc[0] == 5.0


def test_join_of_two_dedup_subqueries_does_not_multiply_rows():
    """
    The actual shape used in digest.py/outliers.py: two independently-deduped
    subqueries joined on symbol. Confirms the join itself doesn't reintroduce
    duplication even when both sides have same-day rerun vintages.
    """
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE beta_tbl (symbol VARCHAR, beta DOUBLE, knowledge_date VARCHAR)")
    con.execute("""
        INSERT INTO beta_tbl VALUES
            ('AAA', 1.1, '2026-08-24'), ('AAA', 1.2, '2026-08-26'), ('BBB', 0.9, '2026-08-24')
    """)
    con.execute("CREATE TABLE mom_tbl (symbol VARCHAR, momentum DOUBLE, knowledge_date VARCHAR)")
    con.execute("""
        INSERT INTO mom_tbl VALUES
            ('AAA', 0.5, '2026-08-24'), ('AAA', 0.6, '2026-08-26'), ('BBB', -0.3, '2026-08-24')
    """)

    out = con.execute("""
        SELECT b.symbol, b.beta, m.momentum
        FROM (SELECT symbol, arg_max(beta, knowledge_date) AS beta FROM beta_tbl GROUP BY symbol) b
        JOIN (SELECT symbol, arg_max(momentum, knowledge_date) AS momentum FROM mom_tbl GROUP BY symbol) m
          USING (symbol)
    """).df().sort_values("symbol", ignore_index=True)
    con.close()

    assert len(out) == 2
    assert out.loc[out["symbol"] == "AAA", "beta"].iloc[0] == 1.2
    assert out.loc[out["symbol"] == "AAA", "momentum"].iloc[0] == 0.6
