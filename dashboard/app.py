"""
Single-pane dashboard over everything.

    streamlit run dashboard/app.py

Reads:
  - DuckDB views over parquet  -> history (EOD, macro, fundamentals, filings)
  - core.quality               -> freshness, volume and revision monitoring
  - Redis last-value cache     -> live ticks, if stream.py is running

Why DuckDB + parquet and not Postgres: the data is append-only analytical time
series. DuckDB queries parquet directly with no server to run. Move to
ClickHouse only when one machine is not enough.

The Data Quality tab is the one to look at first. Everything else shows what the
pipeline collected; that tab shows whether to believe it.
"""
import json
import sys
from pathlib import Path

# Streamlit puts the SCRIPT's directory on sys.path, not the project root, so
# `import core` fails with ModuleNotFoundError however you launch it -- and the
# failure only appears once a browser session actually runs the script, not when
# the server starts. Put the repo root on the path before importing anything
# from it.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from api.serializers.fundamentals import (
    INCOME_STATEMENT_PER_SHARE,
    balance_sheet,
    cash_flow,
    income_statement,
    label_with_units,
)
from core.asof import asof_sql, latest_per
from core.quality import (
    DATASET_KEYS,
    detect_revisions,
    fetch_freshness,
    quality_report,
    row_count_trend,
    view_paths,
)
from core.storage import db, register_views
from dashboard.theme import (
    BEARISH as DIVERGING_BEARISH,
    BULLISH as DIVERGING_BULLISH,
    NEUTRAL as DIVERGING_NEUTRAL,
    inject_css,
    metric_card,
)

st.set_page_config(page_title="Research Terminal", layout="wide")
inject_css()


@st.cache_resource
def _con():
    # READ-ONLY: DuckDB allows either one read-write process or several
    # read-only ones, so a read-write dashboard blocks run_daily.py from
    # writing for as long as the browser tab is open.
    con = db(read_only=True)
    return con, register_views(con)


con, views = _con()


def show(df, **kw):
    st.dataframe(df, width="stretch", **kw)


def query(sql: str) -> pd.DataFrame:
    # cursor(), not con, and the reason is not style. @st.cache_resource hands
    # the SAME DuckDBPyConnection to every browser session and every rerun
    # thread. execute() parks its result ON the connection and df() then fetches
    # it, so two interleaved calls race: one thread's df() collects the other's
    # result and the loser gets back None -- no exception, just None flowing
    # into callers that reasonably assume a DataFrame. cursor() issues a fresh
    # handle over the same database, which is DuckDB's documented way to share
    # one database across threads.
    return con.cursor().execute(sql).df()


# CAPM/correlation/momentum all need several weeks of daily nse_bhavcopy
# history before they can fit anything -- a single trading day gives 0
# return observations. Say that plainly instead of a bare "not computed", so
# an empty section reads as "accumulating", not "broken". Module-level (not
# tab-local) because both Quant Signals and Command Center need it, and
# Command Center's `with` block executes BEFORE Quant Signals' in source
# order -- a tab-local definition inside Quant Signals would not exist yet
# when Command Center runs.
HISTORY_NOTE = (
    "Needs several weeks of daily `nse_bhavcopy` history to compute (beta/"
    "momentum/correlation all need a trailing return window) -- run "
    "`python run_daily.py` daily and this fills in as history accumulates. "
    "A one-day-old warehouse showing nothing here is expected, not broken."
)


@st.cache_data(ttl=300)
def _gold_premium_history() -> pd.DataFrame:
    """Shared by the Gold complex tab and Command Center's snapshot card -- one query, not two."""
    if "derived_india_gold_premium" not in views:
        return pd.DataFrame()
    return query("""
        SELECT rate_date, session, ibja_999, comex_usd_per_oz, usdinr,
               landed_inr_per_10g, premium_inr_per_10g, premium_pct,
               import_duty_pct, gst_pct
        FROM derived_india_gold_premium ORDER BY rate_date, session
    """)


@st.cache_data(ttl=300)
def _top_turnover_symbols(n: int = 15) -> list[str]:
    """Top-n NSE symbols by latest-day turnover (close*volume) -- widens the
    correlation heatmap's default universe beyond the hand-maintained watchlist."""
    from analytics.universe import equity_universe
    try:
        uni = equity_universe()
    except RuntimeError:
        return []
    return uni.sort_values("turnover", ascending=False)["symbol"].head(n).tolist()


@st.cache_data(ttl=300)
def _symbol_picker_universe() -> list[str]:
    """Watchlist first (always mechanically relevant), then the rest of the
    live equity universe -- avoids presenting a ~2,400-symbol unsorted list
    with no ordering signal."""
    from analytics.universe import equity_universe
    from analytics.watchlist import WATCHLIST_SYMBOLS
    try:
        rest = sorted(set(equity_universe()["symbol"]) - set(WATCHLIST_SYMBOLS))
    except RuntimeError:
        rest = []
    return list(WATCHLIST_SYMBOLS) + rest


def _correlation_long(symbols: list[str]) -> pd.DataFrame:
    """
    Server-side-filtered pull from derived_correlation -- this table holds a
    pairwise correlation for the WHOLE NSE equity universe (~9M rows across
    vintages), so this NEVER pulls the full table into pandas; it filters on
    symbol_a/symbol_b in DuckDB first. Same vintage-collapse pattern (QUALIFY
    row_number()) as every other derived_* consumer in this file, keyed on
    the (symbol_a, symbol_b) pair since as_of_date can also advance between runs.
    """
    if "derived_correlation" not in views or len(symbols) < 2:
        return pd.DataFrame()
    placeholders = ", ".join(f"'{s}'" for s in symbols)
    # Two separate steps that used to be one QUALIFY. pit() collapses VINTAGES
    # (several writes of the same (pair, as_of_date)) and applies the header's
    # knowledge date; the outer window then applies the business rule -- of the
    # surviving computation dates, show the most recent. Folding both into one
    # ORDER BY worked, but only because as_of_date happened to lead; keeping
    # them apart means the as-of bound cannot be defeated by a stale row with a
    # newer as_of_date.
    inner = pit(
        "derived_correlation",
        columns="symbol_a, symbol_b, correlation, as_of_date",
        where=f"symbol_a IN ({placeholders}) AND symbol_b IN ({placeholders})",
    )
    return query(f"""
        SELECT * FROM ({inner})
        QUALIFY row_number() OVER (
            PARTITION BY symbol_a, symbol_b ORDER BY as_of_date DESC
        ) = 1
    """)


def _correlation_heatmap_figure(matrix: pd.DataFrame) -> go.Figure:
    """
    matrix may contain NaN for a pair with no stored correlation (see
    to_symmetric_matrix()/sector_correlation()'s NaN-not-zero convention) --
    those cells render as blank text ("no data"), never the literal string
    "nan" a plain float-to-text conversion would produce.
    """
    text = matrix.round(2).map(lambda v: "" if pd.isna(v) else f"{v:+.2f}")
    fig = go.Figure(go.Heatmap(
        z=matrix.values, x=list(matrix.columns), y=list(matrix.index),
        text=text.values, texttemplate="%{text}",
        colorscale=[[0.0, DIVERGING_BEARISH], [0.5, DIVERGING_NEUTRAL], [1.0, DIVERGING_BULLISH]],
        zmin=-1, zmid=0, zmax=1, colorbar=dict(title="corr"),
    ))
    fig.update_layout(margin=dict(t=8, l=8, r=8, b=8), height=max(320, 26 * len(matrix)))
    return fig


def _sector_correlation(long_df: pd.DataFrame) -> pd.DataFrame | None:
    """
    NIFTY-50-only industry rollup of an already-fetched correlation long
    frame. nse_index_constituents is the ONLY dataset in this warehouse with
    a verified Industry field -- bse_scrip_master has none (checked its
    _check_schema() call in connectors/nse_bse.py). NOT valid for symbols
    outside NIFTY 50. Returns None (not an empty frame) when the join can't
    be done at all, so callers can tell "not possible" from "no data yet".
    """
    if "nse_index_constituents" not in views or long_df.empty:
        return None
    industry = query(pit("nse_index_constituents",
                         columns='"Symbol" AS symbol, "Industry" AS industry',
                         where="\"index\" = 'nifty50'"))
    if industry.empty:
        return None
    from analytics.correlation import sector_correlation
    result = sector_correlation(long_df, industry)
    return result if not result.empty else None


@st.cache_data(ttl=300)
def _digest_latest() -> pd.DataFrame:
    """Latest vintage per instrument from derived_digest. Empty if not computed yet."""
    if "derived_digest" not in views:
        return pd.DataFrame()
    inner = pit_latest("derived_digest", ["instrument"],
                       columns="instrument, composite_score, flag, "
                               "contributing_signals, as_of_date, tier")
    return query(f"""
        SELECT * FROM ({inner})
        ORDER BY abs(composite_score) DESC LIMIT 200
    """)


def _digest_top_movers(digest_df: pd.DataFrame, n: int) -> None:
    """
    Bullish/bearish top-N card strip off a digest-shaped dataframe
    (instrument, composite_score, flag). Shared by Command Center's compact
    strip (n=3) and Quant Signals' full strip (n=5) -- same cards, different
    N, so there is one definition instead of two.
    """
    if digest_df.empty:
        st.caption("No signals yet.")
        return
    bullish = digest_df[digest_df["composite_score"] > 0].nlargest(n, "composite_score")
    bearish = digest_df[digest_df["composite_score"] < 0].nsmallest(n, "composite_score")
    for label, rows in [("Bullish", bullish), ("Bearish", bearish)]:
        st.caption(label)
        if rows.empty:
            st.caption("none")
            continue
        cols = st.columns(n)
        for col, (_, r) in zip(cols, rows.iterrows()):
            with col:
                st.metric(r["instrument"], f"{r['composite_score']:+.2f}")
                flag = r["flag"]
                st.caption(flag[:60] + ("..." if len(flag) > 60 else ""))


@st.cache_data(ttl=300)
def _multivariate_outlier_counts() -> tuple:
    """(flagged, total) from derived_multivariate_outliers, latest vintage per symbol."""
    if "derived_multivariate_outliers" not in views:
        return (0, 0)
    df = query(pit_latest("derived_multivariate_outliers", ["symbol"],
                          columns="symbol, is_outlier"))
    return (int(df["is_outlier"].sum()), len(df))


