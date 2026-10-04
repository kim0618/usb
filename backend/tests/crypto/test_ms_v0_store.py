"""Buffering, rotation, the writer lock, restart recovery and the measurements.

`test_fsync_is_not_called_per_event` is the load-bearing one: at the measured ~9 depth frames per
second, an fsync per record is the difference between a collector and an IO incident. The
recovery tests pin the other half of that trade: a buffered writer loses the tail on a crash, so
recovery has to report what it removed and refuse to guess at interior damage.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from app.crypto.market_structure_v0 import store as ST
from app.crypto.market_structure_v0.contract import FINAL_SUFFIX, OPEN_SUFFIX
from app.crypto.market_structure_v0.store import (KindWriter, Store, StoreCorruption, StoreLocked,
                                                  canonical_line, recover_orphans)

SESSION = "abcdef12-1111-2222-3333-444444444444"
S = 1_000_000_000


def record(index: int, kind: str = "derived") -> dict:
    return {"kind": kind, "seq": index, "receive_ms": 1_700_000_000_000 + index,
            "mono_ns": index * S, "payload": {"sample_index": index}}


def store(tmp_path, **kwargs) -> Store:
    return Store.open(tmp_path / "data", SESSION, started_ns=0, **kwargs)


# --- writing and naming -----------------------------------------------------------------------

def test_records_are_canonical_sorted_json_lines():
    line = canonical_line({"b": 1, "a": 2})
    assert line == b'{"a":2,"b":1}\n'


def test_a_file_is_open_while_being_written_and_final_once_closed(tmp_path):
    instance = store(tmp_path)
    instance.write(record(1))
    open_files = list((tmp_path / "data").rglob(f"*{OPEN_SUFFIX}"))
    assert len(open_files) == 1
    assert open_files[0].parent.name == "derived"
    assert SESSION[:8] in open_files[0].name
    instance.close()
    assert list((tmp_path / "data").rglob(f"*{OPEN_SUFFIX}")) == []
    final = list((tmp_path / "data").rglob(f"*{FINAL_SUFFIX}"))
    assert len(final) == 1 and json.loads(final[0].read_text())["seq"] == 1


def test_each_kind_writes_into_its_own_directory(tmp_path):
    instance = store(tmp_path)
    for kind in ("derived", "raw_depth", "trade"):
        instance.write(record(1, kind))
    instance.close()
    assert sorted(path.name for path in (tmp_path / "data").iterdir() if path.is_dir()) == [
        "derived", "raw_depth", "trade"]


def test_an_unknown_kind_is_refused(tmp_path):
    instance = store(tmp_path)
    with pytest.raises(ValueError):
        instance.write({"kind": "signal", "receive_ms": 1, "mono_ns": 1})
    instance.close()


# --- buffering --------------------------------------------------------------------------------

def test_fsync_is_not_called_per_event(tmp_path, monkeypatch):
    instance = store(tmp_path)   # taking the lock fsyncs once, before the counter is installed
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    for index in range(500):
        instance.write(record(index), now_ns=index * 1_000_000, now_ms=index)
    assert calls == []
    instance.close()
    assert len(calls) <= 2  # the seal, plus the lock file release path


def test_the_buffer_flushes_once_a_second_and_fsyncs_every_ten(tmp_path):
    writer = KindWriter(root=tmp_path, kind="derived", session_id=SESSION)
    writer.write(record(1), now_ns=0, now_ms=0)
    assert writer.stats()["buffered_bytes"] > 0 and writer.stats()["flush_calls"] == 0
    writer.tick(1 * S, 0)
    assert writer.stats()["buffered_bytes"] == 0 and writer.stats()["flush_calls"] == 1
    assert writer.stats()["fsync_calls"] == 0
    writer.write(record(2), now_ns=1 * S, now_ms=0)
    writer.tick(11 * S, 0)
    assert writer.stats()["fsync_calls"] == 1
    writer.close()


def test_a_full_buffer_flushes_without_waiting_for_the_tick(tmp_path):
    writer = KindWriter(root=tmp_path, kind="derived", session_id=SESSION, buffer_bytes=200)
    for index in range(20):
        writer.write(record(index), now_ns=0, now_ms=0)
    assert writer.stats()["flush_calls"] >= 1
    assert writer.stats()["buffered_bytes"] < 200
    writer.close()


def test_the_buffer_never_exceeds_its_ceiling_by_more_than_one_record(tmp_path):
    """Bounded memory: the buffer is a ceiling, not a growth area."""
    writer = KindWriter(root=tmp_path, kind="derived", session_id=SESSION, buffer_bytes=512)
    peak = 0
    for index in range(2_000):
        writer.write(record(index), now_ns=index * 1_000, now_ms=0)
        peak = max(peak, writer.stats()["buffered_bytes"])
    assert peak < 512 + 200
    writer.close()


# --- rotation ---------------------------------------------------------------------------------

def test_rotation_by_size_seals_the_previous_file(tmp_path):
    writer = KindWriter(root=tmp_path, kind="derived", session_id=SESSION, rotate_bytes=300)
    for index in range(40):
        writer.write(record(index), now_ns=index * 1_000, now_ms=0)
    writer.close()
    files = sorted((tmp_path / "derived").glob(f"*{FINAL_SUFFIX}"))
    assert len(files) >= 2
    assert writer.stats()["rotations"] >= 1
    # Sequence numbers are distinct, so no file was overwritten.
    assert len({path.name for path in files}) == len(files)
    rows = sum(len(path.read_text().splitlines()) for path in files)
    assert rows == 40


def test_rotation_by_age_happens_on_the_tick_even_with_no_new_records(tmp_path):
    writer = KindWriter(root=tmp_path, kind="derived", session_id=SESSION, rotate_seconds=60)
    writer.write(record(1), now_ns=0, now_ms=0)
    writer.tick(61 * S, 0)
    assert writer.stats()["rotations"] == 1
    writer.close()
    assert len(list((tmp_path / "derived").glob(f"*{FINAL_SUFFIX}"))) == 2


def test_a_rotated_file_is_fsynced_before_being_sealed(tmp_path):
    writer = KindWriter(root=tmp_path, kind="derived", session_id=SESSION, rotate_bytes=200)
    for index in range(20):
        writer.write(record(index), now_ns=index * 1_000, now_ms=0)
    assert writer.stats()["fsync_calls"] >= 1
    writer.close()


# --- the writer lock --------------------------------------------------------------------------

def test_a_second_writer_on_the_same_root_is_refused(tmp_path):
    first = store(tmp_path)
    with pytest.raises(StoreLocked):
        store(tmp_path)
    first.close()
    second = store(tmp_path)  # released, so it is available again
    second.close()


def test_the_lock_records_who_is_holding_it(tmp_path):
    """The pid, the session and the start time, so a refused writer can name the holder."""
    instance = store(tmp_path)
    payload = json.loads((tmp_path / "data" / ".writer.lock").read_text())
    assert payload["pid"] == os.getpid()
    assert payload["session_id"] == instance.session_id
    assert payload["lock_version"] == ST.LOCK_VERSION
    assert isinstance(payload["started_ms"], int)
    instance.close()


# --- restart recovery -------------------------------------------------------------------------

def test_an_orphan_file_is_sealed_and_its_torn_tail_removed(tmp_path):
    root = tmp_path / "data"
    (root / "derived").mkdir(parents=True)
    orphan = root / "derived" / f"derived-20261004-abcdef12-00001{OPEN_SUFFIX}"
    orphan.write_bytes(b'{"a":1}\n{"b":2}\n{"c":')
    report = recover_orphans(root)
    assert report["files_recovered"] == 1 and report["rows_kept"] == 2
    assert report["bytes_removed"] == 5
    sealed = root / "derived" / f"derived-20261004-abcdef12-00001{FINAL_SUFFIX}"
    assert sealed.read_bytes() == b'{"a":1}\n{"b":2}\n'


def test_interior_corruption_fails_closed_rather_than_keeping_the_rest(tmp_path):
    root = tmp_path / "data"
    (root / "derived").mkdir(parents=True)
    orphan = root / "derived" / f"derived-20261004-abcdef12-00001{OPEN_SUFFIX}"
    orphan.write_bytes(b'{"a":1}\nNOT JSON\n{"c":3}\n')
    with pytest.raises(StoreCorruption):
        recover_orphans(root)
    assert orphan.exists()  # nothing was renamed or truncated


def test_recovery_never_overwrites_an_already_sealed_file(tmp_path):
    root = tmp_path / "data"
    (root / "derived").mkdir(parents=True)
    sealed = root / "derived" / f"derived-20261004-abcdef12-00001{FINAL_SUFFIX}"
    sealed.write_bytes(b'{"original":true}\n')
    orphan = root / "derived" / f"derived-20261004-abcdef12-00001{OPEN_SUFFIX}"
    orphan.write_bytes(b'{"recovered":true}\n')
    recover_orphans(root)
    assert sealed.read_bytes() == b'{"original":true}\n'
    assert (root / "derived" / f"derived-20261004-abcdef12-00001-r1{FINAL_SUFFIX}").exists()


def test_opening_a_store_recovers_orphans_and_reports_it_in_the_session_record(tmp_path):
    root = tmp_path / "data"
    (root / "derived").mkdir(parents=True)
    (root / "derived" / f"derived-20261004-abcdef12-00001{OPEN_SUFFIX}").write_bytes(
        b'{"a":1}\n{"torn"')
    instance = Store.open(root, SESSION, started_ns=0)
    assert instance.recovery["files_recovered"] == 1
    assert instance.recovery["bytes_removed"] == 7
    instance.close()


def test_a_complete_restart_keeps_the_valid_lines_and_starts_a_new_file(tmp_path):
    first = store(tmp_path)
    for index in range(3):
        first.write(record(index))
    # Simulate a crash: release the lock without sealing the file.
    first.lock.release()
    first.lock = None
    for writer in first.writers.values():
        writer._flush(force=True)
        writer._handle.close()
        writer._handle = None
    second = store(tmp_path)
    assert second.recovery["rows_kept"] == 3
    second.write(record(99))
    second.close()
    files = sorted((tmp_path / "data" / "derived").glob(f"*{FINAL_SUFFIX}"))
    # The recovered file is kept and the restart writes beside it, never over it.
    assert len(files) == 2
    assert sum(len(path.read_text().splitlines()) for path in files) == 4


# --- measurement ------------------------------------------------------------------------------

def test_stats_report_bytes_and_rows_by_kind_and_project_a_day(tmp_path):
    instance = Store.open(tmp_path / "data", SESSION, started_ns=0)
    for index in range(10):
        instance.write(record(index, "derived"))
    instance.write(record(1, "trade"))
    stats = instance.stats(now_ns=86_400 * S // 2)  # half a day elapsed
    assert stats["by_kind"]["derived"]["rows"] == 10
    assert stats["by_kind"]["trade"]["rows"] == 1
    assert stats["total_rows"] == 11
    assert stats["total_bytes"] == sum(
        item["bytes"] for item in stats["by_kind"].values())
    assert stats["projected_rows_per_day"] == 22
    assert stats["projected_bytes_per_day"] == stats["total_bytes"] * 2
    assert stats["projected_rows_per_day_by_kind"]["derived"] == 20
    instance.close()


def test_stats_expose_memory_latency_backlog_and_the_durability_cost(tmp_path):
    instance = store(tmp_path)
    # The whole test runs on an injected monotonic clock, so the write and the tick have to use
    # the same one; a bare write would stamp the file with the real clock instead.
    instance.write(record(1), now_ns=1 * S, now_ms=0)
    instance.tick(3 * S, 0)
    instance.observe_backlog(17)
    stats = instance.stats(now_ns=3 * S, queue_backlog=4)
    assert stats["max_rss_bytes"] > 0
    assert stats["by_kind"]["derived"]["flush_ms_max"] >= 0
    assert stats["by_kind"]["derived"]["flush_ms_mean"] is not None
    assert stats["queue_backlog"] == 4 and stats["queue_backlog_max"] == 17
    assert stats["fsync_interval_s"] == 10.0
    assert "power loss" in stats["durability_note"]
    instance.close()


def test_the_projection_uses_actual_serialized_bytes(tmp_path):
    instance = Store.open(tmp_path / "data", SESSION, started_ns=0)
    written = instance.write(record(1))
    assert written == len(canonical_line(record(1)))
    stats = instance.stats(now_ns=86_400 * S)
    assert stats["total_bytes"] == written
    assert stats["projected_bytes_per_day"] == written
    instance.close()
