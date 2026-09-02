from datetime import date, datetime

from app.market.domain import MarketSession
from app.market.fake import FakeMarketDataProvider, PricePattern
from tests.market_fixtures import ET, daily_bar, minute_bar


def test_fake_provider_is_deterministic_and_supports_multiple_symbols() -> None:
    bars = [minute_bar("BBB", 32), minute_bar("AAA", 31), minute_bar("BBB", 31)]
    provider = FakeMarketDataProvider(minute_bars=bars)
    first = provider.get_minute_bars(["AAA", "BBB"])
    second = provider.get_minute_bars(["AAA", "BBB"])
    assert first == second
    assert [(bar.timestamp.minute, bar.symbol) for bar in first] == [
        (31, "AAA"), (31, "BBB"), (32, "BBB")
    ]


def test_fake_provider_date_time_and_session_filters() -> None:
    daily = [daily_bar(day=date(2024, 6, 17)), daily_bar(day=date(2024, 6, 18))]
    minute = [
        minute_bar(minute=31),
        minute_bar(minute=32, session=MarketSession.POSTMARKET),
    ]
    provider = FakeMarketDataProvider(daily_bars=daily, minute_bars=minute)
    assert [bar.trading_date for bar in provider.get_daily_bars(["AAA"], date(2024, 6, 18))] == [
        date(2024, 6, 18)
    ]
    result = provider.get_minute_bars(
        ["AAA"],
        datetime(2024, 6, 18, 9, 31, tzinfo=ET),
        datetime(2024, 6, 18, 9, 32, tzinfo=ET),
        MarketSession.REGULAR,
    )
    assert [bar.timestamp.minute for bar in result] == [31]


def test_explicit_fixture_pattern_and_volume() -> None:
    bars = FakeMarketDataProvider.minute_fixture(
        symbol="AAA",
        start=datetime(2024, 6, 18, 9, 30, tzinfo=ET),
        count=3,
        pattern=PricePattern.UP,
        volume=9_000_000,
    )
    assert [bar.close for bar in bars] == sorted(bar.close for bar in bars)
    assert {bar.volume for bar in bars} == {9_000_000}

