"""
Supply-chain analytics on the PortWatch chokepoint series.

TWO SIGNALS, DELIBERATELY SEPARATED

  1. `chokepoint_zscore()` -- how far today's transit count sits from a
     trailing baseline, per chokepoint and vessel class.

  2. `reroute_balance()` -- whether traffic lost at a corridor is showing up
     on its substitute route.

The second is the important one, and the reason this module exists rather
than a generic z-score over yet another time series.

WHY THE MASS BALANCE IS THE SIGNAL
  A collapse at one chokepoint has two very different readings:

    * traffic REROUTED -- the cargo still moves, but further. That is a
      freight-rate, tonne-mile and voyage-duration story: bullish tankers and
      shipping (SCI, GE Shipping), a cost story for refiners, not a physical
      shortage.

    * traffic DESTROYED -- the cargo does not move at all. That is a
      supply story: crude availability, refinery run cuts, landed-cost spikes.

  The z-score alone cannot tell these apart, and they imply opposite trades.
  The balance can, because the substitute route either absorbs the volume or
  it does not.

  This is also how the underlying data was validated in the first place (see
  PHASE7.md): across the 2024 Red Sea crisis, Bab el-Mandeb lost 45.9
  vessels/day while the Cape of Good Hope gained 46.2 -- a near-perfect
  reroute. Under the 2026 Hormuz closure the balance does NOT close, because
  Gulf crude has no substitute sea route out of the Gulf. Same tool,
  opposite reading, and the difference is the trade.

REMEMBER THE LAG
  PortWatch publishes ~9 days behind. Every output here carries an as_of date
  for that reason. This is a structural-trend input, not an execution signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.storage import db, register_views, write_table

DEFAULT_BASELINE_DAYS = 90
DEFAULT_RECENT_DAYS = 14
MIN_BASELINE_OBS = 30

# Corridor -> substitute route. A collapse in the key should show up in the
# value if the cargo is rerouting rather than not moving.
#
# Hormuz maps to None ON PURPOSE: there is no alternative sea route out of the
# Persian Gulf. Encoding that absence is the point -- it is why the 2026
# closure is a supply event and the 2024 Red Sea crisis was a routing event.
SUBSTITUTE_ROUTE = {
    "Bab el-Mandeb Strait": "Cape of Good Hope",
    "Suez Canal": "Cape of Good Hope",
    "Strait of Hormuz": None,
    "Panama Canal": "Magellan Strait",
}


DEFAULT_MIN_SEGMENT_DAYS = 45
DEFAULT_BREAK_T = 8.0
DEFAULT_MAX_BREAKS = 6


SEASONAL_HARMONICS = 2
# Below this span a step and an annual cycle are not separable -- see
# _deseasonalise. Two years is the minimum at which day-of-year coverage is
# balanced on both sides of a mid-series break.
MIN_SEASONAL_YEARS = 2.0


def _deseasonalise(frame: pd.DataFrame, value_col: str,
                   harmonics: int = SEASONAL_HARMONICS) -> pd.Series:
    """
    Remove an annual cycle fitted as a low-order harmonic regression on
    day-of-year.

    WHY DESEASONALISING IS NOT OPTIONAL. Chokepoint traffic is strongly
    seasonal -- Hormuz swung 55 to 105 vessels/day within a normal year. A
    break detector run on the raw series finds seasonal turning points and
    reports them as regime changes, the false positive that would discredit
    the whole tool. Suez's real 2024 break (-46%) is smaller than Hormuz's
    ordinary seasonal swing, so a threshold alone cannot separate them.

    WHY HARMONICS AND NOT A PER-DAY-OF-YEAR MEAN. The obvious implementation
    -- group by day-of-year and subtract the mean -- is badly wrong here, and
    wrong in a way that hides itself. Any day-of-year observed only ONCE has
    a climatology equal to its own value, so its residual is exactly zero:
    with under two years of history the entire signal is annihilated and the
    detector silently finds nothing. Even with four years it attenuates every
    genuine break by 1/n, because the break's own year is inside the mean
    being subtracted from it. On this project's ~3.7 years of chokepoint
    history that quietly shaved ~25% off every effect size.

    A harmonic fit has `2*harmonics + 1` parameters regardless of series
    length, so it CANNOT represent a mid-series level shift -- a step is not
    in its span. It removes the smooth annual cycle and leaves breaks intact,
    which is exactly the asymmetry this needs.

    THE IDENTIFIABILITY LIMIT, and why short series are left alone. Under
    about two years, a step and an annual cycle are genuinely CONFOUNDED:
    with 400 days and a step at day 200, low days-of-year are almost all
    pre-step and high ones almost all post-step, so a harmonic in day-of-year
    fits a large part of the step and the detector then finds the residual
    sawtooth's edges instead of the real break. That is not a bug to code
    around -- the information to separate the two is simply not present. So
    below `MIN_SEASONAL_YEARS` of coverage this returns the mean-centred
    series undeseasonalised, and callers accept a higher risk of a seasonal
    false positive in exchange for not destroying real breaks. The minimum
    segment length and the t threshold are the remaining guards there.
    """
    values = frame[value_col].to_numpy(dtype="float64")
    dates = pd.to_datetime(frame["date"])
    doy = dates.dt.dayofyear.to_numpy(dtype="float64")
    finite = np.isfinite(values)
    if not finite.any():
        return pd.Series(values, index=frame.index)

    span_years = (dates.max() - dates.min()).days / 365.25
    if span_years < MIN_SEASONAL_YEARS:
        return pd.Series(values - float(np.nanmean(values[finite])), index=frame.index)

    cols = [np.ones_like(doy)]
    for k in range(1, harmonics + 1):
        cols.append(np.sin(2 * np.pi * k * doy / 365.25))
        cols.append(np.cos(2 * np.pi * k * doy / 365.25))
    design = np.column_stack(cols)

    if finite.sum() <= design.shape[1]:
        return pd.Series(values - float(np.nanmean(values[finite])), index=frame.index)

    coef, *_ = np.linalg.lstsq(design[finite], values[finite], rcond=None)
    return pd.Series(values - design @ coef, index=frame.index)


def _segment_t(values, lo: int, hi: int, split: int) -> float:
    """
    Welch t-statistic for a mean shift at `split` within values[lo:hi].
    O(1) given prefix sums; the caller supplies them.
    """
    csum, csq = values
    n1, n2 = split - lo, hi - split
    if n1 < 2 or n2 < 2:
        return 0.0
    s1 = csum[split] - csum[lo]
    s2 = csum[hi] - csum[split]
    q1 = csq[split] - csq[lo]
    q2 = csq[hi] - csq[split]
    m1, m2 = s1 / n1, s2 / n2
    v1 = max((q1 - n1 * m1 * m1) / (n1 - 1), 1e-12)
    v2 = max((q2 - n2 * m2 * m2) / (n2 - 1), 1e-12)
    return abs(m1 - m2) / ((v1 / n1 + v2 / n2) ** 0.5)


def _find_breaks(arr, lo: int, hi: int, min_seg: int, threshold: float,
                 budget: list) -> list:
    """
    Binary segmentation. Deliberately boring: find the single most significant
    mean shift in a window, accept it if it clears the threshold, then recurse
    into both halves until the budget runs out.

    Returns split indices, unsorted.
    """
    if budget[0] <= 0 or (hi - lo) < 2 * min_seg:
        return []
    csum, csq = arr
    best_t, best_i = 0.0, None
    for i in range(lo + min_seg, hi - min_seg + 1):
        t = _segment_t(arr, lo, hi, i)
        if t > best_t:
            best_t, best_i = t, i
    if best_i is None or best_t < threshold:
        return []
    budget[0] -= 1
    return ([best_i]
            + _find_breaks(arr, lo, best_i, min_seg, threshold, budget)
            + _find_breaks(arr, best_i, hi, min_seg, threshold, budget))


def regime_breaks(metric: str = "n_total", min_segment_days: int = DEFAULT_MIN_SEGMENT_DAYS,
                  threshold: float = DEFAULT_BREAK_T, max_breaks: int = DEFAULT_MAX_BREAKS,
                  as_of: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Date the structural breaks in each chokepoint's traffic, rather than
    judging its level.

    THIS IS PHASE 7'S CENTRAL LESSON, TURNED INTO CODE. The phase nearly
    discarded the Hormuz series because 1-9 vessels/day looked implausible
    against a remembered prior. It was not implausible, it was a war -- and
    the way to see that was to pull the history and find the break, which has
    a date you can check against the world, rather than to argue about whether
    a level "looks right". A level is an opinion; a dated break is a claim.

    Detection runs on a DESEASONALISED series (see _deseasonalise) using
    binary segmentation on a Welch t-statistic, with a minimum segment length
    so a one-week disruption cannot masquerade as a regime.

    Returns one row per detected break: the date, the mean either side, the
    percentage change and the t-statistic. Sorted by absolute impact, so the
    biggest supply events in the archive surface first.
    """
    panel = _chokepoint_panel(as_of)
    if panel.empty:
        raise RuntimeError(
            "portwatch_chokepoints is empty. Run the connector first: "
            "python run_daily.py --job portwatch_chokepoints"
        )
    if metric not in panel.columns:
        raise ValueError(f"Unknown metric {metric!r}; have {sorted(panel.columns)}")

    rows = []
    for name, grp in panel.groupby("portname"):
        grp = grp.sort_values("date").reset_index(drop=True)
        if len(grp) < 2 * min_segment_days:
            continue
        resid = _deseasonalise(grp, metric).to_numpy(dtype="float64")
        if not np.isfinite(resid).all():
            resid = np.nan_to_num(resid)

        n = len(resid)
        csum = np.zeros(n + 1)
        csq = np.zeros(n + 1)
        csum[1:] = np.cumsum(resid)
        csq[1:] = np.cumsum(resid * resid)
        idx = sorted(_find_breaks((csum, csq), 0, n, min_segment_days,
                                  threshold, [max_breaks]))

        raw = grp[metric].to_numpy(dtype="float64")
        bounds = [0] + idx + [n]
        for k, split in enumerate(idx):
            before = raw[bounds[k]:split]
            after = raw[split:bounds[k + 2]]
            m_before, m_after = float(before.mean()), float(after.mean())
            rows.append({
                "portname": name,
                "break_date": grp.loc[split, "date"],
                "metric": metric,
                "mean_before": round(m_before, 2),
                "mean_after": round(m_after, 2),
                "change_per_day": round(m_after - m_before, 2),
                "pct_change": round((m_after - m_before) / m_before * 100, 1)
                if m_before else None,
                "t_stat": round(_segment_t((csum, csq), bounds[k], bounds[k + 2], split), 1),
                "days_before": int(len(before)),
                "days_after": int(len(after)),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError(
            f"No structural break cleared t>={threshold} in any chokepoint. "
            f"Either the archive is too short (needs >= {2 * min_segment_days} "
            f"days per chokepoint) or the threshold is too strict."
        )
    out = out.reindex(out["change_per_day"].abs().sort_values(ascending=False).index)
    out = out.reset_index(drop=True)
    if persist:
        write_table(out, "analytics", "regime_breaks")
    return out


def current_regime(metric: str = "n_total", recent_days: int = DEFAULT_RECENT_DAYS,
                   as_of: str | None = None, persist: bool = True,
                   **break_kwargs) -> pd.DataFrame:
    """
    Where each chokepoint sits RELATIVE TO ITS OWN CURRENT REGIME, and how far
    that regime is from the one before it.

    This is the fix for the limitation `chokepoint_zscore()` documents. That
    function uses a fixed 90-day baseline, so once a disruption is more than
    90 days old it is *inside* its own baseline and scores near zero -- by
    September 2026 the Hormuz closure, the largest supply event in the
    archive, reads about -4.6/day. Correct for "deviation from the current
    regime", useless for "how disrupted is this route".

    Both questions matter and they are different, so both are answered here:

      `z_vs_regime`            is today unusual FOR THIS REGIME (execution-ish)
      `regime_vs_previous_pct` how far the regime moved from the one before it
      `vs_baseline_pct`        how far it has moved from the PRE-BREAK world

    A route can be perfectly normal for a closed strait. That is exactly the
    Hormuz situation and the reason these must not be collapsed.

    The last two differ once a route breaks TWICE. Hormuz broke again inside
    its own closure (7.7 -> 4.6 vessels/day), so "vs previous regime" reports
    about -40% for a route that is down 91% on where it started. The baseline
    column is the one to quote when asking how disrupted a corridor is.

    KNOWN LIMITATION -- strongly non-sinusoidal seasonality. The deseasonalising
    fits two harmonics, which handles a smooth annual cycle well and an
    ice-bound route badly: the Bering Strait is effectively closed for much of
    the year and open in summer, a near-square wave that two harmonics cannot
    represent. Treat Arctic and other on/off seasonal routes here as
    unreliable rather than reading their breaks literally.
    """
    try:
        breaks = regime_breaks(metric=metric, as_of=as_of, persist=False, **break_kwargs)
    except RuntimeError:
        # A warehouse in which nothing broke is a legitimate state, not a
        # failure -- every route is then simply in its original regime.
        breaks = pd.DataFrame(columns=["portname", "break_date", "mean_before"])
    panel = _chokepoint_panel(as_of)
    panel["date"] = pd.to_datetime(panel["date"])

    rows = []
    for name, grp in panel.groupby("portname"):
        grp = grp.sort_values("date")
        latest = grp["date"].max()
        mine = breaks[breaks["portname"] == name]
        if mine.empty:
            regime_start = grp["date"].min()
            prev_mean = None
        else:
            last_break = pd.to_datetime(mine["break_date"]).max()
            regime_start = last_break
            row = mine[pd.to_datetime(mine["break_date"]) == last_break].iloc[0]
            prev_mean = row["mean_before"]

        regime = grp[grp["date"] >= regime_start]
        recent = grp[grp["date"] > latest - pd.Timedelta(days=recent_days)]
        if regime.empty or recent.empty:
            continue

        # The FIRST regime in the archive -- the world before any break. This
        # is what "how displaced is this route" actually means, and it is not
        # the same as the previous regime once a route breaks twice. Hormuz
        # broke again inside its own closure (7.7 -> 4.6/day), so measuring
        # only against the previous regime reports -40% for a route that is
        # actually down 91% on where it started.
        first_break = pd.to_datetime(mine["break_date"]).min() if not mine.empty else None
        baseline = grp[grp["date"] < first_break] if first_break is not None else grp
        b_mean = float(baseline[metric].mean()) if not baseline.empty else None

        r_mean = float(regime[metric].mean())
        r_std = float(regime[metric].std())
        rec_mean = float(recent[metric].mean())

        rows.append({
            "as_of": latest.date().isoformat(),
            "portname": name,
            "metric": metric,
            "regime_start": regime_start.date().isoformat(),
            "days_in_regime": int(len(regime)),
            "regime_mean": round(r_mean, 2),
            "recent_mean": round(rec_mean, 2),
            # Is today unusual for this regime?
            "z_vs_regime": round((rec_mean - r_mean) / r_std, 2)
            if r_std and r_std > 0 else None,
            # How far did the regime itself move?
            "previous_regime_mean": round(prev_mean, 2) if prev_mean is not None else None,
            "regime_vs_previous_pct": round((r_mean - prev_mean) / prev_mean * 100, 1)
            if prev_mean else None,
            # Displacement from the pre-break world, which is the number that
            # actually describes how disrupted a route is.
            "baseline_mean": round(b_mean, 2) if b_mean is not None else None,
            "vs_baseline_pct": round((r_mean - b_mean) / b_mean * 100, 1)
            if b_mean else None,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError("current_regime: no chokepoint had both a regime and a recent window")
    out = out.reindex(
        pd.to_numeric(out["vs_baseline_pct"], errors="coerce")
        .fillna(0).abs().sort_values(ascending=False).index
    ).reset_index(drop=True)
    if persist:
        write_table(out, "analytics", "current_regime")
    return out


def _chokepoint_panel(as_of: str | None = None) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        where = f"WHERE date <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT date, portname,
                   any_value(n_total)   AS n_total,
                   any_value(n_tanker)  AS n_tanker,
                   any_value(n_cargo)   AS n_cargo,
                   any_value(capacity)  AS capacity
            FROM portwatch_chokepoints
            {where}
            GROUP BY date, portname
            ORDER BY portname, date
        """).df()
    finally:
        con.close()


def chokepoint_zscore(baseline_days: int = DEFAULT_BASELINE_DAYS,
                      recent_days: int = DEFAULT_RECENT_DAYS,
                      as_of: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Recent mean transit count per chokepoint, z-scored against its own
    trailing baseline.

    The baseline is the `baseline_days` window ENDING WHERE the recent window
    begins -- the two never overlap. An overlapping baseline contains the
    event it is meant to measure and mutes exactly the signal we want.

    Returns one row per (chokepoint, metric) with the z-score and both means.
    """
    panel = _chokepoint_panel(as_of)
    if panel.empty:
        raise RuntimeError(
            "portwatch_chokepoints is empty. Run the chokepoints connector "
            "before the analytics: python run_daily.py --job portwatch_chokepoints"
        )

    panel["date"] = pd.to_datetime(panel["date"])
    latest = panel["date"].max()
    recent_start = latest - pd.Timedelta(days=recent_days)
    base_start = recent_start - pd.Timedelta(days=baseline_days)

    rows = []
    for name, grp in panel.groupby("portname"):
        recent = grp[grp["date"] > recent_start]
        baseline = grp[(grp["date"] > base_start) & (grp["date"] <= recent_start)]
        if len(baseline) < MIN_BASELINE_OBS or recent.empty:
            continue
        for metric in ("n_total", "n_tanker", "n_cargo", "capacity"):
            b_mean = baseline[metric].mean()
            b_std = baseline[metric].std()
            r_mean = recent[metric].mean()
            # A flat baseline gives a zero std and an infinite z. Report the
            # level change instead of a divide-by-zero.
            z = (r_mean - b_mean) / b_std if b_std and b_std > 0 else None
            rows.append({
                "as_of": latest.date().isoformat(),
                "portname": name,
                "metric": metric,
                "recent_mean": round(float(r_mean), 2),
                "baseline_mean": round(float(b_mean), 2),
                "baseline_std": round(float(b_std), 2) if b_std == b_std else None,
                "zscore": round(float(z), 2) if z is not None else None,
                "pct_change": round(float((r_mean - b_mean) / b_mean * 100), 1)
                if b_mean else None,
                "recent_days": recent_days,
                "baseline_days": baseline_days,
            })

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError(
            f"No chokepoint had {MIN_BASELINE_OBS}+ baseline observations. "
            f"The warehouse likely holds only a short recent window -- "
            f"backfill with portwatch_chokepoints(since=...)."
        )
    out = out.sort_values(["metric", "zscore"]).reset_index(drop=True)
    if persist:
        write_table(out, "analytics", "chokepoint_zscore")
    return out


def reroute_balance(baseline_days: int = DEFAULT_BASELINE_DAYS,
                    recent_days: int = DEFAULT_RECENT_DAYS,
                    as_of: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    For each corridor with a known substitute route: did the traffic it lost
    reappear on the substitute?

    `absorbed_pct` is the share of the corridor's lost vessels/day that the
    substitute gained over the same windows:

        absorbed_pct = -100 * (substitute_change / corridor_change)

    Read it as:
        ~100%   traffic REROUTED   -> tonne-mile / freight-rate story
        ~0%     traffic DESTROYED  -> physical supply story
        >100%   substitute gained more than this corridor lost -- another
                corridor is feeding it too, or demand grew independently

    Only computed where the corridor actually fell; a rising corridor has
    nothing to absorb and returns a null rather than a meaningless ratio.

    WHY IT MATTERS: these two readings imply opposite positions in shipping
    and refining. See the module docstring.
    """
    z = chokepoint_zscore(baseline_days, recent_days, as_of, persist=False)
    totals = z[z["metric"] == "n_total"].set_index("portname")

    rows = []
    for corridor, substitute in SUBSTITUTE_ROUTE.items():
        if corridor not in totals.index:
            continue
        c = totals.loc[corridor]
        c_change = c["recent_mean"] - c["baseline_mean"]

        s_change = None
        absorbed = None
        if substitute and substitute in totals.index:
            s = totals.loc[substitute]
            s_change = s["recent_mean"] - s["baseline_mean"]
            if c_change < 0:
                absorbed = round(-100.0 * s_change / c_change, 1)

        rows.append({
            "as_of": c["as_of"],
            "corridor": corridor,
            "substitute": substitute,
            "corridor_change_per_day": round(float(c_change), 2),
            "substitute_change_per_day": round(float(s_change), 2)
            if s_change is not None else None,
            "absorbed_pct": absorbed,
            # No substitute route is a fact about geography, not missing data.
            "interpretation": (
                "no_substitute_route" if substitute is None
                else "corridor_not_falling" if c_change >= 0
                else "rerouted" if absorbed is not None and absorbed >= 60
                else "partially_rerouted" if absorbed is not None and absorbed >= 25
                else "traffic_destroyed"
            ),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError("reroute_balance: no known corridor present in the warehouse")
    if persist:
        write_table(out, "analytics", "reroute_balance")
    return out
