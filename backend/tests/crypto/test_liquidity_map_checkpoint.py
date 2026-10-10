"""Reading the collector's compact state file, and refusing it when it cannot be trusted.

The file is what makes a poll cost the same at hour twelve as at minute one, so the tests that
matter are the ones about *not* using it: a file from a previous collector, a file nobody is
updating any more, a file whose shape the reader does not know, and a file that claims to be the
authority the journal is.
"""
from __future__ import annotations

import json
import time

import pytest

from app.crypto.liquidity_map import checkpoint as CP
from app.crypto.liquidity_map import journal as J
from app.crypto.liquidity_map.wallstate import WallFollower
from app.crypto.market_structure_v0 import store as STORE

from tests.crypto.liquidity_map_fixtures import Handwriter, written_journal

SESSION = "aaaaaaaa-0000-0000-0000-000000000000"


def write_state(root, *, session_id=SESSION, written_ms=1_000, items=(), ended=False,
                version="ms-v0-state.v1-1", is_authority=False, seq=99, truncated=False,
                active=None):
    directory = root / CP.STATE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "state_version": version,
        "is_authority": is_authority,
        "written_ms": written_ms,
        "session": {"session_id": session_id, "started_ms": 500, "seq": seq,
                    "sample_index": 3, "ended": ended},
        "collector": {"collector_version": "btc-ms.collector.0.1", "exchange": "binance_usdm",
                      "symbol": "BTCUSDT"},
        "derived": {"book": {"state": "SYNCED", "mid": "84750"}, "flow": {}},
        "freshness": {"depth_age_ms": 100},
        "coverage": {"known_low": "84620", "known_high": "84880"},
        "resnapshot": {"policy": "FAULT_IMMEDIATE_PLUS_COVERAGE_EDGE_PLUS_HOURLY_SAFETY",
                       "coverage_margin_bps": "4.44"},
        "walls": {"counters": {"active": len(items) if active is None else active},
                  "limit": 2_000, "truncated": truncated, "items": list(items)},
        "telemetry_recent": [{"seq": 5, "event": "resync", "stream": "depth", "reason": None}],
    }
    (directory / CP.STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    return payload


def wall_item(side="ASK", price="84800"):
    return {"side": side, "price": price, "qty": "10", "notional": "848000", "multiple": "9",
            "local_average": "1", "neighbours": 10, "first_seen_ms": 0,
            "persistence_ms": 60_000, "samples": 60, "status": "ACTIVE", "event": "UPDATED",
            "coverage": "COMPLETE", "generation": 1}


# --- the path the collector writes to ---------------------------------------------------------

def test_the_reader_and_the_writer_agree_on_where_the_file_is():
    """A silent disagreement here is an empty screen with no error anywhere."""
    assert CP.STATE_DIRNAME == STORE.STATE_DIRNAME
    assert CP.STATE_FILENAME == STORE.STATE_FILENAME


# --- the happy path ---------------------------------------------------------------------------

def test_a_fresh_file_for_this_session_is_usable(tmp_path):
    write_state(tmp_path, written_ms=10_000, items=[wall_item()])
    state = CP.read(tmp_path, now_ms=10_200, session_id=SESSION)
    assert state.usable is True and state.reason is None
    assert state.age_ms == 200 and state.seq == 99
    assert state.active_count == 1 and len(state.wall_items) == 1
    assert state.derived["book"]["mid"] == "84750"
    assert state.resnapshot["coverage_margin_bps"] == "4.44"
    assert state.telemetry_recent[0]["event"] == "resync"


def test_the_view_says_the_journal_is_still_the_authority(tmp_path):
    write_state(tmp_path, written_ms=10_000)
    view = CP.read(tmp_path, now_ms=10_100, session_id=SESSION).view()
    assert view["is_authority"] is False and view["authority"] == "JOURNAL"


# --- the refusals -----------------------------------------------------------------------------

def test_no_file_at_all_is_absent_rather_than_an_error(tmp_path):
    state = CP.read(tmp_path, now_ms=1_000)
    assert state.present is False and state.usable is False
    assert state.reason == CP.MISSING


def test_a_file_from_a_previous_collector_is_refused(tmp_path):
    """Candidate observation never crosses a session boundary, so neither may this."""
    write_state(tmp_path, session_id="bbbbbbbb-0000-0000-0000-000000000000", written_ms=10_000)
    state = CP.read(tmp_path, now_ms=10_100, session_id=SESSION)
    assert state.usable is False and state.reason == CP.OTHER_SESSION


def test_a_file_nobody_is_updating_is_stale(tmp_path):
    """This is the V1 defect: a collector dead for 26 seconds read as a live trade stream."""
    write_state(tmp_path, written_ms=10_000)
    state = CP.read(tmp_path, now_ms=10_000 + CP.STALE_MS + 1, session_id=SESSION)
    assert state.usable is False and state.reason == CP.STALE
    # Still readable, because its contents are the last thing that was true.
    assert state.derived["book"]["mid"] == "84750"


def test_a_shape_the_reader_does_not_know_is_refused(tmp_path):
    write_state(tmp_path, version="ms-v0-state.v9-9", written_ms=10_000)
    assert CP.read(tmp_path, now_ms=10_100, session_id=SESSION).reason == CP.WRONG_SHAPE


def test_a_file_claiming_to_be_the_authority_is_refused(tmp_path):
    """A future writer must not be able to quietly promote a cache to the replay authority."""
    write_state(tmp_path, is_authority=True, written_ms=10_000)
    assert CP.read(tmp_path, now_ms=10_100, session_id=SESSION).reason == CP.CLAIMS_AUTHORITY


def test_damage_is_a_reason_rather_than_a_crash(tmp_path):
    directory = tmp_path / CP.STATE_DIRNAME
    directory.mkdir(parents=True)
    (directory / CP.STATE_FILENAME).write_text('{"state_version": "ms-v0', encoding="utf-8")
    assert CP.read(tmp_path, now_ms=1_000).reason == CP.UNREADABLE


def test_a_truncated_active_list_keeps_an_exact_count(tmp_path):
    write_state(tmp_path, written_ms=10_000, items=[wall_item()], truncated=True, active=2_500)
    state = CP.read(tmp_path, now_ms=10_100, session_id=SESSION)
    assert state.truncated is True
    assert state.active_count == 2_500 and len(state.wall_items) == 1


# --- the follower uses it ---------------------------------------------------------------------

def session_of(root):
    return J.latest_session(root)


def test_the_follower_reads_the_set_instead_of_reconstructing_it(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    write_state(tmp_path, session_id=writer.session_id, written_ms=10_000,
                items=[wall_item("ASK", "84800"), wall_item("BID", "84700")])
    state = WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=10_100)
    assert state.verified_by == "COLLECTOR_STATE_CHECKPOINT"
    assert state.coverage == "COMPLETE" and state.count == 2
    assert state.values_as_of == "CHECKPOINT_CURRENT"
    assert state.scanned_bytes > 0


def test_a_transition_newer_than_the_checkpoint_is_applied_on_top(tmp_path):
    """Normally the file is ahead of the stream, but the tail is read rather than assumed empty."""
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    seq = writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE",
                      receive_ms=2_500, open_file=True)
    write_state(tmp_path, session_id=writer.session_id, written_ms=10_000, seq=seq - 1,
                items=[wall_item("ASK", "84800")])
    state = WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=10_100)
    assert state.count == 2 and state.tail_records == 1
    assert state.tail_complete is True


