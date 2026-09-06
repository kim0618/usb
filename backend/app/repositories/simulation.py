"""Simulation broker state persistence. Transaction ownership remains with services.

Stores already-computed broker state; every average, cost basis, and PnL figure
arrives from SimBroker and is written verbatim so persistence can never disagree
with the running broker.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.broker.domain import SimPosition, TradeResult, TradeStatus
from app.market.symbols import normalize_symbol
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)

TRADE_STATE = ("entry_time", "exit_time", "initial_quantity", "total_quantity",
               "average_entry_price", "average_exit_price", "gross_pnl", "net_pnl",
               "planned_initial_risk", "gross_r", "net_r", "total_cost", "ambiguous_bar_count",
               "exit_reason")


class SimulationStateConflict(RuntimeError):
    """A guarded update matched no row: another writer moved the account on."""


class SimulationStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # Account -------------------------------------------------------------
    # Returns the ORM row: callers need id and state_version, and SimAccount is a
    # computed mark-to-market snapshot rather than a persisted shape.

    def get_account(self, broker_type: str, account_key: str) -> SimulationAccountRecord | None:
        return self.session.scalar(select(SimulationAccountRecord).where(
            SimulationAccountRecord.broker_type == broker_type,
            SimulationAccountRecord.account_key == account_key))

    def get_account_by_id(self, account_id: int) -> SimulationAccountRecord | None:
        return self.session.get(SimulationAccountRecord, account_id)

    def create_account(self, *, broker_type: str, account_key: str, base_currency: str,
                       initial_cash, cash, created_at: datetime) -> SimulationAccountRecord:
        """Explicit only; the unique constraint rejects a duplicate rather than overwriting."""
        row = SimulationAccountRecord(
            broker_type=broker_type, account_key=account_key, base_currency=base_currency,
            initial_cash=initial_cash, cash=cash, created_at=created_at, updated_at=created_at)
        self.session.add(row)
        self.session.flush()
        return row

    def update_account_cash(self, account_id: int, expected_state_version: int, cash,
                            updated_at: datetime) -> int:
        """Bump cash only while state_version still matches; return the new version."""
        result = self.session.execute(
            update(SimulationAccountRecord)
            .where(SimulationAccountRecord.id == account_id,
                   SimulationAccountRecord.state_version == expected_state_version)
            .values(cash=cash, updated_at=updated_at,
                    state_version=SimulationAccountRecord.state_version + 1))
        if result.rowcount != 1:
            raise SimulationStateConflict("simulation account state_version mismatch")
        cached = self.session.get(SimulationAccountRecord, account_id)
        if cached is not None:
            # Refresh this row only. expire_all() would discard changes the caller
            # staged on other objects, which autoflush=False leaves pending.
            self.session.expire(cached)
        return expected_state_version + 1

    # Position ------------------------------------------------------------

    def get_position(self, account_id: int, symbol: str) -> SimPosition | None:
        row = self._position_row(account_id, normalize_symbol(symbol))
        return None if row is None else self._position(row)

    def list_positions(self, account_id: int) -> tuple[SimPosition, ...]:
        rows = self.session.scalars(select(SimulationPositionRecord).where(
            SimulationPositionRecord.account_id == account_id).order_by(SimulationPositionRecord.symbol))
        return tuple(self._position(row) for row in rows)

    def save_position(self, account_id: int, position: SimPosition) -> SimulationPositionRecord:
        symbol = normalize_symbol(position.symbol)
        row = self._position_row(account_id, symbol)
        if row is None:
            row = SimulationPositionRecord(account_id=account_id, symbol=symbol,
                                           opened_at=position.opened_at)
            self.session.add(row)
        row.quantity = position.quantity
        row.average_price = position.average_price
        row.cost_basis = position.cost_basis
        row.realized_pnl = position.realized_pnl
        row.updated_at = position.updated_at
        self.session.flush()
        return row

    def delete_position(self, account_id: int, symbol: str) -> bool:
        """A fully closed position is removed, matching SimBroker dropping the key."""
        row = self._position_row(account_id, normalize_symbol(symbol))
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def _position_row(self, account_id: int, symbol: str) -> SimulationPositionRecord | None:
        return self.session.scalar(select(SimulationPositionRecord).where(
            SimulationPositionRecord.account_id == account_id,
            SimulationPositionRecord.symbol == symbol))

    @staticmethod
    def _position(row: SimulationPositionRecord) -> SimPosition:
        return SimPosition(symbol=row.symbol, quantity=row.quantity, average_price=row.average_price,
                           cost_basis=row.cost_basis, realized_pnl=row.realized_pnl,
                           opened_at=row.opened_at, updated_at=row.updated_at)

    # Trade ---------------------------------------------------------------

    def get_trade(self, account_id: int, trade_uid: str) -> TradeResult | None:
        row = self._trade_row(account_id, trade_uid)
        return None if row is None else self._trade(row)

    def get_open_trade(self, account_id: int, symbol: str) -> TradeResult | None:
        row = self.session.scalar(select(SimulationTradeRecord).where(
            SimulationTradeRecord.account_id == account_id,
            SimulationTradeRecord.symbol == normalize_symbol(symbol),
            SimulationTradeRecord.status == TradeStatus.OPEN.value))
        return None if row is None else self._trade(row)

    def list_open_trades(self, account_id: int) -> tuple[TradeResult, ...]:
        rows = self.session.scalars(select(SimulationTradeRecord).where(
            SimulationTradeRecord.account_id == account_id,
            SimulationTradeRecord.status == TradeStatus.OPEN.value
        ).order_by(SimulationTradeRecord.symbol))
        return tuple(self._trade(row) for row in rows)

    def save_trade(self, account_id: int, trade: TradeResult, *, updated_at: datetime) -> SimulationTradeRecord:
        """Insert or update by trade_uid; closed rows are kept as trade history."""
        row = self._trade_row(account_id, trade.trade_id)
        if row is None:
            row = SimulationTradeRecord(account_id=account_id, trade_uid=trade.trade_id,
                                        symbol=normalize_symbol(trade.symbol), created_at=updated_at)
            self.session.add(row)
        for name in TRADE_STATE:
            setattr(row, name, getattr(trade, name))
        # SELL recomputes the exit average and the exact close PnL from these.
        row.entry_notional = trade._entry_notional
        row.exit_notional = trade._exit_notional
        row.sold_quantity = trade._sold_quantity
        row.status = TradeStatus(trade.status).value
        row.updated_at = updated_at
        self.session.flush()
        return row

    def _trade_row(self, account_id: int, trade_uid: str) -> SimulationTradeRecord | None:
        return self.session.scalar(select(SimulationTradeRecord).where(
            SimulationTradeRecord.account_id == account_id,
            SimulationTradeRecord.trade_uid == trade_uid))

    @staticmethod
    def _trade(row: SimulationTradeRecord) -> TradeResult:
        return TradeResult(
            trade_id=row.trade_uid, symbol=row.symbol,
            **{name: getattr(row, name) for name in TRADE_STATE},
            status=TradeStatus(row.status), _entry_notional=row.entry_notional,
            _exit_notional=row.exit_notional, _sold_quantity=row.sold_quantity)
