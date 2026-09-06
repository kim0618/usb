"""One fill must land as one commit across execution history and broker state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)
from app.repositories.execution import ExecutionRepository
from app.repositories.simulation import SimulationStateConflict, SimulationStateRepository
from app.services.execution import ExecutionService

START = datetime(2026, 9, 1, 13, 45, tzinfo=timezone.utc)
CASH = Decimal("100000")


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


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = create_db_engine(f"sqlite:///{tmp_path / 'atomic.sqlite3'}")
    Base.metadata.create_all(db)
    try:
        yield db
    finally:
        db.dispose()


def opened(session: Session) -> int:
    """Accounts are created explicitly; nothing in the service opens one."""
    return SimulationStateRepository(session).create_account(
        broker_type="SIM", account_key="operator", base_currency="USD",
        initial_cash=CASH, cash=CASH, created_at=START).id


def counts(session: Session) -> dict[str, int]:
    return {name: session.scalar(select(func.count()).select_from(model)) for name, model in (
        ("orders", ExecutionOrderRecord), ("fills", ExecutionFillRecord),
        ("positions", SimulationPositionRecord), ("trades", SimulationTradeRecord))}


def broker_state(broker: SimBroker) -> tuple:
    return (broker.cash, broker.get_positions(), tuple(broker.get_fills()),
            tuple(sorted(broker._orders)), broker.get_trade("AAA"))


# Caller-owned transaction --------------------------------------------------

def test_execution_repository_leaves_the_transaction_to_the_caller(engine: Engine) -> None:
    with Session(engine) as session:
        broker = SimBroker(CASH)
        order = broker.submit_order(intent(), [bar(1)])
        ExecutionRepository(session).persist_execution(order, broker.get_fills(order.id))
        session.rollback()
    with Session(engine) as session:
        assert counts(session)["orders"] == 0 and counts(session)["fills"] == 0


# Filled path ---------------------------------------------------------------

def test_filled_order_commits_history_and_broker_state_together(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        service = ExecutionService(broker, session, account_id=account_id)
        order = service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        assert order.status is OrderStatus.FILLED
    with Session(engine) as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}
        repository = SimulationStateRepository(session)
        stored = repository.get_account("SIM", "operator")
        # The broker computed this cash; persistence copied it rather than recomputing.
        assert stored.cash == broker.cash and stored.cash < CASH and stored.state_version == 1
        position = repository.get_position(account_id, "AAA")
        assert position == broker.get_position("AAA")
        trade = repository.get_open_trade(account_id, "AAA")
        assert trade == broker.get_trade("AAA") and trade.status is TradeStatus.OPEN
        assert trade.planned_initial_risk == Decimal("100")


def test_sell_close_deletes_the_position_and_keeps_the_closed_trade(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        service = ExecutionService(broker, session, account_id=account_id)
        service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        order = service.execute_and_persist(intent(OrderSide.SELL, reason="STOP"),
                                            [bar(2, 105.0)], updated_at=START)
        assert order.status is OrderStatus.FILLED
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        assert repository.get_position(account_id, "AAA") is None
        assert repository.list_open_trades(account_id) == ()
        closed = repository.get_trade(account_id, broker.get_trade("AAA").trade_id)
        assert closed.status is TradeStatus.CLOSED and closed == broker.get_trade("AAA")
        assert counts(session)["orders"] == 2 and counts(session)["fills"] == 2


# Rejected path -------------------------------------------------------------

def test_rejected_order_commits_history_only(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        before = broker_state(broker)
        service = ExecutionService(broker, session, account_id=account_id)
        order = service.execute_and_persist(intent(), [bar(0, 99.0)], updated_at=START)
        assert order.status is OrderStatus.REJECTED
        assert order.rejection_reason is RejectionReason.NO_NEXT_BAR
        assert broker.cash == CASH and broker.get_positions() == () and broker_state(broker)[:3] == before[:3]
    with Session(engine) as session:
        assert counts(session) == {"orders": 1, "fills": 0, "positions": 0, "trades": 0}
        stored = SimulationStateRepository(session).get_account("SIM", "operator")
        assert stored.cash == CASH and stored.state_version == 0


# Failure injection ---------------------------------------------------------

@pytest.mark.parametrize("target", ["update_account_cash", "save_position", "save_trade"])
def test_simulation_state_failure_rolls_back_both_layers(engine: Engine, monkeypatch, target) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        before, sequence = broker_state(broker), broker._sequence
        service = ExecutionService(broker, session, account_id=account_id)

        def explode(*args, **kwargs):
            raise RuntimeError(f"injected {target} failure")

        monkeypatch.setattr(SimulationStateRepository, target, explode)
        with pytest.raises(RuntimeError, match="injected"):
            service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        assert broker_state(broker) == before
        # The sequence never rewinds, so a retry cannot reuse an emitted ID.
        assert broker._sequence > sequence
    with Session(engine) as session:
        assert counts(session) == {"orders": 0, "fills": 0, "positions": 0, "trades": 0}
        assert SimulationStateRepository(session).get_account("SIM", "operator").state_version == 0


def test_commit_failure_rolls_back_both_layers(engine: Engine, monkeypatch) -> None:
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        before = broker_state(broker)
        service = ExecutionService(broker, session, account_id=account_id)
        original = Session.commit

        def explode(self):
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(Session, "commit", explode)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        monkeypatch.setattr(Session, "commit", original)
        assert broker_state(broker) == before
    with Session(engine) as session:
        assert counts(session) == {"orders": 0, "fills": 0, "positions": 0, "trades": 0}


def test_stale_account_version_conflicts_and_rolls_back(engine: Engine, monkeypatch) -> None:
    """A writer landing between the version read and the cash write must not be overwritten."""
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        before = broker_state(broker)
        service = ExecutionService(broker, session, account_id=account_id)
        original = ExecutionRepository.persist_execution

        def concurrent(self, *args, **kwargs):
            with Session(engine) as other:
                SimulationStateRepository(other).update_account_cash(
                    account_id, 0, Decimal("1"), START)
                other.commit()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(ExecutionRepository, "persist_execution", concurrent)
        with pytest.raises(SimulationStateConflict):
            service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        assert broker_state(broker) == before
    with Session(engine) as session:
        assert counts(session) == {"orders": 0, "fills": 0, "positions": 0, "trades": 0}
        # The concurrent writer's value survives; this execution did not clobber it.
        stored = SimulationStateRepository(session).get_account("SIM", "operator")
        assert stored.cash == Decimal("1") and stored.state_version == 1


# Guards --------------------------------------------------------------------

def test_durable_execution_requires_a_session_and_an_account(engine: Engine) -> None:
    broker = SimBroker(CASH)
    with pytest.raises(ValueError):
        ExecutionService(broker).execute_and_persist(intent(), [bar(1)], updated_at=START)
    with Session(engine) as session:
        with pytest.raises(LookupError):
            ExecutionService(broker, session, account_id=999).execute_and_persist(
                intent(), [bar(1)], updated_at=START)
        assert counts(session) == {"orders": 0, "fills": 0, "positions": 0, "trades": 0}
        assert session.scalar(select(func.count()).select_from(SimulationAccountRecord)) == 0


def test_plain_execute_still_persists_nothing(engine: Engine) -> None:
    """The strategy runner keeps using this path, so it must stay side-effect free."""
    with Session(engine) as session:
        opened(session)
        session.commit()
        broker = SimBroker(CASH)
        order = ExecutionService(broker).execute(intent(), [bar(1)])
        assert order.status is OrderStatus.FILLED
        assert counts(session) == {"orders": 0, "fills": 0, "positions": 0, "trades": 0}


def test_broker_is_restored_even_if_rollback_fails(engine: Engine, monkeypatch) -> None:
    """A dead connection must not leave a phantom position in broker memory."""
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = SimBroker(CASH)
        before = broker_state(broker)
        service = ExecutionService(broker, session, account_id=account_id)

        def explode(*args, **kwargs):
            raise RuntimeError("injected save_position failure")

        def dead(self):
            raise RuntimeError("injected rollback failure")

        monkeypatch.setattr(SimulationStateRepository, "save_position", explode)
        monkeypatch.setattr(Session, "rollback", dead)
        with pytest.raises(RuntimeError):
            service.execute_and_persist(intent(), [bar(1)], updated_at=START)
        assert broker_state(broker) == before
