"""In-memory deterministic market data provider for development and tests."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.provider import MarketDataProvider


class PricePattern(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    GAP_UP = "GAP_UP"
    GAP_DOWN = "GAP_DOWN"


class FakeMarketDataProvider(MarketDataProvider):
    """Query explicitly injected fixtures, with helpers for common price shapes."""

    def __init__(
        self,
        daily_bars: Iterable[DailyBar] = (),
        minute_bars: Iterable[MinuteBar] = (),
    ) -> None:
        self._daily = tuple(sorted(daily_bars, key=lambda bar: (bar.trading_date, bar.symbol)))
        self._minute = tuple(sorted(minute_bars, key=lambda bar: (bar.timestamp, bar.symbol)))

    def get_daily_bars(
        self,
        symbols: Sequence[str],
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        wanted = {symbol.strip().upper() for symbol in symbols}
        return [
            bar for bar in self._daily
            if bar.symbol in wanted
            and (start is None or bar.trading_date >= start)
            and (end is None or bar.trading_date <= end)
        ]

    def get_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime | None = None,
        end: datetime | None = None,
        session: MarketSession | None = None,
    ) -> list[MinuteBar]:
        wanted = {symbol.strip().upper() for symbol in symbols}
        return [
            bar for bar in self._minute
            if bar.symbol in wanted
            and (start is None or bar.timestamp >= start)
            and (end is None or bar.timestamp <= end)
            and (session is None or bar.session == session)
        ]

    @staticmethod
    def minute_fixture(
        *,
        symbol: str,
        start: datetime,
        count: int,
        start_price: float = 100.0,
        step: float = 0.25,
        pattern: PricePattern = PricePattern.FLAT,
        volume: int = 1_000,
        session: MarketSession = MarketSession.REGULAR,
        availability_delay: timedelta = timedelta(minutes=1),
    ) -> list[MinuteBar]:
        """Build a deterministic pattern; volume can represent high/low fixtures."""

        direction = {PricePattern.UP: 1.0, PricePattern.DOWN: -1.0}.get(pattern, 0.0)
        gap = {PricePattern.GAP_UP: step * 4, PricePattern.GAP_DOWN: -step * 4}.get(pattern, 0.0)
        bars: list[MinuteBar] = []
        previous = start_price
        for index in range(count):
            timestamp = start + timedelta(minutes=index)
            open_price = previous + (gap if index == 0 else 0.0)
            close_price = open_price + direction * step
            high = max(open_price, close_price) + step / 2
            low = min(open_price, close_price) - step / 2
            observed_at = timestamp + timedelta(minutes=1)
            bars.append(
                MinuteBar(
                    symbol=symbol,
                    timestamp=timestamp,
                    session=session,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close_price,
                    volume=volume,
                    observed_at=observed_at,
                    available_at=observed_at + availability_delay,
                )
            )
            previous = close_price
        return bars
