"""Explicit orchestration from a provider into Parquet storage."""

from dataclasses import dataclass
from datetime import date, datetime

from app.core.exceptions import DataError
from app.market.domain import MarketSession
from app.market.provider import MarketDataProvider
from app.recorder.parquet import ParquetMarketDataStorage


@dataclass(frozen=True)
class RecordingResult:
    daily_bars: int = 0
    minute_bars: int = 0


class MarketRecorder:
    def __init__(self, provider: MarketDataProvider, storage: ParquetMarketDataStorage) -> None:
        self.provider = provider
        self.storage = storage

    def record_daily(self, symbols: list[str], start: date, end: date) -> RecordingResult:
        try:
            bars = self.provider.get_daily_bars(symbols, start, end)
            return RecordingResult(daily_bars=self.storage.write_daily_bars(bars))
        except DataError:
            raise
        except Exception as exc:
            raise DataError("Unable to record daily market data") from exc

    def record_minute(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        session: MarketSession | None = None,
    ) -> RecordingResult:
        try:
            bars = self.provider.get_minute_bars(symbols, start, end, session)
            return RecordingResult(minute_bars=self.storage.write_minute_bars(bars))
        except DataError:
            raise
        except Exception as exc:
            raise DataError("Unable to record minute market data") from exc

