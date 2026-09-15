"""Entry drift observer: Production Entry contract alignment and analytics-only writes.

Market data follows the Kiwoom minute contract (timestamp = bar start, available_at =
bar completion). The differential tests run the same tape through the Production
``EntryLifecycleService`` and the observer and require identical decisions.
"""

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.broker.sim import SimBroker
from app.core.config import PROJECT_ROOT
from app.core.database import Base, create_db_engine
from app.core.exceptions import MarketDataError
from app.dev import observe_entry_drift as cli
from app.execution.config import ExecutionConfig
from app.integrations.kiwoom.mapping import exchange_code
from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.models.analytics import EntryDriftObservation
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.risk import DailySymbolState
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.simulation import (
    AccountDailyPerformanceRecord, SimulationAccountRecord, SimulationPositionRecord,
    SimulationTradeRecord,
)
from app.models.strategy import PremarketDiagnosticRecord, StrategyStateRecord
from app.repositories.simulation import SimulationStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.risk.engine import RiskEngine
from app.services import entry_drift_observer as observer_module
from app.services.entry_drift_observer import (
    OBSERVER_SCHEMA_REVISION, OBSERVER_VERSION, TICK_EPSILON, EntryDriftObserver,
    ObservationStatus, ObserverCandidate, ObserverRefused, WriteOutcome, entry_drift_report,
    observation_statistics, percentile,
)
from app.services.entry_management_runtime import (
    EntryLifecycleService, analysis_session_date, intended_entry_bar_at,
)
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.config import STRATEGY_VERSION
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase

CAL = MarketCalendar()
ET = CAL.timezone
RUN_DAY = date(2026, 9, 14)   # scanner/GPT analysis session (Monday)
ENTRY = date(2026, 9, 15)     # the session its approvals trade (Tuesday)
W = CAL.session(ENTRY)
MIN = timedelta(minutes=1)
AFTER_CLOSE = W.market_close + timedelta(hours=2)
IN_SESSION = W.market_open + timedelta(hours=1)
ANALYSED_AT = datetime(2026, 9, 14, 22, tzinfo=timezone.utc)
EQUITY = Decimal("100000")


def tick(minutes: int, window=W) -> datetime:  # type: ignore[no-untyped-def]
    return window.market_open + minutes * MIN + TICK_EPSILON


def kbar(symbol: str, at: datetime, open_: float, close: float | None = None, *,
         high: float | None = None, low: float | None = None,
         session: MarketSession = MarketSession.REGULAR, volume: int = 20_000) -> MinuteBar:
    close = open_ if close is None else close
    return MinuteBar(symbol=symbol, timestamp=at, open=open_, close=close,
                     high=max(open_, close) + .01 if high is None else high,
                     low=min(open_, close) - .01 if low is None else low,
                     volume=volume, session=session,
                     observed_at=at + MIN, available_at=at + MIN)


def tape(symbol: str = "AAA", *, day: date = ENTRY, breakout: int = 15, signal: float = 100.0,
         fill_open: float = 100.5, bars_until: int | None = None,
         skip: tuple[int, ...] = ()) -> list[MinuteBar]:
    """Opening range [95, 99]; flat at 97 until the ``breakout`` bar closes at ``signal``;
    the next bar opens at the signal, and every bar from the intended one opens at
    ``fill_open``. ``bars_until``/``skip`` drop regular minutes to model truncation."""
    window = CAL.session(day)
    previous = CAL.session(CAL.previous_trading_day(day))
    assert window and previous
    bars = [kbar(symbol, previous.market_close - MIN, 100.0),
            kbar(symbol, window.market_open - 90 * MIN, 105.0,
                 session=MarketSession.PREMARKET, volume=100_000)]
    minute = 0
    while window.market_open + minute * MIN < window.market_close:
        at = window.market_open + minute * MIN
        if (bars_until is None or minute < bars_until) and minute not in skip:
            if minute < 15:
                bars.append(kbar(symbol, at, 97.0, high=99.0, low=95.0))
            elif minute < breakout:
                bars.append(kbar(symbol, at, 97.0))
            elif minute == breakout:
                bars.append(kbar(symbol, at, 97.0, signal))
            elif minute == breakout + 1:
                bars.append(kbar(symbol, at, signal))
            else:
                bars.append(kbar(symbol, at, fill_open))
        minute += 1
    return bars


