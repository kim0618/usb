"""Persistence-only foundation for future scanner snapshots."""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import UTCDateTime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScannerRun(Base):
    __tablename__ = "scanner_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    excluded_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    top8_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    candidates: Mapped[list["ScannerCandidate"]] = relationship(
        back_populates="scanner_run", cascade="all, delete-orphan"
    )


class ScannerCandidate(Base):
    __tablename__ = "scanner_candidates"
    __table_args__ = (
        UniqueConstraint("scanner_run_id", "symbol", name="uq_scanner_candidate_run_symbol"),
        Index("ix_scanner_candidates_run_rank", "scanner_run_id", "rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scanner_run_id: Mapped[int] = mapped_column(
        ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_top8: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_components_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    scanner_run: Mapped[ScannerRun] = relationship(back_populates="candidates")
