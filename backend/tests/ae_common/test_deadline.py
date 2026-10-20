"""The request-level hard deadline: nothing leaves at or after the phase deadline."""

from datetime import time, timedelta
import threading

import pytest

from app.ae_common.deadline import DeadlineRateLimiter, DeadlineReached
from tests.ae_common.helpers import Clock, at, limiter


def test_a_request_before_the_deadline_is_released_and_recorded():
    clock = Clock(at(time(9, 24, 59)))
    lim = limiter(clock)
    lim.begin_phase("a_sweep", at(time(9, 25)))
    lim.acquire()
    assert lim.sent == 1 and lim.last_sent_at == at(time(9, 24, 59))


def test_a_request_at_the_deadline_is_refused_without_being_released():
    clock = Clock(at(time(9, 25)))
    lim = limiter(clock)
    lim.begin_phase("a_sweep", at(time(9, 25)))
    with pytest.raises(DeadlineReached):
        lim.acquire()
    assert lim.sent == 0 and lim.refused == 1


def test_the_pacing_wait_that_crosses_the_deadline_refuses_the_request():
    """The case the uncommitted lane_share fix is about: allowed before the wait, late after it."""
    clock = Clock(at(time(9, 24, 59, 900000)))
    lim = DeadlineRateLimiter(1.0, clock, monotonic=clock.monotonic, sleeper=clock.tick)
    lim.begin_phase("a_sweep", at(time(9, 25)))
    lim.acquire()                                      # 09:24:59.9, sent
    with pytest.raises(DeadlineReached):
        lim.acquire()                                  # the 1 s pacing wait ends at 09:25:00.9
    assert lim.sent == 1 and lim.refused == 1
    assert lim.last_sent_at < at(time(9, 25))


def test_each_phase_files_its_own_count_and_last_release():
    clock = Clock(at(time(9, 14, 59)))
    lim = limiter(clock)
    lim.begin_phase("rolling", at(time(9, 15)))
    lim.acquire()
    clock.tick(1.0)
    lim.begin_phase("a_sweep", at(time(9, 25)))
    lim.acquire()
    lim.acquire()
    phases = {p["phase"]: p for p in lim.declaration()["phases"]}
    assert phases["rolling"]["sent"] == 1 and phases["a_sweep"]["sent"] == 2
    assert all(p["last_sent_before_deadline"] for p in phases.values())


def test_two_threads_never_release_a_request_at_or_after_the_deadline():
    clock = Clock(at(time(9, 24, 50)))
    lim = limiter(clock, rate=4.9)
    lim.begin_phase("a_sweep", at(time(9, 25)))
    released = []
    lock = threading.Lock()

    def worker():
        while True:
            try:
                lim.acquire()
            except DeadlineReached:
                return
            with lock:
                released.append(clock())
            clock.tick(0.2)
    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert released and max(released) < at(time(9, 25))
    assert lim.refused >= 2                              # both workers were stopped by the limiter


def test_a_phase_rate_slows_only_its_phase():
    """Rolling paces at its own rate; the deadline phases keep the lane rate."""
    clock = Clock(at(time(9, 0)))
    lim = DeadlineRateLimiter(5.0, clock, phase_rates={"rolling": 2.0}, monotonic=clock.monotonic, sleeper=clock.tick)
    lim.begin_phase("rolling", at(time(9, 15)))
    lim.acquire()
    lim.acquire()
    assert clock() - at(time(9, 0)) == timedelta(seconds=0.5)          # 1 / 2.0
    lim.begin_phase("a_sweep", at(time(9, 25)))
    start = clock()
    lim.acquire()                                                      # the rolling slot ends first
    lim.acquire()
    assert clock() - start == timedelta(seconds=0.5 + 0.2)             # then 1 / 5.0
    assert lim.declaration()["phase_rates"] == {"rolling": 2.0}
