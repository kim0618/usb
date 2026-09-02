"""Deterministic test-only broker failure injection."""

from collections.abc import Sequence
from enum import StrEnum

from app.broker.sim import SimBroker
from app.execution.domain import OrderIntent
from app.market.domain import MinuteBar


class InjectedFailure(StrEnum):
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


class FailureInjectingSimBroker(SimBroker):
    def __init__(self, *args, failures: Sequence[InjectedFailure] = (), **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._injected = list(failures)

    def submit_order(self, intent: OrderIntent, market_bars: Sequence[MinuteBar]):  # type: ignore[no-untyped-def]
        if self._injected:
            failure = self._injected.pop(0)
            if failure is InjectedFailure.TIMEOUT:
                raise TimeoutError("deterministic injected submit timeout")
            raise ConnectionError("deterministic injected broker unavailable")
        return super().submit_order(intent, market_bars)
