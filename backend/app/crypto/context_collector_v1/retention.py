"""Local retention of a context collector root.

**V1.2 (`RETENTION_CTX_V1_2.md`, the `retain` command) is what the timer runs.** It splits sealed
files into HOT, ARCHIVE_CANDIDATE and PRUNE_ELIGIBLE, writes archive manifests, and deletes only
files an external receipt has verified, behind the Q123 checkpoint guard. It is implemented in the
second half of this module. The V1.1 `prune` command below is kept unchanged for reference and
for its tests; no unit runs it any more.

V1.1: sealed persistent files older than N days.

`RETENTION_CTX_V1_1.md` fixes the policy; this module is its only implementation. It runs as its
own short process (`python -m app.crypto.context_collector_v1 prune`), never inside the collector,
so a failure here can at worst leave old files on disk. It takes no lock, opens no socket and
imports nothing outside this package and the V0 store's file-name constants.

What may be deleted is decided by **exclusion first**:

* only regular files directly inside `<root>/<kind>/` for a kind this collector persists, whose
  name is exactly a sealed V0 store name (`<kind>-YYYYMMDD-<session8>-NNNNN.jsonl[.gz]`);
* never a file still being written (`.jsonl.open`), a compression temporary (`.gz.tmp`), the
  current-state cache (`state/`, or a `state.disk-*` moved aside), the writer lock, a symbolic
  link, or anything outside those kind directories;
* never a `session`, `context`, `wall_v2` or `wall_r0` file of the **active session** - the session
  the writer lock names, i.e. the one that holds (or last held) it - at any age
  (`RETENTION_ACTIVE_SESSION_FIX_V1.md`). A reader rebuilding that session needs its first
  segments; a running session older than the retention period must not lose them;
* nothing at all when the active session cannot be established (no readable session id in the
  lock, or the state file naming another session): a run that cannot tell which files belong to
  the running session deletes none;
* a candidate is deleted only if **both** its modification time (when it was sealed) and the UTC
  date in its name (when it was opened) are older than the cut-off. Two clocks that must agree,
  so one wrong clock keeps a file rather than losing one.

The default is a dry run. `--apply` deletes. A root that does not look like a context collector
root (no `.writer.lock`, no `context/` directory) is refused before anything is listed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..market_structure_v0.contract import FINAL_SUFFIX, LOCK_FILENAME, OPEN_SUFFIX
from ..market_structure_v0.store import STATE_DIRNAME, STATE_FILENAME
from .contract import COMPRESSED_SUFFIX, CONTEXT_KIND, CONTEXT_KINDS, PERSISTED_V0_KINDS

#: Retention contract identity and the one number it fixes.
RETENTION_VERSION = "ctx-retention.v1.1"
RETENTION_DAYS = 7
#: Every kind whose sealed files are subject to retention. Nothing else is ever listed.
RETAINED_KINDS: tuple[str, ...] = tuple(PERSISTED_V0_KINDS) + tuple(CONTEXT_KINDS)
#: The kind whose current-session files are kept regardless of age.
SESSION_KIND = "session"
#: Every kind a reader needs to rebuild a session from its start. The active session's files of
#: these kinds are kept regardless of age; `telemetry`/`storage_stats` are not rebuilt from.
ACTIVE_SESSION_KINDS: tuple[str, ...] = (SESSION_KIND,) + tuple(CONTEXT_KINDS)

ROOT_ENV = "CTX_V1_ROOT"

_DAY_S = 86_400


class RootRefused(RuntimeError):
    """The path given is not a context collector root. Nothing was listed or deleted."""


def _name_pattern(kind: str) -> re.Pattern[str]:
    suffixes = "|".join(re.escape(s) for s in (FINAL_SUFFIX, COMPRESSED_SUFFIX))
    return re.compile(rf"^{re.escape(kind)}-(\d{{8}})-([0-9a-f]{{8}})-\d{{5}}(?:{suffixes})$")


def current_session8(root: Path) -> str | None:
    """The first eight characters of the session id the writer lock names, if it names one."""
    try:
        text = (root / LOCK_FILENAME).read_text(encoding="utf-8").strip()
        holder = json.loads(text)
    except (OSError, ValueError):
        return None
    session_id = holder.get("session_id") if isinstance(holder, dict) else None
    return str(session_id)[:8] if session_id else None


_SESSION8 = re.compile(r"^[0-9a-f]{8}$")


def active_session8(root: Path) -> tuple[str | None, str | None]:
    """`(session8, None)` for the active session, or `(None, reason)` when it cannot be told.

    The writer lock names it. The compact state file, when it names a session, must agree.
    """
    session8 = current_session8(root)
    if session8 is None:
        return None, "LOCK_NAMES_NO_SESSION"
    if not _SESSION8.match(session8):
        return None, "LOCK_SESSION_NOT_HEX8"
    try:
        state = json.loads((root / STATE_DIRNAME / STATE_FILENAME).read_bytes())
    except (OSError, ValueError):
        state = None
    session = state.get("session") if isinstance(state, dict) else None
    state_id = session.get("session_id") if isinstance(session, dict) else None
    if state_id and str(state_id)[:8] != session8:
        return None, "STATE_NAMES_OTHER_SESSION"
    return session8, None


def check_root(root: Path) -> None:
    if not root.is_dir():
        raise RootRefused(f"not a directory: {root}")
    if not (root / LOCK_FILENAME).exists():
        raise RootRefused(f"no {LOCK_FILENAME}: {root} is not a collector root")
    if not (root / CONTEXT_KIND).is_dir():
        raise RootRefused(f"no {CONTEXT_KIND}/ directory: {root} is not a context collector root")


def plan(root: Path, *, days: int = RETENTION_DAYS, now_s: float | None = None
         ) -> dict[str, Any]:
    """Every file under the root that retention would delete, and why each other one stays."""
    if days < 1:
        raise ValueError("retention must be at least one day")
    root = Path(root)
    check_root(root)
    now_s = time.time() if now_s is None else now_s
    cutoff_s = now_s - days * _DAY_S
    cutoff_date = datetime.fromtimestamp(cutoff_s, timezone.utc).strftime("%Y%m%d")
    protected_session8, identity_failure = active_session8(root)
    delete: list[dict[str, Any]] = []
    kept: dict[str, int] = {"young": 0, "active_session": 0, "identity_unknown": 0,
                            "not_sealed": 0, "unrecognised_name": 0, "not_regular_file": 0}
    for kind in RETAINED_KINDS:
        directory = root / kind
        if directory.is_symlink() or not directory.is_dir():
            continue
        pattern = _name_pattern(kind)
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file():
                kept["not_regular_file"] += 1
                continue
            if path.name.endswith(OPEN_SUFFIX) or path.name.endswith(".tmp"):
                kept["not_sealed"] += 1
                continue
            match = pattern.match(path.name)
            if match is None:
                kept["unrecognised_name"] += 1
                continue
            name_date, session8 = match.group(1), match.group(2)
            if kind in ACTIVE_SESSION_KINDS and session8 == protected_session8:
                kept["active_session"] += 1
                continue
            stat = path.stat()
            if not (stat.st_mtime < cutoff_s and name_date < cutoff_date):
                kept["young"] += 1
                continue
            if identity_failure is not None:
                kept["identity_unknown"] += 1
                continue
            delete.append({"path": str(path.relative_to(root)), "bytes": stat.st_size,
                           "sealed_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {"version": RETENTION_VERSION, "root": str(root), "days": days,
            "cutoff_utc": datetime.fromtimestamp(cutoff_s, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "protected_session8": protected_session8, "identity_failure": identity_failure,
            "delete": delete,
            "delete_files": len(delete), "delete_bytes": sum(d["bytes"] for d in delete),
            "kept": kept, "never_listed": [STATE_DIRNAME, f"{STATE_DIRNAME}.disk-*",
                                           LOCK_FILENAME, f"*{OPEN_SUFFIX}"]}


def prune(root: Path, *, days: int = RETENTION_DAYS, apply: bool = False,
          now_s: float | None = None) -> dict[str, Any]:
    """`plan`, then delete its list when `apply`. A file that fails to delete is reported."""
    report = plan(root, days=days, now_s=now_s)
    report["applied"] = apply
    report["deleted_files"] = 0
    report["deleted_bytes"] = 0
    report["failed"] = []
    if not apply:
        return report
    root = Path(root)
    for item in report["delete"]:
        path = root / item["path"]
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            report["failed"].append({"path": item["path"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        report["deleted_files"] += 1
        report["deleted_bytes"] += item["bytes"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.context_collector_v1 prune",
                                     description="Delete sealed context files older than N days.")
    parser.add_argument("--root", default=os.environ.get(ROOT_ENV))
    parser.add_argument("--days", type=int, default=RETENTION_DAYS)
    parser.add_argument("--apply", action="store_true",
                        help="delete; without it nothing is removed (dry run)")
    args = parser.parse_args(argv)
    if not args.root:
        print(json.dumps({"event": "REFUSED", "reason": "NO_ROOT"}), file=sys.stderr)
        return 2
    try:
        report = prune(Path(args.root).expanduser(), days=args.days, apply=args.apply)
    except (RootRefused, ValueError) as exc:
        print(json.dumps({"event": "REFUSED", "reason": str(exc)}), file=sys.stderr)
        return 2
    summary = {key: value for key, value in report.items() if key != "delete"}
    print(json.dumps({"event": "PRUNE", **summary}, sort_keys=True))
    return 1 if report["failed"] else 0


# =========================================================================== V1.2
#
# `RETENTION_CTX_V1_2.md`. Everything below is the `retain` command. Its rules, in the order a file
# meets them:
#
#   HOT                 not sealed, or young on either clock (per-kind period)
#   PINNED              `session` files: kept always, never archived or deleted
#   ARCHIVE_CANDIDATE   raw kinds (`context`, `wall_r0`, `wall_v2`) sealed and old on both clocks;
#                       listed in an immutable manifest
#   PRUNE_ELIGIBLE      a candidate that passes every gate below; deleted only when the run is
#                       `--apply` *and* pruning is enabled (CTX_V1_PRUNE_ENABLED=true)
#   EXPIRED             `telemetry`/`storage_stats` past the V1.1 seven days on both clocks. Their
#                       policy is V1.1's, unchanged and kept apart from the raw kinds: no manifest,
#                       no receipt, no Q123 guard, and not behind the prune switch - deleted on every
#                       `--apply` run whose session identity is established, exactly as V1.1 did.
#
# Gates, each failure a named keep reason: the session can be identified and its state is known;
# for `context`/`wall_r0` the Q123 checkpoint guard (below); a manifest lists the file; a valid
# external receipt lists it with the manifest's sha256; the file on disk still has that sha256.
# Disk pressure changes none of this. A run that cannot decide keeps.

from . import archive as A  # noqa: E402

RETENTION_V12_VERSION = "ctx-retention.v1.2"
#: `context`, `wall_r0`, `wall_v2` leave HOT after 48 h. `telemetry`/`storage_stats` expire after
#: the V1.1 seven days (`RETENTION_DAYS`), on V1.1's rule and independently of the prune switch.
HOT_HOURS = 48
TELEMETRY_HOT_HOURS = 7 * 24
MIN_HOT_HOURS = 24
#: A `context`/`wall_r0` segment of an active session is eligible only this many segments (hours)
#: before the oldest position any valid Q123 checkpoint holds.
MARGIN_SEGMENTS = 12
TELEMETRY_KINDS: tuple[str, ...] = ("telemetry", "storage_stats")
PINNED_KINDS: tuple[str, ...] = (SESSION_KIND,)
#: The kinds the Q123 forward recorder reads (`q123_forward_v1/stream.py KINDS`).
Q123_KINDS: tuple[str, ...] = ("context", "wall_r0")

#: Restated from `q123_forward_v1/recorder.py` (the crypto_context release does not ship Q123);
#: a parity test holds them to the recorder wherever both are importable.
Q123_CHECKPOINT_SCHEMA = "q123-forward-recorder.v1"
Q123_HORIZONS_MIN: tuple[int, ...] = (5, 15, 30, 60)
Q123_SAMPLE_MARGIN_MS = 120_000
Q123_ROW_MARGIN_MS = 2 * 3_600_000

ARCHIVE_ENV = "CTX_V1_ARCHIVE_DIR"
Q123_RUNTIME_ENV = "CTX_V1_Q123_RUNTIME"
Q123_STORE_ENV = "CTX_V1_Q123_STORE"
HOT_HOURS_ENV = "CTX_V1_HOT_HOURS"
TELEMETRY_HOT_HOURS_ENV = "CTX_V1_TELEMETRY_HOT_HOURS"
PRUNE_ENABLED_ENV = "CTX_V1_PRUNE_ENABLED"

STATE_ACTIVE = "ACTIVE"
STATE_CLOSED_CLEAN = "CLOSED_CLEAN"
STATE_CLOSED_CRASHED = "CLOSED_CRASHED"
STATE_UNKNOWN = "UNKNOWN"
CLOSED_STATES = (STATE_CLOSED_CLEAN, STATE_CLOSED_CRASHED)

Q123_OK = "OK"

_ANY_NAME = re.compile(r"^(?P<kind>[a-z_0-9]+)-(?P<date>\d{8})-(?P<s8>[0-9a-f]{8})-(?P<n>\d{5})"
                       r"(?P<suffix>\.jsonl\.gz|\.jsonl)$")


# --------------------------------------------------------------------------- Q123 checkpoints

def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Complete lines of a Q123 store file, read only. A torn tail is skipped, as the store's own
    read-only open does; a complete line that does not parse raises ValueError."""
    if not path.exists():
        return []
    rows = []
    with open(path, "rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            line = raw.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def q123_need_ms(store: Path) -> tuple[int | None, str | None]:
    """`Recorder._needed_from`, restated: the earliest sample a restart may still need.

    `(need, None)`, `(None, None)` for a store with nothing pending (the recorder then rebuilds
    from the session starts), or `(None, reason)` when the store cannot be read.
    """
    store = Path(store)
    if not (store / "meta.json").exists():
        return None, "STORE_MISSING"
    try:
        events = _jsonl_rows(store / "events.jsonl")
        outcome_keys = {r.get("key") for r in _jsonl_rows(store / "outcomes.jsonl")}
        approaches = _jsonl_rows(store / "approaches.jsonl")
    except (OSError, ValueError):
        return None, "STORE_UNREADABLE"
    pend = [ev["decision_ts"] for ev in events if ev.get("validity") == "VALID"
            and any(f'{ev["event_id"]}:{h}' not in outcome_keys for h in Q123_HORIZONS_MIN)]
    anchors = [a["anchor_ts"] for a in approaches if "anchor_ts" in a]
    frontier = (max(anchors) - Q123_ROW_MARGIN_MS) if anchors else None
    candidates = pend + ([frontier] if frontier is not None else [])
    return ((min(candidates) - Q123_SAMPLE_MARGIN_MS) if candidates else None), None


def _cursor_ok(cursor: Any) -> bool:
    if not isinstance(cursor, dict):
        return False
    pos = cursor.get("pos")
    if not isinstance(pos, dict):
        return False
    for kind in Q123_KINDS:
        at = pos.get(kind)
        if not (isinstance(at, list) and len(at) == 2 and all(isinstance(x, int) for x in at)
                and at[0] >= 1 and at[1] >= 0):
            return False
    return (isinstance(cursor.get("resting"), dict) and isinstance(cursor.get("pending"), list)
            and isinstance(cursor.get("done"), bool))


def read_q123(runtime: Path | None, store: Path | None) -> dict[str, Any]:
    """The Q123 checkpoint guard for `context`/`wall_r0`. `state == "OK"` or nothing is pruned.

    A checkpoint whose checksum fails is ignored, exactly as the recorder ignores it. A checkpoint
    whose checksum holds but whose schema or cursor shape is not the expected one is one the
    recorder might load and then read from a session start, so it fails the whole guard closed.
    """
    out: dict[str, Any] = {"state": None, "schema": Q123_CHECKPOINT_SCHEMA, "checkpoints": 0,
                           "valid": 0, "ignored_checksum": 0, "need_ms": None,
                           "selectable_at_need": 0, "oldest_ms": None, "newest_ms": None,
                           "sessions": {}, "unrecoverable": []}
    if runtime is None or not (Path(runtime) / "checkpoints").is_dir():
        out["state"] = "NO_RUNTIME"
        return out
    valid: list[dict[str, Any]] = []
    fail_closed: str | None = None
    for path in sorted((Path(runtime) / "checkpoints").glob("ckpt-*.json")):
        out["checkpoints"] += 1
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            out["ignored_checksum"] += 1
            continue
        if not isinstance(body, dict):
            out["ignored_checksum"] += 1
            continue
        digest = body.get("sha256")
        rest = {k: v for k, v in body.items() if k != "sha256"}
        if not isinstance(digest, str) or \
                hashlib.sha256(A.canonical(rest).encode()).hexdigest() != digest:
            out["ignored_checksum"] += 1
            continue
        if rest.get("schema") != Q123_CHECKPOINT_SCHEMA:
            fail_closed = fail_closed or "SCHEMA_MISMATCH"
            continue
        cursors = rest.get("cursors")
        if not isinstance(rest.get("sample_ms"), int) or not isinstance(cursors, dict) or \
                not all(_cursor_ok(c) for c in cursors.values()):
            fail_closed = fail_closed or "CHECKPOINT_STRUCTURE"
            continue
        valid.append(rest)
    out["valid"] = len(valid)
    if fail_closed:
        out["state"] = fail_closed
        return out
    if not valid:
        out["state"] = "NO_CHECKPOINT"
        return out
    out["oldest_ms"] = min(c["sample_ms"] for c in valid)
    out["newest_ms"] = max(c["sample_ms"] for c in valid)
    need, error = q123_need_ms(store) if store is not None else (None, "STORE_MISSING")
    out["need_ms"] = need
    if error:
        out["state"] = error
        return out
    if need is None:
        out["state"] = "NEED_NONE"           # the recorder would rebuild from session starts
        return out
    out["selectable_at_need"] = sum(1 for c in valid if c["sample_ms"] <= need)
    if not out["selectable_at_need"]:
        out["state"] = "NO_CHECKPOINT_AT_OR_BEFORE_NEED"
        return out
    unrecoverable: set[str] = set()
    for c in valid:
        unrecoverable |= set((c.get("unrecoverable") or {}).keys())
    status = Path(runtime) / "status.json"
    try:
        listed = json.loads(status.read_bytes()).get("unrecoverable_sessions") or {}
        unrecoverable |= set(listed.keys() if isinstance(listed, dict) else listed)
    except (OSError, ValueError, AttributeError):
        pass
    out["unrecoverable"] = sorted(unrecoverable)
    s8s = set().union(*(set(c["cursors"]) for c in valid))
    for s8 in sorted(s8s):
        holding = [c["cursors"][s8] for c in valid if s8 in c["cursors"]]
        out["sessions"][s8] = {
            "in_every_checkpoint": len(holding) == len(valid),
            "watermark": {kind: min(c["pos"][kind][0] for c in holding) for kind in Q123_KINDS},
            "done_in_every_checkpoint": len(holding) == len(valid) and all(c["done"]
                                                                          for c in holding),
            "resting_present": all(isinstance(c["resting"], dict) for c in holding),
            "resting_walls_newest": len(holding[-1]["resting"]) if holding else 0,
        }
    out["state"] = Q123_OK
    return out


# --------------------------------------------------------------------------- session states

def _session_records(root: Path, s8: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    directory = root / SESSION_KIND
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob(f"{SESSION_KIND}-*-{s8}-*")):
        if path.name.endswith(OPEN_SUFFIX) or not path.is_file():
            continue
        try:
            opener = __import__("gzip").open if path.name.endswith(".gz") else open
            with opener(path, "rb") as handle:
                for raw in handle:
                    if raw.endswith(b"\n") and raw.strip():
                        rows.append(json.loads(raw))
        except (OSError, ValueError):
            continue
    return rows


def session_states(root: Path, *, lock_s8: str | None, identity_failure: str | None
                   ) -> dict[str, dict[str, Any]]:
    """Every session8 with a file under the root, and what it is (section 4 of the contract)."""
    seen: set[str] = set()
    writing: set[str] = set()
    for kind in RETAINED_KINDS:
        directory = root / kind
        if directory.is_symlink() or not directory.is_dir():
            continue
        for path in directory.iterdir():
            name = path.name
            if name.endswith(OPEN_SUFFIX) or name.endswith(".tmp"):
                stem = name.split(".", 1)[0]
                parts = stem.split("-")
                if len(parts) >= 4 and _SESSION8.match(parts[-2]):
                    writing.add(parts[-2])
                    seen.add(parts[-2])
                continue
            match = _ANY_NAME.match(name)
            if match:
                seen.add(match["s8"])
    states: dict[str, dict[str, Any]] = {}
    for s8 in sorted(seen):
        records = _session_records(root, s8)
        start = next((r.get("payload") or {} for r in records
                      if (r.get("payload") or {}).get("event") == "START"), {})
        ended = any((r.get("payload") or {}).get("event") == "END" for r in records)
        if identity_failure is not None:
            state, why = STATE_UNKNOWN, identity_failure
        elif s8 == lock_s8:
            state, why = STATE_ACTIVE, "WRITER_LOCK_NAMES_IT"
        elif s8 in writing:
            state, why = STATE_UNKNOWN, "OPEN_FILES_OF_A_SESSION_THE_LOCK_DOES_NOT_NAME"
        else:
            state = STATE_CLOSED_CLEAN if ended else STATE_CLOSED_CRASHED
            why = "END_RECORD" if ended else "SUPERSEDED_WITHOUT_END_RECORD"
        states[s8] = {"state": state, "why": why, "session_id": start.get("session_id"),
                      "end_record": ended,
                      "journal_schema": {"collector_version": start.get("collector_version"),
                                         "contract": start.get("contract")} if start else None}
    return states


# --------------------------------------------------------------------------- plan and run

def _bump(table: dict[str, dict[str, int]], key: str, size: int) -> None:
    entry = table.setdefault(key, {"files": 0, "bytes": 0})
    entry["files"] += 1
    entry["bytes"] += size


def retain(root: Path, archive_dir: Path, *, q123_runtime: Path | None, q123_store: Path | None,
           hot_hours: int = HOT_HOURS, telemetry_hot_hours: int = TELEMETRY_HOT_HOURS,
           margin_segments: int = MARGIN_SEGMENTS, apply: bool = False,
           prune_enabled: bool = False, write_manifests: bool = True,
           now_s: float | None = None, usage: Any = None) -> dict[str, Any]:
    """One V1.2 run. Raw kinds are deleted only when `apply and prune_enabled`; expired
    `telemetry`/`storage_stats` on every `apply` run (V1.1's contract, independent of the switch)."""
    if min(hot_hours, telemetry_hot_hours) < MIN_HOT_HOURS:
        raise ValueError(f"HOT periods must be at least {MIN_HOT_HOURS} hours")
    if margin_segments < 1:
        raise ValueError("the Q123 margin must be at least one segment")
    root = Path(root)
    archive_dir = Path(archive_dir)
    check_root(root)
    now_s = time.time() if now_s is None else now_s
    disk_before = A.disk_health(root, usage=usage)
    lock_s8, identity_failure = active_session8(root)
    states = session_states(root, lock_s8=lock_s8, identity_failure=identity_failure)
    q123 = read_q123(q123_runtime, q123_store)

    files: list[dict[str, Any]] = []
    for kind in RETAINED_KINDS:
        directory = root / kind
        if directory.is_symlink() or not directory.is_dir():
            continue
        hours = telemetry_hot_hours if kind in TELEMETRY_KINDS else hot_hours
        cutoff_s = now_s - hours * 3600
        cutoff_date = datetime.fromtimestamp(cutoff_s, timezone.utc).strftime("%Y%m%d")
        for path in sorted(directory.iterdir()):
            rel = str(path.relative_to(root))
            if path.is_symlink() or not path.is_file():
                files.append({"path": rel, "kind": kind, "class": "KEEP", "reason": "NOT_REGULAR",
                              "bytes": 0})
                continue
            stat = path.stat()
            entry: dict[str, Any] = {"path": rel, "kind": kind, "bytes": stat.st_size,
                                     "mtime": stat.st_mtime}
            files.append(entry)
            if path.name.endswith(OPEN_SUFFIX) or path.name.endswith(".tmp"):
                entry.update({"class": "HOT", "reason": "NOT_SEALED"})
                continue
            match = _ANY_NAME.match(path.name)
            if match is None or match["kind"] != kind:
                entry.update({"class": "KEEP", "reason": "UNRECOGNISED_NAME"})
                continue
            entry.update({"s8": match["s8"], "segment": int(match["n"]), "date": match["date"],
                          "sealed_utc": A.utc_iso(stat.st_mtime)})
            if kind in PINNED_KINDS:
                entry.update({"class": "PINNED", "reason": "ALWAYS_KEPT"})
                continue
            if not (stat.st_mtime < cutoff_s and match["date"] < cutoff_date):
                entry.update({"class": "HOT", "reason": "YOUNG"})
                continue
            if kind in TELEMETRY_KINDS:
                entry.update({"class": "EXPIRED",
                              "reason": "IDENTITY_UNKNOWN" if identity_failure is not None
                              else "EXPIRED_V1_1_RETENTION"})
                continue
            entry["class"] = "ARCHIVE_CANDIDATE"

    candidates = [f for f in files if f["class"] == "ARCHIVE_CANDIDATE"]
    manifests, invalid_manifests = A.load_manifests(archive_dir)
    index = A.manifest_index(manifests)
    new_manifests: list[str] = []
    uncovered = [f for f in candidates if f["path"] not in index]
    if write_manifests and uncovered:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for f in uncovered:
            f["sha256"] = A.sha256_file(root / f["path"])
            groups.setdefault((f["s8"], f["date"]), []).append(f)
        for (s8, date), group in sorted(groups.items()):
            info = states.get(s8, {"state": STATE_UNKNOWN})
            sess_q = q123["sessions"].get(s8)
            manifest = A.build_manifest(
                session8=s8, utc_date=date, part=A.next_part(manifests, s8, date), files=group,
                session={"state": info["state"],
                         "session_id": info.get("session_id") or (
                             None if s8 != lock_s8 else _lock_session_id(root)),
                         "journal_schema": info.get("journal_schema")},
                q123={"state": q123["state"], "checkpoints_valid": q123["valid"],
                      "need_ms": q123["need_ms"],
                      "watermark": sess_q["watermark"] if sess_q else None,
                      "done": sess_q["done_in_every_checkpoint"] if sess_q else None},
                root=root, retention_version=RETENTION_V12_VERSION, now_s=now_s)
            A.write_manifest(archive_dir, manifest)
            manifests[manifest["manifest_id"]] = manifest
            new_manifests.append(manifest["manifest_id"])
        index = A.manifest_index(manifests)
    receipts, rejected_receipts = A.load_receipts(archive_dir, manifests)

    for f in candidates:
        state = states.get(f["s8"], {"state": STATE_UNKNOWN})["state"]
        reason = None
        if identity_failure is not None:
            reason = "IDENTITY_UNKNOWN"
        elif state == STATE_UNKNOWN:
            reason = "SESSION_STATE_UNKNOWN"
        elif f["kind"] in Q123_KINDS:
            sess_q = q123["sessions"].get(f["s8"])
            if q123["state"] != Q123_OK:
                reason = f'Q123_{q123["state"]}'
            elif f["s8"] in q123["unrecoverable"]:
                reason = "Q123_UNRECOVERABLE"
            elif sess_q is None or not sess_q["in_every_checkpoint"]:
                reason = "Q123_SESSION_NOT_IN_EVERY_CHECKPOINT"
            elif state == STATE_ACTIVE and \
                    f["segment"] > sess_q["watermark"][f["kind"]] - margin_segments:
                reason = "Q123_MARGIN"
            elif state in CLOSED_STATES and not sess_q["done_in_every_checkpoint"]:
                reason = "Q123_NOT_DONE"
        if reason is None:
            listed = index.get(f["path"])
            if listed is None:
                reason = "NO_MANIFEST"
            elif f["path"] not in receipts:
                reason = "NO_RECEIPT"
            else:
                want = listed[1]
                if receipts[f["path"]] != want["sha256"] or want["bytes"] != f["bytes"]:
                    reason = "RECEIPT_SHA_MISMATCH"
                else:
                    current = f.get("sha256") or A.sha256_file(root / f["path"])
                    f["sha256"] = current
                    if current != want["sha256"]:
                        reason = "CURRENT_SHA_MISMATCH"
        if reason is None:
            f["class"] = "PRUNE_ELIGIBLE"
            f["reason"] = "ELIGIBLE"
        else:
            f["reason"] = reason

    deleting = bool(apply and prune_enabled)
    deleted = {"files": 0, "bytes": 0}
    deleted_expired = {"files": 0, "bytes": 0}
    failed: list[dict[str, str]] = []

    def expired_deletable(f: dict[str, Any]) -> bool:
        return f["class"] == "EXPIRED" and f["reason"] == "EXPIRED_V1_1_RETENTION"

    for f in files:
        if f["class"] == "PRUNE_ELIGIBLE" and deleting:
            tally = deleted
        elif expired_deletable(f) and apply:
            tally = deleted_expired
        else:
            continue
        try:
            os.unlink(root / f["path"])
        except FileNotFoundError:
            continue
        except OSError as exc:
            failed.append({"path": f["path"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        f["deleted"] = True
        tally["files"] += 1
        tally["bytes"] += f["bytes"]

    by_class: dict[str, dict[str, int]] = {}
    keep_reasons: dict[str, dict[str, int]] = {}
    for f in files:
        _bump(by_class, f["class"], f["bytes"])
        if f["class"] != "PRUNE_ELIGIBLE" and not expired_deletable(f):
            _bump(keep_reasons, f["reason"], f["bytes"])
    expired = [f for f in files if expired_deletable(f)]
    candidates_all = [f for f in files if f["class"] in ("ARCHIVE_CANDIDATE", "PRUNE_ELIGIBLE")]
    verified_on_disk = [f for f in candidates_all if f["path"] in receipts]
    disk_after = (A.disk_health(root, usage=usage)
                  if deleted["files"] or deleted_expired["files"] else disk_before)
    return {
        "version": RETENTION_V12_VERSION, "root": str(root), "archive_dir": str(archive_dir),
        "now_utc": A.utc_iso(now_s), "applied": deleting, "apply_flag": apply,
        "prune_enabled": prune_enabled,
        "hot_hours": hot_hours, "telemetry_hot_hours": telemetry_hot_hours,
        "margin_segments": margin_segments,
        "identity": {"session8": lock_s8, "failure": identity_failure},
        "sessions": {s8: {k: v for k, v in info.items() if k != "journal_schema"}
                     for s8, info in states.items()},
        "q123": q123,
        "hot": by_class.get("HOT", {"files": 0, "bytes": 0}),
        "pinned": by_class.get("PINNED", {"files": 0, "bytes": 0}),
        "archive_candidate": {"files": len(candidates_all),
                              "bytes": sum(f["bytes"] for f in candidates_all)},
        "receipt_verified": {"files": len(verified_on_disk),
                             "bytes": sum(f["bytes"] for f in verified_on_disk)},
        "prune_eligible": by_class.get("PRUNE_ELIGIBLE", {"files": 0, "bytes": 0}),
        "deleted": deleted,
        "expired": {"files": len(expired), "bytes": sum(f["bytes"] for f in expired)},
        "deleted_expired": deleted_expired, "failed": failed,
        "keep_reasons": keep_reasons,
        "manifests": {"total": len(manifests), "new": new_manifests,
                      "invalid": invalid_manifests},
        "receipts": {"verified_paths": len(receipts), "rejected": rejected_receipts},
        "disk_before": disk_before, "disk_after": disk_after,
        "health": disk_after["state"],
        "files": files,
    }


def _lock_session_id(root: Path) -> str | None:
    try:
        holder = json.loads((root / LOCK_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return holder.get("session_id") if isinstance(holder, dict) else None


def summary(report: dict[str, Any]) -> dict[str, Any]:
    """What one run logs: the report without the per-file list and without wide session data."""
    q = report["q123"]
    out = {key: value for key, value in report.items() if key not in ("files", "q123")}
    out["q123"] = {key: q[key] for key in ("state", "checkpoints", "valid", "ignored_checksum",
                                           "need_ms", "selectable_at_need", "oldest_ms",
                                           "newest_ms", "unrecoverable")}
    out["q123"]["checkpoint_position"] = {s8: {"watermark": s["watermark"],
                                               "in_every_checkpoint": s["in_every_checkpoint"],
                                               "done": s["done_in_every_checkpoint"],
                                               "resting_walls_newest": s["resting_walls_newest"]}
                                          for s8, s in q["sessions"].items()}
    return out


def _env_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def retain_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.crypto.context_collector_v1 retain",
        description="Retention V1.2: HOT / ARCHIVE / PRUNE with the Q123 checkpoint guard.")
    parser.add_argument("--root", default=os.environ.get(ROOT_ENV))
    parser.add_argument("--archive-dir", default=os.environ.get(ARCHIVE_ENV))
    parser.add_argument("--q123-runtime", default=os.environ.get(Q123_RUNTIME_ENV))
    parser.add_argument("--q123-store", default=os.environ.get(Q123_STORE_ENV))
    parser.add_argument("--hot-hours", type=int,
                        default=int(os.environ.get(HOT_HOURS_ENV) or HOT_HOURS))
    parser.add_argument("--telemetry-hot-hours", type=int,
                        default=int(os.environ.get(TELEMETRY_HOT_HOURS_ENV) or TELEMETRY_HOT_HOURS))
    parser.add_argument("--margin-segments", type=int, default=MARGIN_SEGMENTS)
    parser.add_argument("--apply", action="store_true",
                        help="delete: expired telemetry/storage_stats always (V1.1), raw "
                             "kinds only when pruning is enabled as well")
    parser.add_argument("--prune-enabled", choices=("true", "false"),
                        default="true" if _env_flag(os.environ.get(PRUNE_ENABLED_ENV)) else "false",
                        help=f"default from {PRUNE_ENABLED_ENV}; false makes every run a dry run")
    parser.add_argument("--no-manifest", action="store_true", help="plan only; write nothing")
    args = parser.parse_args(argv)
    if not args.root or not args.archive_dir:
        print(json.dumps({"event": "REFUSED", "reason": "NO_ROOT_OR_ARCHIVE_DIR"}),
              file=sys.stderr)
        return 2
    try:
        report = retain(Path(args.root).expanduser(), Path(args.archive_dir).expanduser(),
                        q123_runtime=Path(args.q123_runtime) if args.q123_runtime else None,
                        q123_store=Path(args.q123_store) if args.q123_store else None,
                        hot_hours=args.hot_hours, telemetry_hot_hours=args.telemetry_hot_hours,
                        margin_segments=args.margin_segments, apply=args.apply,
                        prune_enabled=args.prune_enabled == "true",
                        write_manifests=not args.no_manifest)
    except (RootRefused, ValueError) as exc:
        print(json.dumps({"event": "REFUSED", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"event": "RETAIN", **summary(report)}, sort_keys=True))
    return 1 if report["failed"] else 0


__all__ = ["RETENTION_VERSION", "RETENTION_DAYS", "RETAINED_KINDS", "ACTIVE_SESSION_KINDS",
           "RootRefused", "plan", "prune", "current_session8", "active_session8", "main",
           "RETENTION_V12_VERSION", "HOT_HOURS", "TELEMETRY_HOT_HOURS", "MARGIN_SEGMENTS",
           "Q123_CHECKPOINT_SCHEMA", "Q123_KINDS", "read_q123", "q123_need_ms",
           "session_states", "retain", "summary", "retain_main"]