@st.cache_data(ttl=300)
def _resolve_company_name(symbol: str) -> str | None:
    """
    Best-effort NSE symbol -> BSE-registered company name, via the ISIN both
    sides share. Used only to pre-fill the Credit Ratings free-text search
    from the global focus symbol -- Credit Ratings keys on company name, not
    symbol, so this is a convenience default, not a hard join. Returns None
    (leaving the search box at its default) whenever either table is
    missing or no ISIN match is found.
    """
    if not symbol or "nse_bhavcopy" not in views or "bse_scrip_master" not in views:
        return None
    df = query(f"""
        SELECT any_value(m."Scrip_Name") AS name
        FROM nse_bhavcopy b
        JOIN bse_scrip_master m ON b."ISIN" = m."ISIN_NUMBER"
        WHERE b."TckrSymb" = '{symbol}'
    """)
    if df.empty or pd.isna(df["name"].iloc[0]):
        return None
    return df["name"].iloc[0]


@st.cache_data(ttl=300)
def _fii_divergence_counts() -> tuple:
    """(diverging metrics, total metrics) from derived_fii_source_divergence, latest vintage per metric."""
    if "derived_fii_source_divergence" not in views:
        return (0, 0)
    df = query(pit_latest("derived_fii_source_divergence", ["metric"],
                          columns="metric, diverges"))
    return (int(df["diverges"].sum()), len(df))


@st.cache_data(ttl=300)
def _cross_asset_correlation_tables() -> tuple:
    """(equity-vs-commodity, equity-vs-macro) latest vintage per pair. Either
    or both can be empty -- see analytics/cross_asset_correlation.py's
    per-leg skip-not-fail contract; this warehouse's commodity/RBI history is
    still thin, so an empty result here is expected, not broken."""
    commodity = query(pit_latest(
        "derived_equity_commodity_correlation", ["asset_a", "asset_b"],
        columns="asset_a, asset_b, correlation, n_obs, as_of_date",
    )) if "derived_equity_commodity_correlation" in views else pd.DataFrame()
    macro = query(pit_latest(
        "derived_equity_macro_correlation", ["equity_symbol", "macro_series"],
        columns="equity_symbol, macro_series, correlation, n_obs, as_of_date",
    )) if "derived_equity_macro_correlation" in views else pd.DataFrame()
    return (commodity, macro)


st.title("Research Terminal")
st.caption(f"{len(views)} datasets registered")

# Cached so the same computation backs both this banner and the Data Quality
# tab's default view -- quality_report() walks the whole raw archive
# (thousands of sidecar files after a backfill), and Streamlit reruns the
# ENTIRE script on every interaction, so computing it twice per rerun was a
# real, measured slowdown, not just tidiness. ttl=300 means a stale-going-OK
# transition (e.g. right after running run_daily.py) shows up within 5
# minutes without a full page reload.
@st.cache_data(ttl=300)
def _cached_quality_report(stale_after_hours: float):
    return quality_report(stale_after_hours=stale_after_hours)


# listed_company_names() scans bse_scrip_master + nse_shareholding_pattern +
# nse_results_Quarterly (~5,700 distinct names combined) -- cheap compared to
# quality_report(), but still no reason to redo it on every rerun.
@st.cache_data(ttl=300)
def _cached_listed_names():
    from analytics.listing_status import listed_company_names
    return listed_company_names()


# The YT roster is a separate pipeline's output (SQLite -> roster.py), so it is
# read as a file rather than queried. Cached on mtime so rebuilding the roster
# shows up without restarting Streamlit, but a rerun does not re-parse 130KB of
# JSON for every widget click.
@st.cache_data(ttl=300)
def _cached_yt_roster(path_str: str, mtime: float):
    with open(path_str, encoding="utf-8") as fh:
        payload = json.load(fh)
    df = pd.DataFrame(payload.get("companies", []))
    if not df.empty:
        df["channels"] = df["channels"].apply(
            lambda cs: ", ".join(c.replace(" Podcast", "") for c in cs)
        )
        df["aliases"] = df["aliases"].apply(lambda a: ", ".join(a))
        df["episodes"] = df["episodes"].apply(len)
    return payload.get("stats", {}), df


# Banner, not a tab someone has to remember to open. Phase 8 found nine
# datasets stuck stale for four days precisely because nothing surfaced it
# outside the Data Quality tab -- this is the fix: visible the moment the
# dashboard opens, on any tab, same 48h threshold run_daily.py itself now
# alerts on (core.alerting.alert_staleness).
_banner_report = _cached_quality_report(48.0)
if not _banner_report.empty:
    _stale = _banner_report[_banner_report["status"] == "STALE"]
    if not _stale.empty:
        _names = ", ".join(_stale["view"].head(8))
        _more = f" (+{len(_stale) - 8} more)" if len(_stale) > 8 else ""
        st.error(
            f"{len(_stale)} dataset(s) have had no fresh bytes in 48h+: "
            f"{_names}{_more} -- see the Data quality tab, or run `python run_daily.py`."
        )

SYMBOL_STATE_KEY = "selected_symbol"
ASOF_STATE_KEY = "as_of_date"

_pick_col, _asof_col = st.columns([3, 1])
with _pick_col:
    _symbol_universe = _symbol_picker_universe()
    if _symbol_universe:
        st.selectbox("Focus symbol (used across tabs below)", _symbol_universe,
                     key=SYMBOL_STATE_KEY)
with _asof_col:
    st.date_input(
        "Knowledge date", value=None, key=ASOF_STATE_KEY,
        help="Show the warehouse as it was on this date. Empty = everything "
             "known now. Rows are filtered on knowledge_date, so derived "
             "signals roll back with the prices they were computed from.",
    )

# None means "everything known now", which is what every query did before the
# control existed.
AS_OF = st.session_state.get(ASOF_STATE_KEY) or None


def pit(view: str, **kw) -> str:
    """
    Point-in-time SQL for `view`, bound to the header's knowledge date.

    Every panel that reads through this collapses vintages on the view's
    declared natural key (core.quality.DATASET_KEYS) instead of carrying its
    own hand-written QUALIFY, and honours the as-of control for free.
    """
    return asof_sql(view, AS_OF, **kw)


def pit_latest(view: str, per: list[str], **kw) -> str:
    """
    One row per `per`, point-in-time. For derived_* tables whose natural key
    includes as_of_date but where a panel wants only the current value.
    """
    return latest_per(view, per, AS_OF, **kw)


# A back-dated screen that looks identical to a live one is an expensive
# mistake to make, so say so persistently rather than relying on the reader
# noticing the date box.
if AS_OF:
    st.warning(
        f"**Historical view — {AS_OF:%Y-%m-%d}.** Panels below marked "
        f"*point-in-time* show only what the warehouse knew on that date. "
        f"Panels not yet migrated still show everything known now."
    )

(tab_command, tab_equities, tab_macro_group, tab_credit_group, tab_flows, tab_signals,
 tab_ops) = st.tabs(
    ["Command Center", "Equities & Quant Signals", "Macro & Commodities",
     "Credit & Fundamentals", "Flows & Positioning", "Signals", "Data Ops"]
)

