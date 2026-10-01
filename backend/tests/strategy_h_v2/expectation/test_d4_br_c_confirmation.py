"""H-V2-D4-BR-C: the confirmation's execution contract, asserted before its first live call.

What these tests are for. D4-BR preregistered a sample and a success rule and was forbidden to spend;
D4-BR-C is the run. Everything that could quietly make the run easier than the one that was
preregistered is pinned here: the ceiling, the sample, the fact that the gates are Tier B's own
functions rather than copies, and the fact that the one piece of new judgement in the chain can only
make a result worse than the gates found it.

They also pin the parameterization of the Tier B runner and audit, because a confirmation that reuses
Tier B's orchestration is only meaningfully "the same pipeline" if Tier B's own defaults did not move.
"""

from __future__ import annotations

import inspect

from app.backtest.strategy_h_v2.expectation import d4_br_c_contract as C
from app.backtest.strategy_h_v2.expectation.contract_v2 import MAX_REPAIR_ATTEMPTS
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    E1_MIN_SCHEMA_VALID_RATE,
    EXCLUDED_CIKS,
    GateStatus,
    TIER_B_SAMPLE,
    evaluate_d4_1_gates,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import TIER_B_BUDGET
from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
    D4_B_CORE_GATES,
    D4_B_GATES,
    D4BVerdict,
    TIER_B_HARD_CAP_USD,
    d4_b_verdict,
    d5_authorization,
)
from app.backtest.strategy_h_v2.expectation.d4_br_contract import (
    D4_BR_CONFIRMATION_CHECKSUM,
    D4_BR_CONFIRMATION_SAMPLE,
    confirmation_checksum,
    e1_confirmation_passes,
)


# --- Budget: $30.40, a ceiling bound to the accounting it came from ----------------------------

def test_the_hard_cap_is_four_candidate_worst_cases_and_not_a_literal():
    assert C.D4_BR_C_HARD_CAP_USD == 30.40
    assert C.D4_BR_C_N == 4
    assert TIER_B_BUDGET.candidate_worst_case_budget_usd == 7.60, (
        "the cap is derived from this; if the per-candidate worst case moved, so did the ceiling"
    )
    assert "30.40" not in inspect.getsource(C).split('"""')[0], (
        "the ceiling is computed from TIER_B_BUDGET, never typed as the operative value"
    )


def test_the_cap_admits_its_own_sample_and_refuses_one_more():
    """A cap that cannot fund its fourth candidate would truncate the sample rather than bound it -
    the IEEE-754 truncation D4-B found on its own dry run, pinned here at n=4."""
    spent = 0.0
    for i in range(C.D4_BR_C_N):
        assert TIER_B_BUDGET.admits_next_candidate(
            spent_so_far_usd=spent, overall_hard_cap_usd=C.D4_BR_C_HARD_CAP_USD), i
        spent += TIER_B_BUDGET.candidate_worst_case_budget_usd
    assert not TIER_B_BUDGET.admits_next_candidate(
        spent_so_far_usd=spent, overall_hard_cap_usd=C.D4_BR_C_HARD_CAP_USD)


def test_the_cap_is_smaller_than_tier_bs_and_is_not_expanded_automatically():
    assert C.D4_BR_C_HARD_CAP_USD < TIER_B_HARD_CAP_USD, "four candidates, not six"
    assert C.D4_BR_C_NO_AUTOMATIC_EXPANSION is True
    assert C.OBSERVED_PROJECTION_USD == 11.06
    assert C.OBSERVED_PROJECTION_USD < C.D4_BR_C_HARD_CAP_USD, (
        "the projection is a diagnostic under the ceiling; the ceiling does the gating"
    )


# --- Sample: the preregistered four, unchanged -------------------------------------------------

