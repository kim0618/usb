"""`HExpectationGapAnalysisV1` - D4's AI output schema (brief §28).

Reuses D3's Validation Contract V2 wholesale: `ClaimV2` is imported unchanged, not re-specified, so
every citation rule D3.3 proved on real output (atomic-by-default, compound fallback needing >= 2
ids, evidence_id must belong to its source, orphan rejection) applies to D4 claims identically.
Brief §24's rule - "D4가 새로 느슨한 claim schema를 만들지 않는다" - is enforced by importing rather
than by intending.

The fields brief §28 bans (`decision`, `recommendation`, `APPROVE`/`WATCH`/`REJECT`, `fair_value`,
`price_target`, `entry`, `exit`) are impossible here twice over: `extra="forbid"` rejects any field
not declared, and `validate.py` checks the banned names FIRST so the failure says which prohibited
field was attempted instead of a generic "extra inputs are not permitted". D3.1 §I.2's lesson was
that an unspecific rejection message produces unspecific repairs.

One D3-facing rule runs through this whole schema: D4 may not restate a D3 finding in its own
words. Where D4 needs a D3 conclusion it copies the bare token and code checks the copy against the
D3 input (`d3_growth_durability_state`, `d3_future_business_max_stage`) - the same
`code_owned_state` device `FundamentalChangeInterpretationV2` already uses against the FACTS block,
applied one stage later for the same reason.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.backtest.strategy_h_v2.expectation.gap_contract import (
    CONTRACT_VERSION,
    GAP_CONTRACT_VERSION,
    ContractRule,
    D6ApprovePrecondition,
    ExpectationGapState,
    GuidanceState,
    Materiality,
    PricedInAssessment,
    RealizationStatus,
    EvidenceAvailability,
    ResultVsCompanyGuidance,
    POSITIVE_STATES,
)
from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    ConflictOrigin,
    ManagementSignalDirection,
)
from app.backtest.strategy_h_v2.expectation.d4_inputs import ConflictResolution
from app.backtest.strategy_h_v2.expectation.guidance_arithmetic import GuidanceRange, GuidanceUnit
from app.backtest.strategy_h_v2.research.schema import Confidence
from app.backtest.strategy_h_v2.research.schema_v2 import ClaimV2, _no_investment_language_v2

SCHEMA_VERSION = "h_expectation_gap_analysis_v2"
#: V1, for reading D4.1's stored records. V2 differs from it in one way only: three fields
#: whose values a field validator already restricted are now typed as the enums that restrict
#: them, so the JSON schema shown to the model lists the members. No field was added, removed,
#: renamed or given a new legal value.
SCHEMA_VERSION_V1 = "h_expectation_gap_analysis_v1"

#: Rejected by name, before Pydantic's generic extra-field error, so a repair round is told what it
#: actually did. Every entry is a field brief §28 names, plus the lowercase decision tokens that
#: would carry the same meaning under a different spelling.
BANNED_D4_FIELD_NAMES: frozenset[str] = frozenset({
    "decision", "recommendation", "rating", "action", "verdict",
    "approve", "watch", "reject", "buy", "sell", "hold",
    "fair_value", "fair_value_estimate", "intrinsic_value", "price_target", "target_price",
    "valuation", "valuation_view", "upside", "downside_target",
    "entry", "entry1", "entry2", "exit", "stop", "tp1", "tp2",
    "position_size", "portfolio_weight", "weight", "conviction_score", "expectation_gap_score",
})


class FundamentalRealitySummaryV1(BaseModel):
    """The REALITY side of the comparison, carried forward from D3 - never re-derived.

    `d3_growth_durability_state` and `d3_future_business_max_stage` are bare tokens copied from the
    immutable D3 output; `validate.py` rejects a copy that does not match it. That is what stops
    D4 from "re-reading" a D3 conclusion it finds inconvenient (brief §3).
    """

    model_config = ConfigDict(extra="forbid")

    d3_growth_durability_state: str
    d3_future_business_max_stage: str
    improvement_claims: list[ClaimV2] = Field(default_factory=list)
    deterioration_claims: list[ClaimV2] = Field(default_factory=list)
    durability_basis: list[ClaimV2] = Field(default_factory=list)

    @field_validator("d3_growth_durability_state", "d3_future_business_max_stage")
    @classmethod
    def _bare_token(cls, value: str) -> str:
        if value != value.strip() or " " in value:
            raise ValueError(
                f"{value!r} must be the bare state token copied from the D3 output, not a phrase"
            )
        return value


class GuidanceMetricAssessmentV1(BaseModel):
    """One guided metric, its two ranges as the sources state them, and the AI's state for it.

    The AI transcribes the numbers; `validate.py` runs `classify_range_movement` over them and
    rejects a `state` that contradicts the arithmetic. So a RAISED here is never the model's
    adjective - it is a comparison code performed, which the model had to agree with.
    """

    model_config = ConfigDict(extra="forbid")

    metric: str
    state: GuidanceState
    period_label: str | None = None
    previous_low: float | None = None
    previous_high: float | None = None
    current_low: float | None = None
    current_high: float | None = None
    unit: GuidanceUnit | None = None
    claims: list[ClaimV2] = Field(default_factory=list)

    @field_validator("metric", "period_label")
    @classmethod
    def _no_banned_language(cls, value: str | None) -> str | None:
        return None if value is None else _no_investment_language_v2(value)

    @model_validator(mode="after")
    def _ranges_are_complete_or_absent(self) -> "GuidanceMetricAssessmentV1":
        for low, high, side in ((self.previous_low, self.previous_high, "previous"),
                                (self.current_low, self.current_high, "current")):
            if (low is None) != (high is None):
                raise ValueError(
                    f"{side} guidance needs both bounds or neither - a half-stated range is how a "
                    "point guidance and a truncated range become indistinguishable"
                )
            if low is not None and high is not None and high < low:
                raise ValueError(f"{side} guidance high {high} is below low {low}")
        if (self.current_low is not None or self.previous_low is not None) and self.unit is None:
            raise ValueError("a numeric guidance range must state its unit")
        if self.state in (GuidanceState.RAISED, GuidanceState.LOWERED, GuidanceState.MIXED) and (
            self.previous_low is None or self.current_low is None
        ):
            raise ValueError(
                f"state={self.state.value} asserts a CHANGE in guidance, which requires both the "
                "previous and the current range - otherwise it is INITIATED or UNKNOWN"
            )
        return self

    def previous_range(self) -> GuidanceRange | None:
        if self.previous_low is None or self.previous_high is None or self.unit is None:
            return None
        return GuidanceRange(self.previous_low, self.previous_high, self.unit)

    def current_range(self) -> GuidanceRange | None:
        if self.current_low is None or self.current_high is None or self.unit is None:
            return None
        return GuidanceRange(self.current_low, self.current_high, self.unit)


class ResultVsGuidanceItemV1(BaseModel):
    """A reported actual against the company's OWN prior guidance (brief §8).

    There is no consensus field here and there will not be one until a consensus provider exists:
    the enum this uses is named `ResultVsCompanyGuidance` precisely so that "beat" - a word about
    analyst expectations - cannot be written in this schema at all.
    """

    model_config = ConfigDict(extra="forbid")

    metric: str
    state: ResultVsCompanyGuidance
    reported_value: float | None = None
    prior_guidance_low: float | None = None
    prior_guidance_high: float | None = None
    unit: GuidanceUnit | None = None
    claims: list[ClaimV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def _comparative_states_need_both_sides(self) -> "ResultVsGuidanceItemV1":
        comparative = {
            ResultVsCompanyGuidance.ABOVE_COMPANY_GUIDANCE,
            ResultVsCompanyGuidance.WITHIN_COMPANY_GUIDANCE,
            ResultVsCompanyGuidance.BELOW_COMPANY_GUIDANCE,
        }
        if self.state in comparative and (
            self.reported_value is None or self.prior_guidance_low is None
            or self.prior_guidance_high is None
        ):
            raise ValueError(
                f"state={self.state.value} is a comparison and requires the reported value and "
                "both prior-guidance bounds - a comparison with one side missing is an assertion"
            )
        return self


class ManagementSignalChangeV1(BaseModel):
    """A target/milestone that moved between two official documents (brief §14).

    `evidence_ids` requires two, because "the language changed" is inherently a statement about two
    documents: one claiming a change while citing a single filing is describing that filing's
    wording, not a change. This is the same two-sided rule `EvidenceConflictV2` applies, for the
    same reason.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str
    direction: ManagementSignalDirection
    """The same seven tokens V1 enforced, now typed as the enum that enforces them.

    V1 declared this `str` and policed it in a field validator, so `content_only_schema()` rendered
    `{"title": "Direction", "type": "string"}` and the model was never shown a member. D4.1 Tier A
    measured the consequence on its first two live candidates: `"INCREASED"`,
    `"QUANTIFIED_AND_EXTENDED_TO_2027"`, and a whole sentence with tonnages in it. Declaring the
    enum changes no legal value - it makes the legal values readable.
    """
    evidence_ids: list[str]
    confidence: Confidence
    claims: list[ClaimV2] = Field(default_factory=list)

    @field_validator("topic")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        return _no_investment_language_v2(value)

    @field_validator("evidence_ids")
    @classmethod
    def _two_documents(cls, value: list[str]) -> list[str]:
        if len(value) < 2:
            raise ValueError(
                "a management expectation CHANGE cites the two documents it changed between - one "
                "citation describes a single filing's wording, not a change"
            )
        return value


