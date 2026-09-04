"""
Monsoon and ENSO analytics.

WHAT THIS IS FOR
  The southwest monsoon (June-September) is the largest exogenous driver of
  Indian rural demand and food inflation. Two questions matter to a book:

    1. What does the ENSO state say about the season BEFORE it resolves?
       That is `enso_state()`, and it is available with 76 years of history
       right now, because ONI is a 23 KB text file.

    2. How is the season actually tracking? That is `monsoon_progress()`,
       and it is limited by how much CPC rainfall we have archived -- there
       is no free backfill of somebody else's history here, only what we
       collect.

  Keeping these separate is deliberate. The first is a prior; the second is
  the observation that updates it. Presenting them as one number would hide
  which half is doing the work.

HONEST LIMITS
  * ENSO is a correlate of monsoon outcomes, not a mechanism and not a
    forecast. El Nino years skew dry in India, but the relationship is
    probabilistic and has notable exceptions. `enso_state()` reports the
    state and the historical base rate; it does not predict rainfall.
  * CPC is a gauge analysis, not IMD's official departure series. IMD is the
    authority for India and is pending API approval (2026-09-02). When it
    lands, keep both and reconcile -- do not silently swap one for the other,
    because their normals differ.
"""
from __future__ import annotations

import pandas as pd

from core.asof import asof_sql
from core.storage import db, register_views, write_table

# The monsoon season, and the ONI seasons that lead it. AMJ and MJJ are known
# before/at onset, which is what makes them usable as a prior.
MONSOON_MONTHS = (6, 7, 8, 9)
LEADING_ONI_SEASONS = ("AMJ", "MJJ")


def _oni() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        return con.execute(asof_sql(
            "noaa_oni",
            columns="season, year, sst_c, anomaly_c, period, phase",
            order_by="year, period",
        )).df()
    finally:
        con.close()


def enso_state(persist: bool = True) -> pd.DataFrame:
    """
    Current ENSO state, its recent trajectory, and the historical base rate
    for the phase we are in.

    Returns a single-row frame: latest season, anomaly, phase, the 6-season
    change in anomaly (is it strengthening or decaying), and how many prior
    years shared this phase at the same point in the calendar.

    WHY IT MATTERS: an El Nino building into the monsoon window is a prior on
    weak rural demand and firmer food CPI -- which is a rates story, since it
    feeds the RBI reaction function this pipeline already tracks. It is a
    prior, not a forecast: see the module docstring.
    """
    oni = _oni()
    if oni.empty:
        raise RuntimeError(
            "noaa_oni is empty. Run: python run_daily.py --job noaa_oni"
        )

    oni = oni.sort_values(["year", "period"]).reset_index(drop=True)
    latest = oni.iloc[-1]
    trail = oni.tail(6)
    delta = float(latest["anomaly_c"] - trail.iloc[0]["anomaly_c"])

    # Base rate: of all prior years, how many were in this phase in this same
    # ONI season? Answers "how unusual is this", not "what happens next".
    same_season = oni[oni["season"] == latest["season"]]
    phase_counts = same_season["phase"].value_counts().to_dict()
    n_prior = int(len(same_season) - 1)

    out = pd.DataFrame([{
        "as_of_season": latest["season"],
        "as_of_year": int(latest["year"]),
        "period": latest["period"],
        "anomaly_c": float(latest["anomaly_c"]),
        "phase": latest["phase"],
        "anomaly_change_6_seasons": round(delta, 2),
        "trajectory": ("strengthening" if delta > 0.25
                       else "decaying" if delta < -0.25 else "stable"),
        "is_leading_monsoon_season": latest["season"] in LEADING_ONI_SEASONS,
        "prior_years_same_season": n_prior,
        "prior_el_nino_share": round(
            phase_counts.get("el_nino", 0) / len(same_season), 3) if len(same_season) else None,
        "prior_la_nina_share": round(
            phase_counts.get("la_nina", 0) / len(same_season), 3) if len(same_season) else None,
    }])

    if persist:
        write_table(out, "analytics", "enso_state")
    return out


def _imd_rainfall() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        return con.execute("""
            SELECT date, region, any_value(rain_mm_per_day) AS rain_mm_per_day
            FROM imd_gridded_rainfall
            GROUP BY date, region
            ORDER BY region, date
        """).df()
    finally:
        con.close()


