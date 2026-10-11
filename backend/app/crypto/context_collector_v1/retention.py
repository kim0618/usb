"""Local retention of a context collector root: sealed persistent files older than N days.

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


__all__ = ["RETENTION_VERSION", "RETENTION_DAYS", "RETAINED_KINDS", "ACTIVE_SESSION_KINDS",
           "RootRefused", "plan", "prune", "current_session8", "active_session8", "main"]
