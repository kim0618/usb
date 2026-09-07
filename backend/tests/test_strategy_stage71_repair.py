from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.broker.sim import SimBroker
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.models.execution import ShadowTradeRecord
from app.repositories.execution import ExecutionRepository
from app.core.database import Base, create_db_engine
from app.market.domain import MarketSession, MinuteBar
from app.models.strategy import StrategyStateRecord
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot, PositionSnapshot
from app.services.strategy import StrategyLifecycleService, StrategyStateService
from app.strategy.config import StrategyConfig, VARIANT_CONFIGS
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import PremarketContext, StrategyReason, StrategyV0Engine
from app.strategy.indicators import atr_sma, closing_strength
from app.strategy.lifecycle import (
    OvernightSuitability, StrategyBook, StrategyPhase, StrategyState, TrailingProfile,
)
from app.strategy.replay import StrategyReplayService
from app.strategy.runner import StrategyLifecycleRunner
from app.shadow.domain import ShadowResult, ShadowStatus, ShadowVariant

NY = ZoneInfo("America/New_York")
DAY = date(2026, 9, 1)
OPEN = datetime(2026, 9, 1, 9, 30, tzinfo=NY)


def make_bar(minute: int, *, day_open: datetime = OPEN, price: float = 100,
             high: float | None = None, low: float | None = None,
             session: MarketSession = MarketSession.REGULAR) -> MinuteBar:
    stamp = day_open + timedelta(minutes=minute)
    return MinuteBar(symbol="ABC", timestamp=stamp, open=price,
                     high=high if high is not None else price + .1,
                     low=low if low is not None else price - .1, close=price,
                     volume=1000, session=session, observed_at=stamp,
                     available_at=stamp + timedelta(minutes=1))


def open_state(**changes):
    base = StrategyState("ABC", DAY, phase=StrategyPhase.POSITION_OPEN,
        entry_trading_date=DAY, entry_price=Decimal("100"), initial_stop=Decimal("99"),
        active_stop=Decimal("99"), highest_price_since_entry=Decimal("100"), holding_day_number=1)
    return replace(base, **changes)


def test_t1_same_bar_uses_previous_stop_before_raising_trailing():
    bars = [make_bar(i, price=100, high=100.2, low=99.8) for i in range(15)]
    current = make_bar(15, price=108, high=110, low=101)
    result = StrategyV0Engine().evaluate_position(state=open_state(add_signal_issued=True), bars=bars + [current],
        market_open=OPEN, as_of=current.available_at, current_price=Decimal("108"),
        average_price=Decimal("100"), variant=VARIANT_CONFIGS["C"])
    assert result.decision.decision is DecisionType.HOLD
    assert result.state.active_stop > Decimal("99")
    assert not result.ambiguous
    stopped = StrategyV0Engine().evaluate_position(
        state=open_state(active_stop=Decimal("102")), bars=bars + [current], market_open=OPEN,
        as_of=current.available_at, current_price=Decimal("108"), average_price=Decimal("100"),
        variant=VARIANT_CONFIGS["C"])
    assert stopped.decision.decision is DecisionType.EXIT
    assert stopped.decision.metadata["active_stop"] == "102"


def test_t2_t3_pre_and_postmarket_do_not_manage_normal_position():
    pre = make_bar(0, price=98, high=99, low=95, session=MarketSession.PREMARKET)
    state = open_state(active_stop=Decimal("100"), highest_price_since_entry=Decimal("100"))
    first = StrategyV0Engine().evaluate_position(state=state, bars=[pre], market_open=OPEN,
        as_of=pre.available_at, current_price=Decimal("98"), average_price=Decimal("100"),
        variant=VARIANT_CONFIGS["C"])
    assert first.decision.decision is DecisionType.HOLD and first.state == state
    post = make_bar(400, price=120, high=130, low=119, session=MarketSession.POSTMARKET)
    second = StrategyV0Engine().evaluate_position(state=state, bars=[post], market_open=OPEN,
        as_of=post.available_at, current_price=Decimal("120"), average_price=Decimal("100"),
        variant=VARIANT_CONFIGS["C"])
    assert second.state.highest_price_since_entry == Decimal("100")
    assert second.state.active_stop == Decimal("100")


