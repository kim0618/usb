"""The collector: a synchronous state machine, and a thin asyncio runner around it.

The split is the point. Every decision - continuity, coverage, freshness, candidate lifecycle,
what gets written - lives in `Collector`, which has no sockets, no clock of its own and no
`await`. The runner's only job is to move bytes and time into it. That is what makes a gap, a
reconnect, a stale book, a duplicate trade and a torn file testable at full speed with no network
and no sleeping, and it is why `tests/crypto/test_ms_v0_collector.py` can assert the exact
sequence of records a fault produces.

Two orderings matter and are easy to get wrong:

* **Buffering starts before the snapshot request.** The depth reader connects, frames begin
  arriving and are buffered, and only then does the REST read go out. A collector that requests
  the snapshot first and subscribes afterwards has an unobservable hole between the two.
* **One task owns the book.** Frames and the snapshot both arrive through a single bounded queue,
  so the snapshot is applied at a defined point in the frame sequence rather than racing with it.
  The queue carries the snapshot as just another item for exactly this reason.

Isolation: this module imports nothing from `app.crypto.paper`, `app.crypto.live` or
`app.crypto.terminal`, reads no credential, and runs as its own process. If it dies, the trading
service does not notice; if the trading service dies, this keeps collecting.

    MS_V0_ROOT=/path/to/data python -m app.crypto.market_structure_v0.collector --duration 86400
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import COLLECTOR_VERSION, EXCHANGE, SYMBOL, VERSION
from . import bands as BD
from . import book as B
from . import flow as F
from . import trades as T
from . import walls as W
from .contract import (BANDS, COMPLETE, DEFAULT_DURATION_S, DEPTH_QUEUE_MAX, DEPTH_REST_LIMIT,
                       DEPTH_REST_URL, DEPTH_STALE_MS, DEPTH_WS_URL, PERSIST_QUEUE_MAX,
                       RECONNECT_BACKOFF_MAX_S, RECV_TIMEOUT_S, SAMPLE_INTERVAL_S,
                       SHUTDOWN_TIMEOUT_S, STATS_INTERVAL_S, TRADE_STALE_MS, TRADE_WS_URL,
                       USER_AGENT, WS_MAX_FRAME_BYTES, contract_identity)
from .envelope import Session, decimal_out, now_ms, now_ns
from .safety import assert_public_url, safety_view
from .store import Store, StoreAuthorityLost

#: Telemetry events. The vocabulary is closed so a reader can group a day's faults.
CONNECT = "connect"
DISCONNECT = "disconnect"
RETRY = "retry"
GAP = "gap"
RESYNC = "resync"
SNAPSHOT_REQUEST = "snapshot_request"
SNAPSHOT_FAILED = "snapshot_failed"
STALE = "stale"
DUPLICATE = "duplicate"
ID_JUMP = "id_jump"
OUT_OF_ORDER = "out_of_order_event_time"
OVERFLOW = "overflow"
SHUTDOWN = "shutdown"
#: Liquidity Map V1.2: how one generation transition was classified and what it did to the wall
#: ledger. An added `event` on an existing contract kind, so the carry is auditable from the
#: journal alone without a `wall` payload changing shape.
WALL_CONTINUITY = "wall_continuity"

DEPTH_STREAM = "depth"
TRADE_STREAM = "trade"

# ----------------------------------------------------------------- resnapshot policy
#
# Added for Liquidity Map V1.1 hardening. These are *operational* settings of this collector,
# not semantics of `btc-ms.v0.1`: the contract's list of things that force a new snapshot (gap,
# overflow, stale connection, invalid or crossed book, reconnect) is unchanged and every one of
# them still fires immediately. What is added is the case the contract does not cover - the known
# interval slowly ceasing to describe where the market is - and a floor under how long the
# collector will run on one snapshot.
#
# The governing cost is **not** the API. A `limit=1000` depth read is weight 20 against a
# 2,400/minute IP budget, so one an hour is 0.014% of it and one a minute would still be under 1%.
# The cost that matters is that `apply_snapshot` increments the book `generation`, and a new
# generation ends **every** wall candidate as UNKNOWN in the journal. That is why there is no
# periodic polling, why the voluntary triggers are rate limited, and why the safety refresh is an
# hour rather than a minute.
#
# **V1.2 narrowed that cost without touching the journal.** A voluntary refresh that passes the
# five continuity gates in `walls.py` is a SOFT refresh: the `wall` records are written exactly as
# the contract fixes them, and a parallel ledger records which exact levels were proven present in
# the installed snapshot, so the viewer's frozen continuity rule can continue their observed span.
# A fault-driven resync remains HARD and still destroys the accumulated observation, because the
# stream was interrupted for an interval that cannot be bounded from the data. The rate limits
# above stay as they are: a SOFT refresh carries only what it can prove, and the counts it could
# not prove (`wall_ended`, `wall_unknown`) are published next to the ones it could.

#: The band the preview is allowed to call COMPLETE. A `limit=1000` snapshot reaches about
#: ±0.15% of mid, so ±0.1% is the only contract band that can ever be complete, and it is the one
#: thing worth spending a resnapshot to protect.
PROTECTED_BAND_BPS = Decimal("10")

#: Resnapshot when the nearer snapshot bound comes within this much of the protected band's edge.
#: Measured 2026-10-04: a fresh snapshot's bounds sat at -15.86 and +14.44 bps of mid, so the
#: margin over ±10 bps was only **4.44 bps at birth**, and 27 minutes of quiet market (2.3 bps of
#: mid movement) ate it down to 2.09. The margin erodes one-for-one with mid movement because the
#: bounds are fixed prices set at snapshot time. Acting at 1 bp of remaining margin replaces the
#: snapshot just before the promise breaks rather than after.
COVERAGE_MARGIN_TRIGGER_BPS = Decimal("1.0")

#: Floor between two coverage-driven resnapshots. Without it, a market that oscillates across the
#: trigger would resnapshot continuously and no candidate would ever accumulate persistence. With
#: it, the worst case is one lost wall history per five minutes, and in between the affected band
#: reports PARTIAL - which is true, and is what PARTIAL is for.
COVERAGE_REFRESH_COOLDOWN_S = 300.0

#: Conservative safety refresh: re-centre the known interval when no snapshot has been installed
#: for this long. A snapshot's bounds are fixed at the moment it was taken while the book around
#: them keeps turning over, so an old interval is increasingly a statement about a book that no
#: longer exists. One hour matches the contract's file rotation period, costs one REST read of
#: weight 20, and costs at most one wall history per hour.
SAFETY_REFRESH_S = 3600.0

#: Reasons a voluntary refresh was requested. Fault-driven resnapshots keep their own telemetry.
REFRESH_COVERAGE_EDGE = "coverage_edge"
REFRESH_SAFETY = "safety_refresh"
#: A voluntary refresh that did not install. V1.1 used this for "the REST read was not newer than
#: the live book"; V1.3 removed that whole situation and the event now reports why a staged
#: refresh was abandoned.
REFRESH_REJECTED = "refresh_rejected"
#: A staged refresh reached the swap invariant and replaced the live book's levels.
REFRESH_APPLIED = "refresh_applied"
#: Consecutive failures have crossed a reporting step. Published so a refresh that can never
#: succeed is visible as a pattern rather than as a slow trickle of single failures.
REFRESH_STORM = "refresh_storm"

# ----------------------------------------------------------------- staged refresh (V1.3)
#
# V1.2 installed a voluntary refresh by handing the REST snapshot to the live book, and refused
# the install whenever the snapshot was not newer than the deltas already applied. Measured on a
# moving book, that refused **every** attempt: two requests in 23 minutes, two refusals, zero
# installs, while the +-0.1% band the preview may call COMPLETE left the known interval for 50
# seconds. The cause was not a stale exchange. An independent probe found the snapshot *newer*
# than the newest stream frame in 10 reads out of 10, by 2,176 to 21,682 ids, with a book age of
# 32 to 178 ms. What went wrong is that the comparison happened at **install** time: during the
# round trip the stream advanced past the snapshot, by 1,695 and 1,789 ids in the two measured
# cases, so by the time the snapshot arrived the live book was ahead of it.
#
# So the snapshot is not the problem and never was; using it as a *replacement* is. V1.3 builds a
# second book from it instead, replays the frames that arrived during the round trip onto that
# book, and only then swaps:
#
#     INVARIANT AT SWAP: staging.last_update_id == live.last_update_id
#
# which makes the swap a change of **levels and bounds only**. The update ids do not move, so the
# next frame chains onto the swapped book exactly as it would have onto the old one, there is no
# first-delta rule to re-satisfy, no gap to risk, and - the part that matters for
# `lm-continuity` - **no unenumerated window at all**: every event between the snapshot and the
# swap was applied individually rather than absorbed. The live book is never touched until the
# invariant holds, so a refresh that cannot reach it costs nothing but the attempt.

#: Most frames held while a staged refresh is in flight. At about 10 frames a second and a
#: refresh that completes in well under a second, this is three orders of magnitude of headroom;
#: it exists so a stalled REST read cannot grow RAM, and overflowing it abandons the refresh
#: rather than the book.
REFRESH_BUFFER_MAX = 2_048

#: How long a staged refresh may take from request to swap before it is abandoned. The REST read
#: itself is gated at 300 ms by `lm-continuity`; this covers the whole operation including the
#: catch-up, which needs the stream to advance at most as far as the snapshot was taken ahead
#: (measured at 2,176 to 21,682 ids, which the stream covers in roughly 15 to 150 ms).
REFRESH_DEADLINE_MS = 1_000

#: Backoff after a refresh that did not install. A failure must not consume the 300 s cooldown:
#: the cooldown exists to bound how often a *successful* refresh destroys wall history, and an
#: attempt that changed nothing destroyed nothing. Ten seconds is long enough that a persistent
#: failure cannot become a request loop, and short enough that a transient one does not leave the
#: protected band unguarded for minutes.
REFRESH_RETRY_BACKOFF_S = 10.0

#: Report a storm every time consecutive failures reach a multiple of this.
REFRESH_STORM_EVERY = 5

#: What a staged refresh is doing.
REFRESH_BUFFERING = "BUFFERING"
REFRESH_REPLAYING = "REPLAYING"
REFRESH_DONE = "DONE"

#: Why a staged refresh was abandoned. Every one of these keeps the live book exactly as it was.
ABANDON_BOOK_UNUSABLE = "LIVE_BOOK_NOT_USABLE"
ABANDON_SNAPSHOT_REJECTED = "STAGING_SNAPSHOT_REJECTED"
ABANDON_REPLAY_GAP = "STAGING_REPLAY_GAP"
ABANDON_REPLAY_INCOMPLETE = "STAGING_BEHIND_LIVE_CHAIN"
ABANDON_BUFFER_OVERFLOW = "REFRESH_BUFFER_OVERFLOW"
ABANDON_DEADLINE = "REFRESH_DEADLINE_EXCEEDED"
ABANDON_FAULT = "LIVE_BOOK_FAULTED"
ABANDON_SNAPSHOT_FAILED = "REST_READ_FAILED"
ABANDON_SUPERSEDED = "REFRESH_SUPERSEDED"

#: How the first frame after the snapshot attached to the staged book. See `_attach_first_frame`.
ATTACH_STRADDLE = "FIRST_FRAME_STRADDLES_SNAPSHOT_ID"
ATTACH_SUCCESSOR = "FIRST_FRAME_IS_IMMEDIATE_SUCCESSOR"

#: Most resting candidates published in the compact state file, largest notional first. The
#: contract bounds every other buffer in this collector and this is the same discipline; 2,000 is
#: an order of magnitude above the 284 observed on a real book.
STATE_MAX_WALLS = 2_000
#: Most recent telemetry events carried in the compact state file, so a reader can show why the
#: feed is in the state it is in without walking the telemetry stream.
STATE_TELEMETRY_EVENTS = 12
#: Version of the compact state file's own shape. V1.2 adds the continuity ledger: a
#: `continuity` section and six additive `continuity_*` fields on each wall row. A reader of the
#: previous version sees a file it does not recognize rather than one it misreads, which is why
#: this moves even though every addition is backwards compatible.
STATE_VERSION = "ms-v0-state.v1-2"


@dataclass
class StreamState:
    """Per-stream connection bookkeeping, shared shape for depth and trades."""

    name: str
    connection_id: str | None = None
    connected: bool = False
    connects: int = 0
    reconnects: int = 0
    retries: int = 0
    frames: int = 0
    malformed: int = 0
    last_receive_ms: int | None = None
    last_mono_ns: int | None = None
    stale_events: int = 0
    stale: bool = False
    last_error: str | None = None

    def counters(self) -> dict[str, Any]:
        return {
            "connected": self.connected, "connection_id": self.connection_id,
            "connects": self.connects, "reconnects": self.reconnects, "retries": self.retries,
            "frames": self.frames, "malformed": self.malformed,
            "stale_events": self.stale_events, "stale": self.stale,
            "last_receive_ms": self.last_receive_ms, "last_error": self.last_error,
        }


@dataclass
class RefreshAttempt:
    """One staged voluntary refresh: its buffer, its second book, and how it ended.

    Nothing here is reachable from the live book until `Collector._swap_refresh` runs, which is
    the single place the two meet. Every other outcome discards this object and leaves the book
    serving exactly as it was.
    """

    reason: str
    requested_ms: int
    requested_ns: int
    #: The live book's state when the refresh was asked for, so continuity can be proved later.
    generation: int
    faults: tuple[tuple[str, int], ...]
    live_update_id: int | None
    state: str = REFRESH_BUFFERING
    #: Frames applied to the live book since the request, in applied order. Only applied frames
    #: are kept, so this *is* the live chain: duplicates and out-of-order frames the live book
    #: refused never enter it, and the replay cannot diverge from the book it has to match.
    frames: list[tuple[B.DepthFrame, int, int]] = field(default_factory=list)
    staging: B.DepthBook | None = None
    snapshot_update_id: int | None = None
    round_trip_ms: int | None = None
    replayed: int = 0
    discarded_old: int = 0
    buffered_at_snapshot: int = 0
    frames_after_snapshot: int = 0
    #: How the first frame after the snapshot attached to the staged book, once that is known.
    attachment: str | None = None
    outcome: str | None = None
    failure: str | None = None
    swapped_ms: int | None = None
    elapsed_ms: int | None = None
    #: Levels inside the intersection of the two known intervals where the staged book and the
    #: live book disagree. Published as evidence, not used as a gate: see `_swap_refresh`.
    divergence: dict[str, Any] | None = None

    def age_ms(self, mono_ns: int) -> int:
        return max(0, (mono_ns - self.requested_ns) // 1_000_000)

    def view(self) -> dict[str, Any]:
        # `trigger` rather than `reason`: `reason` is the telemetry vocabulary's own field and
        # these views are splatted into telemetry calls that set it to the event's reason.
        return {
            "trigger": self.reason, "state": self.state, "outcome": self.outcome,
            "failure": self.failure, "requested_ms": self.requested_ms,
            "generation_at_request": self.generation,
            "live_update_id_at_request": self.live_update_id,
            "snapshot_update_id": self.snapshot_update_id,
            "round_trip_ms": self.round_trip_ms,
            "buffered_at_snapshot": self.buffered_at_snapshot,
            "replayed_frames": self.replayed,
            "discarded_older_than_snapshot": self.discarded_old,
            "attachment": self.attachment,
            "frames_after_snapshot": self.frames_after_snapshot,
            "elapsed_ms": self.elapsed_ms,
            "swapped_ms": self.swapped_ms,
            "divergence": self.divergence,
        }


@dataclass
class Collector:
    """Everything the collector decides. No sockets, no sleeping, no ambient clock."""

    store: Store
    session: Session = field(default_factory=Session)
    depth: B.DepthBook = field(default_factory=B.DepthBook)
    tape: T.TradeTape = field(default_factory=T.TradeTape)
    flow: F.FlowWindows = field(default_factory=F.FlowWindows)
    wall: W.WallTracker = field(default_factory=W.WallTracker)
    depth_stream: StreamState = field(default_factory=lambda: StreamState(DEPTH_STREAM))
    trade_stream: StreamState = field(default_factory=lambda: StreamState(TRADE_STREAM))
    #: Where records go. The runner replaces this with a bounded queue put.
    sink: Callable[[dict[str, Any]], None] | None = None
    #: Set when the book needs a snapshot and none has been requested yet.
    snapshot_wanted: bool = True
    snapshot_in_flight: bool = False
    #: Set when a *usable* book should nonetheless be replaced: the coverage edge or the safety
    #: refresh. Kept separate from `snapshot_wanted` because the two mean opposite things about
    #: the current book, and because only this one may be refused on arrival.
    refresh_wanted: bool = False
    refresh_reason: str | None = None
    refresh_in_flight: bool = False
    #: The staged refresh in progress, if any. One at a time, always.
    refresh: "RefreshAttempt | None" = None
    #: Consecutive refreshes that did not install, and when the next attempt may be made.
    refresh_failures: int = 0
    refresh_retry_after_ns: int | None = None
    refreshes_applied: int = 0
    last_snapshot_ns: int | None = None
    last_coverage_refresh_ns: int | None = None
    coverage_refreshes: int = 0
    safety_refreshes: int = 0
    refreshes_rejected: int = 0
    #: The last generation transition this collector has already published the outcome of.
    #: Compared by identity, so a transition is reported exactly once.
    published_transition: dict[str, Any] | None = None
    #: Recent telemetry, for the compact state file. Bounded like every other buffer here.
    recent_telemetry: deque = field(
        default_factory=lambda: deque(maxlen=STATE_TELEMETRY_EVENTS))
    #: Bound on frames held while a snapshot is in flight.
    buffer_limit: int = DEPTH_QUEUE_MAX
    samples: int = 0
    #: The most recent derived payload, kept so the final state file carries the last known
    #: metrics instead of an empty book. An ended session's values are labelled stale by the
    #: session record; blanking them would throw away the last thing that was true.
    last_derived: dict[str, Any] | None = None
    stats_emitted: int = 0
    records_published: int = 0

    # ------------------------------------------------------------------ emitting

    def _publish(self, record: dict[str, Any]) -> None:
        self.records_published += 1
        if self.sink is None:
            self.store.write(record, now_ns=record["mono_ns"], now_ms=record["receive_ms"])
        else:
            self.sink(record)

    def emit(self, kind: str, payload: dict[str, Any], *, receive_ms: int | None = None,
             mono_ns: int | None = None, connection_id: str | None = None) -> None:
        self._publish(self.session.record(kind, payload, receive_ms=receive_ms, mono_ns=mono_ns,
                                          connection_id=connection_id))

    def telemetry(self, event: str, *, stream: str | None = None, receive_ms: int | None = None,
                  mono_ns: int | None = None, connection_id: str | None = None,
                  **detail: Any) -> None:
        self.emit("telemetry", {"event": event, "stream": stream, **detail},
                  receive_ms=receive_ms, mono_ns=mono_ns, connection_id=connection_id)
        self.recent_telemetry.append({"seq": self.session.seq, "event": event, "stream": stream,
                                      "receive_ms": receive_ms,
                                      "reason": detail.get("reason")})

    def write_session_record(self, *, config: dict[str, Any]) -> None:
        """The first record of every session: who, what contract, what settings, what reach."""
        self.emit("session", {
            "event": "START",
            "session_id": self.session.session_id,
            "started_ms": self.session.started_ms,
            "collector_version": COLLECTOR_VERSION,
            "contract": contract_identity(),
            "config": config,
            "safety": safety_view(),
            "recovery": self.store.recovery,
            "pid": os.getpid(),
        }, receive_ms=self.session.started_ms, mono_ns=self.session.started_ns)

    # ------------------------------------------------------------------ depth

    def on_depth_connect(self, connection_id: str, *, receive_ms: int, mono_ns: int) -> None:
        state = self.depth_stream
        if state.connects:
            state.reconnects += 1
        state.connects += 1
        state.connected = True
        state.stale = False
        state.connection_id = connection_id
        # A reconnect means frames went to nobody, so the book cannot be continued.
        self.depth.on_connect(connection_id)
        self.wall_interrupt(receive_ms=receive_ms, mono_ns=mono_ns, cause=B.RECONNECT)
        self.abandon_refresh(ABANDON_FAULT, receive_ms=receive_ms, mono_ns=mono_ns)
        self.snapshot_wanted = True
        self.snapshot_in_flight = False
        self.telemetry(CONNECT, stream=DEPTH_STREAM, connection_id=connection_id,
                       receive_ms=receive_ms, mono_ns=mono_ns, reconnect=state.connects > 1)

    def on_depth_disconnect(self, reason: str, *, receive_ms: int, mono_ns: int) -> None:
        state = self.depth_stream
        state.connected = False
        state.last_error = reason
        self.depth.invalidate(B.RECONNECT)
        self.snapshot_wanted = True
        self.snapshot_in_flight = False
        self.abandon_refresh(ABANDON_FAULT, receive_ms=receive_ms, mono_ns=mono_ns)
        self.telemetry(DISCONNECT, stream=DEPTH_STREAM, connection_id=state.connection_id,
                       receive_ms=receive_ms, mono_ns=mono_ns, reason=reason)

    def on_depth_frame(self, message: dict[str, Any], *, receive_ms: int, mono_ns: int) -> str:
        """Record the raw frame, then apply it under the futures continuity rules."""
        state = self.depth_stream
        state.frames += 1
        state.last_receive_ms = receive_ms
        state.last_mono_ns = mono_ns
        if state.stale:
            state.stale = False
        # Raw first and unmodified, including a frame that is about to be discarded: raw plus
        # snapshots plus the envelope sequence are the replay authority.
        self.emit("raw_depth", message, receive_ms=receive_ms, mono_ns=mono_ns,
                  connection_id=state.connection_id)
        try:
            frame = B.DepthFrame.parse(message)
        except (KeyError, TypeError, ValueError) as exc:
            state.malformed += 1
            self.telemetry("malformed", stream=DEPTH_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, error=f"{type(exc).__name__}: {exc}")
            return "MALFORMED"

        if self.depth.state != B.SYNCED:
            outcome = self.depth.buffer_frame(frame, receive_ms, mono_ns,
                                              limit=self.buffer_limit)
            if outcome == B.GAP:
                self.telemetry(OVERFLOW, stream=DEPTH_STREAM, receive_ms=receive_ms,
                               mono_ns=mono_ns, what="depth_prefix_buffer",
                               limit=self.buffer_limit)
            self.snapshot_wanted = True
            return outcome

        outcome = self.depth.apply_delta(frame, receive_ms, mono_ns)
        if outcome == B.GAP:
            self.snapshot_wanted = True
            self.wall_interrupt(receive_ms=receive_ms, mono_ns=mono_ns)
            # A gap on the live chain is a HARD fault, and a staged refresh built on that chain
            # cannot be completed across it whatever the staged book looks like.
            self.abandon_refresh(ABANDON_FAULT, receive_ms=receive_ms, mono_ns=mono_ns)
            self.telemetry(GAP, stream=DEPTH_STREAM, receive_ms=receive_ms, mono_ns=mono_ns,
                           reason=self.depth.last_invalidation, first_update_id=frame.first_update_id,
                           last_update_id=frame.last_update_id,
                           previous_update_id=frame.previous_update_id)
        elif outcome == B.DISCARDED_DUPLICATE:
            self.telemetry(DUPLICATE, stream=DEPTH_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, last_update_id=frame.last_update_id)
        elif outcome == B.APPLIED:
            # Only applied frames, and only after the live book has taken them: the buffer is the
            # live chain, not the arrival order.
            self.record_refresh_frame(frame, receive_ms, mono_ns)
        return outcome

    def mark_snapshot_requested(self, *, receive_ms: int, mono_ns: int) -> None:
        self.snapshot_in_flight = True
        # A voluntary refresh is only a refresh while the book it would replace is still usable.
        # If the book has since been invalidated, this request is the ordinary recovery path and
        # must not be refusable on arrival.
        self.refresh_in_flight = bool(self.refresh_wanted and self.refresh is not None
                                      and self.depth.state == B.SYNCED)
        reason = self.refresh_reason if self.refresh_in_flight else None
        if self.refresh is not None and not self.refresh_in_flight:
            self.abandon_refresh(ABANDON_BOOK_UNUSABLE, receive_ms=receive_ms, mono_ns=mono_ns)
        self.snapshot_wanted = False
        self.refresh_wanted = False
        self.telemetry(SNAPSHOT_REQUEST, stream=DEPTH_STREAM, receive_ms=receive_ms,
                       mono_ns=mono_ns, url=DEPTH_REST_URL, limit=DEPTH_REST_LIMIT,
                       generation=self.depth.generation, voluntary=self.refresh_in_flight,
                       reason=reason)

    def on_snapshot_failed(self, reason: str, *, receive_ms: int, mono_ns: int) -> None:
        self.snapshot_in_flight = False
        # A failed *voluntary* refresh leaves a usable book in place, so it must not set
        # `snapshot_wanted`: that would ask the recovery path to replace a book that is fine.
        # It is simply dropped, and the next policy check will ask again if it still applies.
        if self.refresh_in_flight:
            self.refresh_in_flight = False
            self.telemetry(SNAPSHOT_FAILED, stream=DEPTH_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, reason=reason, voluntary=True)
            self.abandon_refresh(ABANDON_SNAPSHOT_FAILED, receive_ms=receive_ms,
                                 mono_ns=mono_ns)
            return
        self.snapshot_wanted = True
        self.telemetry(SNAPSHOT_FAILED, stream=DEPTH_STREAM, receive_ms=receive_ms,
                       mono_ns=mono_ns, reason=reason)

    def _book_window(self) -> W.BookWindow:
        """The book as a continuity proof needs to see it, with the levels copied.

        The copy is the point: `apply_snapshot` replaces the level maps and the deltas that
        follow keep mutating them, so a proof built on live references would compare the book
        against itself. Two thousand entries once per refresh, against a cooldown of 300 s.
        """
        return W.BookWindow(
            generation=self.depth.generation,
            synced=self.depth.state == B.SYNCED,
            first_delta_applied=self.depth.first_delta_applied,
            last_update_id=self.depth.last_update_id,
            known_low=self.depth.known_low, known_high=self.depth.known_high,
            mid=self.depth.mid(),
            levels={"BID": dict(self.depth.bids), "ASK": dict(self.depth.asks)},
            # Compared as a whole, so a fault counter added to the book later is covered by the
            # continuity gate without anybody having to remember to list it here.
            faults=self._fault_counters(),
            invalidation=self.depth.last_invalidation)

    def _classify_install(self, before: W.BookWindow, payload: dict[str, Any], *,
                          voluntary: bool, reason: str | None, request_mono_ns: int,
                          mono_ns: int) -> W.SoftRefreshProof:
        """Classify the install that just happened, hand the verdict to the wall ledger.

        Called for **every** successful install, not only the voluntary ones, because "why was
        this HARD" is the question an operator asks when a wall's history disappears, and the
        answer has to exist before it is asked.
        """
        snapshot_update_id = None
        try:
            snapshot_update_id = int(payload["lastUpdateId"])
        except (KeyError, TypeError, ValueError):
            snapshot_update_id = None
        proof = W.classify_refresh(
            before, self._book_window(), voluntary=voluntary, reason=reason,
            elapsed_ms=max(0, (mono_ns - request_mono_ns) // 1_000_000),
            snapshot_update_id=snapshot_update_id)
        self.wall.note_refresh(proof)
        return proof

    def on_snapshot(self, payload: dict[str, Any], *, receive_ms: int, mono_ns: int,
                    request_ms: int, request_mono_ns: int) -> str:
        """Store the raw snapshot with its request and receive times, then install it."""
        self.snapshot_in_flight = False
        buffered = len(self.depth.buffer)
        self.emit("snapshot", {
            "request_ms": request_ms,
            "request_mono_ns": request_mono_ns,
            "receive_ms": receive_ms,
            "round_trip_ms": max(0, (mono_ns - request_mono_ns) // 1_000_000),
            "url": DEPTH_REST_URL,
            "limit": DEPTH_REST_LIMIT,
            "buffered_frames": buffered,
            "response": payload,
        }, receive_ms=receive_ms, mono_ns=mono_ns,
            connection_id=self.depth_stream.connection_id)

        if self.refresh_in_flight and self.refresh is not None:
            # A voluntary refresh never hands the snapshot to the live book. It builds a second
            # one, replays the round trip onto it, and swaps only once the two stand at the same
            # update id. If any of that fails the live book has not been touched, so there is
            # nothing to recover from and nothing to report but the attempt.
            self.refresh_in_flight = False
            self.install_refresh(payload, receive_ms=receive_ms, mono_ns=mono_ns,
                                 request_mono_ns=request_mono_ns)
            if self.depth.state == B.SYNCED:
                return self.depth.state
            # The book faulted while the read was in flight; the refresh was abandoned above and
            # this snapshot becomes the recovery the invalidation asked for.
        self.refresh_in_flight = False

        # Taken before `apply_snapshot` replaces the levels and the bounds.
        before = self._book_window()
        state = self.depth.apply_snapshot(payload, receive_ms, mono_ns)
        if state != B.SYNCED:
            self.snapshot_wanted = True
            self.telemetry(GAP, stream=DEPTH_STREAM, receive_ms=receive_ms, mono_ns=mono_ns,
                           reason=self.depth.last_invalidation, during="snapshot_replay",
                           buffered_frames=buffered)
            return state
        # The book is synchronized, so nothing is wanted. Buffering a frame sets this flag, and
        # a successful snapshot is the only thing that may clear it; forgetting to do so here is
        # an endless resnapshot loop that also throws away every replayed delta.
        self.snapshot_wanted = False
        self.last_snapshot_ns = mono_ns
        # Reached only by the recovery path: a voluntary refresh returns above, swapped or
        # abandoned, and never arrives here. So this install is never SOFT.
        proof = self._classify_install(before, payload, voluntary=False, reason=None,
                                       request_mono_ns=request_mono_ns, mono_ns=mono_ns)
        self.telemetry(RESYNC, stream=DEPTH_STREAM, receive_ms=receive_ms, mono_ns=mono_ns,
                       generation=self.depth.generation, buffered_frames=buffered,
                       snapshot_update_id=self.depth.snapshot_update_id,
                       last_update_id=self.depth.last_update_id,
                       refresh_type=proof.refresh_type,
                       continuity_reason=proof.continuity_reason)
        # The classification, with its gates and its overlap measurement, as its own record. The
        # counts it will produce are not known yet: they belong to the first sample of the new
        # generation, which publishes them under the same event.
        classified = proof.view()
        # `reason` is the telemetry vocabulary's own field and the one the recent-telemetry ring
        # shows, so it carries the continuity verdict; the trigger keeps its own name.
        classified["refresh_trigger"] = classified.pop("reason")
        self.telemetry(WALL_CONTINUITY, stream=DEPTH_STREAM, receive_ms=receive_ms,
                       mono_ns=mono_ns, reason=proof.continuity_reason, phase="CLASSIFIED",
                       **classified)
        # A synchronized checkpoint accelerates auditing; raw remains the authority.
        self.emit("checkpoint", self.depth.checkpoint(), receive_ms=receive_ms, mono_ns=mono_ns,
                  connection_id=self.depth_stream.connection_id)
        return state

    # ------------------------------------------------------------------ trades

    def on_trade_connect(self, connection_id: str, *, receive_ms: int, mono_ns: int) -> None:
        state = self.trade_stream
        reconnect = state.connects > 0
        if reconnect:
            state.reconnects += 1
        state.connects += 1
        state.connected = True
        state.stale = False
        state.connection_id = connection_id
        # Coverage restarts here: whatever traded while the socket was down was not seen. The
        # tape's silence clock restarts too, so the new socket is not judged stale on the age of
        # a trade that belonged to the previous one.
        self.flow.start_coverage(mono_ns, F.INTERRUPTION_RECONNECT if reconnect else F.WARMUP)
        self.tape.on_connect()
        state.stale = False
        self.telemetry(CONNECT, stream=TRADE_STREAM, connection_id=connection_id,
                       receive_ms=receive_ms, mono_ns=mono_ns, reconnect=reconnect)

    def on_trade_disconnect(self, reason: str, *, receive_ms: int, mono_ns: int) -> None:
        state = self.trade_stream
        state.connected = False
        state.last_error = reason
        self.flow.lose_coverage(F.INTERRUPTION_RECONNECT)
        self.telemetry(DISCONNECT, stream=TRADE_STREAM, connection_id=state.connection_id,
                       receive_ms=receive_ms, mono_ns=mono_ns, reason=reason)

    def on_trade_frame(self, message: dict[str, Any], *, receive_ms: int, mono_ns: int) -> str:
        state = self.trade_stream
        state.frames += 1
        state.last_receive_ms = receive_ms
        state.last_mono_ns = mono_ns
        state.stale = False
        self.emit("raw_trade", message, receive_ms=receive_ms, mono_ns=mono_ns,
                  connection_id=state.connection_id)
        try:
            trade = T.Trade.parse(message, receive_ms, mono_ns)
        except (KeyError, TypeError, ValueError) as exc:
            state.malformed += 1
            self.tape.malformed += 1
            self.telemetry("malformed", stream=TRADE_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, error=f"{type(exc).__name__}: {exc}")
            return "MALFORMED"

        before_out_of_order = self.tape.out_of_order_event_time
        outcome = self.tape.on_trade(trade)
        if outcome == T.DUPLICATE:
            self.telemetry(DUPLICATE, stream=TRADE_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, trade_id=trade.trade_id,
                           high_water_id=self.tape.high_water_id)
            return outcome
        if outcome == T.ID_JUMP:
            # Volume is still real and is still counted; what is lost is the claim that the
            # window saw everything, so coverage restarts from here.
            self.flow.interrupt(mono_ns, F.INTERRUPTION_ID_JUMP)
            self.telemetry(ID_JUMP, stream=TRADE_STREAM, receive_ms=receive_ms, mono_ns=mono_ns,
                           trade_id=trade.trade_id, high_water_id=self.tape.high_water_id)
        if self.tape.out_of_order_event_time > before_out_of_order:
            self.flow.interrupt(mono_ns, F.INTERRUPTION_OUT_OF_ORDER)
            self.telemetry(OUT_OF_ORDER, stream=TRADE_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, event_ms=trade.event_ms)

        overflows_before = self.flow.overflows
        self.flow.add(trade)
        if self.flow.overflows > overflows_before:
            self.telemetry(OVERFLOW, stream=TRADE_STREAM, receive_ms=receive_ms, mono_ns=mono_ns,
                           what="flow_tape", limit=self.flow.max_records)
        self.emit("trade", trade.payload(), receive_ms=receive_ms, mono_ns=mono_ns,
                  connection_id=state.connection_id)
        return outcome

    # ------------------------------------------------------------------ sampling

    def wall_interrupt(self, *, receive_ms: int, mono_ns: int,
                       cause: str | None = None) -> None:
        """End candidate continuity without claiming to have seen the candidates end.

        `cause` is the book's own invalidation word, carried into the continuity ledger so that
        a wall history ending has a named reason rather than only a count.
        """
        for payload in self.wall.shutdown(cause=cause or self.depth.last_invalidation):
            self.emit("wall", payload, receive_ms=receive_ms, mono_ns=mono_ns)

    def check_staleness(self, *, at_ns: int, at_ms: int) -> None:
        """Turn silence into an explicit state. A stale book is invalidated and resnapshotted."""
        depth_age = self.depth.age_ms(at_ns)
        if (self.depth.state == B.SYNCED and depth_age is not None
                and depth_age > DEPTH_STALE_MS):
            self.depth_stream.stale = True
            self.depth_stream.stale_events += 1
            self.depth.invalidate(B.STALE)
            self.snapshot_wanted = True
            self.wall_interrupt(receive_ms=at_ms, mono_ns=at_ns, cause=B.STALE)
            self.abandon_refresh(ABANDON_FAULT, receive_ms=at_ms, mono_ns=at_ns)
            self.telemetry(STALE, stream=DEPTH_STREAM, receive_ms=at_ms, mono_ns=at_ns,
                           age_ms=depth_age, threshold_ms=DEPTH_STALE_MS)

        trade_age = self.tape.age_ms(at_ns)
        if (trade_age is not None and trade_age > TRADE_STALE_MS
                and not self.trade_stream.stale):
            self.trade_stream.stale = True
            self.trade_stream.stale_events += 1
            self.flow.interrupt(at_ns, F.INTERRUPTION_STALE)
            self.telemetry(STALE, stream=TRADE_STREAM, receive_ms=at_ms, mono_ns=at_ns,
                           age_ms=trade_age, threshold_ms=TRADE_STALE_MS)

    def coverage_margin_bps(self) -> Decimal | None:
        """How much mid may still move before the protected band leaves the known interval.

        The snapshot's bounds are **fixed prices**, so this margin erodes one-for-one with mid
        and is the quantity the coverage trigger watches. None when there is no usable book, which
        is a different situation and is handled by the fault path, not by this policy.
        """
        if self.depth.state != B.SYNCED:
            return None
        mid = self.depth.mid()
        low, high = self.depth.known_low, self.depth.known_high
        if mid is None or mid <= 0 or low is None or high is None:
            return None
        reach = min((mid - low) / mid, (high - mid) / mid) * Decimal(10_000)
        return reach - PROTECTED_BAND_BPS

    def check_resnapshot_policy(self, *, at_ns: int, at_ms: int) -> str | None:
        """Ask for a voluntary snapshot when one is due. Returns the reason, or None.

        Two triggers, both conservative, and neither of them a timer that fires regardless of
        state. A fault - gap, overflow, reconnect, stale, crossed book - is handled where it
        happens and is not reconsidered here; by the time this runs such a book is already
        UNSYNCED and `snapshot_wanted` is already set.
        """
        if (self.depth.state != B.SYNCED or self.snapshot_in_flight or self.refresh_wanted
                or self.refresh is not None):
            # One staged refresh at a time. A second would buffer the same frames into a second
            # chain and race the first to the swap, and whichever lost would abandon a book the
            # other had already replaced.
            return None
        if (self.refresh_retry_after_ns is not None and at_ns < self.refresh_retry_after_ns):
            # Backing off after a refresh that did not install. Short, because nothing was lost.
            return None

        margin = self.coverage_margin_bps()
        if margin is not None and margin < COVERAGE_MARGIN_TRIGGER_BPS:
            elapsed = (None if self.last_coverage_refresh_ns is None
                       else (at_ns - self.last_coverage_refresh_ns) / 1e9)
            if elapsed is None or elapsed >= COVERAGE_REFRESH_COOLDOWN_S:
                # The cooldown clock is **not** started here. V1.1 started it on the request, so
                # an attempt that never installed still cost five minutes of unguarded band -
                # measured, twice in 23 minutes, while the margin went negative. It is started by
                # the swap instead, which is the event the cooldown is about.
                self.coverage_refreshes += 1
                self._want_refresh(REFRESH_COVERAGE_EDGE, at_ns=at_ns, at_ms=at_ms,
                                   margin_bps=decimal_out(margin.quantize(Decimal("0.01"))),
                                   trigger_bps=str(COVERAGE_MARGIN_TRIGGER_BPS),
                                   protected_band_bps=str(PROTECTED_BAND_BPS))
                return REFRESH_COVERAGE_EDGE
            # Inside the cooldown the band is left reporting PARTIAL, which is what it is.
            return None

        since = (None if self.last_snapshot_ns is None
                 else (at_ns - self.last_snapshot_ns) / 1e9)
        if since is not None and since >= SAFETY_REFRESH_S:
            self.safety_refreshes += 1
            self._want_refresh(REFRESH_SAFETY, at_ns=at_ns, at_ms=at_ms,
                               snapshot_age_s=round(since, 1),
                               interval_s=SAFETY_REFRESH_S)
            return REFRESH_SAFETY
        return None

    def _want_refresh(self, reason: str, *, at_ns: int, at_ms: int, **detail: Any) -> None:
        self.refresh_wanted = True
        self.refresh_reason = reason
        # Buffering starts here rather than when the request goes out, so no frame between the
        # decision and the socket write can be missing from the chain the replay has to match.
        self.refresh = RefreshAttempt(
            reason=reason, requested_ms=at_ms, requested_ns=at_ns,
            generation=self.depth.generation, faults=self._fault_counters(),
            live_update_id=self.depth.last_update_id)
        self.telemetry(reason, stream=DEPTH_STREAM, receive_ms=at_ms, mono_ns=at_ns,
                       reason=reason, generation=self.depth.generation, **detail)

    # ------------------------------------------------------------------ staged refresh

    def _fault_counters(self) -> tuple[tuple[str, int], ...]:
        """Every counter that must not move while a refresh is being staged."""
        return tuple(sorted({
            "gaps": self.depth.gaps,
            "snapshots_rejected": self.depth.snapshots_rejected,
            "buffered_dropped": self.depth.buffered_dropped,
            "connects": self.depth_stream.connects,
            "reconnects": self.depth_stream.reconnects,
            "stale_events": self.depth_stream.stale_events,
        }.items()))

    def abandon_refresh(self, failure: str, *, receive_ms: int, mono_ns: int) -> None:
        """Drop a staged refresh. The live book is untouched by construction.

        Every exit from a refresh that is not a swap comes through here, so "the book kept
        serving" is a property of the control flow rather than a promise: the only code that
        writes to the live book on behalf of a refresh is `_swap_refresh`.
        """
        attempt = self.refresh
        if attempt is None:
            return
        attempt.state = REFRESH_DONE
        attempt.outcome = "ABANDONED"
        attempt.failure = failure
        attempt.elapsed_ms = attempt.age_ms(mono_ns)
        self.refresh = None
        self.refresh_wanted = False
        self.refresh_in_flight = False
        self.refreshes_rejected += 1
        self.refresh_failures += 1
        # A failure consumes no cooldown. The 300 s floor exists to bound how often a *successful*
        # refresh destroys wall observation, and an attempt that changed nothing destroyed
        # nothing. It takes the shorter backoff instead, so the protected band is not left
        # unguarded for five minutes by an attempt that never touched the book.
        self.refresh_retry_after_ns = mono_ns + int(REFRESH_RETRY_BACKOFF_S * 1e9)
        self.telemetry(REFRESH_REJECTED, stream=DEPTH_STREAM, receive_ms=receive_ms,
                       mono_ns=mono_ns, reason=failure, consecutive_failures=self.refresh_failures,
                       retry_backoff_s=REFRESH_RETRY_BACKOFF_S, cooldown_consumed=False,
                       **attempt.view())
        if self.refresh_failures % REFRESH_STORM_EVERY == 0:
            # A refresh that can never succeed is a pattern, and a pattern has to be visible as
            # one rather than as a trickle of single failures nobody adds up.
            self.telemetry(REFRESH_STORM, stream=DEPTH_STREAM, receive_ms=receive_ms,
                           mono_ns=mono_ns, reason=failure,
                           consecutive_failures=self.refresh_failures,
                           since_ms=receive_ms - attempt.requested_ms,
                           backoff_s=REFRESH_RETRY_BACKOFF_S)

    def record_refresh_frame(self, frame: B.DepthFrame, receive_ms: int, mono_ns: int) -> None:
        """Keep a frame the live book just applied, and let the staged book see it too."""
        attempt = self.refresh
        if attempt is None:
            return
        if attempt.state == REFRESH_BUFFERING:
            if len(attempt.frames) >= REFRESH_BUFFER_MAX:
                self.abandon_refresh(ABANDON_BUFFER_OVERFLOW, receive_ms=receive_ms,
                                     mono_ns=mono_ns)
                return
            attempt.frames.append((frame, receive_ms, mono_ns))
            return
        if attempt.state == REFRESH_REPLAYING:
            attempt.frames_after_snapshot += 1
            self._feed_staging(frame, receive_ms=receive_ms, mono_ns=mono_ns)
            self._advance_refresh(receive_ms=receive_ms, mono_ns=mono_ns)

    def _attach_first_frame(self, frame: B.DepthFrame, *, receive_ms: int,
                            mono_ns: int) -> bool:
        """Decide how the first frame after the snapshot attaches to the staged book.

        Two ways a frame can provably be the next event after `lastUpdateId`, and the live book's
        own rule only knows one of them:

        * it **straddles** - `U <= lastUpdateId <= u` - which is the futures rule the contract
          fixes and `apply_delta` already implements;
        * it is the **immediate successor** - `pu == lastUpdateId` - which happens whenever the
          snapshot's id lands exactly on a frame boundary. The previous frame ended at the
          snapshot's id, so this frame is the very next event and nothing is missing. The live
          book never meets this case, because it only ever attaches to a snapshot it asked for
          mid-stream; a staged replay meets it as soon as the snapshot id is a boundary, and
          measured on synthetic chains it is not rare.

        Anything else is a hole between the snapshot and the first frame held, and the refresh is
        abandoned. `apply_delta` is still what applies the frame either way: the only thing done
        here is choosing which of its two rules the staged book should be asked to use.
        """
        attempt = self.refresh
        assert attempt is not None and attempt.staging is not None
        staging = attempt.staging
        snapshot_id = staging.snapshot_update_id
        assert snapshot_id is not None
        if frame.first_update_id <= snapshot_id <= frame.last_update_id:
            attempt.attachment = ATTACH_STRADDLE
            return True
        if frame.previous_update_id is not None and frame.previous_update_id == snapshot_id:
            staging.last_update_id = snapshot_id
            staging.first_delta_applied = True
            attempt.attachment = ATTACH_SUCCESSOR
            return True
        self.abandon_refresh(ABANDON_REPLAY_GAP, receive_ms=receive_ms, mono_ns=mono_ns)
        return False

    def _feed_staging(self, frame: B.DepthFrame, *, receive_ms: int, mono_ns: int) -> bool:
        """One frame into the staged book. A gap there abandons the refresh, never the book."""
        attempt = self.refresh
        if attempt is None or attempt.staging is None:
            return False
        snapshot_id = attempt.staging.snapshot_update_id
        if (attempt.attachment is None and snapshot_id is not None
                and frame.last_update_id >= snapshot_id):
            if not self._attach_first_frame(frame, receive_ms=receive_ms, mono_ns=mono_ns):
                return False
        outcome = attempt.staging.apply_delta(frame, receive_ms, mono_ns)
        if outcome == B.GAP:
            self.abandon_refresh(ABANDON_REPLAY_GAP, receive_ms=receive_ms, mono_ns=mono_ns)
            return False
        if outcome == B.APPLIED:
            attempt.replayed += 1
        elif outcome == B.DISCARDED_OLD:
            attempt.discarded_old += 1
        return True

    def install_refresh(self, payload: dict[str, Any], *, receive_ms: int, mono_ns: int,
                        request_mono_ns: int) -> None:
        """Build the staged book from the REST snapshot and replay the round trip onto it.

        The live book is not a parameter of any of this. It is read - for the chain position the
        staged book has to reach - and otherwise left alone.
        """
        attempt = self.refresh
        if attempt is None:
            return
        attempt.round_trip_ms = max(0, (mono_ns - request_mono_ns) // 1_000_000)
        if self.depth.state != B.SYNCED or self.depth.last_update_id is None:
            # The book stopped being usable while the read was in flight, so this is no longer a
            # refresh of a healthy book and the caller installs the snapshot the recovery path
            # asked for instead.
            self.abandon_refresh(ABANDON_BOOK_UNUSABLE, receive_ms=receive_ms, mono_ns=mono_ns)
            return
        staging = B.DepthBook()
        if staging.apply_snapshot(payload, receive_ms, mono_ns) != B.SYNCED:
            self.abandon_refresh(ABANDON_SNAPSHOT_REJECTED, receive_ms=receive_ms,
                                 mono_ns=mono_ns)
            return
        attempt.staging = staging
        attempt.snapshot_update_id = staging.snapshot_update_id
        attempt.state = REFRESH_REPLAYING
        attempt.buffered_at_snapshot = len(attempt.frames)
        for frame, frame_ms, frame_ns in attempt.frames:
            # `apply_delta` applies the futures rules itself: a frame wholly older than the
            # snapshot is discarded, the one straddling `lastUpdateId` is the first delta, and
            # the rest chain on `pu`. Replaying the applied chain through the same code the live
            # book used is what makes the two books comparable at all.
            if not self._feed_staging(frame, receive_ms=frame_ms, mono_ns=frame_ns):
                return
        attempt.frames.clear()
        self._advance_refresh(receive_ms=receive_ms, mono_ns=mono_ns)

    def _advance_refresh(self, *, receive_ms: int, mono_ns: int) -> None:
        """Swap when the staged book stands exactly where the live one does, or keep waiting."""
        attempt = self.refresh
        if attempt is None or attempt.state != REFRESH_REPLAYING or attempt.staging is None:
            return
        if attempt.age_ms(mono_ns) > REFRESH_DEADLINE_MS:
            self.abandon_refresh(ABANDON_DEADLINE, receive_ms=receive_ms, mono_ns=mono_ns)
            return
        staged, live = attempt.staging.last_update_id, self.depth.last_update_id
        if staged is None or live is None or attempt.staging.state != B.SYNCED:
            self.abandon_refresh(ABANDON_BOOK_UNUSABLE, receive_ms=receive_ms, mono_ns=mono_ns)
            return
        if staged == live:
            self._swap_refresh(receive_ms=receive_ms, mono_ns=mono_ns)
            return
        if staged < live:
            # The staged book cannot reach the live chain from here: the buffer did not hold
            # every frame between the snapshot and now, so replaying it leaves a hole. There is
            # nothing to salvage and nothing has been damaged.
            self.abandon_refresh(ABANDON_REPLAY_INCOMPLETE, receive_ms=receive_ms,
                                 mono_ns=mono_ns)
            return
        # The snapshot was taken ahead of the live chain, which is the normal case: the stream
        # catches up within a frame or two and the frame that straddles the snapshot id lands
        # both books on the same number.

    def _swap_refresh(self, *, receive_ms: int, mono_ns: int) -> None:
        """Replace the live book's levels and bounds. Ids, freshness and continuity are kept."""
        attempt = self.refresh
        assert attempt is not None and attempt.staging is not None
        staging = attempt.staging
        attempt.divergence = self._replay_divergence(staging)
        before = self._book_window()

        # The only write to the live book any refresh performs. `last_update_id` is deliberately
        # not assigned: it is already equal, and saying so in code is weaker than not touching it.
        self.depth.bids = staging.bids
        self.depth.asks = staging.asks
        self.depth.known_low = staging.known_low
        self.depth.known_high = staging.known_high
        self.depth.snapshot_update_id = staging.snapshot_update_id
        self.depth.generation += 1
        self.depth.resyncs += 1
        self.depth.last_invalidation = None
        # The chain is unbroken across the swap, so the next frame is an ordinary `pu` link and
        # not a first delta. Leaving this False would make the book demand a straddle it has
        # already had and take a gap on a perfectly good frame.
        self.depth.first_delta_applied = True

        attempt.state = REFRESH_DONE
        attempt.outcome = "APPLIED"
        attempt.swapped_ms = receive_ms
        attempt.elapsed_ms = attempt.age_ms(mono_ns)
        self.refresh = None
        self.refresh_in_flight = False
        self.refresh_wanted = False
        self.refresh_failures = 0
        self.refresh_retry_after_ns = None
        self.last_snapshot_ns = mono_ns
        # A refresh that installed is the one that consumes the cooldown, because it is the one
        # that cost a generation.
        self.last_coverage_refresh_ns = (mono_ns if attempt.reason == REFRESH_COVERAGE_EDGE
                                         else self.last_coverage_refresh_ns)
        self.refreshes_applied += 1

        proof = W.classify_refresh(
            before, self._book_window(), voluntary=True, reason=attempt.reason,
            elapsed_ms=attempt.round_trip_ms, snapshot_update_id=attempt.snapshot_update_id,
            replayed_frames=attempt.replayed, chain_preserved=True)
        self.wall.note_refresh(proof)
        self.telemetry(REFRESH_APPLIED, stream=DEPTH_STREAM, receive_ms=receive_ms,
                       mono_ns=mono_ns, reason=attempt.reason,
                       refresh_type=proof.refresh_type,
                       continuity_reason=proof.continuity_reason,
                       generation=self.depth.generation, **attempt.view())
        self.telemetry(RESYNC, stream=DEPTH_STREAM, receive_ms=receive_ms, mono_ns=mono_ns,
                       generation=self.depth.generation, buffered_frames=0,
                       snapshot_update_id=self.depth.snapshot_update_id,
                       last_update_id=self.depth.last_update_id,
                       refresh_type=proof.refresh_type, staged=True,
                       continuity_reason=proof.continuity_reason)
        classified = proof.view()
        classified["refresh_trigger"] = classified.pop("reason")
        self.telemetry(WALL_CONTINUITY, stream=DEPTH_STREAM, receive_ms=receive_ms,
                       mono_ns=mono_ns, reason=proof.continuity_reason, phase="CLASSIFIED",
                       **classified)
        self.emit("checkpoint", self.depth.checkpoint(), receive_ms=receive_ms, mono_ns=mono_ns,
                  connection_id=self.depth_stream.connection_id)

    def _replay_divergence(self, staging: B.DepthBook) -> dict[str, Any]:
        """Where the two books disagree inside the interval they both claim to know.

        Both stand at the same update id and both applied the same frames, so inside the
        intersection they are two independent answers to the same question and ought to be the
        same answer. This is published rather than gated on: a disagreement is evidence about the
        exchange's snapshot or about this collector, and turning a number nobody has characterised
        yet into a refusal would be the kind of threshold the rest of this work refuses to invent.
        """
        bounds = (self.depth.known_low, self.depth.known_high,
                  staging.known_low, staging.known_high)
        report: dict[str, Any] = {"compared": 0, "identical": 0, "differing": 0,
                                  "only_live": 0, "only_staged": 0, "overlap_low": None,
                                  "overlap_high": None}
        if any(bound is None for bound in bounds):
            return report
        low = max(self.depth.known_low, staging.known_low)      # type: ignore[arg-type]
        high = min(self.depth.known_high, staging.known_high)   # type: ignore[arg-type]
        report["overlap_low"], report["overlap_high"] = decimal_out(low), decimal_out(high)
        if high <= low:
            return report
        for live_side, staged_side in ((self.depth.bids, staging.bids),
                                       (self.depth.asks, staging.asks)):
            live_levels = {price: qty for price, qty in live_side.items() if low <= price <= high}
            staged_levels = {price: qty for price, qty in staged_side.items()
                             if low <= price <= high}
            for price, qty in live_levels.items():
                if price not in staged_levels:
                    report["only_live"] += 1
                    continue
                report["compared"] += 1
                if staged_levels[price] == qty:
                    report["identical"] += 1
                else:
                    report["differing"] += 1
            report["only_staged"] += sum(1 for price in staged_levels if price not in live_levels)
        return report

    def check_refresh_deadline(self, *, at_ns: int, at_ms: int) -> None:
        """A refresh that nothing is advancing still has to end. Called once a sample.

        The staged book is normally driven forward by arriving frames; on a silent socket there
        are none, and without this the attempt would sit holding a buffer until the stream came
        back and then try to replay across the silence.
        """
        if self.refresh is not None and self.refresh.age_ms(at_ns) > REFRESH_DEADLINE_MS:
            self.abandon_refresh(ABANDON_DEADLINE, receive_ms=at_ms, mono_ns=at_ns)

    def resnapshot_view(self, *, at_ns: int) -> dict[str, Any]:
        """The policy and what it has done. Published so the screen can state the policy."""
        margin = self.coverage_margin_bps()
        return {
            "policy": "FAULT_IMMEDIATE_PLUS_COVERAGE_EDGE_PLUS_HOURLY_SAFETY",
            "install": "STAGED_BUFFER_AND_REPLAY_ATOMIC_SWAP",
            "refreshes_applied": self.refreshes_applied,
            "refresh_failures_consecutive": self.refresh_failures,
            "refresh_retry_backoff_s": REFRESH_RETRY_BACKOFF_S,
            "refresh_retry_in_s": (None if self.refresh_retry_after_ns is None else max(
                0.0, round((self.refresh_retry_after_ns - at_ns) / 1e9, 1))),
            "refresh_deadline_ms": REFRESH_DEADLINE_MS,
            "refresh_in_progress": None if self.refresh is None else self.refresh.view(),
            "failed_refresh_consumes_cooldown": False,
            "fixed_interval_polling": False,
            "protected_band_bps": str(PROTECTED_BAND_BPS),
            "coverage_margin_bps": (None if margin is None
                                    else decimal_out(margin.quantize(Decimal("0.01")))),
            "coverage_trigger_bps": str(COVERAGE_MARGIN_TRIGGER_BPS),
            "coverage_cooldown_s": COVERAGE_REFRESH_COOLDOWN_S,
            "safety_refresh_s": SAFETY_REFRESH_S,
            "snapshot_age_s": (None if self.last_snapshot_ns is None
                               else round((at_ns - self.last_snapshot_ns) / 1e9, 1)),
            "coverage_cooldown_remaining_s": (
                None if self.last_coverage_refresh_ns is None else max(
                    0.0, round(COVERAGE_REFRESH_COOLDOWN_S
                               - (at_ns - self.last_coverage_refresh_ns) / 1e9, 1))),
            "coverage_refreshes": self.coverage_refreshes,
            "safety_refreshes": self.safety_refreshes,
            "refreshes_rejected": self.refreshes_rejected,
            "resyncs": self.depth.resyncs,
            "generation": self.depth.generation,
            "pending_reason": self.refresh_reason if self.refresh_wanted else None,
            "soft_refreshes": self.wall.soft_refreshes,
            "hard_transitions": self.wall.hard_transitions,
            "cost_note": (
                "a resnapshot increments the book generation, which ends every wall candidate as "
                "UNKNOWN in the journal; the frequency is bounded by that loss, not by API "
                "weight. A voluntary refresh that proves continuity is published as SOFT and the "
                "ledger continues the levels it proved; a fault-driven resync is HARD and "
                "carries nothing. A voluntary refresh is staged on a second book and swapped in "
                "only at an identical update id, so it never rolls the live book back and an "
                "attempt that fails costs nothing but the attempt"),
        }

    def state_payload(self, *, derived: dict[str, Any], at_ns: int, at_ms: int) -> dict[str, Any]:
        """The compact state file's contents: current state, not a history.

        This is what makes a reader's cost independent of session length. Everything here is
        either a small fixed object or the live candidate dictionary, so one read answers
        "what is resting right now" without walking a transition stream whose size grows all day.
        It is explicitly **not** replay authority: `is_authority` is false in the payload, and the
        journal remains the thing an audit reads.
        """
        walls, truncated = self.wall.state_payloads(limit=STATE_MAX_WALLS)
        book = derived.get("book") or {}
        bands = book.get("bands") or []
        coverages = {str((band.get(side) or {}).get("coverage"))
                     for band in bands for side in ("bid", "ask")}
        return {
            "state_version": STATE_VERSION,
            "is_authority": False,
            "authority_note": ("raw frames, snapshots, the envelope sequence and telemetry in the "
                               "journal remain the only replay authority; this file is a cache of "
                               "the collector's current state"),
            "written_ms": at_ms,
            "written_mono_ns": at_ns,
            "session": {
                "session_id": self.session.session_id,
                "started_ms": self.session.started_ms,
                "seq": self.session.seq,
                "sample_index": self.samples,
                "ended": False,
                "elapsed_s": round(self.session.elapsed_s(at_ns), 3),
            },
            "collector": {
                "version": VERSION,
                "collector_version": COLLECTOR_VERSION,
                "exchange": EXCHANGE,
                "symbol": SYMBOL,
                "contract": contract_identity(),
                "pid": os.getpid(),
            },
            "derived": derived,
            "freshness": {
                "depth_age_ms": self.depth.age_ms(at_ns),
                "depth_stale_ms": DEPTH_STALE_MS,
                "depth_state": self.depth.state,
                "depth_connected": self.depth_stream.connected,
                "depth_stale": self.depth_stream.stale,
                "trade_age_ms": self.tape.age_ms(at_ns),
                "trade_stale_ms": TRADE_STALE_MS,
                "trade_connected": self.trade_stream.connected,
                "trade_stale": self.trade_stream.stale,
                "last_invalidation": self.depth.last_invalidation,
            },
            "coverage": {
                "known_low": decimal_out(self.depth.known_low),
                "known_high": decimal_out(self.depth.known_high),
                "best_coverage": COMPLETE if COMPLETE in coverages else (
                    "PARTIAL" if "PARTIAL" in coverages else "UNKNOWN"),
                "bands": [band.get("band_pct") for band in bands],
                "snapshot_limit": DEPTH_REST_LIMIT,
            },
            "resnapshot": self.resnapshot_view(at_ns=at_ns),
            "continuity": self.wall.continuity_view(),
            "walls": {
                "counters": self.wall.counters(),
                "limit": STATE_MAX_WALLS,
                "truncated": truncated,
                "order": "CURRENT_NOTIONAL_DESC",
                "values_as_of": "COLLECTOR_CURRENT_SAMPLE",
                "items": walls,
            },
            "telemetry_recent": list(self.recent_telemetry),
        }

    def write_state(self, *, derived: dict[str, Any], at_ns: int, at_ms: int) -> int:
        return self.store.write_state(
            self.state_payload(derived=derived, at_ns=at_ns, at_ms=at_ms))

    def sample(self, *, at_ns: int, at_ms: int) -> dict[str, Any]:
        """One second of market structure: the derived record plus the wall candidates."""
        self.check_staleness(at_ns=at_ns, at_ms=at_ms)
        self.samples += 1
        payload = {
            "sample_index": self.samples,
            "book": BD.band_view(self.depth, at_ns=at_ns),
            "flow": self.flow.view(at_ns=at_ns, connected=self.trade_stream.connected,
                                   age_ms=self.tape.age_ms(at_ns)),
            "trade_stream": {"connected": self.trade_stream.connected,
                             "age_ms": self.tape.age_ms(at_ns),
                             "last_receive_ms": self.trade_stream.last_receive_ms},
            "coverage_note": (
                "coverage describes this collector's observation of the stream, not a guarantee "
                "that the exchange published everything"),
        }
        self.last_derived = payload
        self.emit("derived", payload, receive_ms=at_ms, mono_ns=at_ns)
        for candidate in self.wall.sample(self.depth, at_ns=at_ns, receive_ms=at_ms):
            self.emit("wall", candidate, receive_ms=at_ms, mono_ns=at_ns)
        self.publish_transition(at_ns=at_ns, at_ms=at_ms)
        self.check_refresh_deadline(at_ns=at_ns, at_ms=at_ms)
        # After the candidates have been updated, so the published state is this sample's state
        # and not the previous one's.
        self.check_resnapshot_policy(at_ns=at_ns, at_ms=at_ms)
        self.write_state(derived=payload, at_ns=at_ns, at_ms=at_ms)
        return payload

    def publish_transition(self, *, at_ns: int, at_ms: int) -> dict[str, Any] | None:
        """Write the outcome of a generation transition, once, to the journal.

        The classification was published when the snapshot was installed; this is what it did to
        the ledger, which is only known after the first sample of the new generation has decided
        which eligible candidates re-qualified. The two records share one event and are told
        apart by `phase`, so a reader can join them on `generation_to` without guessing.
        """
        summary = self.wall.last_transition
        if summary is None or summary is self.published_transition:
            return None
        self.published_transition = summary
        detail = {key: value for key, value in summary.items() if key != "receive_ms"}
        self.telemetry(WALL_CONTINUITY, stream=DEPTH_STREAM, receive_ms=at_ms, mono_ns=at_ns,
                       reason=summary.get("continuity_reason"), phase="APPLIED", **detail)
        return summary

    def emit_stats(self, *, at_ns: int, at_ms: int, queue_backlog: int = 0) -> dict[str, Any]:
        stats = self.store.stats(now_ns=at_ns, queue_backlog=queue_backlog, extra={
            "samples": self.samples,
            "records_published": self.records_published,
            "depth_stream": self.depth_stream.counters(),
            "trade_stream": self.trade_stream.counters(),
            "book": self.depth.counters(),
            "tape": self.tape.counters(),
            "flow": self.flow.counters(),
            "walls": self.wall.counters(),
        })
        self.stats_emitted += 1
        self.emit("storage_stats", stats, receive_ms=at_ms, mono_ns=at_ns)
        return stats

    def finish(self, reason: str, *, at_ns: int, at_ms: int) -> dict[str, Any]:
        """Close candidates, write the last stats and the session end record."""
        self.wall_interrupt(receive_ms=at_ms, mono_ns=at_ns, cause=SHUTDOWN)
        stats = self.emit_stats(at_ns=at_ns, at_ms=at_ms)
        self.emit("session", {
            "event": "END",
            "reason": reason,
            "ended_ms": at_ms,
            "elapsed_s": round(self.session.elapsed_s(at_ns), 3),
            "records": self.records_published,
            "samples": self.samples,
        }, receive_ms=at_ms, mono_ns=at_ns)
        self.telemetry(SHUTDOWN, receive_ms=at_ms, mono_ns=at_ns, reason=reason)
        # The candidates have just been closed, so the final state file is an empty active set on
        # an ended session. A reader that polls after the collector stops must see that, not the
        # last live state, which would keep a dead collector looking alive.
        final = self.state_payload(derived=self.last_derived or {"book": {}, "flow": {}},
                                   at_ns=at_ns, at_ms=at_ms)
        final["session"]["ended"] = True
        final["session"]["end_reason"] = reason
        self.store.write_state(final)
        return stats