class MarketExpectationEvidenceV1(BaseModel):
    """The EXPECTATION side. `consensus_status` and `estimate_revisions_status` are copied from the
    code-owned bundle and checked against it - the model cannot report a consensus that the bundle
    says does not exist, which is brief §18's prohibition made structural rather than advisory."""

    model_config = ConfigDict(extra="forbid")

    overall_guidance_state: GuidanceState
    guidance_assessments: list[GuidanceMetricAssessmentV1] = Field(default_factory=list)
    result_vs_guidance: list[ResultVsGuidanceItemV1] = Field(default_factory=list)
    management_signal_changes: list[ManagementSignalChangeV1] = Field(default_factory=list)
    price_reaction_reading: list[ClaimV2] = Field(default_factory=list)
    pre_event_positioning_reading: list[ClaimV2] = Field(default_factory=list)
    consensus_status: EvidenceAvailability
    """Copied from the code-owned bundle and checked against it by `validate.py`. Typed as the enum
    for the `direction` reason: V1's `str` let the model write any token at all, and the only
    feedback was a mismatch error naming a value it had not been offered."""
    estimate_revisions_status: EvidenceAvailability

    @model_validator(mode="after")
    def _overall_state_follows_metrics(self) -> "MarketExpectationEvidenceV1":
        states = {a.state for a in self.guidance_assessments}
        if not self.guidance_assessments:
            if self.overall_guidance_state not in (GuidanceState.NOT_PROVIDED,
                                                   GuidanceState.UNKNOWN,
                                                   GuidanceState.WITHDRAWN):
                raise ValueError(
                    f"overall_guidance_state={self.overall_guidance_state.value} with no per-metric "
                    "assessment - an overall state must rest on at least one guided metric"
                )
            return self
        definite = states - {GuidanceState.UNKNOWN, GuidanceState.NOT_PROVIDED}
        if len(definite) > 1 and self.overall_guidance_state != GuidanceState.MIXED:
            raise ValueError(
                f"per-metric states {sorted(s.value for s in definite)} disagree, so the overall "
                "state is MIXED - brief §7 forbids collapsing a disagreement into one direction"
            )
        if len(definite) == 1 and self.overall_guidance_state not in (
            definite | {GuidanceState.MIXED}
        ):
            only = next(iter(definite))
            raise ValueError(
                f"the only definite per-metric state is {only.value}, so overall_guidance_state "
                f"cannot be {self.overall_guidance_state.value}"
            )
        return self


