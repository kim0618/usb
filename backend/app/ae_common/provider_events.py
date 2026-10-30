"""Per-symbol provider error telemetry for the common producer's morning.

Every failed chart request is recorded with its phase, lane, exchange, attempt and what Kiwoom
answered (HTTP status, return code, subcode, sanitized message). Nothing from the request headers
or credentials is ever read. Detailed events are capped per phase (500 in all) and the overflow is
counted, so a bad morning cannot grow the run record without bound.

An *episode* is one (symbol, phase) with at least one error; it is recovered when the symbol's
last attempt in that phase succeeded.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
import threading
from typing import Any

from app.core import provider_errors as PE

ROLLING, A_SWEEP, E_FINALIZATION = "rolling", "a_sweep", "e_finalization"
#: Detailed events kept per phase (500 in all); the rest are only counted.
MAX_EVENTS = {ROLLING: 300, A_SWEEP: 100, E_FINALIZATION: 100}
MAX_EXAMPLES = 30
#: retry_outcome values. A queued retry becomes RETRIED once it is sent, or DEADLINE_BEFORE_RETRY.
NEXT_CYCLE, NOT_RETRIED = "NEXT_CYCLE", "NOT_RETRIED"
RETRY_QUEUED, RETRIED, DEADLINE_BEFORE_RETRY = "RETRY_QUEUED", "RETRIED", "DEADLINE_BEFORE_RETRY"
EXHAUSTED, NOT_RETRYABLE = "EXHAUSTED", "NOT_RETRYABLE"
RECOVERED, UNRECOVERED = "RECOVERED", "UNRECOVERED"


class ProviderEvents:
    def __init__(self, clock: Callable[[], datetime], max_events: Mapping[str, int] | None = None) -> None:
        self._clock = clock
        self._max = dict(MAX_EVENTS if max_events is None else max_events)
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self._kept: Counter = Counter()
        self.dropped: Counter = Counter()
        self.errors: Counter = Counter()
        self.code7: Counter = Counter()
        self._episodes: dict[tuple[str, str], dict[str, Any]] = {}
        self._open: dict[tuple[str, str], dict[str, Any]] = {}       # last kept event of an episode

    def record(self, symbol: str, phase: str, lane: Any, error: BaseException, *, attempt: int,
               max_attempts: int, requested_exchange: str | None, resolved_exchange: str | None,
               retry_outcome: str) -> None:
        detail = PE.classify(error)
        code7 = PE.generic_rejection(error)
        event = {"timestamp": self._clock().isoformat(), "symbol": symbol, "phase": phase,
                 "api_id": getattr(lane, "api_id", None), "actual_lane": getattr(lane, "name", None),
                 "requested_exchange": getattr(error, "requested_exchange", None) or requested_exchange,
                 "resolved_exchange": resolved_exchange, "attempt": attempt, "max_attempts": max_attempts,
                 "http_status": detail.http_status, "provider_return_code": detail.provider_return_code,
                 "provider_subcode": detail.provider_subcode, "sanitized_return_msg": detail.sanitized_return_msg,
                 "error_class": detail.error_class, "error_code": detail.error_code,
                 "error_category": detail.error_category, "retryable": code7, "retry_outcome": retry_outcome}
        key = (symbol, phase)
        with self._lock:
            self.errors[phase] += 1
            self.code7[phase] += code7
            episode = self._episodes.setdefault(key, {"symbol": symbol, "phase": phase, "errors": 0,
                                                      "generic_code7": False})
            episode.update(errors=episode["errors"] + 1, code=detail.error_code,
                           provider_return_code=detail.provider_return_code,
                           provider_subcode=detail.provider_subcode, attempt=attempt, result=UNRECOVERED,
                           retry_outcome=retry_outcome)
            episode["generic_code7"] = episode["generic_code7"] or code7
            if self._kept[phase] < self._max.get(phase, 0):
                self._kept[phase] += 1
                self.events.append(event)
                self._open[key] = event
            else:
                self.dropped[phase] += 1
                self._open.pop(key, None)

    def _close(self, key: tuple[str, str], outcome: str) -> None:
        event = self._open.pop(key, None)
        if event is not None and event["retry_outcome"] == RETRY_QUEUED:
            event["retry_outcome"] = outcome
        episode = self._episodes.get(key)
        if episode is not None and episode["retry_outcome"] == RETRY_QUEUED:
            episode["retry_outcome"] = outcome

    def retried(self, symbol: str, phase: str) -> None:
        """A queued retry was sent."""
        with self._lock:
            self._close((symbol, phase), RETRIED)

    def blocked(self, symbols: Iterable[str], phase: str) -> None:
        """Queued retries the deadline stopped before any request was sent."""
        with self._lock:
            for symbol in symbols:
                self._close((symbol, phase), DEADLINE_BEFORE_RETRY)

    def succeeded(self, symbol: str, phase: str, attempt: int = 1) -> None:
        key = (symbol, phase)
        with self._lock:
            episode = self._episodes.get(key)
            if episode is not None:
                episode.update(result=RECOVERED, attempt=attempt)

    def summary(self, http: Mapping[str, Mapping[str, Any]] | None = None,
                phases: Iterable[str] | None = None) -> dict[str, Any]:
        """Counts, the capped events and episode examples; ``http`` is ``CountingChartLaneClient.stats()``
        per lane, for the transport-level 429 / 5xx / transport error counts."""
        with self._lock:
            wanted = set(phases) if phases is not None else None
            episodes = [dict(e) for e in self._episodes.values() if wanted is None or e["phase"] in wanted]
            events = [dict(e) for e in self.events if wanted is None or e["phase"] in wanted]
            errors = {p: n for p, n in self.errors.items() if wanted is None or p in wanted}
            dropped = {p: n for p, n in self.dropped.items() if wanted is None or p in wanted}
            code7_events = sum(n for p, n in self.code7.items() if wanted is None or p in wanted)
        recovered = [e for e in episodes if e["result"] == RECOVERED]
        code7 = [e for e in episodes if e["generic_code7"]]
        order = {ROLLING: 0, A_SWEEP: 1, E_FINALIZATION: 2}
        examples = sorted((e for e in episodes if e["result"] != RECOVERED),
                          key=lambda e: (order.get(e["phase"], 9), e["symbol"]))
        examples += sorted(recovered, key=lambda e: (order.get(e["phase"], 9), e["symbol"]))
        lanes = http or {}
        return {
            "provider_error_count": sum(errors.values()),
            "provider_error_recovered_count": len(recovered),
            "provider_error_unrecovered_count": len(episodes) - len(recovered),
            "episode_unit": "(symbol, phase) with at least one error; recovered = last attempt in the phase succeeded",
            "generic_code7_count": code7_events,
            "generic_code7_recovered_count": sum(1 for e in code7 if e["result"] == RECOVERED),
            "generic_code7_unrecovered_count": sum(1 for e in code7 if e["result"] != RECOVERED),
            "rolling_error_count": errors.get(ROLLING, 0),
            "a_sweep_error_count": errors.get(A_SWEEP, 0),
            "finalization_error_count": errors.get(E_FINALIZATION, 0),
            "http_429_count": sum(int(s.get("status_429", 0)) for s in lanes.values()),
            "http_5xx_count": sum(int(s.get("status_5xx", 0)) for s in lanes.values()),
            "transport_error_count": sum(int(s.get("transport_errors", 0)) for s in lanes.values()),
            "detailed_event_count": len(events),
            "dropped_event_count": sum(dropped.values()),
            "dropped_by_phase": dropped,
            "examples": [{"symbol": e["symbol"], "phase": e["phase"], "code": e["code"],
                          "provider_return_code": e["provider_return_code"], "attempt": e["attempt"],
                          "result": e["result"], "retry_outcome": e["retry_outcome"]}
                         for e in examples[:MAX_EXAMPLES]],
            "events": events,
        }
