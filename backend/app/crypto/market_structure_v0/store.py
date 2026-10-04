"""Buffered, rotating JSONL output, with a recovery path that never invents coverage.

Three decisions shape this module.

**No fsync per event.** At the measured ~9 depth frames/s plus trades plus a derived sample, an
fsync per record would be an fsync several times a second forever, which is how a collector
becomes an IO problem instead of a data source. Records go into a 1 MiB buffer, the buffer is
flushed to the OS once a second, and fsync happens every 10 s and on every rotation and on
shutdown. The honest cost is stated rather than hidden: a power loss can lose up to the fsync
interval, plus whatever the OS and device were still holding, and the recovery report says what
it removed instead of implying the file was intact.

**Rotation by whichever comes first.** 64 MiB or one hour. Files are named by kind, UTC date and
session, so a day's data for one kind is a readable glob and a session never writes into another
session's file. The file being appended to carries `.jsonl.open` and is renamed to `.jsonl` when
it is closed, so a reader can tell a finished file from a live one without asking the collector.

**Recovery fails closed on interior damage.** A crash leaves `.open` files whose last line can be
torn, and that single trailing fragment is truncated and the removed byte count reported. A
complete line that does not parse is different: it means damage *inside* the file, the lines after
it cannot be trusted to be what they claim, and silently keeping them would turn corruption into
data. That case raises. One advisory lock per output root enforces a single writer, because two
collectors interleaving into one directory is the other way to manufacture a file that parses and
means nothing.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import resource
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import (FINAL_SUFFIX, FLUSH_INTERVAL_S, FSYNC_INTERVAL_S, KINDS, LOCK_FILENAME,
                       OPEN_SUFFIX, ROTATE_BYTES, ROTATE_SECONDS, WRITE_BUFFER_BYTES)


#: The compact state file, and the directory it lives in.
#:
#: This is **not** a journal kind and **not** replay authority. The journal (raw frames, the
#: snapshots, the envelope sequence and the telemetry) stays the only authority for what
#: happened. This file is a derived accelerator: the collector's current state, rewritten in
#: place every sample, so that a reader can know the live active candidate set and the latest
#: metrics in one small read instead of walking a stream whose size grows with the session.
#:
#: It is written by whichever process holds the writer lock, which is the only process entitled
#: to say what the current state is, and it is replaced atomically so a reader never sees a half
#: file. It is deliberately not fsynced: it is rebuildable from the journal, so paying a sync per
#: second to make a cache durable would buy nothing.
STATE_DIRNAME = "state"
STATE_FILENAME = "collector_state.json"

#: Shape of the writer lock file's contents. The pre-V1.2 file held a bare pid; both are read.
LOCK_VERSION = "ms-v0-lock.v1-2"

#: How fresh the compact state file has to be before it counts as evidence that a collector is
#: still running in this root. It is rewritten every sample, so three sample intervals is the
#: first age that cannot be explained by scheduling - the same bound the reader uses to decide a
#: checkpoint is stale. A root whose writer was killed therefore unblocks after three seconds.
LIVE_STATE_WINDOW_MS = 3_000


def _process_is_alive(pid: Any) -> bool:
    """Whether `pid` names a running process. A pid we may not signal is still running.

    This process counts. If the current pid published that state file it is writing this root,
    and a second `Store` on it from the same process is the same collision as from another one.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live_writer_evidence(root: Path, *, now_ms: int | None = None) -> dict[str, Any] | None:
    """Signs that a collector is writing this root right now, other than the lock itself.

    This exists because of a measured hole that the lock alone cannot close. `flock` is on an
    inode: remove `<root>/.writer.lock` and the next `Store.open` creates a different file and
    locks it cleanly, and nothing the second process can read tells it that the first one is
    still there. The first writer now notices and stops (`Store.check_authority`), which is the
    half that can be enforced from inside. This is the other half, and it is deliberately a
    **heuristic, not a guarantee**: the compact state file is rewritten every sample and carries
    the writing process's pid, so a state file younger than `LIVE_STATE_WINDOW_MS` whose pid is
    alive means somebody is writing here.

    What it does **not** catch, stated so nobody mistakes it for the contract: a root that was
    removed outright takes the state file with it, and a second writer then has nothing left to
    read. That case is covered only by the first writer failing closed.

    A session that recorded its own end is not evidence of anything, so a clean stop followed by
    an immediate restart is not refused.
    """
    path = root / STATE_DIRNAME / STATE_FILENAME
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    if session.get("ended"):
        return None
    written = payload.get("written_ms")
    if not isinstance(written, int):
        return None
    age = (int(time.time() * 1000) if now_ms is None else now_ms) - written
    if age > LIVE_STATE_WINDOW_MS:
        return None
    collector = payload.get("collector") if isinstance(payload.get("collector"), dict) else {}
    pid = collector.get("pid")
    if not _process_is_alive(pid):
        return None
    return {"pid": pid, "session_id": session.get("session_id"), "state_age_ms": max(0, age),
            "state_file": str(path)}


