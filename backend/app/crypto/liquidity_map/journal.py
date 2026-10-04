"""Reading the V0 journal from the outside, without assuming the writer is finished.

Three facts about the journal shape this module, and each of them is a way to read it wrongly:

* **The newest file is still being appended to.** The collector buffers 1 MiB, flushes once a
  second and renames `.jsonl.open` to `.jsonl` only when it closes the file. So the last line of
  the newest file can be torn mid-record, and a reader that trusts `splitlines()` will eventually
  hand a half object to `json.loads`. Every read here stops at the last newline and treats a
  trailing fragment as absent rather than as data.
* **Files rotate.** 64 MiB or one hour, whichever comes first, and recovery can add `-r1` variants
  of a name that already existed. A cursor therefore cannot be a byte offset into "the file"; it is
  an offset into an identified file, and the identity has to survive the `.open` -> `.jsonl`
  rename that happens underneath it.
* **The newest record is at the end.** The streams this viewer cares about are large (the wall
  stream alone was measured at about 2.3 GB/day), so "read the file and take the last line" is not
  an option. Reads walk backwards in chunks and stop as soon as they have what they need, and
  every backward walk reports how many bytes it actually touched.

Nothing here interprets market data. It returns envelopes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

#: `<kind>-<YYYYMMDD>-<session8>-<00001>[-rN].jsonl[.open]`, exactly as `store.KindWriter` names it.
FILE_PATTERN = re.compile(
    r"^(?P<kind>[a-z_]+)-(?P<date>\d{8})-(?P<session8>[0-9a-f]{8})-(?P<seq>\d{5})"
    r"(?:-r(?P<rev>\d+))?\.jsonl(?P<open>\.open)?$")

#: Chunk size for a backward walk. Large enough that a walk is a handful of reads, small enough
#: that a 64 MiB file is never pulled into memory to read its tail.
REVERSE_CHUNK_BYTES = 1 << 20


class JournalEmpty(RuntimeError):
    """The root exists but holds no session this viewer can read."""


@dataclass(frozen=True)
class StreamFile:
    """One journal file, with the ordering key that places it inside its session."""

    path: Path
    kind: str
    date: str
    session8: str
    seq: int
    rev: int
    is_open: bool

    @property
    def identity(self) -> tuple[str, int, int]:
        """What a cursor remembers. Survives the `.open` -> `.jsonl` rename, which changes the
        name but not the file's place in the session."""
        return (self.date, self.seq, self.rev)

    @property
    def order(self) -> tuple[str, int, int, int]:
        # A sealed file sorts before an `.open` file of the same identity. They should never both
        # exist; if they do, the sealed one is the one that finished being written.
        return (self.date, self.seq, self.rev, 1 if self.is_open else 0)

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


def parse_name(path: Path) -> StreamFile | None:
    match = FILE_PATTERN.match(path.name)
    if match is None:
        return None
    return StreamFile(path=path, kind=match["kind"], date=match["date"],
                      session8=match["session8"], seq=int(match["seq"]),
                      rev=int(match["rev"] or 0), is_open=bool(match["open"]))


def stream_files(root: Path, kind: str, session8: str | None = None) -> list[StreamFile]:
    """Every file of one kind, in the order the collector wrote them."""
    directory = root / kind
    if not directory.is_dir():
        return []
    found: list[StreamFile] = []
    for path in directory.iterdir():
        item = parse_name(path)
        if item is None or item.kind != kind:
            continue
        if session8 is not None and item.session8 != session8:
            continue
        found.append(item)
    return sorted(found, key=lambda item: item.order)


def reverse_lines(path: Path, *, chunk: int = REVERSE_CHUNK_BYTES) -> Iterator[tuple[bytes, int]]:
    """Yield `(line, bytes_touched_for_this_line)` from the end of the file towards the start.

    A trailing fragment - bytes after the last newline, which is what a buffered writer leaves
    behind - is dropped rather than yielded. It is not a record yet.
    """
    try:
        handle = open(path, "rb")
    except OSError:
        return
    with handle:
        handle.seek(0, 2)
        position = handle.tell()
        carry = b""
        first = True
        while position > 0:
            size = min(chunk, position)
            position -= size
            handle.seek(position)
            buffer = handle.read(size) + carry
            pieces = buffer.split(b"\n")
            carry = pieces[0]
            tail = pieces[1:]
            if first:
                # Everything after the final newline is an unfinished record, not a short one.
                if tail:
                    tail = tail[:-1]
                elif carry:
                    carry = b""
                first = False
            for piece in reversed(tail):
                if piece:
                    yield piece, len(piece) + 1
        if carry:
            yield carry, len(carry) + 1


