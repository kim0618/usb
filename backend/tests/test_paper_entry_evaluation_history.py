"""Durable evaluation history for approved paper entry candidates.

Every assertion here is about what the Production Entry runtime actually recorded:
the tests drive the real ``EntryLifecycleService``/``EntryManagementRuntime`` against
a tape, then read the rows back. Nothing replays a session to produce an expectation.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pytest import MonkeyPatch
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.broker.sim import SimBroker
from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import Base, create_db_engine
from app.dev.schema_fingerprint import schema_fingerprint
from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.models.analytics import PaperEntryEvaluation, PremarketVolumeSession
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.risk import DailySymbolState
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.strategy import StrategyStateRecord
from app.api.strategy_history import (
    SOURCE_EVALUATION, SOURCE_LEGACY_STATE, evaluation_detail, evaluation_history,
)
from app.repositories.simulation import SimulationStateRepository
from app.services.entry_evaluation_log import (
    EVALUATION_INCOMPLETE, INCOMPLETE_LEGACY, NOT_EVALUATED, EvaluationStatus, classify,
)
from app.services.entry_management_runtime import (
    EntryAction, EntryLifecycleService, EntryManagementRuntime,
)
from app.services.position_lifecycle import is_mandatory_exit
from app.services.premarket_volume_history import COLLECTOR_VERSION
from app.services.simulation import rehydrate_sim_broker
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.engine import StrategyReason
from app.strategy.lifecycle import (
    OvernightSuitability, StrategyPhase, StrategyState, TrailingProfile,
)

ET = ZoneInfo("America/New_York")
ANALYSIS_DAY = date(2024, 6, 17)
ENTRY_DAY = date(2024, 6, 18)
OPEN = datetime(2024, 6, 18, 9, 30, tzinfo=ET)
CLOSE = datetime(2024, 6, 18, 16, 0, tzinfo=ET)
AFTER_CLOSE = CLOSE + timedelta(minutes=5)
DEADLINE_PASSED = datetime(2024, 6, 18, 10, 31, tzinfo=ET)
CAPITAL = Decimal("7428.92")
NOON = datetime(2024, 6, 17, 12, tzinfo=ET)


# --- Tape ---------------------------------------------------------------------------------------

def bar(symbol: str, at: datetime, close: float, session: MarketSession = MarketSession.REGULAR,
        *, open_: float | None = None, volume: int | None = None) -> MinuteBar:
    default_volume = 100_000 if session is MarketSession.PREMARKET else 20_000
    return MinuteBar(symbol=symbol, timestamp=at, open=close if open_ is None else open_,
                     high=max(close, close if open_ is None else open_) + .2,
                     low=min(close, close if open_ is None else open_) - .2, close=close,
                     volume=default_volume if volume is None else volume, session=session,
                     observed_at=at + timedelta(minutes=1), available_at=at + timedelta(minutes=1))


def tape(symbol: str = "AAA", *, premarket_close: float = 105.0,
         premarket_volume: int = 100_000, opening_range: bool = True,
         breakout: bool = True, execution_open: float = 102.0) -> list[MinuteBar]:
    """One symbol's session. Defaults pass the gate, build an OR, and break out."""
    bars = [bar(symbol, datetime(2024, 6, 17, 15, 59, tzinfo=ET), 100.0),
            bar(symbol, datetime(2024, 6, 18, 8, 0, tzinfo=ET), premarket_close,
                MarketSession.PREMARKET, volume=premarket_volume)]
    if opening_range:
        bars += [bar(symbol, OPEN + timedelta(minutes=index), 100.0) for index in range(15)]
    if breakout:
        bars += [bar(symbol, OPEN + timedelta(minutes=15), 102.0),
                 bar(symbol, OPEN + timedelta(minutes=16), 102.1),
                 bar(symbol, OPEN + timedelta(minutes=17), 102.2, open_=execution_open)]
    else:
        # Never above the opening range high, so no signal is ever produced.
        bars += [bar(symbol, OPEN + timedelta(minutes=index), 99.9) for index in range(15, 62)]
    return bars


