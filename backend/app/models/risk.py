"""Restart-safe planned trading reservations; this is not an order/fill ledger."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.scanner import utc_now
from app.models.types import DecimalString, UTCDateTime


class DailySymbolState(Base):
    __tablename__ = "daily_symbol_states"
    __table_args__ = (
        UniqueConstraint("trading_date", "symbol", name="uq_daily_symbol_state_date_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_intent_issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    planned_risk_amount: Mapped[Decimal] = mapped_column(DecimalString(), nullable=False)
    base_notional_reserved: Mapped[Decimal] = mapped_column(DecimalString(), default=Decimal("0"), nullable=False)
    pyramid_notional_reserved: Mapped[Decimal] = mapped_column(DecimalString(), default=Decimal("0"), nullable=False)
    add_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risk_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
