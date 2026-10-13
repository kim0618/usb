"""The forward context journal: the one thing this package writes.

Purpose, stated plainly because it decides the schema: Market Context R0 could answer one of its
four research questions. The other three were not refused by the model, they were refused by the
archive - 796 of 799 resistance approaches fell `OUTSIDE_JOURNAL` because Layer B was 3.61 hours
long. R0 also established that the gap cannot be closed with history: Binance's public archive sells
aggTrades, bookTicker and percentage depth summaries, and **not** level-resolution quotes. The book
layer exists only if someone is collecting while it happens, and filling a 30-event family gate
needs roughly three to four weeks of it.

So this file exists to not lose that material a second time. It is **not** a trade journal: it is
not linked to an order, it does not require a manual trade to exist, and it holds no intent, no
decision and no outcome. It is a context tape.

Four properties, all of them chosen so a later analysis can trust the tape:

* **Append-only, one file per UTC day, flushed and fsynced per record.** Never rewritten in place.
  A torn final line is dropped by the reader rather than repaired, which is the rule the C1 store
  and the V0 journal already use here.
* **A gap is written, not skipped.** A minute whose liquidity layer was `STALE` still produces a
  record, carrying that state. If the dropouts were omitted the tape would silently become a
  survivorship sample of the market's calm minutes, which is exactly the bias that would make the
  Q1-Q3 re-measurement wrong in a way no one could see.
* **Nothing in the panel reads it back.** The journal is an output of this process and an input to
  a later study, and keeping that one-way means a bug here cannot change what the screen shows.
* **One writer per root, proved rather than assumed.** Two processes appending here would not tear
  the file - they would ruin its meaning, because the cadence rule lives in each writer's own
  `cursors` and two of them produce two interleaved cadences with a duplicate for most minutes and
  `written_because` values that describe neither. So a writer takes an advisory lock on the root
  and the second one fails closed. It fails closed *quietly*: this writer rides on a read-only
  panel, so losing the lock stops the writing and nothing else - every GET still answers, and the
  loss is reported as a state a screen can show. See `WriterLock`.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from . import contract as C

#: Where the tape is written. Set explicitly; with it unset the journal is off and says so, so
#: that running the preview never writes into a path nobody chose.
ROOT_ENV = "MC_V1_JOURNAL_ROOT"

#: The advisory lock that makes this writer the only one for a root.
LOCK_FILENAME = ".writer.lock"
LOCK_VERSION = "mc-v1-journal-lock.1"

#: How long without a line before a reader should be told the tape is stale.
#:
#: The cadence is one record per closed minute, so a minute and a half is already unusual and
#: three minutes cannot be explained by a quiet tape. This is a **display** threshold - it never
#: gates a write, because a journal that stops writing when it looks stale would turn a reporting
#: question into a data loss.
WRITE_STALE_MS = 180_000

#: Every state the writer can be in, worst first. `view()` reports the first that applies, and
#: `JOURNAL_STATES` is the closed list a reader can switch on.
STATE_OFF = "OFF"
STATE_REFUSED = "REFUSED"
STATE_AUTHORITY_LOST = "AUTHORITY_LOST"
STATE_ERROR = "ERROR"
STATE_STALE = "STALE"
STATE_WRITING = "WRITING"
STATE_WAITING = "WAITING"
JOURNAL_STATES: tuple[str, ...] = (STATE_OFF, STATE_REFUSED, STATE_AUTHORITY_LOST, STATE_ERROR,
                                   STATE_STALE, STATE_WRITING, STATE_WAITING)

#: The one sentence a reader needs about a lock file left behind by a dead process.
LOCK_RECOVERY_NOTE = (
    "A lock file is not a lock. `flock` lives on the open file description, so the kernel drops "
    "it when the holder exits for any reason - clean stop, crash, SIGKILL - and the next writer "
    "acquires immediately. There is therefore no stale-lock timeout and no break-the-lock path: "
    "a lock file naming a dead pid is read as a hint and overwritten in place by whoever next "
    "acquires it, and this writer never removes or replaces another process's lock file. The one "
    "case the lock alone cannot cover is the file being deleted or replaced underneath a live "
    "holder, because the replacement is a different inode and locks cleanly; the holder therefore "
    "re-checks the inode before every append and stops writing when it can no longer prove it "
    "owns the root.")


def journal_root() -> Path | None:
    raw = os.environ.get(ROOT_ENV)
    return None if not raw else Path(raw).expanduser()


def day_file(root: Path, written_at_ms: int) -> Path:
    day = time.strftime("%Y%m%d", time.gmtime(written_at_ms / 1000))
    return root / f"context-{day}.jsonl"


def _level_record(structure: dict[str, Any]) -> dict[str, Any]:
    """The nearest structural level, whichever side it is on, plus the other side's distance.

    The contract asks for "nearest structural level" with its side and type. Which side that is
    changes minute to minute, so it is resolved here and the loser is kept too - a later analysis
    that only has the nearer one cannot tell a price pinned between two levels from a price in
    open space.
    """
    levels = (structure or {}).get("levels") or {}
    candidates = [levels.get("resistance"), levels.get("support")]
    live = [level for level in candidates
            if isinstance(level, dict) and level.get("available")]
    if not live:
        return {"available": False, "unavailable_reason": "NO_PUBLISHED_LEVEL",
                "side": None, "type": None, "price": None, "distance_pct": None,
                "other_side": None}
    nearest = min(live, key=lambda level: abs(float(level.get("distance_pct") or 0.0)))
    other = [level for level in live if level is not nearest]
    shape = lambda level: {
        "side": level.get("side"),
        "type": f'{level.get("lifecycle")}|{level.get("origin_kind")}',
        "lifecycle": level.get("lifecycle"),
        "origin_kind": level.get("origin_kind"),
        "price": level.get("price"),
        "distance_pct": level.get("distance_pct"),
        "level_id": level.get("level_id"),
        "confirm_tf": level.get("confirm_tf"),
        "strength": level.get("strength"),
        "touches": level.get("touches"),
    }
    return {"available": True, "unavailable_reason": None, **shape(nearest),
            "other_side": shape(other[0]) if other else None}


def _wall_record(side: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(side, dict):
        return {"state": C.UNKNOWN, "price": None, "notional_usdt": None,
                "distance_bps": None, "persistence_ms": None, "persistence_source": None,
                "continuity_status": None, "bin_low": None, "bin_high": None}
    wall = side.get("nearest_wall") or {}
    return {"state": side.get("wall_state"), "state_reason": side.get("wall_state_reason"),
            "price": wall.get("price"), "notional_usdt": wall.get("notional_usdt"),
            "distance_bps": wall.get("distance_bps"),
            "persistence_ms": wall.get("persistence_ms"),
            "persistence_source": wall.get("persistence_source"),
            "continuity_status": wall.get("continuity_status"),
            "carried_members": wall.get("carried_members"),
            "bin_low": wall.get("bin_low"), "bin_high": wall.get("bin_high"),
            "coverage": wall.get("coverage")}


def _flow_record(flow: dict[str, Any]) -> dict[str, Any]:
    windows = (flow or {}).get("windows") or {}
    out: dict[str, Any] = {}
    for label in C.FLOW_WINDOWS:
        window = windows.get(label) or {}
        out[f"w{label}"] = {"buy_usdt": window.get("buy_usdt"),
                            "sell_usdt": window.get("sell_usdt"),
                            "net_usdt": window.get("net_usdt"),
                            "imbalance_usdt": window.get("imbalance_usdt"),
                            "trades": window.get("trades"),
                            "coverage": window.get("coverage"),
                            "state": window.get("state")}
    return out


def record(payload: dict[str, Any], *, written_at_ms: int) -> dict[str, Any]:
    """One tape line out of one assembled panel payload.

    Every field is nullable and carries its reason when null, so a reader can always tell "the
    market had none of this" from "we did not see it".
    """
    layers = payload.get("layers") or {}
    structure = layers.get("PRICE") or {}
    liquidity = layers.get("LIQUIDITY") or {}
    flow = layers.get("FLOW") or {}
    derivatives = layers.get("DERIVATIVES") or {}
    row = structure.get("row") or {}
    sides = liquidity.get("sides") or {}
    oi_change = (derivatives.get("open_interest_change") or {}).get("windows") or {}

    return {
        "record": C.JOURNAL_RECORD,
        "schema_version": C.JOURNAL_SCHEMA_VERSION,
        "contract": C.CONTRACT_VERSION,
        "panel_version": payload.get("panel_version"),
        "written_at_ms": written_at_ms,
        "symbol": payload.get("symbol"),
        "price": {
            "last_closed_close": row.get("close"),
            "bar_open_ms": row.get("bar_open_ms"),
            "bar_close_ms": row.get("bar_close_ms"),
            "mark_price": (derivatives.get("basis") or {}).get("mark_price"),
            "mark_venue_time_ms": (derivatives.get("basis") or {}).get("venue_time_ms"),
            "book_mid": (liquidity.get("book") or {}).get("mid"),
        },
        "structure": {
            "state": structure.get("state"),
            "nearest": _level_record(structure),
            "trend_structure": row.get("trend_structure"),
            "breakout_state": (structure.get("events") or {}).get("breakout_state"),
            "breakout_level_id": (structure.get("events") or {}).get("breakout_level_id"),
            "breakout_age_min": (structure.get("events") or {}).get("breakout_age_min"),
            "retest_state": (structure.get("events") or {}).get("retest_state"),
            "failed_break_state": (structure.get("events") or {}).get("failed_break_state"),
            "range": structure.get("range"),
            "prev_session": structure.get("prev_session"),
            "active_level_count": row.get("active_level_count"),
            "atr_5m": row.get("atr_5m"),
            "atr_1h": row.get("atr_1h"),
            "series": {k: (structure.get("series") or {}).get(k)
                       for k in ("anchor_ms", "series_rows", "repaint_violations",
                                 "vanished_levels")},
        },
        "wall": {
            "state": liquidity.get("state"),
            "ask": _wall_record(sides.get("ASK")),
            "bid": _wall_record(sides.get("BID")),
            "vanished_this_reading": (flow.get("vanished") or {}).get("this_reading"),
            "absorption": flow.get("absorption"),
        },
        "flow": {"state": flow.get("state"), **_flow_record(flow),
                 "trade_stream": flow.get("trade_stream")},
        "depth": {
            "ask": [{"band_pct": band.get("band_pct"), "coverage": band.get("coverage"),
                     "observed_notional": band.get("observed_notional"),
                     "is_lower_bound": band.get("is_lower_bound")}
                    for band in ((sides.get("ASK") or {}).get("depth") or [])],
            "bid": [{"band_pct": band.get("band_pct"), "coverage": band.get("coverage"),
                     "observed_notional": band.get("observed_notional"),
                     "is_lower_bound": band.get("is_lower_bound")}
                    for band in ((sides.get("BID") or {}).get("depth") or [])],
            "observed_low": (liquidity.get("book") or {}).get("observed_low"),
            "observed_high": (liquidity.get("book") or {}).get("observed_high"),
            "observed_symmetric_pct":
                (liquidity.get("book") or {}).get("observed_symmetric_pct"),
        },
        "coverage": liquidity.get("coverage"),
        "derivatives": {
            "state": derivatives.get("state"),
            "open_interest": (derivatives.get("open_interest") or {}).get("base"),
            "open_interest_venue_time_ms":
                (derivatives.get("open_interest") or {}).get("venue_time_ms"),
            **{f"oi_change_{label}": (oi_change.get(label) or {}).get("base")
               for label in C.OI_CHANGE_WINDOWS},
            "funding_rate": (derivatives.get("funding") or {}).get("last_rate"),
            "next_funding_time_ms":
                (derivatives.get("funding") or {}).get("next_funding_time_ms"),
            "basis_usdt": (derivatives.get("basis") or {}).get("basis_usdt"),
            "premium_pct": (derivatives.get("basis") or {}).get("premium_pct"),
        },
        "quality": {name: {"state": (layer or {}).get("state"),
                           "reasons": (layer or {}).get("reasons")}
                    for name, layer in layers.items()},
    }


#: Fields of a record that, when they change, earn a line of their own between closed minutes.
#: Chosen as the things a later study anchors on: a new event, a new nearest level, or a wall
#: state transition. A changed figure is not a change - every figure changes every second.
CHANGE_KEYS: tuple[str, ...] = ("structure_breakout", "structure_retest", "structure_failed",
                                "nearest_level_id", "nearest_side", "wall_ask_state",
                                "wall_bid_state", "liquidity_state", "flow_state")


def change_signature(line: dict[str, Any]) -> tuple[Any, ...]:
    structure = line.get("structure") or {}
    nearest = structure.get("nearest") or {}
    wall = line.get("wall") or {}
    return (structure.get("breakout_state"), structure.get("retest_state"),
            structure.get("failed_break_state"), nearest.get("level_id"), nearest.get("side"),
            (wall.get("ask") or {}).get("state"), (wall.get("bid") or {}).get("state"),
            wall.get("state"), (line.get("flow") or {}).get("state"))


def lock_file(root: Path) -> Path:
    return root / LOCK_FILENAME


class JournalLocked(RuntimeError):
    """Another process already holds the writer lock for this journal root."""


class WriterLock:
    """One writer per journal root, enforced by an advisory `flock` held for the process's life.

    **Why the tape needs this at all.** Two processes appending to one `context-YYYYMMDD.jsonl`
    do not corrupt each other's lines - `O_APPEND` writes under a page are atomic enough that the
    file stays parseable. What they destroy is the thing the tape is collected for. The cadence
    rule is per-writer state: each `ForwardJournal` holds its own `cursors`, so two writers each
    believe the previous line was theirs and the file ends up with two interleaved cadences, a
    duplicate for most minutes, and `written_because` values that describe neither. A later study
    counting events off that file would count some of them twice and have no way to tell which.
    Market Context R0's questions need a tape whose record count means something, so the single
    writer is part of the data contract and not an operational nicety.

    **What was taken from `market_structure_v0.store.WriterLock`, and what was left there.**
    Taken, because they are properties of `flock` rather than of that collector: the lock is on an
    **inode**, not a path, so a deleted or replaced lock file lets a second writer lock a
    different inode and both processes then believe they are alone - which means the identity of
    the locked inode has to be remembered and re-checked (`verify`); and the file has to carry an
    identifying payload, so a refused writer can say *who* holds the root instead of only that
    somebody does. `acquire` retries once for the same reason that one does: between `open` and
    `flock` the file can be replaced, and a lock on an inode nothing can find protects nothing.

    Left behind deliberately, because this journal is not that store: no orphan `.open` recovery
    (this tape has no half-written files - one append, flushed and fsynced, is the whole unit of
    work), no compact state file and so no `live_writer_evidence` heuristic over a published pid,
    no session bookkeeping, and - the important one - **no raising out of the serving path**. The
    V0 collector exists to write, so losing authority stops the process. This writer rides on a
    read-only panel, so losing authority stops the *writing* and nothing else: the panel keeps
    serving every GET, and the loss is reported in `view()` as a state a screen can show.
    """

    def __init__(self, root: Path) -> None:
        self.path = lock_file(root)
        self._fd: int | None = None
        #: `(st_dev, st_ino)` of the file this lock is actually held on.
        self.identity: tuple[int, int] | None = None
        #: What the lock file claimed when this writer was refused. Reported, never acted on.
        self.holder: dict[str, Any] | None = None
        self.acquired_ms: int | None = None
        self.session_id: str | None = None

    def read_holder(self) -> dict[str, Any] | None:
        """The lock file's first line, parsed. Never raises; it is only ever a hint.

        Only the first line, because `acquire` writes the payload at offset 0 without truncating
        - this package truncates nothing - so a shorter payload can leave a previous holder's
        tail behind it. The tail is ignored rather than cleaned up, since cleaning it would mean
        writing over a file this process may not own.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                first = handle.readline().strip()
        except OSError:
            return None
        if not first:
            return None
        try:
            payload = json.loads(first)
        except ValueError:
            return {"pid": first, "format": "UNPARSEABLE"}
        return payload if isinstance(payload, dict) else None

    def acquire(self, *, session_id: str, started_ms: int) -> None:
        """Take the lock, or raise `JournalLocked` naming whoever holds it.

        The root must already exist; creating it is the journal's single `mkdir`, so that a
        refused writer never leaves a directory behind as a side effect of being refused.
        """
        for attempt in (1, 2):
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as caught:
                os.close(fd)
                if caught.errno in (errno.EACCES, errno.EAGAIN):
                    self.holder = self.read_holder()
                    raise JournalLocked(
                        f"another writer holds {self.path} (holder: {self.holder})") from caught
                raise
            locked = os.fstat(fd)
            try:
                current = os.stat(self.path)
            except OSError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == (locked.st_dev,
                                                                            locked.st_ino):
                payload = {"pid": os.getpid(), "session_id": session_id,
                           "started_ms": started_ms, "lock_version": LOCK_VERSION,
                           "record": C.JOURNAL_RECORD}
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
                os.fsync(fd)
                self._fd = fd
                self.identity = (locked.st_dev, locked.st_ino)
                self.acquired_ms = started_ms
                self.session_id = session_id
                return
            # The file was replaced between the open and the lock. One more try, then refuse -
            # holding a lock on an inode nobody can reach is worse than not holding one, because
            # it reads as success.
            os.close(fd)
            if attempt == 2:
                self.holder = self.read_holder()
                raise JournalLocked(
                    f"{self.path} kept being replaced while it was being locked "
                    f"(holder: {self.holder})")

    def verify(self) -> bool:
        """Whether the lock file under the root is still the inode this writer locked."""
        if self._fd is None or self.identity is None:
            return False
        try:
            current = os.stat(self.path)
        except OSError:
            return False
        return (current.st_dev, current.st_ino) == self.identity

    def release(self) -> None:
        """Unlock and close. Idempotent, and a no-op on a lock that was never taken.

        Calling this is politeness, not the mechanism: the kernel releases the lock when the file
        descriptor closes, which it does when the process exits however it exits. That is why a
        crash needs no recovery rule beyond `LOCK_RECOVERY_NOTE`.
        """
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None
            self.identity = None

    def view(self) -> dict[str, Any]:
        return {"path": str(self.path), "held": self._fd is not None,
                "pid": os.getpid() if self._fd is not None else None,
                "session_id": self.session_id, "acquired_ms": self.acquired_ms,
                "inode": None if self.identity is None else self.identity[1],
                "lock_version": LOCK_VERSION, "holder": self.holder}