class Provider:
    def __init__(self, *tapes: list[MinuteBar]) -> None:
        self.minutes = [item for stream in (tapes or (tape(),)) for item in stream]
        self.failures: dict[str, str | None] = {}

    def fail(self, symbol: str, code: str | None) -> None:
        self.failures[symbol] = code

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        for symbol in symbols:
            code = self.failures.get(symbol)
            if code is not None:
                raise MarketDataError(code, f"{symbol} is unavailable")
        return [item for item in self.minutes
                if item.symbol in set(symbols)
                and (start is None or item.timestamp >= start)
                and (end is None or item.timestamp <= end)
                and (session is None or item.session is session)]

    def get_daily_bars(self, symbols, start=None, end=None):  # type: ignore[no-untyped-def]
        at = datetime(2024, 6, 17, 16, 0, tzinfo=ET)
        return [DailyBar(symbol=symbol, trading_date=ANALYSIS_DAY, open=100, high=101, low=99,
                         close=100, volume=1_000_000, observed_at=at,
                         available_at=at + timedelta(minutes=1)) for symbol in symbols]


# --- Fixtures -----------------------------------------------------------------------------------

@pytest.fixture
def durable(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'evaluations.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        account_id = SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CAPITAL, cash=CAPITAL, created_at=NOON).id
        session.commit()
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), account_id, factory)
    yield runtime, factory
    engine.dispose()


def seed(factory, symbols: tuple[str, ...] = ("AAA",)) -> None:  # type: ignore[no-untyped-def]
    """One completed run and active analysis on the entry session's predecessor."""
    with factory() as session:
        run = ScannerRun(trading_date=ANALYSIS_DAY, started_at=NOON, status="COMPLETED",
                         completed_at=NOON, provider="KIWOOM_REAL", score_version="v0")
        session.add(run)
        session.flush()
        analysis = GPTAnalysis(scanner_run_id=run.id, trading_date=ANALYSIS_DAY, provider="gpt",
                               model="m", prompt_version="1", schema_version="1",
                               evidence_version="1", status="IMPORTED", raw_json="{}",
                               payload_hash="current", analysis_at=NOON)
        session.add(analysis)
        session.flush()
        run.active_gpt_analysis_id = analysis.id
        for rank, symbol in enumerate(symbols, start=1):
            scanned = ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=rank,
                                       is_top8=True, score=1.0,
                                       score_components_json={"exchange": "NASDAQ"},
                                       observed_at=NOON, available_at=NOON)
            session.add(scanned)
            session.flush()
            session.add(GPTCandidateAnalysis(
                gpt_analysis_id=analysis.id, scanner_candidate_id=scanned.id, symbol=symbol,
                gpt_rank=rank, overall_score=1, catalyst_score=1, fundamental_score=1,
                momentum_score=1, risk_score=1, evidence_confidence=1, catalyst_duration="D",
                stop_profile="TIGHT", trailing_profile=TrailingProfile.WIDE.value,
                overnight_suitability=OvernightSuitability.MEDIUM.value, company_summary="",
                catalyst_summary="", risk_summary="", invalidation_summary="",
                unknown_fields_json=[]))
            session.add(HumanDecisionRecord(
                gpt_analysis_id=analysis.id, scanner_candidate_id=scanned.id, symbol=symbol,
                decision="APPROVE", decided_at=NOON))
        session.commit()


def service(runtime) -> EntryLifecycleService:  # type: ignore[no-untyped-def]
    return EntryLifecycleService(runtime)


def candidates(runtime):  # type: ignore[no-untyped-def]
    return service(runtime).approved_candidates_for_entry_session(ENTRY_DAY)


def rows(factory) -> list[PaperEntryEvaluation]:  # type: ignore[no-untyped-def]
    with factory() as session:
        return list(session.scalars(select(PaperEntryEvaluation)
                                    .order_by(PaperEntryEvaluation.symbol)))


def row_for(factory, symbol: str = "AAA") -> PaperEntryEvaluation:  # type: ignore[no-untyped-def]
    found = [item for item in rows(factory) if item.symbol == symbol]
    assert len(found) == 1, f"expected one row for {symbol}, found {len(found)}"
    return found[0]


def run_session(runtime, factory, provider: Provider, moments) -> None:  # type: ignore[no-untyped-def]
    """Evaluate every approved candidate at each moment, then finalize after the close."""
    lifecycle = service(runtime)
    for moment in moments:
        for candidate in candidates(runtime):
            lifecycle.evaluate(candidate, provider, as_of=moment)
    lifecycle.finalize_session(ENTRY_DAY, now=AFTER_CLOSE)


# --- 1-2. Premarket rejections stay in history ---------------------------------------------------