class PricedInAssessmentV1(BaseModel):
    """Brief §23. The strongest inference D4 makes, so it carries the heaviest requirements:
    evidence ids, a confidence, and at least one stated limitation whenever it says anything at
    all. `OVER_PRICED_EXPECTATION` is about expectations, never about value - see the enum."""

    model_config = ConfigDict(extra="forbid")

    state: PricedInAssessment
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    claims: list[ClaimV2] = Field(default_factory=list)

    @field_validator("limitations")
    @classmethod
    def _no_banned_language(cls, value: list[str]) -> list[str]:
        for item in value:
            _no_investment_language_v2(item)
        return value

    @model_validator(mode="after")
    def _non_unknown_is_fully_supported(self) -> "PricedInAssessmentV1":
        if self.state == PricedInAssessment.UNKNOWN:
            if self.confidence != Confidence.UNKNOWN:
                raise ValueError(
                    "an UNKNOWN priced-in state cannot carry a confidence - there is nothing to be "
                    "confident about"
                )
            return self
        if not self.evidence_ids:
            raise ValueError(
                f"priced_in_assessment={self.state.value} requires evidence_ids (brief §23) - this "
                "is an inference, and an uncited inference is a guess"
            )
        if self.confidence == Confidence.UNKNOWN:
            raise ValueError("a stated priced-in assessment must carry a real confidence")
        if not self.limitations:
            raise ValueError(
                f"priced_in_assessment={self.state.value} requires at least one explicit limitation "
                "- with no consensus source available, every priced-in reading has one"
            )
        return self


