"""One symbol, about one year of Massive 1-minute aggregates: range, grouping, quality.

This is the feasibility stage only. Nothing here writes a manifest row, a Parquet store,
or a replay input. Bars are grouped by XNYS session from the America/New_York calendar,
every session gets the same checks as the single-session spike, and the whole range gets
the timestamp and OHLC checks once more across page and session boundaries. No bar is
clamped, filled, or synthesized: a missing minute stays missing, and whether it was a
real no-trade minute is UNKNOWN on the Basic plan.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
from statistics import median

from app.integrations.massive.client import BASIC_CALLS_PER_MINUTE
from app.integrations.massive.minute_bars import (
    BAR_INTERVAL, ET, PREMARKET_START, MinuteBar, SessionPart, classify, latest_completed_session,
)
from app.integrations.massive.validation import SpikeValidation, ohlc_invalid, validate
from app.market.calendar import MarketCalendar, TradingSessionWindow


ANOMALY_SAMPLE_LIMIT = 20
REQUEST_SPACING_SECONDS = 60.0 / BASIC_CALLS_PER_MINUTE


@dataclass(frozen=True)
class YearRange:
    sessions: tuple[TradingSessionWindow, ...]  # oldest first, every XNYS session in range

    @property
    def start(self) -> TradingSessionWindow:
        return self.sessions[0]

    @property
    def end(self) -> TradingSessionWindow:
        return self.sessions[-1]

    @property
    def request_start(self) -> datetime:
        """00:00 ET on the first session date: rows outside 04:00-20:00 ET are observed too."""
        return datetime.combine(self.start.session_date, time(0, 0), tzinfo=ET)

    @property
    def request_end(self) -> datetime:
        """The last millisecond of the last session date (``to`` inclusivity is UNKNOWN)."""
        end_day = datetime.combine(self.end.session_date, time(0, 0), tzinfo=ET)
        return end_day + timedelta(days=1) - timedelta(milliseconds=1)

    @property
    def session_dates(self) -> frozenset[date]:
        return frozenset(window.session_date for window in self.sessions)


def one_year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def year_range(calendar: MarketCalendar, now: datetime) -> YearRange:
    """Every XNYS session from the calendar date one year before the latest completed
    session (or the first session after it) through that completed session."""
    end = latest_completed_session(calendar, now)
    first_day = one_year_earlier(end.session_date)
    if not calendar.is_trading_day(first_day):
        first_day = calendar.next_trading_day(first_day)
    sessions: list[TradingSessionWindow] = []
    day = first_day
    while day <= end.session_date:
        window = calendar.session(day)
        if window is None:
            raise ValueError(f"{day} is not an XNYS session")
        sessions.append(window)
        day = calendar.next_trading_day(day)
    return YearRange(tuple(sessions))


@dataclass(frozen=True)
class SessionReport:
    window: TradingSessionWindow
    validation: SpikeValidation | None  # None: no row at all on this session date
    premarket_largest_gap_minutes: int  # longest run of consecutive missing premarket minutes

    @property
    def session_present(self) -> bool:
        return self.validation is not None and self.validation.total_rows > 0

    @property
    def premarket_present(self) -> bool:
        return self.validation is not None and self.validation.premarket_data_present

    @property
    def first_premarket_timestamp(self) -> datetime | None:
        return None if self.validation is None else self.validation.first_premarket_et

    @property
    def premarket_starts_at_0400(self) -> bool:
        first = self.first_premarket_timestamp
        return first is not None and first.timetz().replace(tzinfo=None) == PREMARKET_START

    @property
    def regular_open_present(self) -> bool:
        return self.validation is not None and self.validation.regular_open_row

    @property
    def regular_close_minus_1_present(self) -> bool:
        return self.validation is not None and self.validation.last_regular_row

    @property
    def expected_regular_minutes(self) -> int:
        return (self.window.market_close - self.window.market_open) // BAR_INTERVAL

    @property
    def regular_row_count(self) -> int:
        return 0 if self.validation is None else self.validation.session_rows[SessionPart.REGULAR]

    @property
    def regular_missing_minutes(self) -> int:
        if self.validation is None:
            return self.expected_regular_minutes
        return len(self.validation.missing_regular_minutes)

    @property
    def regular_complete(self) -> bool:
        return self.validation is not None and self.validation.complete_regular

    @property
    def postmarket_present(self) -> bool:
        return self.validation is not None and self.validation.session_rows[SessionPart.POSTMARKET] > 0

    @property
    def premarket_row_count(self) -> int:
        return 0 if self.validation is None else self.validation.session_rows[SessionPart.PREMARKET]

    @property
    def premarket_volume_total(self) -> float:
        return 0.0 if self.validation is None else self.validation.premarket_volume_total

    @property
    def early_close_valid(self) -> bool:
        """An early-close session whose regular minutes end exactly at the calendar close."""
        return self.window.is_early_close and self.regular_complete and self.regular_close_minus_1_present


def largest_gap_minutes(missing: Sequence[datetime]) -> int:
    longest = run = 0
    previous: datetime | None = None
    for minute in missing:
        run = run + 1 if previous is not None and minute - previous == BAR_INTERVAL else 1
        longest = max(longest, run)
        previous = minute
    return longest


def group_by_session(bars: Sequence[MinuteBar], year: YearRange) -> tuple[dict[date, list[MinuteBar]],
                                                                          list[MinuteBar]]:
    """Bars per expected session date (ET), and the rows on no expected session date."""
    grouped: dict[date, list[MinuteBar]] = {window.session_date: [] for window in year.sessions}
    off_date: list[MinuteBar] = []
    for bar in bars:
        day = bar.bar_start_et.date()
        if day in grouped:
            grouped[day].append(bar)
        else:
            off_date.append(bar)
    return grouped, off_date


def session_reports(bars: Sequence[MinuteBar], year: YearRange) -> tuple[SessionReport, ...]:
    grouped, _ = group_by_session(bars, year)
    reports = []
    for window in year.sessions:
        rows = grouped[window.session_date]
        if not rows:
            reports.append(SessionReport(window, None, (window.market_open - datetime.combine(
                window.session_date, PREMARKET_START, tzinfo=ET)) // BAR_INTERVAL))
            continue
        result = validate(rows, window)
        reports.append(SessionReport(window, result, largest_gap_minutes(result.premarket_missing_minutes)))
    return tuple(reports)


@dataclass(frozen=True)
class AnomalySample:
    kind: str
    session_date: date | None
    timestamp_et: datetime
    detail: str  # field names and numbers only, never a payload fragment


@dataclass(frozen=True)
class RangeQuality:
    total_rows: int
    duplicate_timestamps: int
    non_monotonic_timestamps: int
    ohlc_violations: int
    null_ohlcv_rows: int
    negative_volume_rows: int
    zero_volume_rows: int
    unaligned_timestamps: int
    off_date_rows: int
    outside_extended_hours_rows: int
    samples: tuple[AnomalySample, ...]

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


def range_quality(bars: Sequence[MinuteBar], year: YearRange) -> RangeQuality:
    """The spike checks once more over the whole range, across page and session edges."""
    samples: list[AnomalySample] = []
    dates = year.session_dates
    windows = {window.session_date: window for window in year.sessions}

    def sample(kind: str, bar: MinuteBar, detail: str) -> None:
        if len(samples) < ANOMALY_SAMPLE_LIMIT:
            day = bar.bar_start_et.date()
            samples.append(AnomalySample(kind, day if day in dates else None, bar.bar_start_et, detail))

    seen: Counter[datetime] = Counter(bar.bar_start for bar in bars)
    duplicates = non_monotonic = ohlc = null = negative = zero = unaligned = off_date = outside = 0
    previous: MinuteBar | None = None
    reported_duplicates: set[datetime] = set()
    for bar in bars:
        if seen[bar.bar_start] > 1 and bar.bar_start not in reported_duplicates:
            reported_duplicates.add(bar.bar_start)
            sample("duplicate_timestamp", bar, f"occurrences={seen[bar.bar_start]}")
        if previous is not None and bar.bar_start < previous.bar_start:
            non_monotonic += 1
            sample("non_monotonic_timestamp", bar, f"previous_et={previous.bar_start_et.isoformat()}")
        if ohlc_invalid(bar):
            ohlc += 1
            sample("ohlc_invariant_violation", bar, f"o={bar.open} h={bar.high} l={bar.low} c={bar.close}")
        if bar.has_null_ohlcv:
            null += 1
            sample("null_ohlcv", bar, "one of o/h/l/c/v is null")
        if bar.volume is not None and bar.volume < 0:
            negative += 1
            sample("negative_volume", bar, f"v={bar.volume}")
        if bar.volume == 0:
            zero += 1
        if bar.bar_start.second or bar.bar_start.microsecond:
            unaligned += 1
            sample("unaligned_timestamp", bar, f"second={bar.bar_start.second}")
        day = bar.bar_start_et.date()
        if day not in dates:
            off_date += 1
            sample("off_date_row", bar, "ET date is not an expected XNYS session")
        elif classify(bar.bar_start, windows[day]) is SessionPart.OUTSIDE:
            outside += 1
        previous = bar
    duplicates = len(bars) - len(seen)
    return RangeQuality(
        total_rows=len(bars), duplicate_timestamps=duplicates, non_monotonic_timestamps=non_monotonic,
        ohlc_violations=ohlc, null_ohlcv_rows=null, negative_volume_rows=negative, zero_volume_rows=zero,
        unaligned_timestamps=unaligned, off_date_rows=off_date, outside_extended_hours_rows=outside,
        samples=tuple(samples),
    )


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile on the sorted values; None when there are no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return float(ordered[rank])


def _rate(count: int, total: int) -> float:
    return 0.0 if total == 0 else count / total


@dataclass(frozen=True)
class CoverageSummary:
    expected_sessions: int
    sessions_present: int
    sessions_missing_entirely: tuple[date, ...]
    sessions_with_premarket: int
    sessions_with_premarket_0400: int
    sessions_with_regular_open: int
    sessions_with_exact_final_regular_bar: int
    sessions_with_complete_regular_minutes: int
    sessions_with_regular_missing_bars: tuple[tuple[date, int], ...]  # (date, missing minutes)
    sessions_with_postmarket: int
    early_close_sessions: tuple[date, ...]
    early_close_sessions_valid: tuple[date, ...]
    premarket_row_counts: tuple[int, ...]
    premarket_volume_totals: tuple[float, ...]
    premarket_largest_gaps: tuple[int, ...]
    postmarket_row_counts: tuple[int, ...]
    regular_row_counts: tuple[int, ...]

    @property
    def premarket_present_rate(self) -> float:
        return _rate(self.sessions_with_premarket, self.expected_sessions)

    @property
    def premarket_0400_rate(self) -> float:
        return _rate(self.sessions_with_premarket_0400, self.expected_sessions)

    @property
    def complete_regular_rate(self) -> float:
        return _rate(self.sessions_with_complete_regular_minutes, self.expected_sessions)

    @property
    def worst_regular_session(self) -> tuple[date, int] | None:
        return max(self.sessions_with_regular_missing_bars, key=lambda item: item[1], default=None)

    @property
    def total_regular_missing_minutes(self) -> int:
        return sum(missing for _, missing in self.sessions_with_regular_missing_bars)


def coverage_summary(reports: Sequence[SessionReport]) -> CoverageSummary:
    present = [report for report in reports if report.session_present]
    return CoverageSummary(
        expected_sessions=len(reports),
        sessions_present=len(present),
        sessions_missing_entirely=tuple(r.window.session_date for r in reports if not r.session_present),
        sessions_with_premarket=sum(1 for r in reports if r.premarket_present),
        sessions_with_premarket_0400=sum(1 for r in reports if r.premarket_starts_at_0400),
        sessions_with_regular_open=sum(1 for r in reports if r.regular_open_present),
        sessions_with_exact_final_regular_bar=sum(1 for r in reports if r.regular_close_minus_1_present),
        sessions_with_complete_regular_minutes=sum(1 for r in reports if r.regular_complete),
        sessions_with_regular_missing_bars=tuple((r.window.session_date, r.regular_missing_minutes)
                                                 for r in reports if r.regular_missing_minutes),
        sessions_with_postmarket=sum(1 for r in reports if r.postmarket_present),
        early_close_sessions=tuple(r.window.session_date for r in reports if r.window.is_early_close),
        early_close_sessions_valid=tuple(r.window.session_date for r in reports if r.early_close_valid),
        premarket_row_counts=tuple(r.premarket_row_count for r in present),
        premarket_volume_totals=tuple(r.premarket_volume_total for r in present),
        premarket_largest_gaps=tuple(r.premarket_largest_gap_minutes for r in present),
        postmarket_row_counts=tuple(r.validation.session_rows[SessionPart.POSTMARKET]
                                    for r in present if r.validation is not None),
        regular_row_counts=tuple(r.regular_row_count for r in present),
    )


def median_or_none(values: Iterable[float]) -> float | None:
    items = list(values)
    return float(median(items)) if items else None


@dataclass(frozen=True)
class Projection:
    symbols: int
    http_requests: int
    elapsed_seconds_measured_basis: float
    minimum_seconds_at_basic_rate: float

    @property
    def minimum_minutes_at_basic_rate(self) -> float:
        return self.minimum_seconds_at_basic_rate / 60.0


def project(http_requests_per_symbol: int, elapsed_seconds_per_symbol: float,
            symbols: int) -> Projection:
    """Scale the measured long-range run: requests grow linearly per symbol, and the
    Basic plan spaces requests 12 s apart (the first request of a run waits for nothing)."""
    if http_requests_per_symbol < 1 or symbols < 1:
        raise ValueError("projection needs at least one measured request and one symbol")
    requests = http_requests_per_symbol * symbols
    return Projection(symbols, requests, elapsed_seconds_per_symbol * symbols,
                      max(0, requests - 1) * REQUEST_SPACING_SECONDS)