def test_a_gap_rejection_keeps_its_reason_values_and_thresholds(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(premarket_close=100.8)),
                [OPEN + timedelta(minutes=1)])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.PREMARKET_REJECTED.value
    assert row.final_reason == StrategyReason.GAP_TOO_LOW.value
    assert row.gap_pct == Decimal("0.008") and row.gap_min == Decimal("0.02")
    assert row.gap_max == Decimal("0.15") and row.premarket_passed is False
    assert row.previous_close == Decimal("100.0") and row.premarket_reference_price == Decimal("100.8")
    assert row.rank == 1 and row.last_phase == StrategyPhase.PREMARKET_REJECTED.value


def test_a_premarket_volume_rejection_keeps_its_ratio_and_threshold(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(premarket_volume=10_000)),
                [OPEN + timedelta(minutes=1)])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.PREMARKET_REJECTED.value
    assert row.final_reason == StrategyReason.LOW_PREMARKET_VOLUME.value
    assert row.v1_volume_ratio == Decimal("0.01") and row.v1_volume_min == Decimal("0.05")
    assert row.premarket_volume == Decimal("10000") and row.premarket_passed is False


# --- 3-4. Opening range and the deadline ---------------------------------------------------------

def test_an_incomplete_opening_range_is_recorded_as_its_own_outcome(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(opening_range=False, breakout=False)),
                [DEADLINE_PASSED])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.OR_REJECTED.value
    assert row.final_reason == StrategyReason.INSUFFICIENT_OPENING_RANGE.value
    assert row.premarket_passed is True and row.opening_range_ready is False
    assert row.entry_signalled is False
    with factory() as session:  # the durable state carries the same reason now
        state = session.scalar(select(StrategyStateRecord))
        assert (state.phase, state.phase_reason) == (
            StrategyPhase.NO_TRADE.value, StrategyReason.INSUFFICIENT_OPENING_RANGE.value)


def test_a_complete_opening_range_without_a_breakout_ends_as_no_entry_signal(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(breakout=False)),
                [OPEN + timedelta(minutes=20), DEADLINE_PASSED])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.NO_ENTRY_SIGNAL.value
    assert row.final_reason == StrategyReason.ENTRY_DEADLINE_EXPIRED.value
    assert row.premarket_passed is True and row.opening_range_ready is True
    assert row.entry_signalled is False and row.entry_filled is False
    assert row.opening_range_high == Decimal("100.2") and row.opening_range_low == Decimal("99.8")


# --- 5-7. Settlement ------------------------------------------------------------------------------

