"""Durable simulation broker account, position, and trade state.

Execution orders/fills stay the immutable execution history; these tables are the
authority for the broker's current cash, holdings, and trade lifecycle so a restart
can rehydrate them. Equity is deliberately absent: it needs external mark prices.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import DecimalString, UTCDateTime


class SimulationAccountRecord(Base):
    __tablename__ = "simulation_accounts"
    __table_args__ = (UniqueConstraint("broker_type", "account_key", name="uq_simulation_accounts_broker_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    cash: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # Optimistic lock so a second writer cannot silently overwrite broker state.
    state_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class SimulationPositionRecord(Base):
    """One row per open holding; a fully closed position is deleted, never zeroed."""

    __tablename__ = "simulation_positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol", name="uq_simulation_positions_account_symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    average_price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    cost_basis: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SimulationTradeRecord(Base):
    """Trade lifecycle, including the notional accumulators a restart needs.

    entry_notional/exit_notional/sold_quantity are not reporting extras: SELL
    recomputes average_exit_price and the exact close PnL from them, so a
    rehydrated open trade without them would settle at the wrong price.
    """

    __tablename__ = "simulation_trades"
    __table_args__ = (
        UniqueConstraint("account_id", "trade_uid", name="uq_simulation_trades_account_uid"),
        # The broker keeps one open trade per symbol; encode that, not just uniqueness.
        Index("uq_simulation_trades_open_symbol", "account_id", "symbol",
              unique=True, sqlite_where=text("status = 'OPEN'")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id", ondelete="CASCADE"), nullable=False)
    trade_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entry_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    initial_quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    total_quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    average_entry_price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    average_exit_price: Mapped[Decimal | None] = mapped_column(DecimalString())
    gross_pnl: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    planned_initial_risk: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    gross_r: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    net_r: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    entry_notional: Mapped[Decimal] = mapped_column(DecimalString(), default=Decimal("0"), server_default="0", nullable=False)
    exit_notional: Mapped[Decimal] = mapped_column(DecimalString(), default=Decimal("0"), server_default="0", nullable=False)
    sold_quantity: Mapped[Decimal] = mapped_column(DecimalString(), default=Decimal("0"), server_default="0", nullable=False)
    ambiguous_bar_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    exit_reason: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
