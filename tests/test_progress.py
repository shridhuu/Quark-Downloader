from quarkdl.core import DownloadItem
from quarkdl.progress import ProgressReporter, RateMeter, format_duration


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_format_duration():
    assert format_duration(5.9) == "5s"
    assert format_duration(252) == "4m12s"
    assert format_duration(3720) == "1h02m"
    assert format_duration(None) == "--"
    assert format_duration(float("inf")) == "--"


def test_rate_meter_reports_recent_speed_and_ignores_old_samples():
    clock = FakeClock()
    meter = RateMeter(window=10.0, clock=clock)
    clock.now += 1
    meter.add(1000)  # 1000 B/s over the first second
    assert meter.speed() == 1000
    for _ in range(20):  # then a sustained 50 kB/s
        clock.now += 1
        meter.add(50_000)
    assert meter.total == 1_001_000
    assert meter.speed() == 50_000  # the slow first second has left the 10 s window
    assert meter.average_speed() < meter.speed()


def test_rate_meter_ignores_non_positive_deltas():
    meter = RateMeter(clock=FakeClock())
    meter.add(0)
    meter.add(-5)
    assert meter.total == 0
    assert meter.speed() == 0


def test_reporter_does_not_count_resumed_bytes_as_speed():
    clock = FakeClock()
    lines: list[str] = []
    reporter = ProgressReporter(total_bytes=2000, interval=0, clock=clock, emit=lines.append)
    item = DownloadItem(fid="f", path="dir/a.bin", size=1000)

    reporter(item, 600, 1000)  # baseline: 600 bytes already on disk
    assert lines == []
    clock.now += 2
    reporter(item, 800, 1000)
    clock.now += 2
    reporter(item, 1000, 1000)

    assert reporter._meter.total == 400
    assert len(lines) == 2
    assert "100.0%" in lines[-1]
    assert "dir/a.bin" in lines[-1]
    assert "B/s" in lines[-1]
    assert "all  50.0%" in lines[-1]
    assert "Transferred 400 B" in reporter.summary()


def test_reporter_throttles_output():
    clock = FakeClock()
    lines: list[str] = []
    reporter = ProgressReporter(interval=1.0, clock=clock, emit=lines.append)
    item = DownloadItem(fid="f", path="a", size=100)
    reporter(item, 0, 100)
    for done in (10, 20, 30):
        clock.now += 0.1
        reporter(item, done, 100)
    assert len(lines) == 1  # first update prints, the rest fall inside the interval
    clock.now += 2
    reporter(item, 40, 100)
    assert len(lines) == 2
