from __future__ import annotations

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
    previous run), so resumed or skipped files do not inflate the measured speed.
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
        self._overall_done = 0
        self._last_emit = float("-inf")

    def __call__(self, item: DownloadItem, done: int, total: int) -> None:
        key = item.fid or item.path
        previous = self._seen.get(key)
        self._seen[key] = done
        if previous is None:
            self._overall_done += done
            return
        delta = done - previous
        self._overall_done += delta
        self._meter.add(delta)

        now = self._clock()
        finished = total > 0 and done >= total
        if finished or now - self._last_emit >= self._interval:
            self._emit(self._format(item, done, total))
            self._last_emit = now

    def _format(self, item: DownloadItem, done: int, total: int) -> str:
        pct = 100 * done / total if total else 100.0
        speed = self._meter.speed()
        line = f"  {pct:5.1f}% {human_bytes(done)}/{human_bytes(total)}"
        line += f"  {human_bytes(int(speed))}/s" if speed > 0 else "  --/s"
        if self._total_bytes:
            remaining = max(self._total_bytes - self._overall_done, 0)
            overall_pct = min(100 * self._overall_done / self._total_bytes, 100.0)
            eta = format_duration(remaining / speed) if speed > 0 else "--"
            line += f" | all {overall_pct:5.1f}% ETA {eta}"
        return f"{line} | {item.path}"

    def summary(self) -> str:
        return (
            f"Transferred {human_bytes(self._meter.total)} in "
            f"{format_duration(self._meter.elapsed())} "
            f"(avg {human_bytes(int(self._meter.average_speed()))}/s)"
        )

