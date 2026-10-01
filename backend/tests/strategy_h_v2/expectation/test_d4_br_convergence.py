"""H-V2-D4-BR §19: the convergence / abstention contract.

The question this file answers is the one §0 asked: when FRPT and SPSC produced no final output, was
that because the schema cannot express an honest "I do not know", or because the repair loop failed
to reach an expression the schema already had? The inline tests settle the affordance question on
fixtures, and the replay tests settle it on the actual Tier B payloads.

Nothing here regrades Tier B. `test_the_historical_verdict_is_immutable` exists to make that a
failing test rather than a promise.
"""

from __future__ import annotations

import json

import pytest
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
from app.backtest.strategy_h_v2.expectation.comparability import (
    ABSTENTION_GUIDANCE_STATES,
    ABSTENTION_RESULT_STATES,
    REQUIRED_OPERANDS,
    ComparisonKind,
    ComparisonUnavailableReason,
    assess_comparability,
    overall_guidance_state_errors,
    structural_prescan,
)
from app.backtest.strategy_h_v2.expectation.repair_provenance import (
    ProvenanceLock,
    check_repair_provenance,
)
from app.backtest.strategy_h_v2.expectation.validate import assemble_and_validate_d4

PKG = package()
BUNDLE = expectation_bundle()
FACTS = build_code_fact_index(BUNDLE)


def run(content: dict):
    return assemble_and_validate_d4(
        json.dumps(content), package=PKG, bundle=BUNDLE, research_output=D3_OUTPUT,
        code_facts=FACTS, analysis_id="A-1", version=1, model_name="claude-opus-5-5",
        model_version="opus", prompt_version="h_v2_d4_expectation_gap_v1", created_at=NOW,
    )


def expectation(**overrides):
    base = dict(d4_content()["market_expectation_evidence"])
    base.update(overrides)
    return base


def assessment(**overrides):
    item = {"metric": "Revenue", "state": "UNKNOWN", "unit": "USD",
            "claims": [claim()]}
    item.update(overrides)
    return item


def result_item(**overrides):
    item = {"metric": "Revenue", "state": "UNKNOWN", "unit": "USD", "claims": [claim()]}
    item.update(overrides)
    return item


# --- §6: operand completeness is deterministic and code-owned ----------------------------------

def test_required_operands_are_declared_per_comparison_kind():
    assert REQUIRED_OPERANDS[ComparisonKind.GUIDANCE_RANGE_CHANGE] == (
        "previous_low", "previous_high", "current_low", "current_high", "unit")
    assert REQUIRED_OPERANDS[ComparisonKind.RESULT_VS_COMPANY_GUIDANCE] == (
        "reported_value", "prior_guidance_low", "prior_guidance_high", "unit")


def test_a_complete_range_pair_is_comparable():
    verdict = assess_comparability(
        {"previous_low": 8, "previous_high": 11, "current_low": 10, "current_high": 12,
         "unit": "PERCENT"},
        ComparisonKind.GUIDANCE_RANGE_CHANGE)
    assert verdict.comparison_allowed
    assert verdict.missing_operands == ()
    assert verdict.comparison_unavailable_reason is None


def test_a_half_stated_previous_range_names_the_missing_bound():
    """FRPT's actual shape: 'at least 48%' to 'at least 49%' is not a range change."""
    verdict = assess_comparability(
        {"previous_low": 48, "previous_high": None, "current_low": 49, "current_high": None,
         "unit": "PERCENT"},
        ComparisonKind.GUIDANCE_RANGE_CHANGE)
    assert not verdict.comparison_allowed
    assert verdict.missing_operands == ("previous_high", "current_high")
    assert verdict.comparison_unavailable_reason is (
        ComparisonUnavailableReason.MISSING_PREVIOUS_HIGH)


def test_numbers_without_a_unit_are_not_comparable():
    """A dimensionless pair is the `dimension_swap` defect waiting to happen."""
    verdict = assess_comparability(
        {"reported_value": 197.8, "prior_guidance_low": 190, "prior_guidance_high": 195,
         "unit": None},
        ComparisonKind.RESULT_VS_COMPANY_GUIDANCE)
    assert not verdict.comparison_allowed
    assert verdict.comparison_unavailable_reason is ComparisonUnavailableReason.MISSING_UNIT


def test_an_item_with_no_numbers_at_all_needs_no_unit():
    """An absence is not an incomplete comparison, and must not be reported as a missing unit."""
    verdict = assess_comparability({}, ComparisonKind.GUIDANCE_RANGE_CHANGE)
    assert not verdict.comparison_allowed
    assert "unit" not in verdict.missing_operands


