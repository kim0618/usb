"""PAPER run retention V1: COLD archive of run files the recovery contract no longer reads, then prune.

The run root (`CRYPTO_PAPER_ROOT`) only ever grows: one market record a second per active run,
cut into immutable segments with a checkpoint at each cut. A restart reads very little of it:

* a run that recovers alone loads its newest parseable checkpoint and replays the segments after
  it and the active tape (`persistence.recover_from_checkpoint`);
* the runs that share the wallet (the default run and every run with a wallet event) start
  together from checkpoints at a common cut and replay what follows (`wallet_recovery`);
* charts never read the tape, and the ledger - not the tape - holds every fill, charge and the
  MAE/MFE of every closed position.

Older segments are a safety net for one case: a checkpoint that cannot be used and a full replay
in its place. Without them that replay refuses (LEDGER_DIVERGENCE / WALLET_CUT_UNAVAILABLE), so
losing them costs availability, never a wrong state. This module keeps what recovery reads, plus
one fallback checkpoint for every active run, and moves the rest to COLD the way the context
collector's retention does:

1. `plan --write-manifests` (server): classify every run, choose what may leave, write an
   immutable sealed manifest of those files. Run files are only read; `segments.load_manifests`
   is not called because it repairs (writes) a missing segment manifest.
2. `pull` (operator's PC): fetch manifests, rsync the listed files, verify size, sha256, gzip
   and JSON/JSONL integrity, write a sealed receipt and upload it.
3. `prune` (server): re-plan from scratch and delete a file only when the fresh plan still
   selects it, a manifest lists it, a valid receipt verified it, and the file on disk still has
   that sha256. Dry run unless `--apply`.

Classes, decided from each run's ledger, checkpoints and the HOT slot (never from its name):

* ACTIVE_OPEN_POSITION, ACTIVE_DEFAULT (the build's own symbol, flat), ACTIVE_CURRENT_HOT: keep
  ledger, config, active tape, and every segment and checkpoint from the second newest usable
  checkpoint on (the newest plus one fallback), extended further back whenever a simulated
  wallet recovery needs it.
* CLOSED_TRADED: keep ledger, config, active tape, final checkpoint and last segment.
* CLOSED_NO_TRADE: no wallet event ever, flat, not the HOT symbol: the whole run directory may go.
  The running service holds such a run as a retired session and would write into it if it were
  revived, so whole-run removal requires the paper service to be stopped.
* RETIRED_UNDISCOVERED: a paper run directory the terminal never discovers (so nothing reads it
  and it is in no wallet), recognised from its own `run_config.json` and ledger rather than its
  name, with no wallet event and no position. Any other undiscovered directory is BLOCKED.

Anything that cannot be decided - an unreadable ledger, a position the checkpoint and the ledger
disagree on, a command on the tape the ledger has not caught up with, a segment without a
manifest, an unreadable HOT slot - blocks that run (or every HOT candidate) completely.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from ..symbols import DEFAULT_SYMBOL, SUPPORTED_SYMBOLS
from . import segments as segment_store
from .ledger import InputKind
from .wallet_recovery import WALLET_INERT_COMMANDS, WALLET_INERT_EVENTS, _pick

RETENTION_VERSION = "paper-run-retention.v1"
MANIFEST_VERSION = "paper-run-archive-manifest.v1"
RECEIPT_VERSION = "paper-run-archive-receipt.v1"
MANIFEST_DIRNAME = "manifests"
RECEIPT_DIRNAME = "receipts"
LOCK_FILENAME = ".retention.lock"
RESULT_OK = "OK"
RESULT_PARTIAL = "PARTIAL"
RESULT_FAILED = "FAILED"
SERVICE_UNIT = "usb-crypto-paper.service"

ACTIVE_OPEN_POSITION = "ACTIVE_OPEN_POSITION"
ACTIVE_DEFAULT = "ACTIVE_DEFAULT"
ACTIVE_CURRENT_HOT = "ACTIVE_CURRENT_HOT"
CLOSED_TRADED = "CLOSED_TRADED"
CLOSED_NO_TRADE = "CLOSED_NO_TRADE"
RETIRED_UNDISCOVERED = "RETIRED_UNDISCOVERED"
BLOCKED_RUN = "BLOCKED"
ACTIVE_CLASSES = (ACTIVE_OPEN_POSITION, ACTIVE_DEFAULT, ACTIVE_CURRENT_HOT)
WHOLE_RUN_CLASSES = (CLOSED_NO_TRADE, RETIRED_UNDISCOVERED)

KEEP = "KEEP"
ARCHIVE_REQUIRED = "ARCHIVE_REQUIRED"
PRUNE_ELIGIBLE = "PRUNE_ELIGIBLE"
BLOCKED = "BLOCKED"

LEDGER_FILE = segment_store.LEDGER_FILE
ACTIVE_TAPE = segment_store.ACTIVE_TAPE
RUN_CONFIG_FILE = "run_config.json"
_OPENING = ("POSITION_OPEN", "POSITION_INCREASE")
_REDUCING = ("POSITION_REDUCE", "POSITION_CLOSE")
_CHUNK = 1 << 20


# --------------------------------------------------------------------------- primitives

def canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seal(body: dict[str, Any], field_name: str) -> dict[str, Any]:
    out = {k: v for k, v in body.items() if k != field_name}
    out[field_name] = hashlib.sha256(canonical(out).encode()).hexdigest()
    return out


def sealed_ok(body: Any, field_name: str) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get(field_name), str):
        return False
    rest = {k: v for k, v in body.items() if k != field_name}
    return hashlib.sha256(canonical(rest).encode()).hexdigest() == body[field_name]


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def aggregate_digest(entries: Iterable[dict[str, Any]]) -> str:
    lines = sorted(f'{e["sha256"]}  {e["path"]}' for e in entries)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# --------------------------------------------------------------------------- reading a run

@dataclass
class RunFacts:
    """Everything the plan needs about one run directory, read without writing anything."""

    name: str
    run_dir: Path
    symbol: str | None
    events: list[dict[str, Any]] = field(default_factory=list)
    wallet_times: list[int] = field(default_factory=list)
    position: Decimal | None = None
    segments: dict[int, segment_store.SegmentManifest] = field(default_factory=dict)
    segment_files: dict[int, Path] = field(default_factory=dict)
    orphan_manifests: dict[int, Path] = field(default_factory=dict)
    checkpoints: dict[int, dict[str, Any]] = field(default_factory=dict)
    checkpoint_files: dict[int, Path] = field(default_factory=dict)
    torn_checkpoints: list[int] = field(default_factory=list)
    problem: str | None = None

    @property
    def traded(self) -> bool:
        return bool(self.wallet_times)

    def usable(self, index: int) -> bool:
        """The checks `segments.restore` makes before trusting a checkpoint (`_usable`)."""
        payload = self.checkpoints.get(index)
        if payload is None or payload.get("version") != segment_store.CHECKPOINT_VERSION:
            return False
        if payload.get("run_id") != self.name:
            return False
        try:
            expected = int(payload["ledger_bytes"])
            events = int(payload["ledger_events"])
        except (KeyError, TypeError, ValueError):
            return False
        ledger = self.run_dir / LEDGER_FILE
        if expected:
            if not ledger.exists() or ledger.stat().st_size < expected:
                return False
            if segment_store.sha256_prefix(ledger, expected) != payload.get("ledger_sha256"):
                return False
        return len(self.events) >= events


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    with open(path, "rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                break                         # a torn tail is not an event yet
            if raw.strip():
                events.append(json.loads(raw))
    return events


def _last_line(path: Path) -> dict[str, Any] | None:
    """The last complete JSONL record of a plain file, read from its end.

    A trailing line without its newline is a torn append, not a record, exactly as the tape's
    own reader treats it.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        start = end
        while start > 0:
            start = max(0, start - (1 << 16))
            handle.seek(start)
            chunk = handle.read(end - start)
            body = chunk[:chunk.rfind(b"\n") + 1]          # complete lines only
            lines = [line for line in body.split(b"\n") if line.strip()]
            # Unless the read reached the file's start, the first line may be cut in half.
            usable = lines if start == 0 else lines[1:]
            if usable:
                return json.loads(usable[-1])
    return None


