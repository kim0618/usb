"""``state/CURRENT_STATE.json``: where the work stands, for whichever PC starts next.

The file answers one question - which stage is finished, against which commit, and what
comes next. It never holds data, a credential, or a path into a data file. It is written
atomically so a partially synced file can never be read as a finished stage.
"""

from dataclasses import dataclass, replace
from datetime import datetime
import json
import re

from app.backtest.workspace.errors import StateInvalid
from app.backtest.workspace.guards import assert_no_secret_like
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.safe_write import read_json, utc_now_iso, write_json_atomic


STATE_SCHEMA_VERSION = 1
STATE_FIELDS = ("state_schema_version", "stage", "status", "provider", "source_commit",
                "last_completed_at", "next_stage", "notes")
# A stage that is not committed yet must say so instead of borrowing a neighbouring hash.
UNCOMMITTED = "UNCOMMITTED"
STAGE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_NOTES = 2000


@dataclass(frozen=True)
class CurrentState:
    stage: str
    status: str
    provider: str
    source_commit: str
    last_completed_at: str
    next_stage: str
    notes: str = ""
    state_schema_version: int = STATE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "state_schema_version": self.state_schema_version,
            "stage": self.stage,
            "status": self.status,
            "provider": self.provider,
            "source_commit": self.source_commit,
            "last_completed_at": self.last_completed_at,
            "next_stage": self.next_stage,
            "notes": self.notes,
        }

    @property
    def committed(self) -> bool:
        return self.source_commit != UNCOMMITTED


def parse_state(payload: object) -> CurrentState:
    if not isinstance(payload, dict):
        raise StateInvalid("CURRENT_STATE.json is not a JSON object")
    unknown = sorted(set(payload) - set(STATE_FIELDS))
    if unknown:
        raise StateInvalid(f"CURRENT_STATE.json has unknown fields: {', '.join(unknown)}")
    missing = sorted(set(STATE_FIELDS) - set(payload))
    if missing:
        raise StateInvalid(f"CURRENT_STATE.json is missing fields: {', '.join(missing)}")
    assert_no_secret_like(payload, where="CURRENT_STATE.json")
    version = payload["state_schema_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != STATE_SCHEMA_VERSION:
        raise StateInvalid(
            f"state_schema_version must be {STATE_SCHEMA_VERSION}, got {version!r}")
    for field in ("stage", "status", "provider", "source_commit", "last_completed_at",
                  "next_stage", "notes"):
        if not isinstance(payload[field], str):
            raise StateInvalid(f"{field} must be a string")
    for field in ("stage", "status", "next_stage"):
        if not STAGE_PATTERN.match(payload[field]):
            raise StateInvalid(f"{field} must be an UPPER_SNAKE token, got {payload[field]!r}")
    if not PROVIDER_PATTERN.match(payload["provider"]):
        raise StateInvalid(f"provider must be a lower_snake token, got {payload['provider']!r}")
    commit = payload["source_commit"]
    if commit != UNCOMMITTED and not COMMIT_PATTERN.match(commit):
        raise StateInvalid(
            f"source_commit must be a 40-hex commit or {UNCOMMITTED}, got {commit!r}")
    _parse_timestamp(payload["last_completed_at"])
    if len(payload["notes"]) > MAX_NOTES:
        raise StateInvalid(f"notes exceed {MAX_NOTES} characters")
    return CurrentState(
        stage=payload["stage"], status=payload["status"], provider=payload["provider"],
        source_commit=commit, last_completed_at=payload["last_completed_at"],
        next_stage=payload["next_stage"], notes=payload["notes"],
        state_schema_version=version)


def _parse_timestamp(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise StateInvalid(f"last_completed_at is not an ISO-8601 timestamp: {value}") from error
    if moment.tzinfo is None:
        raise StateInvalid(f"last_completed_at must carry a UTC offset: {value}")
    return moment


def read_current_state(workspace: Workspace) -> CurrentState | None:
    path = workspace.current_state_path
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except json.JSONDecodeError as error:
        raise StateInvalid(f"CURRENT_STATE.json is not valid JSON: {error}") from error
    return parse_state(payload)


def write_current_state(workspace: Workspace, state: CurrentState) -> CurrentState:
    validated = parse_state(state.to_payload())
    write_json_atomic(workspace.current_state_path, validated.to_payload())
    return validated


def update_current_state(workspace: Workspace, *, now: datetime | None = None,
                         **changes: str) -> CurrentState:
    """Rewrite named fields of an existing state, stamping ``last_completed_at``."""
    existing = read_current_state(workspace)
    if existing is None:
        raise StateInvalid("CURRENT_STATE.json does not exist yet")
    unknown = sorted(set(changes) - set(STATE_FIELDS))
    if unknown:
        raise StateInvalid(f"cannot update unknown fields: {', '.join(unknown)}")
    changes.setdefault("last_completed_at", utc_now_iso(now))
    return write_current_state(workspace, replace(existing, **changes))
