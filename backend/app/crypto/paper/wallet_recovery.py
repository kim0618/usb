"""Restart recovery for several runs that share one wallet.

Why a run cannot be replayed alone once it has shared a wallet: its ledger records wallet
figures (`available_balance`, `equity` on every fill) and its margin checks read them, and
those figures include every other member's realised PnL, charges and posted margin. Replaying
one run without the others rebuilds different bytes and `recover()` refuses it
(LEDGER_DIVERGENCE). Example, from the local terminal on 2026-10-11:

    BTC  POSITION_OPEN  0.002 @ 82,9xx   available 727.37   (BTC alone in the wallet)
    RLC  POSITION_OPEN  20.4  @ 0.98     available 719.03   (BTC margin already posted)
    restart: RLC replayed alone rebuilds available 727.3x on that line -> refused

What is shared and what is not is decided by `SharedCash`, not here: the wallet (capital base,
every member's realised PnL and charges) and the posted margin are shared; position, fills,
own realised PnL, fees and funding are per run. A member reads or changes the shared part only
at a ledger event other than the ones in `WALLET_INERT_EVENTS`; market ticks never do.

The fix is to replay the members together, in one order, with the shared cash attached from
the start. Two constraints shape it:

* No new ordering rule. Records are merged on their own `ts_ms` (the quote time a market
  record or a command carries), ties broken by member rank (owner first, then symbol) and
  then by each run's own sequence, which is never reordered. If that order disagrees with what
  really happened, the byte comparison below refuses, exactly as `recover()` does today.
* A full replay is not always possible: the deployed BTC run cannot be replayed from its first
  record (its ledger began on an older schema). So the members start from checkpoints taken at
  a common cut X: each member uses a checkpoint at or before X with no wallet event between
  the checkpoint and X, so at X every member's share of the wallet is exactly its
  checkpoint's, and everything after is replayed in the merged order. A member with no usable
  checkpoint replays from its first record, which is allowed only while it had no wallet event
  before X.

Runs that never touched the wallet are not members of this replay: their share is zero and
they keep the single-run `recover()` path. When no run besides the owner ever touched the
wallet, this module returns None and every run recovers exactly as before, byte for byte.
"""
from __future__ import annotations

import heapq
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import LEDGER_SCHEMA_VERSION
from . import segments as segment_store
from .account import SharedCash
from .config import PaperRunConfig
from .engine import PaperEngine, apply_tape_record
from .instrument import RiskTierTable
from .ledger import EventType, InputKind, InputTape, Ledger
from .persistence import (INPUT_FILE, LEDGER_FILE, RecoveryRefused, RecoveryResult,
                          truncate_to_last_complete_line)

#: Ledger events that neither read nor change the shared wallet.
WALLET_INERT_EVENTS = frozenset({EventType.RUN_START, EventType.MODE_CHANGE,
                                 EventType.LEVERAGE_CHANGE})
#: Commands that cannot reach the wallet. Used only for a tape record the ledger has not caught
#: up with (a crash between the tape append and the ledger append).
WALLET_INERT_COMMANDS = frozenset({"START", "SET_LEVERAGE", "SET_MODE"})
#: Members' quote clocks are different exchanges' clocks. A wallet event this close to the cut
#: is treated as if it were on the wrong side of it.
CUT_MARGIN_MS = 5_000

SOURCE_WALLET_REPLAY = "WALLET_REPLAY"


@dataclass(frozen=True)
class WalletMember:
    symbol: str
    run_dir: Path
    config: PaperRunConfig
    tiers: RiskTierTable


@dataclass
class _Start:
    """Where one member's replay begins: a checkpoint, or its first record (`payload` None)."""
    payload: dict[str, Any] | None
    cut_ts_ms: int | None


@dataclass(frozen=True)
class WalletRecovery:
    cash: SharedCash
    engines: dict[str, tuple[PaperEngine, RecoveryResult]]
    cut_ts_ms: int | None


def _read_events(ledger_path: Path) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    return [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]


def _last_tape_record(run_dir: Path) -> dict[str, Any] | None:
    active = run_dir / INPUT_FILE
    last = None
    if active.exists() and active.stat().st_size:
        for last in InputTape.stream(active):
            pass
    if last is None:
        manifests = segment_store.load_manifests(run_dir)
        if manifests:
            for last in segment_store.read_segment(run_dir, manifests[-1]):
                pass
    return last


