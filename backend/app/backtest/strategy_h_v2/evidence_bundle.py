"""H-V2 D1 Candidate Evidence Stub.

D1's output is a subset of D0's Candidate Evidence Bundle (§G of
`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md`): everything Quant/code can fill in today. The five
AI-only fields (`future_business`, `catalysts`, `expectation_gap`, `competitive_position`,
`thesis`) are always present but fixed at `NOT_RESEARCHED` - D1 never calls an AI and must never
fabricate a placeholder opinion in their place.

Mirrors the existing Strategy A/E research contract's conventions
(`app.research.domain.GPTResearchResult`): `extra="forbid"`, timezone-aware timestamps, and an
explicit `unknown_fields` list, so a later D3 AI Research Engine can extend this shape rather than
replace it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "h_v2_candidate_v1"
CONTRACT_VERSION = "h_v2_d0_v1"
NOT_RESEARCHED = "NOT_RESEARCHED"


class NotResearched(StrEnum):
    NOT_RESEARCHED = "NOT_RESEARCHED"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must be timezone-aware")
    return value


class CandidateEvidenceStub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    contract_version: str = Field(default=CONTRACT_VERSION)
    run_id: str
    generated_at: datetime
    data_cutoff: datetime

    ticker: str
    identity: dict[str, Any]
    market: dict[str, Any]
    fundamentals: dict[str, Any]
    fundamental_changes: dict[str, Any]
    balance_sheet: dict[str, Any]
    cashflow: dict[str, Any]
    price_context: dict[str, Any]
    earnings: dict[str, Any]
    eligibility: dict[str, Any]
    research_priority: dict[str, Any]
    data_quality: dict[str, Any]
    unknown_fields: list[str] = Field(default_factory=list)

    future_business: NotResearched = NotResearched.NOT_RESEARCHED
    catalysts: NotResearched = NotResearched.NOT_RESEARCHED
    expectation_gap: NotResearched = NotResearched.NOT_RESEARCHED
    competitive_position: NotResearched = NotResearched.NOT_RESEARCHED
    thesis: NotResearched = NotResearched.NOT_RESEARCHED

    @field_validator("generated_at", "data_cutoff")
    @classmethod
    def _tz_aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("unknown_fields")
    @classmethod
    def _sorted_unique(cls, values: list[str]) -> list[str]:
        return sorted({v.strip() for v in values if v.strip()})