class Provider:
    """Kiwoom-shaped test provider; records every minute request it serves."""

    def __init__(self, *tapes: list[MinuteBar], fail: tuple[str, ...] = ()) -> None:
        self.minutes = [item for bars in tapes for item in bars]
        self.fail = set(fail)
        self.bound: dict[str, str] = {}
        self.minute_calls: list[tuple[tuple[str, ...], datetime | None, datetime | None]] = []

    def bind_exchange(self, symbol: str, exchange: str) -> None:
        self.bound[symbol] = exchange_code(exchange)

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        self.minute_calls.append((tuple(symbols), start, end))
        if self.fail & set(symbols):
            raise MarketDataError("INSUFFICIENT_HISTORY", "history truncated before start")
        wanted = set(symbols)
        return [item for item in self.minutes if item.symbol in wanted
                and (start is None or item.timestamp >= start)
                and (end is None or item.timestamp <= end)
                and (session is None or item.session is session)]

    def get_daily_bars(self, symbols, start=None, end=None):  # type: ignore[no-untyped-def]
        bars = []
        for symbol in symbols:
            for back in range(30):
                day = (end or RUN_DAY) - timedelta(days=back)
                closed = datetime.combine(day, time(16), ET)
                if start is None or day >= start:
                    bars.append(DailyBar(symbol=symbol, trading_date=day, open=100, high=101,
                                         low=99, close=100, volume=1_000_000,
                                         observed_at=closed, available_at=closed))
        return bars


def add_analysis(session: Session, run: ScannerRun, items: list[ScannerCandidate], *,
                 decision: str | None = "APPROVE") -> GPTAnalysis:
    number = session.scalar(select(func.count()).select_from(GPTAnalysis)) + 1
    analysis = GPTAnalysis(scanner_run_id=run.id, trading_date=run.trading_date, provider="GPT",
                           model="m", prompt_version="p", schema_version="s", evidence_version="e",
                           analysis_at=ANALYSED_AT, status="IMPORTED", raw_json="{}",
                           payload_hash=f"h{number}")
    session.add(analysis)
    session.flush()
    for rank, item in enumerate(items, 1):
        session.add(GPTCandidateAnalysis(
            gpt_analysis_id=analysis.id, scanner_candidate_id=item.id, symbol=item.symbol,
            gpt_rank=rank, overall_score=1, catalyst_score=1, fundamental_score=1,
            momentum_score=1, risk_score=1, evidence_confidence=1, catalyst_duration="UNKNOWN",
            stop_profile="NORMAL", trailing_profile="NORMAL", overnight_suitability="LOW",
            company_summary="x", catalyst_summary="x", risk_summary="x",
            invalidation_summary="x", unknown_fields_json=[]))
        if decision is not None:
            session.add(HumanDecisionRecord(gpt_analysis_id=analysis.id, scanner_candidate_id=item.id,
                                            symbol=item.symbol, decision=decision,
                                            decided_at=ANALYSED_AT))
    run.active_gpt_analysis_id = analysis.id
    session.commit()
    return analysis


def seed(session: Session, symbols: tuple[str, ...] = ("AAA",), *, day: date = RUN_DAY,
         completed_at: datetime = ANALYSED_AT,
         exchanges: dict[str, str] | None = None) -> ScannerRun:
    run = ScannerRun(trading_date=day, started_at=completed_at, completed_at=completed_at,
                     status="COMPLETED", provider="TEST", score_version="v")
    session.add(run)
    session.flush()
    items = []
    for rank, symbol in enumerate(symbols, 1):
        item = ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=rank, is_top8=True,
                                score=1, observed_at=completed_at, available_at=completed_at,
                                score_components_json={"exchange": (exchanges or {}).get(symbol, "NASDAQ")})
        session.add(item)
        session.flush()
        items.append(item)
    add_analysis(session, run, items)
    return run