class StoreLocked(RuntimeError):
    """Another collector holds the writer lock for this output root."""


class StoreAuthorityLost(RuntimeError):
    """This process no longer holds the writer lock it was started with.

    Raised when the lock file under the output root is no longer the file this writer locked -
    it was removed, replaced, or the whole root was. `flock` is on an inode, so from that moment
    a second collector can take a lock on the replacement and both processes believe they are the
    single writer. The one that can no longer prove it holds the root stops, because the
    alternative is two sessions interleaving into one directory, which is the exact failure the
    lock exists to prevent.
    """


class StoreCorruption(RuntimeError):
    """An orphan file is damaged somewhere other than its final line."""


def canonical_line(record: dict[str, Any]) -> bytes:
    """Deterministic bytes for one record. Sorted keys so a file diff means a data diff."""
    return (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d")


class WriterLock:
    """Advisory `flock` on `<root>/.writer.lock`. Held for the life of the process.

    `flock` locks an **inode**, not a path, and that is the whole subtlety. Unlink the lock file
    while a writer holds it and the next `open(..., O_CREAT)` makes a *different* inode which
    locks cleanly, so two collectors end up writing one output root while each believes it is
    alone. Measured, not supposed: with the lock file removed, a second `Store.open` on a live
    root succeeds, its orphan recovery renames the first writer's open files out from under its
    file descriptors, and the first writer's own seal then fails with `FileNotFoundError` on a
    path that no longer exists. Both sessions' rows end up in one directory.

    So two things are kept here that a bare `flock` does not give:

    * the **identity of the locked inode**, so this writer can ask at any time whether the lock
      file under the root is still the one it locked (`verify`), and
    * an identifying **payload** in the file - pid, session and start time - so the process that
      is refused can say who holds it instead of only that somebody does.

    `acquire` also re-checks after taking the lock, because between `open` and `flock` another
    process can replace the file; the check turns that race into a refusal rather than a second
    writer.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / LOCK_FILENAME
        self._fd: int | None = None
        #: `(st_dev, st_ino)` of the file this lock is actually held on.
        self.identity: tuple[int, int] | None = None
        #: What the lock file said when this writer was refused. Published in the error.
        self.holder: dict[str, Any] | None = None

    def _read_holder(self) -> dict[str, Any] | None:
        """Whatever the current lock file claims. Never raises; it is only ever a hint."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            # The pre-V1.2 lock file held a bare pid. Readable, so it is still reported.
            return {"pid": text.split("\n")[0] or None, "format": "LEGACY_PID"}
        return payload if isinstance(payload, dict) else None

    def acquire(self, *, session_id: str | None = None, started_ms: int | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    self.holder = self._read_holder()
                    raise StoreLocked(
                        f"writer lock held by another process: {self.path} "
                        f"(holder: {self.holder})") from exc
                raise
            locked = os.fstat(fd)
            try:
                current = os.stat(self.path)
            except OSError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == (locked.st_dev,
                                                                            locked.st_ino):
                os.truncate(fd, 0)
                os.write(fd, canonical_line({
                    "pid": os.getpid(), "session_id": session_id,
                    "started_ms": started_ms, "lock_version": LOCK_VERSION}))
                os.fsync(fd)
                self._fd = fd
                self.identity = (locked.st_dev, locked.st_ino)
                return
            # The file was replaced between the open and the lock, so this lock is on an inode
            # nothing can find any more. Try once more, then refuse rather than hold a lock that
            # protects nothing.
            os.close(fd)
            if attempt == 2:
                self.holder = self._read_holder()
                raise StoreLocked(
                    f"writer lock file kept being replaced while being taken: {self.path} "
                    f"(holder: {self.holder})")

    def verify(self) -> bool:
        """Whether the lock file under the root is still the file this writer locked."""
        if self._fd is None or self.identity is None:
            return False
        try:
            current = os.stat(self.path)
        except OSError:
            return False
        return (current.st_dev, current.st_ino) == self.identity

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
            self.identity = None


def recover_orphans(root: Path) -> dict[str, Any]:
    """Seal `.open` files left by a previous process. Raises on interior corruption."""
    report: dict[str, Any] = {"files": [], "files_recovered": 0, "bytes_removed": 0,
                              "rows_kept": 0}
    if not root.exists():
        return report
    for path in sorted(root.rglob(f"*{OPEN_SUFFIX}")):
        data = path.read_bytes()
        end = data.rfind(b"\n") + 1
        complete, fragment = data[:end], data[end:]
        rows = 0
        for index, line in enumerate(complete.split(b"\n")[:-1]):
            try:
                json.loads(line)
            except ValueError as exc:
                raise StoreCorruption(
                    f"{path} line {index + 1} is complete but does not parse; refusing to "
                    f"silently keep the {len(complete.splitlines()) - index - 1} lines after it"
                ) from exc
            rows += 1
        if fragment:
            with open(path, "r+b") as handle:
                handle.truncate(end)
                handle.flush()
                os.fsync(handle.fileno())
        final = path.with_name(path.name[: -len(OPEN_SUFFIX)] + FINAL_SUFFIX)
        suffix = 1
        while final.exists():  # never overwrite a sealed file
            final = path.with_name(path.name[: -len(OPEN_SUFFIX)] + f"-r{suffix}" + FINAL_SUFFIX)
            suffix += 1
        os.replace(path, final)
        report["files"].append({"recovered": final.name, "rows_kept": rows,
                                "bytes_removed": len(fragment)})
        report["files_recovered"] += 1
        report["bytes_removed"] += len(fragment)
        report["rows_kept"] += rows
    return report


@dataclass
class KindWriter:
    """One kind's output. Owns its buffer, its current file and its own counters."""

    root: Path
    kind: str
    session_id: str
    rotate_bytes: int = ROTATE_BYTES
    rotate_seconds: int = ROTATE_SECONDS
    buffer_bytes: int = WRITE_BUFFER_BYTES
    _buffer: bytearray = field(default_factory=bytearray)
    _handle: Any = None
    _path: Path | None = None
    _file_bytes: int = 0
    _opened_ns: int = 0
    _last_flush_ns: int = 0
    _last_fsync_ns: int = 0
    _sequence: int = 0
    rows: int = 0
    bytes_written: int = 0
    rotations: int = 0
    flush_calls: int = 0
    flush_ns_total: int = 0
    flush_ns_max: int = 0
    fsync_calls: int = 0
    fsync_ns_total: int = 0
    fsync_ns_max: int = 0

    # ------------------------------------------------------------------ files

    def _open(self, now_ns: int, now_ms: int) -> None:
        directory = self.root / self.kind
        directory.mkdir(parents=True, exist_ok=True)
        # Skip any sequence whose sealed counterpart already exists. A crash plus a restart that
        # reuses the same session can otherwise reopen a name that recovery has just sealed, and
        # the seal below would replace the recovered file with the new one.
        while True:
            self._sequence += 1
            name = f"{self.kind}-{_utc_date(now_ms)}-{self.session_id[:8]}-{self._sequence:05d}"
            if not (directory / f"{name}{FINAL_SUFFIX}").exists():
                break
        self._path = directory / f"{name}{OPEN_SUFFIX}"
        self._handle = open(self._path, "ab", buffering=0)
        self._file_bytes = 0
        self._opened_ns = now_ns
        self._last_flush_ns = now_ns
        self._last_fsync_ns = now_ns

    def abandon(self) -> None:
        """Let go of the current file without renaming it.

        Used when the process has lost the right to write this root. The buffered bytes are
        dropped rather than flushed into a directory somebody else owns, and the `.open` file is
        left exactly as it is, which is the state `recover_orphans` is built to find.
        """
        self._buffer.clear()
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self._path = None

    def _seal(self) -> Path | None:
        """Flush, fsync and rename the current file. Returns the sealed path."""
        if self._handle is None or self._path is None:
            return None
        self._flush(force=True)
        self._fsync()
        self._handle.close()
        self._handle = None
        stem = self._path.name[: -len(OPEN_SUFFIX)]
        final = self._path.with_name(stem + FINAL_SUFFIX)
        suffix = 1
        while final.exists():  # never overwrite a sealed file
            final = self._path.with_name(f"{stem}-r{suffix}{FINAL_SUFFIX}")
            suffix += 1
        os.replace(self._path, final)
        sealed, self._path = final, None
        return sealed

    # ------------------------------------------------------------------ writing

    def write(self, record: dict[str, Any], *, now_ns: int, now_ms: int) -> int:
        """Buffer one record. Returns its serialized size, which is what the stats count."""
        line = canonical_line(record)
        if self._handle is None:
            self._open(now_ns, now_ms)
        self._buffer += line
        self.rows += 1
        self.bytes_written += len(line)
        self._file_bytes += len(line)
        if len(self._buffer) >= self.buffer_bytes:
            self._flush(force=True)
        self._maybe_rotate(now_ns, now_ms)
        return len(line)

    def _flush(self, *, force: bool = False) -> None:
        if not self._buffer or self._handle is None:
            return
        started = time.monotonic_ns()
        self._handle.write(bytes(self._buffer))
        self._buffer.clear()
        elapsed = time.monotonic_ns() - started
        self.flush_calls += 1
        self.flush_ns_total += elapsed
        self.flush_ns_max = max(self.flush_ns_max, elapsed)

    def _fsync(self) -> None:
        if self._handle is None:
            return
        started = time.monotonic_ns()
        os.fsync(self._handle.fileno())
        elapsed = time.monotonic_ns() - started
        self.fsync_calls += 1
        self.fsync_ns_total += elapsed
        self.fsync_ns_max = max(self.fsync_ns_max, elapsed)

    def _maybe_rotate(self, now_ns: int, now_ms: int) -> None:
        if self._handle is None:
            return
        by_size = self._file_bytes >= self.rotate_bytes
        by_age = (now_ns - self._opened_ns) >= self.rotate_seconds * 1_000_000_000
        if not (by_size or by_age):
            return
        self._seal()
        self.rotations += 1
        self._open(now_ns, now_ms)

    def tick(self, now_ns: int, now_ms: int) -> None:
        """Once-a-second flush, ten-second fsync, and the age-based rotation check."""
        if self._handle is None:
            return
        if (now_ns - self._last_flush_ns) >= FLUSH_INTERVAL_S * 1_000_000_000:
            self._flush(force=True)
            self._last_flush_ns = now_ns
        if (now_ns - self._last_fsync_ns) >= FSYNC_INTERVAL_S * 1_000_000_000:
            self._flush(force=True)
            self._fsync()
            self._last_fsync_ns = now_ns
        self._maybe_rotate(now_ns, now_ms)

    def close(self) -> Path | None:
        return self._seal()

    def stats(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "bytes": self.bytes_written,
            "rotations": self.rotations,
            "buffered_bytes": len(self._buffer),
            "open_file": self._path.name if self._path else None,
            "flush_calls": self.flush_calls,
            "flush_ms_max": round(self.flush_ns_max / 1e6, 3),
            "flush_ms_mean": round(self.flush_ns_total / self.flush_calls / 1e6, 3)
            if self.flush_calls else None,
            "fsync_calls": self.fsync_calls,
            "fsync_ms_max": round(self.fsync_ns_max / 1e6, 3),
            "fsync_ms_mean": round(self.fsync_ns_total / self.fsync_calls / 1e6, 3)
            if self.fsync_calls else None,
        }


@dataclass
class Store:
    """One writer per kind under one locked root, plus the measurements the task requires."""

    root: Path
    session_id: str
    started_ns: int
    lock: WriterLock | None = None
    recovery: dict[str, Any] = field(default_factory=dict)
    writers: dict[str, KindWriter] = field(default_factory=dict)
    dropped_records: int = 0
    queue_backlog_max: int = 0
    state_writes: int = 0
    state_writes_failed: int = 0
    state_bytes: int = 0
    state_last_error: str | None = None
    #: Set once this process can no longer prove it holds the root's writer lock. Sticky.
    authority_lost: str | None = None

    @classmethod
    def open(cls, root: Path, session_id: str, *, started_ns: int,
             recover: bool = True) -> "Store":
        """Take the lock, seal anything a previous process left, then start writing."""
        root.mkdir(parents=True, exist_ok=True)
        lock = WriterLock(root)
        lock.acquire(session_id=session_id, started_ms=int(time.time() * 1000))
        evidence = live_writer_evidence(root)
        if evidence is not None:
            # The lock was free and somebody is still writing here, which means the lock file was
            # removed or replaced under a running collector. Taking this root would interleave two
            # sessions into one directory, so the lock is handed straight back.
            lock.release()
            raise StoreLocked(
                f"the writer lock for {root} was free, but a collector is still writing here "
                f"({evidence}). The lock file was removed or replaced under a running process.")
        try:
            # Recovery renames other processes' `.open` files, so it may only ever run while this
            # process is the proven single writer. That is the lock's whole job, and it is why the
            # order here matters: lock, then recover, never the other way round.
            recovery = recover_orphans(root) if recover else {}
        except BaseException:
            lock.release()
            raise
        return cls(root=root, session_id=session_id, started_ns=started_ns, lock=lock,
                   recovery=recovery)

    # ------------------------------------------------------------------ authority

    def holds_authority(self) -> bool:
        """Whether this process can still prove it is the single writer of this root."""
        return self.lock is not None and self.lock.verify()

    def check_authority(self) -> None:
        """Fail closed the moment the lock stops being provable.

        Checked once a second on the flush tick and before every seal and state write, rather
        than per record: a `stat` a second costs nothing, and the failure this guards against -
        somebody removing or replacing the root - is not transient. Once lost it stays lost; a
        writer that rediscovered a lock it cannot prove it kept would be inventing the very
        guarantee it is supposed to provide.
        """
        if self.authority_lost is not None:
            raise StoreAuthorityLost(self.authority_lost)
        if self.lock is None or self.lock.verify():
            return
        holder = None if self.lock is None else self.lock._read_holder()
        self.authority_lost = (
            f"the writer lock under {self.root} is no longer the file this process locked "
            f"(it was removed or replaced; current holder: {holder}). Refusing to keep writing "
            f"a root this process cannot prove it owns.")
        raise StoreAuthorityLost(self.authority_lost)

    def _writer(self, kind: str) -> KindWriter:
        if kind not in KINDS:
            raise ValueError(f"unknown record kind: {kind!r}")
        writer = self.writers.get(kind)
        if writer is None:
            writer = KindWriter(root=self.root, kind=kind, session_id=self.session_id)
            self.writers[kind] = writer
        return writer

    def write(self, record: dict[str, Any], *, now_ns: int | None = None,
              now_ms: int | None = None) -> int:
        at_ns = time.monotonic_ns() if now_ns is None else now_ns
        at_ms = int(time.time() * 1000) if now_ms is None else now_ms
        return self._writer(str(record["kind"])).write(record, now_ns=at_ns, now_ms=at_ms)

    def tick(self, now_ns: int | None = None, now_ms: int | None = None) -> None:
        # Before the flush, not after: bytes that have not left the buffer yet are still this
        # process's to keep out of a directory it no longer owns.
        self.check_authority()
        at_ns = time.monotonic_ns() if now_ns is None else now_ns
        at_ms = int(time.time() * 1000) if now_ms is None else now_ms
        for writer in self.writers.values():
            writer.tick(at_ns, at_ms)

    def observe_backlog(self, backlog: int) -> None:
        self.queue_backlog_max = max(self.queue_backlog_max, backlog)

    # ------------------------------------------------------------------ compact state

    def state_path(self) -> Path:
        return self.root / STATE_DIRNAME / STATE_FILENAME

    def write_state(self, payload: dict[str, Any]) -> int:
        """Replace the compact state file atomically. Returns bytes written, or 0 on failure.

        Two properties are the whole point of this method.

        **Atomic.** The payload goes to a per-process temporary file in the same directory and is
        then `os.replace`d onto the published name, which is atomic within a filesystem. A reader
        polling this file therefore sees either the previous complete state or the new complete
        state, never a truncated one, and needs no lock and no retry loop to be correct.

        **Never fatal.** A failure here is counted and reported in `storage_stats` and otherwise
        ignored. This file is a cache of state that the journal already records; a full disk or a
        permissions problem must not be able to stop the collection it is accelerating.
        """
        if not self.holds_authority():
            # The state file is the one artefact that says "this is the current state", so a
            # process that cannot prove it owns the root must not publish it. Counted, not fatal,
            # like every other failure of this method; the flush tick is what stops the run.
            self.state_writes_failed += 1
            self.state_last_error = "StoreAuthorityLost: writer lock is no longer provable"
            return 0
        directory = self.root / STATE_DIRNAME
        temporary = directory / f".{STATE_FILENAME}.{os.getpid()}.tmp"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8")
            with open(temporary, "wb") as handle:
                handle.write(data)
            os.replace(temporary, self.state_path())
        except Exception as exc:  # noqa: BLE001 - a cache write may never stop the collector
            self.state_writes_failed += 1
            self.state_last_error = f"{type(exc).__name__}: {exc}"
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return 0
        self.state_writes += 1
        self.state_bytes = len(data)
        return len(data)

    def close(self) -> list[str]:
        """Seal and release. A root this process no longer owns is left alone.

        Sealing is an `os.replace` of a path somebody else may now own, and on a root that was
        removed it is an exception thrown from the shutdown path of an otherwise complete run.
        Both are worse than not sealing: the files are recoverable by the next writer either way,
        and `recover_orphans` is exactly the code that does it.
        """
        if not self.holds_authority():
            self.authority_lost = self.authority_lost or (
                f"the writer lock under {self.root} was gone at close; files were left for the "
                f"next writer's orphan recovery rather than sealed into a root this process "
                f"could not prove it owned")
            for writer in self.writers.values():
                writer.abandon()
            if self.lock is not None:
                self.lock.release()
                self.lock = None
            return []
        sealed = [str(path) for path in (writer.close() for writer in self.writers.values())
                  if path is not None]
        if self.lock is not None:
            self.lock.release()
            self.lock = None
        return sealed

    # ------------------------------------------------------------------ measurement

    def stats(self, *, now_ns: int | None = None, queue_backlog: int = 0,
              extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Serialized bytes by kind, and the projection the 24 h trial is judged on."""
        at_ns = time.monotonic_ns() if now_ns is None else now_ns
        elapsed_s = max(1e-9, (at_ns - self.started_ns) / 1e9)
        by_kind = {kind: writer.stats() for kind, writer in sorted(self.writers.items())}
        total_bytes = sum(item["bytes"] for item in by_kind.values())
        total_rows = sum(item["rows"] for item in by_kind.values())
        per_day = 86_400 / elapsed_s
        return {
            "elapsed_s": round(elapsed_s, 3),
            "by_kind": by_kind,
            "total_bytes": total_bytes,
            "total_rows": total_rows,
            "projected_bytes_per_day": int(total_bytes * per_day),
            "projected_rows_per_day": int(total_rows * per_day),
            "projected_bytes_per_day_by_kind": {
                kind: int(item["bytes"] * per_day) for kind, item in by_kind.items()},
            "projected_rows_per_day_by_kind": {
                kind: int(item["rows"] * per_day) for kind, item in by_kind.items()},
            "rotations": sum(item["rotations"] for item in by_kind.values()),
            "recovery": self.recovery,
            "queue_backlog": queue_backlog,
            "queue_backlog_max": self.queue_backlog_max,
            "dropped_records": self.dropped_records,
            "state_file": {"writes": self.state_writes, "failed": self.state_writes_failed,
                           "bytes": self.state_bytes, "last_error": self.state_last_error,
                           "path": str(self.state_path()),
                           "is_authority": False},
            # ru_maxrss is KiB on Linux: the high-water mark, which is what a bounded-memory
            # claim has to be judged on rather than current usage.
            "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "fsync_interval_s": FSYNC_INTERVAL_S,
            "durability_note": (
                "buffered: a power loss may lose up to fsync_interval_s of records plus whatever "
                "the OS and device held; recovery reports removed bytes and never reconstructs them"),
            **(extra or {}),
        }


__all__ = ["Store", "KindWriter", "WriterLock", "StoreLocked", "StoreCorruption",
           "recover_orphans", "canonical_line", "STATE_DIRNAME", "STATE_FILENAME"]