def ledger_position(events: list[dict[str, Any]]) -> Decimal | None:
    """The position the ledger ends with, or None when it cannot be told (a liquidation the
    ledger has not closed yet, or a figure that does not parse)."""
    qty = Decimal(0)
    liquidating = False
    try:
        for event in events:
            kind = event.get("event_type")
            if kind in _OPENING:
                qty, liquidating = Decimal(event["signed_qty"]), False
            elif kind in _REDUCING:
                qty, liquidating = Decimal(event["remaining_signed_qty"]), False
            elif kind == "LIQUIDATION":
                liquidating = True
    except (KeyError, InvalidOperation, TypeError):
        return None
    return None if liquidating else qty


def run_symbol(name: str, run_id: str) -> str | None:
    """The symbol the terminal attaches this directory to (`Runtime._dynamic_run_symbols` and
    `symbol_run_config`), or None when the terminal never discovers it."""
    if name == run_id:
        return DEFAULT_SYMBOL
    prefix = f"{run_id}-"
    if not name.startswith(prefix):
        return None
    symbol = name[len(prefix):]
    if symbol in SUPPORTED_SYMBOLS:
        return symbol
    if symbol.endswith("USDT") and all(ch.isalnum() for ch in symbol):
        return symbol
    return None


def read_run(run_dir: Path, run_id: str) -> RunFacts:
    facts = RunFacts(name=run_dir.name, run_dir=run_dir, symbol=run_symbol(run_dir.name, run_id))
    try:
        facts.events = _read_events(run_dir / LEDGER_FILE)
    except (OSError, ValueError):
        facts.problem = "LEDGER_UNREADABLE"
        return facts
    facts.position = ledger_position(facts.events)
    if facts.position is None:
        facts.problem = "POSITION_UNDETERMINED"

    directory = run_dir / segment_store.SEGMENT_DIR
    manifest_paths: dict[int, Path] = {}
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            name = path.name
            head = name.split(".", 1)[0]
            if name.endswith(".tmp") or not head.isdigit() or not path.is_file() \
                    or path.is_symlink():
                facts.problem = facts.problem or "UNRECOGNISED_SEGMENT_FILE"
                continue
            index = int(head)
            if name.endswith(".manifest.json"):
                manifest_paths[index] = path
            elif name.endswith(".input.jsonl") or name.endswith(".input.jsonl.gz"):
                if index in facts.segment_files:
                    facts.problem = facts.problem or "SEGMENT_DUPLICATE"
                facts.segment_files[index] = path
            else:
                facts.problem = facts.problem or "UNRECOGNISED_SEGMENT_FILE"
    for index, path in facts.segment_files.items():
        manifest_path = manifest_paths.get(index)
        if manifest_path is None:
            facts.problem = facts.problem or "SEGMENT_WITHOUT_MANIFEST"
            continue
        try:
            manifest = segment_store.SegmentManifest.load(json.loads(manifest_path.read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            facts.problem = facts.problem or "SEGMENT_MANIFEST_UNREADABLE"
            continue
        if run_dir / manifest.path != path:
            facts.problem = facts.problem or "SEGMENT_MANIFEST_PATH_MISMATCH"
            continue
        facts.segments[index] = manifest
    facts.orphan_manifests = {i: p for i, p in manifest_paths.items()
                              if i not in facts.segment_files}

    directory = run_dir / segment_store.CHECKPOINT_DIR
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            head = path.name.split(".", 1)[0]
            if not path.name.endswith(".checkpoint.json") or not head.isdigit():
                facts.problem = facts.problem or "UNRECOGNISED_CHECKPOINT_FILE"
                continue
            index = int(head)
            facts.checkpoint_files[index] = path
            try:
                payload = json.loads(path.read_text())
            except (OSError, ValueError):
                facts.torn_checkpoints.append(index)
                continue
            facts.checkpoints[index] = payload

    # When this run read or changed the shared wallet: `wallet_event_times`, read only.
    facts.wallet_times = sorted(int(e["ts_ms"]) for e in facts.events
                                if e.get("event_type") not in WALLET_INERT_EVENTS)
    tail = _last_line(run_dir / ACTIVE_TAPE)
    if tail is None and facts.segments:
        last = facts.segments[max(facts.segments)]
        for tail in segment_store.read_segment(run_dir, last):
            pass
    if tail is not None and tail.get("kind") == InputKind.COMMAND:
        payload = tail.get("payload") or {}
        if payload.get("command") not in WALLET_INERT_COMMANDS:
            facts.wallet_times.append(int(payload["ts_ms"]))
            facts.wallet_times.sort()
            last_ledger = max((int(e.get("ts_ms", 0)) for e in facts.events), default=0)
            if int(payload["ts_ms"]) > last_ledger:
                # A command on the tape the ledger has not recorded: the position is not known.
                facts.problem = facts.problem or "PENDING_COMMAND"

    # The newest checkpoint that covers the whole ledger must agree with it on the position.
    newest = max(facts.checkpoints, default=None)
    if newest is not None and facts.position is not None:
        payload = facts.checkpoints[newest]
        if int(payload.get("ledger_events", -1)) == len(facts.events):
            try:
                if Decimal(payload["position"]["signed_qty"]) != facts.position:
                    facts.problem = facts.problem or "POSITION_MISMATCH"
            except (KeyError, InvalidOperation, TypeError):
                facts.problem = facts.problem or "POSITION_MISMATCH"
    return facts


# --------------------------------------------------------------------------- the plan

@dataclass
class RunPlan:
    facts: RunFacts
    classification: str
    reason: str | None = None
    keep_from: int | None = None              # segments/checkpoints below this may leave
    fallback_verified: bool = False
    files: list[dict[str, Any]] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        candidates = [f for f in self.files if f["candidate"]]
        return {
            "run": self.facts.name, "symbol": self.facts.symbol,
            "classification": self.classification, "reason": self.reason,
            "position": str(self.facts.position) if self.facts.position is not None else None,
            "wallet_events": len(self.facts.wallet_times),
            "fills": sum(1 for e in self.facts.events if e.get("event_type") == "FILL"),
            "checkpoints": sorted(self.facts.checkpoints),
            "segments": [min(self.facts.segments), max(self.facts.segments)]
            if self.facts.segments else [],
            "keep_from": self.keep_from, "fallback_verified": self.fallback_verified,
            "candidate_files": len(candidates),
            "candidate_bytes": sum(f["bytes"] for f in candidates),
            "kept_bytes": sum(f["bytes"] for f in self.files if not f["candidate"]),
        }


def _hot_symbol(root: Path) -> tuple[str | None, str | None]:
    """`(symbol, None)`, `(None, None)` when no slot exists, `(None, reason)` when unreadable."""
    path = root / "hot_slot.json"
    if not path.exists():
        return None, None
    try:
        body = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, "HOT_SLOT_UNREADABLE"
    symbol = (body.get("metrics") or {}).get("symbol") if isinstance(body, dict) else None
    symbol = symbol or (body.get("symbol") if isinstance(body, dict) else None)
    if not isinstance(symbol, str) or not symbol:
        return None, "HOT_SLOT_UNREADABLE"
    return symbol, None


def _cut_checkpoints(facts: RunFacts, allowed: set[int]) -> list[tuple[int, dict[str, Any]]]:
    """`wallet_recovery._checkpoints` over the allowed indices: (cut time, payload), newest first."""
    found = []
    for index in sorted(allowed, reverse=True):
        payload = facts.checkpoints.get(index)
        manifest = facts.segments.get(index)
        if payload is None or manifest is None or manifest.last_ts_ms is None:
            continue
        found.append((int(manifest.last_ts_ms), payload))
    return found


def simulate_wallet_cut(members: list[RunFacts], allowed: dict[str, set[int]]
                        ) -> dict[str, int | None] | None:
    """The plan `recover_wallet` would make if only `allowed` checkpoints existed.

    `{run name: checkpoint segment index, or None for a replay from the first record}`, or None
    when no common cut exists. Same search and the same `_pick` as the recovery; the per-member
    usability is precomputed with the same checks `restore` makes.
    """
    checkpoints = {m.name: _cut_checkpoints(m, allowed[m.name]) for m in members}
    usable = {m.name: {int(p["segment_index"]): m.usable(int(p["segment_index"]))
                       for _, p in checkpoints[m.name]} for m in members}
    candidates: list[int | None] = sorted({ts for cps in checkpoints.values() for ts, _ in cps},
                                          reverse=True)
    candidates.append(None)
    for cut in candidates:
        attempt: dict[str, int | None] = {}
        for member in members:
            start = _pick(member, cut, member.wallet_times, checkpoints[member.name], [],
                          usable[member.name])
            if start is None:
                break
            attempt[member.name] = (int(start.payload["segment_index"])
                                    if start.payload is not None else None)
        else:
            return attempt
    return None


def _usable_desc(facts: RunFacts) -> list[int]:
    return [i for i in sorted(facts.checkpoints, reverse=True)
            if i in facts.segments and facts.usable(i)]


def _initial_keep_from(plan: RunPlan) -> int:
    usable = _usable_desc(plan.facts)
    if plan.classification in ACTIVE_CLASSES:
        return usable[1] if len(usable) >= 2 else 0       # newest + one fallback, else keep all
    return usable[0] if usable else 0                     # CLOSED_TRADED: the final checkpoint


def _is_paper_run(facts: RunFacts) -> bool:
    """A directory a paper session wrote: its run config names this directory as its run, and its
    ledger opens with the engine's RUN_START. What it was for is not asked; that it is a paper
    run the terminal no longer discovers is what makes it safe to treat as retired."""
    try:
        config = json.loads((facts.run_dir / RUN_CONFIG_FILE).read_text())
    except (OSError, ValueError):
        return False
    return (isinstance(config, dict) and config.get("run_id") == facts.name
            and bool(facts.events) and facts.events[0].get("event_type") == "RUN_START")


def classify(root: Path, *, run_id: str) -> list[RunPlan]:
    hot, hot_problem = _hot_symbol(root)
    plans = []
    for run_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        if run_dir.name == "reference":
            continue
        if not (run_dir / LEDGER_FILE).exists() and not (run_dir / ACTIVE_TAPE).exists():
            continue                                      # not a run directory
        facts = read_run(run_dir, run_id)
        if facts.problem:
            plans.append(RunPlan(facts, BLOCKED_RUN, facts.problem))
            continue
        if facts.symbol is None:
            if _is_paper_run(facts) and not facts.traded and facts.position == 0:
                plans.append(RunPlan(facts, RETIRED_UNDISCOVERED))
            else:
                plans.append(RunPlan(facts, BLOCKED_RUN, "UNKNOWN_RUN"))
            continue
        if facts.position != 0:
            plans.append(RunPlan(facts, ACTIVE_OPEN_POSITION))
        elif facts.symbol == DEFAULT_SYMBOL:
            plans.append(RunPlan(facts, ACTIVE_DEFAULT))
        elif hot_problem is not None and facts.symbol not in SUPPORTED_SYMBOLS:
            plans.append(RunPlan(facts, BLOCKED_RUN, hot_problem))
        elif facts.symbol == hot:
            plans.append(RunPlan(facts, ACTIVE_CURRENT_HOT))
        elif facts.traded:
            plans.append(RunPlan(facts, CLOSED_TRADED))
        else:
            plans.append(RunPlan(facts, CLOSED_NO_TRADE))
    return plans


def _settle_boundaries(plans: list[RunPlan]) -> None:
    """Choose `keep_from` per run and prove it against a simulated wallet recovery.

    Members of the wallet replay are the default run and every discovered run with a wallet
    event, exactly as `Runtime._wallet_members` and `recover_wallet` pick them. With every
    checkpoint on disk the simulation reproduces what the server does today. The kept set must
    still yield a plan, and so must the kept set minus any active member's newest checkpoint; in
    both, a member that would replay from its first record counts as a failure unless it keeps
    everything. A boundary that fails is moved back one checkpoint at a time; a run whose
    boundary reaches zero keeps everything.
    """
    for plan in plans:
        if plan.classification in ACTIVE_CLASSES + (CLOSED_TRADED,):
            plan.keep_from = _initial_keep_from(plan)
    member_plans = [p for p in plans if p.facts.symbol is not None
                    and (p.facts.symbol == DEFAULT_SYMBOL or p.facts.traded)]
    owner = [p for p in member_plans if p.facts.symbol == DEFAULT_SYMBOL]
    members = owner + sorted((p for p in member_plans if p.facts.symbol != DEFAULT_SYMBOL),
                             key=lambda p: p.facts.symbol)
    def standalone(plan: RunPlan) -> None:
        # A run that recovers alone uses its newest parseable checkpoint, then the next one.
        plan.fallback_verified = plan.classification == CLOSED_TRADED or plan.keep_from == 0 \
            or len([i for i in _usable_desc(plan.facts) if i >= (plan.keep_from or 0)]) >= 2

    is_member = {id(p) for p in members}
    for plan in plans:
        if id(plan) not in is_member and plan.keep_from is not None:
            standalone(plan)
    if len(members) <= 1:
        for plan in members:
            standalone(plan)                               # the owner alone: no shared replay
        return
    if any(p.classification == BLOCKED_RUN for p in members):
        for plan in members:                               # a replay we cannot model: keep all
            if plan.keep_from is not None:
                plan.keep_from, plan.reason = 0, "WALLET_MEMBER_BLOCKED"
        return
    facts = [p.facts for p in members]
    everything = {p.facts.name: set(p.facts.checkpoints) for p in members}
    today = simulate_wallet_cut(facts, everything)
    if today is None:
        for plan in members:
            plan.keep_from, plan.reason = 0, "WALLET_CUT_UNAVAILABLE_TODAY"
        return
    for plan in members:
        chosen = today[plan.facts.name]
        if chosen is None:
            plan.keep_from = 0                             # it replays from its first record
        elif plan.keep_from is not None and chosen < plan.keep_from:
            plan.keep_from = chosen

    def allowed() -> dict[str, set[int]]:
        return {p.facts.name: {i for i in p.facts.checkpoints if i >= (p.keep_from or 0)}
                for p in members}

    def covered(result: dict[str, int | None] | None) -> bool:
        """A plan the kept files can carry out. A start of None is a replay from the run's first
        record, which needs every segment it has: only valid for a run that keeps everything."""
        return result is not None and all(result[p.facts.name] is not None or not p.keep_from
                                          for p in members)

    for _ in range(10_000):
        kept_plan = simulate_wallet_cut(facts, allowed())
        if not covered(kept_plan):
            # Unreachable while today's starts are kept; a guard, never a reason to prune.
            for plan in members:
                plan.keep_from, plan.reason = 0, "KEPT_SET_HAS_NO_CUT"
            break
        for plan in members:
            plan.reason = plan.reason or ("SAME_CUT_AS_TODAY" if kept_plan == today
                                          else "DIFFERENT_VALID_CUT")
        moved = False
        for plan in members:
            if plan.classification not in ACTIVE_CLASSES:
                plan.fallback_verified = True
                continue
            trial = allowed()
            newest = max(trial[plan.facts.name], default=None)
            if newest is not None:
                trial[plan.facts.name] = trial[plan.facts.name] - {newest}
            if plan.keep_from == 0 or covered(simulate_wallet_cut(facts, trial)):
                plan.fallback_verified = True
                continue
            older = [i for i in _usable_desc(plan.facts) if i < (plan.keep_from or 0)]
            plan.keep_from = older[0] if older else 0
            plan.fallback_verified = False
            moved = True
        if not moved:
            break


def _file_entries(root: Path, plan: RunPlan) -> list[dict[str, Any]]:
    facts = plan.facts
    entries: list[dict[str, Any]] = []

    def add(path: Path, role: str, candidate: bool, why: str) -> None:
        stat = path.stat()
        entries.append({"path": str(path.relative_to(root)), "role": role, "bytes": stat.st_size,
                        "mtime": int(stat.st_mtime), "candidate": candidate, "why": why})

    if plan.classification in WHOLE_RUN_CLASSES:
        for path in sorted(p for p in facts.run_dir.rglob("*") if p.is_file()):
            add(path, "WHOLE_RUN", True, plan.classification)
        return entries
    keep_from = plan.keep_from if plan.classification != BLOCKED_RUN else None
    seen: set[Path] = set()
    for index, path in sorted(facts.segment_files.items()):
        manifest_path = path.parent / f"{index:06d}.manifest.json"
        old = keep_from is not None and index < keep_from
        why = "BELOW_REPLAY_BOUNDARY" if old else "REPLAY_TAIL_OR_FALLBACK" \
            if keep_from is not None else plan.reason or "BLOCKED"
        add(path, "SEGMENT", old, why)
        seen.add(path)
        if manifest_path.exists():
            add(manifest_path, "SEGMENT_MANIFEST", old, why)
            seen.add(manifest_path)
    for index, path in sorted(facts.orphan_manifests.items()):
        old = keep_from is not None and index < keep_from
        add(path, "SEGMENT_MANIFEST", old, "ORPHAN_MANIFEST" if old else "KEPT_ORPHAN_MANIFEST")
        seen.add(path)
    for index, path in sorted(facts.checkpoint_files.items()):
        old = keep_from is not None and index < keep_from
        add(path, "CHECKPOINT", old, "BELOW_REPLAY_BOUNDARY" if old else
            ("LATEST_OR_FALLBACK_CHECKPOINT" if keep_from is not None else plan.reason or "BLOCKED"))
        seen.add(path)
    for path in sorted(p for p in facts.run_dir.rglob("*") if p.is_file() and p not in seen):
        role = ("LEDGER" if path.name == LEDGER_FILE and path.parent == facts.run_dir
                else "ACTIVE_TAPE" if path.name == ACTIVE_TAPE and path.parent == facts.run_dir
                else "OTHER")
        add(path, role, False, role)
    return entries


def build_plan(root: Path, *, run_id: str) -> list[RunPlan]:
    root = Path(root)
    plans = classify(root, run_id=run_id)
    _settle_boundaries(plans)
    for plan in plans:
        plan.files = _file_entries(root, plan)
        # Ledger never leaves while it records money; enforced here rather than trusted above.
        for entry in plan.files:
            if entry["role"] in ("LEDGER", "WHOLE_RUN") and entry["path"].endswith(LEDGER_FILE) \
                    and plan.facts.traded:
                entry["candidate"], entry["why"] = False, "LEDGER_WITH_WALLET_EVENTS"
    return plans


def read_run_id(root: Path, config_path: Path | None = None) -> str:
    """The default run's id, from `CRYPTO_PAPER_RUN_CONFIG` (beside the runs in production)."""
    config = json.loads(Path(config_path or Path(root) / "run_config.json").read_text())
    return str(config["run_id"])


# --------------------------------------------------------------------------- manifests

def manifest_dir(archive_dir: Path) -> Path:
    return Path(archive_dir) / MANIFEST_DIRNAME


def receipt_dir(archive_dir: Path) -> Path:
    return Path(archive_dir) / RECEIPT_DIRNAME


def load_manifests(archive_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    found: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, str]] = []
    directory = manifest_dir(archive_dir)
    if not directory.is_dir():
        return found, invalid
    for path in sorted(directory.glob("*.json")):
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            invalid.append({"file": path.name, "reason": "UNREADABLE"})
            continue
        if not sealed_ok(body, "manifest_sha256") or body.get("version") != MANIFEST_VERSION \
                or body.get("manifest_id") != path.stem:
            invalid.append({"file": path.name, "reason": "SEAL_OR_VERSION"})
            continue
        found[body["manifest_id"]] = body
    return found, invalid


def manifest_index(manifests: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`{path: entry}`; the first manifest naming a path wins."""
    index: dict[str, dict[str, Any]] = {}
    for manifest_id in sorted(manifests):
        for entry in manifests[manifest_id]["files"]:
            index.setdefault(entry["path"], entry)
    return index


def _next_part(manifests: dict[str, dict[str, Any]], run: str) -> int:
    parts = [m.get("part", 0) for m in manifests.values() if m.get("run") == run]
    return (max(parts) + 1) if parts else 1


def build_manifest(plan: RunPlan, files: list[dict[str, Any]], *, part: int,
                   now_s: float) -> dict[str, Any]:
    entries = sorted(files, key=lambda e: e["path"])
    body = {
        "version": MANIFEST_VERSION,
        "manifest_id": f"{plan.facts.name}-{part:03d}",
        "run": plan.facts.name, "part": part, "symbol": plan.facts.symbol,
        "classification": plan.classification,
        "position": str(plan.facts.position) if plan.facts.position is not None else None,
        "wallet_events": len(plan.facts.wallet_times),
        "fills": sum(1 for e in plan.facts.events if e.get("event_type") == "FILL"),
        "checkpoints": sorted(plan.facts.checkpoints),
        "replay_boundary_segment": plan.keep_from,
        "retention_version": RETENTION_VERSION,
        "created_at": utc_iso(now_s),
        "files": [{"path": e["path"], "role": e["role"], "bytes": e["bytes"],
                   "sha256": e["sha256"]} for e in entries],
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "aggregate_digest": aggregate_digest(entries),
    }
    return seal(body, "manifest_sha256")


def write_manifests(root: Path, archive_dir: Path, plans: list[RunPlan], *,
                    now_s: float | None = None) -> list[str]:
    """One immutable manifest per run for its candidates no manifest lists yet."""
    now_s = time.time() if now_s is None else now_s
    manifests, _ = load_manifests(archive_dir)
    index = manifest_index(manifests)
    written = []
    for plan in plans:
        uncovered = [dict(f) for f in plan.files if f["candidate"] and f["path"] not in index]
        if not uncovered:
            continue
        for entry in uncovered:
            entry["sha256"] = sha256_file(Path(root) / entry["path"])
        manifest = build_manifest(plan, uncovered, part=_next_part(manifests, plan.facts.name),
                                  now_s=now_s)
        path = manifest_dir(archive_dir) / f'{manifest["manifest_id"]}.json'
        if path.exists():
            raise FileExistsError(f"{path} exists; manifests are immutable")
        write_atomic(path, (canonical(manifest) + "\n").encode())
        manifests[manifest["manifest_id"]] = manifest
        written.append(manifest["manifest_id"])
    return written


# --------------------------------------------------------------------------- receipts

def verify_file(local: Path, entry: dict[str, Any]) -> str | None:
    """None when the pulled copy is the manifest's file and reads back whole."""
    if not local.is_file():
        return "NOT_PULLED"
    if local.stat().st_size != entry["bytes"]:
        return "SIZE_MISMATCH"
    if sha256_file(local) != entry["sha256"]:
        return "SHA256_MISMATCH"
    name = local.name
    try:
        if name.endswith(".gz"):
            with gzip.open(local, "rb") as handle:
                for raw in handle:
                    if raw.strip():
                        json.loads(raw)
        elif name.endswith(".jsonl"):
            with open(local, "rb") as handle:
                for raw in handle:
                    if raw.strip():
                        json.loads(raw)
        elif name.endswith(".json"):
            json.loads(local.read_bytes())
    except (OSError, EOFError, ValueError, gzip.BadGzipFile):
        return "CONTENT_UNREADABLE"
    return None


def build_receipt(manifest: dict[str, Any], *, verified: list[dict[str, Any]],
                  failed: list[dict[str, Any]], destination: str, verified_at_s: float
                  ) -> dict[str, Any]:
    result = (RESULT_OK if not failed and len(verified) == manifest["file_count"]
              else RESULT_PARTIAL if verified else RESULT_FAILED)
    body = {
        "version": RECEIPT_VERSION,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "verified_at": utc_iso(verified_at_s),
        "destination": destination,
        "result": result,
        "file_count": len(verified),
        "byte_count": sum(e["bytes"] for e in verified),
        "verified": sorted(verified, key=lambda e: e["path"]),
        "failed": sorted(failed, key=lambda e: e["path"]),
        "aggregate_digest": aggregate_digest(verified),
    }
    return seal(body, "receipt_sha256")


def receipt_for(manifest: dict[str, Any], mirror: Path, *, destination: str) -> dict[str, Any]:
    verified, failed = [], []
    for entry in manifest["files"]:
        problem = verify_file(Path(mirror) / entry["path"], entry)
        if problem is None:
            verified.append({"path": entry["path"], "sha256": entry["sha256"],
                             "bytes": entry["bytes"]})
        else:
            failed.append({"path": entry["path"], "reason": problem})
    return build_receipt(manifest, verified=verified, failed=failed, destination=destination,
                         verified_at_s=time.time())


def check_receipt(receipt: Any, manifest: dict[str, Any] | None
                  ) -> tuple[dict[str, str], str | None]:
    """`({path: sha256 verified in COLD}, None)` or `({}, reason)`. Only a full OK counts."""
    if manifest is None:
        return {}, "RECEIPT_WITHOUT_MANIFEST"
    if not sealed_ok(receipt, "receipt_sha256") or receipt.get("version") != RECEIPT_VERSION:
        return {}, "RECEIPT_SEAL_OR_VERSION"
    if receipt.get("manifest_id") != manifest["manifest_id"] or \
            receipt.get("manifest_sha256") != manifest["manifest_sha256"]:
        return {}, "RECEIPT_MANIFEST_MISMATCH"
    if receipt.get("result") != RESULT_OK:
        return {}, "RECEIPT_NOT_OK"
    verified = receipt.get("verified")
    if not isinstance(verified, list) or len(verified) != manifest["file_count"] or \
            aggregate_digest(verified) != receipt.get("aggregate_digest") or \
            aggregate_digest(verified) != manifest["aggregate_digest"]:
        return {}, "RECEIPT_DIGEST_MISMATCH"
    listed = {e["path"]: e for e in manifest["files"]}
    out = {}
    for entry in verified:
        want = listed.get(entry.get("path"))
        if want is None or want["sha256"] != entry.get("sha256") or \
                want["bytes"] != entry.get("bytes"):
            return {}, "RECEIPT_FILE_MISMATCH"
        out[entry["path"]] = entry["sha256"]
    return out, None


def load_receipts(archive_dir: Path, manifests: dict[str, dict[str, Any]]
                  ) -> tuple[dict[str, str], dict[str, str]]:
    verified: dict[str, str] = {}
    rejected: dict[str, str] = {}
    directory = receipt_dir(archive_dir)
    if not directory.is_dir():
        return verified, rejected
    for path in sorted(directory.glob("*.json")):
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            rejected[path.name] = "RECEIPT_UNREADABLE"
            continue
        files, reason = check_receipt(body, manifests.get(path.stem))
        if reason is not None:
            rejected[path.name] = reason
            continue
        verified.update(files)
    return verified, rejected


# --------------------------------------------------------------------------- pull (operator PC)

def _remote(host: str, path: str) -> str:
    return f"{host}:{path}" if host else path


def pull(*, host: str, remote_root: str, remote_archive: str, dest: Path,
         rsync: str = "rsync", upload: bool = True) -> dict[str, Any]:
    """Fetch manifests, pull their files under `dest/run/`, verify, write and upload receipts."""
    dest = Path(dest).expanduser()
    mirror = dest / "run"
    local_manifests, local_receipts = dest / MANIFEST_DIRNAME, dest / RECEIPT_DIRNAME
    for directory in (mirror, local_manifests, local_receipts):
        directory.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"event": "PULL", "dest": str(dest), "manifests": [], "errors": []}
    got = subprocess.run([rsync, "-a", _remote(host, f"{remote_archive.rstrip('/')}/{MANIFEST_DIRNAME}/"),
                          f"{local_manifests}/"], capture_output=True, text=True, check=False)
    if got.returncode != 0:
        report["errors"].append({"step": "fetch_manifests", "rc": got.returncode,
                                 "stderr": got.stderr.strip()[-400:]})
        return report
    manifests, invalid = load_manifests(dest)
    report["invalid_manifests"] = invalid
    for manifest_id in sorted(manifests):
        manifest = manifests[manifest_id]
        receipt_path = local_receipts / f"{manifest_id}.json"
        entry: dict[str, Any] = {"manifest_id": manifest_id, "files": manifest["file_count"],
                                 "bytes": manifest["total_bytes"]}
        existing = None
        if receipt_path.exists():
            try:
                existing = json.loads(receipt_path.read_bytes())
            except (OSError, ValueError):
                existing = None
        if existing is not None and check_receipt(existing, manifest)[1] is None:
            entry["result"] = "ALREADY_VERIFIED"
        else:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".list",
                                             encoding="utf-8") as listing:
                listing.write("".join(f'{f["path"]}\n' for f in manifest["files"]))
                list_path = listing.name
            try:
                got = subprocess.run([rsync, "-a", f"--files-from={list_path}",
                                      _remote(host, f"{remote_root.rstrip('/')}/"), f"{mirror}/"],
                                     capture_output=True, text=True, check=False)
            finally:
                os.unlink(list_path)
            if got.returncode not in (0, 23, 24):
                entry["rsync_rc"], entry["rsync_stderr"] = got.returncode, got.stderr.strip()[-400:]
            receipt = receipt_for(manifest, mirror, destination=str(mirror))
            write_atomic(receipt_path, (canonical(receipt) + "\n").encode())
            entry["result"] = receipt["result"]
            entry["failed"] = receipt["failed"][:5]
        if upload and entry["result"] in (RESULT_OK, "ALREADY_VERIFIED"):
            sent = subprocess.run([rsync, "-a", str(receipt_path),
                                   _remote(host, f"{remote_archive.rstrip('/')}/{RECEIPT_DIRNAME}/")],
                                  capture_output=True, text=True, check=False)
            entry["uploaded"] = sent.returncode == 0
        report["manifests"].append(entry)
    return report


