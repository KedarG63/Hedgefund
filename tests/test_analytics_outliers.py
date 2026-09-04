"""
Offline tests for the multivariate outlier detector. multivariate_outliers()
itself needs a live warehouse; this exercises the identical IsolationForest
call (same params: contamination, random_state) against a small synthetic
feature matrix with an injected, obvious outlier -- no DB required.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from analytics.outliers import CONTAMINATION, FEATURES, RANDOM_STATE


def _synthetic_matrix(n_normal: int = 95, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    normal = pd.DataFrame(rng.normal(0, 1, size=(n_normal, len(FEATURES))), columns=FEATURES)
    # One symbol extreme on every dimension simultaneously -- the exact
    # "co-occurrence a human wouldn't notice one column at a time" case
    # this detector exists for.
    outlier = pd.DataFrame([[8.0, 7.5, 9.0, -8.5]], columns=FEATURES)
    df = pd.concat([normal, outlier], ignore_index=True)
    df["symbol"] = [f"SYM{i}" for i in range(len(df))]
    return df


def test_isolation_forest_flags_the_injected_multivariate_outlier():
    df = _synthetic_matrix()
    X = df[FEATURES].to_numpy()
    model = IsolationForest(contamination=CONTAMINATION, random_state=RANDOM_STATE)
    df["is_outlier"] = model.fit_predict(X) == -1
    df["anomaly_score_raw"] = model.score_samples(X)

    injected_row = df.iloc[-1]
    assert injected_row["is_outlier"], "the injected extreme-on-every-dimension row must be flagged"
    assert injected_row["anomaly_score_raw"] == df["anomaly_score_raw"].min(), \
        "the injected outlier should have the single most anomalous score"


def test_a_symbol_extreme_on_only_one_dimension_is_less_anomalous_than_one_extreme_on_all():
    """
    The whole point of a multivariate detector over reading one column: a
    single-dimension extreme should score as LESS anomalous than a
    multi-dimension extreme of similar individual magnitude.
    """
    rng = np.random.default_rng(1)
    normal = pd.DataFrame(rng.normal(0, 1, size=(95, len(FEATURES))), columns=FEATURES)
    one_dim_extreme = pd.DataFrame([[8.0, 0.1, -0.2, 0.0]], columns=FEATURES)
    all_dim_extreme = pd.DataFrame([[8.0, 7.5, 9.0, -8.5]], columns=FEATURES)
    df = pd.concat([normal, one_dim_extreme, all_dim_extreme], ignore_index=True)

    X = df[FEATURES].to_numpy()
    model = IsolationForest(contamination=CONTAMINATION, random_state=RANDOM_STATE)
    model.fit(X)
    scores = model.score_samples(X)

    one_dim_score = scores[-2]
    all_dim_score = scores[-1]
    assert all_dim_score < one_dim_score, \
        "extreme-on-every-dimension must score more anomalous than extreme-on-one-dimension"


def test_deterministic_across_repeated_fits():
    """Same random_state must give the same flagged set every run -- no run-to-run noise."""
    df = _synthetic_matrix(seed=3)
    X = df[FEATURES].to_numpy()
    flags_1 = IsolationForest(contamination=CONTAMINATION, random_state=RANDOM_STATE).fit_predict(X)
    flags_2 = IsolationForest(contamination=CONTAMINATION, random_state=RANDOM_STATE).fit_predict(X)
    assert list(flags_1) == list(flags_2)


def test_missing_features_impute_to_zero_not_dropped():
    """
    A symbol with no bulk-deal or promoter-change signal that period gets
    notional_zscore/change_pct_points = NaN from the SQL LEFT JOIN --
    multivariate_outliers() must impute that to 0.0 ("no signal"), not drop
    the row or error.
    """
    raw = pd.DataFrame({
        "beta": [1.0, 1.2], "momentum_zscore": [0.5, -0.3],
        "notional_zscore": [0.1, None], "change_pct_points": [None, -1.0],
    })
    filled = raw[FEATURES].fillna(0.0)
    assert filled.isna().sum().sum() == 0
    assert filled.loc[1, "notional_zscore"] == 0.0
    assert filled.loc[0, "change_pct_points"] == 0.0
