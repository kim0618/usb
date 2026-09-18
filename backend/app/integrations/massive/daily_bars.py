"""Massive daily aggregates: one row per session, on the same Custom Bars contract.

A daily bar is the same ``results[]`` object a minute bar is, read under a different
window. Massive documents ``t`` as the start of the aggregate window, so a daily row's
``t`` is the session's own midnight in market time; ``session_date`` is that ET date and
is the only date authority this module offers. Nothing here infers a session from a UTC
date, which would move every bar of the year by a day.

``adjusted=false`` is fixed by the caller, exactly as the minute path fixes it, so the
prices are the traded prices and never a back-adjusted series.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.integrations.massive.minute_bars import ET, MalformedPayload, optional_number, parse_timestamp_ms


@dataclass(frozen=True)
class DailyAggregateBar:
    """One Massive daily aggregate row, kept as sent."""

    bar_start: datetime  # UTC, from ``t``
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    vwap: float | None = None
    trades: float | None = None

    @property
    def session_date(self) -> date:
        """The ET calendar date of the aggregate window this row opened."""
        return self.bar_start.astimezone(ET).date()

    @property
    def has_null_ohlcv(self) -> bool:
        return any(value is None for value in
                   (self.open, self.high, self.low, self.close, self.volume))


def parse_daily_bar(row: Any) -> DailyAggregateBar:
    """One ``results[]`` entry of a daily aggregates response."""
    if not isinstance(row, Mapping):
        raise MalformedPayload("aggregate result must be an object")
    if "t" not in row:
        raise MalformedPayload("aggregate field t is missing")
    return DailyAggregateBar(
        bar_start=parse_timestamp_ms(row["t"]),
        open=optional_number(row, "o"), high=optional_number(row, "h"),
        low=optional_number(row, "l"), close=optional_number(row, "c"),
        volume=optional_number(row, "v"), vwap=optional_number(row, "vw"),
        trades=optional_number(row, "n"),
    )