# ------------------------------------------------------------------ Command Center
with tab_command:
    st.subheader("One screen, everything correlated")
    st.caption(
        "Every panel below reads the exact same tables the other tabs do -- "
        "nothing here is a separate computation. This is where cross-dataset "
        "signals that a single-table tab can't show live: correlation across "
        "the watchlist, macro/market direction, and what's flagged unusual "
        "right now."
    )

    st.markdown("##### Pipeline health")
    if _banner_report.empty:
        st.info("Nothing in the warehouse yet. Run `python run_daily.py`.")
    else:
        _cc_counts = _banner_report["status"].value_counts().to_dict()
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metric_card("Datasets", str(len(_banner_report)))
        with c2:
            metric_card("Stale", str(_cc_counts.get("STALE", 0)),
                        tone="bearish" if _cc_counts.get("STALE", 0) else "neutral")
        with c3:
            metric_card("Collapsed", str(_cc_counts.get("COLLAPSED", 0)),
                        tone="bearish" if _cc_counts.get("COLLAPSED", 0) else "neutral")
        with c4:
            metric_card("Revised", str(_cc_counts.get("REVISED", 0)),
                        tone="bearish" if _cc_counts.get("REVISED", 0) else "neutral")

    st.markdown("---")
    st.markdown("##### Macro & markets snapshot")
    m1, m2, m3, m4, m5 = st.columns(5)

    with m1:
        repo_df = query("""
            SELECT rate_pct FROM rbi_policy_rates
            WHERE rate_name = 'Policy Repo Rate'
            QUALIFY row_number() OVER (ORDER BY observed_at DESC, knowledge_date DESC) = 1
        """) if "rbi_policy_rates" in views else pd.DataFrame()
        metric_card("Policy repo rate",
                    f"{repo_df['rate_pct'].iloc[0]:.2f}%" if not repo_df.empty else "n/a")

    with m2:
        fx_df = query(pit(
            "rbi_forex_reserves", columns="week_ended, amount_usd",
            where="component_code = 'TR'", order_by="week_ended",
        )) if "rbi_forex_reserves" in views else pd.DataFrame()
        if not fx_df.empty:
            fx_delta = ""
            if len(fx_df) > 1:
                prev = fx_df["amount_usd"].iloc[-2]
                fx_delta = f"{(fx_df['amount_usd'].iloc[-1] - prev) / prev * 100:+.2f}% w/w"
            metric_card("Forex reserves (Total)", f"${fx_df['amount_usd'].iloc[-1]:,.0f}M", fx_delta)
        else:
            metric_card("Forex reserves (Total)", "n/a")

    with m3:
        gold_df = _gold_premium_history()
        if not gold_df.empty:
            latest_gold = gold_df.iloc[-1]
            metric_card("India gold premium", f"{latest_gold['premium_pct']:.2f}%",
                        tone="bullish" if latest_gold["premium_pct"] >= 0 else "bearish")
        else:
            metric_card("India gold premium", "n/a")

    # The SELECT DISTINCT this used to open with was a workaround for duplicate
    # vintages, and a weak one: it only dedupes rows that agree on netValue, so
    # a revised figure survived as a second row and the PARTITION BY picked
    # between them arbitrarily. pit() collapses on the declared key
    # (category, date) instead, leaving the outer window to do only its real
    # job -- the most recent published date per category.
    flows_df = query(f"""
        SELECT category, try_cast(netValue AS DOUBLE) AS netValue
        FROM ({pit("nse_fii_dii", columns="category, date, netValue")})
        QUALIFY row_number() OVER (
            PARTITION BY category ORDER BY strptime(date, '%d-%b-%Y') DESC
        ) = 1
    """) if "nse_fii_dii" in views else pd.DataFrame()
    with m4:
        fii_row = flows_df[flows_df["category"] == "FII/FPI"] if not flows_df.empty else pd.DataFrame()
        if not fii_row.empty:
            v = fii_row["netValue"].iloc[0]
            metric_card("FII net flow", f"Rs {v:,.0f} Cr", tone="bullish" if v >= 0 else "bearish")
        else:
            metric_card("FII net flow", "n/a")
    with m5:
        dii_row = flows_df[flows_df["category"] == "DII"] if not flows_df.empty else pd.DataFrame()
        if not dii_row.empty:
            v = dii_row["netValue"].iloc[0]
            metric_card("DII net flow", f"Rs {v:,.0f} Cr", tone="bullish" if v >= 0 else "bearish")
        else:
            metric_card("DII net flow", "n/a")

    st.markdown("---")
    st.markdown("##### Correlation")
    if "derived_correlation" not in views:
        st.caption(HISTORY_NOTE)
    else:
        from analytics.correlation import to_symmetric_matrix
        from analytics.watchlist import WATCHLIST_SYMBOLS as _CC_WATCHLIST

        cc_universe = sorted(set(_CC_WATCHLIST) | set(_top_turnover_symbols(15)))
        _focus = st.session_state.get(SYMBOL_STATE_KEY)
        if _focus and _focus not in cc_universe:
            cc_universe = sorted(set(cc_universe) | {_focus})

        cc_view = st.radio("View", ["By symbol", "By sector (NIFTY 50 only)"],
                           horizontal=True, key="cc_corr_view")
        cc_symbols = st.multiselect(
            "Symbols (max 60)", sorted(set(cc_universe) | set(_symbol_picker_universe())),
            default=cc_universe, key="cc_corr_symbols",
        )
        if len(cc_symbols) > 60:
            st.warning(
                "derived_correlation holds ~9M rows across the full NSE universe -- "
                "capped to the first 60 symbols to keep the heatmap query and render fast."
            )
            cc_symbols = cc_symbols[:60]

        if len(cc_symbols) < 2:
            st.caption("Pick at least 2 symbols.")
        else:
            cc_long = _correlation_long(cc_symbols)
            if cc_long.empty:
                st.caption("No correlation computed yet for these symbols. " + HISTORY_NOTE)
            elif cc_view == "By symbol":
                cc_matrix = to_symmetric_matrix(cc_long, cc_symbols)
                st.plotly_chart(_correlation_heatmap_figure(cc_matrix), width="stretch")
            else:
                st.caption(
                    "NIFTY-50-only: nse_index_constituents is the only dataset in this "
                    "warehouse with a verified industry classification -- this rollup "
                    "cannot cover symbols outside the index."
                )
                sector_df = _sector_correlation(cc_long)
                if sector_df is None or sector_df.empty:
                    st.caption("No NIFTY-50 industry overlap in the selected symbols yet.")
                else:
                    sector_matrix = sector_df.pivot(index="industry_a", columns="industry_b",
                                                    values="correlation")
                    all_industries = sorted(set(sector_matrix.index) | set(sector_matrix.columns))
                    sector_matrix = sector_matrix.reindex(index=all_industries, columns=all_industries)
                    # sector_correlation() only fills whichever (industry_a, industry_b)
                    # direction the underlying symbol pair happened to fall in
                    # (derived_correlation's symbol_a < symbol_b convention) -- mirror
                    # so a cell missing in one triangle but present in the other shows
                    # its real value instead of a false "no correlation". A cell with
                    # NO data in either direction stays NaN and renders blank, which is
                    # honest; it is never filled with 0.
                    sector_matrix = sector_matrix.combine_first(sector_matrix.T)
                    st.plotly_chart(_correlation_heatmap_figure(sector_matrix),
                                    width="stretch")

    st.markdown("---")
    st.markdown("##### Cross-asset check")
    st.caption(
        "NIFTYBEES vs commodities (daily) and vs RBI macro series (monthly) -- "
        "see analytics/cross_asset_correlation.py. Both legs need real accumulated "
        "history (MCX/CME/IBJA daily runs, months of RBI snapshots), so an empty "
        "table here on a young warehouse is expected, not broken."
    )
    _cc_commodity_corr, _cc_macro_corr = _cross_asset_correlation_tables()
    cx1, cx2 = st.columns(2)
    with cx1:
        st.caption("Equity vs commodity (daily)")
        if _cc_commodity_corr.empty:
            st.caption(HISTORY_NOTE)
        else:
            show(_cc_commodity_corr)
    with cx2:
        st.caption("Equity vs macro (monthly)")
        if _cc_macro_corr.empty:
            st.caption(HISTORY_NOTE)
        else:
            show(_cc_macro_corr)

    st.markdown("---")
    st.markdown("##### Signals & anomalies")
    _cc_digest = _digest_latest()
    if _cc_digest.empty:
        st.caption("No digest yet -- run `analytics_digest`. " + HISTORY_NOTE)
    else:
        _digest_top_movers(_cc_digest[_cc_digest.get("tier") == "broad_scan"], n=3)

    a1, a2 = st.columns(2)
    _out_flagged, _out_total = _multivariate_outlier_counts()
    _fii_flagged, _fii_total = _fii_divergence_counts()
    with a1:
        metric_card("Multivariate outliers", f"{_out_flagged} / {_out_total}",
                    tone="bearish" if _out_flagged else "neutral")
    with a2:
        metric_card("FII/DII source divergence", f"{_fii_flagged} / {_fii_total}",
                    tone="bearish" if _fii_flagged else "neutral")


