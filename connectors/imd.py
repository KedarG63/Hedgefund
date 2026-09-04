"""
IMD -- India Meteorological Department's own gridded daily rainfall.

THE POINT OF THIS MODULE
  IMD is the authority for Indian rainfall. Everything else in this pipeline
  that touches the monsoon (NOAA CPC) is a stand-in. This module gets IMD's
  actual data WITHOUT the API approval that is still pending.

  The API is not the only door. IMD Pune publishes its gridded rainfall
  product as plain annual NetCDF downloads behind an ordinary HTML form --
  no key, no registration, no approval. That is the authoritative dataset
  itself, not a derived or third-party copy.

WHAT IT IS
  0.25 x 0.25 degree daily gridded rainfall over India, built by IMD from
  its own gauge network. 135 longitudes (66.5-100.0E) x 129 latitudes
  (6.5-38.5N) x one value per day, in MILLIMETRES, with -999 as the missing
  marker.

  COVERAGE, read off the form itself rather than assumed: 1990-2025, 36
  years. (An earlier draft of this module claimed 1901-present from general
  knowledge; the dropdown is the authority and it starts at 1990.) That still
  covers the WMO-standard 1991-2020 normal period in full, which is what
  analytics/monsoon.py builds its climatology from.

WHY IT MATTERS FOR THIS BOOK
  It supplies the thing analytics/monsoon.py could not previously compute: a
  real DEPARTURE FROM NORMAL, on IMD's own basis, over the full WMO-standard
  1991-2020 climatology. Cumulative departure by
  region over June-September is what actually moves kharif output, rural
  incomes and food CPI -- and therefore rural-exposed equities and the RBI's
  reaction function.

ENDPOINT, VERIFIED LIVE 2026-09-03
  POST https://www.imdpune.gov.in/cmpg/Griddata/RF25.php
       body: RF25=<year>          (a plain form post, Referer required)
       -> Content-disposition: attachment; filename=RF25/ind<year>_rfp25.nc
       -> 25 MB NetCDF-3 classic (magic bytes CDF\\x01)

  Read with scipy.io.netcdf_file. NetCDF-3 classic needs no netCDF4/xarray
  dependency, and scipy is already in the stack.

TWO REAL LIMITS, NEITHER OF THEM FATAL
  1. ANNUAL FILES, PUBLISHED IN ARREARS. The year dropdown ends at 2025 as of
     2026-09-03; posting RF25=2026 returns HTTP 200 with a valid
     Content-disposition header and a ZERO-BYTE body. So a naive caller gets
     "success" and no data. `imd_gridded_rainfall` raises on an empty body
     rather than archiving a 0-byte file that would later read as a year with
     no rain.

     Consequence: IMD cannot cover the CURRENT season. CPC still does that.
     IMD supplies the climatology; CPC supplies today. See
     analytics/monsoon.py for how the two are reconciled rather than mixed.

  2. DIFFERENT GRID FROM CPC. IMD is 0.25 degree over India only; CPC is 0.5
     degree global. Both are averaged over the SAME region boxes using the
     same cosine-latitude weighting (climate.area_weighted_mean), so the
     series are comparable -- but they are not identical instruments, and the
     residual difference between them is measured explicitly rather than
     assumed away.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from connectors.climate import INDIA_REGIONS, area_weighted_mean
from core.http import Fetcher
from core.storage import save_raw, write_table

IMD_FORM_PAGE = "https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html"
IMD_RF25_POST = "https://www.imdpune.gov.in/cmpg/Griddata/RF25.php"

IMD_UNDEF = -999.0
# NetCDF TIME is "days since 1900-12-31", per the file's own units attribute.
IMD_TIME_ORIGIN = pd.Timestamp("1900-12-31")

# Grid shape as published. Checked on read: a change here means IMD altered
# the product, and reshaping anyway would scramble geography silently.
IMD_NLON, IMD_NLAT = 135, 129


def imd_session() -> Fetcher:
    """
    IMD Pune's download endpoint is a plain form post that wants a matching
    Referer. No cookie priming is needed -- verified stateless against a bare
    client, unlike NSE.
    """
    return Fetcher(base_headers={"Referer": IMD_FORM_PAGE}, min_delay=2.0,
                   timeout=180.0)


def available_years(fetcher: Fetcher | None = None) -> list[int]:
    """
    The years IMD currently offers, read off the form's own <option> list.

    Worth calling before a backfill: the newest year appears here only once
    IMD publishes it, and asking for a year that is not listed returns a
    zero-byte body rather than an error.
    """
    f = fetcher or imd_session()
    r = f.get(IMD_FORM_PAGE)
    import re
    years = [int(v) for v in re.findall(r'<option[^>]*value="(\d{4})"', r.text)]
    if not years:
        raise RuntimeError(
            "IMD: no year options found on the download form -- the page "
            "layout changed; re-read it before trusting a backfill range."
        )
    return sorted(years)


def imd_gridded_rainfall(year: int, persist: bool = True,
                         fetcher: Fetcher | None = None) -> pd.DataFrame:
    """
    One year of IMD 0.25-degree gridded daily rainfall, reduced to daily
    area-weighted means over the standard India regions.

    WHY IT MATTERS: this is the authoritative Indian rainfall record, and the
    basis for every departure-from-normal this project computes. Regional
    detail is what carries the signal -- a normal all-India total hiding a
    northwest deficit is a fertiliser and tractor story the headline conceals.

    Returns one row per (date, region), rainfall in mm/day -- the same shape
    and units as connectors.climate.cpc_india_rainfall, so the two stack
    directly.
    """
    f = fetcher or imd_session()
    r = f.post(IMD_RF25_POST, data={"RF25": str(year)})
    content = r.content

    # An unpublished year returns 200 with a valid Content-disposition and an
    # EMPTY body. Archiving that would put a zero-byte file in the immutable
    # archive that later reads as "a year with no rain".
    if not content:
        raise RuntimeError(
            f"IMD returned an empty body for {year}. That year is not "
            f"published yet (the form's dropdown is the authority -- call "
            f"available_years()). IMD publishes annual files in arrears."
        )
    if not content.startswith(b"CDF"):
        raise RuntimeError(
            f"IMD {year}: expected a NetCDF-3 file (magic 'CDF'), got "
            f"{content[:16]!r}. The endpoint may have changed or returned an "
            f"error page with a 200."
        )

    save_raw("imd", "gridded_rainfall", content, "nc",
             {"url": IMD_RF25_POST, "year": year,
              "content_disposition": r.headers.get("content-disposition")})

    from scipy.io import netcdf_file
    nc = netcdf_file(io.BytesIO(content), "r", mmap=False)
    try:
        missing = {"RAINFALL", "LATITUDE", "LONGITUDE", "TIME"} - set(nc.variables)
        if missing:
            raise RuntimeError(
                f"IMD {year}: NetCDF missing variable(s) {sorted(missing)}. "
                f"Got {sorted(nc.variables)}. Update the parser."
            )
        rain = np.asarray(nc.variables["RAINFALL"][:], dtype="float64")
        lats = np.asarray(nc.variables["LATITUDE"][:], dtype="float64")
        lons = np.asarray(nc.variables["LONGITUDE"][:], dtype="float64")
        times = np.asarray(nc.variables["TIME"][:], dtype="float64")
    finally:
        nc.close()

    if rain.shape[1:] != (IMD_NLAT, IMD_NLON):
        raise RuntimeError(
            f"IMD {year}: expected a (days, {IMD_NLAT}, {IMD_NLON}) grid, got "
            f"{rain.shape}. The product's geometry changed -- re-read the "
            f"file's own LATITUDE/LONGITUDE before parsing."
        )

    dates = IMD_TIME_ORIGIN + pd.to_timedelta(times, unit="D")

    rows = []
    for i, day in enumerate(dates):
        layer = rain[i]
        for region, bounds in INDIA_REGIONS.items():
            mm, cells = area_weighted_mean(layer, lats, lons, bounds, IMD_UNDEF)
            rows.append({
                "date": day.date().isoformat(),
                "region": region,
                # Already millimetres -- unlike CPC, which is tenths.
                "rain_mm_per_day": round(mm, 3) if mm == mm else None,
                "valid_cells": cells,
                "year": year,
            })

    out = pd.DataFrame(rows)
    if out["rain_mm_per_day"].isna().all():
        raise RuntimeError(f"IMD {year}: grid parsed but every region is undefined")
    if persist:
        write_table(out, "imd", "gridded_rainfall")
    return out


def imd_gridded_rainfall_range(start_year: int, end_year: int, persist: bool = True,
                               skip_missing: bool = True) -> pd.DataFrame:
    """
    Backfill imd_gridded_rainfall over an inclusive year range.

    One session is reused across the whole loop (rule 7 -- a client per
    request throws away the connection pool). Each file is ~25 MB, so a
    30-year normal period is roughly 750 MB of raw archive; that is the price
    of owning the climatology outright rather than trusting someone's
    published averages.

    `skip_missing` tolerates unpublished years (the newest one or two) rather
    than aborting a long backfill. Skipped years are reported in
    `.attrs["missing_years"]`, never silently dropped.
    """
    f = imd_session()
    frames, missing = [], []
    for year in range(start_year, end_year + 1):
        try:
            frames.append(imd_gridded_rainfall(year, persist=persist, fetcher=f))
        except Exception as exc:  # noqa: BLE001 -- an unpublished year is expected
            if not skip_missing:
                raise
            missing.append((year, str(exc)[:100]))

    if not frames:
        raise RuntimeError(
            f"IMD: no years retrieved between {start_year} and {end_year}; "
            f"{len(missing)} attempted, all failed."
        )
    out = pd.concat(frames, ignore_index=True)
    out.attrs["missing_years"] = missing
    return out
