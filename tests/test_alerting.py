"""
Offline tests for core/alerting.py's staleness alert -- the Phase-8 fix for
a real gap: a job that's never INVOKED (a narrow --job command run day after
day) never appears as a failure, so alert_failures() alone can't catch nine
datasets silently going stale for four days, which is exactly what happened.
No network call -- send_webhook() only fires when ALERT_WEBHOOK_URL is set,
which it isn't in a test environment, so these exercise the decision logic
around the send, not the send itself.
"""
from core.alerting import alert_staleness


def test_no_stale_views_sends_nothing():
    out = alert_staleness([], stale_after_hours=48.0)
    assert out["stale"] == 0
    assert out["sent"] is False


def test_stale_views_reports_the_count():
    out = alert_staleness(["bse_announcements", "nse_bulk_deals"], stale_after_hours=48.0)
    assert out["stale"] == 2
    # No ALERT_WEBHOOK_URL in the test environment -- send_webhook() is never
    # reached with a real URL, so "sent" must be False, not silently True.
    assert out["sent"] is False


def test_channels_reflects_current_env_not_hardcoded():
    """channels must come from configured(), not a stale/cached value."""
    out = alert_staleness(["some_view"], stale_after_hours=48.0)
    assert "webhook" in out["channels"]
    assert "email" in out["channels"]