# --- §8: a comparative state is forbidden when the operands are absent -------------------------

def test_half_a_previous_range_forbids_a_comparative_state():
    analysis, errors = run(d4_content(market_expectation_evidence=expectation(
        overall_guidance_state="RAISED",
        guidance_assessments=[assessment(state="RAISED", previous_low=48, current_low=49,
                                         unit="PERCENT")])))
    assert analysis is None
    assert any("both bounds or neither" in e for e in errors)


def test_a_missing_company_guidance_operand_forbids_above_and_below():
    """SPSC's actual shape: a reported value with no prior-guidance bounds to compare it against."""
    for state in ("ABOVE_COMPANY_GUIDANCE", "BELOW_COMPANY_GUIDANCE"):
        analysis, errors = run(d4_content(market_expectation_evidence=expectation(
            result_vs_guidance=[result_item(state=state, reported_value=197.8)])))
        assert analysis is None, state
        assert any("is a comparison" in e for e in errors), state


def test_the_code_owned_fields_cannot_be_supplied_by_the_model():
    """§7: `comparison_allowed` is not a field the model may write. `extra="forbid"` is the teeth."""
    analysis, errors = run(d4_content(market_expectation_evidence=expectation(
        guidance_assessments=[assessment(comparison_allowed=True)])))
    assert analysis is None
    assert any("Extra inputs are not permitted" in e for e in errors)


# --- §9: honest abstention is a valid output, not a failure ------------------------------------

def test_a_missing_range_bound_abstention_validates():
    """The whole §9 claim, on a fixture: operands absent, state UNKNOWN, output VALID."""
    analysis, errors = run(d4_content(market_expectation_evidence=expectation(
        overall_guidance_state="UNKNOWN",
        guidance_assessments=[assessment(state="UNKNOWN")])))
    assert errors == []
    assert analysis is not None


def test_every_comparison_kind_has_a_reachable_abstention_state():
    assert ABSTENTION_GUIDANCE_STATES and ABSTENTION_RESULT_STATES
    for state in ("UNKNOWN", "NO_PRIOR_GUIDANCE"):
        analysis, errors = run(d4_content(market_expectation_evidence=expectation(
            result_vs_guidance=[result_item(state=state)])))
        assert errors == [], (state, errors)
        assert analysis is not None


def test_naming_an_unknown_quantity_in_unknown_fields_is_not_an_assertion():
    """§5. The abstention channel's contract is "what is NOT known", so the name of the thing being
    abstained from cannot be read as a claim about it. This is the single edit that turns SPSC's
    first repair from a failure into a valid output."""
    analysis, errors = run(d4_content(unknown_fields=["consensus expectations"]))
    assert errors == []
    assert analysis is not None


def test_a_figure_in_unknown_fields_is_still_rejected():
    """The exemption above is for names, not numbers. A figure there is the fabrication itself."""
    analysis, errors = run(d4_content(
        unknown_fields=["consensus expectations of $5.00 per share"]))
    assert analysis is None
    assert any("expectation FIGURE" in e for e in errors)


# --- §16: one error must not hide another -----------------------------------------------------

def test_the_aggregate_rule_is_reported_even_when_a_child_is_malformed():
    """The FRPT mechanism. A malformed assessment stops pydantic's parent validator from running, so
    before D4-BR the overall-state violation in the SAME payload was invisible for a whole round."""
    content = d4_content(market_expectation_evidence=expectation(
        overall_guidance_state="RAISED",
        guidance_assessments=[
            assessment(state="RAISED", previous_low=48, current_low=49, unit="PERCENT"),
            assessment(metric="EBITDA margin", state="MAINTAINED", previous_low=20,
                       previous_high=22, current_low=20, current_high=22, unit="PERCENT"),
        ]))
    analysis, errors = run(content)
    assert analysis is None
    assert any("both bounds or neither" in e for e in errors), "the child error"
    assert any("disagree, so the overall state is MIXED" in e for e in errors), "the masked one"


