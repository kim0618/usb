"""The collector end to end: record sequences, fault handling and the asyncio wiring.

The synchronous tests drive `Collector` directly, so a gap, a reconnect, a stale book and a
duplicate trade each produce an asserted sequence of records with no network and no sleeping. The
async tests use fake sockets and exist for the two orderings only the runner can get wrong:
buffering has to be running before the snapshot request goes out, and the queues have to be
bounded.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0 import collector as C
from app.crypto.market_structure_v0 import flow as F
from app.crypto.market_structure_v0 import walls as W
from app.crypto.market_structure_v0.contract import (COMPLETE, DEPTH_QUEUE_MAX, PARTIAL, UNKNOWN,
                                                     contract_identity)
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store

from tests.crypto.ms_v0_fixtures import (S, agg_trade, collector, depth_frame, snapshot,
                                         wide_snapshot)


def sync_depth(instance: C.Collector, *, receive_ms: int = 5_000, mono_ns: int = 0,
               payload: dict | None = None) -> None:
    """Connect, buffer nothing, install a snapshot: the shortest path to a usable book."""
    instance.on_depth_connect("c1", receive_ms=receive_ms - 1, mono_ns=mono_ns)
    instance.mark_snapshot_requested(receive_ms=receive_ms - 1, mono_ns=mono_ns)
    instance.on_snapshot(payload or snapshot(), receive_ms=receive_ms, mono_ns=mono_ns,
                         request_ms=receive_ms - 1, request_mono_ns=mono_ns)


# --- session record ---------------------------------------------------------------------------

def test_the_session_record_states_the_contract_hash_and_the_read_only_reach(tmp_path):
    instance, sink = collector(tmp_path)
    instance.write_session_record(config={"duration_s": 60})
    payload = sink.last("session")
    assert payload["event"] == "START"
    assert payload["contract"]["contract_sha256"] == contract_identity()["contract_sha256"]
    assert payload["contract"]["sha256_agrees"] is True
    assert payload["safety"]["order_capability"] is False
    assert payload["safety"]["credentials_read"] is False
    assert payload["safety"]["mode"] == "READ_ONLY_PUBLIC_MARKET_DATA"
    instance.store.close()


def test_every_record_carries_the_envelope_with_a_strictly_increasing_sequence(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.sample(at_ns=S, at_ms=6_000)
    sequences = [record["seq"] for record in sink.records]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    for record in sink.records:
        assert record["exchange"] == "binance_usdm" and record["symbol"] == "BTCUSDT"
        assert record["version"] == "btc-ms.v0.1"
        assert record["session_id"] == instance.session.session_id
        assert isinstance(record["receive_ms"], int) and isinstance(record["mono_ns"], int)
        assert set(record) >= {"kind", "payload", "connection_id"}
    instance.store.close()


# --- depth: snapshot, deltas, gap, resync -----------------------------------------------------

def test_raw_frames_are_recorded_before_interpretation(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    message = depth_frame(first=900, last=1100, previous=850, bids=[["84750", "5"]])
    instance.on_depth_frame(message, receive_ms=6_000, mono_ns=S)
    assert sink.of("raw_depth") == [message]
    assert instance.depth.bids[Decimal("84750")] == Decimal("5")
    instance.store.close()


def test_a_discarded_frame_is_still_recorded_as_raw(tmp_path):
    """Raw plus snapshots plus the envelope sequence are the replay authority, so nothing is
    dropped from raw just because the book did not use it."""
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.on_depth_frame(depth_frame(first=1, last=999, previous=0), receive_ms=6_000,
                            mono_ns=S)
    assert len(sink.of("raw_depth")) == 1
    assert instance.depth.discarded_old == 1
    instance.store.close()


def test_a_snapshot_is_stored_with_its_request_and_receive_times_and_a_checkpoint(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_depth_connect("c1", receive_ms=4_000, mono_ns=0)
    instance.mark_snapshot_requested(receive_ms=4_100, mono_ns=100_000_000)
    instance.on_snapshot(snapshot(), receive_ms=4_300, mono_ns=300_000_000,
                         request_ms=4_100, request_mono_ns=100_000_000)
    stored = sink.last("snapshot")
    assert stored["request_ms"] == 4_100 and stored["receive_ms"] == 4_300
    assert stored["round_trip_ms"] == 200
    assert stored["response"]["lastUpdateId"] == 1000
    assert stored["limit"] == 1000
    checkpoint = sink.last("checkpoint")
    assert checkpoint["last_update_id"] == 1000 and checkpoint["generation"] == 1
    assert checkpoint["known_low"] == "84630" and checkpoint["best_bid"] == "84750"
    assert len(checkpoint["bids"]) == 13
    instance.store.close()


def test_a_gap_reports_the_offending_ids_and_asks_for_a_new_snapshot(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.on_depth_frame(depth_frame(first=900, last=1100, previous=850), receive_ms=6_000,
                            mono_ns=S)
    assert instance.on_depth_frame(depth_frame(first=1200, last=1300, previous=1099),
                                   receive_ms=6_100, mono_ns=2 * S) == B.GAP
    event = sink.telemetry(C.GAP)[-1]
    assert event["reason"] == B.GAP_PU_MISMATCH
    assert event["previous_update_id"] == 1099 and event["last_update_id"] == 1300
    assert instance.snapshot_wanted is True
    instance.store.close()


def test_a_gap_then_a_resync_restores_a_complete_book(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.on_depth_frame(depth_frame(first=1200, last=1300, previous=1150), receive_ms=6_000,
                            mono_ns=S)
    assert instance.depth.state == B.UNSYNCED
    instance.mark_snapshot_requested(receive_ms=6_100, mono_ns=2 * S)
    instance.on_snapshot(snapshot(last_update_id=2_000), receive_ms=6_200, mono_ns=2 * S,
                         request_ms=6_100, request_mono_ns=2 * S)
    assert instance.depth.state == B.SYNCED and instance.depth.generation == 2
    assert sink.telemetry(C.RESYNC)[-1]["generation"] == 2
    view = instance.sample(at_ns=2 * S, at_ms=6_300)
    assert view["book"]["bands"][0]["bid"]["coverage"] == COMPLETE
    instance.store.close()


def test_frames_arriving_before_the_snapshot_are_buffered_and_replayed(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_depth_connect("c1", receive_ms=4_000, mono_ns=0)
    assert instance.on_depth_frame(depth_frame(first=900, last=1100, previous=850,
                                               bids=[["84750", "6"]]),
                                   receive_ms=4_100, mono_ns=S) == B.BUFFERED
    instance.mark_snapshot_requested(receive_ms=4_200, mono_ns=S)
    instance.on_snapshot(snapshot(), receive_ms=4_300, mono_ns=S, request_ms=4_200,
                         request_mono_ns=S)
    assert sink.last("snapshot")["buffered_frames"] == 1
    assert instance.depth.bids[Decimal("84750")] == Decimal("6")
    assert instance.depth.last_update_id == 1100
    instance.store.close()


def test_a_failed_snapshot_is_recorded_and_retried(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_depth_connect("c1", receive_ms=4_000, mono_ns=0)
    instance.mark_snapshot_requested(receive_ms=4_100, mono_ns=0)
    assert instance.snapshot_wanted is False
    instance.on_snapshot_failed("HTTPStatusError: 418", receive_ms=4_200, mono_ns=S)
    assert instance.snapshot_wanted is True and instance.snapshot_in_flight is False
    assert sink.telemetry(C.SNAPSHOT_FAILED)[-1]["reason"] == "HTTPStatusError: 418"
    instance.store.close()


def test_a_malformed_depth_frame_is_counted_and_does_not_break_the_book(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    assert instance.on_depth_frame({"e": "depthUpdate", "b": []}, receive_ms=6_000,
                                   mono_ns=S) == "MALFORMED"
    assert instance.depth.state == B.SYNCED
    assert instance.depth_stream.malformed == 1
    assert sink.telemetry("malformed")
    instance.store.close()


# --- reconnect --------------------------------------------------------------------------------

def test_a_depth_reconnect_invalidates_the_book_and_ends_wall_continuity(tmp_path):
    payload = wide_snapshot()
    payload["bids"][4][1] = "50"
    instance, sink = collector(tmp_path)
    sync_depth(instance, payload=payload)
    instance.sample(at_ns=0, at_ms=5_000)
    assert sink.of("wall")[-1]["event"] == W.OPENED

    instance.on_depth_connect("c2", receive_ms=7_000, mono_ns=2 * S)
    assert instance.depth.state == B.UNSYNCED
    assert instance.depth.last_invalidation == B.RECONNECT
    assert instance.depth_stream.reconnects == 1
    # The candidate did not end, we stopped being able to see it.
    assert sink.of("wall")[-1]["status"] == W.INTERRUPTED
    assert sink.telemetry(C.CONNECT)[-1]["reconnect"] is True
    assert instance.snapshot_wanted is True
    instance.store.close()


def test_a_depth_disconnect_is_recorded_with_its_reason(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.on_depth_disconnect("ConnectionClosedError: 1006", receive_ms=7_000, mono_ns=2 * S)
    assert instance.depth_stream.connected is False
    assert sink.telemetry(C.DISCONNECT)[-1]["reason"] == "ConnectionClosedError: 1006"
    assert instance.depth.state == B.UNSYNCED
    instance.store.close()


def test_a_trade_reconnect_restarts_flow_coverage(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_trade_connect("t1", receive_ms=1_000, mono_ns=0)
    instance.on_trade_frame(agg_trade(trade_id=1), receive_ms=1_100, mono_ns=S)
    view = instance.sample(at_ns=90 * S, at_ms=90_000)
    assert view["flow"]["60s"]["coverage"] == UNKNOWN  # stale by then

    instance.on_trade_connect("t2", receive_ms=91_000, mono_ns=91 * S)
    assert instance.trade_stream.reconnects == 1
    assert instance.flow.coverage_reason == F.INTERRUPTION_RECONNECT
    view = instance.sample(at_ns=92 * S, at_ms=92_000)
    assert view["flow"]["5s"]["coverage"] == PARTIAL
    instance.store.close()


# --- staleness --------------------------------------------------------------------------------

def test_a_stale_book_is_invalidated_and_resnapshotted(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance, receive_ms=5_000, mono_ns=0)
    view = instance.sample(at_ns=3 * S, at_ms=8_000)
    event = sink.telemetry(C.STALE)[-1]
    assert event["stream"] == C.DEPTH_STREAM and event["age_ms"] == 3_000
    assert event["threshold_ms"] == 2_000
    assert instance.depth.state == B.UNSYNCED
    assert instance.depth.last_invalidation == B.STALE
    assert instance.snapshot_wanted is True
    # The sample taken in the same breath reports UNKNOWN rather than the last known numbers.
    assert view["book"]["bands"][0]["bid"]["coverage"] == UNKNOWN
    instance.store.close()


def test_a_fresh_book_is_not_declared_stale(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance, receive_ms=5_000, mono_ns=0)
    instance.sample(at_ns=S, at_ms=6_000)
    assert sink.telemetry(C.STALE) == []
    assert instance.depth.state == B.SYNCED
    instance.store.close()


def test_a_stale_trade_stream_interrupts_flow_coverage_once(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_trade_connect("t1", receive_ms=1_000, mono_ns=0)
    instance.on_trade_frame(agg_trade(trade_id=1), receive_ms=1_000, mono_ns=0)
    instance.sample(at_ns=10 * S, at_ms=11_000)
    instance.sample(at_ns=11 * S, at_ms=12_000)
    assert len(sink.telemetry(C.STALE)) == 1
    assert instance.trade_stream.stale_events == 1
    assert instance.flow.coverage_reason == F.INTERRUPTION_STALE
    instance.store.close()


# --- trades -----------------------------------------------------------------------------------

def test_a_trade_produces_a_raw_record_and_a_normalized_record(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_trade_connect("t1", receive_ms=1_000, mono_ns=0)
    message = agg_trade(trade_id=5, qty="0.5", buyer_is_maker=True)
    instance.on_trade_frame(message, receive_ms=1_100, mono_ns=S)
    assert sink.of("raw_trade") == [message]
    normalized = sink.last("trade")
    assert normalized["trade_id"] == 5 and normalized["aggressor"] == "SELL"
    assert normalized["qty"] == "0.5"
    instance.store.close()


def test_a_duplicate_trade_is_recorded_once_and_reported(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_trade_connect("t1", receive_ms=1_000, mono_ns=0)
    instance.on_trade_frame(agg_trade(trade_id=5), receive_ms=1_100, mono_ns=S)
    instance.on_trade_frame(agg_trade(trade_id=5), receive_ms=1_200, mono_ns=2 * S)
    assert len(sink.of("trade")) == 1          # counted once
    assert len(sink.of("raw_trade")) == 2      # both frames survive in raw
    assert sink.telemetry(C.DUPLICATE)[-1]["trade_id"] == 5
    assert len(instance.flow.tape) == 1
    instance.store.close()


def test_an_id_jump_keeps_the_volume_and_downgrades_coverage(tmp_path):
    instance, sink = collector(tmp_path)
    instance.on_trade_connect("t1", receive_ms=1_000, mono_ns=0)
    instance.on_trade_frame(agg_trade(trade_id=1), receive_ms=1_000, mono_ns=0)
    instance.on_trade_frame(agg_trade(trade_id=9, qty="2"), receive_ms=1_100, mono_ns=S)
    assert sink.telemetry(C.ID_JUMP)[-1]["trade_id"] == 9
    assert len(sink.of("trade")) == 2
    view = instance.sample(at_ns=2 * S, at_ms=1_200)
    assert view["flow"]["5s"]["coverage"] == PARTIAL
    assert view["flow"]["5s"]["coverage_reason"] == F.INTERRUPTION_ID_JUMP
    assert view["flow"]["5s"]["observed_buy_btc"] == "3"
    instance.store.close()


# --- sampling and stats -----------------------------------------------------------------------

def test_a_sample_carries_the_book_the_flow_and_the_coverage_caveat(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.on_trade_connect("t1", receive_ms=5_000, mono_ns=0)
    view = instance.sample(at_ns=S, at_ms=6_000)
    assert view["sample_index"] == 1
    assert set(view["flow"]) == {"5s", "15s", "60s"}
    assert view["book"]["source_u"] == 1000
    assert "not a guarantee" in view["coverage_note"]
    assert sink.kinds().count("derived") == 1
    instance.store.close()


def test_storage_stats_expose_every_counter_the_trial_is_judged_on(tmp_path):
    # Written straight to the store, because the byte counters only exist once bytes exist.
    session = Session()
    store = Store.open(tmp_path / "data", session.session_id, started_ns=0)
    instance = C.Collector(store=store, session=session)
    sync_depth(instance)
    instance.sample(at_ns=S, at_ms=6_000)
    stats = instance.emit_stats(at_ns=2 * S, at_ms=7_000, queue_backlog=11)
    assert stats["projected_bytes_per_day"] > 0 and stats["projected_rows_per_day"] > 0
    assert stats["queue_backlog"] == 11
    assert stats["max_rss_bytes"] > 0
    assert stats["depth_stream"]["connects"] == 1
    assert stats["book"]["state"] == B.SYNCED
    assert set(stats["tape"]) >= {"duplicates", "id_jumps"}
    assert set(stats["flow"]) >= {"retained_records", "interruptions"}
    assert set(stats["walls"]) >= {"active", "opened", "ended"}
    instance.store.close()


def test_finish_closes_candidates_and_writes_an_end_record(tmp_path):
    payload = wide_snapshot()
    payload["bids"][4][1] = "50"
    instance, sink = collector(tmp_path)
    sync_depth(instance, payload=payload)
    instance.sample(at_ns=0, at_ms=5_000)
    instance.finish("duration", at_ns=S, at_ms=6_000)
    assert sink.of("wall")[-1]["status"] == W.INTERRUPTED
    end = sink.of("session")[-1]
    assert end["event"] == "END" and end["reason"] == "duration"
    assert end["samples"] == 1
    assert sink.telemetry(C.SHUTDOWN)
    instance.store.close()


def test_records_land_on_disk_under_their_kind(tmp_path):
    session = Session()
    store = Store.open(tmp_path / "data", session.session_id, started_ns=session.started_ns)
    instance = C.Collector(store=store, session=session)
    sync_depth(instance)
    instance.sample(at_ns=S, at_ms=6_000)
    store.close()
    for kind in ("snapshot", "checkpoint", "derived", "telemetry"):
        files = list((tmp_path / "data" / kind).glob("*.jsonl"))
        assert files, kind
        rows = [json.loads(line) for line in files[0].read_text().splitlines()]
        assert rows and all(row["kind"] == kind for row in rows)


# --- the asyncio runner -----------------------------------------------------------------------

class FakeSocket:
    """Delivers the given frames, then goes quiet like a real stream in a calm market."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)
        self.closed = False

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(3_600)
        raise AssertionError("unreachable")

    async def __aenter__(self) -> "FakeSocket":
        return self

    async def __aexit__(self, *_: object) -> bool:
        self.closed = True
        return False