def test_the_run_uses_the_preregistered_sample_and_recomputes_its_checksum():
    integrity = C.sample_integrity()
    assert [row["ticker"] for row in integrity["sample"]] == ["AEYE", "COLL", "FG", "VRRM"]
    assert integrity["checksum_recomputed"] == D4_BR_CONFIRMATION_CHECKSUM
    assert integrity["checksum_matches"] is True
    assert confirmation_checksum(D4_BR_CONFIRMATION_SAMPLE) == D4_BR_CONFIRMATION_CHECKSUM


def test_the_sample_is_still_disjoint_from_every_prior_h_live_issuer():
    chosen = {e.cik for e in D4_BR_CONFIRMATION_SAMPLE}
    assert chosen & EXCLUDED_CIKS == set()
    assert chosen & {e.cik for e in TIER_B_SAMPLE} == set()


def test_the_confirmation_writes_to_its_own_store():
    """A confirmation record under Tier B's root could be read as a Tier B record."""
    assert C.D4_BR_C_ROOT != __import__(
        "app.backtest.strategy_h_v2.expectation.d4_b_contract",
        fromlist=["D4_B_ROOT"]).D4_B_ROOT
    assert str(C.D4_BR_C_ROOT).endswith("d4_br_c")
    assert C.D4_BR_C_RUN_ID_PREFIX == "D4_BR_C" and C.D4_BR_C_RUN_ID_PREFIX != "D4_B"


# --- E1 at n=4: the frozen rate, not a new number ----------------------------------------------

def test_e1_at_n4_is_the_frozen_rate_and_the_two_readings_agree():
    assert E1_MIN_SCHEMA_VALID_RATE == 0.95

    def e1_status(valid: int) -> str:
        gates = evaluate_d4_1_gates(
            attempted=4, schema_valid=valid, unsourced_material_gap_claims=0,
            fabricated_consensus=0, future_source_leaks=0, code_owned_numeric_defects=0,
            unknown_discipline_violations=0, decision_leaks=0,
            unsupported_priced_in_claims=0, tier="B")
        return next(g for g in gates if g.gate == "E1").status.value

    for valid in range(5):
        assert (e1_status(valid) == GateStatus.PASS.value) == e1_confirmation_passes(valid, 4), (
            f"the gate and the confirmation rule disagree at {valid}/4"
        )
    assert e1_status(4) == GateStatus.PASS.value
    assert e1_status(3) == GateStatus.FAIL.value, "3/4 is 75%, not a near miss"


# --- The verdict wrapper can only ever make a result worse -------------------------------------

def _statuses(**overrides) -> dict[str, str]:
    base = {g: GateStatus.PASS.value for g in D4_B_GATES}
    base.update(overrides)
    return base


def test_the_wrapper_passes_a_clean_run_through_unchanged():
    assert C.d4_br_c_verdict(_statuses()) is D4BVerdict.PASS
    assert d5_authorization(C.d4_br_c_verdict(_statuses())) == "READY FOR CONTRACT DESIGN"


def test_high_repair_dependence_downgrades_a_pass_to_pass_with_limitations():
    verdict = C.d4_br_c_verdict(_statuses(), repair_dependence_high=True)
    assert verdict is D4BVerdict.PASS_WITH_LIMITATIONS
    assert d5_authorization(verdict) == "READY FOR CONTRACT DESIGN", (
        "§15: a repair-heavy 4/4 still authorizes D5 CONTRACT DESIGN; it is the claim that is "
        "qualified, not the next step"
    )


def test_structural_instability_downgrades_a_pass_the_same_way():
    assert C.d4_br_c_verdict(
        _statuses(), structural_instability_found=True) is D4BVerdict.PASS_WITH_LIMITATIONS


