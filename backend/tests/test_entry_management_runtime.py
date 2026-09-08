from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.risk import DailySymbolState
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.simulation import SimulationAccountRecord
from app.models.strategy import StrategyStateRecord
from app.repositories.simulation import SimulationStateRepository
from app.repositories.strategy import StrategyStateRepository
from app.services.entry_management_runtime import (
    ApprovedCandidate, EntryAction, EntryLifecycleService, EntryManagementRuntime, EntryOutcome,
)
from app.services.simulation_runtime import SimulationRuntimeContext
from app.services.simulation import rehydrate_sim_broker
from app.strategy.lifecycle import OvernightSuitability, StrategyPhase, TrailingProfile

ET = ZoneInfo("America/New_York")
DAY = date(2024, 6, 18)
OPEN = datetime(2024, 6, 18, 9, 30, tzinfo=ET)
CAPITAL = Decimal("7428.92")
UTC_NOON = datetime(2024, 6, 18, 12, tzinfo=ET)


def minute(at: datetime, close: float, session=MarketSession.REGULAR) -> MinuteBar:
    volume = 100_000 if session is MarketSession.PREMARKET else 20_000
    return MinuteBar(symbol="AAA", timestamp=at, open=close, high=close + .2,
                     low=close - .2, close=close, volume=volume, session=session,
                     observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


class Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.minutes = [minute(datetime(2024, 6, 18, 8, 0, tzinfo=ET), 105,
                               MarketSession.PREMARKET)]
        self.minutes += [minute(OPEN + timedelta(minutes=i), 100) for i in range(15)]
        self.minutes += [minute(OPEN + timedelta(minutes=15), 102),
                         minute(OPEN + timedelta(minutes=16), 102.1),
                         minute(OPEN + timedelta(minutes=17), 102.2)]

    def get_minute_bars(self, symbols, start=None, end=None, session=None):
        self.calls += 1
        return [bar for bar in self.minutes if session is None or bar.session is session]

    def get_daily_bars(self, symbols, start=None, end=None):
        at = datetime(2024, 6, 17, 16, 0, tzinfo=ET)
        return [DailyBar(symbol="AAA", trading_date=DAY - timedelta(days=1), open=100,
                         high=101, low=99, close=100, volume=1_000_000,
                         observed_at=at, available_at=at + timedelta(minutes=1))]


@pytest.fixture
def durable(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'entry.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        account_id = SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CAPITAL, cash=CAPITAL,
            created_at=datetime(2024, 6, 18, 8, tzinfo=ET)).id
        session.commit()
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), account_id, factory)
    yield runtime, factory
    engine.dispose()


def candidate() -> ApprovedCandidate:
    return ApprovedCandidate(1, 1, 1, "AAA", TrailingProfile.WIDE,
                             OvernightSuitability.MEDIUM)