def runner_for(tmp_path, depth_frames: list[dict], trade_frames: list[dict], *,
               snapshot_delay: float = 0.05, duration: float = 0.5):
    session = Session()
    store = Store.open(tmp_path / "data", session.session_id, started_ns=session.started_ns)
    sink_records: list[dict] = []
    instance = C.Collector(store=store, session=session)

    async def fetch() -> dict:
        await asyncio.sleep(snapshot_delay)
        return snapshot()

    runner = C.Runner(
        collector=instance, duration_s=duration, sample_interval_s=0.05, stats_interval_s=0.2,
        depth_connector=lambda url: FakeSocket([json.dumps(f) for f in depth_frames]),
        trade_connector=lambda url: FakeSocket([json.dumps(f) for f in trade_frames]),
        snapshot_fetcher=fetch)
    return runner, instance, sink_records


async def test_the_runner_buffers_depth_frames_before_requesting_the_snapshot(tmp_path):
    frames = [depth_frame(first=900, last=1100, previous=850, bids=[["84750", "8"]])]
    runner, instance, _ = runner_for(tmp_path, frames, [agg_trade(trade_id=1)])
    await runner.run()
    root = tmp_path / "data"
    telemetry = [json.loads(line)["payload"] for path in (root / "telemetry").glob("*.jsonl")
                 for line in path.read_text().splitlines()]
    events = [row["event"] for row in telemetry]
    assert events.index(C.CONNECT) < events.index(C.SNAPSHOT_REQUEST)
    stored = [json.loads(line)["payload"] for path in (root / "snapshot").glob("*.jsonl")
              for line in path.read_text().splitlines()]
    # The frame arrived while the REST read was in flight and was replayed behind it.
    assert stored[0]["buffered_frames"] == 1
    assert instance.depth.bids[Decimal("84750")] == Decimal("8")


