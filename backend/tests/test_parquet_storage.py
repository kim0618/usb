from datetime import date, timedelta
from pathlib import Path

import pytest

from app.core.exceptions import DataError
from app.recorder.parquet import ParquetMarketDataStorage
from tests.market_fixtures import daily_bar, minute_bar


def test_daily_write_read_merge_and_duplicate_replace(tmp_path: Path) -> None:
    storage = ParquetMarketDataStorage(tmp_path, source="test")
    original = daily_bar(day=date(2024, 6, 18))
    replacement = original.model_copy(update={"close": 101.5})
    storage.write_daily_bars([original])
    storage.write_daily_bars([daily_bar(day=date(2024, 6, 17)), replacement])
    result = storage.read_daily_bars(["AAA"])
    assert len(result) == 2
    assert [bar.trading_date for bar in result] == [date(2024, 6, 17), date(2024, 6, 18)]
    assert result[-1].close == 101.5


def test_minute_write_read_order_duplicate_and_timezone(tmp_path: Path) -> None:
    storage = ParquetMarketDataStorage(tmp_path)
    later = minute_bar(minute=32)
    earlier = minute_bar(minute=31)
    replacement = earlier.model_copy(update={"close": 101.5})
    storage.write_minute_bars([later, earlier])
    storage.write_minute_bars([replacement])
    result = storage.read_minute_bars(["AAA"])
    assert len(result) == 2
    assert [bar.timestamp.minute for bar in result] == [31, 32]
    assert result[0].close == 101.5
    assert all(bar.timestamp.tzinfo is not None for bar in result)
    assert result[0].observed_at == earlier.observed_at
    assert result[0].available_at == earlier.available_at


@pytest.mark.parametrize("kind", ["daily", "minute"])
def test_correction_available_at_may_advance_but_not_move_backward(tmp_path: Path, kind: str) -> None:
    storage = ParquetMarketDataStorage(tmp_path)
    original = daily_bar() if kind == "daily" else minute_bar()
    write = storage.write_daily_bars if kind == "daily" else storage.write_minute_bars
    write([original])
    forward = original.model_copy(update={
        "observed_at": original.observed_at + timedelta(minutes=1),
        "available_at": original.available_at + timedelta(minutes=1),
    })
    write([forward])
    backward = original.model_copy(update={
        "observed_at": original.observed_at - timedelta(minutes=1),
        "available_at": original.available_at - timedelta(minutes=1),
    })
    with pytest.raises(DataError, match="must not move backward"):
        write([backward])