def seed_chain(session) -> None:
    """One stale run/analysis and one current one, so identity is what is asserted."""
    def run(hour: int, status: str) -> ScannerRun:
        record = ScannerRun(trading_date=DAY, started_at=UTC_NOON, status=status,
                            completed_at=datetime(2024, 6, 18, hour, tzinfo=ET),
                            provider="KIWOOM_REAL", score_version="v0")
        session.add(record)
        session.flush()
        return record

    def candidate_row(scanner_run: ScannerRun, symbol: str) -> ScannerCandidate:
        record = ScannerCandidate(scanner_run_id=scanner_run.id, symbol=symbol, rank=1,
                                  is_top8=True, score=1.0, score_components_json={},
                                  observed_at=UTC_NOON, available_at=UTC_NOON)
        session.add(record)
        session.flush()
        return record

    def analysis(scanner_run: ScannerRun, hour: int, payload_hash: str) -> GPTAnalysis:
        record = GPTAnalysis(scanner_run_id=scanner_run.id, trading_date=DAY, provider="gpt",
                             model="m", prompt_version="1", schema_version="1",
                             evidence_version="1", status="IMPORTED", raw_json="{}",
                             payload_hash=payload_hash,
                             analysis_at=datetime(2024, 6, 18, hour, tzinfo=ET))
        session.add(record)
        session.flush()
        return record

    def decide(analysed: GPTAnalysis, scanned: ScannerCandidate, rank: int, verdict: str) -> None:
        session.add(GPTCandidateAnalysis(
            gpt_analysis_id=analysed.id, scanner_candidate_id=scanned.id, symbol=scanned.symbol,
            gpt_rank=rank, overall_score=1, catalyst_score=1, fundamental_score=1,
            momentum_score=1, risk_score=1, evidence_confidence=1, catalyst_duration="D",
            stop_profile="TIGHT", trailing_profile=TrailingProfile.WIDE.value,
            overnight_suitability=OvernightSuitability.MEDIUM.value, company_summary="",
            catalyst_summary="", risk_summary="", invalidation_summary="", unknown_fields_json=[]))
        session.add(HumanDecisionRecord(
            gpt_analysis_id=analysed.id, scanner_candidate_id=scanned.id,
            symbol=scanned.symbol, decision=verdict, decided_at=UTC_NOON))
        session.flush()

    superseded = run(9, "COMPLETED")
    decide(analysis(superseded, 9, "old"), candidate_row(superseded, "OLD"), 1, "APPROVE")
    current = run(11, "COMPLETED")
    stale_analysis = analysis(current, 10, "stale")
    decide(stale_analysis, candidate_row(current, "STALE"), 1, "APPROVE")
    latest = analysis(current, 11, "latest")
    decide(latest, candidate_row(current, "AAA"), 2, "APPROVE")
    decide(latest, candidate_row(current, "BBB"), 1, "REJECT")
    # An approval carrying a candidate row that belongs to the superseded run is
    # not this run's candidate, however current the analysis holding it is.
    decide(latest, candidate_row(superseded, "CROSS"), 3, "APPROVE")
    # An approval on a run that never completed is not a decision this day consumes.
    running = run(23, "RUNNING")
    decide(analysis(running, 23, "running"), candidate_row(running, "LATER"), 1, "APPROVE")
    session.commit()


def test_only_an_approved_decision_on_the_current_run_and_analysis_is_entered(durable) -> None:
    """Rank, GPT verdict, a stale run, and a superseded analysis are never authority."""
    runtime, factory = durable
    with factory() as session:
        seed_chain(session)

    approved = EntryLifecycleService(runtime).approved_candidates(DAY)

    assert [entry.symbol for entry in approved] == ["AAA"]
    assert approved[0].trailing_profile is TrailingProfile.WIDE
    assert approved[0].overnight_suitability is OvernightSuitability.MEDIUM
    with factory() as session:
        run_id, analysis_id = session.execute(select(
            ScannerRun.id, GPTAnalysis.id).join(GPTAnalysis).where(
            GPTAnalysis.payload_hash == "latest")).one()
        candidate_id = session.scalar(select(ScannerCandidate.id).where(
            ScannerCandidate.symbol == "AAA"))
    assert (approved[0].scanner_run_id, approved[0].analysis_id, approved[0].candidate_id) == (
        run_id, analysis_id, candidate_id)
    assert EntryLifecycleService(runtime).approved_candidates(DAY - timedelta(days=1)) == ()


