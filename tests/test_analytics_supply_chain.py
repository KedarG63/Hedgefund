"""
Offline tests for analytics/supply_chain.py.

The reroute-balance cases are built from the REAL measured numbers of the two
crises this dataset contains, because the whole point of the module is that
those two events must classify differently.
"""
import pandas as pd
import pytest

import analytics.supply_chain as sc
from analytics.supply_chain import (
    SUBSTITUTE_ROUTE,
    chokepoint_zscore,
    reroute_balance,
)


def _panel(spec: dict, days: int = 200) -> pd.DataFrame:
    """
    Build a synthetic panel. `spec` maps portname -> (baseline_level,
    recent_level); the switch happens `recent` days before the end.
    """
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    rows = []
    for name, (base, recent) in spec.items():
        for i, d in enumerate(dates):
            level = recent if i >= days - 14 else base
            # A little variation so std is non-zero and z is finite.
            level = level + (0.5 if i % 2 else -0.5)
            rows.append({"date": d.date().isoformat(), "portname": name,
                         "n_total": level, "n_tanker": level / 3,
                         "n_cargo": level / 2, "capacity": level * 1000})
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _no_write(monkeypatch):
    monkeypatch.setattr(sc, "write_table", lambda *a, **k: None)


def _patch_panel(monkeypatch, df):
    monkeypatch.setattr(sc, "_chokepoint_panel", lambda as_of=None: df)


# ================================================================= z-score
def test_baseline_window_does_not_overlap_the_recent_window(monkeypatch):
    """
    An overlapping baseline contains the event it is meant to measure and
    mutes exactly the signal we want. With a clean split, a corridor that
    halves must show a strongly negative z.
    """
    _patch_panel(monkeypatch, _panel({"Suez Canal": (80.0, 40.0)}))
    z = chokepoint_zscore(persist=False)
    row = z[(z.portname == "Suez Canal") & (z.metric == "n_total")].iloc[0]
    assert row["baseline_mean"] == pytest.approx(80.0, abs=0.5)
    assert row["recent_mean"] == pytest.approx(40.0, abs=0.5)
    assert row["zscore"] < -5


def test_flat_baseline_reports_level_change_instead_of_dividing_by_zero(monkeypatch):
    """A zero-variance baseline gives an infinite z; report pct_change instead."""
    dates = pd.date_range("2026-01-01", periods=200, freq="D")
    rows = [{"date": d.date().isoformat(), "portname": "Flat Strait",
             "n_total": 10.0 if i < 186 else 5.0, "n_tanker": 1.0,
             "n_cargo": 1.0, "capacity": 100.0} for i, d in enumerate(dates)]
    _patch_panel(monkeypatch, pd.DataFrame(rows))
    z = chokepoint_zscore(persist=False)
    row = z[(z.portname == "Flat Strait") & (z.metric == "n_total")].iloc[0]
    assert row["zscore"] is None
    assert row["pct_change"] == pytest.approx(-50.0, abs=0.1)


def test_empty_warehouse_raises_with_a_runnable_instruction(monkeypatch):
    _patch_panel(monkeypatch, pd.DataFrame(columns=["date", "portname", "n_total"]))
    with pytest.raises(RuntimeError, match="run_daily.py --job"):
        chokepoint_zscore(persist=False)


# ========================================================= reroute balance
def test_red_sea_2024_shape_classifies_as_rerouted(monkeypatch):
    """
    The real 2024 numbers: Bab el-Mandeb 77.3 -> 31.4 (-45.9) while the Cape
    of Good Hope went 49.3 -> 95.5 (+46.2). Traffic conserved -> rerouted.
    """
    _patch_panel(monkeypatch, _panel({
        "Bab el-Mandeb Strait": (77.3, 31.4),
        "Cape of Good Hope": (49.3, 95.5),
    }))
    out = reroute_balance(persist=False).set_index("corridor")
    row = out.loc["Bab el-Mandeb Strait"]
    assert row["absorbed_pct"] > 90
    assert row["interpretation"] == "rerouted"


