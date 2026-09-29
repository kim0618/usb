"""Rules C1-C7 against real validator runs - the tests brief §37 calls the Gap section.

The four it names are the four this engine exists for: a good company is not a positive gap, an
already-re-rated stock may be neutral, insufficient expectation evidence means UNKNOWN, and a
material unresolved conflict prevents HIGH confidence.
"""

from __future__ import annotations

import json

from d4_helpers import (
    D3_OUTPUT,
    EVIDENCE_ID,
    NOW,
    SOURCE_ID,
    claim,
    d4_content,
    expectation_bundle,
    package,
)

from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.evidence_schema import EventPriceReaction
from app.backtest.strategy_h_v2.expectation.gap_contract import ContractRule
from app.backtest.strategy_h_v2.expectation.validate import assemble_and_validate_d4

PKG = package()


def _reaction() -> EventPriceReaction:
    complete = {"value": 0.05, "status": "COMPLETE", "from_session": "2026-03-02",
                "to_session": "2026-03-03"}
    return EventPriceReaction(
        source_id=SOURCE_ID, event_kind="EARNINGS_RESULTS", available_at=NOW,
        event_session=None, prior_session=None, alignment_ambiguous=False, ambiguity_reason=None,
        return_1d=complete, return_3d=complete, benchmark_adjusted_1d=complete,
        benchmark_adjusted_3d=complete, pre_event_return_5d=complete,
        pre_event_return_20d=complete,
    )


BUNDLE_WITH_REACTION = expectation_bundle(price_reaction=[_reaction()])


def run(content: dict, *, bundle=None, research=None):
    bundle = bundle or BUNDLE_WITH_REACTION
    return assemble_and_validate_d4(
        json.dumps(content), package=PKG, bundle=bundle,
        research_output=research or D3_OUTPUT, code_facts=build_code_fact_index(bundle),
        analysis_id="A-1", version=1, model_name="claude-opus-5-5", model_version="opus",
        prompt_version="h_v2_d4_expectation_gap_v1", created_at=NOW,
    )


def _positive(**overrides):
    content = d4_content(
        expectation_gap="POSITIVE", expectation_gap_confidence="MEDIUM",
        gap_rationale=[claim("Operating income inflected positive while the shares lagged.")],
    )
    content["fundamental_reality_summary"]["improvement_claims"] = [
        claim("Operating income turned positive year over year."),
    ]
    content.update(overrides)
    return content


def _with_guidance(content: dict, state: str = "RAISED") -> dict:
    content["market_expectation_evidence"]["guidance_assessments"] = [{
        "metric": "EPS", "state": state, "period_label": "FY2026",
        "previous_low": 1.0, "previous_high": 1.2, "current_low": 1.1, "current_high": 1.3,
        "unit": "USD_PER_SHARE", "claims": [],
    }]
    content["market_expectation_evidence"]["overall_guidance_state"] = state
    return content


# --- C1: a great company is not a positive expectation gap -------------------------------------

def test_a_positive_gap_resting_on_price_history_alone_is_rejected():
    """The engine's central rule. Price history shows the market HAS MOVED; it can never show the
    market is BEHIND."""
    analysis, errors = run(_positive())
    assert analysis is None
    assert any("rule C1" in e for e in errors)
    assert any("can never show the market is BEHIND" in e for e in errors)


def test_a_positive_gap_with_a_non_price_expectation_item_is_accepted():
    analysis, errors = run(_with_guidance(_positive()))
    assert errors == []
    assert analysis is not None
    assert ContractRule.C1_POSITIVE_NEEDS_NON_PRICE_EVIDENCE in analysis.applied_contract_rules


def test_a_guidance_block_saying_no_guidance_was_given_does_not_satisfy_c1():
    """An absence cannot be the evidence that the market is behind - counting it would let C1 be
    satisfied by the very thing it exists to catch."""
    analysis, errors = run(_with_guidance(_positive(), state="NOT_PROVIDED"))
    assert analysis is None
    assert any("rule C1" in e for e in errors)


