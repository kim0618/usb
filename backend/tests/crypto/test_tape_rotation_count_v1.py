"""Tape rotation bookkeeping V1: `should_rotate` without reading the whole active tape.

The observe loop asks `should_rotate` once a second per session, and it used to count the
active tape line by line every time. The count now continues from where it last stopped. What
these tests defend is that this is *only* a cheaper way to the same number: every answer is
compared against `active_records()` (the old whole-file count, left in place), and whole runs
are compared byte for byte against the old rule.
"""
from __future__ import annotations

import codecs
import gzip
import hashlib
import json
import os
import random
from pathlib import Path

import pytest

from app.crypto.paper import segments as seg
from app.crypto.paper.persistence import truncate_to_last_complete_line
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import quote


def _old_should_rotate(self: seg.SegmentedTape) -> bool:
    """The rule as it was before V1, verbatim."""
    return self.active_records() >= self.segment_records


def _tape(tmp_path: Path, every: int = 5) -> seg.SegmentedTape:
    run = tmp_path / "run"
    run.mkdir()
    return seg.SegmentedTape(run_dir=run, segment_records=every)


def _line(i: int) -> bytes:
    return b'{"kind":"MARKET","payload":{"ts_ms":%d},"seq":%d}\n' % (i * 1000, i)


def _append(path: Path, data: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(data)


def _agrees(tape: seg.SegmentedTape) -> int:
    """The fast count and the old count, which must be the same number."""
    fast = tape._count_active()
    assert fast == tape.active_records()
    assert tape.should_rotate() == _old_should_rotate(tape)
    return fast


# ------------------------------------------------------------------ the count itself

def test_missing_active_tape_counts_zero(tmp_path) -> None:
    tape = _tape(tmp_path)
    assert _agrees(tape) == 0
    assert tape.active_full_scans == 0


def test_startup_reads_the_existing_tape_once(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, b"".join(_line(i) for i in range(1, 8)))
    assert _agrees(tape) == 7
    assert tape.active_full_scans == 1
    for _ in range(20):
        assert _agrees(tape) == 7
    assert tape.active_full_scans == 1


def test_each_append_is_counted_without_a_full_scan(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    tape.active_path.touch()
    _agrees(tape)
    for i in range(1, 30):
        _append(tape.active_path, _line(i))
        assert _agrees(tape) == i
    assert tape.active_full_scans == 1


def test_multi_row_append_is_counted_whole(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1))
    _agrees(tape)
    _append(tape.active_path, _line(2) + _line(3) + b"\n  \n" + _line(4))
    assert _agrees(tape) == 4
    assert tape.active_full_scans == 1


def test_a_failed_append_adds_nothing(tmp_path) -> None:
    """A write that never reached the file is not counted, because the count is the file's."""
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1))
    _agrees(tape)
    with pytest.raises(OSError):
        with open(tape.active_path, "rb") as handle:   # read-only: the write fails
            handle.write(_line(2))                      # type: ignore[arg-type]
    assert _agrees(tape) == 1


def test_a_partial_line_counts_like_the_old_rule_and_completes_later(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1) + b'{"kind":"MAR')
    assert _agrees(tape) == 2          # the old count saw a non-blank line, so does this
    _append(tape.active_path, b'KET"}\n')
    assert _agrees(tape) == 2          # the same line, finished: not counted twice
    _append(tape.active_path, b"   ")
    assert _agrees(tape) == 2          # a blank partial is not a record
    assert tape.active_full_scans == 1


def test_exact_threshold(tmp_path) -> None:
    tape = _tape(tmp_path, every=3)
    tape.active_path.touch()
    for i, expected in ((1, False), (2, False), (3, True), (4, True)):
        _append(tape.active_path, _line(i))
        assert tape.should_rotate() is expected
        assert _old_should_rotate(tape) is expected


def test_the_incremental_count_decodes_like_open_does(tmp_path) -> None:
    probe = tmp_path / "probe"
    probe.write_bytes(b"")
    with probe.open() as handle:
        assert codecs.lookup(handle.encoding).name == seg._TEXT_ENCODING
    assert seg._INCREMENTAL_COUNT


