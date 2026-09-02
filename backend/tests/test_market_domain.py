from datetime import datetime

import pytest
from pydantic import ValidationError

from app.market.domain import DailyBar, MinuteBar
from tests.market_fixtures import daily_bar, minute_bar


@pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "ABC-D"])
def test_safe_us_symbols_are_allowed(symbol: str) -> None:
    assert daily_bar(symbol).symbol == symbol


@pytest.mark.parametrize("symbol", ["../../../PWNED", "/tmp/x", r"A\\B", "", "   "])
def test_unsafe_path_symbols_are_rejected(symbol: str) -> None:
    with pytest.raises(ValueError):
        daily_bar(symbol)


def test_valid_daily_and_minute_bars() -> None:
    assert isinstance(daily_bar(), DailyBar)
    assert isinstance(minute_bar(), MinuteBar)


@pytest.mark.parametrize(
    "changes",
    [
        {"high": 98.0},
        {"low": 103.0},
        {"volume": -1},
        {"symbol": "   "},
    ],
)
def test_invalid_bar_values_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        daily_bar().model_copy(update=changes).model_validate(
            {**daily_bar().model_dump(), **changes}
        )


@pytest.mark.parametrize("field", ["timestamp", "observed_at", "available_at"])
def test_naive_minute_datetimes_are_rejected(field: str) -> None:
    values = minute_bar().model_dump()
    values[field] = datetime(2024, 6, 18, 9, 31)
    with pytest.raises(ValidationError):
        MinuteBar.model_validate(values)


def test_available_before_observed_is_rejected() -> None:
    values = minute_bar().model_dump()
    values["available_at"] = values["timestamp"]
    with pytest.raises(ValidationError):
        MinuteBar.model_validate(values)
