from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.domain import MarketSession, MinuteBar
from app.monitoring.config import OperationsConfig
from app.monitoring.domain import (FailureCode, FailureSeverity, MismatchType,
    ReconciliationResult, RuntimeMode)
from app.monitoring.guards import ExecutionFailureTracker, MarketDataStalenessGuard, runtime_invariant_errors
from app.monitoring.kill_switch import activate_kill_switch
from app.monitoring.notification import InMemoryNotificationSink
from app.monitoring.reconciliation import ReconciliationService
from app.monitoring.service import RuntimeHealthService, operational_safe_mode
from app.monitoring.testing import FailureInjectingSimBroker, InjectedFailure
from app.repositories.runtime import RuntimeRepository
from app.risk.domain import (AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot,
    PositionSnapshot, RiskRejectionReason)
from app.risk.engine import RiskEngine
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.lifecycle import StrategyPhase, StrategyState

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'runtime.sqlite3'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def service(engine, sink=None):
    session = Session(engine, expire_on_commit=False)
    return session, RuntimeHealthService(session, notification_sink=sink)


def test_t1_t3_bootstrap_and_restart_mode_persistence(db) -> None:
    session, runtime = service(db)
    assert runtime.bootstrap(NOW).mode is RuntimeMode.NORMAL
    runtime.enter_safe_mode(NOW + timedelta(seconds=1))
    session.close()
    session2, restarted = service(db)
    assert restarted.bootstrap(NOW + timedelta(seconds=2)).mode is RuntimeMode.SAFE_MODE
    restarted.halt(NOW + timedelta(seconds=3))
    session2.close()
    session3, restarted_again = service(db)
    assert restarted_again.bootstrap(NOW + timedelta(seconds=4)).mode is RuntimeMode.HALTED
    session3.close()


def test_heartbeat_recording_and_staleness(db) -> None:
    session, runtime = service(db)
    runtime.record_heartbeat(NOW)
    assert not runtime.heartbeat_is_stale(NOW + timedelta(seconds=180))
    assert runtime.heartbeat_is_stale(NOW + timedelta(seconds=181))
    session.close()


def _risk_inputs(decision_type):
    decision = StrategyDecision("AAA", decision_type, "TEST", NOW)
    account = AccountSnapshot(Decimal("10000"), Decimal("10000"), Currency.USD, NOW)
    position = PositionSnapshot("AAA", Decimal("10"), Decimal("100"), Decimal("110"), Currency.USD,
                                initial_stop=Decimal("95"))
    portfolio = PortfolioSnapshot((position,), Decimal("1000"), Decimal("0"), NOW)
    return decision, account, position, portfolio


def test_t4_t6_safe_mode_blocks_enter_add_and_allows_exit() -> None:
    engine = RiskEngine()
    decision, account, position, portfolio = _risk_inputs(DecisionType.ENTER)
    denied = engine.evaluate_base_entry(decision=decision,
        eligibility=TradingEligibility(True, operational_safe_mode(RuntimeMode.SAFE_MODE)),
        account=account, portfolio=portfolio, daily_state=DailyTradingState(date(2026, 9, 1)),
        entry_price="110", stop_price="100", instrument_currency=Currency.USD, created_at=NOW)
    assert denied.rejection_reason is RiskRejectionReason.SAFE_MODE
    add = StrategyDecision("AAA", DecisionType.ADD, "ADD", NOW)
    denied_add = engine.evaluate_pyramid_add(decision=add, eligibility=TradingEligibility(True, True),
        account=account, portfolio=portfolio, daily_state=DailyTradingState(date(2026, 9, 1)),
        planned_initial_risk="500", requested_notional_account_ccy="100", created_at=NOW)
    assert denied_add.rejection_reason is RiskRejectionReason.SAFE_MODE
    exit_decision = StrategyDecision("AAA", DecisionType.EXIT, "STOP", NOW)
    assert engine.build_exit_intent(decision=exit_decision, position=position,
                                    created_at=NOW).side is OrderSide.SELL