def memory_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def observer(session: Session, provider: Provider, *, clock: datetime = AFTER_CLOSE,
             engine: StrategyV0Engine | None = None) -> EntryDriftObserver:
    return EntryDriftObserver(session, provider, engine=engine, clock=lambda: clock)  # type: ignore[arg-type]


def candidate(exchange: str = "NASDAQ", day: date = ENTRY) -> ObserverCandidate:
    return ObserverCandidate(day, 1, 1, 1, "AAA", exchange)


def rows(session: Session) -> list[EntryDriftObservation]:
    session.expire_all()
    return list(session.scalars(select(EntryDriftObservation).order_by(EntryDriftObservation.id)))


# --- Entry session date authority ----------------------------------------------------

def test_run_date_authority_replays_the_next_trading_session() -> None:
    with memory_session() as session:
        run = seed(session)
        provider = Provider(tape())
        watcher = observer(session, provider)
        # 09/14 consumes 09/11's analysis, so the 09/14 run is not a 09/14 entry.
        assert watcher.candidates(RUN_DAY) == ()
        (item,) = watcher.candidates(ENTRY)
        assert (item.trading_date, item.scanner_run_id) == (ENTRY, run.id)
        assert watcher.observe(item).status is ObservationStatus.VALID
    replays = {start for symbols, start, end in provider.minute_calls if end == W.market_close}
    assert replays == {datetime.combine(ENTRY, time(4), ET)}


def test_analysis_session_minutes_are_not_an_entry_observation() -> None:
    with memory_session() as session:
        seed(session)
        (item,) = observer(session, Provider(tape(day=RUN_DAY))).candidates(ENTRY)
        result = observer(session, Provider(tape(day=RUN_DAY))).observe(item)
    assert (result.status, result.quality_reason) == (ObservationStatus.INVALID_PREMARKET, "NO_PREMARKET_BARS")


@pytest.mark.parametrize(("entry", "analysis"), [
    (date(2026, 9, 14), date(2026, 9, 11)),   # Friday -> Monday
    (date(2026, 9, 8), date(2026, 9, 4)),     # Labor Day 2026-09-07 is skipped
    (date(2026, 11, 27), date(2026, 11, 25)),  # Thanksgiving skipped; early-close entry session
])
def test_entry_session_resolves_its_analysis_through_the_shared_calendar_helper(
        entry: date, analysis: date) -> None:
    assert analysis_session_date(CAL, entry) == analysis
    with memory_session() as session:
        run = seed(session, day=analysis)
        (item,) = observer(session, Provider()).candidates(entry)
    assert (item.trading_date, item.scanner_run_id) == (entry, run.id)


def test_early_close_entry_session_replays_against_its_own_close() -> None:
    day = date(2026, 11, 27)
    window = CAL.session(day)
    assert window and window.is_early_close
    early = ObserverCandidate(day, 1, 1, 1, "AAA", "NASDAQ")
    with memory_session() as session:
        done = observer(session, Provider(tape(day=day)), clock=window.market_close).observe(early)
        live = observer(session, Provider(tape(day=day)),
                        clock=window.market_close - MIN).observe(early)
    assert done.status is ObservationStatus.VALID
    assert done.execution_at == window.market_open + 17 * MIN
    assert live.status is ObservationStatus.SESSION_NOT_COMPLETE


# --- Point in time and tick timing ---------------------------------------------------

class SpyEngine(StrategyV0Engine):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[datetime, tuple[MinuteBar, ...]]] = []

    def evaluate_entry(self, *, state, bars, market_open, as_of, current_reference_price=None):  # type: ignore[no-untyped-def]
        self.calls.append((as_of, tuple(bars)))
        return super().evaluate_entry(state=state, bars=bars, market_open=market_open, as_of=as_of,
                                      current_reference_price=current_reference_price)


