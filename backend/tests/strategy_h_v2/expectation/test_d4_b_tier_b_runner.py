"""H-V2-D4-B runner and preregistration tests. Every model call is a stub: 0 live Opus calls, $0.

Exercised against IDCC's real frozen D2.1 package so citations resolve against real evidence rather
than a synthetic one, the same discipline `test_run_strategy_h_v2_d3_3.py` uses for LUV.

The point of this file is that the Tier B run must not be the first time the chaining, the split
accounting or the budget preflight is executed. A bug found after spending $20 of live budget is a bug
found too late.
"""

from __future__ import annotations

import json

import pytest

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    EXCLUDED_CIKS,
    TIER_B_CHECKSUM,
    TIER_B_SAMPLE,
    SampleEntry,
    regenerate_tier_b_from_universe,
    tier_b_checksum,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    MechanicalGateStatus,
    TIER_B_BUDGET,
)
from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
    D4_B_CORE_GATES,
    D4_B_GATES,
    D4BVerdict,
    M8_R3_COMPOUND_SET_VALUED,
    OBSERVED_PROJECTION_USD,
    SF1_AGGREGATION,
    SF1_GATE_ID,
    SF1_MAX_VIOLATIONS,
    SF1_NOT_A_VIOLATION,
    MUST_ALWAYS_EVALUATE,
    SF1_ZERO_ELIGIBLE_IS,
    TIER_B_HARD_CAP_USD,
    d4_b_verdict,
    d5_authorization,
    sf1_result,
)
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.dev.run_strategy_h_v2_d4_b import MODEL, _d3_spend, run_tier_b
from tests.strategy_h_v2.expectation.d4_helpers import d4_content

IDCC_SOURCE = "SEC:0001405495:0001405495-26-000018"


# --- the frozen preregistration -------------------------------------------------------------------

def test_the_sample_is_the_frozen_six_and_its_checksum_still_matches():
    assert len(TIER_B_SAMPLE) == 6
    assert tier_b_checksum() == TIER_B_CHECKSUM
    assert [e.ticker for e in TIER_B_SAMPLE] == ["IDCC", "DORM", "FRPT", "TG", "CRK", "SPSC"]


def test_the_sample_is_disjoint_from_every_previously_touched_cik():
    """§4. CIK-based, so one issuer under two symbols cannot slip through."""
    assert len(EXCLUDED_CIKS) == 48
    assert not {e.cik for e in TIER_B_SAMPLE} & EXCLUDED_CIKS
    assert len({e.cik for e in TIER_B_SAMPLE}) == 6


def test_the_sample_is_reproducible_from_the_universe_not_just_from_its_own_hash():
    """A hardcoded list checksummed by hashing itself proves nothing. This re-derives it offline."""
    assert regenerate_tier_b_from_universe() == TIER_B_SAMPLE


def test_the_hard_cap_is_the_repaired_accounting_not_a_retyped_literal():
    """§8. $45.60 is the per-call cap times the call topology plus D3's worst case, times six - and
    it is bound to `d4_2_contract` rather than typed here, so the two cannot drift. D4.1's $3.60
    per-candidate reserve is the defect this replaced."""
    assert TIER_B_HARD_CAP_USD == pytest.approx(45.60)
    assert TIER_B_BUDGET.candidate_worst_case_budget_usd == pytest.approx(7.60)
    assert TIER_B_BUDGET.candidate_worst_case_budget_usd * 6 == pytest.approx(TIER_B_HARD_CAP_USD)
    assert OBSERVED_PROJECTION_USD < TIER_B_HARD_CAP_USD, "the projection gates nothing"


