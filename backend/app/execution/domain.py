"""Order intent is a request boundary, never a broker order or fill."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.market.symbols import normalize_symbol


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class IntentType(StrEnum):
    BASE_ENTRY = "BASE_ENTRY"
    PYRAMID_ADD = "PYRAMID_ADD"
    EXIT = "EXIT"


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    intent_type: IntentType
    quantity: Decimal
    reference_price: Decimal
    notional: Decimal
    account_notional: Decimal
    account_currency: str
    instrument_currency: str
    strategy_version: str
    risk_amount: Decimal
    initial_stop: Decimal
    market_as_of: datetime
    created_at: datetime
    reason: str
    # The highest per-share execution price a BUY may fill at: a limit price. Risk
    # derives it from the risk and capacity it approved; the broker only compares
    # against it and never computes one.
    max_execution_price: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if min(self.quantity, self.reference_price, self.notional, self.account_notional) <= 0:
            raise ValueError("order intent quantity and notionals must be positive")
        if self.max_execution_price is not None and self.max_execution_price <= 0:
            raise ValueError("a maximum execution price must be positive")
        for name in ("market_as_of", "created_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
