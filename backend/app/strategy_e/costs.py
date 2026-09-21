"""E-D4 deterministic total round-trip execution-friction stress primitive.

This module applies pre-registered hypothetical costs to one valid E-D3 record. It is not a
historical backtest, fill simulator, broker-fee schedule, or observed slippage estimator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.strategy_e.execution import EXECUTION_RULES_CANONICAL_SHA256, EXECUTION_VERSION
from app.strategy_e.exits import (
    EXIT_RULES_CANONICAL_SHA256,
    EXIT_VERSION,
    HORIZON_SEMANTICS_CANONICAL_SHA256,
    VALID_EXIT,
    ExitRecord,
)
from app.strategy_e.signal import SIGNAL_VERSION, STRATEGY_ID, TRADING_RULES_CANONICAL_SHA256


REPO_ROOT = Path(__file__).resolve().parents[3]
COST_RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_cost_rules_v1.json"
COST_RULES_CANONICAL_SHA256 = (
    "b2229fe5308df6b2b18f4eae8dfedf5d08b4af796ae24aab5ad7861ed3ec454c"
)
COST_VERSION = "STRATEGY_E_COST_V1"
COST_MODEL = "TOTAL_ROUND_TRIP_SUBTRACTION_V1"
BPS_DENOMINATOR = Decimal("10000")


class CostScenario(StrEnum):
    COST_05BP = "COST_05BP"
    COST_10BP = "COST_10BP"
    COST_15BP = "COST_15BP"
    COST_20BP = "COST_20BP"


SCENARIO_BPS = MappingProxyType({
    CostScenario.COST_05BP: Decimal("5"),
    CostScenario.COST_10BP: Decimal("10"),
    CostScenario.COST_15BP: Decimal("15"),
    CostScenario.COST_20BP: Decimal("20"),
})


class CostContractError(ValueError):
    """Fail-closed rejection of invalid cost rules, identity, or scenario."""


@dataclass(frozen=True)
class CostRecord:
    strategy_id: str
    session_date: str
    symbol: str
    signal_digest: str
    execution_digest: str
    exit_digest: str
    entry_price: Decimal
    exit_price: Decimal
    gross_return: Decimal
    cost_model: str
    cost_scenario: str
    round_trip_cost_bp: Decimal
    round_trip_cost_decimal: Decimal
    net_return: Decimal
    e_d0_rules_digest: str
    e_d2_execution_rules_digest: str
    e_d3_exit_rules_digest: str
    e_d4_cost_rules_digest: str
    cost_digest: str


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=_json_default).encode("utf-8")


def _load_rules(path: Path = COST_RULES_PATH) -> Mapping:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CostContractError(f"cannot load frozen Strategy E cost rules: {error}") from error
    found = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if found != COST_RULES_CANONICAL_SHA256:
        raise CostContractError(
            f"Strategy E cost rules digest mismatch: {found} != {COST_RULES_CANONICAL_SHA256}"
        )
    declaration = payload.get("declaration", {})
    if (declaration.get("contract_id") != "STRATEGY_E_COST_RULES_V1"
            or declaration.get("version") != COST_VERSION):
        raise CostContractError("unsupported Strategy E cost rules version")
    model = payload.get("cost_model", {})
    expected_grid = {scenario.value: int(bp) for scenario, bp in SCENARIO_BPS.items()}
    if (model.get("id") != COST_MODEL
            or model.get("stress_scenarios_bp") != expected_grid
            or model.get("price_dependent_cost") is not False
            or model.get("liquidity_dependent_cost") is not False):
        raise CostContractError("Strategy E cost model differs from the frozen E-D4 contract")
    upstream = payload.get("upstream", {})
    expected = {
        "e_d0_rules_canonical_sha256": TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": EXECUTION_RULES_CANONICAL_SHA256,
        "e_d2_execution_version": EXECUTION_VERSION,
        "e_d3_horizon_semantics_canonical_sha256": HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": EXIT_RULES_CANONICAL_SHA256,
        "e_d3_exit_version": EXIT_VERSION,
    }
    if any(upstream.get(key) != value for key, value in expected.items()):
        raise CostContractError("Strategy E cost provenance chain does not match frozen upstream")
    return MappingProxyType(payload)


def _positive_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise CostContractError(f"{name} must be finite and positive")
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise CostContractError(f"{name} must be finite and positive") from error
    if not converted.is_finite() or converted <= 0:
        raise CostContractError(f"{name} must be finite and positive")
    return converted


def _validate_exit(record: ExitRecord) -> tuple[Decimal, Decimal]:
    if (record.strategy_id != STRATEGY_ID or record.signal_version != SIGNAL_VERSION
            or record.execution_version != EXECUTION_VERSION or record.exit_version != EXIT_VERSION):
        raise CostContractError("unsupported Strategy E exit identity")
    if (record.e_d0_rules_digest != TRADING_RULES_CANONICAL_SHA256
            or record.e_d2_execution_rules_digest != EXECUTION_RULES_CANONICAL_SHA256
            or record.e_d3_horizon_semantics_digest != HORIZON_SEMANTICS_CANONICAL_SHA256
            or record.e_d3_exit_rules_digest != EXIT_RULES_CANONICAL_SHA256):
        raise CostContractError("exit does not reference the frozen Strategy E chain")
    if not record.exit_valid or record.exit_status != VALID_EXIT:
        raise CostContractError("invalid execution cannot produce a cost-adjusted result")
    return (_positive_decimal(record.entry_price, "entry_price"),
            _positive_decimal(record.exit_price, "exit_price"))


def apply_cost(record: ExitRecord, exit_digest: str, scenario: CostScenario | str, *,
               rules_path: Path = COST_RULES_PATH) -> CostRecord:
    """Apply one frozen total round-trip stress scenario to one valid synthetic trade."""
    _load_rules(rules_path)
    if not exit_digest:
        raise CostContractError("E-D3 exit digest is required")
    try:
        selected = CostScenario(scenario)
    except ValueError as error:
        raise CostContractError(f"unsupported Strategy E cost scenario: {scenario}") from error
    entry, exit_ = _validate_exit(record)
    bp = SCENARIO_BPS[selected]
    cost = bp / BPS_DENOMINATOR
    gross = exit_ / entry - Decimal("1")
    net = gross - cost
    identity = {
        "cost_model": COST_MODEL,
        "cost_rules_digest": COST_RULES_CANONICAL_SHA256,
        "cost_scenario": selected.value,
        "entry_price": entry,
        "exit_digest": exit_digest,
        "exit_price": exit_,
        "gross_return": gross,
        "net_return": net,
        "round_trip_cost_bp": bp,
        "round_trip_cost_decimal": cost,
        "session_date": record.session_date.isoformat(),
        "symbol": record.symbol,
    }
    digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    return CostRecord(
        strategy_id=STRATEGY_ID, session_date=record.session_date.isoformat(), symbol=record.symbol,
        signal_digest=record.signal_digest, execution_digest=record.execution_digest,
        exit_digest=exit_digest, entry_price=entry, exit_price=exit_, gross_return=gross,
        cost_model=COST_MODEL, cost_scenario=selected.value, round_trip_cost_bp=bp,
        round_trip_cost_decimal=cost, net_return=net,
        e_d0_rules_digest=TRADING_RULES_CANONICAL_SHA256,
        e_d2_execution_rules_digest=EXECUTION_RULES_CANONICAL_SHA256,
        e_d3_exit_rules_digest=EXIT_RULES_CANONICAL_SHA256,
        e_d4_cost_rules_digest=COST_RULES_CANONICAL_SHA256, cost_digest=digest,
    )
