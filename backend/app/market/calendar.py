"""NYSE regular-session calendar utilities."""

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.core.config import get_settings


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    market_open: datetime
    market_close: datetime
    is_early_close: bool


class MarketCalendar:
    """Timezone-aware NYSE calendar backed by official exchange rules."""

    def __init__(self, timezone_name: str | None = None) -> None:
        self._calendar = xcals.get_calendar("XNYS")
        self.timezone = ZoneInfo(timezone_name or get_settings().market_timezone)

    @staticmethod
    def _label(day: date) -> pd.Timestamp:
        return pd.Timestamp(day)

    def is_trading_day(self, day: date) -> bool:
        return bool(self._calendar.is_session(self._label(day)))

    def is_holiday(self, day: date) -> bool:
        return day.weekday() < 5 and not self.is_trading_day(day)

    def session(self, day: date) -> MarketSession | None:
        label = self._label(day)
        if not self._calendar.is_session(label):
            return None
        market_open = self._calendar.session_open(label).to_pydatetime().astimezone(self.timezone)
        market_close = self._calendar.session_close(label).to_pydatetime().astimezone(self.timezone)
        return MarketSession(
            session_date=day,
            market_open=market_open,
            market_close=market_close,
            is_early_close=market_close.timetz().replace(tzinfo=None) < time(16, 0),
        )

    def regular_market_open(self, day: date) -> datetime | None:
        session = self.session(day)
        return session.market_open if session else None

    def regular_market_close(self, day: date) -> datetime | None:
        session = self.session(day)
        return session.market_close if session else None

    def is_early_close(self, day: date) -> bool:
        session = self.session(day)
        return bool(session and session.is_early_close)

