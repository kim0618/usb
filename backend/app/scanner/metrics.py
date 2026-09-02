"""Unambiguous Quant Scanner V0 metric formulas."""

from collections.abc import Sequence
from statistics import fmean

from app.core.exceptions import DataError
from app.market.domain import DailyBar


def return_over_lookback(bars: Sequence[DailyBar], lookback: int) -> float:
    if len(bars) < lookback + 1:
        raise DataError("Insufficient history for return")
    base = bars[-(lookback + 1)].close
    if base <= 0:
        raise DataError("Return denominator must be positive")
    return bars[-1].close / base - 1.0


def rvol(bars: Sequence[DailyBar], lookback: int) -> float:
    if len(bars) < lookback + 1:
        raise DataError("Insufficient history for RVOL")
    baseline = fmean(bar.volume for bar in bars[-(lookback + 1):-1])
    if baseline <= 0:
        raise DataError("RVOL baseline must be positive")
    return bars[-1].volume / baseline


def average_dollar_volume(bars: Sequence[DailyBar], lookback: int) -> float:
    if len(bars) < lookback + 1:
        raise DataError("Insufficient history for average dollar volume")
    historical = bars[-(lookback + 1):-1]
    return fmean(bar.close * bar.volume for bar in historical)


def latest_dollar_volume(bars: Sequence[DailyBar]) -> float:
    if not bars:
        raise DataError("No bar available for dollar volume")
    return bars[-1].close * bars[-1].volume


def relative_strength(
    stock_bars: Sequence[DailyBar], benchmark_bars: Sequence[DailyBar], lookback: int
) -> float:
    return return_over_lookback(stock_bars, lookback) - return_over_lookback(
        benchmark_bars, lookback
    )

