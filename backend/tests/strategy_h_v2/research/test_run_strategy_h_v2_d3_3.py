"""D3.2F live-runner wiring tests (brief §17). Every model call is a stub (`call_fn`) - 0 live
Opus calls, matching this stage's own $0 constraint. Exercised against LUV's real D2.1 package
(frozen, already on disk) so citations resolve against real evidence, not a synthetic package."""

from __future__ import annotations

import json

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.d3_3_contract import SampleEntry
from app.dev.run_strategy_h_v2_d3_3 import PACKAGES_DIR, research_one_v3, run_sample

LUV_SOURCE = "SEC:0000092380:0000092380-26-000006"
LUV_EVIDENCE = f"{LUV_SOURCE}:CHUNK:0"


def _luv_package() -> AIResearchInputV1:
    return AIResearchInputV1.model_validate_json((PACKAGES_DIR / "LUV.json").read_text())


def _valid_content() -> dict:
    return {
        "business_model": {
            "revenue_drivers": [{
                "text": "Southwest generates revenue primarily from passenger air transportation.",
                "claim_type": "FACT", "source_id": LUV_SOURCE, "evidence_id": LUV_EVIDENCE,
                "confidence": "HIGH",
            }],
            "segments": [], "customer_types": [], "geography": [], "cyclicality": None,
            "key_dependencies": [],
        },
        "fundamental_change": [],
        "growth_durability": {"state": "UNKNOWN", "rationale": [], "evidence_checklist": {}},
        "future_business": [], "competitive_position": [], "management_execution": [],
        "catalyst_candidates": [], "why_now_candidate": {"summary": "Worth continued research.",
                                                          "reasons": []},
        "risks": [], "invalidation_candidates": [], "evidence_conflicts": [],
        "open_questions": [], "unknown_fields": [], "sources": [LUV_SOURCE],
        "research_completeness": "PARTIAL",
    }


def _ok_response(cost=1.2, canonical="claude-opus-5-5") -> dict:
    return {"result": json.dumps(_valid_content()), "total_cost_usd": cost, "is_error": False,
           "modelUsage": {"claude-opus-5-5": {"canonicalModel": canonical}}}