def test_the_prescan_mirrors_the_schema_rule_it_restates():
    """Two implementations of the aggregate rule, pinned against each other so they cannot drift."""
    disagreeing = expectation(
        overall_guidance_state="RAISED",
        guidance_assessments=[
            assessment(state="RAISED", previous_low=8, previous_high=11, current_low=10,
                       current_high=12, unit="PERCENT"),
            assessment(metric="EBITDA", state="MAINTAINED", previous_low=20, previous_high=22,
                       current_low=20, current_high=22, unit="PERCENT"),
        ])
    assert overall_guidance_state_errors(disagreeing)
    analysis, errors = run(d4_content(market_expectation_evidence=disagreeing))
    assert analysis is None
    assert any("disagree, so the overall state is MIXED" in e for e in errors)


def test_the_prescan_is_silent_on_a_valid_payload():
    """It may only ADD errors the schema would raise. On something valid it must say nothing."""
    assert structural_prescan(d4_content()) == []


def test_the_prescan_survives_a_malformed_payload():
    for junk in ({}, {"market_expectation_evidence": None},
                 {"market_expectation_evidence": {"guidance_assessments": "not a list"}},
                 {"market_expectation_evidence": {"overall_guidance_state": "NONSENSE"}}):
        assert structural_prescan(junk) == []


# --- §10/§11/§12: the repair contract ---------------------------------------------------------

def test_a_repair_may_downgrade_to_an_existing_unknown_state():
    initial = d4_content(market_expectation_evidence=expectation(
        result_vs_guidance=[result_item(state="ABOVE_COMPANY_GUIDANCE", reported_value=197.8)]))
    repaired = d4_content(market_expectation_evidence=expectation(
        result_vs_guidance=[result_item(state="UNKNOWN", reported_value=197.8)]))
    assert run(initial)[0] is None
    assert run(repaired)[1] == []
    assert check_repair_provenance(repaired, ProvenanceLock.from_content(initial)) == []


def test_a_repair_may_remove_the_unsupported_claim_entirely():
    initial = d4_content(market_expectation_evidence=expectation(
        result_vs_guidance=[result_item(state="ABOVE_COMPANY_GUIDANCE", reported_value=197.8)]))
    repaired = d4_content()
    assert run(repaired)[1] == []
    assert check_repair_provenance(repaired, ProvenanceLock.from_content(initial)) == []


def test_the_provenance_lock_allows_a_shrinking_universe():
    """Removal is the point. A repair that cites less than the initial answer is a repair working."""
    lock = ProvenanceLock.from_content(d4_content(supporting_claims=[claim()]))
    assert check_repair_provenance({"supporting_claims": []}, lock) == []


def test_a_repair_cannot_introduce_a_consensus_evidence_category():
    """§11's exact prohibition: a refused company-guidance thesis coming back as a consensus one."""
    lock = ProvenanceLock.from_content(d4_content(supporting_claims=[claim()]))
    errors = check_repair_provenance(
        {"supporting_claims": [claim(source_id="CONSENSUS:IBES:MSFT",
                                     evidence_id="CONSENSUS:IBES:MSFT:Q2")]},
        lock, citable_source_ids={SOURCE_ID}, citable_evidence_ids={EVIDENCE_ID})
    assert any("evidence category 'CONSENSUS'" in e for e in errors)
    assert any("provenance.source_id" in e for e in errors)
    # The category is the finding and the ids are the detail, so it is reported first.
    assert "evidence category 'CONSENSUS'" in errors[0]


def test_a_repair_may_attach_already_present_valid_evidence():
    """§10 permits exactly this, and it is the usual fix for a citation defect: cite the bundle
    evidence id the claim should have carried. The initial answer by definition did not cite it, so an
    id-level freeze would reject the repair the contract asks for."""
    lock = ProvenanceLock.from_content(d4_content())
    repaired = {"supporting_claims": [claim()]}
    assert check_repair_provenance(
        repaired, lock,
        citable_source_ids={SOURCE_ID}, citable_evidence_ids={EVIDENCE_ID}) == []


def test_a_repair_cannot_cite_an_id_outside_the_citable_universe():
    """§12's "does not exist": an id in an allowed category that code never offered."""
    lock = ProvenanceLock.from_content(d4_content(supporting_claims=[claim()]))
    errors = check_repair_provenance(
        {"supporting_claims": [claim(source_id=SOURCE_ID,
                                     evidence_id=f"{EVIDENCE_ID}:FABRICATED")]},
        lock, citable_source_ids={SOURCE_ID}, citable_evidence_ids={EVIDENCE_ID})
    assert any("not in the code-owned citable" in e for e in errors)
    assert not any("evidence category" in e for e in errors)


