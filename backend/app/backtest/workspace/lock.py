"""Workspace writer lock: one writer at a time across the home and office PC.

The lock file is created with O_EXCL so two processes cannot both win. Because Google
Drive can leave a lock behind after a crash and can also deliver it minutes late, a
present file is not treated as a permanent block: the holder renews a lease, and once
the lease expires the lock is reported STALE. Even then it is never removed
automatically - only the explicit ``unlock`` command can break it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
import os
from typing import Any
import uuid

from app.backtest.workspace.errors import (
    StateInvalid, WriterLockHeld, WriterLockNotOwned, WriterLockStale, WriterLockUnreadable,
)
from app.backtest.workspace.guards import assert_no_secret_like
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.safe_write import (
    read_json, utc_now, utc_now_iso, write_json_atomic,
)


LOCK_SCHEMA_VERSION = 1
LOCK_FIELDS = ("lock_schema_version", "owner_id", "pid", "acquired_at", "heartbeat_at",
               "lease_seconds", "purpose")
# Google Drive propagation is measured in minutes, so a short lease would look stale
# while the holder is still working.
DEFAULT_LEASE_SECONDS = 1800


@dataclass(frozen=True)
class WriterLock:
    owner_id: str
    pid: int
    acquired_at: str
    heartbeat_at: str
    lease_seconds: int
    purpose: str
    lock_schema_version: int = LOCK_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "lock_schema_version": self.lock_schema_version,
            "owner_id": self.owner_id,
            "pid": self.pid,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
            "lease_seconds": self.lease_seconds,
            "purpose": self.purpose,
        }

    def expires_at(self) -> datetime:
        return datetime.fromisoformat(self.heartbeat_at) + timedelta(seconds=self.lease_seconds)

    def is_stale(self, now: datetime | None = None) -> bool:
        return (now or utc_now()) > self.expires_at()


def parse_lock(payload: object) -> WriterLock:
    if not isinstance(payload, dict):
        raise StateInvalid("writer lock is not a JSON object")
    unknown = sorted(set(payload) - set(LOCK_FIELDS))
    if unknown:
        raise StateInvalid(f"writer lock has unknown fields: {', '.join(unknown)}")
    missing = sorted(set(LOCK_FIELDS) - set(payload))
    if missing:
        raise StateInvalid(f"writer lock is missing fields: {', '.join(missing)}")
    assert_no_secret_like(payload, where="writer lock")
    if payload["lock_schema_version"] != LOCK_SCHEMA_VERSION:
        raise StateInvalid(f"writer lock schema {payload['lock_schema_version']} is not supported")
    for field in ("owner_id", "acquired_at", "heartbeat_at", "purpose"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise StateInvalid(f"{field} must be a non-empty string")
    for field in ("pid", "lease_seconds"):
        if not isinstance(payload[field], int) or isinstance(payload[field], bool):
            raise StateInvalid(f"{field} must be an integer")
    if payload["lease_seconds"] <= 0:
        raise StateInvalid("lease_seconds must be positive")
    for field in ("acquired_at", "heartbeat_at"):
        try:
            moment = datetime.fromisoformat(payload[field])
        except ValueError as error:
            raise StateInvalid(f"{field} is not an ISO-8601 timestamp") from error
        if moment.tzinfo is None:
            raise StateInvalid(f"{field} must carry a UTC offset")
    return WriterLock(owner_id=payload["owner_id"], pid=payload["pid"],
                      acquired_at=payload["acquired_at"], heartbeat_at=payload["heartbeat_at"],
                      lease_seconds=payload["lease_seconds"], purpose=payload["purpose"],
                      lock_schema_version=payload["lock_schema_version"])


def read_lock(workspace: Workspace) -> WriterLock | None:
    path = workspace.writer_lock_path
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except json.JSONDecodeError as error:
        raise WriterLockUnreadable(f"writer lock is not valid JSON: {error}") from error
    try:
        return parse_lock(payload)
    except StateInvalid as error:
        raise WriterLockUnreadable(error.reason) from error


def acquire(workspace: Workspace, *, purpose: str, lease_seconds: int = DEFAULT_LEASE_SECONDS,
            now: datetime | None = None, owner_id: str | None = None) -> WriterLock:
    stamp = utc_now_iso(now)
    lock = WriterLock(owner_id=owner_id or uuid.uuid4().hex, pid=os.getpid(), acquired_at=stamp,
                      heartbeat_at=stamp, lease_seconds=lease_seconds, purpose=purpose)
    path = workspace.writer_lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(lock.to_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        _reject(workspace, now=now)
        raise
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    return lock


def _reject(workspace: Workspace, *, now: datetime | None) -> None:
    held = read_lock(workspace)
    if held is None:
        raise WriterLockHeld("writer lock appeared and vanished; retry")
    if held.is_stale(now):
        raise WriterLockStale(
            f"writer lock held by {held.owner_id} for {held.purpose} expired at "
            f"{held.expires_at().isoformat()}; run backtest_workspace unlock --force to break it")
    raise WriterLockHeld(
        f"writer lock held by {held.owner_id} (pid {held.pid}) for {held.purpose} since "
        f"{held.acquired_at}")


def heartbeat(workspace: Workspace, lock: WriterLock, *, now: datetime | None = None) -> WriterLock:
    held = read_lock(workspace)
    if held is None or held.owner_id != lock.owner_id:
        raise WriterLockNotOwned(f"writer lock is no longer owned by {lock.owner_id}")
    renewed = replace(lock, heartbeat_at=utc_now_iso(now))
    write_json_atomic(workspace.writer_lock_path, renewed.to_payload())
    return renewed


def release(workspace: Workspace, lock: WriterLock) -> None:
    held = read_lock(workspace)
    if held is None:
        return
    if held.owner_id != lock.owner_id:
        raise WriterLockNotOwned(
            f"writer lock is owned by {held.owner_id}, not {lock.owner_id}")
    workspace.writer_lock_path.unlink(missing_ok=True)


def force_release(workspace: Workspace, *, require_stale: bool = True,
                  now: datetime | None = None) -> WriterLock | None:
    """Explicit operator action only: never called on the automatic acquire path."""
    held = read_lock(workspace)
    if held is None:
        return None
    if require_stale and not held.is_stale(now):
        raise WriterLockHeld(
            f"writer lock held by {held.owner_id} is still live until "
            f"{held.expires_at().isoformat()}")
    workspace.writer_lock_path.unlink(missing_ok=True)
    return held


@contextmanager
def writer_lock(workspace: Workspace, *, purpose: str,
                lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Iterator[WriterLock]:
    lock = acquire(workspace, purpose=purpose, lease_seconds=lease_seconds)
    try:
        yield lock
    finally:
        release(workspace, lock)