# -------------------------------------------------------------------- Equities & Quant Signals
with tab_equities:
    sub_quant, sub_live = st.tabs(["Quant Signals", "Live Ticks"])

    with sub_quant:
        st.subheader("India equities + F&O: composite signal ranking")
        st.caption(
            "Every number here is deterministic Python -- CAPM beta, momentum "
            "z-score, variance-ratio, bulk/block-deal anomaly, Black-Scholes "
            "Greeks. DeepSeek's only role is to phrase the one-line flag below; "
            "it never computes a score."
        )

        # HISTORY_NOTE is module-level (see its definition above) -- Command
        # Center needs it too, and executes before this tab in source order.

        # Diverging pair for composite_score (-1..1): blue<->red, gray midpoint.
        # NOT green/red -- red-green is the most common form of colorblindness,
        # which would make the single most important signal on this tab
        # unreadable for those users. Blue<->red is a validated CVD-safe
        # diverging pair (see the dataviz skill's palette). Every cell/card also
        # carries an explicit up/down arrow + signed number in TEXT, not just
        # fill color, so nothing here depends on color alone. Defined once in
        # dashboard/theme.py (imported above as DIVERGING_BEARISH/NEUTRAL/
        # BULLISH) so every heatmap/treemap on this page shares one definition.

        if "derived_digest" in views:
            # write_table() never overwrites -- a same-day rerun leaves a second
            # vintage sharing knowledge_date's date-granularity, so a plain
            # SELECT can silently double an instrument's row. _digest_latest()
            # dedupes to the latest vintage per instrument (same fix as
            # analytics/digest.py and analytics/outliers.py needed internally),
            # and is shared with Command Center's compact strip.
            digest = _digest_latest()
            watchlist_df = digest[digest.get("tier") == "watchlist"]
            broad_df = digest[digest.get("tier") == "broad_scan"]

            # ---- Watchlist: index heavyweights, shown regardless of score ----
            st.markdown("##### Watchlist -- index heavyweights (always shown)")
            st.caption(
                "NIFTY 50 constituents matter mechanically: this engine prices NIFTY/"
                "BANKNIFTY options against these levels, so a heavyweight's move "
                "matters regardless of its statistical z-score -- these are shown "
                "every run, not just when something looks extreme. Symbol list is "
                "hand-maintained (analytics/watchlist.py) -- provisional until a "
                "verified index-constituent source exists (see PHASE8 plan)."
            )
            if watchlist_df.empty:
                st.caption("No watchlist symbols have a signal yet.")
            else:
                show(watchlist_df[["instrument", "composite_score", "flag", "as_of_date"]])

            st.markdown("---")

            # ---- Broad scan: top movers card strip ---------------------------
            st.markdown("##### Broad scan -- top movers")
            st.caption(
                "Statistical outliers across the full ~2,463-instrument universe -- "
                "this is a statistical-extremity ranking, not an importance ranking. "
                "A name appearing here is unusual relative to its own history/the "
                "cross-section, not necessarily large or liquid. See Watchlist above "
                "for mechanically important names."
            )
            _digest_top_movers(broad_df, n=5)

            # ---- Broad scan: signal map heatmap -------------------------------
            st.markdown("##### Broad scan -- signal map")
            st.caption(
                "Every broad-scan instrument, one glance. Size = conviction "
                "(|score|), color = direction. Arrow + signed number in every "
                "cell -- never color alone. Excludes Watchlist symbols (shown "
                "separately above) so a name isn't narrated twice."
            )
            if broad_df.empty:
                st.caption("No broad-scan instruments this run.")
            else:
                heat = broad_df.copy()
                heat["cell_label"] = heat["composite_score"].apply(
                    lambda s: ("▲" if s >= 0 else "▼") + f" {s:+.2f}")
                heat["cell_size"] = heat["composite_score"].abs().clip(lower=0.05)

                fig = go.Figure(go.Treemap(
                    labels=heat["instrument"],
                    parents=[""] * len(heat),
                    values=heat["cell_size"],
                    text=heat["cell_label"],
                    textinfo="label+text",
                    customdata=heat[["flag"]],
                    hovertemplate="<b>%{label}</b> %{text}<br>%{customdata[0]}<extra></extra>",
                    marker=dict(
                        colors=heat["composite_score"],
                        colorscale=[[0.0, DIVERGING_BEARISH], [0.5, DIVERGING_NEUTRAL], [1.0, DIVERGING_BULLISH]],
                        cmin=-1, cmid=0, cmax=1,
                        showscale=True,
                        colorbar=dict(title="score", tickvals=[-1, 0, 1],
                                      ticktext=["-1 bearish", "0", "+1 bullish"]),
                    ),
                ))
                fig.update_layout(margin=dict(t=8, l=8, r=8, b=8), height=420)
                st.plotly_chart(fig, width="stretch")

            # ---- the table-view twin -- every value also reachable without
            # hovering or reading color, per the dataviz skill's accessibility rule.
            st.markdown("##### Full ranking (table, both tiers)")
            show(digest)
            _digest_instruments = digest["instrument"].tolist() if not digest.empty else []

            def _push_focus_from_digest():
                st.session_state[SYMBOL_STATE_KEY] = st.session_state["digest_drilldown"]

            _focus = st.session_state.get(SYMBOL_STATE_KEY)
            _default_idx = _digest_instruments.index(_focus) if _focus in _digest_instruments else 0
            pick = st.selectbox("Drill down", _digest_instruments,
                                index=_default_idx if _digest_instruments else 0,
                                key="digest_drilldown", on_change=_push_focus_from_digest)
            if pick:
                st.markdown(f"**{pick} -- contributing signals**")
                c1, c2 = st.columns(2)
                with c1:
                    st.caption("CAPM beta")
                    try:
                        show(query(f"""SELECT * FROM derived_capm_beta
                                       WHERE symbol = '{pick}'
                                       ORDER BY as_of_date DESC, knowledge_date DESC LIMIT 1"""))
                    except Exception as e:
                        st.caption(str(e)[:100])
                with c2:
                    st.caption("Momentum / variance ratio")
                    try:
                        show(query(f"""SELECT * FROM derived_momentum_zscore
                                       WHERE symbol = '{pick}'
                                       ORDER BY as_of_date DESC, knowledge_date DESC LIMIT 1"""))
                    except Exception as e:
                        st.caption(str(e)[:100])
        else:
            st.info(
                "No digest yet -- run `analytics_capm_beta`, `analytics_momentum`, "
                "then `analytics_digest` (needs DEEPSEEK_API_KEY in .env). "
                + HISTORY_NOTE
            )

        st.markdown("---")
        st.markdown("##### Factor model -- momentum / size / low-volatility")
        st.caption(
            "Cross-sectional factor SCORES, not the digest's event-driven composite above -- "
            "this ranks the universe by style tilt (trailing momentum, small-cap, low realized "
            "volatility), not by what's unusual today. Value and quality factors are NOT included: "
            "no book value/earnings/debt is parsed anywhere in this warehouse yet "
            "(nse_results_Quarterly only indexes filings and links to XBRL, it doesn't parse them) "
            "-- see analytics/risk_model.py. Sign convention: higher composite_score = more "
            "attractive by each factor's own academic convention (long small, long calm, long "
            "trailing winners). No separate watchlist tier here, unlike the digest above -- factor "
            "tilt isn't a mechanical-importance signal the way index-heavyweight option pricing is, "
            "so every instrument is scored on equal footing."
        )
        if "derived_factor_model" in views:
            factor_df = query(f"""
                SELECT * FROM ({pit_latest(
                    "derived_factor_model", ["symbol"],
                    columns="symbol, momentum_zscore, size_score, "
                            "low_vol_score, composite_score, as_of_date")})
                ORDER BY composite_score DESC
            """)

            # ---- Top movers card strip ----
            st.markdown("###### Top movers")
            bullish = factor_df[factor_df["composite_score"] > 0].nlargest(5, "composite_score")
            bearish = factor_df[factor_df["composite_score"] < 0].nsmallest(5, "composite_score")

            def _factor_card_row(label, rows):
                st.caption(label)
                if rows.empty:
                    st.caption("none")
                    return
                cols = st.columns(5)
                for col, (_, r) in zip(cols, rows.iterrows()):
                    with col:
                        st.metric(r["symbol"], f"{r['composite_score']:+.2f}")

            _factor_card_row("Bullish tilt (small / calm / trending up)", bullish)
            _factor_card_row("Bearish tilt (large / volatile / trending down)", bearish)

            # ---- Signal map treemap ----
            st.markdown("###### Signal map")
            st.caption(
                "Every scored instrument, one glance. Size = conviction (|score|), color = "
                "direction. Arrow + signed number in every cell -- never color alone (same "
                "accessibility rule as the digest treemap above)."
            )
            if factor_df.empty:
                st.caption("No scored instruments yet.")
            else:
                heat = factor_df.copy()
                heat["cell_label"] = heat["composite_score"].apply(
                    lambda s: ("▲" if s >= 0 else "▼") + f" {s:+.2f}")
                heat["cell_size"] = heat["composite_score"].abs().clip(lower=0.05)

                fig = go.Figure(go.Treemap(
                    labels=heat["symbol"],
                    parents=[""] * len(heat),
                    values=heat["cell_size"],
                    text=heat["cell_label"],
                    textinfo="label+text",
                    hovertemplate="<b>%{label}</b> %{text}<extra></extra>",
                    marker=dict(
                        colors=heat["composite_score"],
                        colorscale=[[0.0, DIVERGING_BEARISH], [0.5, DIVERGING_NEUTRAL], [1.0, DIVERGING_BULLISH]],
                        cmin=-1, cmid=0, cmax=1,
                        showscale=True,
                        colorbar=dict(title="score", tickvals=[-1, 0, 1],
                                      ticktext=["-1 bearish", "0", "+1 bullish"]),
                    ),
                ))
                fig.update_layout(margin=dict(t=8, l=8, r=8, b=8), height=420)
                st.plotly_chart(fig, width="stretch")

            # ---- the table-view twin + drill-down ----
            st.markdown("###### Full ranking (table)")
            show(factor_df)
            _factor_symbols = factor_df["symbol"].tolist() if not factor_df.empty else []

            def _push_focus_from_factor():
                st.session_state[SYMBOL_STATE_KEY] = st.session_state["factor_model_drilldown"]

            _focus = st.session_state.get(SYMBOL_STATE_KEY)
            _default_idx = _factor_symbols.index(_focus) if _focus in _factor_symbols else 0
            factor_pick = st.selectbox("Drill down", _factor_symbols, index=_default_idx,
                                       key="factor_model_drilldown", on_change=_push_focus_from_factor)
            if factor_pick:
                st.markdown(f"**{factor_pick} -- contributing factors**")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.caption("Momentum")
                    try:
                        show(query(f"""SELECT * FROM derived_momentum_zscore
                                       WHERE symbol = '{factor_pick}'
                                       ORDER BY as_of_date DESC, knowledge_date DESC LIMIT 1"""))
                    except Exception as e:
                        st.caption(str(e)[:100])
                with c2:
                    st.caption("Size (BSE market cap)")
                    try:
                        show(query(f"""SELECT * FROM derived_size_factor
                                       WHERE symbol = '{factor_pick}'
                                       ORDER BY as_of_date DESC, knowledge_date DESC LIMIT 1"""))
                    except Exception as e:
                        st.caption(str(e)[:100])
                with c3:
                    st.caption("Low volatility")
                    try:
                        show(query(f"""SELECT * FROM derived_low_vol_factor
                                       WHERE symbol = '{factor_pick}'
                                       ORDER BY as_of_date DESC, knowledge_date DESC LIMIT 1"""))
                    except Exception as e:
                        st.caption(str(e)[:100])
        else:
            st.info(
                "No factor model yet -- run `analytics_momentum`, then `analytics_risk_model` "
                "(needs bse_scrip_master for the size factor's market-cap join). " + HISTORY_NOTE
            )

        st.markdown("---")
        st.markdown("##### Corporate events (BSE, material-event flags)")
        st.caption(
            "Deterministic filter on BSE's SUBCATNAME classifier -- management "
            "changes, resignations, credit actions, scheme of arrangement. No "
            "z-score: discrete events, not a continuous quantity, so this stays "
            "a separate feed rather than folding into the composite score above. "
            "Known gap: BSE caps at 50 rows/category/page, so a busy day in one "
            "category can silently drop rows beyond the first page "
            "(connectors/nse_bse.py's bse_announcements())."
        )
        if "derived_corporate_events" in views:
            events_df = query(f"""
                SELECT * FROM ({pit(
                    "derived_corporate_events",
                    columns="NEWSID, company_name AS company, subcatname AS event_type, "
                            "headline, news_dt")})
                ORDER BY news_dt DESC LIMIT 100
            """)
            if events_df.empty:
                st.caption("No material events in the trailing window.")
            else:
                show(events_df[["company", "event_type", "headline", "news_dt"]])
        else:
            st.info(
                "No corporate-events data yet -- run `analytics_corporate_events` "
                "(needs bse_announcements + bse_scrip_master first)."
            )

        st.markdown("---")
        st.markdown("#### Option chain (NIFTY / BANKNIFTY)")
        st.caption("Once-daily intraday snapshot, not a continuous feed -- see analytics/options.py.")
        if "nse_optchain" in views:
            chain_symbols = sorted(query("SELECT DISTINCT symbol FROM nse_optchain")["symbol"])
            if not chain_symbols:
                st.caption("no rows yet")
            else:
                chain_symbol = st.radio("Symbol", chain_symbols, horizontal=True, key="optchain_symbol")
                raw_chain = query(f"""
                    SELECT strike_price, option_type, open_interest, change_in_oi,
                           total_traded_volume, implied_volatility_nse, last_price,
                           price_change, bid_qty, bid_price, ask_price, ask_qty,
                           underlying_value, expiry_date
                    FROM nse_optchain
                    WHERE symbol = '{chain_symbol}'
                      AND fetched_at_utc = (
                          SELECT max(fetched_at_utc) FROM nse_optchain WHERE symbol = '{chain_symbol}'
                      )
                    ORDER BY strike_price
                """)
                if raw_chain.empty:
                    st.caption("no rows for this symbol")
                else:
                    spot = raw_chain["underlying_value"].iloc[0]
                    expiry = raw_chain["expiry_date"].iloc[0]
                    st.caption(f"Spot {spot:,.2f} · Expiry {expiry}")

                    calls = raw_chain[raw_chain["option_type"] == "CE"].set_index("strike_price")
                    puts = raw_chain[raw_chain["option_type"] == "PE"].set_index("strike_price")
                    strikes = sorted(set(calls.index) | set(puts.index))

                    # Mirrors the standard NSE layout: CALL metrics (OI -> Ask Qty,
                    # nearest-to-strike first) | STRIKE | PUT metrics (Bid Qty ->
                    # OI, mirrored so the two sides read as a reflection around
                    # STRIKE). st.dataframe has no spanning "CALLS"/"PUTS" header,
                    # so each column is labelled explicitly instead.
                    metrics = [
                        ("open_interest", "OI"), ("change_in_oi", "Chng OI"),
                        ("total_traded_volume", "Volume"), ("implied_volatility_nse", "IV"),
                        ("last_price", "LTP"), ("price_change", "Chng"),
                        ("bid_qty", "Bid Qty"), ("bid_price", "Bid"),
                        ("ask_price", "Ask"), ("ask_qty", "Ask Qty"),
                    ]
                    table = pd.DataFrame(index=strikes)
                    for col, label in metrics:
                        table[f"CALL {label}"] = calls[col].reindex(strikes) if col in calls else None
                    table["STRIKE"] = strikes
                    for col, label in reversed(metrics):
                        table[f"PUT {label}"] = puts[col].reindex(strikes) if col in puts else None

                    # Full chains run 100+ strikes either side of spot -- default
                    # to the 20 nearest to spot, expandable to the full chain.
                    table["_dist"] = (table["STRIKE"] - spot).abs()
                    near = table.sort_values("_dist").head(20).sort_values("STRIKE").drop(columns="_dist")
                    show(near, hide_index=True)
                    with st.expander(f"Full chain ({len(table)} strikes)"):
                        show(table.drop(columns="_dist"), hide_index=True)
        else:
            st.caption("not yet ingested")

        st.markdown("---")
        st.markdown("#### Pairs-trading candidates (correlation + cointegration)")
        if "derived_pairs_candidates" in views:
            pairs = query("""
                SELECT symbol_a, symbol_b, correlation, coint_pvalue, n_obs, as_of_date
                FROM derived_pairs_candidates
                ORDER BY coint_pvalue ASC LIMIT 50
            """)
            if pairs.empty:
                st.caption("No pairs cleared the correlation threshold yet. " + HISTORY_NOTE)
            else:
                show(pairs)
        else:
            st.caption(HISTORY_NOTE)

        st.markdown("---")
        st.markdown("#### Correlation heatmap")
        st.caption(
            "derived_correlation holds a pairwise correlation for the WHOLE NSE "
            "equity universe (~9M rows across vintages) -- this always filters to "
            "a small symbol set server-side in DuckDB before rendering; it never "
            "pulls the full table into pandas."
        )
        if "derived_correlation" not in views:
            st.caption(HISTORY_NOTE)
        else:
            from analytics.correlation import to_symmetric_matrix
            from analytics.watchlist import WATCHLIST_SYMBOLS

            heat_symbols = st.multiselect(
                "Symbols", WATCHLIST_SYMBOLS, default=list(WATCHLIST_SYMBOLS),
                key="quant_corr_symbols",
            )
            if len(heat_symbols) < 2:
                st.caption("Pick at least 2 symbols.")
            else:
                corr_long = _correlation_long(heat_symbols)
                if corr_long.empty:
                    st.caption("No correlation computed yet for these symbols. " + HISTORY_NOTE)
                else:
                    matrix = to_symmetric_matrix(corr_long, heat_symbols)
                    st.plotly_chart(_correlation_heatmap_figure(matrix), width="stretch")
                    st.caption(f"As of {corr_long['as_of_date'].max()}.")

        st.markdown("---")
        st.markdown("#### FII cross-source reconciliation")
        st.caption(
            "NSE participant-OI vs Upstox's independently-polled FII feed -- these should "
            "read the same number. A flagged row means one source is stale, lagging, or "
            "wrong; it is a data-quality check, not two institutions disagreeing."
        )
        if "derived_fii_source_divergence" in views:
            # Same-day reruns can leave more than one vintage per metric --
            # dedupe to the latest write before displaying, same fix as the
            # analytics_digest/analytics_outliers joins.
            fii = query(f"""
                SELECT * FROM ({pit_latest(
                    "derived_fii_source_divergence", ["metric"],
                    columns="metric, nse_value, upstox_value, pct_diff, diverges")})
                ORDER BY pct_diff DESC
            """)
            flagged = fii[fii["diverges"]]
            if flagged.empty:
                st.success(f"All {len(fii)} metrics match within tolerance.")
            else:
                st.warning(f"{len(flagged)} of {len(fii)} metrics diverge past tolerance.")
            show(fii)
        else:
            st.caption("Needs nse_participant_oi and upstox_fii_activity -- not yet computed.")

        st.markdown("---")
        st.markdown("#### Multivariate outliers")
        st.caption(
            "IsolationForest across beta, momentum z-score, bulk-deal notional z-score, and "
            "promoter-holding change -- flags stocks extreme on SEVERAL of these at once, "
            "not just one. Deterministic (fixed random_state); weights/contamination are a "
            "hand-set Phase-1 starting point, not backtested."
        )
        if "derived_multivariate_outliers" in views:
            outliers = query(pit_latest(
                "derived_multivariate_outliers", ["symbol"],
                columns="symbol, beta, momentum_zscore, notional_zscore, "
                        "change_pct_points, anomaly_score_raw, is_outlier",
            ))
            flagged = outliers[outliers["is_outlier"]].sort_values("anomaly_score_raw")
            st.caption(f"{len(flagged)} of {len(outliers)} symbols flagged.")
            show(flagged.head(50))
        else:
            st.caption(HISTORY_NOTE)
    with sub_live:
        st.subheader("Live ticks")
        try:
            import redis
            r = redis.Redis(decode_responses=True)
            ltp = {k: json.loads(v) for k, v in r.hgetall("ltp").items()}
            if ltp:
                show(pd.DataFrame(ltp.values()))
            else:
                st.info("No live ticks. Start connectors/stream.py with broker credentials.")
        except Exception as e:
            st.warning(f"Redis unavailable ({e}). This tab needs Redis + stream.py running.")