def wallet_event_times(run_dir: Path, events: list[dict[str, Any]] | None = None) -> list[int]:
    """When this run read or changed the shared wallet, from its ledger and its tape tail."""
    if events is None:
        events = _read_events(run_dir / LEDGER_FILE)
    times = [int(event["ts_ms"]) for event in events
             if event.get("event_type") not in WALLET_INERT_EVENTS]
    tail = _last_tape_record(run_dir)
    if tail is not None and tail.get("kind") == InputKind.COMMAND:
        payload = tail.get("payload") or {}
        if payload.get("command") not in WALLET_INERT_COMMANDS:
            times.append(int(payload["ts_ms"]))
    return sorted(times)


def _checkpoints(run_dir: Path) -> list[tuple[int, dict[str, Any]]]:
    """(cut time, payload) newest first. The cut is the last record time of the segment the
    checkpoint closed; a torn checkpoint file is skipped like `load_latest_checkpoint` does."""
    cuts = {manifest.index: manifest.last_ts_ms
            for manifest in segment_store.load_manifests(run_dir)}
    directory = run_dir / segment_store.CHECKPOINT_DIR
    if not directory.exists():
        return []
    found = []
    for path in sorted(directory.glob("*.checkpoint.json"), reverse=True):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        cut = cuts.get(int(payload.get("segment_index", -1)))
        if cut is not None:
            found.append((int(cut), payload))
    return found


def _usable(member: WalletMember, payload: dict[str, Any],
            events: list[dict[str, Any]]) -> bool:
    """The checks `recover_from_checkpoint` makes before trusting a checkpoint."""
    try:
        segment_store.restore(payload, member.config, member.tiers,
                              ledger_path=member.run_dir / LEDGER_FILE)
    except (segment_store.CheckpointRefused, KeyError, ValueError):
        return False
    return len(events) >= int(payload["ledger_events"])


def _pick(member: WalletMember, cut: int | None, wallet_times: list[int],
          checkpoints: list[tuple[int, dict[str, Any]]], events: list[dict[str, Any]],
          usable: dict[int, bool]) -> _Start | None:
    if cut is None:
        return _Start(payload=None, cut_ts_ms=None)
    for ts, payload in checkpoints:
        if ts > cut:
            continue
        if any(ts - CUT_MARGIN_MS < t <= cut + CUT_MARGIN_MS for t in wallet_times):
            continue
        key = int(payload["segment_index"])
        if key not in usable:
            usable[key] = _usable(member, payload, events)
        if usable[key]:
            return _Start(payload=payload, cut_ts_ms=ts)
    if all(t > cut + CUT_MARGIN_MS for t in wallet_times):
        return _Start(payload=None, cut_ts_ms=None)
    return None


def _records_after(run_dir: Path, after_segment: int) -> Iterator[dict[str, Any]]:
    for manifest in segment_store.load_manifests(run_dir):
        if manifest.index <= after_segment:
            continue
        yield from segment_store.read_segment(run_dir, manifest)
    active = run_dir / INPUT_FILE
    if active.exists():
        yield from InputTape.stream(active)


