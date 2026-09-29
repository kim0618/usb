"""D3 Research Interpretation schema (`HResearchInterpretationV1`).

Every material claim carries `claim_type` (FACT/INTERPRETATION/INFERENCE/UNKNOWN) and, unless it is
`UNKNOWN`, a `source_id` pointing back into the D2.1 evidence the claim came from - enforced by the
`Claim` model itself, not left to convention. No field in this schema can hold an investment
decision, a fair value, or a price target: those fields simply do not exist here (D0's
`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md` §Q's separation of Quant/AI/Decision layers, applied
one layer further - D3 is Research, not Decision).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

SCHEMA_VERSION = "h_research_interpretation_v1"
CONTRACT_VERSION = "h_v2_d0_v1"

#: Phrases D3 must never produce, anywhere in any text field - a decision, valuation, or price
#: conclusion is D4/D5's job, never D3's (brief §0/§23/§24).
#:
#: Deliberately unambiguous, whole-phrase matches only. An earlier version banned the bare words
#: "approve"/"reject", which matches as a substring of completely ordinary filing language - "the
#: Board of Directors approved a share repurchase", "shareholders rejected the proposal" - and a
#: bare "buy"/"sell" similarly false-positives on a company's own business description ("customers
#: buy replacement parts", "the company sells HVAC equipment"). Both were caught on the real D3
#: pilot: 11 of 12 candidates needed at least one schema-repair round, and every one of the traced
#: causes was this false positive, not an actual investment-decision leak. Fixed to whole-word/
#: whole-phrase regex matches on terms that are not plausible business-fact vocabulary.
BANNED_INVESTMENT_LANGUAGE_PATTERNS = (
    r"\bprice target\b", r"\bfair value\b", r"\bentry zone\b", r"\bentry\s*1\b", r"\bentry\s*2\b",
    r"\btp\s*1\b", r"\btp\s*2\b", r"\bundervalued\b", r"\bovervalued\b",
    r"\bexpectation gap (?:is )?positive\b", r"\bexpectation gap (?:is )?negative\b",
    r"\bwe recommend\b", r"\bstrong buy\b", r"\bstrong sell\b", r"\bbuy rating\b",
    r"\bsell rating\b", r"\bprice objective\b",
)


class ClaimType(StrEnum):
    FACT = "FACT"
    """A statement directly reported by a source (e.g. "backlog increased from $X to $Y")."""
    INTERPRETATION = "INTERPRETATION"
    """A reading of what a fact suggests, still tied to specific evidence."""
    INFERENCE = "INFERENCE"
    """A lower-certainty extrapolation beyond what the evidence directly states."""
    UNKNOWN = "UNKNOWN"
    """The evidence does not support an answer. Never omitted in favor of a guess."""


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class CompetitiveStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class FutureBusinessStage(StrEnum):
    STORY = "STORY"
    EARLY_EVIDENCE = "EARLY_EVIDENCE"
    COMMERCIALIZING = "COMMERCIALIZING"
    REAL_BUSINESS = "REAL_BUSINESS"
    MATURE = "MATURE"
    UNKNOWN = "UNKNOWN"


class GrowthDurabilityState(StrEnum):
    DURABLE = "DURABLE"
    POSSIBLY_DURABLE = "POSSIBLY_DURABLE"
    TEMPORARY = "TEMPORARY"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class RiskCategory(StrEnum):
    BUSINESS = "BUSINESS"
    CUSTOMER = "CUSTOMER"
    COMPETITION = "COMPETITION"
    EXECUTION = "EXECUTION"
    BALANCE_SHEET = "BALANCE_SHEET"
    DILUTION = "DILUTION"
    REGULATION = "REGULATION"
    LITIGATION = "LITIGATION"
    SUPPLY_CHAIN = "SUPPLY_CHAIN"
    CYCLICALITY = "CYCLICALITY"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class ResearchCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


_BANNED_LANGUAGE_RE = re.compile("|".join(BANNED_INVESTMENT_LANGUAGE_PATTERNS), re.IGNORECASE)


def _banned_language_check(text: str) -> None:
    match = _BANNED_LANGUAGE_RE.search(text)
    if match:
        raise ValueError(f"prohibited investment language {match.group(0)!r} in D3 output")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    claim_type: ClaimType
    source_id: str | None
    evidence_id: str | None
    confidence: Confidence

    @field_validator("text")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        _banned_language_check(value)
        return value

    @model_validator(mode="after")
    def _material_claim_needs_a_source(self) -> "Claim":
        """D3.1 §2.1/R4: a material (non-UNKNOWN) claim needs BOTH a source and the specific
        evidence chunk it came from. D3 only required `source_id`, which let a claim cite a real
        document with an invented chunk pointer - one such case really occurred in the D3 pilot
        (ACA/V1 cited `...:EX-99.1:CHUNK:13` on a 12-chunk document)."""
        if self.claim_type == ClaimType.UNKNOWN:
            return self
        if self.source_id is None:
            raise ValueError(
                f"claim_type={self.claim_type.value} requires source_id - "
                "only UNKNOWN claims may omit a source"
            )
        if self.evidence_id is None:
            raise ValueError(
                f"claim_type={self.claim_type.value} requires evidence_id - "
                "only UNKNOWN claims may omit the specific evidence chunk"
            )
        return self

    @model_validator(mode="after")
    def _source_known(self, info: ValidationInfo) -> "Claim":
        valid_ids = (info.context or {}).get("valid_source_ids")
        if valid_ids is not None and self.source_id is not None and self.source_id not in valid_ids:
            raise ValueError(f"orphan source_id {self.source_id!r} not present in the input package")
        return self

    @model_validator(mode="after")
    def _evidence_known_and_consistent(self, info: ValidationInfo) -> "Claim":
        """Rejects a nonexistent chunk, a chunk belonging to another company's package (the valid
        set is built per-candidate), and a source/evidence pair that disagree with each other."""
        if self.evidence_id is None:
            return self
        if self.source_id is not None and not self.evidence_id.startswith(f"{self.source_id}:"):
            raise ValueError(
                f"evidence_id {self.evidence_id!r} does not belong to source {self.source_id!r}"
            )
        valid_evidence = (info.context or {}).get("valid_evidence_ids")
        if valid_evidence is not None and self.evidence_id not in valid_evidence:
            raise ValueError(
                f"unknown evidence_id {self.evidence_id!r} - not an evidence chunk in this "
                "candidate's input package"
            )
        return self


def _check_source_ids(values: list[str], info: ValidationInfo, what: str) -> None:
    """The `sources` lists on catalysts, risks, invalidations and future-business items were the
    one citation path with no integrity check at all: `Claim` was validated against the package but
    these plain `list[str]` fields were not. The D3.1 regression caught the model filling them with
    evidence chunk IDs instead of source IDs, which then failed far away in the top-level coverage
    check with an error that pointed at the wrong thing (D3.1 §2.1, "wrong source/evidence
    relationship")."""
    valid_ids = (info.context or {}).get("valid_source_ids")
    if valid_ids is None:
        return
    for value in values:
        if value in valid_ids:
            continue
        if ":CHUNK:" in value:
            raise ValueError(
                f"{what} cites {value!r}, which is an evidence_id - this field takes source_id "
                f"values (the part before ':CHUNK:'), not chunk IDs"
            )
        raise ValueError(f"{what} cites orphan source_id {value!r} not present in the input package")


class BusinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revenue_drivers: list[Claim] = Field(default_factory=list)
    segments: list[Claim] = Field(default_factory=list)
    customer_types: list[Claim] = Field(default_factory=list)
    geography: list[Claim] = Field(default_factory=list)
    cyclicality: Claim | None = None
    key_dependencies: list[Claim] = Field(default_factory=list)


class FundamentalChangeInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    code_owned_state: str = Field(
        description=(
            "The bare state token copied verbatim from the FACTS block's "
            "fundamental_changes[metric].state (for example 'IMPROVING'). Not a summary, not the "
            "whole object, and never with current_value or confidence appended."
        ),
    )
    """Cross-checked against the actual input package by `validate.py`, not by this model alone (a
    schema-level check cannot see the input package).

    The `description=` above is not decoration: the prompt embeds this model's JSON Schema, and a
    plain docstring never reaches it. Without it the D3.1 regression showed the model writing the
    whole rendered object ("state=IMPROVING, current_value=0.236, confidence=MEDIUM") into this
    field and failing the cross-check - a formatting failure that looked like a numeric mutation.
    """
    explanation: list[Claim] = Field(default_factory=list)
    durability_relevant: bool


class GrowthDurabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recurring_revenue: bool | None = None
    orders_backlog: bool | None = None
    customer_diversification: bool | None = None
    capacity: bool | None = None
    contract_duration: bool | None = None
    margin_structure: bool | None = None
    one_off_gains: bool | None = None
    acquisition_effects: bool | None = None


class GrowthDurability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: GrowthDurabilityState
    rationale: list[Claim] = Field(default_factory=list)
    evidence_checklist: GrowthDurabilityEvidence


#: Minimum count of True evidence-checklist flags a `FutureBusinessItem` must carry to claim a
#: given stage - a company cannot be labeled REAL_BUSINESS on zero hard evidence flags. Frozen
#: before any pilot output was read (brief §10-11's "최소 evidence").
FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS: dict[FutureBusinessStage, int] = {
    FutureBusinessStage.STORY: 0,
    FutureBusinessStage.EARLY_EVIDENCE: 1,
    FutureBusinessStage.COMMERCIALIZING: 2,
    FutureBusinessStage.REAL_BUSINESS: 2,
    FutureBusinessStage.MATURE: 2,
    FutureBusinessStage.UNKNOWN: 0,
}


class FutureBusinessItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    stage: FutureBusinessStage
    evidence_strength: Confidence
    current_revenue_evidence: bool
    order_backlog_evidence: bool
    customer_evidence: bool
    capacity_evidence: bool
    margin_evidence: bool
    claims: list[Claim] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stage_matches_evidence_floor(self) -> "FutureBusinessItem":
        flags = (
            self.current_revenue_evidence, self.order_backlog_evidence, self.customer_evidence,
            self.capacity_evidence, self.margin_evidence,
        )
        count = sum(flags)
        minimum = FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS[self.stage]
        if count < minimum:
            raise ValueError(
                f"stage={self.stage.value} requires at least {minimum} evidence flag(s), got {count} "
                "- a future-business item cannot be staged above what its own evidence flags support"
            )
        if self.stage in (FutureBusinessStage.REAL_BUSINESS, FutureBusinessStage.MATURE) and not (
            self.current_revenue_evidence or self.order_backlog_evidence
        ):
            raise ValueError(
                f"stage={self.stage.value} requires revenue or backlog evidence specifically, "
                "not just any two flags"
            )
        return self

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "FutureBusinessItem":
        _check_source_ids(self.sources, info, f"future_business item {self.name!r}")
        return self


class CompetitiveDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    status: CompetitiveStatus
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def _supported_needs_claims(self) -> "CompetitiveDimension":
        if self.status == CompetitiveStatus.SUPPORTED and not self.claims:
            raise ValueError("status=SUPPORTED requires at least one cited claim")
        return self


class ManagementExecutionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    claims: list[Claim] = Field(default_factory=list)


class CatalystCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    description: str
    expected_time: date | None
    timing_confidence: Confidence
    materiality_candidate: Confidence
    sources: list[str]

    @field_validator("description")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        _banned_language_check(value)
        return value

    @field_validator("sources")
    @classmethod
    def _requires_at_least_one_source(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a catalyst candidate must cite at least one source (D3 brief §6/§14)")
        return value

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "CatalystCandidate":
        _check_source_ids(self.sources, info, f"catalyst {self.type!r}")
        return self


class RiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RiskCategory
    description: str
    sources: list[str]

    @field_validator("sources")
    @classmethod
    def _requires_at_least_one_source(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a risk item must be evidence-supported, not a generic template entry")
        return value

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "RiskItem":
        _check_source_ids(self.sources, info, f"risk {self.category.value}")
        return self


class InvalidationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    sources: list[str]

    @field_validator("sources")
    @classmethod
    def _requires_at_least_one_source(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("an invalidation candidate must cite the evidence that would break the thesis")
        return value

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "InvalidationCandidate":
        _check_source_ids(self.sources, info, "invalidation candidate")
        return self


class WhyNowCandidate(BaseModel):
    """`why_research_now`, never `why_buy_now` - see the field docstring and
    `H_V2_D3_AI_RESEARCH_ENGINE_V1.md` §L for the distinction this model exists to enforce."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    reasons: list[Claim] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        _banned_language_check(value)
        return value


class ConflictResolution(StrEnum):
    """Whether the official record itself settles the disagreement."""

    RESOLVED = "RESOLVED"
    """A later official source supersedes the earlier one and says so."""
    UNRESOLVED = "UNRESOLVED"
    """Both sources stand; the record does not reconcile them."""
    UNKNOWN = "UNKNOWN"
    """It cannot be determined from the collected evidence which reading holds."""


class EvidenceConflictV1(BaseModel):
    """A material disagreement *between official sources* (D3.1 §2.2).

    D3 had no way to say "the 10-K and the 8-K disagree", so the model's only options were to pick
    one silently or drop the topic. Both destroy the audit trail. An UNRESOLVED conflict is a
    legitimate, preservable research outcome - it is not a failure to be repaired away.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str
    evidence_ids: list[str]
    description: str
    resolution_status: ConflictResolution
    confidence: Confidence

    @field_validator("topic", "description")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        _banned_language_check(value)
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _needs_two_sides(cls, value: list[str]) -> list[str]:
        if len(value) < 2:
            raise ValueError(
                "a conflict must cite at least two evidence chunks - the two sides that disagree"
            )
        return value

    @model_validator(mode="after")
    def _evidence_known(self, info: ValidationInfo) -> "EvidenceConflictV1":
        valid_evidence = (info.context or {}).get("valid_evidence_ids")
        if valid_evidence is None:
            return self
        unknown = [eid for eid in self.evidence_ids if eid not in valid_evidence]
        if unknown:
            raise ValueError(
                f"unknown evidence_id {sorted(unknown)} in conflict {self.topic!r} - "
                "not evidence chunks in this candidate's input package"
            )
        return self

    def cited_source_ids(self) -> set[str]:
        return {eid.split(":CHUNK:")[0] for eid in self.evidence_ids if ":CHUNK:" in eid}


def _iter_claims(model: BaseModel) -> list[Claim]:
    found: list[Claim] = []
    for value in model.__dict__.values():
        if isinstance(value, Claim):
            found.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Claim):
                    found.append(item)
                elif isinstance(item, BaseModel):
                    found.extend(_iter_claims(item))
        elif isinstance(value, BaseModel):
            found.extend(_iter_claims(value))
    return found


class HResearchInterpretationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    contract_version: str = Field(default=CONTRACT_VERSION)
    research_id: str
    version: int = Field(ge=1)
    company_id: str
    ticker: str
    decision_time: datetime
    input_package_id: str
    input_package_checksum: str
    model: str
    model_version: str | None
    prompt_version: str
    created_at: datetime

    business_model: BusinessModel
    fundamental_change: list[FundamentalChangeInterpretation] = Field(default_factory=list)
    growth_durability: GrowthDurability
    future_business: list[FutureBusinessItem] = Field(default_factory=list)
    competitive_position: list[CompetitiveDimension] = Field(default_factory=list)
    management_execution: list[ManagementExecutionItem] = Field(default_factory=list)
    catalyst_candidates: list[CatalystCandidate] = Field(default_factory=list)
    why_now_candidate: WhyNowCandidate
    risks: list[RiskItem] = Field(default_factory=list)
    invalidation_candidates: list[InvalidationCandidate] = Field(default_factory=list)
    evidence_conflicts: list[EvidenceConflictV1] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    research_completeness: ResearchCompleteness

    @field_validator("decision_time", "created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _sources_cover_every_cited_claim(self) -> "HResearchInterpretationV1":
        cited = {claim.source_id for claim in _iter_claims(self) if claim.source_id}
        for candidate in (*self.catalyst_candidates, *self.risks, *self.invalidation_candidates):
            cited.update(candidate.sources)
        for conflict in self.evidence_conflicts:
            cited.update(conflict.cited_source_ids())
        missing = cited - set(self.sources)
        if missing:
            raise ValueError(
                f"sources[] is missing {sorted(missing)} even though claims cite them - "
                "the top-level sources list must cover every citation in the document"
            )
        return self
