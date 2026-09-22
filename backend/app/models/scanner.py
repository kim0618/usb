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
    active_gpt_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("gpt_analyses.id", name="fk_scanner_runs_active_gpt_analysis", ondelete="SET NULL"),
        nullable=True,
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


class ScannerUniverseInput(Base):
    """One symbol of the universe a paper scanner run ranked, as the provider handed it over.

    ``scanner_candidates`` keeps what the scanner ranked; this table keeps what it was given
    and what became of each symbol - its place in the provider's liquidity ranking, the
    exchange and market cap it arrived with, and either its scanner rank or the exclusion
    reason. It is the RECORDED_PAPER_UNIVERSE authority a later replay can reuse, written in
    the same transaction as the run, so a run never exists without the universe it ranked.
    """

    __tablename__ = "scanner_universe_inputs"
    __table_args__ = (
        UniqueConstraint("scanner_run_id", "position", name="uq_scanner_universe_position"),
        UniqueConstraint("scanner_run_id", "symbol", name="uq_scanner_universe_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scanner_run_id: Mapped[int] = mapped_column(
        ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 1-based place in the provider's liquidity ranking, the order the universe was built in.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Exact text of the provider's value; never a float that could be re-rounded.
    market_cap: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: SCANNER_CANDIDATE with ``scanner_rank``, or EXCLUDED with ``exclusion_reason``.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    scanner_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: One digest over the whole ordered universe of the run, repeated on every row.
    universe_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