def test_t4_actual_fill_price_controls_one_r_and_gpt_profile():
    state = open_state(entry_price=Decimal("102.55"), initial_stop=Decimal("99"),
                       active_stop=Decimal("99"), highest_price_since_entry=Decimal("102.55"),
                       trailing_profile=TrailingProfile.TIGHT)
    bars = [make_bar(i, price=103, high=103.2, low=102.8) for i in range(15)]
    current = make_bar(15, price=105, high=105, low=104.8)
    result = StrategyV0Engine().evaluate_position(state=state, bars=bars + [current],
        market_open=OPEN, as_of=current.available_at, current_price=Decimal("105"),
        average_price=Decimal("102.55"), variant=VARIANT_CONFIGS["D"])
    assert result.state.active_stop == Decimal("99")
    assert result.decision.decision is DecisionType.HOLD


def test_t5_variant_parallel_state_grain(tmp_path):
    db = create_db_engine(f"sqlite:///{tmp_path / 'grain.sqlite'}")
    Base.metadata.create_all(db)
    c = open_state(book=StrategyBook.SHADOW, variant="C", active_stop=Decimal("101"),
                   highest_price_since_entry=Decimal("105"))
    d = replace(c, variant="D", active_stop=Decimal("103"), highest_price_since_entry=Decimal("107"))
    actual = replace(c, book=StrategyBook.ACTUAL, variant="ACTUAL", active_stop=Decimal("99"))
    with Session(db) as session:
        service = StrategyStateService(session)
        for state in (c, d, actual):
            service.save(state, updated_at=OPEN)
    with Session(db) as session:
        service = StrategyStateService(session)
        assert session.scalar(select(func.count()).select_from(StrategyStateRecord)) == 3
        assert service.load("ABC", DAY, book=StrategyBook.SHADOW, variant="C") == c
        assert service.load("ABC", DAY, book=StrategyBook.SHADOW, variant="D") == d


def test_t7_missing_opening_range_deadline_and_configured_start():
    bars = [make_bar(i) for i in range(15) if i != 7]
    result = StrategyV0Engine().evaluate_entry(
        state=StrategyState("ABC", DAY, phase=StrategyPhase.OPENING_RANGE_BUILDING),
        bars=bars, market_open=OPEN, as_of=OPEN + timedelta(minutes=61))
    assert result.state.phase is StrategyPhase.NO_TRADE
    assert result.decision.reason_code == StrategyReason.INSUFFICIENT_OPENING_RANGE
    ten = StrategyV0Engine(StrategyConfig(opening_range_minutes=10))
    bars10 = [make_bar(i, price=100, high=101 if i == 9 else 100.5, low=99) for i in range(10)]
    trigger = make_bar(10, price=102, high=102.1, low=101.8)
    assert ten.evaluate_entry(state=StrategyState("ABC", DAY, phase=StrategyPhase.OPENING_RANGE_BUILDING),
        bars=bars10 + [trigger], market_open=OPEN, as_of=trigger.available_at).decision.decision is DecisionType.ENTER


def test_t8_replay_uses_realistic_available_at():
    bars = [make_bar(i, price=100, high=101 if i == 14 else 100.5, low=99) for i in range(15)]
    trigger = make_bar(15, price=102, high=102.2, low=101.5)
    replay = StrategyReplayService().run_entry_window(
        state=StrategyState("ABC", DAY, phase=StrategyPhase.OPENING_RANGE_BUILDING),
        bars=bars + [trigger], market_open=OPEN)
    assert replay.decisions[-1].market_as_of == trigger.available_at
    assert replay.decisions[-1].decision is DecisionType.ENTER


def test_t9_atr_resets_at_trading_session_boundary():
    day1 = [make_bar(i, price=100) for i in range(15)]
    day2_open = datetime(2026, 9, 2, 9, 30, tzinfo=NY)
    day2 = [make_bar(i, day_open=day2_open, price=90, high=90.1, low=89.9) for i in range(15)]
    atr = atr_sma(day1 + day2, 14, day2[-1].available_at)
    assert atr is not None and atr < Decimal("0.3")


