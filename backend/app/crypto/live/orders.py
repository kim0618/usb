"""LONG, SHORT and CLOSE against a real Binance account - built here, sent nowhere yet.

The order path is written in full and locked behind two independent gates:

1. `BINANCE_LIVE_TRADING_ENABLED` in the environment (`LiveConfig.trading_enabled`), and
2. `BinanceFuturesClient(trading_enabled=True)`, checked again inside `rest.call` before a TRADE
   request is constructed.

Both are false by default, and V1 does not turn either on. A plan can still be built and priced
with both off, which is what the preview and the confirmation dialog need, and what makes the
locked state testable: the refusal is a value the tests assert on, not an absence of code.

Three behaviours are deliberate and are not conveniences:

* **No automatic reverse.** An OPEN against an existing opposite position is refused with
  `REVERSE_NOT_ALLOWED`, the same code and meaning the paper engine uses. Closing and reopening
  is two decisions and the operator makes both.
* **CLOSE re-reads the position from Binance first.** The quantity sent is the one Binance just
  reported, never a cached one, and the order carries `reduceOnly=true` so a size that is
  nevertheless stale can only shrink the position, never open a new one on the other side.
* **Quantities are validated against Binance's own filters.** `SymbolFilters` comes from a live
  `exchangeInfo`; the paper engine's Bybit instrument spec never sizes a Binance order.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .account import AccountReader, LiveSnapshot
from .credentials import CLIENT_ARM_ENV, MAX_QTY_ENV, TRADING_FLAG_ENV, LiveConfig
from .filters import QuantityRejected, SymbolFilters
from .mirror import LiveEvent, LiveMirror
from .models import BOTH, LONG, SHORT, LivePosition
from .rest import BinanceError, BinanceFuturesClient, TradingDisabled

OPEN = "OPEN"
CLOSE = "CLOSE"
BUY = "BUY"
SELL = "SELL"
MARKET = "MARKET"
#: Binance accepts `^[\.A-Z\:/a-z0-9_-]{1,36}$`; this prefix marks every order this terminal
#: sends so a fill can be told apart from one placed in the Binance app.
CLIENT_ORDER_PREFIX = "usbm"

LIVE_TRADING_DISABLED = "LIVE_TRADING_DISABLED"
REVERSE_NOT_ALLOWED = "REVERSE_NOT_ALLOWED"
NO_POSITION_TO_CLOSE = "NO_POSITION_TO_CLOSE"
ACCOUNT_NOT_READY = "ACCOUNT_NOT_READY"
NO_QUOTE = "NO_QUOTE"
QTY_REQUIRED = "QTY_REQUIRED"
#: The self-imposed ceiling from `BINANCE_LIVE_MAX_QTY`, distinct from Binance's own
#: `QTY_ABOVE_MARKET_MAXIMUM` so a report can tell "we refused this" from "the exchange would".
QTY_ABOVE_LOCAL_MAXIMUM = "QTY_ABOVE_LOCAL_MAXIMUM"


class OrderRefused(RuntimeError):
    """A refusal this code made, before Binance was asked anything."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def view(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class OrderPlan:
    """A market order that has passed every local check. Holding one does not send it."""
    symbol: str
    intent: str
    side: str
    order_side: str
    qty: Decimal
    reduce_only: bool
    reference_price: Decimal
    notional: Decimal
    client_order_id: str
    position_side: str = BOTH
    #: The position the plan was built against, so a CLOSE can be shown as "close 0.012 LONG".
    position_qty_at_plan: Decimal | None = None

    def params(self, *, recv_window_ms: int) -> dict[str, Any]:
        """Exactly what would go on the wire. `newOrderRespType=RESULT` so the response carries
        the fill, not just an acknowledgement - the panel must not have to poll to learn the
        average price."""
        params: dict[str, Any] = {
            "symbol": self.symbol, "side": self.order_side, "type": MARKET,
            "quantity": format(self.qty, "f"), "newClientOrderId": self.client_order_id,
            "newOrderRespType": "RESULT", "recvWindow": recv_window_ms,
        }
        if self.reduce_only:
            # One-way mode only. `reduceOnly` is rejected by Binance in hedge mode, and hedge
            # mode is already blocked upstream by the account gate.
            params["reduceOnly"] = "true"
        return params

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "intent": self.intent, "side": self.side,
                "order_side": self.order_side, "qty": self.qty, "reduce_only": self.reduce_only,
                "reference_price": self.reference_price, "notional": self.notional,
                "client_order_id": self.client_order_id,
                "position_qty_at_plan": self.position_qty_at_plan, "type": MARKET}