def test_sf1_is_frozen_before_any_result():
    """§21: the gate id and the aggregation rule are frozen in the preregistration, not chosen after
    the numbers are in."""
    assert SF1_GATE_ID == "SF1"
    assert SF1_AGGREGATION == "ASSERTION"
    assert SF1_MAX_VIOLATIONS == 0
    assert SF1_ZERO_ELIGIBLE_IS is MechanicalGateStatus.NOT_EVALUATED
    assert SF1_NOT_A_VIOLATION == ("NEGATED", "REJECTED_OR_CONTRASTED")
    assert D4_B_GATES == ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "SF1")
    assert SF1_GATE_ID in D4_B_CORE_GATES


def test_sf1_zero_eligible_is_not_a_pass():
    """§26. A gate that evaluated nothing reports nothing."""
    assert sf1_result(evaluated_assertions=0, failed_assertions=0).status is \
        MechanicalGateStatus.NOT_EVALUATED
    assert sf1_result(evaluated_assertions=9, failed_assertions=0).status is \
        MechanicalGateStatus.PASS
    assert sf1_result(evaluated_assertions=9, failed_assertions=1).status is \
        MechanicalGateStatus.FAIL


def test_the_verdict_contract():
    allpass = {g: "PASS" for g in D4_B_GATES}
    assert d4_b_verdict(allpass) is D4BVerdict.PASS
    assert d5_authorization(D4BVerdict.PASS) == "READY FOR CONTRACT DESIGN"

    limited = {**allpass, "E6": "NOT_EVALUATED"}
    assert d4_b_verdict(limited) is D4BVerdict.PASS_WITH_LIMITATIONS
    assert d5_authorization(D4BVerdict.PASS_WITH_LIMITATIONS) == "READY FOR CONTRACT DESIGN"

    for core in D4_B_CORE_GATES:
        assert d4_b_verdict({**allpass, core: "FAIL"}) is D4BVerdict.FAIL, (
            f"{core} is a core gate; a failure there is never a limitation")
    for gate in MUST_ALWAYS_EVALUATE:
        assert d4_b_verdict({**allpass, gate: "NOT_EVALUATED"}) is D4BVerdict.FAIL, (
            f"{gate} counts over every graded output, so an unscored {gate} means the audit "
            "did not run")
    assert d5_authorization(D4BVerdict.FAIL) == "NOT READY"


def test_sf1_with_nothing_to_compare_caps_the_verdict_without_manufacturing_a_failure():
    """The second defect this file's dry run found before any money was spent. SF1 is a core gate, so
    inheriting `d4_1_verdict`'s "a core gate that is not PASS is a FAIL" would have graded a run FAIL
    for the ABSENCE of a defect: a run in which the model never restated a code-owned categorical
    state gives SF1 nothing to compare, and that is a zero denominator, not a fabrication.

    E3/E4/E5/E7 keep the strict reading, because each counts over every graded output and so always
    has a denominator - an unscored one there means the audit did not run. Both halves are asserted
    together so neither can be relaxed alone."""
    allpass = {g: "PASS" for g in D4_B_GATES}
    assert SF1_GATE_ID in D4_B_CORE_GATES
    assert SF1_GATE_ID not in MUST_ALWAYS_EVALUATE
    assert d4_b_verdict({**allpass, SF1_GATE_ID: "NOT_EVALUATED"}) is \
        D4BVerdict.PASS_WITH_LIMITATIONS
    assert d4_b_verdict({**allpass, SF1_GATE_ID: "FAIL"}) is D4BVerdict.FAIL
    assert d4_b_verdict({**allpass, "E5": "NOT_EVALUATED"}) is D4BVerdict.FAIL


def test_a_missing_gate_status_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match="no status recorded"):
        d4_b_verdict({g: "PASS" for g in D4_B_GATES if g != "SF1"})


def test_r3_is_still_deferred_in_this_run():
    assert M8_R3_COMPOUND_SET_VALUED == "DEFERRED"


# --- the chained run, with both legs stubbed ------------------------------------------------------

def _package(ticker: str) -> AIResearchInputV1:
    return AIResearchInputV1.model_validate_json((PACKAGES_DIR / f"{ticker}.json").read_text())


