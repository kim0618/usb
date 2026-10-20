"""The producer's lanes count every HTTP status and keep each 429 / 5xx with its instant and phase."""

from __future__ import annotations

from collections import Counter
from datetime import time
import threading
from types import SimpleNamespace

from app.ae_common import lanes as LN
from tests.ae_common.helpers import Clock, at


class _Inner:
    def __init__(self, codes):
        self.codes = list(codes)

    def post(self, *args, **kwargs):
        return SimpleNamespace(status_code=self.codes.pop(0))


def test_a_429_is_kept_with_its_instant_phase_and_api():
    clock = Clock(at(time(6, 12, 3)))
    statuses, events = Counter(), []
    transport = LN._CountingTransport(_Inner([200, 429, 200]), statuses, threading.Lock(), events,  # noqa: SLF001
                                      clock, lambda: "rolling")
    for _ in range(3):
        transport.post("u", headers={"api-id": "usa06011"}, json={})
    assert statuses == Counter({"200": 2, "429": 1})
    assert events == [{"at": at(time(6, 12, 3)).isoformat(), "status": "429", "phase": "rolling",
                       "api_id": "usa06011"}]


def test_the_event_log_is_bounded():
    statuses, events = Counter(), []
    transport = LN._CountingTransport(_Inner([429] * (LN.MAX_EVENTS + 5)), statuses, threading.Lock(), events)  # noqa: SLF001
    for _ in range(LN.MAX_EVENTS + 5):
        transport.post("u", headers={}, json={})
    assert statuses["429"] == LN.MAX_EVENTS + 5 and len(events) == LN.MAX_EVENTS


def test_build_paces_rolling_slower_and_the_cuts_at_the_lane_rate():
    _, _, clients, limiters = LN.build(base_url="http://kiwoom.invalid", auth=SimpleNamespace(), clock=Clock(at(time(4))))
    assert all(l.requests_per_second == LN.LANE_RATE and l.phase_rates == {"rolling": LN.ROLLING_RATE}
               for l in limiters)
    assert LN.ROLLING_RATE < LN.LANE_RATE
    assert clients[0].stats()["events"] == []
