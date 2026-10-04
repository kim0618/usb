"""Aggregate public trades, normalized without pretending to be fills.

`btcusdt@aggTrade` is Binance's **aggregate** trade stream: one frame can stand for many
individual fills that matched at the same price on the same side, and the frame says so through
`f` and `l` (first and last individual trade id). Measured over 64 frames: 50 carried a single
fill, one carried 11 and one carried 19, for 117 individual fills in total. Treating a frame as
one trade therefore undercounts trade *count* while getting volume exactly right, so `trade_id`
is the aggregate id `a`, `f`/`l` are kept, and `individual_fills` is recorded explicitly rather
than left for a reader to infer.

Aggressor side comes from `m` (was the buyer the maker). If the buyer was the maker then the
seller crossed the spread, so `m=true` is an aggressive **SELL**. Getting this backwards inverts
every flow imbalance in the dataset, which is a silent error, so it is asserted in the tests
with a frame in each direction.

Deduplication is a per-process high-water mark on `a`. Measured over 98 s of live stream, `a`
advanced by exactly 1 on 63 of 63 consecutive frames and never regressed, so a jump is evidence
that frames were missed rather than normal sparseness, and the contract makes an id jump
invalidate flow coverage. V0 does not backfill a jump: it says PARTIAL and moves on.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .envelope import decimal_out, provider_decimal

BUY = "BUY"
SELL = "SELL"

#: `on_trade` outcomes.
ACCEPTED = "ACCEPTED"
#: Already seen, or older than the high-water mark.
DUPLICATE = "DUPLICATE"
#: Accepted, but the aggregate id skipped: frames were missed.
ID_JUMP = "ID_JUMP"


@dataclass(frozen=True)
class Trade:
    """One normalized aggregate trade. Quantities stay decimal strings end to end."""

    trade_id: int
    first_trade_id: int
    last_trade_id: int
    price: Decimal
    qty: Decimal
    aggressor: str
    event_ms: int | None
    trade_ms: int | None
    receive_ms: int
    mono_ns: int

    @property
    def notional(self) -> Decimal:
        return self.price * self.qty

    @property
    def individual_fills(self) -> int:
        return self.last_trade_id - self.first_trade_id + 1

    def payload(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "first_trade_id": self.first_trade_id,
            "last_trade_id": self.last_trade_id,
            "individual_fills": self.individual_fills,
            "price": decimal_out(self.price),
            "qty": decimal_out(self.qty),
            "notional": decimal_out(self.notional),
            "aggressor": self.aggressor,
            "event_ms": self.event_ms,
            "trade_ms": self.trade_ms,
            "receive_ms": self.receive_ms,
            "aggregate_stream": True,
            "qty_includes_rpi": True,
        }

    @classmethod
    def parse(cls, message: dict[str, Any], receive_ms: int, mono_ns: int) -> "Trade":
        """Read an `aggTrade` frame. Unknown extra fields are kept only in the raw record."""
        return cls(
            trade_id=int(message["a"]),
            first_trade_id=int(message["f"]),
            last_trade_id=int(message["l"]),
            price=Decimal(provider_decimal(message["p"])),
            qty=Decimal(provider_decimal(message["q"])),
            # m=true means the buyer was the maker, so the aggressor was the seller.
            aggressor=SELL if bool(message["m"]) else BUY,
            event_ms=None if message.get("E") is None else int(message["E"]),
            trade_ms=None if message.get("T") is None else int(message["T"]),
            receive_ms=receive_ms,
            mono_ns=mono_ns,
        )


@dataclass
class TradeTape:
    """Per-process dedupe and ordering checks. Holds no history; `flow.py` does that."""

    high_water_id: int | None = None
    last_event_ms: int | None = None
    accepted: int = 0
    duplicates: int = 0
    id_jumps: int = 0
    out_of_order_event_time: int = 0
    malformed: int = 0
    last_receive_ms: int | None = None
    last_mono_ns: int | None = None

    def on_trade(self, trade: Trade) -> str:
        """Classify a trade and update the tape. The caller decides what to do with coverage."""
        if self.high_water_id is not None and trade.trade_id <= self.high_water_id:
            self.duplicates += 1
            return DUPLICATE
        outcome = ACCEPTED
        if self.high_water_id is not None and trade.trade_id > self.high_water_id + 1:
            self.id_jumps += 1
            outcome = ID_JUMP
        if (self.last_event_ms is not None and trade.event_ms is not None
                and trade.event_ms < self.last_event_ms):
            # Exchange time going backwards is a coverage problem, not a reason to drop volume.
            self.out_of_order_event_time += 1
        self.high_water_id = trade.trade_id
        if trade.event_ms is not None:
            self.last_event_ms = trade.event_ms
        self.last_receive_ms = trade.receive_ms
        self.last_mono_ns = trade.mono_ns
        self.accepted += 1
        return outcome

    def on_connect(self) -> None:
        """A new connection starts its own silence clock, and keeps the dedupe memory.

        Freshness asks "is *this* connection delivering", so an age carried over from before a
        disconnection would report the new socket as stale before it has had a chance to deliver
        anything. The high-water id is the opposite case and deliberately survives: the contract
        makes it per process, so a frame replayed across a reconnect is still a duplicate.
        """
        self.last_mono_ns = None
        self.last_receive_ms = None
        self.last_event_ms = None

    def age_ms(self, at_ns: int) -> int | None:
        if self.last_mono_ns is None:
            return None
        return max(0, (at_ns - self.last_mono_ns) // 1_000_000)

    def counters(self) -> dict[str, int | None]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "id_jumps": self.id_jumps,
            "out_of_order_event_time": self.out_of_order_event_time,
            "malformed": self.malformed,
            "high_water_id": self.high_water_id,
        }


__all__ = ["Trade", "TradeTape", "BUY", "SELL", "ACCEPTED", "DUPLICATE", "ID_JUMP"]
