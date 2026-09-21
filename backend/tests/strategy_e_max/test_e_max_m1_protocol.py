"""E-MAX-M1 protocol tests (phase A). Synthetic orderings and frozen rules only; no return."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6 import replay as E6
from app.strategy_e import execution, exits, risk, signal
from app.strategy_e_max import m0, m1, ranking
from app.strategy_e_v1_1 import decision


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"


@pytest.fixture(scope="module")
def rules():
    return m1.load_rules()


def _f(rvol, r09, pos, gap=0.01):
    return {"premarket_rvol": rvol, "return_0900_0925": r09, "position_in_premarket_range": pos,
            "premarket_gap": gap}


FEATURES = {"AAA": _f(3.0, 0.001, 0.80), "BBB": _f(9.0, 0.002, 0.90), "CCC": _f(5.0, 0.009, 1.00),
            "DDD": _f(9.0, 0.004, 0.85), "EEE": _f(4.0, 0.009, 0.95)}


def test_m0_checksum_identity(rules) -> None:
    assert rules["upstream"]["m0_rules_canonical_sha256"] == m0.RULES_CANONICAL_SHA256
    m0.load_rules()


def test_base_h5_unchanged() -> None:
    assert decision.load_rules()["alpha"]["statement"] == signal._load_frozen_rules()["alpha"]["statement"]


def test_r0_is_canonical_symbol_order() -> None:
    assert ranking.order("R0", ["EEE", "AAA", "CCC"], FEATURES) == ("AAA", "CCC", "EEE")


def test_r1_orders_by_rvol_descending_with_symbol_tie() -> None:
    assert ranking.order("R1", list(FEATURES), FEATURES) == ("BBB", "DDD", "CCC", "EEE", "AAA")


def test_r2_orders_by_late_momentum_with_symbol_tie() -> None:
    assert ranking.order("R2", list(FEATURES), FEATURES) == ("CCC", "EEE", "DDD", "BBB", "AAA")


def test_r3_equal_rank_composite() -> None:
    ranks = ranking.descending_average_ranks({s: FEATURES[s]["premarket_rvol"] for s in FEATURES})
    assert ranks == {"BBB": 1.5, "DDD": 1.5, "CCC": 3.0, "EEE": 4.0, "AAA": 5.0}
    # rvol ranks + r0900 ranks (CCC,EEE 1.5; DDD 3; BBB 4; AAA 5) + position ranks
    # (CCC 1, EEE 2, BBB 3, DDD 4, AAA 5): CCC 5.5, EEE 7.5, BBB 8.5, DDD 8.5, AAA 15
    assert ranking.order("R3", list(FEATURES), FEATURES) == ("CCC", "EEE", "BBB", "DDD", "AAA")


def test_ties_fall_back_to_symbol_ascending() -> None:
    same = {s: _f(4.0, 0.003, 0.9) for s in ("ZZZ", "MMM", "AAA")}
    for rule in ranking.RULES:
        assert ranking.order(rule, list(same), same) == ("AAA", "MMM", "ZZZ")


def test_max_selected_is_three_without_replacement(rules) -> None:
    selected, rest = ranking.select("R1", list(FEATURES), FEATURES)
    assert selected == ("BBB", "DDD", "CCC") and rest == ("EEE", "AAA")
    assert rules["held_fixed"]["max_selected"] == 3 and rules["held_fixed"]["replacement"] == "NONE"


def test_entry_exit_cost_exposure_held_fixed(rules) -> None:
    held = rules["held_fixed"]
    assert held["primary_cost"] == E6.PRIMARY == "COST_10BP"
    assert tuple(held["costs"]) == E6.SCENARIOS
    assert held["exposure"] == 1.0
    assert "09:30" in held["entry"] and execution.ENTRY_BAR_START_ET == "09:30"
    assert "09:34" in held["exit"] and exits.EXIT_BAR_START_ET == "09:34"


def test_eligibility_gate_identity(rules) -> None:
    good = {"integrity": True, "coverage": 0.97, "mean_10bp": 1e-4, "pf_10bp": 1.1,
            "mdd_10bp": -0.35, "top1_share_10bp": 0.7338}
    assert m1.eligible(good, rules)["pass"]
    for key, value in (("coverage", 0.949), ("mean_10bp", 0.0), ("pf_10bp", 1.0),
                       ("mdd_10bp", -0.3501), ("top1_share_10bp", 0.7339), ("integrity", False)):
        assert not m1.eligible({**good, key: value}, rules)["pass"], key


def test_improvement_gate_identity(rules) -> None:
    assert m1.improved(0.2, 0.1, {"mean": 1e-5, "p_mean_le_zero": 0.10}, rules)["pass"]
    assert not m1.improved(0.2, 0.1, {"mean": 1e-5, "p_mean_le_zero": 0.1001}, rules)["pass"]
    assert not m1.improved(0.1, 0.1, {"mean": 1e-5, "p_mean_le_zero": 0.0}, rules)["pass"]
    assert not m1.improved(0.2, 0.1, {"mean": 0.0, "p_mean_le_zero": 0.0}, rules)["pass"]


def test_paired_bootstrap_is_deterministic() -> None:
    import numpy as np
    delta = np.random.default_rng(0).normal(0, 1e-3, 480)
    assert X.bootstrap_mean_ci(delta) == X.bootstrap_mean_ci(delta)
    assert X.bootstrap_mean_ci(delta)["seed"] == 20260921 and X.BOOTSTRAP_REPLICATES == 10_000


def test_winner_selection(rules) -> None:
    def c(i, cagr, mean=1e-4, ok=True):
        return {"id": i, "eligible_pass": ok, "improved_pass": ok, "cagr_10bp": cagr, "mean_10bp": mean}
    assert m1.winner([c("R1", 0.05), c("R2", 0.07), c("R3", 0.06)], rules)["label"] == "E-MAX-M1 PASS — R2 SELECTED"
    assert m1.winner([c("R1", 0.05), c("R2", 0.05, 2e-4)], rules)["winner"] == "R2"
    assert m1.winner([c("R1", 0.05), c("R2", 0.05)], rules)["winner"] == "R1"
    assert m1.winner([c("R1", 0.09, ok=False)], rules)["label"] == "E-MAX-M1 NO_USEFUL_ENHANCEMENT"
    assert rules["winner"]["no_percentage_point_tie_band"].startswith("M0 declares no CAGR tie band")


def test_no_r4_no_gap_no_weights(rules) -> None:
    assert [h["id"] for h in rules["variants"]["hypotheses"]] == ["R1", "R2", "R3"]
    with pytest.raises(ValueError):
        ranking.order("R4", list(FEATURES), FEATURES)
    assert "premarket_gap" not in ranking.COMPOSITE_FEATURES
    forbidden = " ".join(rules["variants"]["forbidden"])
    assert "gap-only ranking" in forbidden and "weight search" in forbidden


def test_protocol_loads_without_performance(monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise AssertionError("performance invoked in M1 phase A")
    for module, name in ((E6, "replay_session"), (X, "scenario_summary"), (X, "verdict"),
                         (risk, "build_sizing_records")):
        monkeypatch.setattr(module, name, refuse)
    m1.load_rules()


def test_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m1_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == m1.RULES_CANONICAL_SHA256
    body = (DOCS / "strategy_e_max_m1_rules_v1.json").read_bytes()
    assert recorded["file_sha256"] == hashlib.sha256(body).hexdigest()


# -- committed M1 result (phase B) ---------------------------------------------------------------

RESULT = DOCS / "strategy_e_max_m1_result_v1.json"


@pytest.fixture(scope="module")
def result():
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_result_checksum_and_repeat_identity(result) -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m1_result_v1.sha256").read_text("utf-8"))
    assert recorded["file_sha256"] == hashlib.sha256(RESULT.read_bytes()).hexdigest()
    check = recorded["repeat_check"]
    assert check["identical"] and check["artifacts_identical"]
    assert check["this_result_digest"] == recorded["file_sha256"] == check["first_result_digest"]


def test_result_ran_after_the_protocol_commit(result) -> None:
    assert result["identity"]["m1_rules"] == m1.RULES_CANONICAL_SHA256
    assert result["identity"]["m0_rules"] == m0.RULES_CANONICAL_SHA256
    assert result["prechecks"]["chain_ancestry"]["E-MAX-M1-PROTOCOL"]


def test_r0_reproduced_e_r3_and_invariants_held(result, rules) -> None:
    pre = result["prechecks"]
    assert pre["r0_reproduces_e_r3"] == {"trades_csv": rules["upstream"]["e_r3_trades_csv_sha256"],
                                         "daily_returns_csv": rules["upstream"]["e_r3_daily_returns_csv_sha256"]}
    assert pre["subset_equivalence"] and pre["invariants"]["pass"] and pre["in_process_deterministic"]
    assert pre["v1_reproduction"] == {"universe_rows": 70738, "h5_rows": 1729}
    assert pre["pit_poison"]["verdict"] == "PASS"


def test_universe_h5_and_capacity_identical_across_variants(result) -> None:
    funnels = [v["evaluation"]["funnel"] for v in result["variants"].values()]
    assert {f["eligible_universe_rows"]["count"] for f in funnels} == {71119}
    assert {f["H5_candidates"]["count"] for f in funnels} == {1738}
    assert {f["selected_candidates"]["count"] for f in funnels} == {501}
    assert set(result["variants"]) == {"R0", "R1", "R2", "R3"}


def test_paired_bootstrap_uses_the_frozen_configuration(result) -> None:
    for name, v in result["variants"].items():
        assert (v["paired_vs_r0"]["seed"], v["paired_vs_r0"]["replicates"]) == (20260921, 10_000)
    assert result["variants"]["R0"]["paired_vs_r0"]["mean"] == 0.0


def test_verdict_recomputes_from_the_stored_numbers(result, rules) -> None:
    candidates = []
    base_cum = result["variants"]["R0"]["evaluation"]["scenarios"]["COST_10BP"]["cumulative_return"]
    for name in ("R1", "R2", "R3"):
        v = result["variants"][name]
        ev = v["evaluation"]
        s = ev["scenarios"]["COST_10BP"]
        summary = {"integrity": result["integrity"], "coverage": ev["funnel"]["standard_pnl_coverage"],
                   "mean_10bp": s["all_session_mean"], "pf_10bp": s["profit_factor"],
                   "mdd_10bp": s["maximum_drawdown"],
                   "top1_share_10bp": ev["concentration"]["cost_10bp"]["top1"]["share_of_total"]}
        e, i = m1.eligible(summary, rules), m1.improved(s["cumulative_return"], base_cum, v["paired_vs_r0"], rules)
        assert e == v["eligibility"] and i == v["improvement"]
        assert v["cagr"]["COST_10BP"] == m1.cagr(s["cumulative_return"])
        candidates.append({"id": name, "eligible_pass": e["pass"], "improved_pass": i["pass"],
                           "cagr_10bp": v["cagr"]["COST_10BP"], "mean_10bp": s["all_session_mean"]})
    assert m1.winner(candidates, rules) == result["winner"]


def test_frozen_base_and_m0_artifacts_unchanged() -> None:
    import subprocess
    for path in ("docs/backtest/strategy_e_candidate/strategy_e_v1_1_replay_result.json",
                 "docs/backtest/strategy_e_candidate/strategy_e_trading_v1_1_rules.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m0_rules_v1.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m1_rules_v1.json"):
        frozen = subprocess.run(["git", "-C", str(ROOT), "show", f"058217f:{path}"],
                                capture_output=True, check=True).stdout
        assert (ROOT / path).read_bytes() == frozen