def _season_window_sum(df: pd.DataFrame, upto_month: int, upto_day: int) -> pd.DataFrame:
    """
    Cumulative rainfall from 1 June to (upto_month, upto_day) for every
    (year, region) in `df`.

    The window is closed on the SAME calendar day in every year. Comparing a
    season-to-date total against a full-season normal is the single easiest
    way to manufacture a fake deficit, so the normal is always truncated to
    the same day-of-year as the observation.
    """
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["year"] = d["date"].dt.year
    d["month"] = d["date"].dt.month
    d["day"] = d["date"].dt.day
    in_window = (
        d["month"].isin(MONSOON_MONTHS)
        & ((d["month"] < upto_month) | ((d["month"] == upto_month) & (d["day"] <= upto_day)))
    )
    w = d[in_window]
    return (w.groupby(["year", "region"], as_index=False)
             .agg(cumulative_mm=("rain_mm_per_day", "sum"),
                  days=("rain_mm_per_day", "size")))


def imd_normals(start_year: int = 1991, end_year: int = 2020,
                upto_month: int = 9, upto_day: int = 30,
                persist: bool = True) -> pd.DataFrame:
    """
    IMD's own June-to-date rainfall normal per region, over a climatology
    period.

    Defaults to 1991-2020, the WMO-standard 30-year normal period, which the
    IMD download form covers in full (it offers 1990-2025).

    WHY IT MATTERS: this is what makes a *departure* computable at all. Until
    IMD data was reachable, this project could report cumulative rainfall but
    had nothing legitimate to compare it against -- CPC's own normals are a
    different instrument's normals, and quoting a departure against the wrong
    baseline is worse than quoting none.

    Returns one row per region: the mean and standard deviation of the
    June-1-to-(upto_month, upto_day) cumulative across the climatology years.
    """
    imd = _imd_rainfall()
    if imd.empty:
        raise RuntimeError(
            "imd_gridded_rainfall is empty. Backfill first: "
            "connectors.imd.imd_gridded_rainfall_range(1991, 2020)"
        )

    per_year = _season_window_sum(imd, upto_month, upto_day)
    clim = per_year[(per_year["year"] >= start_year) & (per_year["year"] <= end_year)]
    if clim.empty:
        raise RuntimeError(
            f"No IMD years in {start_year}-{end_year}. Available: "
            f"{sorted(per_year['year'].unique())[:5]}..."
        )

    out = (clim.groupby("region", as_index=False)
               .agg(normal_mm=("cumulative_mm", "mean"),
                    normal_sd=("cumulative_mm", "std"),
                    n_years=("cumulative_mm", "size")))
    out["normal_mm"] = out["normal_mm"].round(1)
    out["normal_sd"] = out["normal_sd"].round(1)
    out["climatology"] = f"{start_year}-{end_year}"
    out["window_end"] = f"{upto_month:02d}-{upto_day:02d}"
    out["source"] = "imd"

    if persist:
        write_table(out, "analytics", "imd_normals")
    return out