def test_hormuz_2026_shape_classifies_as_no_substitute(monkeypatch):
    """
    Hormuz has NO alternative sea route out of the Gulf. Encoding that absence
    is the point: it is why 2026 is a supply event and 2024 was a routing one.
    """
    _patch_panel(monkeypatch, _panel({
        "Strait of Hormuz": (78.3, 3.2),
        "Cape of Good Hope": (90.0, 90.0),
    }))
    out = reroute_balance(persist=False).set_index("corridor")
    row = out.loc["Strait of Hormuz"]
    assert row["substitute"] is None
    assert row["interpretation"] == "no_substitute_route"
    assert row["absorbed_pct"] is None


def test_collapse_with_no_absorption_is_traffic_destroyed(monkeypatch):
    """A corridor that falls while its substitute does nothing = supply lost."""
    _patch_panel(monkeypatch, _panel({
        "Suez Canal": (75.0, 30.0),
        "Cape of Good Hope": (50.0, 50.0),
    }))
    out = reroute_balance(persist=False).set_index("corridor")
    assert out.loc["Suez Canal"]["interpretation"] == "traffic_destroyed"


def test_rising_corridor_is_not_given_a_meaningless_ratio(monkeypatch):
    """Nothing was lost, so there is nothing to absorb."""
    _patch_panel(monkeypatch, _panel({
        "Suez Canal": (40.0, 60.0),
        "Cape of Good Hope": (90.0, 85.0),
    }))
    out = reroute_balance(persist=False).set_index("corridor")
    row = out.loc["Suez Canal"]
    assert row["absorbed_pct"] is None
    assert row["interpretation"] == "corridor_not_falling"


# ========================================================== regime detection
def _series(spec, start="2022-01-01", seasonal_amp=0.0):
    """
    A daily panel from [(level, n_days), ...] segments, optionally with an
    annual seasonal cycle laid on top.
    """
    import numpy as np
    rows, day = [], pd.Timestamp(start)
    for level, n in spec:
        for _ in range(n):
            season = seasonal_amp * np.sin(2 * np.pi * day.dayofyear / 365.0)
            v = level + season + (0.5 if day.day % 2 else -0.5)
            rows.append({"date": day.date().isoformat(), "portname": "Test Strait",
                         "n_total": v, "n_tanker": v / 3, "n_cargo": v / 2,
                         "capacity": v * 1000})
            day += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def test_regime_breaks_dates_a_clean_step(monkeypatch):
    _patch_panel(monkeypatch, _series([(80.0, 200), (10.0, 200)]))
    out = sc.regime_breaks(persist=False)
    assert len(out) == 1
    row = out.iloc[0]
    # The step is at index 200 -> 2022-01-01 + 200 days.
    assert row["break_date"] == "2022-07-20"
    assert row["mean_before"] == pytest.approx(80.0, abs=0.5)
    assert row["mean_after"] == pytest.approx(10.0, abs=0.5)
    assert row["pct_change"] == pytest.approx(-87.5, abs=1.0)


def test_seasonality_alone_does_not_produce_a_break(monkeypatch):
    """
    THE FALSE-POSITIVE GUARD. Chokepoint traffic swings ~2x within a normal
    year (Hormuz ran 55-105 pre-war). A detector run on the raw series finds
    seasonal turning points and calls them regime changes -- which would
    discredit the whole tool. Deseasonalising first is what prevents it.
    """
    # FOUR years, matching the real archive's depth. Under MIN_SEASONAL_YEARS
    # the detector deliberately does not deseasonalise at all (a step and a
    # cycle are unidentifiable there), so a shorter fixture would be testing
    # the wrong branch.
    panel = _series([(80.0, 1461)], seasonal_amp=25.0)
    _patch_panel(monkeypatch, panel)
    with pytest.raises(RuntimeError, match="No structural break"):
        sc.regime_breaks(persist=False)


