"""Minimal execution-only broker contract."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.execution.domain import OrderIntent
from app.market.domain import MinuteBar


class Broker(ABC):
    @abstractmethod
    def submit_order(self, intent: OrderIntent, market_bars: Sequence[MinuteBar]): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def cancel_order(self, order_id: str): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_order(self, order_id: str): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_open_orders(self): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_positions(self): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_position(self, symbol: str): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_fills(self, order_id: str | None = None): ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def reset(self) -> None: ...