def cpc_imd_bias(overlap_years: tuple[int, ...] = (2023, 2024, 2025),
                 upto_month: int = 9, upto_day: int = 30,
                 persist: bool = True) -> pd.DataFrame:
    """
    How far CPC's regional rainfall sits from IMD's, measured on years where
    BOTH exist and over the identical calendar window.

    WHY THIS EXISTS. IMD publishes annual files in arrears, so it cannot
    cover the current season -- CPC does. But CPC observations against IMD
    normals is exactly the apples-to-oranges comparison that makes a
    "departure" meaningless: different gauge networks, different grids (0.5
    global vs 0.25 India-only), different interpolation.

    So instead of assuming they agree, measure it. `ratio` is
    mean(CPC) / mean(IMD) per region over the overlap years. A ratio of 1.0
    means the two instruments agree; anything else is the correction that has
    to be applied before a CPC observation can be quoted against an IMD
    normal.

    Returns one row per region. Requires CPC AND IMD coverage of the same
    years -- backfill both before calling.
    """
    cpc = _season_window_sum(_rainfall(), upto_month, upto_day)
    imd = _season_window_sum(_imd_rainfall(), upto_month, upto_day)
    if cpc.empty or imd.empty:
        raise RuntimeError(
            "Need BOTH cpc_india_rainfall and imd_gridded_rainfall in the "
            "warehouse to measure the bias between them."
        )

    merged = cpc.merge(imd, on=["year", "region"], suffixes=("_cpc", "_imd"))
    merged = merged[merged["year"].isin(overlap_years)]
    if merged.empty:
        raise RuntimeError(
            f"No overlapping (year, region) rows for years {overlap_years}. "
            f"CPC has {sorted(cpc['year'].unique())}, "
            f"IMD has {sorted(imd['year'].unique())}."
        )

    # Only compare years where CPC actually observed a comparable number of
    # days. A CPC year with 40 archived days against IMD's 122 would read as a
    # massive dry bias that is purely a coverage artefact.
    merged = merged[merged["days_cpc"] >= 0.9 * merged["days_imd"]]
    if merged.empty:
        raise RuntimeError(
            "Overlap years exist but CPC coverage is too sparse in all of "
            "them (<90% of IMD's days). Backfill CPC over the full window "
            "before measuring bias."
        )

    out = (merged.groupby("region", as_index=False)
                 .agg(cpc_mm=("cumulative_mm_cpc", "mean"),
                      imd_mm=("cumulative_mm_imd", "mean"),
                      n_years=("year", "nunique")))
    out["ratio"] = (out["cpc_mm"] / out["imd_mm"]).round(4)
    out["cpc_mm"] = out["cpc_mm"].round(1)
    out["imd_mm"] = out["imd_mm"].round(1)
    out["overlap_years"] = ",".join(str(y) for y in sorted(set(merged["year"])))

    if persist:
        write_table(out, "analytics", "cpc_imd_bias")
    return out


def monsoon_departure(season_year: int | None = None,
                      climatology: tuple[int, int] = (1991, 2020),
                      persist: bool = True) -> pd.DataFrame:
    """
    Season-to-date rainfall departure from the IMD normal, per region.

    THIS IS THE NUMBER THE BOOK ACTUALLY WANTS. Cumulative departure over
    June-September is what moves kharif output, rural incomes and food CPI,
    and therefore rural-exposed equities and the RBI reaction function.

    HOW THE TWO SOURCES ARE RECONCILED, rather than mixed:
      * The NORMAL is IMD's, over `climatology`, truncated to the same
        day-of-year as the observation.
      * The OBSERVATION is IMD's too, if IMD has published the season.
        Otherwise it is CPC's, DIVIDED BY the measured CPC/IMD ratio from
        cpc_imd_bias() so it is expressed on IMD's basis.
      * `basis` says which of those happened, on every row. A departure whose
        basis is unstated is a departure you cannot act on.

    If the bias cannot be measured (no overlapping backfill), the CPC
    observation is reported UNCORRECTED with basis="cpc_uncorrected" and a
    null departure -- never a departure computed across two instruments as
    though they were one.
    """
    imd = _imd_rainfall()
    cpc = _rainfall()
    if imd.empty:
        raise RuntimeError(
            "imd_gridded_rainfall is empty -- no normal can be computed. "
            "Backfill: connectors.imd.imd_gridded_rainfall_range(1991, 2020)"
        )

    # Which season, and how far into it? Prefer IMD's own coverage; fall back
    # to CPC, which is what carries the current year.
    candidates = []
    for df in (imd, cpc):
        if not df.empty:
            d = pd.to_datetime(df["date"])
            m = d.dt.month.isin(MONSOON_MONTHS)
            if m.any():
                candidates.append(d[m].max())
    if not candidates:
        raise RuntimeError("No archived days fall inside a June-September window.")
    latest = max(candidates)
    if season_year is None:
        season_year = int(latest.year)

    imd_season = _season_window_sum(imd, latest.month, latest.day)
    cpc_season = _season_window_sum(cpc, latest.month, latest.day) if not cpc.empty \
        else pd.DataFrame(columns=["year", "region", "cumulative_mm", "days"])

    normals = imd_normals(*climatology, upto_month=latest.month,
                          upto_day=latest.day, persist=False).set_index("region")

    imd_obs = imd_season[imd_season["year"] == season_year].set_index("region")
    cpc_obs = cpc_season[cpc_season["year"] == season_year].set_index("region")

    ratios = {}
    if imd_obs.empty and not cpc_obs.empty:
        try:
            bias = cpc_imd_bias(upto_month=latest.month, upto_day=latest.day,
                                persist=False)
            ratios = dict(zip(bias["region"], bias["ratio"]))
        except RuntimeError:
            ratios = {}

    rows = []
    for region in normals.index:
        normal = float(normals.loc[region, "normal_mm"])
        sd = normals.loc[region, "normal_sd"]

        if region in imd_obs.index:
            observed = float(imd_obs.loc[region, "cumulative_mm"])
            days = int(imd_obs.loc[region, "days"])
            basis, ratio = "imd", 1.0
        elif region in cpc_obs.index:
            raw = float(cpc_obs.loc[region, "cumulative_mm"])
            days = int(cpc_obs.loc[region, "days"])
            ratio = ratios.get(region)
            if ratio:
                observed, basis = raw / ratio, "cpc_bias_corrected_to_imd"
            else:
                observed, basis = raw, "cpc_uncorrected"
        else:
            continue

        # An uncorrected CPC total is NOT comparable to an IMD normal. Report
        # the observation, refuse the departure.
        departure = None if basis == "cpc_uncorrected" else \
            round((observed - normal) / normal * 100, 1)
        sigma = None
        if departure is not None and sd and sd == sd and sd > 0:
            sigma = round((observed - normal) / float(sd), 2)

        rows.append({
            "season_year": season_year,
            "region": region,
            "as_of": latest.date().isoformat(),
            "observed_mm": round(observed, 1),
            "normal_mm": normal,
            "departure_pct": departure,
            "sigma": sigma,
            "basis": basis,
            "cpc_imd_ratio": round(ratio, 4) if ratio else None,
            "days_observed": days,
            "climatology": f"{climatology[0]}-{climatology[1]}",
        })

    out = pd.DataFrame(rows).sort_values("region").reset_index(drop=True)
    if out.empty:
        raise RuntimeError(f"No region had observations for season {season_year}")
    if persist:
        write_table(out, "analytics", "monsoon_departure")
    return out


