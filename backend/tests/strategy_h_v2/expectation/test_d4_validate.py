"""D4 validation and safety. This is the file that proves D4 cannot become a decision layer, cannot
invent a consensus, and cannot do its own arithmetic."""

from __future__ import annotations

import json

import pytest
from d4_helpers import (
    D3_OUTPUT,
    NOW,
    SOURCE_ID,
    claim,
    d4_content,
    expectation_bundle,
    package,
)

from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index, code_source_id
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability
from app.backtest.strategy_h_v2.expectation.validate import (
    assemble_and_validate_d4,
    find_banned_fields,
)

PKG = package()
BUNDLE = expectation_bundle()
FACTS = build_code_fact_index(BUNDLE)


def run(content: dict, *, bundle=None, research=None, facts=None, pkg=None):
    return assemble_and_validate_d4(
        json.dumps(content), package=pkg or PKG, bundle=bundle or BUNDLE,
        research_output=research or D3_OUTPUT, code_facts=facts if facts is not None else FACTS,
        analysis_id="A-1", version=1, model_name="claude-opus-5-5", model_version="opus",
        prompt_version="h_v2_d4_expectation_gap_v1", created_at=NOW,
    )


# --- the happy path ---------------------------------------------------------------------------

def test_a_minimal_unknown_analysis_validates():
    analysis, errors = run(d4_content())
    assert errors == []
    assert analysis is not None
    assert analysis.expectation_gap.value == "UNKNOWN"


def test_code_fills_the_contract_residue_fields_not_the_model():
    analysis, _ = run(d4_content())
    assert analysis.confidence_ceiling.value == "MEDIUM"
    assert analysis.d6_approve_precondition.value == "BLOCKED"
    assert analysis.wide_positive_deferred_conjunct is None


def test_a_model_supplied_contract_residue_field_is_ignored_not_trusted():
    """These four are code's record that the frozen rules were applied. A model value there would
    be the model grading its own homework, so the injector strips them."""
    analysis, errors = run(d4_content(d6_approve_precondition="SATISFIED",
                                      confidence_ceiling="HIGH"))
    assert errors == []
    assert analysis.d6_approve_precondition.value == "BLOCKED"
    assert analysis.confidence_ceiling.value == "MEDIUM"


# --- safety: decision / valuation leakage -------------------------------------------------------

@pytest.mark.parametrize("field", ["decision", "recommendation", "fair_value", "price_target",
                                    "entry", "tp1", "portfolio_weight", "expectation_gap_score"])
def test_a_prohibited_decision_or_valuation_field_is_rejected_by_name(field: str):
    analysis, errors = run(d4_content(**{field: "APPROVE"}))
    assert analysis is None
    assert any(f"prohibited D4 field {field!r}" in e for e in errors)
    assert any("D6" in e and "D5" in e for e in errors)


def test_a_prohibited_field_is_caught_at_any_nesting_depth():
    """A top-level-only check would pass this straight to Pydantic, which would reject it for the
    wrong reason inside a list element nobody reads."""
    found = find_banned_fields({"why_now": [{"summary": "x", "price_target": 42}]})
    assert found == ["why_now[0].price_target"]


def test_the_schema_forbids_any_undeclared_field_even_a_harmless_one():
    analysis, errors = run(d4_content(notes="a harmless extra field"))
    assert analysis is None
    assert any("extra" in e.lower() for e in errors)


def test_investment_language_in_a_limitation_is_rejected():
    analysis, errors = run(d4_content(limitations=["the shares look undervalued here"]))
    assert analysis is None
    assert any("prohibited investment language" in e for e in errors)


# --- safety: fabricated consensus ----------------------------------------------------------------

def test_reporting_a_consensus_status_the_bundle_does_not_have_is_rejected():
    content = d4_content()
    content["market_expectation_evidence"]["consensus_status"] = "AVAILABLE"
    analysis, errors = run(content)
    assert analysis is None
    assert any("contradicts the code-owned expectation bundle" in e for e in errors)


@pytest.mark.parametrize("phrase", [
    "Analysts expect revenue to grow.",
    "Wall Street expects a stronger second half.",
    "The result beat consensus by a wide margin.",
    "Investors are pricing in a recovery.",
])
def test_an_asserted_consensus_expectation_is_rejected_while_no_source_exists(phrase: str):
    analysis, errors = run(d4_content(
        supporting_claims=[claim(phrase)], sources=[SOURCE_ID],
    ))
    assert analysis is None
    assert any("SOURCE_NOT_AVAILABLE" in e for e in errors)


def test_the_company_s_own_prior_guidance_is_not_treated_as_consensus():
    """ABOVE_COMPANY_GUIDANCE is legitimate evidence; only analyst-expectation language is not."""
    analysis, errors = run(d4_content(
        supporting_claims=[claim("The reported result was above the company's own prior guidance.")],
    ))
    assert errors == []
    assert analysis is not None


def test_consensus_absence_is_not_rewritten_as_neutral_by_the_bundle():
    assert BUNDLE.consensus.status == EvidenceAvailability.SOURCE_NOT_AVAILABLE
    assert BUNDLE.consensus.excerpts == []


# --- numeric discipline -------------------------------------------------------------------------

def _bundle_with_price_context():
    return expectation_bundle(pre_event_price_context={
        "return_3m": {"value": 0.18, "status": "COMPLETE",
                      "from_session": "2026-06-16", "to_session": "2026-09-16"},
    })


