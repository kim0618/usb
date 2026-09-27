"""Pairing DAY T with the next official XNYS session.

G's label lives in a different session from its signal, so the pairing is the study's first
point-in-time claim. The pair is never ``calendar date + 1``: it is the next session of the
exchange calendar (``app.market.calendar.MarketCalendar``, backed by ``exchange_calendars``
XNYS), and the frozen daily grid is required to agree with that calendar session by session.
A Friday pairs with the following Monday, a holiday eve with the first session after the
holiday, and each pair carries the kind of gap it spans so weekends and holidays can be reported
apart from ordinary overnights.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

NORMAL, WEEKEND, HOLIDAY = "normal_overnight", "weekend_gap", "holiday_gap"


@dataclass(frozen=True)
class SessionPair:
    source_session: date
    next_trading_session: date
    kind: str
    calendar_days: int
    source_early_close: bool


def gap_kind(source: date, target: date) -> str:
    """``holiday_gap`` when a weekday between the two sessions is skipped, else weekend/normal."""
    skipped = [source + timedelta(days=k) for k in range(1, (target - source).days)]
    if any(day.weekday() < 5 for day in skipped):
        return HOLIDAY
    if skipped:
        return WEEKEND
    return NORMAL


def build_pairs(grid: Sequence[date], calendar: Any) -> list[SessionPair]:
    """One pair per grid session that has a successor on the grid.

    ``calendar`` needs ``next_trading_day`` and ``is_early_close``. A grid successor that is not
    the calendar's next session is a hole in the grid and stops the study rather than being
    bridged.
    """
    pairs: list[SessionPair] = []
    for source, target in zip(grid, grid[1:]):
        expected = calendar.next_trading_day(source)
        if target != expected:
            raise ValueError(f"grid successor of {source} is {target}, calendar says {expected}")
        pairs.append(SessionPair(source, target, gap_kind(source, target), (target - source).days,
                                 bool(calendar.is_early_close(source))))
    return pairs


def audit(pairs: Sequence[SessionPair], calendar: Any) -> dict[str, Any]:
    """The declared pairing checks, with a few real weekend and holiday examples."""
    sources = [p.source_session for p in pairs]
    failures = {
        "target_not_after_source": sum(1 for p in pairs if not p.next_trading_session > p.source_session),
        "target_not_calendar_next": sum(1 for p in pairs
                                        if calendar.next_trading_day(p.source_session)
                                        != p.next_trading_session),
        "duplicate_pairs": len(pairs) - len({(p.source_session, p.next_trading_session) for p in pairs}),
        "source_paired_twice": len(sources) - len(set(sources)),
    }
    kinds: dict[str, int] = {}
    for p in pairs:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
    examples = {kind: [[p.source_session.isoformat(), p.source_session.strftime("%a"),
                        p.next_trading_session.isoformat(), p.next_trading_session.strftime("%a")]
                       for p in pairs if p.kind == kind][:4]
                for kind in (NORMAL, WEEKEND, HOLIDAY)}
    return {"pairs": len(pairs), "kinds": kinds, "failures": failures,
            "early_close_sources": [p.source_session.isoformat() for p in pairs if p.source_early_close],
            "examples": examples,
            "verdict": "PASS" if not any(failures.values()) else "FAIL"}