def _d3_content(source_id: str) -> dict:
    return {
        "business_model": {
            "revenue_drivers": [{
                "text": "The company licenses its patent portfolio to device makers.",
                "claim_type": "FACT", "source_id": source_id,
                "evidence_id": f"{source_id}:CHUNK:0", "confidence": "HIGH",
            }],
            "segments": [], "customer_types": [], "geography": [], "cyclicality": None,
            "key_dependencies": [],
        },
        "fundamental_change": [],
        "growth_durability": {"state": "UNKNOWN", "rationale": [], "evidence_checklist": {}},
        "future_business": [], "competitive_position": [], "management_execution": [],
        "catalyst_candidates": [],
        "why_now_candidate": {"summary": "Worth continued research.", "reasons": []},
        "risks": [], "invalidation_candidates": [], "evidence_conflicts": [],
        "open_questions": [], "unknown_fields": [], "sources": [source_id],
        "research_completeness": "PARTIAL",
    }


def _response(body: dict, cost: float, canonical: str = MODEL) -> dict:
    return {"result": json.dumps(body), "total_cost_usd": cost, "is_error": False,
            "modelUsage": {MODEL: {"canonicalModel": canonical}},
            "usage": {"input_tokens": 100, "output_tokens": 50}}


def _first_source(ticker: str) -> str:
    return _package(ticker).source_manifest[0].source_id


@pytest.fixture
def roots(tmp_path):
    return {"d3_leg_root": tmp_path / "d3_leg", "analyses_root": tmp_path / "analyses",
            "manifest_root": tmp_path}


def _panel(tickers):
    from app.dev.run_strategy_h_v2_d4_2 import load_price_panel
    return load_price_panel(tickers)


def test_a_clean_candidate_runs_the_whole_chain_and_splits_its_spend(roots):
    """The happy path end to end: D3 live call, expectation evidence built, D4 live call, and the
    spend recorded as D3 (preceding stage) + D4 initial, per §9."""
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),)
    src = _first_source("IDCC")
    calls: list[str] = []

    def d3_stub(system, user):
        calls.append("d3")
        return _response(_d3_content(src), 1.40)

    def d4_stub(system, user):
        calls.append("d4")
        return _response(d4_content(sources=[src]), 1.10)

    manifest = run_tier_b(sample=sample, d3_call_fn=d3_stub, d4_call_fn=d4_stub,
                          panel=_panel(["IDCC"]), **roots)

    assert calls == ["d3", "d4"], "exactly one call per leg, D3 first"
    result = manifest["results"][0]
    assert result["final_status"] == "OK"
    assert result["d3_final_status"] == "OK"
    assert result["d3_initial_valid"] is True and result["d4_initial_valid"] is True
    assert result["d3_repair_rounds"] == 0 and result["d4_repair_rounds"] == 0
    assert result["model_mismatch"] is False
    assert result["d3_canonical_model"] == result["d4_canonical_model"] == MODEL

    spend = result["spend"]
    assert spend["preceding_stage_cost_usd"] == pytest.approx(1.40), "the D3 leg"
    assert spend["initial_cost_usd"] == pytest.approx(1.10), "the D4 initial call"
    assert spend["repair_cost_usd"] == 0.0
    assert spend["candidate_total_cost_usd"] == pytest.approx(2.50)
    assert manifest["run_total_cost_usd"] == pytest.approx(2.50)
    assert result["within_candidate_worst_case"] is True

    assert manifest["model_requested"] == MODEL == "claude-opus-5-5"
    assert manifest["tier_b_hard_cap_usd"] == pytest.approx(45.60)
    assert result["input_package_checksum"] == manifest["d3_leg"]["IDCC"][
        "input_package_checksum"]
    assert result["expectation_evidence_id"] == f"EB-{manifest['run_id']}-IDCC"


