"""Persistence behaviour for the durable simulation broker state."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.broker.domain import SimPosition, TradeResult, TradeStatus
from app.core.database import Base, create_db_engine
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)
from app.repositories.simulation import SimulationStateConflict, SimulationStateRepository

NOW = datetime(2026, 9, 6, 13, 45, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 6, 13, 46, tzinfo=timezone.utc)
# SimBroker sizing, an actual fill price, a notional, a cost, and a long tail.
EXACT = [Decimal("166.6666666666666666666666667"), Decimal("102.153"), Decimal("82957.50"),
         Decimal("-42.50"), Decimal("500.0000000000000000000000001")]
QUANTITY = EXACT[0]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    # The project engine turns foreign keys on, which CASCADE relies on.
    db = create_db_engine(f"sqlite:///{tmp_path / 'simulation.sqlite3'}")
    Base.metadata.create_all(db)
    try:
        yield db
    finally:
        db.dispose()


def account(repository: SimulationStateRepository, key: str = "operator", cash: Decimal | None = None):
    return repository.create_account(broker_type="SIM", account_key=key, base_currency="USD",
                                     initial_cash=Decimal("100000"),
                                     cash=Decimal("100000") if cash is None else cash, created_at=NOW)


def position(symbol: str = "TSLA", quantity: Decimal = QUANTITY, realized: str = "0") -> SimPosition:
    return SimPosition(symbol=symbol, quantity=quantity, average_price=Decimal("102.153"),
                       cost_basis=Decimal("17025.50"), realized_pnl=Decimal(realized),
                       opened_at=NOW, updated_at=NOW)


def trade(uid: str = "TRADE-TSLA-1", symbol: str = "TSLA", status: TradeStatus = TradeStatus.OPEN) -> TradeResult:
    return TradeResult(
        trade_id=uid, symbol=symbol, entry_time=NOW, exit_time=None, initial_quantity=QUANTITY,
        total_quantity=QUANTITY, average_entry_price=Decimal("102.153"), average_exit_price=None,
        gross_pnl=Decimal("0"), net_pnl=Decimal("-42.50"), planned_initial_risk=Decimal("1000"),
        gross_r=Decimal("0"), net_r=Decimal("-0.0425"), total_cost=Decimal("42.50"),
        ambiguous_bar_count=0, exit_reason=None, status=status,
        _entry_notional=Decimal("17025.50"), _exit_notional=Decimal("0"), _sold_quantity=Decimal("0"))


# Account ------------------------------------------------------------------

def test_account_create_and_lookup(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        assert repository.get_account("SIM", "operator") is None
        row = account(repository, cash=Decimal("82957.50"))
        session.commit()
        assert row.state_version == 0
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        stored = repository.get_account("SIM", "operator")
        assert stored is not None and repository.get_account_by_id(stored.id) is stored
        # Starting capital and current cash are independent facts.
        assert stored.initial_cash == Decimal("100000") and stored.cash == Decimal("82957.50")
        assert stored.created_at == NOW and stored.created_at.tzinfo is not None


def test_duplicate_account_identity_is_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        account(SimulationStateRepository(session))
        session.commit()
    with Session(engine) as session:
        # create_account flushes, so the unique constraint fires before any commit.
        with pytest.raises(IntegrityError):
            account(SimulationStateRepository(session))
    with Session(engine) as session:
        # A different key on the same broker, and the same key on another broker, both fit.
        repository = SimulationStateRepository(session)
        account(repository, key="shadow")
        repository.create_account(broker_type="PAPER", account_key="operator", base_currency="USD",
                                  initial_cash=Decimal("1"), cash=Decimal("1"), created_at=NOW)
        session.commit()
        assert session.scalar(select(func.count()).select_from(SimulationAccountRecord)) == 3


def test_account_cash_update_bumps_state_version(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        assert repository.update_account_cash(row.id, 0, Decimal("82957.50"), LATER) == 1
        session.commit()
        stored = repository.get_account_by_id(row.id)
        assert stored.cash == Decimal("82957.50") and stored.state_version == 1
        assert stored.updated_at == LATER


def test_stale_state_version_conflicts_without_writing(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.update_account_cash(row.id, 0, Decimal("50000"), LATER)
        with pytest.raises(SimulationStateConflict):
            repository.update_account_cash(row.id, 0, Decimal("1"), LATER)
        with pytest.raises(SimulationStateConflict):
            repository.update_account_cash(row.id + 99, 0, Decimal("1"), LATER)
        session.commit()
        assert repository.get_account_by_id(row.id).cash == Decimal("50000")


# Position -----------------------------------------------------------------

def test_position_round_trip_and_listing(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        first, second = account(repository), account(repository, key="shadow")
        repository.save_position(first.id, position())
        repository.save_position(first.id, position("NVDA"))
        # The same symbol under another account is a separate row.
        repository.save_position(second.id, position())
        session.commit()
        assert repository.get_position(first.id, "TSLA") == position()
        assert [p.symbol for p in repository.list_positions(first.id)] == ["NVDA", "TSLA"]
        assert [p.symbol for p in repository.list_positions(second.id)] == ["TSLA"]


def test_position_save_updates_in_place(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_position(row.id, position())
        grown = SimPosition("TSLA", QUANTITY * 2, Decimal("103.5"), Decimal("34000"),
                            Decimal("125.25"), NOW, LATER)
        repository.save_position(row.id, grown)
        session.commit()
        assert session.scalar(select(func.count()).select_from(SimulationPositionRecord)) == 1
        stored = repository.get_position(row.id, "TSLA")
        assert stored == grown and stored.opened_at == NOW and stored.updated_at == LATER


def test_position_symbol_is_normalized(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_position(row.id, position(symbol=" tsla "))
        session.commit()
        assert repository.get_position(row.id, "tsla").symbol == "TSLA"
        assert session.scalar(select(SimulationPositionRecord.symbol)) == "TSLA"


def test_duplicate_position_grain_is_rejected_at_the_database(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_position(row.id, position())
        session.commit()
        session.add(SimulationPositionRecord(
            account_id=row.id, symbol="TSLA", quantity=Decimal("1"), average_price=Decimal("1"),
            cost_basis=Decimal("1"), realized_pnl=Decimal("0"), opened_at=NOW, updated_at=NOW))
        with pytest.raises(IntegrityError):
            session.commit()


def test_full_close_deletes_the_position_row(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_position(row.id, position())
        assert repository.delete_position(row.id, "TSLA") is True
        session.commit()
        assert repository.get_position(row.id, "TSLA") is None
        assert repository.list_positions(row.id) == ()
        assert repository.delete_position(row.id, "TSLA") is False


# Trade --------------------------------------------------------------------

def test_trade_round_trip_preserves_rehydration_state(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        original = trade()
        repository.save_trade(row.id, original, updated_at=NOW)
        session.commit()
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        stored = repository.get_trade(1, "TRADE-TSLA-1")
        assert stored == original
        # Without these a rehydrated SELL would settle at the wrong exit average.
        assert stored.planned_initial_risk == Decimal("1000")
        assert (stored._entry_notional, stored._exit_notional, stored._sold_quantity) == (
            Decimal("17025.50"), Decimal("0"), Decimal("0"))
        assert stored.status is TradeStatus.OPEN and stored.entry_time == NOW


def test_trade_update_and_close_keeps_history(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_trade(row.id, trade(), updated_at=NOW)
        closed = trade()
        closed.status = TradeStatus.CLOSED
        closed.exit_time, closed.exit_reason = LATER, "STOP"
        closed.average_exit_price, closed._exit_notional = Decimal("105.5"), Decimal("17583.33")
        closed._sold_quantity, closed.gross_pnl = QUANTITY, Decimal("557.83")
        repository.save_trade(row.id, closed, updated_at=LATER)
        session.commit()
        assert session.scalar(select(func.count()).select_from(SimulationTradeRecord)) == 1
        assert repository.get_trade(row.id, "TRADE-TSLA-1") == closed
        assert repository.get_open_trade(row.id, "TSLA") is None
        assert repository.list_open_trades(row.id) == ()


def test_open_trade_queries(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_trade(row.id, trade(), updated_at=NOW)
        repository.save_trade(row.id, trade("TRADE-NVDA-1", "NVDA"), updated_at=NOW)
        repository.save_trade(row.id, trade("TRADE-MSFT-1", "MSFT", TradeStatus.CLOSED), updated_at=NOW)
        session.commit()
        assert [t.symbol for t in repository.list_open_trades(row.id)] == ["NVDA", "TSLA"]
        assert repository.get_open_trade(row.id, "tsla").trade_id == "TRADE-TSLA-1"
        assert repository.get_open_trade(row.id, "MSFT") is None


def test_duplicate_trade_uid_is_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_trade(row.id, trade(), updated_at=NOW)
        session.commit()
        # A different symbol and CLOSED status isolate the uid constraint from the open index.
        with pytest.raises(IntegrityError):
            session.execute(text(
                "INSERT INTO simulation_trades (account_id, trade_uid, symbol, entry_time, initial_quantity, "
                "total_quantity, average_entry_price, gross_pnl, net_pnl, planned_initial_risk, gross_r, "
                "net_r, total_cost, status, created_at, updated_at) VALUES "
                "(:a, 'TRADE-TSLA-1', 'NVDA', :t, '1', '1', '1', '0', '0', '1', '0', '0', '0', 'CLOSED', :t, :t)"),
                {"a": row.id, "t": NOW.isoformat()})


def test_only_one_open_trade_per_symbol(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_trade(row.id, trade(), updated_at=NOW)
        repository.save_trade(row.id, trade("TRADE-TSLA-0", status=TradeStatus.CLOSED), updated_at=NOW)
        # A closed trade beside an open one on the same symbol is expected history.
        session.commit()
        repository.save_trade(row.id, trade("TRADE-TSLA-OLD", status=TradeStatus.CLOSED), updated_at=NOW)
        session.commit()
        assert session.scalar(select(func.count()).select_from(SimulationTradeRecord)) == 3
        with pytest.raises(IntegrityError):
            repository.save_trade(row.id, trade("TRADE-TSLA-2"), updated_at=NOW)


# Cross-cutting ------------------------------------------------------------

@pytest.mark.parametrize("value", EXACT)
def test_decimal_values_round_trip_exactly(engine: Engine, value: Decimal) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository, cash=value)
        repository.save_position(row.id, SimPosition("TSLA", value, value, value, value, NOW, NOW))
        moved = trade()
        moved.net_pnl, moved._entry_notional = value, value
        repository.save_trade(row.id, moved, updated_at=NOW)
        session.commit()
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        stored = repository.get_account("SIM", "operator")
        held = repository.get_position(stored.id, "TSLA")
        settled = repository.get_trade(stored.id, "TRADE-TSLA-1")
        for actual in (stored.cash, held.quantity, held.realized_pnl, settled.net_pnl,
                       settled._entry_notional):
            assert actual == value and str(actual) == str(value)


def test_deleting_an_account_cascades_to_its_state(engine: Engine) -> None:
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        kept, removed = account(repository), account(repository, key="shadow")
        for row in (kept, removed):
            repository.save_position(row.id, position())
            repository.save_trade(row.id, trade(), updated_at=NOW)
        session.commit()
        session.delete(removed)
        session.commit()
        assert repository.list_positions(removed.id) == () and repository.list_open_trades(removed.id) == ()
        assert len(repository.list_positions(kept.id)) == 1 and len(repository.list_open_trades(kept.id)) == 1


def test_repository_leaves_the_transaction_to_the_caller(engine: Engine) -> None:
    """P1-C folds these writes into one execution transaction, so nothing may self-commit."""
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        row = account(repository)
        repository.save_position(row.id, position())
        repository.save_trade(row.id, trade(), updated_at=NOW)
        session.rollback()
    with Session(engine) as session:
        repository = SimulationStateRepository(session)
        assert repository.get_account("SIM", "operator") is None
        assert session.scalar(select(func.count()).select_from(SimulationPositionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(SimulationTradeRecord)) == 0


def test_cash_update_keeps_other_pending_changes(tmp_path: Path) -> None:
    """The application session runs autoflush=False, so a broad expire would lose writes."""
    db = create_db_engine(f"sqlite:///{tmp_path / 'pending.sqlite3'}")
    Base.metadata.create_all(db)
    try:
        with Session(db, autoflush=False, expire_on_commit=False) as session:
            repository = SimulationStateRepository(session)
            row = account(repository)
            held = repository.save_position(row.id, position())
            session.commit()
            held.realized_pnl = Decimal("125.25")  # Staged by the caller, not yet flushed.
            repository.update_account_cash(row.id, 0, Decimal("82957.50"), LATER)
            assert held.realized_pnl == Decimal("125.25")
            session.commit()
            assert repository.get_position(row.id, "TSLA").realized_pnl == Decimal("125.25")
            stored = repository.get_account_by_id(row.id)
            assert stored.cash == Decimal("82957.50") and stored.state_version == 1
    finally:
        db.dispose()
