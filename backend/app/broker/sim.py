"""Deterministic next-bar long-only SimBroker."""

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite
from uuid import uuid4

from app.broker.contract import Broker
from app.broker.domain import (
    OrderStatus, RejectionReason, SimAccount, SimFill, SimOrder, SimPosition,
    TradeResult, TradeStatus,
)
from app.execution.config import ExecutionConfig
from app.execution.domain import OrderIntent, OrderSide
from app.market.domain import MinuteBar
from app.market.symbols import normalize_symbol
from app.risk.domain import Currency, decimal_from


def generate_execution_scope() -> str:
    """Return a compact, process-independent namespace for durable IDs."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:6]}"


class SimBroker(Broker):
    def __init__(self, starting_cash: Decimal | str, currency: Currency = Currency.USD,
                 config: ExecutionConfig | None = None, execution_scope: str | None = None) -> None:
        self.config = config or ExecutionConfig()
        self.starting_cash = decimal_from(starting_cash)
        if self.starting_cash < 0:
            raise ValueError("starting cash cannot be negative")
        self.currency = Currency(currency)
        self.execution_scope = execution_scope
        self.cash = self.starting_cash
        self._orders: dict[str, SimOrder] = {}
        self._fills: list[SimFill] = []
        self._positions: dict[str, SimPosition] = {}
        self._trades: dict[str, TradeResult] = {}
        self._sequence = 0

    def submit_order(self, intent: OrderIntent, market_bars: Sequence[MinuteBar]) -> SimOrder:
        order = self._new_order(intent)
        if intent.quantity <= 0:
            return self._reject(order, RejectionReason.INVALID_QUANTITY)
        if intent.reference_price <= 0:
            return self._reject(order, RejectionReason.INVALID_PRICE)
        if intent.account_currency != self.currency.value or intent.instrument_currency != self.currency.value:
            return self._reject(order, RejectionReason.UNSUPPORTED_CURRENCY)
        candidates = sorted(
            (bar for bar in market_bars if bar.symbol == intent.symbol and bar.timestamp > intent.market_as_of),
            key=lambda bar: bar.timestamp,
        )
        target_index = self.config.fill_delay_bars - 1
        if len(candidates) <= target_index:
            return self._reject(order, RejectionReason.NO_NEXT_BAR)
        bar = candidates[target_index]
        if not isfinite(bar.open) or bar.open <= 0:
            return self._reject(order, RejectionReason.INVALID_MARKET_DATA)
        quantity = intent.quantity * (self.config.partial_fill_ratio if self.config.partial_fill_enabled else Decimal("1"))
        raw = decimal_from(bar.open)
        fill = self._make_fill(order, quantity, raw, bar)
        debit = fill.fill_price * quantity + fill.commission + fill.fx_cost
        if intent.side is OrderSide.BUY and debit > self.cash:
            return self._reject(order, RejectionReason.INSUFFICIENT_CASH)
        position = self._positions.get(intent.symbol)
        if intent.side is OrderSide.SELL and (position is None or intent.quantity > position.quantity):
            return self._reject(order, RejectionReason.SELL_EXCEEDS_POSITION)
        self._fills.append(fill)
        order.filled_quantity = quantity
        order.filled_at = bar.timestamp
        order.fill_session = bar.session
        preceding = max((b for b in market_bars if b.symbol == intent.symbol and b.timestamp <= intent.market_as_of), default=None, key=lambda b: b.timestamp)
        order.crossed_session = preceding is not None and preceding.session != bar.session
        order.status = OrderStatus.FILLED if quantity == order.requested_quantity else OrderStatus.PARTIALLY_FILLED
        if intent.side is OrderSide.BUY:
            self._apply_buy(intent, fill)
        elif intent.side is OrderSide.SELL:
            self._apply_sell(intent, fill)
        else:
            return self._reject(order, RejectionReason.UNSUPPORTED_SIDE)
        return order

    def cancel_order(self, order_id: str) -> SimOrder:
        order = self._orders[order_id]
        if order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            order.rejection_reason = RejectionReason.ORDER_ALREADY_FINAL
            return order
        order.status = OrderStatus.CANCELLED
        return order

    def get_order(self, order_id: str) -> SimOrder | None:
        return self._orders.get(order_id)

    def get_open_orders(self) -> tuple[SimOrder, ...]:
        return tuple(o for o in self._orders.values() if o.status in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED})

    def get_positions(self) -> tuple[SimPosition, ...]:
        return tuple(self._positions[k] for k in sorted(self._positions))

    def get_position(self, symbol: str) -> SimPosition | None:
        return self._positions.get(normalize_symbol(symbol))

    def get_fills(self, order_id: str | None = None) -> tuple[SimFill, ...]:
        return tuple(f for f in self._fills if order_id is None or f.order_id == order_id)

    def get_trade(self, symbol: str) -> TradeResult | None:
        return self._trades.get(normalize_symbol(symbol))

    def record_ambiguity(self, symbol: str, count: int = 1) -> None:
        if count < 0 or normalize_symbol(symbol) not in self._trades:
            raise ValueError("invalid ambiguity update")
        self._trades[normalize_symbol(symbol)].ambiguous_bar_count += count

    def account_snapshot(self, marks: dict[str, Decimal | float | str], as_of: datetime) -> SimAccount:
        value = Decimal("0")
        for symbol, position in self._positions.items():
            mark = decimal_from(marks[symbol])
            if mark <= 0:
                raise ValueError("mark prices must be positive")
            value += position.quantity * mark
        return SimAccount(self.cash, self.cash + value, self.currency, as_of)

    def state_snapshot(self) -> dict[str, object]:
        """Copy business state so a failed durable write can be undone.

        The ID sequence is deliberately excluded: rewinding it would let a retry
        reuse an order/fill ID that a previous attempt may already have emitted.
        """
        return {"cash": self.cash, "orders": deepcopy(self._orders), "fills": list(self._fills),
                "positions": deepcopy(self._positions), "trades": deepcopy(self._trades)}

    def restore_state(self, snapshot: dict[str, object]) -> None:
        """Return to a snapshot; _sequence keeps moving forward, so IDs stay unique."""
        self.cash = snapshot["cash"]
        self._orders = deepcopy(snapshot["orders"])
        self._fills = list(snapshot["fills"])
        self._positions = deepcopy(snapshot["positions"])
        self._trades = deepcopy(snapshot["trades"])

    def reset(self) -> None:
        self.cash = self.starting_cash
        self._orders.clear(); self._fills.clear(); self._positions.clear(); self._trades.clear()
        if self.execution_scope is None:
            self._sequence = 0

    def _scoped_id(self, prefix: str) -> str:
        scope = f"-{self.execution_scope}" if self.execution_scope is not None else ""
        return f"{prefix}{scope}-{self._sequence:08d}"

    def _new_order(self, intent: OrderIntent) -> SimOrder:
        self._sequence += 1
        order_id = self._scoped_id("SIM")
        order = SimOrder(order_id, intent.symbol, intent.side, intent.quantity, intent.reference_price,
                         intent.created_at, intent.market_as_of, intent.intent_type.value,
                         intent.strategy_version, self.config.version)
        self._orders[order_id] = order
        return order

    @staticmethod
    def _reject(order: SimOrder, reason: RejectionReason) -> SimOrder:
        order.status = OrderStatus.REJECTED
        order.rejection_reason = reason
        return order

    def _make_fill(self, order: SimOrder, quantity: Decimal, raw: Decimal, bar: MinuteBar) -> SimFill:
        bps = Decimal("10000")
        spread = raw * quantity * self.config.default_spread_bps / bps
        slippage = raw * quantity * self.config.default_slippage_bps / bps
        direction = Decimal("1") if order.side is OrderSide.BUY else Decimal("-1")
        fill_price = raw + direction * raw * (self.config.default_spread_bps + self.config.default_slippage_bps) / bps
        commission = raw * quantity * self.config.commission_bps / bps
        fx = raw * quantity * self.config.fx_cost_bps / bps
        self._sequence += 1
        return SimFill(self._scoped_id("FILL"), order.id, order.symbol, order.side, quantity,
                       raw, fill_price, spread, slippage, commission, fx,
                       spread + slippage + commission + fx, bar.timestamp, bar.session)

    def _apply_buy(self, intent: OrderIntent, fill: SimFill) -> None:
        notional = fill.fill_price * fill.quantity
        self.cash -= notional + fill.commission + fill.fx_cost
        position = self._positions.get(intent.symbol)
        if position is None:
            if intent.risk_amount <= 0:
                raise ValueError("initial planned risk must be positive")
            self._positions[intent.symbol] = SimPosition(intent.symbol, fill.quantity, fill.fill_price,
                notional, Decimal("0"), fill.filled_at, fill.filled_at)
            self._trades[intent.symbol] = TradeResult(
                f"TRADE-{intent.symbol}-{fill.filled_at.isoformat()}", intent.symbol, fill.filled_at, None,
                fill.quantity, fill.quantity, fill.fill_price, None, Decimal("0"), -fill.total_cost,
                intent.risk_amount, Decimal("0"), -fill.total_cost / intent.risk_amount,
                fill.total_cost, _entry_notional=notional,
            )
        else:
            new_qty = position.quantity + fill.quantity
            position.cost_basis += notional
            position.quantity = new_qty
            position.average_price = position.cost_basis / new_qty
            position.updated_at = fill.filled_at
            trade = self._trades[intent.symbol]
            trade.total_quantity += fill.quantity
            trade._entry_notional += notional
            trade.average_entry_price = trade._entry_notional / trade.total_quantity
            trade.total_cost += fill.total_cost
            trade.net_pnl -= fill.total_cost
            trade.net_r = trade.net_pnl / trade.planned_initial_risk

    def _apply_sell(self, intent: OrderIntent, fill: SimFill) -> None:
        position = self._positions[intent.symbol]
        gross = (fill.fill_price - position.average_price) * fill.quantity
        position.quantity -= fill.quantity
        position.cost_basis = position.average_price * position.quantity
        position.realized_pnl += gross
        position.updated_at = fill.filled_at
        self.cash += fill.fill_price * fill.quantity - fill.commission - fill.fx_cost
        trade = self._trades[intent.symbol]
        trade._sold_quantity += fill.quantity
        trade._exit_notional += fill.fill_price * fill.quantity
        trade.average_exit_price = trade._exit_notional / trade._sold_quantity
        trade.gross_pnl += gross
        trade.total_cost += fill.total_cost
        trade.net_pnl = trade.gross_pnl - trade.total_cost
        trade.gross_r = trade.gross_pnl / trade.planned_initial_risk
        trade.net_r = trade.net_pnl / trade.planned_initial_risk
        if position.quantity == 0:
            # Exact lifecycle totals avoid repeating-Decimal weighted-average residue.
            trade.gross_pnl = trade._exit_notional - trade._entry_notional
            trade.net_pnl = trade.gross_pnl - trade.total_cost
            trade.gross_r = trade.gross_pnl / trade.planned_initial_risk
            trade.net_r = trade.net_pnl / trade.planned_initial_risk
            trade.exit_time = fill.filled_at
            trade.exit_reason = intent.reason
            trade.status = TradeStatus.CLOSED
            del self._positions[intent.symbol]
