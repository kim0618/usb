"""Segmented tape, checkpoints and long-run recovery.

The property everything here defends: a checkpoint may make a restart fast, but it must never
make a wrong state look right. Every test either proves a shortcut agrees with the long way
round, or proves the shortcut refuses itself when it cannot.
"""
from __future__ import annotations

import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper import segments as seg
from app.crypto.paper.engine import PaperEngine
from app.crypto.paper.ledger import InputTape
from app.crypto.paper.persistence import recover
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


def session(config, tiers, root: Path, *, every: int = 10, compress: bool = False) -> PaperSession:
    return PaperSession(config=config, tiers=tiers, root=root,
                        segment_records=every, compress_segments=compress)


def drive(s: PaperSession, ticks: int, *, start: int = 2_000, trade_at: int | None = None) -> None:
    """Feed market records, optionally opening and closing a position part way through."""
    for i in range(ticks):
        ts = start + i * 1_000
        s.observe(quote(ts, bid=f"{100000 + i}.0", ask=f"{100000 + i}.1"), force=True)
        if trade_at is not None and i == trade_at:
            s.command({"command": "ORDER", "ts_ms": ts, "side": "LONG", "qty": "0.010",
                       "intent": "OPEN", "request_id": f"o{ts}"},
                      quote=quote(ts, bid=f"{100000 + i}.0", ask=f"{100000 + i}.1"))
        if trade_at is not None and i == trade_at + 3:
            s.command({"command": "ORDER", "ts_ms": ts, "side": "LONG", "qty": "0.010",
                       "intent": "CLOSE", "request_id": f"c{ts}"},
                      quote=quote(ts, bid=f"{100000 + i}.0", ask=f"{100000 + i}.1"))


# ------------------------------------------------------------------ rotation

def test_the_tape_rotates_into_immutable_segments(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)

    manifests = seg.load_manifests(s.run_dir)
    assert len(manifests) >= 3
    assert [m.index for m in manifests] == list(range(1, len(manifests) + 1))
    # Nothing is lost at a cut: the segments plus the active tape hold every record.
    total = sum(m.records for m in manifests) + s.segments.active_records()
    assert total == len(s.tape)