def test_a_negative_gap_may_rest_on_price_context_alone():
    """Deliberately asymmetric: a large completed re-rating IS direct evidence that expectations
    moved. A good company receiving a NEGATIVE gap is a correct outcome of this engine."""
    content = d4_content(
        expectation_gap="NEGATIVE", expectation_gap_confidence="LOW",
        gap_rationale=[claim("The shares re-rated sharply before the evidenced progress.")],
    )
    analysis, errors = run(content)
    assert errors == []
    assert analysis is not None


def test_a_rerated_stock_may_be_neutral_with_no_extra_requirement():
    content = d4_content(
        expectation_gap="NEUTRAL", expectation_gap_confidence="MEDIUM",
        gap_rationale=[claim("Current pricing appears to reflect the disclosed trajectory.")],
    )
    analysis, errors = run(content)
    assert errors == []
    assert analysis.expectation_gap.value == "NEUTRAL"


def test_a_positive_gap_without_a_single_improvement_claim_is_rejected():
    content = _with_guidance(_positive())
    content["fundamental_reality_summary"]["improvement_claims"] = []
    analysis, errors = run(content)
    assert analysis is None
    assert any("improvement PLUS evidence" in e for e in errors)


# --- C2: WIDE_POSITIVE conjunction ---------------------------------------------------------------

def test_wide_positive_without_a_future_business_item_above_story_is_rejected():
    content = _with_guidance(_positive(expectation_gap="WIDE_POSITIVE"))
    analysis, errors = run(content, research={**D3_OUTPUT,
                                              "future_business": [{"stage": "STORY"}]})
    assert analysis is None
    assert any("above STORY" in e for e in errors)


def test_wide_positive_without_an_unrealized_why_now_item_is_rejected():
    content = _with_guidance(_positive(expectation_gap="WIDE_POSITIVE"))
    content["fundamental_reality_summary"]["d3_future_business_max_stage"] = "REAL_BUSINESS"
    content["why_now"] = [{
        "summary": "the capacity ramp completed last quarter",
        "realization_status": "REALIZED", "expected_window": None,
        "claims": [claim("The ramp completed.")], "sources": [SOURCE_ID],
    }]
    analysis, errors = run(content, research={
        **D3_OUTPUT, "future_business": [{"stage": "REAL_BUSINESS"}],
    })
    assert analysis is None
    assert any("unrealized why-now item" in e for e in errors)


def test_wide_positive_records_the_deferred_valuation_conjunct_it_could_not_evaluate():
    """D0 §L's WIDE_POSITIVE also requires that valuation has not re-rated. D5 has not run, so the
    conjunct is recorded as unevaluated rather than silently dropped."""
    content = _with_guidance(_positive(expectation_gap="WIDE_POSITIVE"))
    content["fundamental_reality_summary"]["d3_future_business_max_stage"] = "REAL_BUSINESS"
    content["why_now"] = [{
        "summary": "a capacity ramp is scheduled but has not started",
        "realization_status": "UNREALIZED", "expected_window": "next two quarters",
        "claims": [claim("The ramp has not started.")], "sources": [SOURCE_ID],
    }]
    analysis, errors = run(content, research={
        **D3_OUTPUT, "future_business": [{"stage": "REAL_BUSINESS"}],
    })
    assert errors == []
    assert "D5 Valuation has not run" in analysis.wide_positive_deferred_conjunct
    assert "must be re-checked by D6" in analysis.wide_positive_deferred_conjunct


# --- C3/C4: confidence ceilings --------------------------------------------------------------------

def test_high_confidence_is_rejected_while_no_consensus_source_exists():
    analysis, errors = run(_with_guidance(_positive(expectation_gap_confidence="HIGH")))
    assert analysis is None
    assert any("exceeds the frozen ceiling MEDIUM" in e for e in errors)
    assert any("C4_NO_CONSENSUS_CONFIDENCE_CEILING" in e for e in errors)


