"""
Rolling pairwise correlation of daily returns, and a cointegration-screened
pairs-trading shortlist.

Correlation over ~2,000 symbols is one vectorized pivot+corr call -- cheap.
Cointegration (statsmodels' Engle-Granger test) is NOT vectorizable and must
never run on the full all-pairs combinatorial space (~2M pairs at this
universe size) -- it only ever runs on the shortlist already above the
correlation threshold, which in practice is low hundreds of pairs. This is
the boring/debuggable choice over anything resembling a full-universe
cointegration screen.
"""
from __future__ import annotations

from itertools import combinations

import pandas as pd
from statsmodels.tsa.stattools import coint

from analytics.returns import log_returns
from analytics.universe import equity_universe
from core.storage import db, register_views, write_table

DEFAULT_WINDOW_DAYS = 60
MIN_OBS = 20
DEFAULT_CORR_THRESHOLD = 0.8
# Hard cap on how many pairs ever reach coint() -- a low threshold or a wide
# window should never be able to make the O(pairs) cointegration step blow up.
MAX_PAIRS_FOR_COINTEGRATION = 500


def _price_panel(symbols: list[str], as_of: str | None = None) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        placeholders = ", ".join(f"'{s}'" for s in symbols)
        where_date = f"AND TradDt <= '{as_of}'" if as_of else ""
        return con.execute(f"""
            SELECT TckrSymb AS symbol, TradDt AS trade_date, any_value(ClsPric) AS close
            FROM nse_bhavcopy
            WHERE TckrSymb IN ({placeholders}) {where_date}
            GROUP BY TckrSymb, TradDt
            ORDER BY symbol, trade_date
        """).df()
    finally:
        con.close()


def rolling_correlation(window_days: int = DEFAULT_WINDOW_DAYS, min_obs: int = MIN_OBS,
                        as_of: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Pairwise Pearson correlation of daily log returns over the trailing
    `window_days`, long format (symbol_a, symbol_b, correlation, n_obs).
    Only the upper triangle is kept (symbol_a < symbol_b) -- correlation is
    symmetric, storing both halves would double the table for no information.
    """
    universe = equity_universe(as_of=as_of)
    symbols = universe["symbol"].tolist()
    hist = _price_panel(symbols, as_of=as_of)
    hist = log_returns(hist, price_col="close", symbol_col="symbol", date_col="trade_date")

    panel = hist.pivot(index="trade_date", columns="symbol", values="log_return")
    panel = panel.tail(window_days)
    enough = panel.columns[panel.count() >= min_obs]
    panel = panel[enough]
    if panel.shape[1] < 2:
        raise RuntimeError(
            f"rolling_correlation: fewer than 2 symbols have {min_obs}+ return "
            "observations in the current window -- not enough bhavcopy history yet."
        )

    corr = panel.corr(min_periods=min_obs)
    counts = panel.count()

    rows = []
    for a, b in combinations(corr.columns, 2):
        v = corr.loc[a, b]
        if pd.isna(v):
            continue
        rows.append({
            "symbol_a": a, "symbol_b": b, "correlation": v,
            "n_obs": int(min(counts[a], counts[b])),
            "as_of_date": panel.index.max(),
        })
    df = pd.DataFrame(rows)
    if persist and not df.empty:
        write_table(df, "derived", "correlation")
    return df


def to_symmetric_matrix(long: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """
    rolling_correlation() only stores the upper triangle (symbol_a < symbol_b)
    -- mirror it into a full symmetric matrix for a heatmap. Diagonal is 1.0:
    a symbol always correlates perfectly with itself, and rolling_correlation()
    never computes or stores that row.

    Off-diagonal cells default to NaN, not 1.0: rolling_correlation() drops
    any symbol with fewer than min_obs return observations from its panel
    (see MIN_OBS), so a thinly-traded or newly-listed symbol in `symbols`
    can genuinely have no stored correlation against another -- that must
    render as "no data" (NaN, blank in a heatmap), never as "1.0 / perfectly
    correlated", which a naive all-ones default would silently imply.
    """
    mat = pd.DataFrame(float("nan"), index=symbols, columns=symbols)
    for s in symbols:
        mat.loc[s, s] = 1.0
    for _, r in long.iterrows():
        a, b, c = r["symbol_a"], r["symbol_b"], r["correlation"]
        if a in mat.index and b in mat.columns:
            mat.loc[a, b] = c
            mat.loc[b, a] = c
    return mat


def sector_correlation(long_df: pd.DataFrame, industry: pd.DataFrame) -> pd.DataFrame:
    """
    Average pairwise correlation per (industry_a, industry_b) pair, given an
    already-fetched correlation long frame (symbol_a, symbol_b, correlation)
    and a symbol->industry mapping (symbol, industry). Pure pandas -- the
    caller is responsible for the industry mapping's provenance (in this
    warehouse, only nse_index_constituents carries a verified Industry
    field, so this is a NIFTY-50-only rollup in practice; see
    dashboard/app.py's _sector_correlation()).
    """
    # Rename+merge on the exact symbol_a/symbol_b column names instead of a
    # generic "symbol" + suffixes=: two merges against the SAME frame both
    # carry a "symbol"/"industry" column, so a suffix-based second merge
    # collides with the already-real "symbol_a" column from long_df itself
    # (pandas raises MergeError on the resulting duplicate name).
    merged = (long_df.merge(industry.rename(columns={"symbol": "symbol_a", "industry": "industry_a"}),
                            on="symbol_a")
                     .merge(industry.rename(columns={"symbol": "symbol_b", "industry": "industry_b"}),
                            on="symbol_b"))
    if merged.empty:
        return pd.DataFrame(columns=["industry_a", "industry_b", "correlation"])
    return merged.groupby(["industry_a", "industry_b"])["correlation"].mean().reset_index()


def pairs_screen(corr_threshold: float = DEFAULT_CORR_THRESHOLD, window_days: int = DEFAULT_WINDOW_DAYS,
                 as_of: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Candidate pairs-trading shortlist: correlation above `corr_threshold`,
    then Engle-Granger cointegration on price LEVELS for that shortlist only.
    """
    corr = rolling_correlation(window_days=window_days, as_of=as_of, persist=False)
    shortlist = corr[corr["correlation"].abs() >= corr_threshold].copy()
    if len(shortlist) > MAX_PAIRS_FOR_COINTEGRATION:
        shortlist = shortlist.loc[
            shortlist["correlation"].abs().sort_values(ascending=False).index
        ].head(MAX_PAIRS_FOR_COINTEGRATION)

    if shortlist.empty:
        df = pd.DataFrame(columns=["symbol_a", "symbol_b", "correlation",
                                   "coint_pvalue", "n_obs", "as_of_date"])
        if persist:
            write_table(df, "derived", "pairs_candidates")
        return df

    universe_symbols = sorted(set(shortlist["symbol_a"]) | set(shortlist["symbol_b"]))
    prices = _price_panel(universe_symbols, as_of=as_of)
    panel = prices.pivot(index="trade_date", columns="symbol", values="close").tail(window_days)

    rows = []
    for _, r in shortlist.iterrows():
        a, b = r["symbol_a"], r["symbol_b"]
        pair = panel[[a, b]].dropna()
        if len(pair) < MIN_OBS:
            continue
        _, pvalue, _ = coint(pair[a], pair[b])
        rows.append({
            "symbol_a": a, "symbol_b": b, "correlation": r["correlation"],
            "coint_pvalue": pvalue, "n_obs": len(pair), "as_of_date": r["as_of_date"],
        })
    df = pd.DataFrame(rows)
    if persist:
        write_table(df, "derived", "pairs_candidates")
    return df
