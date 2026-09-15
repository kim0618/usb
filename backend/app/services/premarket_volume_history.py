"""Analytics-only PREMARKET volume history and the V2 median normalisation.

V2 divides one entry session's Kiwoom PREMARKET volume (04:00 <= t < 09:30 ET) by the
median of that same window over the exact 20 XNYS sessions before the entry session,
every value read from the one usa06011 minute source. It is a counterfactual field
only: Production StrategyV0 keeps its V1 gate (premarket volume / 20-day average daily
volume >= 5%), and nothing in the Strategy, Risk, or Entry path reads this module.

The collector runs after the morning Scanner, never on the Entry critical path. It
reads market data and writes nothing but ``premarket_volume_sessions``; a session
whose stored answer is final for this source and collector version is never fetched
again, and a COMPLETE session is never overwritten by a worse answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import StrEnum
import logging
from statistics import median
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.mapping import exchange_code
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import MarketSession, MinuteBar
from app.market.symbols import normalize_symbol
from app.models.analytics import PremarketVolumeSession
from app.models.scanner import ScannerCandidate, ScannerRun
from app.services.exchange_authority import stored_exchange

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]

SOURCE = "KIWOOM_USA06011"
# The schema revision the collector writes; a newer head needs review before writing.
HISTORY_SCHEMA_REVISION = "20260915_0016"
# The meaning of a stored row: its window, completeness rules, and volume sum. A
# contract change bumps this, and rows of another version are never read as this one.
COLLECTOR_VERSION = "premarket_volume_collector_v1"
BASELINE_METHOD = "MEDIAN"
BASELINE_SESSIONS = 20
PREMARKET_START = time(4)
# Counterfactual thresholds only; none of them is a Production strategy gate value.
V2_THRESHOLDS = tuple(Decimal(value) for value in ("1.00", "1.25", "1.50", "2.00", "2.50", "3.00"))


class SessionQuality(StrEnum):
    COMPLETE = "COMPLETE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    TARGET_NOT_REACHED = "TARGET_NOT_REACHED"
    NO_PREMARKET_BARS = "NO_PREMARKET_BARS"
    NO_REGULAR_BARS = "NO_REGULAR_BARS"
    SESSION_IDENTITY_MISMATCH = "SESSION_IDENTITY_MISMATCH"


# A provider failure says nothing about the session, so a later run asks again. A
# short history (TARGET_NOT_REACHED) is retried only once the symbol is known to be
# listed by then (see ``retryable``); every other status is what the source itself
# returned for that session and is kept.
RETRYABLE_QUALITY = frozenset({SessionQuality.PROVIDER_FAILURE.value})


class V2Status(StrEnum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_PREMARKET_HISTORY = "INSUFFICIENT_PREMARKET_HISTORY"
    ZERO_BASELINE_VOLUME = "ZERO_BASELINE_VOLUME"
    NO_TODAY_PREMARKET_VOLUME = "NO_TODAY_PREMARKET_VOLUME"
    UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"
    ANALYTICS_ERROR = "ANALYTICS_ERROR"


class HistoryWrite(StrEnum):
    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    KEPT_COMPLETE = "KEPT_COMPLETE"


@dataclass(frozen=True)
class SessionFetch:
    """One session's minute bars and whether the source reached the requested start."""

    bars: tuple[MinuteBar, ...]
    pages_used: int | None
    target_reached: bool


class PremarketSessionSource(Protocol):
    """Market-data read of one symbol's minute bars over ``[start, end]``."""

    def fetch_session(self, symbol: str, exchange: str, start: datetime,
                      end: datetime) -> SessionFetch:
        """Raise ``MarketDataError`` on any provider failure, truncation included."""