def test_t6_t11_shadow_risk_is_local_and_add_count_changes_only_on_fill():
    broker = SimBroker("100000")
    runner = StrategyLifecycleRunner(broker)
    state = StrategyState("ABC", DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
        book=StrategyBook.SHADOW, variant="C", entry_price=Decimal("102"),
        initial_stop=Decimal("99"), active_stop=Decimal("99"), holding_day_number=1)
    decision = StrategyDecision("ABC", DecisionType.ENTER, "ENTRY", OPEN + timedelta(minutes=15),
                                strategy_version="strategy_v0")
    account = AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, OPEN)
    empty = PortfolioSnapshot((), Decimal("0"), Decimal("0"), OPEN)
    fillbar = make_bar(17, price=102.55)
    entered = runner.execute_entry(state=state, decision=decision, account=account, portfolio=empty,
        market_bars=(fillbar,), instrument_currency=Currency.USD, created_at=decision.market_as_of)
    assert entered.state.phase is StrategyPhase.POSITION_OPEN
    assert entered.state.entry_price == broker.get_position("ABC").average_price
    actual = DailyTradingState(DAY)
    assert not actual.attempted_symbols
    position = broker.get_position("ABC")
    # An add is only ever approved inside the trade's own stop-risk budget, so the
    # raised stop the pyramid trigger implies is part of the position snapshot.
    snapshot = PositionSnapshot("ABC", position.quantity, position.average_price, Decimal("105"),
        Currency.USD, initial_stop=Decimal("99"), active_stop=Decimal("103"),
        base_notional_account_ccy=position.cost_basis)
    portfolio = PortfolioSnapshot((snapshot,), position.cost_basis, Decimal("0"), OPEN)
    add_decision = StrategyDecision("ABC", DecisionType.ADD, "ADD", OPEN + timedelta(minutes=20),
                                    strategy_version="strategy_v0")
    unfilled = runner.execute_add(state=entered.state, decision=add_decision, account=account,
        portfolio=portfolio, planned_initial_risk=broker.get_trade("ABC").planned_initial_risk,
        requested_notional=Decimal("1000"), market_bars=(),
        created_at=add_decision.market_as_of)
    assert unfilled.state.add_count == 0
    addbar = make_bar(22, price=105)
    filled = runner.execute_add(state=unfilled.state, decision=add_decision, account=account,
        portfolio=portfolio, planned_initial_risk=broker.get_trade("ABC").planned_initial_risk,
        requested_notional=Decimal("1000"), market_bars=(addbar,),
        created_at=add_decision.market_as_of)
    assert filled.state.add_count == 1 and filled.state.phase is StrategyPhase.PYRAMID_ADDED


def test_t12_exit_signal_waits_for_fill_and_day2_has_no_day3():
    state = open_state().transition(StrategyPhase.EXIT_SIGNALLED)
    assert state.phase is StrategyPhase.EXIT_SIGNALLED
    unchanged = StrategyLifecycleService.mark_exit_filled(state, market_as_of=OPEN,
                                                           remaining_quantity=Decimal("1"))
    assert unchanged.phase is StrategyPhase.EXIT_SIGNALLED
    assert StrategyLifecycleService.mark_exit_filled(state, market_as_of=OPEN,
                                                      remaining_quantity=Decimal("0")).phase is StrategyPhase.EXITED


def test_t10_overnight_reduce_executes_partial_sell_then_holds():
    broker = SimBroker("100000")
    entry_time = OPEN + timedelta(minutes=20)
    buy = OrderIntent("ABC", OrderSide.BUY, IntentType.BASE_ENTRY, Decimal("400"),
        Decimal("100"), Decimal("40000"), Decimal("40000"), "USD", "USD",
        "strategy_v0", Decimal("500"), Decimal("99"), entry_time, entry_time, "ENTRY")
    broker.submit_order(buy, (make_bar(22, price=100),))
    position = broker.get_position("ABC")
    snapshot = PositionSnapshot("ABC", position.quantity, position.average_price, Decimal("100"),
        Currency.USD, initial_stop=Decimal("99"), base_notional_account_ccy=Decimal("40000"))
    portfolio = PortfolioSnapshot((snapshot,), Decimal("40000"), Decimal("0"), entry_time)
    account = AccountSnapshot(Decimal("100000"), Decimal("60000"), Currency.USD, entry_time)
    state = open_state(entry_price=position.average_price, phase=StrategyPhase.POSITION_OPEN)
    decision = StrategyDecision("ABC", DecisionType.OVERNIGHT_HOLD, "CLOSE", OPEN + timedelta(hours=6),
                                strategy_version="strategy_v0")
    result = StrategyLifecycleRunner(broker).apply_overnight_risk(
        state=state, decision=decision, account=account, portfolio=portfolio, position=snapshot,
        market_bars=(make_bar(362, price=100),), created_at=decision.market_as_of)
    assert result.state.phase is StrategyPhase.OVERNIGHT_HELD
    assert broker.get_position("ABC").quantity < position.quantity + result.order.filled_quantity
    assert result.order.intent_type == IntentType.EXIT.value


