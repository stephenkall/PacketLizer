"""Analise das amostras: KPIs, deteccao de quedas (outages) e tendencias.

Tudo aqui e funcao pura sobre uma sequencia de `Sample`, para ser testavel
sem banco de dados nem rede.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from .config import STATUS_LABEL, STATUS_OK


@dataclass
class Outage:
    start_ts: int
    end_ts: int          # ts da ultima amostra perdida
    recovered_ts: int | None  # ts da primeira amostra OK apos a queda (se houve)
    lost_count: int
    status_kinds: dict[str, int] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        end = self.recovered_ts if self.recovered_ts is not None else self.end_ts
        return max(0, end - self.start_ts)

    @property
    def start_dt(self) -> datetime:
        return datetime.fromtimestamp(self.start_ts)


@dataclass
class Report:
    target: str
    generated_at: float
    first_ts: int | None
    last_ts: int | None
    total: int
    ok: int
    lost: int
    interval_seconds: float
    timeout_sentinel_ms: float
    outages: list[Outage]
    status_breakdown: dict[str, int]
    latency: dict[str, float]
    daily: list[dict]
    hour_histogram: list[int]      # 24 posicoes: nº de inicios de queda por hora do dia
    weekday_histogram: list[int]   # 7 posicoes (0=segunda)

    # ---- KPIs derivados -------------------------------------------------
    @property
    def span_seconds(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return max(0.0, self.last_ts - self.first_ts)

    @property
    def span_days(self) -> float:
        return self.span_seconds / 86400 or 1e-9

    @property
    def loss_pct(self) -> float:
        return (self.lost / self.total * 100.0) if self.total else 0.0

    @property
    def availability_pct(self) -> float:
        return 100.0 - self.loss_pct

    @property
    def outage_count(self) -> int:
        return len(self.outages)

    @property
    def total_downtime_s(self) -> float:
        return sum(o.duration_s for o in self.outages)

    @property
    def avg_outage_s(self) -> float:
        return statistics.fmean(o.duration_s for o in self.outages) if self.outages else 0.0

    @property
    def median_outage_s(self) -> float:
        return statistics.median(o.duration_s for o in self.outages) if self.outages else 0.0

    @property
    def max_outage_s(self) -> float:
        return max((o.duration_s for o in self.outages), default=0.0)

    @property
    def outages_per_day(self) -> float:
        return self.outage_count / self.span_days

    @property
    def mean_interval_between_outages_s(self) -> float:
        starts = sorted(o.start_ts for o in self.outages)
        if len(starts) < 2:
            return 0.0
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        return statistics.fmean(gaps)

    @property
    def mtbf_s(self) -> float:
        return self.span_seconds / self.outage_count if self.outage_count else 0.0

    @property
    def mttr_s(self) -> float:
        return self.total_downtime_s / self.outage_count if self.outage_count else 0.0

    @property
    def peak_hour(self) -> int | None:
        if not any(self.hour_histogram):
            return None
        return max(range(24), key=lambda h: self.hour_histogram[h])

    @property
    def peak_weekday(self) -> int | None:
        if not any(self.weekday_histogram):
            return None
        return max(range(7), key=lambda d: self.weekday_histogram[d])


def _percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def analyze(
    samples: Iterable,
    *,
    target: str = "?",
    interval_seconds: float = 1.0,
    timeout_sentinel_ms: float = 9999.0,
    outage_min_consecutive: int = 3,
) -> Report:
    total = ok = lost = 0
    first_ts: int | None = None
    last_ts: int | None = None
    status_breakdown: dict[str, int] = {}
    lat_values: list[float] = []
    prev_lat: float | None = None
    jitter_acc = 0.0
    jitter_n = 0

    outages: list[Outage] = []
    run_start: int | None = None
    run_last: int | None = None
    run_len = 0
    run_kinds: dict[str, int] = {}

    daily: dict[str, dict] = {}
    hour_hist = [0] * 24
    wday_hist = [0] * 7

    def _close_run(recovered_ts: int | None) -> None:
        nonlocal run_start, run_last, run_len, run_kinds
        if run_start is not None and run_len >= outage_min_consecutive:
            o = Outage(run_start, run_last, recovered_ts, run_len, dict(run_kinds))
            outages.append(o)
            dt = o.start_dt
            hour_hist[dt.hour] += 1
            wday_hist[dt.weekday()] += 1
        run_start = run_last = None
        run_len = 0
        run_kinds = {}

    for s in samples:
        total += 1
        first_ts = s.ts if first_ts is None else first_ts
        last_ts = s.ts
        lbl = STATUS_LABEL.get(s.status, str(s.status))
        status_breakdown[lbl] = status_breakdown.get(lbl, 0) + 1

        day = datetime.fromtimestamp(s.ts).strftime("%Y-%m-%d")
        d = daily.setdefault(day, {"date": day, "samples": 0, "lost": 0, "rtt_sum": 0.0, "rtt_n": 0})
        d["samples"] += 1

        if s.status == STATUS_OK:
            ok += 1
            if s.rtt_ms is not None:
                lat_values.append(s.rtt_ms)
                d["rtt_sum"] += s.rtt_ms
                d["rtt_n"] += 1
                if prev_lat is not None:
                    jitter_acc += abs(s.rtt_ms - prev_lat)
                    jitter_n += 1
                prev_lat = s.rtt_ms
            _close_run(recovered_ts=s.ts)
        else:
            lost += 1
            d["lost"] += 1
            if run_start is None:
                run_start = s.ts
            run_last = s.ts
            run_len += 1
            run_kinds[lbl] = run_kinds.get(lbl, 0) + 1

    _close_run(recovered_ts=None)

    lat_sorted = sorted(lat_values)
    latency = {
        "count": float(len(lat_sorted)),
        "min": lat_sorted[0] if lat_sorted else 0.0,
        "avg": statistics.fmean(lat_sorted) if lat_sorted else 0.0,
        "p50": _percentile(lat_sorted, 0.50),
        "p95": _percentile(lat_sorted, 0.95),
        "p99": _percentile(lat_sorted, 0.99),
        "max": lat_sorted[-1] if lat_sorted else 0.0,
        "jitter": (jitter_acc / jitter_n) if jitter_n else 0.0,
    }

    daily_list = []
    for day in sorted(daily):
        d = daily[day]
        d["loss_pct"] = (d["lost"] / d["samples"] * 100.0) if d["samples"] else 0.0
        d["avg_rtt_ms"] = (d["rtt_sum"] / d["rtt_n"]) if d["rtt_n"] else None
        d["outages"] = sum(
            1 for o in outages if datetime.fromtimestamp(o.start_ts).strftime("%Y-%m-%d") == day
        )
        daily_list.append(d)

    return Report(
        target=target,
        generated_at=time.time(),
        first_ts=first_ts,
        last_ts=last_ts,
        total=total,
        ok=ok,
        lost=lost,
        interval_seconds=interval_seconds,
        timeout_sentinel_ms=timeout_sentinel_ms,
        outages=outages,
        status_breakdown=status_breakdown,
        latency=latency,
        daily=daily_list,
        hour_histogram=hour_hist,
        weekday_histogram=wday_hist,
    )


def humanize_seconds(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


WEEKDAY_PT = ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"]
