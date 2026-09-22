"""Premarket volume V2 median normalisation: analytics only, V1 Production untouched.

History fixtures follow the Kiwoom minute contract: a minute without trades has no
row, a session is complete only when the source reached 04:00, and a dated request
covers one session. The event-day and cross-symbol fixtures pin the median contract.
"""

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pytest import MonkeyPatch
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  # Register every application table.
from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import Base
from app.core.exceptions import MarketDataError
from app.dev import collect_premarket_volume as collector_cli
from app.dev import observe_entry_drift as observer_cli
from app.dev.schema_fingerprint import schema_fingerprint
from app.integrations.kiwoom.client import MinuteHistoryCollection
from app.market.domain import MarketSession
from app.models.analytics import EntryDriftObservation, PremarketVolumeSession
from app.services import premarket_volume_history as history_module
from app.services.entry_drift_observer import (
    OBSERVER_SCHEMA_REVISION, EntryDriftObserver, ObservationStatus, ObserverCandidate,
    WriteOutcome, entry_drift_report,
)
from app.services.entry_management_runtime import build_premarket_context
from app.services.premarket_volume_history import (
    COLLECTOR_VERSION, HISTORY_SCHEMA_REVISION, SOURCE, HistoryWrite,
    KiwoomPremarketSessionSource, PremarketVolumeHistoryService, SessionFetch, SessionQuality,
    V2Status, assess_session, calculate_median, history_statistics, required_sessions,
    scanner_collection_targets, v2_analytics,
)
from app.strategy.config import STRATEGY_VERSION, StrategyConfig
from app.strategy.domain import DecisionType
from app.strategy.engine import StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase
from tests.test_entry_drift_observer import (
    AFTER_CLOSE, CAL, ENTRY, ET, MIN, RUN_DAY, TRADING_TABLES, DurableEnv, Provider, candidate,
    cli_database, digest, kbar, memory_session, observer, rows, seed, tape, tick,
)

HISTORY_DAYS = required_sessions(CAL, ENTRY)
CHART = "/api/us/chart"
PAGES = 12  # HTTP chart pages per complete session, as measured on Kiwoom
# A permutation of 100k..119k: median 109_500, and no two sessions alike.
BASE = tuple(100_000 + 1_000 * ((7 * index) % 20) for index in range(20))
CLEAN_MEDIAN = Decimal(109_500)


def split(volume: int, parts: int) -> list[int]:
    share = volume // parts
    return [share] * (parts - 1) + [volume - share * (parts - 1)]


def session_bars(symbol: str, day: date, volume: int, *, regular: bool = True):  # type: ignore[no-untyped-def]
    """Three PREMARKET bars with long no-trade gaps, two regular bars, one postmarket."""
    window = CAL.session(day)
    assert window
    start = datetime.combine(day, time(4), ET)
    stamps = (start, start + 193 * MIN, window.market_open - MIN)
    bars = [kbar(symbol, at, 50.0, session=MarketSession.PREMARKET, volume=part)
            for at, part in zip(stamps, split(volume, len(stamps)))]
    if regular:
        bars += [kbar(symbol, window.market_open, 50.0, volume=999_999),
                 kbar(symbol, window.market_close - MIN, 50.0, volume=999_999)]
    bars.append(kbar(symbol, window.market_close, 50.0, session=MarketSession.POSTMARKET,
                     volume=777_777))
    return bars


class HistorySource:
    """Kiwoom-shaped session source; records every market-data session request."""

    def __init__(self, volumes: dict[str, dict[date, int]], *, fail: tuple[str, ...] = (),
                 crash: tuple[str, ...] = (),
                 short: frozenset[tuple[str, date]] = frozenset()) -> None:
        self.volumes = volumes
        self.fail, self.crash = set(fail), set(crash)
        # A listed session the provider answers short: a truncated page, not the market.
        self.short = set(short)
        self.calls: list[tuple[str, str, date]] = []
        self.provider = SimpleNamespace(client=SimpleNamespace(request_counts={},
                                                               order_request_count=0))

    def fetch_session(self, symbol: str, exchange: str, start: datetime,
                      end: datetime) -> SessionFetch:
        day = start.astimezone(ET).date()
        self.calls.append((symbol, exchange, day))
        window = CAL.session(day)
        assert window and start == datetime.combine(day, time(4), ET) and end == window.market_close
        if symbol in self.crash:
            raise RuntimeError("unexpected")
        counts = self.provider.client.request_counts
        counts[CHART] = counts.get(CHART, 0) + (1 if symbol in self.fail else PAGES)
        if symbol in self.fail:
            raise MarketDataError("PROVIDER_TIMEOUT", "timed out")
        volume = self.volumes.get(symbol, {}).get(day)
        if volume is None:  # the source holds nothing that old: a pre-listing session
            return SessionFetch((), 1, False)
        if (symbol, day) in self.short:
            return SessionFetch(tuple(session_bars(symbol, day, volume))[-3:], 3, False)
        return SessionFetch(tuple(session_bars(symbol, day, volume)), PAGES, True)


def history(values: tuple[int, ...] = BASE, days: tuple[date, ...] = HISTORY_DAYS) -> dict[date, int]:
    return dict(zip(days, values))


def service(session: Session, source: HistorySource | None = None, *,
            clock: datetime = AFTER_CLOSE, **options) -> PremarketVolumeHistoryService:  # type: ignore[no-untyped-def]
    return PremarketVolumeHistoryService(session, source, calendar=CAL, clock=lambda: clock,
                                         **options)


def history_rows(session: Session) -> list[PremarketVolumeSession]:
    session.expire_all()
    return list(session.scalars(select(PremarketVolumeSession).order_by(PremarketVolumeSession.id)))


# --- Exact 20 sessions and the median ------------------------------------------------------

def test_baseline_is_the_median_of_the_exact_20_previous_sessions() -> None:
    assert len(HISTORY_DAYS) == 20 and all(CAL.is_trading_day(day) for day in HISTORY_DAYS)
    assert HISTORY_DAYS[-1] == RUN_DAY == CAL.previous_trading_day(ENTRY)
    assert HISTORY_DAYS[0] == date(2026, 8, 17) and date(2026, 9, 7) not in HISTORY_DAYS  # Labor Day
    with memory_session() as session:
        (item,) = service(session, HistorySource({"AAA": history()})).collect(
            [("AAA", "NASDAQ")], ENTRY).symbols
    baseline = item.baseline
    assert item.status is V2Status.AVAILABLE and item.exchange == "ND"
    assert baseline.median_volume == CLEAN_MEDIAN == calculate_median(BASE)
    assert (baseline.start_date, baseline.end_date) == (HISTORY_DAYS[0], HISTORY_DAYS[-1])
    assert (baseline.valid_sessions, baseline.method, baseline.collector_version) == (
        20, "MEDIAN", COLLECTOR_VERSION)


def test_no_session_at_or_after_the_entry_session_reaches_the_baseline() -> None:
    later = CAL.next_trading_day(ENTRY)
    volumes = history() | {ENTRY: 50_000_000, later: 90_000_000}
    with memory_session() as session:
        svc = service(session, HistorySource({"AAA": volumes}), clock=AFTER_CLOSE + timedelta(days=3))
        svc.collect([("AAA", "NASDAQ")], ENTRY)
        for day in (ENTRY, later):  # stored for later entries, as a live cache would be
            svc.upsert(svc.collect_session("AAA", "ND", day))
        session.commit()
        baseline = svc.baseline("AAA", "ND", ENTRY)
        assert len(history_rows(session)) == 22
    assert all(day < ENTRY for day in baseline.required_sessions)
    assert baseline.median_volume == CLEAN_MEDIAN


