"""Versioned operational safety policy."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


OPERATIONS_VERSION = "operations_v0"


class OperationsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = OPERATIONS_VERSION
    heartbeat_interval_seconds: int = Field(default=60, gt=0)
    heartbeat_stale_seconds: int = Field(default=180, gt=0)
    market_data_stale_seconds: int = Field(default=120, gt=0)
    monitor_premarket: bool = False
    execution_failure_threshold: int = Field(default=3, gt=0)
    execution_failure_window_seconds: int = Field(default=300, gt=0)
    auto_safe_mode_on_db_error: bool = True
    auto_safe_mode_on_reconciliation_failure: bool = True
    kill_switch_requires_confirmation: bool = True
    recovery_requires_manual_ack: bool = True

    @model_validator(mode="after")
    def validate_windows(self) -> "OperationsConfig":
        if self.heartbeat_stale_seconds <= self.heartbeat_interval_seconds:
            raise ValueError("heartbeat stale threshold must exceed its interval")
        return self