def test_the_wrapper_can_never_upgrade_a_result():
    """The property that makes adding it safe. Over every gate-status combination the frozen
    function can produce, the wrapper's output is never better than the frozen one."""
    order = {D4BVerdict.FAIL: 0, D4BVerdict.PASS_WITH_LIMITATIONS: 1, D4BVerdict.PASS: 2}
    cases = [
        _statuses(),
        _statuses(E1=GateStatus.FAIL.value),
        _statuses(SF1=GateStatus.NOT_EVALUATED.value),
        _statuses(E3=GateStatus.FAIL.value),
        _statuses(E6=GateStatus.NOT_EVALUATED.value),
        _statuses(E5=GateStatus.NOT_EVALUATED.value),
    ]
    for statuses in cases:
        frozen = d4_b_verdict(statuses)
        for dependence in (False, True):
            for instability in (False, True):
                got = C.d4_br_c_verdict(statuses, repair_dependence_high=dependence,
                                        structural_instability_found=instability)
                assert order[got] <= order[frozen], (statuses, dependence, instability)
    assert C.d4_br_c_verdict(_statuses(E3=GateStatus.FAIL.value),
                             repair_dependence_high=False) is D4BVerdict.FAIL


def test_a_core_gate_failure_is_still_fatal_through_the_wrapper():
    for gate in D4_B_CORE_GATES:
        assert C.d4_br_c_verdict(_statuses(**{gate: GateStatus.FAIL.value})) is D4BVerdict.FAIL
        assert d5_authorization(
            C.d4_br_c_verdict(_statuses(**{gate: GateStatus.FAIL.value}))) == "NOT READY"


def test_repair_dependence_is_most_of_four_and_frozen_before_the_run():
    assert C.REPAIR_DEPENDENCE_FULL_BUDGET_ROUNDS == MAX_REPAIR_ATTEMPTS == 2
    assert C.REPAIR_DEPENDENCE_HIGH_AT == 3, "strictly more than half of four"
    assert not C.repair_dependence_is_high(0)
    assert not C.repair_dependence_is_high(2), "a 2-of-4 split is not 'most'"
    assert C.repair_dependence_is_high(3) and C.repair_dependence_is_high(4)


# --- The reused orchestration is still Tier B's ------------------------------------------------

def test_the_tier_b_runner_defaults_did_not_move():
    """D4-BR-C reuses `run_tier_b` instead of copying it, which is only honest if a call that passes
    none of the new arguments is still the Tier B run exactly as it was graded."""
    from app.dev.run_strategy_h_v2_d4_b import run_tier_b
    from app.backtest.strategy_h_v2.expectation.d4_1_contract import tier_b_checksum
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
        ANALYSES_ROOT, D3_LEG_ATTEMPTS_ROOT, D4_B_CONTRACT_VERSION, D4_B_ROOT,
        OBSERVED_PROJECTION_USD,
    )
    params = inspect.signature(run_tier_b).parameters
    assert params["sample"].default is TIER_B_SAMPLE
    assert params["hard_cap_usd"].default == TIER_B_HARD_CAP_USD == 45.60
    assert params["run_id_prefix"].default == "D4_B"
    assert params["tier"].default == "B"
    assert params["schema"].default == "H_V2_D4_B_RUN_MANIFEST_V1"
    assert params["contract_version"].default == D4_B_CONTRACT_VERSION
    assert params["checksum_fn"].default is tier_b_checksum
    assert params["observed_projection_usd"].default == OBSERVED_PROJECTION_USD
    assert params["d3_leg_root"].default == D3_LEG_ATTEMPTS_ROOT
    assert params["analyses_root"].default == ANALYSES_ROOT
    assert params["manifest_root"].default == D4_B_ROOT


def test_the_tier_b_audit_defaults_did_not_move():
    from app.dev.audit_strategy_h_v2_d4_b import audit_run
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
        D4_B_CONTRACT_VERSION, sample_integrity,
    )
    params = inspect.signature(audit_run).parameters
    assert params["sample"].default is TIER_B_SAMPLE
    assert params["hard_cap_usd"].default == TIER_B_HARD_CAP_USD
    assert params["schema"].default == "H_V2_D4_B_TIER_B_AUDIT_V1"
    assert params["contract_version"].default == D4_B_CONTRACT_VERSION
    assert params["sample_integrity_fn"].default is sample_integrity
    assert params["verdict_fn"].default is None, "no verdict layering unless one is asked for"


