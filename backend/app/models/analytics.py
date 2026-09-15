"""Strategy analytics persistence; no trading, risk, or entry path reads it."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.scanner import utc_now
from app.models.types import UTCDateTime


class EntryDriftObservation(Base):
    __tablename__ = "entry_drift_observations"
    __table_args__ = (
        UniqueConstraint("scanner_candidate_id", name="uq_entry_drift_candidate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The ENTRY session observed; its authority is the scanner run of the session before.
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scanner_run_id: Mapped[int] = mapped_column(
        ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False)
    gpt_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False)
    scanner_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("scanner_candidates.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_version: Mapped[str] = mapped_column(String(64), nullable=False)
    observer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    quality_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signal_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    signal_bar_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    signal_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    initial_stop: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    intended_execution_bar_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    intended_execution_raw_open: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    drift_pct: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    stop_distance_pct: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    projections_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