class KiwoomPremarketSessionSource:
    """usa06011 through the existing Kiwoom provider; the same source the Entry uses.

    Kiwoom pages a dated minute request backwards from that session's 20:00 and stops
    once a page reaches ``start``. A history truncated with pages still remaining is a
    provider failure; ``target_reached`` false without continuation is the source
    holding nothing older, which is what a session before a listing looks like.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def fetch_session(self, symbol: str, exchange: str, start: datetime,
                      end: datetime) -> SessionFetch:
        self.provider.bind_exchange(symbol, exchange)
        bars = self.provider.get_minute_bars([symbol], start, end)
        collection = self.provider.client.last_minute_collection
        if collection is None:
            raise MarketDataError("MARKET_DATA_UNAVAILABLE", "No minute collection was recorded")
        return SessionFetch(tuple(bars), collection.pages_used, collection.target_reached)


@dataclass(frozen=True)
class SessionVolume:
    symbol: str
    exchange: str
    trading_date: date
    source: str
    collector_version: str
    quality_status: SessionQuality
    quality_reason: str | None
    premarket_volume: int | None
    bar_count: int
    regular_bar_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    pages_used: int | None
    target_reached: bool | None
    collected_at: datetime


@dataclass(frozen=True)
class PremarketVolumeBaseline:
    status: V2Status
    required_sessions: tuple[date, ...]  # oldest first; exactly BASELINE_SESSIONS long
    valid_sessions: int
    missing_sessions: tuple[date, ...]
    invalid_sessions: tuple[date, ...]
    median_volume: Decimal | None
    method: str = BASELINE_METHOD
    source: str = SOURCE
    collector_version: str = COLLECTOR_VERSION

    @property
    def start_date(self) -> date:
        return self.required_sessions[0]

    @property
    def end_date(self) -> date:
        return self.required_sessions[-1]


@dataclass(frozen=True)
class V2VolumeAnalytics:
    status: V2Status
    today_premarket_volume: Decimal | None
    baseline: PremarketVolumeBaseline | None
    ratio: Decimal | None

    def projections(self) -> dict[str, bool] | None:
        """Whether each counterfactual threshold would pass; None when V2 is unavailable."""
        if self.ratio is None:
            return None
        return {str(threshold): self.ratio >= threshold for threshold in V2_THRESHOLDS}


@dataclass(frozen=True)
class SymbolCollection:
    symbol: str
    exchange: str
    entry_session_date: date
    fetched_sessions: tuple[date, ...]
    writes: tuple[HistoryWrite, ...]
    baseline: PremarketVolumeBaseline | None
    failure: str | None = None
    # The sessions this run planned to fetch, and the required ones already stored final.
    requested_sessions: tuple[date, ...] = ()
    cache_hits: int = 0
    records: tuple[SessionVolume, ...] = ()

    @property
    def status(self) -> V2Status:
        if self.baseline is None:
            return (V2Status.UNSUPPORTED_EXCHANGE if self.failure == "UNSUPPORTED_EXCHANGE"
                    else V2Status.ANALYTICS_ERROR)
        return self.baseline.status

    def quality_count(self, quality: SessionQuality) -> int:
        return sum(1 for record in self.records if record.quality_status is quality)


@dataclass(frozen=True)
class CollectionRun:
    entry_session_date: date
    symbols: tuple[SymbolCollection, ...]

    @property
    def market_data_sessions(self) -> int:
        return sum(len(item.fetched_sessions) for item in self.symbols)

    def summary(self) -> dict[str, int]:
        """Request accounting for one run; counts only, never a payload or credential."""
        return {
            "symbols_requested": len(self.symbols),
            "sessions_requested": sum(len(item.requested_sessions) for item in self.symbols),
            "sessions_fetched": self.market_data_sessions,
            "cache_hits": sum(item.cache_hits for item in self.symbols),
            "provider_failures": sum(item.quality_count(SessionQuality.PROVIDER_FAILURE)
                                     for item in self.symbols),
            "target_not_reached": sum(item.quality_count(SessionQuality.TARGET_NOT_REACHED)
                                      for item in self.symbols),
        }


def calculate_median(volumes: Sequence[int | Decimal]) -> Decimal:
    """The baseline statistic; the middle-pair average for an even count."""
    if not volumes:
        raise ValueError("a median needs at least one volume")
    return Decimal(median(Decimal(volume) for volume in volumes))


def required_sessions(calendar: MarketCalendar, entry_session_date: date) -> tuple[date, ...]:
    """The exact BASELINE_SESSIONS XNYS sessions before the entry session, oldest first.

    The entry session itself is never one of them: its PREMARKET volume is the
    numerator, and nothing at or after it is known when the entry session begins.
    """
    if not calendar.is_trading_day(entry_session_date):
        raise ValueError(f"{entry_session_date} is not an XNYS session")
    sessions: list[date] = []
    day = entry_session_date
    for _ in range(BASELINE_SESSIONS):
        day = calendar.previous_trading_day(day)
        sessions.append(day)
    return tuple(reversed(sessions))


def assess_session(symbol: str, exchange: str, day: date, window: TradingSessionWindow,
                   fetch: SessionFetch, *, collected_at: datetime,
                   source: str = SOURCE,
                   collector_version: str = COLLECTOR_VERSION) -> SessionVolume:
    """Classify one fetched session and sum its PREMARKET window.

    A minute without trades has no Kiwoom row, so gaps inside the window are not
    incompleteness; they contribute zero. Truncation is decided by ``target_reached``
    alone. Every bar must carry this symbol, this ET date, and the session label its
    timestamp implies against the exchange calendar.
    """
    start = datetime.combine(day, PREMARKET_START, window.market_open.tzinfo)
    premarket: list[MinuteBar] = []
    regular = 0
    mismatch: str | None = None
    for bar in sorted(fetch.bars, key=lambda item: item.timestamp):
        local = bar.timestamp.astimezone(window.market_open.tzinfo)
        expected = (None if local < start
                    else MarketSession.PREMARKET if local < window.market_open
                    else MarketSession.REGULAR if local < window.market_close
                    else MarketSession.POSTMARKET)
        if bar.symbol != symbol or local.date() != day or bar.session is not expected:
            mismatch = mismatch or ("SYMBOL" if bar.symbol != symbol
                                    else "DATE" if local.date() != day else "SESSION_LABEL")
            continue
        if expected is MarketSession.PREMARKET:
            premarket.append(bar)
        elif expected is MarketSession.REGULAR:
            regular += 1
    quality, reason = (
        (SessionQuality.SESSION_IDENTITY_MISMATCH, mismatch) if mismatch
        else (SessionQuality.TARGET_NOT_REACHED, None) if not fetch.target_reached
        else (SessionQuality.NO_PREMARKET_BARS, None) if not premarket
        else (SessionQuality.NO_REGULAR_BARS, None) if not regular
        else (SessionQuality.COMPLETE, None))
    return SessionVolume(
        symbol, exchange, day, source, collector_version, quality, reason,
        sum(bar.volume for bar in premarket) if premarket else None, len(premarket), regular,
        premarket[0].timestamp if premarket else None,
        premarket[-1].timestamp if premarket else None,
        fetch.pages_used, fetch.target_reached, collected_at)


def load_baseline(session: Session, symbol: str, exchange: str, entry_session_date: date, *,
                  calendar: MarketCalendar | None = None, source: str = SOURCE,
                  collector_version: str = COLLECTOR_VERSION) -> PremarketVolumeBaseline:
    """The V2 denominator from durable rows only; never a market-data request.

    All BASELINE_SESSIONS sessions must be COMPLETE. One missing or invalid session
    makes V2 unavailable: no shorter window, no older replacement session.
    """
    calendar = calendar or MarketCalendar()
    days = required_sessions(calendar, entry_session_date)
    symbol = normalize_symbol(symbol)
    rows = _stored(session, symbol, exchange, days, source, collector_version)
    listed = first_complete_session(session, symbol, exchange, source, collector_version)
    missing = tuple(day for day in days if day not in rows or retryable(rows[day], listed))
    invalid = tuple(day for day in days if day in rows and day not in missing
                    and rows[day].quality_status != SessionQuality.COMPLETE.value)
    complete = [rows[day].premarket_volume for day in days
                if day in rows and rows[day].quality_status == SessionQuality.COMPLETE.value]
    if missing or invalid or len(complete) != BASELINE_SESSIONS:
        return PremarketVolumeBaseline(V2Status.INSUFFICIENT_PREMARKET_HISTORY, days,
                                       len(complete), missing, invalid, None,
                                       source=source, collector_version=collector_version)
    volumes = [volume for volume in complete if volume is not None]
    value = calculate_median(volumes)
    return PremarketVolumeBaseline(
        V2Status.AVAILABLE if value > 0 else V2Status.ZERO_BASELINE_VOLUME, days,
        len(volumes), (), (), value, source=source, collector_version=collector_version)


def v2_analytics(today_premarket_volume: Decimal | None,
                 baseline: PremarketVolumeBaseline) -> V2VolumeAnalytics:
    """today / median_20, available only when both sides are."""
    if baseline.status is not V2Status.AVAILABLE:
        return V2VolumeAnalytics(baseline.status, today_premarket_volume, baseline, None)
    if today_premarket_volume is None:
        return V2VolumeAnalytics(V2Status.NO_TODAY_PREMARKET_VOLUME, None, baseline, None)
    assert baseline.median_volume is not None
    return V2VolumeAnalytics(V2Status.AVAILABLE, today_premarket_volume, baseline,
                             Decimal(today_premarket_volume) / baseline.median_volume)


def first_complete_session(session: Session, symbol: str, exchange: str, source: str,
                           collector_version: str) -> date | None:
    """The oldest stored COMPLETE session: proof the symbol was listed by that date."""
    return session.scalar(select(func.min(PremarketVolumeSession.trading_date)).where(
        PremarketVolumeSession.symbol == symbol,
        PremarketVolumeSession.exchange == exchange,
        PremarketVolumeSession.source == source,
        PremarketVolumeSession.collector_version == collector_version,
        PremarketVolumeSession.quality_status == SessionQuality.COMPLETE.value))


def retryable(row: PremarketVolumeSession, first_complete: date | None) -> bool:
    """Whether a stored answer is not final, so a later run asks the source again.

    A provider failure says nothing about the session. A short history is final only
    where it can be the source holding nothing that old: on or before the symbol's
    first COMPLETE session, which is what a pre-listing session looks like. After a
    COMPLETE session the symbol was already listed, so a short answer was the
    provider's (a short page or a lost continuation), never the market's.
    """
    if row.quality_status in RETRYABLE_QUALITY:
        return True
    return (row.quality_status == SessionQuality.TARGET_NOT_REACHED.value
            and first_complete is not None and first_complete < row.trading_date)


def _stored(session: Session, symbol: str, exchange: str, days: Sequence[date], source: str,
            collector_version: str) -> dict[date, PremarketVolumeSession]:
    rows = session.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.symbol == symbol,
        PremarketVolumeSession.exchange == exchange,
        PremarketVolumeSession.source == source,
        PremarketVolumeSession.collector_version == collector_version,
        PremarketVolumeSession.trading_date.in_(list(days))))
    return {row.trading_date: row for row in rows}


class PremarketVolumeHistoryService:
    """Keeps the durable per-session PREMARKET volume cache the V2 baseline reads."""

    def __init__(self, session: Session, source: PremarketSessionSource | None = None, *,
                 calendar: MarketCalendar | None = None, clock: Clock | None = None,
                 source_name: str = SOURCE,
                 collector_version: str = COLLECTOR_VERSION) -> None:
        self.session = session
        self.source = source
        self.calendar = calendar or MarketCalendar()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.source_name = source_name
        self.collector_version = collector_version

    def required_sessions(self, entry_session_date: date) -> tuple[date, ...]:
        return required_sessions(self.calendar, entry_session_date)

    def missing_sessions(self, symbol: str, exchange: str,
                         entry_session_date: date) -> tuple[date, ...]:
        """Required sessions with no final stored answer yet, newest first."""
        symbol = normalize_symbol(symbol)
        days = self.required_sessions(entry_session_date)
        rows = _stored(self.session, symbol, exchange, days, self.source_name,
                       self.collector_version)
        listed = first_complete_session(self.session, symbol, exchange, self.source_name,
                                        self.collector_version)
        return tuple(day for day in reversed(days)
                     if day not in rows or retryable(rows[day], listed))

    def collect_session(self, symbol: str, exchange: str, day: date) -> SessionVolume:
        window = self.calendar.session(day)
        if window is None:
            raise ValueError(f"{day} is not an XNYS session")
        now = self.clock()
        if now < window.market_close:
            raise ValueError(f"session {day} has not completed")
        if self.source is None:
            raise RuntimeError("collection needs a market-data source")
        start = datetime.combine(day, PREMARKET_START, window.market_open.tzinfo)
        try:
            fetch = self.source.fetch_session(symbol, exchange, start, window.market_close)
        except MarketDataError as exc:
            return SessionVolume(symbol, exchange, day, self.source_name, self.collector_version,
                                 SessionQuality.PROVIDER_FAILURE, exc.code, None, 0, 0, None,
                                 None, None, None, now)
        return assess_session(symbol, exchange, day, window, fetch, collected_at=now,
                              source=self.source_name, collector_version=self.collector_version)

    def upsert(self, record: SessionVolume) -> HistoryWrite:
        """Idempotent on (symbol, exchange, trading_date, source, collector_version)."""
        row = self.session.scalar(select(PremarketVolumeSession).where(
            PremarketVolumeSession.symbol == record.symbol,
            PremarketVolumeSession.exchange == record.exchange,
            PremarketVolumeSession.trading_date == record.trading_date,
            PremarketVolumeSession.source == record.source,
            PremarketVolumeSession.collector_version == record.collector_version))
        now = self.clock()
        if row is None:
            row = PremarketVolumeSession(symbol=record.symbol, exchange=record.exchange,
                                         trading_date=record.trading_date, source=record.source,
                                         collector_version=record.collector_version,
                                         created_at=now)
            self.session.add(row)
            outcome = HistoryWrite.INSERTED
        elif (row.quality_status == SessionQuality.COMPLETE.value
              and record.quality_status is not SessionQuality.COMPLETE):
            # A complete session is final; a later failed read never erases it.
            return HistoryWrite.KEPT_COMPLETE
        else:
            outcome = HistoryWrite.UPDATED
        values = {
            "premarket_volume": record.premarket_volume, "bar_count": record.bar_count,
            "regular_bar_count": record.regular_bar_count,
            "first_timestamp": record.first_timestamp, "last_timestamp": record.last_timestamp,
            "pages_used": record.pages_used, "target_reached": record.target_reached,
            "quality_status": record.quality_status.value, "quality_reason": record.quality_reason,
            "collected_at": record.collected_at, "updated_at": now,
        }
        for name, value in values.items():
            setattr(row, name, value)
        return outcome

    def baseline(self, symbol: str, exchange: str,
                 entry_session_date: date) -> PremarketVolumeBaseline:
        return load_baseline(self.session, symbol, exchange, entry_session_date,
                             calendar=self.calendar, source=self.source_name,
                             collector_version=self.collector_version)

    def collect_symbol(self, symbol: str, exchange: str, entry_session_date: date, *,
                       attempted: set[tuple[str, str, date]] | None = None) -> SymbolCollection:
        """Fetch only this symbol's missing required sessions, committing each one.

        The first provider failure ends this symbol's run: whatever broke it would
        almost certainly break the next request too, and the stored failure is retried
        on the next run. ``attempted`` is the run's ledger: a session is asked at most
        once per run, so a retryable answer that comes back the same waits for the
        next run instead of looping.
        """
        symbol = normalize_symbol(symbol)
        try:
            code = exchange_code(exchange)
        except MarketDataError as exc:
            return SymbolCollection(symbol, exchange, entry_session_date, (), (), None, exc.code)
        attempted = set() if attempted is None else attempted
        planned = tuple(day for day in self.missing_sessions(symbol, code, entry_session_date)
                        if (symbol, code, day) not in attempted)
        fetched: list[date] = []
        writes: list[HistoryWrite] = []
        records: list[SessionVolume] = []
        failure: str | None = None
        for day in planned:
            attempted.add((symbol, code, day))
            record = self.collect_session(symbol, code, day)
            fetched.append(day)
            records.append(record)
            writes.append(self.upsert(record))
            self.session.commit()
            if record.quality_status is SessionQuality.PROVIDER_FAILURE:
                failure = record.quality_reason
                break
        return SymbolCollection(symbol, code, entry_session_date, tuple(fetched), tuple(writes),
                                self.baseline(symbol, code, entry_session_date), failure,
                                planned, BASELINE_SESSIONS - len(planned), tuple(records))

    def collect(self, candidates: Iterable[tuple[str, str]],
                entry_session_date: date) -> CollectionRun:
        """One symbol's failure, of any kind, never ends the other symbols' collection."""
        self.required_sessions(entry_session_date)
        results: list[SymbolCollection] = []
        attempted: set[tuple[str, str, date]] = set()
        for symbol, exchange in candidates:
            try:
                results.append(self.collect_symbol(symbol, exchange, entry_session_date,
                                                   attempted=attempted))
            except Exception as exc:
                self.session.rollback()
                logger.exception("PREMARKET VOLUME HISTORY FAILED: %s", symbol)
                results.append(SymbolCollection(normalize_symbol(symbol), exchange,
                                                entry_session_date, (), (), None,
                                                type(exc).__name__))
        return CollectionRun(entry_session_date, tuple(results))


def scanner_collection_targets(session: Session, scanner_trading_date: date,
                               calendar: MarketCalendar | None = None
                               ) -> tuple[date, tuple[tuple[str, str], ...]]:
    """The entry session a completed scanner run feeds and its TOP8 (symbol, exchange).

    The run is the newest completed one for that date, the same run the Entry
    authority resolves; the exchange is the one the scanner snapshot stored.
    """
    calendar = calendar or MarketCalendar()
    run = session.scalar(select(ScannerRun).where(
        ScannerRun.trading_date == scanner_trading_date, ScannerRun.status == "COMPLETED",
    ).order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1))
    if run is None:
        return calendar.next_trading_day(scanner_trading_date), ()
    candidates = session.scalars(select(ScannerCandidate).where(
        ScannerCandidate.scanner_run_id == run.id, ScannerCandidate.is_top8.is_(True),
    ).order_by(ScannerCandidate.rank, ScannerCandidate.symbol))
    return (calendar.next_trading_day(scanner_trading_date),
            tuple((item.symbol, stored_exchange(item)) for item in candidates))


def history_statistics(session: Session, *, source: str = SOURCE,
                       collector_version: str = COLLECTOR_VERSION) -> dict[str, Any]:
    rows = list(session.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.source == source,
        PremarketVolumeSession.collector_version == collector_version)))
    listed: dict[tuple[str, str], date] = {}
    for row in rows:
        if row.quality_status == SessionQuality.COMPLETE.value:
            key = (row.symbol, row.exchange)
            listed[key] = min(listed.get(key, row.trading_date), row.trading_date)
    return {
        "source": source, "collector_version": collector_version,
        "session_rows": len(rows), "symbols": len({row.symbol for row in rows}),
        "quality_counts": dict(Counter(row.quality_status for row in rows)),
        "provider_failure_sessions": sum(
            1 for row in rows if row.quality_status in RETRYABLE_QUALITY),
        "retryable_target_not_reached_sessions": sum(
            1 for row in rows if row.quality_status == SessionQuality.TARGET_NOT_REACHED.value
            and retryable(row, listed.get((row.symbol, row.exchange)))),
    }