def test_without_a_citable_set_no_id_level_check_runs():
    """Guessing the universe and rejecting against the guess would reject honest repairs."""
    lock = ProvenanceLock.from_content(d4_content())
    assert check_repair_provenance({"supporting_claims": [claim()]}, lock) == []


def test_the_lock_harvests_both_singular_and_plural_id_fields():
    lock = ProvenanceLock.from_content(
        {"a": {"source_id": "SEC:1", "evidence_ids": ["SEC:1:C0", "SEC:1:C1"]},
         "b": [{"evidence_id": "CODE:D4:x"}]})
    assert lock.source_ids == frozenset({"SEC:1"})
    assert lock.evidence_ids == frozenset({"SEC:1:C0", "SEC:1:C1", "CODE:D4:x"})
    assert lock.categories == frozenset({"SEC", "CODE"})


# --- §17: the D3 figure is a planning estimate, and it was exceeded ---------------------------

def test_the_d3_figure_is_labelled_a_planning_estimate_that_was_exceeded():
    from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
        D3_OBSERVED_MAX_CANDIDATE_USD,
        D3_PLANNING_ESTIMATE_CANDIDATE_USD,
        D3_WORST_CASE_CANDIDATE_USD,
    )
    assert D3_PLANNING_ESTIMATE_CANDIDATE_USD == 1.60
    assert D3_WORST_CASE_CANDIDATE_USD == D3_PLANNING_ESTIMATE_CANDIDATE_USD
    assert D3_OBSERVED_MAX_CANDIDATE_USD > D3_PLANNING_ESTIMATE_CANDIDATE_USD


def test_the_tier_b_hard_cap_is_unchanged():
    """§1 forbids touching the budget. The relabelling must not have moved a number."""
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import TIER_B_HARD_CAP_USD
    assert TIER_B_HARD_CAP_USD == 45.60


# --- §15/§16: diagnostics survive a candidate with no final output -----------------------------

def test_the_record_carries_terminal_failure_and_fabrication_diagnostics():
    from app.backtest.strategy_h_v2.expectation.ledger import (
        LEDGER_SCHEMA_VERSION,
        D4AnalysisRecordV1,
    )
    from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1
    raw = RawResponseRecordV1.capture(candidate_id="FRPT", attempt=0, role="initial",
                                      raw_text="{}", prompt_version="p")
    record = D4AnalysisRecordV1(
        schema_version=LEDGER_SCHEMA_VERSION, analysis_id="A", analysis_run_id="R",
        candidate_id="C", ticker="FRPT", research_input_id="r", research_output_checksum="x",
        expectation_evidence_id="e", expectation_evidence_checksum="y", input_package_id="p",
        input_package_checksum="z", model_requested="m", canonical_model="m", model_mismatch=False,
        prompt_version="p", schema_version_out="s", gap_contract_version="g",
        validation_contract_version="v", started_at="t", completed_at="t",
        initial_raw_response=raw, initial_parse_status="PARSED",
        initial_validation_status="FAILED", final_status="SCHEMA_VALIDATION_FAILED",
        terminal_failure_codes=["SCHEMA"],
        terminal_failure_details=["per-metric states disagree"],
        attempted_consensus_attributions=1, attempted_quantified_consensus=0,
        final_fabricated_consensus=0,
    )
    as_dict = record.to_dict()
    assert as_dict["terminal_failure_details"] == ["per-metric states disagree"]
    assert as_dict["attempted_consensus_attributions"] == 1
    assert as_dict["attempted_quantified_consensus"] == 0
    assert as_dict["final_fabricated_consensus"] == 0
    assert LEDGER_SCHEMA_VERSION.endswith("_v2"), "a new field shape needs a new version"


def test_attempted_and_final_consensus_are_counted_separately():
    """§15. An attribution trip is not by itself a fabrication; a FIGURE is. Tier B conflated them."""
    from app.dev.run_strategy_h_v2_d4_2 import _consensus_diagnostics
    attribution_only = ["a sentence attributes an expectation to analysts or the market ('x')"]
    quantified = ["a sentence attributes an expectation to analysts or the market by stating a "
                  "figure ('consensus of $5.00')"]
    assert _consensus_diagnostics(attribution_only) == (1, 0)
    assert _consensus_diagnostics(quantified) == (1, 1)
    assert _consensus_diagnostics([]) == (0, 0)