class WhyNowItemV1(BaseModel):
    """Brief §22: why a RE-RATING WINDOW may exist, never why to buy.

    `realization_status` is required because the whole idea depends on it - an already-realized
    event cannot open a window. A `REALIZED` item is still allowed and still useful (it is how D4
    says "this has already happened, so it is not why-now"), it just cannot be counted as one.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    realization_status: RealizationStatus
    expected_window: str | None = None
    claims: list[ClaimV2] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    @field_validator("summary", "expected_window")
    @classmethod
    def _no_banned_language(cls, value: str | None) -> str | None:
        return None if value is None else _no_investment_language_v2(value)

    @model_validator(mode="after")
    def _sourced(self) -> "WhyNowItemV1":
        if not self.sources:
            raise ValueError("a why-now item must cite at least one source (brief §22)")
        if not self.claims:
            raise ValueError("a why-now item must carry at least one cited claim")
        return self


class ExpectationConflictV1(BaseModel):
    """`EvidenceConflictV2` plus the two fields D4 needs and D3 did not: `materiality` (rule C3's
    ceiling keys off it) and `origin` (a conflict carried forward from D3 is not a new D4 finding
    and must not be presented as one). Every V2 rule is restated here unchanged - this extends the
    conflict contract, it does not relax it."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    description: str
    evidence_ids: list[str]
    resolution_status: ConflictResolution
    materiality: Materiality
    origin: ConflictOrigin
    """Carried forward from D3, or found by D4. Same exposure defect as `direction`: V1 typed it
    `str` and checked the two tokens in a validator the model could not read."""
    confidence: Confidence

    @field_validator("topic", "description")
    @classmethod
    def _no_banned_language(cls, value: str) -> str:
        return _no_investment_language_v2(value)

    @field_validator("evidence_ids")
    @classmethod
    def _needs_two_sides(cls, value: list[str]) -> list[str]:
        if len(value) < 2:
            raise ValueError("a conflict must cite at least two evidence ids - the two sides")
        return value

    @model_validator(mode="after")
    def _evidence_known(self, info: ValidationInfo) -> "ExpectationConflictV1":
        valid = (info.context or {}).get("valid_evidence_ids")
        if valid is None:
            return self
        unknown = sorted(e for e in self.evidence_ids if e not in valid)
        if unknown:
            raise ValueError(f"unknown evidence_id {unknown} in conflict {self.topic!r}")
        return self

    def is_material_unresolved(self) -> bool:
        return (self.materiality == Materiality.MATERIAL
                and self.resolution_status == ConflictResolution.UNRESOLVED)