def test_the_stored_artifacts_are_all_written(roots):
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),)
    src = _first_source("IDCC")
    manifest = run_tier_b(
        sample=sample, d3_call_fn=lambda s, u: _response(_d3_content(src), 1.0),
        d4_call_fn=lambda s, u: _response(d4_content(sources=[src]), 1.0),
        panel=_panel(["IDCC"]), **roots)
    run_id = manifest["run_id"]
    assert list(roots["d3_leg_root"].rglob("*.json")), "the D3 attempt must be persisted"
    analyses = roots["analyses_root"] / run_id / "IDCC"
    assert (analyses / "expectation_evidence.json").exists()
    assert [p for p in analyses.glob("*.json") if p.name != "expectation_evidence.json"]
    assert (roots["manifest_root"] / f"{run_id}.manifest.json").exists()


def test_a_failed_d3_leg_gets_no_d4_call_and_no_substitute_input(roots):
    """§1 forbids sample substitution. A D3 leg that does not return OK ends that candidate; the D3
    cost is still charged, because a failed call was still paid for."""
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),)
    d4_called = []

    manifest = run_tier_b(
        sample=sample,
        d3_call_fn=lambda s, u: {"result": "", "total_cost_usd": 0.42, "is_error": True},
        d4_call_fn=lambda s, u: d4_called.append(1) or _response(d4_content(), 1.0),
        panel=_panel(["IDCC"]), **roots)

    assert d4_called == [], "no D4 call on a failed D3 leg"
    result = manifest["results"][0]
    assert result["final_status"] == "D3_LEG_FAILED"
    assert result["spend"]["preceding_stage_cost_usd"] == pytest.approx(0.42)
    assert manifest["run_total_cost_usd"] == pytest.approx(0.42)


def test_the_budget_preflight_stops_before_a_candidate_that_could_not_finish(roots):
    """§9: checked BEFORE the candidate starts, against the candidate's whole worst case. With a cap
    of $10.00 and a $7.60 reserve, the second candidate cannot be started once the first has spent
    anything at all."""
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),
              SampleEntry("DORM", "0000868780", "FULL", "E3_P1_HIGH"))
    src = _first_source("IDCC")
    manifest = run_tier_b(
        sample=sample, hard_cap_usd=10.00,
        d3_call_fn=lambda s, u: _response(_d3_content(src), 1.50),
        d4_call_fn=lambda s, u: _response(d4_content(sources=[src]), 1.50),
        panel=_panel(["IDCC", "DORM"]), **roots)

    assert manifest["attempted"] == 1
    assert manifest["stopped_early"] == "DORM"
    assert manifest["budget_exhausted"] is True


def test_the_preflight_admits_every_candidate_under_the_real_cap():
    """The converse, so the stop rule cannot be satisfied by simply never starting: at $45.60 the
    contract admits all six even if each spends its full worst case, and refuses a seventh."""
    spent = 0.0
    for i in range(6):
        assert TIER_B_BUDGET.admits_next_candidate(
            spent_so_far_usd=spent, overall_hard_cap_usd=TIER_B_HARD_CAP_USD), f"candidate {i + 1}"
        spent += TIER_B_BUDGET.candidate_worst_case_budget_usd
    assert spent == pytest.approx(TIER_B_HARD_CAP_USD)
    assert not TIER_B_BUDGET.admits_next_candidate(
        spent_so_far_usd=spent, overall_hard_cap_usd=TIER_B_HARD_CAP_USD)