def test_real_break_still_found_underneath_seasonality(monkeypatch):
    """A genuine step must survive the deseasonalising, not be absorbed by it."""
    panel = _series([(80.0, 730), (12.0, 731)], seasonal_amp=25.0)
    _patch_panel(monkeypatch, panel)
    out = sc.regime_breaks(persist=False)
    assert not out.empty
    biggest = out.iloc[0]
    assert biggest["pct_change"] < -70
    # And it must be dated at the real step, not at a seasonal turning point.
    # 2022-01-01 + 730 days == 2024-01-01 (two non-leap years).
    assert biggest["break_date"] == "2024-01-01"


def test_short_wobble_is_not_a_regime(monkeypatch):
    """
    A one-week disruption must not register as a regime change. The minimum
    segment length is what separates an incident from a regime.
    """
    _patch_panel(monkeypatch, _series([(80.0, 200), (10.0, 7), (80.0, 200)]))
    with pytest.raises(RuntimeError, match="No structural break"):
        sc.regime_breaks(persist=False, min_segment_days=45)


def test_current_regime_separates_normal_for_regime_from_disrupted(monkeypatch):
    """
    THE POINT OF THE MODULE. After a collapse settles in, a route is perfectly
    normal FOR ITS NEW REGIME while still being catastrophically disrupted
    versus the old one. Collapsing these into one number loses the story --
    it is exactly why the fixed-baseline z-score reads Hormuz as -4.6/day.
    """
    _patch_panel(monkeypatch, _series([(80.0, 200), (8.0, 200)]))
    out = sc.current_regime(persist=False).iloc[0]
    assert abs(out["z_vs_regime"]) < 1.5          # normal for this regime
    assert out["regime_vs_previous_pct"] < -80    # and still a collapse
    assert out["regime_start"] == "2022-07-20"


def test_baseline_comparison_survives_a_second_break(monkeypatch):
    """
    THE DOUBLE-BREAK CASE, taken from the real Hormuz series. It collapsed
    90->8, then broke AGAIN inside its own closure (8->4.5). Measuring only
    against the previous regime then reports about -44% for a route that is
    actually down ~95% on the pre-crisis world. `vs_baseline_pct` is the
    number that describes how disrupted a corridor is; `regime_vs_previous_pct`
    is not, once a route breaks twice.
    """
    _patch_panel(monkeypatch, _series([(90.0, 400), (8.0, 300), (4.5, 300)]))
    out = sc.current_regime(persist=False).iloc[0]
    assert out["baseline_mean"] == pytest.approx(90.0, abs=0.5)
    assert out["vs_baseline_pct"] < -90
    # The previous-regime view understates it, which is the whole point.
    assert out["regime_vs_previous_pct"] > -60


def test_current_regime_falls_back_to_full_history_when_no_break(monkeypatch):
    _patch_panel(monkeypatch, _series([(50.0, 300)]))
    out = sc.current_regime(persist=False).iloc[0]
    assert out["regime_start"] == "2022-01-01"
    assert out["previous_regime_mean"] is None
    assert out["regime_vs_previous_pct"] is None


def test_regime_breaks_rejects_an_unknown_metric(monkeypatch):
    _patch_panel(monkeypatch, _series([(50.0, 300)]))
    with pytest.raises(ValueError, match="Unknown metric"):
        sc.regime_breaks(metric="not_a_column", persist=False)


def test_hormuz_substitute_is_explicitly_none_not_missing():
    """
    None here is a fact about geography, not an unfilled entry. If someone
    "helpfully" maps Hormuz to a substitute, the 2026 supply signal silently
    becomes a routing signal.
    """
    assert "Strait of Hormuz" in SUBSTITUTE_ROUTE
    assert SUBSTITUTE_ROUTE["Strait of Hormuz"] is None
