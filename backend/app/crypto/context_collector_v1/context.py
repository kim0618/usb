"""One second of Market Context, computed by the frozen code that would compute it anyway.

Nothing in this module decides a value. Every figure comes out of a function another frozen
contract owns, called in the order its own consumer calls it:

1. **The Liquidity Map viewer's read path**, on the root this process just wrote: the compact
   state file (`checkpoint.read`), the session stream (`journal.latest_session`), the wall set
   (`WallFollower.refresh`) and `view.snapshot_view`. This is `liquidity_map.api.snapshot` minus
   the HTTP layer, so the reading is the one the viewer itself would serve for this root, including
   `lm-wall.v2` and `lm-continuity` already applied. Parity with the viewer is therefore structural:
   there is no second implementation that could drift.
2. **Market Context V1's LIQUIDITY and FLOW layers** (`mcv1_vendored`), fed that reading with the
   panel's own display filter and wall limit, and one `VanishTracker` for the life of the process.
   CANCEL_LIKE, CONSUMED_CANDIDATE and ABSORPTION_CANDIDATE are that code's verdicts, made at the
   collector's own one-second cadence, which is the cadence the touch test was measured at.
3. **The full `lm-wall.v2` selection**, through the same `view.side_walls` with the display filter
   opened to zero and no limit. Its bin transitions are the `wall_v2` stream: what a forward study
   needs to know which bins were present at which second, without 28.9 walls a second being written
   every second.

What this module does add is bookkeeping that is not a market reading: the compact journal shape,
the transition diff, and `collector.state`, a word about the collector process itself (SYNCING
while a book or a persistence window is being rebuilt), published beside the per-layer states and
never instead of them.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..liquidity_map import checkpoint as CP
from ..liquidity_map import journal as J
from ..liquidity_map import view as V
from ..liquidity_map import wallrule as R
from ..liquidity_map.wallstate import WallFilter, WallFollower
from ..market_structure_v0.contract import COMPLETE, PARTIAL
from . import VERSION
from .contract import (CLOSE_NO_READING, CLOSE_NOT_SELECTED, CLOSE_SESSION_END,
                       DISPLAY_MIN_NOTIONAL_USDT, DISPLAY_WALL_LIMIT, FEED_LIVE, FEED_PARTIAL,
                       FEED_STALE, FEED_SYNCING, FEED_UNKNOWN, SUPPORTED_SYMBOL, VENUE,
                       WALL_V2_CHANGE, WALL_V2_CLOSE, WALL_V2_OPEN, WALL_V2_TRACKED)
from .mcv1_vendored import contract as MCC
from .mcv1_vendored import flow as MCF
from .mcv1_vendored import liquidity as MCL

#: The record name every `context` line carries.
CONTEXT_RECORD = "CTX_V1_SNAPSHOT"
WALL_V2_RECORD = "CTX_V1_WALL_V2"

#: The full-selection filter: the rule's own floors only, nothing on top.
FULL_SELECTION_FILTER = WallFilter(min_notional_usdt=Decimal(0))
FULL_SELECTION_LIMIT = 1_000_000

#: Why the collector says SYNCING. Closed list.
SYNC_NO_SESSION = "SESSION_RECORD_NOT_YET_FLUSHED"
SYNC_BOOK = "BOOK_UNSYNCED"
SYNC_FLOW_WARMUP = "FLOW_WINDOW_WARMUP"
SYNC_WALL_WARMUP = "WALL_PERSISTENCE_WARMUP"


def record_from_state(file: CP.StateFile) -> dict[str, Any] | None:
    """`liquidity_map.api._record_from_state`, restated because importing that module would load
    the viewer's FastAPI app into the collector. A parity test compares the two on real files."""
    derived = file.derived
    if not derived:
        return None
    collector = file.payload.get("collector") or {}
    session = file.payload.get("session") or {}
    return {"kind": "derived", "payload": derived, "receive_ms": file.written_ms,
            "seq": file.seq, "collector_version": collector.get("collector_version"),
            "exchange": collector.get("exchange"), "symbol": collector.get("symbol"),
            "session_id": session.get("session_id")}


