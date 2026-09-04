"""
Offline tests for the screener.in connector's pure logic.

No network: builds a synthetic Data Sheet workbook matching the real shape
(verified live 2026-08-30 against RELIANCE/TCS/INFY -- see connectors/
screener.py's module docstring) rather than committing a real export file,
since that file is the vendor's own proprietary output.

    python -m pytest tests/test_screener.py -v
"""
import io

import openpyxl
import pytest

from connectors.screener import EXPORT_ID_RE, CSRF_RE, _parse_data_sheet


def _build_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data Sheet"

    rows = [
        ["COMPANY NAME", "TEST COMPANY LTD"],
        ["LATEST VERSION", 2.1],
        [],
        ["META"],
        ["Current Price", 1287.0],
        [],
        ["PROFIT & LOSS"],
        ["Report Date", "2024-03-31", "2025-03-31"],
        ["Sales", 1000.0, 1200.0],
        ["Net profit", 100.0, 150.0],
        [],
        ["Quarters"],
        ["Report Date", "2026-03-31", "2026-06-30"],
        ["Sales", 290.0, 310.0],
        [],
        ["BALANCE SHEET"],
        # "Total" repeats -- liabilities-side total, then assets-side total,
        # the real shape's actual landmine (see connectors/screener.py).
        ["Report Date", "2025-03-31"],
        ["Equity Share Capital", 50.0],
        ["Total", 900.0],
        ["Net Block", 400.0],
        ["Total", 900.0],
        [],
        ["CASH FLOW:"],
        ["Report Date", "2025-03-31"],
        ["Cash from Operating Activity", 80.0],
    ]
    for row in rows:
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_data_sheet_extracts_all_four_statements():
    df = _parse_data_sheet(_build_workbook())
    assert set(df["statement"].unique()) == {"income_statement", "balance_sheet", "cash_flow"}
    assert set(df[df["statement"] == "income_statement"]["period_type"].unique()) == {"annual", "quarterly"}


def test_parse_data_sheet_current_quarter_survives():
    df = _parse_data_sheet(_build_workbook())
    q = df[(df["concept"] == "Sales") & (df["period_type"] == "quarterly")]
    latest = q.sort_values("period_end").iloc[-1]
    assert latest["period_end"] == "2026-06-30"
    assert latest["value"] == 310.0


def test_parse_data_sheet_renames_repeated_balance_sheet_totals():
    """
    REGRESSION: the Balance Sheet's "Total" label appears twice in the real
    file (liabilities+equity side, then assets side, in that fixed order) --
    a naive parse would either collide them into one concept or leave an
    unlabeled "Total"/"Total_2" for the dashboard to guess at.
    """
    df = _parse_data_sheet(_build_workbook())
    totals = df[df["concept"].isin(["Total Equity and Liabilities", "Total Assets"])]
    assert set(totals["concept"]) == {"Total Equity and Liabilities", "Total Assets"}
    assert len(totals) == 2
    assert "Total" not in df["concept"].values


def test_parse_data_sheet_skips_meta_section():
    df = _parse_data_sheet(_build_workbook())
    assert "Current Price" not in df["concept"].values


def test_parse_data_sheet_raises_when_no_known_sections_found():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data Sheet"
    ws.append(["COMPANY NAME", "TEST COMPANY LTD"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(RuntimeError, match="Data Sheet layout"):
        _parse_data_sheet(buf.getvalue())


def test_export_id_regex_matches_real_shape():
    html = '<form action="/user/company/export/6598251/" method="post">'
    m = EXPORT_ID_RE.search(html)
    assert m.group(1) == "6598251"


def test_csrf_regex_matches_real_shape():
    html = '<input type="hidden" name="csrfmiddlewaretoken" value="abc123XYZ">'
    m = CSRF_RE.search(html)
    assert m.group(1) == "abc123XYZ"
