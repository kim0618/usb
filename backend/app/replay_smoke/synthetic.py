"""Deterministic XNYS dataset used only by Replay Smoke Validation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.fake import FakeMarketDataProvider
from app.market.reference import FakeSymbolMetadataProvider, SymbolMetadata

NY = ZoneInfo("America/New_York")
DEFAULT_UNIVERSE = tuple(f"S{index:02d}" for index in range(24))


@dataclass(frozen=True)
class SyntheticReplayDataset:
    trading_days: tuple[date, ...]
    scan_days: tuple[date, ...]
    universe: tuple[str, ...]
    benchmark: str
    daily_bars: tuple[DailyBar, ...]
    minute_bars: tuple[MinuteBar, ...]
    market_data: FakeMarketDataProvider
    metadata: FakeSymbolMetadataProvider
    patterns: tuple[str, ...]

    @classmethod
    def build(cls, *, start_date: date = date(2025, 11, 3), trading_day_count: int = 20,
              universe: tuple[str, ...] = DEFAULT_UNIVERSE,
              calendar: MarketCalendar | None = None) -> "SyntheticReplayDataset":
        if trading_day_count < 1 or len(set(universe)) < 20:
            raise ValueError("smoke requires at least one day and 20 unique symbols")
        calendar = calendar or MarketCalendar()
        days = _sessions_from(calendar, start_date, trading_day_count)
        first_scan = _previous_session(calendar, days[0])
        scan_days = (first_scan,) + days[:-1]
        history_start = first_scan
        for _ in range(25):
            history_start = _previous_session(calendar, history_start)
        history_days = _sessions_through(calendar, history_start, scan_days[-1])
        daily: list[DailyBar] = []
        all_symbols = tuple(sorted(set(universe))) + ("SPY",)
        for day_index, day in enumerate(history_days):
            close_time = calendar.regular_market_close(day)
            assert close_time is not None
            available = close_time + timedelta(minutes=2)
            for symbol_index, symbol in enumerate(all_symbols):
                if symbol == "SPY":
                    close, volume = 450 + day_index * .1, 5_000_000
                elif symbol == "S02":
                    close, volume = 4.0, 300_000
                else:
                    close = 50 + symbol_index * 1.7 + day_index * .08
                    # Rotating deterministic spikes change cross-sectional Top8 daily.
                    boost = 1 + ((symbol_index * 7 + day_index * 11) % 17) / 4
                    volume = int(300_000 * boost)
                daily.append(DailyBar(symbol=symbol, trading_date=day, open=close - .1,
                    high=close + .5, low=close - .5, close=close, volume=volume,
                    observed_at=close_time + timedelta(minutes=1), available_at=available))
        minute: list[MinuteBar] = []
        for day_index, day in enumerate(days):
            session = calendar.session(day)
            assert session is not None
            for symbol_index, symbol in enumerate(sorted(universe)):
                base = 50 + symbol_index * 1.7 + (len(history_days) + day_index) * .08
                for offset in (-90, -60):
                    minute.append(_minute(symbol, session.market_open + timedelta(minutes=offset),
                                          base * 1.05, MarketSession.PREMARKET, 50_000))
                for offset in range(18):
                    price = base if offset < 15 else base + 2 + (offset - 15) * .3
                    high = price + (.6 if offset == 14 else .2)
                    minute.append(_minute(symbol, session.market_open + timedelta(minutes=offset),
                                          price, MarketSession.REGULAR, 10_000, high=high,
                                          low=price - .3))
                review = session.market_close - timedelta(minutes=10)
                minute.append(_minute(symbol, review, base + 3, MarketSession.REGULAR, 12_000))
                minute.append(_minute(symbol, review + timedelta(minutes=2), base + 3.1,
                                      MarketSession.REGULAR, 12_000))
        metadata_time = datetime.combine(history_start, datetime.min.time(), tzinfo=NY)
        metadata = [SymbolMetadata(symbol=symbol, company_name=f"{symbol} Synthetic",
                    market_cap=100_000_000 if symbol in {"S00", "S01"} else 1_000_000_000,
                    exchange="NASDAQ", observed_at=metadata_time, available_at=metadata_time)
                    for symbol in sorted(universe)]
        patterns = ("MOMENTUM", "PREMARKET_REJECTION", "OPENING_REJECTION", "STOP_LOSS",
                    "TRAILING_WINNER", "PYRAMIDING", "OVERNIGHT_HOLD", "OVERNIGHT_REDUCE",
                    "OVERNIGHT_REJECT", "DAY2_MANDATORY_EXIT", "AMBIGUOUS_BAR",
                    "MISSING_OPENING_BAR", "MISSING_NEXT_BAR", "EARLY_CLOSE",
                    "WEEKEND_HOLIDAY_ROLLOVER")
        return cls(days, scan_days, tuple(sorted(set(universe))), "SPY", tuple(daily), tuple(minute),
                   FakeMarketDataProvider(daily, minute), FakeSymbolMetadataProvider(metadata), patterns)


def _minute(symbol: str, timestamp: datetime, price: float, session: MarketSession,
            volume: int, *, high: float | None = None, low: float | None = None) -> MinuteBar:
    completed = timestamp + timedelta(minutes=1)
    return MinuteBar(symbol=symbol, timestamp=timestamp, open=price, high=high or price + .2,
                     low=low or price - .2, close=price, volume=volume, session=session,
                     observed_at=completed, available_at=completed)


def _sessions_from(calendar: MarketCalendar, start: date, count: int) -> tuple[date, ...]:
    cursor = start
    while not calendar.is_trading_day(cursor):
        cursor += timedelta(days=1)
    result: list[date] = []
    while len(result) < count:
        if calendar.is_trading_day(cursor):
            result.append(cursor)
        cursor += timedelta(days=1)
    return tuple(result)


def _sessions_through(calendar: MarketCalendar, start: date, end: date) -> tuple[date, ...]:
    result, cursor = [], start
    while cursor <= end:
        if calendar.is_trading_day(cursor):
            result.append(cursor)
        cursor += timedelta(days=1)
    return tuple(result)


def _previous_session(calendar: MarketCalendar, day: date) -> date:
    cursor = day - timedelta(days=1)
    while not calendar.is_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor
