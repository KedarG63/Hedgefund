"""
Offline tests for analytics/monsoon.py.

The coverage tests matter most: a cumulative rainfall total built over an
unknown number of missing days is not a small version of the season total, it
is a different quantity, and the denominator has to travel with the number.
"""
import pandas as pd
import pytest

import analytics.monsoon as mn
from analytics.monsoon import LEADING_ONI_SEASONS, enso_state, monsoon_progress


def _oni_frame(rows):
    return pd.DataFrame(rows, columns=["season", "year", "sst_c", "anomaly_c",
                                       "period", "phase"])


@pytest.fixture(autouse=True)
def _no_write(monkeypatch):
    monkeypatch.setattr(mn, "write_table", lambda *a, **k: None)


# ==================================================================== ENSO
def test_enso_state_reports_strengthening_trajectory(monkeypatch):
    """
    The real 2026 shape: neutral through winter, crossing into El Nino in the
    AMJ/MJJ window that leads the monsoon.
    """
    rows = [
        ("JAS", 2025, 27.0, -0.30, "2025-08", "neutral"),
        ("ASO", 2025, 27.0, -0.20, "2025-09", "neutral"),
        ("SON", 2025, 27.0, -0.10, "2025-10", "neutral"),
        ("DJF", 2026, 26.15, -0.39, "2026-01", "neutral"),
        ("MAM", 2026, 28.09, 0.46, "2026-04", "neutral"),
        ("MJJ", 2026, 29.02, 1.39, "2026-06", "el_nino"),
    ]
    monkeypatch.setattr(mn, "_oni", lambda: _oni_frame(rows))
    out = enso_state(persist=False).iloc[0]
    assert out["phase"] == "el_nino"
    assert out["anomaly_c"] == pytest.approx(1.39)
    assert out["trajectory"] == "strengthening"
    # bool(), not `is True` -- pandas hands back np.True_, which is truthy but
    # not the singleton.
    assert bool(out["is_leading_monsoon_season"])


def test_enso_state_flags_the_seasons_that_actually_lead_the_monsoon():
    """
    AMJ and MJJ are known before/at onset, which is what makes them usable as
    a prior. A DJF reading is not a monsoon signal.
    """
    assert set(LEADING_ONI_SEASONS) == {"AMJ", "MJJ"}


def test_enso_state_decaying_trajectory(monkeypatch):
    rows = [
        ("JAS", 2025, 29.0, 1.60, "2025-08", "el_nino"),
        ("SON", 2025, 28.0, 0.90, "2025-10", "el_nino"),
        ("DJF", 2026, 27.0, 0.20, "2026-01", "neutral"),
    ]
    monkeypatch.setattr(mn, "_oni", lambda: _oni_frame(rows))
    assert enso_state(persist=False).iloc[0]["trajectory"] == "decaying"


def test_enso_state_raises_with_a_runnable_instruction(monkeypatch):
    monkeypatch.setattr(mn, "_oni", lambda: _oni_frame([]))
    with pytest.raises(RuntimeError, match="run_daily.py --job noaa_oni"):
        enso_state(persist=False)


# ================================================================= rainfall
def _rain_frame(dates, region="all_india", mm=10.0):
    return pd.DataFrame([
        {"date": d, "region": region, "rain_mm_per_day": mm, "valid_cells": 2466}
        for d in dates
    ])


def test_monsoon_progress_reports_coverage_alongside_the_total(monkeypatch):
    """
    THE IMPORTANT ONE. 12 observed days out of 60 elapsed is not 20% of a
    season -- it is an unknown fraction. The denominator must travel with the
    number so nobody reads a partial cumulative as a seasonal total.
    """
    dates = pd.date_range("2026-06-01", periods=12, freq="D").date.astype(str).tolist()
    # Push the as-of date out so elapsed >> observed.
    dates.append("2026-07-30")
    monkeypatch.setattr(mn, "_rainfall", lambda: _rain_frame(dates))
    out = monsoon_progress(persist=False).iloc[0]
    assert out["days_observed"] == 13
    assert out["season_days_elapsed"] == 60
    assert out["coverage_pct"] == pytest.approx(21.7, abs=0.2)


