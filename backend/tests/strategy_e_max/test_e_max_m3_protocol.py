"""E-MAX-M3 protocol, breadth exposure and risk-diagnostic tests (phase A). No return computed."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e_d6 import metrics as X
from app.strategy_e_max import breadth, m0, m1, m2, m3, risk_diag

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"


@pytest.fixture(scope="module")
def rules():
    return m3.load_rules()


def _session(weights, nets):
    records = [{"symbol": f"S{i}", "weight": w, "standard_pnl": w != "0/1",
                **({"COST_10BP": Decimal(n), "GROSS_0BP": Decimal(n) + Decimal("0.001")} if w != "0/1" else {})}
               for i, (w, n) in enumerate(zip(weights, nets))]
    def dec(weight):
        f = Fraction(weight)
        return Decimal(f.numerator) / Decimal(f.denominator)
    returns = {"COST_10BP": sum((dec(r["weight"]) * r["COST_10BP"] for r in records if r["standard_pnl"]),
                                Decimal(0))}
    return {"session": "2026-06-01", "exposure": str(sum(Fraction(w) for w in weights)),
            "returns": returns, "records": records}


def test_upstream_identity(rules) -> None:
    up = rules["upstream"]
    assert up["m0_rules_canonical_sha256"] == m0.RULES_CANONICAL_SHA256
    assert up["m1_rules_canonical_sha256"] == m1.RULES_CANONICAL_SHA256
    assert up["m2_rules_canonical_sha256"] == m2.RULES_CANONICAL_SHA256
    assert (up["m1_winner"], up["m2_winner"]) == ("R1", "C1")


def test_threshold_and_universe_gate_identity(rules) -> None:
    assert rules["variants"]["threshold"] == breadth.THRESHOLD == 0.030741
    assert rules["variants"]["min_universe_rows"] == breadth.MIN_UNIVERSE_ROWS == 100
    assert breadth.h5_rate(5, 99) is None
    assert breadth.multiplier(30, 99) == 1
    assert breadth.multiplier(4, 100) == Fraction(3, 2)          # 0.04 >= threshold
    assert breadth.multiplier(3, 100) == 1                        # 0.03 < threshold
    assert breadth.multiplier(30741, 1_000_000) == Fraction(3, 2)
    assert breadth.multiplier(30740, 1_000_000) == 1


def test_b1_is_constant_one_and_b2_is_one_or_one_and_a_half(rules) -> None:
    assert rules["variants"]["comparator"]["exposure"] == "1.0 on every session"
    assert {breadth.multiplier(c, u) for c in range(0, 60) for u in (50, 100, 500)} == {1, Fraction(3, 2)}


@pytest.mark.parametrize("weights,expected", [(("1/3", "1/3", "1/3"), "1/2"), (("1/2", "1/2", "0/1"), "3/4"),
                                              (("1/1", "0/1", "0/1"), "3/2")])
def test_high_breadth_weights_sum_to_one_and_a_half(weights, expected) -> None:
    scaled = breadth.scale(_session(weights, ["0.01", "-0.002", "0.004"]), Fraction(3, 2))
    assert {r["weight"] for r in scaled["records"] if r["weight"] != "0/1"} == {expected}
    assert Fraction(scaled["exposure"]) == Fraction(3, 2)


def test_scaling_keeps_the_trade_set_and_scales_cost_inclusive_returns() -> None:
    base = _session(("1/3", "1/3", "1/3"), ["0.01", "-0.002", "0.004"])
    scaled = breadth.scale(base, Fraction(3, 2))
    assert [r["symbol"] for r in scaled["records"]] == [r["symbol"] for r in base["records"]]
    assert [r["COST_10BP"] for r in scaled["records"]] == [r["COST_10BP"] for r in base["records"]]
    assert scaled["returns"]["COST_10BP"] == base["returns"]["COST_10BP"] * Decimal("1.5")
    assert breadth.scale(base, Fraction(1))["returns"] == base["returns"]
    assert base["records"][0]["weight"] == "1/3", "scaling must not mutate its input"


def test_no_threshold_or_multiplier_search(rules) -> None:
    forbidden = " ".join(rules["variants"]["forbidden"])
    assert "any other threshold" in forbidden and "any other multiplier" in forbidden
    assert "trade filter" in forbidden


def test_financing_not_modeled_and_research_only(rules) -> None:
    assert rules["exposure_semantics"]["financing"].startswith("NOT MODELED")
    assert "not a live 1.5x leverage approval" in rules["exposure_semantics"]["risk_status"]


def test_gates_comparator_and_m4(rules) -> None:
    assert rules["improvement"]["comparator"].startswith("B1")
    e = rules["eligibility"]
    assert (e["mdd_10bp_ge"], e["top1_share_10bp_le"], e["min_coverage"]) == (-0.35, 0.7338, 0.95)
    ok = {"pass": True}
    no = {"pass": False}
    assert m3.decide(ok, ok, ok, rules) == {"winner": "B2", "label": "E-MAX-M3 PASS — B2 SELECTED", "m4": "M4 AUTHORIZED"}
    assert m3.decide(ok, no, ok, rules)["label"] == "E-MAX-M3 NO_USEFUL_ENHANCEMENT"
    assert m3.decide(ok, no, ok, rules)["m4"] == "M4 AUTHORIZED"
    assert m3.decide(no, no, no, rules)["m4"] == "M4 NOT AUTHORIZED"


def test_mdd_ceiling_and_concentration_guard(rules) -> None:
    good = {"integrity": True, "coverage": 0.96, "mean_10bp": 1e-4, "pf_10bp": 1.2,
            "mdd_10bp": -0.35, "top1_share_10bp": 0.7338}
    assert m3.eligible(good, rules)["pass"]
    assert not m3.eligible({**good, "mdd_10bp": -0.3501}, rules)["pass"]
    assert not m3.eligible({**good, "top1_share_10bp": 0.734}, rules)["pass"]


def test_paired_bootstrap_deterministic() -> None:
    delta = np.zeros(480)
    delta[:26] = np.random.default_rng(2).normal(0, 1e-3, 26)
    assert X.bootstrap_mean_ci(delta) == X.bootstrap_mean_ci(delta)


def test_risk_diagnostics_on_a_known_series() -> None:
    sessions = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-08", "2026-06-09", "2026-07-01"]
    out = risk_diag.diagnostics(sessions, [0.01, -0.02, -0.01, 0.005, 0.05, -0.03])
    assert out["worst_session"] == {"session": "2026-07-01", "return": -0.03}
    assert out["longest_losing_run_sessions"] == 2
    assert out["recovery_duration_sessions"] == 3 and out["recovery_span"]["recovered"]
    assert out["worst_month"]["period"] == "2026-07"


def test_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m3_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == m3.RULES_CANONICAL_SHA256
    assert recorded["file_sha256"] == hashlib.sha256((DOCS / "strategy_e_max_m3_rules_v1.json").read_bytes()).hexdigest()
