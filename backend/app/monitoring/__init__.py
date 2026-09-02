"""Monitoring package reserved for later stages."""
"""Failure management and Safe Mode foundation."""

from app.monitoring.config import OPERATIONS_VERSION, OperationsConfig
from app.monitoring.domain import FailureCode, FailureSeverity, RuntimeMode
from app.monitoring.service import RuntimeHealthService, operational_safe_mode

__all__ = ["OPERATIONS_VERSION", "OperationsConfig", "FailureCode", "FailureSeverity",
           "RuntimeMode", "RuntimeHealthService", "operational_safe_mode"]
