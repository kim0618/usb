"""Provider contract used by scanner, recorder, and future strategy code."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class ExchangeAwareProvider(Protocol):
    """A provider whose lookups are routed per listing exchange.

    Callers that own an exchange authority (the scanner universe, an approved entry
    candidate) bind it before querying, so the symbol reaches the venue it lists on.
    """

    def bind_exchange(self, symbol: str, exchange: str) -> None:
        """Bind ``symbol`` to ``exchange``; an unsupported value must fail closed."""
