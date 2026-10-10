"""Writer and storage safety of the Production Context Collector V1.

What is inherited from the V0 store is re-asserted here on the subclass rather than assumed: the
single-writer lock and its holder payload, failing closed when the lock file is replaced, orphan
recovery and its refusal of interior damage. What is new is asserted for the first time: the kind
filter, compression at seal, the latest-context file, and stopping on a storage failure.
"""
from __future__ import annotations

import asyncio
import errno
import gzip
import json
import os

import pytest

from app.crypto.context_collector_v1 import contract as K
from app.crypto.context_collector_v1 import store as CS
from app.crypto.context_collector_v1.collector import ContextRunner
from app.crypto.context_collector_v1.store import ContextStore, StorageFailed
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import (KindWriter, Store, StoreAuthorityLost,
                                                  StoreCorruption, StoreLocked, canonical_line)

from tests.crypto.ctx_v1_fixtures import Script, context_collector, flush


def record(session: Session, kind: str, payload: dict | None = None, *, at: int = 1) -> dict:
    return session.record(kind, payload or {"n": at}, receive_ms=1_791_600_000_000 + at,
                          mono_ns=at * 10**9)


def open_store(root, session=None, **kwargs) -> tuple[ContextStore, Session]:
    session = session or Session()
    return ContextStore.open(root, session.session_id, started_ns=0, **kwargs), session


# --------------------------------------------------------------------------- kind policy

def test_every_v0_kind_is_either_persisted_or_dropped_and_dropped_ones_get_no_file(tmp_path):
    store, session = open_store(tmp_path / "root", shadow_bytes=True)
    sizes = {}
    for kind in (*K.PERSISTED_V0_KINDS, *K.DROPPED_V0_KINDS, *K.CONTEXT_KINDS):
        rec = record(session, kind)
        sizes[kind] = len(canonical_line(rec))
        store.write(rec, now_ns=10**9, now_ms=rec["receive_ms"])
    store.close()
    dirs = {p.name for p in (tmp_path / "root").iterdir() if p.is_dir()}
    assert dirs == set(K.PERSISTED_V0_KINDS) | set(K.CONTEXT_KINDS)
    for kind in K.DROPPED_V0_KINDS:
        assert store.dropped[kind] == {"rows": 1, "bytes": sizes[kind]}
    policy = store.stats()["persistence_policy"]
    assert policy["dropped_rows"] == len(K.DROPPED_V0_KINDS)
    assert policy["shadow_dropped_bytes"] == sum(sizes[k] for k in K.DROPPED_V0_KINDS)


def test_without_shadow_mode_dropped_records_are_not_even_serialized(tmp_path, monkeypatch):
    store, session = open_store(tmp_path / "root")
    calls = []
    monkeypatch.setattr(CS, "canonical_line", lambda rec: calls.append(rec) or b"")
    store.write(record(session, "raw_depth"))
    assert calls == [] and store.dropped["raw_depth"] == {"rows": 1, "bytes": None}
    store.close()


def test_an_unknown_kind_is_refused(tmp_path):
    store, session = open_store(tmp_path / "root")
    with pytest.raises(ValueError, match="unknown record kind"):
        store.write(record(session, "orders"))
    with pytest.raises(ValueError, match="not persisted"):
        store._writer("raw_depth")
    store.close()


# --------------------------------------------------------------------------- compression

def test_a_sealed_own_kind_is_compressed_and_reads_back_identically(tmp_path):
    store, session = open_store(tmp_path / "root")
    rows = [record(session, K.CONTEXT_KIND, {"i": i}, at=i) for i in range(50)]
    for row in rows:
        store.write(row, now_ns=10**9, now_ms=row["receive_ms"])
    store.write(record(session, "session", {"event": "START"}), now_ns=10**9,
                now_ms=1_791_600_000_000)
    store.close()
    files = list((tmp_path / "root" / K.CONTEXT_KIND).iterdir())
    assert len(files) == 1 and files[0].name.endswith(K.COMPRESSED_SUFFIX)
    lines = gzip.decompress(files[0].read_bytes()).splitlines()
    assert [json.loads(line) for line in lines] == rows
    writer = store.writers[K.CONTEXT_KIND]
    assert writer.compressed_files == 1 and writer.compress_failures == 0
    assert writer.compressed_bytes_out < writer.compressed_bytes_in
    # V0 kinds stay plain so the frozen viewer can read them.
    assert all(p.name.endswith(".jsonl") for p in (tmp_path / "root" / "session").iterdir())


def test_a_failed_compression_leaves_the_plain_file_and_says_so(tmp_path, monkeypatch):
    store, session = open_store(tmp_path / "root")
    store.write(record(session, K.WALL_V2_KIND))

    def broken(*_, **__):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(CS.gzip, "GzipFile", broken)
    store.close()
    names = [p.name for p in (tmp_path / "root" / K.WALL_V2_KIND).iterdir()]
    assert names == [n for n in names if n.endswith(".jsonl")] and len(names) == 1
    assert store.writers[K.WALL_V2_KIND].compress_failures == 1
    assert "No space left" in store.writers[K.WALL_V2_KIND].compress_last_error


# --------------------------------------------------------------------------- single writer

def test_a_second_writer_is_refused_and_told_who_holds_the_root(tmp_path):
    store, session = open_store(tmp_path / "root")
    with pytest.raises(StoreLocked) as refused:
        open_store(tmp_path / "root")
    assert str(os.getpid()) in str(refused.value)
    assert session.session_id in str(refused.value)
    # A research collector cannot take a context root either: it is the same lock file.
    with pytest.raises(StoreLocked):
        Store.open(tmp_path / "root", Session().session_id, started_ns=0)
    store.close()
    again, _ = open_store(tmp_path / "root")       # released on close
    again.close()