def test_the_audit_reports_repair_rounds_for_an_invalid_final_candidate():
    """The §16 reporting bug, as a test. Tier B emitted `{has_final_output: False, defects: {}}` for
    FRPT and SPSC and nothing else - no attempt, no rounds, no cause, no cost."""
    import inspect

    from app.dev import audit_strategy_h_v2_d4_b as audit
    source = inspect.getsource(audit.audit_run)
    assert "_repair_diagnostics(record)" in source
    head, _, tail = source.partition('row.update({"has_final_output": False, "defects": {}})')
    assert tail.lstrip().startswith("row.update(_repair_diagnostics(record))"), (
        "the invalid-final branch must enrich the row before appending it")


# --- §13/§14/§20: the offline counterfactual, on the real Tier B payloads ----------------------

try:
    from app.dev.replay_d4_br_convergence import (
        ANALYSES_ROOT,
        HISTORICAL,
        TIER_B_RUN_ID,
        replay,
    )
    _HAVE_REPLAY = (ANALYSES_ROOT / TIER_B_RUN_ID).exists()
except Exception:                                    # pragma: no cover - import-time only
    _HAVE_REPLAY = False

NEEDS_STORED_RUN = pytest.mark.skipif(
    not _HAVE_REPLAY, reason="the stored Tier B run is gitignored runtime data")


@pytest.fixture(scope="module")
def report():
    return replay()


@NEEDS_STORED_RUN
def test_the_replay_is_free(report):
    assert report["live_calls"] == 0 and report["live_cost_usd"] == 0.0


@NEEDS_STORED_RUN
def test_the_historical_verdict_is_immutable(report):
    """§2/§20. The replay may discover anything at all; Tier B still failed, at 4 of 6."""
    assert report["historical"] == HISTORICAL
    assert HISTORICAL["d4_final_ok"] == 4 and HISTORICAL["sample_n"] == 6
    assert HISTORICAL["e1_verdict"] == "FAIL" and HISTORICAL["tier_b_verdict"] == "FAIL"
    assert HISTORICAL["final_fabricated_consensus"] == 0
    statuses = {c["ticker"]: c["historical_final_status"] for c in report["candidates"]}
    assert {t for t, s in statuses.items() if s != "OK"} == set(HISTORICAL["no_final_output"])


@NEEDS_STORED_RUN
def test_frpt_evidence_is_representable_as_a_valid_abstention(report):
    """§13. One enum edit on the payload FRPT actually produced, and it validates."""
    frpt = next(c for c in report["candidates"] if c["ticker"] == "FRPT")
    assert frpt["counterfactual"]["valid"], frpt["counterfactual"]["errors"]
    assert frpt["counterfactual"]["base_round"] == "repair2"


@NEEDS_STORED_RUN
def test_spsc_evidence_is_representable_as_a_valid_abstention(report):
    """§14. SPSC's first repair had already abstained on both comparisons."""
    spsc = next(c for c in report["candidates"] if c["ticker"] == "SPSC")
    assert spsc["counterfactual"]["valid"], spsc["counterfactual"]["errors"]
    assert spsc["counterfactual"]["base_round"] == "repair1"


@NEEDS_STORED_RUN
def test_the_terminal_failure_reasons_were_recovered_not_recorded(report):
    """§16, measured. Both failures ended on a rule the live record never named, because the record
    stored only each round's PRECEDING error."""
    by_ticker = {c["ticker"]: c for c in report["candidates"]}
    frpt, spsc = by_ticker["FRPT"], by_ticker["SPSC"]
    assert frpt["record_stored_terminal_failure"] == []
    assert spsc["record_stored_terminal_failure"] == []
    assert any("overall state is MIXED" in e for e in frpt["terminal_errors_recovered"])
    assert any("attributes an expectation" in e for e in spsc["terminal_errors_recovered"])


@NEEDS_STORED_RUN
def test_the_four_graded_candidates_still_validate(report):
    """No regression: every candidate that reached a final output still reaches one."""
    for candidate in report["candidates"]:
        if candidate["historical_final_status"] != "OK":
            continue
        assert any(r["valid"] for r in candidate["rounds"]), candidate["ticker"]


# --- §21/§22: the confirmation preregistration -------------------------------------------------

def test_the_confirmation_sample_regenerates_from_the_universe():
    """A hardcoded list checksummed by hashing itself proves nothing - re-derive it."""
    from app.backtest.strategy_h_v2.expectation import d4_br_contract as br
    assert br.regenerate_confirmation_sample() == br.D4_BR_CONFIRMATION_SAMPLE
    assert br.confirmation_checksum() == br.D4_BR_CONFIRMATION_CHECKSUM
    assert len(br.D4_BR_CONFIRMATION_SAMPLE) == 4


