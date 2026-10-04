"""Which wall candidates are resting *right now*, and whether that set can be proven complete.

This is the one genuinely hard part of reading the V0 journal, and it is worth saying why.

The collector persists wall candidates **by transition**: a row when a candidate opens and a row
when it ends. That decision was measured - a row per candidate per second made the wall stream
88.5% of all bytes - and it is the right one for a dataset. But it means the journal never
contains a line that says "these are the candidates currently resting". That set only exists
inside the collector's memory, and this viewer is not allowed to reach into it.

So the set is reconstructed, and the reconstruction is **verified rather than assumed**:

1. Walk the wall stream backwards. For each `(side, price)` the first row met going backwards is
   the newest one, so it decides the key's state: `OPENED` means resting, `ENDED`/`UNKNOWN` means
   gone. This is exact for every key the walk reaches.
2. The walk can only miss one thing: a candidate that opened *before* the walk's window and has
   been resting ever since. Nothing in the window mentions it, so the set would be silently short
   by exactly the walls that have lasted longest - which are the ones an operator most wants to
   see. Guessing here would be the worst possible failure mode.
3. So the count is checked against an authority the collector itself wrote. `storage_stats` carries
   `walls.active`, the real size of the collector's candidate dictionary, with an envelope `seq`.
   Rebuild the set as of that `seq` from the same backward walk and compare sizes. The walk can
   only ever *under*count, so equality proves the window caught everything. Mismatch is reported
   as a number of missing candidates, and the view then refuses to call anything "nearest".
4. A walk that reaches the first byte of the session's first wall file is complete by
   construction and needs no authority - which is the usual case in the first hour of a session,
   before the first `storage_stats` row even exists.

After the first walk the follower moves **forwards** from a cursor, applying transitions as they
are appended, so steady-state cost is the bytes written since the last poll rather than the
session's whole history. Once a set has been proven complete it stays proven, because every
later change to it arrives through that cursor.

**V1.1 changed which of these paths is normal.** The reconstruction above is correct but its
*first* poll still costs the session's wall history - 7.1 MB at 12 minutes, 33.8 MB at 75, and
growing all day - so the screen got slower the longer the collector had been up, which is exactly
backwards. The collector now publishes its live candidate dictionary in a compact state file
(`checkpoint.py`), replaced atomically every sample. When that file is present and belongs to this
session, the set is **read** rather than reconstructed: no walk, no verification needed, cost
independent of session length, and each candidate carries its *current* size instead of the size
it had when its OPENED row was written. The reconstruction below stays as the fallback for an
older session, a collector that predates the state file, or a state file that cannot be trusted -
and it is still the thing that makes the fallback honest rather than approximate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN
from ..market_structure_v0.walls import ACTIVE, ENDED, INTERRUPTED, OPENED, UPDATED
from . import checkpoint as CP
from . import journal as J
from .wallrule import VALUES_CHECKPOINT, VALUES_JOURNAL_OPEN_ROW

#: Ceiling on the first backward walk. The wall stream was measured at about 2.3 GB/day, so this
#: is roughly the last two and a half hours of a busy session. It is a bound on work, not a
#: statement about the data: whether the walk was enough is decided by the verification, never by
#: the budget being generous.
DEFAULT_SCAN_BUDGET_BYTES = 256 << 20

#: How the active set came to be trusted.
#: The collector published it. It holds the writer lock, so its dictionary *is* the authority for
#: "resting now" - this is a read, not a reconstruction, and there is nothing to verify.
VERIFIED_BY_STATE_FILE = "COLLECTOR_STATE_CHECKPOINT"
VERIFIED_BY_STREAM_START = "STREAM_START"
VERIFIED_BY_AUTHORITY_COUNT = "AUTHORITY_ACTIVE_COUNT"
VERIFIED_BY_FOLLOWING = "FOLLOWED_FROM_VERIFIED_STATE"
#: Every candidate the initial scan could not reach has since been seen closing.
VERIFIED_BY_MISSING_RETIRED = "MISSING_CANDIDATES_ALL_CLOSED"
NOT_VERIFIED_BUDGET = "SCAN_BUDGET_EXHAUSTED"
NOT_VERIFIED_NO_AUTHORITY = "NO_AUTHORITY_RECORD_IN_WINDOW"


def _key(payload: dict[str, Any]) -> tuple[str, str]:
    """`(side, price)` with the price kept as the collector's own text.

    The collector writes canonical plain decimals, so the string is a stable identity and
    re-parsing it to compare would only add a way to disagree with the writer.
    """
    return str(payload.get("side") or ""), str(payload.get("price") or "")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _is_resting(payload: dict[str, Any]) -> bool:
    """Whether this row leaves the candidate resting.

    `status` is the contract's own field and is checked first; `event` is the tiebreaker for an
    `UPDATED` row, which only exists when the collector ran with `--wall-updates`.
    """
    status = str(payload.get("status") or "")
    if status == ACTIVE:
        return True
    if status in (ENDED, INTERRUPTED):
        return False
    return str(payload.get("event") or "") in (OPENED, UPDATED)


@dataclass
class WallSet:
    """The reconstructed resting candidates and the evidence for believing the set is whole."""

    resting: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    coverage: str = UNKNOWN
    verified_by: str | None = None
    unverified_reason: str | None = None
    authority_active: int | None = None
    authority_seq: int | None = None
    authority_age_ms: int | None = None
    reconstructed_at_authority: int | None = None
    missing_count: int | None = None
    scanned_bytes: int = 0
    scanned_records: int = 0
    #: Candidates the initial scan missed that have since been observed closing.
    missing_retired: int = 0
    #: Which moment the candidates' figures describe. The checkpoint is current; a journal
    #: reconstruction gives each resting candidate's OPENED row, which can be far older.
    values_as_of: str = VALUES_JOURNAL_OPEN_ROW
    #: The checkpoint this set was read from, when it was.
    state: CP.StateFile | None = None
    #: Transitions found in the journal after the checkpoint's `seq`, and whether that tail was
    #: read to its end. Normally zero: journal writes are buffered for up to a second while the
    #: state file is replaced immediately, so the file is usually ahead of the stream.
    tail_records: int = 0
    tail_bytes: int = 0
    tail_complete: bool = True
    #: Set when the collector's own list was cut by its bound. The count stays exact.
    truncated: bool = False
    #: The collector's continuity ledger, as published. Empty when there is none, which is the
    #: state a `v1-1` checkpoint and every journal reconstruction are in: a reconstruction reads
    #: `wall` rows, and the ledger deliberately never travels in one.
    continuity: dict[str, Any] = field(default_factory=dict)
    window_first_receive_ms: int | None = None
    last_seq: int | None = None
    #: Receive time of the newest wall row read. The derived stream and the wall stream are
    #: flushed by separate writers, so the two heads can differ by up to a flush interval, and
    #: anything measured against "the latest sample" has to publish that skew rather than hide it.
    last_receive_ms: int | None = None
    transitions_applied: int = 0

    @property
    def count(self) -> int:
        return len(self.resting)

    def rows(self) -> list[dict[str, Any]]:
        return list(self.resting.values())


@dataclass
class WallFollower:
    """Per-session wall state, scanned once and then followed forwards.

    One instance per output root. Held for the life of the preview process, which is what makes
    the steady-state cost proportional to new bytes rather than to session length.
    """

    scan_budget_bytes: int = DEFAULT_SCAN_BUDGET_BYTES
    session_id: str | None = None
    state: WallSet = field(default_factory=WallSet)
    cursor: J.Cursor = field(default_factory=J.Cursor)

    def refresh(self, root: Path, session: J.SessionRef, *, now_ms: int,
                state: CP.StateFile | None = None) -> WallSet:
        """The resting set. Read from the collector's checkpoint when there is a usable one."""
        state = (CP.read(root, now_ms=now_ms, session_id=session.session_id)
                 if state is None else state)
        # A stale checkpoint is still used, and deliberately so. Stale means the collector stopped
        # writing, and the right screen for that is its last state plus "the collector stopped" -
        # not a 30 MB backward walk that reconstructs the same thing from a stream nobody is
        # appending to. The staleness travels to the view, which refuses to call the feed live.
        if state.present and state.reason in (CP.USABLE, CP.STALE):
            return self._from_state(root, session, state)
        if session.session_id != self.session_id:
            self.session_id = session.session_id
            self.state = self._initial_scan(root, session, now_ms=now_ms)
            self.cursor = J.Cursor().at_end(root, "wall", session)
            return self.state
        # Taken before the new transitions are applied: a set that becomes provable *during*
        # this poll must keep the reason it became provable, not be relabelled as inherited.
        was_complete = self.state.coverage == COMPLETE
        records, touched = self.cursor.advance(root, "wall", session)
        self._apply_forward(records)
        self.state.scanned_bytes = touched
        self.state.scanned_records = len(records)
        # The authority row is re-read every poll. Reading it once at startup and leaving it on
        # screen would show a count and an age that stopped moving, which reads as current and is
        # not: a figure that says "48 s ago" forever is worse than no figure.
        authority, authority_seq, authority_ms = self._authority(root, session)
        self.state.authority_active = authority
        self.state.authority_seq = authority_seq
        self.state.authority_age_ms = (None if authority_ms is None
                                       else max(0, now_ms - authority_ms))
        if was_complete:
            self.state.verified_by = VERIFIED_BY_FOLLOWING
        self.state.values_as_of = VALUES_JOURNAL_OPEN_ROW
        return self.state

    # ------------------------------------------------------------------ state file

    def _from_state(self, root: Path, session: J.SessionRef, state: CP.StateFile) -> WallSet:
        """The set as the collector published it, plus any journal tail newer than it.

        No verification step appears here and none is needed: the collector is not reconstructing
        the set, it is reporting the dictionary it maintains. The things that *can* be wrong are
        checked in `checkpoint.read` before this is reached - wrong session, wrong shape, a file
        claiming to be authority - and the one thing left is that the published list may have been
        cut by its bound, which is carried through as `truncated` rather than smoothed over.
        """
        # The follower's reconstruction state is dropped: mixing a read set with a previously
        # reconstructed one would produce a set that is neither, and the cursor's position means
        # nothing now that transitions are no longer how the set is maintained.
        self.session_id = session.session_id
        self.cursor = J.Cursor()
        resting: dict[tuple[str, str], dict[str, Any]] = {}
        for payload in state.wall_items:
            resting[_key(payload)] = payload
        new_state = WallSet(
            resting=resting, coverage=COMPLETE, verified_by=VERIFIED_BY_STATE_FILE,
            values_as_of=VALUES_CHECKPOINT, state=state, truncated=state.truncated,
            missing_count=0, authority_active=state.active_count,
            authority_seq=state.seq, authority_age_ms=state.age_ms,
            reconstructed_at_authority=len(resting),
            last_seq=state.seq, last_receive_ms=state.written_ms,
            scanned_bytes=state.bytes_read, scanned_records=len(state.wall_items),
            continuity=state.continuity)
        if state.seq is not None:
            records, touched, complete = J.records_after_seq(root, "wall", session,
                                                             after_seq=state.seq)
            new_state.tail_records = len(records)
            new_state.tail_bytes = touched
            new_state.tail_complete = complete
            new_state.scanned_bytes += touched
            self.state = new_state
            self._apply_forward(records)
        else:
            self.state = new_state
        return self.state

    # ------------------------------------------------------------------ forward

    def _apply_forward(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            if record.get("kind") != "wall":
                continue
            payload = record.get("payload") or {}
            key = _key(payload)
            if _is_resting(payload):
                self.state.resting[key] = payload
            else:
                if self.state.resting.pop(key, None) is None:
                    # A close for a key this follower never held. Since the cursor starts exactly
                    # where the scan stopped, there is no gap between them, so the only candidate
                    # this can be is one the scan's window did not reach. Each such close retires
                    # one of the missing candidates, and when none is left the set is whole -
                    # which is how a PARTIAL reconstruction becomes provably COMPLETE while the
                    # preview is simply left running.
                    self._retire_missing()
            self.state.transitions_applied += 1
            seq = record.get("seq")
            if isinstance(seq, int):
                self.state.last_seq = seq
            receive_ms = record.get("receive_ms")
            if isinstance(receive_ms, int):
                self.state.last_receive_ms = receive_ms

    def _retire_missing(self) -> None:
        if self.state.coverage == COMPLETE or self.state.missing_count is None:
            return
        self.state.missing_count = max(0, self.state.missing_count - 1)
        self.state.missing_retired += 1
        if self.state.missing_count == 0:
            self.state.coverage = COMPLETE
            self.state.verified_by = VERIFIED_BY_MISSING_RETIRED
            self.state.unverified_reason = None

    # ------------------------------------------------------------------ backward

    def _initial_scan(self, root: Path, session: J.SessionRef, *, now_ms: int) -> WallSet:
        authority, authority_seq, authority_ms = self._authority(root, session)
        state = WallSet(authority_active=authority, authority_seq=authority_seq,
                        authority_age_ms=None if authority_ms is None
                        else max(0, now_ms - authority_ms))

        files = J.stream_files(root, "wall", session.session8)
        if not files:
            # No wall file at all is not a failure: a session this young, or a book that never
            # synchronized, has simply produced no candidate yet. That is a complete empty set.
            state.coverage = COMPLETE
            state.verified_by = VERIFIED_BY_STREAM_START
            state.missing_count = 0
            return state

        latest: dict[tuple[str, str], dict[str, Any]] = {}
        at_authority: dict[tuple[str, str], bool] = {}
        reached_start = False
        for item in reversed(files):
            exhausted = False
            for line, cost in J.reverse_lines(item.path):
                state.scanned_bytes += cost
                record = J.decode(line)
                if record is None:
                    if state.scanned_bytes >= self.scan_budget_bytes:
                        exhausted = True
                        break
                    continue
                state.scanned_records += 1
                payload = record.get("payload") or {}
                key = _key(payload)
                seq = record.get("seq")
                if state.last_seq is None and isinstance(seq, int):
                    state.last_seq = seq
                if state.last_receive_ms is None and isinstance(record.get("receive_ms"), int):
                    state.last_receive_ms = int(record["receive_ms"])
                if key not in latest:
                    latest[key] = payload
                if (authority_seq is not None and isinstance(seq, int) and seq < authority_seq
                        and key not in at_authority):
                    at_authority[key] = _is_resting(payload)
                receive_ms = record.get("receive_ms")
                if isinstance(receive_ms, int):
                    state.window_first_receive_ms = receive_ms
                if state.scanned_bytes >= self.scan_budget_bytes:
                    exhausted = True
                    break
            if exhausted:
                break
            if item is files[0]:
                reached_start = True

        state.resting = {key: payload for key, payload in latest.items() if _is_resting(payload)}

        if reached_start:
            state.coverage = COMPLETE
            state.verified_by = VERIFIED_BY_STREAM_START
            state.missing_count = 0
            return state

        if authority is not None and authority_seq is not None:
            rebuilt = sum(1 for resting in at_authority.values() if resting)
            state.reconstructed_at_authority = rebuilt
            # The walk cannot invent a resting candidate, so it can only undercount. Equality
            # therefore proves the window reached every candidate the collector was holding.
            state.missing_count = max(0, authority - rebuilt)
            if rebuilt == authority:
                state.coverage = COMPLETE
                state.verified_by = VERIFIED_BY_AUTHORITY_COUNT
            else:
                state.coverage = PARTIAL
                state.unverified_reason = NOT_VERIFIED_BUDGET
            return state

        state.coverage = PARTIAL
        state.unverified_reason = NOT_VERIFIED_NO_AUTHORITY
        return state

    def _authority(self, root: Path, session: J.SessionRef
                   ) -> tuple[int | None, int | None, int | None]:
        """`walls.active`, its envelope `seq` and its receive time, from the newest stats row."""
        record, _ = J.last_record(root, "storage_stats", session)
        if record is None:
            return None, None, None
        payload = record.get("payload") or {}
        walls = payload.get("walls") or {}
        active = walls.get("active")
        seq = record.get("seq")
        receive_ms = record.get("receive_ms")
        return (active if isinstance(active, int) else None,
                seq if isinstance(seq, int) else None,
                receive_ms if isinstance(receive_ms, int) else None)


# --------------------------------------------------------------------------- selection


@dataclass(frozen=True)
class WallFilter:
    """How much of the selected set the operator wants on screen. One threshold, on notional.

    This is a **zoom, not a rule**. Whether something is a wall at all is decided by the frozen
    `lm-wall.v2` selection rule in `wallrule.py`, which an operator cannot move. This filter then
    decides how much of that set fits on a ladder, and it is allowed to move precisely because it
    changes nothing about what qualified.

    V1 also exposed a `min_multiple` here. It is gone: the multiple floor now lives in the frozen
    rule, and two different multiple floors - one frozen, one adjustable - is a trap, because a
    screen showing fewer walls would not say which of the two had removed them.
    """

    min_notional_usdt: Decimal

    def keeps(self, wall: dict[str, Any]) -> bool:
        notional = _decimal(wall.get("notional_usdt"))
        return notional is not None and notional >= self.min_notional_usdt

    def view(self) -> dict[str, Any]:
        return {"min_notional_usdt": str(self.min_notional_usdt),
                "is_display_filter_not_rule": True,
                "applies_after": "lm-wall.v2"}


#: Kept at 500,000 USDT. Chosen on the measured candidate distribution (2026-10-04: 93 candidates
#: at 200k, 43 at 300k, 17 at 500k, 3 at 1M) as the point where each side fits on a ladder and
#: still shows more than one line, and left here deliberately: it is the operator's zoom, and the
#: frozen rule's own floor (250,000) sits below it so the response can always report how many
#: walls exist beyond the ones being drawn.
DEFAULT_MIN_NOTIONAL_USDT = Decimal("500000")
DEFAULT_WALL_FILTER = WallFilter(min_notional_usdt=DEFAULT_MIN_NOTIONAL_USDT)

__all__ = ["DEFAULT_SCAN_BUDGET_BYTES", "DEFAULT_WALL_FILTER", "DEFAULT_MIN_NOTIONAL_USDT",
           "WallSet", "WallFollower", "WallFilter", "VERIFIED_BY_STATE_FILE",
           "VERIFIED_BY_STREAM_START",
           "VERIFIED_BY_AUTHORITY_COUNT", "VERIFIED_BY_FOLLOWING", "VERIFIED_BY_MISSING_RETIRED",
           "NOT_VERIFIED_BUDGET",
           "NOT_VERIFIED_NO_AUTHORITY"]
