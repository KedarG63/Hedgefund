"""
Offline tests for connectors/climate.py.

The CPC fixture is a REAL synthetic grid at the true dimensions (720x360,
two float32 variables, little-endian) rather than a stub, because every bug
this parser can have is a geometry bug -- a flipped latitude axis or a
transposed reshape produces perfectly plausible numbers for the wrong place.
"""
import numpy as np
import pandas as pd
import pytest

import connectors.climate as cl
from connectors.climate import (
    CPC_NLAT,
    CPC_NLON,
    CPC_UNDEF,
    INDIA_REGIONS,
    _cpc_area_mean,
    _enso_phase,
    cpc_india_rainfall,
    noaa_oni,
)

ONI_FIXTURE = b""" SEAS  YR   TOTAL   ANOM
  DJF 1950  25.01  -1.32
  JFM 1950  25.36  -1.20
  AMJ 2026  28.74   0.95
  MJJ 2026  29.02   1.39
"""


class _FakeResponse:
    def __init__(self, content):
        self.content = content


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    monkeypatch.setattr(cl, "save_raw", lambda *a, **k: None)
    monkeypatch.setattr(cl, "write_table", lambda *a, **k: None)


def _fake_fetcher(content):
    class F:
        def get(self, url, **kw):
            return _FakeResponse(content)
    return lambda *a, **k: F()


# ==================================================================== ENSO
@pytest.mark.parametrize("anom,expected", [
    (1.39, "el_nino"), (0.5, "el_nino"), (0.49, "neutral"),
    (0.0, "neutral"), (-0.49, "neutral"), (-0.5, "la_nina"), (-1.32, "la_nina"),
])
def test_enso_phase_thresholds(anom, expected):
    """NOAA's operational thresholds are inclusive at +/-0.5."""
    assert _enso_phase(anom) == expected


def test_oni_parses_and_stamps_a_joinable_period(monkeypatch):
    """
    A season label like "MJJ" cannot be joined to a monthly series. The centre
    month is what makes ONI mergeable against CPI/policy data without a
    lookup table at every call site.
    """
    monkeypatch.setattr(cl, "Fetcher", _fake_fetcher(ONI_FIXTURE))
    df = noaa_oni(persist=False)
    assert len(df) == 4
    last = df.iloc[-1]
    assert last["season"] == "MJJ"
    assert last["centre_month"] == 6
    assert last["period"] == "2026-06"
    assert last["phase"] == "el_nino"


def test_oni_raises_on_schema_drift(monkeypatch):
    monkeypatch.setattr(cl, "Fetcher", _fake_fetcher(b" SEAS  YR  SOMETHING\n DJF 1950 1.0\n"))
    with pytest.raises(RuntimeError, match="schema drift"):
        noaa_oni(persist=False)


# ==================================================================== CPC
def _grid(rain_value=100.0, station_value=2.0):
    """
    A full-size CPC binary payload. rain is in TENTHS of a mm/day, so 100.0
    here must come back as 10.0 mm/day.
    """
    rain = np.full((CPC_NLAT, CPC_NLON), rain_value, dtype="<f4")
    gnum = np.full((CPC_NLAT, CPC_NLON), station_value, dtype="<f4")
    return rain.tobytes() + gnum.tobytes()


def test_cpc_parses_full_size_grid_and_scales_tenths(monkeypatch):
    monkeypatch.setattr(cl, "Fetcher", _fake_fetcher(_grid(100.0)))
    df = cpc_india_rainfall("2026-08-31", persist=False)
    assert set(df["region"]) == set(INDIA_REGIONS)
    # 100 tenths of a mm/day == 10.0 mm/day.
    assert all(abs(v - 10.0) < 1e-6 for v in df["rain_mm_per_day"])


def test_cpc_rejects_a_wrong_sized_payload(monkeypatch):
    """
    The grid is fixed by the .ctl descriptor. Reshaping a differently-sized
    payload anyway would silently scramble geography rather than fail.
    """
    monkeypatch.setattr(cl, "Fetcher", _fake_fetcher(b"\x00" * 1234))
    with pytest.raises(RuntimeError, match="layout changed"):
        cpc_india_rainfall("2026-08-31", persist=False)


