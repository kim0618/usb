"""Persistent singleton runtime state and append-oriented failure history."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.scanner import utc_now
from app.models.types import UTCDateTime


class RuntimeStateRecord(Base):
    __tablename__ = "runtime_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_runtime_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    entered_mode_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    reason_message: Mapped[str | None] = mapped_column(Text)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_market_data_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_execution_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class RuntimeFailureRecord(Base):
    __tablename__ = "runtime_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    failure_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    market_as_of: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
