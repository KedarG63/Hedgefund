"""
The Redis connection contract.

This exists because of a bug that was invisible for as long as it existed:
three of four call sites built their client with a bare `redis.Redis()`, which
hardcodes localhost db 0 and ignores REDIS_URL entirely. The setting was
present, looked configured, and did nothing -- so the tick cache shared a
database with an unrelated project's BullMQ queue, where a FLUSHDB from either
side would have wiped the other's live state mid-session.

The tests below are about CONFIGURATION being honoured and keys being
namespaced. Neither needs a running Redis.
"""
from __future__ import annotations

import pytest

import core.redis_client as rc


def test_the_default_database_is_not_zero():
    """
    Anything shipping a db 0 default eventually shares a database with
    something else on a developer's machine. This one did.
    """
    assert rc.DEFAULT_URL.endswith("/1")


def test_redis_url_comes_from_configuration(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/7")
    monkeypatch.setattr(rc, "get", lambda k, d=None: "redis://example:6379/7")
    assert rc.redis_url() == "redis://example:6379/7"


def test_every_key_we_write_is_namespaced():
    """
    The second defence. Even pointed at a shared database, a qd: prefix means
    the two applications cannot collide -- the db index alone would be undone
    by one misedited .env.
    """
    assert rc.LTP_HASH.startswith(rc.PREFIX)
    assert rc.TICKS_CHANNEL.startswith(rc.PREFIX)
    assert rc.PREFIX and rc.PREFIX.endswith(":")


def test_no_module_builds_its_own_client_any_more():
    """
    A regression guard on the ACTUAL bug. `redis.Redis(...)` anywhere outside
    core/redis_client.py silently ignores REDIS_URL, and the failure is
    invisible: everything connects, just to the wrong database.
    """
    import ast
    import pathlib

    def attr_path(node) -> str:
        """Dotted name for an attribute chain, e.g. redis.Redis.from_url."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in (list((repo / "connectors").glob("*.py"))
                 + list((repo / "core").glob("*.py"))
                 + list((repo / "api").rglob("*.py"))
                 + [repo / "dashboard" / "app.py"]):
        if path.name == "redis_client.py":
            continue
        # Parsed, not grepped: the previous text scan flagged the comments
        # explaining WHY not to do this, which is the wrong lesson to teach.
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = attr_path(node.func)
            if name.startswith("redis.Redis"):
                offenders.append(f"{path.relative_to(repo)}:{node.lineno} {name}()")

    assert not offenders, (
        "build clients through core.redis_client.redis_client() so REDIS_URL is "
        f"honoured: {offenders}"
    )


def test_no_module_hardcodes_the_unprefixed_key_names():
    """The publishers and the reader must agree, and they only do if both take
    the names from one place."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in [repo / "connectors" / "stream.py", repo / "connectors" / "upstox.py",
                 repo / "dashboard" / "app.py", repo / "api" / "ws.py"]:
        text = path.read_text(encoding="utf-8")
        for bad in ('hset("ltp"', "hset('ltp'", 'publish("ticks"', "publish('ticks'",
                    'hgetall("ltp"', "hgetall('ltp'"):
            if bad in text:
                offenders.append(f"{path.relative_to(repo)}: {bad}")
    assert not offenders, f"use LTP_HASH / TICKS_CHANNEL: {offenders}"


def test_an_unreachable_redis_returns_none_rather_than_raising(monkeypatch):
    """
    The feed only runs during market hours and the broker token expires 03:30
    IST daily, so "no Redis" is the ordinary overnight state, not an error.
    """
    monkeypatch.setattr(rc, "redis_url", lambda: "redis://127.0.0.1:6399/1")
    assert rc.redis_client(connect_timeout=0.2) is None


def test_required_true_raises_with_the_url_in_the_message(monkeypatch):
    """A job whose whole purpose is the stream should fail loudly rather than
    run on silently throwing ticks away -- and the message has to say WHERE it
    tried, since the whole bug was connecting to the wrong place."""
    monkeypatch.setattr(rc, "redis_url", lambda: "redis://127.0.0.1:6399/1")
    with pytest.raises(RuntimeError, match="6399"):
        rc.redis_client(required=True, connect_timeout=0.2)


def test_namespace_report_degrades_when_redis_is_absent(monkeypatch):
    monkeypatch.setattr(rc, "redis_client", lambda **kw: None)
    report = rc.namespace_report()
    assert report["reachable"] is False and report["foreign_keys"] == 0
