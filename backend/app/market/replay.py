"""Point-in-time-safe deterministic replay provider."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from app.core.exceptions import DataError
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.recorder.parquet import ParquetMarketDataStorage


class ReplayMarketDataProvider(MarketDataProvider):
    """Expose only bars available at an explicit immutable replay time."""

    def __init__(
        self,
        current_time: datetime,
        *,
        storage: ParquetMarketDataStorage | None = None,
        daily_bars: Iterable[DailyBar] = (),
        minute_bars: Iterable[MinuteBar] = (),
    ) -> None:
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise DataError("Replay current_time must be timezone-aware")
        self.current_time = current_time
        self._storage = storage
        self._daily = tuple(sorted(daily_bars, key=lambda bar: (bar.trading_date, bar.symbol)))
        self._minute = tuple(sorted(minute_bars, key=lambda bar: (bar.timestamp, bar.symbol)))

    def at(self, current_time: datetime) -> "ReplayMarketDataProvider":
        """Return a new replay view; the existing view remains unchanged."""

        return ReplayMarketDataProvider(
            current_time,
            storage=self._storage,
            daily_bars=self._daily,
            minute_bars=self._minute,
        )

    def get_daily_bars(
        self,
        symbols: Sequence[str],
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        bars = (
            self._storage.read_daily_bars(symbols, start, end)
            if self._storage else self._filter_daily(symbols, start, end)
        )
        return [bar for bar in bars if bar.available_at <= self.current_time]

    def get_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime | None = None,
        end: datetime | None = None,
        session: MarketSession | None = None,
    ) -> list[MinuteBar]:
        bars = (
            self._storage.read_minute_bars(symbols, start, end, session)
            if self._storage else self._filter_minute(symbols, start, end, session)
        )
        return [bar for bar in bars if bar.available_at <= self.current_time]

    def _filter_daily(
        self, symbols: Sequence[str], start: date | None, end: date | None
    ) -> list[DailyBar]:
        wanted = {symbol.strip().upper() for symbol in symbols}
        return [
            bar for bar in self._daily
            if bar.symbol in wanted
            and (start is None or bar.trading_date >= start)
            and (end is None or bar.trading_date <= end)
        ]

    def _filter_minute(
        self,
        symbols: Sequence[str],
        start: datetime | None,
        end: datetime | None,
        session: MarketSession | None,
    ) -> list[MinuteBar]:
        wanted = {symbol.strip().upper() for symbol in symbols}
        return [
            bar for bar in self._minute
            if bar.symbol in wanted
            and (start is None or bar.timestamp >= start)
            and (end is None or bar.timestamp <= end)
            and (session is None or bar.session == session)
        ]
