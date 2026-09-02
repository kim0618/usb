"""Stage 4 GPT research and human-decision persistence models."""

from datetime import date, datetime

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.scanner import ScannerCandidate, ScannerRun, utc_now
from app.models.types import UTCDateTime


class GPTAnalysis(Base):
    __tablename__ = "gpt_analyses"
    __table_args__ = (
        UniqueConstraint("scanner_run_id", "payload_hash", name="uq_gpt_analysis_run_payload"),
        Index("ix_gpt_analyses_run_latest", "scanner_run_id", "status", "analysis_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scanner_run_id: Mapped[int] = mapped_column(ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scanner_run: Mapped[ScannerRun] = relationship()
    candidates: Mapped[list["GPTCandidateAnalysis"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")
    decisions: Mapped[list["HumanDecisionRecord"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")


class GPTCandidateAnalysis(Base):
    __tablename__ = "gpt_candidate_analyses"
    __table_args__ = (
        UniqueConstraint("gpt_analysis_id", "symbol", name="uq_gpt_candidate_analysis_symbol"),
        UniqueConstraint("gpt_analysis_id", "gpt_rank", name="uq_gpt_candidate_analysis_rank"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gpt_analysis_id: Mapped[int] = mapped_column(ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False)
    scanner_candidate_id: Mapped[int] = mapped_column(ForeignKey("scanner_candidates.id", ondelete="RESTRICT"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    gpt_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    catalyst_score: Mapped[float] = mapped_column(Float, nullable=False)
    fundamental_score: Mapped[float] = mapped_column(Float, nullable=False)
    momentum_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    catalyst_duration: Mapped[str] = mapped_column(String(32), nullable=False)
    stop_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    trailing_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    overnight_suitability: Mapped[str] = mapped_column(String(16), nullable=False)
    company_summary: Mapped[str] = mapped_column(Text, nullable=False)
    catalyst_summary: Mapped[str] = mapped_column(Text, nullable=False)
    risk_summary: Mapped[str] = mapped_column(Text, nullable=False)
    invalidation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    unknown_fields_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    analysis: Mapped[GPTAnalysis] = relationship(back_populates="candidates")
    scanner_candidate: Mapped[ScannerCandidate] = relationship()
    sources: Mapped[list["GPTSource"]] = relationship(back_populates="candidate_analysis", cascade="all, delete-orphan")


class GPTSource(Base):
    __tablename__ = "gpt_sources"
    __table_args__ = (Index("ix_gpt_sources_candidate", "gpt_candidate_analysis_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gpt_candidate_analysis_id: Mapped[int] = mapped_column(ForeignKey("gpt_candidate_analyses.id", ondelete="CASCADE"), nullable=False)
    claim: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    source_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    candidate_analysis: Mapped[GPTCandidateAnalysis] = relationship(back_populates="sources")


class HumanDecisionRecord(Base):
    __tablename__ = "human_decisions"
    __table_args__ = (UniqueConstraint("gpt_analysis_id", "symbol", name="uq_human_decision_analysis_symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gpt_analysis_id: Mapped[int] = mapped_column(ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False)
    scanner_candidate_id: Mapped[int] = mapped_column(ForeignKey("scanner_candidates.id", ondelete="RESTRICT"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    analysis: Mapped[GPTAnalysis] = relationship(back_populates="decisions")
    scanner_candidate: Mapped[ScannerCandidate] = relationship()
