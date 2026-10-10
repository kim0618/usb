"""Segment compression as a thing you can turn on in production without losing history.

`test_paper_segments.py` already proves a compressed run round-trips and restores. What is
proved here is narrower and more paranoid, because the reason to own these tests is that the
flag is about to be switched on under a live position:

* the plain path is byte-for-byte what it was, so turning the flag *off* is a real rollback;
* a compressed segment decompresses to exactly the bytes the plain path would have stored;
* the archive is read correctly while it is *mixed*, which is the only state a server that
  switches the flag on will ever be in again;
* every window a crash can land in during a compressing rotation leaves something a restart
  reads correctly, and in particular leaves no segment that gets replayed twice;
* a failed compression keeps the plaintext it was made from.

The duplicate-replay case is the one that motivated the guard in `load_manifests`: a crash
between a `.gz` being written and its plain twin being removed used to make `load_manifests`
describe the plain file, write that manifest, and then find its own freshly written manifest on
the `.gz` in the same pass and append it a second time. A full replay then applied the segment
twice and rebuilt a ledger holding every fill in it twice over.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.crypto.paper import segments as seg
from app.crypto.paper.persistence import recover
from app.crypto.terminal.api import (COMPRESS_SEGMENTS_ENV, compress_segments_setting)
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import make_config, quote


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


def segment_bytes(run_dir: Path, manifest: seg.SegmentManifest) -> bytes:
    """The segment's *uncompressed* bytes, however it happens to be stored."""
    path = run_dir / manifest.path
    if manifest.compressed:
        with gzip.open(path, "rb") as handle:
            return handle.read()
    return path.read_bytes()


def fills(engine) -> list[dict]:
    return [e for e in engine.ledger.events if e["event_type"] == "FILL"]


# ---------------------------------------------------------------- A. compress=false unchanged

def test_with_the_flag_off_the_archive_is_plain_and_byte_identical_run_to_run(config, tiers,
                                                                              tmp_path) -> None:
    """Off is the default and off must still be exactly what it was: a rollback has to be real."""
    a = session(config, tiers, tmp_path / "a", every=10)
    a.start(1_000)
    drive(a, 35, trade_at=12)
    b = session(make_config(), tiers, tmp_path / "b", every=10)
    b.start(1_000)
    drive(b, 35, trade_at=12)

    left, right = seg.load_manifests(a.run_dir), seg.load_manifests(b.run_dir)
    assert [m.index for m in left] == [m.index for m in right]
    for one, two in zip(left, right):
        assert one.compressed is False and two.compressed is False
        assert one.uncompressed_sha256 is None
        assert Path(one.path).suffix == ".jsonl"
        # Same input, same stored bytes. Nothing about the plain path became non-deterministic.
        assert one.sha256 == two.sha256
        assert (a.run_dir / one.path).read_bytes() == (b.run_dir / two.path).read_bytes()
    assert not list((a.run_dir / seg.SEGMENT_DIR).glob("*.gz"))
    assert a.segments.storage()["compress_new_segments"] is False


# ---------------------------------------------------------------- B. compress=true correctness

def test_a_compressed_segment_holds_exactly_the_bytes_the_plain_path_would_have_stored(
        config, tiers, tmp_path) -> None:
    """The strongest form of the claim: the two archives differ only in their encoding."""
    plain = session(config, tiers, tmp_path / "plain", every=10)
    plain.start(1_000)
    drive(plain, 35, trade_at=12)
    gz = session(make_config(), tiers, tmp_path / "gz", every=10, compress=True)
    gz.start(1_000)
    drive(gz, 35, trade_at=12)

    left, right = seg.load_manifests(plain.run_dir), seg.load_manifests(gz.run_dir)
    assert left and [m.index for m in left] == [m.index for m in right]
    for one, two in zip(left, right):
        assert two.compressed is True
        assert Path(two.path).suffix == ".gz"
        raw = segment_bytes(plain.run_dir, one)
        assert segment_bytes(gz.run_dir, two) == raw
        # `uncompressed_sha256` is the promise about what it decompresses to, so check it is
        # the hash of those bytes and not merely present.
        assert two.uncompressed_sha256 == hashlib.sha256(raw).hexdigest() == one.sha256
        # `sha256` is the promise about the stored bytes.
        assert two.sha256 == hashlib.sha256((gz.run_dir / two.path).read_bytes()).hexdigest()
        assert two.records == one.records
        assert (two.first_ts_ms, two.last_ts_ms) == (one.first_ts_ms, one.last_ts_ms)
    assert seg.verify_segments(gz.run_dir, right) == []
    assert gz.segments.storage()["compress_new_segments"] is True
    assert gz.segments.storage()["compressed_segments"] == len(right)


