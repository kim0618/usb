"""Hourly segment files: `.partial` -> fsync -> gzip -> fsync -> atomic rename -> sha256 in manifest.

Contract section 6. A leftover `.partial` (crash) is sealed on the next start after cutting the torn
last line. Existing files are never overwritten.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_hour(ms: int) -> tuple[str, str]:
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return d.strftime("%Y-%m-%d"), d.strftime("%H")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


class Manifest:
    def __init__(self, root: Path):
        self.path = root / "manifest" / "segments.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, rec: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())


def cut_torn_tail(p: Path) -> int:
    """Remove a trailing partial line (no newline, or not valid JSON). Returns bytes removed."""
    data = p.read_bytes()
    if not data:
        return 0
    keep = data
    if not data.endswith(b"\n"):
        keep = data[: data.rfind(b"\n") + 1]
    else:
        last = data[:-1].split(b"\n")[-1]
        try:
            json.loads(last)
        except ValueError:
            keep = data[: len(data) - len(last) - 1]
    removed = len(data) - len(keep)
    if removed:
        with open(p, "r+b") as f:
            f.truncate(len(keep))
            f.flush()
            os.fsync(f.fileno())
    return removed


def seal(partial: Path, manifest: Manifest, kind: str, exchange: str, recovered: bool = False, cut: int = 0) -> Path | None:
    """partial jsonl -> .jsonl.gz (atomic). Empty partials are removed and recorded as empty."""
    rows = 0
    first = last = None
    with open(partial, "rb") as f:
        for line in f:
            rows += 1
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("receive_ts_ms") or r.get("recv_ms")
            if t is not None:
                first = t if first is None else min(first, t)
                last = t if last is None else max(last, t)
    final = partial.with_name(partial.name.replace(".jsonl.partial", ".jsonl.gz"))
    n = 1
    while final.exists():                      # never overwrite
        final = partial.with_name(partial.name.replace(".jsonl.partial", f"-r{n}.jsonl.gz"))
        n += 1
    tmp = final.with_name(final.name + ".partial")
    with open(partial, "rb") as src, gzip.open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, final)
    _fsync_dir(final.parent)
    partial.unlink()
    manifest.append({"kind": kind, "exchange": exchange, "file": str(final), "rows": rows, "bytes": final.stat().st_size,
                     "sha256": sha256_file(final), "first_receive_ms": first, "last_receive_ms": last,
                     "recovered": recovered, "torn_bytes_cut": cut, "sealed_ms": int(time.time() * 1000)})
    return final


class SegmentWriter:
    """One stream (kind x exchange). Rows are routed to the UTC hour of their receive time."""

    def __init__(self, root: Path, kind: str, exchange: str, manifest: Manifest):
        self.root, self.kind, self.exchange, self.manifest = root, kind, exchange, manifest
        self.cur: tuple[str, str] | None = None
        self.f = None
        self.path: Path | None = None
        self.rows = 0

    def _path(self, date: str, hour: str) -> Path:
        return self.root / self.kind / f"exchange={self.exchange}" / f"date={date}" / f"{self.exchange}-{date}T{hour}.jsonl.partial"

    def write(self, rec: dict, receive_ms: int) -> None:
        dh = utc_hour(receive_ms)
        if dh != self.cur:
            self.close()
            self.cur = dh
            self.path = self._path(*dh)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():             # same hour after a restart: seal the old one first
                seal(self.path, self.manifest, self.kind, self.exchange, recovered=True, cut=cut_torn_tail(self.path))
            self.f = open(self.path, "a", buffering=1)
        self.f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self.rows += 1

    def sync(self) -> None:
        if self.f:
            self.f.flush()
            os.fsync(self.f.fileno())

    def close(self) -> None:
        if self.f:
            self.sync()
            self.f.close()
            self.f = None
            seal(self.path, self.manifest, self.kind, self.exchange)

    def rotate_if_stale(self, now_ms: int) -> None:
        if self.cur and utc_hour(now_ms) != self.cur:
            self.close()
            self.cur = None


def recover_partials(root: Path, manifest: Manifest) -> list[dict]:
    """Seal every leftover .partial from a previous (crashed) run."""
    out = []
    for p in sorted(root.glob("*/exchange=*/date=*/*.jsonl.gz.partial")):
        p.unlink()                              # half-written gzip from a crash during sealing; its source is intact
        out.append({"removed_half_gzip": str(p)})
    for p in sorted(root.glob("*/exchange=*/date=*/*.jsonl.partial")):
        cut = cut_torn_tail(p)
        exch = p.parent.parent.name.split("=", 1)[1]
        final = seal(p, manifest, p.parent.parent.parent.name, exch, recovered=True, cut=cut)
        out.append({"file": str(final), "torn_bytes_cut": cut})
    return out


def iter_norm_rows(root: Path, since_ms: int) -> "list[dict]":
    """Normalized rows from segments whose receive window may reach since_ms (for dedupe reload)."""
    rows = []
    for p in sorted(root.glob("norm/exchange=*/date=*/*.jsonl.gz")):
        date = p.parent.name.split("=", 1)[1]
        if datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000 < since_ms - 86_400_000:
            continue
        with gzip.open(p, "rt") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows
