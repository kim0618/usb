"""Reading the collector's compact state file, and deciding whether to believe it.

V1 reconstructed the resting candidate set by walking the wall transition stream backwards,
because the journal records when a candidate opened and when it ended and never says "these are
resting now". That worked and was verified, but it cost the whole session's wall history on the
first poll: measured at 7.1 MB for a 12-minute session and 33.8 MB for a 75-minute one, growing
all day. An operator screen whose first read is proportional to how long the collector has been
up is a screen that stops working on the day it matters.

So the collector now publishes that set. It holds the writer lock, so it is the only process
entitled to say what the current state is, and the dictionary it publishes **is** the authority
for "resting now" rather than a reconstruction of it. The file is replaced atomically, so a
reader sees the previous complete state or the next one and never a torn one.

Four things are checked before the file is used, and each of them is a way to be wrong:

* **It must belong to the session being viewed.** A file left by a previous collector describes
  a book that no longer exists. Candidate observation never crosses a session boundary.
* **It must not be stale.** The file is rewritten every sample. If it stopped moving, the
  collector stopped, and the right answer on screen is "the collector stopped" - not the last
  state it happened to publish. This is the V1 defect that mattered most: a collector dead for
  26 seconds read as "trading LIVE".
* **It is not replay authority, and says so itself.** It accelerates a reader; an audit still
  reads the journal. The payload carries `is_authority: false` and this module refuses a file
  that claims otherwise, so a future writer cannot quietly promote a cache.
* **It may be truncated.** The active list is bounded, largest notional first. When the bound
  bites, the count is still exact and the list is not, and that has to reach the screen.

What remains after the checkpoint is a **tail**: wall transitions the collector wrote to the
journal after the state file was published. In practice the file is usually *ahead* of the
journal, because journal writes are buffered for up to a second while this file is replaced
immediately, so the tail is normally empty. It is read anyway, bounded by `seq`, because
"usually" is not a guarantee.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN

#: Where the collector publishes it. Mirrors `store.STATE_DIRNAME`/`STATE_FILENAME`; a test
#: asserts the two agree, because a silent disagreement would show an empty screen with no error.
STATE_DIRNAME = "state"
STATE_FILENAME = "collector_state.json"

#: Shapes the reader understands. `v1-2` adds the continuity ledger; `v1-1` is still accepted
#: and simply carries no ledger, in which case no wall is ever carried and the screen behaves
#: exactly as it did before. Reading an older file is not the same as inventing a carry for it.
SUPPORTED_STATE_VERSIONS = ("ms-v0-state.v1-1", "ms-v0-state.v1-2")

#: How old the file may be before it stops describing now. It is rewritten every sample, so
#: three sample intervals is the first age that cannot be explained by scheduling.
STALE_MS = 3_000

#: Why the checkpoint was not used. Each one falls back to the journal reconstruction.
MISSING = "STATE_FILE_ABSENT"
UNREADABLE = "STATE_FILE_UNREADABLE"
WRONG_SHAPE = "STATE_FILE_SHAPE_UNKNOWN"
CLAIMS_AUTHORITY = "STATE_FILE_CLAIMS_AUTHORITY"
OTHER_SESSION = "STATE_FILE_FROM_ANOTHER_SESSION"
STALE = "STATE_FILE_STALE"
USABLE = None


def state_path(root: Path) -> Path:
    return root / STATE_DIRNAME / STATE_FILENAME


@dataclass
class StateFile:
    """The compact state, with the verdict on whether it may be used."""

    path: Path
    present: bool = False
    usable: bool = False
    reason: str | None = MISSING
    payload: dict[str, Any] = field(default_factory=dict)
    bytes_read: int = 0
    age_ms: int | None = None

    # ------------------------------------------------------------------ accessors

    @property
    def session_id(self) -> str | None:
        session = self.payload.get("session") or {}
        value = session.get("session_id")
        return str(value) if value else None

    @property
    def seq(self) -> int | None:
        session = self.payload.get("session") or {}
        value = session.get("seq")
        return value if isinstance(value, int) else None

    @property
    def written_ms(self) -> int | None:
        value = self.payload.get("written_ms")
        return value if isinstance(value, int) else None

    @property
    def session_ended(self) -> bool:
        return bool((self.payload.get("session") or {}).get("ended"))

    @property
    def derived(self) -> dict[str, Any]:
        value = self.payload.get("derived")
        return value if isinstance(value, dict) else {}

    @property
    def walls(self) -> dict[str, Any]:
        value = self.payload.get("walls")
        return value if isinstance(value, dict) else {}

    @property
    def wall_items(self) -> list[dict[str, Any]]:
        items = self.walls.get("items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @property
    def truncated(self) -> bool:
        return bool(self.walls.get("truncated"))

    @property
    def active_count(self) -> int | None:
        counters = self.walls.get("counters") or {}
        value = counters.get("active")
        return value if isinstance(value, int) else None

    @property
    def resnapshot(self) -> dict[str, Any]:
        value = self.payload.get("resnapshot")
        return value if isinstance(value, dict) else {}

    @property
    def continuity(self) -> dict[str, Any]:
        """The collector's continuity ledger. Empty on a `v1-1` file, which carries none."""
        value = self.payload.get("continuity")
        return value if isinstance(value, dict) else {}

    @property
    def freshness(self) -> dict[str, Any]:
        value = self.payload.get("freshness")
        return value if isinstance(value, dict) else {}

    @property
    def coverage(self) -> dict[str, Any]:
        value = self.payload.get("coverage")
        return value if isinstance(value, dict) else {}

    @property
    def telemetry_recent(self) -> list[dict[str, Any]]:
        items = self.payload.get("telemetry_recent")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def view(self) -> dict[str, Any]:
        """What the screen says about the checkpoint itself."""
        return {
            "path": str(self.path),
            "present": self.present,
            "usable": self.usable,
            "unusable_reason": self.reason,
            "state_version": self.payload.get("state_version"),
            "written_ms": self.written_ms,
            "age_ms": self.age_ms,
            "stale_ms": STALE_MS,
            "bytes": self.bytes_read,
            "seq": self.seq,
            "active_count": self.active_count,
            "items": len(self.wall_items),
            "truncated": self.truncated,
            "is_authority": False,
            "authority": "JOURNAL",
        }


