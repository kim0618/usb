"""Production Context Collector V1 end to end, with no network and no sleeping.

The claims under test, in the order the task states them:

* the collector runs the V0 state machine **unchanged** - fed the same input, it produces the same
  derived samples, wall rows, telemetry and state file as a research collector;
* its LIQUIDITY reading **is** the Liquidity Map viewer's route for the same root, and its FLOW and
  vanished-wall verdicts are Market Context V1's;
* every fault the task lists - gap, resync, stale book, stale trades, reconnect, duplicate trade,
  a failed computation, a restart - ends in a non-LIVE state with its reason, never in a neutral
  or stale LIVE reading.
"""
from __future__ import annotations

import asyncio
import copy
import json
import types
from decimal import Decimal

import pytest

from app.crypto.context_collector_v1 import contract as K
from app.crypto.context_collector_v1.context import (SYNC_BOOK, SYNC_FLOW_WARMUP,
                                                     SYNC_WALL_WARMUP)
from app.crypto.context_collector_v1.store import ContextStore
from app.crypto.liquidity_map import api as LMAPI
from app.crypto.liquidity_map import checkpoint as CP
from app.crypto.liquidity_map import continuity as CN
from app.crypto.liquidity_map import wallrule as R
from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0.envelope import Session

from tests.crypto.ctx_v1_fixtures import (BASE_MS, Script, context_collector, flush, latest,
                                          research_collector)
from tests.crypto.ms_v0_fixtures import depth_frame, snapshot

ASK_WALL = "84820"   # wall_snapshot: MID + 10 * (6 + 1)
BID_WALL = "84710"   # wall_snapshot: MID - 10 * 4


def contexts(sink) -> list[dict]:
    return sink.of(K.CONTEXT_KIND)


def last_context(sink) -> dict:
    return contexts(sink)[-1]


# --------------------------------------------------------------------------- V0 parity

def test_fed_the_same_input_the_v0_state_machine_behaves_identically(tmp_path):
    """Faults included: a gap, a resync, a trade reconnect and a duplicate trade."""
    session = Session(started_ms=BASE_MS, started_ns=0)
    research, research_sink = research_collector(tmp_path, session=session)
    # A second Session object with the same identity, so both envelopes carry the same seq.
    twin = Session(session_id=session.session_id, started_ms=BASE_MS, started_ns=0)
    ctx, ctx_sink = context_collector(tmp_path, session=twin)
    script = Script([research, ctx])
    script.start()
    script.run(20)
    script.trade(20.5, trade_id=script.trade_id - 1)              # duplicate
    script.each("on_trade_disconnect", "socket closed", receive_ms=script.ms(20.6),
                mono_ns=script.ns(20.6))
    script.each("on_trade_connect", "t2", receive_ms=script.ms(20.7), mono_ns=script.ns(20.7))
    script.second = 21
    script.run(5)
    script.each("on_depth_frame", depth_frame(first=999_000, last=999_100, previous=998_000),
                receive_ms=script.ms(26.2), mono_ns=script.ns(26.2))          # gap
    script.sample(26.5)
    script.each("mark_snapshot_requested", receive_ms=script.ms(26.6), mono_ns=script.ns(26.6))
    from tests.crypto.liquidity_map_fixtures import wall_snapshot
    script.each("on_snapshot", wall_snapshot(last_update_id=5_000), receive_ms=script.ms(26.7),
                mono_ns=script.ns(26.7), request_ms=script.ms(26.6),
                request_mono_ns=script.ns(26.6))
    script.next_update = 5_000
    script.each("on_depth_frame", depth_frame(first=4_900, last=5_100, previous=4_850),
                receive_ms=script.ms(26.8), mono_ns=script.ns(26.8))
    script.next_update = 5_100
    script.second = 27
    script.run(15)

    for kind in ("derived", "wall", "telemetry", "raw_depth", "raw_trade", "snapshot",
                 "checkpoint", "trade"):
        assert ctx_sink.of(kind) == research_sink.of(kind), kind
    # The state file is the parent's. The only difference is the envelope sequence numbers it
    # quotes, which run ahead here because `context` and `wall_v2` rows share the sequence.
    research_state = json.loads(CP.state_path(research.store.root).read_bytes())
    ctx_state = json.loads(CP.state_path(ctx.store.root).read_bytes())
    assert ctx_state["session"].pop("seq") > research_state["session"].pop("seq")
    for state in (research_state, ctx_state):
        for item in state["telemetry_recent"]:
            item.pop("seq")
    assert research_state == ctx_state
    assert research.depth.generation == ctx.depth.generation == 2
    research.store.close()
    ctx.store.close()


