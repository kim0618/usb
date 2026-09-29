"""Segmented input tape and checkpoints.

The tape is the run's memory and it only ever grows: one market record a second, about 34MB a
day. Replaying all of it on every restart worked while runs were hours old and stops working
when they are months old, so the tape is cut into immutable segments and a checkpoint is taken
at each cut. A restart then loads the newest checkpoint and replays only what came after it.

The safety property that must not be weakened: **the ledger stays the authority**. A checkpoint
does not replace the ledger, it attests to a prefix of it. Restoring verifies that the ledger on
disk still starts with exactly the bytes the checkpoint was taken over, by hash. If it does not,
the checkpoint is refused and the caller can fall back to replaying everything. A checkpoint can
therefore make a restart fast, but it can never make a wrong state look right.

Crash safety comes from doing the irreversible step last and making every write atomic:

    fsync active tape
      -> rename active to segments/NNNNNN.input.jsonl   (atomic within the directory)
      -> write the manifest                             (tmp + fsync + rename)
      -> write the checkpoint                           (tmp + fsync + rename)
      -> open a fresh active tape

A crash between any two steps leaves a state the loader recognises: a segment with no manifest
is re-hashed and its manifest rewritten, and a missing checkpoint simply means the previous one
is used and more tail gets replayed. Nothing is lost because nothing is ever deleted or
rewritten in place.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .account import Account, Position
from .config import PaperRunConfig
from .engine import PaperEngine, apply_tape_record
from .instrument import RiskTierTable
from .ledger import InputTape, Ledger
from .state import TerminalState

SEGMENT_DIR = "segments"
CHECKPOINT_DIR = "checkpoints"
ACTIVE_TAPE = "input.jsonl"
LEDGER_FILE = "ledger.jsonl"
CHECKPOINT_VERSION = "crypto.paper.checkpoint.v1"

#: Records per segment. At 1 Hz this is roughly six hours, which keeps a tail replay short
#: without producing so many files that listing the directory becomes the slow part.
DEFAULT_SEGMENT_RECORDS = 20_000


class CheckpointRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_prefix(path: Path, length: int) -> str:
    """Hash the first `length` bytes, which is what a checkpoint attests to."""
    digest = hashlib.sha256()
    remaining = length
    with path.open("rb") as handle:
        while remaining > 0:
            block = handle.read(min(1 << 20, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    if remaining > 0:
        raise CheckpointRefused("LEDGER_TOO_SHORT",
                                f"ledger has fewer than the {length} bytes the checkpoint covers")
    return digest.hexdigest()


def write_atomic(path: Path, payload: str) -> None:
    """Write through a temporary file so a reader never sees half a document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


# ------------------------------------------------------------------ manifests


@dataclass(frozen=True)
class SegmentManifest:
    index: int
    path: str
    records: int
    bytes: int
    sha256: str
    first_ts_ms: int | None
    last_ts_ms: int | None
    compressed: bool = False
    uncompressed_sha256: str | None = None

    def view(self) -> dict[str, Any]:
        return {"index": self.index, "path": self.path, "records": self.records,
                "bytes": self.bytes, "sha256": self.sha256, "first_ts_ms": self.first_ts_ms,
                "last_ts_ms": self.last_ts_ms, "compressed": self.compressed,
                "uncompressed_sha256": self.uncompressed_sha256}

    @classmethod
    def load(cls, payload: dict[str, Any]) -> "SegmentManifest":
        return cls(index=int(payload["index"]), path=payload["path"],
                   records=int(payload["records"]), bytes=int(payload["bytes"]),
                   sha256=payload["sha256"], first_ts_ms=payload.get("first_ts_ms"),
                   last_ts_ms=payload.get("last_ts_ms"),
                   compressed=bool(payload.get("compressed", False)),
                   uncompressed_sha256=payload.get("uncompressed_sha256"))


def read_segment(run_dir: Path, manifest: SegmentManifest) -> Iterator[dict[str, Any]]:
    """Stream one closed segment, transparently through gzip when it was compressed."""
    path = run_dir / manifest.path
    opener = gzip.open if manifest.compressed else open
    with opener(path, "rt") as handle:      # type: ignore[operator]
        for line in handle:
            if line.strip():
                yield json.loads(line)