def test_t13_ambiguity_reaches_shadow_trade_record(tmp_path):
    broker = SimBroker("100000")
    signal = OPEN + timedelta(minutes=10)
    buy = OrderIntent("ABC", OrderSide.BUY, IntentType.BASE_ENTRY, Decimal("10"), Decimal("100"),
        Decimal("1000"), Decimal("1000"), "USD", "USD", "strategy_v0", Decimal("100"),
        Decimal("99"), signal, signal, "ENTRY")
    broker.submit_order(buy, (make_bar(12, price=100),))
    broker.record_ambiguity("ABC", 2)
    pos = broker.get_position("ABC")
    sell_signal = OPEN + timedelta(minutes=20)
    sell = OrderIntent("ABC", OrderSide.SELL, IntentType.EXIT, pos.quantity, Decimal("101"),
        pos.quantity * Decimal("101"), pos.quantity * Decimal("101"), "USD", "USD",
        "strategy_v0", Decimal("0"), Decimal("99"), sell_signal, sell_signal, "DAY2_MAX_HOLD")
    order = broker.submit_order(sell, (make_bar(22, price=101),))
    trade = broker.get_trade("ABC")
    db = create_db_engine(f"sqlite:///{tmp_path / 'ambiguity.sqlite'}")
    Base.metadata.create_all(db)
    with Session(db) as session:
        ExecutionRepository(session).persist_execution(order, broker.get_fills(order.id), trade,
            ShadowResult("ABC", ShadowVariant.C, status=ShadowStatus.CLOSED, holding_days=2))
        session.commit()  # The repository flushes; the caller owns the transaction.
    with Session(db) as session:
        row = session.scalar(select(ShadowTradeRecord))
        assert row.ambiguous_bar_count == 2


def _run_complete_lifecycle():
    broker = SimBroker("100000")
    runner = StrategyLifecycleRunner(broker)
    state = StrategyState("ABC", DAY, book=StrategyBook.SHADOW, variant="C",
                          overnight_suitability=OvernightSuitability.HIGH)
    state = StrategyLifecycleService.apply_human_gate(state, approved=False, shadow_mode=True)
    gate = StrategyV0Engine().premarket_gate(
        PremarketContext(Decimal("100"), Decimal("105"), Decimal("100000"), Decimal("1000000")),
        human_approved=False, shadow_mode=True)
    state = StrategyLifecycleService.apply_premarket_gate(state, gate)
    opening = [make_bar(i, price=100, high=101 if i == 14 else 100.5, low=99) for i in range(15)]
    trigger = make_bar(15, price=102, high=102.2, low=101.5)
    evaluated = StrategyV0Engine().evaluate_entry(state=state, bars=opening + [trigger],
        market_open=OPEN, as_of=trigger.available_at)
    account = AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, OPEN)
    entered = runner.execute_entry(state=evaluated.state, decision=evaluated.decision,
        account=account, portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), OPEN),
        market_bars=(make_bar(18, price=102.55),), instrument_currency=Currency.USD,
        created_at=evaluated.decision.market_as_of)
    state = entered.state
    state = state.transition(StrategyPhase.OVERNIGHT_REVIEW)
    state = state.transition(StrategyPhase.OVERNIGHT_HELD, overnight=True)
    day2_open = datetime(2026, 9, 2, 9, 30, tzinfo=NY)
    state = runner.activate_day2(state, as_of=day2_open)
    broker_pos = broker.get_position("ABC")
    snapshot = PositionSnapshot("ABC", broker_pos.quantity, broker_pos.average_price, Decimal("104"),
        Currency.USD, initial_stop=Decimal("99"), base_notional_account_ccy=broker_pos.cost_basis,
        overnight=True)
    exit_signal = StrategyDecision("ABC", DecisionType.EXIT, "DAY2_MAX_HOLD",
                                   day2_open + timedelta(hours=6), strategy_version="strategy_v0")
    exited = runner.execute_exit(state=state, decision=exit_signal, position=snapshot,
        market_bars=(make_bar(362, day_open=day2_open, price=104),), created_at=exit_signal.market_as_of)
    trade = broker.get_trade("ABC")
    return exited.state, trade, tuple((o.side, o.status, o.filled_quantity) for o in broker._orders.values())


