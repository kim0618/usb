"""Simulation order, fill, position, account, and trade domains."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.execution.domain import OrderSide
from app.market.domain import MarketSession
from app.market.symbols import normalize_symbol
from app.risk.domain import Currency, decimal_from


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class RejectionReason(StrEnum):
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_PRICE = "INVALID_PRICE"
    NO_NEXT_BAR = "NO_NEXT_BAR"
    SELL_EXCEEDS_POSITION = "SELL_EXCEEDS_POSITION"
    UNSUPPORTED_SIDE = "UNSUPPORTED_SIDE"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    ORDER_ALREADY_FINAL = "ORDER_ALREADY_FINAL"


class TradeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class SimOrder:
    id: str
    symbol: str
    side: OrderSide
    requested_quantity: Decimal
    reference_price: Decimal
    submitted_at: datetime
    market_as_of: datetime
    intent_type: str
    intent_version: str
    execution_version: str
    filled_quantity: Decimal = Decimal("0")
    filled_at: datetime | None = None
    status: OrderStatus = OrderStatus.PENDING
    rejection_reason: RejectionReason | None = None
    fill_session: MarketSession | None = None
    crossed_session: bool = False

    @property
    def remaining_quantity(self) -> Decimal:
        return self.requested_quantity - self.filled_quantity


@dataclass(frozen=True)
class SimFill:
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    raw_market_price: Decimal
    fill_price: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    commission: Decimal
    fx_cost: Decimal
    total_cost: Decimal
    filled_at: datetime
    session: MarketSession


@dataclass
class SimPosition:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal
    opened_at: datetime
    updated_at: datetime


@dataclass
class TradeResult:
    trade_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime | None
    initial_quantity: Decimal
    total_quantity: Decimal
    average_entry_price: Decimal
    average_exit_price: Decimal | None
    gross_pnl: Decimal
    net_pnl: Decimal
    planned_initial_risk: Decimal
    gross_r: Decimal
    net_r: Decimal
    total_cost: Decimal
    ambiguous_bar_count: int = 0
    exit_reason: str | None = None
    status: TradeStatus = TradeStatus.OPEN
    _entry_notional: Decimal = field(default=Decimal("0"), repr=False)
    _exit_notional: Decimal = field(default=Decimal("0"), repr=False)
    _sold_quantity: Decimal = field(default=Decimal("0"), repr=False)

    @property
    def holding_duration(self) -> timedelta | None:
        return None if self.exit_time is None else self.exit_time - self.entry_time


@dataclass(frozen=True)
class SimAccount:
    cash: Decimal
    equity: Decimal
    currency: Currency
    as_of: datetime


def execution_gap_bps(actual: Decimal, shadow: Decimal) -> Decimal:
    actual, shadow = decimal_from(actual), decimal_from(shadow)
    if shadow <= 0:
        raise ValueError("shadow price must be positive")
    return (actual - shadow) / shadow * Decimal("10000")
