"""How E's worker and A's mover worker share the one Kiwoom app key (E-RUNTIME-R1).

Until 2026-10-16 A's mover cut ran inside E's worker and E waited for it - A's 09:15 cut, its scan
and its GPT handoff - before starting its own 09:25 finalization, which then began between 09:27
and 09:52 ET. The two now run as separate processes and never wait for each other. What they still
share is the key's per-API-ID rate (measured ~5 req/s each for usa06011 and usa06010), so the
split is fixed here by the ET clock, the same on both sides and decided by nobody at runtime:

========================  =====================  =====================
ET window                 minute API usa06011     tick API usa06010
========================  =====================  =====================
04:00 - 09:15             E 2.45  |  A 2.45      A 4.9
09:15 - 09:20:40          E 0     |  A 4.9       A 4.9      (A's cut)
09:20:40 - 09:25:00       E 4.9   |  A 0         A 4.9      (E's tick-shard refresh, as before 10-05)
09:25:00 - 09:29:45       E 4.9   |  A closed    E 4.9      (E's finalization, E alone)
after 09:29:45            A runtime / E entry and exit; neither worker's rolling
========================  =====================  =====================

E keeps exactly the refresh and finalization budget it had before A was attached (09:20:40 tick-
shard refresh, 09:25:00 - 09:29:45 finalization on both lanes alone). A's cut gets both lanes from
09:15 and loses the minute lane at 09:20:40; every A call stops at 09:25:00. These are clock windows,
not messages: neither process reads the other's state, so a slow or failed A cannot move E's T0.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
import threading
import time as _time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FULL_RATE = 4.9

ROLLING_START = time(4, 0)
A_CUT = time(9, 15)
E_TICK_REFRESH = time(9, 20, 40)
E_FINALIZE = time(9, 25)
E_DEADLINE = time(9, 29, 45)

#: (start, end, rate) per side and API; a rate of 0 inside a window means "wait for the next open
#: window", and no window at all means closed for the rest of the session.
E_MINUTE = ((ROLLING_START, A_CUT, 2.45), (A_CUT, E_TICK_REFRESH, 0.0), (E_TICK_REFRESH, E_DEADLINE, FULL_RATE))
E_TICK = ((E_FINALIZE, E_DEADLINE, FULL_RATE),)
A_MINUTE = ((ROLLING_START, A_CUT, 2.45), (A_CUT, E_TICK_REFRESH, FULL_RATE))
A_TICK = ((ROLLING_START, E_FINALIZE, FULL_RATE),)


class KiwoomWindowClosed(RuntimeError):
    """The caller's share of this API is over for the session; no request was sent."""

    code = "KIWOOM_WINDOW_CLOSED"


@dataclass
class ScheduledRateLimiter:
    """A ``RequestRateLimiter`` whose rate follows the ET clock of one session.

    Drop-in for ``KiwoomRateLimits.chart``: ``acquire`` either paces the request at the window's
    rate, sleeps until the next window that has a rate, or raises ``KiwoomWindowClosed``.
    Requests made before the first window wait for it.
    """

    session: date
    windows: Sequence[tuple[time, time, float]]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(ET))
    sleeper: Callable[[float], None] = _time.sleep
    #: When the last window is over, block until this ET time before raising. A's minute lane uses
    #: it (09:25): its cut worker claims a symbol before asking, so a fast refusal would claim the
    #: rest of its list away from the tick lane; blocking costs it one symbol instead.
    hold_until: time | None = None
    _next_allowed: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def _at(self, moment: time) -> datetime:
        return datetime.combine(self.session, moment, tzinfo=ET)

    def window(self, now: datetime | None = None) -> tuple[float, datetime | None]:
        """(rate now, start of the next open window or None)."""
        now = now or self.clock()
        for start, end, rate in self.windows:
            if self._at(start) <= now < self._at(end):
                if rate > 0:
                    return rate, None
        upcoming = [self._at(start) for start, _end, rate in self.windows if rate > 0 and self._at(start) > now]
        return 0.0, (min(upcoming) if upcoming else None)

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self.clock()
                rate, resume = self.window(now)
                if rate > 0:
                    break
                if resume is None:
                    if self.hold_until is not None and now < self._at(self.hold_until):
                        self.sleeper(max((self._at(self.hold_until) - now).total_seconds(), 0.05))
                        continue
                    raise KiwoomWindowClosed(f"no Kiwoom window left after {now.time()} ET")
                self.sleeper(max((resume - now).total_seconds(), 0.05))
            interval = 1.0 / rate
            mono = _time.monotonic()
            delay = max(0.0, self._next_allowed - mono)
            if delay:
                self.sleeper(delay)
                mono = _time.monotonic()
            self._next_allowed = max(mono, self._next_allowed) + interval


def combined_rate(moment: time, api: str) -> float:
    """What both workers may send together at ``moment`` (used by the contract test)."""
    tables = {"minute": (E_MINUTE, A_MINUTE), "tick": (E_TICK, A_TICK)}[api]
    return sum(rate for table in tables for start, end, rate in table if start <= moment < end)
