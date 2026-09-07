"""A restarted process must rebuild the broker it lost, not recompute it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.broker.domain import SimPosition, TradeResult, TradeStatus
from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.execution.config import ExecutionConfig
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)
from app.repositories.simulation import SimulationStateRepository
from app.services.execution import ExecutionService
from app.services.simulation import SimulationStateInconsistent, rehydrate_sim_broker

START = datetime(2026, 9, 6, 13, 45, tzinfo=timezone.utc)
CASH = Decimal("100000")
# A SimBroker sizing that does not terminate in base ten, and a real fill price.
QUANTITY = Decimal("166.6666666666666666666666667")
PRICE = Decimal("102.153")


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = create_db_engine(f"sqlite:///{tmp_path / 'rehydration.sqlite3'}")
    Base.metadata.create_all(db)
    try:
        yield db
    finally:
        db.dispose()


def opened(session: Session, key: str = "operator", cash: Decimal | None = None) -> int:
    return SimulationStateRepository(session).create_account(
        broker_type="SIM", account_key=key, base_currency="USD", initial_cash=CASH,
        cash=CASH if cash is None else cash, created_at=START).id


def position(symbol: str = "TSLA", quantity: Decimal = QUANTITY,
             realized: str = "0", price: Decimal = PRICE) -> SimPosition:
    return SimPosition(symbol=symbol, quantity=quantity, average_price=price,
                       cost_basis=price * quantity, realized_pnl=Decimal(realized),
                       opened_at=START, updated_at=START + timedelta(minutes=1))


def trade(symbol: str = "TSLA", status: TradeStatus = TradeStatus.OPEN,
          risk: str = "1000", quantity: Decimal = QUANTITY) -> TradeResult:
    return TradeResult(
        trade_id=f"TRADE-{symbol}-1", symbol=symbol, entry_time=START, exit_time=None,
        initial_quantity=quantity, total_quantity=quantity, average_entry_price=PRICE,
        average_exit_price=None, gross_pnl=Decimal("0"), net_pnl=Decimal("-42.50"),
        planned_initial_risk=Decimal(risk), gross_r=Decimal("0"), net_r=Decimal("-0.0425"),
        total_cost=Decimal("42.50"), ambiguous_bar_count=3, exit_reason=None, status=status,
        _entry_notional=PRICE * quantity, _exit_notional=Decimal("0"), _sold_quantity=Decimal("0"))


def seeded(session: Session, account_id: int, *, positions=(), trades=()) -> None:
    repository = SimulationStateRepository(session)
    for item in positions:
        repository.save_position(account_id, item)
    for item in trades:
        repository.save_trade(account_id, item, updated_at=START)
    session.commit()


# Account -------------------------------------------------------------------

def test_missing_account_fails_closed_without_creating_one(engine: Engine) -> None:
    """A restart never opens an account; that stays an explicit operator action."""
    with Session(engine) as session:
        with pytest.raises(LookupError):
            rehydrate_sim_broker(session, 999)
        assert session.scalar(select(func.count()).select_from(SimulationAccountRecord)) == 0


def test_empty_account_rehydrates_into_a_usable_broker(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = rehydrate_sim_broker(session, account_id)
        assert broker.cash == CASH and broker.starting_cash == CASH
        assert broker.get_positions() == () and broker._trades == {}
        assert broker.get_fills() == () and broker.get_open_orders() == ()


def test_current_cash_is_restored_not_starting_cash(engine: Engine) -> None:
    """82957.50 was spent down from 100000; a restart must not refund the difference."""
    with Session(engine) as session:
        account_id = opened(session, cash=Decimal("82957.50"))
        seeded(session, account_id, positions=[position()], trades=[trade()])
        broker = rehydrate_sim_broker(session, account_id)
        assert broker.cash == Decimal("82957.50")
        assert broker.starting_cash == CASH
        assert broker.cash != broker.starting_cash
        assert broker.currency.value == "USD"


# Positions and trades ------------------------------------------------------

def test_single_position_and_open_trade_restore_exactly(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session, cash=Decimal("82957.50"))
        held, opened_trade = position(realized="-42.50"), trade()
        seeded(session, account_id, positions=[held], trades=[opened_trade])
        broker = rehydrate_sim_broker(session, account_id)
        assert broker.get_position("TSLA") == held
        assert broker.get_trade("TSLA") == opened_trade
        assert broker.get_positions() == (held,)


def test_multiple_symbols_restore_without_cross_contamination(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        held = [position("TSLA"), position("AAPL", Decimal("10"), price=Decimal("50.5")),
                position("MSFT", Decimal("3.25"), realized="17.75")]
        opened_trades = [trade("TSLA"), trade("AAPL", quantity=Decimal("10")),
                         trade("MSFT", risk="250", quantity=Decimal("3.25"))]
        seeded(session, account_id, positions=held, trades=opened_trades)
        broker = rehydrate_sim_broker(session, account_id)
        assert broker.get_positions() == tuple(sorted(held, key=lambda p: p.symbol))
        for item in opened_trades:
            assert broker.get_trade(item.symbol) == item
        assert set(broker._positions) == {"TSLA", "AAPL", "MSFT"}


def test_closed_trades_are_not_loaded_into_the_live_trade_slot(engine: Engine) -> None:
    """_trades holds one trade per symbol, so a closed one would shadow the next entry."""
    with Session(engine) as session:
        account_id = opened(session)
        closed = trade("TSLA", TradeStatus.CLOSED)
        closed.exit_time, closed.status = START + timedelta(hours=1), TradeStatus.CLOSED
        seeded(session, account_id, trades=[closed])
        broker = rehydrate_sim_broker(session, account_id)
        assert broker.get_trade("TSLA") is None and broker._trades == {}
        # The row itself is untouched history.
        assert SimulationStateRepository(session).get_trade(account_id, closed.trade_id) is not None


def test_execution_history_is_not_replayed_into_broker_memory(engine: Engine) -> None:
    """execution_orders/execution_fills stay the durable authority for history."""
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        service = ExecutionService(SimBroker(CASH), session, account_id=account_id)
        service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        assert counts(session)["orders"] == 1 and counts(session)["fills"] == 1
        broker = rehydrate_sim_broker(session, account_id)
        assert broker._orders == {} and broker._fills == []
        assert broker.get_open_orders() == () and broker.get_fills() == ()
        # The position and trade the same execution wrote are restored.
        assert broker.get_position("AAA") is not None and broker.get_trade("AAA") is not None


# Exactness -----------------------------------------------------------------

@pytest.mark.parametrize("value", [
    Decimal("166.6666666666666666666666667"), Decimal("82957.50"),
    Decimal("500.0000000000000000000000001"), Decimal("-42.50")])
def test_decimal_values_survive_the_round_trip_exactly(engine: Engine, value: Decimal) -> None:
    with Session(engine) as session:
        account_id = opened(session, cash=value)
        held = position(quantity=abs(value) + Decimal("1"), realized=str(value))
        seeded(session, account_id, positions=[held], trades=[trade(quantity=abs(value) + Decimal("1"))])
        broker = rehydrate_sim_broker(session, account_id)
        restored = broker.get_position("TSLA")
        assert broker.cash == value and str(broker.cash) == str(value)
        assert restored.realized_pnl == value and str(restored.realized_pnl) == str(value)
        assert restored.quantity == held.quantity and restored.cost_basis == held.cost_basis


def test_timestamps_return_as_aware_utc(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position()], trades=[trade()])
        broker = rehydrate_sim_broker(session, account_id)
        held, opened_trade = broker.get_position("TSLA"), broker.get_trade("TSLA")
        for moment in (held.opened_at, held.updated_at, opened_trade.entry_time):
            assert moment.tzinfo is not None and moment.utcoffset() == timedelta(0)
        assert held.opened_at == START and held.updated_at == START + timedelta(minutes=1)
        assert opened_trade.entry_time == START and opened_trade.exit_time is None


# Consistency ---------------------------------------------------------------

def test_position_without_an_open_trade_is_rejected(engine: Engine) -> None:
    """_apply_sell indexes _trades[symbol] directly, so this would raise KeyError mid-fill."""
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position()])
        with pytest.raises(SimulationStateInconsistent, match="TSLA"):
            rehydrate_sim_broker(session, account_id)


def test_open_trade_without_a_position_is_rejected(engine: Engine) -> None:
    """A first buy overwrites the slot, silently discarding the open trade's accumulators."""
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, trades=[trade()])
        with pytest.raises(SimulationStateInconsistent, match="TSLA"):
            rehydrate_sim_broker(session, account_id)