# --------------------------------------------------------------------------- asyncio runner


@dataclass
class Runner:
    """Moves bytes and time into `Collector`. Owns the sockets, the queues and the clock."""

    collector: Collector
    duration_s: float = DEFAULT_DURATION_S
    depth_connector: Callable[[str], Any] | None = None
    trade_connector: Callable[[str], Any] | None = None
    snapshot_fetcher: Callable[[], Awaitable[dict[str, Any]]] | None = None
    sample_interval_s: float = SAMPLE_INTERVAL_S
    stats_interval_s: float = STATS_INTERVAL_S
    _stop: asyncio.Event | None = None
    _depth_queue: asyncio.Queue | None = None
    _persist_queue: asyncio.Queue | None = None
    _stop_reason: str = "duration"

    # ------------------------------------------------------------------ plumbing

    def _sink(self, record: dict[str, Any]) -> None:
        queue = self._persist_queue
        assert queue is not None
        try:
            queue.put_nowait(record)
        except asyncio.QueueFull:
            # Explicit, counted and reported in storage_stats. Dropping a record costs raw
            # replayability of that record, so it is never allowed to be silent.
            self.collector.store.dropped_records += 1
        self.collector.store.observe_backlog(queue.qsize())

    async def _persist_worker(self) -> None:
        queue = self._persist_queue
        assert queue is not None
        while True:
            record = await queue.get()
            try:
                if record is None:
                    return
                self.collector.store.write(record, now_ns=record["mono_ns"],
                                           now_ms=record["receive_ms"])
            except Exception as exc:  # a disk failure stops the collector, nothing else
                self.collector.store.dropped_records += 1
                self._stop_reason = f"persistence_error: {type(exc).__name__}: {exc}"
                if self._stop is not None:
                    self._stop.set()
                return
            finally:
                queue.task_done()

    # ------------------------------------------------------------------ depth

    async def _depth_reader(self) -> None:
        assert self._stop is not None and self._depth_queue is not None
        attempt = 0
        url = assert_public_url(DEPTH_WS_URL)
        while not self._stop.is_set():
            connection_id = f"d{int(time.time() * 1000):x}{random.randrange(16**4):04x}"
            try:
                factory = self.depth_connector or _websocket_factory
                async with factory(url) as socket:
                    attempt = 0
                    self._depth_queue.put_nowait(("CONNECT", connection_id, now_ms(), now_ns()))
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(socket.recv(), timeout=RECV_TIMEOUT_S)
                        at_ms, at_ns_ = now_ms(), now_ns()
                        try:
                            self._depth_queue.put_nowait(("FRAME", raw, at_ms, at_ns_))
                        except asyncio.QueueFull:
                            self._depth_queue.put_nowait(("OVERFLOW", None, at_ms, at_ns_))
            except Exception as exc:
                if self._stop.is_set():
                    return
                self._depth_queue.put_nowait(
                    ("DISCONNECT", f"{type(exc).__name__}: {exc}", now_ms(), now_ns()))
            attempt += 1
            await self._backoff(DEPTH_STREAM, attempt)

    async def _book_worker(self) -> None:
        """The single owner of the book. Snapshots arrive here as items, not as a race."""
        assert self._depth_queue is not None and self._stop is not None
        snapshot_task: asyncio.Task | None = None
        while not self._stop.is_set():
            try:
                kind, data, at_ms, at_ns_ = await asyncio.wait_for(
                    self._depth_queue.get(), timeout=self.sample_interval_s)
            except asyncio.TimeoutError:
                if self._should_request_snapshot(snapshot_task):
                    snapshot_task = self._request_snapshot()
                continue
            self.collector.store.observe_backlog(self._depth_queue.qsize())
            if kind == "CONNECT":
                self.collector.on_depth_connect(str(data), receive_ms=at_ms, mono_ns=at_ns_)
            elif kind == "DISCONNECT":
                self.collector.on_depth_disconnect(str(data), receive_ms=at_ms, mono_ns=at_ns_)
            elif kind == "OVERFLOW":
                self.collector.depth.invalidate(B.QUEUE_OVERFLOW)
                self.collector.snapshot_wanted = True
                self.collector.telemetry(OVERFLOW, stream=DEPTH_STREAM, receive_ms=at_ms,
                                         mono_ns=at_ns_, what="depth_frame_queue",
                                         limit=DEPTH_QUEUE_MAX)
            elif kind == "FRAME":
                try:
                    message = json.loads(data)
                except ValueError:
                    self.collector.depth_stream.malformed += 1
                    self.collector.telemetry("malformed", stream=DEPTH_STREAM, receive_ms=at_ms,
                                             mono_ns=at_ns_, error="json")
                    message = None
                if isinstance(message, dict):
                    self.collector.on_depth_frame(message, receive_ms=at_ms, mono_ns=at_ns_)
            elif kind == "SNAPSHOT":
                payload, request_ms, request_ns = data
                self.collector.on_snapshot(payload, receive_ms=at_ms, mono_ns=at_ns_,
                                           request_ms=request_ms, request_mono_ns=request_ns)
            elif kind == "SNAPSHOT_FAILED":
                self.collector.on_snapshot_failed(str(data), receive_ms=at_ms, mono_ns=at_ns_)
            if self._should_request_snapshot(snapshot_task):
                snapshot_task = self._request_snapshot()

    def _should_request_snapshot(self, task: asyncio.Task | None) -> bool:
        wanted = self.collector.snapshot_wanted or self.collector.refresh_wanted
        if not wanted or self.collector.snapshot_in_flight:
            return False
        # A synchronized book never *needs* a REST read, whatever `snapshot_wanted` says. This is
        # the invariant rather than the flag, and it is checked second on purpose. A voluntary
        # refresh is the one thing allowed past it, because it is asking to replace a book that is
        # working - and it is the only request that can be refused on arrival.
        if self.collector.depth.state == B.SYNCED and not self.collector.refresh_wanted:
            return False
        # Buffering has to be running before the request goes out, so a snapshot is only asked
        # for once the depth socket is actually connected.
        if not self.collector.depth_stream.connected:
            return False
        return task is None or task.done()

    def _request_snapshot(self) -> asyncio.Task:
        request_ms, request_ns = now_ms(), now_ns()
        self.collector.mark_snapshot_requested(receive_ms=request_ms, mono_ns=request_ns)
        return asyncio.ensure_future(self._fetch_snapshot(request_ms, request_ns))

    async def _fetch_snapshot(self, request_ms: int, request_ns: int) -> None:
        assert self._depth_queue is not None
        try:
            fetcher = self.snapshot_fetcher or _rest_snapshot
            payload = await fetcher()
            self._depth_queue.put_nowait(
                ("SNAPSHOT", (payload, request_ms, request_ns), now_ms(), now_ns()))
        except Exception as exc:
            self._depth_queue.put_nowait(
                ("SNAPSHOT_FAILED", f"{type(exc).__name__}: {exc}", now_ms(), now_ns()))

    # ------------------------------------------------------------------ trades

    async def _trade_reader(self) -> None:
        assert self._stop is not None
        attempt = 0
        url = assert_public_url(TRADE_WS_URL)
        while not self._stop.is_set():
            connection_id = f"t{int(time.time() * 1000):x}{random.randrange(16**4):04x}"
            try:
                factory = self.trade_connector or _websocket_factory
                async with factory(url) as socket:
                    attempt = 0
                    self.collector.on_trade_connect(connection_id, receive_ms=now_ms(),
                                                    mono_ns=now_ns())
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(socket.recv(), timeout=RECV_TIMEOUT_S)
                        at_ms, at_ns_ = now_ms(), now_ns()
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            self.collector.trade_stream.malformed += 1
                            continue
                        if isinstance(message, dict):
                            self.collector.on_trade_frame(message, receive_ms=at_ms,
                                                          mono_ns=at_ns_)
            except Exception as exc:
                if self._stop.is_set():
                    return
                self.collector.on_trade_disconnect(f"{type(exc).__name__}: {exc}",
                                                   receive_ms=now_ms(), mono_ns=now_ns())
            attempt += 1
            await self._backoff(TRADE_STREAM, attempt)

    async def _backoff(self, stream: str, attempt: int) -> None:
        assert self._stop is not None
        if self._stop.is_set():
            return
        delay = min(RECONNECT_BACKOFF_MAX_S, 2 ** min(attempt, 5)) + random.random()
        state = (self.collector.depth_stream if stream == DEPTH_STREAM
                 else self.collector.trade_stream)
        state.retries += 1
        self.collector.telemetry(RETRY, stream=stream, receive_ms=now_ms(), mono_ns=now_ns(),
                                 attempt=attempt, delay_s=round(delay, 3))
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------ sampling

    async def _sampler(self) -> None:
        assert self._stop is not None
        next_sample = time.monotonic() + self.sample_interval_s
        next_stats = time.monotonic() + self.stats_interval_s
        while not self._stop.is_set():
            delay = max(0.0, next_sample - time.monotonic())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
            at_ms, at_ns_ = now_ms(), now_ns()
            self.collector.sample(at_ns=at_ns_, at_ms=at_ms)
            try:
                self.collector.store.tick(at_ns_, at_ms)
            except StoreAuthorityLost as exc:
                # This process can no longer prove it owns its output root: the lock file under
                # it was removed or replaced, so a second collector may already be writing here.
                # Stopping is the whole point - a writer that keeps going would interleave two
                # sessions into one directory - and it is reported rather than left to be
                # inferred from a journal that simply stops.
                self._stop_reason = f"writer_authority_lost: {exc}"
                self._stop.set()
                return
            next_sample += self.sample_interval_s
            if time.monotonic() >= next_stats:
                backlog = self._persist_queue.qsize() if self._persist_queue else 0
                self.collector.emit_stats(at_ns=at_ns_, at_ms=at_ms, queue_backlog=backlog)
                next_stats += self.stats_interval_s

    # ------------------------------------------------------------------ lifecycle

    async def run(self, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
        self._stop = asyncio.Event()
        self._depth_queue = asyncio.Queue(maxsize=DEPTH_QUEUE_MAX)
        self._persist_queue = asyncio.Queue(maxsize=PERSIST_QUEUE_MAX)
        self.collector.sink = self._sink

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._on_signal)
            except (NotImplementedError, RuntimeError):
                pass

        self.collector.write_session_record(config=config or self.config_view())
        persist = asyncio.ensure_future(self._persist_worker())
        workers = [asyncio.ensure_future(task()) for task in
                   (self._depth_reader, self._book_worker, self._trade_reader, self._sampler)]
        if self.duration_s:
            loop.call_later(self.duration_s, self._stop.set)
        try:
            await self._stop.wait()
        finally:
            # Every stage of shutdown is bounded. An unattended run must always get as far as
            # sealing its files, so a socket that will not close, or a writer that will not
            # drain, costs a recorded abandonment rather than a process that never finishes.
            for worker in workers:
                worker.cancel()
            await self._bounded(asyncio.gather(*workers, return_exceptions=True), "workers")
            stats = self.collector.finish(self._stop_reason, at_ns=now_ns(), at_ms=now_ms())
            drained = await self._bounded(self._persist_queue.join(), "persistence_drain")
            if not drained:
                # Records still queued were never written, so they are counted as dropped. The
                # dataset must not imply that a record reached disk because it was emitted.
                self.collector.store.dropped_records += self._persist_queue.qsize()
            persist.cancel()
            await self._bounded(asyncio.gather(persist, return_exceptions=True), "writer_stop")
            self.collector.store.close()
            # Why the run ended travels with the numbers. A collector that stopped because it
            # lost its output root must not look like one that reached its duration.
            stats["stop_reason"] = self._stop_reason
            stats["authority_lost"] = self.collector.store.authority_lost
        return stats

    async def _bounded(self, awaitable: Awaitable[Any], stage: str) -> bool:
        """Await `awaitable` with the shutdown ceiling. Returns whether it finished in time."""
        try:
            await asyncio.wait_for(awaitable, timeout=SHUTDOWN_TIMEOUT_S)
            return True
        except asyncio.TimeoutError:
            self.collector.telemetry(SHUTDOWN, receive_ms=now_ms(), mono_ns=now_ns(),
                                     stage=stage, abandoned_after_s=SHUTDOWN_TIMEOUT_S)
            return False
        except Exception:
            return True

    def _on_signal(self) -> None:
        self._stop_reason = "signal"
        if self._stop is not None:
            self._stop.set()

    def config_view(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "depth_ws_url": DEPTH_WS_URL,
            "trade_ws_url": TRADE_WS_URL,
            "depth_rest_url": DEPTH_REST_URL,
            "depth_rest_limit": DEPTH_REST_LIMIT,
            "duration_s": self.duration_s,
            "sample_interval_s": self.sample_interval_s,
            "stats_interval_s": self.stats_interval_s,
            "depth_stale_ms": DEPTH_STALE_MS,
            "trade_stale_ms": TRADE_STALE_MS,
            "depth_queue_max": DEPTH_QUEUE_MAX,
            "persist_queue_max": PERSIST_QUEUE_MAX,
            "root": str(self.collector.store.root),
        }


