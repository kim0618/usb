"""E-MAX-M6 / E-MAX V1 protocol, composition, diagnostics and committed-result tests."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e_d6 import metrics as X
from app.strategy_e_max import breadth, exposure, m0, m1, m2, m3, m4, m5, ranking, v1

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"


@pytest.fixture(scope="module")
def rules():
    return v1.load_rules()


def _dec(weight: str) -> Decimal:
    f = Fraction(weight)
    return Decimal(f.numerator) / Decimal(f.denominator)


def _session(nets, candidates, eligible):
    n = len(nets)
    records = [{"symbol": f"S{i}", "weight": f"1/{n}", "standard_pnl": True,
                "GROSS_0BP": Decimal(v), "COST_10BP": Decimal(v) - Decimal("0.001")}
               for i, v in enumerate(nets)]
    returns = {k: sum((_dec(r["weight"]) * r[k] for r in records), Decimal(0))
               for k in ("GROSS_0BP", "COST_10BP")}
    return {"session": "2026-06-01", "candidates": candidates, "eligible": eligible,
            "exposure": "1", "returns": returns, "records": records, "active": True}


# -- provenance ---------------------------------------------------------------------------------

def test_m0_to_m5_provenance_identity(rules) -> None:
    stages = rules["upstream"]["stages"]
    for name, module in {"M0": m0, "M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5}.items():
        assert stages[name]["rules_canonical_sha256"] == module.RULES_CANONICAL_SHA256
    for i in range(1, 6):
        path = DOCS / f"strategy_e_max_m{i}_result_v1.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == stages[f"M{i}"]["result_file_sha256"]
    assert [stages[f"M{i}"]["winner"] for i in range(1, 6)] == ["R1", "C1", "B2", "X1", "E20"]
    assert stages["M5"]["m6_authorization"] == "M6 AUTHORIZED"


def test_checksum_file(rules) -> None:
    recorded = json.loads((DOCS / "strategy_e_max_v1_rules.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == v1.RULES_CANONICAL_SHA256 == m0.canonical_sha256(dict(rules))
    assert recorded["file_sha256"] == hashlib.sha256((DOCS / "strategy_e_max_v1_rules.json").read_bytes()).hexdigest()


def test_stage_artifact_targets_are_the_committed_hashes(rules) -> None:
    art = rules["upstream"]["stage_artifacts"]
    m5_sha = json.loads((DOCS / "strategy_e_max_m5_result_v1.sha256").read_text("utf-8"))["runtime_artifacts"]
    m1_sha = json.loads((DOCS / "strategy_e_max_m1_result_v1.sha256").read_text("utf-8"))["runtime_artifacts"]
    assert art["S3_PLUS_GLOBAL_2X"]["trades_csv_sha256"] == m5_sha["E20_trades.csv"]
    assert art["S2_PLUS_B2"]["trades_csv_sha256"] == m5_sha["E1_trades.csv"]
    assert art["S1_R1_MAX3_X1_1X"]["daily_returns_csv_sha256"] == m1_sha["R1_daily_returns.csv"]


@pytest.mark.parametrize("path,value", [
    (("definition", "global_exposure", "multiplier"), 2.5),
    (("definition", "breadth", "threshold"), 0.03),
    (("definition", "capacity", "max_selected"), 5),
    (("definition", "ranking", "id"), "R2"),
    (("gate", "mdd_10bp_ge"), -0.40),
    (("gate", "top1_share_10bp_le"), 0.80),
    (("upstream", "stages", "M4", "winner"), "X2"),
])
def test_drift_is_refused(tmp_path, monkeypatch, path, value) -> None:
    payload = json.loads(v1.RULES_PATH.read_text(encoding="utf-8"))
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    target = tmp_path / "v1.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(v1, "RULES_CANONICAL_SHA256", m0.canonical_sha256(payload))
    with pytest.raises(v1.V1ContractError):
        v1.load_rules(target)


def test_digest_drift_is_refused(tmp_path) -> None:
    payload = json.loads(v1.RULES_PATH.read_text(encoding="utf-8"))
    payload["declaration"]["purpose"] += "."
    target = tmp_path / "v1.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(v1.V1ContractError):
        v1.load_rules(target)


# -- definition ---------------------------------------------------------------------------------

def test_h5_r1_max3_x1_identity(rules) -> None:
    d = rules["definition"]
    assert "premarket_rvol >= 3.0" in d["alpha"]["rule"] and "position_in_premarket_range >= 0.8" in d["alpha"]["rule"]
    assert (d["ranking"]["id"], d["capacity"]["max_selected"], d["exit"]["id"]) == ("R1", 3, "X1")
    feats = {"A": {"premarket_rvol": 4.0}, "B": {"premarket_rvol": 9.0}, "C": {"premarket_rvol": 4.0},
             "D": {"premarket_rvol": 5.0}}
    assert ranking.select(v1.RANKING, list(feats), feats, v1.MAX_SELECTED) == (("B", "D", "A"), ("C",))


def test_b2_and_global_identity(rules) -> None:
    assert breadth.multiplier(16, 520) == Fraction(3, 2)       # 0.0308 >= 0.030741
    assert breadth.multiplier(15, 520) == 1                    # 0.0288
    assert breadth.multiplier(5, 99) == 1                      # coverage guard
    assert v1.GLOBAL == exposure.MULTIPLIERS["E20"] == 2


@pytest.mark.parametrize("candidates,eligible,expected_weight,expected_exposure", [
    (3, 500, "2/3", Fraction(2)),           # normal
    (20, 500, "1/1", Fraction(3)),          # high breadth
])
def test_final_exposure_and_equal_weight(candidates, eligible, expected_weight, expected_exposure) -> None:
    s2, s3 = v1.compose(_session(["0.01", "-0.004", "0.002"], candidates, eligible))
    assert {r["weight"] for r in s3["records"]} == {expected_weight}
    assert Fraction(s3["exposure"]) == Fraction(s3["final_exposure_multiplier"]) == expected_exposure
    assert sum(Fraction(r["weight"]) for r in s3["records"]) == expected_exposure


def test_cost_scales_with_notional() -> None:
    base = _session(["0.01", "-0.004", "0.002"], 20, 500)
    _, s3 = v1.compose(base)
    drag = s3["returns"]["GROSS_0BP"] - s3["returns"]["COST_10BP"]
    assert abs(drag - Decimal(3) * Decimal("0.001")) < Decimal("1e-18")
    assert s3["returns"]["COST_10BP"] == base["returns"]["COST_10BP"] * Decimal("1.5") * 2
    assert base["records"][0]["weight"] == "1/3", "input must not be mutated"


def test_compound_equity_and_mdd() -> None:
    r = np.array([0.10, -0.20, 0.05, 0.30])
    stats = X.equity_stats(r)
    assert stats["cumulative_return"] == pytest.approx(1.1 * 0.8 * 1.05 * 1.3 - 1)
    assert stats["maximum_drawdown"] == pytest.approx(-0.20)


# -- gate ---------------------------------------------------------------------------------------

GOOD = {"coverage": 0.96, "mean_10bp": 1e-4, "pf_10bp": 1.2, "mdd_10bp": -0.35, "top1_share_10bp": 0.7338,
        "catastrophic": False, "stop_a": False, "stop_b_flagged": False, "stop_d": False}


@pytest.mark.parametrize("change,failing", [
    ({}, None), ({"mdd_10bp": -0.3501}, "mdd_10bp"), ({"coverage": 0.9499}, "coverage"),
    ({"mean_10bp": 0.0}, "mean_session_10bp"), ({"pf_10bp": 1.0}, "profit_factor_10bp"),
    ({"top1_share_10bp": 0.74}, "concentration_top1"), ({"stop_b_flagged": True}, "stop_b_clear"),
    ({"catastrophic": True}, "no_catastrophic_session"),
])
def test_risk_gate(rules, change, failing) -> None:
    checks = v1.gate({**GOOD, **change}, rules)
    assert checks["pass"] is (failing is None)
    if failing:
        assert checks[failing] is False


def test_stop_b_is_the_m0_literal_against_e_base(rules) -> None:
    ref = v1.e_base_reference()
    assert ref["cumulative_10bp"] == pytest.approx(0.0719, abs=1e-4)
    assert ref["cagr_10bp"] == m1.cagr(ref["cumulative_10bp"])
    assert v1.stop_b(0.30, -0.40, 0.20, -0.20)["flagged"]
    ok = v1.stop_b(0.40, -0.30, 0.20, -0.20)
    assert not ok["flagged"] and ok["margin_cagr_minus_mdd_ratio"] == pytest.approx(0.5)
    assert "M0 literal" in rules["gate"]["stop_conditions"]["B"]
    assert not v1.stop_a()


def test_cost_sensitivity_classification(rules) -> None:
    assert v1.cost_sensitivity({"COST_15BP": -0.3514, "COST_20BP": -0.40}, rules) == "HIGH"
    assert v1.cost_sensitivity({"COST_15BP": -0.30, "COST_20BP": -0.40}, rules) == "ELEVATED"
    assert v1.cost_sensitivity({"COST_15BP": -0.30, "COST_20BP": -0.35}, rules) == "LOW"


def test_verdict_labels(rules) -> None:
    ok = {"pass": True}
    assert v1.verdict(integrity=True, reproducible=True, m5_identity=True, gate_checks=ok, rules=rules) == \
        "E-MAX-M6 PASS — E-MAX V1 DEVELOPMENT CANDIDATE FROZEN"
    assert v1.verdict(integrity=True, reproducible=True, m5_identity=True, gate_checks={"pass": False},
                      rules=rules) == "E-MAX-M6 FAIL"
    for flags in ({"integrity": False}, {"reproducible": False}, {"m5_identity": False}):
        args = {"integrity": True, "reproducible": True, "m5_identity": True, **flags}
        assert v1.verdict(**args, gate_checks=ok, rules=rules) == "E-MAX-M6 BLOCKED — INTEGRITY"


def test_no_further_optimization_path(rules) -> None:
    forbidden = rules["no_further_optimization"]["forbidden_on_this_development_data"]
    for item in ("2.1x", "1.9x", "new breadth threshold", "new ranking", "new exit", "new max positions",
                 "symbol exclusion", "cost relaxation"):
        assert item in forbidden
    assert "NOT LIVE-LEVERAGE APPROVED" in rules["financing_and_live"]["label"]
    assert "3x leverage approved" in rules["pass_meaning"]["does_not_mean"]
    assert not any(k in rules for k in ("variants", "candidates"))


# -- replay diagnostics (synthetic) --------------------------------------------------------------

from app.backtest.strategy_e_max import v1_replay as V


def test_drawdown_peak_trough_recovery() -> None:
    sessions = ["d1", "d2", "d3", "d4", "d5", "d6", "d7"]
    series = np.array([0.10, 0.0, -0.10, -0.05, 0.05, 0.12, 0.01])
    out = V.drawdown(series, sessions)
    assert out["peak_session_first"] == "d1" and out["peak_session_last"] == "d2"
    assert out["trough_session"] == "d4" and out["recovery_session"] == "d6"
    assert out["mdd"] == pytest.approx(0.9 * 0.95 - 1)
    assert (out["sessions_peak_to_trough"], out["sessions_trough_to_recovery"],
            out["underwater_sessions_peak_to_recovery"]) == (2, 2, 3)
    assert V.drawdown(np.array([0.1, -0.2, 0.05]), ["a", "b", "c"])["recovery_session"] == "OPEN"


def test_regime_breadth_and_quarter_splits(rules) -> None:
    sessions = ["2026-04-16", "2026-04-17", "2026-04-20", "2026-07-01"]
    series = np.array([-0.02, 0.01, 0.04, 0.03])
    s3 = [{"session": d, "active": True, "breadth_multiplier": "3/2" if d == "2026-07-01" else "1/1",
           "records": [{"standard_pnl": True}]} for d in sessions]
    reg = V.regimes(series, sessions, s3, rules)
    assert reg["LEGACY_NARROW"]["sessions"] == 2 and reg["BROAD_COVERAGE"]["sessions"] == 2
    assert reg["LEGACY_NARROW"]["mdd_10bp"] == pytest.approx(-0.02)
    assert reg["BROAD_COVERAGE"]["cumulative_10bp"] == pytest.approx(1.04 * 1.03 - 1)
    bd = V.breadth_dependence(series, sessions, s3)
    assert bd["high_breadth"]["sessions"] == 1 and bd["high_breadth"]["share_of_total"] == pytest.approx(0.5)
    q = V.quarters(series, sessions, ("2026Q2", "2026Q3"))
    assert q["2026Q2"]["sessions"] == 3 and q["2026Q3"]["share_of_total"] == pytest.approx(0.5)


def test_first_difference() -> None:
    assert V.first_difference({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) is None
    assert V.first_difference({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == "/a[1]/b: 2 != 3"


# -- committed E-MAX V1 result (phase B) ----------------------------------------------------------

RESULT = DOCS / "strategy_e_max_v1_result.json"


@pytest.fixture(scope="module")
def result():
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_result_checksum_and_repeat(result) -> None:
    recorded = json.loads((DOCS / "strategy_e_max_v1_result.sha256").read_text("utf-8"))
    assert recorded["file_sha256"] == hashlib.sha256(RESULT.read_bytes()).hexdigest()
    check = recorded["repeat_check"]
    assert check["identical"] and check["artifacts_identical"]
    assert check["first_result_digest"] == check["this_result_digest"] == recorded["file_sha256"]
    assert result["identity"]["v1_rules"] == v1.RULES_CANONICAL_SHA256


def test_stage_identities_and_m5_exact_reproduction(result) -> None:
    assert all(all(v.values()) for v in result["stage_identity"].values())
    assert all(v["identical"] and v["first_difference"] is None for v in result["m5_identity"].values())
    committed = json.loads((DOCS / "strategy_e_max_m5_result_v1.json").read_text("utf-8"))["variants"]["E20"]
    assert result["sections"]["evaluation"] == committed["evaluation"]
    assert result["independent_checks"]["pass"] and result["prechecks"]["in_process_deterministic"]


def test_trade_set_exposure_and_cost_table(result) -> None:
    f = result["funnel"]
    assert (f["standard_trades"], f["high_breadth_sessions"], f["selected"]) == (480, 26, 501)
    table = result["cost_table"]
    assert table["COST_10BP"]["mdd_ceiling_pass"] and not table["COST_15BP"]["mdd_ceiling_pass"]
    cumulative = [table[k]["cumulative"] for k in ("GROSS_0BP", "COST_05BP", "COST_10BP", "COST_15BP", "COST_20BP")]
    assert cumulative == sorted(cumulative, reverse=True)
    assert result["execution_cost_sensitivity"] == "HIGH"


def test_gate_stop_b_and_verdict_recompute(result, rules) -> None:
    assert v1.gate(result["verdict"]["summary"], rules) == result["gate"]
    ev = result["sections"]["evaluation"]["scenarios"]["COST_10BP"]
    ref = v1.e_base_reference()
    assert v1.stop_b(result["sections"]["cagr"]["COST_10BP"], ev["maximum_drawdown"], ref["cagr_10bp"],
                     ref["mdd_10bp"]) == {k: v for k, v in result["stop_b"]["authoritative_vs_e_base"].items()
                                          if k != "reference"}
    assert result["stop_b"]["diagnostic_vs_m5_e1"]["margin_cagr_minus_mdd_ratio"] < 0.02
    assert v1.verdict(integrity=result["integrity"], reproducible=True, m5_identity=True,
                      gate_checks=result["gate"], rules=rules) == result["verdict"]["label"]
    assert result["verdict"]["label"] == "E-MAX-M6 PASS — E-MAX V1 DEVELOPMENT CANDIDATE FROZEN"


def test_tail_breadth_and_regime_diagnostics(result) -> None:
    assert result["sections"]["tail"]["top5"]["share_of_total"] > 0.8
    assert result["breadth_dependence"]["high_breadth"]["sessions"] == 26
    reg = result["coverage_regimes"]
    assert reg["LEGACY_NARROW"]["cumulative_10bp"] < 0 < reg["BROAD_COVERAGE"]["cumulative_10bp"]
    assert sum(b["positive"] for b in result["sections"]["blocks"]) == 2
    assert result["drawdown"]["underwater_sessions_peak_to_recovery"] == \
        result["sections"]["risk"]["recovery_duration_sessions"]
