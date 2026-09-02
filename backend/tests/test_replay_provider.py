from datetime import datetime

from app.market.domain import MarketSession
from app.market.replay import ReplayMarketDataProvider
from tests.market_fixtures import ET, minute_bar


def test_replay_hides_future_available_data() -> None:
    delayed = minute_bar(minute=31, available_minute=33)
    replay = ReplayMarketDataProvider(
        datetime(2024, 6, 18, 9, 32, tzinfo=ET), minute_bars=[delayed]
    )
    assert replay.get_minute_bars(["AAA"]) == []
    assert replay.at(datetime(2024, 6, 18, 9, 33, tzinfo=ET)).get_minute_bars(["AAA"]) == [
        delayed
    ]


def test_replay_order_filters_and_deterministic_rerun() -> None:
    bars = [
        minute_bar("BBB", 32, session=MarketSession.POSTMARKET),
        minute_bar("AAA", 32),
        minute_bar("AAA", 31),
    ]
    replay = ReplayMarketDataProvider(
        datetime(2024, 6, 18, 12, 0, tzinfo=ET), minute_bars=bars
    )
    first = replay.get_minute_bars(
        ["AAA", "BBB"],
        datetime(2024, 6, 18, 9, 31, tzinfo=ET),
        datetime(2024, 6, 18, 9, 32, tzinfo=ET),
        MarketSession.REGULAR,
    )
    second = replay.get_minute_bars(
        ["AAA", "BBB"],
        datetime(2024, 6, 18, 9, 31, tzinfo=ET),
        datetime(2024, 6, 18, 9, 32, tzinfo=ET),
        MarketSession.REGULAR,
    )
    assert first == second
    assert [(bar.timestamp.minute, bar.symbol) for bar in first] == [(31, "AAA"), (32, "AAA")]

