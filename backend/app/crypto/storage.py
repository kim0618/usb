from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import COLLECTOR_VERSION, MARKET, PROVIDER, SCHEMA_VERSION, SYMBOL


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_line(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


@dataclass(frozen=True)
class ManifestEntry:
    provider: str
    market: str
    symbol: str
    dataset: str
    schema_version: str
    collector_version: str
    requested_start_ms: int
    requested_end_ms: int
    actual_start_ms: int | None
    actual_end_ms: int | None
    generated_at: str
    relative_path: str
    row_count: int
    checksum: str
    status: str
    requests: int
    retries: int
    rate_limits: int


class JsonlDatasetWriter:
    """Append-resumable JSONL writer with atomic finalization and no overwrite."""
    def __init__(self, final_path: Path) -> None:
        self.final_path = final_path
        self.partial_path = final_path.with_name(final_path.name + ".partial")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            raise FileExistsError(f"refusing silent overwrite: {final_path}")

    def resume_state(self) -> tuple[set[int], int | None]:
        """Read what survived, and drop a torn trailing line so the next append cannot glue onto it."""
        seen: set[int] = set()
        if not self.partial_path.exists():
            return seen, None
        complete_bytes = 0
        with self.partial_path.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(b"\n"):
                    break
                row = json.loads(raw)
                seen.add(int(row["timestamp_ms"]))
                complete_bytes += len(raw)
        if complete_bytes != self.partial_path.stat().st_size:
            with self.partial_path.open("r+b") as stream:
                stream.truncate(complete_bytes)
        return seen, max(seen) if seen else None

    def append(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with self.partial_path.open("ab") as stream:
            for row in rows:
                stream.write(canonical_line(row)); count += 1
            stream.flush(); os.fsync(stream.fileno())
        return count

    def finalize(self) -> tuple[int, str]:
        if not self.partial_path.exists():
            self.partial_path.touch()
        count = sum(1 for _ in self.partial_path.open("rb"))
        os.replace(self.partial_path, self.final_path)
        return count, sha256_file(self.final_path)


def write_manifest(root: Path, entry: ManifestEntry) -> Path:
    target = root / "manifest" / f"{entry.dataset}_{entry.requested_start_ms}_{entry.requested_end_ms}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing silent manifest overwrite: {target}")
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(json.dumps(asdict(entry), indent=2, sort_keys=True).encode() + b"\n")
    os.replace(partial, target)
    return target


def manifest_entry(**kwargs: Any) -> ManifestEntry:
    return ManifestEntry(provider=PROVIDER, market=MARKET, symbol=SYMBOL, schema_version=SCHEMA_VERSION, collector_version=COLLECTOR_VERSION, generated_at=datetime.now(timezone.utc).isoformat(), status="COMPLETE", **kwargs)


@dataclass(frozen=True)
class StreamManifestEntry:
    """Provenance for a realtime session's outputs; WS telemetry has no REST request counters."""
    provider: str
    market: str
    symbol: str
    dataset: str
    schema_version: str
    collector_version: str
    session_id: int
    session_start_ms: int
    session_end_ms: int
    actual_start_ms: int | None
    actual_end_ms: int | None
    generated_at: str
    relative_path: str
    row_count: int
    checksum: str
    status: str
    telemetry: dict[str, int]


def write_stream_manifest(root: Path, entry: StreamManifestEntry) -> Path:
    target = root / "manifest" / f"{entry.dataset}_session_{entry.session_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing silent manifest overwrite: {target}")
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(json.dumps(asdict(entry), indent=2, sort_keys=True).encode() + b"\n")
    os.replace(partial, target)
    return target


def stream_manifest_entry(**kwargs: Any) -> StreamManifestEntry:
    return StreamManifestEntry(provider=PROVIDER, market=MARKET, symbol=SYMBOL, schema_version=SCHEMA_VERSION, collector_version=COLLECTOR_VERSION, generated_at=datetime.now(timezone.utc).isoformat(), status="COMPLETE", **kwargs)
