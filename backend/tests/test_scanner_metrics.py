import pytest

from app.core.exceptions import DataError
from app.scanner.metrics import (
    average_dollar_volume,
    relative_strength,
    return_over_lookback,
    rvol,
)
from tests.scanner_fixtures import history


def test_rvol_excludes_latest_from_baseline() -> None:
    bars = history("AAA", historical_volume=100, latest_volume=250)
    assert rvol(bars, 20) == pytest.approx(2.5)


def test_rvol_zero_denominator_is_rejected() -> None:
    bars = history("AAA", historical_volume=0, latest_volume=100)
    with pytest.raises(DataError):
        rvol(bars, 20)


def test_insufficient_history_is_rejected() -> None:
    with pytest.raises(DataError):
        return_over_lookback(history("AAA", count=20), 20)


def test_momentum_uses_latest_and_twenty_bars_prior() -> None:
    bars = history("AAA", base_close=100.0, latest_close=120.0)
    assert return_over_lookback(bars, 20) == pytest.approx(0.20)


def test_relative_strength_subtracts_spy_five_day_return() -> None:
    stock = history("AAA", base_close=100.0, latest_close=110.0)
    spy = history("SPY", base_close=100.0, latest_close=104.0)
    expected = (stock[-1].close / stock[-6].close - 1) - (
        spy[-1].close / spy[-6].close - 1
    )
    assert relative_strength(stock, spy, 5) == pytest.approx(expected)


def test_average_dollar_volume_uses_prior_twenty_bars() -> None:
    bars = history("AAA", base_close=100.0, historical_volume=250_000, latest_volume=9_000_000)
    assert average_dollar_volume(bars, 20) == pytest.approx(25_000_000.0)