def scan_segment(path: Path) -> tuple[int, int | None, int | None]:
    """Count records and find the first and last timestamp, without holding the file."""
    records = 0
    first: int | None = None
    last: int | None = None
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            records += 1
            ts = (json.loads(line).get("payload") or {}).get("ts_ms")
            if ts is not None:
                ts = int(ts)
                first = ts if first is None else first
                last = ts
    return records, first, last


def load_manifests(run_dir: Path) -> list[SegmentManifest]:
    """Every closed segment in order, repairing a manifest a crash left unwritten."""
    directory = run_dir / SEGMENT_DIR
    if not directory.exists():
        return []
    manifests: list[SegmentManifest] = []
    for segment in sorted(directory.glob("*.input.jsonl*")):
        if segment.name.endswith(".tmp"):
            continue
        index = int(segment.name.split(".", 1)[0])
        manifest_path = directory / f"{index:06d}.manifest.json"
        if manifest_path.exists():
            manifests.append(SegmentManifest.load(json.loads(manifest_path.read_text())))
            continue
        # The rename landed but the manifest did not. The segment is immutable, so it can be
        # described now; refusing here would strand a perfectly good file.
        compressed = segment.suffix == ".gz"
        records, first, last = (0, None, None) if compressed else scan_segment(segment)
        manifest = SegmentManifest(
            index=index, path=f"{SEGMENT_DIR}/{segment.name}", records=records,
            bytes=segment.stat().st_size, sha256=sha256_file(segment),
            first_ts_ms=first, last_ts_ms=last, compressed=compressed)
        write_atomic(manifest_path, json.dumps(manifest.view(), indent=2, sort_keys=True) + "\n")
        manifests.append(manifest)
    return sorted(manifests, key=lambda item: item.index)


def verify_segments(run_dir: Path, manifests: list[SegmentManifest]) -> list[dict[str, str]]:
    """Re-hash every closed segment. Empty means the archive is intact."""
    broken: list[dict[str, str]] = []
    for manifest in manifests:
        path = run_dir / manifest.path
        if not path.exists():
            broken.append({"index": str(manifest.index), "problem": "MISSING"})
            continue
        actual = sha256_file(path)
        if actual != manifest.sha256:
            broken.append({"index": str(manifest.index), "problem": "CHECKSUM_MISMATCH",
                           "expected": manifest.sha256, "actual": actual})
    return broken


# ------------------------------------------------------------------ checkpoints


def capture(engine: PaperEngine, *, segment_index: int, tape_records: int,
            ledger_path: Path) -> dict[str, Any]:
    """Everything a restart needs that the remaining tape will not re-establish."""
    account = engine.account
    position = account.position
    ledger_bytes = ledger_path.stat().st_size if ledger_path.exists() else 0
    return {
        "version": CHECKPOINT_VERSION,
        "run_id": engine.config.run_id,
        "segment_index": segment_index,
        "tape_records": tape_records,
        "ledger_bytes": ledger_bytes,
        "ledger_events": len(engine.ledger.events),
        "ledger_sha256": sha256_prefix(ledger_path, ledger_bytes) if ledger_bytes else "",
        "engine": {
            "leverage": str(engine.leverage),
            "mode": engine.state.mode,
            "last_market_ts_ms": engine.last_market_ts_ms,
            "liquidation_count": engine.liquidation_count,
            "funding_grid_mismatches": engine.funding_grid_mismatches,
            "started": engine._started,
        },
        "account": {
            "starting_capital_usdt": str(account.starting_capital_usdt),
            "realized_pnl": str(account.realized_pnl),
            "cumulative_fees": str(account.cumulative_fees),
            "cumulative_funding_paid": str(account.cumulative_funding_paid),
            "capital_base_usdt": str(account.capital_base_usdt),
            "realized_at_anchor": str(account.realized_at_anchor),
            "charges_at_anchor": str(account.charges_at_anchor),
            "reset_count": account.reset_count,
            "last_reset_ts_ms": account.last_reset_ts_ms,
        },
        "position": {
            "signed_qty": str(position.signed_qty),
            "avg_entry": str(position.avg_entry),
            "leverage": str(position.leverage),
            "opened_ts_ms": position.opened_ts_ms,
            "max_adverse_excursion": str(position.max_adverse_excursion),
            "max_favourable_excursion": str(position.max_favourable_excursion),
            "mae_price": str(position.mae_price) if position.mae_price is not None else None,
            "mfe_price": str(position.mfe_price) if position.mfe_price is not None else None,
        },
    }


