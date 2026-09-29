"""Immutable ledger, upstream linkage, prompt contract, and the frozen D4.1 preregistration."""

from __future__ import annotations

import json

import pytest
from d4_helpers import D3_OUTPUT, chunk, excerpt, expectation_bundle, package

from app.backtest.strategy_h_v2.expectation import d4_1_contract as pilot
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.evidence_schema import EvidenceBlock
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability
from app.backtest.strategy_h_v2.expectation.ledger import (
    AnalysisAlreadyExistsError,
    D4AnalysisRecordV1,
    load_analysis,
    store_analysis,
    store_evidence_bundle,
    verify_linkage,
)
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION, build_d4_prompt
from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1, checksum


def _raw(text: str = '{"a": 1}') -> RawResponseRecordV1:
    return RawResponseRecordV1.capture(candidate_id="ACME", attempt=0, role="initial",
                                        raw_text=text, prompt_version=PROMPT_VERSION)


def _record(analysis_id: str = "A-1", **overrides) -> D4AnalysisRecordV1:
    base = dict(
        schema_version="h_v2_d4_ledger_v1", analysis_id=analysis_id, analysis_run_id="D4_1-X",
        candidate_id="1", ticker="ACME", research_input_id="R-1",
        research_output_checksum="rc", expectation_evidence_id="EB-1",
        expectation_evidence_checksum="ec", input_package_id="RUN-1:ACME",
        input_package_checksum="pc", model_requested="claude-opus-5-5",
        canonical_model="claude-opus-5-5", model_mismatch=False, prompt_version=PROMPT_VERSION,
        schema_version_out="h_expectation_gap_analysis_v1",
        gap_contract_version="h_v2_d4_gap_contract_v1",
        validation_contract_version="h_v2_d4_validation_contract_v1",
        started_at="2026-09-29T00:00:00Z", completed_at="2026-09-29T00:01:00Z",
        initial_raw_response=_raw(), initial_parse_status="OK",
        initial_validation_status="OK", final_status="OK",
    )
    base.update(overrides)
    return D4AnalysisRecordV1(**base)


# --- immutability ------------------------------------------------------------------------------

def test_a_stored_analysis_cannot_be_overwritten(tmp_path):
    store_analysis(tmp_path, _record())
    with pytest.raises(AnalysisAlreadyExistsError, match="new analysis_id"):
        store_analysis(tmp_path, _record())


def test_a_rerun_with_a_new_id_coexists_with_the_original(tmp_path):
    first = store_analysis(tmp_path, _record("A-1"))
    second = store_analysis(tmp_path, _record("A-2"))
    assert first.exists() and second.exists() and first != second


def test_an_evidence_bundle_is_equally_immutable(tmp_path):
    """The analysis's evidence checksum is meaningless if the bundle it points at can be rewritten
    afterwards."""
    store_evidence_bundle(tmp_path, "RUN", "ACME", "{}")
    with pytest.raises(AnalysisAlreadyExistsError):
        store_evidence_bundle(tmp_path, "RUN", "ACME", "{}")


def test_the_raw_response_is_stored_in_full_never_a_preview(tmp_path):
    """D3.1's MRVI failure became unauditable because only a 1,500-character preview survived."""
    long_text = "x" * 40_000
    path = store_analysis(tmp_path, _record(initial_raw_response=_raw(long_text)))
    stored = load_analysis(path)
    assert stored["initial_raw_response"]["raw_text"] == long_text
    assert stored["initial_raw_response"]["checksum"] == checksum(long_text)


def test_token_counts_absent_from_a_response_stay_none_rather_than_invented(tmp_path):
    stored = load_analysis(store_analysis(tmp_path, _record()))
    assert stored["input_tokens"] is None and stored["output_tokens"] is None


# --- linkage -----------------------------------------------------------------------------------