def decode(line: bytes) -> dict[str, Any] | None:
    """A record, or None for anything that is not one. Damage is skipped, never guessed at."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def forward_records(path: Path, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    """Records from `offset` up to the last complete line. Returns (records, new_offset, bytes)."""
    try:
        handle = open(path, "rb")
    except OSError:
        return [], offset, 0
    with handle:
        handle.seek(offset)
        data = handle.read()
    end = data.rfind(b"\n") + 1
    if end <= 0:
        return [], offset, len(data)
    records = [record for record in (decode(line) for line in data[:end].split(b"\n") if line)
               if record is not None]
    return records, offset + end, end


@dataclass(frozen=True)
class SessionRef:
    """Which collector session this viewer is looking at."""

    session_id: str
    session8: str
    started_ms: int
    start_payload: dict[str, Any]
    end_payload: dict[str, Any] | None

    @property
    def ended(self) -> bool:
        return self.end_payload is not None


def latest_session(root: Path) -> SessionRef:
    """The newest session in the root, by its own recorded start time.

    The `session` stream is two records per run, so it is read whole. Picking by start time rather
    than by file mtime matters: a previous session's files are sealed *after* the new one starts,
    so mtime can name the wrong session.
    """
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for item in stream_files(root, "session"):
        records, _, _ = forward_records(item.path, 0)
        for record in records:
            payload = record.get("payload") or {}
            session_id = str(record.get("session_id") or payload.get("session_id") or "")
            if not session_id:
                continue
            if payload.get("event") == "START":
                starts[session_id] = payload
            elif payload.get("event") == "END":
                ends[session_id] = payload
    if not starts:
        raise JournalEmpty(f"no collector session recorded under {root}")
    session_id = max(starts, key=lambda key: int(starts[key].get("started_ms") or 0))
    return SessionRef(session_id=session_id, session8=session_id[:8],
                      started_ms=int(starts[session_id].get("started_ms") or 0),
                      start_payload=starts[session_id], end_payload=ends.get(session_id))


def last_record(root: Path, kind: str, session: SessionRef, *,
                max_bytes: int = 1 << 20) -> tuple[dict[str, Any] | None, int]:
    """The newest record of one kind in one session, and the bytes it took to find it."""
    touched = 0
    for item in reversed(stream_files(root, kind, session.session8)):
        for line, cost in reverse_lines(item.path):
            touched += cost
            record = decode(line)
            if record is not None:
                return record, touched
            if touched >= max_bytes:
                return None, touched
        if touched >= max_bytes:
            return None, touched
    return None, touched


def recent_records(root: Path, kind: str, session: SessionRef, *, limit: int,
                   max_bytes: int = 1 << 20) -> tuple[list[dict[str, Any]], int]:
    """Up to `limit` newest records of one kind, newest first."""
    out: list[dict[str, Any]] = []
    touched = 0
    for item in reversed(stream_files(root, kind, session.session8)):
        for line, cost in reverse_lines(item.path):
            touched += cost
            record = decode(line)
            if record is not None:
                out.append(record)
                if len(out) >= limit:
                    return out, touched
            if touched >= max_bytes:
                return out, touched
    return out, touched


def records_after_seq(root: Path, kind: str, session: SessionRef, *, after_seq: int,
                     max_bytes: int = 1 << 22) -> tuple[list[dict[str, Any]], int, bool]:
    """Records newer than `after_seq`, oldest first, read backwards and stopped by `seq`.

    This is the tail a compact checkpoint leaves behind, and the stopping rule is what keeps it
    cheap. The walk goes backwards from the end and **stops at the first record whose `seq` is at
    or below `after_seq`**, because `seq` increases strictly within a session: once one is seen,
    everything before it is older. The work is therefore bounded by how much was written since
    the checkpoint, not by how long the session has run, and it is bounded *by the data* rather
    than by a budget that might be the wrong size.

    `max_bytes` is a second, cruder ceiling for the case where the checkpoint's `seq` is not in
    this stream at all - a journal that lost its newest file, say. Returns
    `(records, bytes_touched, complete)`, where `complete` is False when that ceiling was the
    thing that stopped the walk, so a caller can refuse to claim the tail was fully read.
    """
    collected: list[dict[str, Any]] = []
    touched = 0
    for item in reversed(stream_files(root, kind, session.session8)):
        for line, cost in reverse_lines(item.path):
            touched += cost
            record = decode(line)
            if record is not None:
                seq = record.get("seq")
                if isinstance(seq, int) and seq <= after_seq:
                    collected.reverse()
                    return collected, touched, True
                collected.append(record)
            if touched >= max_bytes:
                collected.reverse()
                return collected, touched, False
    # The whole stream is newer than the checkpoint, which is what a very young session looks
    # like. Reaching the first byte is a complete answer, not a truncated one.
    collected.reverse()
    return collected, touched, True


@dataclass
class Cursor:
    """A resumable position in one kind's stream of one session."""

    identity: tuple[str, int, int] | None = None
    offset: int = 0
    #: Identities fully consumed, so a rotation does not re-read a sealed file.
    done: set[tuple[str, int, int]] = field(default_factory=set)

    def at_end(self, root: Path, kind: str, session: SessionRef) -> "Cursor":
        """Place the cursor at the current end of the stream without reading anything."""
        files = stream_files(root, kind, session.session8)
        if not files:
            return Cursor()
        newest = files[-1]
        size = newest.size()
        # Only whole lines count as consumed; a torn tail must be re-read once it is completed.
        with_newline = size
        try:
            with open(newest.path, "rb") as handle:
                probe = max(0, size - REVERSE_CHUNK_BYTES)
                handle.seek(probe)
                data = handle.read()
            end = data.rfind(b"\n") + 1
            with_newline = probe + end if end > 0 else 0
        except OSError:
            with_newline = 0
        return Cursor(identity=newest.identity, offset=with_newline,
                      done={item.identity for item in files[:-1]})

    def advance(self, root: Path, kind: str, session: SessionRef
                ) -> tuple[list[dict[str, Any]], int]:
        """Every complete record written since the cursor was last here."""
        records: list[dict[str, Any]] = []
        touched = 0
        for item in stream_files(root, kind, session.session8):
            if item.identity in self.done:
                continue
            start = self.offset if item.identity == self.identity else 0
            batch, offset, cost = forward_records(item.path, start)
            records.extend(batch)
            touched += cost
            self.identity = item.identity
            self.offset = offset
            if not item.is_open:
                # Sealed: nothing more will ever be appended, so it never needs reopening.
                self.done.add(item.identity)
        return records, touched


__all__ = ["FILE_PATTERN", "REVERSE_CHUNK_BYTES", "JournalEmpty", "StreamFile", "SessionRef",
           "Cursor", "parse_name", "stream_files", "reverse_lines", "decode", "forward_records",
           "latest_session", "last_record", "recent_records", "records_after_seq"]
