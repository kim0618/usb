"""Strategy analytics persistence; no trading, risk, or entry path reads it."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.scanner import utc_now
from app.models.types import DecimalString, UTCDateTime


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
    # The Production V1 ratio the gate used, beside the counterfactual V2 median ratio.
    # Nullable with no backfill: rows written before V2 analytics have no V2 answer.
    # Exact Decimal text: SQLite REAL would round 1.24999999999 across a threshold.
    v1_volume_ratio: Mapped[Decimal | None] = mapped_column(DecimalString(), nullable=True)
    v2_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    v2_median_ratio: Mapped[Decimal | None] = mapped_column(DecimalString(), nullable=True)
    v2_today_premarket_volume: Mapped[Decimal | None] = mapped_column(DecimalString(), nullable=True)
    v2_baseline_volume: Mapped[Decimal | None] = mapped_column(DecimalString(), nullable=True)
    v2_baseline_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    v2_baseline_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    v2_baseline_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    v2_baseline_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    v2_collector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    v2_projections_json: Mapped[dict[str, bool] | None] = mapped_column(JSON, nullable=True)


class PremarketVolumeSession(Base):
    """One symbol-session's Kiwoom PREMARKET volume, the V2 baseline's durable cache.

    The collector version is part of the identity, so a changed collection contract
    starts its own rows instead of silently reinterpreting stored ones.
    """

    __tablename__ = "premarket_volume_sessions"
    __table_args__ = (
        UniqueConstraint("symbol", "exchange", "trading_date", "source", "collector_version",
                         name="uq_premarket_volume_session"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    collector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Sum over 04:00 <= t < regular open; None when the window held no bar at all.
    premarket_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    regular_bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pages_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_reached: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    quality_status: Mapped[str] = mapped_column(String(48), nullable=False)
    quality_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
