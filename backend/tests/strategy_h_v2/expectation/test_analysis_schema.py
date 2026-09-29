"""Schema-level rules that need no external input - the ones a Pydantic model can and should own."""

from __future__ import annotations

import pytest
from d4_helpers import EVIDENCE_ID, SOURCE_ID, claim

from app.backtest.strategy_h_v2.expectation.analysis_schema import (
    BANNED_D4_FIELD_NAMES,
    GuidanceMetricAssessmentV1,
    HExpectationGapAnalysisV1,
    ManagementSignalChangeV1,
    MarketExpectationEvidenceV1,
    PricedInAssessmentV1,
    WhyNowItemV1,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import (
    GuidanceState,
    PricedInAssessment,
    RealizationStatus,
)
from app.backtest.strategy_h_v2.research.schema import Confidence
from app.backtest.strategy_h_v2.research.schema_v2 import ClaimV2


def _claim() -> ClaimV2:
    return ClaimV2.model_validate(claim())


def _expectation(**overrides) -> dict:
    base = {
        "overall_guidance_state": GuidanceState.UNKNOWN, "guidance_assessments": [],
        "result_vs_guidance": [], "management_signal_changes": [], "price_reaction_reading": [],
        "pre_event_positioning_reading": [], "consensus_status": "SOURCE_NOT_AVAILABLE",
        "estimate_revisions_status": "SOURCE_NOT_AVAILABLE",
    }
    base.update(overrides)
    return base


# --- banned names ----------------------------------------------------------------------------

def test_every_field_brief_section_28_bans_is_in_the_banned_set():
    for name in ("decision", "recommendation", "fair_value", "price_target", "entry", "exit",
                 "portfolio_weight"):
        assert name in BANNED_D4_FIELD_NAMES


def test_no_declared_schema_field_collides_with_a_banned_name():
    """A collision would make a legitimate field unreachable. Checked rather than assumed, because
    the two lists are maintained independently."""
    declared = set(HExpectationGapAnalysisV1.model_json_schema()["properties"])
    assert not (declared & BANNED_D4_FIELD_NAMES)


# --- guidance assessment ------------------------------------------------------------------------

def test_a_half_stated_guidance_range_is_rejected():
    with pytest.raises(ValueError, match="both bounds or neither"):
        GuidanceMetricAssessmentV1(metric="EPS", state=GuidanceState.UNKNOWN, previous_low=1.0)


def test_a_numeric_range_must_state_its_unit():
    with pytest.raises(ValueError, match="must state its unit"):
        GuidanceMetricAssessmentV1(metric="EPS", state=GuidanceState.UNKNOWN,
                                    current_low=1.0, current_high=1.2)


def test_a_change_state_requires_both_ranges():
    with pytest.raises(ValueError, match="asserts a CHANGE"):
        GuidanceMetricAssessmentV1(metric="EPS", state=GuidanceState.RAISED, current_low=1.0,
                                    current_high=1.2, unit="USD_PER_SHARE")


def test_initiated_guidance_needs_only_the_current_range():
    assessment = GuidanceMetricAssessmentV1(
        metric="EPS", state=GuidanceState.INITIATED, current_low=1.0, current_high=1.2,
        unit="USD_PER_SHARE",
    )
    assert assessment.previous_range() is None
    assert assessment.current_range() is not None


# --- overall guidance state ----------------------------------------------------------------------

def test_disagreeing_per_metric_states_force_an_overall_mixed():
    """Brief §7: revenue raised while margin is cut is two facts, and a layer that averages them
    has destroyed the only information that mattered."""
    metrics = [
        GuidanceMetricAssessmentV1(metric="revenue", state=GuidanceState.RAISED, previous_low=1.0,
                                    previous_high=1.2, current_low=1.1, current_high=1.3,
                                    unit="USD"),
        GuidanceMetricAssessmentV1(metric="margin", state=GuidanceState.LOWERED, previous_low=10.0,
                                    previous_high=12.0, current_low=9.0, current_high=11.0,
                                    unit="PERCENT"),
    ]
    with pytest.raises(ValueError, match="forbids collapsing a disagreement"):
        MarketExpectationEvidenceV1(**_expectation(
            overall_guidance_state=GuidanceState.RAISED, guidance_assessments=metrics,
        ))
    ok = MarketExpectationEvidenceV1(**_expectation(
        overall_guidance_state=GuidanceState.MIXED, guidance_assessments=metrics,
    ))
    assert ok.overall_guidance_state == GuidanceState.MIXED


def test_an_overall_state_with_no_guided_metric_must_be_an_absence_state():
    with pytest.raises(ValueError, match="must rest on at least one guided metric"):
        MarketExpectationEvidenceV1(**_expectation(overall_guidance_state=GuidanceState.RAISED))


def test_an_overall_state_contradicting_the_single_metric_is_rejected():
    metric = GuidanceMetricAssessmentV1(
        metric="revenue", state=GuidanceState.LOWERED, previous_low=1.0, previous_high=1.2,
        current_low=0.9, current_high=1.1, unit="USD",
    )
    with pytest.raises(ValueError, match="cannot be RAISED"):
        MarketExpectationEvidenceV1(**_expectation(
            overall_guidance_state=GuidanceState.RAISED, guidance_assessments=[metric],
        ))


# --- management signals ----------------------------------------------------------------------------

def test_a_management_change_needs_the_two_documents_it_changed_between():
    with pytest.raises(ValueError, match="two documents"):
        ManagementSignalChangeV1(topic="ramp", direction="DELAYED", evidence_ids=[EVIDENCE_ID],
                                  confidence=Confidence.MEDIUM)


def test_an_unrecognized_direction_is_rejected():
    """`IMPROVED` is not one of the seven, and under V2 the rejection names all seven.

    D4.1's live run failed on exactly this field with `INCREASED` and
    `QUANTIFIED_AND_EXTENDED_TO_2027`, because the schema the model was shown said `"type":
    "string"`. The value set did not change in V2; the error message and the schema now agree
    about what it is.
    """
    with pytest.raises(ValueError, match="Input should be 'STRENGTHENED'"):
        ManagementSignalChangeV1(topic="ramp", direction="IMPROVED", confidence=Confidence.LOW,
                                  evidence_ids=[EVIDENCE_ID, EVIDENCE_ID])


# --- priced-in --------------------------------------------------------------------------------------

def test_an_unknown_priced_in_state_cannot_carry_a_confidence():
    with pytest.raises(ValueError, match="nothing to be confident about"):
        PricedInAssessmentV1(state=PricedInAssessment.UNKNOWN, confidence=Confidence.MEDIUM)


def test_over_priced_expectation_is_about_expectations_not_value():
    """The enum member exists so an expectation statement never has to borrow valuation words."""
    assessment = PricedInAssessmentV1(
        state=PricedInAssessment.OVER_PRICED_EXPECTATION, confidence=Confidence.LOW,
        evidence_ids=[EVIDENCE_ID], limitations=["no consensus source"],
    )
    assert assessment.state.value == "OVER_PRICED_EXPECTATION"


def test_a_limitation_containing_valuation_language_is_rejected():
    with pytest.raises(ValueError, match="prohibited investment language"):
        PricedInAssessmentV1(state=PricedInAssessment.LIKELY_PRICED, confidence=Confidence.LOW,
                              evidence_ids=[EVIDENCE_ID],
                              limitations=["the stock is overvalued at this level"])


# --- why now -------------------------------------------------------------------------------------

def test_a_why_now_item_must_be_sourced_and_cited():
    with pytest.raises(ValueError, match="at least one source"):
        WhyNowItemV1(summary="something may happen",
                      realization_status=RealizationStatus.UNREALIZED, claims=[_claim()],
                      sources=[])
    with pytest.raises(ValueError, match="at least one cited claim"):
        WhyNowItemV1(summary="something may happen",
                      realization_status=RealizationStatus.UNREALIZED, claims=[],
                      sources=[SOURCE_ID])


def test_a_realized_why_now_item_is_allowed_and_is_how_d4_says_not_why_now():
    item = WhyNowItemV1(summary="the ramp completed last quarter",
                        realization_status=RealizationStatus.REALIZED, claims=[_claim()],
                        sources=[SOURCE_ID])
    assert item.realization_status == RealizationStatus.REALIZED


def test_a_why_now_summary_using_decision_language_is_rejected():
    with pytest.raises(ValueError, match="prohibited investment language"):
        WhyNowItemV1(summary="a strong buy ahead of the print",
                      realization_status=RealizationStatus.UNREALIZED, claims=[_claim()],
                      sources=[SOURCE_ID])


# --- claim contract reuse ---------------------------------------------------------------------------

def test_d4_reuses_d3s_claim_contract_unchanged():
    """Brief §24: D4 must not invent a looser claim schema. Reuse is proven by identity, not by
    intention - a copy could drift."""
    from app.backtest.strategy_h_v2.expectation import analysis_schema
    from app.backtest.strategy_h_v2.research import schema_v2
    assert analysis_schema.ClaimV2 is schema_v2.ClaimV2


def test_a_material_claim_with_no_citation_is_still_rejected_under_d4():
    with pytest.raises(ValueError, match="requires source_id"):
        ClaimV2(text="revenue improved", claim_type="FACT", confidence="MEDIUM")


def test_a_compound_citation_still_needs_at_least_two_ids():
    with pytest.raises(ValueError, match="at least 2 chunks"):
        ClaimV2(text="revenue improved", claim_type="FACT", confidence="MEDIUM",
                evidence_ids=[EVIDENCE_ID])