def test_the_tail_stops_at_the_checkpoint_seq_rather_than_reading_the_session(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    for index in range(200):
        writer.wall(side="ASK", price=str(84_800 + index), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index, open_file=True)
    last = writer.seq
    everything = J.stream_files(tmp_path, "wall", writer.session_id[:8])[0].size()
    write_state(tmp_path, session_id=writer.session_id, written_ms=10_000, seq=last - 2,
                items=[wall_item()])
    state = WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=10_100)
    assert state.tail_records == 2
    # Reading two records out of two hundred has to cost a fraction of the stream, which is the
    # whole claim: the cost follows what was appended, not how long the session has run.
    assert state.tail_bytes < everything / 10


def test_a_stale_checkpoint_is_used_rather_than_triggering_a_full_walk(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    write_state(tmp_path, session_id=writer.session_id, written_ms=10_000, items=[wall_item()])
    state = WallFollower().refresh(tmp_path, session_of(tmp_path),
                                   now_ms=10_000 + CP.STALE_MS + 5_000)
    assert state.state is not None and state.state.usable is False
    assert state.state.reason == CP.STALE
    assert state.count == 1, "the last published state is what there is to show"


def test_without_a_usable_file_the_reconstruction_still_runs(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE", receive_ms=1_500)
    state = WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_100)
    assert state.verified_by == "STREAM_START"
    assert state.values_as_of == "JOURNAL_OPEN_ROW" and state.count == 1


def test_a_file_from_another_session_falls_back_rather_than_showing_the_wrong_book(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE", receive_ms=1_500)
    write_state(tmp_path, session_id="cccccccc-0000-0000-0000-000000000000", written_ms=10_000,
                items=[wall_item("BID", "1")])
    state = WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=10_100)
    assert state.verified_by == "STREAM_START"
    assert ("BID", "1") not in state.resting


# --- the real collector writes one ------------------------------------------------------------

def test_the_real_collector_publishes_its_live_candidate_set(tmp_path):
    root = tmp_path / "journal"
    info = written_journal(root, samples=12, end_session=False, seal=False)
    state = CP.read(root, now_ms=info["last_sample_ms"], session_id=info["session_id"])
    assert state.usable is True
    assert state.active_count and state.active_count == len(state.wall_items)
    assert state.truncated is False
    assert state.payload["walls"]["values_as_of"] == "COLLECTOR_CURRENT_SAMPLE"
    assert state.resnapshot["fixed_interval_polling"] is False
    assert state.derived["book"]["state"] == "SYNCED"


def test_an_ended_session_publishes_an_empty_set_and_says_it_ended(tmp_path):
    """A collector that stopped must not leave its last candidates looking like they are resting."""
    root = tmp_path / "journal"
    info = written_journal(root, samples=4, end_session=True, seal=True)
    state = CP.read(root, now_ms=info["last_sample_ms"], session_id=info["session_id"])
    assert state.session_ended is True
    assert state.wall_items == [] and state.active_count == 0
    # The metrics of the last sample survive, because "how old" is the operator's question.
    assert state.derived["book"]["mid"] is not None


def test_a_reader_polling_during_a_write_never_sees_half_a_file(tmp_path):
    """`os.replace` is atomic, so there is no torn-read window to retry around."""
    root = tmp_path / "journal"
    written_journal(root, samples=3, end_session=False, seal=False)
    path = CP.state_path(root)
    for _ in range(50):
        assert json.loads(path.read_bytes())["state_version"] in CP.SUPPORTED_STATE_VERSIONS
    assert list((root / CP.STATE_DIRNAME).glob("*.tmp")) == []


def test_the_reader_accepts_every_version_the_collector_has_ever_written():
    """A version list that drifts behind the writer shows an empty screen with no error.

    V1.5 bumped the writer to `v1-3` because the resnapshot section's coverage keys were
    replaced rather than added to, so this is the assertion that the two moved together.
    """
    from app.crypto.market_structure_v0.collector import STATE_VERSION

    assert STATE_VERSION == "ms-v0-state.v1-3"
    assert STATE_VERSION in CP.SUPPORTED_STATE_VERSIONS
    assert CP.SUPPORTED_STATE_VERSIONS == ("ms-v0-state.v1-1", "ms-v0-state.v1-2",
                                           "ms-v0-state.v1-3")


@pytest.mark.parametrize("payload", ["[]", "null", '"text"', "7"])
def test_a_file_that_is_not_an_object_is_refused(tmp_path, payload):
    directory = tmp_path / CP.STATE_DIRNAME
    directory.mkdir(parents=True)
    (directory / CP.STATE_FILENAME).write_text(payload, encoding="utf-8")
    assert CP.read(tmp_path, now_ms=1_000).usable is False


def test_the_write_never_stops_the_collector_when_the_path_is_unusable(tmp_path):
    """A full disk or a permissions problem must not stop the collection it is accelerating."""
    root = tmp_path / "journal"
    root.mkdir()
    session_id = "dddddddd-0000-0000-0000-000000000000"
    store = STORE.Store.open(root, session_id, started_ns=time.monotonic_ns())
    try:
        # A file where the state directory has to go: the write cannot succeed and must not raise.
        (root / CP.STATE_DIRNAME).write_text("in the way", encoding="utf-8")
        assert store.write_state({"state_version": "ms-v0-state.v1-1"}) == 0
        assert store.state_writes_failed == 1 and store.state_last_error
        assert store.stats()["state_file"]["failed"] == 1
    finally:
        store.close()