def client_order_id(intent: str, ts_ms: int | None = None) -> str:
    stamp = int(time.time() * 1000) if ts_ms is None else ts_ms
    return f"{CLIENT_ORDER_PREFIX}-{intent.lower()}-{stamp}"[:36]


class LiveOrderRouter:
    """Builds and (when armed) sends orders for one account."""

    def __init__(self, *, reader: AccountReader, client: BinanceFuturesClient,
                 config: LiveConfig, mirror: LiveMirror | None = None,
                 arm: Any | None = None) -> None:
        self.reader = reader
        self.client = client
        self.config = config
        self.mirror = mirror
        #: The `ArmSession` that owns `client.trading_enabled`, when there is one. Typed loosely
        #: to keep `live.arm` out of this module's imports: the router does not construct one and
        #: must keep working with `None`, which is the shape every existing test builds.
        self.arm = arm

    # ------------------------------------------------------------------ gates

    @property
    def armed(self) -> bool:
        """Both gates. Either one false means no order can be sent, whatever is asked.

        The session is read *before* the client flag, and that ordering is the safety property
        rather than a style choice. `ArmSession` expires on read: it lowers `trading_enabled`
        when somebody looks at it, and nothing else does. Reading the client flag alone would
        make the deadline depend on the screen still polling - an operator who closed the tab
        would leave a window that never shut.
        """
        if self.arm is not None:
            self.arm.armed  # syncs the expiry through to `client.trading_enabled`
        return self.config.trading_enabled and self.client.trading_enabled

    def _locked_message(self) -> str:
        """Name the gate that is actually shut. The two fail for different reasons and need
        different actions - one is a deployment setting, the other is a click - so a single
        message for both used to send the operator to edit a file they did not need to touch."""
        if not self.config.trading_enabled:
            return (f"실주문이 잠겨 있습니다. 이 서버는 {TRADING_FLAG_ENV}=false 입니다. "
                    "서버 설정을 바꿔야 열립니다.")
        return ("실주문이 잠겨 있습니다. LIVE 수동매매가 무장돼 있지 않습니다. "
                "화면에서 무장한 뒤 다시 시도하세요(무장은 제한 시간이 지나면 자동 해제됩니다).")

    def gate_view(self) -> dict[str, Any]:
        armed = self.armed  # first, so the client flag below is read after any expiry
        return {"armed": armed, "env_flag": self.config.trading_enabled,
                "client_armed": self.client.trading_enabled,
                "arm_session": self.arm.view() if self.arm is not None else None,
                "env_flag_name": TRADING_FLAG_ENV,
                "client_arm_env_name": CLIENT_ARM_ENV,
                "max_open_qty": self.config.max_open_qty,
                "max_open_qty_env_name": MAX_QTY_ENV}

    # ------------------------------------------------------------------ planning

    def plan(self, *, side: str, intent: str, qty: str | Decimal | None = None,
             notional_usdt: str | Decimal | None = None,
             snapshot: LiveSnapshot | None = None) -> OrderPlan:
        """Everything that must hold before an order exists, checked in refusal order.

        The request is recorded before the first check. A refusal here (a reverse, an account
        that cannot be read, a size the exchange would not take) has to leave the same trace as
        a refusal at the gate, otherwise the audit file shows nothing at all for a button the
        operator did press.
        """
        self._record(LiveEvent.ORDER_INTENT, stage="PLAN", side=side, intent=intent,
                     qty=str(qty) if qty is not None else None,
                     notional_usdt=str(notional_usdt) if notional_usdt is not None else None,
                     gates=self.gate_view())
        try:
            return self._plan(side=side, intent=intent, qty=qty, notional_usdt=notional_usdt,
                              snapshot=snapshot)
        except OrderRefused as exc:
            self._record(LiveEvent.ORDER_REFUSED, stage="PLAN", code=exc.code, message=exc.message)
            raise

    def _record(self, event: str, **payload: Any) -> None:
        if self.mirror is not None:
            self.mirror.append(event, **payload)

    def _plan(self, *, side: str, intent: str, qty: str | Decimal | None,
              notional_usdt: str | Decimal | None, snapshot: LiveSnapshot | None) -> OrderPlan:
        if side not in (LONG, SHORT) and intent == OPEN:
            raise OrderRefused("UNKNOWN_SIDE", f"side must be LONG or SHORT, got {side!r}")
        state = snapshot if snapshot is not None else self.reader.snapshot()
        if not state.ready:
            first = state.blockers[0]
            raise OrderRefused(ACCOUNT_NOT_READY, first.message)
        filters = state.filters
        book = state.book
        position = state.position
        if filters is None or book is None or position is None:
            raise OrderRefused(ACCOUNT_NOT_READY, "Binance 계좌 스냅샷이 완성되지 않았습니다.")

        if intent == CLOSE:
            return self._close_plan(filters, book)
        return self._open_plan(side, qty, notional_usdt, filters, book, position)

    def _open_plan(self, side: str, qty: Any, notional_usdt: Any, filters: SymbolFilters,
                   book: Any, position: LivePosition) -> OrderPlan:
        if not position.is_flat and position.side != side:
            raise OrderRefused(
                REVERSE_NOT_ALLOWED,
                f"{position.side} 포지션 {position.qty}이 열려 있습니다. 먼저 청산한 뒤 진입하세요.")
        reference = book.reference(side)
        if reference <= 0:
            raise OrderRefused(NO_QUOTE, "Binance 호가를 읽지 못했습니다.")
        if qty not in (None, ""):
            size = Decimal(str(qty))
        elif notional_usdt not in (None, ""):
            size = filters.qty_from_notional(Decimal(str(notional_usdt)), reference_price=reference)
        else:
            raise OrderRefused(QTY_REQUIRED, "qty 또는 notional_usdt 중 하나가 필요합니다.")
        ceiling = self.config.max_open_qty
        if ceiling is not None and size > ceiling:
            # Checked before Binance's filters, because this is the tighter of the two and the
            # operator needs to be told which limit they hit. Only OPEN is bounded: `_close_plan`
            # never consults this, so a position larger than the ceiling - one opened before the
            # ceiling was set, or by hand in the Binance app - can still be flattened.
            raise OrderRefused(
                QTY_ABOVE_LOCAL_MAXIMUM,
                f"수량 {size}이 이 프로세스의 상한 {ceiling}을 넘습니다 "
                f"(BINANCE_LIVE_MAX_QTY). 청산은 이 상한의 제한을 받지 않습니다.")
        try:
            filters.validate_market_qty(size, reference_price=reference)
        except QuantityRejected as exc:
            raise OrderRefused(exc.code, exc.message) from None
        return OrderPlan(symbol=filters.symbol, intent=OPEN, side=side,
                         order_side=BUY if side == LONG else SELL, qty=size, reduce_only=False,
                         reference_price=reference, notional=size * reference,
                         client_order_id=client_order_id(OPEN),
                         position_qty_at_plan=position.qty)

    def _close_plan(self, filters: SymbolFilters, book: Any) -> OrderPlan:
        """The quantity comes from a fresh position read, not from the snapshot that was used
        to render the screen: between the render and the click the position may have been
        reduced elsewhere, and sending the stale size would be an order for coins that are no
        longer there."""
        position = self.reader.position()
        if position.is_flat:
            raise OrderRefused(NO_POSITION_TO_CLOSE, "청산할 포지션이 없습니다.")
        side = position.side or LONG
        reference = book.reference(SHORT if side == LONG else LONG)
        size = position.qty
        try:
            filters.validate_market_qty(size, reference_price=reference)
        except QuantityRejected as exc:
            # A position smaller than the exchange minimum can still be closed by Binance's own
            # close-position path, but not by a plain market order. Surfaced rather than
            # silently rounded, which would leave a residue.
            raise OrderRefused(exc.code, exc.message) from None
        return OrderPlan(symbol=filters.symbol, intent=CLOSE, side=side,
                         order_side=SELL if side == LONG else BUY, qty=size, reduce_only=True,
                         reference_price=reference, notional=size * reference,
                         client_order_id=client_order_id(CLOSE),
                         position_qty_at_plan=position.qty)

    # ------------------------------------------------------------------ sending

    def submit(self, plan: OrderPlan) -> dict[str, Any]:
        """Send the plan, if and only if both gates are open. V1 never opens them."""
        # The button press was recorded by `plan`; this line records the plan that survived every
        # local check and is now being offered to the gate.
        self._record(LiveEvent.ORDER_INTENT, stage="SUBMIT", **plan.view(), gates=self.gate_view())
        if not self.armed:
            message = self._locked_message()
            self._record(LiveEvent.ORDER_REFUSED, stage="GATE", code=LIVE_TRADING_DISABLED,
                         message=message, client_order_id=plan.client_order_id)
            raise OrderRefused(LIVE_TRADING_DISABLED, message)
        params = plan.params(recv_window_ms=self.config.recv_window_ms)
        if self.mirror is not None:
            self.mirror.append(LiveEvent.ORDER_SENT, client_order_id=plan.client_order_id,
                               params={key: value for key, value in params.items()})
        try:
            response = self.client.call("new_order", params)
        except (BinanceError, TradingDisabled) as exc:
            code = getattr(exc, "code", None)
            self._record(LiveEvent.ORDER_REFUSED, stage="EXCHANGE",
                         code=str(code or type(exc).__name__), message=str(exc),
                         client_order_id=plan.client_order_id)
            raise
        if self.mirror is not None:
            self.mirror.append(LiveEvent.ORDER_RESULT, client_order_id=plan.client_order_id,
                               response=response)
        return response

    # ------------------------------------------------------------------ leverage

    def set_leverage(self, leverage: int) -> dict[str, Any]:
        """Implemented, gated, and never optimistic: the caller re-reads `symbolConfig` after
        this returns and shows what Binance reports, not what was asked for."""
        if self.mirror is not None:
            self.mirror.append(LiveEvent.LEVERAGE_INTENT, leverage=leverage, gates=self.gate_view())
        if not self.armed:
            message = f"레버리지 변경도 실계좌 쓰기입니다. {self._locked_message()}"
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="GATE", code=LIVE_TRADING_DISABLED,
                         message=message)
            raise OrderRefused(LIVE_TRADING_DISABLED, message)
        response = self.client.call("set_leverage", {
            "symbol": self.config.symbol, "leverage": int(leverage),
            "recvWindow": self.config.recv_window_ms})
        if self.mirror is not None:
            self.mirror.append(LiveEvent.LEVERAGE_RESULT, requested=leverage, response=response)
        return response


__all__ = ["LiveOrderRouter", "OrderPlan", "OrderRefused", "OPEN", "CLOSE", "BUY", "SELL",
           "LIVE_TRADING_DISABLED", "REVERSE_NOT_ALLOWED", "NO_POSITION_TO_CLOSE",
           "ACCOUNT_NOT_READY", "QTY_ABOVE_LOCAL_MAXIMUM", "client_order_id"]
