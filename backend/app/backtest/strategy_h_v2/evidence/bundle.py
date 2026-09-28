"""D2 Evidence Bundle V2 - the AI-ready output of the Research Evidence Collector.

Extends D1's Candidate Evidence Stub (`strategy_h_v2.evidence_bundle.CandidateEvidenceStub`) with
the raw-source layer D0's contract always specified (`recent_filings`, `recent_material_events`)
but D1 left as code-only metadata. Every numeric/factual field here is `CODE_OWNED`: nothing in
this schema can hold an interpretation ("undervalued", "strong moat", "APPROVE", ...). The six
AI-interpretable fields are fixed to `NOT_RESEARCHED` the same way D1 fixed five of them - D3 is the
only place they may ever be filled in, and only through its own separate contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.backtest.strategy_h_v2.evidence.sources import SourceProvenance

SCHEMA_VERSION = "h_v2_evidence_bundle_v2"
CONTRACT_VERSION = "h_v2_d0_v1"

#: Interpretive language D2 must never produce (D1.1 brief §37). Checked against every code-owned
#: numeric/status section on every bundle build - see `EvidenceBundleV2._no_interpretive_language`.
BANNED_INTERPRETIVE_SUBSTRINGS = (
    "undervalued", "overvalued", "strong moat", "weak moat", "future winner",
    "catalyst strong", "expectation gap positive", "approve", "reject", " buy ", " sell ",
    "good company", "bad company",
)


class NotResearched(StrEnum):
    NOT_RESEARCHED = "NOT_RESEARCHED"


class CandidateSource(StrEnum):
    E3_P1_HIGH = "E3_P1_HIGH"
    E3_P2_MEDIUM = "E3_P2_MEDIUM"
    MANUAL = "MANUAL"
    """A ticker requested outside the E3 funnel (D1.1 brief §31). Must never be presented as if it
    were an E3 output - `candidate_source` is the single place that distinction lives."""


class CollectionDepth(StrEnum):
    FULL = "FULL"
    """P1_HIGH and manual candidates: SEC filings plus an explicit attempt to identify
    earnings-release / investor-presentation / earnings-call / news candidates, even when the
    honest result of that attempt is `NOT_AVAILABLE` or `NOT_CONNECTED`."""
    CORE = "CORE"
    """P2_MEDIUM candidates: required SEC filings only. Not a smaller company - a smaller research
    budget allocation (D1.1 brief §26-27)."""


class EvidenceCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class MaterialEventRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    """References a `SourceProvenance.source_id` in `filings` or `source_manifest` - never an
    orphan fact with no traceable source."""
    item_code: str
    item_label: str
    item_category: str
    filing_date: str


class EvidenceBundleV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    contract_version: str = Field(default=CONTRACT_VERSION)
    run_id: str
    generated_at: datetime
    data_cutoff: datetime
    ticker: str
    candidate_source: CandidateSource
    collection_depth: CollectionDepth
    evidence_completeness: EvidenceCompleteness

    identity: dict[str, Any]
    market: dict[str, Any]
    fundamentals: dict[str, Any]
    fundamental_changes: dict[str, Any]
    balance_sheet: dict[str, Any]
    cashflow: dict[str, Any]
    price_context: dict[str, Any]
    earnings: dict[str, Any]

    filings: list[SourceProvenance] = Field(default_factory=list)
    company_releases: list[MaterialEventRef] = Field(default_factory=list)
    earnings_materials: list[SourceProvenance] = Field(default_factory=list)
    investor_materials: list[MaterialEventRef] = Field(default_factory=list)
    material_events: list[MaterialEventRef] = Field(default_factory=list)
    source_manifest: list[SourceProvenance] = Field(default_factory=list)

    data_quality: dict[str, Any]
    unknown_fields: list[str] = Field(default_factory=list)

    future_business: NotResearched = NotResearched.NOT_RESEARCHED
    catalysts: NotResearched = NotResearched.NOT_RESEARCHED
    expectation_gap: NotResearched = NotResearched.NOT_RESEARCHED
    competitive_position: NotResearched = NotResearched.NOT_RESEARCHED
    thesis: NotResearched = NotResearched.NOT_RESEARCHED
    decision: NotResearched = NotResearched.NOT_RESEARCHED

    @field_validator("generated_at", "data_cutoff")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must be timezone-aware")
        return value

    @field_validator("unknown_fields")
    @classmethod
    def _sorted_unique(cls, values: list[str]) -> list[str]:
        return sorted({v.strip() for v in values if v.strip()})

    @model_validator(mode="after")
    def _no_orphan_event_refs(self) -> "EvidenceBundleV2":
        known = {s.source_id for s in self.filings} | {s.source_id for s in self.source_manifest}
        for event in (*self.company_releases, *self.investor_materials, *self.material_events):
            if event.source_id not in known:
                raise ValueError(f"orphan material-event source_id {event.source_id!r}")
        return self

    @field_validator("fundamentals", "fundamental_changes", "balance_sheet", "cashflow",
                      "price_context", "earnings", "data_quality", mode="after")
    @classmethod
    def _no_interpretive_language(cls, value: dict[str, Any]) -> dict[str, Any]:
        blob = str(value).lower()
        for banned in BANNED_INTERPRETIVE_SUBSTRINGS:
            if banned in blob:
                raise ValueError(f"interpretive language {banned!r} is not permitted in a D2 bundle")
        return value
