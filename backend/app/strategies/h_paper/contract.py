"""The frozen H-V2 paper execution contract, verified by checksum on every load.

The forward shadow contract (``app.strategies.h_forward.contract``) stays frozen and keeps saying
that the forward shadow itself holds no position. This second contract
(``docs/operations/h_paper_execution_v1.json``) is the separate, later decision that gives H a paper
book: sizing, entry, exits and the launch clock. Like every other frozen contract in this repository
it holds its rules in the JSON, and a JSON that no longer matches its stored sha256 loads nothing.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "docs/operations/h_paper_execution_v1.json"
CHECKSUM = REPO_ROOT / "docs/operations/h_paper_execution_v1.sha256"

LAUNCH_RECORD = "H_PAPER_V1_LAUNCH"
SHADOW_ONLY, SHADOW_PLUS_PAPER = "SHADOW_ONLY", "SHADOW_PLUS_PAPER"


class PaperContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != CHECKSUM.read_text(encoding="utf-8").strip():
        raise PaperContractError("H paper execution contract does not match its frozen checksum")
    return body


def contract_id() -> str:
    return contract()["declaration"]["contract_id"]


def sha256() -> str:
    return CHECKSUM.read_text(encoding="utf-8").strip()


def initial_equity() -> Decimal:
    return Decimal(contract()["account"]["initial_equity"])


def max_open_positions() -> int:
    return int(contract()["sizing"]["max_open_positions"])


def slot_fraction() -> Decimal:
    numerator, denominator = contract()["sizing"]["slot_fraction"].split("/")
    return Decimal(numerator) / Decimal(denominator)


def sizing_contract() -> str:
    return contract()["sizing"]["contract"]


def required_d5_contract() -> str:
    return contract()["signals"]["d5_contract_required"]


def entry_decision() -> str:
    return contract()["signals"]["entry_signal_decision"]


def state() -> dict[str, Any]:
    body = contract()
    return {"contract_id": contract_id(), "contract_sha256": sha256(),
            "sizing_contract": sizing_contract(), "max_open_positions": max_open_positions(),
            "slot_fraction": body["sizing"]["slot_fraction"],
            "initial_equity": body["account"]["initial_equity"], "leverage": body["account"]["leverage"],
            "short_selling": body["account"]["short_selling"],
            "cost_config": "execution_v0", "accounting": "ACCOUNTING_V1",
            "entry_rule": body["execution"]["entry_price"],
            "exit_reasons": ["TP1", "TP2", "DECISION_DOWNGRADE_WATCH", "DECISION_DOWNGRADE_REJECT",
                             "THESIS_INVALIDATED", "BEAR_BREACH"],
            "production_daily_research_budget_usd": body["research_budget"]["production_daily_budget_usd"]}