def test_an_unfinished_session_is_never_collected() -> None:
    with memory_session() as session:
        svc = service(session, HistorySource({"AAA": history()}),
                      clock=CAL.session(RUN_DAY).market_close - MIN)
        with pytest.raises(ValueError, match="has not completed"):
            svc.collect_session("AAA", "ND", RUN_DAY)


@pytest.mark.parametrize("gap", ["never_collected", "target_not_reached"])
def test_nineteen_sessions_are_insufficient(gap: str) -> None:
    oldest = HISTORY_DAYS[0]
    volumes = {day: value for day, value in history().items() if day != oldest}
    with memory_session() as session:
        svc = service(session, HistorySource({"AAA": volumes}))
        if gap == "never_collected":
            for day in HISTORY_DAYS[1:]:
                svc.upsert(svc.collect_session("AAA", "ND", day))
            session.commit()
            baseline = svc.baseline("AAA", "ND", ENTRY)
            assert (baseline.missing_sessions, baseline.invalid_sessions) == ((oldest,), ())
        else:
            baseline = svc.collect([("AAA", "NASDAQ")], ENTRY).symbols[0].baseline
            assert (baseline.missing_sessions, baseline.invalid_sessions) == ((), (oldest,))
    assert baseline.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY
    assert baseline.valid_sessions == 19 and baseline.median_volume is None
    assert v2_analytics(Decimal(500_000), baseline).ratio is None


def test_an_older_session_never_replaces_a_missing_one() -> None:
    older = CAL.previous_trading_day(HISTORY_DAYS[0])
    missing = HISTORY_DAYS[10]
    volumes = history() | {older: 100_000}
    del volumes[missing]
    with memory_session() as session:
        svc = service(session, HistorySource({"AAA": volumes}))
        for day in (older, *HISTORY_DAYS):
            svc.upsert(svc.collect_session("AAA", "ND", day))
        session.commit()
        complete = sum(1 for row in history_rows(session) if row.quality_status == "COMPLETE")
        baseline = svc.baseline("AAA", "ND", ENTRY)
    assert complete == 20  # 19 required sessions plus the older one
    # The older COMPLETE session proves the listing, so the short one is awaiting a retry.
    assert older not in baseline.required_sessions and baseline.missing_sessions == (missing,)
    assert baseline.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY and baseline.median_volume is None


# --- Session completeness -------------------------------------------------------------------

def test_minutes_without_trades_are_not_incompleteness() -> None:
    day = HISTORY_DAYS[-1]
    window = CAL.session(day)
    with memory_session() as session:
        record = service(session, HistorySource({"AAA": {day: 90_001}})).collect_session("AAA", "ND", day)
    assert record.quality_status is SessionQuality.COMPLETE
    # Only 04:00 <= t < 09:30 counts: regular and postmarket volume never leak in.
    assert (record.premarket_volume, record.bar_count, record.regular_bar_count) == (90_001, 3, 2)
    assert record.first_timestamp == datetime.combine(day, time(4), ET)
    assert record.last_timestamp == window.market_open - MIN
    assert (record.pages_used, record.target_reached) == (12, True)


def _assessed(bars, *, reached: bool = True):  # type: ignore[no-untyped-def]
    day = HISTORY_DAYS[-1]
    return assess_session("AAA", "ND", day, CAL.session(day), SessionFetch(tuple(bars), 3, reached),
                          collected_at=AFTER_CLOSE)


def test_target_not_reached_is_rejected_even_with_bars() -> None:
    record = _assessed(session_bars("AAA", HISTORY_DAYS[-1], 50_000), reached=False)
    assert record.quality_status is SessionQuality.TARGET_NOT_REACHED


def test_session_identity_and_coverage_are_checked() -> None:
    day = HISTORY_DAYS[-1]
    window = CAL.session(day)
    good = session_bars("AAA", day, 50_000)
    foreign = [*good, kbar("BBB", window.market_open - 60 * MIN, 50.0, session=MarketSession.PREMARKET)]
    mislabelled = [*good, kbar("AAA", window.market_open - 5 * MIN, 50.0)]  # REGULAR before the open
    other_day = [*good, kbar("AAA", CAL.session(HISTORY_DAYS[-2]).market_close, 50.0,
                             session=MarketSession.POSTMARKET)]
    no_premarket = [bar for bar in good if bar.session is not MarketSession.PREMARKET]
    no_regular = session_bars("AAA", day, 50_000, regular=False)
    assert [(_assessed(bars).quality_status, _assessed(bars).quality_reason) for bars in (
        foreign, mislabelled, other_day, no_premarket, no_regular)] == [
        (SessionQuality.SESSION_IDENTITY_MISMATCH, "SYMBOL"),
        (SessionQuality.SESSION_IDENTITY_MISMATCH, "SESSION_LABEL"),
        (SessionQuality.SESSION_IDENTITY_MISMATCH, "DATE"),
        (SessionQuality.NO_PREMARKET_BARS, None),
        (SessionQuality.NO_REGULAR_BARS, None)]


# --- Median robustness and cross-symbol normalisation -----------------------------------------

EVENT = BASE[:19] + (30 * 109_500,)  # one catalyst session at 30x the usual median


def _event_baseline(session: Session):  # type: ignore[no-untyped-def]
    return service(session, HistorySource({"AAA": history(EVENT)})).collect(
        [("AAA", "NASDAQ")], ENTRY).symbols[0].baseline


def _event_contained(value: Decimal) -> bool:
    return abs(value - CLEAN_MEDIAN) / CLEAN_MEDIAN <= Decimal("0.01")


def test_an_event_day_barely_moves_the_median_baseline() -> None:
    with memory_session() as session:
        baseline = _event_baseline(session)
    assert baseline.status is V2Status.AVAILABLE and _event_contained(baseline.median_volume)
    assert baseline.median_volume != Decimal(mean(Decimal(value) for value in EVENT))
    ratio = v2_analytics(Decimal(164_250), baseline).ratio  # 1.5x the usual session
    assert Decimal("1.48") <= ratio <= Decimal("1.52")