def test_cpc_station_count_is_not_scaled_like_rainfall(monkeypatch):
    """
    REGRESSION. rain is tenths-of-mm and gnum is a plain count, so a single
    scale factor applied to both corrupts one. An earlier version also
    unpacked the (mean, n_cells) tuple backwards and reported 7280 stations
    per cell for a region that merely had 728 cells.
    """
    monkeypatch.setattr(cl, "Fetcher", _fake_fetcher(_grid(100.0, station_value=2.0)))
    df = cpc_india_rainfall("2026-08-31", persist=False)
    assert all(abs(v - 2.0) < 1e-6 for v in df["mean_stations_per_cell"])
    assert all(df["valid_cells"] > 100)


def test_cpc_undef_cells_are_excluded_not_averaged_in(monkeypatch):
    """
    -999.0 is the undefined marker. Averaging it in would drag every regional
    mean deeply negative, which is not a plausible rainfall and would be
    caught -- but a PARTIAL mask silently biases the number instead.
    """
    rain = np.full((CPC_NLAT, CPC_NLON), CPC_UNDEF, dtype="<f4")
    # Leave one real value inside the all_india box (lon 68-97.5, lat 6.5-37.5).
    iy = int((20.0 + 89.75) / 0.5)
    ix = int((78.0 - 0.25) / 0.5)
    rain[iy, ix] = 50.0
    gnum = np.full((CPC_NLAT, CPC_NLON), CPC_UNDEF, dtype="<f4")
    gnum[iy, ix] = 1.0
    monkeypatch.setattr(cl, "Fetcher", _fake_fetcher(rain.tobytes() + gnum.tobytes()))

    df = cpc_india_rainfall("2026-08-31", persist=False)
    all_india = df[df["region"] == "all_india"].iloc[0]
    assert all_india["valid_cells"] == 1
    assert abs(all_india["rain_mm_per_day"] - 5.0) < 1e-6


def test_cpc_area_mean_latitude_orientation():
    """
    THE GEOMETRY TEST. The .ctl says ydef runs -89.75 NORTHWARD, so row 0 is
    the South Pole. A flipped axis still produces plausible-looking rainfall
    for entirely the wrong hemisphere, which no value check would catch.
    """
    grid = np.zeros((CPC_NLAT, CPC_NLON), dtype="<f4")
    # Northern-hemisphere India latitude ~20N -> row (20 + 89.75)/0.5.
    iy_north = int((20.0 + 89.75) / 0.5)
    ix = int((78.0 - 0.25) / 0.5)
    grid[iy_north, ix] = 100.0

    inside, cells = _cpc_area_mean(grid, INDIA_REGIONS["all_india"])
    assert inside > 0, "20N/78E must fall inside the India box"

    # The mirror-image southern latitude must NOT be inside the India box.
    grid_south = np.zeros((CPC_NLAT, CPC_NLON), dtype="<f4")
    grid_south[int((-20.0 + 89.75) / 0.5), ix] = 100.0
    outside, _ = _cpc_area_mean(grid_south, INDIA_REGIONS["all_india"])
    assert outside == 0, "20S must fall OUTSIDE the India box -- latitude axis is flipped"


def test_cpc_area_mean_is_cosine_latitude_weighted():
    """
    0.5-degree cells are not equal-area. An unweighted mean over a box
    spanning 6N-37N systematically overweights the north.
    """
    grid = np.zeros((CPC_NLAT, CPC_NLON), dtype="<f4")
    bounds = INDIA_REGIONS["all_india"]
    lat_s, lat_n = bounds[2], bounds[3]
    ix = int((78.0 - 0.25) / 0.5)
    south_row = int((lat_s + 0.5 + 89.75) / 0.5)
    north_row = int((lat_n - 0.5 + 89.75) / 0.5)

    grid[south_row, ix] = 100.0
    south_only, _ = _cpc_area_mean(grid, bounds)
    grid[south_row, ix] = 0.0
    grid[north_row, ix] = 100.0
    north_only, _ = _cpc_area_mean(grid, bounds)

    # Same value, lower latitude -> larger cell -> larger weighted contribution.
    assert south_only > north_only


def test_firms_requires_a_key(monkeypatch):
    """
    FIRMS_MAP_KEY is not present in .env, so this connector has never run
    successfully. It must fail loudly rather than appear to work.
    """
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    monkeypatch.setattr(cl, "require",
                        lambda k: (_ for _ in ()).throw(RuntimeError(f"{k} is not set")))
    with pytest.raises(RuntimeError, match="FIRMS_MAP_KEY"):
        cl.firms_hotspots(persist=False)


def test_firms_rejects_an_unknown_area():
    with pytest.raises(ValueError, match="Unknown area"):
        cl.firms_hotspots(area="atlantis", persist=False)