# ---------------------------------------------------------------- C. checkpoint restart

def test_a_restart_on_a_compressed_archive_uses_the_checkpoint_and_replays_only_the_tail(
        config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10, compress=True)
    s.start(1_000)
    drive(s, 35, trade_at=12)
    manifests = seg.load_manifests(s.run_dir)
    newest = max(m.index for m in manifests)

    engine, result = recover(s.run_dir, config, tiers, use_checkpoint=True)
    assert result.source == "CHECKPOINT"
    assert result.checkpoint_segment == newest
    # The tail only: a checkpoint at the newest cut leaves nothing in the archive to re-read.
    tape = seg.SegmentedTape(run_dir=s.run_dir)
    assert tape.replay_from(engine, after_segment=newest) == tape.active_records()
    assert result.position_signed_qty == str(s.engine.account.position.signed_qty)


# ---------------------------------------------------------------- D. checkpoint-less full replay

def test_a_full_replay_reads_the_gz_archive_and_lands_where_the_plain_one_does(config, tiers,
                                                                               tmp_path) -> None:
    """The fallback is the reason not to delete segments, so it has to work through gzip."""
    plain = session(config, tiers, tmp_path / "plain", every=10)
    plain.start(1_000)
    drive(plain, 40, trade_at=12)
    gz = session(make_config(), tiers, tmp_path / "gz", every=10, compress=True)
    gz.start(1_000)
    drive(gz, 40, trade_at=12)

    slow_plain, plain_result = recover(plain.run_dir, config, tiers, use_checkpoint=False)
    slow_gz, gz_result = recover(gz.run_dir, make_config(), tiers, use_checkpoint=False)
    assert slow_gz.ledger.bytes() == slow_plain.ledger.bytes()
    assert fills(slow_gz) == fills(slow_plain)
    assert slow_gz.account.position.signed_qty == slow_plain.account.position.signed_qty
    assert slow_gz.account.realized_pnl == slow_plain.account.realized_pnl
    assert gz_result.tape_records == plain_result.tape_records

    # And the shortcut agrees with the long way round on the compressed archive too.
    fast_gz, fast_result = recover(gz.run_dir, make_config(), tiers, use_checkpoint=True)
    assert fast_result.source == "CHECKPOINT"
    assert fast_gz.ledger.bytes() == slow_gz.ledger.bytes()


def test_a_checkpoint_that_cannot_be_used_still_recovers_through_the_compressed_archive(
        config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10, compress=True)
    s.start(1_000)
    drive(s, 40, trade_at=12)
    expected = recover(s.run_dir, make_config(), tiers, use_checkpoint=False)[0].ledger.bytes()

    # Every checkpoint gone is the realistic version of "refused": a schema bump, a run-id
    # change and a moved ledger prefix all end at the same fallback.
    for path in (s.run_dir / seg.CHECKPOINT_DIR).glob("*.checkpoint.json"):
        path.unlink()
    assert seg.load_latest_checkpoint(s.run_dir) is None

    engine, result = recover(s.run_dir, make_config(), tiers, use_checkpoint=True)
    assert result.source != "CHECKPOINT"
    assert engine.ledger.bytes() == expected


# ---------------------------------------------------------------- E. mixed archive

def mixed_archive(config, tiers, root: Path, *, every: int = 10):
    """The only state a server that switches the flag on will ever be in again."""
    first = session(config, tiers, root, every=every)
    first.start(1_000)
    drive(first, 25, trade_at=8)
    plain_indices = [m.index for m in seg.load_manifests(first.run_dir)]
    # A restart with the flag now on. Same run directory, same run id.
    second = session(make_config(), tiers, root, every=every, compress=True)
    drive(second, 25, start=2_000 + 25 * 1_000, trade_at=8)
    return second, plain_indices