# --------------------------------------------------------------------------- prune (server)

def service_running(unit: str = SERVICE_UNIT) -> bool | None:
    """True/False from systemd, None when it cannot be asked (which blocks whole-run removal)."""
    if shutil.which("systemctl") is None:
        return None
    got = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True,
                         check=False)
    state = got.stdout.strip()
    if state in ("active", "activating", "reloading", "deactivating"):
        return True
    if state in ("inactive", "failed"):
        return False
    return None


def decide(root: Path, archive_dir: Path, plans: list[RunPlan], *,
           paper_running: bool | None) -> dict[str, Any]:
    """Every candidate's decision: PRUNE_ELIGIBLE, ARCHIVE_REQUIRED or BLOCKED (with reason)."""
    manifests, invalid = load_manifests(archive_dir)
    index = manifest_index(manifests)
    receipts, rejected = load_receipts(archive_dir, manifests)
    for plan in plans:
        whole = plan.classification in WHOLE_RUN_CLASSES
        for entry in plan.files:
            if not entry["candidate"]:
                entry["decision"] = KEEP
                continue
            listed = index.get(entry["path"])
            path = Path(root) / entry["path"]
            if listed is None or entry["path"] not in receipts:
                entry["decision"], entry["block"] = ARCHIVE_REQUIRED, \
                    "NO_MANIFEST" if listed is None else "NO_RECEIPT"
            elif whole and paper_running is not False:
                # Before hashing: a run that cannot go now is not worth reading off the disk.
                entry["decision"], entry["block"] = BLOCKED, \
                    "SERVICE_RUNNING" if paper_running else "SERVICE_STATE_UNKNOWN"
            elif receipts[entry["path"]] != listed["sha256"] or listed["bytes"] != entry["bytes"] \
                    or sha256_file(path) != listed["sha256"]:
                entry["decision"], entry["block"] = BLOCKED, "SHA_MISMATCH"
            else:
                entry["decision"] = PRUNE_ELIGIBLE
        if whole and any(e["decision"] != PRUNE_ELIGIBLE for e in plan.files):
            # All or nothing: half a run is a run that recovers into something else.
            for entry in plan.files:
                if entry["decision"] == PRUNE_ELIGIBLE:
                    entry["decision"], entry["block"] = BLOCKED, "RUN_INCOMPLETE"
    return {"manifests": len(manifests), "invalid_manifests": invalid,
            "rejected_receipts": rejected}