def test_the_confirmation_sample_is_disjoint_from_every_prior_h_live_cik():
    from app.backtest.strategy_h_v2.expectation import d4_br_contract as br
    from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
        EXCLUDED_CIKS,
        TIER_B_SAMPLE,
    )
    chosen = {e.cik for e in br.D4_BR_CONFIRMATION_SAMPLE}
    assert chosen & EXCLUDED_CIKS == set()
    assert chosen & {e.cik for e in TIER_B_SAMPLE} == set()
    assert len(chosen) == 4, "four distinct issuers"
    assert len(br.D4_BR_EXCLUDED_CIKS) == 54


def test_the_confirmation_reuses_the_frozen_e1_threshold():
    """§22. 4/4 is what >= 95% means at n=4; it is not a new threshold."""
    from app.backtest.strategy_h_v2.expectation.d4_1_contract import E1_MIN_SCHEMA_VALID_RATE
    from app.backtest.strategy_h_v2.expectation.d4_br_contract import (
        D4_BR_E1_REQUIRED_FINAL_VALID,
        e1_confirmation_passes,
    )
    assert E1_MIN_SCHEMA_VALID_RATE == 0.95
    assert D4_BR_E1_REQUIRED_FINAL_VALID == 4
    assert e1_confirmation_passes(4) and not e1_confirmation_passes(3)
    assert not e1_confirmation_passes(0, attempted=0)


def test_the_preregistration_authorizes_no_spending():
    """§1. This step makes no live call, so the contract must carry no runner and no budget."""
    import inspect

    from app.backtest.strategy_h_v2.expectation import d4_br_contract as br
    source = inspect.getsource(br)
    assert "call_opus" not in source and "HARD_CAP" not in source


# --- §18: R3 status moves, R3 does not get implemented -----------------------------------------

def test_r3_is_recorded_as_observed_and_still_deferred():
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
        M8_ATOMIC_NUMERIC_OWNERSHIP,
        M8_R3_COMPOUND_SET_VALUED,
        M8_R3_OBSERVED_EXPOSURE,
        M8_R3_STATUS_AFTER_TIER_B,
    )
    assert M8_R3_STATUS_AFTER_TIER_B == "OBSERVED / DEFERRED"
    assert M8_R3_OBSERVED_EXPOSURE == 1, "CRK; it was 0 across the D4-H corpus"
    assert M8_ATOMIC_NUMERIC_OWNERSHIP == "ACTIVE", "M8's unit of comparison is unchanged"
    assert M8_R3_COMPOUND_SET_VALUED == "DEFERRED", (
        "Tier B's own literal, which its frozen tests and its gate audit assert - D4-BR records the "
        "new status beside it rather than rewriting what a graded run reported")


def test_the_m8_gate_list_is_unchanged_by_this_step():
    """§1 forbids a gate-semantics change. R3 moving to OBSERVED must not wire it to a gate."""
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import M8_SCOPE_NOTE
    assert "wired to no gate" in M8_SCOPE_NOTE


def test_the_lock_watches_the_top_level_sources_list():
    """The D4 schema's `sources[]` must cover every citation, so it is where a new provider would be
    declared. A lock that watched only the per-claim keys would miss exactly that."""
    lock = ProvenanceLock.from_content({"sources": [SOURCE_ID]})
    assert lock.source_ids == frozenset({SOURCE_ID})
    errors = check_repair_provenance({"sources": [SOURCE_ID, "CONSENSUS:IBES:X"]}, lock)
    assert any("evidence category 'CONSENSUS'" in e for e in errors)


def test_the_validation_contract_version_moves_and_the_graded_ones_stay_readable():
    """Tier B was graded under V2 and a payload V2 rejected can now be accepted, so the version a
    record stores has to distinguish them. The older literals stay, for reading older records."""
    from app.backtest.strategy_h_v2.expectation.validate import (
        VALIDATION_CONTRACT_VERSION,
        VALIDATION_CONTRACT_VERSION_V1,
        VALIDATION_CONTRACT_VERSION_V2,
    )
    assert VALIDATION_CONTRACT_VERSION == "h_v2_d4_validation_contract_v3"
    assert VALIDATION_CONTRACT_VERSION_V2 == "h_v2_d4_validation_contract_v2"
    assert VALIDATION_CONTRACT_VERSION_V1 == "h_v2_d4_validation_contract_v1"
