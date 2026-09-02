"""Thin OrderIntent-to-Broker orchestration boundary."""

from collections.abc import Sequence

from app.broker.contract import Broker
from app.execution.domain import OrderIntent
from app.market.domain import MinuteBar


class ExecutionService:
    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def execute(self, intent: OrderIntent, market_bars: Sequence[MinuteBar]):  # type: ignore[no-untyped-def]
        return self.broker.submit_order(intent, market_bars)
