from datetime import datetime

from packetlizer.analytics import analyze, humanize_seconds
from packetlizer.config import STATUS_OK, STATUS_TIMEOUT
from packetlizer.storage import Sample


def _mk(n, lost_ranges, base=1_700_000_000, rtt=20.0):
    lost = set()
    for a, b in lost_ranges:
        lost.update(range(a, b))
    out = []
    for i in range(n):
        if i in lost:
            out.append(Sample(base + i, None, STATUS_TIMEOUT))
        else:
            out.append(Sample(base + i, rtt + (i % 3), STATUS_OK))
    return out


def test_basic_counts_and_loss():
    samples = _mk(100, [(10, 20), (30, 32)])  # 10 + 2 perdidos
    rep = analyze(samples, target="x", interval_seconds=1.0, outage_min_consecutive=3)
    assert rep.total == 100
    assert rep.lost == 12
    assert abs(rep.loss_pct - 12.0) < 1e-9
    assert abs(rep.availability_pct - 88.0) < 1e-9


def test_outage_detection_threshold():
    samples = _mk(100, [(10, 20), (30, 32)])
    rep = analyze(samples, interval_seconds=1.0, outage_min_consecutive=3)
    # so a sequencia de 10 conta; a de 2 fica abaixo do limiar
    assert rep.outage_count == 1
    o = rep.outages[0]
    assert o.lost_count == 10
    assert o.start_ts == 1_700_000_000 + 10
    assert o.recovered_ts == 1_700_000_000 + 20
    assert o.duration_s == 10


def test_trailing_outage_without_recovery():
    samples = _mk(30, [(25, 30)])  # termina em queda, sem recuperar
    rep = analyze(samples, interval_seconds=1.0, outage_min_consecutive=3)
    assert rep.outage_count == 1
    o = rep.outages[0]
    assert o.recovered_ts is None
    assert o.duration_s == 4  # end_ts - start_ts = 29 - 25


def test_latency_percentiles():
    samples = [Sample(1_700_000_000 + i, float(i), STATUS_OK) for i in range(101)]
    rep = analyze(samples)
    assert rep.latency["min"] == 0.0
    assert rep.latency["max"] == 100.0
    assert abs(rep.latency["p50"] - 50.0) < 1e-6
    assert abs(rep.latency["avg"] - 50.0) < 1e-6


def test_hour_histogram_marks_outage_start():
    base = int(datetime(2026, 1, 5, 3, 0, 0).timestamp())  # 03h local
    samples = []
    for i in range(60):
        st = STATUS_TIMEOUT if 10 <= i < 20 else STATUS_OK
        samples.append(Sample(base + i, None if st else 15.0, st))
    rep = analyze(samples, interval_seconds=1.0, outage_min_consecutive=3)
    assert rep.peak_hour == 3
    assert rep.hour_histogram[3] == 1


def test_humanize_seconds():
    assert humanize_seconds(45) == "45s"
    assert humanize_seconds(125) == "2m 5s"
    assert humanize_seconds(3700).startswith("1h")
