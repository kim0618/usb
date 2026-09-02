from datetime import date, datetime
from pathlib import Path

from app.market.fake import FakeMarketDataProvider
from app.market.replay import ReplayMarketDataProvider
from app.recorder.market_recorder import MarketRecorder
from app.recorder.parquet import ParquetMarketDataStorage
from tests.market_fixtures import ET, daily_bar, minute_bar


def test_fake_recorder_parquet_replay_round_trip(tmp_path: Path) -> None:
    daily = [daily_bar("AAA")]
    minute = [minute_bar("AAA", 32), minute_bar("AAA", 31, available_minute=33)]
    fake = FakeMarketDataProvider(daily_bars=daily, minute_bars=minute)
    storage = ParquetMarketDataStorage(tmp_path, source="fake")
    recorder = MarketRecorder(fake, storage)
    assert recorder.record_daily(["AAA"], date(2024, 6, 18), date(2024, 6, 18)).daily_bars == 1
    assert recorder.record_minute(
        ["AAA"],
        datetime(2024, 6, 18, 9, 31, tzinfo=ET),
        datetime(2024, 6, 18, 9, 32, tzinfo=ET),
    ).minute_bars == 2

    early = ReplayMarketDataProvider(datetime(2024, 6, 18, 9, 32, tzinfo=ET), storage=storage)
    assert early.get_minute_bars(["AAA"]) == []
    replay = early.at(datetime(2024, 6, 18, 17, 0, tzinfo=ET))
    first = replay.get_minute_bars(["AAA"])
    second = replay.get_minute_bars(["AAA"])
    assert first == second
    assert len(first) == 2
    assert [bar.timestamp.minute for bar in first] == [31, 32]
    assert first[0].session == minute[1].session
    assert first[0].observed_at == minute[1].observed_at
    assert first[0].available_at == minute[1].available_at
    assert replay.get_daily_bars(["AAA"])[0].model_dump() == daily[0].model_dump()
