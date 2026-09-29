"""D3.2R schema V2 tests (brief §20). 0 model calls."""

from __future__ import annotations

import pytest
from helpers import utc
from pydantic import ValidationError

from app.backtest.strategy_h_v2.research.schema import (
    ClaimType,
    ConflictResolution,
    Confidence,
    FutureBusinessStage,
    GrowthDurabilityEvidence,
    GrowthDurabilityState,
    ResearchCompleteness,
)
from app.backtest.strategy_h_v2.research.schema_v2 import (
    BusinessModelV2,
    CatalystCandidateV2,
    ClaimV2,
    EvidenceConflictV2,
    FutureBusinessItemV2,
    GrowthDurabilityV2,
    HResearchInterpretationV2,
    InvalidationCandidateV2,
    RiskItemV2,
    WhyNowCandidateV2,
)
from app.backtest.strategy_h_v2.research.schema import RiskCategory
from fixtures.d3_2r_fixtures import (
    FAIR_VALUE_GAAP_SAFE_EXAMPLES,
    FAIR_VALUE_INVESTMENT_OPINION_EXAMPLE,
    FUTURE_BUSINESS_REPAIR_CASES,
)

NOW = utc(2026, 9, 28)
SOURCE = "SEC:1:A-1"
EID0 = f"{SOURCE}:CHUNK:0"
EID1 = f"{SOURCE}:CHUNK:1"
CTX = {"valid_source_ids": {SOURCE}, "valid_evidence_ids": {EID0, EID1}}


def _base_kwargs(**overrides):
    kwargs = dict(
        research_id="R-1", version=1, company_id="1", ticker="ACME", decision_time=NOW,
        input_package_id="P-1", input_package_checksum="deadbeef", model="claude-opus-5-5",
        model_version="claude-opus-5-5", prompt_version="h_v2_d3_research_v2", created_at=NOW,
        business_model=BusinessModelV2(), fundamental_change=[],
        growth_durability=GrowthDurabilityV2(state=GrowthDurabilityState.UNKNOWN,
                                             evidence_checklist=GrowthDurabilityEvidence()),
        future_business=[], competitive_position=[], management_execution=[],
        catalyst_candidates=[], why_now_candidate=WhyNowCandidateV2(summary="worth a look"),
        risks=[], invalidation_candidates=[], open_questions=[], unknown_fields=[],
        sources=[], research_completeness=ResearchCompleteness.INSUFFICIENT_EVIDENCE,
    )
    kwargs.update(overrides)
    return kwargs


# --- atomic claim contract (brief §7) ---------------------------------------------------------

def test_atomic_claim_validates():
    claim = ClaimV2(text="Revenue grew.", claim_type=ClaimType.FACT, source_id=SOURCE,
                    evidence_id=EID0, confidence=Confidence.HIGH)
    ClaimV2.model_validate(claim.model_dump(), context=CTX)


def test_unknown_claim_needs_no_citation():
    ClaimV2(text="Not disclosed.", claim_type=ClaimType.UNKNOWN, confidence=Confidence.UNKNOWN)


def test_material_claim_with_no_citation_at_all_is_rejected():
    with pytest.raises(ValidationError):
        ClaimV2(text="Revenue grew.", claim_type=ClaimType.FACT, confidence=Confidence.HIGH)


def test_both_citation_forms_at_once_rejected():
    with pytest.raises(ValidationError):
        ClaimV2(text="Revenue grew.", claim_type=ClaimType.FACT, source_id=SOURCE,
               evidence_id=EID0, evidence_ids=[EID0, EID1], confidence=Confidence.HIGH)


# --- multiple evidence_ids / compound-claim fallback (brief §8) ----------------------------------

def test_multiple_evidence_ids_supported_for_genuine_compound_claim():
    claim = ClaimV2(text="Two segments moved in opposite directions this quarter.",
                    claim_type=ClaimType.FACT, evidence_ids=[EID0, EID1],
                    confidence=Confidence.MEDIUM)
    ClaimV2.model_validate(claim.model_dump(), context=CTX)


def test_single_element_evidence_ids_rejected_use_singular_field_instead():
    with pytest.raises(ValidationError):
        ClaimV2(text="Revenue grew.", claim_type=ClaimType.FACT, evidence_ids=[EID0],
               confidence=Confidence.HIGH)