def test_partial_symbol_overlap_names_only_the_stranded_symbols(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position("TSLA"), position("AAPL")],
               trades=[trade("TSLA"), trade("MSFT")])
        with pytest.raises(SimulationStateInconsistent, match="AAPL, MSFT"):
            rehydrate_sim_broker(session, account_id)


def test_non_positive_position_quantity_is_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position(quantity=Decimal("0"))], trades=[trade()])
        with pytest.raises(SimulationStateInconsistent, match="quantity"):
            rehydrate_sim_broker(session, account_id)


def test_open_trade_without_planned_risk_is_rejected(engine: Engine) -> None:
    """Every R figure a sell writes divides by this; it is never estimated back."""
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position()], trades=[trade(risk="0")])
        with pytest.raises(SimulationStateInconsistent, match="planned initial risk"):
            rehydrate_sim_broker(session, account_id)


# Identity contracts --------------------------------------------------------

def test_every_rehydration_mints_a_new_execution_scope(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        first, second = rehydrate_sim_broker(session, account_id), rehydrate_sim_broker(session, account_id)
        assert first.execution_scope and second.execution_scope
        assert first.execution_scope != second.execution_scope


def test_sequence_starts_at_zero_and_cannot_reuse_a_durable_id(engine: Engine) -> None:
    """A fresh scope is what makes restarting the counter safe."""
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        service = ExecutionService(SimBroker(CASH), session, account_id=account_id)
        service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        durable = set(session.scalars(select(ExecutionOrderRecord.id))) | set(
            session.scalars(select(ExecutionFillRecord.id)))
        broker = rehydrate_sim_broker(session, account_id)
        assert broker._sequence == 0
        order = broker.submit_order(intent(OrderSide.SELL, quantity="1", reason="STOP"), [bar(2, 105.0)])
        assert order.id not in durable
        assert {fill.fill_id for fill in broker.get_fills()}.isdisjoint(durable)


# Read-only -----------------------------------------------------------------

def test_rehydration_writes_nothing(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position()], trades=[trade()])
    with Session(engine) as session:
        before = snapshot(session)
        rehydrate_sim_broker(session, account_id)
        rehydrate_sim_broker(session, account_id)
    with Session(engine) as session:
        assert snapshot(session) == before
        assert SimulationStateRepository(session).get_account_by_id(account_id).state_version == 0