def test_a_mean_baseline_is_caught_by_the_event_guard(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(history_module, "calculate_median",
                        lambda volumes: Decimal(mean(Decimal(value) for value in volumes)))
    with memory_session() as session:
        baseline = _event_baseline(session)
    assert not _event_contained(baseline.median_volume)
    assert v2_analytics(Decimal(164_250), baseline).ratio < Decimal("0.7")  # the distortion


def test_the_same_relationship_to_own_history_gives_the_same_ratio() -> None:
    scales = {"AAPL": 400, "AMD": 37, "ORCL": 3}
    exchanges = {"AAPL": "NASDAQ", "AMD": "NASDAQ", "ORCL": "NYSE"}
    volumes = {symbol: history(tuple(scale * value for value in BASE)) for symbol, scale in scales.items()}
    with memory_session() as session:
        run = service(session, HistorySource(volumes)).collect(list(exchanges.items()), ENTRY)
    medians = {item.symbol: item.baseline.median_volume for item in run.symbols}
    ratios = {item.symbol: v2_analytics(Decimal(scales[item.symbol] * 197_100), item.baseline).ratio
              for item in run.symbols}
    assert len(set(medians.values())) == 3  # absolute shares differ by two orders
    assert set(ratios.values()) == {Decimal("1.8")}
    assert {item.symbol: item.exchange for item in run.symbols}["ORCL"] == "NY"


# --- Cache and API cost ------------------------------------------------------------------------

def test_bootstrap_then_zero_then_one_new_session_across_a_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'history.sqlite3'}"
    later = CAL.next_trading_day(ENTRY)
    volumes = {"AAA": history() | {ENTRY: 118_500}}
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    source = HistorySource(volumes)
    with Session(engine) as session:
        first = service(session, source).collect([("AAA", "NASDAQ")], ENTRY)
        assert len(source.calls) == first.market_data_sessions == 20
        second = service(session, source).collect([("AAA", "NASDAQ")], ENTRY)
        assert len(source.calls) == 20 and second.market_data_sessions == 0
        assert second.symbols[0].baseline == first.symbols[0].baseline
    engine.dispose()

    engine = create_engine(url)  # a new process: nothing in memory, the same file
    restarted = HistorySource(volumes)
    try:
        with Session(engine) as session:
            service(session, restarted).collect([("AAA", "NASDAQ")], ENTRY)
            assert restarted.calls == []
            rolled = service(session, restarted).collect([("AAA", "NASDAQ")], later)
    finally:
        engine.dispose()
    assert restarted.calls == [("AAA", "ND", ENTRY)]
    assert rolled.symbols[0].baseline.required_sessions == (*HISTORY_DAYS[1:], ENTRY)
    assert rolled.symbols[0].status is V2Status.AVAILABLE


def test_one_symbol_failure_leaves_the_other_candidates_complete() -> None:
    symbols = ("AAPL", "AMD", "ORCL", "MSFT", "NVDA", "META", "MU", "SPCX")
    volumes = {symbol: history() for symbol in symbols}
    with memory_session() as session:
        broken = HistorySource(volumes, fail=("MU",), crash=("META",))
        run = service(session, broken).collect([(symbol, "NASDAQ") for symbol in symbols], ENTRY)
        outcome = {item.symbol: (item.status, item.failure) for item in run.symbols}
        complete = Counter(row.symbol for row in history_rows(session) if row.quality_status == "COMPLETE")
        assert outcome["MU"] == (V2Status.INSUFFICIENT_PREMARKET_HISTORY, "PROVIDER_TIMEOUT")
        assert outcome["META"] == (V2Status.ANALYTICS_ERROR, "RuntimeError")
        healthy = set(symbols) - {"MU", "META"}
        assert all(outcome[symbol] == (V2Status.AVAILABLE, None) for symbol in healthy)
        assert complete == {symbol: 20 for symbol in healthy}
        # A failing symbol stops at its first failure instead of spending 20 requests.
        assert Counter(call[0] for call in broken.calls if call[0] in {"MU", "META"}) == {"MU": 1, "META": 1}

        recovered = HistorySource(volumes)
        retry = service(session, recovered).collect([(symbol, "NASDAQ") for symbol in symbols], ENTRY)
    assert Counter(call[0] for call in recovered.calls) == {"MU": 20, "META": 20}
    assert all(item.status is V2Status.AVAILABLE for item in retry.symbols)


def test_upsert_is_idempotent_versioned_and_never_erases_a_complete_session() -> None:
    day = HISTORY_DAYS[-1]
    with memory_session() as session:
        svc = service(session, HistorySource({"AAA": {day: 70_000}}))
        record = svc.collect_session("AAA", "ND", day)
        assert svc.upsert(record) is HistoryWrite.INSERTED
        session.commit()
        assert svc.upsert(record) is HistoryWrite.UPDATED
        failed = service(session, HistorySource({}, fail=("AAA",))).collect_session("AAA", "ND", day)
        assert failed.quality_status is SessionQuality.PROVIDER_FAILURE
        assert svc.upsert(failed) is HistoryWrite.KEPT_COMPLETE
        session.commit()
        assert [(row.premarket_volume, row.quality_status) for row in history_rows(session)] == [
            (70_000, "COMPLETE")]

        # Another collector contract keeps its own rows and is never read as this one.
        older = service(session, HistorySource({"AAA": history()}),
                        collector_version="premarket_volume_collector_v0")
        older.collect([("AAA", "NASDAQ")], ENTRY)
        assert older.baseline("AAA", "ND", ENTRY).status is V2Status.AVAILABLE
        current = svc.baseline("AAA", "ND", ENTRY)
        assert Counter(row.collector_version for row in history_rows(session)) == {
            COLLECTOR_VERSION: 1, "premarket_volume_collector_v0": 20}
    assert current.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY
    assert len(current.missing_sessions) == 19 and current.valid_sessions == 1


def test_kiwoom_source_reports_the_collection_it_just_made() -> None:
    day = HISTORY_DAYS[-1]
    window = CAL.session(day)
    start = datetime.combine(day, time(4), ET)
    bars = session_bars("AAA", day, 1_000)
    provider = SimpleNamespace(bound={}, calls=[], client=SimpleNamespace(
        last_minute_collection=MinuteHistoryCollection((), 12, True, False)))
    provider.bind_exchange = lambda symbol, exchange: provider.bound.update({symbol: exchange})
    provider.get_minute_bars = lambda symbols, start=None, end=None, session=None: (
        provider.calls.append((tuple(symbols), start, end, session)) or bars)
    fetch = KiwoomPremarketSessionSource(provider).fetch_session("AAA", "ND", start, window.market_close)
    assert provider.bound == {"AAA": "ND"}
    assert provider.calls == [(("AAA",), start, window.market_close, None)]
    assert (fetch.pages_used, fetch.target_reached, len(fetch.bars)) == (12, True, 6)


# --- New listings ------------------------------------------------------------------------------

def test_new_listing_fails_closed_for_v2_while_v1_trades_as_before() -> None:
    listed = HISTORY_DAYS[8:]
    with memory_session() as session:
        seed(session, ("SPCX",))
        source = HistorySource({"SPCX": {day: 80_000 for day in listed}})
        (item,) = service(session, source).collect([("SPCX", "NASDAQ")], ENTRY).symbols
        again = service(session, source).collect([("SPCX", "NASDAQ")], ENTRY)
        result = observer(session, Provider(tape("SPCX"))).observe(
            ObserverCandidate(ENTRY, 1, 1, 1, "SPCX", "NASDAQ"))
    assert item.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY
    assert item.baseline.valid_sessions == 12 and item.baseline.invalid_sessions == HISTORY_DAYS[:8]
    assert again.market_data_sessions == 0  # the source's own pre-listing answer is kept
    assert result.status is ObservationStatus.VALID
    assert result.v2.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY
    assert result.v2.ratio is None and result.v2.projections() is None


# --- Observer integration ----------------------------------------------------------------------

HALF = (40_000,) * 10 + (60_000,) * 10  # median 50_000; the tape's PREMARKET volume is 100_000


