"""
Offline tests for connectors/imd.py.

Fixtures are REAL NetCDF-3 files at IMD's true grid dimensions (135x129),
written with scipy, because every failure mode this parser has is a geometry
or units failure -- and those produce plausible-looking numbers for the wrong
place or the wrong scale rather than an exception.
"""
import io

import numpy as np
import pandas as pd
import pytest
from scipy.io import netcdf_file

import connectors.imd as imd
from connectors.imd import (
    IMD_NLAT,
    IMD_NLON,
    IMD_TIME_ORIGIN,
    IMD_UNDEF,
    available_years,
    imd_gridded_rainfall,
)


def _netcdf_bytes(days=3, value=10.0, nlat=IMD_NLAT, nlon=IMD_NLON,
                  time_offsets=None, drop_var=None):
    """A real NetCDF-3 classic file shaped like IMD's published product."""
    buf = io.BytesIO()
    f = netcdf_file(buf, "w")
    f.createDimension("LONGITUDE", nlon)
    f.createDimension("LATITUDE", nlat)
    f.createDimension("TIME", days)

    if drop_var != "LONGITUDE":
        v = f.createVariable("LONGITUDE", "d", ("LONGITUDE",))
        v[:] = 66.5 + 0.25 * np.arange(nlon)
    if drop_var != "LATITUDE":
        v = f.createVariable("LATITUDE", "d", ("LATITUDE",))
        v[:] = 6.5 + 0.25 * np.arange(nlat)
    if drop_var != "TIME":
        v = f.createVariable("TIME", "d", ("TIME",))
        # 2025-06-01 is 45443 days after 1900-12-31.
        base = (pd.Timestamp("2025-06-01") - IMD_TIME_ORIGIN).days
        v[:] = time_offsets if time_offsets is not None else \
            [base + i for i in range(days)]
    if drop_var != "RAINFALL":
        v = f.createVariable("RAINFALL", "f", ("TIME", "LATITUDE", "LONGITUDE"))
        v[:] = np.full((days, nlat, nlon), value, dtype="float32")
    f.flush()
    data = buf.getvalue()
    f.close()
    return data


class _FakeResponse:
    def __init__(self, content, headers=None, text=""):
        self.content = content
        self.headers = headers or {}
        self.text = text


class _FakeFetcher:
    def __init__(self, content=b"", text=""):
        self._content = content
        self._text = text
        self.posts = []

    def post(self, url, **kw):
        self.posts.append(kw.get("data"))
        return _FakeResponse(self._content, {"content-disposition": "attachment"})

    def get(self, url, **kw):
        return _FakeResponse(b"", {}, self._text)


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    monkeypatch.setattr(imd, "save_raw", lambda *a, **k: None)
    monkeypatch.setattr(imd, "write_table", lambda *a, **k: None)


# ============================================================ the empty-year trap
def test_unpublished_year_raises_instead_of_archiving_zero_bytes():
    """
    THE IMPORTANT ONE. Posting an unpublished year returns HTTP 200 with a
    valid Content-disposition header and a ZERO-BYTE body. Archiving that
    would put a 0-byte file in the immutable archive that reads forever after
    as "a year with no rain".
    """
    with pytest.raises(RuntimeError, match="empty body"):
        imd_gridded_rainfall(2026, persist=False, fetcher=_FakeFetcher(b""))


def test_html_error_page_with_a_200_is_rejected():
    """A 200 carrying an error page must not be parsed as a grid."""
    with pytest.raises(RuntimeError, match="magic 'CDF'"):
        imd_gridded_rainfall(2025, persist=False,
                             fetcher=_FakeFetcher(b"<html>error</html>"))


# ==================================================================== parsing
def test_parses_regional_daily_series():
    df = imd_gridded_rainfall(2025, persist=False,
                              fetcher=_FakeFetcher(_netcdf_bytes(days=3, value=10.0)))
    assert df["date"].nunique() == 3
    assert set(df["region"]) == set(imd.INDIA_REGIONS)
    # A uniform 10 mm grid must average to 10 mm in every region.
    assert all(abs(v - 10.0) < 1e-6 for v in df["rain_mm_per_day"])


def test_units_are_millimetres_not_tenths():
    """
    IMD publishes mm; CPC publishes tenths of a mm. Applying CPC's /10 here
    would understate Indian rainfall by an order of magnitude while still
    producing entirely plausible-looking numbers.
    """
    df = imd_gridded_rainfall(2025, persist=False,
                              fetcher=_FakeFetcher(_netcdf_bytes(days=1, value=25.0)))
    assert df["rain_mm_per_day"].iloc[0] == pytest.approx(25.0)


