"""The open BTC episode's entry context, read from the Manual Trade Snapshot Recorder's store.

Read only. The recorder (`app.crypto.manual_snapshot_v1`, its own oneshot) appends
`events.jsonl`, `snapshots.jsonl` and `episodes.jsonl`; this module opens them with `"rb"` and
nothing else, takes no lock and imports nothing from the recorder or any order path. When the
store is not configured, missing or unreadable, the answer is "not available" and the screen shows
only the current context.

Which row is the entry context:

* the open episode is the episode of the newest event, if that episode has no closed summary in
  `episodes.jsonl` and its newest event leaves a position (`qty_after` not zero);
* its entry is that episode's first `OPEN_*` event, and the context is the recorder's EVENT
  snapshot of it (`snapshot_id = "ev:<event_id>"`), taken as of the fill.

The store only grows, so a row once found does not change. Files are re-read only when their size
or mtime changed; the recorder appends at most once a minute.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The recorder's store directory (the one holding `events.jsonl`). Unset means no entry context.
STORE_ENV = "BTC_CONTEXT_MANUAL_STORE"

NOT_CONFIGURED = "NOT_CONFIGURED"
NO_STORE = "STORE_UNREADABLE"
NO_OPEN_EPISODE = "NO_OPEN_EPISODE"
NO_ENTRY_SNAPSHOT = "ENTRY_SNAPSHOT_NOT_WRITTEN"
FIXTURE_REFUSED = "FIXTURE_STORE_REFUSED"

#: Set to "1" only by a preview run (`--allow-fixture-entry`). A production unit never sets it,
#: so a hand-built store can never put an entry context on the operator's screen.
ALLOW_FIXTURE_ENV = "BTC_CONTEXT_ALLOW_FIXTURE_ENTRY"
SOURCE_RECORDER = "RECORDER"
SOURCE_FIXTURE = "FIXTURE"


def store_root() -> Path | None:
    raw = os.environ.get(STORE_ENV, "").strip()
    return Path(raw) if raw else None


def fixture_allowed() -> bool:
    return os.environ.get(ALLOW_FIXTURE_ENV, "").strip() == "1"


def snapshot_source(snapshot: dict[str, Any]) -> str:
    """RECORDER for a row the recorder wrote (`recorder._snapshot`: it stamps `recorded_at_ms`
    and its `contract`, and never a preview note); FIXTURE for anything else."""
    recorded = snapshot.get("recorded_at_ms")
    if (isinstance(recorded, int) and not isinstance(recorded, bool)
            and isinstance(snapshot.get("contract"), str)
            and not any(str(key).startswith("preview") for key in snapshot)):
        return SOURCE_RECORDER
    return SOURCE_FIXTURE


def _rows(path: Path, needle: str | None = None) -> list[dict[str, Any]]:
    """Whole JSON lines; a torn last line (the recorder repairs it on its next run) is skipped."""
    out: list[dict[str, Any]] = []
    with open(path, "rb") as handle:
        for line in handle:
            if needle is not None and needle.encode() not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def open_entry(root: Path, *, allow_fixture: bool = False) -> dict[str, Any]:
    """`{"available": True, "source": ..., "event": ..., "snapshot": ...}` or
    `{"available": False, "reason": ...}`. A row the recorder did not write is refused unless
    `allow_fixture` (a preview run); there is no other fallback."""
    try:
        events = _rows(root / "events.jsonl")
        closed_path = root / "episodes.jsonl"
        closed = {row.get("episode_id") for row in _rows(closed_path)} if closed_path.exists() else set()
    except OSError:
        return {"available": False, "reason": NO_STORE}
    if not events:
        return {"available": False, "reason": NO_OPEN_EPISODE}
    last = max(events, key=lambda row: (int(row.get("last_fill_ms") or 0)))
    episode = last.get("episode_id")
    try:
        held = float(last.get("qty_after") or 0) != 0
    except (TypeError, ValueError):
        held = False
    if episode is None or episode in closed or not held:
        return {"available": False, "reason": NO_OPEN_EPISODE}
    opens = [row for row in events if row.get("episode_id") == episode
             and str(row.get("event_type") or "").startswith("OPEN")]
    if not opens:
        return {"available": False, "reason": NO_OPEN_EPISODE}
    first = min(opens, key=lambda row: int(row.get("first_fill_ms") or 0))
    sid = f"ev:{first.get('event_id')}"
    try:
        found = [row for row in _rows(root / "snapshots.jsonl", needle=f'"{sid}"')
                 if row.get("snapshot_id") == sid]
    except OSError:
        found = []
    if not found:
        return {"available": False, "reason": NO_ENTRY_SNAPSHOT, "event": first}
    source = snapshot_source(found[0])
    if source != SOURCE_RECORDER and not allow_fixture:
        return {"available": False, "reason": FIXTURE_REFUSED, "event": first}
    return {"available": True, "source": source, "event": first, "snapshot": found[0]}


@dataclass
class EntryReader:
    """`open_entry` cached on the store files' (size, mtime)."""

    _key: tuple | None = None
    _value: dict[str, Any] = field(default_factory=lambda: {"available": False,
                                                            "reason": NOT_CONFIGURED})

    def read(self, root: Path | None, *, allow_fixture: bool = False) -> dict[str, Any]:
        if root is None:
            return {"available": False, "reason": NOT_CONFIGURED}
        key: list[Any] = [str(root), allow_fixture]
        for name in ("events.jsonl", "episodes.jsonl", "snapshots.jsonl"):
            try:
                st = (root / name).stat()
                key.append((st.st_size, st.st_mtime_ns))
            except OSError:
                key.append(None)
        if tuple(key) != self._key:
            self._value = open_entry(root, allow_fixture=allow_fixture)
            self._key = tuple(key)
        return self._value


__all__ = ["STORE_ENV", "ALLOW_FIXTURE_ENV", "EntryReader", "open_entry", "store_root",
           "fixture_allowed", "snapshot_source", "NOT_CONFIGURED", "NO_STORE", "NO_OPEN_EPISODE",
           "NO_ENTRY_SNAPSHOT", "FIXTURE_REFUSED", "SOURCE_RECORDER", "SOURCE_FIXTURE"]
