"""The V0 store with a persistence policy: same lock, same recovery, fewer kinds.

Everything that makes the V0 store safe is inherited rather than re-written: the `flock` on
`<root>/.writer.lock` with its holder payload, the inode re-check before every flush and state
write (`StoreAuthorityLost`), orphan recovery that truncates a torn final line and refuses interior
damage (`StoreCorruption`), hourly rotation and the atomic state-file replace. What this subclass
adds is narrow:

* **A kind filter.** V0 kinds listed in `contract.DROPPED_V0_KINDS` are counted and discarded at
  `write`. The state machine upstream is not told, so it behaves exactly as it does in a research
  run; only the bytes stop. With `shadow_bytes` on, each dropped record is still serialized and
  its size counted, which is how one run can state what a research collector would have written
  for the same stream. It costs the serialization, so it is a measurement mode, not the default.
* **Two more kinds.** `context` and `wall_v2` get ordinary `KindWriter`s, so they rotate, flush,
  fsync and recover exactly like the V0 kinds.
* **Compression at seal.** A sealed hour of an own kind is gzip-compressed into `.jsonl.gz`
  through a temporary file and an atomic rename; the plain file is removed only after the
  compressed one is durable. A failure leaves the plain file, which is still valid data.
* **The latest context file.** `state/context_latest.json`, replaced atomically every sample,
  never fatal, and refused when the writer lock is no longer provable - the same rules as the
  compact state file, because it makes the same kind of claim about "now".
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..market_structure_v0.contract import FINAL_SUFFIX, KINDS
from ..market_structure_v0.store import (STATE_DIRNAME, KindWriter, Store, StoreAuthorityLost,
                                         canonical_line)
from .contract import (COMPRESSED_KINDS, COMPRESSED_SUFFIX, CONTEXT_KINDS, DROPPED_V0_KINDS,
                       LATEST_FILENAME)

#: Suffix of a compression in progress. Never read as data; removed at the next open.
COMPRESS_TMP_SUFFIX = ".gz.tmp"


def state_cache_target(root: Path, cache_dir: Path) -> Path:
    """The cache directory for one root: one subdirectory per root, so two roots never share."""
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return cache_dir / f"ctx-state-{digest}"


def install_state_cache(root: Path, cache_dir: Path) -> dict[str, Any]:
    """CONTRACT_CTX_V1_1 section 3: make `<root>/state` a link to a volatile directory.

    Called with the writer lock held. A real directory found there is renamed aside, never
    deleted; a link to somewhere else is replaced. Readers keep reading `<root>/state/...`.
    """
    link = root / STATE_DIRNAME
    target = state_cache_target(root, cache_dir)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    report: dict[str, Any] = {"link": str(link), "target": str(target), "moved_aside": None}
    if link.is_symlink():
        if Path(os.readlink(link)) == target:
            return report
        link.unlink()
    elif link.exists():
        aside = root / f"{STATE_DIRNAME}.disk-{int(time.time() * 1000)}"
        os.replace(link, aside)
        report["moved_aside"] = str(aside)
    link.symlink_to(target, target_is_directory=True)
    return report


class StorageFailed(RuntimeError):
    """The journal could not be written. The collector stops rather than serve without one."""


def compress_sealed(path: Path) -> Path:
    """`x.jsonl` -> `x.jsonl.gz`, durable before the plain file is removed. Returns the result."""
    target = path.with_name(path.name[: -len(FINAL_SUFFIX)] + COMPRESSED_SUFFIX)
    temporary = target.with_name(target.name + ".tmp")
    try:
        with open(path, "rb") as source, open(temporary, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as packed:
                shutil.copyfileobj(source, packed)
            raw.flush()
            os.fsync(raw.fileno())
        if target.exists():
            raise FileExistsError(f"{target} already exists; refusing to overwrite a sealed file")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    path.unlink()
    return target


@dataclass
class CompressingKindWriter(KindWriter):
    """A `KindWriter` whose sealed files are compressed. Counts what compression did."""

    compressed_files: int = 0
    compressed_bytes_in: int = 0
    compressed_bytes_out: int = 0
    compress_failures: int = 0
    compress_last_error: str | None = None

    def _seal(self) -> Path | None:
        sealed = super()._seal()
        if sealed is None:
            return None
        size = sealed.stat().st_size
        try:
            packed = compress_sealed(sealed)
        except Exception as exc:  # noqa: BLE001 - the plain file is still valid data
            self.compress_failures += 1
            self.compress_last_error = f"{type(exc).__name__}: {exc}"
            return sealed
        self.compressed_files += 1
        self.compressed_bytes_in += size
        self.compressed_bytes_out += packed.stat().st_size
        return packed

    def stats(self) -> dict[str, Any]:
        out = super().stats()
        out.update({"compressed_files": self.compressed_files,
                    "compressed_bytes_in": self.compressed_bytes_in,
                    "compressed_bytes_out": self.compressed_bytes_out,
                    "compress_failures": self.compress_failures,
                    "compress_last_error": self.compress_last_error})
        return out


def compact_root(root: Path) -> dict[str, Any]:
    """Finish what a previous process left: stray temporaries and uncompressed sealed hours.

    Run once at open, after orphan recovery, under the writer lock. Recovery seals a crashed
    process's `.open` files as plain `.jsonl`; an own kind's plain file is then compressed here so
    the directory converges to one shape. A failure leaves the plain file and is reported.
    """
    report: dict[str, Any] = {"temporaries_removed": 0, "compressed": 0, "failed": []}
    for kind in COMPRESSED_KINDS:
        directory = root / kind
        if not directory.exists():
            continue
        for stray in sorted(directory.glob(f"*{COMPRESS_TMP_SUFFIX}")):
            stray.unlink(missing_ok=True)
            report["temporaries_removed"] += 1
        for sealed in sorted(directory.glob(f"*{FINAL_SUFFIX}")):
            try:
                compress_sealed(sealed)
                report["compressed"] += 1
            except Exception as exc:  # noqa: BLE001
                report["failed"].append({"file": sealed.name,
                                         "error": f"{type(exc).__name__}: {exc}"})
    return report


@dataclass
class ContextStore(Store):
    """`Store` with the production persistence policy. Open it with `ContextStore.open`."""

    shadow_bytes: bool = False
    #: kind -> {"rows": n, "bytes": n or None}. Bytes are only known in shadow mode.
    dropped: dict[str, dict[str, Any]] = field(default_factory=dict)
    compaction: dict[str, Any] = field(default_factory=dict)
    latest_writes: int = 0
    latest_writes_failed: int = 0
    latest_bytes: int = 0
    latest_last_error: str | None = None
    storage_error: str | None = None
    #: CONTRACT_CTX_V1_1 section 3. None keeps the cache on disk under the root, as V1 did.
    state_cache_dir: Path | None = None
    state_cache: dict[str, Any] = field(default_factory=dict)
    state_cache_recreated: int = 0

    @classmethod
    def open(cls, root: Path, session_id: str, *, started_ns: int, recover: bool = True,
             shadow_bytes: bool = False, state_cache_dir: Path | None = None
             ) -> "ContextStore":
        store = super().open(root, session_id, started_ns=started_ns, recover=recover)
        assert isinstance(store, ContextStore)
        store.shadow_bytes = shadow_bytes
        try:
            store.compaction = compact_root(root) if recover else {}
            if state_cache_dir is not None:
                store.state_cache_dir = Path(state_cache_dir)
                store.state_cache = install_state_cache(root, store.state_cache_dir)
        except BaseException:
            store.close()
            raise
        return store

    def _ensure_state_cache(self) -> None:
        """A volatile target can vanish (reboot, cleanup, deletion). Recreate it, never fail."""
        if self.state_cache_dir is None:
            return
        target = state_cache_target(self.root, self.state_cache_dir)
        if not target.is_dir():
            try:
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.state_cache_recreated += 1
            except OSError:
                return
        link = self.root / STATE_DIRNAME
        if not link.is_symlink():
            try:
                install_state_cache(self.root, self.state_cache_dir)
            except OSError:
                return

    def write_state(self, payload: dict[str, Any]) -> int:
        self._ensure_state_cache()
        return super().write_state(payload)

    # ------------------------------------------------------------------ writing

    def _writer(self, kind: str) -> KindWriter:
        if kind not in KINDS and kind not in CONTEXT_KINDS:
            raise ValueError(f"unknown record kind: {kind!r}")
        if kind in DROPPED_V0_KINDS:
            # Reaching here means a caller bypassed `write`. A dropped kind must never get a file.
            raise ValueError(f"record kind {kind!r} is not persisted by this collector")
        writer = self.writers.get(kind)
        if writer is None:
            factory = CompressingKindWriter if kind in COMPRESSED_KINDS else KindWriter
            writer = factory(root=self.root, kind=kind, session_id=self.session_id)
            self.writers[kind] = writer
        return writer

    def write(self, record: dict[str, Any], *, now_ns: int | None = None,
              now_ms: int | None = None) -> int:
        kind = str(record["kind"])
        if kind in DROPPED_V0_KINDS:
            entry = self.dropped.setdefault(kind, {"rows": 0, "bytes": None})
            entry["rows"] += 1
            if self.shadow_bytes:
                entry["bytes"] = (entry["bytes"] or 0) + len(canonical_line(record))
            return 0
        try:
            return super().write(record, now_ns=now_ns, now_ms=now_ms)
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError) and "unknown record kind" in str(exc):
                raise
            self.storage_error = f"{type(exc).__name__}: {exc}"
            raise StorageFailed(self.storage_error) from exc

    def tick(self, now_ns: int | None = None, now_ms: int | None = None) -> None:
        try:
            super().tick(now_ns, now_ms)
        except StoreAuthorityLost:
            raise
        except OSError as exc:
            self.storage_error = f"{type(exc).__name__}: {exc}"
            raise StorageFailed(self.storage_error) from exc

    # ------------------------------------------------------------------ latest context

    def latest_path(self) -> Path:
        return self.root / STATE_DIRNAME / LATEST_FILENAME

    def write_latest(self, payload: dict[str, Any]) -> int:
        """Replace `state/context_latest.json` atomically. Bytes written, or 0 on failure."""
        if not self.holds_authority():
            self.latest_writes_failed += 1
            self.latest_last_error = "StoreAuthorityLost: writer lock is no longer provable"
            return 0
        self._ensure_state_cache()
        directory = self.root / STATE_DIRNAME
        temporary = directory / f".{LATEST_FILENAME}.{os.getpid()}.tmp"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8")
            with open(temporary, "wb") as handle:
                handle.write(data)
            os.replace(temporary, self.latest_path())
        except Exception as exc:  # noqa: BLE001 - never fatal, like the state file
            self.latest_writes_failed += 1
            self.latest_last_error = f"{type(exc).__name__}: {exc}"
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return 0
        self.latest_writes += 1
        self.latest_bytes = len(data)
        return len(data)

    # ------------------------------------------------------------------ measurement

    def stats(self, *, now_ns: int | None = None, queue_backlog: int = 0,
              extra: dict[str, Any] | None = None) -> dict[str, Any]:
        out = super().stats(now_ns=now_ns, queue_backlog=queue_backlog, extra=extra)
        elapsed = max(1e-9, float(out["elapsed_s"]))
        per_day = 86_400 / elapsed
        dropped_rows = sum(entry["rows"] for entry in self.dropped.values())
        shadow_total = (sum(entry["bytes"] or 0 for entry in self.dropped.values())
                        if self.shadow_bytes else None)
        compressed_in = sum(getattr(w, "compressed_bytes_in", 0) for w in self.writers.values())
        compressed_out = sum(getattr(w, "compressed_bytes_out", 0) for w in self.writers.values())
        out["persistence_policy"] = {
            "persisted_v0_kinds": sorted(k for k in self.writers if k in KINDS),
            "own_kinds": sorted(k for k in self.writers if k in CONTEXT_KINDS),
            "dropped_v0_kinds": sorted(DROPPED_V0_KINDS),
            "dropped": {kind: dict(entry) for kind, entry in sorted(self.dropped.items())},
            "dropped_rows": dropped_rows,
            "dropped_rows_per_day": int(dropped_rows * per_day),
            "shadow_bytes": self.shadow_bytes,
            "shadow_dropped_bytes": shadow_total,
            "shadow_dropped_bytes_per_day": (None if shadow_total is None
                                             else int(shadow_total * per_day)),
            "compressed_bytes_in": compressed_in,
            "compressed_bytes_out": compressed_out,
            "compaction_at_open": self.compaction,
        }
        out["latest_file"] = {"writes": self.latest_writes, "failed": self.latest_writes_failed,
                              "bytes": self.latest_bytes, "last_error": self.latest_last_error,
                              "path": str(self.latest_path())}
        out["storage_error"] = self.storage_error
        out["state_cache"] = {"volatile": self.state_cache_dir is not None, **self.state_cache,
                              "recreated": self.state_cache_recreated}
        return out


__all__ = ["ContextStore", "CompressingKindWriter", "StorageFailed", "compress_sealed",
           "compact_root", "COMPRESS_TMP_SUFFIX", "install_state_cache", "state_cache_target"]
