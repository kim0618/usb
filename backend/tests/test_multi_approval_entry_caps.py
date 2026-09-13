"""APPROVE admits a symbol to entry evaluation; RiskConfig caps what is actually entered.

Runtime tests use the default risk_v1 engine: a fixed session 1R, a 3R daily budget, and
single-counted base exposure let three 1R entries fit, so the count caps bind.
"""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.database import Base, create_db_engine
from app.execution.domain import IntentType, OrderSide
from app.market.domain import MarketSession
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.research import HumanDecisionRecord
from app.models.strategy import StrategyStateRecord
from app.repositories.research import ResearchRepository
from app.risk.config import RiskConfig
from app.risk.domain import Currency, DailyTradingState, PositionSnapshot, RiskRejectionReason
from app.risk.engine import RiskEngine
from app.services.entry_capacity import (
    EntryCapacity, EntryCapacityReason, pending_entries, pending_entry_symbols,
)
from app.services.entry_management_runtime import (
    EntryAction, EntryLifecycleService, EntryManagementRuntime,
)
from app.services.position_management_runtime import PositionManagementRuntime
from app.services.research import GPTImportService, HumanDecisionService
from app.strategy.lifecycle import StrategyPhase
import tests.test_research_stage4 as research
import tests.test_trading_risk_stage5 as risk
from tests.test_entry_management_runtime import DAY, OPEN, Provider, durable, seed_session  # noqa: F401

ANALYSIS_DAY = date(2024, 6, 17)  # XNYS predecessor of the 2024-06-18 entry session


def test_default_caps_are_three_new_symbols_and_three_open_positions() -> None:
    config = RiskConfig()
    assert (config.max_new_symbols_per_day, config.max_open_positions) == (3, 3)
    assert config.max_daily_risk_units == Decimal("3")


# --- Approval contract ---------------------------------------------------------------

def test_every_top8_candidate_can_be_approved(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'approve.sqlite3'}")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker
    with sessionmaker(bind=engine)() as db:
        run, scanner = research.setup_run(db, 8)
        analysis = GPTImportService(ResearchRepository(db), scanner).import_json(
            json.dumps(research.payload(run.id, 8)))
        service = HumanDecisionService(ResearchRepository(db))
        for candidate in ResearchRepository(db).get_candidates(analysis.id):
            service.decide(analysis.id, candidate.symbol, "APPROVE")
        assert ResearchRepository(db).count_approvals(analysis.id) == 8
        service.decide(analysis.id, "AAA", "REJECT")  # replace semantics unchanged
        assert ResearchRepository(db).count_approvals(analysis.id) == 7
        assert db.scalar(select(func.count()).select_from(HumanDecisionRecord)) == 8
    engine.dispose()


def test_six_approvals_become_six_entry_candidates_in_gpt_rank_order(durable) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("FFF", "EEE", "DDD", "CCC", "BBB", "AAA"))
        session.commit()
    approved = EntryLifecycleService(runtime).approved_candidates_for_entry_session(DAY)
    assert [c.symbol for c in approved] == ["FFF", "EEE", "DDD", "CCC", "BBB", "AAA"]


# --- Risk gate -------------------------------------------------------------------------

def held(symbol: str) -> PositionSnapshot:
    return PositionSnapshot(symbol, Decimal("1"), Decimal("100"), Decimal("100"), Currency.USD)


def test_fourth_new_symbol_is_rejected_after_three_entries() -> None:
    state = DailyTradingState(risk.DAY, attempted_symbols=frozenset({"X", "Y", "Z"}))
    assert risk.evaluate(daily_state=state).rejection_reason is RiskRejectionReason.DAILY_SYMBOL_LIMIT


def test_same_day_exit_does_not_free_a_new_entry_slot() -> None:
    # X, Y, Z were entered today and Z already exited: two held, three still counted.
    state = DailyTradingState(risk.DAY, attempted_symbols=frozenset({"X", "Y", "Z"}))
    result = risk.evaluate(daily_state=state, portfolio=risk.portfolio(held("X"), held("Y")))
    assert result.rejection_reason is RiskRejectionReason.DAILY_SYMBOL_LIMIT