def test_monsoon_progress_only_counts_june_to_september(monkeypatch):
    """
    The southwest monsoon is June-September. A May or October day in the
    archive must not inflate the season total.
    """
    dates = ["2026-05-31", "2026-06-01", "2026-06-02", "2026-10-01"]
    monkeypatch.setattr(mn, "_rainfall", lambda: _rain_frame(dates, mm=10.0))
    out = monsoon_progress(persist=False).iloc[0]
    assert out["days_observed"] == 2
    assert out["cumulative_mm"] == pytest.approx(20.0)


def test_monsoon_progress_separates_regions(monkeypatch):
    """
    A normal all-India total hiding a northwest deficit is the exact failure
    the regional split exists to prevent.
    """
    dates = pd.date_range("2026-06-01", periods=10, freq="D").date.astype(str).tolist()
    df = pd.concat([
        _rain_frame(dates, region="all_india", mm=8.0),
        _rain_frame(dates, region="northwest", mm=1.0),
    ], ignore_index=True)
    monkeypatch.setattr(mn, "_rainfall", lambda: df)
    out = monsoon_progress(persist=False).set_index("region")
    assert out.loc["all_india"]["cumulative_mm"] == pytest.approx(80.0)
    assert out.loc["northwest"]["cumulative_mm"] == pytest.approx(10.0)


def test_monsoon_progress_raises_outside_the_season(monkeypatch):
    """
    Not a pipeline failure -- run_daily catches this and prints it. But it
    must be visible rather than returning a silently empty frame.
    """
    monkeypatch.setattr(mn, "_rainfall",
                        lambda: _rain_frame(["2026-01-15", "2026-02-20"]))
    with pytest.raises(RuntimeError, match="June-September"):
        monsoon_progress(persist=False)


def test_monsoon_progress_raises_on_empty_warehouse(monkeypatch):
    monkeypatch.setattr(mn, "_rainfall",
                        lambda: pd.DataFrame(columns=["date", "region",
                                                      "rain_mm_per_day", "valid_cells"]))
    with pytest.raises(RuntimeError, match="run_daily.py --job cpc_rainfall"):
        monsoon_progress(persist=False)


# ======================================================= IMD normals / departure
def _daily(years, mm, region="all_india", start="06-01", end_month=9, end_day=30):
    """Uniform daily rainfall across full Jun-Sep seasons for the given years."""
    rows = []
    for y in years:
        for d in pd.date_range(f"{y}-{start}", f"{y}-{end_month:02d}-{end_day:02d}",
                               freq="D"):
            rows.append({"date": d.date().isoformat(), "region": region,
                         "rain_mm_per_day": mm, "valid_cells": 100})
    return pd.DataFrame(rows)


def test_imd_normals_averages_across_the_climatology(monkeypatch):
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: _daily(range(1991, 2021), 5.0))
    out = mn.imd_normals(1991, 2020, persist=False).iloc[0]
    # 122 days Jun 1 - Sep 30, 5 mm/day.
    assert out["normal_mm"] == pytest.approx(610.0, abs=0.5)
    assert out["n_years"] == 30
    assert out["source"] == "imd"


def test_normal_is_truncated_to_the_same_day_of_year(monkeypatch):
    """
    THE IMPORTANT ONE. Comparing a season-to-date total against a FULL-season
    normal is the easiest way to manufacture a fake deficit. Closing the
    normal on the same calendar day is what prevents it.
    """
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: _daily(range(1991, 2021), 5.0))
    full = mn.imd_normals(1991, 2020, persist=False).iloc[0]["normal_mm"]
    partial = mn.imd_normals(1991, 2020, upto_month=7, upto_day=1,
                             persist=False).iloc[0]["normal_mm"]
    assert partial == pytest.approx(155.0, abs=0.5)   # 31 days of June + Jul 1
    assert partial < full


