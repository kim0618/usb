"""D3.2F frozen D3.3 contract tests (brief §17). 0 model calls."""

from __future__ import annotations

from app.backtest.strategy_h_v2.research.d3_3_contract import (
    D3_1_BATCH2_CIKS,
    D3_3_HARD_BUDGET_USD,
    D3_3_MANUAL_AUDIT_CHECKSUM,
    D3_3_MANUAL_AUDIT_TICKERS,
    D3_3_SAMPLE,
    D3_3_SAMPLE_CHECKSUM,
    D3_3_WORST_CASE_CANDIDATE_USD,
    D3_PILOT_CIKS,
    EXCLUDED_CIKS,
    D33Verdict,
    GateStatus,
    d3_3_verdict,
    evaluate_d3_3_gates,
    manual_audit_checksum,
    regenerate_sample_from_universe,
    sample_checksum,
)


# --- sample disjointness (brief §8) -------------------------------------------------------------

def test_pilot_and_batch2_cik_sets_disjoint():
    assert D3_PILOT_CIKS.isdisjoint(D3_1_BATCH2_CIKS)
    assert len(EXCLUDED_CIKS) == 36


def test_sample_disjoint_from_every_prior_live_cik():
    sample_ciks = {e.cik for e in D3_3_SAMPLE}
    assert sample_ciks.isdisjoint(EXCLUDED_CIKS)


def test_sample_ciks_unique_no_same_company_two_tickers():
    ciks = [e.cik for e in D3_3_SAMPLE]
    assert len(ciks) == len(set(ciks))


def test_sample_depth_split_is_6_and_6():
    assert sum(1 for e in D3_3_SAMPLE if e.depth == "FULL") == 6
    assert sum(1 for e in D3_3_SAMPLE if e.depth == "CORE") == 6
    assert len(D3_3_SAMPLE) == 12


# --- deterministic manifest / checksum (brief §9) -----------------------------------------------

def test_frozen_sample_checksum_matches_its_own_recomputation():
    assert sample_checksum() == D3_3_SAMPLE_CHECKSUM


def test_frozen_sample_matches_independent_regeneration_from_universe():
    """The strongest check: not "does the list match its own hash" but "does an independent
    re-derivation from the D2.1 snapshot, run fresh right now, produce the exact same 12 - proving
    the frozen literal was not hand-edited after the fact."""
    assert regenerate_sample_from_universe() == D3_3_SAMPLE


def test_manual_audit_subsample_checksum_matches():
    assert manual_audit_checksum() == D3_3_MANUAL_AUDIT_CHECKSUM


def test_manual_audit_subsample_is_3_full_3_core_drawn_from_the_sample():
    sample_by_ticker = {e.ticker: e for e in D3_3_SAMPLE}
    picked = [sample_by_ticker[t] for t in D3_3_MANUAL_AUDIT_TICKERS]
    assert sum(1 for e in picked if e.depth == "FULL") == 3
    assert sum(1 for e in picked if e.depth == "CORE") == 3


# --- budget stop (brief §10) ---------------------------------------------------------------------

def test_hard_budget_and_worst_case_frozen_values():
    assert D3_3_HARD_BUDGET_USD == 30.00
    assert D3_3_WORST_CASE_CANDIDATE_USD == 6.00


def test_budget_stop_before_not_after():
    """The actual stop decision lives in `run_strategy_h_v2_d3_3.run_sample` (tested against a
    stub call_fn in test_run_strategy_h_v2_d3_3.py) - this test only locks the frozen numbers this
    stage must not silently change (brief §1: "자동 확대 금지")."""
    assert D3_3_HARD_BUDGET_USD / D3_3_WORST_CASE_CANDIDATE_USD >= 5


# --- L1-L7 gate aggregation (brief §11/§17) ------------------------------------------------------

def _perfect_inputs(**overrides) -> dict:
    kwargs = dict(
        attempted=12, final_valid=12, initial_valid=10, material_content_defects=0,
        unsupported_stage_escalations=0, material_claims=500, citation_defective_claims=20,
        fabricated_numeric_facts=0, decision_leaks=0,
    )
    kwargs.update(overrides)
    return kwargs


def test_all_gates_pass_gives_pass_verdict():
    gates = evaluate_d3_3_gates(**_perfect_inputs())
    assert all(g.status == GateStatus.PASS for g in gates)
    assert d3_3_verdict(gates) == D33Verdict.PASS


def test_l3_not_evaluated_when_manual_audit_not_performed():
    gates = evaluate_d3_3_gates(**_perfect_inputs(material_content_defects=None))
    l3 = next(g for g in gates if g.gate == "L3")
    assert l3.status == GateStatus.NOT_EVALUATED
    # An unperformed manual audit must not silently pass the overall verdict either.
    assert d3_3_verdict(gates) == D33Verdict.FAIL


def test_core_gate_failure_is_always_fail():
    for override in (
        {"material_content_defects": 1}, {"unsupported_stage_escalations": 1},
        {"fabricated_numeric_facts": 1}, {"decision_leaks": 1},
    ):
        gates = evaluate_d3_3_gates(**_perfect_inputs(**override))
        assert d3_3_verdict(gates) == D33Verdict.FAIL, override


def test_non_core_gate_failure_alone_is_limitations_not_fail():
    for override in (
        {"final_valid": 10},  # L1 below 95%
        {"initial_valid": 5},  # L2 below 80%
        {"citation_defective_claims": 60},  # L5 above 10%
    ):
        gates = evaluate_d3_3_gates(**_perfect_inputs(**override))
        assert d3_3_verdict(gates) == D33Verdict.PASS_WITH_LIMITATIONS, override


def test_l5_uses_material_claims_denominator_not_total_tokens():
    gates = evaluate_d3_3_gates(**_perfect_inputs(material_claims=200, citation_defective_claims=21))
    l5 = next(g for g in gates if g.gate == "L5")
    assert l5.status == GateStatus.FAIL  # 21/200 = 10.5% > 10%


def test_zero_material_claims_does_not_divide_by_zero():
    gates = evaluate_d3_3_gates(**_perfect_inputs(material_claims=0, citation_defective_claims=0))
    l5 = next(g for g in gates if g.gate == "L5")
    assert l5.status == GateStatus.PASS