def test_strategy_signal_retries_no_next_bar_then_fills_once_and_is_restart_safe(durable) -> None:
    runtime, factory = durable
    provider = Provider()
    service = EntryLifecycleService(runtime)

    first = service.evaluate(candidate(), provider, as_of=OPEN + timedelta(minutes=16))
    assert first.action is EntryAction.REJECTED
    assert runtime.broker.get_positions() == ()
    with factory() as session:
        assert session.scalar(select(StrategyStateRecord)).phase == StrategyPhase.ENTRY_SIGNALLED.value
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 0
        assert session.scalar(select(SimulationAccountRecord)).state_version == 0

    second = service.evaluate(candidate(), provider, as_of=OPEN + timedelta(minutes=18))
    assert second.action is EntryAction.FILLED
    assert runtime.broker.get_position("AAA") is not None
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 1
        assert session.scalar(select(func.count()).select_from(DailySymbolState)) == 1
        account = session.scalar(select(SimulationAccountRecord))
        assert account.cash < CAPITAL and account.state_version == 1

    with factory() as session:
        restarted = SimulationRuntimeContext(rehydrate_sim_broker(session, runtime.account_id),
                                             runtime.account_id, factory)
    assert EntryLifecycleService(restarted).evaluate(
        candidate(), provider, as_of=OPEN + timedelta(minutes=19)).action is EntryAction.SKIPPED

    third = service.evaluate(candidate(), provider, as_of=OPEN + timedelta(minutes=19))
    assert third.action is EntryAction.SKIPPED
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 1


def test_entry_strategy_state_failure_rolls_back_fill_and_broker_memory(durable, monkeypatch) -> None:
    runtime, factory = durable
    provider = Provider()
    service = EntryLifecycleService(runtime)
    service.evaluate(candidate(), provider, as_of=OPEN + timedelta(minutes=16))
    before_cash = runtime.broker.cash

    def explode(*args, **kwargs):
        raise RuntimeError("injected strategy state failure")

    monkeypatch.setattr(StrategyStateRepository, "save", explode)
    with pytest.raises(RuntimeError, match="injected strategy state failure"):
        service.evaluate(candidate(), provider, as_of=OPEN + timedelta(minutes=18))
    assert runtime.broker.cash == before_cash and runtime.broker.get_positions() == ()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionOrderRecord)) == 1
        assert session.scalar(select(SimulationAccountRecord)).state_version == 0


@pytest.mark.asyncio
async def test_no_approved_candidate_fast_path_never_constructs_provider(durable) -> None:
    runtime, _ = durable
    built = []

    class EmptyLifecycle:
        def approved_candidates_for_entry_session(self, entry_session_date):
            return ()

    owner = EntryManagementRuntime(runtime, lambda: built.append(True), lifecycle=EmptyLifecycle())
    assert await owner.run_once(as_of=OPEN + timedelta(minutes=30)) == ()
    assert built == []


# --- Entry session / analysis session date contract -------------------------------------
# Scanner and GPT stamp `trading_date` with the last COMPLETED XNYS session, so an entry
# session consumes the analysis of its immediate predecessor session, never its own date.

ENTRY_SESSION = date(2026, 9, 8)
ANALYSIS_SESSION = date(2026, 9, 4)


def seed_session(session, trading_date: date, symbols: tuple[str, ...]) -> None:
    """One COMPLETED run and one IMPORTED analysis on `trading_date`, every symbol APPROVEd."""
    at = datetime.combine(trading_date, time(16, 30), ET)
    run = ScannerRun(trading_date=trading_date, started_at=at, status="COMPLETED",
                     completed_at=at, provider="KIWOOM_REAL", score_version="v0")
    session.add(run)
    session.flush()
    analysed = GPTAnalysis(scanner_run_id=run.id, trading_date=trading_date, provider="gpt",
                           model="m", prompt_version="1", schema_version="1",
                           evidence_version="1", status="IMPORTED", raw_json="{}",
                           payload_hash=f"analysis-{trading_date}", analysis_at=at)
    session.add(analysed)
    session.flush()
    for rank, symbol in enumerate(symbols, start=1):
        scanned = ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=rank, is_top8=True,
                                   score=1.0, score_components_json={}, observed_at=at,
                                   available_at=at)
        session.add(scanned)
        session.flush()
        session.add(GPTCandidateAnalysis(
            gpt_analysis_id=analysed.id, scanner_candidate_id=scanned.id, symbol=symbol,
            gpt_rank=rank, overall_score=1, catalyst_score=1, fundamental_score=1,
            momentum_score=1, risk_score=1, evidence_confidence=1, catalyst_duration="D",
            stop_profile="TIGHT", trailing_profile=TrailingProfile.WIDE.value,
            overnight_suitability=OvernightSuitability.MEDIUM.value, company_summary="",
            catalyst_summary="", risk_summary="", invalidation_summary="", unknown_fields_json=[]))
        session.add(HumanDecisionRecord(
            gpt_analysis_id=analysed.id, scanner_candidate_id=scanned.id, symbol=symbol,
            decision="APPROVE", decided_at=at))
    session.commit()


