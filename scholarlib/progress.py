"""Heartbeats for the long serial loops.

Stage-boundary logging says a stage started and, eventually, that it finished.
It does not say a stage is still alive twenty minutes in, so a stall and slow
progress look identical from outside. These emit a periodic counter with a rate
and an ETA while the loop runs.

Emission is time-based rather than every-N-items on purpose: per-item cost
varies by two orders of magnitude across these stages (a cached DOI lookup
against a cold three-source fallback chain), so a fixed item stride either
floods or goes silent depending on the corpus.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")

DEFAULT_INTERVAL = 5.0
DEFAULT_MAX_INTERVAL = 60.0
DEFAULT_MIN_UNITS = 200


def _fmt_duration(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    if seconds < 10:
        # Flooring to int here reads as "done in 0s" on a fast loop.
        return f"{seconds:.1f}s"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


class Heartbeat:
    """Periodic progress for a loop whose unit of work is not an iteration.

    Used directly where the loop counts comparisons rather than items; the
    `heartbeat()` wrapper below covers the plain per-item case.
    """

    def __init__(self, label: str, *, logger: logging.Logger,
                 total: Optional[int] = None,
                 interval: float = DEFAULT_INTERVAL,
                 max_interval: float = DEFAULT_MAX_INTERVAL,
                 min_units: int = DEFAULT_MIN_UNITS) -> None:
        self.label = label
        self.logger = logger
        self.total = total
        # The interval backs off as the loop proves it is alive. The first
        # updates answer "did this hang?" and want to be prompt; by minute
        # twenty the question is only "is it still going?", and a line every
        # five seconds there is 240 lines of noise.
        self.interval = interval
        self.max_interval = max_interval
        # Below this, the loop finishes before anyone would want an update.
        self.enabled = total is None or total >= min_units
        self.count = 0
        self._start = time.monotonic()
        self._last = self._start
        self._emitted = False

    def tick(self, n: int = 1) -> None:
        self.count += n
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last < self.interval:
            return
        self._last = now
        self.interval = min(self.interval * 2, self.max_interval)
        self.logger.info("  %s", self._line(now))
        self._emitted = True

    def done(self) -> None:
        """Log a final line only if intermediate ones were emitted.

        Quiet loops stay quiet; the caller's own summary line already covers
        them. A loop that did report should report its total, so the last
        heartbeat is not mistaken for where it stopped.
        """
        if not self._emitted:
            return
        self.logger.info("  %s", self._line(time.monotonic(), final=True))

    def _line(self, now: float, *, final: bool = False) -> str:
        elapsed = now - self._start
        rate = self.count / elapsed if elapsed > 0 else 0.0
        parts = [f"{self.label}:"]
        if self.total:
            pct = 100.0 * self.count / self.total
            parts.append(f"{self.count}/{self.total} ({pct:.0f}%)")
        else:
            parts.append(str(self.count))
        parts.append(f"· {rate:,.0f}/s" if rate >= 1 else f"· {rate:.2f}/s")
        if final:
            parts.append(f"· done in {_fmt_duration(elapsed)}")
        elif self.total and rate > 0:
            parts.append(f"· eta {_fmt_duration((self.total - self.count) / rate)}")
        return " ".join(parts)


def heartbeat(items: Iterable[T], label: str, *, logger: logging.Logger,
              total: Optional[int] = None,
              interval: float = DEFAULT_INTERVAL,
              max_interval: float = DEFAULT_MAX_INTERVAL,
              min_units: int = DEFAULT_MIN_UNITS) -> Iterator[T]:
    """Wrap a loop so it reports progress while it runs.

    `total` is taken from the iterable when it has a length.
    """
    if total is None:
        try:
            total = len(items)  # type: ignore[arg-type]
        except TypeError:
            total = None
    hb = Heartbeat(label, logger=logger, total=total, interval=interval,
                   max_interval=max_interval, min_units=min_units)
    try:
        for item in items:
            yield item
            hb.tick()
    finally:
        # Runs on break and on exception too: where it stopped is the
        # interesting part of an interrupted run.
        hb.done()
