"""Runtime safety persistence. Transaction ownership remains with services."""

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.runtime import RuntimeFailureRecord, RuntimeStateRecord
from app.monitoring.domain import FailureCode, FailureEvent, FailureSeverity, RuntimeMode


RUNTIME_STATE_ID = 1


class RuntimeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, now: datetime) -> RuntimeStateRecord:
        row = self.session.get(RuntimeStateRecord, RUNTIME_STATE_ID)
        if row is None:
            row = RuntimeStateRecord(id=RUNTIME_STATE_ID, mode=RuntimeMode.NORMAL.value,
                                     entered_mode_at=now, created_at=now, updated_at=now)
            self.session.add(row)
            self.session.flush()
        return row

    def add_failure(self, event: FailureEvent, created_at: datetime) -> RuntimeFailureRecord:
        row = RuntimeFailureRecord(
            failure_code=event.failure_code.value, severity=event.severity.value,
            component=event.component, message=event.message, occurred_at=event.occurred_at,
            market_as_of=event.market_as_of,
            metadata_json=json.dumps(event.metadata, sort_keys=True, separators=(",", ":"), default=str),
            resolved=event.resolved, resolved_at=event.resolved_at, created_at=created_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def unresolved(self) -> tuple[RuntimeFailureRecord, ...]:
        return tuple(self.session.scalars(select(RuntimeFailureRecord).where(
            RuntimeFailureRecord.resolved.is_(False)).order_by(RuntimeFailureRecord.occurred_at, RuntimeFailureRecord.id)))

    def unresolved_critical_count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(RuntimeFailureRecord).where(
            RuntimeFailureRecord.resolved.is_(False),
            RuntimeFailureRecord.severity == FailureSeverity.CRITICAL.value)) or 0)

    def last_failure(self) -> RuntimeFailureRecord | None:
        return self.session.scalar(select(RuntimeFailureRecord).order_by(
            RuntimeFailureRecord.occurred_at.desc(), RuntimeFailureRecord.id.desc()).limit(1))

    def resolve(self, failure_id: int, resolved_at: datetime) -> RuntimeFailureRecord:
        row = self.session.get(RuntimeFailureRecord, failure_id)
        if row is None:
            raise ValueError(f"failure {failure_id} does not exist")
        if not row.resolved:
            row.resolved = True
            row.resolved_at = resolved_at
            self.session.flush()
        return row

    @staticmethod
    def event(row: RuntimeFailureRecord) -> FailureEvent:
        return FailureEvent(id=row.id, failure_code=FailureCode(row.failure_code),
            severity=FailureSeverity(row.severity), component=row.component, message=row.message,
            occurred_at=row.occurred_at, market_as_of=row.market_as_of,
            metadata=json.loads(row.metadata_json), resolved=row.resolved, resolved_at=row.resolved_at)
