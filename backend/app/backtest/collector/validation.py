"""Validation while the rows stream past, so a year never has to be held to be judged.

Bars arrive in ascending order, one page at a time. They are grouped into the XNYS
session they belong to and each finished session is handed to the same per-session
checks the spike and the feasibility probe used, then released. Only the current
session's rows and the per-session reports stay in memory.

What fails a collection: a session the calendar expects and the provider did not send, a
missing regular-hours minute, a row on a date that is not an expected session, and any
timestamp or OHLC corruption. What does not fail it: a missing premarket or postmarket
minute, which Massive omits when no eligible trade printed. No bar is ever synthesized.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.backtest.collector.range import CollectionRange
from app.integrations.massive.long_range import (
    ANOMALY_SAMPLE_LIMIT, AnomalySample, CoverageSummary, RangeQuality, SessionReport,
    coverage_summary, largest_gap_minutes,
)
from app.integrations.massive.minute_bars import (
    BAR_INTERVAL, ET, PREMARKET_START, MinuteBar, SessionPart, classify,
)
from app.integrations.massive.validation import ohlc_invalid, validate
from app.market.calendar import TradingSessionWindow


SessionSink = Callable[[TradingSessionWindow, Sequence[MinuteBar]], None]


@dataclass
class _QualityCounters:
    total_rows: int = 0
    duplicate_timestamps: int = 0
    non_monotonic_timestamps: int = 0
    ohlc_violations: int = 0
    null_ohlcv_rows: int = 0
    negative_volume_rows: int = 0
    zero_volume_rows: int = 0
    unaligned_timestamps: int = 0
    off_date_rows: int = 0
    outside_extended_hours_rows: int = 0
    samples: list[AnomalySample] = field(default_factory=list)

    def sample(self, kind: str, bar: MinuteBar, detail: str, *, session_date: date | None) -> None:
        if len(self.samples) < ANOMALY_SAMPLE_LIMIT:
            self.samples.append(AnomalySample(kind, session_date, bar.bar_start_et, detail))

    def to_quality(self) -> RangeQuality:
        return RangeQuality(
            total_rows=self.total_rows, duplicate_timestamps=self.duplicate_timestamps,
            non_monotonic_timestamps=self.non_monotonic_timestamps,
            ohlc_violations=self.ohlc_violations, null_ohlcv_rows=self.null_ohlcv_rows,
            negative_volume_rows=self.negative_volume_rows, zero_volume_rows=self.zero_volume_rows,
            unaligned_timestamps=self.unaligned_timestamps, off_date_rows=self.off_date_rows,
            outside_extended_hours_rows=self.outside_extended_hours_rows,
            samples=tuple(self.samples))


@dataclass(frozen=True)
class CollectionValidation:
    reports: tuple[SessionReport, ...]
    coverage: CoverageSummary
    quality: RangeQuality
    rows_written: int
    last_expected_session: date
    last_session_present: bool

    @property
    def missing_sessions(self) -> tuple[date, ...]:
        return self.coverage.sessions_missing_entirely

    @property
    def sessions_with_missing_regular_minutes(self) -> tuple[tuple[date, int], ...]:
        return self.coverage.sessions_with_regular_missing_bars

    @property
    def early_close_sessions(self) -> tuple[date, ...]:
        return self.coverage.early_close_sessions

    @property
    def complete(self) -> bool:
        return not self.failures()

    def failures(self) -> tuple[str, ...]:
        """Every reason this dataset may not be recorded COMPLETE, worst first."""
        reasons: list[str] = []
        if not self.last_session_present:
            reasons.append(
                f"INCOMPLETE_RANGE: the last expected session {self.last_expected_session} "
                "is absent although every page answered HTTP 200")
        if self.missing_sessions:
            listed = ",".join(str(day) for day in self.missing_sessions[:10])
            reasons.append(f"SESSIONS_MISSING: {len(self.missing_sessions)} expected sessions "
                           f"have no rows ({listed})")
        if self.sessions_with_missing_regular_minutes:
            worst = max(self.sessions_with_missing_regular_minutes, key=lambda item: item[1])
            reasons.append(
                f"REGULAR_MINUTES_MISSING: {len(self.sessions_with_missing_regular_minutes)} "
                f"sessions miss a regular minute (worst {worst[0]} missing {worst[1]})")
        if self.quality.verdict != "CLEAN":
            reasons.append(f"DATA_QUALITY: {self.quality.verdict}")
        return tuple(reasons)


class StreamingValidator:
    """Feed bars in ascending order; finished sessions go to ``sink`` and are released."""

    def __init__(self, collection_range: CollectionRange, sink: SessionSink) -> None:
        self._range = collection_range
        self._windows = {window.session_date: window for window in collection_range.sessions}
        self._sink = sink
        self._reports: dict[date, SessionReport] = {}
        self._counters = _QualityCounters()
        self._current_date: date | None = None
        self._current_rows: list[MinuteBar] = []
        self._current_starts: set[datetime] = set()
        self._previous_start: datetime | None = None
        self._rows_written = 0

    @property
    def rows_written(self) -> int:
        return self._rows_written

    def add(self, bar: MinuteBar) -> None:
        counters = self._counters
        counters.total_rows += 1
        day = bar.bar_start_et.date()
        window = self._windows.get(day)
        if self._previous_start is not None and bar.bar_start < self._previous_start:
            counters.non_monotonic_timestamps += 1
            counters.sample("non_monotonic_timestamp", bar,
                            f"previous_et={self._previous_start.astimezone(ET).isoformat()}",
                            session_date=day if window else None)
        self._previous_start = bar.bar_start
        if ohlc_invalid(bar):
            counters.ohlc_violations += 1
            counters.sample("ohlc_invariant_violation", bar,
                            f"o={bar.open} h={bar.high} l={bar.low} c={bar.close}",
                            session_date=day if window else None)
        if bar.has_null_ohlcv:
            counters.null_ohlcv_rows += 1
            counters.sample("null_ohlcv", bar, "one of o/h/l/c/v is null",
                            session_date=day if window else None)
        if bar.volume is not None and bar.volume < 0:
            counters.negative_volume_rows += 1
            counters.sample("negative_volume", bar, f"v={bar.volume}",
                            session_date=day if window else None)
        if bar.volume == 0:
            counters.zero_volume_rows += 1
        if bar.bar_start.second or bar.bar_start.microsecond:
            counters.unaligned_timestamps += 1
            counters.sample("unaligned_timestamp", bar, f"second={bar.bar_start.second}",
                            session_date=day if window else None)
        if window is None:
            counters.off_date_rows += 1
            counters.sample("off_date_row", bar, "ET date is not an expected XNYS session",
                            session_date=None)
            return  # never stored: the collection will fail on this row
        if classify(bar.bar_start, window) is SessionPart.OUTSIDE:
            counters.outside_extended_hours_rows += 1
        if day != self._current_date:
            self._close_session()
            self._current_date = day
        if bar.bar_start in self._current_starts:
            counters.duplicate_timestamps += 1
            counters.sample("duplicate_timestamp", bar, "repeated within the session",
                            session_date=day)
        self._current_starts.add(bar.bar_start)
        self._current_rows.append(bar)

    def _close_session(self) -> None:
        if self._current_date is None or not self._current_rows:
            self._current_rows = []
            self._current_starts = set()
            return
        window = self._windows[self._current_date]
        result = validate(self._current_rows, window)
        self._reports[self._current_date] = SessionReport(
            window, result, largest_gap_minutes(result.premarket_missing_minutes))
        self._sink(window, self._current_rows)
        self._rows_written += len(self._current_rows)
        self._current_rows = []
        self._current_starts = set()
        self._current_date = None

    def finish(self) -> CollectionValidation:
        self._close_session()
        reports = tuple(self._reports.get(window.session_date) or _absent(window)
                        for window in self._range.sessions)
        last = self._range.effective_end
        return CollectionValidation(
            reports=reports, coverage=coverage_summary(reports),
            quality=self._counters.to_quality(), rows_written=self._rows_written,
            last_expected_session=last,
            last_session_present=self._reports.get(last) is not None)


def _absent(window: TradingSessionWindow) -> SessionReport:
    """A session the provider sent nothing for: every regular minute counts as missing."""
    premarket_minutes = (window.market_open - datetime.combine(
        window.session_date, PREMARKET_START, tzinfo=ET)) // BAR_INTERVAL
    return SessionReport(window, None, premarket_minutes)