def test_linkage_verification_passes_on_matching_upstream_artifacts():
    research, bundle = json.dumps(D3_OUTPUT), "{}"
    record = _record(research_output_checksum=checksum(research),
                     expectation_evidence_checksum=checksum(bundle)).to_dict()
    assert verify_linkage(record, research_output_json=research,
                          evidence_bundle_json=bundle) == []


def test_a_changed_d3_output_is_reported_not_repaired():
    """Recomputing the checksum to make it agree would destroy the only evidence that the analysis
    interpreted a different D3 output than the one now on disk."""
    record = _record(research_output_checksum="stale").to_dict()
    problems = verify_linkage(record, research_output_json=json.dumps(D3_OUTPUT),
                              evidence_bundle_json="{}")
    assert any("did not read the D3 output it points at" in p for p in problems)


def test_a_final_output_checksum_mismatch_is_reported():
    research, bundle = json.dumps(D3_OUTPUT), "{}"
    record = _record(
        research_output_checksum=checksum(research),
        expectation_evidence_checksum=checksum(bundle),
        final_output={"expectation_gap": "UNKNOWN"}, final_output_checksum="wrong",
    ).to_dict()
    problems = verify_linkage(record, research_output_json=research, evidence_bundle_json=bundle)
    assert any("final_output_checksum" in p for p in problems)


# --- prompt ------------------------------------------------------------------------------------

def _prompt():
    pkg = package(chunks=[chunk("We now expect full-year revenue of $1.0 to $1.1 billion.")])
    bundle = expectation_bundle(guidance=EvidenceBlock(
        status=EvidenceAvailability.AVAILABLE, excerpts=[excerpt()],
    ))
    return build_d4_prompt(package=pkg, bundle=bundle, research_output=D3_OUTPUT,
                            code_facts=build_code_fact_index(bundle))


def test_the_prompt_is_deterministic():
    assert _prompt() == _prompt()


def test_the_prompt_states_the_c1_asymmetry_with_its_reason():
    system, _ = _prompt()
    assert "can never show that the market is BEHIND" in system
    assert "A GREAT COMPANY IS NOT A POSITIVE EXPECTATION GAP" in system


def test_the_prompt_forbids_consensus_language_and_supplies_the_replacement():
    system, _ = _prompt()
    assert "Available evidence does not establish consensus expectations." in system
    assert "2,010 of 2,010" in system


def test_the_prompt_never_offers_a_decision_or_valuation_vocabulary():
    system, _ = _prompt()
    for banned in ("APPROVE / WATCH / REJECT", "fair value", "price target"):
        assert banned in system, "the prohibition must be stated"
    assert "you are not in a position to reach either conclusion" in system


def test_the_embedded_schema_excludes_orchestration_metadata():
    system, _ = _prompt()
    schema = json.loads(system.split("JSON Schema (content fields only):\n", 1)[1])
    assert "analysis_id" not in schema["properties"]
    assert "d6_approve_precondition" not in schema["properties"]
    assert "expectation_gap" in schema["properties"]


def test_the_user_prompt_carries_the_d3_output_and_the_code_facts():
    _, user = _prompt()
    assert "IMMUTABLE D3 RESEARCH OUTPUT" in user
    assert "CODE-OWNED FACTS" in user
    assert "UNTRUSTED_RESEARCH_DATA" in user


def test_an_oversized_d3_output_stops_rather_than_truncating_the_reality_side():
    pkg, bundle = package(), expectation_bundle()
    huge = {**D3_OUTPUT, "open_questions": ["x" * 200_000]}
    with pytest.raises(ValueError, match="over the"):
        build_d4_prompt(package=pkg, bundle=bundle, research_output=huge, code_facts={})


# --- frozen D4.1 preregistration ------------------------------------------------------------------

def test_the_exclusion_set_covers_every_issuer_any_opus_call_has_touched():
    assert len(pilot.EXCLUDED_CIKS) == 48


def test_the_tier_b_sample_is_disjoint_from_every_touched_issuer():
    assert not ({e.cik for e in pilot.TIER_B_SAMPLE} & pilot.EXCLUDED_CIKS)