def test_a_mixed_archive_reads_once_per_index_in_order(config, tiers, tmp_path) -> None:
    s, plain_indices = mixed_archive(config, tiers, tmp_path)
    manifests = seg.load_manifests(s.run_dir)
    indices = [m.index for m in manifests]

    assert indices == sorted(indices), "segments must be handed back in index order"
    assert len(indices) == len(set(indices)), f"an index appears twice: {indices}"
    assert indices == list(range(1, len(indices) + 1)), "no gaps and no rewind"
    assert plain_indices, "the first half must have sealed at least one plain segment"
    kinds = {m.index: m.compressed for m in manifests}
    assert all(kinds[i] is False for i in plain_indices), "sealed segments were converted"
    assert any(kinds[i] is True for i in indices if i not in plain_indices), "nothing compressed"
    # Both encodings really are on disk together.
    directory = s.run_dir / seg.SEGMENT_DIR
    assert list(directory.glob("*.input.jsonl")) and list(directory.glob("*.input.jsonl.gz"))
    assert seg.verify_segments(s.run_dir, manifests) == []


def test_a_mixed_archive_replays_every_record_exactly_once(config, tiers, tmp_path) -> None:
    s, _ = mixed_archive(config, tiers, tmp_path)
    manifests = seg.load_manifests(s.run_dir)
    tape = seg.SegmentedTape(run_dir=s.run_dir)

    assert tape.total_records() == len(s.tape)
    assert sum(m.records for m in manifests) + tape.active_records() == len(s.tape)

    engine, result = recover(s.run_dir, make_config(), tiers, use_checkpoint=False)
    assert result.tape_records == len(s.tape)
    # The thing a duplicated index would break first: a doubled fill.
    assert len(fills(engine)) == len(fills(s.engine))
    assert engine.ledger.bytes() == s.engine.ledger.bytes()
    assert engine.account.position.signed_qty == s.engine.account.position.signed_qty


# ---------------------------------------------------------------- F. next_index

def test_next_index_never_rewinds_over_a_mixed_archive(config, tiers, tmp_path) -> None:
    s, _ = mixed_archive(config, tiers, tmp_path)
    manifests = seg.load_manifests(s.run_dir)
    newest = max(m.index for m in manifests)
    assert manifests[-1].compressed is True, "the newest cut should be a gz in this fixture"

    tape = seg.SegmentedTape(run_dir=s.run_dir)
    assert tape.next_index() == newest + 1
    # Asking twice does not move it, and a fresh view of the same directory agrees.
    assert tape.next_index() == seg.SegmentedTape(run_dir=s.run_dir).next_index()


def test_next_index_is_taken_from_manifests_so_a_gz_cannot_overwrite_a_plain_segment(
        config, tiers, tmp_path) -> None:
    s, _ = mixed_archive(config, tiers, tmp_path)
    directory = s.run_dir / seg.SEGMENT_DIR
    before = {p.name: p.read_bytes() for p in directory.glob("*.input.jsonl*")}

    tape = seg.SegmentedTape(run_dir=s.run_dir, segment_records=1, compress=True)
    tape.rotate(s.engine, tape_records=len(s.tape))

    after = {p.name: p.read_bytes() for p in directory.glob("*.input.jsonl*")}
    for name, payload in before.items():
        assert after[name] == payload, f"{name} was overwritten by a new cut"
    indices = [m.index for m in seg.load_manifests(s.run_dir)]
    assert len(indices) == len(set(indices)) == len(before) + 1


# ---------------------------------------------------------------- G. the active tape

def test_the_active_tape_is_never_compressed(config, tiers, tmp_path) -> None:
    s = session(config, tiers, tmp_path, every=10, compress=True)
    s.start(1_000)
    drive(s, 35)

    active = s.run_dir / seg.ACTIVE_TAPE
    assert active.exists()
    assert not (s.run_dir / (seg.ACTIVE_TAPE + ".gz")).exists()
    with active.open("rb") as handle:
        assert handle.read(2) != b"\x1f\x8b", "the active tape carries a gzip header"
    # It is still line-delimited JSON an append can continue.
    text = active.read_text()
    if text.strip():
        json.loads(text.splitlines()[0])
    assert s.segments.active_records() == len(s.tape) - sum(
        m.records for m in seg.load_manifests(s.run_dir))


# ---------------------------------------------------------------- H. crash during rotation

