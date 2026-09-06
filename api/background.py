"""
Values too slow to compute on the request path.

quality_report() walks the whole raw archive -- 32k files, 4.7 GB, ~16 seconds
on this machine. That is fine for a once-a-day pipeline check and completely
unacceptable for a status-strip health dot, which the shell wants on a timer
while panels are painting. Blocking a request on it would also tie up a
threadpool worker for 16s, so a few concurrent polls could starve every other
endpoint.

So it is computed OFF the request path and served from a snapshot. The route
never waits: it returns whatever the last refresh produced, plus how old that
is, and lets the caller decide whether the age matters. A cold snapshot
reports `computing` rather than blocking or erroring -- "not measured yet" is
a real answer and the shell can render a grey dot for it.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable


class Snapshot:
    """
    A value refreshed on a timer, read without waiting.

    The compute runs in a worker thread (asyncio.to_thread), so a blocking
    filesystem walk never stalls the event loop. Failures are kept as the
    snapshot's error rather than raised: monitoring that goes silent when it
    breaks is the failure core.quality exists to prevent, so a broken refresher
    must be visible in the payload, not absent from it.
    """

    def __init__(self, compute: Callable[[], Any], ttl_seconds: float, name: str):
        self._compute = compute
        self._ttl = ttl_seconds
        self._name = name
        self._value: Any = None
        self._computed_at: float | None = None
        self._error: str | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------- reading

    @property
    def age_seconds(self) -> float | None:
        return None if self._computed_at is None else time.time() - self._computed_at

    def read(self) -> dict:
        """Never blocks, never raises."""
        age = self.age_seconds
        return {
            "value": self._value,
            "computed_at": self._computed_at,
            "age_seconds": None if age is None else round(age, 1),
            "computing": self._value is None and self._error is None,
            "stale": age is not None and age > self._ttl,
            "error": self._error,
        }

    # -------------------------------------------------------------- refresh

    async def refresh(self) -> None:
        """One refresh at a time -- the lock stops a slow compute from being
        started again by the next tick while it is still running."""
        async with self._lock:
            try:
                self._value = await asyncio.to_thread(self._compute)
                self._error = None
            except Exception as exc:                                  # noqa: BLE001
                self._error = f"{type(exc).__name__}: {exc}"
            finally:
                self._computed_at = time.time()

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self._ttl)

    def start(self) -> None:
        """Begin refreshing. The first pass runs immediately, so the snapshot
        is usually warm by the time anyone opens the terminal."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"snapshot:{self._name}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):               # noqa: BLE001
                pass
            self._task = None


# quality_report() is the only thing slow enough to need this today. 10 minutes
# because the underlying facts move at pipeline speed -- run_daily.py runs once
# a day, and nothing about freshness changes minute to minute.
QUALITY_TTL_SECONDS = 600

_quality: Snapshot | None = None


def quality_snapshot() -> Snapshot:
    global _quality
    if _quality is None:
        from core.quality import quality_report

        _quality = Snapshot(lambda: quality_report(stale_after_hours=48.0),
                            QUALITY_TTL_SECONDS, "quality")
    return _quality