# --------------------------------------------------------------------------- real IO


def _websocket_factory(url: str) -> Any:
    import websockets
    return websockets.connect(assert_public_url(url), ping_interval=20, ping_timeout=20,
                              max_size=WS_MAX_FRAME_BYTES, max_queue=512,
                              user_agent_header=USER_AGENT)


async def _rest_snapshot() -> dict[str, Any]:
    import httpx
    url = assert_public_url(DEPTH_REST_URL)
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params={"symbol": "BTCUSDT",
                                                 "limit": DEPTH_REST_LIMIT})
        response.raise_for_status()
        return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.crypto.market_structure_v0.collector",
        description="BTC Market Structure V0 read-only Binance USDⓈ-M forward collector")
    parser.add_argument("--root", default=os.environ.get("MS_V0_ROOT"),
                        help="output directory (or MS_V0_ROOT)")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S,
                        help="seconds to run; 0 runs until stopped")
    parser.add_argument("--wall-updates", action="store_true",
                        help="write a wall row per candidate per sample instead of only the "
                             "open and end transitions; measured at about 8x the wall bytes")
    args = parser.parse_args(argv)
    if not args.root:
        parser.error("--root or MS_V0_ROOT is required")

    root = Path(args.root).expanduser().resolve()
    session = Session()
    store = Store.open(root, session.session_id, started_ns=session.started_ns)
    collector = Collector(store=store, session=session,
                          wall=W.WallTracker(emit_updates=args.wall_updates))
    runner = Runner(collector=collector, duration_s=args.duration)
    print(json.dumps({"event": "START", "session_id": session.session_id, "root": str(root),
                      "duration_s": args.duration, "version": VERSION,
                      "contract": contract_identity()}, indent=2), flush=True)
    try:
        stats = asyncio.run(runner.run())
    except Exception:
        store.close()
        raise
    print(json.dumps({"event": "END", "session_id": session.session_id,
                      "stop_reason": stats.get("stop_reason"),
                      "authority_lost": stats.get("authority_lost"),
                      "elapsed_s": stats["elapsed_s"], "total_rows": stats["total_rows"],
                      "total_bytes": stats["total_bytes"],
                      "projected_bytes_per_day": stats["projected_bytes_per_day"],
                      "projected_rows_per_day": stats["projected_rows_per_day"],
                      "dropped_records": stats["dropped_records"],
                      "queue_backlog_max": stats["queue_backlog_max"]}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
