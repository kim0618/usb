"""Restart-critical Strategy V0 lifecycle state."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.scanner import utc_now
from app.models.types import DecimalString, UTCDateTime


class StrategyStateRecord(Base):
    __tablename__ = "strategy_states"
    __table_args__ = (UniqueConstraint("symbol", "trading_date", "book", "variant",
                                       name="uq_strategy_state_grain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scanner_candidate_id: Mapped[int | None] = mapped_column(Integer)
    book: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTUAL")
    variant: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTUAL")
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    phase_reason: Mapped[str | None] = mapped_column(String(32))
    entry_trading_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[Decimal | None] = mapped_column(DecimalString())
    initial_stop: Mapped[Decimal | None] = mapped_column(DecimalString())
    active_stop: Mapped[Decimal | None] = mapped_column(DecimalString())
    highest_price: Mapped[Decimal | None] = mapped_column(DecimalString())
    add_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    add_signal_issued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    holding_day: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overnight: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_market_as_of: Mapped[datetime | None] = mapped_column(UTCDateTime())
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trailing_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    overnight_suitability: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class PremarketDiagnosticRecord(Base):
    """One compact, durable record of the inputs used by an actual premarket gate."""

    __tablename__ = "premarket_diagnostics"
    __table_args__ = (UniqueConstraint("strategy_state_id", name="uq_premarket_diagnostic_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_state_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_states.id", ondelete="CASCADE"), nullable=False)
    minute_bars_count: Mapped[int] = mapped_column(Integer, nullable=False)
    premarket_bars_count: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_close: Mapped[Decimal | None] = mapped_column(DecimalString())
    reference_price: Mapped[Decimal | None] = mapped_column(DecimalString())
    gap_pct: Mapped[Decimal | None] = mapped_column(DecimalString())
    premarket_volume: Mapped[Decimal | None] = mapped_column(DecimalString())
    historical_average_daily_volume: Mapped[Decimal | None] = mapped_column(DecimalString())
    volume_ratio: Mapped[Decimal | None] = mapped_column(DecimalString())
    first_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invalid_field: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