def test_a_price_above_the_ceiling_is_recorded_with_the_brokers_own_rejection(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(execution_open=130.0)),
                [OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.EXECUTION_REJECTED.value
    assert row.final_reason == StrategyReason.ENTRY_PRICE_ABOVE_CEILING.value
    assert row.final_detail == "PRICE_ABOVE_LIMIT"
    assert row.entry_signalled is True and row.entry_filled is False
    assert row.signal_price == Decimal("102.0")
    assert row.signal_at == OPEN + timedelta(minutes=16)
    assert row.intended_entry_bar_at == OPEN + timedelta(minutes=17)


def test_a_risk_rejection_is_recorded_with_the_risk_engines_own_reason(durable) -> None:
    runtime, factory = durable
    seed(factory)
    with factory() as session:  # the symbol was already attempted today
        session.add(DailySymbolState(
            trading_date=ENTRY_DAY, symbol="AAA", entry_intent_issued_at=OPEN,
            planned_risk_amount=Decimal("1"), base_notional_reserved=Decimal("1"),
            pyramid_notional_reserved=Decimal("0"), add_count=0, risk_version="risk_v1",
            strategy_version="strategy_v0", created_at=OPEN, updated_at=OPEN))
        session.commit()

    run_session(runtime, factory, Provider(),
                [OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.EXECUTION_REJECTED.value
    assert row.final_reason == StrategyReason.ENTRY_RISK_REJECTED.value
    assert row.final_detail == "SYMBOL_ALREADY_ATTEMPTED"
    assert runtime.broker.get_position("AAA") is None


def test_a_filled_entry_is_recorded_as_traded_with_its_fill(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(),
                [OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.TRADED.value
    assert row.entry_filled is True and row.entry_signalled is True
    assert row.fill_price is not None and row.fill_quantity > 0
    assert row.order_id is not None and row.trade_uid is not None
    assert row.last_phase == StrategyPhase.POSITION_OPEN.value


# --- 8-9. Provider failures ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_provider_failure_that_never_recovers_finalizes_as_a_data_error(durable) -> None:
    runtime, factory = durable
    seed(factory)
    provider = Provider()
    provider.fail("AAA", "MARKET_DATA_UNAVAILABLE")
    owner = EntryManagementRuntime(runtime, lambda: provider)

    for minutes in (1, 2, 3):
        await owner.run_once(as_of=OPEN + timedelta(minutes=minutes))
    interim = row_for(factory)
    assert interim.final_status == EvaluationStatus.IN_PROGRESS.value
    assert interim.error_tick_count == 3

    await owner.run_once(as_of=AFTER_CLOSE)

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.DATA_ERROR.value
    assert row.final_reason == "MARKET_DATA_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_recovered_provider_failure_never_leaves_a_terminal_failure(durable) -> None:
    runtime, factory = durable
    seed(factory)
    provider = Provider(tape(premarket_close=100.8))
    provider.fail("AAA", "AUTH_FAILED")
    owner = EntryManagementRuntime(runtime, lambda: provider)

    await owner.run_once(as_of=OPEN + timedelta(minutes=1))
    assert row_for(factory).final_status == EvaluationStatus.IN_PROGRESS.value
    provider.fail("AAA", None)
    await owner.run_once(as_of=OPEN + timedelta(minutes=2))
    await owner.run_once(as_of=AFTER_CLOSE)

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.PREMARKET_REJECTED.value
    assert row.final_reason == StrategyReason.GAP_TOO_LOW.value
    # The failed tick stays visible as evidence without ever becoming the verdict.
    assert row.error_tick_count == 1 and row.last_error_code == "AUTH_FAILED"


# --- 10-11. The daily funnel ------------------------------------------------------------------------

def test_a_day_with_no_trade_is_a_normal_summary_row(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(premarket_close=100.8)),
                [OPEN + timedelta(minutes=1)])

    with factory() as session:
        history = evaluation_history(session, calendar=MarketCalendar())
    assert [item["trading_date"] for item in history] == [ENTRY_DAY]
    summary = history[0]
    assert summary["approved_count"] == 1 and summary["gap_rejected_count"] == 1
    assert summary["filled_count"] == 0 and summary["trade_count"] == 0
    assert summary["result"] == "NO_TRADE_DAY" and summary["realized_pnl"] == "0"
    assert summary["source"] == SOURCE_EVALUATION


def test_every_approved_candidate_ends_with_exactly_one_outcome(durable) -> None:
    runtime, factory = durable
    seed(factory, ("AAA", "BBB", "CCC", "DDD", "EEE"))
    provider = Provider(
        tape("AAA"),                                    # fills
        tape("BBB", premarket_close=100.8),             # gap too low
        tape("CCC", premarket_volume=10_000),           # low premarket volume
        tape("DDD", breakout=False),                    # no entry signal
        tape("EEE", opening_range=False, breakout=False))  # insufficient opening range
    run_session(runtime, factory, provider,
                [OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18), DEADLINE_PASSED])

    with factory() as session:
        detail = evaluation_detail(session, ENTRY_DAY, calendar=MarketCalendar())
    outcomes = {item["symbol"]: item["final_status"] for item in detail["candidates"]}
    assert outcomes == {"AAA": EvaluationStatus.TRADED.value,
                        "BBB": EvaluationStatus.PREMARKET_REJECTED.value,
                        "CCC": EvaluationStatus.PREMARKET_REJECTED.value,
                        "DDD": EvaluationStatus.NO_ENTRY_SIGNAL.value,
                        "EEE": EvaluationStatus.OR_REJECTED.value}
    summary = detail["summary"]
    assert summary["approved_count"] == 5
    assert (summary["filled_count"] + summary["premarket_rejected_count"]
            + summary["or_rejected_count"] + summary["no_entry_signal_count"]
            + summary["execution_rejected_count"] + summary["data_error_count"]
            + summary["incomplete_count"]) == 5
    assert [item["rank"] for item in detail["candidates"]] == [1, 2, 3, 4, 5]


# --- 12-14. Idempotency, restart, and a later run ----------------------------------------------------

def test_a_restart_reuses_the_stored_outcome_without_a_second_row(durable) -> None:
    runtime, factory = durable
    seed(factory)
    provider = Provider()
    run_session(runtime, factory, provider,
                [OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)])
    stored = row_for(factory)

    with factory() as session:
        restarted = SimulationRuntimeContext(rehydrate_sim_broker(session, runtime.account_id),
                                             runtime.account_id, factory)
    lifecycle = EntryLifecycleService(restarted)
    for candidate in candidates(restarted):
        lifecycle.evaluate(candidate, provider, as_of=OPEN + timedelta(minutes=19))
    lifecycle.finalize_session(ENTRY_DAY, now=AFTER_CLOSE)

    assert len(rows(factory)) == 1
    after = row_for(factory)
    assert (after.id, after.final_status, after.final_reason) == (
        stored.id, stored.final_status, stored.final_reason)


def test_finalizing_the_same_session_twice_changes_nothing(durable) -> None:
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(premarket_close=100.8)),
                [OPEN + timedelta(minutes=1)])
    first = row_for(factory)
    finalized_at, status = first.finalized_at, first.final_status

    service(runtime).finalize_session(ENTRY_DAY, now=AFTER_CLOSE + timedelta(hours=2))

    assert len(rows(factory)) == 1
    again = row_for(factory)
    assert (again.final_status, again.finalized_at) == (status, finalized_at)


