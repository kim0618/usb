"""D2.1 Evidence Chunk schema - the AI-readable text unit a future D3 Research Engine actually
reads. Every chunk traces back to exactly one source and carries that source's own PIT metadata; no
chunk is ever built by merging text from two different sources (D2.1 brief §13's "no cross-source
merge").
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.backtest.strategy_h_v2.evidence.bundle import EvidenceBundleV2
from app.backtest.strategy_h_v2.evidence.sources import SOURCE_BOUNDARY, SourceProvenance, SourceType

SCHEMA_VERSION = "h_v2_evidence_chunk_v1"
INPUT_PACKAGE_SCHEMA_VERSION = "h_v2_ai_research_input_v1"


class MaterializationStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    CONTENT_NOT_EXTRACTABLE = "CONTENT_NOT_EXTRACTABLE"
    EXHIBIT_UNRESOLVED = "EXHIBIT_UNRESOLVED"
    """The filing's own index page did not identify a matching exhibit deterministically (e.g. a
    2.02 8-K with no `EX-99.*` document) - not a fetch failure, a real fact about that filing."""
    NOT_FETCHED = "NOT_FETCHED"
    """In scope for this candidate's depth policy but not attempted (bounds cost) or the fetch
    itself failed after retries - always fail-soft, never silently dropped."""


class EvidenceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    evidence_id: str
    source_id: str
    candidate_id: str
    source_type: SourceType
    section: str | None
    text: str
    published_at: datetime | None
    available_at: datetime | None
    fetched_at: datetime
    content_checksum: str
    chunk_index: int
    chunk_count: int
    pit_eligible: bool
    extraction_status: MaterializationStatus
    source_boundary: str = SOURCE_BOUNDARY

    @field_validator("published_at", "available_at", "fetched_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("must be timezone-aware")
        return value

    @field_validator("source_boundary")
    @classmethod
    def _boundary_is_fixed(cls, value: str) -> str:
        if value != SOURCE_BOUNDARY:
            raise ValueError("source_boundary is a fixed constant, not a per-chunk choice")
        return value

    @field_validator("text")
    @classmethod
    def _text_matches_status(cls, value: str, info) -> str:
        status = info.data.get("extraction_status")
        if status == MaterializationStatus.EXTRACTED and not value.strip():
            raise ValueError("EXTRACTED status requires non-empty text")
        return value


class DocumentMaterialization(BaseModel):
    """One filing/exhibit's materialization outcome - independent of how many chunks it produced."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    role: str
    """`LATEST_10K` / `RECENT_10Q` / `EARNINGS_RELEASE` / `IR_PRESENTATION` / `MATERIAL_8K`, per the
    D2.1 materialization priority (§4-§5)."""
    status: MaterializationStatus
    content_type: str
    chunk_count: int
    raw_checksum: str | None


class CandidateMaterializationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    candidate_id: str
    content_readiness: str
    documents: list[DocumentMaterialization] = Field(default_factory=list)


class AIResearchInputV1(BaseModel):
    """D2 + D2.1 combined, immutable, per-candidate package - the actual input a future D3 engine
    reads. Contains no AI interpretation (inherited unchanged from `EvidenceBundleV2`)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=INPUT_PACKAGE_SCHEMA_VERSION)
    run_id: str
    generated_at: datetime
    evidence_bundle: EvidenceBundleV2
    chunks: list[EvidenceChunk] = Field(default_factory=list)
    materialization: CandidateMaterializationRecord
    source_manifest: list[SourceProvenance] = Field(default_factory=list)

    @field_validator("generated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must be timezone-aware")
        return value