def test_the_confirmation_runner_calls_the_frozen_legs_and_the_frozen_model():
    """The legs are the point of the exercise: a confirmation that ran a different D3 or D4 would
    not be asking whether THIS pipeline converges."""
    from app.dev import run_strategy_h_v2_d4_br_c as runner
    from app.dev.run_strategy_h_v2_d4_2 import analyze_one_v2, build_bundle_for
    from app.dev.run_strategy_h_v2_d3_3 import research_one_v3
    from app.dev import run_strategy_h_v2_d4_b as tier_b

    assert runner.MODEL == "claude-opus-5-5"
    assert tier_b.run_tier_b.__module__ == "app.dev.run_strategy_h_v2_d4_b"
    source = inspect.getsource(tier_b.run_tier_b)
    assert "research_one_v3" in source and "analyze_one_v2" in source
    assert research_one_v3 and analyze_one_v2 and build_bundle_for


def test_the_confirmation_refuses_to_run_against_a_moved_sample(monkeypatch):
    import app.dev.run_strategy_h_v2_d4_br_c as runner
    monkeypatch.setattr(runner, "sample_integrity",
                        lambda: {"checksum_matches": False, "sample": []})
    try:
        runner.run_confirmation()
    except RuntimeError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("a moved sample must stop the run before it spends")


def test_the_confirmation_audit_adjudicates_with_the_frozen_gate_functions():
    import app.dev.audit_strategy_h_v2_d4_br_c as audit
    source = inspect.getsource(audit)
    assert "evaluate_d4_1_gates" not in source and "sf1_result" not in source, (
        "the gates are reached through the Tier B audit, never re-implemented here"
    )
    assert "audit_tier_b_run" in source


# --- The result, recorded: run D4_BR_C-20261001T005758Z ----------------------------------------
#
# Written after the run and before any repair of what it found, for the reason Tier B's result tests
# were: a graded run's numbers are what the run reported, and a test is the only thing that notices
# when a later step quietly reinterprets them.

RUN_ID = "D4_BR_C-20261001T005758Z"

CONFIRMATION_RESULT = {
    "d3_final_valid": 4,
    "d4_initial_valid": 1,
    "d4_final_valid": 4,
    "repair_candidates": 3,
    "repair_rounds": 4,
    "full_repair_budget_candidates": 1,
    "attempted_unsupported_consensus": 0,
    "attempted_quantified_consensus": 0,
    "final_unsupported_consensus": 0,
    "abstentions": ("FG", "VRRM"),
    "e7_decision_leaks": 1,
    "sf1_evaluated": 18,
    "sf1_failed": 0,
    "c1": "NOT_EVALUATED",
    "c4": "PASS",
    "code_owned_numeric_defects": 0,
    "compound_coverage_gap_findings": 0,
    "verdict": "FAIL",
    "d5": "NOT READY",
    "live_cost_usd": 11.2124832,
}
"""Asserted, not recomputed. §13 of the brief forbade a code change inside the run, so these are the
numbers the run produced under the contract as it stood."""


def test_the_confirmation_verdict_is_fail_on_e7_and_not_on_convergence():
    """The distinction the write-up turns on: E1 passed 4/4 and a different core gate failed."""
    statuses = _statuses(E7=GateStatus.FAIL.value)
    assert C.d4_br_c_verdict(statuses) is D4BVerdict.FAIL
    assert CONFIRMATION_RESULT["d4_final_valid"] == 4
    assert e1_confirmation_passes(CONFIRMATION_RESULT["d4_final_valid"], 4)
    assert CONFIRMATION_RESULT["e7_decision_leaks"] > 0
    assert d5_authorization(C.d4_br_c_verdict(statuses)) == "NOT READY"
    assert CONFIRMATION_RESULT["verdict"] == "FAIL" and CONFIRMATION_RESULT["d5"] == "NOT READY"