async def test_the_runner_writes_a_session_start_and_end_and_seals_its_files(tmp_path):
    runner, instance, _ = runner_for(tmp_path, [], [], duration=0.3)
    stats = await runner.run()
    assert stats["total_rows"] > 0
    assert list((tmp_path / "data").rglob("*.jsonl.open")) == []
    sessions = [json.loads(line)["payload"] for path in (tmp_path / "data" / "session").glob("*.jsonl")
                for line in path.read_text().splitlines()]
    assert [row["event"] for row in sessions] == ["START", "END"]
    assert sessions[1]["reason"] == "duration"


async def test_the_runner_samples_once_per_interval_and_emits_stats(tmp_path):
    runner, instance, _ = runner_for(tmp_path, [], [], duration=0.5)
    await runner.run()
    assert instance.samples >= 5
    assert instance.stats_emitted >= 2


async def test_a_full_persistence_queue_drops_explicitly_and_counts_it(tmp_path):
    runner, instance, _ = runner_for(tmp_path, [], [], duration=0.1)
    runner._persist_queue = asyncio.Queue(maxsize=1)
    runner._stop = asyncio.Event()
    instance.sink = runner._sink
    for index in range(10):
        instance.telemetry("test", receive_ms=index, mono_ns=index)
    assert instance.store.dropped_records == 9
    assert instance.store.queue_backlog_max == 1
    instance.store.close()


