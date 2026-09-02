"""Runtime safety domain values; audit time and market time stay distinct."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RuntimeMode(StrEnum):
    NORMAL = "NORMAL"
    SAFE_MODE = "SAFE_MODE"
    HALTED = "HALTED"


class FailureSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class FailureCode(StrEnum):
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
    MARKET_DATA_INVALID = "MARKET_DATA_INVALID"
    EXECUTION_UNAVAILABLE = "EXECUTION_UNAVAILABLE"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    ORDER_MISMATCH = "ORDER_MISMATCH"
    STRATEGY_STATE_MISMATCH = "STRATEGY_STATE_MISMATCH"
    DATABASE_ERROR = "DATABASE_ERROR"
    RUNTIME_INVARIANT_VIOLATION = "RUNTIME_INVARIANT_VIOLATION"
    HEARTBEAT_MISSED = "HEARTBEAT_MISSED"
    STARTUP_RECONCILIATION_FAILED = "STARTUP_RECONCILIATION_FAILED"
    MANUAL_SAFE_MODE = "MANUAL_SAFE_MODE"
    MANUAL_HALT = "MANUAL_HALT"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"


@dataclass(frozen=True)
class FailureEvent:
    failure_code: FailureCode
    severity: FailureSeverity
    component: str
    message: str
    occurred_at: datetime
    market_as_of: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    resolved: bool = False
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class RuntimeHealthState:
    mode: RuntimeMode
    healthy: bool
    last_heartbeat_at: datetime | None
    last_market_data_at: datetime | None
    last_execution_at: datetime | None
    active_failure_count: int
    last_failure_code: FailureCode | None
    last_failure_at: datetime | None
    updated_at: datetime


class MismatchType(StrEnum):
    INTERNAL_POSITION_WITHOUT_BROKER = "INTERNAL_POSITION_WITHOUT_BROKER"
    BROKER_POSITION_WITHOUT_INTERNAL = "BROKER_POSITION_WITHOUT_INTERNAL"
    TERMINAL_STATE_WITH_OPEN_POSITION = "TERMINAL_STATE_WITH_OPEN_POSITION"
    NONTERMINAL_STATE_WITHOUT_POSITION = "NONTERMINAL_STATE_WITHOUT_POSITION"
    OPEN_ORDER_STATE_MISMATCH = "OPEN_ORDER_STATE_MISMATCH"


@dataclass(frozen=True)
class ReconciliationMismatch:
    mismatch_type: MismatchType
    symbol: str
    details: str


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    mismatches: tuple[ReconciliationMismatch, ...]
    checked_at: datetime


@dataclass(frozen=True)
class FreshnessResult:
    healthy: bool
    stale_seconds: float
    expected_activity: bool
    failure_code: FailureCode | None = None


@dataclass(frozen=True)
class RuntimeStatusSnapshot:
    runtime_mode: RuntimeMode
    healthy: bool
    last_heartbeat: datetime | None
    market_data_status: str
    execution_status: str
    unresolved_failures: int
    open_positions_count: int
    open_orders_count: int
    last_reconciliation: datetime | None
    last_failure: FailureEvent | None
