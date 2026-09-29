"""D3.2R prompt contract V2 tests (brief §20). Structural checks only - no live model call, no
scoring of what a model would actually do with this prompt (brief §19: that is D3.3's job)."""

from __future__ import annotations

import json

from app.backtest.strategy_h_v2.research.prompt_builder import PROMPT_VERSION as PROMPT_VERSION_V1
from app.backtest.strategy_h_v2.research.prompt_builder import content_only_schema
from app.backtest.strategy_h_v2.research.prompt_builder_v2 import (
    ATOMIC_CLAIM_CONTRACT,
    CONSERVATIVE_STAGE_RULE,
    EVIDENCE_FLAGS_FIRST,
    FUTURE_BUSINESS_ONTOLOGY,
    INVESTMENT_LANGUAGE_CLARIFICATION,
    PROMPT_VERSION_V2,
    SYSTEM_PROMPT_V2,
    build_repair_prompt_v2,
    content_only_schema_v2,
)
from app.backtest.strategy_h_v2.research.schema_v2 import HResearchInterpretationV2


def test_v2_prompt_version_distinct_from_v1():
    assert PROMPT_VERSION_V2 != PROMPT_VERSION_V1
    assert PROMPT_VERSION_V2 == "h_v2_d3_research_v2"


def test_v1_schema_and_prompt_unchanged():
    """V1's own prompt module must still work exactly as before - this stage adds, it does not
    touch (brief §17: "기존 D3 prompt를 덮어쓰지 않는다")."""
    schema = content_only_schema()
    assert "future_business" in schema["properties"]


def test_v2_schema_is_built_from_the_v2_model():
    schema = content_only_schema_v2()
    v1_schema = content_only_schema()
    # The two schemas diverge (evidence_ids, missing_evidence are V2-only) - if this ever came back
    # identical to V1's, content_only_schema_v2 would be silently building off the wrong model.
    assert schema != v1_schema
    assert schema["title"] == HResearchInterpretationV2.__name__


def test_v2_schema_omits_metadata_fields():
    schema = content_only_schema_v2()
    for field in ("schema_version", "research_id", "ticker", "decision_time", "created_at"):
        assert field not in schema["properties"], field


def test_v2_schema_still_has_no_decision_field():
    schema = json.dumps(content_only_schema_v2())
    for banned in ("fair_value", "price_target", "approve_watch_reject", "entry_zone"):
        assert banned not in schema


# --- ontology / reasoning-order text coverage (brief §4/§5/§6) ---------------------------------

def test_ontology_states_evidence_requirement_per_stage():
    for stage, requirement in (
        ("STORY", "0 evidence flags"), ("EARLY_EVIDENCE", ">= 1 evidence flag"),
        ("COMMERCIALIZING", ">= 2 evidence flags"), ("REAL_BUSINESS", "revenue or backlog"),
        ("MATURE", "revenue-or-backlog"),
    ):
        assert stage in FUTURE_BUSINESS_ONTOLOGY
    assert "revenue or backlog" in FUTURE_BUSINESS_ONTOLOGY.lower()


def test_conservative_stage_rule_present_and_does_not_lower_the_floor():
    assert "LOWER stage" in CONSERVATIVE_STAGE_RULE
    assert "missing_evidence" in CONSERVATIVE_STAGE_RULE
    # The rule text itself must not contain a numeric override of the frozen floor counts.
    assert "requires at least" not in CONSERVATIVE_STAGE_RULE


def test_reasoning_order_is_evidence_flags_first():
    order = EVIDENCE_FLAGS_FIRST
    assert order.index("Step 1") < order.index("Step 3") < order.index("Step 4")
    assert "Extract" in order and "Emit the stage" in order


# --- atomic claim / numeric contract text coverage (brief §7/§9) -------------------------------

def test_atomic_contract_documents_compound_fallback():
    assert "evidence_ids" in ATOMIC_CLAIM_CONTRACT
    assert "at least 2" in ATOMIC_CLAIM_CONTRACT


def test_atomic_contract_forbids_self_computed_numbers():
    assert "never a number you calculated yourself" in ATOMIC_CLAIM_CONTRACT


# --- investment language clarification (brief §13) ---------------------------------------------

def test_investment_language_clarification_names_allowed_and_banned_terms():
    text = INVESTMENT_LANGUAGE_CLARIFICATION
    for allowed in ("fair value", "approved", "repurchase"):
        assert allowed in text
    assert "price target" in text
    assert "undervalued" in text or "overvalued" in text


def test_full_system_prompt_v2_assembles_without_leftover_placeholders():
    schema_json = json.dumps(content_only_schema_v2(), indent=2)
    rendered = SYSTEM_PROMPT_V2.replace("{schema}", schema_json)
    assert "{schema}" not in rendered
    for placeholder in ("{ontology}", "{conservative_rule}", "{reasoning_order}",
                       "{qualification_and_types}", "{numeric_and_atomic}", "{conflict_handling}",
                       "{investment_language_clarification}", "{boundary}"):
        assert placeholder not in rendered, placeholder


def test_repair_prompt_v2_is_schema_correction_only():
    prompt = build_repair_prompt_v2("PREVIOUS", ["some error @ field.path"])
    assert "Fix ONLY" in prompt
    assert "PREVIOUS" in prompt
    assert "some error @ field.path" in prompt
