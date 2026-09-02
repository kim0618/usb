"""Execution and shadow research persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import DecimalString, UTCDateTime


class ExecutionOrderRecord(Base):
    __tablename__ = "execution_orders"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    broker_type: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    requested_quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(64))
    reference_price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    execution_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ExecutionFillRecord(Base):
    __tablename__ = "execution_fills"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("execution_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    raw_market_price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    spread_cost: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    slippage_cost: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    commission: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    fx_cost: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ShadowTradeRecord(Base):
    __tablename__ = "shadow_trades"
    __table_args__ = (UniqueConstraint("scanner_candidate_id", "variant", "variant_version", name="uq_shadow_candidate_variant_version"),)
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scanner_run_id: Mapped[int | None] = mapped_column(ForeignKey("scanner_runs.id", ondelete="SET NULL"))
    scanner_candidate_id: Mapped[int | None] = mapped_column(ForeignKey("scanner_candidates.id", ondelete="SET NULL"))
    gpt_analysis_id: Mapped[int | None] = mapped_column(ForeignKey("gpt_analyses.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    variant: Mapped[str] = mapped_column(String(4), nullable=False)
    variant_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_control: Mapped[bool] = mapped_column(Boolean, nullable=False)
    initial_planned_risk: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    entry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    exit_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    average_entry_price: Mapped[Decimal | None] = mapped_column(DecimalString())
    average_exit_price: Mapped[Decimal | None] = mapped_column(DecimalString())
    gross_pnl: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    gross_r: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    net_r: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    ambiguous_bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    premarket_passed: Mapped[bool | None] = mapped_column(Boolean)
    opening_passed: Mapped[bool | None] = mapped_column(Boolean)
    entry_signalled: Mapped[bool | None] = mapped_column(Boolean)
    entry_filled: Mapped[bool | None] = mapped_column(Boolean)
    no_trade_reason: Mapped[str | None] = mapped_column(String(64))
    exit_reason: Mapped[str | None] = mapped_column(String(64))
    gate_reached: Mapped[str | None] = mapped_column(String(32))
    holding_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