def test_time_axis_decodes_against_the_files_own_origin():
    """
    TIME is "days since 1900-12-31". An off-by-one origin shifts the entire
    season, which silently misaligns every departure calculation.
    """
    df = imd_gridded_rainfall(2025, persist=False,
                              fetcher=_FakeFetcher(_netcdf_bytes(days=2)))
    assert sorted(df["date"].unique()) == ["2025-06-01", "2025-06-02"]


def test_partially_masked_grid_excludes_undef_cells_from_the_mean():
    """
    A PARTIAL -999 mask is the dangerous case: an all-undef grid trips the
    "every region is undefined" guard loudly, but a partial one silently
    drags regional means toward -999 if the mask is not honoured. Here half
    the grid is undefined and the other half is a uniform 20 mm, so every
    region must read exactly 20.0 -- not some average of 20 and -999.
    """
    buf = io.BytesIO()
    f = netcdf_file(buf, "w")
    f.createDimension("LONGITUDE", IMD_NLON)
    f.createDimension("LATITUDE", IMD_NLAT)
    f.createDimension("TIME", 1)
    v = f.createVariable("LONGITUDE", "d", ("LONGITUDE",))
    v[:] = 66.5 + 0.25 * np.arange(IMD_NLON)
    v = f.createVariable("LATITUDE", "d", ("LATITUDE",))
    v[:] = 6.5 + 0.25 * np.arange(IMD_NLAT)
    v = f.createVariable("TIME", "d", ("TIME",))
    v[:] = [(pd.Timestamp("2025-06-01") - IMD_TIME_ORIGIN).days]
    grid = np.full((1, IMD_NLAT, IMD_NLON), 20.0, dtype="float32")
    grid[:, :, ::2] = IMD_UNDEF          # every other longitude is missing
    v = f.createVariable("RAINFALL", "f", ("TIME", "LATITUDE", "LONGITUDE"))
    v[:] = grid
    f.flush()
    data = buf.getvalue()
    f.close()

    df = imd_gridded_rainfall(2025, persist=False, fetcher=_FakeFetcher(data))
    assert all(abs(v - 20.0) < 1e-6 for v in df["rain_mm_per_day"])
    assert (df["valid_cells"] > 0).all()


def test_all_undefined_grid_raises_rather_than_returning_negatives():
    data = _netcdf_bytes(days=1, value=IMD_UNDEF)
    with pytest.raises(RuntimeError, match="every region is undefined"):
        imd_gridded_rainfall(2025, persist=False, fetcher=_FakeFetcher(data))


# ================================================================ geometry
def test_wrong_grid_shape_is_rejected():
    """
    A geometry change must fail loudly. Reshaping anyway would scramble
    which cells belong to which region -- plausible numbers, wrong places.
    """
    bad = _netcdf_bytes(days=1, nlat=100, nlon=100)
    with pytest.raises(RuntimeError, match="geometry changed"):
        imd_gridded_rainfall(2025, persist=False, fetcher=_FakeFetcher(bad))


def test_missing_variable_is_rejected():
    bad = _netcdf_bytes(days=1, drop_var="RAINFALL")
    with pytest.raises(RuntimeError, match="missing variable"):
        imd_gridded_rainfall(2025, persist=False, fetcher=_FakeFetcher(bad))


def test_regions_come_from_the_same_definition_as_cpc():
    """
    IMD and CPC MUST average over identical boxes, or the two series are not
    comparable and the bias correction in analytics/monsoon.py is meaningless.
    """
    from connectors.climate import INDIA_REGIONS as CPC_REGIONS
    assert imd.INDIA_REGIONS is CPC_REGIONS


# =============================================================== year listing
def test_available_years_reads_the_forms_own_options():
    html = ('<select name="RF25"><option value="select">--</option>'
            '<option value="2025">2025</option>'
            '<option value="1990">1990</option></select>')
    years = available_years(fetcher=_FakeFetcher(text=html))
    assert years == [1990, 2025]


def test_available_years_raises_if_the_form_changes():
    with pytest.raises(RuntimeError, match="no year options"):
        available_years(fetcher=_FakeFetcher(text="<html>nothing</html>"))


def test_post_sends_the_year_as_the_form_field():
    f = _FakeFetcher(_netcdf_bytes(days=1))
    imd_gridded_rainfall(2019, persist=False, fetcher=f)
    assert f.posts == [{"RF25": "2019"}]