def _invalid_response(cost=1.1) -> dict:
    bad = _valid_content()
    bad["business_model"]["revenue_drivers"][0]["source_id"] = None  # FACT with no source: rejected
    bad["business_model"]["revenue_drivers"][0]["evidence_id"] = None
    return {"result": json.dumps(bad), "total_cost_usd": cost, "is_error": False,
           "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}}}


def _error_response() -> dict:
    return {"result": "", "total_cost_usd": 0.0, "is_error": True}


# --- successful first attempt --------------------------------------------------------------------

def test_research_one_v3_succeeds_on_first_call():
    calls = []

    def stub(system, user):
        calls.append((system, user))
        return _ok_response()

    record = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-1", call_fn=stub)
    assert len(calls) == 1
    assert record.final_status == "OK"
    assert record.repair_attempts == []
    assert record.canonical_model == "claude-opus-5-5"
    assert record.model_mismatch is False
    assert record.final_output is not None
    assert record.final_output_checksum is not None
    assert record.cost_usd == 1.2


# --- repair lifecycle (brief §6/§17) ---------------------------------------------------------------

def test_research_one_v3_repairs_once_then_succeeds():
    responses = iter([_invalid_response(), _ok_response(cost=0.6)])

    def stub(system, user):
        return next(responses)

    record = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-2", call_fn=stub)
    assert record.final_status == "OK"
    assert len(record.repair_attempts) == 1
    assert record.repair_attempts[0].validation_after == "OK"
    assert record.repair_attempts[0].failure_codes_before == ["CITATION"]
    # both call costs accumulated
    assert round(record.cost_usd, 2) == round(1.1 + 0.6, 2)


def test_research_one_v3_exhausts_repair_budget_and_fails():
    def stub(system, user):
        return _invalid_response()

    record = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-3", call_fn=stub)
    assert record.final_status == "SCHEMA_VALIDATION_FAILED"
    assert len(record.repair_attempts) == 2  # MAX_REPAIR_ATTEMPTS
    assert record.final_output is None


def test_repair_prompt_is_schema_correction_only_never_asks_for_new_evidence_or_a_decision():
    """brief §6: the repair prompt must not solicit new evidence or an investment judgment - it
    must explicitly forbid both, not merely fail to mention them."""
    from app.backtest.strategy_h_v2.research.prompt_builder_v2 import build_repair_prompt_v2
    prompt = build_repair_prompt_v2("previous output", ["some error"])
    assert "do not change any factual claim, add new analysis, or introduce new evidence" in \
        prompt.lower()
    for banned in ("recommend", "buy", "sell", "fair value", "price target"):
        assert banned not in prompt.lower()


def test_model_call_error_on_initial_call():
    def stub(system, user):
        return _error_response()

    record = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-4", call_fn=stub)
    assert record.final_status == "MODEL_CALL_FAILED"
    assert record.canonical_model is None


def test_model_call_error_during_repair():
    responses = iter([_invalid_response(), _error_response()])

    def stub(system, user):
        return next(responses)

    record = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-5", call_fn=stub)
    assert record.final_status == "MODEL_CALL_FAILED"
    assert len(record.repair_attempts) == 1
    assert record.repair_attempts[0].validation_after == "MODEL_CALL_FAILED"


# --- canonical model mismatch (brief §7/§17) -----------------------------------------------------

def test_requested_canonical_model_mismatch_flagged():
    def stub(system, user):
        return _ok_response(canonical="claude-sonnet-5")

    record = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-6", call_fn=stub)
    assert record.model_mismatch is True
    # The record is still produced (so the mismatch is auditable), the mismatch flag is what marks
    # it invalid for gate-counting purposes downstream, not a silently dropped result.
    assert record.canonical_model == "claude-sonnet-5"


# --- immutability through the full pipeline (brief §5/§17) ----------------------------------------

def test_attempt_ids_are_unique_across_retries():
    def stub(system, user):
        return _ok_response()

    first = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-7", call_fn=stub)
    second = research_one_v3(_luv_package(), "LUV", "0000092380", "D3_3-TEST-7", call_fn=stub)
    assert first.attempt_id != second.attempt_id


# --- budget stop before, not after (brief §10/§17) -------------------------------------------------

def test_run_sample_stops_before_a_candidate_that_would_breach_budget(tmp_path):
    ticker_ciks = {"LUV": "0000092380", "WEN": "0000030697", "GOOG": "0001652044"}
    tiny_sample = tuple(
        SampleEntry(t, cik, "FULL", "E3_P1_HIGH") for t, cik in ticker_ciks.items()
    )

    def stub(system, user):
        return _ok_response(cost=1.0)

    manifest = run_sample(sample=tiny_sample, hard_budget_usd=6.5, call_fn=stub,
                          attempts_root=tmp_path, manifest_root=tmp_path)
    # worst_case=6.0: candidate 1 proceeds (0+6<=6.5), spends 1.0; candidate 2 proceeds
    # (1.0+6=7.0>6.5) -> STOPS before candidate 2, not after it partially runs.
    assert manifest["attempted"] == 1
    assert manifest["stopped_early"] == "WEN"
    assert manifest["total_cost_usd"] == 1.0


def test_run_sample_persists_every_attempt_immutably(tmp_path):
    tiny_sample = (SampleEntry("LUV", "0000092380", "FULL", "E3_P1_HIGH"),)

    def stub(system, user):
        return _ok_response()

    manifest = run_sample(sample=tiny_sample, hard_budget_usd=30.0, call_fn=stub,
                          attempts_root=tmp_path, manifest_root=tmp_path)
    stored = [p for p in tmp_path.rglob("*.json") if "manifest" not in p.name]
    assert len(stored) == 1
    assert manifest["results"][0]["final_status"] == "OK"