@pytest.mark.parametrize("field, value", [
    ("cash", Decimal("1")), ("state_version", 7), ("updated_at", START + timedelta(days=1))])
def test_the_no_write_detector_actually_notices_a_write(engine: Engine, field, value) -> None:
    """Negative control: an unchanged snapshot is only evidence if a change would show."""
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position()], trades=[trade()])
    with Session(engine) as session:
        before = snapshot(session)
        setattr(SimulationStateRepository(session).get_account_by_id(account_id), field, value)
        session.commit()
    with Session(engine) as session:
        assert snapshot(session) != before


def test_the_no_write_detector_notices_a_trade_field_write(engine: Engine) -> None:
    """A stray write to a PnL figure must not hide behind stable row counts."""
    with Session(engine) as session:
        account_id = opened(session)
        seeded(session, account_id, positions=[position()], trades=[trade()])
    with Session(engine) as session:
        before = snapshot(session)
        session.scalars(select(SimulationTradeRecord)).one().net_pnl = Decimal("-99.99")
        session.commit()
    with Session(engine) as session:
        assert snapshot(session) != before


# Sell equivalence ----------------------------------------------------------

def bar(minutes: int, opening: float = 100.0) -> MinuteBar:
    moment = START + timedelta(minutes=minutes)
    return MinuteBar(symbol="AAA", timestamp=moment, open=opening, high=opening + 1,
                     low=opening - 1, close=opening, volume=1000, session=MarketSession.REGULAR,
                     observed_at=moment + timedelta(minutes=1),
                     available_at=moment + timedelta(minutes=1))


def intent(side: OrderSide = OrderSide.BUY, quantity: str = "10", reason: str = "TEST") -> OrderIntent:
    amount, price = Decimal(quantity), Decimal("99")
    return OrderIntent("AAA", side,
                       IntentType.BASE_ENTRY if side is OrderSide.BUY else IntentType.EXIT,
                       amount, price, amount * price, CASH, "USD", "USD", "fixed_test_v0",
                       Decimal("100"), Decimal("90"), START, START, reason)


def counts(session: Session) -> dict[str, int]:
    return {name: session.scalar(select(func.count()).select_from(model)) for name, model in (
        ("orders", ExecutionOrderRecord), ("fills", ExecutionFillRecord),
        ("positions", SimulationPositionRecord), ("trades", SimulationTradeRecord))}


ACCOUNT_STATE = ("id", "account_key", "broker_type", "base_currency", "initial_cash", "cash",
                 "created_at", "updated_at", "state_version")
TRADE_ROW_STATE = ("account_id", "trade_uid", "symbol", "status", "entry_time", "exit_time",
                   "initial_quantity", "total_quantity", "average_entry_price", "average_exit_price",
                   "gross_pnl", "net_pnl", "planned_initial_risk", "gross_r", "net_r", "total_cost",
                   "entry_notional", "exit_notional", "sold_quantity", "ambiguous_bar_count",
                   "exit_reason", "created_at", "updated_at")