def test_e7_is_a_core_gate_so_the_failure_cannot_be_reported_as_a_limitation():
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import D4_B_CORE_GATES
    assert "E7" in D4_B_CORE_GATES
    for dependence in (False, True):
        assert C.d4_br_c_verdict(_statuses(E7=GateStatus.FAIL.value),
                                 repair_dependence_high=dependence) is D4BVerdict.FAIL


def test_the_e7_detector_matches_sell_inside_a_hyphenated_price_action_word():
    """The mechanism of the failure, as a property of the detector rather than of the output: the
    terms are compiled as whole words and a hyphen is a word boundary, so a compound noun that
    recommends nothing matches the recommendation vocabulary. No data needed to show it."""
    from app.dev.audit_strategy_h_v2_d4_1 import DECISION_VOCABULARY

    sentence = ("The flat pre-event return suggests the guidance cut was not anticipated in the "
                "price beforehand: there was neither a run-up nor a sell-off before the event.")
    matched = [p.pattern for p in DECISION_VOCABULARY if p.search(sentence)]
    assert matched == [r"\bsell\b"], matched
    assert any(p.search("sell-off") for p in DECISION_VOCABULARY)
    assert any(p.search("a selloff") for p in DECISION_VOCABULARY) is False, (
        "unhyphenated 'selloff' does NOT match, which is what makes this a word-boundary artifact "
        "rather than a vocabulary decision"
    )


def test_the_decision_vocabulary_has_no_context_window():
    """Stated as a fact about the detector, because it is the thing the finding is about. `fair
    value` and `approve/reject` are resolved through a surrounding window in `validation_v2`; the
    audit's decision vocabulary and the validator's unambiguous list are not."""
    import inspect

    from app.backtest.strategy_h_v2.research import validation_v2
    from app.dev import audit_strategy_h_v2_d4_1 as audit

    vocab_src = inspect.getsource(audit).split("DECISION_VOCABULARY")[1][:400]
    assert "window" not in vocab_src.lower()
    assert "_FV_SAFE_FRAME" in inspect.getsource(validation_v2), (
        "the context-window machinery exists for 'fair value', which is why its absence on the "
        "unambiguous list is a gap rather than an oversight everywhere"
    )
    assert any(t.strip("r'\"").startswith(r"\bprice target") or "price target" in t
               for t in [p for p in validation_v2._UNAMBIGUOUS_TERMS])


def test_the_validator_rejects_a_disclaimer_that_denies_producing_a_price_target():
    """COLL's initial failure, reproduced on the sentence itself. The model's natural way to comply
    with the prohibition trips the unconditional match on the prohibited phrase."""
    from app.backtest.strategy_h_v2.research.validation_v2 import (
        LanguageVerdict,
        classify_investment_language,
    )
    disclaimer = "Nothing here is a valuation, a price target or a decision."
    verdicts = [m.verdict for m in classify_investment_language(disclaimer)]
    assert LanguageVerdict.VIOLATION in verdicts, (
        "recorded as the behaviour observed on this run, not endorsed as correct"
    )


