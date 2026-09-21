"""E-MAX-M0: load the frozen aggressive-research contract and refuse anything outside its budget.

Nothing here ranks, replays or prices a trade. The validator proves that the contract is the
frozen file, that it points at the frozen E-Base artifacts, and that every stage stays inside its
finite, declared variant list.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from app.backtest.strategy_e1_forward.seal import H5_INPUTS
from app.strategy_e.signal import TRADING_RULES_CANONICAL_SHA256
from app.strategy_e_v1_1 import decision, replay_protocol

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m0_rules_v1.json"
RULES_CANONICAL_SHA256 = "3ae07d755501e82c2b7ff2bd765d93f4353aa9319b34a3e6f59d0aad098082ac"
BASE_RESULT_PATH = (REPO_ROOT
                    / "docs/backtest/strategy_e_candidate/strategy_e_v1_1_replay_result.json")
BASE_RESULT_SHA256 = "2d50cca5cfe2ee4b284eb097c87afd2a51fd4b20c11dfa86e95752fa8a94b593"
BUDGET = {"ranking": 3, "capacity": 2, "breadth": 2, "exit": 2, "exposure": 3}
MDD_CEILING = -0.35
PRIMARY_COST = "COST_10BP"
ROADMAP = ("E-MAX-M0", "E-MAX-M1", "E-MAX-M2", "E-MAX-M3", "E-MAX-M4", "E-MAX-M5", "E-MAX-M6",
           "FORWARD")
ALLOWED_CAPACITIES = (3, 5)
ALLOWED_MULTIPLIERS = (1.0, 1.5, 2.0)


class MaxContractError(ValueError):
    """The E-MAX contract drifted, broke provenance, or exceeded its finite budget."""


def canonical_sha256(payload: object) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MaxContractError(message)


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = canonical_sha256(payload)
    _require(found == RULES_CANONICAL_SHA256, f"E-MAX M0 digest {found} != frozen")
    _require(payload["declaration"]["namespace"] == "STRATEGY_E_MAX", "E-MAX namespace moved")

    base = payload["base_frozen"]
    _require(TRADING_RULES_CANONICAL_SHA256 in base["alpha"], "Base Alpha provenance broken")
    _require(decision.RULES_CANONICAL_SHA256 in base["trading"], "Trading V1.1 provenance broken")
    _require(replay_protocol.RULES_CANONICAL_SHA256 in base["replay_protocol"],
             "E-R2 provenance broken")
    result_sha = hashlib.sha256(BASE_RESULT_PATH.read_bytes()).hexdigest()
    _require(result_sha == BASE_RESULT_SHA256 and BASE_RESULT_SHA256 in base["base_result"],
             "E-R3 Base result changed")
    decision.load_rules()
    replay_protocol.load_rules()

    _require(tuple(payload["feature_pool"]["allowed"]) == H5_INPUTS, "feature pool is not H5's")
    _require(payload["feature_pool"]["gap_alone_ranking"]["included"] is False,
             "gap-alone ranking is excluded in V1")

    budget = payload["research_budget"]
    _require({k: budget[k] for k in BUDGET} == BUDGET, "research budget moved")
    _require(budget["grid_search_forbidden"] is True and budget["cartesian_combination_forbidden"] is True,
             "grid or Cartesian search must stay forbidden")
    counts = {
        "ranking": len(payload["ranking"]["hypotheses"]),
        "capacity": len(payload["capacity"]["hypotheses"]),
        "breadth": len(payload["breadth"]["hypotheses"]),
        "exit": len(payload["exit"]["hypotheses"]),
        "exposure": len(payload["exposure"]["hypotheses"]),
    }
    for axis, count in counts.items():
        _require(count <= BUDGET[axis], f"{axis} declares {count} > budget {BUDGET[axis]}")
    ranking_features = {"premarket_rvol", "return_0900_0925", "position_in_premarket_range"}
    for hypothesis in payload["ranking"]["hypotheses"]:
        named = {f for f in H5_INPUTS if f in hypothesis["rule"]}
        _require(named and named <= ranking_features, f"{hypothesis['id']} reads outside the pool")
    _require(tuple(h["max_selected"] for h in payload["capacity"]["hypotheses"]) == ALLOWED_CAPACITIES,
             "capacity must be exactly 3 and 5")
    _require(tuple(h["multiplier"] for h in payload["exposure"]["hypotheses"]) == ALLOWED_MULTIPLIERS,
             "exposure must be exactly 1.0, 1.5, 2.0")
    _require(payload["exit"]["horizon_scan_forbidden"] is True, "exit horizon scan must stay forbidden")

    envelope = payload["risk_envelope"]
    _require(envelope["mdd_hard_ceiling"] == MDD_CEILING, "MDD ceiling moved")
    _require(envelope["primary_cost"] == PRIMARY_COST, "primary cost moved")
    _require("forbidden" in envelope["symbol_exclusion"] and "ABSI" in envelope["symbol_exclusion"],
             "symbol exclusion must stay forbidden")
    _require(tuple(payload["stages"]["order"]) == ROADMAP, "roadmap moved")
    return MappingProxyType(payload)


def mdd_eligible(maximum_drawdown: float) -> bool:
    """The development risk envelope: -35% is eligible, anything deeper is not."""
    return maximum_drawdown >= MDD_CEILING
