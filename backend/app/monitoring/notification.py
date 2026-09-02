"""Small notification boundary; no external messaging provider."""

import logging
from typing import Protocol

from app.monitoring.domain import FailureEvent


class NotificationSink(Protocol):
    def notify(self, event: FailureEvent) -> None: ...


class LoggingNotificationSink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("usb.runtime")

    def notify(self, event: FailureEvent) -> None:
        self.logger.error("runtime_failure code=%s severity=%s component=%s message=%s timestamp=%s",
                          event.failure_code, event.severity, event.component, event.message,
                          event.occurred_at.isoformat())


class InMemoryNotificationSink:
    def __init__(self) -> None:
        self.events: list[FailureEvent] = []

    def notify(self, event: FailureEvent) -> None:
        self.events.append(event)