def test_replay_keeps_provider_availability_and_ticks_on_completion_plus_epsilon() -> None:
    spy = SpyEngine()
    with memory_session() as session:
        result = observer(session, Provider(tape()), engine=spy).observe(candidate())
    assert result.status is ObservationStatus.VALID
    for as_of, bars in spy.calls:
        assert (as_of - W.market_open) % MIN == TICK_EPSILON
        for item in bars:
            assert item.available_at == item.timestamp + MIN  # never re-stamped
            assert item.available_at <= as_of                 # completed before the tick
    # The 09:45 bar completes at 09:46; the decision is that tick, not the bar's start.
    assert result.signal_bar_timestamp == W.market_open + 15 * MIN
    assert result.signal_at == tick(16)
    assert result.execution_at == W.market_open + 17 * MIN
    assert result.execution_open == Decimal("100.5")
    assert result.drift_pct == (Decimal("100.5") / Decimal("100") - 1) * 100


# --- Differential: Production Entry vs observer --------------------------------------

class DurableEnv:
    def __init__(self, tmp_path: Path) -> None:
        database = create_db_engine(f"sqlite:///{tmp_path / 'entry.sqlite3'}")
        Base.metadata.create_all(database)
        self.factory = sessionmaker(bind=database, expire_on_commit=False)
        with self.factory() as session:
            self.run_id = seed(session).id
            self.account_id = SimulationStateRepository(session).create_account(
                broker_type="SIM", account_key="operator", base_currency="USD",
                initial_cash=EQUITY, cash=EQUITY, created_at=ANALYSED_AT).id
            session.commit()
        self.runtime = SimulationRuntimeContext(SimBroker(EQUITY), self.account_id, self.factory)
        self.service = EntryLifecycleService(self.runtime)

    def count(self, model) -> int:  # type: ignore[no-untyped-def]
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("breakout", [15, 58, 59, 60])
def test_production_entry_and_observer_agree_tick_for_tick(tmp_path: Path, breakout: int) -> None:
    env = DurableEnv(tmp_path)
    provider = Provider(tape(breakout=breakout))
    (approved,) = env.service.approved_candidates_for_entry_session(ENTRY)
    with env.factory() as session:
        watcher = observer(session, provider)
        (observed,) = watcher.candidates(ENTRY)
        result = watcher.observe(observed)
    assert (observed.scanner_run_id, observed.analysis_id, observed.candidate_id, observed.symbol) == (
        approved.scanner_run_id, approved.analysis_id, approved.candidate_id, approved.symbol)

    production: list[tuple[datetime, str]] = []
    at = tick(0)
    while True:
        outcome = env.service.evaluate(approved, provider, as_of=at)
        if outcome.state.phase in {StrategyPhase.ENTRY_SIGNALLED, StrategyPhase.NO_TRADE}:
            break
        production.append((at, outcome.reason))
        at += MIN
        assert at < W.market_close

    # Identical decisions on every tick, ending on the same deciding tick.
    assert result.trace[:-1] == tuple(production)
    assert result.trace[-1][0] == at
    # Evaluation starts on the 09:45 tick, which sees the 09:44 bar complete the range.
    reasons = dict(result.trace)
    assert reasons[tick(14)] == StrategyReason.OPENING_RANGE_BUILDING.value
    assert reasons[tick(15)] != StrategyReason.OPENING_RANGE_BUILDING.value
    with env.factory() as session:
        diagnostic = session.scalar(select(PremarketDiagnosticRecord))
    assert (result.gap_pct, result.volume_ratio) == (diagnostic.gap_pct, diagnostic.volume_ratio)
    # Production reached its decision without submitting anything.
    assert env.count(ExecutionOrderRecord) == env.count(ExecutionFillRecord) == 0
    assert env.count(DailySymbolState) == 0
    assert env.runtime.broker.get_open_orders() == () and env.runtime.broker.get_position("AAA") is None

    if breakout <= 58:  # the 10:28 bar is the last one a 10:29+e tick can signal on
        state = outcome.state
        assert outcome.reason == StrategyReason.ENTRY_AWAITING_EXECUTION_BAR.value
        assert result.status is ObservationStatus.VALID
        assert result.signal_at == state.last_market_as_of == tick(breakout + 1)
        assert result.signal_bar_timestamp == W.market_open + breakout * MIN
        assert (result.signal_price, result.initial_stop) == (state.entry_price, state.initial_stop)
        assert result.opening_range_low == state.initial_stop
        assert result.execution_at == intended_entry_bar_at(
            state.last_market_as_of, env.runtime.broker.config.fill_delay_bars)
        assert result.execution_at == W.market_open + (breakout + 2) * MIN
    else:  # a 10:29 or 10:30 breakout completes after the 10:30 deadline tick
        assert at == tick(60)
        assert outcome.reason == result.quality_reason == StrategyReason.ENTRY_DEADLINE_EXPIRED.value
        assert result.status is ObservationStatus.NO_SIGNAL


