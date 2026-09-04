"""
Pure-function tests for dashboard/theme.py -- no Streamlit runtime needed.
metric_card()/inject_css() call st.markdown(), which is a documented no-op
outside a real script run (same reason tests/test_dashboard.py has to launch
app.py as a subprocess instead of importing it) -- so those two are only
smoke-tested for "does not raise"; tone_color() is the pure logic worth
pinning directly.
"""
from dashboard.theme import ACCENT, BEARISH, BULLISH, NEUTRAL, inject_css, metric_card, tone_color


def test_tone_colors_are_distinct():
    assert len({BEARISH, NEUTRAL, BULLISH, ACCENT}) == 4


def test_tone_color_maps_bullish_and_bearish():
    assert tone_color("bullish") == BULLISH
    assert tone_color("bearish") == BEARISH


def test_tone_color_falls_back_to_neutral_for_anything_else():
    assert tone_color("neutral") == NEUTRAL
    assert tone_color("unrecognized-tone") == NEUTRAL


def test_metric_card_does_not_raise_for_any_documented_tone():
    for tone in ("bullish", "bearish", "neutral"):
        metric_card("label", "value", delta="+1.2%", tone=tone)


def test_inject_css_does_not_raise():
    inject_css()
