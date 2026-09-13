"""risk_v1 daily accounting: a fixed session 1R, a 3R budget, single-counted base exposure."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.market.calendar import MarketCalendar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.repositories.risk import DailyRiskRepository
from app.repositories.simulation import SimulationStateRepository
from app.risk.config import RiskConfig
from app.risk.domain import Currency, DailyTradingState, PortfolioSnapshot, PositionSnapshot, RiskRejectionReason
from app.risk.engine import RiskEngine
from app.services.daily_performance import SessionEquitySource, session_opening_equity
from app.services.entry_capacity import EntryCapacityReason, load_entry_capacity
from app.services.entry_management_runtime import EntryLifecycleService, EntryManagementRuntime
from app.services.simulation import rehydrate_sim_broker
from app.services.simulation_runtime import SimulationRuntimeContext
import tests.test_trading_risk_stage5 as risk
from tests.test_entry_management_runtime import CAPITAL, DAY, OPEN, durable, seed_session  # noqa: F401
from tests.test_multi_approval_entry_caps import ANALYSIS_DAY, MultiProvider

SESSION = Decimal("7428.92")
R = SESSION * Decimal("0.005")          # 37.14460
ENGINE = RiskEngine()


def acct(equity: Decimal | str):  # type: ignore[no-untyped-def]
    return risk.account(str(equity))


def daily(reserved: Decimal = Decimal("0"), attempted=(), by_symbol=None,  # type: ignore[no-untyped-def]
          session: Decimal | None = SESSION) -> DailyTradingState:
    by_symbol = by_symbol or {}
    return DailyTradingState(risk.DAY, attempted_symbols=frozenset(attempted), planned_risk_reserved=reserved,
                             base_notional_reserved=sum(by_symbol.values(), Decimal("0")),
                             base_notional_by_symbol=by_symbol, session_equity=session)


def held(symbol: str, notional: Decimal) -> PositionSnapshot:
    return PositionSnapshot(symbol, Decimal("1"), notional, notional, Currency.USD, base_notional_account_ccy=notional)


def book(*positions: PositionSnapshot, pending=None) -> PortfolioSnapshot:  # type: ignore[no-untyped-def]
    return PortfolioSnapshot(positions, sum((p.base_notional_account_ccy for p in positions), Decimal("0")),
                             Decimal("0"), risk.NOW, pending or {})


# --- Session R ---------------------------------------------------------------------------

def test_session_r_is_fixed_while_costs_move_equity() -> None:
    assert ENGINE.session_risk_unit(acct("7400"), daily()) == R == Decimal("37.14460")
    assert ENGINE.daily_risk_limit(acct("7400"), daily()) == Decimal("111.43380")
    assert ENGINE.session_risk_unit(acct("7400"), daily(session=None)) == Decimal("37.000")  # legacy fallback
    # First fill's costs cut equity to 7420; the second 1R entry still fits the 3R budget.
    second = risk.evaluate(account=acct("7420"), daily_state=daily(R, {"ZZZ"}))
    assert second.approved and second.metrics.one_r == Decimal("37.100")


def test_three_full_1r_entries_fit_and_a_rounding_residue_does_not_block_the_third() -> None:
    assert risk.evaluate(account=acct(SESSION), daily_state=daily(2 * R, {"A", "B"})).approved
    residue = risk.evaluate(account=acct(SESSION), daily_state=daily(2 * R + Decimal("1e-20"), {"A", "B"}))
    assert residue.approved and residue.metrics.planned_risk <= R


def test_equity_above_session_start_still_sizes_one_session_r() -> None:
    result = risk.evaluate(account=acct("8000"), daily_state=daily(2 * R, {"A", "B"}))
    assert result.approved and result.metrics.one_r == R


def test_fourth_entry_is_rejected_once_three_r_is_reserved() -> None:
    wide = RiskEngine(RiskConfig(max_new_symbols_per_day=10))
    result = risk.evaluate(engine=wide, account=acct(SESSION), daily_state=daily(3 * R, {"A", "B", "C"}))
    assert result.rejection_reason is RiskRejectionReason.DAILY_RISK_LIMIT


def test_partial_r_entries_leave_budget_but_the_symbol_cap_still_stops_a_fourth() -> None:
    third = risk.evaluate(account=acct(SESSION), daily_state=daily(Decimal("0.7") * R + Decimal("0.8") * R, {"A", "B"}))
    assert third.approved and third.metrics.planned_risk <= R
    fourth = risk.evaluate(account=acct(SESSION), daily_state=daily(Decimal("2.5") * R, {"A", "B", "C"}))
    assert fourth.rejection_reason is RiskRejectionReason.DAILY_SYMBOL_LIMIT


# --- Base capacity -------------------------------------------------------------------------

EQUITY = Decimal("100000")
P23 = EQUITY * Decimal("0.23")


def test_an_entry_filled_today_counts_once() -> None:
    assert ENGINE.base_exposure_used(book(held("A", P23)), daily(by_symbol={"A": P23})) == P23


def test_open_plus_pending_and_the_pending_to_open_transition_keep_one_count() -> None:
    before = ENGINE.base_exposure_used(book(held("A", P23), held("B", P23), pending={"C": P23}),
                                       daily(by_symbol={"A": P23, "B": P23}))
    after = ENGINE.base_exposure_used(book(held("A", P23), held("B", P23), held("C", P23)),
                                      daily(by_symbol={"A": P23, "B": P23, "C": P23}))
    assert before == after == 3 * P23


def test_a_same_day_exit_keeps_its_reservation() -> None:
    assert ENGINE.base_exposure_used(book(held("A", P23)), daily(by_symbol={"A": P23, "Z": P23})) == 2 * P23


def test_unattributed_reservations_stay_conservative() -> None:
    legacy = DailyTradingState(risk.DAY, base_notional_reserved=P23)
    assert ENGINE.base_exposure_used(book(held("A", P23)), legacy) == 2 * P23


def test_third_entry_inside_the_80_percent_base_cap_is_not_capped() -> None:
    two = book(held("A", P23), held("B", P23))
    result = risk.evaluate(portfolio=two, daily_state=daily(by_symbol={"A": P23, "B": P23}, session=EQUITY))
    assert result.approved
    assert result.metrics.final_notional_account_ccy == result.metrics.requested_notional_account_ccy


def test_over_the_base_cap_is_still_base_capacity_exhausted() -> None:
    full = book(held("A", EQUITY * Decimal("0.40")), held("B", EQUITY * Decimal("0.40")))
    result = risk.evaluate(portfolio=full, daily_state=daily(session=EQUITY))
    assert result.rejection_reason is RiskRejectionReason.BASE_CAPACITY_EXHAUSTED


# --- Session equity authority --------------------------------------------------------------

def add_close(session, account_id, day: date, closing: str) -> None:  # type: ignore[no-untyped-def]
    SimulationStateRepository(session).add_daily_performance(
        account_id=account_id, trading_date=day, opening_equity=Decimal(closing), closing_equity=Decimal(closing),
        cash=Decimal(closing), position_market_value=Decimal("0"), daily_pnl=Decimal("0"),
        daily_return=Decimal("0"), recorded_at=datetime.combine(day, datetime.min.time(), timezone.utc))


def test_session_equity_sources_are_durable(durable) -> None:
    runtime, factory = durable
    calendar = MarketCalendar()
    with factory() as session:
        first = session_opening_equity(session, runtime.account_id, DAY, calendar)
        assert (first.equity, first.source) == (CAPITAL, SessionEquitySource.INITIAL_CASH)
        add_close(session, runtime.account_id, date(2024, 6, 13), "7300")
        session.commit()
        stale = session_opening_equity(session, runtime.account_id, DAY, calendar)
        assert (stale.equity, stale.source) == (Decimal("7300"), SessionEquitySource.LATEST_EARLIER_CLOSE)
        add_close(session, runtime.account_id, date(2024, 6, 17), "7500")
        session.commit()
        exact = session_opening_equity(session, runtime.account_id, DAY, calendar)
        assert (exact.equity, exact.source) == (Decimal("7500"), SessionEquitySource.PREVIOUS_SESSION_CLOSE)


# --- End to end ------------------------------------------------------------------------------

def snapshot(runtime, factory) -> dict:  # type: ignore[no-untyped-def]
    broker = runtime.broker
    with factory() as session:
        state = DailyRiskRepository(session).load(DAY)
        equity = session_opening_equity(session, runtime.account_id, DAY, MarketCalendar()).equity
        capacity = load_entry_capacity(session, broker, DAY)
    positions = broker.get_positions()
    portfolio = PortfolioSnapshot(tuple(PositionSnapshot(p.symbol, p.quantity, p.average_price, p.average_price,
                                                         Currency.USD, base_notional_account_ccy=p.cost_basis)
                                        for p in positions),
                                  sum((p.cost_basis for p in positions), Decimal("0")), Decimal("0"), risk.NOW)
    return {"symbols": sorted(p.symbol for p in positions), "session_r": equity * Decimal("0.005"),
            "risk_used": state.planned_risk_reserved, "new_entries": capacity.new_entries_used,
            "open": capacity.open_positions_used, "pending": capacity.pending_entry_symbols,
            "base_used": ENGINE.base_exposure_used(portfolio, state), "held_cost": portfolio.base_exposure_used}


@pytest.mark.asyncio
async def test_six_approvals_enter_exactly_three_with_default_risk_and_survive_restart(durable) -> None:
    runtime, factory = durable
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"))
        session.commit()
    owner = EntryManagementRuntime(runtime, lambda: MultiProvider(frozenset({"AAA", "DDD"})),
                                   lifecycle=EntryLifecycleService(runtime))
    await owner.run_once(as_of=OPEN + timedelta(minutes=16))
    outcomes = await owner.run_once(as_of=OPEN + timedelta(minutes=18))

    assert {o.symbol: o.reason for o in outcomes}["FFF"] == EntryCapacityReason.DAILY_ENTRY_CAP_REACHED.value
    before = snapshot(runtime, factory)
    assert before["symbols"] == ["BBB", "CCC", "EEE"]
    assert before["risk_used"] <= 3 * before["session_r"] and before["session_r"] == CAPITAL * Decimal("0.005")
    assert before["base_used"] == before["held_cost"]  # each entry counted once
    assert (before["new_entries"], before["open"]) == (3, 3)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 3
        assert session.scalar(select(func.count()).select_from(ExecutionOrderRecord).where(
            ExecutionOrderRecord.status == "FILLED")) == 3

    with factory() as session:
        restarted = SimulationRuntimeContext(rehydrate_sim_broker(session, runtime.account_id),
                                             runtime.account_id, factory)
    after = snapshot(restarted, factory)
    assert after == before