# --- Projection math -----------------------------------------------------------------

def test_tolerance_quantity_is_sized_at_the_ceiling_not_the_actual_open() -> None:
    decided = tick(16)
    with memory_session() as session:
        watcher = observer(session, Provider())
        by_open = {
            open_: {item["tolerance_pct"]: item for item in watcher.tolerance_projections(
                "AAA", Decimal("100"), Decimal("95"), open_, decided)}
            for open_ in (Decimal("100.10"), Decimal("100.90"), Decimal("101.50"))
        }
    expected = RiskEngine().evaluate_base_entry(
        decision=StrategyDecision("AAA", DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK", decided,
                                  strategy_version=STRATEGY_VERSION),
        eligibility=TradingEligibility(True),
        account=AccountSnapshot(EQUITY, EQUITY, Currency.USD, decided),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), decided),
        daily_state=DailyTradingState(ENTRY, session_equity=EQUITY),
        entry_price=Decimal("101"), stop_price=Decimal("95"), instrument_currency=Currency.USD,
        created_at=decided, execution_config=ExecutionConfig()).metrics
    at_one = [by_open[open_]["1.00"] for open_ in by_open]
    assert {Decimal(item["quantity_per_100k_usd"]) for item in at_one} == {expected.final_quantity}
    assert [item["would_execute"] for item in at_one] == [True, True, False]
    for item in at_one[:2]:
        assert Decimal(item["realised_initial_risk"]) <= Decimal(item["planned_initial_risk"])
    base = by_open[Decimal("100.10")]["0.00"]
    assert base["would_execute"] is False and Decimal(base["quantity_reduction_pct"]) == 0
    assert Decimal(at_one[0]["quantity_reduction_pct"]) > 0


# --- Data completeness ---------------------------------------------------------------

@pytest.mark.parametrize("shape", [
    {"bars_until": 15},                   # the provider stops at 09:44
    {"breakout": 10_000, "skip": (30,)},  # a minute inside the signal window is missing
    {"breakout": 10_000, "bars_until": 40},
])
def test_incomplete_signal_window_is_never_no_signal(shape: dict) -> None:
    with memory_session() as session:
        result = observer(session, Provider(tape(**shape))).observe(candidate())
    assert (result.status, result.quality_reason) == (
        ObservationStatus.INSUFFICIENT_MINUTE_DATA, "INCOMPLETE_SIGNAL_WINDOW")


def test_complete_window_without_breakout_is_no_signal() -> None:
    with memory_session() as session:
        result = observer(session, Provider(tape(breakout=10_000))).observe(candidate())
    assert (result.status, result.quality_reason) == (
        ObservationStatus.NO_SIGNAL, StrategyReason.ENTRY_DEADLINE_EXPIRED.value)


def test_open_session_is_not_observed_or_persisted() -> None:
    provider = Provider(tape())
    with memory_session() as session:
        seed(session)
        watcher = observer(session, provider, clock=IN_SESSION)
        assert watcher.observe(candidate()).status is ObservationStatus.SESSION_NOT_COMPLETE
        with pytest.raises(ObserverRefused):
            watcher.observe_date(ENTRY, persist=True)
        assert rows(session) == []
    assert provider.minute_calls == []