def test_non_ascii_records_are_counted_incrementally(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    tape.active_path.touch()
    _agrees(tape)
    _append(tape.active_path, '{"reason":"AUTO 포지션 청산"}\n'.encode())
    assert _agrees(tape) == 1
    assert tape.active_full_scans == 1


# ------------------------------------------------------------------ drift falls back to a full read

def test_truncation_is_detected_and_recounted(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, b"".join(_line(i) for i in range(1, 6)))
    _agrees(tape)
    with tape.active_path.open("r+b") as handle:
        handle.truncate(len(_line(1)) * 2)
    assert _agrees(tape) == 2
    assert tape.active_full_scans == 2


def test_truncation_then_regrowth_past_the_old_size_is_detected(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1) + _line(2))
    _agrees(tape)
    tape.active_path.write_bytes(b"x" * 10 + b"\n" + _line(3) + _line(4) + _line(5))
    assert _agrees(tape) == 4
    assert tape.active_full_scans == 2


def test_replacement_by_another_file_is_detected(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1))
    _agrees(tape)
    other = tape.run_dir / "other.jsonl"
    other.write_bytes(_line(7) + _line(8) + _line(9))
    os.replace(other, tape.active_path)
    assert _agrees(tape) == 3
    assert tape.active_full_scans == 2


def test_in_place_rewrite_of_the_counted_part_is_detected(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1) + _line(2) + _line(3))
    _agrees(tape)
    data = bytearray(tape.active_path.read_bytes())
    data[-1:] = b"X"                         # the last counted newline is gone
    with tape.active_path.open("r+b") as handle:
        handle.write(bytes(data))
    _append(tape.active_path, b"\n" + _line(4))
    assert _agrees(tape) == 4
    assert tape.active_full_scans == 2


def test_carriage_returns_and_bad_bytes_keep_the_old_answer(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1))
    _agrees(tape)
    _append(tape.active_path, b'{"a":1}\r{"b":2}\n')
    assert _agrees(tape) == 3                # text mode splits on \r too
    _append(tape.active_path, b"\xff\xfe\n")
    with pytest.raises(UnicodeDecodeError):
        tape.active_records()
    with pytest.raises(UnicodeDecodeError):
        tape.should_rotate()                 # the old rule raised here as well


def test_recovery_truncation_of_a_torn_line(tmp_path) -> None:
    tape = _tape(tmp_path, every=100)
    _append(tape.active_path, _line(1) + _line(2) + b'{"torn')
    assert _agrees(tape) == 3
    truncate_to_last_complete_line(tape.active_path)
    assert _agrees(tape) == 2


def test_randomized_mutations_always_agree_with_the_full_count(tmp_path) -> None:
    rng = random.Random(20261013)
    tape = _tape(tmp_path, every=40)
    path = tape.active_path
    n = 0
    for step in range(3_000):
        roll = rng.random()
        n += 1
        if roll < 0.55:
            _append(path, _line(n))
        elif roll < 0.65:
            _append(path, b"".join(_line(n + k) for k in range(rng.randint(2, 6))))
        elif roll < 0.70:
            _append(path, rng.choice([b"\n", b"  \n", b'{"part', b'ial"}\n', b"\t"]))
        elif roll < 0.73 and path.exists():
            size = path.stat().st_size
            with path.open("r+b") as handle:
                handle.truncate(rng.randint(0, size))
        elif roll < 0.75:
            other = tape.run_dir / "swap.jsonl"
            other.write_bytes(b"".join(_line(n + k) for k in range(rng.randint(0, 8))))
            os.replace(other, path)
        elif roll < 0.76 and path.exists():
            path.unlink()
        elif roll < 0.78:
            _append(path, '{"r":"한글"}\n'.encode())
        assert tape._count_active() == tape.active_records(), f"step {step}"
    # Drift is rare in this mix, so most answers must have come from the incremental path.
    assert tape.active_full_scans < 400


# ------------------------------------------------------------------ the session, old rule vs new

def _run_dir_digest(run_dir: Path) -> dict[str, str]:
    """Every file's hash. gzip stamps the write time into its header, so a compressed segment
    is compared by its content and its manifest without the two fields that stamp reaches."""
    digest: dict[str, str] = {}
    for item in sorted(run_dir.rglob("*")):
        if not item.is_file():
            continue
        data = item.read_bytes()
        if item.suffix == ".gz":
            data = gzip.decompress(data)
        elif item.name.endswith(".manifest.json"):
            manifest = json.loads(data)
            if manifest["compressed"]:
                manifest.pop("bytes"), manifest.pop("sha256")
            data = json.dumps(manifest, sort_keys=True).encode()
        digest[item.relative_to(run_dir).as_posix()] = hashlib.sha256(data).hexdigest()
    return digest