def test_observer_records_v2_beside_v1() -> None:
    with memory_session() as session:
        seed(session)
        service(session, HistorySource({"AAA": history(HALF)})).collect([("AAA", "NASDAQ")], ENTRY)
        run = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        (row,) = rows(session)
        report = entry_drift_report(session)
    (result,) = run.results
    assert result.status is ObservationStatus.VALID
    assert (result.v2.status, result.v2.ratio) == (V2Status.AVAILABLE, Decimal(2))
    assert row.v1_volume_ratio == Decimal("0.1")  # 100_000 / 1_000_000 average daily volume
    assert (row.v2_status, row.v2_median_ratio, row.v2_today_premarket_volume, row.v2_baseline_volume) == (
        "AVAILABLE", Decimal(2), Decimal(100_000), Decimal(50_000))
    assert (row.v2_baseline_method, row.v2_baseline_sessions, row.v2_collector_version) == (
        "MEDIAN", 20, COLLECTOR_VERSION)
    assert (row.v2_baseline_start_date, row.v2_baseline_end_date) == (HISTORY_DAYS[0], HISTORY_DAYS[-1])
    assert row.v2_projections_json == {"1.00": True, "1.25": True, "1.50": True, "2.00": True,
                                       "2.50": False, "3.00": False}
    v2 = report["premarket_volume_v2"]
    assert (v2["candidate_count"], v2["v2_available"], v2["insufficient_history"]) == (1, 1, 0)
    assert v2["ratio"]["median"] == Decimal(2) and v2["history"]["session_rows"] == 20
    assert {item["threshold"]: item["would_pass"] for item in v2["threshold_projections"]} == {
        "1.00": 1, "1.25": 1, "1.50": 1, "2.00": 1, "2.50": 0, "3.00": 0}