def test_a_claim_citing_a_code_fact_must_state_that_number():
    bundle = _bundle_with_price_context()
    facts = build_code_fact_index(bundle)
    fact_id = next(iter(facts))
    good = d4_content(
        supporting_claims=[claim("The shares rose 18% over the last 63 sessions.",
                                 evidence_id=fact_id, source_id=code_source_id(bundle.bundle_id))],
        sources=[code_source_id(bundle.bundle_id)],
    )
    analysis, errors = run(good, bundle=bundle, facts=facts)
    assert errors == []
    assert analysis is not None


def test_a_claim_citing_a_code_fact_while_stating_a_different_number_is_rejected():
    """The exact shape of brief §25's prohibition: the model did not transcribe, it computed."""
    bundle = _bundle_with_price_context()
    facts = build_code_fact_index(bundle)
    fact_id = next(iter(facts))
    analysis, errors = run(d4_content(
        supporting_claims=[claim("The shares rose 42% over the last 63 sessions.",
                                 evidence_id=fact_id, source_id=code_source_id(bundle.bundle_id))],
        sources=[code_source_id(bundle.bundle_id)],
    ), bundle=bundle, facts=facts)
    assert analysis is None
    assert any("transcribed, never computed" in e for e in errors)


def test_a_qualitative_claim_citing_a_code_fact_is_allowed():
    """Citing the 3-month return as the basis for "it underperformed" quotes no figure at all and
    is ordinary, correct behaviour - not every code-fact citation is a number being quoted."""
    bundle = _bundle_with_price_context()
    facts = build_code_fact_index(bundle)
    fact_id = next(iter(facts))
    analysis, errors = run(d4_content(
        supporting_claims=[claim("The shares advanced materially over the quarter.",
                                 evidence_id=fact_id, source_id=code_source_id(bundle.bundle_id))],
        sources=[code_source_id(bundle.bundle_id)],
    ), bundle=bundle, facts=facts)
    assert errors == []
    assert analysis is not None


def test_an_incomplete_price_window_produces_no_citable_code_fact():
    """A citable id for a number that does not exist is how "the 3-day reaction was flat" gets
    written about a window that never completed."""
    bundle = expectation_bundle(pre_event_price_context={
        "return_3m": {"value": None, "status": "INCOMPLETE_FUTURE",
                      "from_session": None, "to_session": None},
    })
    assert build_code_fact_index(bundle) == {}


def test_an_unknown_evidence_id_is_rejected():
    analysis, errors = run(d4_content(
        supporting_claims=[claim("x", evidence_id=f"{SOURCE_ID}:CHUNK:99")],
    ))
    assert analysis is None
    assert any("unknown evidence_id" in e for e in errors)


# --- guidance arithmetic agreement ---------------------------------------------------------------

def _guidance_content(state: str, prev=(1.0, 1.2), cur=(1.1, 1.3)):
    content = d4_content()
    content["market_expectation_evidence"]["guidance_assessments"] = [{
        "metric": "EPS", "state": state, "period_label": "FY2026",
        "previous_low": prev[0], "previous_high": prev[1],
        "current_low": cur[0], "current_high": cur[1],
        "unit": "USD_PER_SHARE", "claims": [],
    }]
    content["market_expectation_evidence"]["overall_guidance_state"] = state
    return content


def test_a_guidance_state_contradicting_its_own_transcribed_ranges_is_rejected():
    analysis, errors = run(_guidance_content("LOWERED"))
    assert analysis is None
    assert any("code's arithmetic, not a judgement" in e for e in errors)


def test_a_guidance_state_agreeing_with_the_arithmetic_is_accepted():
    analysis, errors = run(_guidance_content("RAISED"))
    assert errors == []
    assert analysis is not None


def test_a_narrowed_range_reported_as_maintained_is_rejected():
    analysis, errors = run(_guidance_content("MAINTAINED", prev=(1.0, 1.2), cur=(1.05, 1.15)))
    assert analysis is None
    assert any("MIXED_BOUNDS" in e for e in errors)


def test_a_result_vs_guidance_state_contradicting_the_numbers_is_rejected():
    content = d4_content()
    content["market_expectation_evidence"]["result_vs_guidance"] = [{
        "metric": "revenue", "state": "ABOVE_COMPANY_GUIDANCE", "reported_value": 1.05,
        "prior_guidance_low": 1.0, "prior_guidance_high": 1.1, "unit": "USD", "claims": [],
    }]
    analysis, errors = run(content)
    assert analysis is None
    assert any("WITHIN_COMPANY_GUIDANCE" in e for e in errors)


# --- D3 immutability ------------------------------------------------------------------------------

def test_a_restated_d3_durability_state_is_rejected():
    content = d4_content()
    content["fundamental_reality_summary"]["d3_growth_durability_state"] = "DURABLE"
    analysis, errors = run(content, research={**D3_OUTPUT,
                                              "growth_durability": {"state": "TEMPORARY"}})
    assert analysis is None
    assert any("does not re-decide it" in e for e in errors)


def test_a_restated_d3_future_business_stage_is_rejected():
    content = d4_content()
    content["fundamental_reality_summary"]["d3_future_business_max_stage"] = "REAL_BUSINESS"
    analysis, errors = run(content, research={
        **D3_OUTPUT, "future_business": [{"stage": "STORY"}, {"stage": "EARLY_EVIDENCE"}],
    })
    assert analysis is None
    assert any("highest stage in the D3 output" in e for e in errors)


def test_the_highest_d3_stage_is_the_one_that_must_be_copied():
    content = d4_content()
    content["fundamental_reality_summary"]["d3_future_business_max_stage"] = "COMMERCIALIZING"
    analysis, errors = run(content, research={
        **D3_OUTPUT,
        "future_business": [{"stage": "STORY"}, {"stage": "COMMERCIALIZING"},
                            {"stage": "EARLY_EVIDENCE"}],
    })
    assert errors == []
    assert analysis is not None
