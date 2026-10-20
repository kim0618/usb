"""A request-level hard deadline for the producer's chart lanes.

``KiwoomMarketDataClient`` calls ``limits.chart.acquire()`` immediately before every HTTP attempt,
retries included. This limiter paces like ``RequestRateLimiter`` and then reads the ET clock
*again* after the pacing wait: a request whose turn comes at or after the phase deadline is never
sent, it raises :class:`DeadlineReached` instead. That is the principle of the uncommitted
``lane_share`` pacing fix, carried over without its fixed A/E clock windows: here one process owns
both lanes and only the phase deadline moves (09:15:00 rolling, 09:25:00 A sweep, 09:29:45 E).

Each phase is closed with :meth:`DeadlineRateLimiter.begin_phase`, which files the phase's own
sent count and last release instant, so "no request at or after 09:25:00" is read off the record
rather than assumed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import threading
import time as _time
from typing import Any


class DeadlineReached(RuntimeError):
    """The lane's phase deadline has passed; no request was sent."""

    code = "KIWOOM_DEADLINE_REACHED"


@dataclass
class DeadlineRateLimiter:
    """``RequestRateLimiter`` pacing plus a re-checked wall-clock deadline."""

    requests_per_second: float
    clock: Callable[[], datetime]
    deadline: datetime | None = None
    #: A slower pace for named phases (e.g. the five-hour rolling phase, which has no deadline to
    #: make); every other phase paces at ``requests_per_second``.
    phase_rates: dict[str, float] = field(default_factory=dict)
    phase: str = "setup"
    monotonic: Callable[[], float] = _time.monotonic
    sleeper: Callable[[float], None] = _time.sleep
    sent: int = 0
    refused: int = 0
    last_sent_at: datetime | None = None
    phases: list[dict[str, Any]] = field(default_factory=list)
    _phase_sent: int = field(default=0, init=False)
    _phase_refused: int = field(default=0, init=False)
    _phase_last: datetime | None = field(default=None, init=False)
    _next_allowed: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def begin_phase(self, name: str, deadline: datetime | None) -> None:
        """Close the current phase's record and move the deadline."""
        with self._lock:
            self.phases.append(self._phase_record())
            self.phase, self.deadline = name, deadline
            self._phase_sent, self._phase_refused, self._phase_last = 0, 0, None

    def _phase_record(self) -> dict[str, Any]:
        return {"phase": self.phase, "deadline": self.deadline.isoformat() if self.deadline else None,
                "sent": self._phase_sent, "refused": self._phase_refused,
                "last_sent_at": self._phase_last.isoformat() if self._phase_last else None,
                "last_sent_before_deadline": (self._phase_last is None or self.deadline is None
                                              or self._phase_last < self.deadline)}

    def _refuse(self, moment: datetime, why: str) -> None:
        self.refused += 1
        self._phase_refused += 1
        raise DeadlineReached(f"{why}: {moment.isoformat()} >= deadline {self.deadline.isoformat()}")

    def rate(self) -> float:
        return self.phase_rates.get(self.phase, self.requests_per_second)

    def acquire(self) -> None:
        with self._lock:
            interval = 1.0 / self.rate()
            moment = self.clock()
            if self.deadline is not None and moment >= self.deadline:
                self._refuse(moment, "deadline reached")
            delay = max(0.0, self._next_allowed - self.monotonic())
            if delay:
                self.sleeper(delay)
            # the pacing wait can run past the deadline: the release instant is read once more and
            # is the instant the decision is made on, so nothing leaves at or after the deadline
            released = self.clock()
            if self.deadline is not None and released >= self.deadline:
                self._refuse(released, "deadline reached after pacing")
            self._next_allowed = max(self.monotonic(), self._next_allowed) + interval
            self.sent += 1
            self._phase_sent += 1
            self.last_sent_at = self._phase_last = released

    def declaration(self) -> dict[str, Any]:
        with self._lock:
            return {"rate_per_s": self.requests_per_second, "phase_rates": dict(self.phase_rates),
                    "sent": self.sent, "refused": self.refused,
                    "phases": [*self.phases, self._phase_record()]}
