"""The D6-C preregistration, plus the two documents it stands on.

Three hashes are checked before anything runs: the D6-A strategy contract, the I1 execution
interpretation, and this backtest contract. Any of them differing from its freeze record stops
the run, because a result produced under a different contract is not the result that was
preregistered.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..d6.contract import (Contract as StrategyContract, ContractHashMismatch,
                           ContractIncomplete, sha256_file)
from ..d6.contract import load as load_strategy_contract

REPO_ROOT = Path(__file__).resolve().parents[5]
D6C_MD = REPO_ROOT / "docs/crypto/CRYPTO_D6_C_BACKTEST_CONTRACT_V1.md"
D6C_JSON = REPO_ROOT / "data/research/crypto/d6/d6c_contract_v1.json"
D6C_FREEZE = REPO_ROOT / "data/runtime/crypto/d6/d6c_freeze_v1.json"
I1_MD = REPO_ROOT / "docs/crypto/CRYPTO_D6_EXECUTION_INTERPRETATION_I1_V1.md"
I1_JSON = REPO_ROOT / "data/research/crypto/d6/execution_interpretation_i1_v1.json"
I1_FREEZE = REPO_ROOT / "data/runtime/crypto/d6/i1_freeze_v1.json"

RISK_LIMIT = REPO_ROOT / "data/runtime/crypto/BTCUSDT/reference/risk_limit_1790128376.json"
FEE_VERIFICATION = REPO_ROOT / "data/runtime/crypto/reference/fee_source_verification_v1.json"


@dataclass(frozen=True)
class BacktestContract:
    doc: dict[str, Any]
    sha256: str
    i1: dict[str, Any]
    i1_sha256: str
    strategy: StrategyContract

    # --- window and folds -----------------------------------------------------------------
    @property
    def fold_boundaries_utc(self) -> list[str]:
        return list(self.doc["folds"]["boundaries_utc"])

    @property
    def sample_start_utc(self) -> str:
        return self.doc["window"]["sample_start_utc"]

    @property
    def end_exclusive_utc(self) -> str:
        return self.doc["window"]["end_exclusive_utc"]

    # --- account --------------------------------------------------------------------------
    @property
    def account(self) -> dict[str, Any]:
        return self.doc["account"]

    @property
    def fill_model(self) -> dict[str, Any]:
        return self.doc["fill_model"]

    @property
    def arms(self) -> dict[str, dict[str, Any]]:
        return self.doc["arms"]

    @property
    def costs(self) -> dict[str, Any]:
        return self.doc["costs"]

    @property
    def bootstrap(self) -> dict[str, Any]:
        return self.doc["metrics"]["bootstrap"]

    def arm(self, name: str) -> dict[str, Any]:
        base = {"score_threshold": self.strategy.long_entry_min_score,
                "hold_min": self.strategy.max_hold_min,
                "stop_k": self.strategy.stop["sigma_multiplier"],
                "oi_window_min": 60,
                "stop_enabled": True,
                "judging": False}
        base.update(self.arms[name])
        return base


def _verify(path: Path, expected: str, label: str) -> str:
    digest = sha256_file(path)
    if digest != expected:
        raise ContractHashMismatch(f"{label}: {digest} != frozen {expected}")
    return digest


def load() -> BacktestContract:
    """Load all three contracts, refusing on any hash or structural disagreement."""
    for path in (D6C_MD, D6C_JSON, D6C_FREEZE, I1_MD, I1_JSON, I1_FREEZE):
        if not path.exists():
            raise ContractIncomplete(f"missing: {path}")

    strategy = load_strategy_contract()          # verifies the D6-A pair on its own

    i1_freeze = json.loads(I1_FREEZE.read_text())
    _verify(I1_MD, i1_freeze["sha256"], "I1 interpretation")
    i1_digest = _verify(I1_JSON, i1_freeze["machine_readable_sha256"], "I1 machine-readable")
    if i1_freeze["amends_sha256"] != strategy.sha256 and \
            i1_freeze["amends_sha256"] != sha256_file(
                REPO_ROOT / "docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md"):
        raise ContractHashMismatch("I1 amends a different D6-A contract")

    freeze = json.loads(D6C_FREEZE.read_text())
    _verify(D6C_MD, freeze["sha256"], "D6-C contract")
    digest = _verify(D6C_JSON, freeze["machine_readable_sha256"], "D6-C machine-readable")

    doc = json.loads(D6C_JSON.read_text())
    i1 = json.loads(I1_JSON.read_text())

    if doc["depends_on"]["i1_sha256"] != freeze["i1_sha256"]:
        raise ContractHashMismatch("D6-C names an I1 hash the freeze record does not")
    if doc["window"]["untouched_holdout"] is not False:
        raise ContractIncomplete("the window must be declared as not an untouched holdout")
    if doc["gates"]["not_evaluable_counts_as_pass"] is not False:
        raise ContractIncomplete("NOT_EVALUABLE must never count as PASS")
    if doc["costs"]["maker_used"] is not False or doc["costs"]["maker_rescue_scenario_banned"] is not True:
        raise ContractIncomplete("maker is banned as an execution mode and as a rescue scenario")
    if doc["account"]["leverage"] != strategy.leverage:
        raise ContractIncomplete("D6-C leverage disagrees with the strategy contract")
    judging = [name for name, spec in doc["arms"].items() if spec.get("judging")]
    if judging != ["A-MAIN"]:
        raise ContractIncomplete(f"exactly one judging arm expected, found {judging}")

    return BacktestContract(doc=doc, sha256=digest, i1=i1, i1_sha256=i1_digest,
                            strategy=strategy)