def test_a_material_unresolved_conflict_prevents_high_confidence():
    content = _with_guidance(_positive(expectation_gap_confidence="HIGH"))
    content["conflicts"] = [{
        "topic": "capacity timing", "description": "two filings disagree on the ramp date",
        "evidence_ids": [EVIDENCE_ID, EVIDENCE_ID], "resolution_status": "UNRESOLVED",
        "materiality": "MATERIAL", "origin": "D4_EXPECTATION", "confidence": "MEDIUM",
    }]
    analysis, errors = run(content)
    assert analysis is None
    assert any("C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING" in e for e in errors)


def test_an_immaterial_unresolved_conflict_does_not_fire_the_c3_ceiling():
    content = _with_guidance(_positive())
    content["conflicts"] = [{
        "topic": "a footnote", "description": "two filings word a footnote differently",
        "evidence_ids": [EVIDENCE_ID, EVIDENCE_ID], "resolution_status": "UNRESOLVED",
        "materiality": "IMMATERIAL", "origin": "D4_EXPECTATION", "confidence": "LOW",
    }]
    analysis, errors = run(content)
    assert errors == []
    assert ContractRule.C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING not in analysis.applied_contract_rules


def test_the_ceiling_is_enforced_rather_than_silently_applied():
    """Silently lowering a HIGH would hide that an explicitly stated constraint was ignored - which
    is a real instruction-following signal the gates need to see."""
    _, errors = run(_with_guidance(_positive(expectation_gap_confidence="HIGH")))
    assert any("rather than relying on code to lower it" in e for e in errors)


# --- C6: no expectation evidence at all -------------------------------------------------------------

def test_a_stated_gap_with_no_expectation_evidence_of_any_kind_must_be_unknown():
    bare = expectation_bundle()
    content = d4_content(
        expectation_gap="NEUTRAL", expectation_gap_confidence="LOW",
        gap_rationale=[claim("Pricing looks consistent with the trajectory.")],
    )
    analysis, errors = run(content, bundle=bare)
    assert analysis is None
    assert any("rule C6" in e for e in errors)
    assert any("the only defensible state is UNKNOWN" in e for e in errors)


def test_unknown_is_always_available_when_the_evidence_is_thin():
    analysis, errors = run(d4_content(), bundle=expectation_bundle())
    assert errors == []
    assert analysis.expectation_gap.value == "UNKNOWN"


# --- C5 / C7 --------------------------------------------------------------------------------------

def test_an_unknown_gap_cannot_carry_a_confidence():
    analysis, errors = run(d4_content(expectation_gap_confidence="MEDIUM"))
    assert analysis is None
    assert any("C5" in e for e in errors)


def test_a_stated_gap_must_carry_a_confidence():
    content = d4_content(expectation_gap="NEUTRAL", expectation_gap_confidence="UNKNOWN")
    analysis, errors = run(content)
    assert analysis is None
    assert any("C5" in e for e in errors)


def test_a_priced_in_assessment_without_evidence_ids_is_rejected():
    content = d4_content()
    content["priced_in_assessment"] = {
        "state": "LIKELY_PRICED", "confidence": "MEDIUM", "evidence_ids": [],
        "limitations": ["no consensus source"], "claims": [],
    }
    analysis, errors = run(content)
    assert analysis is None
    assert any("requires evidence_ids" in e for e in errors)


def test_a_priced_in_assessment_without_a_limitation_is_rejected():
    content = d4_content()
    content["priced_in_assessment"] = {
        "state": "LIKELY_PRICED", "confidence": "MEDIUM", "evidence_ids": [EVIDENCE_ID],
        "limitations": [], "claims": [],
    }
    analysis, errors = run(content)
    assert analysis is None
    assert any("at least one explicit limitation" in e for e in errors)


def test_a_fully_supported_priced_in_assessment_is_accepted_and_records_c7():
    content = d4_content()
    content["priced_in_assessment"] = {
        "state": "LIKELY_PRICED", "confidence": "MEDIUM", "evidence_ids": [EVIDENCE_ID],
        "limitations": ["no analyst-consensus source is available for this candidate"],
        "claims": [],
    }
    analysis, errors = run(content)
    assert errors == []
    assert ContractRule.C7_PRICED_IN_NEEDS_EVIDENCE in analysis.applied_contract_rules