class ForwardJournal:
    """The append-only writer. Off, and visibly off, when its root is not configured."""

    def __init__(self, root: Path | None, *, session_id: str | None = None) -> None:
        self.root = root
        self.enabled = root is not None
        self.written = 0
        self.last_written_ms: int | None = None
        self.last_error: str | None = None
        self.last_path: str | None = None
        self.skipped_duplicate = 0
        # Keyed by symbol. A shared cursor would make every symbol switch look like a context
        # change and write a line that says nothing, and - worse - would let one symbol's
        # unchanged reading suppress another's.
        self.cursors: dict[str, dict[str, Any]] = {}
        #: Identifies this writer in the lock file. The pid alone is not enough: pids are reused,
        #: and a refused writer reporting a pid that now belongs to something else is worse than
        #: reporting nothing.
        self.session_id = session_id or f"{os.getpid()}-{int(time.time() * 1000)}"
        self.lock = None if root is None else WriterLock(root)
        #: Whether this writer currently holds the authority to append. `None` means it has not
        #: tried yet, which is not the same as having been refused.
        self.writable: bool | None = None if root is None else None
        #: Why writing is off, when it is. One of `JOURNAL_STATES` minus the healthy ones.
        self.lock_refusal: str | None = None
        #: Appends refused because this process does not hold the lock. Counted separately from
        #: `skipped_duplicate`, because a line the cadence did not want and a line the cadence
        #: wanted but authority refused are different losses.
        self.blocked_writes = 0

    # -- the one directory this package creates ---------------------------------------------
    def _ensure_root(self) -> Path:
        """Create the journal root. The package's only `mkdir`, kept to one call site.

        The collector journal and the C1 store are owned by other processes, and the test that
        enforces that counts `mkdir` occurrences in this file - so every path that needs the
        directory comes through here rather than creating it again.
        """
        assert self.root is not None
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    # -- writer authority -------------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        """Take the writer lock, or record that it is held elsewhere. Never raises.

        Called eagerly at process start so that a second preview is refused at boot rather than
        at the first closed minute, and called lazily by `append` so that no line can be written
        by a writer that never asked. Both paths land here, so there is one acquisition rule.
        """
        if self.root is None or self.lock is None:
            return self.view()
        if self.writable:
            return self.view()
        try:
            self._ensure_root()
            self.lock.acquire(session_id=self.session_id,
                              started_ms=int(time.time() * 1000))
        except JournalLocked as caught:
            self.writable = False
            self.lock_refusal = STATE_REFUSED
            self.last_error = f"{type(caught).__name__}:{caught}"
            return self.view()
        except OSError as caught:
            # A root that cannot be created or a lock file that cannot be opened. Reported the
            # same way a failed append is: the panel keeps serving, the tape says why it is off.
            self.writable = False
            self.lock_refusal = STATE_ERROR
            self.last_error = f"{type(caught).__name__}:{caught}"
            return self.view()
        self.writable = True
        self.lock_refusal = None
        self.last_error = None
        return self.view()

    def stop(self) -> None:
        """Release the lock on an orderly shutdown. The kernel does it anyway on exit."""
        if self.lock is not None:
            self.lock.release()
        self.writable = False if self.root is not None else None

    def _authorised(self) -> bool:
        """Whether this process may append right now, re-proved before every line.

        The re-check is the half of the inode problem that can be enforced from inside: if the
        lock file under the root is no longer the one this writer locked, somebody deleted or
        replaced it and a second writer can now hold a lock on the replacement. This writer
        stops, permanently, because two cadences in one file is the failure the lock exists to
        prevent and a writer that cannot prove it owns the root is the one that has to yield.
        """
        if self.root is None or self.lock is None:
            return False
        if self.writable is None:
            self.start()
        if not self.writable:
            return False
        if not self.lock.verify():
            self.writable = False
            self.lock_refusal = STATE_AUTHORITY_LOST
            self.last_error = (f"AuthorityLost:{self.lock.path} is no longer the file this "
                               f"writer locked")
            return False
        return True

    def state(self, *, now_ms: int | None = None) -> str:
        """One word for a screen, worst-first over `JOURNAL_STATES`."""
        if not self.enabled:
            return STATE_OFF
        if self.lock_refusal is not None:
            return self.lock_refusal
        if self.last_error is not None:
            return STATE_ERROR
        if self.last_written_ms is None:
            return STATE_WAITING
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if now - self.last_written_ms > WRITE_STALE_MS:
            return STATE_STALE
        return STATE_WRITING

    def view(self, *, now_ms: int | None = None) -> dict[str, Any]:
        return {"enabled": self.enabled, "root": None if self.root is None else str(self.root),
                "root_env": ROOT_ENV, "records_written": self.written,
                "symbols_written": sorted(self.cursors),
                "last_written_ms": self.last_written_ms, "last_path": self.last_path,
                "last_error": self.last_error,
                "skipped_as_duplicate": self.skipped_duplicate,
                "record": C.JOURNAL_RECORD,
                "schema_version": C.JOURNAL_SCHEMA_VERSION,
                "cadence": "ONE_PER_CLOSED_MINUTE_PLUS_ONE_PER_STRUCTURE_CHANGE",
                "linked_to_orders": False, "read_back_by_the_panel": False,
                "status": self.state(now_ms=now_ms),
                "states": list(JOURNAL_STATES),
                "stale_after_ms": WRITE_STALE_MS,
                "writer": {
                    "writable": bool(self.writable),
                    "session_id": self.session_id,
                    "refusal": self.lock_refusal,
                    "blocked_writes": self.blocked_writes,
                    "single_writer": True,
                    "fail_closed": True,
                    "blocks_the_panel": False,
                    "recovery": LOCK_RECOVERY_NOTE,
                    "lock": None if self.lock is None else self.lock.view(),
                },
                "note": C.JOURNAL_NOTE}

    def should_write(self, line: dict[str, Any], *, written_at_ms: int) -> tuple[bool, str]:
        """A new minute for this symbol, or a change in one of its anchor fields.

        The minute is the structure layer's closed bar where there is one. Where there is not -
        a symbol with no level book, which still has derivatives worth taping - the wall clock
        minute stands in, so the tape keeps a steady cadence for every instrument instead of
        holding exactly one line for the ones without a structure layer.
        """
        symbol = str(line.get("symbol"))
        bar_close = (line.get("price") or {}).get("bar_close_ms")
        minute = (int(bar_close) if isinstance(bar_close, int)
                  else written_at_ms - written_at_ms % 60_000)
        minute_kind = "BAR_CLOSE" if isinstance(bar_close, int) else "WALL_CLOCK_MINUTE"
        signature = change_signature(line)
        cursor = self.cursors.get(symbol)
        if cursor is None:
            return True, "FIRST_RECORD"
        if minute != cursor["minute"]:
            return True, f"NEW_MINUTE_{minute_kind}"
        if signature != cursor["signature"]:
            return True, "CONTEXT_CHANGE"
        return False, "NO_CHANGE_SINCE_LAST_RECORD"

    def append(self, line: dict[str, Any], *, written_at_ms: int) -> bool:
        """One line, flushed and fsynced. Returns whether it was written.

        Authority is proved before the cadence is consulted, and that order is deliberate: a
        writer without the lock must not advance its own cursor, or it would believe it had
        written lines that are not in the file and then skip the next real one as a duplicate.
        """
        if not self.enabled or self.root is None:
            return False
        if not self._authorised():
            self.blocked_writes += 1
            return False
        write, reason = self.should_write(line, written_at_ms=written_at_ms)
        if not write:
            self.skipped_duplicate += 1
            return False
        line = dict(line)
        line["written_because"] = reason
        path = day_file(self.root, written_at_ms)
        try:
            self._ensure_root()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, sort_keys=True, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as caught:
            self.last_error = f"{type(caught).__name__}:{caught}"
            return False
        self.written += 1
        self.last_error = None
        self.last_path = str(path)
        self.last_written_ms = written_at_ms
        bar_close = (line.get("price") or {}).get("bar_close_ms")
        self.cursors[str(line.get("symbol"))] = {
            "minute": (int(bar_close) if isinstance(bar_close, int)
                       else written_at_ms - written_at_ms % 60_000),
            "signature": change_signature(line),
            "written_at_ms": written_at_ms,
            "records": self.cursors.get(str(line.get("symbol")), {}).get("records", 0) + 1,
        }
        return True


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every intact line. For tests and for a later study, never for the panel."""
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as handle:
        for text in handle:
            text = text.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue              # a torn final line, dropped rather than repaired
            if isinstance(parsed, dict):
                out.append(parsed)
    return out


__all__ = ["ROOT_ENV", "ForwardJournal", "JournalLocked", "WriterLock", "LOCK_FILENAME",
           "LOCK_VERSION", "LOCK_RECOVERY_NOTE", "JOURNAL_STATES", "WRITE_STALE_MS",
           "journal_root", "lock_file", "day_file", "record", "read_records",
           "change_signature", "CHANGE_KEYS"]
