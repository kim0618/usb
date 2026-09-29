"""Restart recovery.

There is no separate state snapshot, on purpose. The input tape already determines the ledger
byte for byte (D3 proved it), so recovery is a replay of that tape. A second stored copy of
account state would be a second source of truth, and the first time the two disagreed the
operator would have no way to know which one was lying.

What recovery must handle:
  torn write   the process died mid-line; only the last line of a file can be incomplete
  lag          the tape record is written and fsynced before the engine runs, so the ledger
               can be behind the tape but never ahead of it
  divergence   anything else means the files do not belong together, and that is refused
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import LEDGER_SCHEMA_VERSION
from .config import PaperRunConfig
from .engine import PaperEngine, apply_tape_record
from .instrument import RiskTierTable
from .ledger import InputTape, Ledger

INPUT_FILE = "input.jsonl"
LEDGER_FILE = "ledger.jsonl"


class RecoveryRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TruncationReport:
    path: str
    complete_bytes: int
    dropped_bytes: int

    @property
    def was_torn(self) -> bool:
        return self.dropped_bytes > 0


@dataclass(frozen=True)
class RecoveryResult:
    restored: bool
    tape_records: int
    ledger_events: int
    ledger_bytes: int
    ledger_tail_rewritten: int
    truncations: tuple[TruncationReport, ...]
    schema_version: str | None
    position_signed_qty: str
    mode: str
    #: FULL_REPLAY re-derived every event from the tape; CHECKPOINT trusted an attested ledger
    #: prefix and replayed only what came after it.
    source: str = "FULL_REPLAY"
    checkpoint_segment: int | None = None

    def view(self) -> dict[str, Any]:
        return {
            "restored": self.restored,
            "tape_records": self.tape_records,
            "ledger_events": self.ledger_events,
            "ledger_offset_bytes": self.ledger_bytes,
            "ledger_tail_rewritten_bytes": self.ledger_tail_rewritten,
            "torn_writes_repaired": [
                {"path": item.path, "dropped_bytes": item.dropped_bytes}
                for item in self.truncations if item.was_torn
            ],
            "ledger_schema_version": self.schema_version,
            "position_signed_qty": self.position_signed_qty,
            "mode": self.mode,
            "source": self.source,
            "checkpoint_segment": self.checkpoint_segment,
        }


def truncate_to_last_complete_line(path: Path) -> TruncationReport:
    """Drop an unterminated trailing line. Only the last line can ever be torn, because every
    record is written and fsynced as one append."""
    if not path.exists():
        return TruncationReport(path.as_posix(), 0, 0)
    size = path.stat().st_size
    complete = 0
    with path.open("rb") as stream:
        for raw in stream:
            if raw.endswith(b"\n"):
                complete += len(raw)
            else:
                break
    if complete != size:
        with path.open("r+b") as stream:
            stream.truncate(complete)
    return TruncationReport(path.as_posix(), complete, size - complete)


def _read_schema_version(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("event_type") == "RUN_START":
            return event.get("ledger_schema_version")
    return None


def _events_from_prefix(path: Path, length: int) -> list[dict[str, Any]]:
    """Parse the first `length` bytes of the ledger into events.

    Loaded, not replayed. The ledger is small (an event per decision, not per tick) and the
    analytics fold all of it, so the whole history stays in memory even when a checkpoint made
    it unnecessary to replay the tape that produced it.
    """
    with path.open("rb") as handle:
        raw = handle.read(length)
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


def recover_from_checkpoint(run_dir: Path, config: PaperRunConfig,
                            tiers: RiskTierTable) -> tuple[PaperEngine, RecoveryResult] | None:
    """Restore from the newest usable checkpoint, or return None to fall back to a full replay.

    Every reason to refuse is a reason to replay everything instead, which is always correct
    and merely slower. A checkpoint is an optimisation and is never allowed to be the thing
    that decides what the state is.
    """
    from . import segments as segment_store

    payload = segment_store.load_latest_checkpoint(run_dir)
    if payload is None:
        return None
    ledger_path = run_dir / LEDGER_FILE
    try:
        engine = segment_store.restore(payload, config, tiers, ledger_path=ledger_path)
    except segment_store.CheckpointRefused:
        return None

    offset = int(payload["ledger_bytes"])
    engine.ledger = Ledger(events=_events_from_prefix(ledger_path, offset) if offset else [])
    if len(engine.ledger.events) != int(payload["ledger_events"]):
        return None      # the prefix does not hold the number of events it claimed

    tape = segment_store.SegmentedTape(run_dir=run_dir)
    tape_records = int(payload["tape_records"])
    tape_records += tape.replay_from(engine, after_segment=int(payload["segment_index"]))

    rebuilt = engine.ledger.bytes()
    on_disk = ledger_path.read_bytes() if ledger_path.exists() else b""
    if not rebuilt.startswith(on_disk):
        return None      # the tail disagrees; replaying everything will say so properly
    tail = rebuilt[len(on_disk):]
    if tail:
        with ledger_path.open("ab") as stream:
            stream.write(tail)
            stream.flush()
            os.fsync(stream.fileno())
    engine.ledger.path = ledger_path

    return engine, RecoveryResult(
        restored=True, tape_records=tape_records, ledger_events=len(engine.ledger.events),
        ledger_bytes=len(rebuilt), ledger_tail_rewritten=len(tail),
        truncations=(), schema_version=LEDGER_SCHEMA_VERSION,
        position_signed_qty=str(engine.account.position.signed_qty), mode=engine.state.mode,
        source="CHECKPOINT", checkpoint_segment=int(payload["segment_index"]))


def recover(run_dir: Path, config: PaperRunConfig, tiers: RiskTierTable, *,
            use_checkpoint: bool = True) -> tuple[PaperEngine, RecoveryResult]:
    """Rebuild the engine from what is on disk, then hand back a ledger that keeps appending.

    Restarting twice in a row produces the same state as restarting once: the replay is a pure
    function of the tape, and nothing is appended to the ledger unless it was genuinely missing.
    """
    tape_path = run_dir / INPUT_FILE
    ledger_path = run_dir / LEDGER_FILE
    truncations = (truncate_to_last_complete_line(tape_path),
                   truncate_to_last_complete_line(ledger_path))

    if use_checkpoint:
        # Torn lines are repaired first: a checkpoint must be judged against a consistent file.
        shortcut = recover_from_checkpoint(run_dir, config, tiers)
        if shortcut is not None:
            engine, result = shortcut
            return engine, replace(result, truncations=truncations)

    from . import segments as _seg
    if (not tape_path.exists() or tape_path.stat().st_size == 0) and not _seg.load_manifests(run_dir):
        engine = PaperEngine(config, tiers, ledger=Ledger(path=ledger_path))
        return engine, RecoveryResult(
            restored=False, tape_records=0, ledger_events=0, ledger_bytes=0,
            ledger_tail_rewritten=0, truncations=truncations, schema_version=None,
            position_signed_qty="0", mode=engine.state.mode)

    # Replay into memory first: nothing is written until the on-disk ledger has been checked.
    # The tape is streamed rather than loaded: a run that has been up for a day is tens of
    # megabytes of JSON, and holding it whole was enough to push the service past its memory
    # limit before it finished starting.
    from . import segments as segment_store
    engine = PaperEngine(config, tiers, ledger=Ledger())
    tape_records = 0
    for manifest in segment_store.load_manifests(run_dir):
        for record in segment_store.read_segment(run_dir, manifest):
            apply_tape_record(engine, record)
            tape_records += 1
    for record in InputTape.stream(tape_path):
        apply_tape_record(engine, record)
        tape_records += 1

    stored_version = _read_schema_version(
        [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
        if ledger_path.exists() else [])
    if stored_version is not None and stored_version != LEDGER_SCHEMA_VERSION:
        raise RecoveryRefused(
            "LEDGER_SCHEMA_MISMATCH",
            f"run was written with ledger schema {stored_version}, this build writes "
            f"{LEDGER_SCHEMA_VERSION}; replaying across a schema change would rewrite history")

    rebuilt = engine.ledger.bytes()
    on_disk = ledger_path.read_bytes() if ledger_path.exists() else b""
    if not rebuilt.startswith(on_disk):
        raise RecoveryRefused(
            "LEDGER_DIVERGENCE",
            f"the stored ledger ({len(on_disk)} bytes) is not a prefix of the ledger the tape "
            f"rebuilds ({len(rebuilt)} bytes); the two files do not belong to the same run")

    tail = rebuilt[len(on_disk):]
    if tail:
        # The crash landed between a tape append and its ledger append. The tape is authority.
        with ledger_path.open("ab") as stream:
            stream.write(tail)
            stream.flush()
            import os
            os.fsync(stream.fileno())

    engine.ledger.path = ledger_path
    return engine, RecoveryResult(
        restored=True, tape_records=tape_records, ledger_events=len(engine.ledger.events),
        ledger_bytes=len(rebuilt), ledger_tail_rewritten=len(tail), truncations=truncations,
        schema_version=stored_version or LEDGER_SCHEMA_VERSION,
        position_signed_qty=str(engine.account.position.signed_qty), mode=engine.state.mode)
