"""Strategy E's official paper cost: the frozen research cost contract, applied unchanged.

Sources, read and checksum-verified on every load (no value is typed here):

* ``docs/backtest/strategy_e_candidate/strategy_e_cost_rules_v1.json`` (E-D4,
  ``STRATEGY_E_COST_RULES_V1``, frozen 2026-09-21 before any E trading backtest):
  model ``TOTAL_ROUND_TRIP_SUBTRACTION_V1``, "one total deduction covering entry plus exit for the
  complete trade", ``leg_allocation`` NONE, scenarios 5 / 10 / 15 / 20 bp, GROSS_0BP diagnostic only.
* ``docs/backtest/strategy_e_max/strategy_e_max_v1_rules.json`` (E-MAX V1, frozen 2026-09-22):
  ``definition.cost.primary`` = COST_10BP, "levered net = exposure x (gross - round-trip cost);
  cost is notional-proportional". The forward rules name COST_10BP "primary official evidence" and
  forbid a cost-model change inside V1.

So E's official paper cost is 10 bp of entry notional per round trip, charged once. The other
scenarios are not charged; they stay the recorded ``fixed_bp_views`` stress results.

Before 2026-09-27 the E paper runner charged A's ``execution_v0`` instead (25 bp per leg: spread 10
+ slippage 5 inside the fill price, commission 10 beside it), which is not E's contract. Rows written
that way stay in the legacy book, untouched.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.execution.config import RoundTripCostConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
COST_RULES = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_cost_rules_v1.json"
COST_RULES_SHA256 = "b2229fe5308df6b2b18f4eae8dfedf5d08b4af796ae24aab5ad7861ed3ec454c"
MAX_RULES = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_v1_rules.json"
MAX_RULES_SHA256 = "b30a3e95d6333c17d736c4c4fa23f55a7f67ba4af328317379a86a4a37e89d0e"

ACCOUNTING_VERSION = "V1"
EXECUTION_VERSION = "e_cost_v1_round_trip"


class CostContractError(RuntimeError):
    pass


def _canonical(path: Path, expected: str) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    if digest != expected:
        raise CostContractError(f"{path.name} does not match its frozen canonical sha256")
    return body


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    """The official scenario and its stress siblings, as the two frozen files state them."""
    cost_rules = _canonical(COST_RULES, COST_RULES_SHA256)
    max_rules = _canonical(MAX_RULES, MAX_RULES_SHA256)
    model = cost_rules["cost_model"]
    if model["id"] != "TOTAL_ROUND_TRIP_SUBTRACTION_V1" or not model["leg_allocation"].startswith("NONE"):
        raise CostContractError("E cost model is not the frozen total round-trip subtraction")
    primary = max_rules["definition"]["cost"]["primary"]
    scenarios = {name: Decimal(str(bp)) for name, bp in model["stress_scenarios_bp"].items()}
    if primary not in scenarios:
        raise CostContractError(f"primary scenario {primary} is not one of the frozen scenarios")
    return {
        "contract_id": cost_rules["declaration"]["contract_id"],
        "model": model["id"],
        "official_scenario": primary,
        "official_round_trip_bp": scenarios[primary],
        "stress_scenarios_bp": {k: v for k, v in scenarios.items() if k != primary},
        "diagnostic_scenario": "GROSS_0BP",
        "round_trip": True,
        "leg_allocation": model["leg_allocation"],
        "scaling": max_rules["definition"]["cost"]["scaling"],
        "version": f"{cost_rules['declaration']['version']}:{primary}",
    }


def official_execution() -> RoundTripCostConfig:
    """The SimBroker config for E's OFFICIAL paper book: raw-price fills, 10 bp once per trade."""
    body = contract()
    return RoundTripCostConfig(version=EXECUTION_VERSION, default_spread_bps=Decimal("0"),
                               default_slippage_bps=Decimal("0"), commission_bps=Decimal("0"),
                               fx_cost_bps=Decimal("0"), round_trip_cost_bps=body["official_round_trip_bp"],
                               cost_contract=body["version"])


def record() -> dict[str, str]:
    """What every V1 E trade and book carries about the cost it was charged."""
    body = contract()
    return {"accounting_version": ACCOUNTING_VERSION, "cost_contract_version": body["version"],
            "execution_cost_bp": str(body["official_round_trip_bp"]), "cost_basis": "ROUND_TRIP_ON_ENTRY_NOTIONAL"}
