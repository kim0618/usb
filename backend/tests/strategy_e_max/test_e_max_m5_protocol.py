"""E-MAX-M5 protocol and exposure-composition tests (phase A). Synthetic sessions only."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e_d6 import metrics as X
from app.strategy_e_max import breadth, exposure, m0, m4, m5

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"


@pytest.fixture(scope="module")
def rules():
    return m5.load_rules()


def _dec(weight: str) -> Decimal:
    f = Fraction(weight)
    return Decimal(f.numerator) / Decimal(f.denominator)


def _session(weights, nets, breadth_k=Fraction(1)):
    records = [{"symbol": f"S{i}", "weight": w, "standard_pnl": w != "0/1", "entry_price": 10.0,
                "exit_price": 10.1, **({"COST_10BP": Decimal(n)} if w != "0/1" else {})}
               for i, (w, n) in enumerate(zip(weights, nets))]
    base = {"session": "2026-06-01", "exposure": str(sum(Fraction(w) for w in weights)),
            "returns": {"COST_10BP": sum((_dec(r["weight"]) * r["COST_10BP"] for r in records
                                          if r["standard_pnl"]), Decimal(0))},
            "records": records}
    return breadth.scale(base, breadth_k)


def test_upstream_identity(rules) -> None:
    up = rules["upstream"]
    assert up["m4_rules_canonical_sha256"] == m4.RULES_CANONICAL_SHA256
    assert (up["m4_winner"], up["m4_m5_authorization"]) == ("X1", "M5 AUTHORIZED")
    assert "R1 ordering + max 3 + B2" in rules["configuration"] and "X1" in rules["configuration"]


def test_candidates_are_exactly_the_m0_grid(rules) -> None:
    grid = [h["multiplier"] for h in m0.load_rules()["exposure"]["hypotheses"]]
    assert grid == [1.0, 1.5, 2.0]
    assert {k: float(v) for k, v in exposure.MULTIPLIERS.items()} == {"E1": 1.0, "E15": 1.5, "E20": 2.0}
    assert [c["id"] for c in rules["variants"]["candidates"]] == ["E15", "E20"]


@pytest.mark.parametrize("value", [1.25, 1.75, 2.5, 3.0])
def test_other_multipliers_are_refused(tmp_path, monkeypatch, value) -> None:
    payload = json.loads(m5.RULES_PATH.read_text(encoding="utf-8"))
    payload["variants"]["candidates"][1]["global_multiplier"] = value
    path = tmp_path / "m5.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(m5, "RULES_CANONICAL_SHA256", m0.canonical_sha256(payload))
    with pytest.raises(m5.M5ContractError):
        m5.load_rules(path)


@pytest.mark.parametrize("g,breadth_k,weights,expected", [
    (Fraction(2), Fraction(1), ("1/3", "1/3", "1/3"), "2/3"),
    (Fraction(2), Fraction(3, 2), ("1/3", "1/3", "1/3"), "1/1"),
    (Fraction(3, 2), Fraction(3, 2), ("1/2", "1/2", "0/1"), "9/8"),
])
def test_global_times_breadth_composition_and_equal_weights(g, breadth_k, weights, expected) -> None:
    out = exposure.apply_global(_session(weights, ["0.01", "-0.004", "0.002"], breadth_k), g)
    assert {r["weight"] for r in out["records"] if r["weight"] != "0/1"} == {expected}
    assert Fraction(out["final_exposure_multiplier"]) == g * breadth_k
    assert Fraction(out["exposure"]) == g * breadth_k


def test_cost_scales_with_notional_and_trades_are_untouched() -> None:
    base = _session(("1/3", "1/3", "1/3"), ["0.01", "-0.004", "0.002"])
    out = exposure.apply_global(base, Fraction(2))
    assert out["returns"]["COST_10BP"] == base["returns"]["COST_10BP"] * 2
    for a, b in zip(base["records"], out["records"]):
        assert {k: v for k, v in a.items() if k != "weight"} == {k: v for k, v in b.items() if k != "weight"}
    assert exposure.apply_global(base, Fraction(1))["returns"] == base["returns"]
    assert base["records"][0]["weight"] == "1/3", "input must not be mutated"


def test_compounded_mdd_is_recomputed_not_multiplied() -> None:
    r = np.array([0.05, -0.10, 0.02, -0.08, 0.04])
    one, two = X.equity_stats(r), X.equity_stats(2 * r)
    assert two["maximum_drawdown"] != pytest.approx(2 * one["maximum_drawdown"])
    equity = np.cumprod(1 + 2 * r)
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    assert two["maximum_drawdown"] == pytest.approx(float(np.min(equity / peak - 1)))


def test_catastrophic_sessions_are_detected_not_clipped() -> None:
    sessions = [{"session": "a", "returns": {"COST_10BP": Decimal("-1.0")}},
                {"session": "b", "returns": {"COST_10BP": Decimal("-0.99")}}]
    assert exposure.catastrophic(sessions) == ["a"]


def test_eligibility_includes_the_catastrophic_rule_and_ceiling(rules) -> None:
    good = {"integrity": True, "coverage": 0.96, "mean_10bp": 1e-4, "pf_10bp": 1.2,
            "mdd_10bp": -0.35, "top1_share_10bp": 0.7338, "catastrophic": False}
    assert m5.eligible(good, rules)["pass"]
    assert not m5.eligible({**good, "catastrophic": True}, rules)["pass"]
    assert not m5.eligible({**good, "mdd_10bp": -0.3501}, rules)["pass"]
    assert not m5.eligible({**good, "top1_share_10bp": 0.74}, rules)["pass"]


def test_improvement_winner_and_m6(rules) -> None:
    assert rules["improvement"]["comparator"].startswith("E1 for each candidate")
    assert m5.improved(0.5, 0.4, {"mean": 1e-5, "p_mean_le_zero": 0.1}, rules)["pass"]
    assert not m5.improved(0.5, 0.4, {"mean": 1e-5, "p_mean_le_zero": 0.11}, rules)["pass"]

    def c(i, cagr, ok=True):
        return {"id": i, "eligible_pass": ok, "improved_pass": ok, "cagr_10bp": cagr, "mean_10bp": 1e-4}
    assert m5.winner([c("E15", 0.3), c("E20", 0.4)], rules)["label"] == "E-MAX-M5 PASS — 2.0X SELECTED"
    assert m5.winner([c("E15", 0.3), c("E20", 0.4, ok=False)], rules)["label"] == "E-MAX-M5 PASS — 1.5X SELECTED"
    assert m5.winner([c("E15", 0.3, ok=False), c("E20", 0.4, ok=False)], rules) == \
        {"winner": "E1", "label": "E-MAX-M5 NO_USEFUL_ENHANCEMENT", "pool": []}
    assert m5.m6({"pass": True}, rules) == "M6 AUTHORIZED"
    assert m5.m6({"pass": False}, rules) == "M6 NOT AUTHORIZED"
    assert m5.m6({"pass": True}, rules, stop_d=True) == "M6 NOT AUTHORIZED"


def test_scaling_caveat_and_financing_recorded(rules) -> None:
    assert "not an exposure effect" in rules["improvement"]["scaling_caveat"]
    assert rules["financing"]["FINANCING_COST"].startswith("NOT MODELED")
    assert rules["financing"]["BROKER_LEVERAGE_FEASIBILITY"].startswith("NOT VALIDATED")
    forbidden = " ".join(rules["variants"]["forbidden"])
    assert "1.25" in forbidden and "clipping" in forbidden


def test_paired_bootstrap_deterministic() -> None:
    delta = np.random.default_rng(4).normal(0, 1e-3, 480)
    assert X.bootstrap_mean_ci(delta) == X.bootstrap_mean_ci(delta)


def test_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m5_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == m5.RULES_CANONICAL_SHA256
    assert recorded["file_sha256"] == hashlib.sha256((DOCS / "strategy_e_max_m5_rules_v1.json").read_bytes()).hexdigest()


# -- replay diagnostics (synthetic) --------------------------------------------------------------

from app.backtest.strategy_e_max import m5_replay as M5R


def test_mdd_window_and_breadth_split() -> None:
    sessions = ["d1", "d2", "d3", "d4", "d5"]
    series = np.array([0.10, -0.05, -0.10, 0.02, 0.20])
    replayed = [{"session": d, "active": True, "breadth_multiplier": "3/2" if d == "d3" else "1/1",
                 "final_exposure_multiplier": "3/1" if d == "d3" else "2/1"} for d in sessions]
    out = M5R.breadth_interaction(sessions, series, replayed)
    assert out["mdd_window"]["from"] == "d2" and out["mdd_window"]["to"] == "d3"
    assert out["mdd_window"]["sum_high_breadth_10bp"] == pytest.approx(-0.10)
    assert out["mdd_window"]["sum_normal_10bp"] == pytest.approx(-0.05)
    assert out["high_breadth"]["sessions"] == 1 and out["normal"]["final_exposure"] == ["2/1"]


def test_tail_blocks_and_stop_b() -> None:
    sessions = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    series = np.array([0.03, -0.01, 0.01, 0.02])
    t = M5R.tail(series, sessions)
    assert t["top1"]["sessions"] == ["2026-01-02"] and t["top1"]["share_of_total"] == pytest.approx(0.6)
    b = M5R.blocks(series, sessions, [{"block": 1, "first": "2026-01-02", "last": "2026-01-07", "trades": 4}])
    assert b[0]["positive"] and b[0]["block_mdd_10bp"] == pytest.approx(-0.01)
    assert M5R.stop_b(0.30, -0.40, 0.20, -0.20)["flagged"]
    assert not M5R.stop_b(0.40, -0.30, 0.20, -0.20)["flagged"]


# -- committed M5 result (phase B) ---------------------------------------------------------------

RESULT = DOCS / "strategy_e_max_m5_result_v1.json"


@pytest.fixture(scope="module")
def result():
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_result_checksum_and_repeat(result) -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m5_result_v1.sha256").read_text("utf-8"))
    assert recorded["file_sha256"] == hashlib.sha256(RESULT.read_bytes()).hexdigest()
    check = recorded["repeat_check"]
    assert check["identical"] and check["artifacts_identical"]
    assert check["first_result_digest"] == check["this_result_digest"] == recorded["file_sha256"]


def test_e1_reproduced_m4_x1_and_invariants(result, rules) -> None:
    pre = result["prechecks"]
    assert pre["e1_reproduces_m4_x1"] == {"trades_csv": rules["upstream"]["m4_x1_trades_csv_sha256"],
                                          "daily_returns_csv": rules["upstream"]["m4_x1_daily_returns_csv_sha256"]}
    assert pre["invariants"] == {"same_trade_set": True, "exact_scaling": True, "pass": True}
    assert result["identity"]["m5_rules"] == m5.RULES_CANONICAL_SHA256


def test_trade_set_and_concentration_identical_across_multipliers(result) -> None:
    evs = [v["evaluation"] for v in result["variants"].values()]
    assert {e["funnel"]["standard_pnl_trades"]["count"] for e in evs} == {480}
    assert len({round(e["concentration"]["cost_10bp"]["top1"]["share_of_total"], 12) for e in evs}) == 1


def test_mdd_is_from_the_compounded_series(result) -> None:
    e1 = result["variants"]["E1"]["evaluation"]["scenarios"]["COST_10BP"]["maximum_drawdown"]
    e20 = result["variants"]["E20"]["evaluation"]["scenarios"]["COST_10BP"]["maximum_drawdown"]
    assert e20 != pytest.approx(2 * e1)


def test_verdict_and_m6_recompute(result, rules) -> None:
    candidates = []
    for name in ("E15", "E20"):
        v = result["variants"][name]
        s = v["evaluation"]["scenarios"]["COST_10BP"]
        summary = {"integrity": result["integrity"], "coverage": v["evaluation"]["funnel"]["standard_pnl_coverage"],
                   "mean_10bp": s["all_session_mean"], "pf_10bp": s["profit_factor"],
                   "mdd_10bp": s["maximum_drawdown"], "catastrophic": bool(v["catastrophic_sessions"]),
                   "top1_share_10bp": v["evaluation"]["concentration"]["cost_10bp"]["top1"]["share_of_total"]}
        assert m5.eligible(summary, rules) == v["eligibility"]
        base = result["variants"]["E1"]["evaluation"]["scenarios"]["COST_10BP"]["cumulative_return"]
        assert m5.improved(s["cumulative_return"], base, v["paired_vs_e1"], rules) == v["improvement"]
        candidates.append({"id": name, "eligible_pass": v["eligibility"]["pass"],
                           "improved_pass": v["improvement"]["pass"],
                           "cagr_10bp": v["cagr"]["COST_10BP"], "mean_10bp": s["all_session_mean"]})
    decided = m5.winner(candidates, rules)
    assert {k: decided[k] for k in ("winner", "label", "pool")} == \
        {k: result["verdict"][k] for k in ("winner", "label", "pool")}
    assert m5.m6(result["variants"][decided["winner"]]["eligibility"], rules) == result["verdict"]["m6"]


def test_paired_p_is_identical_across_candidates_as_declared(result) -> None:
    assert result["variants"]["E15"]["paired_vs_e1"]["p_mean_le_zero"] == \
        result["variants"]["E20"]["paired_vs_e1"]["p_mean_le_zero"]


def test_upstream_artifacts_unchanged() -> None:
    import subprocess
    for path in ("docs/backtest/strategy_e_max/strategy_e_max_m0_rules_v1.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m4_result_v1.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m5_rules_v1.json",
                 "docs/backtest/strategy_e_candidate/strategy_e_v1_1_replay_result.json"):
        frozen = subprocess.run(["git", "-C", str(ROOT), "show", f"d413da0:{path}"],
                                capture_output=True, check=True).stdout
        assert (ROOT / path).read_bytes() == frozen