def test_two_overnight_positions_leave_exactly_one_open_slot() -> None:
    two = risk.portfolio(held("P"), held("Q"))
    assert risk.evaluate(portfolio=two).approved
    three = risk.portfolio(held("P"), held("Q"), held("R"))
    assert risk.evaluate(portfolio=three).rejection_reason is RiskRejectionReason.OPEN_POSITION_LIMIT


def test_pending_base_entry_consumes_open_and_daily_capacity() -> None:
    open_two_pending_one = risk.portfolio(held("P"), held("Q"))
    open_two_pending_one = type(open_two_pending_one)(
        open_two_pending_one.positions, Decimal("0"), Decimal("0"), risk.NOW, {"R": Decimal("1000")})
    assert risk.evaluate(portfolio=open_two_pending_one).rejection_reason is RiskRejectionReason.OPEN_POSITION_LIMIT
    pending_two = type(open_two_pending_one)((), Decimal("0"), Decimal("0"), risk.NOW, {"R": Decimal("1000"), "S": Decimal("1000")})
    state = DailyTradingState(risk.DAY, attempted_symbols=frozenset({"T"}))
    assert risk.evaluate(daily_state=state, portfolio=pending_two).rejection_reason is RiskRejectionReason.DAILY_SYMBOL_LIMIT


def test_same_day_reentry_stays_forbidden_including_a_pending_entry() -> None:
    state = DailyTradingState(risk.DAY, attempted_symbols=frozenset({"AAA"}))
    assert risk.evaluate(daily_state=state).rejection_reason is RiskRejectionReason.SYMBOL_ALREADY_ATTEMPTED
    pending = type(risk.portfolio())((), Decimal("0"), Decimal("0"), risk.NOW, {"AAA": Decimal("1000")})
    assert risk.evaluate(portfolio=pending).rejection_reason is RiskRejectionReason.SYMBOL_ALREADY_ATTEMPTED


def test_exhausted_three_r_budget_binds_before_the_symbol_cap() -> None:
    engine = RiskEngine()
    three_r = engine.one_r(risk.account()) * 3
    state = DailyTradingState(risk.DAY, attempted_symbols=frozenset({"X", "Y"}), planned_risk_reserved=three_r)
    assert risk.evaluate(engine=engine, daily_state=state).rejection_reason is RiskRejectionReason.DAILY_RISK_LIMIT


# --- Capacity projection ---------------------------------------------------------------

def capacity(entered=(), open_=(), pending=()) -> EntryCapacity:
    return EntryCapacity(DAY, frozenset(entered), frozenset(open_), frozenset(pending), 3, 3)


def test_capacity_reasons_are_distinct_and_pending_counts_once() -> None:
    assert capacity(entered="ABC", open_="AB").blocked_reason is EntryCapacityReason.DAILY_ENTRY_CAP_REACHED
    assert capacity(open_="XY", pending="Z").blocked_reason is EntryCapacityReason.OPEN_POSITION_CAP_REACHED
    assert capacity(entered="A", open_="XY").blocked_reason is None
    partial = capacity(entered="A", open_="A", pending="A")
    assert (partial.new_entries_used, partial.open_positions_used) == (1, 1)


def test_only_base_entry_buy_orders_are_pending_entries() -> None:
    def order(symbol, side, intent):  # type: ignore[no-untyped-def]
        return SimpleNamespace(symbol=symbol, side=side, intent_type=intent,
                               remaining_quantity=Decimal("2"), reference_price=Decimal("50"))
    orders = (order("AAA", OrderSide.BUY, IntentType.BASE_ENTRY),
              order("BBB", OrderSide.BUY, IntentType.PYRAMID_ADD),
              order("CCC", OrderSide.SELL, IntentType.EXIT))
    broker = SimpleNamespace(get_open_orders=lambda: orders)
    assert pending_entry_symbols(broker) == frozenset({"AAA"})
    assert pending_entries(broker) == {"AAA": Decimal("100")}