def test_compound_under_citation_rejected_when_evidence_id_unknown():
    """A compound claim's evidence_ids are validated exactly like a single evidence_id - an
    unknown chunk in the list fails the same way (brief §8's 'validation FAIL' requirement, applied
    structurally: every cited chunk must exist and belong to this candidate)."""
    claim_dict = ClaimV2(text="Two things happened.", claim_type=ClaimType.FACT,
                        evidence_ids=[EID0, "SEC:1:A-1:CHUNK:99"],
                        confidence=Confidence.MEDIUM).model_dump()
    with pytest.raises(ValidationError):
        ClaimV2.model_validate(claim_dict, context=CTX)


def test_evidence_ids_cross_candidate_rejected():
    claim_dict = ClaimV2(text="Two things happened.", claim_type=ClaimType.FACT,
                        evidence_ids=[EID0, "SEC:9999:OTHER:CHUNK:0"],
                        confidence=Confidence.MEDIUM).model_dump()
    with pytest.raises(ValidationError):
        ClaimV2.model_validate(claim_dict, context=CTX)


# --- Future Business ontology (brief §4/§5) - frozen floor, imported not redefined ------------

@pytest.mark.parametrize("case", FUTURE_BUSINESS_REPAIR_CASES)
def test_proposed_stage_from_real_repair_cases_still_rejected_by_the_frozen_floor(case):
    """Every one of D3.1's real stage-floor repair cases must still be rejected by the SAME floor
    under schema_v2 - the ontology is not relaxed by this stage (brief §0/§2)."""
    flags = dict(current_revenue_evidence=False, order_backlog_evidence=False,
                customer_evidence=False, capacity_evidence=False, margin_evidence=False)
    # Distribute `proposed_flags` truthy flags deterministically (which specific flag does not
    # matter for the floor count check itself).
    flag_names = list(flags)
    for i in range(case["proposed_flags"]):
        flags[flag_names[i]] = True
    with pytest.raises(ValidationError):
        FutureBusinessItemV2(name=case["ticker"], stage=FutureBusinessStage[case["proposed_stage"]],
                            evidence_strength=Confidence.MEDIUM, **flags)


@pytest.mark.parametrize("case", [c for c in FUTURE_BUSINESS_REPAIR_CASES])
def test_repaired_stage_from_real_repair_cases_validates(case):
    """The stage each real case was actually repaired TO must validate cleanly - confirming the
    frozen floor and the repaired outcomes stay consistent under V2."""
    flags = dict(current_revenue_evidence=False, order_backlog_evidence=False,
                customer_evidence=False, capacity_evidence=False, margin_evidence=False)
    flag_names = list(flags)
    for i in range(case["repaired_flags"]):
        flags[flag_names[i]] = True
    FutureBusinessItemV2(name=case["ticker"], stage=FutureBusinessStage[case["repaired_stage"]],
                        evidence_strength=Confidence.MEDIUM, **flags)


def test_real_business_requires_revenue_or_backlog_specifically():
    with pytest.raises(ValidationError):
        FutureBusinessItemV2(
            name="X", stage=FutureBusinessStage.REAL_BUSINESS, evidence_strength=Confidence.HIGH,
            current_revenue_evidence=False, order_backlog_evidence=False, customer_evidence=True,
            capacity_evidence=True, margin_evidence=False,
        )


def test_commercializing_valid_without_revenue_when_two_other_flags_present():
    """COMMERCIALIZING's frozen floor is 2 flags, no revenue-or-backlog condition (that condition
    is REAL_BUSINESS/MATURE-specific) - a signed customer + capacity evidence is sufficient."""
    FutureBusinessItemV2(
        name="X", stage=FutureBusinessStage.COMMERCIALIZING, evidence_strength=Confidence.MEDIUM,
        current_revenue_evidence=False, order_backlog_evidence=False, customer_evidence=True,
        capacity_evidence=True, margin_evidence=False,
    )


def test_missing_evidence_field_not_validated_against_flags():
    """`missing_evidence` is a declared record, not a re-derivation of the floor - any free text is
    accepted, and the floor check runs independently of it."""
    FutureBusinessItemV2(
        name="X", stage=FutureBusinessStage.STORY, evidence_strength=Confidence.LOW,
        current_revenue_evidence=False, order_backlog_evidence=False, customer_evidence=False,
        capacity_evidence=False, margin_evidence=False,
        missing_evidence=["REVENUE", "A NAMED CUSTOMER"],
    )


# --- GAAP fair-value language allowed in claim text (brief §13, reusing D3.2's fixed check) -----