def test_a_later_scanner_run_never_changes_a_finished_session(durable) -> None:
    """The entry board follows the current run; history answers for its own session."""
    runtime, factory = durable
    seed(factory)
    run_session(runtime, factory, Provider(tape(premarket_close=100.8)),
                [OPEN + timedelta(minutes=1)])
    with factory() as session:
        before = evaluation_detail(session, ENTRY_DAY, calendar=MarketCalendar())

    with factory() as session:  # a newer run for the next entry session
        later = ScannerRun(trading_date=ENTRY_DAY, started_at=CLOSE, status="COMPLETED",
                           completed_at=CLOSE, provider="KIWOOM_REAL", score_version="v0")
        session.add(later)
        session.flush()
        session.add(ScannerCandidate(scanner_run_id=later.id, symbol="ZZZ", rank=1, is_top8=True,
                                     score=1.0, score_components_json={"exchange": "NASDAQ"},
                                     observed_at=CLOSE, available_at=CLOSE))
        session.commit()

    with factory() as session:
        after = evaluation_detail(session, ENTRY_DAY, calendar=MarketCalendar())
    assert after == before


# --- 15. Legacy sessions ----------------------------------------------------------------------------

def test_a_legacy_session_is_projected_and_a_missing_reason_is_never_guessed(durable) -> None:
    runtime, factory = durable
    seed(factory)
    with factory() as session:  # a NO_TRADE the old runtime wrote without a reason
        session.add(StrategyStateRecord(
            symbol="AAA", trading_date=ENTRY_DAY, scanner_candidate_id=session.scalar(
                select(ScannerCandidate.id).where(ScannerCandidate.symbol == "AAA")),
            book="ACTUAL", variant="ACTUAL", phase=StrategyPhase.NO_TRADE.value,
            phase_reason=None, add_count=0, add_signal_issued=False, holding_day=0,
            overnight=False, strategy_version="strategy_v0", trailing_profile="WIDE",
            overnight_suitability="MEDIUM", created_at=OPEN, updated_at=CLOSE))
        session.commit()

    with factory() as session:
        detail = evaluation_detail(session, ENTRY_DAY, calendar=MarketCalendar())
    assert detail["source"] == SOURCE_LEGACY_STATE
    row = detail["candidates"][0]
    assert row["final_status"] == EvaluationStatus.INCOMPLETE.value
    assert row["final_reason"] == INCOMPLETE_LEGACY
    assert row["gap_pct"] is None and row["opening_range_high"] is None
    assert row["opening_range_ready"] is None
    # A read-only projection: no evaluation row is created for a past session.
    assert rows(factory) == []


def test_a_candidate_the_runtime_never_reached_is_incomplete_not_excluded(durable) -> None:
    runtime, factory = durable
    seed(factory)

    service(runtime).finalize_session(ENTRY_DAY, now=AFTER_CLOSE)

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.INCOMPLETE.value
    assert row.final_reason == NOT_EVALUATED and row.final_detail is None


# --- 17. Unknown reasons ------------------------------------------------------------------------------

def test_an_unrecognised_reason_is_reported_not_interpreted() -> None:
    assert classify("SOMETHING_NEW") is EvaluationStatus.UNKNOWN
    assert classify(None) is EvaluationStatus.INCOMPLETE
    assert classify(StrategyReason.INVALID_PREMARKET_DATA.value,
                    invalid_field="NO_EXACT_PREVIOUS_CLOSE") is EvaluationStatus.DATA_ERROR
    assert classify(StrategyReason.INVALID_PREMARKET_DATA.value,
                    invalid_field="NO_PREMARKET_BARS") is EvaluationStatus.PREMARKET_REJECTED


