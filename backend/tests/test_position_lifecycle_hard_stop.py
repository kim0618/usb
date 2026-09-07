"""An open position meets its stop and is closed, durably, by a production caller.

The entry here is the deterministic P1-F one (reference 102, stop 99, fill
102.153), so the numbers a stop produces can be read against a known starting
point. Every figure asserted about the sell comes from the broker or the durable
row; this module never re-derives a fill price or a PnL, because a test that
recomputes the formula only proves the formula equals itself.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.core import database
from app.core.config import get_settings
from app.execution.config import ExecutionConfig
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.main import create_app
from app.market import factory as market_factory
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.simulation import SimulationPositionRecord, SimulationTradeRecord
from app.repositories.simulation import SimulationStateRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.services.position_lifecycle import (
    ACTUAL_VARIANT, PositionAction, PositionLifecycleService, strategy_state_sink,
)
from app.services.position_management_runtime import (
    PositionManagementRuntime, get_position_management_runtime,
)
from app.services.simulation_runtime import (
    activate_operator_simulation_runtime, clear_active_sim_broker, get_active_sim_broker,
)
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.lifecycle import StrategyBook, StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner
from backend.tests.test_simulation_persistence_schema import REVISION, _migrated_engine

ET = ZoneInfo("America/New_York")
DAY = date(2026, 7, 2)
MARKET_OPEN = datetime(2026, 7, 2, 9, 30, tzinfo=ET)
SIGNAL_AT = datetime(2026, 7, 2, 9, 46, tzinfo=ET)
ENTRY_FILL_AT = datetime(2026, 7, 2, 9, 47, tzinfo=ET)
STOP_BAR_AT = datetime(2026, 7, 2, 10, tzinfo=ET)
# A completed bar is visible one minute later, so the stop is evaluated at 10:01
# and the broker's next-bar rule settles on the first bar after that.
STOP_AS_OF = datetime(2026, 7, 2, 10, 1, tzinfo=ET)
EXIT_FILL_AT = datetime(2026, 7, 2, 10, 2, tzinfo=ET)

SYMBOL = "TSLA"
CASH = Decimal("100000")
ENTRY_REFERENCE = Decimal("102")
INITIAL_STOP = Decimal("99")
QUANTITY = Decimal("166.6666666666666666666666667")
ENTRY_FILL = Decimal("102.153")
ENTRY_CASH = Decimal("82957.50")
EXIT_OPEN = 98.80


# Environment ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def ownership():
    assert get_active_sim_broker() is None
    yield
    clear_active_sim_broker()


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = _migrated_engine(tmp_path / "lifecycle.sqlite3", monkeypatch, REVISION)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setenv("RUNTIME_PROFILE", "real_market_operator")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kiwoom")
    get_settings.cache_clear()
    with sessions() as session:
        SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CASH, cash=CASH, created_at=MARKET_OPEN)
        session.commit()
    try:
        yield sessions
    finally:
        engine.dispose()
        get_settings.cache_clear()


def bar(at: datetime, *, open_: float, high: float, low: float, close: float,
        session: MarketSession = MarketSession.REGULAR) -> MinuteBar:
    return MinuteBar(symbol=SYMBOL, timestamp=at, open=open_, high=high, low=low, close=close,
                     volume=20_000, session=session, observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


def flat(at: datetime, price: float) -> MinuteBar:
    return bar(at, open_=price, high=price + 0.1, low=price - 0.1, close=price)


def session_bars(*extra: MinuteBar) -> tuple[MinuteBar, ...]:
    """Regular-session tape from the open through the entry fill, plus extras."""
    base = [flat(MARKET_OPEN + timedelta(minutes=i), 100.0 + i * 0.05) for i in range(17)]
    base.append(bar(ENTRY_FILL_AT, open_=102.0, high=102.1, low=101.9, close=102.0))
    return tuple(base) + extra


def activate():
    runtime = activate_operator_simulation_runtime(get_settings(), config=ExecutionConfig())
    assert runtime is not None and runtime.durable
    return runtime


def enter(runtime, factory) -> StrategyState:
    """Reproduce the P1-F entry and commit POSITION_OPEN with the fill."""
    signalled = StrategyState(SYMBOL, DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
                              book=StrategyBook.ACTUAL, entry_price=ENTRY_REFERENCE,
                              initial_stop=INITIAL_STOP, active_stop=INITIAL_STOP,
                              entry_trading_date=DAY, holding_day_number=1)
    decision = StrategyDecision(SYMBOL, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK", SIGNAL_AT,
                                strategy_version="strategy_v0")
    runner = StrategyLifecycleRunner.for_active_runtime()
    result = runner.execute_entry(
        state=signalled, decision=decision,
        account=AccountSnapshot(CASH, CASH, Currency.USD, SIGNAL_AT),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), SIGNAL_AT),
        market_bars=session_bars(), instrument_currency=Currency.USD, created_at=SIGNAL_AT,
        actual_risk_state=DailyTradingState(DAY), on_state=strategy_state_sink(SIGNAL_AT))
    assert result.order is not None and result.order.status is OrderStatus.FILLED
    assert runtime.broker.cash == ENTRY_CASH
    return result.state


def counts(session: Session) -> dict[str, int]:
    return {name: session.scalar(select(func.count()).select_from(model)) for name, model in (
        ("orders", ExecutionOrderRecord), ("fills", ExecutionFillRecord),
        ("positions", SimulationPositionRecord), ("trades", SimulationTradeRecord))}


def stored_state(factory, phase_of: str = SYMBOL):
    with factory() as session:
        return StrategyStateRepository(session).load(phase_of, DAY)


def count_submissions(broker) -> list:
    seen: list = []
    original = broker.submit_order

    def counted(intent, market_bars):
        seen.append(intent)
        return original(intent, market_bars)

    broker.submit_order = counted
    return seen


def durable_shape(factory, account_id: int = 1) -> dict:
    """The financial truth a closed lifecycle leaves behind, ids excluded."""
    with factory() as session:
        repository = SimulationStateRepository(session)
        trade = session.scalars(select(SimulationTradeRecord)).one()
        return {
            "cash": repository.get_account_by_id(account_id).cash,
            "state_version": repository.get_account_by_id(account_id).state_version,
            "counts": counts(session),
            "status": trade.status, "exit_reason": trade.exit_reason,
            "exit_time": trade.exit_time, "average_exit_price": trade.average_exit_price,
            "gross_pnl": trade.gross_pnl, "net_pnl": trade.net_pnl,
            "gross_r": trade.gross_r, "net_r": trade.net_r, "total_cost": trade.total_cost,
            "sold_quantity": trade.sold_quantity, "exit_notional": trade.exit_notional,
            "phase": stored_state(factory).phase,
        }


# Entry persistence ---------------------------------------------------------

def test_entry_fill_persists_position_open_with_its_stop(factory) -> None:
    """The driver can only enforce a stop it can read back."""
    runtime = activate()
    enter(runtime, factory)
    state = stored_state(factory)
    assert state is not None
    assert state.phase is StrategyPhase.POSITION_OPEN
    assert state.entry_price == ENTRY_FILL and state.initial_stop == INITIAL_STOP
    assert state.active_stop == INITIAL_STOP
    assert state.highest_price_since_entry == ENTRY_FILL
    assert state.add_count == 0 and state.holding_day_number == 1
    assert state.last_market_as_of == ENTRY_FILL_AT


def test_entry_state_and_fill_commit_together(factory, monkeypatch) -> None:
    """A failing state write takes the whole entry with it, not just itself."""
    runtime = activate()

    def boom(*args, **kwargs):
        raise RuntimeError("state write failed")

    monkeypatch.setattr(StrategyStateRepository, "save", boom)
    with pytest.raises(RuntimeError, match="state write failed"):
        enter(runtime, factory)
    assert runtime.broker.cash == CASH and runtime.broker.get_positions() == ()
    with factory() as session:
        assert counts(session) == {"orders": 0, "fills": 0, "positions": 0, "trades": 0}


def test_restart_reloads_the_open_position_and_its_stop(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    clear_active_sim_broker()

    restarted = activate()
    assert restarted.broker is not runtime.broker
    assert restarted.broker.cash == ENTRY_CASH
    assert restarted.broker.get_position(SYMBOL).quantity == QUANTITY
    state = stored_state(factory)
    assert state.phase is StrategyPhase.POSITION_OPEN and state.active_stop == INITIAL_STOP
    with factory() as session:
        assert len(StrategyStateRepository(session).list_open(SYMBOL)) == 1


# Hard stop -----------------------------------------------------------------

def test_a_bar_above_the_stop_holds_and_submits_nothing(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    submissions = count_submissions(runtime.broker)
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.5, low=99.01, close=100.0))
    outcome = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]
    assert outcome.action is PositionAction.HOLD
    assert submissions == []
    assert runtime.broker.get_position(SYMBOL) is not None
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN
    with factory() as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}


def test_a_bar_touching_the_stop_closes_the_position_durably(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    submissions = count_submissions(runtime.broker)
    driver = PositionLifecycleService(runtime)
    # Low exactly at the stop: the engine treats the touch as a breach.
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                        bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
    outcome = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]

    assert outcome.action is PositionAction.EXIT_FILLED and outcome.reason == "INITIAL_STOP"
    assert len(submissions) == 1
    order = outcome.order
    assert order is not None and order.status is OrderStatus.FILLED
    assert order.side.value == "SELL" and order.filled_quantity == QUANTITY
    assert order.filled_at == EXIT_FILL_AT and order.rejection_reason is None
    assert outcome.state.phase is StrategyPhase.EXITED

    broker = runtime.broker
    assert broker.get_position(SYMBOL) is None
    closed = broker.get_trade(SYMBOL)
    assert closed.status is TradeStatus.CLOSED and closed.exit_reason == "INITIAL_STOP"
    assert broker.cash > ENTRY_CASH

    with factory() as session:
        repository = SimulationStateRepository(session)
        account = repository.get_account_by_id(1)
        assert account.cash == broker.cash and account.state_version == 2
        assert counts(session) == {"orders": 2, "fills": 2, "positions": 0, "trades": 1}
        assert repository.get_position(1, SYMBOL) is None
        assert repository.get_open_trade(1, SYMBOL) is None
        row = session.scalars(select(SimulationTradeRecord)).one()
        assert row.status == "CLOSED" and row.exit_reason == "INITIAL_STOP"
        assert row.exit_time == EXIT_FILL_AT and row.sold_quantity == QUANTITY
        assert row.average_exit_price == closed.average_exit_price
        assert (row.gross_pnl, row.net_pnl) == (closed.gross_pnl, closed.net_pnl)
        assert (row.gross_r, row.net_r) == (closed.gross_r, closed.net_r)
        assert row.total_cost == closed.total_cost and row.exit_notional == closed._exit_notional
        assert row.net_pnl < 0 and row.net_r < 0
        sell = session.scalars(select(ExecutionOrderRecord).where(
            ExecutionOrderRecord.side == "SELL")).one()
        assert sell.status == "FILLED" and sell.filled_quantity == QUANTITY
    assert stored_state(factory).phase is StrategyPhase.EXITED


def test_a_second_evaluation_after_the_exit_sells_nothing_more(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                        bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
    assert driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0].exited
    submissions = count_submissions(runtime.broker)
    later = STOP_AS_OF + timedelta(minutes=5)

    assert driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF) == ()
    assert driver.evaluate({SYMBOL: bars}, as_of=later) == ()
    assert submissions == []
    with factory() as session:
        assert counts(session) == {"orders": 2, "fills": 2, "positions": 0, "trades": 1}


def test_the_same_bar_is_not_evaluated_twice(factory) -> None:
    """Idempotency while still open: a repeated bar cannot re-signal."""
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.5, low=99.5, close=100.0))
    assert driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0].action is PositionAction.HOLD
    repeat = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]
    assert repeat.action is PositionAction.SKIPPED
    assert repeat.reason == "already evaluated through this bar"


def test_the_same_completed_bar_is_skipped_at_a_later_call_time(factory) -> None:
    """Idempotency follows bar availability, not exact scheduler timing."""
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.5, low=99.5, close=100.0))
    assert driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0].action is PositionAction.HOLD

    repeat = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF + timedelta(seconds=30))[0]
    assert repeat.action is PositionAction.SKIPPED
    assert repeat.reason == "already evaluated through this bar"


# Restart across the stop ----------------------------------------------------

def close_on_the_stop(factory, *, restart: bool) -> dict:
    runtime = activate()
    enter(runtime, factory)
    if restart:
        clear_active_sim_broker()
        runtime = activate()
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                        bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
    outcome = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]
    assert outcome.exited
    return durable_shape(factory)


def test_a_stop_after_a_restart_closes_identically(factory, tmp_path, monkeypatch) -> None:
    """The stop survives the process that recorded it."""
    restarted = close_on_the_stop(factory, restart=True)
    clear_active_sim_broker()

    # A second, untouched database runs the same lifecycle without a restart.
    engine = _migrated_engine(tmp_path / "control.sqlite3", monkeypatch, REVISION)
    control_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", control_factory)
    monkeypatch.setenv("RUNTIME_PROFILE", "real_market_operator")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kiwoom")
    get_settings.cache_clear()
    with control_factory() as session:
        SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CASH, cash=CASH, created_at=MARKET_OPEN)
        session.commit()
    try:
        assert close_on_the_stop(control_factory, restart=False) == restarted
    finally:
        engine.dispose()


def test_after_the_close_a_restart_finds_no_open_position(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                        bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
    assert driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0].exited
    final_cash = runtime.broker.cash
    clear_active_sim_broker()

    restarted = activate()
    assert restarted.broker.cash == final_cash
    assert restarted.broker.get_positions() == ()
    # The closed trade stays history: loading it back would hand the next entry a
    # settled trade in the live per-symbol slot.
    assert restarted.broker.get_trade(SYMBOL) is None
    assert restarted.broker.get_open_orders() == () and restarted.broker.get_fills() == ()
    with factory() as session:
        assert counts(session) == {"orders": 2, "fills": 2, "positions": 0, "trades": 1}
        assert StrategyStateRepository(session).list_open(SYMBOL) == ()
    assert stored_state(factory).phase is StrategyPhase.EXITED
    assert PositionLifecycleService(restarted).evaluate({}, as_of=STOP_AS_OF) == ()


# Failure and retry ----------------------------------------------------------

def test_no_next_bar_keeps_the_position_open_and_signalled(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    # The stop bar is the last one anyone has; there is nothing to sell into.
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2))
    outcome = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]

    assert outcome.action is PositionAction.EXIT_UNFILLED
    assert outcome.order.status is OrderStatus.REJECTED
    assert outcome.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert runtime.broker.cash == ENTRY_CASH
    with factory() as session:
        assert counts(session) == {"orders": 2, "fills": 1, "positions": 1, "trades": 1}
        repository = SimulationStateRepository(session)
        assert repository.get_account_by_id(1).state_version == 1
        assert repository.get_open_trade(1, SYMBOL).status is TradeStatus.OPEN
    # The exit is still owed, so the phase records that rather than reverting.
    assert stored_state(factory).phase is StrategyPhase.EXIT_SIGNALLED


def test_a_signalled_exit_is_retried_on_the_next_bar(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    signalled = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2))
    assert driver.evaluate({SYMBOL: signalled}, as_of=STOP_AS_OF)[0].action is PositionAction.EXIT_UNFILLED

    # The 10:02 bar completes and becomes visible at 10:03; the retry keeps the
    # 10:01 signal time, so that bar is the first one eligible to settle it.
    with_fill = signalled + (bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9),)
    retry = driver.evaluate({SYMBOL: with_fill}, as_of=STOP_AS_OF + timedelta(minutes=2))[0]
    assert retry.action is PositionAction.EXIT_FILLED and retry.reason == "INITIAL_STOP"
    assert retry.order.filled_at == EXIT_FILL_AT
    assert runtime.broker.get_position(SYMBOL) is None
    with factory() as session:
        assert counts(session) == {"orders": 3, "fills": 2, "positions": 0, "trades": 1}
        assert session.scalars(select(SimulationTradeRecord)).one().exit_reason == "INITIAL_STOP"
    assert stored_state(factory).phase is StrategyPhase.EXITED


def test_a_failed_state_write_rolls_the_whole_exit_back(factory, monkeypatch) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                        bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
    original = StrategyStateRepository.save

    def fail_on_exit(self, state, *, updated_at):
        if state.phase is StrategyPhase.EXITED:
            raise RuntimeError("state write failed")
        return original(self, state, updated_at=updated_at)

    monkeypatch.setattr(StrategyStateRepository, "save", fail_on_exit)
    with pytest.raises(RuntimeError, match="state write failed"):
        driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)

    # Broker memory and every durable layer stay on the pre-exit side together.
    assert runtime.broker.cash == ENTRY_CASH
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert runtime.broker.get_trade(SYMBOL).status is TradeStatus.OPEN
    with factory() as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}
        assert SimulationStateRepository(session).get_account_by_id(1).state_version == 1
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN


# Contracts this stage settled ----------------------------------------------

def test_an_exit_does_not_fill_outside_the_regular_session(factory) -> None:
    """Exits now use the same session policy entries always have."""
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    after_close = datetime(2026, 7, 2, 16, 5, tzinfo=ET)
    bars = session_bars(
        bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
        bar(after_close, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9,
            session=MarketSession.POSTMARKET))
    outcome = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]
    assert outcome.action is PositionAction.EXIT_UNFILLED
    assert outcome.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert runtime.broker.get_position(SYMBOL) is not None


@pytest.mark.asyncio
async def test_the_api_agrees_with_the_broker_before_and_after_the_exit(factory) -> None:
    """Both position counters move together now that phases are persisted."""
    import httpx

    from app.core.database import get_db
    from app.main import create_app

    runtime = activate()
    enter(runtime, factory)
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/v1/trading")).json()
        assert len(body["open_positions"]) == 1
        assert (await client.get("/api/v1/dashboard")).json()["trading"]["open_positions_count"] == 1

        driver = PositionLifecycleService(runtime)
        bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                            bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
        assert driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0].exited

        body = (await client.get("/api/v1/trading")).json()
        assert body["availability"] == "AVAILABLE" and body["open_positions"] == []
        assert Decimal(body["account"]["cash"]) == runtime.broker.cash
        assert (await client.get("/api/v1/dashboard")).json()["trading"]["open_positions_count"] == 0
        orders = (await client.get("/api/v1/trading/orders")).json()
        assert [row["side"] for row in orders] == ["SELL", "BUY"]
        assert {row["status"] for row in orders} == {"FILLED"}
        assert len((await client.get("/api/v1/trading/fills")).json()) == 2
        # The closed trade has no actual-book endpoint yet; /trading/trades reads
        # the shadow table, so it stays empty here.
        assert (await client.get("/api/v1/trading/trades")).json() == []


def test_the_actual_book_variant_is_the_control_variant(factory) -> None:
    """Recorded so a later trailing stage changes it deliberately, not by default."""
    assert ACTUAL_VARIANT.variant == "C" and ACTUAL_VARIANT.is_control
    assert ACTUAL_VARIANT.allow_overnight and ACTUAL_VARIANT.max_holding_days == 2
    runtime = activate()
    assert PositionLifecycleService(runtime).variant is ACTUAL_VARIANT


def test_a_position_without_strategy_state_is_refused_not_reconstructed(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    with factory() as session:
        from app.models.strategy import StrategyStateRecord

        session.query(StrategyStateRecord).delete()
        session.commit()
    driver = PositionLifecycleService(runtime)
    submissions = count_submissions(runtime.broker)
    bars = session_bars(bar(STOP_BAR_AT, open_=100.0, high=100.2, low=99.0, close=99.2),
                        bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9))
    outcome = driver.evaluate({SYMBOL: bars}, as_of=STOP_AS_OF)[0]
    assert outcome.action is PositionAction.SKIPPED
    assert outcome.reason == "no open strategy state"
    assert submissions == [] and runtime.broker.get_position(SYMBOL) is not None


def test_durable_state_persistence_requires_a_durable_runtime(factory) -> None:
    from app.broker.sim import SimBroker

    activate()
    runner = StrategyLifecycleRunner(SimBroker(CASH))
    with pytest.raises(RuntimeError, match="durable runtime"):
        runner._submit(object(), (), created_at=SIGNAL_AT, on_persist=lambda session, order: None)


@pytest.mark.asyncio
async def test_background_owner_automatically_retries_and_closes(factory) -> None:
    """No direct lifecycle call: cadence ticks carry the durable position to exit."""
    runtime = activate()
    enter(runtime, factory)
    submissions = count_submissions(runtime.broker)

    class SequencedProvider:
        bars = session_bars(
            bar(STOP_BAR_AT, open_=100.0, high=100.2, low=98.9, close=99.2),
        )

        def get_minute_bars(self, symbols, start=None, end=None, session=None):
            return [item for item in self.bars if end is None or item.timestamp <= end]

    provider = SequencedProvider()
    owner = PositionManagementRuntime(runtime, lambda: provider, clock=lambda: STOP_AS_OF)

    first = await owner.run_once(as_of=STOP_AS_OF)
    assert first[0].action is PositionAction.EXIT_UNFILLED
    assert first[0].order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert stored_state(factory).phase is StrategyPhase.EXIT_SIGNALLED
    assert runtime.broker.get_position(SYMBOL) is not None

    provider.bars += (bar(EXIT_FILL_AT, open_=EXIT_OPEN, high=99.0, low=98.5, close=98.9),)
    second = await owner.run_once(as_of=EXIT_FILL_AT + timedelta(minutes=1))
    assert second[0].action is PositionAction.EXIT_FILLED
    # The retry kept the 10:01 signal time, so the first bar after it settles the
    # exit; a re-dated signal would have skipped past this bar entirely.
    assert second[0].order.filled_at == EXIT_FILL_AT
    assert runtime.broker.get_position(SYMBOL) is None
    assert stored_state(factory).phase is StrategyPhase.EXITED
    assert len(submissions) == 2  # one rejected SELL, one filled SELL
    with factory() as session:
        assert counts(session) == {"orders": 3, "fills": 2, "positions": 0, "trades": 1}

    # Repeated ticks discover no position and cannot duplicate the sell.
    assert await owner.run_once(as_of=EXIT_FILL_AT + timedelta(minutes=2)) == ()
    assert len(submissions) == 2


@pytest.fixture
def open_session(monkeypatch):
    """Open the cadence gate for the owner alone; every other caller keeps XNYS.

    The startup cadence only ticks inside a regular session, so without this a
    lifespan test asserts nothing on a holiday and reaches the network on a
    trading afternoon. Fixing the clock is what makes the assertion honest.
    """
    import app.services.end_of_day_runtime as eod_module
    import app.services.position_management_runtime as owner_module
    now = datetime.now(timezone.utc)

    class AlwaysOpen(MarketCalendar):
        def session(self, day):
            return TradingSessionWindow(day, now - timedelta(hours=1),
                                        now + timedelta(hours=1), False)

    monkeypatch.setattr(owner_module, "MarketCalendar", AlwaysOpen)
    # The closing-review owner starts beside the minute driver, so it is pinned to
    # the same window. Its review moment then sits an hour ahead of this clock,
    # which keeps these assertions about the minute driver alone - and keeps them
    # from depending on whether the suite happens to run near a real XNYS close.
    monkeypatch.setattr(eod_module, "MarketCalendar", AlwaysOpen)


@pytest.mark.asyncio
async def test_startup_cadence_acquires_only_through_the_configured_factory(
    factory, monkeypatch, open_session,
) -> None:
    """Startup cadence reaches market data only through the composition seam.

    A name imported into the app module would bypass the seam the rest of the
    suite patches, which is how an unattended tick reaches the real Kiwoom host
    with the operator's own credentials during a live session.
    """
    runtime = activate()
    enter(runtime, factory)
    clear_active_sim_broker()
    with factory() as session:
        before = counts(session)

    built: list[object] = []
    requested: list[str] = []

    class NoBars:
        def get_minute_bars(self, symbols, start=None, end=None, session=None):
            requested.append(symbols[0])
            return []

    monkeypatch.setattr(market_factory, "build_kiwoom_provider",
                        lambda *a, **k: built.append(NoBars()) or built[-1])
    monkeypatch.setattr(KiwoomMarketDataClient, "request",
                        lambda *a, **k: pytest.fail("cadence reached the real Kiwoom client"))
    app = create_app()
    async with app.router.lifespan_context(app):
        broker = get_active_sim_broker()
        assert broker is not None and broker.get_position(SYMBOL) is not None
        for _ in range(20):
            await asyncio.sleep(0)
        assert built and requested == [SYMBOL]
    # A tick that acquired nothing evaluates nothing and moves nothing.
    assert len(built) == 1
    with factory() as session:
        assert counts(session) == before
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN


@pytest.mark.asyncio
async def test_shutdown_releases_the_broker_even_when_the_owner_dies(
    factory, monkeypatch, open_session,
) -> None:
    """A cadence task that died is reported, but it never keeps broker ownership."""
    runtime = activate()
    enter(runtime, factory)
    clear_active_sim_broker()

    class OwnerDied(BaseException):
        """Escapes the owner's own Exception handling, as a hard failure would."""

    monkeypatch.setattr(market_factory, "build_kiwoom_provider",
                        lambda *a, **k: (_ for _ in ()).throw(OwnerDied()))
    app = create_app()
    with pytest.raises(OwnerDied):
        async with app.router.lifespan_context(app):
            for _ in range(20):
                await asyncio.sleep(0)
    assert get_active_sim_broker() is None
    assert get_position_management_runtime() is None
