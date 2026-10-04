"""The local Binance USDⓈ-M depth book, synchronized Binance's way.

This is deliberately **not** `app/crypto/orderbook.py`. That class implements Bybit's contract,
where a delta is accepted when `u == previous u + 1` and a snapshot arrives on the stream itself.
Both assumptions are wrong here, and silently so:

* Binance futures sends `U` (first update id in the frame), `u` (last) and `pu` (the previous
  frame's `u`). The documented continuity check is `pu == previous u`. It is **not**
  `U == pu + 1`: measured over 881 live frames, `U - pu` took values 51, 55, 66, 80, 119 and
  others. A collector that asserted the arithmetic relation would declare a gap several times a
  second and resnapshot forever.
* The first delta applied after a REST snapshot must satisfy `U <= lastUpdateId <= u`. Spot's
  rule is `U <= lastUpdateId + 1 <= u`; using the spot rule on futures discards a frame that
  should have been applied, and the book then starts one event behind with no gap reported.
* A snapshot never arrives on the stream. It is a separate REST read, which is why buffering has
  to start *before* the request goes out.

The second idea in this module is the **known interval**. A `limit=1000` snapshot does not cover
the whole book: measured 2026-10-04 its outer bounds reached only -0.1507% and +0.1499% of mid.
Levels outside those bounds do arrive on the diff stream, but seeing a level change outside the
snapshot is not evidence that we know every level out there. So the snapshot's outer bid and ask
define the interval this book claims knowledge of, levels outside it are not retained, and
`bands.py` refuses to call a band COMPLETE unless the whole band sits inside that interval. The
honest consequence, with these bounds, is that the +-1% band is PARTIAL rather than COMPLETE, and
that is reported rather than papered over with a zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .contract import MAX_BOOK_LEVELS
from .envelope import provider_decimal

#: Book is synchronized and usable.
SYNCED = "SYNCED"
#: No usable book: never snapshotted, or invalidated and waiting for a new snapshot.
UNSYNCED = "UNSYNCED"

#: Why a book stopped being usable. Written into telemetry and into the derived record.
GAP_FIRST_DELTA = "GAP_FIRST_DELTA"
GAP_PU_MISMATCH = "GAP_PU_MISMATCH"
GAP_NON_INCREASING = "GAP_NON_INCREASING"
GAP_MALFORMED_IDS = "GAP_MALFORMED_IDS"
CROSSED_BOOK = "CROSSED_BOOK"
LEVEL_OVERFLOW = "LEVEL_OVERFLOW"
STALE = "STALE"
RECONNECT = "RECONNECT"
QUEUE_OVERFLOW = "QUEUE_OVERFLOW"
SNAPSHOT_REJECTED = "SNAPSHOT_REJECTED"

#: What `apply_delta` decided about a frame.
APPLIED = "APPLIED"
#: Older than the snapshot: `u < lastUpdateId`. Expected, not a fault.
DISCARDED_OLD = "DISCARDED_OLD"
#: Already applied once (`u <= last applied u`) after a replay or a duplicate frame.
DISCARDED_DUPLICATE = "DISCARDED_DUPLICATE"
#: Buffered because there is no snapshot yet.
BUFFERED = "BUFFERED"
#: Continuity broke. The book is now UNSYNCED and a snapshot is required.
GAP = "GAP"


@dataclass(frozen=True)
class DepthFrame:
    """One `depthUpdate` frame, with the identifiers the sync rules need."""

    first_update_id: int       # U
    last_update_id: int        # u
    previous_update_id: int | None   # pu
    event_ms: int | None       # E
    transaction_ms: int | None  # T
    bids: tuple[tuple[str, str], ...]
    asks: tuple[tuple[str, str], ...]

    @classmethod
    def parse(cls, message: dict[str, Any]) -> "DepthFrame":
        """Read the frame, rejecting anything that is not a well-formed depth update.

        `pu` is treated as optional rather than assumed: it is absent on some combined-stream
        shapes, and a missing `pu` must mean "cannot prove continuity" instead of raising.
        """
        bids = tuple((provider_decimal(p), provider_decimal(q)) for p, q in message["b"])
        asks = tuple((provider_decimal(p), provider_decimal(q)) for p, q in message["a"])
        pu = message.get("pu")
        return cls(
            first_update_id=int(message["U"]),
            last_update_id=int(message["u"]),
            previous_update_id=None if pu is None else int(pu),
            event_ms=None if message.get("E") is None else int(message["E"]),
            transaction_ms=None if message.get("T") is None else int(message["T"]),
            bids=bids, asks=asks,
        )


@dataclass
class DepthBook:
    """Owns the levels, the known interval and the continuity state. One task mutates it."""

    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    state: str = UNSYNCED
    #: `u` of the last applied frame.
    last_update_id: int | None = None
    #: `lastUpdateId` of the snapshot currently underpinning the book.
    snapshot_update_id: int | None = None
    #: Outer bid and outer ask of that snapshot: the interval this book claims to know.
    known_low: Decimal | None = None
    known_high: Decimal | None = None
    #: Incremented on every successful snapshot. Wall candidates carry it so a candidate cannot
    #: appear to survive a resync.
    generation: int = 0
    #: True once the first delta after a snapshot has passed the `U <= lastUpdateId <= u` test.
    first_delta_applied: bool = False
    #: Receipt times of the last applied frame, for freshness and lag.
    last_receive_ms: int | None = None
    last_mono_ns: int | None = None
    last_event_ms: int | None = None
    last_invalidation: str | None = None
    #: Frames held while a snapshot is in flight.
    buffer: list[tuple[DepthFrame, int, int]] = field(default_factory=list)
    #: Counters, surfaced in telemetry and the storage stats.
    applied: int = 0
    discarded_old: int = 0
    discarded_duplicate: int = 0
    gaps: int = 0
    resyncs: int = 0
    snapshots_rejected: int = 0
    buffered_dropped: int = 0

    # ------------------------------------------------------------------ lifecycle

    def invalidate(self, reason: str) -> None:
        """Drop the book. Never stitch across a gap: levels, bounds and ids all go."""
        self.bids.clear()
        self.asks.clear()
        self.state = UNSYNCED
        self.last_update_id = None
        self.snapshot_update_id = None
        self.known_low = None
        self.known_high = None
        self.first_delta_applied = False
        self.last_invalidation = reason

    def on_connect(self, _connection_id: str | None = None) -> None:
        """A new connection invalidates whatever was there and starts buffering."""
        self.invalidate(RECONNECT)
        self.buffer.clear()

    def buffer_frame(self, frame: DepthFrame, receive_ms: int, mono_ns: int, *,
                     limit: int) -> str:
        """Hold a frame until the snapshot lands, bounded so a slow REST read cannot grow RAM."""
        if len(self.buffer) >= limit:
            # Dropping the oldest would silently create the gap we are trying to avoid, so the
            # buffer is declared overflowed and a fresh snapshot is required instead.
            self.buffer.clear()
            self.buffered_dropped += 1
            self.invalidate(QUEUE_OVERFLOW)
            return GAP
        self.buffer.append((frame, receive_ms, mono_ns))
        return BUFFERED

    # ------------------------------------------------------------------ snapshot

    def apply_snapshot(self, payload: dict[str, Any], receive_ms: int, mono_ns: int) -> str:
        """Install a REST snapshot, then replay whatever was buffered behind it.

        Returns the state after replay: a buffered frame can itself contain the gap, in which
        case the book is UNSYNCED again and the caller has to fetch another snapshot.
        """
        try:
            snapshot_id = int(payload["lastUpdateId"])
            bid_rows = [(Decimal(provider_decimal(p)), Decimal(provider_decimal(q)))
                        for p, q in payload["bids"]]
            ask_rows = [(Decimal(provider_decimal(p)), Decimal(provider_decimal(q)))
                        for p, q in payload["asks"]]
        except (KeyError, TypeError, ValueError):
            self.snapshots_rejected += 1
            self.invalidate(SNAPSHOT_REJECTED)
            self.buffer.clear()
            return self.state
        if not bid_rows or not ask_rows:
            self.snapshots_rejected += 1
            self.invalidate(SNAPSHOT_REJECTED)
            self.buffer.clear()
            return self.state

        self.bids = {price: qty for price, qty in bid_rows if qty > 0}
        self.asks = {price: qty for price, qty in ask_rows if qty > 0}
        # The snapshot's own extremes define the interval, taken from the rows as delivered
        # rather than from the retained map, so a zero-quantity outer row still bounds knowledge.
        self.known_low = min(price for price, _ in bid_rows)
        self.known_high = max(price for price, _ in ask_rows)
        self.snapshot_update_id = snapshot_id
        self.last_update_id = snapshot_id
        self.first_delta_applied = False
        self.last_receive_ms = receive_ms
        self.last_mono_ns = mono_ns
        self.last_event_ms = None if payload.get("E") is None else int(payload["E"])
        self.state = SYNCED
        self.generation += 1
        self.resyncs += 1
        self.last_invalidation = None

        if self._crossed():
            self.snapshots_rejected += 1
            self.invalidate(CROSSED_BOOK)
            self.buffer.clear()
            return self.state

        pending, self.buffer = self.buffer, []
        for frame, frame_receive_ms, frame_mono_ns in pending:
            if self.apply_delta(frame, frame_receive_ms, frame_mono_ns) == GAP:
                break
        return self.state

    # ------------------------------------------------------------------ deltas

    def apply_delta(self, frame: DepthFrame, receive_ms: int, mono_ns: int) -> str:
        """Apply one frame under Binance's futures continuity rules."""
        if self.state != SYNCED or self.snapshot_update_id is None or self.last_update_id is None:
            return BUFFERED
        if frame.last_update_id < frame.first_update_id:
            self.gaps += 1
            self.invalidate(GAP_MALFORMED_IDS)
            return GAP
        if frame.last_update_id < self.snapshot_update_id:
            # Older than the snapshot. Expected while a REST read was in flight.
            self.discarded_old += 1
            return DISCARDED_OLD

        if not self.first_delta_applied:
            # Futures: U <= lastUpdateId <= u. Spot's `lastUpdateId + 1` form is wrong here.
            if not (frame.first_update_id <= self.snapshot_update_id <= frame.last_update_id):
                self.gaps += 1
                self.invalidate(GAP_FIRST_DELTA)
                return GAP
        elif frame.previous_update_id is not None and frame.previous_update_id == self.last_update_id:
            # `pu == previous u` is the only continuity rule there is, and it is satisfied, so
            # this frame claims to be the next one. Then `u` has to move forward; a frame that
            # follows ours and does not advance is self-contradictory, not a re-delivery.
            if frame.last_update_id <= self.last_update_id:
                self.gaps += 1
                self.invalidate(GAP_NON_INCREASING)
                return GAP
        elif frame.last_update_id <= self.last_update_id:
            # Does not chain onto our last frame and carries nothing new: a re-delivered or
            # replayed frame. Discarded without invalidating, because no information is missing.
            self.discarded_duplicate += 1
            return DISCARDED_DUPLICATE
        else:
            # Advances past our last frame but does not chain onto it. Frames were missed, and a
            # missing `pu` cannot prove continuity either.
            self.gaps += 1
            self.invalidate(GAP_PU_MISMATCH)
            return GAP

        self._apply_levels(self.bids, frame.bids)
        self._apply_levels(self.asks, frame.asks)
        self.last_update_id = frame.last_update_id
        self.first_delta_applied = True
        self.last_receive_ms = receive_ms
        self.last_mono_ns = mono_ns
        self.last_event_ms = frame.event_ms
        self.applied += 1

        if len(self.bids) + len(self.asks) > MAX_BOOK_LEVELS:
            self.invalidate(LEVEL_OVERFLOW)
            return GAP
        if self._crossed():
            self.invalidate(CROSSED_BOOK)
            return GAP
        return APPLIED

    def _apply_levels(self, side: dict[Decimal, Decimal], levels: Iterable[tuple[str, str]]) -> None:
        """Absolute quantities replace; zero deletes, including a level we never held.

        Prices outside the known interval are dropped rather than stored. Deleting one is a
        no-op either way, so the two cases collapse into a single bounds test.
        """
        low, high = self.known_low, self.known_high
        for price_text, qty_text in levels:
            price = Decimal(price_text)
            qty = Decimal(qty_text)
            if qty == 0:
                side.pop(price, None)
                continue
            if low is not None and price < low:
                continue
            if high is not None and price > high:
                continue
            side[price] = qty

    def _crossed(self) -> bool:
        best_bid, best_ask = self.best_bid(), self.best_ask()
        if best_bid is None or best_ask is None:
            return True
        return best_bid >= best_ask

    # ------------------------------------------------------------------ view

    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    def mid(self) -> Decimal | None:
        """Mid of best bid and best ask. Never a last trade, a mark or an index price."""
        best_bid, best_ask = self.best_bid(), self.best_ask()
        if best_bid is None or best_ask is None:
            return None
        if self.known_low is None or self.known_high is None:
            return None
        value = (best_bid + best_ask) / 2
        # A mid outside the interval we claim to know is not a usable reference price.
        if value < self.known_low or value > self.known_high:
            return None
        return value

    def age_ms(self, at_ns: int) -> int | None:
        if self.last_mono_ns is None:
            return None
        return max(0, (at_ns - self.last_mono_ns) // 1_000_000)

    def lag_ms(self) -> int | None:
        """Exchange lag `receive_ms - E`, or None when either side is unknown."""
        if self.last_receive_ms is None or self.last_event_ms is None:
            return None
        return self.last_receive_ms - self.last_event_ms

    def level_count(self) -> int:
        return len(self.bids) + len(self.asks)

    def checkpoint(self) -> dict[str, Any]:
        """A synchronized checkpoint: levels, boundaries, last `u` and the source event time."""
        return {
            "generation": self.generation,
            "state": self.state,
            "snapshot_update_id": self.snapshot_update_id,
            "last_update_id": self.last_update_id,
            "known_low": _out(self.known_low),
            "known_high": _out(self.known_high),
            "best_bid": _out(self.best_bid()),
            "best_ask": _out(self.best_ask()),
            "mid": _out(self.mid()),
            "event_ms": self.last_event_ms,
            "receive_ms": self.last_receive_ms,
            "bid_levels": len(self.bids),
            "ask_levels": len(self.asks),
            "bids": [[_out(p), _out(q)] for p, q in sorted(self.bids.items(), reverse=True)],
            "asks": [[_out(p), _out(q)] for p, q in sorted(self.asks.items())],
        }

    def counters(self) -> dict[str, int | str | None]:
        return {
            "applied": self.applied,
            "discarded_old": self.discarded_old,
            "discarded_duplicate": self.discarded_duplicate,
            "gaps": self.gaps,
            "resyncs": self.resyncs,
            "snapshots_rejected": self.snapshots_rejected,
            "buffer_overflows": self.buffered_dropped,
            "generation": self.generation,
            "state": self.state,
            "last_invalidation": self.last_invalidation,
            "levels": self.level_count(),
            "buffered": len(self.buffer),
        }


def _out(value: Decimal | None) -> str | None:
    from .envelope import decimal_out
    return decimal_out(value)


__all__ = ["DepthBook", "DepthFrame", "SYNCED", "UNSYNCED", "APPLIED", "BUFFERED", "GAP",
           "DISCARDED_OLD", "DISCARDED_DUPLICATE", "GAP_FIRST_DELTA", "GAP_PU_MISMATCH",
           "GAP_NON_INCREASING", "GAP_MALFORMED_IDS", "CROSSED_BOOK", "LEVEL_OVERFLOW", "STALE",
           "RECONNECT", "QUEUE_OVERFLOW", "SNAPSHOT_REJECTED"]