def test_todays_entry_session_consumes_the_labor_day_predecessor_analysis(durable) -> None:
    """Production case: 09/08 entry must read the 09/04 analysis, not an empty 09/08 one."""
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_SESSION, ("NVDA", "AAPL"))
    service = EntryLifecycleService(runtime)

    assert service.analysis_session_date(ENTRY_SESSION) == ANALYSIS_SESSION
    approved = service.approved_candidates_for_entry_session(ENTRY_SESSION)

    assert sorted(entry.symbol for entry in approved) == ["AAPL", "NVDA"]
    # The pre-fix lookup, kept explicit so the defect cannot silently return.
    assert service.approved_candidates(ENTRY_SESSION) == ()


@pytest.mark.parametrize("entry_session, analysis_session, case", [
    (date(2026, 9, 8), date(2026, 9, 4), "labor day 09/07 and the weekend are skipped"),
    (date(2026, 9, 9), date(2026, 9, 8), "consecutive sessions"),
    (date(2026, 9, 14), date(2026, 9, 11), "monday entry consumes friday analysis"),
    (date(2026, 11, 27), date(2026, 11, 25), "thanksgiving 11/26 is skipped"),
    (date(2026, 1, 20), date(2026, 1, 16), "mlk day and the weekend are skipped"),
])
def test_previous_xnys_session_is_the_analysis_authority(durable, entry_session: date,
                                                         analysis_session: date, case: str) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, analysis_session, ("NVDA",))
    service = EntryLifecycleService(runtime)

    assert service.analysis_session_date(entry_session) == analysis_session, case
    assert [entry.symbol for entry in
            service.approved_candidates_for_entry_session(entry_session)] == ["NVDA"], case


def test_a_missing_exact_predecessor_never_falls_back_to_an_older_analysis(durable) -> None:
    """09/09 entry with no 09/08 analysis enters nothing; 09/04 approvals are not reused."""
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_SESSION, ("NVDA", "AAPL"))

    assert EntryLifecycleService(runtime).approved_candidates_for_entry_session(
        date(2026, 9, 9)) == ()


def test_a_superseded_sessions_approvals_never_leak_into_the_next_entry_session(durable) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_SESSION, ("OLD", "STALE"))
        seed_session(session, ENTRY_SESSION, ("NVDA",))

    approved = EntryLifecycleService(runtime).approved_candidates_for_entry_session(
        date(2026, 9, 9))

    assert [entry.symbol for entry in approved] == ["NVDA"]


@pytest.mark.asyncio
async def test_run_once_evaluates_the_previous_sessions_approvals_in_the_current_session(
        durable) -> None:
    """End to end through run_once: the 09/08 regular session evaluates 09/04's approvals."""
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_SESSION, ("NVDA", "AAPL"))

    class RecordingLifecycle(EntryLifecycleService):
        def __init__(self, context) -> None:
            super().__init__(context)
            self.evaluated: list[str] = []

        def evaluate(self, candidate, provider, *, as_of):
            self.evaluated.append(candidate.symbol)
            return EntryOutcome(candidate.symbol, EntryAction.HOLD, "recorded")

    lifecycle = RecordingLifecycle(runtime)
    owner = EntryManagementRuntime(runtime, lambda: Provider(), lifecycle=lifecycle)

    outcomes = await owner.run_once(as_of=datetime(2026, 9, 8, 10, 0, tzinfo=ET))

    assert sorted(lifecycle.evaluated) == ["AAPL", "NVDA"]
    assert sorted(outcome.symbol for outcome in outcomes) == ["AAPL", "NVDA"]
    assert all(outcome.action is EntryAction.HOLD for outcome in outcomes)