# -------------------------------------------------------------------- Macro & Commodities
with tab_macro_group:
    sub_macro, sub_gold, sub_supply, sub_monsoon = st.tabs(
        ["RBI Macro", "Gold Complex", "Supply Chain", "Monsoon & ENSO"]
    )

    with sub_macro:
        st.subheader("RBI macro")
        macro_views = [v for v in views if v.startswith("rbi_")]
        if macro_views:
            pick = st.selectbox("Series", sorted(macro_views))
            show(query(f"SELECT * FROM {pick} LIMIT 500"))
        else:
            st.info("Run the RBI connectors first.")

    with sub_gold:
        st.subheader("Gold: four independent sources, and the spread between them")
        st.markdown(
            "COMEX settlement (global benchmark) · MCX (India futures) · "
            "IBJA (India physical) · RBI (the rupee leg).\n\n"
            "**The spread is the signal.** IBJA 999 minus landed COMEX cost is the "
            "India physical premium — it widens on import restrictions, festival "
            "demand and smuggling-channel shifts, and is sold nowhere because it "
            "requires owning all four legs."
        )

        if "derived_india_gold_premium" in views:
            prem = _gold_premium_history()
            if not prem.empty:
                latest = prem.iloc[-1]
                a, b, c, d = st.columns(4)
                a.metric("IBJA 999 (₹/10g)", f"{latest['ibja_999']:,.0f}")
                b.metric("Landed cost (₹/10g)", f"{latest['landed_inr_per_10g']:,.0f}")
                c.metric("Premium (₹/10g)", f"{latest['premium_inr_per_10g']:,.0f}")
                d.metric("Premium %", f"{latest['premium_pct']:.2f}%")
                st.caption(
                    f"Duty {latest['import_duty_pct']}% + GST {latest['gst_pct']}% — "
                    "a POLICY assumption, not market data. The premium's *level* moves "
                    "with it; its *changes* do not. Verify against the current CBIC "
                    "notification before trusting a level."
                )
                if len(prem) > 1:
                    st.line_chart(prem.set_index("rate_date")["premium_pct"])
                show(prem)
        else:
            st.info("Run the commodity jobs to compute the premium.")

        cols = st.columns(3)
        for col, (name, sql) in zip(cols, [
            ("COMEX front month", """SELECT month, settle, volume, openInterest
                                     FROM cme_settlements
                                     WHERE product='gold' AND NOT is_total
                                     ORDER BY contract_order LIMIT 8"""),
            # Order by the PARSED expiry, not the string: "04DEC2026" sorts before
            # "05OCT2026" alphabetically, which puts the liquid front month last.
            ("MCX gold futures", """SELECT Symbol, ExpiryDate, Close, Volume, OpenInterest
                                    FROM mcx_bhavcopy
                                    WHERE Symbol='GOLD' AND InstrumentName='FUTCOM'
                                    ORDER BY strptime(ExpiryDate, '%d%b%Y') LIMIT 8"""),
            ("IBJA physical", """SELECT rate_date, session, metal_purity, rate_inr_per_10g
                                 FROM ibja_rates WHERE metal='gold'
                                 ORDER BY rate_date DESC, session LIMIT 10"""),
        ]):
            with col:
                st.markdown(f"**{name}**")
                try:
                    show(query(sql))
                except Exception as e:
                    st.caption(f"not ingested ({str(e)[:60]})")

    # ------------------------------------------------------------ Supply chain
    with sub_supply:
        st.subheader("Maritime chokepoints: is traffic rerouting, or disappearing?")
        st.caption(
            "IMF PortWatch daily transits, derived from AIS. **This feed lags "
            "3-9 days** — it is a structural-trend input, never an execution "
            "signal, so every panel below is stamped with its own as-of date."
        )
        st.markdown(
            "The z-score alone cannot tell a **reroute** from a **supply loss**, "
            "and they imply opposite trades: rerouted cargo is a tonne-mile and "
            "freight-rate story (bullish shipping, a cost story for refiners), "
            "destroyed cargo is a crude-availability story. The mass balance "
            "below separates them by asking whether the substitute route "
            "absorbed the traffic."
        )
        try:
            balance = query(pit(
                "analytics_reroute_balance",
                columns="corridor, substitute, corridor_change_per_day, "
                        "substitute_change_per_day, absorbed_pct, interpretation, as_of",
                order_by="corridor_change_per_day",
            ))
            if balance.empty:
                st.info("Run `python run_daily.py --job analytics_supply_chain`.")
            else:
                st.caption(f"As of {balance['as_of'].iloc[0]} (PortWatch's latest published day)")
                show(balance)
                st.markdown(
                    "`no_substitute_route` on Hormuz is a **fact about geography, "
                    "not missing data** — there is no alternative sea route out of "
                    "the Persian Gulf. That absence is precisely why the 2026 "
                    "closure reads as a supply event while the 2024 Red Sea crisis "
                    "read as a routing one."
                )
        except Exception as e:
            st.caption(f"not ingested ({str(e)[:80]})")

        st.markdown("##### Regimes — where each route stands, and when it moved")
        st.caption(
            "Two different questions, deliberately not collapsed. **z vs regime** "
            "asks whether today is unusual *for the current regime*; **vs baseline** "
            "asks how far the route has moved from the pre-break world. A closed "
            "strait is perfectly normal for itself — Hormuz reads ~0 on the first "
            "and about −93% on the second, and only the second describes the "
            "disruption."
        )
        try:
            reg = query(pit(
                "analytics_current_regime",
                columns="portname, regime_start, days_in_regime, regime_mean, "
                        "recent_mean, z_vs_regime, previous_regime_mean, "
                        "regime_vs_previous_pct, baseline_mean, vs_baseline_pct, as_of",
                order_by="abs(coalesce(vs_baseline_pct, 0)) DESC",
            ))
            if reg.empty:
                st.info("Run `python run_daily.py --job analytics_supply_chain`.")
            else:
                show(reg)
                st.caption(
                    "Seasonality is removed with a two-harmonic annual fit before "
                    "breaks are detected, which handles a smooth cycle well and an "
                    "ice-bound route badly — treat **Bering Strait** and other "
                    "on/off seasonal routes here as unreliable."
                )
        except Exception as e:
            st.caption(f"not ingested ({str(e)[:80]})")

        st.markdown("##### Dated structural breaks")
        st.caption(
            "Detected from the data alone, with no event dates hardcoded. The "
            "detector independently dates the Hormuz collapse to within days of "
            "the 2026-02-28 escalation, and finds the 2024 Red Sea crisis at "
            "Bab el-Mandeb and Suez alongside the *mirror-image positive* break "
            "at the Cape of Good Hope — the reroute, rediscovered rather than "
            "assumed."
        )
        try:
            brk = query(pit(
                "analytics_regime_breaks",
                columns="portname, break_date, mean_before, mean_after, "
                        "change_per_day, pct_change, t_stat, days_after",
                order_by="abs(change_per_day) DESC", limit=15,
            ))
            if brk.empty:
                st.info("Run `python run_daily.py --job analytics_supply_chain`.")
            else:
                show(brk)
        except Exception as e:
            st.caption(f"not ingested ({str(e)[:80]})")

        st.markdown("##### Transit history")
        try:
            names = query("""SELECT DISTINCT portname FROM portwatch_chokepoints
                             ORDER BY portname""")["portname"].tolist()
            default = [n for n in ("Strait of Hormuz", "Suez Canal",
                                   "Bab el-Mandeb Strait", "Cape of Good Hope")
                       if n in names]
            picked = st.multiselect("Chokepoints", names, default=default)
            if picked:
                quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in picked)
                hist = query(f"""
                    SELECT date, portname, any_value(n_total) AS vessels_per_day,
                           any_value(n_tanker) AS tankers_per_day
                    FROM portwatch_chokepoints
                    WHERE portname IN ({quoted})
                    GROUP BY date, portname ORDER BY date
                """)
                hist["date"] = pd.to_datetime(hist["date"])
                # graph_objects, not plotly.express -- px is not imported in
                # this module and this is the idiom every other chart here uses.
                fig = go.Figure()
                for name, grp in hist.groupby("portname"):
                    fig.add_trace(go.Scatter(
                        x=grp["date"], y=grp["vessels_per_day"],
                        mode="lines", name=name,
                    ))
                fig.update_layout(
                    height=380, margin=dict(l=0, r=0, t=10, b=0),
                    yaxis_title="vessels/day",
                    legend=dict(orientation="h", y=-0.2),
                )
                st.plotly_chart(fig, width="stretch")
        except Exception as e:
            st.caption(f"not ingested ({str(e)[:80]})")

    # ---------------------------------------------------------------- Monsoon
    with sub_monsoon:
        st.subheader("Monsoon and ENSO: the prior, and the observation")
        st.caption(
            "ENSO is a **prior** on the season, available months ahead. Rainfall "
            "is the **observation** that updates it. They are kept apart on "
            "purpose — collapsing them into one number would hide which half is "
            "doing the work."
        )
        st.markdown("##### Departure from the IMD normal")
        st.caption(
            "The normal is **IMD's own**, 1991-2020, truncated to the same "
            "day-of-year as the observation. IMD publishes annual files in "
            "arrears, so the current season is observed by CPC and divided by "
            "the measured CPC/IMD ratio to put it on IMD's basis — the `basis` "
            "column says which happened on every row."
        )
        try:
            dep = query(pit(
                "analytics_monsoon_departure",
                columns="region, observed_mm, normal_mm, departure_pct, sigma, "
                        "basis, cpc_imd_ratio, days_observed, as_of",
                order_by="departure_pct",
            ))
            if dep.empty:
                st.info(
                    "Needs the IMD climatology. Backfill: "
                    "`connectors.imd.imd_gridded_rainfall_range(1991, 2020)`, "
                    "then `python run_daily.py --job analytics_monsoon`."
                )
            else:
                show(dep)
                if (dep["basis"] == "cpc_uncorrected").any():
                    st.warning(
                        "Some rows are `cpc_uncorrected` — the CPC/IMD bias could "
                        "not be measured, so **no departure is reported for them**. "
                        "A CPC total against an IMD normal is two different "
                        "instruments; backfill overlapping CPC and IMD seasons to "
                        "enable the correction rather than reading across them."
                    )
        except Exception as e:
            st.caption(f"not ingested ({str(e)[:80]})")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**ENSO state** (NOAA ONI)")
            try:
                enso = query("""
                    SELECT period, anomaly_c, phase, trajectory,
                           anomaly_change_6_seasons, is_leading_monsoon_season
                    FROM analytics_enso_state
                    ORDER BY knowledge_date DESC LIMIT 1
                """)
                if enso.empty:
                    st.info("Run `--job noaa_oni` then `--job analytics_monsoon`.")
                else:
                    row = enso.iloc[0]
                    st.metric(f"ONI anomaly ({row['period']})", f"{row['anomaly_c']:+.2f} °C",
                              f"{row['anomaly_change_6_seasons']:+.2f} over 6 seasons")
                    st.write(f"Phase: **{row['phase']}** · {row['trajectory']}")
                    st.caption(
                        "El Nino skews Indian monsoons dry, but the relationship is "
                        "probabilistic with real exceptions. This is a prior, not a "
                        "forecast."
                    )
            except Exception as e:
                st.caption(f"not ingested ({str(e)[:80]})")
        with col_b:
            st.markdown("**Season to date** (NOAA CPC gauge analysis)")
            try:
                prog = query(pit(
                    "analytics_monsoon_progress",
                    columns="region, cumulative_mm, mean_mm_per_day, "
                            "days_observed, season_days_elapsed, coverage_pct, as_of",
                    order_by="region",
                ))
                if prog.empty:
                    st.info("Run `python run_daily.py --job analytics_monsoon`.")
                else:
                    show(prog)
                    st.caption(
                        "`coverage_pct` travels with the total on purpose: a "
                        "cumulative built from 12 of 90 elapsed days is not a small "
                        "version of the season, it is a different quantity."
                    )
            except Exception as e:
                st.caption(f"not ingested ({str(e)[:80]})")

        st.caption(
            "**Not IMD.** CPC is an independent gauge analysis standing in while "
            "IMD API approval is pending. When IMD lands, keep both and reconcile "
            "— their normals differ, so swapping one for the other silently "
            "changes what 'departure from normal' means."
        )


