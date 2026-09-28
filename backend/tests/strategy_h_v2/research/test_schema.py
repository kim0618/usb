from __future__ import annotations

import pytest
from helpers import utc
from pydantic import ValidationError

from app.backtest.strategy_h_v2.research.schema import (
    Claim,
    ClaimType,
    CompetitiveDimension,
    CompetitiveStatus,
    Confidence,
    CatalystCandidate,
    FutureBusinessItem,
    FutureBusinessStage,
    GrowthDurability,
    GrowthDurabilityEvidence,
    GrowthDurabilityState,
    HResearchInterpretationV1,
    InvalidationCandidate,
    RiskCategory,
    RiskItem,
    WhyNowCandidate,
    BusinessModel,
    ResearchCompleteness,
)

NOW = utc(2026, 9, 28)


def _fact_claim(source_id: str = "SEC:1:A-1", evidence_id: str = "SEC:1:A-1:CHUNK:0") -> Claim:
    return Claim(text="Revenue grew per the filing.", claim_type=ClaimType.FACT,
                 source_id=source_id, evidence_id=evidence_id, confidence=Confidence.HIGH)


def _base_kwargs(**overrides):
    kwargs = dict(
        research_id="R-1", version=1, company_id="1", ticker="ACME", decision_time=NOW,
        input_package_id="P-1", input_package_checksum="deadbeef", model="claude-opus-5-5",
        model_version="claude-opus-5-5", prompt_version="v1", created_at=NOW,
        business_model=BusinessModel(), fundamental_change=[],
        growth_durability=GrowthDurability(state=GrowthDurabilityState.UNKNOWN,
                                            evidence_checklist=GrowthDurabilityEvidence()),
        future_business=[], competitive_position=[], management_execution=[],
        catalyst_candidates=[], why_now_candidate=WhyNowCandidate(summary="worth a look"),
        risks=[], invalidation_candidates=[], open_questions=[], unknown_fields=[],
        sources=[], research_completeness=ResearchCompleteness.INSUFFICIENT_EVIDENCE,
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_output_validates():
    HResearchInterpretationV1(**_base_kwargs())


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        HResearchInterpretationV1(**_base_kwargs(decision="APPROVE"))


def test_aware_datetime_required():
    with pytest.raises(ValidationError):
        HResearchInterpretationV1(**_base_kwargs(decision_time=NOW.replace(tzinfo=None)))


def test_no_decision_enum_field_exists_in_schema():
    field_names = set(HResearchInterpretationV1.model_fields.keys())
    banned = {"decision", "approve_watch_reject", "fair_value", "price_target",
              "entry_zone", "entry1", "entry2", "tp1", "tp2", "rating"}
    assert field_names.isdisjoint(banned)


# --- claim provenance -----------------------------------------------------------------------

def test_fact_claim_without_source_is_rejected():
    with pytest.raises(ValidationError):
        Claim(text="X grew", claim_type=ClaimType.FACT, source_id=None, evidence_id=None,
              confidence=Confidence.HIGH)


def test_unknown_claim_may_omit_source():
    claim = Claim(text="Cannot determine backlog conversion timing.", claim_type=ClaimType.UNKNOWN,
                  source_id=None, evidence_id=None, confidence=Confidence.UNKNOWN)
    assert claim.source_id is None


def test_orphan_source_id_rejected_via_context():
    with pytest.raises(ValidationError):
        Claim.model_validate(
            {"text": "X", "claim_type": "FACT", "source_id": "SEC:1:NOT-REAL",
             "evidence_id": "SEC:1:NOT-REAL:CHUNK:0", "confidence": "HIGH"},
            context={"valid_source_ids": {"SEC:1:A-1"}},
        )


def test_known_source_id_accepted_via_context():
    Claim.model_validate(
        {"text": "X", "claim_type": "FACT", "source_id": "SEC:1:A-1",
         "evidence_id": "SEC:1:A-1:CHUNK:0", "confidence": "HIGH"},
        context={"valid_source_ids": {"SEC:1:A-1"}},
    )


def test_sources_list_must_cover_every_cited_claim():
    with pytest.raises(ValidationError):
        HResearchInterpretationV1(**_base_kwargs(
            business_model=BusinessModel(revenue_drivers=[_fact_claim()]), sources=[],
        ))


def test_sources_list_covering_the_claim_is_accepted():
    HResearchInterpretationV1(**_base_kwargs(
        business_model=BusinessModel(revenue_drivers=[_fact_claim()]), sources=["SEC:1:A-1"],
    ))


# --- safety: banned investment language -------------------------------------------------------

@pytest.mark.parametrize("phrase", ["undervalued", "overvalued", "price target", "fair value",
                                     "we recommend"])
def test_banned_investment_language_rejected_in_claim_text(phrase):
    with pytest.raises(ValidationError):
        Claim(text=f"This looks {phrase} based on the filing.", claim_type=ClaimType.INTERPRETATION,
              source_id="SEC:1:A-1", evidence_id="SEC:1:A-1:CHUNK:0", confidence=Confidence.LOW)


def test_banned_language_rejected_in_why_now_summary():
    with pytest.raises(ValidationError):
        WhyNowCandidate(summary="We recommend accumulating shares given this setup.")


def test_ordinary_prose_is_not_falsely_flagged():
    # "hold" and "watch" are ordinary English words and must not be banned outright.
    WhyNowCandidate(summary="Customers hold multi-year contracts and watch capacity closely.")


def test_board_approval_language_is_not_falsely_flagged():
    """Regression test for a real defect found on the D3 pilot: 11 of 12 real candidates needed a
    schema-repair round because ordinary filing language like "the Board approved a buyback"
    contains "approve" and "buy" as substrings, and the original banned-word list matched bare
    substrings rather than whole investment-decision phrases."""
    claim = Claim(
        text="The Board of Directors approved a new share repurchase program in the quarter.",
        claim_type=ClaimType.FACT, source_id="SEC:1:A-1", evidence_id="SEC:1:A-1:CHUNK:0",
        confidence=Confidence.HIGH,
    )
    assert claim.claim_type == ClaimType.FACT
    WhyNowCandidate(summary="Customers buy replacement parts and the company sells service plans.")


# --- future business evidence floor -------------------------------------------------------------

def test_real_business_stage_requires_hard_evidence():
    with pytest.raises(ValidationError):
        FutureBusinessItem(
            name="New AI product", stage=FutureBusinessStage.REAL_BUSINESS,
            evidence_strength=Confidence.LOW, current_revenue_evidence=False,
            order_backlog_evidence=False, customer_evidence=True, capacity_evidence=False,
            margin_evidence=False,
        )


def test_story_stage_requires_no_evidence():
    item = FutureBusinessItem(
        name="Announced initiative", stage=FutureBusinessStage.STORY,
        evidence_strength=Confidence.LOW, current_revenue_evidence=False,
        order_backlog_evidence=False, customer_evidence=False, capacity_evidence=False,
        margin_evidence=False,
    )
    assert item.stage == FutureBusinessStage.STORY


def test_real_business_with_revenue_and_backlog_evidence_is_accepted():
    FutureBusinessItem(
        name="New segment", stage=FutureBusinessStage.REAL_BUSINESS,
        evidence_strength=Confidence.HIGH, current_revenue_evidence=True,
        order_backlog_evidence=True, customer_evidence=False, capacity_evidence=False,
        margin_evidence=False,
    )


def test_real_business_with_two_flags_but_no_revenue_or_backlog_is_rejected():
    with pytest.raises(ValidationError):
        FutureBusinessItem(
            name="New segment", stage=FutureBusinessStage.REAL_BUSINESS,
            evidence_strength=Confidence.HIGH, current_revenue_evidence=False,
            order_backlog_evidence=False, customer_evidence=True, capacity_evidence=True,
            margin_evidence=False,
        )


# --- catalyst / risk / invalidation require citations -------------------------------------------

def test_catalyst_candidate_requires_at_least_one_source():
    with pytest.raises(ValidationError):
        CatalystCandidate(type="earnings", description="Next print", expected_time=None,
                          timing_confidence=Confidence.MEDIUM, materiality_candidate=Confidence.MEDIUM,
                          sources=[])


def test_risk_item_requires_at_least_one_source():
    with pytest.raises(ValidationError):
        RiskItem(category=RiskCategory.CUSTOMER, description="Customer concentration", sources=[])


def test_invalidation_candidate_requires_at_least_one_source():
    with pytest.raises(ValidationError):
        InvalidationCandidate(description="Guidance cut would break this", sources=[])


def test_competitive_supported_status_requires_claims():
    with pytest.raises(ValidationError):
        CompetitiveDimension(dimension="pricing_power", status=CompetitiveStatus.SUPPORTED, claims=[])


def test_competitive_unknown_status_needs_no_claims():
    dim = CompetitiveDimension(dimension="pricing_power", status=CompetitiveStatus.UNKNOWN, claims=[])
    assert dim.status == CompetitiveStatus.UNKNOWN
