"""
CLIMATE -- the India monsoon complex, plus fire hotspots.

WHY THIS MATTERS FOR THIS BOOK
  The monsoon is the single largest exogenous variable in Indian macro. It
  drives kharif sowing, rural incomes and therefore rural demand (tractors,
  two-wheelers, fertiliser, rural FMCG volumes), and food CPI -- which feeds
  straight into the RBI policy corridor this pipeline already collects. A
  weak monsoon is a rate story and a rural-demand story at the same time.

  Nothing here is a forecast. These are measured, published observations with
  known lags, kept point-in-time like everything else in the warehouse.

WHAT IS HERE AND WHAT IS NOT
  noaa_oni()            ENSO / El Nino index. Monthly, tiny, free, 1950->now.
  cpc_india_rainfall()  Daily gridded rainfall over India, from gauges.
  firms_hotspots()      Active-fire detections. NEEDS A FREE KEY (see below).

  IMD's own rainfall-departure series is NOT here. It is the authoritative
  India source and the one we actually want, but mausam.imd.gov.in serves a
  253 KB SPA shell and its API needs approval before keys are issued -- that
  approval is pending as of 2026-09-02. CPC below is the substitute: an
  independent gauge analysis on the same physical quantity, available now.
  When IMD approval lands, add it alongside rather than replacing this --
  two independent measurements of the same rainfall is a data-quality asset,
  not redundancy.

ENDPOINT STATE, VERIFIED LIVE 2026-09-01/02
  ONI    plain fixed-width text, no key, no session. 23 KB for 76 years.
  CPC    daily binary grids under year subdirectories, ~1-2 day lag
         (20260831 was present on 2026-09-02). Layout is NOT guessed -- it
         comes from the dataset's own GrADS .ctl descriptor, and the file
         size confirms it exactly (720*360*4*2 = 2,073,600 bytes, and
         Content-Length is 2,073,600).
  FIRMS  endpoint shape confirmed -- a bad key returns "Invalid MAP_KEY", so
         the path is right -- but no FIRMS_MAP_KEY is present in .env, so
         firms_hotspots() has NEVER BEEN RUN SUCCESSFULLY from this machine.
         It will raise until the key is added. Treat it as unverified code.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from core.config import require
from core.http import Fetcher
from core.storage import save_raw, write_table

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
CPC_BASE = "https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/RT"
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# ONI's three-month seasons, in order. The label is the centre month, which is
# what makes a season joinable to a monthly series.
ONI_SEASON_CENTRE = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}

# NOAA's operational thresholds on the ONI anomaly.
def _enso_phase(anom: float) -> str:
    if anom >= 0.5:
        return "el_nino"
    if anom <= -0.5:
        return "la_nina"
    return "neutral"


# --------------------------------------------------------------------- ONI
def noaa_oni(persist: bool = True) -> pd.DataFrame:
    """
    Oceanic Nino Index -- the standard ENSO measure, from NOAA CPC.

    Three-month running mean of sea-surface temperature anomaly in the Nino
    3.4 region. >= +0.5 is El Nino, <= -0.5 is La Nina.

    WHY IT MATTERS: El Nino is associated with weak or erratic Indian
    monsoons. Since it is observable months before the June-September monsoon
    resolves, it is one of the few genuinely leading inputs to a rural-demand
    or food-inflation view -- and therefore to the RBI's reaction function.

    76 years of history in 23 KB. The best signal-per-byte source in the
    pipeline.
    """
    r = Fetcher().get(ONI_URL)
    save_raw("noaa", "oni", r.content, "txt", {"url": ONI_URL})

    df = pd.read_csv(io.BytesIO(r.content), sep=r"\s+", engine="python")
    expected = {"SEAS", "YR", "TOTAL", "ANOM"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"noaa.oni: schema drift -- missing {sorted(missing)}. "
            f"Got {sorted(df.columns)}. Update the parser before trusting this."
        )

    out = df.rename(columns={
        "SEAS": "season", "YR": "year", "TOTAL": "sst_c", "ANOM": "anomaly_c",
    })
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
    out["sst_c"] = pd.to_numeric(out["sst_c"], errors="coerce")
    out["anomaly_c"] = pd.to_numeric(out["anomaly_c"], errors="coerce")
    out = out.dropna(subset=["year", "anomaly_c"])

    # A season label alone is not joinable. Stamp the centre month so this can
    # be merged against monthly CPI/IIP/policy series without a lookup table.
    out["centre_month"] = out["season"].map(ONI_SEASON_CENTRE).astype("Int64")
    out["period"] = (
        out["year"].astype(str) + "-" + out["centre_month"].astype(int).astype(str).str.zfill(2)
    )
    out["phase"] = out["anomaly_c"].apply(_enso_phase)

    if out.empty:
        raise RuntimeError("noaa.oni: parsed but no numeric rows survived")
    if persist:
        write_table(out, "noaa", "oni")
    return out


# --------------------------------------------------------------- CPC rainfall
# Grid definition, read from the dataset's own .ctl descriptor -- NOT assumed:
#   xdef 720 linear   0.25 0.50   -> lon 0.25 .. 359.75 (0-360 convention)
#   ydef 360 linear -89.75 0.50   -> lat -89.75 .. 89.75 (south to north)
#   vars 2: rain (0.1 mm/day), gnum (station count);  undef -999.0
CPC_NLON, CPC_NLAT = 720, 360
CPC_UNDEF = -999.0

# Regions roughly matching IMD's homogeneous rainfall regions. Bounds are
# (lon_west, lon_east, lat_south, lat_north) in the grid's 0-360 convention,
# which needs no wrapping for India.
INDIA_REGIONS = {
    "all_india":        (68.0, 97.5, 6.5, 37.5),
    "northwest":        (69.0, 82.0, 23.0, 37.0),   # the wheat/rice breadbasket
    "central":          (72.0, 87.0, 18.0, 26.0),   # soybean, cotton, pulses
    "south_peninsula":  (73.0, 81.0, 8.0, 18.0),
    "east_northeast":   (85.0, 97.0, 21.0, 29.0),
}


def area_weighted_mean(grid: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                       bounds: tuple, undef: float) -> tuple[float, int]:
    """
    Cosine-latitude-weighted mean of a lat/lon grid over a bounding box.

    Weighting matters: constant-degree cells are not equal-area, and an
    unweighted mean over a box spanning 6N-37N systematically overweights the
    north.

    Shared by the CPC (0.5-degree global) and IMD (0.25-degree India) readers
    on purpose. Both express regional rainfall over the SAME bounding boxes,
    and a subtly different weighting in each would produce two series that
    look comparable and are not -- which is exactly the reconciliation this
    project needs them for.

    `grid` is indexed [lat, lon]. Returns (weighted_mean_in_RAW_grid_units,
    n_valid_cells). Unit scaling is deliberately left to the caller: CPC rain
    is tenths of a mm while IMD rain is mm, and a factor baked in here would
    silently corrupt one of them.
    """
    lon_w, lon_e, lat_s, lat_n = bounds

    ix = np.where((lons >= lon_w) & (lons <= lon_e))[0]
    iy = np.where((lats >= lat_s) & (lats <= lat_n))[0]
    if ix.size == 0 or iy.size == 0:
        raise RuntimeError(f"empty grid selection for bounds {bounds}")

    box = grid[np.ix_(iy, ix)]
    valid = box != undef
    if not valid.any():
        return float("nan"), 0

    weights = np.cos(np.deg2rad(lats[iy]))[:, None] * np.ones((1, ix.size))
    w = np.where(valid, weights, 0.0)
    return float((np.where(valid, box, 0.0) * w).sum() / w.sum()), int(valid.sum())


def _cpc_area_mean(grid: np.ndarray, bounds: tuple) -> tuple[float, int]:
    """CPC's global 0.5-degree grid, per its .ctl descriptor."""
    lons = 0.25 + 0.5 * np.arange(CPC_NLON)
    lats = -89.75 + 0.5 * np.arange(CPC_NLAT)
    return area_weighted_mean(grid, lats, lons, bounds, CPC_UNDEF)


