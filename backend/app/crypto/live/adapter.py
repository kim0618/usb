"""The seam the manual terminal talks to: one shape, two accounts behind it.

    manual terminal  ->  ManualTradingAdapter  ->  PaperAdapter      (existing paper engine)
                                              ->  BinanceLiveAdapter (real Binance account)

`ManualTradingAdapter` is a Protocol rather than a base class so neither implementation has to
change shape to fit the other, and so the paper side stays exactly the code that is already
tested: `PaperAdapter` reads the running `PaperSession` and adds nothing to it. The paper order
path is not moved behind this seam - it keeps the route it already has - because moving it would
put new code between the operator and an engine whose behaviour is fixed by a determinism proof.
That is stated in `place_order` rather than left implicit.

Read cadence on the LIVE side is two-tier, and the split is a rate-limit decision measured in
Binance's own weights:

    fast (every poll)   mark price 1 + book ticker 2 + positionRisk 5   =  8 weight
    slow (30 s, or on a stream event)   account 5 + symbolConfig 5 + commissionRate 20
                                        + positionSide/dual 30          = 60 weight

A one-second poll of the fast tier costs 480 weight a minute against Binance's 2400 IP budget; a
one-second poll of everything would cost more than 4000 and be throttled. The user data stream
is what makes the slow tier safe: a fill or a balance change marks the adapter dirty and the next
read is a full one, so the slow tier's age never matters at the moment it would.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from .account import (AccountReader, Blocker, LiveSnapshot, RESPONSE_SHAPE_CHANGED,
                      _binance_blocker)
from .credentials import LiveConfig
from .mirror import LiveEvent, LiveMirror
from .models import BookTop, LONG, SHORT, LiveFieldMissing, LivePosition, MarkPrice
from .orders import CLOSE, OPEN, LiveOrderRouter, OrderPlan, OrderRefused
from .position_card import card as position_card
from .preview import round_trip
from .rest import BinanceError, BinanceFuturesClient
from .sizing import presets as sizing_presets
from .stream import UserDataStream

PAPER = "PAPER"
BINANCE_LIVE = "BINANCE_LIVE"

FAST_TTL_MS = 1_000
SLOW_TTL_MS = 30_000

LEVERAGE_RESYNC_FAILED = "LEVERAGE_RESYNC_FAILED"
LEVERAGE_CONFIRMATION_FAILED = "LEVERAGE_CONFIRMATION_FAILED"


@runtime_checkable
class ManualTradingAdapter(Protocol):
    """What the manual terminal needs from an account, whichever account it is."""

    source: str

    def get_account(self) -> dict[str, Any]: ...
    def get_position(self) -> dict[str, Any]: ...
    def get_quote(self) -> dict[str, Any]: ...
    def get_order_preview(self, side: str, qty: str | None, notional_usdt: str | None) -> dict[str, Any]: ...
    def get_recent_fills(self, limit: int) -> list[dict[str, Any]]: ...
    def get_funding(self, limit: int) -> list[dict[str, Any]]: ...
    def place_order(self, side: str, intent: str, qty: str | None,
                    notional_usdt: str | None) -> dict[str, Any]: ...
    def set_leverage(self, leverage: str) -> dict[str, Any]: ...


class PaperAdapterOrdersRouteElsewhere(RuntimeError):
    """Paper orders keep their existing route. Raised if something tries to send one here."""


@dataclass
class PaperAdapter:
    """Read-only view of the running paper session, in the adapter's shape.

    Deliberately thin: every figure is taken from `PaperSession.snapshot()`, which is the same
    dictionary `/api/crypto/state` already returns. Nothing is recomputed, so this class cannot
    make the paper screen disagree with itself.
    """
    session: Any
    source: str = PAPER

    def get_account(self) -> dict[str, Any]:
        return self.session.snapshot().get("account") or {}

    def get_position(self) -> dict[str, Any]:
        account = self.get_account()
        return {"side": account.get("position_side"), "qty": account.get("position_qty"),
                "entry_price": account.get("avg_entry"),
                "liquidation_price": account.get("liquidation_price"),
                "leverage": account.get("leverage"),
                "unrealized_pnl": account.get("unrealized_pnl"),
                "margin_type": "PAPER_CROSS_EQUIVALENT"}

    def get_quote(self) -> dict[str, Any]:
        return self.session.snapshot().get("quote") or {}

    def get_order_preview(self, side: str, qty: str | None = None,
                          notional_usdt: str | None = None) -> dict[str, Any]:
        raise PaperAdapterOrdersRouteElsewhere(
            "the paper preview is served by /api/crypto/order-preview and is unchanged")

    def get_recent_fills(self, limit: int = 50) -> list[dict[str, Any]]:
        events = self.session.engine.ledger.events
        return [event for event in events if event["event_type"] == "FILL"][-limit:][::-1]

    def get_funding(self, limit: int = 50) -> list[dict[str, Any]]:
        events = self.session.engine.ledger.events
        return [event for event in events if event["event_type"] == "FUNDING"][-limit:][::-1]

    def place_order(self, side: str, intent: str, qty: str | None = None,
                    notional_usdt: str | None = None) -> dict[str, Any]:
        raise PaperAdapterOrdersRouteElsewhere(
            "paper orders keep /api/crypto/order; this adapter does not duplicate that path")

    def set_leverage(self, leverage: str) -> dict[str, Any]:
        raise PaperAdapterOrdersRouteElsewhere(
            "paper leverage keeps /api/crypto/leverage; this adapter does not duplicate that path")


class BinanceLiveAdapter:
    """The real account, behind the same shape. Never touches the paper engine or its ledger."""

    source = BINANCE_LIVE

    def __init__(self, *, config: LiveConfig, client: BinanceFuturesClient,
                 reader: AccountReader | None = None, router: LiveOrderRouter | None = None,
                 mirror: LiveMirror | None = None, stream: UserDataStream | None = None,
                 fast_ttl_ms: int = FAST_TTL_MS, slow_ttl_ms: int = SLOW_TTL_MS) -> None:
        self.config = config
        self.client = client
        self.mirror = mirror
        self.reader = reader or AccountReader(client, config)
        self.router = router or LiveOrderRouter(reader=self.reader, client=client, config=config,
                                                mirror=mirror)
        self.stream = stream
        self.fast_ttl_ms = fast_ttl_ms
        self.slow_ttl_ms = slow_ttl_ms
        self._snapshot: LiveSnapshot | None = None
        self._full_at_ms: int | None = None
        self._fast_at_ms: int | None = None

    # ------------------------------------------------------------------ snapshot

    @property
    def dirty(self) -> bool:
        return bool(self.stream is not None and self.stream.telemetry.dirty)

    def snapshot(self, *, force: bool = False) -> LiveSnapshot:
        """The account as it stands, refreshed at whichever tier is due."""
        now = int(time.time() * 1000)
        needs_full = (force or self._snapshot is None or self._full_at_ms is None
                      or now - self._full_at_ms > self.slow_ttl_ms or self.dirty
                      or not self._snapshot.ready)
        if needs_full:
            reason = "FORCED" if force else ("STREAM_EVENT" if self.dirty else "TTL")
            snapshot = self.reader.snapshot()
            self._snapshot = snapshot
            self._full_at_ms = snapshot.fetched_at_ms
            self._fast_at_ms = snapshot.fetched_at_ms
            if self.stream is not None:
                self.stream.clear_dirty()
            if self.mirror is not None:
                self.mirror.append(LiveEvent.RECONCILE, reason=reason, ready=snapshot.ready,
                                   blockers=[item.code for item in snapshot.blockers],
                                   position=snapshot.position.view() if snapshot.position else None,
                                   wallet_balance=(snapshot.balance.wallet_balance
                                                   if snapshot.balance else None))
            return snapshot
        snapshot = self._snapshot
        assert snapshot is not None
        if self._fast_at_ms is None or now - self._fast_at_ms > self.fast_ttl_ms:
            self._refresh_fast(snapshot, now)
        return snapshot

    def _refresh_fast(self, snapshot: LiveSnapshot, now_ms: int) -> None:
        """Mark, book and position only. A failure here downgrades the snapshot to blocked
        rather than leaving yesterday's price on the screen."""
        try:
            snapshot.mark = MarkPrice.from_payload(
                self.client.call("mark_price", {"symbol": self.config.symbol}))
            snapshot.book = BookTop.from_payload(
                self.client.call("book_ticker", {"symbol": self.config.symbol}))
            snapshot.position = LivePosition.from_rows(
                self.client.call("position_risk", {"symbol": self.config.symbol}),
                self.config.symbol)
            snapshot.fetched_at_ms = now_ms
            self._fast_at_ms = now_ms
        except BinanceError as exc:
            snapshot.blockers.append(Blocker(*_binance_blocker(exc)))
        except (LiveFieldMissing, ValueError) as exc:
            snapshot.blockers.append(Blocker(RESPONSE_SHAPE_CHANGED, str(exc)))

    def resync(self) -> LiveSnapshot:
        """Startup and post-reconnect path: forget everything local and ask Binance.

        There is no local state to prefer - that is the point. The method exists so the call
        site reads as the contract does ("on restart, resync from Binance") rather than as a
        cache invalidation.
        """
        self._snapshot = None
        self._full_at_ms = None
        self._fast_at_ms = None
        return self.snapshot(force=True)

    def on_stream_change(self, event: str) -> None:
        """Called from the stream thread. Only marks; the REST read happens on the next poll,
        on the request thread, so a socket frame can never start an HTTP call inside the
        socket's own loop."""
        if self.mirror is not None:
            self.mirror.append(LiveEvent.STREAM_STATE, state="ACCOUNT_DIRTY", event=event)

    # ------------------------------------------------------------------ reads

    def get_account(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return snapshot.balance.view() if snapshot.balance else {}

    def get_position(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        position = snapshot.position.view() if snapshot.position else {}
        if snapshot.symbol_config is not None:
            position = {**position, "leverage": snapshot.symbol_config.leverage,
                        "margin_type": snapshot.symbol_config.margin_type}
        return position

    def get_quote(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {"mark": snapshot.mark.view() if snapshot.mark else None,
                "book": snapshot.book.view() if snapshot.book else None}

    def get_recent_fills(self, limit: int = 50) -> list[dict[str, Any]]:
        return [trade.view() for trade in self.reader.recent_fills(limit)][::-1]

    def get_funding(self, limit: int = 50) -> list[dict[str, Any]]:
        return [row.view() for row in self.reader.funding(limit)][::-1]

    def get_order_preview(self, side: str, qty: str | None = None,
                          notional_usdt: str | None = None, depth_limit: int = 20) -> dict[str, Any]:
        """Priced on Binance's book, with the account's own taker rate."""
        snapshot = self.snapshot()
        if not snapshot.ready or snapshot.filters is None or snapshot.mark is None:
            first = snapshot.blockers[0] if snapshot.blockers else None
            return {"side": side, "feasible": False, "reject_stage": "ACCOUNT",
                    "reject_code": first.code if first else "ACCOUNT_NOT_READY",
                    "reject_message": first.message if first else "LIVE 계좌를 읽지 못했습니다."}
        if snapshot.commission is None:
            return {"side": side, "feasible": False, "reject_stage": "ACCOUNT",
                    "reject_code": "COMMISSION_UNAVAILABLE",
                    "reject_message": "계정 수수료율을 읽지 못해 비용을 계산하지 않습니다."}
        depth = self.client.call("depth", {"symbol": self.config.symbol, "limit": depth_limit})
        try:
            size = (Decimal(str(qty)) if qty not in (None, "")
                    else snapshot.filters.qty_from_notional(
                        Decimal(str(notional_usdt)),
                        reference_price=snapshot.book.reference(side) if snapshot.book else Decimal(0)))
        except Exception as exc:  # a bad number from the UI is an input problem, not an outage
            return {"side": side, "feasible": False, "reject_stage": "INPUT",
                    "reject_code": "INVALID_QTY", "reject_message": str(exc)}
        leverage = snapshot.symbol_config.leverage if snapshot.symbol_config else None
        return round_trip(side=side, qty=size, depth=depth, mark=snapshot.mark,
                          commission=snapshot.commission, filters=snapshot.filters,
                          leverage=leverage)

    def get_sizing(self, depth_limit: int = 20) -> dict[str, Any]:
        """Quick-size ladder for both sides, computed here rather than on the screen.

        One depth read serves both sides, so the two ladders are priced on the same book. The
        panel receives quantities and the reason for any it cannot offer; it never derives a
        size of its own.
        """
        snapshot = self.snapshot()
        if not snapshot.ready or snapshot.filters is None or snapshot.mark is None:
            first = snapshot.blockers[0] if snapshot.blockers else None
            return {"available": False,
                    "reject_code": first.code if first else "ACCOUNT_NOT_READY",
                    "reject_message": first.message if first else "LIVE 계좌를 읽지 못했습니다."}
        if snapshot.commission is None:
            return {"available": False, "reject_code": "COMMISSION_UNAVAILABLE",
                    "reject_message": "계정 수수료율을 읽지 못해 수량을 계산하지 않습니다."}
        depth = self.client.call("depth", {"symbol": self.config.symbol, "limit": depth_limit})
        leverage = snapshot.symbol_config.leverage if snapshot.symbol_config else None
        available = snapshot.balance.available_balance if snapshot.balance else None
        return {
            "available": True,
            "symbol": self.config.symbol,
            "fetched_at_ms": snapshot.fetched_at_ms,
            "age_ms": snapshot.age_ms(),
            "sides": {side: sizing_presets(side=side, depth=depth, mark=snapshot.mark,
                                           commission=snapshot.commission,
                                           filters=snapshot.filters, leverage=leverage,
                                           available=available,
                                           ceiling=self.config.max_open_qty,
                                           position=snapshot.position)
                      for side in (LONG, SHORT)},
        }

    def get_position_card(self, depth_limit: int = 50) -> dict[str, Any]:
        """The held position, its history and what closing it now would net.

        Only read when a position exists: with a flat account there is nothing to price, and
        the three extra reads this needs (trades, funding, book) would be spent on nothing.
        """
        snapshot = self.snapshot()
        if not snapshot.ready or snapshot.filters is None:
            first = snapshot.blockers[0] if snapshot.blockers else None
            return {"open": False, "available": False,
                    "reject_code": first.code if first else "ACCOUNT_NOT_READY",
                    "reject_message": first.message if first else "LIVE 계좌를 읽지 못했습니다."}
        position = snapshot.position
        if position is None or position.is_flat:
            return {"open": False, "available": True}
        if snapshot.commission is None:
            return {"open": True, "available": False, "reject_code": "COMMISSION_UNAVAILABLE",
                    "reject_message": "계정 수수료율을 읽지 못해 청산 손익을 계산하지 않습니다."}
        depth = self.client.call("depth", {"symbol": self.config.symbol, "limit": depth_limit})
        leverage = snapshot.symbol_config.leverage if snapshot.symbol_config else None
        built = position_card(position=position, trades=self.reader.recent_fills(200),
                              funding_rows=self.reader.funding(100), depth=depth,
                              commission=snapshot.commission, filters=snapshot.filters,
                              leverage=leverage)
        return {**built, "available": True, "symbol": self.config.symbol,
                "fetched_at_ms": snapshot.fetched_at_ms, "age_ms": snapshot.age_ms()}

    # ------------------------------------------------------------------ writes (gated)

    def plan_order(self, side: str, intent: str, qty: str | None = None,
                   notional_usdt: str | None = None) -> OrderPlan:
        return self.router.plan(side=side, intent=intent, qty=qty, notional_usdt=notional_usdt,
                                snapshot=self.snapshot())

    def place_order(self, side: str, intent: str, qty: str | None = None,
                    notional_usdt: str | None = None) -> dict[str, Any]:
        """Builds the order and then asks the router to send it. In V1 the router refuses."""
        plan = self.plan_order(side, intent, qty, notional_usdt)
        response = self.router.submit(plan)
        self.resync()
        return {"plan": plan.view(), "response": response}

    def open_long(self, qty: str | None = None, notional_usdt: str | None = None) -> dict[str, Any]:
        return self.place_order("LONG", OPEN, qty, notional_usdt)

    def open_short(self, qty: str | None = None, notional_usdt: str | None = None) -> dict[str, Any]:
        return self.place_order("SHORT", OPEN, qty, notional_usdt)

    def close_position(self) -> dict[str, Any]:
        return self.place_order("", CLOSE)

    def set_leverage(self, leverage: str) -> dict[str, Any]:
        """Change it, then read it back. The UI shows the read, never the request."""
        requested = int(Decimal(str(leverage)))
        before = self.resync()
        if not before.ready:
            raise OrderRefused(
                LEVERAGE_RESYNC_FAILED,
                "현재 Binance 계좌 상태를 확인하지 못했습니다. 동기화 후 다시 시도하세요.")
        result = self.router.set_leverage(requested)
        after = self.resync()
        if not after.ready or after.symbol_config is None:
            raise OrderRefused(
                LEVERAGE_RESYNC_FAILED,
                "Binance 변경 후 계좌 동기화에 실패했습니다. 현재 레버리지를 다시 확인하세요.")
        actual = after.symbol_config.leverage
        if actual != Decimal(requested):
            raise OrderRefused(
                LEVERAGE_CONFIRMATION_FAILED,
                "Binance 응답과 실제 계좌 레버리지가 일치하지 않습니다. 계좌를 새로고침하세요.")
        return {"requested": str(requested), "response": result, "leverage": actual}

    # ------------------------------------------------------------------ view

    def view(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {"source": self.source, "config": self.config.view(),
                "gates": self.router.gate_view(),
                "snapshot": snapshot.view(),
                "stream": self.stream.view() if self.stream is not None else None,
                "mirror": self.mirror.view() if self.mirror is not None else None,
                "rest": self.client.telemetry.view()}


__all__ = ["BINANCE_LIVE", "PAPER", "BinanceLiveAdapter", "ManualTradingAdapter", "PaperAdapter",
           "PaperAdapterOrdersRouteElsewhere", "OrderRefused"]
