"""Session boundaries for one US trading date, as Strategy B's own input.

The caller derives boundaries from the exchange calendar (early closes included) and
passes them in. This module does not import A's ``SessionPolicy`` or the calendar, so B's
session labels cannot drift when A's policy changes, and B stays testable with plain
fixtures.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.strategy_b.models import BAR_INTERVAL, Session


ET = ZoneInfo("America/New_York")
PREMARKET_START = time(4, 0)
AFTER_END = time(20, 0)


class AggregationScope(StrEnum):
    """Which part of the trading day a cumulative or windowed feature may look at."""

    SESSION_LOCAL = "SESSION_LOCAL"
    """Only bars of the session the as-of time is in (PREMARKET, REGULAR or AFTER)."""
    PREMARKET_AND_REGULAR = "PREMARKET_AND_REGULAR"
    """From 04:00 ET while in PREMARKET or REGULAR; AFTER stays session-local."""
    EXTENDED_DAY = "EXTENDED_DAY"
    """From 04:00 ET regardless of the current session."""


@dataclass(frozen=True, slots=True)
class SessionBoundaries:
    session_date: date
    premarket_start: datetime
    regular_open: datetime
    regular_close: datetime
    after_close: datetime

    def __post_init__(self) -> None:
        moments = (self.premarket_start, self.regular_open, self.regular_close, self.after_close)
        for moment in moments:
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("session boundaries must be timezone-aware")
        if not self.premarket_start <= self.regular_open < self.regular_close <= self.after_close:
            raise ValueError("session boundaries must be ordered")

    @classmethod
    def standard(cls, session_date: date, *, regular_open: time = time(9, 30),
                 regular_close: time = time(16, 0)) -> "SessionBoundaries":
        """04:00 / open / close / 20:00 ET. Pass ``regular_close`` for an early close."""
        return cls(
            session_date=session_date,
            premarket_start=datetime.combine(session_date, PREMARKET_START, tzinfo=ET),
            regular_open=datetime.combine(session_date, regular_open, tzinfo=ET),
            regular_close=datetime.combine(session_date, regular_close, tzinfo=ET),
            after_close=datetime.combine(session_date, AFTER_END, tzinfo=ET),
        )

    def classify(self, moment: datetime) -> Session:
        """Label a bar-open time or an as-of time. Intervals are half-open."""
        if self.premarket_start <= moment < self.regular_open:
            return Session.PREMARKET
        if self.regular_open <= moment < self.regular_close:
            return Session.REGULAR
        if self.regular_close <= moment < self.after_close:
            return Session.AFTER
        return Session.OUTSIDE

    def session_start(self, session: Session) -> datetime | None:
        return {
            Session.PREMARKET: self.premarket_start,
            Session.REGULAR: self.regular_open,
            Session.AFTER: self.regular_close,
            Session.OUTSIDE: None,
        }[session]

    def session_end(self, session: Session) -> datetime | None:
        return {
            Session.PREMARKET: self.regular_open,
            Session.REGULAR: self.regular_close,
            Session.AFTER: self.after_close,
            Session.OUTSIDE: None,
        }[session]

    def scope_start(self, as_of: datetime, scope: AggregationScope) -> datetime | None:
        """First moment a feature in ``scope`` may aggregate from, or None outside the day."""
        session = self.classify(as_of)
        if scope is AggregationScope.EXTENDED_DAY:
            return self.premarket_start if as_of >= self.premarket_start else None
        if session is Session.OUTSIDE:
            return None
        if scope is AggregationScope.PREMARKET_AND_REGULAR and session is not Session.AFTER:
            return self.premarket_start
        return self.session_start(session)

    def elapsed_minute_slots(self, start: datetime, as_of: datetime) -> int:
        """Minute slots opening at or after ``start`` whose bar would be available by ``as_of``.

        The slots are capped at the after-hours close, so a late as-of time does not count
        minutes in which no bar can exist.
        """
        if as_of <= start:
            return 0
        observable = (as_of - start) // BAR_INTERVAL
        possible = max(timedelta(0), self.after_close - start) // BAR_INTERVAL
        return int(min(observable, possible))