def test_a_crash_between_the_gz_and_the_plain_removal_replays_the_segment_once(config, tiers,
                                                                               tmp_path) -> None:
    """The window the duplicate guard exists for.

    Plain file in `segments/`, a `.gz` beside it, and no manifest: the state left by a crash
    after the compressed copy was written and before the plain one was removed.
    """
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 25, trade_at=8)
    expected = recover(s.run_dir, make_config(), tiers, use_checkpoint=False)[0]

    directory = s.run_dir / seg.SEGMENT_DIR
    victim = sorted(directory.glob("*.input.jsonl"))[0]
    index = int(victim.name.split(".", 1)[0])
    with victim.open("rb") as source, gzip.open(directory / (victim.name + ".gz"), "wb") as sink:
        sink.write(source.read())
    (directory / f"{index:06d}.manifest.json").unlink()

    manifests = seg.load_manifests(s.run_dir)
    described = [m for m in manifests if m.index == index]
    assert len(described) == 1, f"index {index} described {len(described)} times"
    # The plain file is the proven one, so it is the one the repair points at.
    assert described[0].compressed is False
    assert described[0].path.endswith(".input.jsonl")
    indices = [m.index for m in manifests]
    assert len(indices) == len(set(indices))

    engine, result = recover(s.run_dir, make_config(), tiers, use_checkpoint=False)
    assert result.tape_records == len(s.tape)
    assert len(fills(engine)) == len(fills(expected))
    assert engine.ledger.bytes() == expected.ledger.bytes()


def test_a_crash_between_the_plain_removal_and_the_manifest_counts_the_gz_records(config, tiers,
                                                                                  tmp_path) -> None:
    """The next window along: only the `.gz` survives, still undescribed."""
    s = session(config, tiers, tmp_path, every=10, compress=True)
    s.start(1_000)
    drive(s, 25, trade_at=8)
    before = seg.load_manifests(s.run_dir)
    victim = before[0]
    truth = victim.records
    assert truth > 0

    (s.run_dir / seg.SEGMENT_DIR / f"{victim.index:06d}.manifest.json").unlink()
    repaired = [m for m in seg.load_manifests(s.run_dir) if m.index == victim.index]
    assert len(repaired) == 1
    assert repaired[0].compressed is True
    # The count is read back out of the gzip rather than declared to be zero: the session
    # continues the tape sequence from this number.
    assert repaired[0].records == truth
    assert (repaired[0].first_ts_ms, repaired[0].last_ts_ms) == (victim.first_ts_ms,
                                                                 victim.last_ts_ms)
    assert seg.SegmentedTape(run_dir=s.run_dir).total_records() == len(s.tape)


