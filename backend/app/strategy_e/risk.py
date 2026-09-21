"""E-D5 deterministic normalized Strategy E sizing.

Weights are exact backtest normalization units, not account allocation, order quantity, leverage,
or Common Risk V1 stop-based sizing. The module computes no return or PnL.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.risk.config import RiskConfig
from app.strategy_e.costs import COST_RULES_CANONICAL_SHA256
from app.strategy_e.execution import (
    EXECUTION_RULES_CANONICAL_SHA256,
    EXECUTION_VERSION,
    NOT_SELECTED_CAPACITY,
)
from app.strategy_e.exits import (
    EXIT_RULES_CANONICAL_SHA256,
    EXIT_VERSION,
    HORIZON_SEMANTICS_CANONICAL_SHA256,
    VALID_EXIT,
    ExitBatch,
    ExitRecord,
)
from app.strategy_e.signal import SIGNAL_VERSION, STRATEGY_ID, TRADING_RULES_CANONICAL_SHA256


REPO_ROOT = Path(__file__).resolve().parents[3]
RISK_RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_risk_rules_v1.json"
RISK_RULES_CANONICAL_SHA256 = (
    "2e1e796d7e3563da4b1fd4d4a007314b49d3c3ac9a55e63519076b8ac8d104ee"
)
RISK_VERSION = "STRATEGY_E_RISK_V1"
SIZING_MODEL = "EQUAL_WEIGHT_EXECUTABLE_V1"
MAX_POSITIONS = 3

SIZED = "SIZED"
NOT_SELECTED = "NOT_SELECTED"
NOT_EXECUTABLE = "NOT_EXECUTABLE"
UNRESOLVED_EXIT = "UNRESOLVED_EXIT"
INVALID_FOR_STANDARD_PNL = "INVALID_FOR_STANDARD_PNL"


class RiskContractError(ValueError):
    """Fail-closed rejection of invalid E-D5 rules or upstream identity."""


@dataclass(frozen=True)
class SizingRecord:
    strategy_id: str
    risk_version: str
    session_date: str
    symbol: str
    selected: bool
    entry_executable: bool
    exit_valid: bool
    executable: bool
    normalized_weight: Fraction
    risk_status: str
    skip_reason: str | None
    signal_digest: str
    execution_digest: str
    exit_digest: str
    risk_rules_digest: str


@dataclass(frozen=True)
class SizingBatch:
    records: tuple[SizingRecord, ...]
    normalized_daily_gross_exposure: Fraction
    sizing_digest: str


def _json_default(value: object) -> str:
    if isinstance(value, Fraction):
        return f"{value.numerator}/{value.denominator}"
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=_json_default).encode("utf-8")


def _load_rules(path: Path = RISK_RULES_PATH) -> Mapping:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RiskContractError(f"cannot load frozen Strategy E risk rules: {error}") from error
    found = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if found != RISK_RULES_CANONICAL_SHA256:
        raise RiskContractError(
            f"Strategy E risk rules digest mismatch: {found} != {RISK_RULES_CANONICAL_SHA256}"
        )
    declaration = payload.get("declaration", {})
    if (declaration.get("contract_id") != "STRATEGY_E_RISK_RULES_V1"
            or declaration.get("version") != RISK_VERSION):
        raise RiskContractError("unsupported Strategy E risk rules version")
    risk = RiskConfig()
    audit = payload.get("common_risk_audit", {})
    selection = payload.get("selection", {})
    sizing = payload.get("sizing", {})
    if (risk.version != payload.get("upstream", {}).get("common_risk_version")
            or risk.max_new_symbols_per_day != MAX_POSITIONS
            or risk.max_open_positions != MAX_POSITIONS
            or audit.get("max_new_symbols_per_day") != risk.max_new_symbols_per_day
            or audit.get("max_open_positions") != risk.max_open_positions
            or selection.get("max_selected_candidates") != MAX_POSITIONS
            or selection.get("replacement_policy") != "NONE"
            or sizing.get("model") != SIZING_MODEL
            or sizing.get("normalized_daily_gross_notional") != 1.0):
        raise RiskContractError("Strategy E sizing conflicts with frozen Common Risk V1")
    upstream = payload.get("upstream", {})
    expected = {
        "e_d0_rules_canonical_sha256": TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": EXECUTION_RULES_CANONICAL_SHA256,
        "e_d2_execution_version": EXECUTION_VERSION,
        "e_d3_horizon_semantics_canonical_sha256": HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": EXIT_RULES_CANONICAL_SHA256,
        "e_d3_exit_version": EXIT_VERSION,
        "e_d4_cost_rules_canonical_sha256": COST_RULES_CANONICAL_SHA256,
        "common_risk_version": risk.version,
    }
    if any(upstream.get(key) != value for key, value in expected.items()):
        raise RiskContractError("Strategy E risk provenance chain does not match frozen upstream")
    return MappingProxyType(payload)


def _validate_record(record: ExitRecord) -> None:
    if (record.strategy_id != STRATEGY_ID or record.signal_version != SIGNAL_VERSION
            or record.execution_version != EXECUTION_VERSION or record.exit_version != EXIT_VERSION):
        raise RiskContractError("unsupported Strategy E exit identity")
    if (record.e_d0_rules_digest != TRADING_RULES_CANONICAL_SHA256
            or record.e_d2_execution_rules_digest != EXECUTION_RULES_CANONICAL_SHA256
            or record.e_d3_horizon_semantics_digest != HORIZON_SEMANTICS_CANONICAL_SHA256
            or record.e_d3_exit_rules_digest != EXIT_RULES_CANONICAL_SHA256):
        raise RiskContractError("exit does not reference the frozen Strategy E chain")


def _classification(record: ExitRecord) -> tuple[bool, bool, bool, str, str | None]:
    selected = record.entry_status != NOT_SELECTED_CAPACITY
    entry_executable = record.entry_status == "EXECUTED_PROXY"
    executable = selected and entry_executable and record.exit_valid and record.exit_status == VALID_EXIT
    if not selected:
        return selected, entry_executable, executable, NOT_SELECTED, NOT_SELECTED_CAPACITY
    if not entry_executable:
        return selected, entry_executable, executable, NOT_EXECUTABLE, record.entry_status
    if not executable:
        return selected, entry_executable, executable, UNRESOLVED_EXIT, INVALID_FOR_STANDARD_PNL
    return selected, entry_executable, executable, SIZED, None


def build_sizing_records(exits: ExitBatch, *,
                         rules_path: Path = RISK_RULES_PATH) -> SizingBatch:
    """Assign exact equal weights to the frozen selected set's standard-PnL-eligible records."""
    _load_rules(rules_path)
    if not exits.exit_digest:
        raise RiskContractError("E-D3 exit digest is required")
    ordered = tuple(sorted(exits.records, key=lambda record: record.symbol))
    if len({record.symbol for record in ordered}) != len(ordered):
        raise RiskContractError("duplicate Strategy E symbol identity")
    for record in ordered:
        _validate_record(record)
    classified = tuple((record, _classification(record)) for record in ordered)
    selected_count = sum(int(values[0]) for _, values in classified)
    if selected_count > MAX_POSITIONS:
        raise RiskContractError("selected Strategy E positions exceed frozen maximum of three")
    executable_count = sum(int(values[2]) for _, values in classified)
    weight = Fraction(1, executable_count) if executable_count else Fraction(0, 1)
    records = tuple(
        SizingRecord(
            strategy_id=STRATEGY_ID,
            risk_version=RISK_VERSION,
            session_date=record.session_date.isoformat(),
            symbol=record.symbol,
            selected=values[0],
            entry_executable=values[1],
            exit_valid=record.exit_valid,
            executable=values[2],
            normalized_weight=weight if values[2] else Fraction(0, 1),
            risk_status=values[3],
            skip_reason=values[4],
            signal_digest=record.signal_digest,
            execution_digest=record.execution_digest,
            exit_digest=exits.exit_digest,
            risk_rules_digest=RISK_RULES_CANONICAL_SHA256,
        )
        for record, values in classified
    )
    exposure = sum((record.normalized_weight for record in records), Fraction(0, 1))
    payload = {
        "exit_digest": exits.exit_digest,
        "records": [asdict(record) for record in records],
        "risk_rules_digest": RISK_RULES_CANONICAL_SHA256,
        "risk_version": RISK_VERSION,
        "sizing_model": SIZING_MODEL,
        "normalized_daily_gross_exposure": exposure,
    }
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return SizingBatch(records, exposure, digest)
