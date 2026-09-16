"""``state/workspace.json``: proof that both PCs are pointing at the same shared root.

Nothing machine specific is recorded, so the file is identical on every PC and never
produces a sync conflict. An existing file is validated, never rewritten.
"""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from app.backtest.workspace.discovery import WORKSPACE_DIR_NAME
from app.backtest.workspace.errors import StateInvalid, WorkspaceSchemaIncompatible
from app.backtest.workspace.guards import assert_no_secret_like
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.safe_write import read_json, utc_now_iso, write_json_atomic


IDENTITY_SCHEMA_VERSION = 1
IDENTITY_FIELDS = ("workspace_schema_version", "workspace_name", "created_at", "updated_at")


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_schema_version: int
    workspace_name: str
    created_at: str
    updated_at: str

    def to_payload(self) -> dict[str, object]:
        return {
            "workspace_schema_version": self.workspace_schema_version,
            "workspace_name": self.workspace_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def parse_identity(payload: object) -> WorkspaceIdentity:
    if not isinstance(payload, dict):
        raise StateInvalid("workspace.json is not a JSON object")
    unknown = sorted(set(payload) - set(IDENTITY_FIELDS))
    if unknown:
        raise StateInvalid(f"workspace.json has unknown fields: {', '.join(unknown)}")
    missing = sorted(set(IDENTITY_FIELDS) - set(payload))
    if missing:
        raise StateInvalid(f"workspace.json is missing fields: {', '.join(missing)}")
    assert_no_secret_like(payload, where="workspace.json")
    version = payload["workspace_schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise StateInvalid("workspace_schema_version must be an integer")
    if version != IDENTITY_SCHEMA_VERSION:
        raise WorkspaceSchemaIncompatible(
            f"workspace.json schema {version} is not supported (expected {IDENTITY_SCHEMA_VERSION})")
    for field in ("workspace_name", "created_at", "updated_at"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise StateInvalid(f"{field} must be a non-empty string")
    for field in ("created_at", "updated_at"):
        _parse_timestamp(field, payload[field])
    return WorkspaceIdentity(version, payload["workspace_name"],
                             payload["created_at"], payload["updated_at"])


def _parse_timestamp(field: str, value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise StateInvalid(f"{field} is not an ISO-8601 timestamp: {value}") from error
    if moment.tzinfo is None:
        raise StateInvalid(f"{field} must carry a UTC offset: {value}")
    return moment


def read_identity(workspace: Workspace) -> WorkspaceIdentity | None:
    path: Path = workspace.identity_path
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except json.JSONDecodeError as error:
        raise StateInvalid(f"workspace.json is not valid JSON: {error}") from error
    return parse_identity(payload)


def ensure_identity(workspace: Workspace, *,
                    now: datetime | None = None) -> tuple[WorkspaceIdentity, bool]:
    """Return the identity and whether it was created by this call."""
    existing = read_identity(workspace)
    if existing is not None:
        return existing, False
    stamp = utc_now_iso(now)
    identity = WorkspaceIdentity(IDENTITY_SCHEMA_VERSION, WORKSPACE_DIR_NAME, stamp, stamp)
    write_json_atomic(workspace.identity_path, identity.to_payload())
    return identity, True