# --- Upsert monotonicity and versions -------------------------------------------------

def test_valid_is_not_downgraded_by_a_worse_rerun() -> None:
    with memory_session() as session:
        seed(session)
        first = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        again = observer(session, Provider(tape(skip=(17,)))).observe_date(ENTRY, persist=True)
        (row,) = rows(session)
    assert first.writes == (WriteOutcome.INSERTED,)
    assert again.results[0].status is ObservationStatus.MISSING_EXECUTION_BAR
    assert again.writes == (WriteOutcome.KEPT_FINAL,)
    assert row.status == "VALID" and Decimal(row.drift_pct) == Decimal("0.5")


def test_incomplete_observation_upgrades_to_valid() -> None:
    with memory_session() as session:
        seed(session)
        first = observer(session, Provider(tape(bars_until=15))).observe_date(ENTRY, persist=True)
        assert rows(session)[0].status == "INSUFFICIENT_MINUTE_DATA"
        again = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        (row,) = rows(session)
    assert (first.writes, again.writes) == ((WriteOutcome.INSERTED,), (WriteOutcome.UPDATED,))
    assert row.status == "VALID"


def test_other_version_rows_are_replaced_only_on_purpose() -> None:
    with memory_session() as session:
        seed(session)
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        rows(session)[0].observer_version = "entry_drift_observer_v1"
        session.commit()
        kept = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        assert kept.writes == (WriteOutcome.VERSION_CONFLICT,)
        assert rows(session)[0].observer_version == "entry_drift_observer_v1"
        replaced = observer(session, Provider(tape())).observe_date(
            ENTRY, persist=True, replace_versions=True)
        assert replaced.writes == (WriteOutcome.UPDATED,)
        assert rows(session)[0].observer_version == OBSERVER_VERSION


def test_rerun_is_idempotent() -> None:
    with memory_session() as session:
        seed(session)
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        before = [(row.status, row.drift_pct, row.projections_json) for row in rows(session)]
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        after = [(row.status, row.drift_pct, row.projections_json) for row in rows(session)]
    assert before == after and len(after) == 1


# --- Authority-filtered statistics ----------------------------------------------------

def test_superseded_run_is_excluded_from_statistics() -> None:
    with memory_session() as session:
        seed(session, completed_at=ANALYSED_AT - timedelta(hours=1))
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        seed(session, completed_at=ANALYSED_AT)
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        assert len(rows(session)) == 2
        report = entry_drift_report(session)
    assert (report["count"], report["excluded_superseded_authority"]) == (1, 1)
    assert report["duplicate_symbol_sessions"] == 0


def test_inactive_analysis_is_excluded_from_statistics() -> None:
    with memory_session() as session:
        run = seed(session)
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        add_analysis(session, run, [], decision=None)  # a newer active analysis, no approvals
        report = entry_drift_report(session)
    assert (report["count"], report["excluded_superseded_authority"]) == (0, 1)
    assert report["verdict"] == "ENTRY PRICE DRIFT DATA INSUFFICIENT"


def test_candidate_no_longer_approved_is_excluded_from_statistics() -> None:
    with memory_session() as session:
        seed(session)
        observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        decision = session.scalar(select(HumanDecisionRecord))
        decision.decision = "REJECT"
        session.commit()
        report = entry_drift_report(session)
    assert (report["count"], report["excluded_superseded_authority"]) == (0, 1)