# -------------------------------------------------------------------- Credit & Fundamentals
with tab_credit_group:
    sub_fund, sub_credit = st.tabs(["Fundamentals", "Credit Ratings"])

    with sub_fund:
        st.subheader("India equities: income statement, balance sheet, cash flow")
        st.caption(
            "One reconciled view, regardless of source. Base layer is each "
            "company's own XBRL filing to NSE (richest detail, but stuck at "
            "Q3 FY24-25 -- see ROADMAP.md's Phase 4b). Click **Refresh from "
            "screener.in** to extend every table with whatever more current "
            "quarters/years it has -- matched to the same line items, XBRL kept "
            "wherever both sources cover a period. screener.in use is a "
            "deliberate, scoped exception to CLAUDE.md's no-third-party-vendors "
            "rule (decided 2026-08-30); needs SCREENER_CSRFTOKEN/SCREENER_SESSIONID "
            "in .env (see .env.example). Per-source raw fetches (including BSE's "
            "standalone-only summary) are still available under **Advanced** "
            "below -- nothing was removed, just tucked out of the way."
        )
        if "nse_xbrl_facts" not in views:
            st.info("No fundamentals data yet -- run the Nifty 50 backfill, or "
                    "`python run_daily.py --job nse_fundamentals`.")
        else:
            @st.cache_data(ttl=300)
            def _cached_fundamentals_facts():
                return query("SELECT * FROM nse_xbrl_facts")

            facts = _cached_fundamentals_facts()
            symbols = sorted(facts["symbol"].dropna().unique())

            c1, c2 = st.columns([2, 1])
            with c1:
                def _push_focus_symbol():
                    st.session_state[SYMBOL_STATE_KEY] = st.session_state["fundamentals_symbol_pick"]

                _focus = st.session_state.get(SYMBOL_STATE_KEY)
                _default_idx = symbols.index(_focus) if _focus in symbols else 0
                symbol = st.selectbox("Company", symbols, index=_default_idx,
                                      key="fundamentals_symbol_pick", on_change=_push_focus_symbol)
            company_facts = facts[facts["symbol"] == symbol]
            with c2:
                basis_options = sorted(company_facts["consolidated"].dropna().unique())
                basis = st.radio("Basis", basis_options, horizontal=True) if basis_options else None

            shown = company_facts[company_facts["consolidated"] == basis] if basis else company_facts

            screener_key = f"screener_data_{symbol}_{basis}"
            if st.button(f"Refresh from screener.in ({symbol}, {basis or 'all'})", key=f"fetch_screener_{symbol}_{basis}"):
                from connectors.screener import financial_facts as screener_facts
                try:
                    st.session_state[screener_key] = screener_facts(symbol, consolidated=(basis != "Non-Consolidated"))
                except Exception as e:
                    st.caption(f"fetch failed: {str(e)[:200]}")
            screener_long = st.session_state.get(screener_key)
            if screener_long is None:
                st.caption("Showing XBRL only -- click above to extend with screener.in's current quarters.")

            if shown.empty:
                st.caption("no rows for this selection")
            else:
                # The XBRL/screener merge, the fiscal-year labelling and the
                # balance-check row all live in api.serializers.fundamentals so
                # that the React terminal's endpoint assembles these statements
                # from the same code rather than reimplementing the concept
                # mappings in TypeScript.
                income_raw = income_statement(shown, screener_long)
                balance_raw = balance_sheet(shown, screener_long)
                cash_raw = cash_flow(shown, screener_long)

                # Trend chart: oldest-to-newest (a table reads best most-recent-
                # first; a trend chart reads best left-to-right in time order) --
                # built off the SAME merged data as the table below it, so it
                # picks up screener's current quarters once fetched, same as
                # everything else on this tab.
                if not income_raw.empty and "Revenue" in income_raw.index:
                    chart_cols = list(reversed(income_raw.columns))
                    fig = go.Figure()
                    fig.add_bar(x=chart_cols, y=income_raw.loc["Revenue", chart_cols],
                                name="Revenue (Rs Cr)", marker_color="#4a7fd6")
                    if "Net Profit" in income_raw.index:
                        fig.add_scatter(x=chart_cols, y=income_raw.loc["Net Profit", chart_cols],
                                        name="Net Profit (Rs Cr)", mode="lines+markers",
                                        marker_color="#e34948", yaxis="y")
                    fig.update_layout(height=320, margin=dict(t=20, b=20, l=20, r=20),
                                      legend=dict(orientation="h", y=1.1))
                    st.plotly_chart(fig, width="stretch")

                # NOT `show(x) if cond else st.caption(...)` as a bare expression:
                # Streamlit's "magic" auto-display re-renders any bare non-None
                # expression result, and st.caption() returns a DeltaGenerator
                # (not None) -- the ternary's value leaked through as a literal
                # "None" line under the page on the empty-table branch. A real
                # if/else statement discards the branch's return value instead.
                st.markdown("##### Quarterly results")
                if income_raw.empty:
                    st.caption("no quarterly P&L rows found")
                else:
                    show(label_with_units(income_raw, INCOME_STATEMENT_PER_SHARE))

                st.markdown("##### Balance sheet")
                st.caption("Annual snapshots. \"Balance check\" should read ~0 -- Assets and "
                           "Equity+Liabilities are two views of the same total and must match.")
                if balance_raw.empty:
                    st.caption("no balance-sheet rows found")
                else:
                    show(label_with_units(balance_raw))

                st.markdown("##### Cash flow")
                if cash_raw.empty:
                    st.caption("no cash-flow rows found")
                else:
                    show(label_with_units(cash_raw))

            with st.expander("Advanced: raw per-source fetches (unmerged)"):
                st.caption(
                    "The individual sources behind the reconciled view above, "
                    "each in its own native shape -- useful for auditing a "
                    "number back to where it came from."
                )

                st.markdown("###### screener.in (raw, unmerged)")
                if screener_long is None:
                    st.caption("Not fetched yet -- use the Refresh button above.")
                else:
                    for stmt, period_type, title in [
                        ("income_statement", "quarterly", "Quarterly P&L"),
                        ("balance_sheet", "annual", "Annual balance sheet"),
                        ("cash_flow", "annual", "Annual cash flow"),
                    ]:
                        sub = screener_long[(screener_long["statement"] == stmt)
                                            & (screener_long["period_type"] == period_type)]
                        if sub.empty:
                            continue
                        st.markdown(f"**{title}**")
                        cols = sorted(sub["period_end"].unique(), reverse=True)
                        wide = sub.pivot(index="concept", columns="period_end", values="value")[cols]
                        show(wide)

                st.markdown("###### BSE quarterly-results summary (live, standalone only)")
                st.caption(
                    "On-demand fetch straight from BSE's own results widget. "
                    "**Appears to be STANDALONE, not consolidated** -- verified "
                    "against RELIANCE's own XBRL data (this endpoint's figure "
                    "sits in its standalone range) and against screener.in's "
                    "consolidated number for the same quarter, which doesn't "
                    "match. Not cached: click to fetch."
                )

                @st.cache_data(ttl=300)
                def _cached_scrip_master():
                    return query('SELECT "SCRIP_CD", "ISIN_NUMBER" FROM bse_scrip_master') \
                        if "bse_scrip_master" in views else pd.DataFrame()

                isin_vals = company_facts["isin"].dropna()
                isin = isin_vals.iloc[0] if not isin_vals.empty else None
                scrip_master = _cached_scrip_master()
                scripcode = None
                if isin is not None and not scrip_master.empty:
                    match = scrip_master[scrip_master["ISIN_NUMBER"] == isin]
                    if not match.empty:
                        scripcode = str(match["SCRIP_CD"].iloc[0])

                if scripcode is None:
                    st.caption("No BSE scrip code found for this company's ISIN.")
                elif st.button(f"Fetch latest from BSE (scrip {scripcode})", key=f"fetch_bse_{symbol}"):
                    from connectors.nse_bse import bse_quarterly_results_summary
                    try:
                        bse_summary = bse_quarterly_results_summary(scripcode)
                    except Exception as e:
                        st.caption(f"fetch failed: {str(e)[:200]}")
                    else:
                        col_order = bse_summary["period"].drop_duplicates().tolist()
                        wide = bse_summary.pivot(index="concept", columns="period", values="value")[col_order]
                        show(wide)

    with sub_credit:
        st.subheader("Credit-rating agency feeds (CRISIL / ICRA / CARE)")
        st.caption(
            "connectors/mca_charges.py names these as the free substitute for the "
            "CAPTCHA-gated MCA charge register -- a rating action is often more "
            "current than a quarterly filing, and covers unlisted subsidiaries a "
            "listed parent's consolidated accounts can hide behind. CRISIL and "
            "ICRA run daily via run_daily.py, market-wide, no lookup needed. CARE "
            "has no verified market-wide feed (see connectors/credit_ratings.py's "
            "CARE contract note) and is company-lookup only."
        )

        # POINT-IN-TIME. write_table() never overwrites -- rating_actions() has
        # been rerun many times against the same trailing window, so a plain
        # SELECT double-counts every row still inside that window across
        # vintages, AND can surface a STALE classification from before a fix
        # (e.g. the "issuer non-cooperating" pattern fix) alongside the
        # corrected one for the same action. pit() collapses to the latest
        # vintage per declared natural key (pr_id / rationale_id, in
        # core.quality.DATASET_KEYS) and honours the header's knowledge date --
        # so this panel can also show what a rating looked like BEFORE a
        # correction, which is the version a backtest should have seen.
        listed_names = _cached_listed_names()
        from analytics.listing_status import tag_listing_status

        for agency, view in [("CRISIL", "crisil_rating_actions"),
                             ("ICRA", "icra_rating_actions")]:
            st.markdown(f"##### {agency}")
            if view not in views:
                st.info(f"No {agency} data yet -- run `python run_daily.py --job "
                        f"{agency.lower()}_rating_actions`.")
                continue
            try:
                df = query(pit(view, order_by="rating_date DESC", limit=2000))
            except Exception as e:
                st.caption(f"query failed: {str(e)[:150]}")
                continue
            if df.empty:
                st.caption("no rows yet")
                continue
            df = tag_listing_status(df, "company_name", listed_names)

            counts = df["action_type"].value_counts()
            cols = st.columns(len(counts))
            for col, (action, n) in zip(cols, counts.items()):
                col.metric(action, int(n))

            c1, c2 = st.columns([1, 2])
            with c1:
                action_pick = st.selectbox("action_type", ["(all)"] + sorted(df["action_type"].unique()),
                                           key=f"{agency}_action_filter")
            with c2:
                # Best-effort default from the global focus symbol -- only takes
                # effect the first time this widget renders in a session; once a
                # user types their own filter, their edit persists (key-based
                # session state) regardless of what the focus symbol is.
                _default_name = _resolve_company_name(st.session_state.get(SYMBOL_STATE_KEY)) or ""
                name_filter = st.text_input("company name contains", value=_default_name,
                                            key=f"{agency}_name_filter")
            shown = df if action_pick == "(all)" else df[df["action_type"] == action_pick]
            if name_filter:
                shown = shown[shown["company_name"].str.contains(name_filter, case=False, na=False)]

            # Separated, not just filterable -- most of what CRISIL/ICRA rate is
            # privately held or an unlisted subsidiary (see analytics/
            # listing_status.py's verified ~10% listed rate), and mixing that
            # into the same table as the tradeable slice buries the signal a
            # fund can actually act on directly.
            display_cols = ["company_name", "rating_date", "action_type", "heading", "document_url"]
            public_df = shown[shown["listing_status"] == "Public (listed)"]
            private_df = shown[shown["listing_status"] == "Private/unlisted"]

            st.markdown(f"**Public — listed on NSE/BSE** ({len(public_df)})")
            if public_df.empty:
                st.caption("none in the current filter")
            else:
                show(public_df[display_cols].head(200))

            st.markdown(f"**Private / unlisted** ({len(private_df)})")
            if private_df.empty:
                st.caption("none in the current filter")
            else:
                show(private_df[display_cols].head(200))

        st.markdown("---")
        st.markdown("##### ICRA lender-wise bank facilities")
        st.caption(
            "The one structured leverage table this repo has found -- lender, "
            "facility type, amount (Rs Crore) per company, straight from ICRA's "
            "own HTML table (no prose parsing). Per-company, not a daily feed, so "
            "a name only appears below once someone has looked it up. Fetch a new "
            "one from a terminal or notebook (company_id/company_name for any "
            "ICRA-covered name are in the ICRA table above):"
        )
        st.code(
            "from connectors.credit_ratings import icra_bank_facilities\n"
            "icra_bank_facilities('<company_id>', '<company_name>')",
            language="python",
        )
        if "icra_bank_facilities" in views:
            try:
                fac_df = query("SELECT * FROM icra_bank_facilities ORDER BY company_name")
            except Exception as e:
                fac_df = pd.DataFrame()
                st.caption(f"query failed: {str(e)[:150]}")
            if not fac_df.empty:
                companies = sorted(fac_df["company_name"].dropna().unique())
                pick = st.selectbox("Company", companies)
                show(fac_df[fac_df["company_name"] == pick])
        else:
            st.info("No lender-facility lookups yet -- none have been fetched (see snippet above).")

        st.markdown("---")
        st.markdown("##### CARE / CareEdge (company lookup only)")
        st.caption(
            "No verified market-wide feed exists for CARE -- nothing here runs "
            "automatically. Fetch a company from a terminal or notebook:"
        )
        st.code(
            "from connectors.credit_ratings import care_search_by_company\n"
            "care_search_by_company('Reliance Industries Limited')",
            language="python",
        )
        if "care_rating_actions" in views:
            try:
                care_df = query(pit("care_rating_actions",
                                    order_by="published_date DESC", limit=200))
            except Exception as e:
                care_df = pd.DataFrame()
                st.caption(f"query failed: {str(e)[:150]}")
            if not care_df.empty:
                # A substring search ("Tata") can return several group entities
                # at once, not just one -- same public/private split as CRISIL/ICRA.
                care_df = tag_listing_status(care_df, "company_name", listed_names)
                care_public = care_df[care_df["listing_status"] == "Public (listed)"]
                care_private = care_df[care_df["listing_status"] == "Private/unlisted"]
                st.markdown(f"**Public — listed on NSE/BSE** ({len(care_public)})")
                show(care_public) if not care_public.empty else st.caption("none")
                st.markdown(f"**Private / unlisted** ({len(care_private)})")
                show(care_private) if not care_private.empty else st.caption("none")
        else:
            st.info("No CARE lookups yet.")


