"""Provider contract used by scanner, recorder, and future strategy code."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime

from app.market.domain import DailyBar, MarketSession, MinuteBar


class MarketDataProvider(ABC):
    """Synchronous, deterministic historical bar query contract."""

    @abstractmethod
    def get_daily_bars(
        self,
        symbols: Sequence[str],
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        """Return bars ordered by trading date and symbol, inclusive bounds."""

    @abstractmethod
    def get_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime | None = None,
        end: datetime | None = None,
        session: MarketSession | None = None,
    ) -> list[MinuteBar]:
        """Return bars ordered by timestamp and symbol, inclusive bounds."""