def test_dropped_kinds_reach_no_file_and_are_counted(tmp_path):
    ctx, sink = context_collector(tmp_path, shadow_bytes=True)
    script = Script([ctx])
    script.start()
    script.run(12)
    ctx.store.close()
    root = ctx.store.root
    on_disk = {path.name for path in root.iterdir() if path.is_dir()}
    assert on_disk == {"session", "telemetry", "state", "context", "wall_v2", "wall_r0"}
    for kind in K.DROPPED_V0_KINDS:
        assert not (root / kind).exists(), kind
    dropped = ctx.store.dropped
    assert dropped["raw_depth"]["rows"] == len(sink.of("raw_depth"))
    assert dropped["derived"]["rows"] == 12
    assert dropped["raw_depth"]["bytes"] > 0      # shadow mode serialized them


# --------------------------------------------------------------------------- viewer parity

def test_the_liquidity_reading_is_the_viewer_route_for_the_same_root(tmp_path, monkeypatch):
    ctx, _ = context_collector(tmp_path)
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(ctx.store.root))
    monkeypatch.setattr(LMAPI, "state", LMAPI.PreviewState())
    script = Script([ctx])
    script.start()
    compared = 0
    for _ in range(25):
        script.run(1)
        at_ms = script.ms(script.second)
        monkeypatch.setattr(LMAPI, "time", types.SimpleNamespace(time=lambda: at_ms / 1000))
        body = asyncio.run(LMAPI.snapshot(min_notional_usdt=K.DISPLAY_MIN_NOTIONAL_USDT,
                                          wall_limit=K.DISPLAY_WALL_LIMIT))
        mine = copy.deepcopy(ctx.engine.last_reading.snapshot)
        body["source"].pop("read_cost")
        mine["source"].pop("read_cost")
        assert body == mine
        compared += 1
    assert compared == 25
    ctx.store.close()


def test_the_record_from_state_restatement_matches_the_viewer(tmp_path):
    from app.crypto.context_collector_v1.context import record_from_state
    ctx, _ = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(3)
    state = CP.read(ctx.store.root, now_ms=script.ms(3))
    assert record_from_state(state) == LMAPI._record_from_state(state)
    ctx.store.close()


