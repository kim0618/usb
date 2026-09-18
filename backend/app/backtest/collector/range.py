"""What range the collector may actually ask for, and which XNYS sessions it expects.

Stocks Basic does not publish the session that is running or that has just finished:
a single-date request for today answers HTTP 403, and a long range that contains today
answers HTTP 200 and quietly stops a day early. So the collector's end is the previous
trading day, always, and the requested range and the effective range are both reported.

Session windows come from the XNYS calendar in America/New_York, early closes included.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.backtest.collector.errors import EmptyRange, SameDayRequest
from app.integrations.massive.long_range import YearRange, one_year_earlier
from app.integrations.massive.minute_bars import ET
from app.market.calendar import MarketCalendar, TradingSessionWindow


@dataclass(frozen=True)
class CollectionRange:
    """A requested range, the range that may be collected, and its expected sessions."""

    requested_start: date
    requested_end: date
    last_collectable_session: date  # XNYS previous trading day, in ET, at ``now``
    window: YearRange  # the expected sessions, oldest first

    @property
    def effective_start(self) -> date:
        return self.window.start.session_date

    @property
    def effective_end(self) -> date:
        return self.window.end.session_date

    @property
    def sessions(self) -> tuple[TradingSessionWindow, ...]:
        return self.window.sessions

    @property
    def expected_sessions(self) -> int:
        return len(self.window.sessions)

    @property
    def end_clamped(self) -> bool:
        return self.requested_end > self.effective_end

    @property
    def start_moved(self) -> bool:
        return self.requested_start < self.effective_start

    @property
    def request_start(self) -> datetime:
        return self.window.request_start

    @property
    def request_end(self) -> datetime:
        return self.window.request_end

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(sorted({window.session_date.year for window in self.window.sessions}))


def previous_trading_day(calendar: MarketCalendar, now: datetime) -> date:
    """The last XNYS session strictly before today in ET: the newest date Basic publishes."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return calendar.previous_trading_day(now.astimezone(ET).date())


def sessions_between(calendar: MarketCalendar, start: date,
                     end: date) -> tuple[TradingSessionWindow, ...]:
    if end < start:
        return ()
    windows: list[TradingSessionWindow] = []
    day = start if calendar.is_trading_day(start) else calendar.next_trading_day(start)
    while day <= end:
        window = calendar.session(day)
        if window is None:  # pragma: no cover - is_trading_day already agreed
            raise ValueError(f"{day} is not an XNYS session")
        windows.append(window)
        day = calendar.next_trading_day(day)
    return tuple(windows)


def plan_range(calendar: MarketCalendar, *, start: date, end: date,
               now: datetime) -> CollectionRange:
    """Clamp the request to T-1 and list every XNYS session the collection must contain."""
    if end < start:
        raise EmptyRange(f"end {end} is before start {start}")
    last_collectable = previous_trading_day(calendar, now)
    effective_end = min(end, last_collectable)
    today = now.astimezone(ET).date()
    if effective_end >= today:  # pragma: no cover - previous_trading_day is strictly earlier
        raise SameDayRequest(f"effective end {effective_end} is not before today {today} in ET")
    windows = sessions_between(calendar, start, effective_end)
    if not windows:
        raise SameDayRequest(
            f"no published XNYS session between {start} and {end}: Stocks Basic publishes "
            f"through {last_collectable} and the collector never requests a later session")
    return CollectionRange(requested_start=start, requested_end=end,
                           last_collectable_session=last_collectable, window=YearRange(windows))


def plan_years(calendar: MarketCalendar, *, years: int, now: datetime) -> CollectionRange:
    """The ``years`` calendar years that end at the last publishable session."""
    if years < 1:
        raise EmptyRange("--years must be at least 1")
    end = previous_trading_day(calendar, now)
    start = end
    for _ in range(years):
        start = one_year_earlier(start)
    return plan_range(calendar, start=start + timedelta(days=1), end=end, now=now)


def assert_publishable(range_: CollectionRange, now: datetime) -> None:
    """Last guard before any HTTP request: the range must end before today in ET."""
    today = now.astimezone(ET).date()
    if range_.effective_end >= today:
        raise SameDayRequest(
            f"effective end {range_.effective_end} is today or later in ET ({today})")


def missing_sessions(range_: CollectionRange,
                     present: Sequence[date]) -> tuple[date, ...]:
    seen = set(present)
    return tuple(window.session_date for window in range_.sessions if window.session_date not in seen)