def prune(root: Path, archive_dir: Path, *, apply: bool = False,
          paper_running: bool | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Re-plan, decide, and (with `apply`) delete exactly the PRUNE_ELIGIBLE files."""
    root, archive_dir = Path(root), Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    with open(archive_dir / LOCK_FILENAME, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plans = build_plan(root, run_id=run_id or read_run_id(root))
        gates = decide(root, archive_dir, plans, paper_running=paper_running)
        deleted = {"files": 0, "bytes": 0}
        failed: list[dict[str, str]] = []
        removed_dirs: list[str] = []
        if apply:
            for plan in plans:
                eligible = [e for e in plan.files if e["decision"] == PRUNE_ELIGIBLE]
                # Segment data before its manifest: a crash between them leaves a manifest with
                # no segment, which recovery ignores, never a segment it would re-describe.
                eligible.sort(key=lambda e: (e["role"] == "SEGMENT_MANIFEST", e["path"]))
                for entry in eligible:
                    try:
                        os.unlink(root / entry["path"])
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        failed.append({"path": entry["path"], "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    entry["deleted"] = True
                    deleted["files"] += 1
                    deleted["bytes"] += entry["bytes"]
                if plan.classification in WHOLE_RUN_CLASSES and eligible and \
                        all(e.get("deleted") for e in plan.files):
                    directories = sorted((p for p in plan.facts.run_dir.rglob("*") if p.is_dir()),
                                         key=lambda p: len(p.parts), reverse=True)
                    try:
                        for directory in [*directories, plan.facts.run_dir]:
                            directory.rmdir()          # only ever an empty directory
                        removed_dirs.append(plan.facts.name)
                    except OSError as exc:
                        failed.append({"path": plan.facts.name,
                                       "error": f"{type(exc).__name__}: {exc}"})
    return {"event": "PAPER_RETENTION_PRUNE", "version": RETENTION_VERSION, "apply": apply,
            "paper_running": paper_running, **gates, "runs": [summarise(p) for p in plans],
            "totals": totals(plans), "deleted": deleted, "removed_dirs": removed_dirs,
            "failed": failed}


def summarise(plan: RunPlan) -> dict[str, Any]:
    view = plan.view()
    decisions: dict[str, dict[str, int]] = {}
    for entry in plan.files:
        key = entry.get("decision", KEEP if not entry["candidate"] else ARCHIVE_REQUIRED)
        if entry.get("block"):
            key = f'{key}:{entry["block"]}'
        bucket = decisions.setdefault(key, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += entry["bytes"]
    view["decisions"] = decisions
    return view


def totals(plans: list[RunPlan]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for plan in plans:
        for entry in plan.files:
            key = entry.get("decision", KEEP if not entry["candidate"] else ARCHIVE_REQUIRED)
            bucket = out.setdefault(key, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += entry["bytes"]
    return out


# --------------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.paper.retention",
                                     description="PAPER run retention V1 (manual, receipt-gated).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="server: classify runs; optionally write manifests")
    p.add_argument("--root", required=True, help="CRYPTO_PAPER_ROOT (the run root)")
    p.add_argument("--archive-dir", required=True)
    p.add_argument("--run-config", help="default: <root>/run_config.json")
    p.add_argument("--write-manifests", action="store_true")
    p.add_argument("--files", action="store_true", help="include every file decision")
    q = sub.add_parser("pull", help="operator PC: pull, verify, write and upload receipts")
    q.add_argument("--host", required=True)
    q.add_argument("--remote-root", required=True)
    q.add_argument("--remote-archive", required=True)
    q.add_argument("--dest", required=True)
    q.add_argument("--rsync", default="rsync")
    q.add_argument("--no-upload", action="store_true")
    r = sub.add_parser("prune", help="server: re-plan and delete receipt-verified candidates")
    r.add_argument("--root", required=True)
    r.add_argument("--archive-dir", required=True)
    r.add_argument("--run-config", help="default: <root>/run_config.json")
    r.add_argument("--apply", action="store_true", help="delete; without it this is a dry run")
    args = parser.parse_args(argv)

    if args.command == "pull":
        report = pull(host=args.host, remote_root=args.remote_root,
                      remote_archive=args.remote_archive, dest=Path(args.dest),
                      rsync=args.rsync, upload=not args.no_upload)
        print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
        bad = report["errors"] or any(m["result"] not in (RESULT_OK, "ALREADY_VERIFIED")
                                      or m.get("uploaded") is False for m in report["manifests"])
        return 1 if bad else 0
    root = Path(args.root)
    run_id = read_run_id(root, Path(args.run_config) if args.run_config else None)
    if args.command == "plan":
        plans = build_plan(root, run_id=run_id)
        written = write_manifests(root, Path(args.archive_dir), plans) \
            if args.write_manifests else []
        gates = decide(root, Path(args.archive_dir), plans, paper_running=service_running())
        out = {"event": "PAPER_RETENTION_PLAN", "version": RETENTION_VERSION,
               "manifests_written": written, **gates, "totals": totals(plans),
               "runs": [summarise(p) for p in plans]}
        if args.files:
            out["files"] = [e for p in plans for e in p.files]
        print(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False))
        return 0
    report = prune(root, Path(args.archive_dir), apply=args.apply,
                   paper_running=service_running(), run_id=run_id)
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