def cpc_india_rainfall(day: str, persist: bool = True) -> pd.DataFrame:
    """
    Daily gauge-analysed rainfall over India and four sub-regions, from NOAA
    CPC's global 0.5-degree unified gauge analysis.

    `day` is an ISO date ("2026-08-31"). Files appear with a ~1-2 day lag.

    WHY IT MATTERS: this is the monsoon, measured. Cumulative departure from
    normal over June-September is what moves kharif output, rural incomes and
    food CPI. Sub-regions matter more than the national mean -- a normal
    all-India total hiding a northwest deficit is a fertiliser and tractor
    story that the headline number conceals.

    STANDS IN FOR IMD, does not replace it. IMD's own departure series is the
    authoritative India source and is pending API approval; when it arrives,
    keep both.

    Returns one row per region for the given day, in mm/day.
    """
    stamp = pd.to_datetime(day).strftime("%Y%m%d")
    year = stamp[:4]
    name = f"PRCP_CU_GAUGE_V1.0GLB_0.50deg.lnx.{stamp}.RT"
    url = f"{CPC_BASE}/{year}/{name}"

    r = Fetcher().get(url)
    content = r.content
    save_raw("cpc", "india_rainfall", content, "bin", {"url": url, "day": stamp})

    expected_bytes = CPC_NLON * CPC_NLAT * 4 * 2
    if len(content) != expected_bytes:
        # The grid is fixed by the .ctl. A size change means the product
        # changed shape, and reshaping anyway would silently scramble geography.
        raise RuntimeError(
            f"CPC {stamp}: expected {expected_bytes} bytes "
            f"({CPC_NLON}x{CPC_NLAT} float32 x 2 vars), got {len(content)}. "
            f"The product layout changed -- re-read the .ctl before parsing."
        )

    both = np.frombuffer(content, dtype="<f4")  # little_endian per the .ctl
    rain = both[: CPC_NLON * CPC_NLAT].reshape(CPC_NLAT, CPC_NLON)
    gnum = both[CPC_NLON * CPC_NLAT:].reshape(CPC_NLAT, CPC_NLON)

    rows = []
    for region, bounds in INDIA_REGIONS.items():
        rain_tenths, cells = _cpc_area_mean(rain, bounds)
        stations, _ = _cpc_area_mean(gnum, bounds)
        # rain is tenths of a mm/day per the .ctl; gnum is already a count.
        mm = rain_tenths / 10.0
        rows.append({
            "date": pd.to_datetime(day).date().isoformat(),
            "region": region,
            "rain_mm_per_day": round(mm, 3) if mm == mm else None,
            "valid_cells": cells,
            "mean_stations_per_cell": round(stations, 3) if stations == stations else None,
        })

    out = pd.DataFrame(rows)
    if out["rain_mm_per_day"].isna().all():
        raise RuntimeError(f"CPC {stamp}: grid parsed but every region is undefined")
    if persist:
        write_table(out, "cpc", "india_rainfall")
    return out


