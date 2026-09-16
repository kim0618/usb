"""Massive minute aggregates under the documented timestamp and XNYS session contract.

Massive documents the Custom Bars ``t`` field as "The Unix millisecond timestamp for
the start of the aggregate window", so a 1-minute bar covers
``[bar_start, bar_start + 1 minute)``. ``MinuteBar.bar_end`` is exposed only as the
candidate replay availability boundary. It is not an ``available_at``: Stocks Basic is
documented as End of Day data and its publication latency is UNKNOWN.

Sessions come from the XNYS calendar in America/New_York. The aggregate payload carries
no session label, and a provider label would not be authority here anyway.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
import math
from typing import Any
from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar, TradingSessionWindow


ET = ZoneInfo("America/New_York")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
BAR_INTERVAL = timedelta(minutes=1)
PREMARKET_START = time(4, 0)
POSTMARKET_END = time(20, 0)


class MalformedPayload(ValueError):
    """A row whose shape cannot be trusted. Messages name fields, never values."""


class SessionPart(str, Enum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    POSTMARKET = "POSTMARKET"
    OUTSIDE = "OUTSIDE"


@dataclass(frozen=True)
class MinuteBar:
    bar_start: datetime  # UTC, from ``t``
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    vwap: float | None = None
    trades: float | None = None

    @property
    def bar_start_et(self) -> datetime:
        return self.bar_start.astimezone(ET)

    @property
    def bar_end(self) -> datetime:
        """Candidate replay availability boundary (bar_start + 1 minute); NOT adopted."""
        return self.bar_start + BAR_INTERVAL

    @property
    def ohlc(self) -> tuple[float, float, float, float] | None:
        if self.open is None or self.high is None or self.low is None or self.close is None:
            return None
        return self.open, self.high, self.low, self.close

    @property
    def has_null_ohlcv(self) -> bool:
        return self.ohlc is None or self.volume is None


def epoch_ms(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(milliseconds=1)


def parse_timestamp_ms(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedPayload("aggregate field t must be non-negative integer Unix milliseconds")
    return EPOCH + timedelta(milliseconds=value)


def _number(row: Mapping[str, Any], name: str) -> float | None:
    value = row.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MalformedPayload(f"aggregate field {name} must be a finite number or null")
    return float(value)


def parse_bar(row: Any) -> MinuteBar:
    """One ``results[]`` entry. Null OHLCV is kept for validation; a wrong type fails."""
    if not isinstance(row, Mapping):
        raise MalformedPayload("aggregate result must be an object")
    if "t" not in row:
        raise MalformedPayload("aggregate field t is missing")
    return MinuteBar(
        bar_start=parse_timestamp_ms(row["t"]),
        open=_number(row, "o"), high=_number(row, "h"), low=_number(row, "l"),
        close=_number(row, "c"), volume=_number(row, "v"),
        vwap=_number(row, "vw"), trades=_number(row, "n"),
    )


def extended_bounds(window: TradingSessionWindow) -> tuple[datetime, datetime]:
    """04:00 ET and 20:00 ET on the session date."""
    return (datetime.combine(window.session_date, PREMARKET_START, tzinfo=ET),
            datetime.combine(window.session_date, POSTMARKET_END, tzinfo=ET))


def classify(bar_start: datetime, window: TradingSessionWindow) -> SessionPart:
    premarket_start, postmarket_end = extended_bounds(window)
    if premarket_start <= bar_start < window.market_open:
        return SessionPart.PREMARKET
    if window.market_open <= bar_start < window.market_close:
        return SessionPart.REGULAR
    if window.market_close <= bar_start < postmarket_end:
        return SessionPart.POSTMARKET
    return SessionPart.OUTSIDE


def latest_completed_session(calendar: MarketCalendar, now: datetime) -> TradingSessionWindow:
    """The most recent XNYS session whose extended hours (to 20:00 ET) have ended.

    A session is in progress from 04:00 ET until 20:00 ET, so today's session qualifies
    only at or after 20:00 ET. Weekends and holidays fall back to the prior session.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    day = now.astimezone(ET).date()
    window = calendar.session(day)
    if window is None or now < extended_bounds(window)[1]:
        window = calendar.session(calendar.previous_trading_day(day))
    if window is None:
        raise ValueError("no completed XNYS session found")
    return window


def latest_completed_sessions(calendar: MarketCalendar, now: datetime,
                              count: int) -> tuple[TradingSessionWindow, ...]:
    """The ``count`` most recent completed XNYS sessions, oldest first."""
    if count < 1:
        raise ValueError("session count must be positive")
    windows = [latest_completed_session(calendar, now)]
    while len(windows) < count:
        previous = calendar.session(calendar.previous_trading_day(windows[-1].session_date))
        if previous is None:
            raise ValueError("no completed XNYS session found")
        windows.append(previous)
    return tuple(reversed(windows))


def request_range(window: TradingSessionWindow) -> tuple[datetime, datetime]:
    """The whole ET calendar date, so rows outside 04:00-20:00 ET are observed too.

    Whether ``to`` is inclusive is UNKNOWN; the last millisecond of the date makes the
    answer irrelevant.
    """
    start = datetime.combine(window.session_date, time(0, 0), tzinfo=ET)
    return start, start + timedelta(days=1) - timedelta(milliseconds=1)
