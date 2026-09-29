"""D4.2 Defect 3: a candidate's reserve covers the calls a candidate can actually make.

D4.1 booked $2.00 per candidate and handed the CLI `--max-budget-usd 2.00`. Those are not the same
number: the flag caps ONE call, and a candidate is one initial call plus up to two repairs. Its two
attempted candidates cost $2.43 and $2.11, so the preflight could not have stopped an overrun - it
was reserving a third of what a candidate was permitted to spend.

Every test here drives the runner with a fake `call_fn`, so the whole file is zero live calls and
zero dollars.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    MAX_CALLS_PER_CANDIDATE,
    MAX_REPAIR_ATTEMPTS,
    CandidateBudgetContract,
    CandidateSpend,
    run_total_cost_usd,
)
from app.backtest.strategy_h_v2.expectation.d4_1_contract import D4_WORST_CASE_CANDIDATE_USD
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    D4_2_REMAINING_CAP_USD,
    OVERALL_HARD_CAP_USD,
    PER_CALL_MAX_BUDGET_USD,
    TIER_A_BUDGET,
    TIER_A_HARD_BUDGET_USD,
    TIER_B_BUDGET,
    TIER_B_FITS_REMAINING_CAP,
    MechanicalGateStatus,
    MechanicalVerdict,
    build_gates,
    tier_a_verdict,
    tier_b_authorized,
)

D4_1_MEASURED_CANDIDATE_COSTS = (2.4343832, 2.1070108)
"""SCCO and GOOG, from `D4_1_A-20260929T045619Z.manifest.json`. Not estimates."""


# --- the arithmetic ---------------------------------------------------------------------------

def test_the_worst_case_is_the_per_call_cap_times_the_call_count():
    assert MAX_CALLS_PER_CANDIDATE == 1 + MAX_REPAIR_ATTEMPTS == 3
    assert TIER_A_BUDGET.candidate_worst_case_budget_usd == PER_CALL_MAX_BUDGET_USD * 3


@pytest.mark.parametrize("repairs,expected_calls", [(0, 1), (1, 2), (2, 3)])
def test_a_candidate_spend_is_the_initial_call_plus_every_repair(repairs, expected_calls):
    spend = CandidateSpend(ticker="ACME", initial_cost_usd=0.90,
                           repair_costs_usd=tuple([0.50] * repairs))
    assert len(spend.repair_costs_usd) + 1 == expected_calls
    assert spend.repair_cost_usd == pytest.approx(0.50 * repairs)
    assert spend.candidate_total_cost_usd == pytest.approx(0.90 + 0.50 * repairs)


def test_repair_cost_is_inside_the_candidate_total_not_beside_it():
    """Brief §11: no hidden second budget. A repair is charged where every other call is."""
    spend = CandidateSpend(ticker="ACME", initial_cost_usd=1.00, repair_costs_usd=(0.60, 0.70))
    assert spend.candidate_total_cost_usd == pytest.approx(2.30)
    assert run_total_cost_usd([spend, spend]) == pytest.approx(4.60)


def test_the_v1_reserve_could_not_have_bounded_d4_1s_measured_candidates():
    """The defect, stated as a measurement rather than as a claim: both real candidates exceeded
    the old reserve, and both sit comfortably inside the new one."""
    for cost in D4_1_MEASURED_CANDIDATE_COSTS:
        assert cost > D4_WORST_CASE_CANDIDATE_USD
        assert cost <= TIER_A_BUDGET.candidate_worst_case_budget_usd


# --- the preflight ------------------------------------------------------------------------------

def test_preflight_admits_a_candidate_that_can_finish_inside_the_cap():
    assert TIER_A_BUDGET.admits_next_candidate(spent_so_far_usd=0.0, overall_hard_cap_usd=18.0)
    assert TIER_A_BUDGET.admits_next_candidate(spent_so_far_usd=12.0, overall_hard_cap_usd=18.0)


def test_preflight_refuses_a_candidate_that_could_not():
    assert not TIER_A_BUDGET.admits_next_candidate(
        spent_so_far_usd=12.01, overall_hard_cap_usd=18.0)


def test_preflight_reserves_the_preceding_stage_for_a_chained_tier():
    """Tier B pays for a D3 run before the D4 call topology, and that dollar is reserved too."""
    assert TIER_B_BUDGET.preceding_stage_worst_case_usd > 0
    assert (TIER_B_BUDGET.candidate_worst_case_budget_usd
            == TIER_A_BUDGET.candidate_worst_case_budget_usd
            + TIER_B_BUDGET.preceding_stage_worst_case_usd)


def test_a_degenerate_budget_is_rejected_at_construction():
    with pytest.raises(ValueError):
        CandidateBudgetContract(per_call_max_budget_usd=0.0)
    with pytest.raises(ValueError):
        CandidateBudgetContract(per_call_max_budget_usd=2.0, max_calls_per_candidate=0)


# --- the run --------------------------------------------------------------------------------------

def _fake_call(cost: float):
    def call_fn(_system: str, _user: str) -> dict:
        return {"result": "not json", "total_cost_usd": cost, "is_error": False,
                "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}}}
    return call_fn


def _fake_d3_root(tmp_path: Path, tickers) -> Path:
    root = tmp_path / "d3"
    root.mkdir()
    for ticker in tickers:
        (root / f"{ticker}.json").write_text(json.dumps({
            "ticker": ticker, "cik": "1", "attempt_id": f"{ticker}-1", "final_status": "OK",
            "completed_at": "2026-09-29T00:00:00+00:00", "cost_usd": 1.0,
            "final_output": {"research_id": "R-1", "growth_durability": {"state": "UNKNOWN"},
                             "future_business": []},
            "final_output_checksum": "abc",
        }))
    return root


def test_the_run_stops_before_a_candidate_it_could_not_afford(tmp_path, monkeypatch):
    """Three candidates, a tier budget that fits two worst cases. The third must never start."""
    from app.dev import run_strategy_h_v2_d4_2 as runner

    tickers = ("SCCO", "GOOG", "BSY")
    calls = {"n": 0}

    def counting_call(system, user):
        calls["n"] += 1
        return _fake_call(2.0)(system, user)

    monkeypatch.setattr(runner, "PACKAGES_DIR", tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    from d4_helpers import package as build_package

    for ticker in tickers:
        (tmp_path / "packages" / f"{ticker}.json").write_text(build_package().model_dump_json())

    manifest = runner.run_tier_a_v2(
        tickers=tickers, call_fn=counting_call, hard_budget_usd=12.0,
        d3_attempts_root=_fake_d3_root(tmp_path, tickers),
        analyses_root=tmp_path / "analyses", manifest_root=tmp_path / "manifests",
        panel={},
    )
    assert manifest["attempted"] == 2
    assert manifest["stopped_early"] == "BSY"
    assert manifest["budget_exhausted"] is True
    assert calls["n"] == 2 * MAX_CALLS_PER_CANDIDATE


def test_the_manifest_records_the_split_every_candidate_spent(tmp_path, monkeypatch):
    from app.dev import run_strategy_h_v2_d4_2 as runner
    from d4_helpers import package as build_package

    monkeypatch.setattr(runner, "PACKAGES_DIR", tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "SCCO.json").write_text(build_package().model_dump_json())

    manifest = runner.run_tier_a_v2(
        tickers=("SCCO",), call_fn=_fake_call(0.5),
        d3_attempts_root=_fake_d3_root(tmp_path, ("SCCO",)),
        analyses_root=tmp_path / "analyses", manifest_root=tmp_path / "manifests", panel={},
    )
    spend = manifest["candidate_spends"][0]
    assert spend["initial_cost_usd"] == pytest.approx(0.5)
    assert len(spend["repair_costs_usd"]) == MAX_REPAIR_ATTEMPTS
    assert spend["candidate_total_cost_usd"] == pytest.approx(1.5)
    assert manifest["run_total_cost_usd"] == pytest.approx(1.5)
    assert manifest["results"][0]["within_candidate_worst_case"] is True
    assert manifest["budget_contract"]["max_calls_per_candidate"] == MAX_CALLS_PER_CANDIDATE


# --- the Tier B stop rule -------------------------------------------------------------------------

def _gates(status: MechanicalGateStatus):
    from app.backtest.strategy_h_v2.expectation.d4_2_contract import M_GATE_IDS
    return build_gates({g: (status == MechanicalGateStatus.PASS, "fixture") for g in M_GATE_IDS})


def test_tier_b_is_not_authorized_when_tier_a_is_not_ready():
    verdict = tier_a_verdict(_gates(MechanicalGateStatus.FAIL))
    assert verdict == MechanicalVerdict.MECHANICAL_NOT_READY
    assert tier_b_authorized(verdict) is False


def test_a_not_evaluated_gate_is_never_a_silent_pass():
    gates = build_gates({"M1": (True, "ok")})
    assert gates[0].status == MechanicalGateStatus.PASS
    assert all(g.status == MechanicalGateStatus.NOT_EVALUATED for g in gates[1:])
    assert tier_a_verdict(gates) == MechanicalVerdict.MECHANICAL_NOT_READY


def test_tier_b_does_not_fit_the_remaining_cap_under_the_repaired_accounting():
    """Not a failure of D4.2 - a consequence of counting correctly. The frozen six-issuer Tier B
    needs $45.60 of worst case against $25.46 left on the $30.00 authorization, so Tier B cannot be
    authorized on this cap however Tier A turns out. That is a user decision, not a code change."""
    assert TIER_B_FITS_REMAINING_CAP is False
    assert tier_b_authorized(MechanicalVerdict.MECHANICAL_READY) is False
    assert D4_2_REMAINING_CAP_USD < OVERALL_HARD_CAP_USD
    assert TIER_A_HARD_BUDGET_USD <= D4_2_REMAINING_CAP_USD