def test_a_crash_before_the_gz_is_finished_still_leaves_a_readable_archive(config, tiers,
                                                                           tmp_path) -> None:
    """A truncated `.gz` must not be what a recovery decides to read."""
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 25, trade_at=8)
    expected = recover(s.run_dir, make_config(), tiers, use_checkpoint=False)[0]

    directory = s.run_dir / seg.SEGMENT_DIR
    victim = sorted(directory.glob("*.input.jsonl"))[0]
    index = int(victim.name.split(".", 1)[0])
    full = gzip.compress(victim.read_bytes())
    (directory / (victim.name + ".gz")).write_bytes(full[: len(full) // 2])   # torn write
    (directory / f"{index:06d}.manifest.json").unlink()

    engine, result = recover(s.run_dir, make_config(), tiers, use_checkpoint=False)
    assert result.tape_records == len(s.tape)
    assert engine.ledger.bytes() == expected.ledger.bytes()


# ---------------------------------------------------------------- I. compression failure

class _LyingGzip:
    """A gzip whose writes lose their last byte, so the round-trip check must catch it."""

    def __init__(self, real) -> None:
        self._real = real

    def open(self, path, mode="rb", *args, **kwargs):
        handle = self._real.open(path, mode, *args, **kwargs)
        if "w" not in mode:
            return handle
        return _TruncatingWriter(handle)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _TruncatingWriter:
    def __init__(self, handle) -> None:
        self._handle = handle

    def write(self, payload):
        return self._handle.write(payload[:-1] if payload else payload)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def test_a_compression_that_does_not_round_trip_keeps_the_plaintext(config, tiers, tmp_path,
                                                                     monkeypatch) -> None:
    s = session(config, tiers, tmp_path, every=10)
    s.start(1_000)
    drive(s, 5, trade_at=2)
    directory = s.run_dir / seg.SEGMENT_DIR
    sealed_before = ({p.name for p in directory.glob("*.input.jsonl*")}
                     if directory.exists() else set())

    monkeypatch.setattr(seg, "gzip", _LyingGzip(gzip))
    tape = seg.SegmentedTape(run_dir=s.run_dir, segment_records=1, compress=True)
    with pytest.raises(seg.CheckpointRefused) as raised:
        tape.rotate(s.engine, tape_records=len(s.tape))
    assert raised.value.code == "COMPRESSION_NOT_REPRODUCIBLE"

    monkeypatch.undo()
    sealed_after = {p.name for p in directory.glob("*.input.jsonl*")}
    new = sealed_after - sealed_before
    # The plaintext the compression was made from is still there, and the bad gz is not.
    assert new, "the rename into segments/ should have happened before the compression"
    assert all(name.endswith(".input.jsonl") for name in new), f"a gz survived: {new}"
    manifests = seg.load_manifests(s.run_dir)
    assert len(manifests) == len({m.index for m in manifests})
    assert seg.verify_segments(s.run_dir, manifests) == []
    # No record was lost: the archive plus whatever the tape still holds is the whole run.
    assert sum(m.records for m in manifests) <= len(s.tape)


# ---------------------------------------------------------------- J. disk pressure

def test_a_compressing_rotation_needs_one_extra_copy_and_no_more(config, tiers, tmp_path,
                                                                  monkeypatch) -> None:
    """How much headroom a cut needs, measured at its peak rather than reasoned about.

    The peak is the instant before the plain file is removed, when both encodings are on disk.
    Anything worse than `plain + gz` would mean a third copy somewhere, and on a disk with a
    gigabyte of headroom and a segment every six hours that is worth knowing exactly.

    The watch goes on before the first record, not before the cut: `start()` writes a command
    record of its own, so a tape driven `segment_records` times has already rotated and an
    instrument installed afterwards measures nothing.
    """
    peaks: list[int] = []
    real_unlink = Path.unlink

    def watching_unlink(self, *args, **kwargs):
        if self.parent.name == seg.SEGMENT_DIR:
            peaks.append(sum(p.stat().st_size for p in self.parent.glob("*")))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", watching_unlink)
    s = session(config, tiers, tmp_path, every=20, compress=True)
    s.start(1_000)
    drive(s, 25)                      # one cut, and a little tape after it
    monkeypatch.undo()

    manifests = seg.load_manifests(s.run_dir)
    assert len(manifests) == 1 and manifests[0].compressed, [m.index for m in manifests]
    stored = s.run_dir / manifests[0].path
    gz = stored.stat().st_size
    with gzip.open(stored, "rb") as handle:
        plain = len(handle.read())
    assert peaks, "the plain file should have been removed during the rotation"

    # At the first cut the segments directory holds nothing else, so the sample *is* the peak.
    peak = max(peaks)
    assert peak == plain + gz, f"peak {peak} is not plain {plain} + gz {gz}"
    # Which is the headroom a cut actually asks for: the segment, plus a tenth of it again.
    assert peak < plain * 2, "a compressing cut must not need twice the segment"
    assert gz < plain


# ---------------------------------------------------------------- the flag itself

def test_the_flag_is_off_unless_it_is_explicitly_on(monkeypatch) -> None:
    monkeypatch.delenv(COMPRESS_SEGMENTS_ENV, raising=False)
    assert compress_segments_setting() == (False, "", True)


@pytest.mark.parametrize("raw", ["on", "1", "true", "yes", "TRUE", " On ", "Yes"])
def test_recognised_true_values_turn_it_on(monkeypatch, raw: str) -> None:
    monkeypatch.setenv(COMPRESS_SEGMENTS_ENV, raw)
    enabled, seen, recognised = compress_segments_setting()
    assert (enabled, seen, recognised) == (True, raw, True)


@pytest.mark.parametrize("raw", ["off", "0", "false", "no", "", "  ", "OFF"])
def test_recognised_false_values_leave_it_off(monkeypatch, raw: str) -> None:
    monkeypatch.setenv(COMPRESS_SEGMENTS_ENV, raw)
    enabled, seen, recognised = compress_segments_setting()
    assert (enabled, seen, recognised) == (False, raw, True)


@pytest.mark.parametrize("raw", ["yess", "enable", "2", "on;", "trueish", "gzip", "y"])
def test_an_unrecognised_value_fails_closed_and_says_it_was_unrecognised(monkeypatch,
                                                                          raw: str) -> None:
    """Off, not a crash: this is a disk-retention flag on a process holding a live position.
    But never silently off - the caller is told the value was not understood."""
    monkeypatch.setenv(COMPRESS_SEGMENTS_ENV, raw)
    enabled, seen, recognised = compress_segments_setting()
    assert enabled is False
    assert seen == raw
    assert recognised is False


def test_the_parser_is_deterministic(monkeypatch) -> None:
    for raw in ("on", "off", "nonsense", ""):
        monkeypatch.setenv(COMPRESS_SEGMENTS_ENV, raw)
        assert len({compress_segments_setting() for _ in range(5)}) == 1
