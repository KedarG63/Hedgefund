"""
Multivariate outlier detection across the signals this package already
computes -- beta, momentum z-score, bulk/block-deal notional z-score,
promoter-holding change. A stock that is merely "high momentum" is not
unusual by itself; this catches a stock that is simultaneously extreme
across SEVERAL unrelated dimensions at once -- the kind of co-occurrence a
human scanning one column at a time would not notice.

Isolation Forest (scikit-learn), not a closed-form statistic like the rest
of this package -- deliberately a different tool for a different job. The
feature space here is not assumed elliptical/Gaussian (return z-scores and
notional z-scores are fat-tailed and often zero-heavy for symbols with no
event that period), which is exactly the regime Isolation Forest is built
for, unlike a Mahalanobis-distance approach that assumes roughly-normal
data. Deterministic via a fixed random_state -- same inputs always produce
the same flagged set, no run-to-run noise.
"""
from __future__ import annotations

import pandas as pd
from sklearn.ensemble import IsolationForest

from core.asof import latest_per
from core.storage import db, register_views, write_table

FEATURES = ["beta", "momentum_zscore", "notional_zscore", "change_pct_points"]

# Hand-set, documented Phase 1 starting point -- NOT backtested or tuned.
CONTAMINATION = 0.05   # expect roughly 5% of the universe to be flagged
RANDOM_STATE = 42
MIN_SYMBOLS = 30        # IsolationForest on too few points is not meaningful


def _registered_views(con) -> set[str]:
    return set(con.execute("SELECT table_name FROM information_schema.tables").df()["table_name"])


def _fetch_feature_matrix() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        views = _registered_views(con)
        required = {"derived_capm_beta", "derived_momentum_zscore"}
        missing = required - views
        if missing:
            raise RuntimeError(
                f"multivariate_outliers needs {sorted(missing)} first -- run "
                "analytics_capm_beta and analytics_momentum before analytics_outliers."
            )

        # write_table() never overwrites -- a job rerun leaves a SECOND
        # vintage that can share the same as_of_date as the first, so
        # filtering on "as_of_date = max(as_of_date)" alone does not dedupe
        # and silently doubles that symbol's row (and any join off it) if a
        # job has ever been rerun for the same day. latest_per() collapses to
        # the latest vintage per symbol, row-wise -- see core/asof.py for why
        # row-wise rather than the arg_max-per-column form this replaced.
        beta_latest = f"({latest_per('derived_capm_beta', ['symbol'], columns='symbol, beta, as_of_date')})"
        momentum_latest = f"({latest_per('derived_momentum_zscore', ['symbol'], columns='symbol, momentum_zscore, as_of_date')})"

        bulk_join, bulk_expr = "", "CAST(NULL AS DOUBLE)"
        if "derived_bulk_block_anomaly" in views:
            bulk_join = f"""
                LEFT JOIN ({latest_per('derived_bulk_block_anomaly', ['symbol'],
                                       columns='symbol, notional_zscore')}) e USING (symbol)
            """
            bulk_expr = "e.notional_zscore"

        promoter_join, promoter_expr = "", "CAST(NULL AS DOUBLE)"
        if "derived_promoter_holding_change" in views:
            promoter_join = f"""
                LEFT JOIN ({latest_per('derived_promoter_holding_change', ['symbol'],
                                       columns='symbol, change_pct_points')}) p USING (symbol)
            """
            promoter_expr = "p.change_pct_points"

        return con.execute(f"""
            SELECT b.symbol, b.beta, m.momentum_zscore,
                   {bulk_expr} AS notional_zscore,
                   {promoter_expr} AS change_pct_points,
                   m.as_of_date
            FROM {beta_latest} b
            JOIN {momentum_latest} m USING (symbol)
            {bulk_join}
            {promoter_join}
        """).df()
    finally:
        con.close()


def multivariate_outliers(contamination: float = CONTAMINATION, persist: bool = True) -> pd.DataFrame:
    """
    Fits IsolationForest on [beta, momentum_zscore, notional_zscore,
    change_pct_points] (missing features for a symbol are imputed to 0.0 --
    "no signal that period", not "extreme"), scores every symbol, and flags
    the `contamination` fraction with the most anomalous score.

    anomaly_score_raw is sklearn's continuous score_samples output (more
    negative = more anomalous); is_outlier is the boolean flag from the
    fitted contamination threshold. Output is sorted most-anomalous first.
    """
    df = _fetch_feature_matrix()
    if len(df) < MIN_SYMBOLS:
        raise RuntimeError(
            f"multivariate_outliers: only {len(df)} symbol(s) have a full beta+momentum "
            f"signal; need at least {MIN_SYMBOLS} for IsolationForest to be meaningful."
        )

    X = df[FEATURES].fillna(0.0).to_numpy()
    model = IsolationForest(contamination=contamination, random_state=RANDOM_STATE)
    df = df.copy()
    df["is_outlier"] = model.fit_predict(X) == -1
    df["anomaly_score_raw"] = model.score_samples(X)
    out = df.sort_values("anomaly_score_raw", ignore_index=True)

    if persist:
        write_table(out, "derived", "multivariate_outliers")
    return out