def test_t7_t8_session_aware_staleness(db) -> None:
    guard = MarketDataStalenessGuard(OperationsConfig(market_data_stale_seconds=120))
    ny = ZoneInfo("America/New_York")
    current = datetime(2026, 9, 1, 9, 49, tzinfo=ny)
    stale = guard.check(market_as_of=current,
        latest_data_available_at=datetime(2026, 9, 1, 9, 45, tzinfo=ny), session=MarketSession.REGULAR)
    assert not stale.healthy and stale.failure_code is FailureCode.MARKET_DATA_STALE
    closed = guard.check(market_as_of=datetime(2026, 9, 1, 18, 0, tzinfo=ny),
        latest_data_available_at=datetime(2026, 9, 1, 16, 0, tzinfo=ny), session=MarketSession.REGULAR)
    assert closed.healthy and not closed.expected_activity
    session, runtime = service(db)
    runtime.check_market_data(market_as_of=current,
        latest_data_available_at=datetime(2026, 9, 1, 9, 45, tzinfo=ny), session=MarketSession.REGULAR)
    assert runtime.health(current).mode is RuntimeMode.SAFE_MODE
    session.close()


def test_t9_t10_execution_failure_sliding_window(db) -> None:
    config = OperationsConfig(execution_failure_threshold=3, execution_failure_window_seconds=300)
    tracker = ExecutionFailureTracker(config)
    assert tracker.record(FailureCode.EXECUTION_TIMEOUT, NOW) == 1
    assert tracker.record(FailureCode.EXECUTION_TIMEOUT, NOW + timedelta(seconds=301)) == 1
    session = Session(db, expire_on_commit=False)
    runtime = RuntimeHealthService(session, config=config)
    for seconds in (0, 10, 20):
        runtime.record_execution_failure(code=FailureCode.EXECUTION_TIMEOUT,
            occurred_at=NOW + timedelta(seconds=seconds), message="timeout")
    assert runtime.health(NOW + timedelta(seconds=20)).mode is RuntimeMode.SAFE_MODE
    session.close()


def test_t11_t14_reconciliation_and_startup_halted(db) -> None:
    broker = SimBroker("10000")
    broker._positions["AAA"] = __import__("app.broker.domain", fromlist=["SimPosition"]).SimPosition(
        "AAA", Decimal("1"), Decimal("100"), Decimal("100"), Decimal("0"), NOW, NOW)
    active = StrategyState("AAA", date(2026, 9, 1), phase=StrategyPhase.POSITION_OPEN)
    reconciler = ReconciliationService()
    assert reconciler.compare(strategy_states=[active], broker_positions=broker.get_positions(),
                              open_orders=[], checked_at=NOW).matched
    terminal = StrategyState("AAA", date(2026, 9, 1), phase=StrategyPhase.EXITED)
    mismatch = reconciler.compare(strategy_states=[terminal], broker_positions=broker.get_positions(),
                                  open_orders=[], checked_at=NOW)
    assert mismatch.mismatches[0].mismatch_type is MismatchType.TERMINAL_STATE_WITH_OPEN_POSITION
    session, runtime = service(db)
    runtime.apply_reconciliation(mismatch, startup=True)
    assert runtime.health(NOW).mode is RuntimeMode.SAFE_MODE
    runtime.halt(NOW + timedelta(seconds=1))
    runtime.apply_reconciliation(ReconciliationResult(True, (), NOW + timedelta(seconds=2)), startup=True)
    assert runtime.health(NOW + timedelta(seconds=2)).mode is RuntimeMode.HALTED
    session.close()


def _bar(symbol, minute, price=100.0):
    ts = NOW + timedelta(minutes=minute)
    return MinuteBar(symbol=symbol, timestamp=ts, open=price, high=price + 1, low=price - 1,
        close=price, volume=1000, session=MarketSession.REGULAR,
        observed_at=ts + timedelta(minutes=1), available_at=ts + timedelta(minutes=1))


def _buy(symbol="AAA"):
    q = Decimal("1"); p = Decimal("99")
    return OrderIntent(symbol, OrderSide.BUY, IntentType.BASE_ENTRY, q, p, q*p, q*p,
        "USD", "USD", "strategy_v0", Decimal("10"), Decimal("90"), NOW, NOW, "ENTRY")