class HExpectationGapAnalysisV1(BaseModel):
    """D4's output. Contains no decision, no valuation, and no price level anyone could trade on.

    The contract-residue fields at the bottom (`applied_contract_rules`, `confidence_ceiling`,
    `d6_approve_precondition`, `wide_positive_deferred_conjunct`) are filled by CODE after the
    model responds, never by the model - they are the record that D4 applied the frozen rules, and
    a model-supplied value there would be the model grading its own homework.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    contract_version: str = Field(default=CONTRACT_VERSION)
    gap_contract_version: str = Field(default=GAP_CONTRACT_VERSION)
    analysis_id: str
    version: int = Field(ge=1)
    candidate_id: str
    ticker: str
    decision_time: datetime

    research_input_id: str
    research_input_checksum: str
    expectation_evidence_id: str
    expectation_evidence_checksum: str

    model_name: str
    model_version: str | None
    prompt_version: str
    created_at: datetime

    fundamental_reality_summary: FundamentalRealitySummaryV1
    market_expectation_evidence: MarketExpectationEvidenceV1

    expectation_gap: ExpectationGapState
    expectation_gap_confidence: Confidence
    gap_rationale: list[ClaimV2] = Field(default_factory=list)

    priced_in_assessment: PricedInAssessmentV1
    why_now: list[WhyNowItemV1] = Field(default_factory=list)

    supporting_claims: list[ClaimV2] = Field(default_factory=list)
    conflicts: list[ExpectationConflictV1] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    applied_contract_rules: list[ContractRule] = Field(default_factory=list)
    confidence_ceiling: Confidence = Confidence.HIGH
    d6_approve_precondition: D6ApprovePrecondition = D6ApprovePrecondition.BLOCKED
    wide_positive_deferred_conjunct: str | None = None

    @field_validator("decision_time", "created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must be timezone-aware")
        return value

    @field_validator("limitations")
    @classmethod
    def _no_banned_language(cls, value: list[str]) -> list[str]:
        for item in value:
            _no_investment_language_v2(item)
        return value

    @model_validator(mode="after")
    def _unknown_gap_has_unknown_confidence(self) -> "HExpectationGapAnalysisV1":
        """Rule C5. An UNKNOWN gap with a stated confidence is a contradiction: the confidence
        would be describing a comparison that was not made."""
        if self.expectation_gap == ExpectationGapState.UNKNOWN:
            if self.expectation_gap_confidence != Confidence.UNKNOWN:
                raise ValueError(
                    "expectation_gap=UNKNOWN requires expectation_gap_confidence=UNKNOWN (C5) - "
                    "there is no comparison to be confident about"
                )
        elif self.expectation_gap_confidence == Confidence.UNKNOWN:
            raise ValueError(
                f"expectation_gap={self.expectation_gap.value} states a comparison, so it must "
                "carry a real confidence - use UNKNOWN for both or neither (C5)"
            )
        return self

    @model_validator(mode="after")
    def _stated_gap_is_argued(self) -> "HExpectationGapAnalysisV1":
        if self.expectation_gap != ExpectationGapState.UNKNOWN and not self.gap_rationale:
            raise ValueError(
                f"expectation_gap={self.expectation_gap.value} requires gap_rationale claims - a "
                "gap state with no cited reasoning is a verdict, not an interpretation"
            )
        return self

    @model_validator(mode="after")
    def _positive_gap_needs_a_reality_side(self) -> "HExpectationGapAnalysisV1":
        """Brief §19's first conjunct, structurally: a positive gap claims the market is behind an
        improvement, so an improvement must have been claimed. The SECOND conjunct (evidence the
        market is behind) is rule C1 and lives in `validate.py`, because it needs the code-owned
        evidence bundle this schema deliberately does not carry."""
        if (self.expectation_gap in POSITIVE_STATES
                and not self.fundamental_reality_summary.improvement_claims):
            raise ValueError(
                f"expectation_gap={self.expectation_gap.value} without a single improvement claim - "
                "a positive gap is improvement PLUS evidence the market is behind it, never the "
                "second alone (brief §19)"
            )
        return self

    @model_validator(mode="after")
    def _sources_cover_citations(self) -> "HExpectationGapAnalysisV1":
        cited: set[str] = set()
        for claim in self._all_claims():
            cited |= claim.cited_source_ids()
        for item in self.why_now:
            cited |= set(item.sources)
        missing = sorted(cited - set(self.sources))
        if missing:
            raise ValueError(
                f"sources[] is missing {missing} even though claims cite them - the top-level "
                "sources list must cover every citation in the document"
            )
        return self

    def _all_claims(self) -> list[ClaimV2]:
        reality = self.fundamental_reality_summary
        expectation = self.market_expectation_evidence
        claims: list[ClaimV2] = [
            *reality.improvement_claims, *reality.deterioration_claims, *reality.durability_basis,
            *expectation.price_reaction_reading, *expectation.pre_event_positioning_reading,
            *self.gap_rationale, *self.priced_in_assessment.claims, *self.supporting_claims,
        ]
        for assessment in expectation.guidance_assessments:
            claims.extend(assessment.claims)
        for item in expectation.result_vs_guidance:
            claims.extend(item.claims)
        for change in expectation.management_signal_changes:
            claims.extend(change.claims)
        for item in self.why_now:
            claims.extend(item.claims)
        return claims

    def material_unresolved_conflicts(self) -> list[ExpectationConflictV1]:
        return [c for c in self.conflicts if c.is_material_unresolved()]
