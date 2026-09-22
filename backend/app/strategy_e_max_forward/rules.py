"""E-MAX-F0 forward rules: loader, upstream integrity and the M6 provenance closure.

The forward code lives outside ``app.strategy_e_max`` and ``app.backtest.strategy_e_max`` on
purpose: M6's code identity hashes every module in those two packages, and adding a file there
would make the committed M6 result unreproducible.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.backtest.strategy_e1_forward import checkpoint as E1C, layout
from app.strategy_e_max import m0, v1
from app.strategy_e_v1_1 import context, decision

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs/backtest/strategy_e_max"
RULES_PATH = DOCS / "strategy_e_max_forward_rules_v1.json"
RULES_CANONICAL_SHA256 = "dd8ddf42f3fba3ef77a44d9e40bb6308290fda4c45b5a7df7ce268514b1cfbaa"
M6_RESULT_PATH = DOCS / "strategy_e_max_v1_result.json"
M6_RUNNER = REPO_ROOT / "backend/app/dev/run_strategy_e_max_m6.py"
COST_SCENARIOS = ("GROSS_0BP", "COST_05BP", "COST_10BP", "COST_15BP", "COST_20BP")
E_BASE, E_MAX = "E_BASE_FORWARD", "E_MAX_FORWARD"
LIVE, RECONSTRUCTED = context.LIVE, context.RECONSTRUCTED
EVIDENCE_CLASS = {LIVE: "PRIMARY", RECONSTRUCTED: "SECONDARY"}


class ForwardRulesError(ValueError):
    """The F0 contract or one of its upstream artifacts does not verify."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ForwardRulesError(message)


def m6_code_identity() -> str:
    """M6's ``identity.code`` recomputed from the sources as they are now."""
    from app.backtest.strategy_e_d6 import run as R6
    from app.dev.run_strategy_e_max_m1 import code_digest
    return R6.canonical_sha256({"packages_and_m1_runner": code_digest(),
                                "m6_runner": hashlib.sha256(M6_RUNNER.read_bytes()).hexdigest()})


def provenance_closure(rules: Mapping) -> dict[str, Any]:
    """V1 rules, M6 result file, M6 verdict and M6 code identity against the committed values."""
    up = rules["upstream"]
    result_bytes = M6_RESULT_PATH.read_bytes()
    result = json.loads(result_bytes)
    code_now = m6_code_identity()
    checks = {
        "v1_rules_digest": v1.RULES_CANONICAL_SHA256 == up["e_max_v1_rules_canonical_sha256"]
        == result["identity"]["v1_rules"],
        "m6_result_digest": hashlib.sha256(result_bytes).hexdigest() == up["e_max_m6_result_file_sha256"],
        "m6_verdict": result["verdict"]["label"] == up["e_max_m6_verdict"],
        "m6_code_identity": code_now == result["identity"]["code"],
        "development_tape": result["identity"]["tape"] == up["development_tape_digest"],
        "e_base_v1_1": result["identity"]["trading_v1_1"] == up["e_base_trading_v1_1_canonical_sha256"]
        == decision.RULES_CANONICAL_SHA256,
    }
    checks["pass"] = all(checks.values())
    return {"checks": checks, "m6_code_identity_committed": result["identity"]["code"],
            "m6_code_identity_now": code_now}


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(m0.canonical_sha256(payload) == RULES_CANONICAL_SHA256, "F0 contract digest differs")
    v1.load_rules()                                   # M0-M5 chain, V1 definition
    context.load_contract()
    decision.load_rules()
    up = payload["upstream"]
    _require(up["e_max_v1_rules_canonical_sha256"] == v1.RULES_CANONICAL_SHA256, "V1 rules moved")
    _require(up["forward_feature_context_canonical_sha256"] == context.CONTRACT_CANONICAL_SHA256,
             "context contract moved")
    _require(up["e_base_trading_v1_1_canonical_sha256"] == decision.RULES_CANONICAL_SHA256, "V1.1 moved")
    b = payload["boundary"]
    _require(b["forward_holdout_start"] == layout.FORWARD_HOLDOUT_START.isoformat()
             and b["development_last_session"] == layout.HISTORICAL_LAST_SESSION.isoformat(), "boundary moved")
    _require(tuple(payload["checkpoints"]["values"]) == E1C.CHECKPOINTS, "checkpoints differ from E1 forward")
    _require(tuple(payload["cost"]["scenarios"]) == COST_SCENARIOS
             and payload["cost"]["primary"] == m0.PRIMARY_COST, "cost scenarios moved")
    e = payload["strategies"][E_MAX]
    _require(e["global_multiplier"] == float(v1.GLOBAL)
             and e["final_exposure"] == {k: float(x) for k, x in v1.FINAL_EXPOSURE.items()}, "E-MAX exposure moved")
    _require(payload["evidence_modes"]["LIVE"]["evidence_class"] == EVIDENCE_CLASS[LIVE]
             and payload["evidence_modes"]["RECONSTRUCTED"]["evidence_class"] == EVIDENCE_CLASS[RECONSTRUCTED],
             "evidence classes moved")
    return MappingProxyType(payload)