# --- Runtime -----------------------------------------------------------------------------

class MultiProvider:
    """Every symbol trades the passing AAA tape; `rejected` symbols have no premarket data."""

    def __init__(self, rejected: frozenset[str] = frozenset()) -> None:
        self.base, self.rejected = Provider(), rejected

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        symbol = list(symbols)[0]
        bars = [bar.model_copy(update={"symbol": symbol}) for bar in self.base.minutes]
        if symbol in self.rejected:
            bars = [bar for bar in bars if bar.session is not MarketSession.PREMARKET]
        return [bar for bar in bars if session is None or bar.session is session]

    def get_daily_bars(self, symbols, start=None, end=None):  # type: ignore[no-untyped-def]
        symbol = list(symbols)[0]
        return [bar.model_copy(update={"symbol": symbol}) for bar in self.base.get_daily_bars(symbols, start, end)]


async def run_entry_ticks(runtime, provider) -> list:  # type: ignore[no-untyped-def]
    lifecycle = EntryLifecycleService(runtime)
    owner = EntryManagementRuntime(runtime, lambda: provider, lifecycle=lifecycle)
    first = await owner.run_once(as_of=OPEN + timedelta(minutes=16))
    second = await owner.run_once(as_of=OPEN + timedelta(minutes=18))
    return [*first, *second]


@pytest.mark.asyncio
async def test_rejected_leaders_do_not_block_later_approved_candidates(durable) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"))
        session.commit()
    await run_entry_ticks(runtime, MultiProvider(frozenset({"AAA", "BBB", "EEE"})))
    assert sorted(p.symbol for p in runtime.broker.get_positions()) == ["CCC", "DDD", "FFF"]
    with factory() as session:
        phases = dict(session.execute(select(StrategyStateRecord.symbol, StrategyStateRecord.phase)).all())
    assert {s: phases[s] for s in ("AAA", "BBB", "EEE")} == dict.fromkeys(("AAA", "BBB", "EEE"),
                                                                           StrategyPhase.PREMARKET_REJECTED.value)


@pytest.mark.asyncio
async def test_four_eligible_in_one_tick_create_exactly_three_orders_by_gpt_rank(durable) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"))
        session.commit()
    outcomes = await run_entry_ticks(runtime, MultiProvider())
    assert sorted(p.symbol for p in runtime.broker.get_positions()) == ["AAA", "BBB", "CCC"]
    with factory() as session:
        # First-tick NO_NEXT_BAR rejections are broker orders too; only fills are entries.
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 3
        assert session.scalar(select(func.count()).select_from(ExecutionOrderRecord).where(
            ExecutionOrderRecord.symbol.in_(("DDD", "EEE", "FFF", "GGG", "HHH")),
            ExecutionOrderRecord.submitted_at >= OPEN + timedelta(minutes=18))) == 0
        # Capped candidates keep a re-evaluable, non-terminal state; nothing marks them rejected.
        phases = dict(session.execute(select(StrategyStateRecord.symbol, StrategyStateRecord.phase)).all())
    capped = [o for o in outcomes if o.reason == EntryCapacityReason.DAILY_ENTRY_CAP_REACHED.value]
    assert {o.symbol for o in capped} == {"DDD", "EEE", "FFF", "GGG", "HHH"}
    assert all(o.action is EntryAction.SKIPPED for o in capped)
    assert all(phases.get(s) != StrategyPhase.PREMARKET_REJECTED.value for s in ("DDD", "HHH"))


@pytest.mark.asyncio
async def test_position_management_keeps_running_after_the_daily_cap(durable) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("AAA", "BBB", "CCC", "DDD"))
        session.commit()
    provider = MultiProvider()
    await run_entry_ticks(runtime, provider)
    assert len(runtime.broker.get_positions()) == 3
    outcomes = await PositionManagementRuntime(runtime, lambda: provider).run_once(
        as_of=OPEN + timedelta(minutes=19))
    assert sorted(o.symbol for o in outcomes) == ["AAA", "BBB", "CCC"]