def _drive(config, tiers, root: Path, *, seed: int, every: int, compress: bool,
           old_rule: bool, monkeypatch) -> tuple[list[int], dict[str, str], int]:
    """One deterministic session history: ticks, orders, restarts, a torn write."""
    if old_rule:
        monkeypatch.setattr(seg.SegmentedTape, "should_rotate", _old_should_rotate)
    else:
        monkeypatch.undo()
    rng = random.Random(seed)
    s = PaperSession(config=config, tiers=tiers, root=root, segment_records=every,
                     compress_segments=compress)
    s.start(1_000)
    rotations: list[int] = []
    open_qty = False
    for i in range(rng.randint(150, 260)):
        ts = 2_000 + i * 1_000
        q = quote(ts, bid=f"{100000 + rng.randint(-50, 50)}.0",
                  ask=f"{100000 + rng.randint(51, 60)}.1")
        before = len(seg.load_manifests(s.run_dir))
        s.observe(q, force=rng.random() < 0.2)
        if len(seg.load_manifests(s.run_dir)) != before:
            rotations.append(len(s.tape))
        roll = rng.random()
        if roll < 0.06:
            intent = "CLOSE" if open_qty else "OPEN"
            s.command({"command": "ORDER", "ts_ms": ts, "side": "LONG", "qty": "0.010",
                       "intent": intent, "request_id": f"r{ts}"}, quote=q)
            open_qty = not open_qty
        elif roll < 0.09:
            # Restart, sometimes after a torn write the recovery has to cut off.
            if rng.random() < 0.5:
                _append(s.run_dir / "input.jsonl", b'{"kind":"MARKET","pay')
            s = PaperSession(config=config, tiers=tiers, root=root, segment_records=every,
                             compress_segments=compress)
            assert s.segments._count_active() == s.segments.active_records()
    scans = s.segments.active_full_scans if not old_rule else -1
    return rotations, _run_dir_digest(s.run_dir), scans


@pytest.mark.parametrize("seed,every,compress", [
    (1, 7, False), (2, 13, True), (3, 20, False), (4, 1, False), (5, 31, True),
    (6, 9, False), (7, 50, False), (8, 17, True),
])
def test_old_and_new_rule_produce_identical_runs(config, tiers, tmp_path, monkeypatch,
                                                 seed, every, compress) -> None:
    old = _drive(config, tiers, tmp_path / "old", seed=seed, every=every, compress=compress,
                 old_rule=True, monkeypatch=monkeypatch)
    new = _drive(config, tiers, tmp_path / "new", seed=seed, every=every, compress=compress,
                 old_rule=False, monkeypatch=monkeypatch)
    assert old[0] == new[0]                  # cut at the same tape lengths
    assert old[1] == new[1]                  # every segment, manifest, checkpoint, ledger
    assert old[0], "the fixture must actually rotate"


def test_restart_recovers_the_count_and_then_stops_scanning(config, tiers, tmp_path) -> None:
    s = PaperSession(config=config, tiers=tiers, root=tmp_path, segment_records=50)
    s.start(1_000)
    for i in range(70):
        s.observe(quote(2_000 + i * 1_000, bid="100000.0", ask="100000.1"))
    restarted = PaperSession(config=config, tiers=tiers, root=tmp_path, segment_records=50)
    assert restarted.segments._count_active() == restarted.segments.active_records()
    scans = restarted.segments.active_full_scans
    for i in range(70, 200):
        restarted.observe(quote(2_000 + i * 1_000, bid="100000.0", ask="100000.1"))
        assert restarted.segments._count_active() == restarted.segments.active_records()
    # One read at start; each rotation hands over an empty file whose count is known.
    assert restarted.segments.active_full_scans == scans == 1
    assert len(seg.load_manifests(restarted.run_dir)) == 4     # 201 records, cut every 50


def test_rotation_resets_the_count_without_reading(config, tiers, tmp_path) -> None:
    s = PaperSession(config=config, tiers=tiers, root=tmp_path, segment_records=10)
    s.start(1_000)
    for i in range(45):
        s.observe(quote(2_000 + i * 1_000, bid="100000.0", ask="100000.1"))
    assert len(seg.load_manifests(s.run_dir)) == 4
    assert s.segments.active_full_scans == 1     # the first observe, on a fresh tape
    assert s.segments._count_active() == s.segments.active_records() == 6


def test_output_is_deterministic(config, tiers, tmp_path, monkeypatch) -> None:
    a = _drive(config, tiers, tmp_path / "a", seed=11, every=12, compress=True,
               old_rule=False, monkeypatch=monkeypatch)
    b = _drive(config, tiers, tmp_path / "b", seed=11, every=12, compress=True,
               old_rule=False, monkeypatch=monkeypatch)
    assert a == b