@dataclass
class Reading:
    """The viewer's snapshot plus the inputs it was built from, for the full selection."""

    snapshot: dict[str, Any]
    wall_set: Any = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    mid: Decimal | None = None
    known_low: Decimal | None = None
    known_high: Decimal | None = None
    latest_sample_ms: int | None = None
    state_file: CP.StateFile | None = None
    session: J.SessionRef | None = None


def read_viewer(root: Path, follower: WallFollower, *, now_ms: int,
                wall_filter: WallFilter, wall_limit: int) -> Reading:
    """`liquidity_map.api.snapshot` for `root`, without HTTP. Same calls, same order."""
    if not root.exists():
        return Reading(V.empty_view(root=str(root), now_ms=now_ms, reason="ROOT_NOT_FOUND"))
    try:
        session = J.latest_session(root)
    except J.JournalEmpty:
        return Reading(V.empty_view(root=str(root), now_ms=now_ms,
                                    reason="NO_SESSION_RECORDED"))
    state_file = CP.read(root, now_ms=now_ms, session_id=session.session_id)
    derived_bytes = telemetry_bytes = 0
    derived_record = record_from_state(state_file) if state_file.present else None
    telemetry: list[dict[str, Any]] = []
    if derived_record is not None:
        telemetry = [{"seq": item.get("seq"), "receive_ms": item.get("receive_ms"),
                      "payload": item} for item in state_file.telemetry_recent]
    else:
        derived_record, derived_bytes = J.last_record(root, "derived", session)
        telemetry, telemetry_bytes = J.recent_records(root, "telemetry", session, limit=12)
    wall_set = follower.refresh(root, session, now_ms=now_ms, state=state_file)
    snapshot = V.snapshot_view(
        root=str(root), session=session, derived_record=derived_record, wall_set=wall_set,
        wall_filter=wall_filter, now_ms=now_ms, wall_limit=wall_limit, telemetry=telemetry,
        read_cost={"derived_bytes": derived_bytes, "telemetry_bytes": telemetry_bytes,
                   "state_bytes": state_file.bytes_read, "state_usable": state_file.usable,
                   "wall_bytes": wall_set.scanned_bytes,
                   "wall_records": wall_set.scanned_records,
                   "wall_tail_bytes": wall_set.tail_bytes,
                   "wall_tail_records": wall_set.tail_records,
                   "total_bytes": derived_bytes + telemetry_bytes + wall_set.scanned_bytes,
                   "journal_walked": derived_bytes > 0 or not state_file.usable,
                   "elapsed_ms": max(0, int(time.time() * 1000) - now_ms)})
    derived = (derived_record or {}).get("payload") or {}
    book = derived.get("book") or {}
    sample_ms = (derived_record or {}).get("receive_ms")
    return Reading(snapshot=snapshot, wall_set=wall_set, rows=wall_set.rows(),
                   mid=V._d(book.get("mid")), known_low=V._d(book.get("known_low")),
                   known_high=V._d(book.get("known_high")),
                   latest_sample_ms=sample_ms if isinstance(sample_ms, int) else None,
                   state_file=state_file, session=session)


def full_selection(reading: Reading) -> dict[str, list[dict[str, Any]]]:
    """Every `lm-wall.v2` wall per side, nearest first, exactly as the viewer selects them."""
    out: dict[str, list[dict[str, Any]]] = {R.SIDE_ASK: [], R.SIDE_BID: []}
    if reading.wall_set is None:
        return out
    for side in (R.SIDE_ASK, R.SIDE_BID):
        selected = V.side_walls(reading.rows, side, mid=reading.mid, wall_set=reading.wall_set,
                                wall_filter=FULL_SELECTION_FILTER,
                                latest_sample_ms=reading.latest_sample_ms,
                                limit=FULL_SELECTION_LIMIT, known_low=reading.known_low,
                                known_high=reading.known_high)
        out[side] = list(selected.get("walls") or [])
    return out


# --------------------------------------------------------------------------- compact shapes


def _wall_brief(wall: dict[str, Any] | None) -> dict[str, Any] | None:
    """The fields a forward study reads off a wall. Values exactly as the layer published them."""
    if wall is None:
        return None
    return {key: wall.get(key) for key in (
        "price", "qty_btc", "notional_usdt", "distance_bps", "multiple", "persistence_ms",
        "own_persistence_ms", "persistence_source", "continuity_status", "bin_low", "bin_high",
        "coverage", "first_seen_ms", "generation")}