def test_the_recorded_result_matches_the_stored_gate_audit():
    """Data-backed, so it skips on a clean checkout - `data/runtime` is gitignored. Every claim
    above is also asserted without it."""
    import json
    import pytest

    path = C.D4_BR_C_ROOT / f"{RUN_ID}.gateaudit.json"
    if not path.exists():
        pytest.skip(f"{path} is not in this checkout")
    report = json.loads(path.read_text())
    conv = report["convergence"]
    gates = {g["gate"]: g for g in report["gates"]}

    assert report["verdict"] == CONFIRMATION_RESULT["verdict"]
    assert report["d5_authorization"] == CONFIRMATION_RESULT["d5"]
    assert conv["d3_final_valid"] == CONFIRMATION_RESULT["d3_final_valid"]
    assert conv["d4_initial_valid"] == CONFIRMATION_RESULT["d4_initial_valid"]
    assert conv["d4_final_valid"] == CONFIRMATION_RESULT["d4_final_valid"]
    assert conv["repair_candidates"] == CONFIRMATION_RESULT["repair_candidates"]
    assert conv["repair_rounds"] == CONFIRMATION_RESULT["repair_rounds"]
    assert tuple(conv["abstaining_candidates"]) == CONFIRMATION_RESULT["abstentions"]
    assert conv["attempted_consensus_attributions"] == CONFIRMATION_RESULT[
        "attempted_unsupported_consensus"]
    assert conv["attempted_quantified_consensus"] == CONFIRMATION_RESULT[
        "attempted_quantified_consensus"]
    assert conv["final_unsupported_consensus"] == CONFIRMATION_RESULT[
        "final_unsupported_consensus"]
    assert conv["repair_dependence_high"] is False
    assert report["repair_dependence"]["full_repair_budget_candidates"] == CONFIRMATION_RESULT[
        "full_repair_budget_candidates"]

    assert gates["E1"]["status"] == GateStatus.PASS.value and "4/4" in gates["E1"]["observed"]
    assert gates["E7"]["status"] == GateStatus.FAIL.value
    assert gates["E7"]["observed"] == str(CONFIRMATION_RESULT["e7_decision_leaks"])
    assert gates["SF1"]["status"] == GateStatus.PASS.value
    assert gates["SF1"]["eligible_assertions"] == CONFIRMATION_RESULT["sf1_evaluated"]
    assert gates["SF1"]["violations"] == CONFIRMATION_RESULT["sf1_failed"]
    for gate in ("E2", "E3", "E4", "E5", "E6", "E8"):
        assert gates[gate]["status"] == GateStatus.PASS.value, gate

    assert report["rules"]["C1"]["status"] == CONFIRMATION_RESULT["c1"]
    assert report["rules"]["C1"]["eligible"] == 0, "no POSITIVE-family output to evaluate"
    assert report["rules"]["C4"]["status"] == CONFIRMATION_RESULT["c4"]
    assert report["m8_scope"]["atomic_defects"] == CONFIRMATION_RESULT[
        "code_owned_numeric_defects"]
    assert report["m8_scope"]["compound_coverage_gap_findings"] == CONFIRMATION_RESULT[
        "compound_coverage_gap_findings"]
    assert report["m8_scope"]["r3_compound_set_valued"] == "DEFERRED", "R3 untouched by this step"
    assert report["models"]["any_mismatch"] is False
    assert report["models"]["d4_canonical"] == ["claude-opus-5-5"]
    assert report["telemetry_complete"] is True

    assert report["budget"]["within_hard_cap"] is True
    assert report["budget"]["every_candidate_within_its_worst_case"] is True
    assert report["budget"]["hard_cap_usd"] == C.D4_BR_C_HARD_CAP_USD == 30.40
    assert abs(report["budget"]["run_total_cost_usd"]
               - CONFIRMATION_RESULT["live_cost_usd"]) < 1e-6
    assert report["budget"]["stopped_early"] is None
    assert report["budget"]["budget_exhausted"] is False
    assert report["sample_integrity"]["checksum_matches"] is True


def test_the_sell_token_had_never_appeared_in_any_prior_h_v2_output():
    """Why the E7 failure is new exposure rather than a regression, checked over the stored corpus
    rather than argued. Skips on a clean checkout."""
    import glob
    import json
    import re

    import pytest

    paths = [p for p in glob.glob("data/runtime/strategy_h_v2/**/analyses/**/*.json",
                                  recursive=True)
             if not p.endswith("expectation_evidence.json")]
    if not paths:
        pytest.skip("no stored analyses in this checkout")
    pattern = re.compile(r"\bsell\b", re.I)
    hits = []
    for path in paths:
        try:
            record = json.loads(open(path).read())
        except (ValueError, OSError):
            continue
        output = record.get("final_output")
        if output and pattern.search(json.dumps(output)):
            hits.append((record.get("analysis_run_id"), record.get("ticker")))
    assert hits == [(RUN_ID, "COLL")], hits