# --- 18. The evaluation history changes no trading decision ---------------------------------------------

def test_stored_v2_volume_history_changes_no_entry_decision_and_no_v1_record(durable) -> None:
    """V2 is counterfactual analytics: the gate, the fill, and the row stay V1's own."""
    runtime, factory = durable
    seed(factory)
    with factory() as session:
        # A V2 baseline that would read this premarket volume very differently.
        session.add(PremarketVolumeSession(
            symbol="AAA", exchange="ND", trading_date=ANALYSIS_DAY, source="KIWOOM",
            collector_version=COLLECTOR_VERSION, premarket_volume=1, bar_count=1,
            regular_bar_count=0, quality_status="CLEAN", collected_at=NOON,
            created_at=NOON, updated_at=NOON))
        session.commit()

    run_session(runtime, factory, Provider(),
                [OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)])

    row = row_for(factory)
    assert row.final_status == EvaluationStatus.TRADED.value
    assert runtime.broker.get_position("AAA") is not None
    # The ratio recorded is the V1 one the gate used, against the V1 threshold.
    assert row.v1_volume_ratio == Decimal("0.1") and row.v1_volume_min == Decimal("0.05")


def test_a_failing_recorder_never_changes_the_entry_outcome(durable, monkeypatch) -> None:
    runtime, factory = durable
    seed(factory)
    lifecycle = service(runtime)

    def explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("evaluation history is unavailable")

    monkeypatch.setattr("app.services.entry_evaluation_log.upsert", explode)
    provider = Provider()
    actions = []
    for moment in (OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)):
        for candidate in candidates(runtime):
            actions.append(lifecycle.evaluate(candidate, provider, as_of=moment).action)

    assert actions == [EntryAction.HOLD, EntryAction.FILLED]
    assert runtime.broker.get_position("AAA") is not None
    assert rows(factory) == []


def test_the_finalizer_refuses_to_answer_for_a_session_that_is_still_open(durable) -> None:
    runtime, factory = durable
    seed(factory)

    assert service(runtime).finalize_session(ENTRY_DAY, now=OPEN + timedelta(minutes=30)) == 0
    assert rows(factory) == []


# --- Migration -----------------------------------------------------------------------------------

def _alembic(path: Path, monkeypatch: MonkeyPatch, revision: str, *, down: bool = False) -> None:
    monkeypatch.setenv("RUNTIME_PROFILE", "default")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    (command.downgrade if down else command.upgrade)(Config(PROJECT_ROOT / "alembic.ini"), revision)


BUSINESS_QUERIES = {
    "scanner_runs": "SELECT id, trading_date, status FROM scanner_runs ORDER BY id",
    "scanner_candidates": "SELECT id, scanner_run_id, symbol FROM scanner_candidates ORDER BY id",
    "gpt_analyses": "SELECT id, scanner_run_id, status, payload_hash FROM gpt_analyses ORDER BY id",
    "strategy_states": ("SELECT symbol, trading_date, phase, phase_reason FROM strategy_states "
                        "ORDER BY id"),
    "entry_drift_observations": ("SELECT scanner_candidate_id, symbol, status "
                                 "FROM entry_drift_observations ORDER BY id"),
}


def _business_rows(path: Path) -> dict[str, list[tuple]]:  # type: ignore[type-arg]
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