def test_nearest_walls_are_lm_wall_v2_applied_to_the_live_candidate_set(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(5)
    # R4: nothing has been observed for ten seconds yet, so no wall qualifies.
    early = last_context(sink)["liquidity"]
    assert early["ASK"]["nearest"] is None and early["BID"]["nearest"] is None
    script.run(10)
    record = last_context(sink)
    assert record["liquidity"]["ASK"]["nearest"]["price"] == ASK_WALL
    assert record["liquidity"]["BID"]["nearest"]["price"] == BID_WALL
    # Exactly what the frozen rule says when applied to the state file's rows by hand.
    state = CP.read(ctx.store.root, now_ms=script.ms(15))
    for side, price in (("ASK", ASK_WALL), ("BID", BID_WALL)):
        selection = R.select(state.wall_items, side=side, mid=Decimal("84755"),
                             latest_sample_ms=state.written_ms,
                             known_low=Decimal(state.derived["book"]["known_low"]),
                             known_high=Decimal(state.derived["book"]["known_high"]),
                             carried_span=CN.carried_span_ms,
                             wall_continuity=CN.wall_continuity)
        assert [w["price"] for w in selection.walls] == [price]
        nearest = record["liquidity"][side]["nearest"]
        assert nearest["notional_usdt"] == selection.walls[0]["notional_usdt"]
        assert nearest["persistence_ms"] >= R.MIN_PERSISTENCE_MS
    ctx.store.close()


# --------------------------------------------------------------------------- flow parity

def test_rolling_windows_sum_exactly_the_trades_inside_each_window(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(61, trades_per_second=0)
    # Second 62..70: one buy of 2 BTC each second, plus one sell of 1 BTC at 68.5.
    for i in range(62, 71):
        script.depth(i - 0.5)
        script.trade(i - 0.4, qty="2")
        if i == 69:
            script.trade(68.5, qty="1", sell=True)
        script.sample(i)
    script.second = 70
    windows = last_context(sink)["flow"]["windows"]
    price = Decimal("84750")
    # (now - 5 s, now] at t=70 holds the buys at 65.6 .. 69.6 and the sell at 68.5.
    assert Decimal(windows["5s"]["buy_btc"]) == 10
    assert Decimal(windows["5s"]["sell_btc"]) == 1
    assert Decimal(windows["15s"]["buy_btc"]) == 18
    assert Decimal(windows["60s"]["buy_usdt"]) == 18 * 2 * price / 2
    assert windows["60s"]["trades"] == 10
    assert Decimal(windows["5s"]["imbalance_btc"]) == (Decimal(9) / Decimal(11)).quantize(
        Decimal("1e-10"))
    ctx.store.close()


def test_a_duplicate_trade_is_not_counted_twice(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(3, trades_per_second=0)
    script.trade(3.5, qty="5", trade_id=777)
    script.trade(3.6, qty="5", trade_id=777)
    script.depth(3.7)
    script.sample(4)
    window = last_context(sink)["flow"]["windows"]["5s"]
    assert window["trades"] == 1
    assert Decimal(window["buy_btc"]) == 5
    ctx.store.close()


# --------------------------------------------------------------------------- wall_v2 and MC rules

def test_bins_are_journaled_as_transitions_not_rewritten_every_second(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(40)
    events = sink.of(K.WALL_V2_KIND)
    assert [(e["event"], e["side"], e["bin_low"]) for e in events] == [
        ("OPEN", "ASK", "84820"), ("OPEN", "BID", "84710")]
    opened = events[0]
    for key in ("price", "notional_usdt", "multiple", "distance_bps", "first_seen_ms",
                "observed_persistence_ms", "own_persistence_ms", "persistence_source",
                "coverage", "generation", "bin_high"):
        assert opened[key] is not None, key
    # Every wall_v2 row of a sample precedes that sample's context row in the shared sequence.
    seqs = [(r["kind"], r["seq"], r["payload"].get("sample_index")) for r in sink.records
            if r["kind"] in (K.WALL_V2_KIND, K.CONTEXT_KIND)]
    for kind, seq, index in seqs:
        if kind == K.WALL_V2_KIND:
            context_seq = next(s for k, s, i in seqs if k == K.CONTEXT_KIND and i == index)
            assert seq < context_seq
    ctx.store.close()


def test_a_pulled_wall_is_cancel_like_and_its_bin_closes(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(20)
    script.second = 21
    script.depth(20.5, asks=[[ASK_WALL, "0"]])
    script.trade(20.6)
    script.sample(21)
    record = last_context(sink)
    assert [(v["state"], v["side"], v["bin_low"]) for v in record["vanished"]] == [
        ("CANCEL_LIKE", "ASK", "84820")]
    closes = [e for e in sink.of(K.WALL_V2_KIND) if e["event"] == "CLOSE"]
    assert [(e["side"], e["bin_low"], e["reason"]) for e in closes] == [
        ("ASK", "84820", K.CLOSE_NOT_SELECTED)]
    ctx.store.close()


def test_a_wall_price_traded_through_is_only_a_consumed_candidate(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(20)
    script.second = 21
    # Lift every ask up to and including the wall, and bid up behind it: mid moves past bin_high.
    asks = [[str(84760 + 10 * k), "0"] for k in range(8)]
    bids = [["84830", "1"]]
    script.depth(20.5, asks=asks, bids=bids)
    script.sample(21)
    vanished = last_context(sink)["vanished"]
    assert [(v["state"], v["side"]) for v in vanished] == [("CONSUMED_CANDIDATE", "ASK")]
    assert vanished[0]["mid_path_touched_bin"] is True
    assert "CONSUMED" not in {v["state"] for v in vanished}
    ctx.store.close()


def test_absorption_is_a_candidate_with_its_inputs_never_a_verdict(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(61)
    # 45 BTC of aggressive buying (about 3.8M USDT) into a 3.39M ask wall that stays put.
    script.second = 62
    script.depth(61.5)
    script.trade(61.6, qty="45")
    script.sample(62)
    absorption = last_context(sink)["absorption"]
    assert absorption["state"] == "ABSORPTION_CANDIDATE"
    assert absorption["side"] == "ASK" and absorption["bin_low"] == "84820"
    assert Decimal(absorption["aggressive_usdt"]) >= Decimal(absorption["wall_notional_usdt"])
    full = latest(ctx)["flow"]["absorption"]
    assert full["order_identity_proven"] is False
    ctx.store.close()


# --------------------------------------------------------------------------- quality states

def test_warmup_is_syncing_with_its_reasons_then_live(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(3)
    first = last_context(sink)["collector"]
    assert first["state"] == K.FEED_SYNCING
    assert set(first["reasons"]) == {SYNC_FLOW_WARMUP, SYNC_WALL_WARMUP}
    script.run(10)
    assert last_context(sink)["collector"]["reasons"] == [SYNC_FLOW_WARMUP]
    script.run(50)
    assert last_context(sink)["collector"]["state"] == K.FEED_LIVE
    assert "NEUTRAL" not in json.dumps(contexts(sink))
    ctx.store.close()


def test_a_gap_is_never_live_and_a_hard_resync_restarts_the_persistence_window(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(65)
    assert last_context(sink)["collector"]["state"] == K.FEED_LIVE
    ctx.on_depth_frame(depth_frame(first=999_000, last=999_100, previous=998_000),
                       receive_ms=script.ms(65.3), mono_ns=script.ns(65.3))
    assert ctx.depth.state == B.UNSYNCED
    script.sample(66)
    record = last_context(sink)
    assert record["collector"]["state"] == K.FEED_SYNCING
    assert SYNC_BOOK in record["collector"]["reasons"]
    assert record["liquidity"]["state"] == "UNKNOWN"
    closes = [e for e in sink.of(K.WALL_V2_KIND) if e["event"] == "CLOSE"]
    assert {e["reason"] for e in closes} == {K.CLOSE_NO_READING} and len(closes) == 2
    from tests.crypto.liquidity_map_fixtures import wall_snapshot
    ctx.mark_snapshot_requested(receive_ms=script.ms(66.1), mono_ns=script.ns(66.1))
    ctx.on_snapshot(wall_snapshot(last_update_id=5_000), receive_ms=script.ms(66.2),
                    mono_ns=script.ns(66.2), request_ms=script.ms(66.1),
                    request_mono_ns=script.ns(66.1))
    ctx.on_depth_frame(depth_frame(first=4_900, last=5_100, previous=4_850),
                       receive_ms=script.ms(66.3), mono_ns=script.ns(66.3))
    script.next_update = 5_100
    script.second = 66
    script.run(3)
    after = last_context(sink)["collector"]
    assert after["state"] == K.FEED_SYNCING and SYNC_WALL_WARMUP in after["reasons"]
    script.run(10)
    assert last_context(sink)["collector"]["state"] == K.FEED_LIVE
    ctx.store.close()


def test_a_silent_depth_stream_is_invalidated_not_served(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(65)
    script.trade(65.5)
    script.trade(67.5)
    script.sample(68)          # last depth frame at 64.5: 3.5 s of silence
    record = last_context(sink)
    assert ctx.depth.last_invalidation == B.STALE
    assert record["collector"]["state"] != K.FEED_LIVE
    assert record["liquidity"]["state"] != "LIVE"
    assert record["book"]["last_invalidation"] == B.STALE
    ctx.store.close()


def test_a_silent_trade_stream_is_never_read_as_zero_flow(tmp_path):
    """Past `TRADE_STALE_MS` V0 stops vouching for any window, so every window is UNKNOWN and the
    FLOW layer with it; the stream itself is labelled STALE. Not LIVE, and not a zero."""
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(65)
    for i in range(66, 73):
        script.depth(i - 0.5)
        script.sample(i)       # depth keeps flowing, trades stop at 64.6
    record = last_context(sink)
    assert record["flow"]["state"] == "UNKNOWN"
    assert record["flow"]["trade_state"] == "STALE"
    assert {w["coverage"] for w in record["flow"]["windows"].values()} == {"UNKNOWN"}
    assert {w["buy_usdt"] for w in record["flow"]["windows"].values()} == {None}
    assert record["collector"]["state"] == K.FEED_UNKNOWN
    assert "FLOW_UNKNOWN" in record["collector"]["reasons"]
    ctx.store.close()


def test_a_trade_reconnect_is_unknown_then_partial_never_syncing(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(65)
    ctx.on_trade_disconnect("closed", receive_ms=script.ms(65.2), mono_ns=script.ns(65.2))
    script.depth(65.5)
    script.sample(66)
    down = last_context(sink)
    assert down["flow"]["state"] == "UNKNOWN"
    assert down["collector"]["state"] == K.FEED_UNKNOWN
    ctx.on_trade_connect("t2", receive_ms=script.ms(66.2), mono_ns=script.ns(66.2))
    script.second = 66
    script.run(10)
    back = last_context(sink)
    assert back["flow"]["windows"]["60s"]["coverage"] == "PARTIAL"
    assert back["flow"]["windows"]["60s"]["coverage_reason"] == "RECONNECT"
    assert back["collector"]["state"] == K.FEED_PARTIAL
    script.run(60)
    assert last_context(sink)["collector"]["state"] == K.FEED_LIVE
    ctx.store.close()


def test_a_failed_computation_is_written_as_unknown_and_collection_continues(tmp_path,
                                                                            monkeypatch):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(3)

    def broken(**_):
        raise RuntimeError("boom")

    monkeypatch.setattr(ctx.engine, "step", broken)
    script.run(2)
    record = last_context(sink)
    assert record["collector"]["state"] == K.FEED_UNKNOWN
    assert record["collector"]["reasons"][0] == "CONTEXT_COMPUTE_ERROR"
    latest_file = json.loads(ctx.store.latest_path().read_text())
    assert latest_file["collector"]["state"] == K.FEED_UNKNOWN
    assert len(sink.of("derived")) == 5      # the V0 sample kept running
    assert ctx.context_errors == 2
    ctx.store.close()


# --------------------------------------------------------------------------- end and restart

def test_finish_closes_every_open_bin_and_marks_the_latest_file_ended(tmp_path):
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(20)
    ctx.finish("signal", at_ns=script.ns(21), at_ms=script.ms(21))
    closes = [e for e in sink.of(K.WALL_V2_KIND) if e["event"] == "CLOSE"]
    assert {(e["side"], e["reason"]) for e in closes} == {("ASK", K.CLOSE_SESSION_END),
                                                          ("BID", K.CLOSE_SESSION_END)}
    kinds = sink.kinds()
    assert kinds.index("session", 1) > max(i for i, k in enumerate(kinds) if k == K.WALL_V2_KIND)
    final = json.loads(ctx.store.latest_path().read_text())
    assert final["session_ended"] is True and final["collector"]["state"] == K.FEED_STALE
    ctx.store.close()


def test_a_restart_in_the_same_root_is_a_new_session_and_never_inherits_walls(tmp_path):
    ctx, _ = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(20)
    ctx.finish("signal", at_ns=script.ns(21), at_ms=script.ms(21))
    flush(ctx.store)
    ctx.store.close()

    second = Session(started_ms=BASE_MS + 30_000, started_ns=30 * 10**9)
    again, sink = context_collector(tmp_path, session=second)
    assert again.store.root == ctx.store.root
    script = Script([again])
    script.second = 30

    class Shifted(Script):
        pass

    script.start()
    script.run(3)
    record = last_context(sink)
    assert record["collector"]["state"] == K.FEED_SYNCING
    assert record["liquidity"]["ASK"]["nearest"] is None      # observation restarted
    assert latest(again)["session_id"] == second.session_id
    again.store.close()


def test_an_unreadable_state_file_is_unknown_never_a_reading(tmp_path, monkeypatch):
    """The engine reads the file the parent just wrote. If that file is damaged on disk, the
    viewer's path finds no sample and the record says so; it does not fall back to memory."""
    ctx, sink = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(65)
    assert last_context(sink)["collector"]["state"] == K.FEED_LIVE
    original = ctx.store.write_state

    def damaged(payload):
        written = original(payload)
        CP.state_path(ctx.store.root).write_bytes(b'{"state_version": "ms-v0-state.v1-2", tr')
        return written

    monkeypatch.setattr(ctx.store, "write_state", damaged)
    script.run(2)
    record = last_context(sink)
    assert record["collector"]["state"] in (K.FEED_UNKNOWN, K.FEED_SYNCING)
    assert record["collector"]["state"] != K.FEED_LIVE
    assert record["liquidity"]["state"] == "UNKNOWN" and record["flow"]["state"] == "UNKNOWN"
    assert record["liquidity"]["ASK"]["nearest"] is None
    ctx.store.close()
