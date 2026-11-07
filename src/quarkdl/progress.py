from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from .core import DownloadItem, human_bytes


def format_duration(seconds: float | None) -> str:
    """Compact duration such as ``5s``, ``4m12s`` or ``1h02m``; ``--`` when unknown."""
    if seconds is None or seconds != seconds or seconds == float("inf") or seconds < 0:
        return "--"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class RateMeter:
    """Rolling-window throughput meter (bytes/second)."""

    def __init__(self, window: float = 10.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._window = window
        self._start = clock()
        self._total = 0
        self._samples: deque[tuple[float, int]] = deque([(self._start, 0)])

    @property
    def total(self) -> int:
        return self._total

    def add(self, nbytes: int) -> None:
        if nbytes <= 0:
            return
        now = self._clock()
        self._total += nbytes
        self._samples.append((now, self._total))
        while len(self._samples) > 1 and self._samples[1][0] <= now - self._window:
            self._samples.popleft()

    def speed(self) -> float:
        """Bytes/second over the recent window; decays toward zero if transfers stall."""
        t0, b0 = self._samples[0]
        elapsed = self._clock() - t0
        if elapsed <= 0:
            return 0.0
        return (self._total - b0) / elapsed

    def average_speed(self) -> float:
        elapsed = self._clock() - self._start
        return self._total / elapsed if elapsed > 0 else 0.0

    def elapsed(self) -> float:
        return self._clock() - self._start


class ProgressReporter:
    """Progress callback printing per-file and overall progress, speed and ETA.

    The first callback for a file only establishes its baseline (bytes already on disk from a
    previous run), so resumed or skipped files do not inflate the measured speed. Thread-safe.
    """

    def __init__(
        self,
        total_bytes: int = 0,
        *,
        interval: float = 1.0,
        window: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        emit: Callable[[str], None] = print,
    ) -> None:
        self._total_bytes = total_bytes
        self._interval = interval
        self._clock = clock
        self._emit = emit
        self._meter = RateMeter(window, clock)
        self._seen: dict[str, int] = {}
        self._totals: dict[str, int] = {}
        self._items: dict[str, DownloadItem] = {}
        self._active_key: str | None = None
        self._overall_done = 0
        self._uncommitted = 0
        self._last_emit = float("-inf")
        self._lock = threading.Lock()
        self._wire_mode = False
        self._extra: Callable[[], str] | None = None

    def set_extra(self, extra: Callable[[], str] | None) -> None:
        """Register a callable whose text (e.g. ``conn 28/32``) is appended to each line."""
        self._extra = extra

    def record_bytes(self, nbytes: int) -> None:
        """Feed bytes as they arrive off the network.

        Once used, speed comes from these per-block samples instead of per-chunk completions,
        and progress lines continue ticking every ``interval`` even while large or heavily
        throttled chunks are still in flight.
        """
        if nbytes <= 0:
            return
        with self._lock:
            self._wire_mode = True
            self._meter.add(nbytes)
            self._uncommitted += nbytes
            now = self._clock()
            if self._active_key is None or now - self._last_emit < self._interval:
                return
            key = self._active_key
            item = self._items.get(key)
            base_done = self._seen.get(key, 0)
            total = self._totals.get(key, 0)
            if item is None or (total > 0 and base_done >= total):
                return
            live_done = self._live_item_done(base_done, total)
            msg = self._format(item, live_done, total)
            self._last_emit = now
            self._emit(msg)

    def __call__(self, item: DownloadItem, done: int, total: int) -> None:
        key = item.fid or item.path
        with self._lock:
            previous = self._seen.get(key)
            self._seen[key] = done
            self._totals[key] = total
            self._items[key] = item
            if previous is None:
                self._overall_done += done
                if total > 0 and done < total:
                    self._active_key = key
                return
            delta = max(0, done - previous)
            self._overall_done += delta
            if self._wire_mode:
                self._uncommitted = max(0, self._uncommitted - delta)
            else:
                self._meter.add(delta)

            now = self._clock()
            finished = total == 0 or done >= total
            if finished:
                incomplete = [k for k, d in self._seen.items() if d < self._totals.get(k, 0)]
                if incomplete:
                    self._active_key = incomplete[0]
                else:
                    self._active_key = None
                    self._uncommitted = 0
            else:
                self._active_key = key

            if finished or now - self._last_emit >= self._interval:
                live_done = done if finished else self._live_item_done(done, total)
                msg = self._format(item, live_done, total)
                self._last_emit = now
                self._emit(msg)

    def _live_item_done(self, committed: int, total: int) -> int:
        if not self._wire_mode or total <= 0 or committed >= total:
            return committed
        incomplete_count = sum(1 for k, d in self._seen.items() if d < self._totals.get(k, 0))
        if incomplete_count != 1:
            return committed
        return min(max(total - 1, committed), committed + self._uncommitted)

    def _format(self, item: DownloadItem, done: int, total: int) -> str:
        pct = 100 * done / total if total else 100.0
        speed = self._meter.speed()
        line = f"  {pct:5.1f}% {human_bytes(done)}/{human_bytes(total)}"
        line += f"  {human_bytes(int(speed))}/s" if speed > 0 else "  --/s"
        if self._total_bytes:
            if self._wire_mode and self._overall_done < self._total_bytes:
                live_overall = min(
                    max(self._total_bytes - 1, self._overall_done),
                    self._overall_done + self._uncommitted,
                )
            else:
                live_overall = self._overall_done
            remaining = max(self._total_bytes - live_overall, 0)
            overall_pct = min(100 * live_overall / self._total_bytes, 100.0)
            eta = format_duration(remaining / speed) if speed > 0 else "--"
            line += f" | all {overall_pct:5.1f}% ETA {eta}"
        if self._extra is not None:
            line += f" | {self._extra()}"
        return f"{line} | {item.path}"

    def summary(self) -> str:
        with self._lock:
            return (
                f"Transferred {human_bytes(self._meter.total)} in "
                f"{format_duration(self._meter.elapsed())} "
                f"(avg {human_bytes(int(self._meter.average_speed()))}/s)"
            )