def _seed_0016_rows(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    at = "2026-09-15T20:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO scanner_runs (id, trading_date, started_at,
            completed_at, status, provider, score_version, universe_count, excluded_count,
            candidate_count, top8_count, created_at) VALUES (1, '2026-09-12', :at, :at,
            'COMPLETED', 'TEST', 'quant_v0', 0, 0, 1, 1, :at)"""), {"at": at})
        connection.execute(text("""INSERT INTO scanner_candidates (id, scanner_run_id, symbol,
            rank, is_top8, score, score_components_json, observed_at, available_at, created_at)
            VALUES (7, 1, 'AAA', 1, 1, 1.0, '{"exchange": "NASDAQ"}', :at, :at, :at)"""), {"at": at})
        connection.execute(text("""INSERT INTO gpt_analyses (id, scanner_run_id, trading_date,
            provider, model, prompt_version, schema_version, evidence_version, analysis_at,
            imported_at, status, raw_json, payload_hash) VALUES (3, 1, '2026-09-12', 'GPT', 'm',
            'p', 's', 'e', :at, :at, 'IMPORTED', '{}', :hash)"""), {"at": at, "hash": "b" * 64})
        connection.execute(text("""INSERT INTO strategy_states (symbol, trading_date,
            scanner_candidate_id, book, variant, phase, phase_reason, add_count,
            add_signal_issued, holding_day, overnight, strategy_version, trailing_profile,
            overnight_suitability, created_at, updated_at) VALUES ('AAA', '2026-09-15', 7,
            'ACTUAL', 'ACTUAL', 'NO_TRADE', NULL, 0, 0, 0, 0, 'strategy_v0', 'WIDE', 'MEDIUM',
            :at, :at)"""), {"at": at})
        connection.execute(text("""INSERT INTO entry_drift_observations (trading_date,
            scanner_run_id, gpt_analysis_id, scanner_candidate_id, symbol, exchange,
            strategy_version, risk_version, observer_version, status, projections_json,
            created_at, updated_at) VALUES ('2026-09-15', 1, 3, 7, 'AAA', 'ND', 'strategy_v0',
            'risk_v1', 'entry_drift_observer_v2', 'NO_SIGNAL', '[]', :at, :at)"""), {"at": at})
    engine.dispose()


def test_migration_0016_to_0017_roundtrip_preserves_business_rows(
        tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "roundtrip.sqlite3"
    try:
        _alembic(path, monkeypatch, "20260915_0016")
        fingerprint_0016 = _fingerprint(path)
        _seed_0016_rows(path)
        business = _business_rows(path)
        assert all(business.values())

        _alembic(path, monkeypatch, "head")
        head = _fingerprint(path)
        engine = create_engine(f"sqlite:///{path}")
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalars().all() == ["20260921_0018"]
            # No backfill: a past session gains no invented evaluation row.
            assert connection.execute(text(
                "SELECT count(*) FROM paper_entry_evaluations")).scalar() == 0
            assert connection.execute(text("PRAGMA quick_check")).scalars().all() == ["ok"]
            assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        with engine.begin() as connection:  # exact Decimal text, never a rounded REAL
            connection.execute(text("""INSERT INTO paper_entry_evaluations (trading_date,
                analysis_trading_date, scanner_run_id, gpt_analysis_id, scanner_candidate_id,
                symbol, final_status, v1_volume_ratio, error_tick_count, strategy_version,
                created_at, updated_at) VALUES ('2026-09-15', '2026-09-12', 1, 3, 7, 'AAA',
                'IN_PROGRESS', '0.0499999999', 0, 'strategy_v0', :at, :at)"""),
                {"at": "2026-09-15T20:00:00+00:00"})
            assert connection.execute(text(
                "SELECT typeof(v1_volume_ratio), v1_volume_ratio FROM paper_entry_evaluations"
            )).one() == ("text", "0.0499999999")
            connection.execute(text("DELETE FROM paper_entry_evaluations"))
        engine.dispose()
        assert _business_rows(path) == business

        created = create_engine(f"sqlite:///{tmp_path / 'create-all.sqlite3'}")
        Base.metadata.create_all(created)
        assert schema_fingerprint(created) == head
        created.dispose()

        _alembic(path, monkeypatch, "20260915_0016", down=True)
        assert _fingerprint(path) == fingerprint_0016 and _business_rows(path) == business
        _alembic(path, monkeypatch, "head")
        assert _fingerprint(path) == head and _business_rows(path) == business
    finally:
        get_settings.cache_clear()


def test_the_fresh_head_holds_the_evaluation_grain(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "fresh.sqlite3"
    try:
        _alembic(path, monkeypatch, "head")
        fingerprint = _fingerprint(path)
    finally:
        get_settings.cache_clear()
    assert fingerprint["unique_constraints"]["paper_entry_evaluations"] == [  # type: ignore[index]
        ("trading_date", "scanner_candidate_id")]
    head = ScriptDirectory.from_config(Config(PROJECT_ROOT / "alembic.ini")).get_current_head()
    assert head == "20260921_0018"


# --- Trading safety ------------------------------------------------------------------------------

def test_the_recorded_no_trade_reason_is_never_read_by_a_trading_rule(durable) -> None:
    """The reason is written for the record; no gate, sizing, or exit rule consults it."""
    runtime, factory = durable
    seed(factory)
    lifecycle = service(runtime)
    provider = Provider(tape(opening_range=False, breakout=False))
    for candidate in candidates(runtime):
        lifecycle.evaluate(candidate, provider, as_of=DEADLINE_PASSED)
    with factory() as session:
        state = session.scalar(select(StrategyStateRecord))
        assert state.phase_reason == StrategyReason.INSUFFICIENT_OPENING_RANGE.value

    # A terminal state is skipped on its phase alone, and a NO_TRADE never signals an exit.
    later = [lifecycle.evaluate(item, provider, as_of=DEADLINE_PASSED + timedelta(minutes=1))
             for item in candidates(runtime)]
    assert [item.action for item in later] == [EntryAction.SKIPPED]
    assert [item.reason for item in later] == [StrategyPhase.NO_TRADE.value]
    assert runtime.broker.get_positions() == () and runtime.broker.get_fills() == ()
    assert not is_mandatory_exit(StrategyState(
        "AAA", ENTRY_DAY, phase=StrategyPhase.NO_TRADE,
        phase_reason=StrategyReason.INSUFFICIENT_OPENING_RANGE.value))


def test_a_legacy_day_is_summarised_from_its_own_records_only(durable) -> None:
    runtime, factory = durable
    seed(factory, ("AAA", "BBB"))
    with factory() as session:
        ids = {row.symbol: row.id for row in session.scalars(select(ScannerCandidate))}
        # One session that rejected on gap, and one that recorded nothing at all.
        session.add(StrategyStateRecord(
            symbol="AAA", trading_date=ENTRY_DAY, scanner_candidate_id=ids["AAA"], book="ACTUAL",
            variant="ACTUAL", phase=StrategyPhase.PREMARKET_REJECTED.value,
            phase_reason=StrategyReason.GAP_TOO_LOW.value, add_count=0, add_signal_issued=False,
            holding_day=0, overnight=False, strategy_version="strategy_v0",
            trailing_profile="WIDE", overnight_suitability="MEDIUM",
            created_at=OPEN, updated_at=OPEN))
        session.commit()

    with factory() as session:
        detail = evaluation_detail(session, ENTRY_DAY, calendar=MarketCalendar())
    assert detail["source"] == SOURCE_LEGACY_STATE
    outcomes = {row["symbol"]: (row["final_status"], row["final_reason"])
                for row in detail["candidates"]}
    assert outcomes == {"AAA": (EvaluationStatus.PREMARKET_REJECTED.value, "GAP_TOO_LOW"),
                        "BBB": (EvaluationStatus.INCOMPLETE.value, NOT_EVALUATED)}
    summary = detail["summary"]
    assert summary["approved_count"] == 2 and summary["gap_rejected_count"] == 1
    assert summary["incomplete_count"] == 1 and summary["result"] == "NO_TRADE_DAY"
    # The frozen policy that judged it is recorded, so its thresholds are reported.
    assert detail["candidates"][0]["thresholds_source"] == "STRATEGY_VERSION_MATCH"
    assert detail["candidates"][1]["gap_min"] is None


@pytest.mark.asyncio
async def test_the_runtime_finalizes_after_the_close_without_trading_anything(durable) -> None:
    """The post-close tick is the finalization cadence, and it submits no order."""
    runtime, factory = durable
    seed(factory, ("AAA", "BBB"))
    provider = Provider(tape("AAA", breakout=False), tape("BBB", premarket_close=100.8))
    owner = EntryManagementRuntime(runtime, lambda: provider)

    await owner.run_once(as_of=OPEN + timedelta(minutes=20))
    assert {row.final_status for row in rows(factory)} == {
        EvaluationStatus.IN_PROGRESS.value, EvaluationStatus.PREMARKET_REJECTED.value}

    assert await owner.run_once(as_of=AFTER_CLOSE) == ()

    outcomes = {row.symbol: row.final_status for row in rows(factory)}
    assert outcomes == {"AAA": EvaluationStatus.INCOMPLETE.value,
                        "BBB": EvaluationStatus.PREMARKET_REJECTED.value}
    # Stopped mid-evaluation, which is not the same as a finished session missing a reason.
    assert row_for(factory, "AAA").final_reason == EVALUATION_INCOMPLETE
    assert row_for(factory, "AAA").final_detail == StrategyPhase.WAITING_ENTRY.value
    assert runtime.broker.get_fills() == () and runtime.broker.get_open_orders() == ()
    # A second post-close tick is idempotent; the finalized outcomes stand.
    await owner.run_once(as_of=AFTER_CLOSE + timedelta(minutes=30))
    assert len(rows(factory)) == 2
    assert {row.symbol: row.final_status for row in rows(factory)} == outcomes
