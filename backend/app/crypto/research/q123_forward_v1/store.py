"""The recorder's permanent store: append-only JSONL, one line per fact, never rewritten.

Layout of a store root (`/root/usb_runtime/q123_forward/store` in production)::

    meta.json            written once when the store is created (identity, coverage start)
    approaches.jsonl     one line per (structural episode, question): ADMITTED or the R0 reason
    events.jsonl         one line per admitted episode (= detector record), keyed by event_id
    outcomes.jsonl       one line per (event_id, horizon), MEASURED or MISSING, final when written
    quality.jsonl        one line per (collector session, UTC hour): feed and layer state counts
    conflicts.jsonl      a recomputed line that differed from the stored one (never applied)

**Append safety.** Each batch is one `write` of whole lines on an `O_APPEND` descriptor followed by
`fsync`. A crash can therefore leave at most one torn line, at the very end of one file. Opening a
store repairs exactly that: bytes after the last newline are cut (and logged), nothing else. A
complete line that does not parse, or a key stored twice, is corruption and the store refuses to
open: it never "fixes" a fact.

**Idempotency.** Every line carries its key. Appending a key that exists compares the canonical
line: equal is a no-op, different is a conflict that is written to `conflicts.jsonl` and never
replaces the stored fact. The recorder is deterministic, so a conflict is a defect to look at.

**Retention.** None. Nothing in this module deletes or truncates beyond a torn tail.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

APPROACHES = "approaches.jsonl"
EVENTS = "events.jsonl"
OUTCOMES = "outcomes.jsonl"
QUALITY = "quality.jsonl"
CONFLICTS = "conflicts.jsonl"
META = "meta.json"
FILES = (APPROACHES, EVENTS, OUTCOMES, QUALITY)


class StoreCorrupt(RuntimeError):
    pass


def canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Temporary file, fsync, rename, fsync of the directory."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def repair_torn_tail(path: Path) -> int:
    """Cut bytes after the last newline. Returns how many were cut."""
    if not path.exists():
        return 0
    size = path.stat().st_size
    if size == 0:
        return 0
    with open(path, "rb+") as handle:
        handle.seek(max(0, size - 1))
        if handle.read(1) == b"\n":
            return 0
        # Scan back for the last newline in blocks.
        pos, block = size, 65536
        cut = 0
        while pos > 0:
            start = max(0, pos - block)
            handle.seek(start)
            chunk = handle.read(pos - start)
            k = chunk.rfind(b"\n")
            if k >= 0:
                cut = start + k + 1
                break
            pos = start
        handle.truncate(cut)
        handle.flush()
        os.fsync(handle.fileno())
    return size - cut


class Store:
    def __init__(self, root: Path, *, fault=None):
        self.root = Path(root)
        self.fault = fault or (lambda point: None)
        self.keys: dict[str, dict[str, str]] = {name: {} for name in FILES}
        self.rows: dict[str, dict[str, dict]] = {name: {} for name in FILES}
        self.repaired: dict[str, int] = {}
        self.conflicts = 0

    # ---------------------------------------------------------------- open
    @classmethod
    def open(cls, root: Path, *, create_meta: dict | None = None, fault=None,
             read_only: bool = False) -> "Store":
        """`read_only` neither creates nor repairs anything: a torn tail is skipped, not cut."""
        store = cls(root, fault=fault)
        if not read_only:
            store.root.mkdir(parents=True, exist_ok=True)
        meta = store.root / META
        if not meta.exists():
            if read_only:
                raise StoreCorrupt(f"{meta} missing")
            if create_meta is None:
                raise StoreCorrupt(f"{meta} missing and no meta to create it with")
            atomic_write(meta, (json.dumps(create_meta, indent=2, sort_keys=True) + "\n").encode())
        store.meta = json.loads(meta.read_text())
        for name in FILES + (CONFLICTS,):
            if read_only:
                break
            cut = repair_torn_tail(store.root / name)
            if cut:
                store.repaired[name] = cut
        for name in FILES:
            path = store.root / name
            if not path.exists():
                continue
            with open(path, "rb") as handle:
                for number, raw in enumerate(handle, 1):
                    if read_only and not raw.endswith(b"\n"):
                        break
                    line = raw.decode().rstrip("\n")
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError as exc:
                        raise StoreCorrupt(f"{name}:{number} does not parse") from exc
                    key = rec.get("key")
                    if not key or key in store.keys[name]:
                        raise StoreCorrupt(f"{name}:{number} key {key!r} missing or repeated")
                    store.keys[name][key] = line
                    store.rows[name][key] = rec
        if (store.root / CONFLICTS).exists():
            store.conflicts = sum(1 for _ in open(store.root / CONFLICTS, "rb"))
        return store

    def set_meta(self, **fields) -> None:
        """Fill fields of `meta.json` that are still unset. A set field is never changed."""
        changed = False
        for k, v in fields.items():
            if self.meta.get(k) is None and v is not None:
                self.meta[k] = v
                changed = True
        if changed:
            atomic_write(self.root / META,
                         (json.dumps(self.meta, indent=2, sort_keys=True) + "\n").encode())

    # ---------------------------------------------------------------- append
    def has(self, name: str, key: str) -> bool:
        return key in self.keys[name]

    def append(self, name: str, records: list[dict]) -> dict:
        report = {"new": 0, "existing": 0, "conflict": 0}
        fresh: list[str] = []
        fresh_keys: set[str] = set()
        clash: list[str] = []
        for rec in records:
            key = rec["key"]
            line = canonical(rec)
            have = self.keys[name].get(key)
            if have is not None:
                if have == line:
                    report["existing"] += 1
                else:
                    report["conflict"] += 1
                    clash.append(canonical({"file": name, "key": key, "at_ms": int(time.time() * 1000),
                                            "stored": json.loads(have), "recomputed": rec}))
                continue
            if key in fresh_keys:
                continue
            fresh_keys.add(key)
            fresh.append(line)
        if fresh:
            data = "".join(x + "\n" for x in fresh).encode()
            self.fault(f"before_append:{name}")
            self._write(self.root / name, data, name)
            self.fault(f"after_append:{name}")
            for line in fresh:
                rec = json.loads(line)
                self.keys[name][rec["key"]] = line
                self.rows[name][rec["key"]] = rec
            report["new"] = len(fresh)
        if clash:
            self._write(self.root / CONFLICTS, "".join(x + "\n" for x in clash).encode(), CONFLICTS)
            self.conflicts += len(clash)
        return report

    def _write(self, path: Path, data: bytes, name: str) -> None:
        new_file = not path.exists()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            torn = self.fault(f"torn_append:{name}")
            if torn:
                os.write(fd, data[: max(1, len(data) // 2)])
                os.fsync(fd)
                os._exit(137)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if new_file:
            fsync_dir(path.parent)


__all__ = ["Store", "StoreCorrupt", "canonical", "atomic_write", "repair_torn_tail",
           "APPROACHES", "EVENTS", "OUTCOMES", "QUALITY", "CONFLICTS", "META"]
