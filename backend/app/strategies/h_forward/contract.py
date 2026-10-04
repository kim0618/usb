"""The frozen H-V2-D7 forward shadow contract, verified by checksum on every load.

This module holds no threshold and no rule of its own: every answer it gives is read out of
``docs/operations/h_forward_shadow_v1.json``, whose canonical serialisation must match the stored
sha256 or nothing loads. That is the same arrangement the A/E official clock and the A/E paper gate
use (``app.strategies.official``, ``app.strategies.paper_gate``), for the same reason - a forward
observation whose rules can drift while it runs is not a forward observation.

Two refusals live here because they are the ones that would quietly invalidate the whole step:

* a launch under any D5 window contract other than ``D5_D2R_V1`` is refused, because D5-D2R is the
  repair that made VRRM's valuation honest and a shadow launched on the superseded rule would be
  observing a number the repository has already shown to be wrong;
* opening a paper position without a frozen sizing contract is refused, because neither A's
  risk-based sizing nor E's equal-weight sizing can be applied to H without inventing an entry
  level, a stop or an exit, and D6 produces none of the three.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "docs/operations/h_forward_shadow_v1.json"
CHECKSUM = REPO_ROOT / "docs/operations/h_forward_shadow_v1.sha256"

#: The registry id. H's ledgers, snapshots and runtime files are keyed by this and are never renamed.
STRATEGY_H = "STRATEGY_H_V2"

APPROVE, WATCH, REJECT = "APPROVE", "WATCH", "REJECT"
NOT_DECISION_ELIGIBLE = "NOT_DECISION_ELIGIBLE"
DECISION_STATES = (APPROVE, WATCH, REJECT)

INITIAL_COHORT, NEW_CANDIDATE = "INITIAL_D7_COHORT", "NEW_FORWARD_CANDIDATE"

PENDING = "PENDING"
NOT_AVAILABLE = "NOT_AVAILABLE"
NEGATIVE_IMPLIED_EQUITY = "NEGATIVE_IMPLIED_EQUITY"

RUNNING, INCONCLUSIVE = "RUNNING", "INCONCLUSIVE"
SIZING_CONTRACT_REQUIRED = "SIZING_CONTRACT_REQUIRED"


class ForwardContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != CHECKSUM.read_text(encoding="utf-8").strip():
        raise ForwardContractError("H forward shadow contract does not match its frozen checksum")
    return body


def contract_id() -> str:
    return contract()["declaration"]["contract_id"]


def required_d5_contract() -> str:
    return contract()["upstream"]["d5_contract_required"]


def decision_session() -> str:
    return contract()["upstream"]["decision_session"]


def horizons() -> tuple[int, ...]:
    return tuple(int(h) for h in contract()["outcomes"]["horizons_sessions"])


def benchmark() -> str:
    return contract()["outcomes"]["benchmark"]


def allowed_transitions() -> frozenset[tuple[str, str]]:
    pairs = (text.split("->") for text in contract()["transitions"]["allowed"])
    return frozenset((a, b) for a, b in pairs)


def material_events() -> tuple[str, ...]:
    return tuple(contract()["refresh"]["material_events"])


def creates_position(decision: str) -> bool:
    """Whether a decision may become a paper position. False for all three, by contract."""
    rule = contract()["position_rule"]
    return bool(rule.get(f"{decision.lower()}_creates_position", False))


def creates_entry_candidate(decision: str) -> bool:
    return decision == APPROVE and bool(contract()["position_rule"]["approve_creates_entry_candidate"])


def sizing_contract() -> str:
    return contract()["position_rule"]["sizing_contract"]


def sizing_is_defined() -> bool:
    return sizing_contract() != "NOT_DEFINED"


def require_d5_contract(window_contract: str) -> None:
    """Raise unless the valuation being launched was produced under the required contract."""
    expected = required_d5_contract()
    if window_contract != expected:
        raise ForwardContractError(
            f"H forward shadow requires D5 contract {expected}, refusing {window_contract!r}: "
            f"{contract()['upstream']['refusal_reason']}")


def state() -> dict[str, Any]:
    body = contract()
    return {
        "contract_id": contract_id(),
        "contract_sha256": CHECKSUM.read_text(encoding="utf-8").strip(),
        "step": body["declaration"]["step"],
        "research_build": body["declaration"]["research_build"],
        "forward_observation": body["declaration"]["forward_observation"],
        "d5_contract": required_d5_contract(),
        "d6_contract": body["upstream"]["d6_contract"],
        "decision_session": decision_session(),
        "horizons": list(horizons()),
        "benchmark": benchmark(),
        "sizing_contract": sizing_contract(),
        "sample_needed": body["evaluation"]["sample_needed"],
        "preregistered_forward_questions": list(body["evaluation"]["preregistered_forward_questions"]),
    }