# ------------------------------------------------------------------ Flows
with tab_flows:
    st.subheader("Institutional positioning")
    st.caption(
        "India publishes participant-wise open interest daily and free — no "
        "other major market does. The US analogue is 13F, on a 45-day lag."
    )
    for view, label, order in [
        ("nse_participant_oi", "NSE participant-wise OI (FII/DII/pro/retail)", "trade_date DESC"),
        ("nse_fii_dii", "FII/DII cash flows", "knowledge_date DESC"),
        ("sec_13f_holdings", "US 13F holdings (top positions)", "value_usd DESC"),
    ]:
        st.markdown(f"**{label}**")
        if view in views:
            try:
                show(query(f"SELECT * FROM {view} ORDER BY {order} LIMIT 20"))
            except Exception as e:
                st.caption(str(e)[:120])
        else:
            st.caption("not yet ingested")


# -------------------------------------------------------------------- Signals
with tab_signals:
    sub_filings, sub_yt = st.tabs(["US Filings", "YT Signals"])

    with sub_filings:
        st.subheader("US filings: events, activists and insiders")
        picks = {
            "8-K red flags": """SELECT cik, item, item_description, report_date, filed
                                FROM sec_8k_items WHERE is_red_flag
                                ORDER BY filed DESC LIMIT 40""",
            "Insider cluster buys": """SELECT issuer_name, ticker, window_start, n_insiders,
                                              n_officers, n_directors, total_value_usd
                                       FROM sec_insider_cluster_buys
                                       ORDER BY total_value_usd DESC LIMIT 40""",
            "13G → 13D switches": """SELECT issuer_name, person_name_13d, first_13g,
                                            first_13d, days_passive_first
                                     FROM sec_13g_to_13d_transitions
                                     ORDER BY first_13d DESC LIMIT 40""",
            "Pay vs performance": """SELECT entity, fiscal_year, ceo_pay_disclosed,
                                            ceo_pay_actually_paid, pay_premium,
                                            company_tsr, peer_tsr, tsr_vs_peer
                                     FROM sec_pay_vs_tsr WHERE pay_up_performance_down
                                     ORDER BY ceo_pay_actually_paid DESC LIMIT 40""",
        }
        choice = st.radio("View", list(picks), horizontal=True)
        try:
            show(query(picks[choice]))
        except Exception as e:
            st.caption(f"not yet ingested ({str(e)[:80]})")

    with sub_yt:
        st.subheader("Signals extracted from tracked YouTube channels")
        st.caption(
            "Opinion, not data — every line here is something a podcast host said, "
            "not a fact. Separate pipeline (own SQLite db, not the parquet "
            "warehouse); see latest_information/yt-signal-engine/."
        )
        _YT_DATA = (
            Path(_REPO_ROOT) / "latest_information" / "yt-signal-engine"
            / "yt-signal-engine" / "data"
        )
        yt_desk, yt_roster = st.tabs(["Signal Desk", "Company Roster"])

        # The desk is organised by EPISODE, the roster by COMPANY. Same signals,
        # two cuts: "what was said this week" versus "everything ever said about
        # this name, and by whom".
        with yt_desk:
            yt_dashboard = _YT_DATA / "dashboard.html"
            if yt_dashboard.exists():
                st.iframe(yt_dashboard, height=1400)
            else:
                st.info(
                    "No dashboard built yet. Run `python run.py build` in "
                    "latest_information/yt-signal-engine/yt-signal-engine/."
                )

        with yt_roster:
            roster_json = _YT_DATA / "roster.json"
            if not roster_json.exists():
                st.info(
                    "No roster built yet. Run `python roster.py` in "
                    "latest_information/yt-signal-engine/yt-signal-engine/."
                )
            else:
                _stats, _roster = _cached_yt_roster(
                    str(roster_json), roster_json.stat().st_mtime
                )
                if _roster.empty:
                    st.info("Roster is empty — rebuild it with `python roster.py`.")
                else:
                    st.caption(
                        f"{len(_roster)} companies across {_stats.get('episodes', 0)} "
                        f"analyzed episodes ({_stats.get('signals', 0)} signals). "
                        "Mention counts come from the signal database; the context "
                        "line is model-written from those signals and attributed to "
                        "the speakers. Tickers are best-effort and blank where "
                        "uncertain — verify before trading."
                    )
                    r1, r2, r3, r4 = st.columns([2, 2, 1, 3])
                    with r1:
                        _themes = ["All"] + sorted(_roster["theme"].dropna().unique())
                        _theme = st.selectbox("Theme", _themes, key="yt_roster_theme")
                    with r2:
                        _chans = ["All"] + sorted(
                            {c for row in _roster["channels"] for c in row.split(", ") if c}
                        )
                        _chan = st.selectbox("Named by", _chans, key="yt_roster_chan")
                    with r3:
                        _listing = st.selectbox(
                            "Listing", ["All", "public", "private", "subsidiary", "unknown"],
                            key="yt_roster_listing",
                        )
                    with r4:
                        _rq = st.text_input(
                            "Search", key="yt_roster_q",
                            placeholder="company, ticker, claim…",
                        )

                    _view = _roster
                    if _theme != "All":
                        _view = _view[_view["theme"] == _theme]
                    if _chan != "All":
                        _view = _view[_view["channels"].str.contains(_chan, regex=False)]
                    if _listing != "All":
                        _view = _view[_view["listing"] == _listing]
                    if _rq.strip():
                        _needle = _rq.strip().lower()
                        _view = _view[
                            _view["company"].str.lower().str.contains(_needle, regex=False)
                            | _view["ticker"].str.lower().str.contains(_needle, regex=False)
                            | _view["context"].str.lower().str.contains(_needle, regex=False)
                            | _view["aliases"].str.lower().str.contains(_needle, regex=False)
                        ]

                    st.caption(f"{len(_view)} of {len(_roster)} companies shown")
                    show(
                        _view[["company", "ticker", "listing", "theme", "mentions",
                               "channels", "context"]]
                        .sort_values("mentions", ascending=False),
                        hide_index=True,
                    )


