"""The shared A/E premarket acquisition schedule. One process, two cuts, E's contract intact.

There is no separate A collector. The timestamp-safe Kiwoom lanes are exactly two
(``usa06011`` minute and ``usa06010`` tick) and E already holds both at 4.9 req/s against a
measured 5/s per-API-ID limit, so a second process asking for either lane has 0.1 req/s of
room and cannot finalize anything. One process serving both cuts serially does fit, because
the cuts are ten minutes apart and A's window is a *prefix* of E's:

    04:00            rolling cache over the union, exactly E's existing cycle
    09:15:00   T0_A  A's cut finalized (the 09:14 bar is complete only at 09:15:00)
    T1_A - 09:25     E's refresh of its tick shard, in E's own finalization order
    09:25:00   T0_E  E's cut finalized over E's own universe, unchanged
    09:29:45         Kiwoom calls stop, before Strategy A's first call at the 09:30 open

Two properties make this safe rather than merely plausible.

**A's window is a prefix of E's.** A reads ``[04:00, 09:15)`` and E reads ``[04:00, 09:24]``,
so the bars A finalizes are bars E would have read anyway. No request exists for A's sake that
E would not have made; the union's extra *symbols* are the only added cost.

**A's pass is ordered with E's tick shard last.** A symbol's tick-page cost at E's cut grows
with how stale its cached minute is. E's measured run leaves its tick shard stale for 260 s
(09:20:40 -> 09:25:00) and pages 1.037 times per symbol on average. Reading the tick shard at
the *end* of A's pass puts those symbols' staleness between ``T0_E - T1_A`` and
``T0_E - T1_A + (tick shard pass duration)``; while that upper end is at or below E's measured
260 s, every tick-shard symbol is fresher at E's cut than in the run E's cost was measured on,
so its page count cannot exceed the measured one. That removes the one unmeasured risk the
earlier audit left open, at a cost of zero extra calls.

Every instant is built with ``datetime.combine(..., tzinfo=ET)`` on the session's own date, so
a DST transition moves the wall clock and not the schedule; nothing here adds or subtracts
hours from a UTC instant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.strategy_a_mover_live import contract as LC
from app.strategy_e_max_rt.finalizer import (
    CUTOFF_LAST, DEADLINE, ET, FINALIZE_AT, PREMARKET_START, REFRESH_B_AT,
)

#: A's cut, from the research contract's own scan minute. Never written here.
A_CUT_MINUTE = LC.LiveContract().scan_cut_minute
A_CUT = time(A_CUT_MINUTE // 60, A_CUT_MINUTE % 60)
#: The last minute A may read: the bar starting at ``A_CUT_MINUTE - 1`` has ended at the cut.
A_CUTOFF_LAST_MINUTE = A_CUT_MINUTE - 1
#: The last minute E may read, restated from E's own module.
E_CUTOFF_LAST_MINUTE = CUTOFF_LAST.hour * 60 + CUTOFF_LAST.minute
PREMARKET_START_MINUTE = PREMARKET_START.hour * 60 + PREMARKET_START.minute
#: Strategy A's own first Kiwoom call of the session.
REGULAR_OPEN = time(9, 30)


class ScheduleInvalid(RuntimeError):
    """The two cuts cannot be served by one process under E's contract."""


def at(session: date, moment: time) -> datetime:
    """``moment`` of ``session`` in exchange time, over the zone's real offset."""
    return datetime.combine(session, moment, tzinfo=ET)


@dataclass(frozen=True)
class SharedSchedule:
    """One session's instants for both cuts, plus the invariants that make them compatible."""

    session: date

    # -- instants
    @property
    def rolling_start(self) -> datetime:
        return at(self.session, PREMARKET_START)

    @property
    def a_cut(self) -> datetime:
        return at(self.session, A_CUT)

    @property
    def e_tick_refresh_at(self) -> datetime:
        return at(self.session, REFRESH_B_AT)

    @property
    def e_cut(self) -> datetime:
        return at(self.session, FINALIZE_AT)

    @property
    def kiwoom_deadline(self) -> datetime:
        return at(self.session, DEADLINE)

    @property
    def regular_open(self) -> datetime:
        return at(self.session, REGULAR_OPEN)

    # -- invariants
    def validate(self) -> None:
        if not self.rolling_start < self.a_cut < self.e_cut < self.regular_open:
            raise ScheduleInvalid("the cuts must fall inside the premarket, in order")
        if self.kiwoom_deadline >= self.regular_open:
            raise ScheduleInvalid("Kiwoom calls must stop before A's first call at the open")
        if A_CUTOFF_LAST_MINUTE >= E_CUTOFF_LAST_MINUTE:
            raise ScheduleInvalid("A's window must be a strict prefix of E's")

    @property
    def a_window_minutes(self) -> tuple[int, int]:
        """``[start, end)`` ET minutes A reads. The end is the cut, so 09:14 is the last bar."""
        return PREMARKET_START_MINUTE, A_CUT_MINUTE

    @property
    def e_window_minutes(self) -> tuple[int, int]:
        """``[start, end]`` ET minutes E reads, inclusive, as E's own cutoff states it."""
        return PREMARKET_START_MINUTE, E_CUTOFF_LAST_MINUTE

    def a_window_is_prefix_of_e(self) -> bool:
        a_start, a_end = self.a_window_minutes
        e_start, e_end = self.e_window_minutes
        return a_start == e_start and a_end - 1 <= e_end

    def declaration(self) -> dict[str, Any]:
        self.validate()
        return {
            "collector_version": LC.COLLECTOR_VERSION,
            "timezone": "America/New_York",
            "session": self.session.isoformat(),
            "rolling_start_et": self.rolling_start.isoformat(),
            "a_cut_et": self.a_cut.isoformat(),
            "e_cut_et": self.e_cut.isoformat(),
            "kiwoom_deadline_et": self.kiwoom_deadline.isoformat(),
            "regular_open_et": self.regular_open.isoformat(),
            "a_window_minutes": list(self.a_window_minutes),
            "e_window_minutes": list(self.e_window_minutes),
            "a_window_is_prefix_of_e": self.a_window_is_prefix_of_e(),
            "a_pass_ordering": "E tick shard last, in E's finalization order",
        }