def test_departure_uses_imd_directly_when_imd_covers_the_season(monkeypatch):
    imd = pd.concat([_daily(range(1991, 2021), 5.0), _daily([2025], 6.0)])
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: imd)
    monkeypatch.setattr(mn, "_rainfall", lambda: pd.DataFrame(
        columns=["date", "region", "rain_mm_per_day", "valid_cells"]))
    out = mn.monsoon_departure(persist=False).iloc[0]
    assert out["basis"] == "imd"
    assert out["cpc_imd_ratio"] == 1.0
    assert out["departure_pct"] == pytest.approx(20.0, abs=0.5)  # 6 vs 5 mm/day


def test_departure_bias_corrects_a_cpc_observation(monkeypatch):
    """
    IMD has no current year, so the season is observed by CPC. CPC reads
    1.25x IMD on the overlap years, so a CPC total must be DIVIDED by 1.25
    before it can be quoted against an IMD normal.
    """
    imd = pd.concat([_daily(range(1991, 2021), 5.0), _daily([2023, 2024], 5.0)])
    cpc = pd.concat([_daily([2023, 2024], 6.25), _daily([2026], 6.25)])
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: imd)
    monkeypatch.setattr(mn, "_rainfall", lambda: cpc)

    out = mn.monsoon_departure(persist=False).iloc[0]
    assert out["basis"] == "cpc_bias_corrected_to_imd"
    assert out["cpc_imd_ratio"] == pytest.approx(1.25, abs=0.01)
    # 6.25 / 1.25 == 5.0 == the normal, so the departure is ~0 despite the raw
    # CPC total being 25% above it.
    assert out["departure_pct"] == pytest.approx(0.0, abs=0.5)


def test_departure_refuses_to_compare_across_instruments_without_a_bias(monkeypatch):
    """
    No overlap years -> no measurable bias -> NO departure. Reporting one
    anyway would be a CPC total against an IMD normal, which is two different
    instruments read as one.
    """
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: _daily(range(1991, 2021), 5.0))
    monkeypatch.setattr(mn, "_rainfall", lambda: _daily([2026], 6.0))
    out = mn.monsoon_departure(persist=False).iloc[0]
    assert out["basis"] == "cpc_uncorrected"
    assert out["departure_pct"] is None
    assert out["observed_mm"] == pytest.approx(732.0, abs=1.0)   # still reported


def test_bias_ignores_years_where_cpc_coverage_is_too_sparse(monkeypatch):
    """
    A CPC year with a handful of archived days against IMD's 122 would read as
    a huge dry bias that is purely a coverage artefact.
    """
    imd = _daily([2023, 2024], 5.0)
    sparse = _daily([2023], 5.0, end_month=6, end_day=10)   # 10 days only
    cpc = pd.concat([sparse, _daily([2024], 6.0)])
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: imd)
    monkeypatch.setattr(mn, "_rainfall", lambda: cpc)

    out = mn.cpc_imd_bias(overlap_years=(2023, 2024), persist=False).iloc[0]
    assert out["n_years"] == 1                      # 2023 dropped
    assert out["ratio"] == pytest.approx(1.2, abs=0.01)


def test_bias_raises_when_every_overlap_year_is_too_sparse(monkeypatch):
    imd = _daily([2023], 5.0)
    monkeypatch.setattr(mn, "_imd_rainfall", lambda: imd)
    monkeypatch.setattr(mn, "_rainfall",
                        lambda: _daily([2023], 5.0, end_month=6, end_day=5))
    with pytest.raises(RuntimeError, match="coverage is too sparse"):
        mn.cpc_imd_bias(overlap_years=(2023,), persist=False)
