"""
Additive styling layer for the Streamlit dashboard -- CSS injection + small
render helpers. Does not touch any query/data logic; every existing
st.dataframe/st.metric call keeps working unchanged if this module is never
imported.

Palette adapted from a separate project's (Infra_as_a_service) dark
"Midnight Blueprint" theme -- a navy background with blue/green accents and
monospace numeric readouts, translated here into Streamlit's theme config
(.streamlit/config.toml) plus this file's injected CSS. Nothing else from
that project's code is reused; only the palette/feel.

BEARISH/NEUTRAL/BULLISH is the single shared definition of this dashboard's
diverging color pair, used by every correlation/composite-score heatmap and
treemap. Deliberately blue<->red, not red<->green: red-green is the most
common form of colorblindness, which would make the single most important
signal on several tabs unreadable for those users. Every cell/card that uses
these colors also carries an explicit arrow + signed number in TEXT -- never
color alone.
"""
import streamlit as st

BEARISH = "#e34948"
NEUTRAL = "#f0efec"
BULLISH = "#2a78d6"
ACCENT = "#4D9EFF"  # primaryColor from .streamlit/config.toml, echoed here for use in f-strings/HTML


def inject_css() -> None:
    """Call once, right after st.set_page_config()."""
    st.markdown(
        """<style>
        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', 'Consolas', monospace;
        }
        div[data-testid="stDataFrame"] {
            font-family: 'JetBrains Mono', 'Consolas', monospace;
        }
        .qd-card {
            background-color: rgba(77, 158, 255, 0.04);
            background-image: radial-gradient(rgba(77, 158, 255, 0.18) 1px, transparent 1px);
            background-size: 14px 14px;
            border: 1px solid rgba(77, 158, 255, 0.25);
            border-radius: 8px;
            padding: 12px 16px;
            transition: box-shadow 0.15s ease;
        }
        .qd-card:hover {
            box-shadow: 0 0 16px rgba(77, 158, 255, 0.35);
        }
        </style>""",
        unsafe_allow_html=True,
    )


def tone_color(tone: str) -> str:
    """bullish -> BULLISH, bearish -> BEARISH, anything else -> NEUTRAL."""
    return {"bullish": BULLISH, "bearish": BEARISH}.get(tone, NEUTRAL)


def metric_card(label: str, value: str, delta: str = "", tone: str = "neutral") -> None:
    """
    Bloomberg-style stat tile: st.metric alone can't do the hover-glow/
    dot-grid look, so this renders a styled div via markdown instead. Use
    where the extra visual weight is worth it (Command Center strips);
    ordinary st.metric is fine elsewhere and already inherits the monospace
    CSS from inject_css().
    """
    color = tone_color(tone)
    delta_html = f'<div style="font-size:0.8rem;color:{color}">{delta}</div>' if delta else ""
    st.markdown(
        f"""<div class="qd-card">
            <div style="font-size:0.8rem;opacity:0.7">{label}</div>
            <div style="font-size:1.4rem;font-family:'JetBrains Mono','Consolas',monospace;color:{color}">{value}</div>
            {delta_html}
        </div>""",
        unsafe_allow_html=True,
    )
