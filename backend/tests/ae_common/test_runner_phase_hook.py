"""A phase starts on the clock: the backend health probe runs beside it and never delays its T0."""

from __future__ import annotations

import threading
import time

from app.dev import run_ae_common_producer as RUN


class _Sampler:
    def __init__(self):
        self.intervals = []

    def dense(self, interval):
        self.intervals.append(interval)


def test_on_phase_returns_before_a_slow_health_probe_finishes():
    release = threading.Event()

    def slow_health():
        release.wait(5)                     # 2026-10-19: systemctl + HTTP under memory pressure, 1.2 s
        return {"health": "ok"}

    record, probes, sampler = {"a_health": []}, [], _Sampler()
    on_phase = RUN.phase_hook(sampler, record, slow_health, probes)
    started = time.monotonic()
    on_phase("e_finalization")
    assert time.monotonic() - started < 0.1 and record["a_health"] == []
    release.set()
    for thread in probes:
        thread.join(timeout=2)
    assert record["a_health"] == [{"health": "ok", "phase": "e_finalization"}]
    assert sampler.intervals == [2.0]


def test_rolling_has_no_probe():
    record, probes = {"a_health": []}, []
    RUN.phase_hook(_Sampler(), record, lambda: {"health": "ok"}, probes)("rolling")
    assert probes == [] and record["a_health"] == []
