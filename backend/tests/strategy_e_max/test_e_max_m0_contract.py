"""E-MAX-M0 contract tests. Contract identity only: no ranking, replay or return is computed."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from app.backtest.strategy_e1_forward.seal import H5_INPUTS
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6 import replay as E6
from app.strategy_e import execution, exits, risk, signal
from app.strategy_e_max import m0
from app.strategy_e_v1_1 import decision


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"
BASE_DOCS = ROOT / "docs/backtest/strategy_e_candidate"


@pytest.fixture(scope="module")
def rules():
    return m0.load_rules()


def _mutated(tmp_path: Path, edit) -> Path:
    payload = json.loads(m0.RULES_PATH.read_text(encoding="utf-8"))
    edit(payload)
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# 1-3 base untouched -----------------------------------------------------------------------------
def test_base_h5_is_unchanged(rules) -> None:
    alpha = signal._load_frozen_rules()["alpha"]
    assert alpha["statement"] == decision.load_rules()["alpha"]["statement"]
    assert signal.TRADING_RULES_CANONICAL_SHA256 in rules["base_frozen"]["alpha"]


def test_base_trading_v1_1_is_unchanged(rules) -> None:
    decision.load_rules()
    frozen = subprocess.run(["git", "-C", str(ROOT), "show", "0b932e6:docs/backtest/"
                             "strategy_e_candidate/strategy_e_trading_v1_1_rules.json"],
                            capture_output=True, check=True).stdout
    assert frozen == (BASE_DOCS / "strategy_e_trading_v1_1_rules.json").read_bytes()


def test_base_e_r3_result_is_unchanged(rules) -> None:
    body = (BASE_DOCS / "strategy_e_v1_1_replay_result.json").read_bytes()
    assert hashlib.sha256(body).hexdigest() == m0.BASE_RESULT_SHA256
    result = json.loads(body)
    assert result["verdict"]["label"] == "E-R3 INCONCLUSIVE — STATISTICAL"
    ref = rules["base_reference_values"]
    assert ref["standard_trades"] == result["funnel"]["standard_pnl_trades"]["count"]
    assert round(result["primary_gate"]["all_session_mean"] * 1e4, 2) == ref["session_mean_10bp_bp"]
    assert round(result["scenarios"]["COST_10BP"]["maximum_drawdown"], 4) == ref["mdd_10bp"]
    assert round(result["concentration"]["cost_10bp"]["top1"]["share_of_total"], 4) == ref["top1_share_10bp"]


# 4-9 feature pool and budgets ------------------------------------------------------------------
def test_feature_pool_is_the_h5_inputs(rules) -> None:
    assert tuple(rules["feature_pool"]["allowed"]) == H5_INPUTS
    assert rules["feature_pool"]["new_features_forbidden"] is True
    assert rules["feature_pool"]["gap_alone_ranking"]["included"] is False


@pytest.mark.parametrize("axis,limit", sorted(m0.BUDGET.items()))
def test_each_axis_stays_within_budget(rules, axis, limit) -> None:
    assert rules["research_budget"][axis] == limit
    assert len(rules[axis]["hypotheses"]) <= limit


def test_ranking_hypotheses_are_the_declared_three(rules) -> None:
    assert [h["id"] for h in rules["ranking"]["hypotheses"]] == ["R1", "R2", "R3"]
    assert rules["ranking"]["tie_break"] == "canonical symbol ascending"
    assert "weight search forbidden" in rules["ranking"]["hypotheses"][2]["weights"]


def test_a_fourth_ranking_hypothesis_is_refused(tmp_path) -> None:
    path = _mutated(tmp_path, lambda p: p["ranking"]["hypotheses"].append(
        {"id": "R4", "rule": "premarket_gap descending"}))
    with pytest.raises(m0.MaxContractError):
        m0.load_rules(path)


def test_capacity_breadth_exit_exposure_values(rules) -> None:
    assert [h["max_selected"] for h in rules["capacity"]["hypotheses"]] == [3, 5]
    assert [h["id"] for h in rules["breadth"]["hypotheses"]] == ["B1", "B2"]
    assert "0.030741" in rules["breadth"]["hypotheses"][1]["rule"]
    assert [h["id"] for h in rules["exit"]["hypotheses"]] == ["X1", "X2"]
    assert [h["multiplier"] for h in rules["exposure"]["hypotheses"]] == [1.0, 1.5, 2.0]


# 10-12 envelope --------------------------------------------------------------------------------
def test_mdd_hard_ceiling_is_minus_35_percent(rules) -> None:
    assert rules["risk_envelope"]["mdd_hard_ceiling"] == m0.MDD_CEILING == -0.35
    assert m0.mdd_eligible(-0.35) and m0.mdd_eligible(-0.10) and not m0.mdd_eligible(-0.3501)


def test_primary_cost_remains_10bp(rules) -> None:
    assert rules["risk_envelope"]["primary_cost"] == E6.PRIMARY == "COST_10BP"
    assert "lowering the primary cost below 10 bp" in rules["prohibitions"]


def test_absi_exclusion_is_forbidden(rules) -> None:
    assert "forbidden" in rules["risk_envelope"]["symbol_exclusion"]
    assert "excluding ABSI or any symbol, or any period" in rules["prohibitions"]


# 13 no performance ------------------------------------------------------------------------------
def test_contract_loads_without_any_performance_entry_point(monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a performance entry point was invoked during E-MAX-M0")

    for module, name in ((E6, "replay_session"), (X, "scenario_summary"), (X, "bootstrap_mean_ci"),
                         (X, "verdict"), (execution, "build_entry_records"),
                         (exits, "resolve_exit_batch"), (risk, "build_sizing_records")):
        monkeypatch.setattr(module, name, refuse)
    m0.load_rules()
    assert not (ROOT / "data/runtime/strategy_e_max").exists()


# 14 no unlimited grid ---------------------------------------------------------------------------
@pytest.mark.parametrize("edit", [
    lambda p: p["capacity"]["hypotheses"].append({"id": "C3", "max_selected": 7}),
    lambda p: p["capacity"]["hypotheses"][1].__setitem__("max_selected", 6),
    lambda p: p["exposure"]["hypotheses"][2].__setitem__("multiplier", 3.0),
    lambda p: p["research_budget"].__setitem__("grid_search_forbidden", False),
    lambda p: p["exit"].__setitem__("horizon_scan_forbidden", False),
    lambda p: p["risk_envelope"].__setitem__("mdd_hard_ceiling", -0.5),
])
def test_grid_expansion_is_refused(tmp_path, edit) -> None:
    with pytest.raises(m0.MaxContractError):
        m0.load_rules(_mutated(tmp_path, edit))


# 15 roadmap -------------------------------------------------------------------------------------
def test_roadmap_identity(rules) -> None:
    assert tuple(rules["stages"]["order"]) == m0.ROADMAP
    assert rules["stages"]["skip_rule"].startswith("a stage whose outcome is NO_USEFUL_ENHANCEMENT")


def test_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m0_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == m0.RULES_CANONICAL_SHA256
    body = (DOCS / "strategy_e_max_m0_rules_v1.json").read_bytes()
    assert recorded["file_sha256"] == hashlib.sha256(body).hexdigest()
