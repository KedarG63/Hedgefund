"""
Single-pane dashboard over everything.

    streamlit run dashboard/app.py

Reads:
  - DuckDB views over parquet  -> history (EOD, macro, fundamentals)
  - Redis last-value cache     -> live ticks, if stream.py is running

Why DuckDB + parquet and not Postgres: your data is append-only analytical
time series. DuckDB queries parquet files directly at ~Postgres-beating speed
with zero server to run. Move to ClickHouse only when you outgrow one machine.
"""
import json
import streamlit as st
import pandas as pd

from core.storage import db, register_views

st.set_page_config(page_title="Research Terminal", layout="wide")


@st.cache_resource
def _con():
    con = db()
    views = register_views(con)
    return con, views


con, views = _con()

st.title("Research Terminal")
st.caption(f"{len(views)} datasets registered")

tab_live, tab_gold, tab_flows, tab_macro, tab_sql = st.tabs(
    ["Live", "Gold complex", "Flows & positioning", "Macro", "SQL"]
)

# ------------------------------------------------------------------ Live
with tab_live:
    st.subheader("Live ticks")
    try:
        import redis
        r = redis.Redis(decode_responses=True)
        ltp = {k: json.loads(v) for k, v in r.hgetall("ltp").items()}
        if ltp:
            st.dataframe(pd.DataFrame(ltp.values()), width="stretch")
        else:
            st.info("No live ticks. Start connectors/stream.py with broker credentials.")
    except Exception as e:
        st.warning(f"Redis unavailable ({e}). Live tab needs Redis + stream.py running.")

# ------------------------------------------------------------------ Gold
with tab_gold:
    st.subheader("Gold: the four independent sources, side by side")
    st.markdown(
        "COMEX settlement (global benchmark) | MCX (India futures) | "
        "IBJA (India physical) | CBIC (government notified customs value)\n\n"
        "**The spreads between these are the signal.** IBJA vs (COMEX x USDINR "
        "+ duty) is the India physical premium/discount -- it widens on import "
        "restrictions, festival demand and smuggling-channel shifts."
    )
    cols = st.columns(4)
    for col, (name, view) in zip(cols, [
        ("COMEX", "cme_settlements"), ("MCX", "mcx_bhavcopy"),
        ("IBJA", "ibja_rates"), ("CBIC", "cbic_tariff"),
    ]):
        with col:
            st.metric(name, "--")
            if view in views:
                try:
                    df = con.execute(f"SELECT * FROM {view} ORDER BY knowledge_date DESC LIMIT 5").df()
                    st.dataframe(df, width="stretch")
                except Exception as e:
                    st.caption(f"{e}")
            else:
                st.caption("not yet ingested")

# ------------------------------------------------------------------ Flows
with tab_flows:
    st.subheader("FII / DII flows and derivatives positioning")
    for view, label in [("nse_fii_dii", "FII/DII cash"),
                        ("nse_participant_oi", "Participant-wise OI")]:
        st.markdown(f"**{label}**")
        if view in views:
            st.dataframe(con.execute(f"SELECT * FROM {view} ORDER BY knowledge_date DESC LIMIT 20").df(),
                         width="stretch")
        else:
            st.caption("not yet ingested")

# ------------------------------------------------------------------ Macro
with tab_macro:
    st.subheader("India high-frequency indicators")
    st.caption("e-way bills | power demand | fuel consumption | vehicle regs | UPI | tolls")
    macro_views = [v for v in views if v.startswith(("rbi_", "mospi_", "gstn_", "grid_"))]
    if macro_views:
        pick = st.selectbox("Series", macro_views)
        st.dataframe(con.execute(f"SELECT * FROM {pick} LIMIT 200").df(), width="stretch")
    else:
        st.info("Run the macro connectors first.")

# ------------------------------------------------------------------ SQL
with tab_sql:
    st.subheader("Query anything")
    st.caption("Every dataset is a view. Join across sources freely.")
    q = st.text_area("SQL", "SELECT * FROM nse_bhavcopy LIMIT 100", height=120)
    if st.button("Run"):
        try:
            st.dataframe(con.execute(q).df(), width="stretch")
        except Exception as e:
            st.error(e)
    with st.expander("Available views"):
        st.write(views)