def test_the_depth_frame_queue_bound_comes_from_the_contract():
    assert DEPTH_QUEUE_MAX == 2_048
    assert C.PERSIST_QUEUE_MAX == 8_192


async def test_a_depth_queue_overflow_invalidates_the_book_rather_than_claiming_complete(tmp_path):
    instance, sink = collector(tmp_path)
    sync_depth(instance)
    instance.depth.invalidate(B.QUEUE_OVERFLOW)
    instance.snapshot_wanted = True
    instance.telemetry(C.OVERFLOW, stream=C.DEPTH_STREAM, receive_ms=1, mono_ns=1,
                       what="depth_frame_queue", limit=DEPTH_QUEUE_MAX)
    view = instance.sample(at_ns=S, at_ms=6_000)
    assert view["book"]["state"] == B.UNSYNCED
    assert view["book"]["last_invalidation"] == B.QUEUE_OVERFLOW
    assert all(band["bid"]["coverage"] == UNKNOWN for band in view["book"]["bands"])
    instance.store.close()


async def test_shutdown_is_bounded_so_the_files_are_always_sealed(tmp_path):
    """A socket that refuses to close must not cost the run its data."""
    session = Session()
    store = Store.open(tmp_path / "data", session.session_id, started_ns=session.started_ns)
    instance = C.Collector(store=store, session=session)

    class Unclosable(FakeSocket):
        async def __aexit__(self, *_: object) -> bool:
            await asyncio.sleep(3_600)
            return False

    runner = C.Runner(collector=instance, duration_s=0.1, sample_interval_s=0.05,
                      stats_interval_s=1.0,
                      depth_connector=lambda url: Unclosable([]),
                      trade_connector=lambda url: Unclosable([]),
                      snapshot_fetcher=None)
    import app.crypto.market_structure_v0.collector as module
    original = module.SHUTDOWN_TIMEOUT_S
    module.SHUTDOWN_TIMEOUT_S = 0.2
    try:
        stats = await runner.run()
    finally:
        module.SHUTDOWN_TIMEOUT_S = original
    assert stats["total_rows"] > 0
    # Sealed despite the socket, and the abandonment is on the record.
    assert list((tmp_path / "data").rglob("*.jsonl.open")) == []
    telemetry = [json.loads(line)["payload"]
                 for path in (tmp_path / "data" / "telemetry").glob("*.jsonl")
                 for line in path.read_text().splitlines()]
    assert any(row.get("stage") == "workers" for row in telemetry)