def a_pass_order(shard_minute: Sequence[str], shard_tick: Sequence[str]) -> tuple[str, ...]:
    """A's rolling/finalization order: everything else first, E's tick shard last.

    The tick shard keeps E's own finalization order inside the tail, so the symbol E will ask
    for first is the one A touched first within the tail - the oldest of the fresh ones, which
    is the ordering that bounds the staleness window stated in the module docstring.
    """
    tail = list(shard_tick)
    tail_set = set(tail)
    head = [symbol for symbol in shard_minute if symbol not in tail_set]
    return tuple(head + tail)


@dataclass(frozen=True)
class StalenessBound:
    """What E's tick shard looks like at E's cut, given when A's pass ended.

    **Two mechanisms cover the two ends, and which one applies depends on the union size.**
    They are stated together because modelling only the second one gives the wrong answer at
    the small end:

    * **A small union finishes early**, leaving a wide gap before E's cut. That gap is exactly
      where E's *own* tick-shard refresh already runs, so when the slack is at least that
      refresh's duration the measured window is preserved by E's existing code and A has
      changed nothing at all.
    * **A large union finishes late**, so E's own refresh is cut short. Then the ordering
      carries it: A read the tick shard in the final stretch of its own pass, so at E's cut
      those symbols are stale by between ``slack`` and ``slack + tick shard pass``, which has
      to be at or under E's own measured window.

    A union that satisfies neither would degrade E, and :attr:`preserves_measured_cost` is
    false for it rather than quietly optimistic.
    """

    a_t1: datetime
    e_cut: datetime
    tick_shard_pass_seconds: float
    measured_window_seconds: float
    #: How long E's own refresh of its tick shard takes; it runs in the slack when it fits.
    e_own_refresh_seconds: float = 0.0

    @property
    def newest_stale_seconds(self) -> float:
        """The slack between A's end and E's cut: the freshest tick-shard symbol's age."""
        return (self.e_cut - self.a_t1).total_seconds()

    @property
    def oldest_stale_seconds(self) -> float:
        return self.newest_stale_seconds + self.tick_shard_pass_seconds

    @property
    def e_own_refresh_fits(self) -> bool:
        """Mechanism (a): the slack is wide enough for E's own refresh to run as it does today."""
        return self.newest_stale_seconds >= self.e_own_refresh_seconds

    @property
    def ordering_bounds_staleness(self) -> bool:
        """Mechanism (b): tick-shard-last keeps every symbol inside E's measured window."""
        return (self.newest_stale_seconds >= 0
                and self.oldest_stale_seconds <= self.measured_window_seconds)

    @property
    def preserves_measured_cost(self) -> bool:
        """Either mechanism is enough; neither one alone covers both ends."""
        return (self.newest_stale_seconds >= 0
                and (self.e_own_refresh_fits or self.ordering_bounds_staleness))

    @property
    def mechanism(self) -> str:
        if not self.preserves_measured_cost:
            return "NONE"
        if self.e_own_refresh_fits:
            return "E_OWN_REFRESH_STILL_FITS"
        return "A_PASS_ORDERED_TICK_SHARD_LAST"

    def declaration(self) -> dict[str, Any]:
        return {"a_t1_et": self.a_t1.isoformat(), "e_cut_et": self.e_cut.isoformat(),
                "newest_stale_s": self.newest_stale_seconds,
                "oldest_stale_s": self.oldest_stale_seconds,
                "e_measured_stale_window_s": self.measured_window_seconds,
                "e_own_refresh_seconds": self.e_own_refresh_seconds,
                "e_own_refresh_fits": self.e_own_refresh_fits,
                "ordering_bounds_staleness": self.ordering_bounds_staleness,
                "mechanism": self.mechanism,
                "preserves_measured_tick_cost": self.preserves_measured_cost}


def a_pass_fits(schedule: SharedSchedule, a_pass_seconds: float) -> bool:
    """Whether A's finalization, started at its cut, ends before E's."""
    return schedule.a_cut + timedelta(seconds=a_pass_seconds) <= schedule.e_cut