def snapshot(session: Session) -> tuple:
    """Every persisted simulation value, so a stray write cannot hide behind a stable count."""
    repository = SimulationStateRepository(session)
    accounts = tuple(session.scalars(
        select(SimulationAccountRecord).order_by(SimulationAccountRecord.id)))
    trades = tuple(session.scalars(
        select(SimulationTradeRecord).order_by(SimulationTradeRecord.trade_uid)))
    return (tuple(tuple(getattr(row, name) for name in ACCOUNT_STATE) for row in accounts),
            tuple(repository.list_positions(row.id) for row in accounts),
            tuple(tuple(getattr(row, name) for name in TRADE_ROW_STATE) for row in trades),
            tuple(counts(session).items()))


@pytest.mark.parametrize("config, closes", [(None, True), (ExecutionConfig(partial_fill_enabled=True), False)])
def test_sell_after_a_restart_settles_identically(engine: Engine, config: ExecutionConfig | None,
                                                 closes: bool) -> None:
    """The core restart contract: the same buy then sell, with a process boundary in between."""
    entry, exit_bars = [bar(1)], [bar(2, 105.0)]
    with Session(engine) as session:
        straight_id, restarted_id = opened(session, "straight"), opened(session, "restarted")
        session.commit()

        # Two distinct pre-restart processes share one execution history table, so each
        # mints its own scope; that is the very collision rehydration must not reintroduce.
        straight = SimBroker(CASH, config=config, execution_scope="straight")
        service = ExecutionService(straight, session, account_id=straight_id)
        service.execute_and_persist(intent(), entry, updated_at=START)
        sold = straight.get_position("AAA").quantity
        service.execute_and_persist(intent(OrderSide.SELL, str(sold), "STOP"), exit_bars, updated_at=START)

        restarted = SimBroker(CASH, config=config, execution_scope="restarted")
        ExecutionService(restarted, session, account_id=restarted_id).execute_and_persist(
            intent(), entry, updated_at=START)

    # The process ends here: new session, new broker, state read back from the database.
    with Session(engine) as session:
        rehydrated = rehydrate_sim_broker(session, restarted_id, config=config)
        assert rehydrated.cash == restarted.cash
        assert rehydrated.get_position("AAA") == restarted.get_position("AAA")
        assert rehydrated.get_trade("AAA") == restarted.get_trade("AAA")
        ExecutionService(rehydrated, session, account_id=restarted_id).execute_and_persist(
            intent(OrderSide.SELL, str(sold), "STOP"), exit_bars, updated_at=START)

    baseline, after_restart = straight.get_trade("AAA"), rehydrated.get_trade("AAA")
    # A partial sell leaves the trade open on both sides; equivalence is the claim, not closure.
    assert (baseline.status is TradeStatus.CLOSED) is closes
    assert after_restart.status is baseline.status
    assert rehydrated.cash == straight.cash
    assert rehydrated.get_position("AAA") == straight.get_position("AAA")
    assert (straight.get_position("AAA") is None) is closes
    for name in ("gross_pnl", "net_pnl", "gross_r", "net_r", "average_exit_price", "total_cost",
                 "average_entry_price", "planned_initial_risk", "exit_time", "exit_reason",
                 "initial_quantity", "total_quantity", "_entry_notional", "_exit_notional",
                 "_sold_quantity"):
        assert getattr(after_restart, name) == getattr(baseline, name), name
    # Nothing at all differs: the trade identity is derived from the fill, not the scope.
    assert after_restart == baseline


def test_restarted_sell_matches_the_durable_row_it_wrote(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        ExecutionService(SimBroker(CASH), session, account_id=account_id).execute_and_persist(
            intent(), [bar(1)], updated_at=START)
    with Session(engine) as session:
        broker = rehydrate_sim_broker(session, account_id)
        held = broker.get_position("AAA").quantity
        ExecutionService(broker, session, account_id=account_id).execute_and_persist(
            intent(OrderSide.SELL, str(held), "STOP"), [bar(2, 105.0)], updated_at=START)
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        assert repository.get_position(account_id, "AAA") is None
        assert repository.list_open_trades(account_id) == ()
        stored = repository.get_trade(account_id, broker.get_trade("AAA").trade_id)
        assert stored == broker.get_trade("AAA")
        assert repository.get_account_by_id(account_id).cash == broker.cash
        assert counts(session)["orders"] == 2 and counts(session)["fills"] == 2
