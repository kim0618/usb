"""Incremental, read-only reader of a Context Collector root, for the forward recorder.

`frames.second_frame` loads every record of every session into memory before it can yield one
second (measured: 3.9 GB RSS for 15 hours). A recorder that runs for weeks cannot do that, so this
module reads the same two streams (`context`, `wall_r0`) a few records at a time, from a cursor,
and rebuilds exactly the seconds `context_collector_v1.reader.seconds(with_r0=True)` yields.

**R0's merge is restated, not changed.** Inside one session, records are taken in envelope `seq`
order; a `wall_r0` row goes to `pending`; a `context` record with a sample applies the pending rows
whose `v0_seq` is below its `v0_derived_seq` (resting rows are set, terminal rows popped) and then
selects `walls_r0` with `wallrule.select`. A new session starts from empty state. The parity of the
seconds this module yields with `reader.seconds` is a test, on real journals.

**Why a cursor needs state, not only an offset.** The resting set is cumulative from the start of a
session. A reader that starts in the middle of a session cannot know which walls were already
resting, so a cursor here is a *checkpoint*: per kind the segment and byte offset after the last
record consumed, plus the resting set and the pending rows at that point. Without a checkpoint a
session can only be read from its first segment; when retention has removed that segment, the
session is reported `UNRECOVERABLE_START` and skipped, never read with a guessed resting set.

**Reading a live journal.** The collector buffers each kind separately and flushes all of them
once a second, so a `context` record can be on disk a moment before a `wall_r0` row with a lower
`seq`. Context is therefore read first and wall rows second, and a context record is consumed only
when it is at least `HOLDBACK_MS` older than the newest context sample read, and only when the wall
stream has been read past its `seq` (or to its end). A session that is over (a later session
exists) is read to its last complete line. A torn trailing line is never consumed.

Nothing here writes to the collector root, takes its lock or opens a socket.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from ...context_collector_v1 import reader as CR
from ...context_collector_v1.contract import CONTEXT_KIND, WALL_R0_KIND
from ...context_collector_v1.wallr0 import is_resting
from ...liquidity_map import wallrule as R
from ..market_context_r0 import layers as R0L
from . import frames as F

KINDS = (CONTEXT_KIND, WALL_R0_KIND)
HOLDBACK_MS = 10_000
_SUFFIXES = (".jsonl.open", ".jsonl.gz", ".jsonl")
_NAME = re.compile(r"^(?P<kind>.+)-(?P<date>\d{8})-(?P<s8>[0-9a-f]{8})-(?P<n>\d{5})"
                   r"(?P<suffix>\.jsonl\.open|\.jsonl\.gz|\.jsonl)$")
#: The wall fields `joint_family` reads (`distance_bps`, `bin_high`, the bin key) plus the two the
#: recorder keeps as diagnostics. Dropping the rest changes no condition and saves most of the RSS.
WALL_FIELDS = ("bin_low", "bin_high", "price", "distance_bps", "observed_persistence_ms",
               "coverage")


class UnrecoverableStart(RuntimeError):
    """A session whose first segment is gone and for which no checkpoint exists."""


# ----------------------------------------------------------------------------- segments
@dataclass(frozen=True)
class Segment:
    kind: str
    s8: str
    n: int
    path: Path
    live: bool            # `.jsonl.open`: still being written


def list_segments(root: Path, kind: str) -> dict[str, dict[int, Segment]]:
    """`{session8: {n: Segment}}`. A sealed name wins over `.open` for the same n (rename race)."""
    out: dict[str, dict[int, Segment]] = {}
    directory = root / kind
    if not directory.is_dir():
        return out
    for path in directory.iterdir():
        m = _NAME.match(path.name)
        if not m or m["kind"] != kind:
            continue
        seg = Segment(kind, m["s8"], int(m["n"]), path, m["suffix"] == ".jsonl.open")
        have = out.setdefault(seg.s8, {}).get(seg.n)
        if have is None or (have.live and not seg.live) or \
                (not have.live and not seg.live and seg.path.name.endswith(".jsonl")):
            out[seg.s8][seg.n] = seg          # plain sealed file is the source of its .gz
    return out


def _open(seg: Segment):
    return gzip.open(seg.path, "rb") if seg.path.name.endswith(".gz") else open(seg.path, "rb")


def read_lines(seg: Segment, offset: int, limit: int) -> tuple[list[tuple[dict, int]], bool]:
    """Complete records after `offset` (uncompressed bytes), with the offset after each one.

    Returns `(records, at_end)`; `at_end` is True when the last complete line was reached.
    """
    out: list[tuple[dict, int]] = []
    pos = offset
    with _open(seg) as handle:
        if offset:
            handle.seek(offset)
        while len(out) < limit:
            line = handle.readline()
            if not line:
                return out, True
            if not line.endswith(b"\n"):
                return out, True              # torn tail of a live file: not data yet
            pos += len(line)
            if line.strip():
                out.append((json.loads(line), pos))
        return out, False


# ----------------------------------------------------------------------------- one session
@dataclass
class SessionCursor:
    """Everything needed to resume one session exactly where it was left."""

    s8: str
    pos: dict[str, list[int]] = field(default_factory=dict)       # kind -> [segment n, offset]
    resting: dict[str, dict] = field(default_factory=dict)         # "side|price" -> row
    pending: list[dict] = field(default_factory=list)
    last_ctx_seq: int | None = None
    session_id: str | None = None
    first_receive_ms: int | None = None
    samples: int = 0
    last_sample_ms: int | None = None
    done: bool = False

    def to_json(self) -> dict:
        return {"s8": self.s8, "pos": self.pos, "resting": self.resting, "pending": self.pending,
                "last_ctx_seq": self.last_ctx_seq, "session_id": self.session_id,
                "first_receive_ms": self.first_receive_ms, "samples": self.samples,
                "last_sample_ms": self.last_sample_ms, "done": self.done}

    @classmethod
    def from_json(cls, d: dict) -> "SessionCursor":
        return cls(**d)


@dataclass
class Sample:
    """One second as the recorder keeps it: R0's `SecondSample` plus the per-sample fields the
    generation gate and the diagnostics read. Built with `frames._sample`, the proven adapter."""

    s: R0L.SecondSample
    session_id: str
    seq: int
    generation: Any
    absorption: Any
    liquidity_state: Any
    flow_state: Any
    book_state: Any
    band01_bid_coverage: Any
    band01_ask_coverage: Any
    flow60_coverage: Any
    collector_state: Any


#: Stands in for every wall outside R0's 1-10 bp band. `joint_family` reads such a wall only as a
#: key (is the anchor's bin still present at s+W?) and through `distance_bps` to reject it as an
#: anchor wall, which the sentinel's distance does exactly as the real one would.
FAR_WALL = {"distance_bps": "Infinity", "far_outside_band": True}


def _compact(bins: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for low, w in bins.items():
        d = abs(float(w["distance_bps"]))
        near = R0L.WALL_PROX_BPS_LO <= d <= R0L.WALL_PROX_BPS_HI
        out[sys.intern(low)] = w if near else FAR_WALL
    return out


def _slim(walls: list[dict]) -> list[dict]:
    return [{k: w.get(k) for k in WALL_FIELDS} for w in walls]


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def to_sample(record: dict, walls_r0: dict[str, list[dict]], *, compact: bool = False) -> Sample:
    payload = record["payload"]
    second = CR.Second(sample_ms=payload["sample_ms"], session_id=record["session_id"],
                       seq=record["seq"], v0_derived_seq=payload.get("v0_derived_seq"),
                       context=payload, walls_display={}, walls_r0=walls_r0)
    book = payload.get("book") or {}
    d01 = next((d for d in (payload.get("depth") or []) if d.get("band_pct") == "0.1"), {})
    flow = payload.get("flow") or {}
    f60 = (flow.get("windows") or {}).get("60s") or {}
    base = F._sample(second)
    if compact:
        base.ask_bins = _compact(base.ask_bins)
        base.bid_bins = _compact(base.bid_bins)
    return Sample(s=base, session_id=record["session_id"], seq=record["seq"],
                  generation=book.get("generation"),
                  absorption=(payload.get("absorption") or {}).get("state"),
                  liquidity_state=(payload.get("liquidity") or {}).get("state"),
                  flow_state=flow.get("state"), book_state=book.get("state"),
                  band01_bid_coverage=d01.get("bid_coverage"),
                  band01_ask_coverage=d01.get("ask_coverage"),
                  flow60_coverage=f60.get("coverage"),
                  collector_state=(payload.get("collector") or {}).get("state"))


def _time_of(record: dict) -> int:
    payload = record.get("payload") or {}
    return payload["sample_ms"] if payload.get("sample_ms") is not None else record["receive_ms"]


class SessionReader:
    """Reads one session forward from a cursor and yields `Sample`s in envelope order."""

    def __init__(self, root: Path, cursor: SessionCursor, *, compact: bool = False):
        self.root = root
        self.c = cursor
        self.compact = compact

    # ---------------------------------------------------------------- reading
    def _segments(self, kind: str) -> dict[int, Segment]:
        return list_segments(self.root, kind).get(self.c.s8, {})

    def _start_pos(self) -> None:
        for kind in KINDS:
            if kind in self.c.pos:
                continue
            segs = self._segments(kind)
            if segs and min(segs) != 1:
                raise UnrecoverableStart(f"{kind} of session {self.c.s8} starts at segment "
                                         f"{min(segs)}; the resting set before it is unknown")
            self.c.pos[kind] = [1, 0]

    def _read(self, kind: str, limit: int) -> tuple[list[tuple[dict, tuple[int, int]]], bool]:
        """Records after the cursor of `kind`, across segments. `at_end` = nothing more on disk."""
        segs = self._segments(kind)
        n, off = self.c.pos[kind]
        out: list[tuple[dict, tuple[int, int]]] = []
        while len(out) < limit:
            seg = segs.get(n)
            later = sorted(k for k in segs if k > n)
            if seg is None:
                if later and off == 0:
                    n, off = later[0], 0          # a sequence number the writer skipped
                    continue
                if later:
                    raise UnrecoverableStart(f"{kind} segment {n} of {self.c.s8} vanished "
                                             "while being read")
                return out, True
            try:
                rows, at_end = read_lines(seg, off, limit - len(out))
            except FileNotFoundError:                 # sealed or compressed under us
                segs = self._segments(kind)
                continue
            for rec, end in rows:
                out.append((rec, (n, end)))
            if rows:
                off = rows[-1][1]
            if not at_end:
                return out, False
            if seg.live or not later:
                return out, True
            n, off = later[0], 0
        return out, False

    # ---------------------------------------------------------------- merging
    def step(self, *, session_over: bool, limit: int = 4000) -> tuple[list[Sample], bool]:
        """Consume what can safely be consumed. Returns `(samples, progressed)`."""
        self._start_pos()
        # At least a minute of context per read, so a record HOLDBACK_MS older than the newest
        # one read always exists and a small batch size cannot stall a live session.
        ctx, ctx_end = self._read(CONTEXT_KIND, max(limit, 64))
        walls, walls_end = self._read(WALL_R0_KIND, max(limit * 4, 256))   # read after context
        if session_over:
            bound = None
        else:
            newest = max((_time_of(r) for r, _ in ctx), default=None)
            bound = None if newest is None else newest - HOLDBACK_MS
        out: list[Sample] = []
        wi = 0
        progressed = False
        consumed_all = True
        for rec, (n, end) in ctx:
            if self.c.session_id is None:
                self.c.session_id = rec["session_id"]
                self.c.first_receive_ms = rec["receive_ms"]
            if bound is not None and _time_of(rec) > bound:
                consumed_all = False
                break
            while wi < len(walls) and walls[wi][0]["seq"] < rec["seq"]:
                wrec, (wn, wend) = walls[wi]
                self.c.pending.append(wrec["payload"])
                self.c.pos[WALL_R0_KIND] = [wn, wend]
                wi += 1
                progressed = True
            if wi == len(walls) and not walls_end:
                consumed_all = False                    # wall rows below this seq may be unread
                break
            sample = self._consume(rec)
            self.c.pos[CONTEXT_KIND] = [n, end]
            self.c.last_ctx_seq = rec["seq"]
            progressed = True
            if sample is not None:
                out.append(sample)
        if session_over and ctx_end and walls_end and consumed_all:
            # Wall rows after the last context record change nothing any second reads.
            self.c.done = True
        return out, progressed

    def _consume(self, record: dict) -> Sample | None:
        """`reader.seconds`, the body of its loop for a `context` record, restated."""
        payload = record["payload"]
        if payload.get("sample_ms") is None:
            return None
        derived_seq = payload.get("v0_derived_seq")
        still = []
        for row in self.c.pending:
            if derived_seq is not None and row["v0_seq"] < derived_seq:
                key = f'{row.get("side") or ""}|{row.get("price") or ""}'
                if is_resting(row):
                    self.c.resting[key] = row
                else:
                    self.c.resting.pop(key, None)
            else:
                still.append(row)
        self.c.pending = still
        book = payload.get("book") or {}
        rows = list(self.c.resting.values())
        walls_r0 = {}
        for side in (R.SIDE_BID, R.SIDE_ASK):
            walls_r0[side] = _slim(R.select(rows, side=side, mid=_decimal(book.get("mid")),
                                            latest_sample_ms=payload["sample_ms"],
                                            known_low=_decimal(book.get("known_low")),
                                            known_high=_decimal(book.get("known_high"))).walls)
        self.c.samples += 1
        self.c.last_sample_ms = payload["sample_ms"]
        return to_sample(record, walls_r0, compact=self.compact)


# ----------------------------------------------------------------------------- whole root
def sessions_in_order(root: Path, known: dict[str, SessionCursor]) -> list[str]:
    """Session8s ordered by their first record's `receive_ms`, as `reader.seconds` orders them."""
    s8s = set()
    for kind in KINDS:
        s8s |= set(list_segments(root, kind))
    first: dict[str, int] = {}
    for s8 in s8s:
        if s8 in known and known[s8].first_receive_ms is not None:
            first[s8] = known[s8].first_receive_ms
            continue
        best = None
        for kind in KINDS:
            segs = list_segments(root, kind).get(s8, {})
            if not segs:
                continue
            try:
                rows, _ = read_lines(segs[min(segs)], 0, 1)
            except FileNotFoundError:
                continue
            if rows:
                ms = rows[0][0]["receive_ms"]
                best = ms if best is None else min(best, ms)
        if best is not None:
            first[s8] = best
    return sorted(first, key=lambda s: (first[s], s))


def iterate_reference(root: Path) -> Iterator[Sample]:
    """The batch reader's seconds through the same adapter: the parity reference for tests."""
    for second in CR.seconds(root):
        rec = {"payload": second.context, "session_id": second.session_id, "seq": second.seq}
        yield to_sample(rec, {k: _slim(v) for k, v in second.walls_r0.items()})


__all__ = ["SessionCursor", "SessionReader", "Sample", "UnrecoverableStart", "list_segments",
           "sessions_in_order", "iterate_reference", "HOLDBACK_MS", "to_sample"]
