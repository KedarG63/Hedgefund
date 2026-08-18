"""
Offline tests for the RBI parsers.

No network: these pin the table-shape logic that live pages exercised, so the
two header-detection bugs found on 2026-08-18 cannot come back silently.

    python -m pytest tests/test_rbi.py -v
"""
import io

import pandas as pd
import pytest

from connectors.rbi_publications import (
    _is_index_row,
    _is_merged_row,
    _is_unit_row,
    _numeric,
    tidy_table,
)


def _table(html: str) -> pd.DataFrame:
    return pd.read_html(io.StringIO(html))[0]


# --------------------------------------------------------------- row classifiers
def test_index_row_matches_column_numbers():
    assert _is_index_row(["Item", "1", "2", "3", "4"])


def test_index_row_rejects_years():
    """
    REGRESSION: RBI's NSDP header row is '2025 2025 2026 2026 ...'. Treating it
    as the column-index row discarded every real period label beneath it, so
    series came out as col1..col6 and the rate history was unusable.
    """
    assert not _is_index_row(["Item", "2025", "2025", "2026", "2026"])


def test_index_row_needs_at_least_two_numbers():
    assert not _is_index_row(["Item", "1"])


def test_unit_and_merged_row_detection():
    assert _is_unit_row(["(₹ Crore)", "(₹ Crore)", "(₹ Crore)"])
    assert not _is_unit_row(["Item", "1", "2"])
    assert _is_merged_row(["Note: x", "Note: x", "Note: x"])
    assert not _is_merged_row(["Bank Credit", "100", "200"])


# --------------------------------------------------------------- numeric coercion
def test_numeric_strips_commas_and_parens():
    got = _numeric(pd.Series(["26,941,367", "(1,234)", "5.04", "-", "4.00-6.50"]))
    assert got.iloc[0] == 26941367
    assert got.iloc[1] == -1234
    assert got.iloc[2] == 5.04
    assert pd.isna(got.iloc[3]) and pd.isna(got.iloc[4])


# --------------------------------------------------------------- tidy_table
WSS_STYLE = """
<table>
 <tr><td>(₹ Crore)</td><td>(₹ Crore)</td><td>(₹ Crore)</td></tr>
 <tr><td>Item</td><td>Outstanding</td><td>Variation over</td></tr>
 <tr><td>Item</td><td>Jul. 31</td><td>Year-on-Year</td></tr>
 <tr><td>Item</td><td>1</td><td>2</td></tr>
 <tr><td>7 Bank Credit</td><td>22078095</td><td>3576717</td></tr>
 <tr><td>7.1a Growth (Per cent)</td><td></td><td>19.3</td></tr>
 <tr><td>Note: data include x</td><td>Note: data include x</td><td>Note: data include x</td></tr>
</table>
"""

MMO_STYLE = """
<table>
 <tr><td>(Amount in ₹ crore)</td><td></td><td></td></tr>
 <tr><td>Money Markets@</td><td>Volume (One Leg)</td><td>Weighted Average Rate</td></tr>
 <tr><td>I. Call Money</td><td>13126.53</td><td>5.18</td></tr>
</table>
"""


def test_tidy_table_uses_multilevel_headers_and_drops_footnotes():
    out = tidy_table(_table(WSS_STYLE), table_no=4)

    assert set(out["row_label"]) == {"7 Bank Credit", "7.1a Growth (Per cent)"}, \
        "unit row, header rows and the merged footnote must all be excluded"

    credit = out[(out.row_label == "7 Bank Credit") & (out.series.str.contains("Year-on-Year"))]
    assert credit["value"].iloc[0] == "3576717"
    # header levels are joined, not collapsed to a positional name
    assert credit["series"].iloc[0] == "Variation over / Year-on-Year"


def test_tidy_table_skips_empty_cells():
    out = tidy_table(_table(WSS_STYLE), table_no=4)
    growth = out[out.row_label == "7.1a Growth (Per cent)"]
    assert len(growth) == 1, "the blank Outstanding cell must not become a row"


def test_tidy_table_handles_tables_with_no_index_row():
    """
    REGRESSION: Money Market Operations tables carry no '1 2 3' row. The old
    code assumed row 0 was the header, so the UNIT row became the header
    (series -> col1, col2) and the real header row became a data row.
    """
    out = tidy_table(_table(MMO_STYLE), table_no=0)

    assert set(out["series"]) == {"Volume (One Leg)", "Weighted Average Rate"}
    assert not any(s.startswith("col") for s in out["series"]), \
        "positional fallback names mean header detection failed"
    assert list(out["row_label"].unique()) == ["I. Call Money"]
    assert "Money Markets@" not in set(out["row_label"]), "header row leaked into data"


def test_tidy_table_blanks_positional_column_titles():
    """read_html names headerless columns '0','1',... -- not a real title."""
    out = tidy_table(_table(MMO_STYLE), table_no=0)
    assert (out["table_title"] == "").all()


def test_tidy_table_keeps_real_title():
    html = WSS_STYLE.replace(
        "<tr><td>(₹ Crore)</td>",
        "<tr><th>4. Scheduled Commercial Banks</th><th>4. Scheduled Commercial Banks</th>"
        "<th>4. Scheduled Commercial Banks</th></tr><tr><td>(₹ Crore)</td>",
    )
    out = tidy_table(_table(html), table_no=4)
    assert (out["table_title"] == "4. Scheduled Commercial Banks").all()


def test_tidy_table_returns_empty_for_layout_tables():
    out = tidy_table(_table("<table><tr><td>nav</td><td>links</td></tr></table>"), table_no=0)
    assert out.empty, "a table with no numeric data must yield nothing, not junk rows"
