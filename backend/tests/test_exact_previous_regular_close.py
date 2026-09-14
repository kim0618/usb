"""Exact previous close comes only from the predecessor's final REGULAR minute."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.fake import FakeMarketDataProvider
from app.integrations.kiwoom.mapping import map_daily_bar, map_minute_bar
from app.services.entry_management_runtime import (
    EntryLifecycleService, PremarketInvalidField,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
FIXTURE = json.loads((Path(__file__).parent / "fixtures" /
                      "kiwoom_exact_previous_close.json").read_text())


def minute(symbol: str, timestamp: datetime, close: float, *,
           session: MarketSession = MarketSession.REGULAR,
           available_at: datetime | None = None) -> MinuteBar:
    completed = timestamp + timedelta(minutes=1)
    return MinuteBar(
        symbol=symbol, timestamp=timestamp, session=session,
        open=close, high=close, low=close, close=close, volume=1000,
        observed_at=completed, available_at=available_at or completed,
    )


def exact_close(entry: date, bars: list[MinuteBar], *, symbol: str = "AAA",
                as_of: datetime | None = None) -> Decimal | None:
    calendar = MarketCalendar()
    evaluation = as_of or datetime.combine(entry, datetime.min.time(), ET).replace(
        hour=9, minute=31)
    provider = FakeMarketDataProvider(minute_bars=bars)
    owner = SimpleNamespace(calendar=calendar)
    return EntryLifecycleService._previous_regular_close(
        owner, symbol, entry, provider, evaluation)


def final_timestamp(entry: date) -> datetime:
    calendar = MarketCalendar()
    previous = calendar.previous_trading_day(entry)
    window = calendar.session(previous)
    assert window is not None
    return window.market_close - timedelta(minutes=1)


def historical_daily(symbol: str, entry: date, count: int = 20) -> list[DailyBar]:
    calendar = MarketCalendar()
    day = calendar.previous_trading_day(calendar.previous_trading_day(entry))
    result: list[DailyBar] = []
    for index in range(count):
        close = datetime.combine(day, datetime.min.time(), ET).replace(hour=16)
        result.append(DailyBar(
            symbol=symbol, trading_date=day, open=100, high=101, low=99, close=100,
            volume=1_000_000 + index, observed_at=close, available_at=close,
        ))
        day = calendar.previous_trading_day(day)
    return result


def test_normal_session_uses_exact_1559_regular_close() -> None:
    entry = date(2026, 9, 14)
    target = final_timestamp(entry)
    assert target == datetime(2026, 9, 11, 15, 59, tzinfo=ET)
    assert exact_close(entry, [minute("AAA", target, 123.45)]) == Decimal("123.45")


def test_early_close_uses_calendar_1259_not_a_hard_coded_1559() -> None:
    entry = date(2026, 11, 30)
    target = final_timestamp(entry)
    assert target == datetime(2026, 11, 27, 12, 59, tzinfo=ET)
    assert exact_close(entry, [minute("AAA", target, 88.5)]) == Decimal("88.5")


@pytest.mark.parametrize(("symbol", "expected"), [("META", "648.23"),
                                                     ("ORCL", "150.27")])
def test_invalid_daily_close_never_blocks_exact_regular_minute_authority(
        symbol: str, expected: str) -> None:
    raw = FIXTURE[symbol]
    as_of = datetime(2026, 9, 14, 9, 31, tzinfo=ET)
    with pytest.raises(ValidationError, match="low must be less than"):
        map_daily_bar(symbol, raw["daily"], as_of)
    final = map_minute_bar(symbol, raw["final_regular_minute"], as_of)
    provider = FakeMarketDataProvider(
        daily_bars=historical_daily(symbol, as_of.date()),
        minute_bars=[final, minute(symbol, datetime(2026, 9, 14, 8, tzinfo=ET), 155,
                                  session=MarketSession.PREMARKET)],
    )
    built = EntryLifecycleService._premarket_context(
        SimpleNamespace(calendar=MarketCalendar()), symbol, as_of.date(),
        provider.get_minute_bars([symbol], datetime(2026, 9, 14, 4, tzinfo=ET), as_of),
        provider, as_of)
    assert built.diagnostic.previous_close == Decimal(expected)
    assert built.diagnostic.invalid_field is None
    assert built.diagnostic.historical_average_daily_volume == Decimal("1000009.5")


@pytest.mark.parametrize("bars", [
    [],
    [minute("AAA", datetime(2026, 9, 11, 15, 58, tzinfo=ET), 99)],
    [minute("AAA", datetime(2026, 9, 11, 16, 0, tzinfo=ET), 99,
            session=MarketSession.POSTMARKET)],
    [minute("AAA", datetime(2026, 9, 10, 15, 59, tzinfo=ET), 99)],
])
def test_missing_final_bar_never_uses_last_postmarket_or_older_fallback(
        bars: list[MinuteBar]) -> None:
    assert exact_close(date(2026, 9, 14), bars) is None


def test_future_or_unavailable_final_bar_is_rejected() -> None:
    entry = date(2026, 9, 14)
    target = final_timestamp(entry)
    as_of = datetime(2026, 9, 14, 9, 31, tzinfo=ET)
    bar = minute("AAA", target, 100, available_at=as_of + timedelta(seconds=1))
    assert exact_close(entry, [bar], as_of=as_of) is None


@pytest.mark.parametrize(("entry", "previous"), [
    (date(2026, 9, 8), date(2026, 9, 4)),
    (date(2026, 9, 14), date(2026, 9, 11)),
])
def test_holiday_and_weekend_use_only_exact_previous_session(entry: date,
                                                             previous: date) -> None:
    target = final_timestamp(entry)
    assert target.date() == previous
    assert exact_close(entry, [minute("AAA", target, 101)]) == Decimal("101.0")


def test_dst_boundaries_preserve_et_close_and_change_utc_offset() -> None:
    before = final_timestamp(date(2026, 3, 9))
    after = final_timestamp(date(2026, 3, 10))
    assert (before.hour, before.minute, before.astimezone(UTC).hour) == (15, 59, 20)
    assert (after.hour, after.minute, after.astimezone(UTC).hour) == (15, 59, 19)


def test_context_persists_exact_missing_reason_even_with_daily_history() -> None:
    entry = date(2026, 9, 14)
    as_of = datetime(2026, 9, 14, 9, 31, tzinfo=ET)
    premarket = minute("AAA", datetime(2026, 9, 14, 8, tzinfo=ET), 105,
                       session=MarketSession.PREMARKET)
    provider = FakeMarketDataProvider(
        daily_bars=historical_daily("AAA", entry), minute_bars=[premarket])
    built = EntryLifecycleService._premarket_context(
        SimpleNamespace(calendar=MarketCalendar()), "AAA", entry, [premarket], provider, as_of)
    assert built.context.previous_regular_close == 0
    assert built.diagnostic.invalid_field is PremarketInvalidField.NO_EXACT_PREVIOUS_CLOSE


class RecordingProvider(FakeMarketDataProvider):
    def __init__(self, bars: list[MinuteBar]) -> None:
        super().__init__(minute_bars=bars)
        self.minute_queries: list[tuple[datetime | None, datetime | None,
                                        MarketSession | None]] = []

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        self.minute_queries.append((start, end, session))
        return super().get_minute_bars(symbols, start, end, session)


def test_previous_close_query_requests_only_the_calendar_final_minute_window() -> None:
    entry = date(2026, 9, 14)
    target = final_timestamp(entry)
    provider = RecordingProvider([minute("AAA", target, 100)])
    result = EntryLifecycleService._previous_regular_close(
        SimpleNamespace(calendar=MarketCalendar()), "AAA", entry, provider,
        datetime(2026, 9, 14, 9, 31, tzinfo=ET))
    assert result == Decimal("100.0")
    assert provider.minute_queries == [(target, target + timedelta(minutes=1),
                                        MarketSession.REGULAR)]