def test_the_frozen_samples_match_their_own_checksums():
    assert pilot.tier_a_checksum() == pilot.TIER_A_CHECKSUM
    assert pilot.tier_b_checksum() == pilot.TIER_B_CHECKSUM


def test_tier_a_regenerates_from_the_d3_3_sample_by_the_declared_seed():
    assert pilot.regenerate_tier_a_from_d3_3() == pilot.TIER_A_TICKERS


@pytest.mark.skipif(not pilot.PACKAGES_DIR.exists(), reason="D2.1 snapshot not present")
def test_tier_b_regenerates_from_the_universe_rather_than_hashing_itself():
    """A hardcoded list checksummed by hashing itself proves nothing about whether it was produced
    the declared way."""
    assert pilot.regenerate_tier_b_from_universe() == pilot.TIER_B_SAMPLE


def test_the_frozen_sample_fits_its_own_worst_case_inside_the_budget():
    worst = (pilot.TIER_A_N * pilot.D4_WORST_CASE_CANDIDATE_USD
             + (pilot.TIER_B_N_FULL + pilot.TIER_B_N_CORE)
             * pilot.TIER_B_WORST_CASE_CANDIDATE_USD)
    assert worst <= pilot.D4_1_HARD_BUDGET_USD


def _gates(**overrides):
    base = dict(attempted=6, schema_valid=6, unsourced_material_gap_claims=0,
                fabricated_consensus=0, future_source_leaks=0, code_owned_numeric_defects=0,
                unknown_discipline_violations=0, decision_leaks=0,
                unsupported_priced_in_claims=0)
    base.update(overrides)
    return pilot.evaluate_d4_1_gates(**base)


def test_a_clean_tier_b_run_passes():
    assert pilot.d4_1_verdict(_gates()) == pilot.D41Verdict.PASS


@pytest.mark.parametrize("gate,kwargs", [
    ("E3", {"fabricated_consensus": 1}),
    ("E4", {"future_source_leaks": 1}),
    ("E5", {"code_owned_numeric_defects": 1}),
    ("E7", {"decision_leaks": 1}),
])
def test_any_core_gate_failure_is_a_fail_never_a_limitation(gate: str, kwargs: dict):
    results = _gates(**kwargs)
    assert {g.gate: g.status for g in results}[gate] == pilot.GateStatus.FAIL
    assert pilot.d4_1_verdict(results) == pilot.D41Verdict.FAIL


def test_an_unperformed_manual_gate_is_not_evaluated_never_a_silent_pass():
    results = _gates(unknown_discipline_violations=None, unsupported_priced_in_claims=None)
    by_id = {g.gate: g for g in results}
    assert by_id["E6"].status == pilot.GateStatus.NOT_EVALUATED
    assert by_id["E8"].status == pilot.GateStatus.NOT_EVALUATED
    assert pilot.d4_1_verdict(results) == pilot.D41Verdict.PASS_WITH_LIMITATIONS


def test_tier_a_can_never_return_pass_because_it_evaluates_no_content_gate():
    """Its issuers are not unseen, so a content-quality result from it would not mean what a gate
    result is supposed to mean."""
    results = pilot.evaluate_d4_1_gates(
        attempted=3, schema_valid=3, unsourced_material_gap_claims=0, fabricated_consensus=0,
        future_source_leaks=0, code_owned_numeric_defects=0, unknown_discipline_violations=None,
        decision_leaks=0, unsupported_priced_in_claims=None, tier="A",
    )
    assert {g.gate: g.status for g in results}["E3"] == pilot.GateStatus.NOT_EVALUATED
    assert pilot.d4_1_verdict(results, tier="A") == pilot.D41Verdict.PASS_WITH_LIMITATIONS


def test_the_gate_list_records_what_it_does_not_prove():
    assert "Alpha is D6 Decision plus D7 Forward Shadow" in pilot.E_GATES_DO_NOT_PROVE