def recover_wallet(owner: WalletMember, others: list[WalletMember]) -> WalletRecovery | None:
    """Recover the owner and every run that touched the shared wallet, together.

    Returns None when no run besides the owner ever touched the wallet: the caller then keeps
    the single-run `recover()` for every run, unchanged. Raises `RecoveryRefused` when no cut
    can be found or the merged replay does not rebuild every member's ledger.
    """
    for member in [owner, *others]:
        truncate_to_last_complete_line(member.run_dir / INPUT_FILE)
        truncate_to_last_complete_line(member.run_dir / LEDGER_FILE)
    events = {m.symbol: _read_events(m.run_dir / LEDGER_FILE) for m in [owner, *others]}
    active = [m for m in others if wallet_event_times(m.run_dir, events[m.symbol])]
    if not active:
        return None
    members = [owner, *sorted(active, key=lambda m: m.symbol)]
    times = {m.symbol: wallet_event_times(m.run_dir, events[m.symbol]) for m in members}
    checkpoints = {m.symbol: _checkpoints(m.run_dir) for m in members}

    plan: dict[str, _Start] | None = None
    usable: dict[str, dict[int, bool]] = {m.symbol: {} for m in members}
    candidates: list[int | None] = sorted({ts for cps in checkpoints.values() for ts, _ in cps},
                                          reverse=True)
    candidates.append(None)
    chosen_cut: int | None = None
    for cut in candidates:
        attempt = {}
        for member in members:
            start = _pick(member, cut, times[member.symbol], checkpoints[member.symbol],
                          events[member.symbol], usable[member.symbol])
            if start is None:
                break
            attempt[member.symbol] = start
        else:
            plan, chosen_cut = attempt, cut
            break
    if plan is None:
        raise RecoveryRefused("WALLET_CUT_UNAVAILABLE",
                              "no common cut where every wallet member has a usable start")

    engines: dict[str, PaperEngine] = {}
    tape_counts: dict[str, int] = {}
    streams: dict[str, Iterator[dict[str, Any]]] = {}
    for member in members:
        start = plan[member.symbol]
        ledger_path = member.run_dir / LEDGER_FILE
        if start.payload is None:
            stored = next((e.get("ledger_schema_version") for e in events[member.symbol]
                           if e.get("event_type") == EventType.RUN_START), None)
            if stored is not None and stored != LEDGER_SCHEMA_VERSION:
                raise RecoveryRefused(
                    "LEDGER_SCHEMA_MISMATCH",
                    f"{member.symbol}: run was written with ledger schema {stored}, this build "
                    f"writes {LEDGER_SCHEMA_VERSION}, and no checkpoint lets it skip the replay")
            engine = PaperEngine(member.config, member.tiers, ledger=Ledger())
            tape_counts[member.symbol] = 0
            streams[member.symbol] = _records_after(member.run_dir, 0)
        else:
            engine = segment_store.restore(start.payload, member.config, member.tiers,
                                           ledger_path=ledger_path)
            prefix = events[member.symbol][:int(start.payload["ledger_events"])]
            engine.ledger = Ledger(events=prefix)
            tape_counts[member.symbol] = int(start.payload["tape_records"])
            streams[member.symbol] = _records_after(member.run_dir,
                                                    int(start.payload["segment_index"]))
        engines[member.symbol] = engine

    cash = SharedCash(owner=owner.symbol)
    for member in members:
        cash.join(member.symbol, engines[member.symbol].account)

    rank = {member.symbol: index for index, member in enumerate(members)}
    heap: list[tuple[int, int, int, str, dict[str, Any]]] = []
    counter = 0

    def push(symbol: str) -> None:
        nonlocal counter
        record = next(streams[symbol], None)
        if record is not None:
            counter += 1
            heapq.heappush(heap, (int(record["payload"]["ts_ms"]), rank[symbol], counter,
                                  symbol, record))

    for member in members:
        push(member.symbol)
    while heap:
        _, _, _, symbol, record = heapq.heappop(heap)
        apply_tape_record(engines[symbol], record)
        tape_counts[symbol] += 1
        push(symbol)

    results: dict[str, tuple[PaperEngine, RecoveryResult]] = {}
    rebuilt_by_symbol = {}
    for member in members:
        rebuilt = engines[member.symbol].ledger.bytes()
        on_disk = (member.run_dir / LEDGER_FILE).read_bytes() \
            if (member.run_dir / LEDGER_FILE).exists() else b""
        if not rebuilt.startswith(on_disk):
            raise RecoveryRefused(
                "LEDGER_DIVERGENCE",
                f"{member.symbol}: the stored ledger ({len(on_disk)} bytes) is not a prefix of "
                f"the ledger the shared-wallet replay rebuilds ({len(rebuilt)} bytes)")
        rebuilt_by_symbol[member.symbol] = (rebuilt, on_disk)
    # Written only once every member has been checked, so a refusal leaves every file as it was.
    for member in members:
        engine = engines[member.symbol]
        rebuilt, on_disk = rebuilt_by_symbol[member.symbol]
        ledger_path = member.run_dir / LEDGER_FILE
        tail = rebuilt[len(on_disk):]
        if tail:
            with ledger_path.open("ab") as stream:
                stream.write(tail)
                stream.flush()
                os.fsync(stream.fileno())
        engine.ledger.path = ledger_path
        start = plan[member.symbol]
        results[member.symbol] = (engine, RecoveryResult(
            restored=True, tape_records=tape_counts[member.symbol],
            ledger_events=len(engine.ledger.events), ledger_bytes=len(rebuilt),
            ledger_tail_rewritten=len(tail), truncations=(),
            schema_version=LEDGER_SCHEMA_VERSION,
            position_signed_qty=str(engine.account.position.signed_qty),
            mode=engine.state.mode, source=SOURCE_WALLET_REPLAY,
            checkpoint_segment=(int(start.payload["segment_index"])
                                if start.payload is not None else None)))
    return WalletRecovery(cash=cash, engines=results, cut_ts_ms=chosen_cut)


__all__ = ["CUT_MARGIN_MS", "SOURCE_WALLET_REPLAY", "WALLET_INERT_COMMANDS",
           "WALLET_INERT_EVENTS", "WalletMember", "WalletRecovery", "recover_wallet",
           "wallet_event_times"]
