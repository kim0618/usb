from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import decimal_string


class BookGap(RuntimeError): pass


class LocalOrderBook:
    def __init__(self) -> None:
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.update_id: int | None = None
        self.sequence: int | None = None
        self.ready = False

    def reset(self) -> None:
        self.bids.clear(); self.asks.clear(); self.update_id = None; self.sequence = None; self.ready = False

    @staticmethod
    def _apply(side: dict[Decimal, Decimal], levels: list[list[str]]) -> None:
        for price_raw, qty_raw in levels:
            price, qty = Decimal(price_raw), Decimal(qty_raw)
            if qty == 0: side.pop(price, None)
            else: side[price] = qty

    def apply(self, message_type: str, data: dict[str, Any]) -> None:
        update_id, sequence = int(data["u"]), int(data["seq"])
        if message_type == "snapshot":
            self.reset(); self._apply(self.bids, data["b"]); self._apply(self.asks, data["a"])
            self.update_id, self.sequence, self.ready = update_id, sequence, True
            return
        if not self.ready: raise BookGap("delta before snapshot")
        if update_id != self.update_id + 1 or sequence < self.sequence:  # type: ignore[operator]
            self.reset(); raise BookGap("orderbook continuity gap")
        self._apply(self.bids, data["b"]); self._apply(self.asks, data["a"])
        self.update_id, self.sequence = update_id, sequence

    def features(self, timestamp_ms: int, receive_timestamp_ms: int) -> dict[str, str | int]:
        if not self.ready or not self.bids or not self.asks: raise BookGap("book is not ready")
        bids = sorted(self.bids.items(), reverse=True); asks = sorted(self.asks.items())
        best_bid, bid_q = bids[0]; best_ask, ask_q = asks[0]
        mid = (best_bid + best_ask) / 2; spread = best_ask - best_bid
        def volume(levels: list[tuple[Decimal, Decimal]], count: int) -> Decimal: return sum((q for _, q in levels[:count]), Decimal())
        b5, a5, b20, a20 = volume(bids, 5), volume(asks, 5), volume(bids, 20), volume(asks, 20)
        def imbalance(bid: Decimal, ask: Decimal) -> Decimal: return (bid - ask) / (bid + ask) if bid + ask else Decimal()
        micro = (best_ask * bid_q + best_bid * ask_q) / (bid_q + ask_q) if bid_q + ask_q else mid
        values: dict[str, Decimal | int] = {
            "timestamp_ms": timestamp_ms, "receive_timestamp_ms": receive_timestamp_ms,
            "best_bid": best_bid, "best_ask": best_ask, "spread": spread,
            "spread_bps": spread / mid * Decimal(10000), "mid": mid,
            "bid_volume_top5": b5, "ask_volume_top5": a5, "bid_volume_top20": b20, "ask_volume_top20": a20,
            "imbalance_5": imbalance(b5, a5), "imbalance_20": imbalance(b20, a20),
            "microprice": micro, "microprice_minus_mid": micro - mid,
            "top5_depth": b5 + a5, "top20_depth": b20 + a20,
            "book_pressure_ratio": b20 / a20 if a20 else Decimal(0),
        }
        return {key: (value if isinstance(value, int) else decimal_string(value)) for key, value in values.items()}