def _depth_brief(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Every band, both sides: coverage, the observed notional and whether it is a bound.

    The canonical notional is the observed one exactly when the band is COMPLETE, so it is not
    written twice; a PARTIAL band's observed figure is a lower bound and says so.
    """
    sides = snapshot.get("sides") or {}
    ask = {str(b.get("band_pct")): b for b in (sides.get("ASK") or {}).get("depth") or []}
    bid = {str(b.get("band_pct")): b for b in (sides.get("BID") or {}).get("depth") or []}
    out = []
    for label in [str(b.get("band_pct")) for b in (sides.get("BID") or {}).get("depth") or []]:
        b, a = bid.get(label) or {}, ask.get(label) or {}
        out.append({"band_pct": label,
                    "bid_coverage": b.get("coverage"), "ask_coverage": a.get("coverage"),
                    "bid_notional": b.get("observed_notional"),
                    "ask_notional": a.get("observed_notional"),
                    "bid_is_lower_bound": b.get("is_lower_bound"),
                    "ask_is_lower_bound": a.get("is_lower_bound"),
                    "imbalance_usdt": b.get("imbalance_usdt")})
    return out


def _flow_brief(flow_layer: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = ((snapshot.get("flow") or {}).get("windows")) or {}
    windows = {}
    for label, window in (flow_layer.get("windows") or {}).items():
        source = raw.get(label) or {}
        windows[label] = {
            "state": window.get("state"), "coverage": window.get("coverage"),
            "coverage_reason": window.get("coverage_reason"), "trades": window.get("trades"),
            # Observed sides: canonical exactly when COMPLETE, a lower bound when PARTIAL.
            "buy_usdt": source.get("observed_buy_usdt"),
            "sell_usdt": source.get("observed_sell_usdt"),
            "buy_btc": source.get("observed_buy_btc"),
            "sell_btc": source.get("observed_sell_btc"),
            "is_lower_bound": source.get("is_lower_bound"),
            "imbalance_usdt": window.get("imbalance_usdt"),
            "imbalance_btc": window.get("imbalance_btc")}
    stream = flow_layer.get("trade_stream") or {}
    return {"state": flow_layer.get("state"), "reasons": flow_layer.get("reasons"),
            "trade_connected": stream.get("connected"), "trade_age_ms": stream.get("age_ms"),
            "trade_state": stream.get("state"), "windows": windows}


def _vanish_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in (
        "state", "reason", "side", "bin_low", "bin_high", "price", "notional_usdt",
        "last_seen_ms", "observed_at_ms", "path_samples", "mid_path_touched_bin")}


def _absorption_brief(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row.get(key) for key in (
        "state", "reason", "side", "window", "aggressive_usdt", "wall_notional_usdt",
        "wall_price", "bin_low", "bin_high", "wall_persistence_ms") if key in row}


# --------------------------------------------------------------------------- engine


@dataclass
class ContextEngine:
    """Per-process context state: the viewer's follower, the panel's tracker, the open bins."""

    root: Path
    wall_filter: WallFilter = field(
        default_factory=lambda: WallFilter(min_notional_usdt=Decimal(DISPLAY_MIN_NOTIONAL_USDT)))
    wall_limit: int = DISPLAY_WALL_LIMIT
    follower: WallFollower = field(default_factory=WallFollower)
    tracker: MCF.VanishTracker = field(default_factory=MCF.VanishTracker)
    open_bins: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: When the current wall observation window began: session start or the last HARD transition.
    observation_since_ms: int | None = None
    last_hard_ms: int | None = None
    last_hard_fingerprint: str | None = None
    samples: int = 0
    wall_v2_events: dict[str, int] = field(
        default_factory=lambda: {WALL_V2_OPEN: 0, WALL_V2_CHANGE: 0, WALL_V2_CLOSE: 0})
    absorption_candidates: int = 0
    collector_states: dict[str, int] = field(default_factory=dict)
    compute_ns_total: int = 0
    compute_ns_max: int = 0
    last_latest: dict[str, Any] | None = None
    #: The viewer reading the last step was computed from. Kept for audit and parity checks.
    last_reading: Reading | None = None

    # ------------------------------------------------------------------ one sample

    def step(self, *, now_ms: int, session_started_ms: int, sample_index: int,
             ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        """Returns (wall_v2 payloads, the compact `context` payload, the full latest payload)."""
        started = time.monotonic_ns()
        reading = read_viewer(self.root, self.follower, now_ms=now_ms,
                              wall_filter=self.wall_filter, wall_limit=self.wall_limit)
        snapshot = reading.snapshot
        liquidity = MCL.liquidity_view(snapshot, symbol=SUPPORTED_SYMBOL)
        flow = MCF.flow_view(snapshot, liquidity, symbol=SUPPORTED_SYMBOL, tracker=self.tracker)
        self._note_transition(reading, session_started_ms)
        events = self._wall_v2_events(reading, sample_index=sample_index, now_ms=now_ms)
        collector = self._collector_state(snapshot, liquidity, flow, now_ms=now_ms,
                                          session_started_ms=session_started_ms)
        self.collector_states[collector["state"]] = (
            self.collector_states.get(collector["state"], 0) + 1)
        absorption = flow.get("absorption") or {}
        if absorption.get("state") == MCC.ABSORPTION_CANDIDATE:
            self.absorption_candidates += 1

        source = snapshot.get("source") or {}
        price = snapshot.get("price") or {}
        quality = snapshot.get("quality") or {}
        walls = snapshot.get("walls") or {}
        sides = liquidity.get("sides") or {}
        compact = {
            "record": CONTEXT_RECORD,
            "schema": VERSION,
            "symbol": SUPPORTED_SYMBOL,
            "venue": VENUE,
            "sample_index": source.get("sample_index"),
            "sample_ms": source.get("sample_receive_ms"),
            "collector": collector,
            "book": {
                "state": quality.get("book_state"), "viewer_state": quality.get("state"),
                "age_ms": quality.get("depth_age_ms"),
                "generation": quality.get("generation"),
                "last_invalidation": quality.get("last_invalidation"),
                "mid": price.get("mid"), "best_bid": price.get("best_bid"),
                "best_ask": price.get("best_ask"), "spread_bps": price.get("spread_bps"),
                "known_low": price.get("known_low"), "known_high": price.get("known_high"),
            },
            "depth": _depth_brief(snapshot),
            "liquidity": {
                "state": liquidity.get("state"), "reasons": liquidity.get("reasons"),
                "wall_set_coverage": walls.get("coverage"),
                "candidates": walls.get("candidate_count"),
                "band_imbalance": liquidity.get("band_imbalance"),
                **{side: {"wall_state": (sides.get(side) or {}).get("wall_state"),
                          "wall_state_reason": (sides.get(side) or {}).get("wall_state_reason"),
                          "nearest": _wall_brief((sides.get(side) or {}).get("nearest_wall")),
                          "walls_selected": (sides.get(side) or {}).get("walls_selected"),
                          "walls_shown": (sides.get(side) or {}).get("walls_shown")}
                   for side in (R.SIDE_ASK, R.SIDE_BID)},
            },
            "flow": _flow_brief(flow, snapshot),
            "vanished": [_vanish_brief(item)
                         for item in ((flow.get("vanished") or {}).get("this_reading") or [])],
            "absorption": _absorption_brief(flow.get("absorption")),
            "open_v2_bins": {side: sum(1 for key in self.open_bins if key[0] == side)
                             for side in (R.SIDE_ASK, R.SIDE_BID)},
        }
        latest = {
            "record": CONTEXT_RECORD, "schema": VERSION, "symbol": SUPPORTED_SYMBOL,
            "venue": VENUE, "written_ms": now_ms, "collector": collector,
            "sample_index": source.get("sample_index"),
            "sample_ms": source.get("sample_receive_ms"),
            "session_id": source.get("session_id"),
            "liquidity": liquidity, "flow": flow,
            "open_v2_bins": compact["open_v2_bins"],
        }
        self.samples += 1
        elapsed = time.monotonic_ns() - started
        self.compute_ns_total += elapsed
        self.compute_ns_max = max(self.compute_ns_max, elapsed)
        self.last_latest = latest
        self.last_reading = reading
        return events, compact, latest

    # ------------------------------------------------------------------ collector state

    def _note_transition(self, reading: Reading, session_started_ms: int) -> None:
        """Restart the persistence window at each new HARD transition the collector publishes.

        A transition is recognised by its content, not by its time: the interruption a gap
        produces carries `receive_ms: null`, so keying on time would mistake one transition for a
        new one every sample. A transition seen for the first time without a time of its own is
        dated by the sample it was first seen in.
        """
        if self.observation_since_ms is None:
            self.observation_since_ms = session_started_ms
        state = reading.state_file
        last = ((state.continuity if state is not None else {}) or {}).get("last_transition")
        if not isinstance(last, dict) or last.get("refresh_type") != "HARD":
            return
        fingerprint = json.dumps(last, sort_keys=True, default=str)
        if fingerprint == self.last_hard_fingerprint:
            return
        self.last_hard_fingerprint = fingerprint
        at = last.get("receive_ms")
        at = at if isinstance(at, int) else reading.latest_sample_ms
        if isinstance(at, int):
            self.last_hard_ms = at
            self.observation_since_ms = max(self.observation_since_ms or 0, at)

    def _collector_state(self, snapshot: dict[str, Any], liquidity: dict[str, Any],
                         flow: dict[str, Any], *, now_ms: int,
                         session_started_ms: int) -> dict[str, Any]:
        """A word about the collector process, never about the market and never a roll-up.

        The per-layer states stay where the panel's contract puts them. This answers the one
        question they cannot: whether the collector is still rebuilding something, so that a
        LIQUIDITY `NONE` right after a restart - when no wall can yet have met R4's ten seconds -
        is not read as an empty book.
        """
        reasons: list[str] = []
        quality = snapshot.get("quality") or {}
        viewer_state = str(quality.get("state") or V.NO_DATA)
        source = snapshot.get("source") or {}
        if source.get("unavailable_reason") is not None:
            reasons.append(str(source["unavailable_reason"]))
        if viewer_state == V.NO_DATA:
            state = FEED_SYNCING if source.get("unavailable_reason") == "NO_SESSION_RECORDED" \
                else FEED_UNKNOWN
            if state == FEED_SYNCING:
                reasons = [SYNC_NO_SESSION]
            return self._state_view(state, reasons, now_ms, session_started_ms)
        if viewer_state == V.STALE:
            reasons.extend(str(item) for item in quality.get("reasons") or [])
            return self._state_view(FEED_STALE, reasons, now_ms, session_started_ms)
        syncing: list[str] = []
        if viewer_state == V.SYNCING or quality.get("book_state") != "SYNCED":
            syncing.append(SYNC_BOOK)
        windows = (flow.get("windows") or {})
        if any((window or {}).get("coverage_reason") == "WARMUP"
               and (window or {}).get("coverage") != COMPLETE for window in windows.values()):
            syncing.append(SYNC_FLOW_WARMUP)
        since = self.observation_since_ms or session_started_ms
        if now_ms - since < R.MIN_PERSISTENCE_MS:
            syncing.append(SYNC_WALL_WARMUP)
        if syncing:
            return self._state_view(FEED_SYNCING, syncing, now_ms, session_started_ms)
        layer_states = {"LIQUIDITY": liquidity.get("state"), "FLOW": flow.get("state")}
        if any(value in (MCC.UNKNOWN, MCC.STALE) for value in layer_states.values()):
            reasons.extend(f"{name}_{value}" for name, value in layer_states.items()
                           if value != MCC.LIVE)
            return self._state_view(FEED_UNKNOWN if MCC.UNKNOWN in layer_states.values()
                                    else FEED_STALE, reasons, now_ms, session_started_ms)
        if any(value == PARTIAL for value in layer_states.values()):
            reasons.extend(f"{name}_{value}" for name, value in layer_states.items()
                           if value != MCC.LIVE)
            return self._state_view(FEED_PARTIAL, reasons, now_ms, session_started_ms)
        return self._state_view(FEED_LIVE, reasons, now_ms, session_started_ms)

    def _state_view(self, state: str, reasons: list[str], now_ms: int,
                    session_started_ms: int) -> dict[str, Any]:
        return {"state": state, "reasons": reasons,
                "session_age_ms": max(0, now_ms - session_started_ms),
                "observation_since_ms": self.observation_since_ms,
                "is_rollup_of_layers": False}

    # ------------------------------------------------------------------ wall_v2

    def _wall_v2_events(self, reading: Reading, *, sample_index: int,
                        now_ms: int) -> list[dict[str, Any]]:
        sample_ms = reading.latest_sample_ms
        usable = (reading.mid is not None and reading.wall_set is not None
                  and reading.wall_set.coverage == COMPLETE)
        current: dict[tuple[str, str], dict[str, Any]] = {}
        if usable:
            for side, walls in full_selection(reading).items():
                for wall in walls:
                    current[(side, str(wall.get("bin_low")))] = wall
        events: list[dict[str, Any]] = []
        base = {"record": WALL_V2_RECORD, "schema": VERSION, "sample_index": sample_index,
                "sample_ms": sample_ms}
        for key in sorted(set(self.open_bins) - set(current)):
            last = self.open_bins.pop(key)
            events.append({**base, "event": WALL_V2_CLOSE, "side": key[0], "bin_low": key[1],
                           "bin_high": last.get("bin_high"),
                           "reason": CLOSE_NOT_SELECTED if usable else CLOSE_NO_READING,
                           "last_price": last.get("price"),
                           "last_notional_usdt": last.get("notional_usdt")})
        for key in sorted(current):
            wall = current[key]
            previous = self.open_bins.get(key)
            if previous is None:
                events.append({**base, "event": WALL_V2_OPEN, "side": key[0], "bin_low": key[1],
                               **{name: wall.get(name) for name in (
                                   "bin_high", "price", "qty_btc", "notional_usdt", "multiple",
                                   "distance_bps", "observed_persistence_ms",
                                   "own_persistence_ms", "persistence_source",
                                   "continuity_status", "first_seen_ms", "generation",
                                   "coverage", "bin_members", "bin_candidate_notional_usdt")}})
            else:
                changed = {name: wall.get(name) for name in WALL_V2_TRACKED
                           if wall.get(name) != previous.get(name)}
                if changed:
                    events.append({**base, "event": WALL_V2_CHANGE, "side": key[0],
                                   "bin_low": key[1], "changed": sorted(changed),
                                   **changed, "notional_usdt": wall.get("notional_usdt"),
                                   "distance_bps": wall.get("distance_bps"),
                                   "first_seen_ms": wall.get("first_seen_ms")})
            self.open_bins[key] = {name: wall.get(name) for name in (
                "bin_high", "price", "notional_usdt", *WALL_V2_TRACKED)}
        for event in events:
            self.wall_v2_events[event["event"]] += 1
        return events

    def close_all(self, *, sample_index: int, sample_ms: int | None) -> list[dict[str, Any]]:
        """Close every open bin at session end, so a reader never sees a bin left dangling."""
        events = []
        for key in sorted(self.open_bins):
            last = self.open_bins.pop(key)
            events.append({"record": WALL_V2_RECORD, "schema": VERSION,
                           "sample_index": sample_index, "sample_ms": sample_ms,
                           "event": WALL_V2_CLOSE, "side": key[0], "bin_low": key[1],
                           "bin_high": last.get("bin_high"), "reason": CLOSE_SESSION_END,
                           "last_price": last.get("price"),
                           "last_notional_usdt": last.get("notional_usdt")})
            self.wall_v2_events[WALL_V2_CLOSE] += 1
        return events

    def counters(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "wall_v2_events": dict(self.wall_v2_events),
            "open_v2_bins": len(self.open_bins),
            "vanish_totals": dict(self.tracker.totals),
            "absorption_candidates": self.absorption_candidates,
            "collector_states": dict(self.collector_states),
            "compute_ms_mean": (round(self.compute_ns_total / self.samples / 1e6, 3)
                                if self.samples else None),
            "compute_ms_max": round(self.compute_ns_max / 1e6, 3),
        }


__all__ = ["ContextEngine", "Reading", "read_viewer", "full_selection", "record_from_state",
           "CONTEXT_RECORD", "WALL_V2_RECORD", "SYNC_NO_SESSION", "SYNC_BOOK", "SYNC_FLOW_WARMUP",
           "SYNC_WALL_WARMUP", "FULL_SELECTION_FILTER"]