def test_v2_statistics_report_distribution_and_unavailability() -> None:
    symbols = ("AAA", "BBB", "CCC", "DDD")
    with memory_session() as session:
        seed(session, symbols)
        service(session, HistorySource({"AAA": history(HALF), "BBB": history(tuple(v * 2 for v in HALF)),
                                        "CCC": history(tuple(v // 4 for v in HALF))})).collect(
            [(symbol, "NASDAQ") for symbol in symbols[:3]], ENTRY)
        provider = Provider(*(tape(symbol) for symbol in symbols[:3]), fail=("DDD",))
        observer(session, provider).observe_date(ENTRY, persist=True)
        v2 = entry_drift_report(session)["premarket_volume_v2"]
    # Ratios: AAA 2x, BBB 1x, CCC 8x; DDD has no history and no minute data.
    assert (v2["candidate_count"], v2["v2_available"], v2["insufficient_history"]) == (4, 3, 1)
    assert v2["observation_provider_failures"] == 1
    assert v2["ratio"]["median"] == Decimal(2) and v2["ratio"]["max"] == Decimal(8)
    assert set(v2["ratio"]) == {"median", "p75", "p90", "p95", "min", "max"}
    assert {item["threshold"]: item["would_pass"] for item in v2["threshold_projections"]} == {
        "1.00": 3, "1.25": 2, "1.50": 2, "2.00": 2, "2.50": 1, "3.00": 1}


@pytest.mark.parametrize("breakout", [15, 60])
def test_v2_never_changes_the_production_v1_replay(tmp_path: Path, breakout: int) -> None:
    env = DurableEnv(tmp_path)
    plain, with_history = Provider(tape(breakout=breakout)), Provider(tape(breakout=breakout))
    (approved,) = env.service.approved_candidates_for_entry_session(ENTRY)
    with env.factory() as session:
        (item,) = observer(session, plain).candidates(ENTRY)
        without = EntryDriftObserver(session, plain, clock=lambda: AFTER_CLOSE,  # type: ignore[arg-type]
                                     v2_baseline=lambda *_: None).observe(item)
        service(session, HistorySource({"AAA": history((1,) * 20)})).collect([("AAA", "NASDAQ")], ENTRY)
        observed = observer(session, with_history).observe(item)
    assert without.v2 is None and observed.v2.ratio == Decimal(100_000)
    assert replace(observed, v2=None) == without
    assert with_history.minute_calls == plain.minute_calls  # V2 made no market-data request

    production: list[tuple[datetime, str]] = []
    at = tick(0)
    while True:
        outcome = env.service.evaluate(approved, with_history, as_of=at)
        if outcome.state.phase in {StrategyPhase.ENTRY_SIGNALLED, StrategyPhase.NO_TRADE}:
            break
        production.append((at, outcome.reason))
        at += MIN
    assert observed.trace[:-1] == tuple(production) and observed.trace[-1][0] == at


def test_v1_rejection_stands_whatever_v2_says() -> None:
    bars = tape()
    premarket = bars[1]
    assert premarket.session is MarketSession.PREMARKET
    bars[1] = kbar("AAA", premarket.timestamp, 105.0, session=MarketSession.PREMARKET, volume=10_000)
    with memory_session() as session:
        seed(session)
        service(session, HistorySource({"AAA": history((1_000,) * 20)})).collect([("AAA", "NASDAQ")], ENTRY)
        result = observer(session, Provider(bars)).observe(candidate())
    assert (result.status, result.quality_reason) == (ObservationStatus.INVALID_PREMARKET,
                                                      "LOW_PREMARKET_VOLUME")
    assert result.volume_ratio == Decimal("0.01")
    assert result.v2.ratio == Decimal(10) and all(result.v2.projections().values())


def test_v2_failure_is_isolated_from_the_v1_result() -> None:
    def broken(*_args):  # type: ignore[no-untyped-def]
        raise RuntimeError("history unreadable")

    with memory_session() as session:
        seed(session)
        reference = observer(session, Provider(tape())).observe(candidate())
        failed = EntryDriftObserver(session, Provider(tape()), clock=lambda: AFTER_CLOSE,  # type: ignore[arg-type]
                                    v2_baseline=broken).observe(candidate())
    assert failed.v2.status is V2Status.ANALYTICS_ERROR
    assert replace(failed, v2=None) == replace(reference, v2=None)


def test_production_strategy_contract_is_still_v1() -> None:
    config = StrategyConfig()
    assert config.premarket_volume_ratio_min == Decimal("0.05") and STRATEGY_VERSION == "strategy_v0"
    visible = [bar for bar in tape() if bar.timestamp <= tick(0) and bar.available_at <= tick(0)]
    built = build_premarket_context(CAL, "AAA", ENTRY, visible, Provider(tape()), tick(0))
    # V1 stays today PREMARKET / 20-day average daily volume, not the V2 median.
    assert built.context.volume_ratio == Decimal(100_000) / Decimal(1_000_000)
    app_root = Path(history_module.__file__).parents[1]
    trading = [*app_root.glob("strategy/*.py"), *app_root.glob("risk/*.py"),
               *(app_root / "services" / name for name in (
                   "entry_management_runtime.py", "strategy.py", "risk.py", "position_lifecycle.py",
                   "position_management_runtime.py", "end_of_day_lifecycle.py", "execution.py"))]
    for path in trading:
        source = path.read_text()
        assert "premarket_volume_history" not in source and "v2_median" not in source, path


# --- Collector isolation -----------------------------------------------------------------------

def test_collector_writes_only_premarket_volume_history(tmp_path: Path) -> None:
    env = DurableEnv(tmp_path)
    before = {model.__tablename__: env.count(model) for model in (*TRADING_TABLES, EntryDriftObservation)}
    with env.factory() as session:
        entry, targets = scanner_collection_targets(session, RUN_DAY, CAL)
        run = service(session, HistorySource({"AAA": history()})).collect(targets, entry)
    assert (entry, targets) == (ENTRY, (("AAA", "NASDAQ"),))
    assert run.symbols[0].status is V2Status.AVAILABLE
    assert {model.__tablename__: env.count(model) for model in (*TRADING_TABLES, EntryDriftObservation)} == before
    assert env.count(PremarketVolumeSession) == 20
    assert env.runtime.broker.get_open_orders() == () and env.runtime.broker.get_position("AAA") is None
    source = Path(history_module.__file__).read_text() + Path(collector_cli.__file__).read_text()
    for forbidden in ("EntryLifecycleService", "EntryManagementRuntime", "run_once", "SimBroker",
                      "submit_order", "execute_entry", "reserve_entry", "StrategyStateRepository",
                      "DailyRiskRepository", ".activate(", "create_all", "create_db_engine",
                      "HumanDecisionRecord", "DailySymbolState", "StrategyStateRecord",
                      "ExecutionOrderRecord", "ExecutionFillRecord", "SimulationPositionRecord",
                      "GPTAnalysis", "StrategyConfig"):
        assert forbidden not in source, forbidden


# --- CLIs --------------------------------------------------------------------------------------

def run_collector(path: Path, *extra: str, source: HistorySource | None = None,
                  clock: datetime = AFTER_CLOSE) -> int:
    return collector_cli.main(["--scanner-date", RUN_DAY.isoformat(), "--database", str(path), *extra],
                              source_factory=lambda: source or HistorySource({"AAA": history()}),
                              clock=lambda: clock)


def test_collector_cli_dry_run_makes_no_request_and_mutates_nothing(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    before = digest(path)
    built: list[bool] = []
    assert collector_cli.main(["--scanner-date", RUN_DAY.isoformat(), "--database", str(path)],
                              source_factory=lambda: built.append(True),  # type: ignore[arg-type, return-value]
                              clock=lambda: AFTER_CLOSE) == 0
    assert built == [] and digest(path) == before


def test_collector_cli_writes_then_reuses_the_durable_cache(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    source = HistorySource({"AAA": history()})
    assert run_collector(path, "--write", source=source) == 0
    assert run_collector(path, "--write", source=source) == 0
    assert len(source.calls) == 20
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM premarket_volume_sessions")).scalar() == 20
    engine.dispose()


def test_collector_cli_refuses_before_any_request(tmp_path: Path) -> None:
    built: list[bool] = []
    factory = lambda: built.append(True)  # noqa: E731
    stale = cli_database(tmp_path, revision="20260915_0015")
    with pytest.raises(SystemExit, match="revision"):
        collector_cli.main(["--scanner-date", RUN_DAY.isoformat(), "--database", str(stale), "--write"],
                           source_factory=factory, clock=lambda: AFTER_CLOSE)  # type: ignore[arg-type]
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    current = cli_database(fresh_dir)
    open_session = CAL.session(RUN_DAY).market_close - MIN
    with pytest.raises(SystemExit, match="has not completed"):
        collector_cli.main(["--scanner-date", RUN_DAY.isoformat(), "--database", str(current), "--write"],
                           source_factory=factory, clock=lambda: open_session)  # type: ignore[arg-type]
    assert collector_cli.main(["--database", str(current), "--report"], source_factory=factory) == 0  # type: ignore[arg-type]
    assert built == []


def test_observer_cli_dry_run_on_a_pre_v2_database_still_replays_v1(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE premarket_volume_sessions"))
    engine.dispose()
    before = digest(path)
    provider = Provider(tape())
    assert observer_cli.main(["--date", ENTRY.isoformat(), "--database", str(path)],
                             provider_factory=lambda: provider, clock=lambda: AFTER_CLOSE) == 0  # type: ignore[arg-type, return-value]
    assert provider.minute_calls and digest(path) == before
    with pytest.raises(SystemExit, match="predates V2"):
        observer_cli.main(["--database", str(path), "--report"])


# --- Pre-deploy hardening: TARGET_NOT_REACHED recovery ------------------------------------------

def _qualities(session: Session) -> dict[date, str]:
    return {row.trading_date: row.quality_status for row in history_rows(session)}


def test_transient_target_not_reached_is_retried_at_most_once_per_run() -> None:
    middle, newest = HISTORY_DAYS[9], HISTORY_DAYS[-1]
    flaky = HistorySource({"AAA": history()}, short=frozenset({("AAA", middle), ("AAA", newest)}))
    with memory_session() as session:
        # A duplicated candidate in one run still asks each session once.
        (first, duplicate) = service(session, flaky).collect(
            [("AAA", "NASDAQ"), ("AAA", "NASDAQ")], ENTRY).symbols
        assert len(flaky.calls) == 20 and duplicate.fetched_sessions == ()
        assert first.quality_count(SessionQuality.TARGET_NOT_REACHED) == 2
        # Surrounded by COMPLETE sessions: awaiting a retry, not a final invalid answer.
        assert set(first.baseline.missing_sessions) == {middle, newest}
        assert first.baseline.invalid_sessions == () and first.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY
        assert history_statistics(session)["retryable_target_not_reached_sessions"] == 2

        still = service(session, flaky).collect([("AAA", "NASDAQ")], ENTRY)
        assert flaky.calls[20:] == [("AAA", "ND", newest), ("AAA", "ND", middle)]
        # Still short: kept typed, never a silent COMPLETE, and the run stops asking.
        assert {day: q for day, q in _qualities(session).items() if q != "COMPLETE"} == {
            middle: "TARGET_NOT_REACHED", newest: "TARGET_NOT_REACHED"}
        assert still.symbols[0].status is V2Status.INSUFFICIENT_PREMARKET_HISTORY

        recovered = HistorySource({"AAA": history()})
        final = service(session, recovered).collect([("AAA", "NASDAQ")], ENTRY)
    assert recovered.calls == [("AAA", "ND", newest), ("AAA", "ND", middle)]
    assert final.symbols[0].writes == (HistoryWrite.UPDATED, HistoryWrite.UPDATED)
    assert final.symbols[0].status is V2Status.AVAILABLE
    assert final.symbols[0].baseline.median_volume == CLEAN_MEDIAN


def test_pre_listing_target_not_reached_stays_final_beside_a_retried_gap() -> None:
    listed, gap = HISTORY_DAYS[8:], HISTORY_DAYS[14]
    source = HistorySource({"SPCX": {day: 80_000 for day in listed}},
                           short=frozenset({("SPCX", gap)}))
    with memory_session() as session:
        (item,) = service(session, source).collect([("SPCX", "NASDAQ")], ENTRY).symbols
        assert item.baseline.invalid_sessions == HISTORY_DAYS[:8]  # before the listing: final
        assert item.baseline.missing_sessions == (gap,)            # after it: retryable
        service(session, source).collect([("SPCX", "NASDAQ")], ENTRY)
        stats = history_statistics(session)
    assert source.calls[20:] == [("SPCX", "ND", gap)]  # no pre-listing session is asked again
    assert stats["retryable_target_not_reached_sessions"] == 1
    assert stats["quality_counts"] == {"COMPLETE": 11, "TARGET_NOT_REACHED": 9}


# --- Pre-deploy hardening: final quality monotonicity -------------------------------------------

def test_complete_session_never_downgrades() -> None:
    day = HISTORY_DAYS[-1]
    with memory_session() as session:
        svc = service(session, HistorySource({"AAA": {day: 70_000}}))
        assert svc.upsert(svc.collect_session("AAA", "ND", day)) is HistoryWrite.INSERTED
        session.commit()
        short = service(session, HistorySource({"AAA": {day: 70_000}}, short=frozenset({("AAA", day)})))
        failing = service(session, HistorySource({"AAA": {day: 70_000}}, fail=("AAA",)))
        worse = (short.collect_session("AAA", "ND", day), failing.collect_session("AAA", "ND", day))
        assert [record.quality_status for record in worse] == [SessionQuality.TARGET_NOT_REACHED,
                                                               SessionQuality.PROVIDER_FAILURE]
        assert [svc.upsert(record) for record in worse] == [HistoryWrite.KEPT_COMPLETE] * 2
        session.commit()
        assert [(row.premarket_volume, row.quality_status, row.target_reached)
                for row in history_rows(session)] == [(70_000, "COMPLETE", True)]
        assert svc.missing_sessions("AAA", "ND", ENTRY)[0] != day  # never asked again


def _v2_row(session: Session) -> tuple:
    (row,) = rows(session)
    return (row.status, row.v2_status, row.v2_median_ratio, row.v2_baseline_volume,
            row.v2_projections_json)


def _broken(*_args):  # type: ignore[no-untyped-def]
    raise RuntimeError("history unreadable")


def test_available_v2_never_downgrades_on_a_rerun() -> None:
    with memory_session() as session:
        seed(session)
        service(session, HistorySource({"AAA": history(HALF)})).collect([("AAA", "NASDAQ")], ENTRY)
        first = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        available = _v2_row(session)
        errored = EntryDriftObserver(session, Provider(tape()), clock=lambda: AFTER_CLOSE,  # type: ignore[arg-type]
                                     v2_baseline=_broken).observe_date(ENTRY, persist=True)
        assert _v2_row(session) == available
        session.execute(delete(PremarketVolumeSession))
        session.commit()
        thinner = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        assert _v2_row(session) == available
    assert first.writes == (WriteOutcome.INSERTED,) and available[1:3] == ("AVAILABLE", Decimal(2))
    assert errored.results[0].v2.status is V2Status.ANALYTICS_ERROR
    assert thinner.results[0].v2.status is V2Status.INSUFFICIENT_PREMARKET_HISTORY
    assert errored.writes == thinner.writes == (WriteOutcome.UPDATED_V2_KEPT,)


def test_available_v2_survives_a_non_final_v1_rerun() -> None:
    with memory_session() as session:
        seed(session)
        service(session, HistorySource({"AAA": history(HALF)})).collect([("AAA", "NASDAQ")], ENTRY)
        observer(session, Provider(tape(bars_until=15))).observe_date(ENTRY, persist=True)
        assert _v2_row(session)[:2] == ("INSUFFICIENT_MINUTE_DATA", "AVAILABLE")
        failed = observer(session, Provider(tape(), fail=("AAA",))).observe_date(ENTRY, persist=True)
        # V1 moved to the newer non-final status; the AVAILABLE V2 answer stayed.
        assert _v2_row(session)[:3] == ("PROVIDER_FAILURE", "AVAILABLE", Decimal(2))
    assert failed.results[0].v2.status is V2Status.NO_TODAY_PREMARKET_VOLUME
    assert failed.writes == (WriteOutcome.UPDATED_V2_KEPT,)


def test_insufficient_and_errored_v2_upgrade_to_available() -> None:
    with memory_session() as session:
        seed(session)
        statuses = []
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        statuses.append(_v2_row(session)[1])
        EntryDriftObserver(session, Provider(tape()), clock=lambda: AFTER_CLOSE,  # type: ignore[arg-type]
                           v2_baseline=_broken).observe_date(ENTRY, persist=True)
        statuses.append(_v2_row(session)[1])
        service(session, HistorySource({"AAA": history(HALF)})).collect([("AAA", "NASDAQ")], ENTRY)
        upgraded = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        statuses.append(_v2_row(session)[1])
        # Monotonicity is scoped to the collector contract: another contract's answer
        # is not final for this one.
        (row,) = rows(session)
        row.v2_collector_version = "premarket_volume_collector_v0"
        session.commit()
        EntryDriftObserver(session, Provider(tape()), clock=lambda: AFTER_CLOSE,  # type: ignore[arg-type]
                           v2_baseline=_broken).observe_date(ENTRY, persist=True)
        statuses.append(_v2_row(session)[1])
    assert statuses == ["INSUFFICIENT_PREMARKET_HISTORY", "ANALYTICS_ERROR", "AVAILABLE",
                        "ANALYTICS_ERROR"]
    assert upgraded.writes == (WriteOutcome.UPDATED,)


# --- Pre-deploy hardening: exact ratio storage and projection consistency ------------------------

MEDIAN_1E11 = 100_000_000_000


@pytest.mark.parametrize("today, passes", [
    (124_999_999_999, False),  # 1.24999999999: REAL storage reads this back as 1.25
    (125_000_000_000, True),   # exactly 1.25
    (125_000_000_001, True),   # 1.25000000001
])
def test_stored_ratio_projection_and_report_agree_at_the_125_boundary(
        tmp_path: Path, today: int, passes: bool) -> None:
    bars = tape()
    bars[1] = kbar("AAA", bars[1].timestamp, 105.0, session=MarketSession.PREMARKET, volume=today)
    url = f"sqlite:///{tmp_path / 'boundary.sqlite3'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        seed(session)
        service(session, HistorySource({"AAA": history((MEDIAN_1E11,) * 20)})).collect(
            [("AAA", "NASDAQ")], ENTRY)
        observer(session, Provider(bars)).observe_date(ENTRY, persist=True)
    engine.dispose()

    engine = create_engine(url)  # everything below is read back from the file
    try:
        with Session(engine) as session:
            (row,) = session.scalars(select(EntryDriftObservation)).all()
            raw = session.execute(text("SELECT typeof(v2_median_ratio), v2_median_ratio "
                                       "FROM entry_drift_observations")).one()
            report = entry_drift_report(session)["premarket_volume_v2"]
    finally:
        engine.dispose()
    exact = Decimal(today) / Decimal(MEDIAN_1E11)
    assert row.v2_median_ratio == exact and tuple(raw) == ("text", str(exact))
    assert (row.v2_today_premarket_volume, row.v2_baseline_volume) == (Decimal(today), Decimal(MEDIAN_1E11))
    stored = row.v2_projections_json["1.25"]
    recomputed = row.v2_median_ratio >= Decimal("1.25")
    reported = {item["threshold"]: item["would_pass"] for item in report["threshold_projections"]}
    assert stored is recomputed is passes and reported["1.25"] == int(passes)
    assert (report["ratio"]["min"] >= Decimal("1.25")) is passes


# --- Pre-deploy hardening: durable cache without process memory ----------------------------------

def test_sql_inserted_complete_rows_serve_a_new_service_with_zero_requests(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'sql-only.sqlite3'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    at = AFTER_CLOSE.astimezone(timezone.utc).isoformat()
    with engine.begin() as connection:  # no service, no ORM: this process never saw these rows
        for day, volume in history().items():
            connection.execute(text(
                "INSERT INTO premarket_volume_sessions (symbol, exchange, trading_date, source, "
                "collector_version, premarket_volume, bar_count, regular_bar_count, pages_used, "
                "target_reached, quality_status, collected_at, created_at, updated_at) VALUES "
                "('AAA', 'ND', :day, :source, :version, :volume, 3, 2, 12, 1, 'COMPLETE', :at, :at, :at)"),
                {"day": day.isoformat(), "source": SOURCE, "version": COLLECTOR_VERSION,
                 "volume": volume, "at": at})
    engine.dispose()

    engine = create_engine(url)
    source = HistorySource({})  # it would answer every session pre-listing
    try:
        with Session(engine) as session:
            run = service(session, source).collect([("AAA", "NASDAQ")], ENTRY)
    finally:
        engine.dispose()
    assert source.calls == [] and source.provider.client.request_counts == {}
    assert run.summary() == {"symbols_requested": 1, "sessions_requested": 0, "sessions_fetched": 0,
                             "cache_hits": 20, "provider_failures": 0, "target_not_reached": 0}
    assert run.symbols[0].status is V2Status.AVAILABLE
    assert run.symbols[0].baseline.median_volume == CLEAN_MEDIAN


# --- Pre-deploy hardening: observer context ------------------------------------------------------

class UndecidedEngine(StrategyV0Engine):
    """Never decides: every tick holds, so the replay runs out of session."""

    def evaluate_entry(self, **kwargs):  # type: ignore[no-untyped-def]
        evaluated = super().evaluate_entry(**kwargs)
        if evaluated.decision.decision not in {DecisionType.ENTER, DecisionType.NO_TRADE}:
            return evaluated
        return replace(evaluated, state=kwargs["state"],
                       decision=replace(evaluated.decision, decision=DecisionType.HOLD))


def test_no_entry_decision_carries_the_known_premarket_volume_to_v2() -> None:
    with memory_session() as session:
        seed(session)
        service(session, HistorySource({"AAA": history(HALF)})).collect([("AAA", "NASDAQ")], ENTRY)
        result = observer(session, Provider(tape()), engine=UndecidedEngine()).observe(candidate())
    assert (result.status, result.quality_reason) == (ObservationStatus.INSUFFICIENT_MINUTE_DATA,
                                                      "NO_ENTRY_DECISION")
    assert result.signal_at is None and result.drift_pct is None and result.projections == ()
    assert result.trace[-1][0] <= CAL.session(ENTRY).market_close and len(result.trace) == 390
    assert (result.premarket_volume, result.volume_ratio, result.gate_reason) == (
        Decimal(100_000), Decimal("0.1"), "PREMARKET_PASS")
    assert (result.v2.status, result.v2.ratio) == (V2Status.AVAILABLE, Decimal(2))


# --- Pre-deploy hardening: single-symbol collector and request accounting ------------------------

TOP3 = ("AAPL", "ORCL", "MSFT")


def top8_database(tmp_path: Path) -> Path:
    path = tmp_path / "top8.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": HISTORY_SCHEMA_REVISION})
    with Session(engine) as session:
        seed(session, TOP3, exchanges={"ORCL": "NYSE"})
    engine.dispose()
    return path


def _counts(path: Path) -> dict[str, int]:
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    try:
        with Session(engine) as session:
            return {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                    for model in (*TRADING_TABLES, EntryDriftObservation, PremarketVolumeSession)}
    finally:
        engine.dispose()


def _symbols_stored(path: Path) -> Counter:
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    try:
        with engine.connect() as connection:
            return Counter(connection.execute(text(
                "SELECT symbol || '/' || exchange FROM premarket_volume_sessions")).scalars())
    finally:
        engine.dispose()


@pytest.mark.parametrize("given, exchange", [("ORCL", "NY"), ("aapl", "ND")])
def test_symbol_collects_exactly_one_candidate_on_its_stored_exchange(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], given: str, exchange: str) -> None:
    path = top8_database(tmp_path)
    before = _counts(path)
    source = HistorySource({symbol: history() for symbol in TOP3})
    assert run_collector(path, "--write", "--symbol", given, source=source) == 0
    out = capsys.readouterr().out
    symbol = given.upper()
    assert set(source.calls) == {(symbol, exchange, day) for day in HISTORY_DAYS}
    assert len(source.calls) == 20
    assert _symbols_stored(path) == {f"{symbol}/{exchange}": 20}  # the other TOP8 untouched
    after = _counts(path)
    assert {name: after[name] - before[name] for name in after} == {
        **{name: 0 for name in after}, "premarket_volume_sessions": 20}  # order/fill/position 0
    assert (f"{symbol} {exchange} AVAILABLE fetched_sessions=20 complete=20/20 "
            f"target_reached=20/20 target_not_reached=0 pages_used={20 * PAGES}") in out
    assert ("symbols_requested=1 sessions_requested=20 sessions_fetched=20 cache_hits=0 "
            f"provider_failures=0 target_not_reached=0 http_chart_requests={20 * PAGES} "
            f"http_requests={20 * PAGES}") in out
    assert "Kiwoom order requests executed = 0" in out

    assert run_collector(path, "--write", "--symbol", given, source=source) == 0
    assert len(source.calls) == 20
    assert ("symbols_requested=1 sessions_requested=0 sessions_fetched=0 cache_hits=20 "
            "provider_failures=0 target_not_reached=0 http_chart_requests=0 http_requests=0"
            ) in capsys.readouterr().out


@pytest.mark.parametrize("mode", [(), ("--dry-run",), ("--write",)])
@pytest.mark.parametrize("given", ["TSLA", "NOT A SYMBOL"])
def test_unknown_symbol_fails_before_any_provider_call(tmp_path: Path, mode: tuple[str, ...],
                                                       given: str) -> None:
    path = top8_database(tmp_path)
    before = digest(path)
    built: list[bool] = []
    with pytest.raises(SystemExit, match="NOT RUN: --symbol"):
        collector_cli.main(["--scanner-date", RUN_DAY.isoformat(), "--database", str(path),
                            *mode, "--symbol", given],
                           source_factory=lambda: built.append(True),  # type: ignore[arg-type, return-value]
                           clock=lambda: AFTER_CLOSE)
    assert built == [] and digest(path) == before


def test_collector_dry_run_with_symbol_is_a_plan_with_zero_requests(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = top8_database(tmp_path)
    before = digest(path)
    built: list[bool] = []
    assert collector_cli.main(["--scanner-date", RUN_DAY.isoformat(), "--database", str(path),
                               "--symbol", "ORCL"],
                              source_factory=lambda: built.append(True),  # type: ignore[arg-type, return-value]
                              clock=lambda: AFTER_CLOSE) == 0
    out = capsys.readouterr().out
    assert built == [] and digest(path) == before
    assert "ORCL NY missing_sessions=20" in out and "AAPL" not in out
    assert "symbols_requested=1 sessions_requested=20 cache_hits=0 market_data_requests=0" in out


def test_run_summary_counts_failures_and_partial_fetches() -> None:
    volumes = {symbol: history() for symbol in ("AAPL", "MU")}
    with memory_session() as session:
        run = service(session, HistorySource(volumes, fail=("MU",))).collect(
            [("AAPL", "NASDAQ"), ("MU", "NASDAQ")], ENTRY)
    assert run.summary() == {"symbols_requested": 2, "sessions_requested": 40, "sessions_fetched": 21,
                             "cache_hits": 0, "provider_failures": 1, "target_not_reached": 0}


def test_dry_run_help_states_the_two_different_meanings(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as collector_help:
        collector_cli.main(["--help"])
    collector = " ".join(capsys.readouterr().out.split())
    with pytest.raises(SystemExit) as observer_help:
        observer_cli.main(["--help"])
    observer_text = " ".join(capsys.readouterr().out.split())
    assert collector_help.value.code == observer_help.value.code == 0
    assert "PLAN ONLY" in collector and "market-data request 0" in collector
    assert "REAL market-data replay" in observer_text and "DB write 0" in observer_text
    assert "PLAN ONLY" in collector_cli.__doc__ and "REAL MARKET-DATA REPLAY" in observer_cli.__doc__


# --- Migration ---------------------------------------------------------------------------------

def _alembic(path: Path, monkeypatch: MonkeyPatch, revision: str, *, down: bool = False) -> None:
    monkeypatch.setenv("RUNTIME_PROFILE", "default")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    (command.downgrade if down else command.upgrade)(Config(PROJECT_ROOT / "alembic.ini"), revision)


BUSINESS_QUERIES = {
    "scanner_runs": "SELECT id, trading_date, status, completed_at FROM scanner_runs ORDER BY id",
    "scanner_candidates": "SELECT id, scanner_run_id, symbol, score_components_json FROM scanner_candidates",
    "gpt_analyses": "SELECT id, scanner_run_id, status, payload_hash FROM gpt_analyses",
    "entry_drift_observations": ("SELECT id, scanner_candidate_id, symbol, status, drift_pct, "
                                 "projections_json, created_at FROM entry_drift_observations"),
    "runtime_failures": "SELECT failure_code, message FROM runtime_failures",
}


def _business_rows(path: Path) -> dict[str, list[tuple]]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            return {table: [tuple(row) for row in connection.execute(text(sql))]
                    for table, sql in BUSINESS_QUERIES.items()}
    finally:
        engine.dispose()


def _fingerprint(path: Path) -> dict[str, object]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        return schema_fingerprint(engine)
    finally:
        engine.dispose()


def test_migration_0015_to_0016_roundtrip_preserves_business_rows(
        tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "roundtrip.sqlite3"
    try:
        _alembic(path, monkeypatch, "20260915_0015")
        fingerprint_0015 = _fingerprint(path)
        engine = create_engine(f"sqlite:///{path}")
        at = "2026-09-14T20:00:00+00:00"
        with engine.begin() as connection:
            connection.execute(text("""INSERT INTO scanner_runs (id, trading_date, started_at,
                completed_at, status, provider, score_version, universe_count, excluded_count,
                candidate_count, top8_count, created_at) VALUES (1, '2026-09-14', :at, :at,
                'COMPLETED', 'TEST', 'quant_v0', 0, 0, 1, 1, :at)"""), {"at": at})
            connection.execute(text("""INSERT INTO scanner_candidates (id, scanner_run_id, symbol,
                rank, is_top8, score, score_components_json, observed_at, available_at, created_at)
                VALUES (7, 1, 'AAA', 1, 1, 1.0, '{"exchange": "NASDAQ"}', :at, :at, :at)"""), {"at": at})
            connection.execute(text("""INSERT INTO gpt_analyses (id, scanner_run_id, trading_date,
                provider, model, prompt_version, schema_version, evidence_version, analysis_at,
                imported_at, status, raw_json, payload_hash) VALUES (3, 1, '2026-09-14', 'GPT', 'm',
                'p', 's', 'e', :at, :at, 'IMPORTED', '{}', :hash)"""), {"at": at, "hash": "a" * 64})
            connection.execute(text("""INSERT INTO entry_drift_observations (trading_date,
                scanner_run_id, gpt_analysis_id, scanner_candidate_id, symbol, exchange,
                strategy_version, risk_version, observer_version, status, drift_pct,
                projections_json, created_at, updated_at) VALUES ('2026-09-15', 1, 3, 7, 'AAA',
                'ND', 'strategy_v0', 'risk', 'entry_drift_observer_v2', 'VALID', 0.5, '[]', :at, :at)"""),
                {"at": at})
            connection.execute(text("""INSERT INTO runtime_failures (failure_code, severity,
                component, message, occurred_at, created_at) VALUES ('KEEP', 'WARNING', 'test',
                'kept', :at, :at)"""), {"at": at})
        engine.dispose()
        business = _business_rows(path)
        assert all(business.values())

        _alembic(path, monkeypatch, "20260915_0016")
        head = _fingerprint(path)
        engine = create_engine(f"sqlite:///{path}")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [
                "20260915_0016"]
            assert connection.execute(text(
                "SELECT v1_volume_ratio, v2_status, v2_median_ratio, v2_projections_json "
                "FROM entry_drift_observations")).one() == (None, None, None, None)
            assert connection.execute(text("SELECT count(*) FROM premarket_volume_sessions")).scalar() == 0
            assert connection.execute(text("PRAGMA quick_check")).scalars().all() == ["ok"]
            assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        with engine.begin() as connection:  # exact Decimal text, not REAL, in the migrated schema
            connection.execute(text("UPDATE entry_drift_observations SET v2_median_ratio = :v"),
                               {"v": "1.24999999999"})
            assert connection.execute(text(
                "SELECT typeof(v2_median_ratio), v2_median_ratio FROM entry_drift_observations"
            )).one() == ("text", "1.24999999999")
            connection.execute(text("UPDATE entry_drift_observations SET v2_median_ratio = NULL"))
        engine.dispose()
        exact = head["columns"]["entry_drift_observations"]  # type: ignore[index]
        for name in ("v1_volume_ratio", "v2_median_ratio", "v2_today_premarket_volume",
                     "v2_baseline_volume"):
            assert exact[name][0] == "varchar(100)", name
        assert _business_rows(path) == business

        # Declarative metadata is the current head's, which is past 0016; the V2 tables
        # and columns this revision adds must match it exactly all the same.
        created = create_engine(f"sqlite:///{tmp_path / 'create-all.sqlite3'}")
        Base.metadata.create_all(created)
        current = schema_fingerprint(created)
        for key, value in head.items():
            if isinstance(value, dict):
                assert {name: value[name] for name in value} == {
                    name: current[key][name] for name in value}  # type: ignore[index]
        created.dispose()

        _alembic(path, monkeypatch, "20260915_0015", down=True)
        assert _fingerprint(path) == fingerprint_0015 and _business_rows(path) == business
        _alembic(path, monkeypatch, "20260915_0016")
        assert _fingerprint(path) == head and _business_rows(path) == business
    finally:
        get_settings.cache_clear()


def test_fresh_database_reaches_the_current_head_with_the_v2_contract(
        tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "fresh.sqlite3"
    try:
        _alembic(path, monkeypatch, "head")
        fingerprint = _fingerprint(path)
    finally:
        get_settings.cache_clear()
    unique = fingerprint["unique_constraints"]["premarket_volume_sessions"]  # type: ignore[index]
    assert unique == [("symbol", "exchange", "trading_date", "source", "collector_version")]
    head = ScriptDirectory.from_config(Config(PROJECT_ROOT / "alembic.ini")).get_current_head()
    assert head == HISTORY_SCHEMA_REVISION == OBSERVER_SCHEMA_REVISION == "20260921_0018"