def test_t15_t17_kill_switch_no_position_full_and_partial(db) -> None:
    session, runtime = service(db)
    empty = activate_kill_switch(runtime=runtime, broker=SimBroker("10000"), market_bars={},
        activated_at=NOW, market_as_of=NOW)
    assert empty.runtime_mode is RuntimeMode.HALTED and not empty.liquidation_orders
    session.close()

    session2, runtime2 = service(db)
    # Re-use a fresh DB mode row by explicit release is intentionally unnecessary: activation is idempotent HALTED.
    broker = SimBroker("10000")
    broker.submit_order(_buy(), [_bar("AAA", 1)])
    result = activate_kill_switch(runtime=runtime2, broker=broker,
        market_bars={"AAA": [_bar("AAA", 1), _bar("AAA", 2, 101)]},
        activated_at=NOW + timedelta(minutes=1), market_as_of=NOW + timedelta(minutes=1))
    assert not result.failed_symbols and not broker.get_positions()
    assert runtime2.health(NOW).mode is RuntimeMode.HALTED
    session2.close()


def test_t17_partial_liquidation_failure_remains_halted(db) -> None:
    class RejectSellBroker(SimBroker):
        def submit_order(self, intent, market_bars):  # type: ignore[no-untyped-def]
            if intent.side is OrderSide.SELL:
                return self._reject(self._new_order(intent),
                                    __import__("app.broker.domain", fromlist=["RejectionReason"]).RejectionReason.NO_NEXT_BAR)
            return super().submit_order(intent, market_bars)

    broker = RejectSellBroker("10000")
    broker.submit_order(_buy(), [_bar("AAA", 1)])
    session, runtime = service(db)
    result = activate_kill_switch(runtime=runtime, broker=broker,
        market_bars={"AAA": [_bar("AAA", 1), _bar("AAA", 2)]},
        activated_at=NOW + timedelta(minutes=1), market_as_of=NOW + timedelta(minutes=1))
    assert result.failed_symbols == ("AAA",)
    assert broker.get_positions() and runtime.health(NOW).mode is RuntimeMode.HALTED
    session.close()


def test_t18_t24_resolution_recovery_notification_status_and_atomicity(db) -> None:
    sink = InMemoryNotificationSink(); session, runtime = service(db, sink)
    failure = runtime.enter_safe_mode(NOW)
    passed = ReconciliationResult(True, (), NOW)
    with pytest.raises(ValueError, match="unresolved critical"):
        runtime.recover_to_normal(acknowledged=True, reconciliation=passed, recovered_at=NOW)
    runtime.resolve_failure(failure.id, NOW + timedelta(seconds=1))  # type: ignore[arg-type]
    assert runtime.recover_to_normal(acknowledged=True, reconciliation=passed,
                                     recovered_at=NOW + timedelta(seconds=2)).mode is RuntimeMode.NORMAL
    status = runtime.status(NOW, open_positions_count=2, open_orders_count=1)
    assert status.open_positions_count == 2 and sink.events
    assert RuntimeRepository(session).last_failure() is not None
    session.close()


def test_t20_invariant_violation_enters_safe_mode(db) -> None:
    state = StrategyState("AAA", date(2026, 9, 1), add_count=2)
    errors = runtime_invariant_errors(cash=Decimal("-1"), positions=(), strategy_states=(state,))
    session, runtime = service(db)
    runtime.report_invariant_violations(errors, NOW)
    assert runtime.health(NOW).mode is RuntimeMode.SAFE_MODE
    session.close()


def test_t25_shadow_is_explicitly_independent() -> None:
    assert TradingEligibility(False, safe_mode=False, book="SHADOW").safe_mode is False
    assert operational_safe_mode(RuntimeMode.SAFE_MODE) is True


def test_t26_deterministic_failure_injection() -> None:
    for _ in range(2):
        broker = FailureInjectingSimBroker("10000", failures=[InjectedFailure.TIMEOUT])
        with pytest.raises(TimeoutError, match="deterministic injected submit timeout"):
            broker.submit_order(_buy(), [_bar("AAA", 1)])


def test_operations_config_validation() -> None:
    with pytest.raises(ValueError):
        OperationsConfig(heartbeat_interval_seconds=60, heartbeat_stale_seconds=60)