def test_every_closed_segment_carries_a_checksum_that_verifies(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    manifests = seg.load_manifests(s.run_dir)
    assert manifests
    assert seg.verify_segments(s.run_dir, manifests) == []


def test_a_tampered_segment_is_caught_by_its_checksum(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    manifests = seg.load_manifests(s.run_dir)
    victim = s.run_dir / manifests[0].path
    victim.write_text(victim.read_text().replace("100000", "999999", 1))

    broken = seg.verify_segments(s.run_dir, manifests)
    assert [item["problem"] for item in broken] == ["CHECKSUM_MISMATCH"]


def test_a_checkpoint_is_written_at_every_cut(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    saved = sorted((s.run_dir / seg.CHECKPOINT_DIR).glob("*.checkpoint.json"))
    assert len(saved) == len(seg.load_manifests(s.run_dir))
    payload = json.loads(saved[-1].read_text())
    assert payload["version"] == seg.CHECKPOINT_VERSION
    assert payload["run_id"] == config.run_id
    assert payload["ledger_sha256"]


def test_a_manifest_a_crash_never_wrote_is_rebuilt_from_the_segment(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    manifests = seg.load_manifests(s.run_dir)
    (s.run_dir / seg.SEGMENT_DIR / f"{manifests[0].index:06d}.manifest.json").unlink()

    # The segment is immutable, so it can be described after the fact rather than stranded.
    rebuilt = seg.load_manifests(s.run_dir)
    assert [m.index for m in rebuilt] == [m.index for m in manifests]
    assert rebuilt[0].sha256 == manifests[0].sha256
    assert rebuilt[0].records == manifests[0].records


# ------------------------------------------------------------------ equivalence

def test_a_checkpoint_restore_lands_exactly_where_a_full_replay_lands(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 40, trade_at=12)
    expected_ledger = (s.run_dir / "ledger.jsonl").read_bytes()

    fast, fast_result = recover(s.run_dir, config, tiers, use_checkpoint=True)
    slow, slow_result = recover(s.run_dir, config, tiers, use_checkpoint=False)

    assert fast_result.source == "CHECKPOINT"
    assert slow_result.source == "FULL_REPLAY"
    # The whole design rests on this line.
    assert fast.ledger.bytes() == slow.ledger.bytes() == expected_ledger
    assert fast.account.wallet_balance == slow.account.wallet_balance
    assert fast.account.realized_pnl == slow.account.realized_pnl
    assert fast.account.cumulative_fees == slow.account.cumulative_fees
    assert fast.account.position.signed_qty == slow.account.position.signed_qty
    assert fast.state.mode == slow.state.mode
    assert fast.leverage == slow.leverage


def test_a_checkpoint_restore_keeps_the_whole_ledger_not_just_the_tail(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 40, trade_at=12)
    before = len(s.engine.ledger.events)

    engine, result = recover(s.run_dir, config, tiers, use_checkpoint=True)
    assert result.source == "CHECKPOINT"
    # Analytics fold the ledger, so a shortcut that dropped pre-checkpoint history would
    # silently shrink every statistic in the system.
    assert len(engine.ledger.events) == before
    assert engine.ledger.events[0]["event_type"] == "RUN_START"


def test_restoring_twice_is_the_same_as_restoring_once(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 40, trade_at=12)

    first, _ = recover(s.run_dir, config, tiers)
    second, _ = recover(s.run_dir, config, tiers)
    assert first.ledger.bytes() == second.ledger.bytes()
    assert first.account.wallet_balance == second.account.wallet_balance


def test_a_restored_session_keeps_trading_and_rotating(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 25)
    records_before = len(s.tape)

    second = session(config, tiers, tmp_path, every=10)
    assert second.recovery is not None and second.recovery.restored
    drive(second, 25, start=200_000)
    assert len(second.tape) > records_before
    assert seg.verify_segments(second.run_dir, seg.load_manifests(second.run_dir)) == []


# ------------------------------------------------------------------ refusal

def test_a_checkpoint_over_a_ledger_that_moved_is_refused(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 40, trade_at=12)

    # Rewrite a byte inside the region the checkpoint attests to.
    ledger = s.run_dir / "ledger.jsonl"
    raw = ledger.read_bytes()
    ledger.write_bytes(raw.replace(b"RUN_START", b"RUN_STARX", 1))

    assert seg.load_latest_checkpoint(s.run_dir) is not None
    from app.crypto.paper.persistence import recover_from_checkpoint
    # Refusing means falling back to a full replay, which is always correct and merely slower.
    assert recover_from_checkpoint(s.run_dir, config, tiers) is None


def test_a_checkpoint_from_another_run_is_refused(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 25)
    payload = seg.load_latest_checkpoint(s.run_dir)
    payload["run_id"] = "someone-elses-run"
    with pytest.raises(seg.CheckpointRefused) as raised:
        seg.restore(payload, config, tiers, ledger_path=s.run_dir / "ledger.jsonl")
    assert raised.value.code == "CHECKPOINT_RUN_MISMATCH"


def test_a_checkpoint_of_an_unknown_version_is_refused(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 25)
    payload = seg.load_latest_checkpoint(s.run_dir)
    payload["version"] = "crypto.paper.checkpoint.v99"
    with pytest.raises(seg.CheckpointRefused) as raised:
        seg.restore(payload, config, tiers, ledger_path=s.run_dir / "ledger.jsonl")
    assert raised.value.code == "CHECKPOINT_VERSION_MISMATCH"


def test_a_torn_checkpoint_falls_back_to_an_older_one(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 40, trade_at=12)
    saved = sorted((s.run_dir / seg.CHECKPOINT_DIR).glob("*.checkpoint.json"))
    assert len(saved) >= 2
    saved[-1].write_text('{"version": "crypto.paper.che')     # a crash mid-write

    engine, result = recover(s.run_dir, config, tiers)
    slow, _ = recover(s.run_dir, config, tiers, use_checkpoint=False)
    assert engine.ledger.bytes() == slow.ledger.bytes()


# ------------------------------------------------------------------ compression

def test_a_compressed_segment_round_trips_and_keeps_both_checksums(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10, compress=True)
    s.start(1_000)
    drive(s, 35)

    manifests = seg.load_manifests(s.run_dir)
    assert manifests and all(m.compressed for m in manifests)
    for manifest in manifests:
        # Both hashes are kept: one proves the stored bytes, one proves what they decompress to.
        assert manifest.uncompressed_sha256
        assert (s.run_dir / manifest.path).suffix == ".gz"
    assert seg.verify_segments(s.run_dir, manifests) == []

    plain = b"".join(json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                     for r in seg.read_segment(s.run_dir, manifests[0]))
    assert plain      # readable straight back through gzip


def test_a_compressed_run_restores_to_the_same_state_as_an_uncompressed_one(config, tiers,
                                                                            tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10, compress=True)
    s.start(1_000)
    drive(s, 40, trade_at=12)

    fast, fast_result = recover(s.run_dir, config, tiers, use_checkpoint=True)
    slow, _ = recover(s.run_dir, config, tiers, use_checkpoint=False)
    assert fast.ledger.bytes() == slow.ledger.bytes()
    assert fast.account.wallet_balance == slow.account.wallet_balance


def test_compression_actually_shrinks_the_archive(config, tiers, tmp_path) -> None:
    plain_root, gz_root = tmp_path / "plain", tmp_path / "gz"
    a = session(config, tiers, plain_root, every=50)
    a.start(1_000)
    drive(a, 120)
    b = session(config, tiers, gz_root, every=50, compress=True)
    b.start(1_000)
    drive(b, 120)

    plain_bytes = sum(m.bytes for m in seg.load_manifests(a.run_dir))
    gz_bytes = sum(m.bytes for m in seg.load_manifests(b.run_dir))
    assert gz_bytes < plain_bytes / 2


# ------------------------------------------------------------------ open position restarts

@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_a_restart_across_a_segment_boundary_keeps_an_open_position(config, tiers, tmp_path,
                                                                    side: str) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    for i in range(6):
        ts = 2_000 + i * 1_000
        s.observe(quote(ts), force=True)
    open_ts = 2_000 + 6 * 1_000
    s.command({"command": "ORDER", "ts_ms": open_ts, "side": side, "qty": "0.010",
               "intent": "OPEN", "request_id": "o1"}, quote=quote(open_ts))
    # Keep going well past the cut so the position lives on both sides of it.
    for i in range(7, 40):
        s.observe(quote(2_000 + i * 1_000), force=True)
    assert seg.load_manifests(s.run_dir)
    expected = s.engine.account.position.signed_qty

    fast, result = recover(s.run_dir, config, tiers)
    slow, _ = recover(s.run_dir, config, tiers, use_checkpoint=False)
    assert fast.account.position.signed_qty == slow.account.position.signed_qty == expected
    assert fast.account.position.avg_entry == slow.account.position.avg_entry
    assert fast.ledger.bytes() == slow.ledger.bytes()


def test_a_reset_before_a_cut_survives_a_checkpoint_restore(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 12, trade_at=2)
    s.command({"command": "ACCOUNT_RESET", "ts_ms": 50_000, "target_krw": "10000000"},
              quote=quote(50_000))
    drive(s, 30, start=60_000)

    fast, _ = recover(s.run_dir, config, tiers)
    slow, _ = recover(s.run_dir, config, tiers, use_checkpoint=False)
    assert fast.account.reset_count == slow.account.reset_count == 1
    assert fast.account.capital_base_usdt == slow.account.capital_base_usdt
    assert fast.account.realized_pnl == slow.account.realized_pnl
    assert fast.ledger.bytes() == slow.ledger.bytes()


def test_a_torn_active_tape_after_a_cut_is_repaired_and_still_agrees(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    active = s.run_dir / "input.jsonl"
    with active.open("ab") as handle:
        handle.write(b'{"seq": 999, "kind": "MARKET", "payl')     # killed mid-write

    fast, result = recover(s.run_dir, config, tiers)
    slow, _ = recover(s.run_dir, config, tiers, use_checkpoint=False)
    assert any(item.was_torn for item in result.truncations)
    assert fast.ledger.bytes() == slow.ledger.bytes()


def test_no_duplicate_fill_across_a_cut_and_a_restart(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 40, trade_at=12)
    fills = [e for e in s.engine.ledger.events if e["event_type"] == "FILL"]

    for _ in range(3):
        engine, _ = recover(s.run_dir, config, tiers)
        assert [e for e in engine.ledger.events if e["event_type"] == "FILL"] == fills


def test_storage_reports_what_the_run_is_actually_using(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    storage = s.segments.storage()
    assert storage["segments"] >= 3
    assert storage["records"] == len(s.tape)
    assert storage["total_bytes"] == storage["segment_bytes"] + storage["active_bytes"]
    assert storage["ledger_bytes"] > 0


# ------------------------------------------------------------------ start-time after rotation

def test_a_restart_after_rotation_still_knows_when_the_run_started(config, tiers, tmp_path) -> None:
    """The regression that took the live service down.

    After a rotation the START command lives in a closed segment, not in the active tape.
    Reading the start time by scanning the active tape returned None, the caller concluded the
    run had never started and issued a second START, and that second START made the *next*
    restart refuse to load at all.
    """
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    assert seg.load_manifests(s.run_dir)            # the START is now in segment 1

    second = PaperSession(config=config, tiers=tiers, root=tmp_path, segment_records=10)
    assert second.started_at_ms == 1_000
    assert second.is_started is True                # so nothing starts the run again
    assert second.engine.last_market_ts_ms is not None
    assert len(second.tape) == len(s.tape)          # segments counted, not just the active file


def test_a_tape_carrying_a_duplicate_start_still_loads(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 35)
    # Exactly what the old bug wrote into the active tape.
    s.tape.record("COMMAND", {"command": "START", "ts_ms": 90_000})

    engine, result = recover(s.run_dir, config, tiers)
    slow, _ = recover(s.run_dir, config, tiers, use_checkpoint=False)
    # The first START is the one that happened; the second is ignored rather than fatal.
    assert engine.ledger.bytes() == slow.ledger.bytes()
    assert sum(1 for e in engine.ledger.events if e["event_type"] == "RUN_START") == 1

    revived = PaperSession(config=config, tiers=tiers, root=tmp_path, segment_records=10)
    assert revived.started_at_ms == 1_000
    assert revived.engine.account.position.signed_qty == s.engine.account.position.signed_qty
