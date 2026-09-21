"""E-R2 V1.1 development replay protocol tests.

Protocol identity only. No replay, cost, bootstrap, block statistic or verdict is computed; test 13
makes every one of those entry points raise and loads the protocol anyway.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

from app.backtest.strategy_e1_forward import layout as forward_layout
from app.backtest.strategy_e_d6 import dataset as DS
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6 import replay as E6
from app.strategy_e import execution, exits, risk, signal
from app.strategy_e_v1_1 import context, decision
from app.strategy_e_v1_1 import replay_protocol as R


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_candidate"
E_R1_COMMIT = "0b932e60e02cda20a21e459d95a4929d48ce6d6f"


@pytest.fixture(scope="module")
def rules():
    return R.load_rules()


@pytest.fixture(scope="module")
def e_d6():
    return json.loads((DOCS / "strategy_e_d6_result_v1.json").read_text(encoding="utf-8"))


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)


# 1 ---------------------------------------------------------------------------------------------
def test_development_dataset_binding_is_the_e_d6_binding(rules, e_d6) -> None:
    data = rules["dataset"]
    manifest = DS.load_manifest()
    assert data["minute_tape_digest"] == DS.DEVELOPMENT_TAPE_DIGEST == manifest.digest
    assert data["minute_files"] == DS.DEVELOPMENT_FILES == len(manifest.files)
    assert data["minute_symbols"] == DS.DEVELOPMENT_SYMBOLS == len(manifest.symbols)
    bound = e_d6["dataset"]
    assert data["legacy_minute_digest"] == bound["minute_tape"]["legacy_minute_digest"]
    assert data["legacy_minute_files"] == bound["minute_tape"]["legacy_minute_files"]
    assert data["daily_freeze_digest"] == bound["daily"]["freeze_digest"]
    assert data["daily_read_set_digest"] == bound["daily"]["read_set_digest"]
    assert data["daily_snapshot_id"] == bound["daily"]["snapshot_id"] == "USB-HIST-V1"
    assert (data["grid"]["first"], data["grid"]["last"], data["grid"]["sessions"]) == (
        bound["daily"]["grid_first"], bound["daily"]["grid_last"], bound["daily"]["grid_sessions"])
    assert data["glob_of_live_store_forbidden"] is True
    assert data["minute_manifest"].endswith(DS.MANIFEST_PATH.name)


# 2 ---------------------------------------------------------------------------------------------
def test_forward_holdout_is_excluded(rules, e_d6) -> None:
    start = date.fromisoformat(rules["forward_exclusion"]["forward_holdout_start"])
    assert start == forward_layout.FORWARD_HOLDOUT_START == R.FORWARD_HOLDOUT_START
    sessions = R.timeline()
    assert sessions[-1] < start and date.fromisoformat(rules["dataset"]["grid"]["last"]) < start
    last_file_date = max(max(re.findall(r"\d{4}-\d{2}-\d{2}", name))
                         for _, name, _ in DS.load_manifest().files)
    assert date.fromisoformat(last_file_date) < start
    assert any(source.startswith("market_data/forward")
               for source in rules["forward_exclusion"]["excluded_sources"])
    assert forward_layout.FORWARD_MARKET_DATA.startswith("market_data/forward")


# 3 ---------------------------------------------------------------------------------------------
def test_v1_1_artifacts_link_to_the_e_r1_commit(rules) -> None:
    assert rules["upstream"]["e_r1_commit"] == E_R1_COMMIT
    assert _git("merge-base", "--is-ancestor", E_R1_COMMIT, "HEAD").returncode == 0
    for name in ("strategy_e_trading_v1_1_rules.json", "strategy_e_forward_feature_context_v1.json"):
        frozen = _git("show", f"{E_R1_COMMIT}:docs/backtest/strategy_e_candidate/{name}").stdout
        assert frozen == (DOCS / name).read_bytes()
    assert rules["upstream"]["trading_v1_1_rules_canonical_sha256"] == decision.RULES_CANONICAL_SHA256
    assert (rules["upstream"]["forward_feature_context_canonical_sha256"]
            == context.CONTRACT_CANONICAL_SHA256)


# 4-7 -------------------------------------------------------------------------------------------
def test_h5_is_unchanged(rules) -> None:
    alpha = decision.load_rules()["alpha"]
    assert alpha["statement"] == signal._load_frozen_rules()["alpha"]["statement"]
    assert alpha["changed"] is False
    assert rules["upstream"]["e_d0_rules_canonical_sha256"] == signal.TRADING_RULES_CANONICAL_SHA256
    assert "evaluate_h5_signal" in rules["strategy"]["signal"]


def test_max3_canonical_selection_is_unchanged(rules) -> None:
    e_d6_rules = json.loads((DOCS / "strategy_e_backtest_rules_v1.json").read_text("utf-8"))
    frozen = execution._load_rules()["selection"]["max_selected_candidates"]
    assert rules["strategy"]["max_selected"] == frozen == e_d6_rules["strategy"]["max_selected"] == 3
    assert (rules["strategy"]["candidate_priority"] == e_d6_rules["strategy"]["candidate_priority"]
            == "CANONICAL_SYMBOL_ASCENDING")


def test_no_backfill(rules) -> None:
    e_d6_rules = json.loads((DOCS / "strategy_e_backtest_rules_v1.json").read_text("utf-8"))
    assert rules["strategy"]["replacement_policy"] == e_d6_rules["strategy"]["replacement_policy"] == "NONE"
    assert decision.load_rules()["selection"]["selection_before_entry_validation"] is True
    assert "slot stays empty" in rules["strategy"]["entry"]
    assert "candidate replacement or invalid-entry backfill" in rules["prohibitions"]


def test_entry_and_exit_are_unchanged(rules) -> None:
    e_d6_rules = json.loads((DOCS / "strategy_e_backtest_rules_v1.json").read_text("utf-8"))
    assert rules["upstream"]["e_d2_execution_rules_canonical_sha256"] == execution.EXECUTION_RULES_CANONICAL_SHA256
    assert rules["upstream"]["e_d3_exit_rules_canonical_sha256"] == exits.EXIT_RULES_CANONICAL_SHA256
    assert e_d6_rules["strategy"]["entry"] in rules["strategy"]["entry"]
    assert e_d6_rules["strategy"]["exit"] in rules["strategy"]["exit"]
    assert rules["strategy"]["exit"].endswith("fallback NONE")
    assert (execution.ENTRY_BAR_START_ET, exits.EXIT_BAR_START_ET) == ("09:30", "09:34")
    for extra in ("stop", "profit_target", "partial_exit"):
        assert rules["strategy"][extra] == "NONE"
    assert risk.SIZING_MODEL in rules["strategy"]["sizing"]


# 8/9 -------------------------------------------------------------------------------------------
def test_cost_grid_is_unchanged(rules) -> None:
    assert tuple(rules["costs"]["scenarios"]) == E6.SCENARIOS
    assert rules["costs"]["scenario_bp"] == E6.SCENARIO_BP


def test_primary_is_10bp(rules) -> None:
    assert rules["costs"]["primary_scenario"] == E6.PRIMARY == "COST_10BP"
    assert rules["costs"]["primary_cannot_change_after_results"] is True


# 10-12 -----------------------------------------------------------------------------------------
def test_e_d6_gate_is_reused_verbatim(rules) -> None:
    e_d6_rules = json.loads((DOCS / "strategy_e_backtest_rules_v1.json").read_text("utf-8"))
    for key in ("data_quality", "net_economics", "statistical_support", "chronological_robustness",
                "precedence"):
        assert rules["gate"][key] == e_d6_rules["gate"][key]
    assert e_d6_rules["data_quality"]["minimum_coverage"] == X.MIN_COVERAGE == 0.95
    labels = rules["verdict_labels"]
    assert set(labels) == {X.PASS, X.FAIL, X.INCONCLUSIVE_STATISTICAL, X.INCONCLUSIVE_DATA,
                           X.INTEGRITY_BLOCK}
    assert labels[X.PASS] == "E-R3 PASS — TRADING V1.1 READY FOR FORWARD"
    assert labels[X.FAIL] == "E-R3 FAIL"
    assert labels[X.INCONCLUSIVE_STATISTICAL] == "E-R3 INCONCLUSIVE — STATISTICAL"
    assert labels[X.INCONCLUSIVE_DATA] == "E-R3 INCONCLUSIVE — DATA QUALITY"


def test_bootstrap_config_is_e_d6s(rules) -> None:
    boot = rules["statistics"]["bootstrap"]
    assert (boot["seed"], boot["replicates"]) == (X.BOOTSTRAP_SEED, X.BOOTSTRAP_REPLICATES)
    assert rules["statistics"]["annualization_sessions"] == X.ANNUALIZATION


def test_chronological_block_config_is_e_d6s(rules) -> None:
    assert rules["statistics"]["blocks"] == X.BLOCKS == 4
    sessions = [s.isoformat() for s in R.timeline()]
    assert len(sessions) == rules["evaluation_timeline"]["count"] == 480
    blocks = X.chronological_blocks(sessions)
    assert [[b[0], b[-1]] for b in blocks] == rules["statistics"]["expected_block_bounds"]


# 13 --------------------------------------------------------------------------------------------
def test_protocol_loads_without_any_performance_entry_point(monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a performance entry point was invoked during E-R2")

    for module, name in ((E6, "replay_session"), (E6, "evaluate_signals"),
                         (X, "scenario_summary"), (X, "bootstrap_mean_ci"), (X, "block_table"),
                         (X, "verdict"), (X, "concentration"), (execution, "build_entry_records"),
                         (exits, "resolve_exit_batch"), (risk, "build_sizing_records")):
        monkeypatch.setattr(module, name, refuse)
    R.load_rules()
    assert not (ROOT / R.RUNTIME_ROOT).exists(), "an E-R3 run exists before the protocol commit"
    assert not list(DOCS.glob("*_r3_*")) and not list(DOCS.glob("E_R3_*"))


def test_protocol_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_v1_1_replay_rules.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == R.RULES_CANONICAL_SHA256
    body = (DOCS / "strategy_e_v1_1_replay_rules.json").read_bytes()
    assert recorded["file_sha256"] == hashlib.sha256(body).hexdigest()