def test_a_replaced_lock_file_stops_the_writer_and_its_latest_file(tmp_path):
    store, session = open_store(tmp_path / "root")
    assert store.write_latest({"schema": "x"}) > 0
    (tmp_path / "root" / ".writer.lock").unlink()
    assert store.write_latest({"schema": "x"}) == 0
    assert store.latest_writes_failed == 1
    with pytest.raises(StoreAuthorityLost):
        store.tick()
    store.close()


# --------------------------------------------------------------------------- restart and damage

def crash(store: ContextStore) -> None:
    """What a killed process leaves: buffers flushed to `.open` files, nothing sealed, lock gone."""
    for writer in store.writers.values():
        writer._flush(force=True)
        writer._handle.close()
        writer._handle = None
    store.lock.release()
    store.lock = None


def test_a_crashed_session_is_recovered_compressed_and_its_torn_line_dropped(tmp_path):
    store, session = open_store(tmp_path / "root")
    rows = [record(session, K.CONTEXT_KIND, {"i": i}, at=i) for i in range(5)]
    for row in rows:
        store.write(row)
    crash(store)
    open_file = next((tmp_path / "root" / K.CONTEXT_KIND).glob("*.open"))
    with open(open_file, "ab") as handle:
        handle.write(b'{"torn": ')
    again, _ = open_store(tmp_path / "root")
    assert again.recovery["files_recovered"] == 1
    assert again.recovery["bytes_removed"] == len(b'{"torn": ')
    assert again.compaction["compressed"] == 1
    packed = next((tmp_path / "root" / K.CONTEXT_KIND).glob(f"*{K.COMPRESSED_SUFFIX}"))
    assert [json.loads(l) for l in gzip.decompress(packed.read_bytes()).splitlines()] == rows
    again.close()


def test_interior_damage_refuses_to_start_and_releases_the_lock(tmp_path):
    store, session = open_store(tmp_path / "root")
    for i in range(3):
        store.write(record(session, K.CONTEXT_KIND, at=i))
    crash(store)
    open_file = next((tmp_path / "root" / K.CONTEXT_KIND).glob("*.open"))
    lines = open_file.read_bytes().splitlines(keepends=True)
    open_file.write_bytes(lines[0] + b"not json\n" + lines[2])
    with pytest.raises(StoreCorruption):
        open_store(tmp_path / "root")
    open_file.unlink()
    healthy, _ = open_store(tmp_path / "root")     # the refused open did not keep the lock
    healthy.close()


def test_a_stray_compression_temporary_is_removed_at_open(tmp_path):
    (tmp_path / "root" / K.CONTEXT_KIND).mkdir(parents=True)
    stray = tmp_path / "root" / K.CONTEXT_KIND / f"context-x.jsonl.gz{CS.COMPRESS_TMP_SUFFIX[3:]}"
    stray = stray.with_name("context-20261010-aaaaaaaa-00001.jsonl" + CS.COMPRESS_TMP_SUFFIX)
    stray.write_bytes(b"half")
    store, _ = open_store(tmp_path / "root")
    assert not stray.exists() and store.compaction["temporaries_removed"] == 1
    store.close()


def test_a_corrupted_latest_file_is_simply_replaced(tmp_path):
    store, _ = open_store(tmp_path / "root")
    store.latest_path().parent.mkdir(parents=True, exist_ok=True)
    store.latest_path().write_bytes(b"\x00garbage")
    assert store.write_latest({"schema": "ok"}) > 0
    assert json.loads(store.latest_path().read_text()) == {"schema": "ok"}
    assert not list(store.latest_path().parent.glob("*.tmp"))
    store.close()


# --------------------------------------------------------------------------- disk failure

def test_a_failing_flush_raises_storage_failed_rather_than_dying_quietly(tmp_path, monkeypatch):
    store, session = open_store(tmp_path / "root")
    store.write(record(session, K.CONTEXT_KIND), now_ns=10**9, now_ms=1_791_600_001_000)

    def full(self, *, force=False):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(KindWriter, "_flush", full)
    with pytest.raises(StorageFailed, match="No space left"):
        store.tick(now_ns=5 * 10**9, now_ms=1_791_600_005_000)
    assert "ENOSPC" in store.stats()["storage_error"] or "No space" in store.stats()["storage_error"]
    monkeypatch.undo()
    store.close()


def test_a_failing_latest_write_is_counted_and_never_raised(tmp_path):
    store, _ = open_store(tmp_path / "root")
    state_dir = tmp_path / "root" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    store.latest_path().mkdir()              # a directory where the file should be
    assert store.write_latest({"schema": "x"}) == 0
    assert store.latest_writes_failed == 1 and store.latest_last_error
    store.close()


def test_the_runner_stops_with_the_storage_error_instead_of_a_silent_dead_sampler(tmp_path):
    ctx, _ = context_collector(tmp_path)
    script = Script([ctx])
    script.start()

    def failing_tick(*_, **__):
        raise StorageFailed("OSError: [Errno 28] No space left on device")

    ctx.store.tick = failing_tick
    runner = ContextRunner(collector=ctx, duration_s=0, sample_interval_s=0.01,
                           stats_interval_s=3600)

    async def run_sampler():
        runner._stop = asyncio.Event()
        await asyncio.wait_for(runner._sampler(), timeout=5)
        return runner._stop.is_set()

    assert asyncio.run(run_sampler()) is True
    assert runner._stop_reason.startswith("storage_error")
    assert runner.storage_stops == 1
    ctx.store.close()
