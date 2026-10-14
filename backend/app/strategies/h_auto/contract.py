"""The frozen H-V2 autonomous pipeline contract, verified by checksum on every load.

Same arrangement as the forward shadow and paper contracts: every priority, retry, intake and stage
rule the autonomous pipeline enforces is read out of ``docs/operations/h_autonomous_pipeline_v2.json``
(``H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI``) and a JSON that no longer matches its stored sha256 loads
nothing. V2 has no model, budget or authorization section: production evaluation is code only. V1
(``h_autonomous_pipeline_v1.json``) stays in the repository unchanged as history. A checksum mismatch is one of the
three hard failures of the daily job (the others are forward-ledger integrity and paper accounting).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "docs/operations/h_autonomous_pipeline_v2.json"
CHECKSUM = REPO_ROOT / "docs/operations/h_autonomous_pipeline_v2.sha256"

D5_CONTRACT = "D5_D2R_V1"


class AutonomyContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != CHECKSUM.read_text(encoding="utf-8").strip():
        raise AutonomyContractError("H autonomous pipeline contract does not match its frozen checksum")
    if body["stages"]["d5_contract"] != D5_CONTRACT:
        raise AutonomyContractError("H autonomous pipeline must run under D5_D2R_V1")
    if body.get("production_ai_calls", body["declaration"].get("production_ai_calls")) != 0 or "budget" in body \
            or body.get("model", {}).get("production_model") != "NONE":
        raise AutonomyContractError("H production evaluation must declare no AI model and no AI budget")
    return body


def contract_id() -> str:
    return contract()["declaration"]["contract_id"]


def sha256() -> str:
    return CHECKSUM.read_text(encoding="utf-8").strip()


def max_queue_attempts() -> int:
    return int(contract()["retry"]["max_queue_attempts"])


def infra_transient() -> frozenset[str]:
    return frozenset(contract()["retry"]["infra_transient"])


def reference_max_age_days() -> int:
    return int(contract()["new_candidate_intake"]["freshness_gate"]["reference_snapshot_max_age_days"])


def intake_fetch_cap() -> int:
    """SEC CIKs the intake scan may (re)fetch per run (submissions + companyfacts each)."""
    return int(contract()["new_candidate_intake"]["sec_on_demand"]["fetch_cap_per_run"])


def state() -> dict[str, Any]:
    body = contract()
    return {"contract_id": contract_id(), "contract_sha256": sha256(),
            "production_model": body["model"]["production_model"], "production_ai_calls": 0,
            "ai_runtime_cost_usd": 0.0, "d5_contract": D5_CONTRACT,
            "d3_generator": "H_D3_CODE_OWNED_V1", "d4_generator": "H_D4_CODE_OWNED_V1",
            "max_queue_attempts": max_queue_attempts()}
