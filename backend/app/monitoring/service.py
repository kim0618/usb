"""Transactional runtime health, failure, recovery, and status orchestration."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.monitoring.config import OperationsConfig
from app.monitoring.domain import (FailureCode, FailureEvent, FailureSeverity,
    ReconciliationResult, RuntimeHealthState, RuntimeMode, RuntimeStatusSnapshot)
from app.monitoring.notification import LoggingNotificationSink, NotificationSink
from app.monitoring.guards import ExecutionFailureTracker, MarketDataStalenessGuard
from app.repositories.runtime import RuntimeRepository


SAFE_MODE_CODES = {FailureCode.MARKET_DATA_STALE, FailureCode.MARKET_DATA_UNAVAILABLE,
    FailureCode.EXECUTION_UNAVAILABLE,
    FailureCode.POSITION_MISMATCH, FailureCode.ORDER_MISMATCH,
    FailureCode.STRATEGY_STATE_MISMATCH, FailureCode.DATABASE_ERROR,
    FailureCode.RUNTIME_INVARIANT_VIOLATION, FailureCode.STARTUP_RECONCILIATION_FAILED,
    FailureCode.MANUAL_SAFE_MODE}


class RuntimeHealthService:
    def __init__(self, session: Session, *, config: OperationsConfig | None = None,
                 notification_sink: NotificationSink | None = None) -> None:
        self.session = session
        self.config = config or OperationsConfig()
        self.repository = RuntimeRepository(session)
        self.notifications = notification_sink or LoggingNotificationSink()
        self.execution_failures = ExecutionFailureTracker(self.config)
        self.staleness_guard = MarketDataStalenessGuard(self.config)

    def bootstrap(self, now: datetime) -> RuntimeHealthState:
        row = self.repository.get_or_create(now)
        self.session.commit()
        return self.health(row.updated_at)

    def record_heartbeat(self, now: datetime) -> RuntimeHealthState:
        row = self.repository.get_or_create(now)
        row.last_heartbeat_at = now; row.updated_at = now
        self.session.commit()
        return self.health(now)

    def heartbeat_is_stale(self, now: datetime) -> bool:
        row = self.repository.get_or_create(now)
        return (row.last_heartbeat_at is None or
                now - row.last_heartbeat_at > timedelta(seconds=self.config.heartbeat_stale_seconds))

    def record_market_data(self, market_at: datetime, updated_at: datetime) -> None:
        row = self.repository.get_or_create(updated_at)
        row.last_market_data_at = market_at; row.updated_at = updated_at
        self.session.commit()

    def record_execution(self, execution_at: datetime, updated_at: datetime) -> None:
        row = self.repository.get_or_create(updated_at)
        row.last_execution_at = execution_at; row.updated_at = updated_at
        self.session.commit()

    def report_failure(self, *, code: FailureCode, severity: FailureSeverity,
                       component: str, message: str, occurred_at: datetime,
                       market_as_of: datetime | None = None, metadata: dict | None = None,
                       target_mode: RuntimeMode | None = None) -> FailureEvent:
        event = FailureEvent(code, severity, component, message, occurred_at,
                             market_as_of, metadata or {})
        try:
            row = self.repository.get_or_create(occurred_at)
            stored = self.repository.add_failure(event, occurred_at)
            mode = target_mode
            if mode is None and code in SAFE_MODE_CODES:
                mode = RuntimeMode.SAFE_MODE
            if severity is FailureSeverity.CRITICAL and mode is None:
                mode = RuntimeMode.SAFE_MODE
            if mode is not None and not (RuntimeMode(row.mode) is RuntimeMode.HALTED
                                         and mode is RuntimeMode.SAFE_MODE):
                self._set_mode(row, mode, occurred_at, code, message)
            self.session.commit()
            event = self.repository.event(stored)
        except Exception:
            self.session.rollback()
            raise
        if severity is FailureSeverity.CRITICAL or target_mode is not None or code in SAFE_MODE_CODES:
            self.notifications.notify(event)
        return event

    def enter_safe_mode(self, now: datetime, message: str = "manual operator request") -> FailureEvent:
        return self.report_failure(code=FailureCode.MANUAL_SAFE_MODE,
            severity=FailureSeverity.CRITICAL, component="operator", message=message,
            occurred_at=now, target_mode=RuntimeMode.SAFE_MODE)

    def check_market_data(self, *, market_as_of: datetime, latest_data_available_at: datetime | None,
                          session, expected_market_activity: bool | None = None):  # type: ignore[no-untyped-def]
        result = self.staleness_guard.check(market_as_of=market_as_of,
            latest_data_available_at=latest_data_available_at, session=session,
            expected_market_activity=expected_market_activity)
        if result.failure_code is not None:
            self.report_failure(code=result.failure_code, severity=FailureSeverity.CRITICAL,
                component="market_data", message=f"market data stale by {result.stale_seconds} seconds",
                occurred_at=market_as_of, market_as_of=market_as_of)
        elif latest_data_available_at is not None:
            self.record_market_data(latest_data_available_at, market_as_of)
        return result

    def record_execution_failure(self, *, code: FailureCode, occurred_at: datetime,
                                 message: str, infrastructure: bool = True) -> int:
        count = self.execution_failures.record(code, occurred_at, infrastructure=infrastructure)
        self.report_failure(code=code, severity=FailureSeverity.ERROR, component="execution",
            message=message, occurred_at=occurred_at,
            target_mode=(RuntimeMode.SAFE_MODE if self.execution_failures.threshold_reached(occurred_at)
                         else None))
        return count

    def report_invariant_violations(self, errors: tuple[str, ...], occurred_at: datetime) -> None:
        if errors:
            self.report_failure(code=FailureCode.RUNTIME_INVARIANT_VIOLATION,
                severity=FailureSeverity.CRITICAL, component="runtime",
                message="; ".join(errors), occurred_at=occurred_at)

    def halt(self, now: datetime, *, code: FailureCode = FailureCode.MANUAL_HALT,
             message: str = "manual halt") -> FailureEvent:
        return self.report_failure(code=code, severity=FailureSeverity.CRITICAL,
            component="operator", message=message, occurred_at=now,
            target_mode=RuntimeMode.HALTED)

    def apply_reconciliation(self, result: ReconciliationResult, *, startup: bool = False) -> None:
        row = self.repository.get_or_create(result.checked_at)
        if result.matched:
            row.last_reconciliation_at = result.checked_at; row.updated_at = result.checked_at
            self.session.commit()
            return
        self.session.rollback()
        code = FailureCode.STARTUP_RECONCILIATION_FAILED if startup else FailureCode.POSITION_MISMATCH
        self.report_failure(code=code, severity=FailureSeverity.CRITICAL,
            component="reconciliation", message="; ".join(m.details for m in result.mismatches),
            occurred_at=result.checked_at, metadata={"mismatches": [m.mismatch_type.value for m in result.mismatches]})

    def recover_to_normal(self, *, acknowledged: bool, reconciliation: ReconciliationResult,
                          recovered_at: datetime) -> RuntimeHealthState:
        row = self.repository.get_or_create(recovered_at)
        if RuntimeMode(row.mode) is RuntimeMode.HALTED:
            raise ValueError("HALTED requires a separate operator release")
        if self.config.recovery_requires_manual_ack and not acknowledged:
            raise ValueError("manual acknowledgement is required")
        if not reconciliation.matched:
            raise ValueError("reconciliation must pass")
        if self.repository.unresolved_critical_count():
            raise ValueError("unresolved critical failures block recovery")
        self._set_mode(row, RuntimeMode.NORMAL, recovered_at, None, "manual recovery")
        row.last_reconciliation_at = reconciliation.checked_at
        self.session.commit()
        return self.health(recovered_at)

    def release_halt(self, *, acknowledged: bool, reconciliation: ReconciliationResult,
                     released_at: datetime) -> RuntimeHealthState:
        if not acknowledged:
            raise ValueError("explicit operator acknowledgement is required")
        row = self.repository.get_or_create(released_at)
        if RuntimeMode(row.mode) is not RuntimeMode.HALTED:
            raise ValueError("runtime is not HALTED")
        if not reconciliation.matched or self.repository.unresolved_critical_count():
            raise ValueError("resolved failures and reconciliation are required")
        self._set_mode(row, RuntimeMode.NORMAL, released_at, None, "operator halt release")
        self.session.commit()
        return self.health(released_at)

    def resolve_failure(self, failure_id: int, resolved_at: datetime) -> FailureEvent:
        row = self.repository.resolve(failure_id, resolved_at)
        self.session.commit()
        return self.repository.event(row)

    def health(self, now: datetime) -> RuntimeHealthState:
        row = self.repository.get_or_create(now)
        failures = self.repository.unresolved()
        last = self.repository.last_failure()
        mode = RuntimeMode(row.mode)
        return RuntimeHealthState(mode, mode is RuntimeMode.NORMAL and not failures,
            row.last_heartbeat_at, row.last_market_data_at, row.last_execution_at,
            len(failures), None if last is None else FailureCode(last.failure_code),
            None if last is None else last.occurred_at, row.updated_at)

    def status(self, now: datetime, *, open_positions_count: int, open_orders_count: int) -> RuntimeStatusSnapshot:
        row = self.repository.get_or_create(now); health = self.health(now)
        last = self.repository.last_failure()
        return RuntimeStatusSnapshot(health.mode, health.healthy, health.last_heartbeat_at,
            "UNKNOWN" if row.last_market_data_at is None else "RECORDED",
            "UNKNOWN" if row.last_execution_at is None else "RECORDED",
            health.active_failure_count, open_positions_count, open_orders_count,
            row.last_reconciliation_at, None if last is None else self.repository.event(last))

    @staticmethod
    def _set_mode(row, mode: RuntimeMode, at: datetime, code: FailureCode | None, message: str) -> None:  # type: ignore[no-untyped-def]
        if RuntimeMode(row.mode) is not mode:
            row.mode = mode.value; row.entered_mode_at = at
        row.reason_code = None if code is None else code.value
        row.reason_message = message; row.updated_at = at


def operational_safe_mode(mode: RuntimeMode) -> bool:
    """Pass this only to Actual/Paper TradingEligibility; Shadow/Replay stays independent."""
    return mode in {RuntimeMode.SAFE_MODE, RuntimeMode.HALTED}