@pytest.mark.parametrize("text", FAIR_VALUE_GAAP_SAFE_EXAMPLES)
def test_gaap_fair_value_text_allowed_in_claim(text):
    """schema_v2 reuses `schema._banned_language_check` unchanged (D3.2 already fixed the
    validator side - `validation_v2.classify_investment_language` - not the schema's own banned
    list). These three real sentences must still validate; if `fair value` were re-added to
    `BANNED_INVESTMENT_LANGUAGE_PATTERNS`, this test documents exactly what would break again."""
    ClaimV2(text=text, claim_type=ClaimType.FACT, source_id=SOURCE, evidence_id=EID0,
           confidence=Confidence.HIGH)


def test_investment_opinion_fair_value_still_blocked_in_claim():
    """The classifier fix must not have gone too far the other way - a real valuation opinion
    still fails, in the exact lexical neighbourhood of the GAAP-safe examples above."""
    with pytest.raises(ValidationError):
        ClaimV2(text=FAIR_VALUE_INVESTMENT_OPINION_EXAMPLE, claim_type=ClaimType.FACT,
               source_id=SOURCE, evidence_id=EID0, confidence=Confidence.HIGH)


# --- mirrored V2 classes: consistent language check, unchanged source/sources contract ---------

def test_catalyst_candidate_v2_allows_gaap_language_and_still_requires_a_source():
    CatalystCandidateV2(type="EARNINGS", description=FAIR_VALUE_GAAP_SAFE_EXAMPLES[1],
                        expected_time=None, timing_confidence=Confidence.LOW,
                        materiality_candidate=Confidence.MEDIUM, sources=[SOURCE])
    with pytest.raises(ValidationError):
        CatalystCandidateV2(type="EARNINGS", description="ordinary text", expected_time=None,
                           timing_confidence=Confidence.LOW, materiality_candidate=Confidence.MEDIUM,
                           sources=[])


def test_risk_item_v2_now_checks_language_a_v1_gap_this_stage_closes_incidentally():
    with pytest.raises(ValidationError):
        RiskItemV2(category=RiskCategory.DILUTION, description=FAIR_VALUE_INVESTMENT_OPINION_EXAMPLE,
                  sources=[SOURCE])
    RiskItemV2(category=RiskCategory.DILUTION, description=FAIR_VALUE_GAAP_SAFE_EXAMPLES[2],
              sources=[SOURCE])


def test_invalidation_candidate_v2_checks_language_too():
    with pytest.raises(ValidationError):
        InvalidationCandidateV2(description=FAIR_VALUE_INVESTMENT_OPINION_EXAMPLE, sources=[SOURCE])


def test_evidence_conflict_v2_allows_gaap_language_and_still_needs_two_sides():
    EvidenceConflictV2(topic="revenue recognition", evidence_ids=[EID0, EID1],
                       description=FAIR_VALUE_GAAP_SAFE_EXAMPLES[0],
                       resolution_status=ConflictResolution.UNRESOLVED, confidence=Confidence.LOW)
    with pytest.raises(ValidationError):
        EvidenceConflictV2(topic="x", evidence_ids=[EID0], description="only one side",
                          resolution_status=ConflictResolution.UNRESOLVED, confidence=Confidence.LOW)


# --- top-level assembly ------------------------------------------------------------------------

def test_minimal_v2_output_validates():
    HResearchInterpretationV2(**_base_kwargs())


def test_extra_field_still_rejected():
    with pytest.raises(ValidationError):
        HResearchInterpretationV2(**_base_kwargs(decision="APPROVE"))


def test_no_decision_enum_field_exists_in_v2_schema():
    field_names = set(HResearchInterpretationV2.model_fields.keys())
    banned = {"decision", "approve_watch_reject", "fair_value", "price_target",
             "entry_zone", "entry1", "entry2", "tp1", "tp2", "rating"}
    assert field_names.isdisjoint(banned)


def test_sources_must_cover_compound_claim_citations():
    claim = ClaimV2(text="Two things happened.", claim_type=ClaimType.FACT,
                    evidence_ids=[EID0, EID1], confidence=Confidence.MEDIUM)
    kwargs = _base_kwargs(business_model=BusinessModelV2(revenue_drivers=[claim]), sources=[])
    with pytest.raises(ValidationError):
        HResearchInterpretationV2(**kwargs)
    kwargs["sources"] = [SOURCE]
    HResearchInterpretationV2(**kwargs)
