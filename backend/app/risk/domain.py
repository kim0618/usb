"""Decimal money, account, portfolio, and risk-result domain."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from app.execution.domain import OrderIntent
from app.market.symbols import normalize_symbol


def decimal_from(value: Decimal | int | str | float) -> Decimal:
    """Convert at the analytics/money boundary without importing float artifacts."""
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


class Currency(StrEnum):
    KRW = "KRW"
    USD = "USD"


@dataclass(frozen=True)
class FxRate:
    base_currency: Currency
    quote_currency: Currency
    quote_per_base: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_currency", Currency(self.base_currency))
        object.__setattr__(self, "quote_currency", Currency(self.quote_currency))
        object.__setattr__(self, "quote_per_base", decimal_from(self.quote_per_base))
        if self.base_currency == self.quote_currency or self.quote_per_base <= 0:
            raise ValueError("FX rate requires different currencies and a positive rate")


@dataclass(frozen=True)
class AccountSnapshot:
    equity: Decimal
    cash: Decimal
    currency: Currency
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "equity", decimal_from(self.equity))
        object.__setattr__(self, "cash", decimal_from(self.cash))
        object.__setattr__(self, "currency", Currency(self.currency))
        if self.equity < 0 or self.cash < 0:
            raise ValueError("equity and cash cannot be negative")
        _aware(self.as_of, "as_of")


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    current_price: Decimal
    instrument_currency: Currency
    initial_stop: Decimal | None = None
    add_count: int = 0
    base_notional_account_ccy: Decimal = Decimal("0")
    pyramid_notional_account_ccy: Decimal = Decimal("0")
    overnight: bool = False
    # The stop currently enforced on this position. A trailed stop is what an
    # add is actually protected by, so pyramid risk is measured against this and
    # falls back to the initial stop only while nothing has raised it.
    active_stop: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        for name in ("quantity", "average_price", "current_price", "base_notional_account_ccy", "pyramid_notional_account_ccy"):
            object.__setattr__(self, name, decimal_from(getattr(self, name)))
        for name in ("initial_stop", "active_stop"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, decimal_from(getattr(self, name)))
        if self.quantity < 0 or self.average_price <= 0 or self.current_price <= 0 or self.add_count < 0:
            raise ValueError("invalid position state")
        if self.base_notional_account_ccy < 0 or self.pyramid_notional_account_ccy < 0:
            raise ValueError("position exposure cannot be negative")

    @property
    def effective_stop(self) -> Decimal | None:
        """The stop an incremental buy would be exposed to, or None if unknown."""
        return self.active_stop if self.active_stop is not None else self.initial_stop


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: tuple[PositionSnapshot, ...]
    base_exposure_used: Decimal
    pyramid_exposure_used: Decimal
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "base_exposure_used", decimal_from(self.base_exposure_used))
        object.__setattr__(self, "pyramid_exposure_used", decimal_from(self.pyramid_exposure_used))
        if self.base_exposure_used < 0 or self.pyramid_exposure_used < 0:
            raise ValueError("portfolio exposure cannot be negative")
        if len({position.symbol for position in self.positions}) != len(self.positions):
            raise ValueError("portfolio symbols must be unique")
        _aware(self.as_of, "as_of")

    def position(self, symbol: str) -> PositionSnapshot | None:
        normalized = normalize_symbol(symbol)
        return next((item for item in self.positions if item.symbol == normalized), None)

    @property
    def overnight_position_count(self) -> int:
        return sum(position.overnight for position in self.positions)


@dataclass(frozen=True)
class DailyTradingState:
    trading_date: date
    attempted_symbols: frozenset[str] = frozenset()
    planned_risk_reserved: Decimal = Decimal("0")
    base_notional_reserved: Decimal = Decimal("0")
    pyramid_notional_reserved: Decimal = Decimal("0")
    add_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempted_symbols", frozenset(normalize_symbol(v) for v in self.attempted_symbols))
        for name in ("planned_risk_reserved", "base_notional_reserved", "pyramid_notional_reserved"):
            object.__setattr__(self, name, decimal_from(getattr(self, name)))
            if getattr(self, name) < 0:
                raise ValueError("daily reservations cannot be negative")


class RiskRejectionReason(StrEnum):
    NOT_ENTER_DECISION = "NOT_ENTER_DECISION"
    HUMAN_NOT_APPROVED = "HUMAN_NOT_APPROVED"
    INVALID_ENTRY_PRICE = "INVALID_ENTRY_PRICE"
    INVALID_STOP_PRICE = "INVALID_STOP_PRICE"
    ZERO_RISK_DISTANCE = "ZERO_RISK_DISTANCE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    INVALID_FX_RATE = "INVALID_FX_RATE"
    DAILY_RISK_LIMIT = "DAILY_RISK_LIMIT"
    DAILY_SYMBOL_LIMIT = "DAILY_SYMBOL_LIMIT"
    SYMBOL_ALREADY_ATTEMPTED = "SYMBOL_ALREADY_ATTEMPTED"
    BASE_CAPACITY_EXHAUSTED = "BASE_CAPACITY_EXHAUSTED"
    PYRAMID_RESERVE_EXHAUSTED = "PYRAMID_RESERVE_EXHAUSTED"
    SYMBOL_EXPOSURE_LIMIT = "SYMBOL_EXPOSURE_LIMIT"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    SAFE_MODE = "SAFE_MODE"
    INVALID_ACCOUNT_STATE = "INVALID_ACCOUNT_STATE"
    INVALID_PORTFOLIO_STATE = "INVALID_PORTFOLIO_STATE"
    PYRAMID_LIMIT = "PYRAMID_LIMIT"
    PYRAMID_RISK_BUDGET_EXHAUSTED = "PYRAMID_RISK_BUDGET_EXHAUSTED"
    POSITION_NOT_PROFITABLE = "POSITION_NOT_PROFITABLE"
    POSITION_NOT_FOUND = "POSITION_NOT_FOUND"
    OVERNIGHT_POSITION_LIMIT = "OVERNIGHT_POSITION_LIMIT"


@dataclass(frozen=True)
class RiskMetrics:
    one_r: Decimal
    planned_risk: Decimal
    per_share_risk: Decimal
    requested_quantity: Decimal
    final_quantity: Decimal
    requested_notional_account_ccy: Decimal
    final_notional_account_ccy: Decimal
    base_capacity_remaining: Decimal
    pyramid_capacity_remaining: Decimal
    symbol_capacity_remaining: Decimal
    # Pyramid-only stop-risk authority. A base entry has no position behind it,
    # so these stay at zero there rather than being given an invented meaning.
    risk_budget: Decimal = Decimal("0")
    current_stop_risk: Decimal = Decimal("0")
    post_add_stop_risk: Decimal = Decimal("0")
    risk_capped_notional_account_ccy: Decimal = Decimal("0")


@dataclass(frozen=True)
class RiskEvaluation:
    approved: bool
    order_intent: OrderIntent | None = None
    metrics: RiskMetrics | None = None
    rejection_reason: RiskRejectionReason | None = None
    details: str | None = None


@dataclass(frozen=True)
class OvernightStress:
    estimated_loss: Decimal
    account_loss_pct: Decimal
    max_notional_by_stress: Decimal
    within_limit: bool


class OvernightAction(StrEnum):
    HOLD_FULL = "HOLD_FULL"
    REDUCE_AND_HOLD = "REDUCE_AND_HOLD"
    EXIT_ALL = "EXIT_ALL"


@dataclass(frozen=True)
class OvernightRiskEvaluation:
    action: OvernightAction
    maximum_notional: Decimal
    reduce_notional: Decimal


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
