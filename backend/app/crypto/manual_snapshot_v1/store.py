"""The recorder's own store (contract section 6): append-only JSONL, fsync per line, one key per
row, never rewritten. A torn last line (a crash mid-write) is cut back to the last newline before
anything new is appended."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

FILES = {"events": ("events.jsonl", "event_id"), "snapshots": ("snapshots.jsonl", "snapshot_id"),
         "episodes": ("episodes.jsonl", "episode_id")}


class StoreLocked(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "runtime").mkdir(exist_ok=True)
        self.keys: dict[str, set[str]] = {}
        self.repaired: dict[str, int] = {}
        self._lock_fd: int | None = None

    # -- single writer -------------------------------------------------------------------------
    def lock(self) -> None:
        path = self.root / "runtime" / "lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise StoreLocked("another run holds the store")
        # flock locks the inode, not the path: if the lock file was replaced while we waited, the
        # lock we hold protects nothing. Re-check and refuse.
        if os.fstat(fd).st_ino != os.stat(path).st_ino:
            os.close(fd)
            raise StoreLocked("lock file replaced")
        self._lock_fd = fd

    def unlock(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    # -- reading and repair --------------------------------------------------------------------
    def _path(self, kind: str) -> Path:
        return self.root / FILES[kind][0]

    def load(self) -> None:
        for kind, (_, key) in FILES.items():
            path = self._path(kind)
            keys: set[str] = set()
            if path.exists():
                with open(path, "rb+") as fh:
                    data = fh.read()
                    end = data.rfind(b"\n") + 1
                    if end != len(data):
                        fh.truncate(end)
                        fh.flush()
                        os.fsync(fh.fileno())
                        self.repaired[kind] = len(data) - end
                        data = data[:end]
                for line in data.splitlines():
                    try:
                        keys.add(str(json.loads(line)[key]))
                    except (ValueError, KeyError, TypeError):
                        continue
            self.keys[kind] = keys

    def rows(self, kind: str) -> list[dict]:
        path = self._path(kind)
        out = []
        if path.exists():
            with open(path, "rb") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def has(self, kind: str, key: str) -> bool:
        return key in self.keys.get(kind, set())

    # -- writing -------------------------------------------------------------------------------
    def append(self, kind: str, row: dict) -> bool:
        key = str(row[FILES[kind][1]])
        if self.has(kind, key):
            return False
        data = (json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()
        with open(self._path(kind), "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        self.keys.setdefault(kind, set()).add(key)
        return True

    def write_cursor(self, cursor: dict) -> None:
        path = self.root / "cursor.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cursor, sort_keys=True, indent=1, default=str))
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