def restore(payload: dict[str, Any], config: PaperRunConfig, tiers: RiskTierTable, *,
            ledger_path: Path) -> PaperEngine:
    """Rebuild an engine from a checkpoint, refusing it unless the ledger still agrees."""
    if payload.get("version") != CHECKPOINT_VERSION:
        raise CheckpointRefused("CHECKPOINT_VERSION_MISMATCH",
                                f"checkpoint version {payload.get('version')} is not {CHECKPOINT_VERSION}")
    if payload.get("run_id") != config.run_id:
        raise CheckpointRefused("CHECKPOINT_RUN_MISMATCH",
                                f"checkpoint belongs to run {payload.get('run_id')}")
    expected_bytes = int(payload["ledger_bytes"])
    if expected_bytes:
        actual = sha256_prefix(ledger_path, expected_bytes)
        if actual != payload["ledger_sha256"]:
            # The checkpoint attests to a ledger prefix. If the prefix moved, the checkpoint
            # describes a history this file no longer has, and using it would invent a state.
            raise CheckpointRefused(
                "CHECKPOINT_LEDGER_MISMATCH",
                "the ledger no longer starts with the bytes this checkpoint was taken over")

    account_payload = payload["account"]
    position_payload = payload["position"]
    account = Account(
        starting_capital_usdt=Decimal(account_payload["starting_capital_usdt"]),
        realized_pnl=Decimal(account_payload["realized_pnl"]),
        cumulative_fees=Decimal(account_payload["cumulative_fees"]),
        cumulative_funding_paid=Decimal(account_payload["cumulative_funding_paid"]),
        capital_base_usdt=Decimal(account_payload["capital_base_usdt"]),
        realized_at_anchor=Decimal(account_payload["realized_at_anchor"]),
        charges_at_anchor=Decimal(account_payload["charges_at_anchor"]),
        reset_count=int(account_payload["reset_count"]),
        last_reset_ts_ms=account_payload["last_reset_ts_ms"],
        position=Position(
            signed_qty=Decimal(position_payload["signed_qty"]),
            avg_entry=Decimal(position_payload["avg_entry"]),
            leverage=Decimal(position_payload["leverage"]),
            opened_ts_ms=position_payload["opened_ts_ms"],
            max_adverse_excursion=Decimal(position_payload["max_adverse_excursion"]),
            max_favourable_excursion=Decimal(position_payload["max_favourable_excursion"]),
            mae_price=(Decimal(position_payload["mae_price"])
                       if position_payload["mae_price"] is not None else None),
            mfe_price=(Decimal(position_payload["mfe_price"])
                       if position_payload["mfe_price"] is not None else None),
        ),
    )

    engine_payload = payload["engine"]
    # The ledger starts empty and is *not* replayed: the events before the checkpoint are
    # already on disk and must not be written twice. The offset is what the caller appends from.
    engine = PaperEngine(config, tiers, ledger=Ledger())
    engine.account = account
    engine.state = TerminalState(mode=engine_payload["mode"])
    engine.leverage = Decimal(engine_payload["leverage"])
    engine.last_market_ts_ms = engine_payload["last_market_ts_ms"]
    engine.liquidation_count = int(engine_payload["liquidation_count"])
    engine.funding_grid_mismatches = int(engine_payload["funding_grid_mismatches"])
    engine._started = bool(engine_payload["started"])
    return engine


def load_latest_checkpoint(run_dir: Path) -> dict[str, Any] | None:
    directory = run_dir / CHECKPOINT_DIR
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.checkpoint.json"), reverse=True):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            continue     # a torn checkpoint is skipped; an older one is still valid
    return None


# ------------------------------------------------------------------ rotation


