"""
The OSINT layer: maritime chokepoints and the monsoon.

Both exist to be drawn against equities rather than admired on their own -- a
chokepoint z-score matters because of what it does to refiner margins, and a
rainfall departure matters because of what it does to agri and FMCG earnings.
These endpoints serve the raw reads; the overlay is the panel's job.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from api.deps import AsOf, cursor, require_view, views
from api.responses import frame

router = APIRouter(prefix="/api", tags=["world"])


@router.get("/supply-chain/regimes")
def regimes(as_of: AsOf = None, fmt: str = "arrow"):
    """
    Where each corridor stands now, plus how the traffic was absorbed.

    Two columns that must not be collapsed into one: `z_vs_regime` asks whether
    today is unusual FOR THE CURRENT REGIME, `vs_baseline_pct` asks how far the
    route has moved from the pre-break world. A closed strait is perfectly
    normal for itself -- Hormuz reads ~0 on the first and about -93% on the
    second, and only the second describes the disruption.
    """
    from core.asof import latest_per

    require_view("analytics_current_regime")
    reg = latest_per("analytics_current_regime", ["portname", "metric"], as_of,
                     columns="portname, metric, regime_start, days_in_regime, "
                             "regime_mean, recent_mean, z_vs_regime, "
                             "regime_vs_previous_pct, baseline_mean, vs_baseline_pct, as_of")
    sql = f"SELECT * FROM ({reg})"

    if "analytics_reroute_balance" in views():
        # interpretation is the column that separates a reroute from a supply
        # loss; without it the z-score alone implies the wrong trade.
        bal = latest_per("analytics_reroute_balance", ["corridor"], as_of,
                         columns="corridor AS portname, substitute, absorbed_pct, "
                                 "interpretation")
        sql = (f"SELECT r.*, b.substitute, b.absorbed_pct, b.interpretation "
               f"FROM ({sql}) r LEFT JOIN ({bal}) b USING (portname)")

    return frame(cursor().execute(
        f"SELECT * FROM ({sql}) ORDER BY abs(coalesce(vs_baseline_pct, 0)) DESC"), fmt)


@router.get("/supply-chain/breaks")
def breaks(as_of: AsOf = None, limit: int = Query(40, le=500), fmt: str = "arrow"):
    """
    Dated structural breaks, detected from the data with no event dates
    hardcoded -- which is what makes them evidence rather than annotation.
    """
    from core.asof import asof_sql

    require_view("analytics_regime_breaks")
    return frame(cursor().execute(asof_sql(
        "analytics_regime_breaks", as_of,
        columns="portname, break_date, metric, mean_before, mean_after, "
                "change_per_day, pct_change, t_stat, days_after",
        order_by="abs(change_per_day) DESC", limit=limit,
    )), fmt)


@router.get("/supply-chain/transits")
def transits(portname: str | None = None, as_of: AsOf = None,
             limit: int = Query(4000, le=50000), fmt: str = "arrow"):
    """Daily transit counts per chokepoint -- the series the regimes sit on."""
    from core.asof import asof_sql

    require_view("portwatch_chokepoints")
    where = ""
    if portname:
        where = "portname = '" + portname.replace("'", "''") + "'"
    return frame(cursor().execute(asof_sql(
        "portwatch_chokepoints", as_of,
        columns="date, portname, n_total, n_cargo, n_tanker, n_container, capacity",
        where=where, order_by="date", limit=limit,
    )), fmt)


@router.get("/climate/monsoon")
def monsoon(as_of: AsOf = None, fmt: str = "arrow"):
    """
    Rainfall departure from the IMD normal, per region, with season progress.

    `basis` travels with every row on purpose: IMD publishes its own normals in
    arrears, so the current season is observed by CPC and rescaled onto IMD's
    basis. A number whose provenance is `cpc_uncorrected` deserves less weight
    than one that is not, and the panel cannot know that unless the API says so.
    """
    from core.asof import latest_per

    require_view("analytics_monsoon_departure")
    dep = latest_per("analytics_monsoon_departure", ["region"], as_of,
                     columns="region, season_year, observed_mm, normal_mm, "
                             "departure_pct, sigma, basis, cpc_imd_ratio, "
                             "days_observed, as_of")
    sql = f"SELECT * FROM ({dep})"

    if "analytics_monsoon_progress" in views():
        prog = latest_per("analytics_monsoon_progress", ["region"], as_of,
                          columns="region, cumulative_mm, mean_mm_per_day, "
                                  "season_days_elapsed, coverage_pct")
        sql = (f"SELECT d.*, p.cumulative_mm, p.mean_mm_per_day, "
               f"p.season_days_elapsed, p.coverage_pct "
               f"FROM ({sql}) d LEFT JOIN ({prog}) p USING (region)")

    return frame(cursor().execute(f"SELECT * FROM ({sql}) ORDER BY departure_pct"), fmt)


@router.get("/climate/enso")
def enso(as_of: AsOf = None, fmt: str = "arrow"):
    """ENSO state and trajectory -- the prior the monsoon observation updates."""
    from core.asof import latest_per

    if "analytics_enso_state" not in views():
        return frame(pd.DataFrame(columns=["period", "anomaly_c", "phase", "trajectory"]), fmt)
    return frame(cursor().execute(latest_per(
        "analytics_enso_state", ["as_of_season", "as_of_year"], as_of,
        columns="as_of_season, as_of_year, period, anomaly_c, phase, "
                "anomaly_change_6_seasons, trajectory, is_leading_monsoon_season, "
                "prior_el_nino_share, prior_la_nina_share",
    )), fmt)