def test_t14_t15_full_day1_day2_lifecycle_is_fill_based_and_deterministic():
    first = _run_complete_lifecycle()
    second = _run_complete_lifecycle()
    assert first == second
    state, trade, orders = first
    assert state.phase is StrategyPhase.EXITED and state.holding_day_number == 2
    assert trade.status.value == "CLOSED" and trade.exit_reason == "DAY2_MAX_HOLD"
    assert len(orders) == 2


def test_actual_reject_shadow_runs_without_actual_reservation():
    context = PremarketContext(Decimal("100"), Decimal("105"), Decimal("100000"), Decimal("1000000"))
    engine = StrategyV0Engine()
    assert not engine.premarket_gate(context, human_approved=False, shadow_mode=False).passed
    assert engine.premarket_gate(context, human_approved=False, shadow_mode=True).passed
    actual = DailyTradingState(DAY)
    runner = StrategyLifecycleRunner(SimBroker("100000"))
    shadow = StrategyState("ABC", DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
        book=StrategyBook.SHADOW, variant="C", entry_price=Decimal("102"),
        initial_stop=Decimal("99"), active_stop=Decimal("99"), holding_day_number=1)
    decision = StrategyDecision("ABC", DecisionType.ENTER, "ENTRY", OPEN + timedelta(minutes=15),
                                strategy_version="strategy_v0")
    runner.execute_entry(state=shadow, decision=decision,
        account=AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, OPEN),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), OPEN),
        market_bars=(make_bar(17, price=102.5),), instrument_currency=Currency.USD,
        created_at=decision.market_as_of)
    assert actual == DailyTradingState(DAY)


def test_all_five_variant_states_are_independent(tmp_path):
    db = create_db_engine(f"sqlite:///{tmp_path / 'five.sqlite'}")
    Base.metadata.create_all(db)
    with Session(db) as session:
        service = StrategyStateService(session)
        for index, variant in enumerate("ABCDE"):
            service.save(open_state(book=StrategyBook.SHADOW, variant=variant,
                active_stop=Decimal(100 + index)), updated_at=OPEN)
    with Session(db) as session:
        service = StrategyStateService(session)
        assert [service.load("ABC", DAY, book=StrategyBook.SHADOW, variant=v).active_stop
                for v in "ABCDE"] == [Decimal(100 + i) for i in range(5)]


def test_closing_strength_ignores_future_and_non_regular_bars():
    visible = make_bar(10, price=105, high=110, low=100)
    future = make_bar(20, price=50, high=120, low=40)
    post = make_bar(11, price=10, high=200, low=5, session=MarketSession.POSTMARKET)
    value = closing_strength([visible, future, post], OPEN, visible.available_at, Decimal("108"))
    assert value == Decimal("0.8")


def test_complete_transition_table_normal_flow():
    state = StrategyState("ABC", DAY)
    for phase in (StrategyPhase.HUMAN_APPROVED, StrategyPhase.PREMARKET_PASSED,
                  StrategyPhase.OPENING_RANGE_BUILDING, StrategyPhase.WAITING_ENTRY,
                  StrategyPhase.ENTRY_SIGNALLED, StrategyPhase.POSITION_OPEN,
                  StrategyPhase.PYRAMID_ADDED, StrategyPhase.OVERNIGHT_REVIEW,
                  StrategyPhase.OVERNIGHT_HELD, StrategyPhase.DAY2_ACTIVE,
                  StrategyPhase.EXIT_SIGNALLED, StrategyPhase.EXITED):
        state = state.transition(phase)
    assert state.phase is StrategyPhase.EXITED
