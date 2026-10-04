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
from .leverage import ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE, LeverageCapability
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
LEVERAGE_POSITION_OPEN = "LEVERAGE_POSITION_OPEN"
UNSUPPORTED_LEVERAGE = "UNSUPPORTED_LEVERAGE"
LEVERAGE_CONSTRAINT_UNAVAILABLE = "LEVERAGE_CONSTRAINT_UNAVAILABLE"
#: The self-imposed ceiling from `BINANCE_LIVE_MAX_QTY`, distinct from Binance's own
#: `QTY_ABOVE_MARKET_MAXIMUM` so a report can tell "we refused this" from "the exchange would".
QTY_ABOVE_LOCAL_MAXIMUM = "QTY_ABOVE_LOCAL_MAXIMUM"
#: The four-way symbol agreement below failed. This is the refusal that must never be reachable
#: in a correct build, which is exactly why it is checked: on a multi-symbol screen the
#: catastrophic failure is not a rejected order, it is an accepted one against the wrong
#: instrument, and that failure is silent unless something asserts against it.
SYMBOL_MISMATCH = "SYMBOL_MISMATCH"


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
                 arm: Any | None = None,
                 capability: LeverageCapability | None = None) -> None:
        self.reader = reader
        self.client = client
        self.config = config
        self.mirror = mirror
        #: What this account has actually been allowed to set. Starts empty and knows nothing;
        #: see `live.leverage` for why the bracket table cannot answer this.
        self.capability = capability if capability is not None else LeverageCapability()
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

    def _agree_on_symbol(self, requested: str | None, state: LiveSnapshot,
                         position: LivePosition) -> None:
        """Every symbol on the path must be the same string, or no order is built.

        Five sources are compared, not one: what the screen asked for, what this router's config
        names, what the reader this router shares with the adapter is pointed at, what the
        snapshot was taken for, whose filters are about to validate the quantity, and which
        position Binance just reported. In a correct build they are the same object's symbol
        copied five times. The check exists because the one bug a multi-symbol terminal can have
        that costs real money is placing an ETH order through a BTC-shaped path, and nothing
        downstream would notice: `exchangeInfo` for the wrong symbol yields a plausible step
        size, `positionRisk` for the wrong symbol yields a plausible flat position, and Binance
        would fill the result.

        `requested` is `None` when the caller named no symbol, which is the single-symbol path
        and is not a disagreement.
        """
        seen = {
            "router_config": self.config.symbol,
            "reader": self.reader.symbol,
            "snapshot": state.symbol,
            "filters": state.filters.symbol if state.filters is not None else None,
            "position": position.symbol,
        }
        if requested not in (None, ""):
            seen["request"] = str(requested).strip().upper()
        distinct = {value for value in seen.values() if value}
        if len(distinct) > 1 or None in seen.values():
            self._record(LiveEvent.ORDER_REFUSED, stage="SYMBOL", code=SYMBOL_MISMATCH,
                         message="symbol disagreement", symbols=seen)
            raise OrderRefused(
                SYMBOL_MISMATCH,
                "주문 경로의 심볼이 일치하지 않아 주문을 만들지 않았습니다. "
                f"({', '.join(f'{key}={value}' for key, value in seen.items())})")

    def plan(self, *, side: str, intent: str, qty: str | Decimal | None = None,
             notional_usdt: str | Decimal | None = None,
             snapshot: LiveSnapshot | None = None,
             symbol: str | None = None) -> OrderPlan:
        """Everything that must hold before an order exists, checked in refusal order.

        The request is recorded before the first check. A refusal here (a reverse, an account
        that cannot be read, a size the exchange would not take) has to leave the same trace as
        a refusal at the gate, otherwise the audit file shows nothing at all for a button the
        operator did press.
        """
        self._record(LiveEvent.ORDER_INTENT, stage="PLAN", side=side, intent=intent,
                     qty=str(qty) if qty is not None else None,
                     notional_usdt=str(notional_usdt) if notional_usdt is not None else None,
                     symbol=symbol or self.config.symbol, gates=self.gate_view())
        try:
            return self._plan(side=side, intent=intent, qty=qty, notional_usdt=notional_usdt,
                              snapshot=snapshot, symbol=symbol)
        except OrderRefused as exc:
            self._record(LiveEvent.ORDER_REFUSED, stage="PLAN", code=exc.code, message=exc.message)
            raise

    def _record(self, event: str, **payload: Any) -> None:
        if self.mirror is not None:
            self.mirror.append(event, **payload)

    def _plan(self, *, side: str, intent: str, qty: str | Decimal | None,
              notional_usdt: str | Decimal | None, snapshot: LiveSnapshot | None,
              symbol: str | None = None) -> OrderPlan:
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
        # Before the quantity is read and before anything is priced: a disagreement here means
        # every figure that follows is about a different instrument than the operator clicked.
        self._agree_on_symbol(symbol, state, position)

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
        if position.symbol != filters.symbol:
            # The fresh read is the quantity that would actually be sent, so it is checked
            # against the filters that are about to validate it rather than trusted because an
            # earlier snapshot agreed.
            self._record(LiveEvent.ORDER_REFUSED, stage="SYMBOL", code=SYMBOL_MISMATCH,
                         message="close re-read symbol disagreement",
                         symbols={"filters": filters.symbol, "position": position.symbol})
            raise OrderRefused(SYMBOL_MISMATCH,
                               "청산 직전 포지션 재조회의 심볼이 달라 청산을 중단했습니다.")
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

    def submit_close_only(self, plan: OrderPlan) -> dict[str, Any]:
        """Guard-owned CLOSE permission, independent of the manual arm TTL."""
        if not self.config.trading_enabled:
            raise OrderRefused(LIVE_TRADING_DISABLED, self._locked_message())
        if plan.intent != CLOSE or not plan.reduce_only:
            raise OrderRefused("CLOSE_ONLY_VIOLATION",
                               "자동청산 권한은 전량 reduceOnly CLOSE만 허용합니다.")
        params = plan.params(recv_window_ms=self.config.recv_window_ms)
        self._record(LiveEvent.ORDER_SENT, client_order_id=plan.client_order_id,
                     permission="AUTO_EXIT_CLOSE_ONLY", params=params)
        response = self.client.call_close_only("new_order", params)
        self._record(LiveEvent.ORDER_RESULT, client_order_id=plan.client_order_id,
                     permission="AUTO_EXIT_CLOSE_ONLY", response=response)
        return response

    # ------------------------------------------------------------------ leverage

    def set_leverage(self, leverage: int) -> dict[str, Any]:
        """Implemented, gated, and never optimistic: the caller re-reads `symbolConfig` after
        this returns and shows what Binance reports, not what was asked for."""
        if self.mirror is not None:
            self.mirror.append(LiveEvent.LEVERAGE_INTENT, symbol=self.config.symbol,
                               leverage=leverage, gates=self.gate_view())
        if not self.armed:
            message = f"레버리지 변경도 실계좌 쓰기입니다. {self._locked_message()}"
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="GATE", symbol=self.config.symbol,
                         code=LIVE_TRADING_DISABLED, message=message)
            raise OrderRefused(LIVE_TRADING_DISABLED, message)
        position = self.reader.position()
        if not position.is_flat:
            message = "포지션 보유 중에는 레버리지를 변경할 수 없습니다. 청산 후 다시 시도하세요."
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="POSITION", symbol=self.config.symbol,
                         code=LEVERAGE_POSITION_OPEN, message=message)
            raise OrderRefused(LEVERAGE_POSITION_OPEN, message)
        try:
            payload = self.client.call("leverage_bracket", {"symbol": self.config.symbol})
            row = payload[0] if isinstance(payload, list) and payload else (payload or {})
            brackets = row.get("brackets") or []
            maximum = max(int(item["initialLeverage"]) for item in brackets)
        except (BinanceError, KeyError, TypeError, ValueError) as exc:
            message = "Binance 레버리지 허용 범위를 확인하지 못했습니다. 계좌 동기화 후 다시 시도하세요."
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="CONSTRAINT", symbol=self.config.symbol,
                         code=LEVERAGE_CONSTRAINT_UNAVAILABLE,
                         exchange_code=exc.code if isinstance(exc, BinanceError) else None,
                         message=exc.message if isinstance(exc, BinanceError) else type(exc).__name__)
            raise OrderRefused(LEVERAGE_CONSTRAINT_UNAVAILABLE, message) from None
        if leverage < 1 or leverage > maximum:
            message = f"현재 Binance 계정에서 {leverage}x 레버리지를 사용할 수 없습니다."
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="CONSTRAINT", symbol=self.config.symbol,
                         code=UNSUPPORTED_LEVERAGE, leverage=leverage, maximum=maximum,
                         message=message)
            raise OrderRefused(UNSUPPORTED_LEVERAGE, message)
        # What the bracket table cannot say. If Binance has already refused this account at or
        # below this step, there is no reason to send the write again: the refusal is replayed
        # with the same message the screen would have shown, and the restriction expires by
        # itself at the instant Binance named.
        known = self.capability.restriction_for(leverage)
        if known is not None:
            message = known.message(leverage)
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="CAPABILITY", symbol=self.config.symbol,
                         code=ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE, leverage=leverage,
                         above=known.above, until_ms=known.until_ms, message=message)
            raise OrderRefused(ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE, message)
        try:
            response = self.client.call("set_leverage", {
                "symbol": self.config.symbol, "leverage": int(leverage),
                "recvWindow": self.config.recv_window_ms})
        except BinanceError as exc:
            self._record(LiveEvent.LEVERAGE_REFUSED, stage="EXCHANGE", symbol=self.config.symbol,
                         code=exc.code, status=exc.status, message=exc.message,
                         requested=leverage)
            # Only the restriction family teaches anything; every other error leaves the ladder
            # exactly as wide as it was.
            learned = self.capability.note_refusal(leverage=leverage, code=exc.code,
                                                   message=exc.message)
            if learned is not None:
                self._record(LiveEvent.LEVERAGE_REFUSED, stage="CAPABILITY_LEARNED",
                             symbol=self.config.symbol,
                             code=ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE, above=learned.above,
                             until_ms=learned.until_ms, requested=leverage)
                raise OrderRefused(ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE,
                                   learned.message(leverage)) from None
            raise
        self.capability.note_success(leverage)
        if self.mirror is not None:
            self.mirror.append(LiveEvent.LEVERAGE_RESULT, symbol=self.config.symbol,
                               requested=leverage, response=response)
        return response


__all__ = ["LiveOrderRouter", "OrderPlan", "OrderRefused", "OPEN", "CLOSE", "BUY", "SELL",
           "LIVE_TRADING_DISABLED", "REVERSE_NOT_ALLOWED", "NO_POSITION_TO_CLOSE",
           "ACCOUNT_NOT_READY", "QTY_ABOVE_LOCAL_MAXIMUM", "LEVERAGE_POSITION_OPEN",
           "UNSUPPORTED_LEVERAGE", "LEVERAGE_CONSTRAINT_UNAVAILABLE",
           "ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE", "SYMBOL_MISMATCH", "client_order_id"]