def test_the_cap_is_not_seven_femtocents_below_its_own_authorization():
    """The defect this file's dry run found before any money was spent. `6 * 7.6` is
    45.599999999999994 in IEEE-754, and `admits_next_candidate` compares `38.0 + 7.6 <= cap`, so the
    SIXTH candidate was refused by a float artifact - the frozen six truncated to five with
    `budget_exhausted: True`, on exactly the run where costs had gone high enough to matter.

    The correction is a round to cents, and it is bounded in both directions so it can never become a
    way to widen a cap: it must land on the authorized $45.60 exactly, and it must move the product by
    less than one cent."""
    from app.backtest.strategy_h_v2.expectation.d4_2_contract import TIER_B_HARD_BUDGET_USD

    assert TIER_B_HARD_BUDGET_USD < 45.60, "the raw product is below the authorized amount"
    assert TIER_B_HARD_CAP_USD == 45.60
    assert abs(TIER_B_HARD_CAP_USD - TIER_B_HARD_BUDGET_USD) < 0.01
    assert not TIER_B_BUDGET.admits_next_candidate(
        spent_so_far_usd=38.0, overall_hard_cap_usd=TIER_B_HARD_BUDGET_USD), (
        "the uncorrected product refuses the sixth candidate - this is the defect, pinned")
    assert TIER_B_BUDGET.admits_next_candidate(
        spent_so_far_usd=38.0, overall_hard_cap_usd=TIER_B_HARD_CAP_USD)


def test_a_model_mismatch_is_recorded_and_never_silently_accepted(roots):
    """§7: `canonicalModel` is checked against what was requested, and a mismatch is flagged rather
    than trusted. No fallback."""
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),)
    src = _first_source("IDCC")
    manifest = run_tier_b(
        sample=sample,
        d3_call_fn=lambda s, u: _response(_d3_content(src), 1.0, canonical="claude-sonnet-5"),
        d4_call_fn=lambda s, u: _response(d4_content(sources=[src]), 1.0),
        panel=_panel(["IDCC"]), **roots)
    assert manifest["d3_leg"]["IDCC"]["model_mismatch"] is True
    assert manifest["results"][0]["model_mismatch"] is True


def test_a_d3_repair_round_is_charged_and_split(roots):
    """A repair is paid out of the same cap, and the split records how much of the leg was repair."""
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),)
    src = _first_source("IDCC")
    seen = {"n": 0}

    def d3_stub(system, user):
        seen["n"] += 1
        if seen["n"] == 1:
            bad = _d3_content(src)
            bad["business_model"]["revenue_drivers"][0]["source_id"] = None
            bad["business_model"]["revenue_drivers"][0]["evidence_id"] = None
            return _response(bad, 1.00)
        return _response(_d3_content(src), 0.60)

    manifest = run_tier_b(
        sample=sample, d3_call_fn=d3_stub,
        d4_call_fn=lambda s, u: _response(d4_content(sources=[src]), 0.90),
        panel=_panel(["IDCC"]), **roots)

    leg = manifest["d3_leg"]["IDCC"]
    assert leg["final_status"] == "OK"
    assert leg["initial_validation_status"] == "FAILED"
    assert leg["repair_attempts"] == 1
    assert leg["repair_causes"], "the repair cause is recorded, not just the count"
    assert leg["d3_initial_cost_usd"] == pytest.approx(1.00)
    assert leg["d3_repair_costs_usd"] == [pytest.approx(0.60)]
    assert manifest["results"][0]["d3_initial_valid"] is False
    assert manifest["run_total_cost_usd"] == pytest.approx(2.50)


def test_the_d3_spend_split_reconstructs_the_total():
    class _Repair:
        def __init__(self, cost):
            self.cost_usd = cost

    class _Record:
        cost_usd = 2.75
        repair_attempts = [_Repair(0.5), _Repair(0.25)]

    initial, repairs = _d3_spend(_Record())
    assert initial == pytest.approx(2.00)
    assert repairs == (0.5, 0.25)
    assert initial + sum(repairs) == pytest.approx(_Record.cost_usd)


def test_the_manifest_stamps_every_frozen_contract_version(roots):
    sample = (SampleEntry("IDCC", "0001405495", "FULL", "E3_P1_HIGH"),)
    src = _first_source("IDCC")
    manifest = run_tier_b(
        sample=sample, d3_call_fn=lambda s, u: _response(_d3_content(src), 1.0),
        d4_call_fn=lambda s, u: _response(d4_content(sources=[src]), 1.0),
        panel=_panel(["IDCC"]), **roots)
    for key in ("contract_version", "d4_contract_version", "d3_prompt_version",
                "d3_schema_version", "d4_prompt_version", "gap_contract_version",
                "validation_contract_version", "tier_b_checksum"):
        assert manifest[key], key


