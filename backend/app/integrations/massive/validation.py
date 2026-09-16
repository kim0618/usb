"""Data-quality checks for one symbol's minute aggregates over one completed XNYS session.

Missing minutes and zero-volume rows are reported, not failed: Massive documents that
a minute without eligible trades is omitted from intraday aggregates.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.integrations.massive.minute_bars import (
    BAR_INTERVAL, ET, MinuteBar, SessionPart, classify, extended_bounds,
)
from app.market.calendar import TradingSessionWindow


@dataclass(frozen=True)
class SpikeValidation:
    window: TradingSessionWindow
    total_rows: int
    first_et: datetime | None
    last_et: datetime | None
    session_rows: dict[SessionPart, int]
    first_premarket_et: datetime | None
    regular_open_row: bool
    last_regular_row: bool
    rows_at_or_after_close: int
    duplicate_timestamps: int
    non_monotonic_timestamps: int
    ohlc_violations: int
    null_ohlcv_rows: int
    negative_volume_rows: int
    zero_volume_rows: int
    unaligned_timestamps: int
    off_date_rows: int
    missing_regular_minutes: tuple[datetime, ...]
    premarket_missing_minutes: tuple[datetime, ...]
    postmarket_missing_minutes: tuple[datetime, ...]
    premarket_ohlc_rows: int
    premarket_volume_rows: int
    premarket_volume_total: float

    @property
    def last_regular_minute(self) -> datetime:
        return self.window.market_close - BAR_INTERVAL

    @property
    def expected_regular_minutes(self) -> int:
        """390 on a full XNYS day; fewer on an early close."""
        return (self.window.market_close - self.window.market_open) // BAR_INTERVAL

    @property
    def complete_regular(self) -> bool:
        return (self.session_rows[SessionPart.REGULAR] == self.expected_regular_minutes
                and not self.missing_regular_minutes)

    @property
    def premarket_data_present(self) -> bool:
        return self.session_rows[SessionPart.PREMARKET] > 0

    @property
    def premarket_ohlc_present(self) -> bool:
        return self.premarket_ohlc_rows > 0

    @property
    def premarket_volume_present(self) -> bool:
        return self.premarket_volume_rows > 0

    def anomalies(self) -> tuple[str, ...]:
        checks = {
            "duplicate_timestamps": self.duplicate_timestamps,
            "non_monotonic_timestamps": self.non_monotonic_timestamps,
            "ohlc_invariant_violations": self.ohlc_violations,
            "null_ohlcv_rows": self.null_ohlcv_rows,
            "negative_volume_rows": self.negative_volume_rows,
            "unaligned_timestamps": self.unaligned_timestamps,
            "off_date_rows": self.off_date_rows,
        }
        return tuple(name for name, count in checks.items() if count)

    @property
    def verdict(self) -> str:
        if self.total_rows == 0:
            return "NO_DATA"
        anomalies = self.anomalies()
        return "CLEAN" if not anomalies else "ANOMALIES:" + ",".join(anomalies)


def _minutes(start: datetime, end: datetime) -> Iterator[datetime]:
    minute = start
    while minute < end:
        yield minute
        minute += BAR_INTERVAL


def _missing(start: datetime, end: datetime, present: set[datetime]) -> tuple[datetime, ...]:
    return tuple(minute.astimezone(ET) for minute in _minutes(start, end) if minute not in present)


def _ohlc_invalid(bar: MinuteBar) -> bool:
    ohlc = bar.ohlc
    if ohlc is None:
        return False
    open_, high, low, close = ohlc
    return low <= 0 or high < max(open_, close, low) or low > min(open_, close, high)


def validate(bars: Sequence[MinuteBar], window: TradingSessionWindow) -> SpikeValidation:
    parts = [classify(bar.bar_start, window) for bar in bars]
    counts = {part: 0 for part in SessionPart}
    by_part: dict[SessionPart, set[datetime]] = {part: set() for part in SessionPart}
    for bar, part in zip(bars, parts):
        counts[part] += 1
        by_part[part].add(bar.bar_start)
    starts = [bar.bar_start for bar in bars]
    present = set(starts)
    premarket = [bar for bar, part in zip(bars, parts) if part is SessionPart.PREMARKET]
    positive_premarket = [bar.volume for bar in premarket if bar.volume is not None and bar.volume > 0]
    premarket_start, postmarket_end = extended_bounds(window)
    return SpikeValidation(
        window=window,
        total_rows=len(bars),
        first_et=min(starts).astimezone(ET) if starts else None,
        last_et=max(starts).astimezone(ET) if starts else None,
        session_rows=counts,
        first_premarket_et=min(bar.bar_start for bar in premarket).astimezone(ET) if premarket else None,
        regular_open_row=window.market_open in present,
        last_regular_row=window.market_close - BAR_INTERVAL in present,
        rows_at_or_after_close=sum(1 for start in starts if start >= window.market_close),
        duplicate_timestamps=len(starts) - len(present),
        non_monotonic_timestamps=sum(1 for before, after in zip(starts, starts[1:]) if after < before),
        ohlc_violations=sum(1 for bar in bars if _ohlc_invalid(bar)),
        null_ohlcv_rows=sum(1 for bar in bars if bar.has_null_ohlcv),
        negative_volume_rows=sum(1 for bar in bars if bar.volume is not None and bar.volume < 0),
        zero_volume_rows=sum(1 for bar in bars if bar.volume == 0),
        unaligned_timestamps=sum(1 for start in starts if start.second or start.microsecond),
        off_date_rows=sum(1 for bar in bars if bar.bar_start_et.date() != window.session_date),
        missing_regular_minutes=_missing(window.market_open, window.market_close,
                                         by_part[SessionPart.REGULAR]),
        premarket_missing_minutes=_missing(premarket_start, window.market_open,
                                           by_part[SessionPart.PREMARKET]),
        postmarket_missing_minutes=_missing(window.market_close, postmarket_end,
                                            by_part[SessionPart.POSTMARKET]),
        premarket_ohlc_rows=sum(1 for bar in premarket if bar.ohlc is not None),
        premarket_volume_rows=len(positive_premarket),
        premarket_volume_total=float(sum(positive_premarket)),
    )
