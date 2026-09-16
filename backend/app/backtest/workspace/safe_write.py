"""Write contract for files that Google Drive replicates to the other PC.

A file is never streamed straight to its final name: a half-written file that syncs
looks complete to the other machine. Data goes to a sibling ``.partial`` first, is
flushed and checksummed, and only then replaced into place in one rename. The sibling
lives in the destination directory on purpose, because a rename is only atomic inside
one filesystem.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from app.backtest.workspace.errors import SafeWriteError


PARTIAL_SUFFIX = ".partial"
CHUNK = 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso(now: datetime | None = None) -> str:
    return (now or utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(directory: Path) -> None:
    # Best effort: several network and FUSE filesystems reject a directory fsync.
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_replace(source: Path, destination: Path) -> None:
    try:
        os.replace(source, destination)
    except OSError as error:
        raise SafeWriteError(f"cannot replace {destination}: {error}") from error
    _fsync_directory(destination.parent)


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, body.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class PartialWrite:
    destination: Path
    partial_path: Path
    checksum: str | None = None
    size: int | None = None


@contextmanager
def safe_write(destination: Path) -> Iterator[PartialWrite]:
    """Yield a ``.partial`` path; on a clean exit it is checksummed and renamed into place."""
    destination = Path(destination)
    if destination.name.endswith(PARTIAL_SUFFIX):
        raise SafeWriteError(f"{destination} is already a partial path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}{PARTIAL_SUFFIX}")
    partial.unlink(missing_ok=True)
    handle = PartialWrite(destination=destination, partial_path=partial)
    try:
        yield handle
        if not partial.is_file():
            raise SafeWriteError(f"{partial} was never written")
        size = partial.stat().st_size
        if size == 0:
            raise SafeWriteError(f"{partial} is empty")
        handle.checksum = sha256_file(partial)
        handle.size = size
        atomic_replace(partial, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