def test_statistics_are_per_version_and_per_tolerance() -> None:
    with memory_session() as session:
        seed(session, ("AAA", "BBB"))
        observer(session, Provider(tape("AAA"), tape("BBB", fill_open=101.2))).observe_date(
            ENTRY, persist=True)
        rows(session)[1].observer_version = "entry_drift_observer_v1"
        session.commit()
        report = entry_drift_report(session)
    assert (report["count"], report["excluded_other_versions"]) == (1, 1)
    assert report["observer_version"] == OBSERVER_VERSION
    tolerances = {item["tolerance_pct"]: item for item in report["tolerances"]}
    assert len(tolerances) == 8
    assert tolerances["0.00"]["quantity_reduction_pct"]["max"] == 0
    assert (tolerances["2.00"]["quantity_reduction_pct"]["mean"]
            > tolerances["1.00"]["quantity_reduction_pct"]["mean"] > 0)
    assert (tolerances["0.25"]["accepted"], tolerances["0.50"]["accepted"]) == (0, 1)


def test_empty_statistics_are_insufficient_not_zero() -> None:
    report = observation_statistics([])
    assert report["verdict"] == "ENTRY PRICE DRIFT DATA INSUFFICIENT"
    assert report["count"] == 0 and report["drift"] is None
    assert all(item["acceptance_rate"] is None and item["quantity_reduction_pct"] is None
               for item in report["tolerances"])
    values = [Decimal(value) for value in "0123"]
    assert (percentile(values, 50), percentile(values, 75)) == (Decimal("1.5"), Decimal("2.25"))


# --- Candidate isolation and exchanges ------------------------------------------------

def test_one_candidate_provider_failure_does_not_abort_the_others() -> None:
    with memory_session() as session:
        seed(session, ("AAA", "BBB", "CCC"))
        provider = Provider(tape("AAA"), tape("BBB"), tape("CCC"), fail=("BBB",))
        run = observer(session, provider).observe_date(ENTRY, persist=True)
        stored = rows(session)
    assert [(item.candidate.symbol, item.status, item.quality_reason) for item in run.results] == [
        ("AAA", ObservationStatus.VALID, None),
        ("BBB", ObservationStatus.PROVIDER_FAILURE, "INSUFFICIENT_HISTORY"),
        ("CCC", ObservationStatus.VALID, None)]
    assert len(stored) == 3


def test_exchange_authority_maps_nasdaq_nyse_amex_and_fails_closed() -> None:
    exchanges = {"AAA": "NASDAQ", "BBB": "NYSE", "CCC": "AMEX", "DDD": "OTC"}
    with memory_session() as session:
        seed(session, tuple(exchanges), exchanges=exchanges)
        provider = Provider(*(tape(symbol) for symbol in exchanges))
        run = observer(session, provider).observe_date(ENTRY, persist=True)
        stored = {row.symbol: (row.exchange, row.status) for row in rows(session)}
    assert provider.bound == {"AAA": "ND", "BBB": "NY", "CCC": "NA"}
    assert stored == {"AAA": ("ND", "VALID"), "BBB": ("NY", "VALID"), "CCC": ("NA", "VALID"),
                      "DDD": ("OTC", "UNSUPPORTED_EXCHANGE")}
    assert not any("DDD" in symbols for symbols, _start, _end in provider.minute_calls)
    assert run.results[3].quality_reason == "UNSUPPORTED_EXCHANGE"


# --- Trading isolation ------------------------------------------------------------------

TRADING_TABLES = (ExecutionOrderRecord, ExecutionFillRecord, ShadowTradeRecord, StrategyStateRecord,
                  PremarketDiagnosticRecord, DailySymbolState, SimulationAccountRecord,
                  SimulationPositionRecord, SimulationTradeRecord, AccountDailyPerformanceRecord,
                  HumanDecisionRecord, GPTAnalysis, ScannerRun, ScannerCandidate)


def test_observer_writes_only_analytics(tmp_path: Path) -> None:
    env = DurableEnv(tmp_path)
    before = {model.__tablename__: env.count(model) for model in TRADING_TABLES}
    with env.factory() as session:
        run = observer(session, Provider(tape())).observe_date(ENTRY, persist=True)
        active = session.get(ScannerRun, env.run_id).active_gpt_analysis_id
    assert run.results[0].status is ObservationStatus.VALID
    assert {model.__tablename__: env.count(model) for model in TRADING_TABLES} == before
    assert env.count(StrategyStateRecord) == 0 and env.count(EntryDriftObservation) == 1
    with env.factory() as session:
        assert session.get(ScannerRun, env.run_id).active_gpt_analysis_id == active
    source = Path(observer_module.__file__).read_text() + Path(cli.__file__).read_text()
    for forbidden in ("EntryLifecycleService", "EntryManagementRuntime", "run_once", "SimBroker",
                      "submit_order", "execute_entry", "reserve_entry", "StrategyStateRepository",
                      "DailyRiskRepository", ".activate(", "create_all", "create_db_engine"):
        assert forbidden not in source, forbidden