def cpc_india_rainfall_range(start: str, end: str, persist: bool = True,
                             skip_missing: bool = True) -> pd.DataFrame:
    """
    Backfill cpc_india_rainfall over an inclusive date range.

    `skip_missing` tolerates individual absent days (CPC occasionally has
    gaps, and the newest 1-2 days are not published yet) rather than aborting
    a 120-day backfill on one 404. Set it False when you need a complete
    range and want the gap to be loud.

    A skipped day is REPORTED, not silently dropped: the returned frame's
    attrs carry the list, because a monsoon cumulative built over an unknown
    number of missing days is a different quantity than one built over a
    complete range (see analytics/monsoon.py).
    """
    days = pd.date_range(start, end, freq="D")
    frames, missing = [], []
    for day in days:
        iso = day.date().isoformat()
        try:
            frames.append(cpc_india_rainfall(iso, persist=persist))
        except Exception as exc:  # noqa: BLE001 -- a 404 here is expected, not exceptional
            if not skip_missing:
                raise
            missing.append((iso, str(exc)[:80]))

    if not frames:
        raise RuntimeError(
            f"CPC: no days retrieved between {start} and {end}. "
            f"{len(missing)} attempted, all failed."
        )
    out = pd.concat(frames, ignore_index=True)
    out.attrs["missing_days"] = missing
    return out


# ------------------------------------------------------------------- FIRMS
# India + the Indonesia/Malaysia palm belt. (west, south, east, north)
FIRMS_AREAS = {
    "india": (68.0, 6.0, 98.0, 38.0),
    "palm_belt": (95.0, -8.0, 120.0, 8.0),
}


def firms_hotspots(area: str = "india", source: str = "VIIRS_SNPP_NRT",
                   days: int = 1, persist: bool = True) -> pd.DataFrame:
    """
    NASA FIRMS active-fire detections in a bounding box.

    WHY IT MATTERS: two distinct theses. Over India, post-harvest stubble
    burning in Punjab/Haryana times the kharif harvest and drives the
    north-India air-quality season. Over the palm belt, burning is a supply
    signal for palm oil -- India is the world's largest edible-oil importer,
    so it feeds the import bill and food CPI.

    NOT VERIFIED. FIRMS_MAP_KEY is not present in .env, so this function has
    never returned data on this machine. The endpoint SHAPE is confirmed -- a
    deliberately bad key returns "Invalid MAP_KEY", which means the path and
    parameter order are right -- but the parse below is written against
    FIRMS's documented CSV columns, not against a response we have actually
    seen. Verify the first real call before trusting any of it.

    Get a free key at https://firms.modaps.eosdis.nasa.gov/api/area/ and put
    it in .env as FIRMS_MAP_KEY.
    """
    if area not in FIRMS_AREAS:
        raise ValueError(f"Unknown area {area!r}; known: {sorted(FIRMS_AREAS)}")
    key = require("FIRMS_MAP_KEY")
    west, south, east, north = FIRMS_AREAS[area]
    box = f"{west},{south},{east},{north}"
    url = f"{FIRMS_BASE}/{key}/{source}/{box}/{days}"

    r = Fetcher().get(url)
    body = r.content
    # FIRMS signals key/permission failures with a 200 and a plain-text body.
    head = body[:200].decode("utf-8", "replace").strip()
    if head.lower().startswith("invalid") or "error" in head.lower()[:40]:
        raise RuntimeError(f"FIRMS refused the request: {head!r}")

    # Archive with the key stripped from the recorded URL -- a raw sidecar is
    # not a place for a credential (CLAUDE.md: nothing sensitive in sidecars).
    save_raw("firms", f"hotspots_{area}", body, "csv",
             {"url": url.replace(key, "<FIRMS_MAP_KEY>"), "source": source,
              "area": area, "days": days})

    df = pd.read_csv(io.BytesIO(body))
    if df.empty:
        raise RuntimeError(
            f"FIRMS returned no detections for {area}/{source}/{days}d. "
            f"That can be genuine in a wet season -- but this connector has "
            f"never been verified, so confirm against the FIRMS web map first."
        )
    for col in ("latitude", "longitude", "brightness", "frp", "confidence"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["area"] = area
    df["source_sensor"] = source

    if persist:
        write_table(df, "firms", f"hotspots_{area}")
    return df