def _rainfall() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        return con.execute("""
            SELECT date, region,
                   any_value(rain_mm_per_day) AS rain_mm_per_day,
                   any_value(valid_cells)     AS valid_cells
            FROM cpc_india_rainfall
            GROUP BY date, region
            ORDER BY region, date
        """).df()
    finally:
        con.close()


def monsoon_progress(season_year: int | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Season-to-date cumulative rainfall by region for the June-September
    monsoon, from archived CPC daily grids.

    `season_year` defaults to the most recent season present.

    COVERAGE IS REPORTED, NOT ASSUMED. `days_observed` and `season_days
    _elapsed` are both returned, and `coverage_pct` is the ratio. A cumulative
    total built from 12 of 90 elapsed days is not a small version of the
    season total, it is a different quantity -- so the denominator travels
    with the number instead of being left for the reader to infer.

    WHY IT MATTERS: cumulative departure over the season, by region, is what
    moves kharif output and rural incomes. A normal all-India total hiding a
    northwest deficit is a fertiliser and tractor story the headline conceals.
    """
    rain = _rainfall()
    if rain.empty:
        raise RuntimeError(
            "cpc_india_rainfall is empty. Run: python run_daily.py --job cpc_rainfall"
        )

    rain["date"] = pd.to_datetime(rain["date"])
    rain["year"] = rain["date"].dt.year
    rain["month"] = rain["date"].dt.month
    season = rain[rain["month"].isin(MONSOON_MONTHS)]
    if season.empty:
        raise RuntimeError(
            "No archived CPC days fall inside a June-September monsoon window."
        )

    if season_year is None:
        season_year = int(season["year"].max())
    cur = season[season["year"] == season_year]
    if cur.empty:
        raise RuntimeError(f"No archived monsoon days for season_year={season_year}")

    latest = cur["date"].max()
    elapsed = (latest - pd.Timestamp(year=season_year, month=6, day=1)).days + 1

    rows = []
    for region, grp in cur.groupby("region"):
        observed = len(grp)
        total = float(grp["rain_mm_per_day"].sum())
        rows.append({
            "season_year": season_year,
            "region": region,
            "as_of": latest.date().isoformat(),
            "cumulative_mm": round(total, 1),
            "mean_mm_per_day": round(float(grp["rain_mm_per_day"].mean()), 3),
            "days_observed": observed,
            "season_days_elapsed": elapsed,
            "coverage_pct": round(100.0 * observed / elapsed, 1) if elapsed > 0 else None,
        })

    out = pd.DataFrame(rows).sort_values("region").reset_index(drop=True)
    if persist:
        write_table(out, "analytics", "monsoon_progress")
    return out