def read(root: Path, *, now_ms: int, session_id: str | None = None) -> StateFile:
    """Read and judge the compact state file. Never raises; an unusable file is a reason."""
    path = state_path(root)
    state = StateFile(path=path)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return state
    except OSError:
        state.reason = UNREADABLE
        return state
    state.present = True
    state.bytes_read = len(data)
    try:
        payload = json.loads(data)
    except ValueError:
        # A torn read should be impossible through `os.replace`, but a reader that trusts that
        # and crashes is worse than one that falls back.
        state.reason = UNREADABLE
        return state
    if not isinstance(payload, dict):
        state.reason = WRONG_SHAPE
        return state
    state.payload = payload
    if str(payload.get("state_version") or "") not in SUPPORTED_STATE_VERSIONS:
        state.reason = WRONG_SHAPE
        return state
    if payload.get("is_authority") is not False:
        # The journal is the authority. A state file that claims to be one is either a different
        # artefact or a mistake, and either way is not what this reader was built to trust.
        state.reason = CLAIMS_AUTHORITY
        return state
    written = state.written_ms
    state.age_ms = None if written is None else max(0, now_ms - written)
    if session_id is not None and state.session_id != session_id:
        state.reason = OTHER_SESSION
        return state
    if state.age_ms is None or state.age_ms > STALE_MS:
        state.reason = STALE
        return state
    state.usable = True
    state.reason = USABLE
    return state


def best_coverage(derived: dict[str, Any]) -> str:
    """The best coverage any band achieved, in the contract's own vocabulary."""
    bands = (derived.get("book") or {}).get("bands") or []
    found = {str((band.get(side) or {}).get("coverage"))
             for band in bands if isinstance(band, dict) for side in ("bid", "ask")}
    if COMPLETE in found:
        return COMPLETE
    return PARTIAL if PARTIAL in found else UNKNOWN


__all__ = ["STATE_DIRNAME", "STATE_FILENAME", "SUPPORTED_STATE_VERSIONS", "STALE_MS", "MISSING",
           "UNREADABLE", "WRONG_SHAPE", "CLAIMS_AUTHORITY", "OTHER_SESSION", "STALE", "USABLE",
           "StateFile", "state_path", "read", "best_coverage"]
