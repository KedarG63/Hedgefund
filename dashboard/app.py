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
import streamlit as st

from core.quality import (
    DATASET_KEYS,
    detect_revisions,
    fetch_freshness,
    quality_report,
    row_count_trend,
    view_paths,
)
from core.storage import db, register_views

st.set_page_config(page_title="Research Terminal", layout="wide")


@st.cache_resource
def _con():
    con = db()
    return con, register_views(con)


con, views = _con()


def show(df, **kw):
    st.dataframe(df, width="stretch", **kw)


def query(sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


st.title("Research Terminal")
st.caption(f"{len(views)} datasets registered")

tab_quality, tab_gold, tab_flows, tab_filings, tab_macro, tab_live, tab_sql = st.tabs(
    ["Data quality", "Gold complex", "Flows & positioning", "US filings",
     "Macro", "Live", "SQL"]
)

# ------------------------------------------------------------------ Quality
with tab_quality:
    st.subheader("Is the pipeline still telling the truth?")
    st.caption(
        "A scraped pipeline rarely dies loudly. It stops, or a source quietly "
        "rewrites history, and the output still looks like data. "
        "**STALE** = no bytes recently. **COLLAPSED** = this run wrote less than "
        "half the last. **REVISED** = a value we already recorded changed."
    )

    stale_after = st.slider("Consider a source stale after (hours)", 6, 168, 48, 6)
    report = quality_report(stale_after_hours=stale_after)

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

# ------------------------------------------------------------------ Gold
with tab_gold:
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
        prem = query("""
            SELECT rate_date, session, ibja_999, comex_usd_per_oz, usdinr,
                   landed_inr_per_10g, premium_inr_per_10g, premium_pct,
                   import_duty_pct, gst_pct
            FROM derived_india_gold_premium ORDER BY rate_date, session
        """)
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
        ("MCX gold futures", """SELECT Symbol, ExpiryDate, Close, Volume, OpenInterest
                                FROM mcx_bhavcopy
                                WHERE Symbol='GOLD' AND InstrumentName='FUTCOM'
                                ORDER BY ExpiryDate LIMIT 8"""),
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

# ------------------------------------------------------------------ Filings
with tab_filings:
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

# ------------------------------------------------------------------ Macro
with tab_macro:
    st.subheader("RBI macro")
    macro_views = [v for v in views if v.startswith("rbi_")]
    if macro_views:
        pick = st.selectbox("Series", sorted(macro_views))
        show(query(f"SELECT * FROM {pick} LIMIT 500"))
    else:
        st.info("Run the RBI connectors first.")

# ------------------------------------------------------------------ Live
with tab_live:
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

# ------------------------------------------------------------------ SQL
with tab_sql:
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