# --- the audit, over a stubbed run -----------------------------------------------------------------

def _run_and_audit(roots, tickers, d3_body_for, d4_body_for, cost=1.0):
    from app.dev.audit_strategy_h_v2_d4_b import audit_run

    sample = tuple(e for e in TIER_B_SAMPLE if e.ticker in tickers)
    state = {"ticker": None}

    def d3_stub(system, user):
        return _response(d3_body_for(state["ticker"]), cost)

    def d4_stub(system, user):
        return _response(d4_body_for(state["ticker"]), cost)

    # The runner iterates the sample in order, so the D3 call for a ticker always precedes its D4
    # call; tracking the current ticker from the prompt keeps the stub per-candidate without
    # reaching into the runner.
    def d3_tracking(system, user):
        for entry in sample:
            if entry.ticker in user[:4000] or entry.ticker in system[:4000]:
                state["ticker"] = entry.ticker
                break
        return d3_stub(system, user)

    manifest = run_tier_b(sample=sample, d3_call_fn=d3_tracking, d4_call_fn=d4_stub,
                          panel=_panel(list(tickers)), **roots)
    report = audit_run(manifest["run_id"], analyses_root=roots["analyses_root"],
                       d3_leg_root=roots["d3_leg_root"], manifest_root=roots["manifest_root"])
    return manifest, report


def test_the_audit_grades_a_clean_stubbed_run(roots):
    """The grader must work before the live run, not after. A clean UNKNOWN-everywhere output has no
    POSITIVE gap and asserts no state, so C1 and SF1 both land on NOT_EVALUATED - and the verdict is
    PASS_WITH_LIMITATIONS rather than PASS, which is the whole point of §26."""
    src = _first_source("IDCC")
    _, report = _run_and_audit(
        roots, ["IDCC"], lambda t: _d3_content(src), lambda t: d4_content(sources=[src]))

    statuses = {g["gate"]: g["status"] for g in report["gates"]}
    assert statuses["E1"] == "PASS"
    for gate in ("E2", "E3", "E4", "E5", "E7", "E8"):
        assert statuses[gate] == "PASS", (gate, statuses)
    assert statuses["SF1"] == "NOT_EVALUATED", "no state was asserted, so SF1 evaluated nothing"
    assert report["rules"]["C1"]["status"] == "NOT_EVALUATED"
    assert report["rules"]["C1"]["eligible"] == 0
    assert report["verdict"] == "PASS_WITH_LIMITATIONS"
    assert report["d5_authorization"] == "READY FOR CONTRACT DESIGN"
    assert report["models"]["requested"] == "claude-opus-5-5"
    assert report["models"]["any_mismatch"] is False
    assert report["budget"]["within_hard_cap"] is True
    assert report["sample_integrity"]["tier_b_checksum_recomputed"] == TIER_B_CHECKSUM
    assert report["m8_scope"]["r3_compound_set_valued"] == "DEFERRED"


def test_the_audit_reports_the_d3_leg_quality_for_every_candidate(roots):
    """§10. The D3 leg is graded by D3.3's own frozen audit, not by a second opinion invented here."""
    src = _first_source("IDCC")
    _, report = _run_and_audit(
        roots, ["IDCC"], lambda t: _d3_content(src), lambda t: d4_content(sources=[src]))
    candidate = report["candidates"][0]
    assert candidate["d3_final_status"] == "OK"
    assert candidate["d3_initial_valid"] is True
    d3 = candidate["d3_audit"]
    for key in ("material_claims", "structural_provenance_defects", "numeric_defect_claims",
                "future_business_stage_distribution", "investment_language_violations",
                "research_completeness"):
        assert key in d3, key
    assert d3["structural_provenance_defects"] == []
    assert d3["investment_language_violations"] == []