# -------------------------------------------------------------------- Data Ops
with tab_ops:
    sub_quality, sub_sql = st.tabs(["Data Quality", "SQL"])

    with sub_quality:
        st.subheader("Is the pipeline still telling the truth?")
        st.caption(
            "A scraped pipeline rarely dies loudly. It stops, or a source quietly "
            "rewrites history, and the output still looks like data. "
            "**STALE** = no bytes recently. **COLLAPSED** = this run wrote less than "
            "half the last. **REVISED** = a value we already recorded changed."
        )

        stale_after = st.slider("Consider a source stale after (hours)", 6, 168, 48, 6)
        report = _cached_quality_report(stale_after)

        if report.empty:
            st.info("Nothing in the warehouse yet. Run `python run_daily.py`.")
        else:
            counts = report["status"].value_counts().to_dict()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Datasets", len(report))
            c2.metric("Stale", counts.get("STALE", 0))
            c3.metric("Collapsed", counts.get("COLLAPSED", 0))
            c4.metric("Revised", counts.get("REVISED", 0))

            problems = report[report["status"] != "OK"]
            if problems.empty:
                st.success("No stale, collapsed or revised datasets.")
            else:
                st.warning(f"{len(problems)} dataset(s) need a look — shown first below.")

            show(report[["view", "status", "rows_latest", "vintages", "row_change",
                         "revisions", "age_hours", "freshness_basis", "revision_key"]])

            st.markdown("---")
            st.markdown("#### Row-count history")
            st.caption(
                "One point per run. `write_table()` never overwrites, so every run "
                "leaves its own parquet vintage — this is that history."
            )
            paths = view_paths()
            pick = st.selectbox("Dataset", sorted(paths), key="trend_pick")
            trend = row_count_trend(pick, paths)
            if trend.empty:
                st.caption("No vintages.")
            else:
                show(trend[["vintage", "written_utc", "rows", "row_change", "collapsed"]])
                if trend["rows"].notna().sum() > 1:
                    st.line_chart(trend.set_index("written_utc")["rows"])

            st.markdown("---")
            st.markdown("#### Silent revisions")
            st.caption(
                "Values that CHANGED between runs, compared on a declared key. "
                "A hit has two possible causes and the data cannot tell them apart: "
                "the **source** revised history, or **our parser** changed between "
                "the two vintages. Check whether the connector moved before treating "
                "it as the source rewriting itself."
            )
            revisable = [v for v in sorted(paths) if v in DATASET_KEYS]
            if not revisable:
                st.caption("No dataset has a declared key yet — see core.quality.DATASET_KEYS.")
            else:
                rpick = st.selectbox("Dataset", revisable, key="rev_pick")
                st.caption(f"Key: `{', '.join(DATASET_KEYS[rpick])}`")
                try:
                    revs = detect_revisions(rpick, paths=paths)
                except KeyError as e:
                    st.error(f"Declared key does not match the data: {e}")
                    revs = pd.DataFrame()
                if revs.empty:
                    st.info("No revisions detected (or only one vintage exists so far).")
                else:
                    st.warning(f"{len(revs)} key(s) changed between runs.")
                    show(revs)

            with st.expander("Raw archive freshness (per source/dataset)"):
                st.caption(
                    "Measured on the RAW archive, not parquet: parsing can succeed "
                    "against a stale file, so recent rows do not prove a recent fetch."
                )
                show(fetch_freshness())

    with sub_sql:
        st.subheader("Query anything")
        st.caption("Every dataset is a view. Join across sources freely.")
        q = st.text_area("SQL", "SELECT * FROM derived_india_gold_premium", height=120)
        if st.button("Run"):
            try:
                show(query(q))
            except Exception as e:
                st.error(e)
        with st.expander("Available views"):
            st.write(sorted(views))