def test_observer_schema_revision_is_the_alembic_head() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == OBSERVER_SCHEMA_REVISION


# --- CLI safety ---------------------------------------------------------------------------

def cli_database(tmp_path: Path, revision: str = OBSERVER_SCHEMA_REVISION) -> Path:
    path = tmp_path / "authority.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision})
    with Session(engine) as session:
        seed(session)
    engine.dispose()
    return path


def stored_rows(path: Path) -> int:
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM entry_drift_observations")).scalar()
    engine.dispose()
    return count


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(path: Path, *extra: str, provider: Provider | None = None,
            clock: datetime = AFTER_CLOSE) -> int:
    return cli.main(["--date", ENTRY.isoformat(), "--database", str(path), *extra],
                    provider_factory=lambda: provider or Provider(tape()), clock=lambda: clock)


def test_cli_default_is_read_only_dry_run(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    before, files = digest(path), sorted(item.name for item in tmp_path.iterdir())
    provider = Provider(tape())
    assert run_cli(path, provider=provider) == 0
    assert provider.minute_calls  # it observed
    assert stored_rows(path) == 0
    assert digest(path) == before and sorted(item.name for item in tmp_path.iterdir()) == files


def test_cli_explicit_dry_run_mutates_nothing(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    before = digest(path)
    assert run_cli(path, "--dry-run") == 0
    assert digest(path) == before and stored_rows(path) == 0


def test_cli_writes_only_with_explicit_write(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    assert run_cli(path, "--write") == 0
    assert stored_rows(path) == 1
    with pytest.raises(SystemExit) as refused:
        run_cli(path, "--write", "--dry-run")
    assert refused.value.code == 2


def test_cli_revision_mismatch_fails_before_any_provider_call(tmp_path: Path) -> None:
    path = cli_database(tmp_path, revision="20260914_0014")
    built: list[bool] = []
    with pytest.raises(SystemExit, match="revision"):
        cli.main(["--date", ENTRY.isoformat(), "--database", str(path), "--write"],
                 provider_factory=lambda: built.append(True), clock=lambda: AFTER_CLOSE)  # type: ignore[arg-type, return-value]
    assert built == []


def test_cli_refuses_a_database_without_authority_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE scanner_runs (id INTEGER PRIMARY KEY, trading_date DATE)"))
    engine.dispose()
    with pytest.raises(SystemExit, match="authority columns"):
        run_cli(path, provider=None)


def test_cli_missing_database_fails_without_creating_it(tmp_path: Path) -> None:
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(SystemExit, match="does not exist"):
        run_cli(path, "--write")
    assert not path.exists()


def test_cli_refuses_an_open_session_before_any_provider_call(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    built: list[bool] = []
    with pytest.raises(SystemExit, match="has not completed"):
        cli.main(["--date", ENTRY.isoformat(), "--database", str(path), "--write"],
                 provider_factory=lambda: built.append(True), clock=lambda: IN_SESSION)  # type: ignore[arg-type, return-value]
    assert built == [] and stored_rows(path) == 0


def test_cli_report_makes_no_market_data_request(tmp_path: Path) -> None:
    path = cli_database(tmp_path)
    run_cli(path, "--write")
    before = digest(path)
    built: list[bool] = []
    assert cli.main(["--database", str(path), "--report"],
                    provider_factory=lambda: built.append(True)) == 0  # type: ignore[arg-type, return-value]
    assert built == [] and digest(path) == before