def test_a_state_mismatch_fails_sf1_and_the_whole_verdict(roots):
    """SF1 is a core gate: a fabricated categorical state is a FAIL, never a limitation. The claim
    below asserts a revenue state, which every one of the six candidates' own code facts owns, so the
    comparison is real rather than a fixture artifact."""
    src = _first_source("IDCC")
    package = _package("IDCC")
    authority = {m: b.get("state") for m, b in
                 (package.evidence_bundle.fundamental_changes or {}).items()
                 if isinstance(b, dict)}
    wrong = next(s for s in ("ACCELERATING", "DECELERATING", "STABLE", "IMPROVING")
                 if s != authority.get("revenue"))

    def d4_body(_t):
        return d4_content(
            sources=[src],
            gap_rationale=[{"text": f"Revenue is {wrong}.", "claim_type": "INTERPRETATION",
                            "source_id": src, "evidence_id": f"{src}:CHUNK:0",
                            "evidence_ids": [], "confidence": "MEDIUM"}])

    _, report = _run_and_audit(roots, ["IDCC"], lambda t: _d3_content(src), d4_body)
    statuses = {g["gate"]: g["status"] for g in report["gates"]}
    assert statuses["SF1"] == "FAIL"
    assert report["state_fidelity"]["assertions_failed"] == 1
    assert report["state_fidelity"]["violating_claims"][0]["ticker"] == "IDCC"
    assert report["verdict"] == "FAIL"
    assert report["d5_authorization"] == "NOT READY"


def test_a_correct_state_restatement_passes_sf1_on_a_real_denominator(roots):
    """The converse, so SF1's PASS cannot be reached by never asserting anything."""
    src = _first_source("IDCC")
    package = _package("IDCC")
    revenue_state = (package.evidence_bundle.fundamental_changes or {})["revenue"]["state"]

    def d4_body(_t):
        return d4_content(
            sources=[src],
            gap_rationale=[{"text": f"Revenue is {revenue_state}.", "claim_type": "INTERPRETATION",
                            "source_id": src, "evidence_id": f"{src}:CHUNK:0",
                            "evidence_ids": [], "confidence": "MEDIUM"}])

    _, report = _run_and_audit(roots, ["IDCC"], lambda t: _d3_content(src), d4_body)
    statuses = {g["gate"]: g["status"] for g in report["gates"]}
    assert report["state_fidelity"]["assertions_evaluated"] == 1
    assert report["state_fidelity"]["assertions_failed"] == 0
    assert statuses["SF1"] == "PASS"


def test_a_negated_state_is_not_an_sf1_violation(roots):
    """§16. Naming a state to deny it is not asserting it."""
    src = _first_source("IDCC")
    package = _package("IDCC")
    revenue_state = (package.evidence_bundle.fundamental_changes or {})["revenue"]["state"]
    wrong = "DECELERATING" if revenue_state != "DECELERATING" else "ACCELERATING"

    def d4_body(_t):
        return d4_content(
            sources=[src],
            gap_rationale=[{"text": f"Revenue is not {wrong}.", "claim_type": "INTERPRETATION",
                            "source_id": src, "evidence_id": f"{src}:CHUNK:0",
                            "evidence_ids": [], "confidence": "MEDIUM"}])

    _, report = _run_and_audit(roots, ["IDCC"], lambda t: _d3_content(src), d4_body)
    sf = report["state_fidelity"]
    assert sf["assertions_failed"] == 0
    assert sf["polarity_counts"].get("NEGATED") == 1
    assert sf["not_evaluated_by_reason"].get("POLARITY_NOT_ASSERTED") == 1


def test_a_gate_is_never_missing_from_the_report(roots):
    src = _first_source("IDCC")
    _, report = _run_and_audit(
        roots, ["IDCC"], lambda t: _d3_content(src), lambda t: d4_content(sources=[src]))
    assert [g["gate"] for g in report["gates"]] == list(D4_B_GATES)