@dataclass
class SegmentedTape:
    """The active tape plus the archive behind it."""
    run_dir: Path
    segment_records: int = DEFAULT_SEGMENT_RECORDS
    compress: bool = False

    @property
    def active_path(self) -> Path:
        return self.run_dir / ACTIVE_TAPE

    def active_records(self) -> int:
        if not self.active_path.exists():
            return 0
        return sum(1 for line in self.active_path.open() if line.strip())

    def next_index(self) -> int:
        manifests = load_manifests(self.run_dir)
        return (manifests[-1].index + 1) if manifests else 1

    def should_rotate(self) -> bool:
        return self.active_records() >= self.segment_records

    def rotate(self, engine: PaperEngine, *, tape_records: int) -> SegmentManifest | None:
        """Close the active tape and take a checkpoint over it.

        The order is deliberate: the rename happens before the manifest and the checkpoint, so
        a crash can only ever leave *more* work for the next start, never less history.
        """
        if not self.active_path.exists() or self.active_records() == 0:
            return None
        index = self.next_index()
        directory = self.run_dir / SEGMENT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{index:06d}.input.jsonl"

        records, first_ts, last_ts = scan_segment(self.active_path)
        os.replace(self.active_path, target)
        plain_sha = sha256_file(target)

        stored, compressed = target, False
        if self.compress:
            stored = directory / f"{index:06d}.input.jsonl.gz"
            with target.open("rb") as source, gzip.open(stored, "wb") as sink:
                while True:
                    block = source.read(1 << 20)
                    if not block:
                        break
                    sink.write(block)
            # Prove the compressed copy reads back identically before removing the plain file.
            digest = hashlib.sha256()
            with gzip.open(stored, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != plain_sha:
                stored.unlink(missing_ok=True)
                raise CheckpointRefused("COMPRESSION_NOT_REPRODUCIBLE",
                                        f"segment {index} did not survive a compress/decompress round trip")
            target.unlink()
            compressed = True

        manifest = SegmentManifest(
            index=index, path=f"{SEGMENT_DIR}/{stored.name}", records=records,
            bytes=stored.stat().st_size, sha256=sha256_file(stored), first_ts_ms=first_ts,
            last_ts_ms=last_ts, compressed=compressed,
            uncompressed_sha256=plain_sha if compressed else None)
        write_atomic(directory / f"{index:06d}.manifest.json",
                     json.dumps(manifest.view(), indent=2, sort_keys=True) + "\n")

        checkpoint = capture(engine, segment_index=index, tape_records=tape_records,
                             ledger_path=self.run_dir / LEDGER_FILE)
        write_atomic(self.run_dir / CHECKPOINT_DIR / f"{index:06d}.checkpoint.json",
                     json.dumps(checkpoint, indent=2, sort_keys=True) + "\n")
        self.active_path.touch()
        return manifest

    def replay_from(self, engine: PaperEngine, *, after_segment: int) -> int:
        """Apply every record after `after_segment`, closed segments first then the active tape."""
        applied = 0
        for manifest in load_manifests(self.run_dir):
            if manifest.index <= after_segment:
                continue
            for record in read_segment(self.run_dir, manifest):
                apply_tape_record(engine, record)
                applied += 1
        if self.active_path.exists():
            for record in InputTape.stream(self.active_path):
                apply_tape_record(engine, record)
                applied += 1
        return applied

    def total_records(self) -> int:
        return sum(m.records for m in load_manifests(self.run_dir)) + self.active_records()

    def storage(self) -> dict[str, Any]:
        manifests = load_manifests(self.run_dir)
        segment_bytes = sum(m.bytes for m in manifests)
        active_bytes = self.active_path.stat().st_size if self.active_path.exists() else 0
        ledger = self.run_dir / LEDGER_FILE
        return {
            "segments": len(manifests),
            "segment_bytes": segment_bytes,
            "active_bytes": active_bytes,
            "ledger_bytes": ledger.stat().st_size if ledger.exists() else 0,
            "total_bytes": segment_bytes + active_bytes,
            "records": self.total_records(),
            "compressed_segments": sum(1 for m in manifests if m.compressed),
        }
