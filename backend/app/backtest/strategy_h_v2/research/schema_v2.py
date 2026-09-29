"""D3.2R Research Output Schema V2 (`HResearchInterpretationV2`).

A minimal, additive extension of `schema.py`'s `HResearchInterpretationV1` - not a replacement.
`schema.py` is untouched by this stage and stays the schema every existing D3/D3.1/D3.2 immutable
output validates against; no past result is migrated to this shape (D3.2R brief §18).

Two additive changes over V1, both minimal:

1. `ClaimV2.evidence_ids` - an explicit compound-claim fallback. The atomic contract (D3.2R brief
   §7: one claim, one evidence chunk) stays the default and preferred path via `evidence_id`; a
   claim that genuinely cannot be split cites `evidence_ids` (>= 2, so a single citation always
   uses the singular field) instead, and each one is validated exactly like `evidence_id` was.
   D3.2's own `validation_v2.is_compound_claim` heuristic is NOT wired in here as a generation-time
   reject: D3.2 §J.6 found it flags mostly noun-phrase lists, not genuine compound propositions, and
   a validator built on a heuristic proven unreliable on real output would manufacture exactly the
   kind of false-positive repair pressure D3.1 §I.2 already found once with `fair value`. Whether a
   claim needed `evidence_ids` is still checked offline, by `validation_v2`, against live D3.3
   output - not gated at generation time on a heuristic guess.
2. `FutureBusinessItemV2.missing_evidence` - an optional, free-text list the model uses to state what
   a *higher* stage would have needed, operationalizing the conservative-stage rule (brief §5)
   without changing the stage floor itself. Never validated against the flags (that would just be
   re-deriving the floor from the model's own prose) - it exists so the model's own account of what
   it did not have is on the record, the same way `evidence_conflicts` keeps a disagreement on the
   record instead of silently resolving it.

A third change is a fix, not an addition: every text field's banned-language check now runs
`validation_v2.classify_investment_language` (D3.2's SAFE/VIOLATION/UNCLASSIFIED classifier)
instead of `schema.py`'s bare `\bfair value\b` regex. This was found necessary while writing this
file's own tests: `schema._banned_language_check`, imported unchanged, rejects all three of D3.1
§I.2's real GAAP sentences exactly as before - D3.2 fixed this false positive only in the offline
*audit* (`validation_v2.py`), never in the live schema check every generation and repair attempt
actually runs against. Reusing `_banned_language_check` here would have carried the original bug
straight into V2 while this stage's own point is to stop it recurring. Because that classifier is
finer-grained than a single field validator, `CatalystCandidateV2`, `RiskItemV2`,
`InvalidationCandidateV2` and `EvidenceConflictV2` are mirrored here too (not reused from `schema.py`
as originally planned) so the fix is not applied inconsistently to only some of the fields that
carry filing prose.

The Future Business stage floor (`FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS`) and every enum are imported
from `schema.py` unchanged - this stage repairs the research prompt/output contract, not the frozen
ontology (D3.2R brief §0, §2).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.backtest.strategy_h_v2.research.schema import (
    FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS,
    ClaimType,
    CompetitiveStatus,
    Confidence,
    ConflictResolution,
    FutureBusinessStage,
    GrowthDurabilityEvidence,
    GrowthDurabilityState,
    ResearchCompleteness,
    RiskCategory,
    _check_source_ids,
)
from app.backtest.strategy_h_v2.research.validation_v2 import LanguageVerdict, classify_investment_language


def _no_investment_language_v2(value: str) -> str:
    """Replaces `schema._banned_language_check` for every V2 text field. Blocks only what
    `classify_investment_language` resolves to an actual `VIOLATION` - an explicit stock-valuation
    opinion, or the unambiguous analyst/strategy jargon list (`price target`, `strong buy`, ...).
    An `UNCLASSIFIED` match (ambiguous, no safe or opinion marker either) is treated as SAFE here,
    consistent with D3.2 §G's own declared default: in Batch 2's real evidence `fair value` occurred
    552 times across 3 candidates' own filings and was a genuine investment opinion 0 times, so
    defaulting to VIOLATION would recreate the exact false-positive class this stage exists to close.
    An `UNCLASSIFIED` match is still available for offline review via `validation_v2` against real
    D3.3 output - this function is the generation-time gate, not the only check.
    """
    for match in classify_investment_language(value):
        if match.verdict == LanguageVerdict.VIOLATION:
            raise ValueError(
                f"prohibited investment language {match.term!r} in D3 output (context: "
                f"{match.context!r})"
            )
    return value

SCHEMA_VERSION = "h_research_interpretation_v2"
#: The D0 architecture contract (Quant/AI/Decision layer separation) is unchanged by this stage -
#: same value as `schema.CONTRACT_VERSION`, restated rather than imported so a future change to one
#: does not silently drag the other along without a deliberate decision.
CONTRACT_VERSION = "h_v2_d0_v1"

#: Re-exported so callers that only need the V2 module do not also have to import from `schema.py`
#: for enums that did not change.
__all__ = [
    "SCHEMA_VERSION", "CONTRACT_VERSION", "ClaimV2", "BusinessModelV2",
    "FundamentalChangeInterpretationV2", "GrowthDurabilityV2", "FutureBusinessItemV2",
    "CompetitiveDimensionV2", "ManagementExecutionItemV2", "WhyNowCandidateV2",
    "CatalystCandidateV2", "RiskItemV2", "InvalidationCandidateV2", "EvidenceConflictV2",
    "HResearchInterpretationV2", "cited_source_ids_v2",
]


class ClaimV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    claim_type: ClaimType
    source_id: str | None = None
    evidence_id: str | None = None
    evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Compound-claim fallback ONLY. Leave empty and use source_id/evidence_id for the "
            "normal, preferred atomic case (one claim, one fact, one evidence chunk). Use this "
            "field, with source_id/evidence_id left null, only when the claim genuinely cannot be "
            "split into separate atomic claims - and then cite EVERY evidence chunk each part of "
            "the claim actually depends on, at least two."
        ),
    )
    confidence: Confidence

    @field_validator("text")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        return _no_investment_language_v2(value)

    @model_validator(mode="after")
    def _exactly_one_citation_form(self) -> "ClaimV2":
        """UNKNOWN needs neither. Otherwise exactly one of the two citation forms - never both
        (an ambiguous claim that cites one way AND the other), never neither (an uncited material
        claim), and never `evidence_ids` with fewer than 2 (that is just the atomic case wearing
        the compound field - use `evidence_id` instead, so "this claim used the fallback form" is
        an unambiguous signal, not something a reader has to infer from a list's length)."""
        if self.claim_type == ClaimType.UNKNOWN:
            return self
        atomic = self.source_id is not None or self.evidence_id is not None
        compound = len(self.evidence_ids) > 0
        if atomic and compound:
            raise ValueError(
                f"claim_type={self.claim_type.value} uses both evidence_id and evidence_ids - "
                "use exactly one citation form"
            )
        if not atomic and not compound:
            raise ValueError(
                f"claim_type={self.claim_type.value} requires source_id+evidence_id (atomic) or "
                "evidence_ids (compound fallback) - only UNKNOWN claims may omit a citation"
            )
        if atomic and (self.source_id is None or self.evidence_id is None):
            raise ValueError(
                f"claim_type={self.claim_type.value} requires BOTH source_id and evidence_id in "
                "the atomic form"
            )
        if compound and len(self.evidence_ids) < 2:
            raise ValueError(
                "evidence_ids is a compound-claim fallback and must cite at least 2 chunks - a "
                "single citation belongs in evidence_id, not a 1-element evidence_ids list"
            )
        return self

    @model_validator(mode="after")
    def _citations_known_and_consistent(self, info: ValidationInfo) -> "ClaimV2":
        valid_sources = (info.context or {}).get("valid_source_ids")
        valid_evidence = (info.context or {}).get("valid_evidence_ids")
        if self.source_id is not None and valid_sources is not None and self.source_id not in valid_sources:
            raise ValueError(f"orphan source_id {self.source_id!r} not present in the input package")
        if self.evidence_id is not None:
            if self.source_id is not None and not self.evidence_id.startswith(f"{self.source_id}:"):
                raise ValueError(
                    f"evidence_id {self.evidence_id!r} does not belong to source {self.source_id!r}"
                )
            if valid_evidence is not None and self.evidence_id not in valid_evidence:
                raise ValueError(f"unknown evidence_id {self.evidence_id!r}")
        for eid in self.evidence_ids:
            if valid_evidence is not None and eid not in valid_evidence:
                raise ValueError(f"unknown evidence_id {eid!r} in evidence_ids")
        return self

    def cited_source_ids(self) -> set[str]:
        if self.source_id is not None:
            return {self.source_id}
        return {eid.split(":CHUNK:")[0] for eid in self.evidence_ids if ":CHUNK:" in eid}


class BusinessModelV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revenue_drivers: list[ClaimV2] = Field(default_factory=list)
    segments: list[ClaimV2] = Field(default_factory=list)
    customer_types: list[ClaimV2] = Field(default_factory=list)
    geography: list[ClaimV2] = Field(default_factory=list)
    cyclicality: ClaimV2 | None = None
    key_dependencies: list[ClaimV2] = Field(default_factory=list)


class FundamentalChangeInterpretationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    code_owned_state: str = Field(
        description=(
            "The bare state token copied verbatim from the FACTS block's "
            "fundamental_changes[metric].state (for example 'IMPROVING'). Not a summary, not the "
            "whole object, and never with current_value or confidence appended."
        ),
    )
    explanation: list[ClaimV2] = Field(default_factory=list)
    durability_relevant: bool


class GrowthDurabilityV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: GrowthDurabilityState
    rationale: list[ClaimV2] = Field(default_factory=list)
    evidence_checklist: GrowthDurabilityEvidence


class FutureBusinessItemV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    stage: FutureBusinessStage
    evidence_strength: Confidence
    current_revenue_evidence: bool
    order_backlog_evidence: bool
    customer_evidence: bool
    capacity_evidence: bool
    margin_evidence: bool
    missing_evidence: list[str] = Field(
        default_factory=list,
        description=(
            "What a HIGHER stage than the one chosen would have needed and does not have yet (e.g. "
            "'REVENUE', 'BACKLOG', 'A NAMED CUSTOMER'). Not validated against the flags above - it "
            "is the model's own record of the conservative-stage call (brief §5), kept on the "
            "record rather than left implicit in a stage number alone."
        ),
    )
    claims: list[ClaimV2] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stage_matches_evidence_floor(self) -> "FutureBusinessItemV2":
        """Restates `schema.FutureBusinessItem`'s own check, unchanged - the floor table is
        imported, not redefined, so this stage cannot loosen it even by accident."""
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
    def _sources_are_source_ids(self, info: ValidationInfo) -> "FutureBusinessItemV2":
        _check_source_ids(self.sources, info, f"future_business item {self.name!r}")
        return self


class CompetitiveDimensionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    status: CompetitiveStatus
    claims: list[ClaimV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def _supported_needs_claims(self) -> "CompetitiveDimensionV2":
        if self.status == CompetitiveStatus.SUPPORTED and not self.claims:
            raise ValueError("status=SUPPORTED requires at least one cited claim")
        return self


class ManagementExecutionItemV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    claims: list[ClaimV2] = Field(default_factory=list)


class WhyNowCandidateV2(BaseModel):
    """`why_research_now`, never `why_buy_now` - see `schema.WhyNowCandidate`'s own docstring."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    reasons: list[ClaimV2] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        return _no_investment_language_v2(value)


class CatalystCandidateV2(BaseModel):
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
        return _no_investment_language_v2(value)

    @field_validator("sources")
    @classmethod
    def _requires_at_least_one_source(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a catalyst candidate must cite at least one source (D3 brief §6/§14)")
        return value

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "CatalystCandidateV2":
        _check_source_ids(self.sources, info, f"catalyst {self.type!r}")
        return self


class RiskItemV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RiskCategory
    description: str
    sources: list[str]

    @field_validator("description")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        """`schema.RiskItem` never had this check - added here for consistency now that every
        other filing-prose field in this schema runs the same classifier (an incidental fix, not
        this stage's main target; noted in `H_V2_D3_2R_RESEARCH_CONTRACT_ALIGNMENT_V1.md`)."""
        return _no_investment_language_v2(value)

    @field_validator("sources")
    @classmethod
    def _requires_at_least_one_source(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a risk item must be evidence-supported, not a generic template entry")
        return value

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "RiskItemV2":
        _check_source_ids(self.sources, info, f"risk {self.category.value}")
        return self


class InvalidationCandidateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    sources: list[str]

    @field_validator("description")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        """See `RiskItemV2._no_banned_language` - same incidental consistency fix."""
        return _no_investment_language_v2(value)

    @field_validator("sources")
    @classmethod
    def _requires_at_least_one_source(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("an invalidation candidate must cite the evidence that would break the thesis")
        return value

    @model_validator(mode="after")
    def _sources_are_source_ids(self, info: ValidationInfo) -> "InvalidationCandidateV2":
        _check_source_ids(self.sources, info, "invalidation candidate")
        return self


class EvidenceConflictV2(BaseModel):
    """See `schema.EvidenceConflictV1`'s own docstring for why this structure exists (D3.1 §2.2) -
    mirrored here only to route `topic`/`description` through the V2 language classifier."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    evidence_ids: list[str]
    description: str
    resolution_status: ConflictResolution
    confidence: Confidence

    @field_validator("topic", "description")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        return _no_investment_language_v2(value)

    @field_validator("evidence_ids")
    @classmethod
    def _needs_two_sides(cls, value: list[str]) -> list[str]:
        if len(value) < 2:
            raise ValueError(
                "a conflict must cite at least two evidence chunks - the two sides that disagree"
            )
        return value

    @model_validator(mode="after")
    def _evidence_known(self, info: ValidationInfo) -> "EvidenceConflictV2":
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


def _iter_claims_v2(model: BaseModel) -> list[ClaimV2]:
    found: list[ClaimV2] = []
    for value in model.__dict__.values():
        if isinstance(value, ClaimV2):
            found.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ClaimV2):
                    found.append(item)
                elif isinstance(item, BaseModel):
                    found.extend(_iter_claims_v2(item))
        elif isinstance(value, BaseModel):
            found.extend(_iter_claims_v2(value))
    return found


def cited_source_ids_v2(output: "HResearchInterpretationV2") -> set[str]:
    cited = {sid for claim in _iter_claims_v2(output) for sid in claim.cited_source_ids()}
    for candidate in (*output.catalyst_candidates, *output.risks, *output.invalidation_candidates):
        cited.update(candidate.sources)
    for conflict in output.evidence_conflicts:
        cited.update(conflict.cited_source_ids())
    return cited


class HResearchInterpretationV2(BaseModel):
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

    business_model: BusinessModelV2
    fundamental_change: list[FundamentalChangeInterpretationV2] = Field(default_factory=list)
    growth_durability: GrowthDurabilityV2
    future_business: list[FutureBusinessItemV2] = Field(default_factory=list)
    competitive_position: list[CompetitiveDimensionV2] = Field(default_factory=list)
    management_execution: list[ManagementExecutionItemV2] = Field(default_factory=list)
    catalyst_candidates: list[CatalystCandidateV2] = Field(default_factory=list)
    why_now_candidate: WhyNowCandidateV2
    risks: list[RiskItemV2] = Field(default_factory=list)
    invalidation_candidates: list[InvalidationCandidateV2] = Field(default_factory=list)
    evidence_conflicts: list[EvidenceConflictV2] = Field(default_factory=list)
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
    def _sources_cover_every_cited_claim(self) -> "HResearchInterpretationV2":
        missing = cited_source_ids_v2(self) - set(self.sources)
        if missing:
            raise ValueError(
                f"sources[] is missing {sorted(missing)} even though claims cite them - "
                "the top-level sources list must cover every citation in the document"
            )
        return self
