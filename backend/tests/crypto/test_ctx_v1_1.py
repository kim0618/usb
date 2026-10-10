"""Production Context Collector V1.1: the changes `CONTRACT_CTX_V1_1.md` freezes.

1. it runs on the V1.5 V0 collector (request ownership, late snapshot discard, v1-3 state);
2. the warm-up gate: no LIVE with a NONE wall before `lm-wall.v2` R4 can have been met;
3. display disappearances are classified, and a rank eviction is never an end;
4. the current-state cache can live on a volatile filesystem and be lost without losing data;
5. BTCUSDT only.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.context_collector_v1 import CONTRACT_RELATIVE_PATH, CONTRACT_SHA256
from app.crypto.context_collector_v1 import contract as K
from app.crypto.context_collector_v1 import reader as RD
from app.crypto.context_collector_v1.collector import (UnsupportedSymbol, build, main,
                                                       require_btc)
from app.crypto.context_collector_v1.context import ContextEngine, classify_end
from app.crypto.context_collector_v1.store import ContextStore, state_cache_target
from app.crypto.liquidity_map import checkpoint as CP
from app.crypto.liquidity_map import continuity as CN
from app.crypto.liquidity_map import wallrule as R
from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0 import collector as V0
from app.crypto.market_structure_v0.envelope import Session

from tests.crypto.ctx_v1_fixtures import (BASE_MS, Script, TeeSink, context_collector, flush,
                                          latest)
from tests.crypto.liquidity_map_fixtures import wall_snapshot
from tests.crypto.ms_v0_fixtures import depth_frame, snapshot

MID = Decimal("84750")
TICK = Decimal("10")
REPO = Path(__file__).resolve().parents[3]


def ctx(sink):
    return sink.of(K.CONTEXT_KIND)


def last(sink):
    return ctx(sink)[-1]


def book(*, ask_walls: dict[int, str] | None = None, bid_walls: dict[int, str] | None = None,
         levels: int = 120, last_update_id: int = 1000) -> dict:
    """A wide snapshot (bounds past +-1%), 1 BTC everywhere except the named offsets."""
    ask_walls, bid_walls = ask_walls or {}, bid_walls or {}
    bids = [[str(MID - TICK * i), bid_walls.get(i, "1")] for i in range(levels)]
    asks = [[str(MID + TICK * (i + 1)), ask_walls.get(i, "1")] for i in range(levels)]
    return {"lastUpdateId": last_update_id, "bids": bids, "asks": asks, "E": 1_000, "T": 1_000}


def ask_price(offset: int) -> str:
    return str(MID + TICK * (offset + 1))


def started(tmp_path, payload=None, **kwargs):
    collector, sink = context_collector(tmp_path, **kwargs)
    script = Script([collector])
    script.start(snapshot_payload=payload)
    return collector, sink, script


# --------------------------------------------------------------------------- 0. contract

def test_the_frozen_contract_is_the_one_the_code_names():
    data = (REPO / CONTRACT_RELATIVE_PATH).read_bytes()
    assert hashlib.sha256(data).hexdigest() == CONTRACT_SHA256
    sidecar = (REPO / CONTRACT_RELATIVE_PATH).with_suffix(".sha256").read_text().split()[0]
    assert sidecar == CONTRACT_SHA256


# --------------------------------------------------------------------------- 1. V1.5

def test_the_collector_runs_on_the_v15_v0_collector():
    assert V0.STATE_VERSION == "ms-v0-state.v1-3"
    assert CN.RULE_VERSION == "lm-continuity.v5"
    assert "ms-v0-state.v1-3" in CP.SUPPORTED_STATE_VERSIONS
    assert hasattr(V0, "SnapshotRequest")
    assert V0.REFRESH_MIN_INTERVAL_S == {V0.REFRESH_COVERAGE_EDGE: 10.0,
                                         V0.REFRESH_SAFETY: 300.0}
    assert not hasattr(V0, "COVERAGE_REFRESH_COOLDOWN_S")


def test_a_late_snapshot_is_discarded_and_the_context_reading_does_not_move(tmp_path):
    collector, sink, script = started(tmp_path)
    script.run(70)
    assert last(sink)["collector"]["state"] == K.FEED_LIVE
    at_ms, at_ns = script.ms(70.2), script.ns(70.2)
    collector._want_refresh(V0.REFRESH_COVERAGE_EDGE, at_ns=at_ns, at_ms=at_ms)
    request = collector.mark_snapshot_requested(receive_ms=at_ms, mono_ns=at_ns)
    collector.check_refresh_deadline(at_ns=at_ns + 1_100_000_000, at_ms=at_ms + 1_100)
    before = (collector.depth.generation, collector.depth.last_update_id,
              dict(collector.depth.asks))
    collector.on_snapshot(dict(wall_snapshot(), lastUpdateId=500), receive_ms=at_ms + 1_400,
                          mono_ns=at_ns + 1_400_000_000, request_ms=at_ms,
                          request_mono_ns=at_ns, request_id=request.request_id)
    assert (collector.depth.generation, collector.depth.last_update_id,
            dict(collector.depth.asks)) == before
    script.second = 72
    script.run(2)
    record = last(sink)
    assert record["collector"]["state"] == K.FEED_LIVE
    assert record["book"]["generation"] == before[0]
    collector.store.close()


# --------------------------------------------------------------------------- 2. warm-up gate

def test_restart_is_unknown_not_live_none_until_r4_can_be_met_then_live(tmp_path):
    collector, sink, script = started(tmp_path)
    script.run(1)                                            # t=0: first observed sample
    first = last(sink)
    assert first["warmup"]["active"] and first["warmup"]["observed_ms"] == 0
    assert first["liquidity"]["state"] == "UNKNOWN"
    assert {first["liquidity"][s]["wall_state"] for s in ("ASK", "BID")} == {"UNKNOWN"}
    script.run(9)                                            # t < R4
    mid = last(sink)
    assert mid["warmup"]["observed_ms"] == 9_000 and mid["warmup"]["active"]
    assert mid["liquidity"]["state"] == "UNKNOWN"
    assert "NONE" not in {mid["liquidity"][s]["wall_state"] for s in ("ASK", "BID")}
    script.run(1)                                            # t == R4
    open_ = last(sink)
    assert open_["warmup"]["observed_ms"] == R.MIN_PERSISTENCE_MS
    assert not open_["warmup"]["active"]
    # The gate opens at the same instant a wall observed since the first sample qualifies.
    assert {open_["liquidity"][s]["wall_state"] for s in ("ASK", "BID")} == {"OK"}
    assert open_["liquidity"]["state"] == "LIVE"
    assert collector.engine.false_live_none == 0
    assert collector.engine.prevented_live_none == 10
    collector.store.close()


def test_a_book_with_no_wall_says_none_only_after_the_gate(tmp_path):
    collector, sink, script = started(tmp_path, payload=book())
    script.run(10)
    assert all(r["liquidity"]["state"] == "UNKNOWN" for r in ctx(sink))
    assert all(r["liquidity"][s]["wall_state"] == "UNKNOWN" for r in ctx(sink)
               for s in ("ASK", "BID"))
    script.run(2)
    record = last(sink)
    assert record["liquidity"]["state"] == "LIVE"
    assert {record["liquidity"][s]["wall_state"] for s in ("ASK", "BID")} == {"NONE"}
    assert record["liquidity"]["ASK"]["wall_state_reason"] == \
        "NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2"
    assert collector.engine.false_live_none == 0
    collector.store.close()


def test_a_stale_book_during_warmup_restarts_the_gate(tmp_path):
    collector, sink, script = started(tmp_path)
    script.run(5)
    script.trade(5.5)
    script.sample(8.5)                                        # 3 s without a depth frame
    assert last(sink)["warmup"]["observation_since_ms"] is None
    collector.mark_snapshot_requested(receive_ms=script.ms(8.6), mono_ns=script.ns(8.6))
    collector.on_snapshot(wall_snapshot(last_update_id=5_000), receive_ms=script.ms(8.7),
                          mono_ns=script.ns(8.7), request_ms=script.ms(8.6),
                          request_mono_ns=script.ns(8.6))
    collector.on_depth_frame(depth_frame(first=4_900, last=5_100, previous=4_850),
                             receive_ms=script.ms(8.8), mono_ns=script.ns(8.8))
    script.next_update, script.second = 5_100, 8
    script.run(1)
    restart = last(sink)["warmup"]
    assert restart["observation_since_ms"] == script.ms(9) and restart["active"]
    script.run(9)
    assert last(sink)["liquidity"]["state"] == "UNKNOWN"
    script.run(1)
    assert last(sink)["liquidity"]["state"] != "UNKNOWN" or \
        last(sink)["liquidity"]["reasons"] != [K.WARMUP_REASON]
    assert collector.engine.false_live_none == 0
    collector.store.close()


def test_a_depth_reconnect_during_warmup_restarts_the_gate(tmp_path):
    collector, sink, script = started(tmp_path)
    script.run(4)
    collector.on_depth_disconnect("closed", receive_ms=script.ms(4.2), mono_ns=script.ns(4.2))
    script.trade(4.5)
    script.sample(5)
    record = last(sink)
    assert record["warmup"]["observation_since_ms"] is None and record["warmup"]["active"]
    assert record["collector"]["state"] == K.FEED_SYNCING
    assert record["liquidity"]["state"] == "UNKNOWN"
    assert collector.engine.false_live_none == 0
    collector.store.close()


def test_after_a_hard_resync_the_gate_closes_again_and_no_live_none_escapes(tmp_path):
    collector, sink, script = started(tmp_path)
    script.run(30)
    collector.on_depth_frame(depth_frame(first=999_000, last=999_100, previous=998_000),
                             receive_ms=script.ms(30.3), mono_ns=script.ns(30.3))
    collector.mark_snapshot_requested(receive_ms=script.ms(30.4), mono_ns=script.ns(30.4))
    collector.on_snapshot(wall_snapshot(last_update_id=5_000), receive_ms=script.ms(30.5),
                          mono_ns=script.ns(30.5), request_ms=script.ms(30.4),
                          request_mono_ns=script.ns(30.4))
    collector.on_depth_frame(depth_frame(first=4_900, last=5_100, previous=4_850),
                             receive_ms=script.ms(30.6), mono_ns=script.ns(30.6))
    script.next_update = 5_100
    script.run(12)
    states = [(r["warmup"]["active"], r["liquidity"]["state"],
               r["liquidity"]["ASK"]["wall_state"]) for r in ctx(sink)[30:]]
    assert states[0][0] is True and states[0][1] == "UNKNOWN"
    assert not [s for s in states if s[0] and (s[1] == "LIVE" or s[2] == "NONE")]
    assert states[-1] == (False, "LIVE", "OK")
    assert collector.engine.false_live_none == 0
    collector.store.close()


# --------------------------------------------------------------------------- 3. end classes

def eight_walls(tmp_path):
    walls = {offset: "40" for offset in (10, 20, 30, 40, 50, 60, 70, 80)}
    collector, sink, script = started(tmp_path, payload=book(ask_walls=walls))
    script.run(15)
    shown = [w["price"] for w in latest(collector)["liquidity"]["sides"]["ASK"]["walls"]]
    assert shown == [ask_price(o) for o in (10, 20, 30, 40, 50, 60)]
    return collector, sink, script


def test_a_wall_pushed_out_of_the_top_six_is_rank_evicted_not_cancel_like(tmp_path):
    collector, sink, script = eight_walls(tmp_path)
    script.second = 16
    script.depth(15.5, asks=[[ask_price(3), "40"]])        # a new nearer wall
    script.trade(15.6)
    script.sample(16)
    script.run(12)                                         # it qualifies after R4
    exits = [x for r in ctx(sink) for x in r["display_exits"]]
    assert [(x["end_class"], x["end_reason"], x["bin_low"]) for x in exits] == [
        ("RANK_EVICTED", "BEYOND_DISPLAY_LIMIT", ask_price(60)[:-1] + "0")]
    assert exits[0]["mc_v1_raw_state"] == "CANCEL_LIKE"    # what V1 would have published
    vanished = [v for r in ctx(sink) for v in r["vanished"]]
    assert vanished == []
    assert collector.engine.classified_totals["RANK_EVICTED"] == 1
    assert collector.engine.tracker.totals["CANCEL_LIKE"] == 1   # the raw tracker is unchanged
    collector.store.close()


def test_a_wall_under_the_display_filter_that_is_still_a_wall_is_rank_evicted(tmp_path):
    collector, sink, script = eight_walls(tmp_path)
    script.second = 16
    script.depth(15.5, asks=[[ask_price(10), "5.5"]])      # 5.5x neighbours, ~467k USDT
    script.trade(15.6)
    script.sample(16)
    exits = last(sink)["display_exits"]
    assert [(x["end_class"], x["end_reason"]) for x in exits] == [
        ("RANK_EVICTED", "BELOW_DISPLAY_FILTER")]
    assert last(sink)["vanished"] == []
    collector.store.close()


def test_a_pulled_wall_is_true_ended_and_keeps_the_panels_verdict(tmp_path):
    collector, sink, script = eight_walls(tmp_path)
    script.second = 16
    script.depth(15.5, asks=[[ask_price(10), "0"]])
    script.trade(15.6)
    script.sample(16)
    record = last(sink)
    assert [(v["state"], v["end_class"]) for v in record["vanished"]] == [
        ("CANCEL_LIKE", "TRUE_ENDED")]
    # The 7th wall moves up into the display; nothing was evicted.
    assert record["display_exits"] == []
    collector.store.close()


def test_a_wall_that_drifts_past_the_candidate_band_is_out_of_coverage(tmp_path):
    collector, sink, script = started(tmp_path, payload=book(ask_walls={84: "40"}))
    script.run(15)
    assert latest(collector)["liquidity"]["sides"]["ASK"]["nearest_wall"]["price"] == \
        ask_price(84)
    script.second = 16
    # Bids down to 84710 leave mid ~84735: the wall at 85600 is now beyond 1% of mid.
    script.depth(15.5, bids=[[str(MID - TICK * i), "0"] for i in range(4)])
    script.trade(15.6)
    script.sample(16)
    exits = last(sink)["display_exits"]
    assert [(x["end_class"], x["end_reason"]) for x in exits] == [
        ("OUT_OF_COVERAGE", "BEYOND_V0_CANDIDATE_BAND")]
    collector.store.close()


def test_a_disappearance_across_a_resnapshot_is_unknown(tmp_path):
    collector, sink, script = started(tmp_path)
    script.run(20)
    collector.on_depth_frame(depth_frame(first=999_000, last=999_100, previous=998_000),
                             receive_ms=script.ms(20.3), mono_ns=script.ns(20.3))
    script.sample(21)
    ends = [v for r in ctx(sink) for v in r["vanished"]]
    assert ends and {v["end_class"] for v in ends} == {"UNKNOWN"}
    assert {v["state"] for v in ends} == {"UNKNOWN"}
    collector.store.close()


def test_the_classifier_follows_the_contract_order():
    selection = {"ASK": [{"bin_low": "84820", "notional_usdt": "400000"}], "BID": []}
    known = {"mid": Decimal("84755"), "known_low": Decimal("84000"),
             "known_high": Decimal("85500")}
    item = {"state": "CANCEL_LIKE", "side": "ASK", "bin_low": "84820", "bin_high": "84825"}
    assert classify_end(item, selection=selection, **known) == ("RANK_EVICTED",
                                                                "BELOW_DISPLAY_FILTER")
    assert classify_end({**item, "state": "UNKNOWN", "reason": "X"}, selection=selection,
                        **known) == ("UNKNOWN", "X")
    gone = {**item, "bin_low": "85600", "bin_high": "85605"}
    assert classify_end(gone, selection=selection, **known)[0] == "OUT_OF_COVERAGE"
    near = {**item, "bin_low": "84900", "bin_high": "84905"}
    assert classify_end(near, selection=selection, **known) == ("TRUE_ENDED", None)
    assert classify_end(near, selection=selection, mid=None, known_low=None,
                        known_high=None)[0] == "UNKNOWN"


# --------------------------------------------------------------------------- 4. volatile cache

def cached(tmp_path, *, session=None):
    cache = tmp_path / "volatile"
    session = session or Session(started_ms=BASE_MS, started_ns=0)
    root = tmp_path / "ctx"
    store = ContextStore.open(root, session.session_id, started_ns=0, state_cache_dir=cache)
    sink = TeeSink(store)
    from app.crypto.context_collector_v1.collector import ContextCollector
    collector = ContextCollector(store=store, session=session, sink=sink,
                                 engine=ContextEngine(root=root))
    return collector, sink, cache


def test_the_state_cache_lives_in_the_volatile_directory_and_the_journal_on_disk(tmp_path):
    collector, sink, cache = cached(tmp_path)
    script = Script([collector])
    script.start()
    script.run(15)
    root = collector.store.root
    link = root / "state"
    target = state_cache_target(root, cache)
    assert link.is_symlink() and Path(link.readlink()) == target
    assert (target / "collector_state.json").is_file()
    assert (target / K.LATEST_FILENAME).is_file()
    assert not list(root.glob("state.disk-*"))
    assert last(sink)["liquidity"]["state"] == "LIVE"
    assert (root / "context").is_dir()                 # journal stays under the root
    assert not (target / "context").exists()
    collector.store.close()


def test_a_real_state_directory_is_moved_aside_never_deleted(tmp_path):
    root = tmp_path / "ctx"
    (root / "state").mkdir(parents=True)
    (root / "state" / "collector_state.json").write_text("{}")
    collector, sink, cache = cached(tmp_path)
    aside = list(root.glob("state.disk-*"))
    assert len(aside) == 1 and (aside[0] / "collector_state.json").read_text() == "{}"
    assert (root / "state").is_symlink()
    collector.store.close()


def test_losing_the_cache_costs_nothing_but_the_cache(tmp_path):
    collector, sink, cache = cached(tmp_path)
    script = Script([collector])
    script.start()
    script.run(15)
    before = [s.context for s in RD.seconds(collector.store.root)]
    import shutil
    shutil.rmtree(state_cache_target(collector.store.root, cache))      # deletion
    script.run(1)
    record = last(sink)
    assert record["liquidity"]["state"] == "LIVE"           # rewritten before it was read
    assert collector.store.state_cache_recreated == 1
    (collector.store.root / "state" / "collector_state.json").write_bytes(b"\x00broken")
    (collector.store.root / "state" / K.LATEST_FILENAME).write_bytes(b"\x00broken")
    script.run(1)                                           # corruption is simply overwritten
    assert last(sink)["liquidity"]["state"] == "LIVE"
    assert json.loads((collector.store.root / "state" / K.LATEST_FILENAME).read_text())
    flush(collector.store)
    after = [s.context for s in RD.seconds(collector.store.root)]
    assert after[:len(before)] == before                    # the journal never noticed
    collector.store.close()


def test_a_crash_with_the_cache_gone_restarts_clean_and_keeps_the_journal(tmp_path):
    collector, _, cache = cached(tmp_path)
    script = Script([collector])
    script.start()
    script.run(15)
    flush(collector.store)
    rows = len(list(RD.seconds(collector.store.root)))
    for writer in collector.store.writers.values():         # kill -9: nothing sealed
        writer._handle.close()
        writer._handle = None
    collector.store.lock.release()
    collector.store.lock = None
    import shutil
    shutil.rmtree(cache)                                    # reboot: tmpfs is empty
    again, sink, _ = cached(tmp_path, session=Session(started_ms=BASE_MS + 60_000,
                                                      started_ns=60 * 10**9))
    assert again.store.recovery["files_recovered"] >= 1
    script = Script([again])
    script.ms = lambda i: BASE_MS + 60_000 + int(i * 1000)
    script.start()
    script.run(2)
    assert last(sink)["collector"]["state"] == K.FEED_SYNCING
    assert last(sink)["warmup"]["active"]
    flush(again.store)
    assert len(list(RD.seconds(again.store.root))) == rows + 2
    again.store.close()


# --------------------------------------------------------------------------- 5. BTC only

@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT", "btcusdt", "BTCUSD", ""])
def test_every_other_symbol_is_refused_before_anything_is_opened(tmp_path, symbol):
    with pytest.raises(UnsupportedSymbol):
        require_btc(symbol)
    with pytest.raises(UnsupportedSymbol):
        build(tmp_path / "root", duration_s=1, symbol=symbol)
    assert not (tmp_path / "root").exists()


def test_the_cli_refuses_a_non_btc_symbol_with_its_own_exit_code(tmp_path, capsys):
    assert main(["--root", str(tmp_path / "root"), "--symbol", "ETHUSDT"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["event"] == "REFUSED" and out["reason"] == "UNSUPPORTED_SYMBOL"
    assert not (tmp_path / "root").exists()


def test_a_state_naming_another_symbol_is_never_published(tmp_path, monkeypatch):
    collector, sink, script = started(tmp_path)
    script.run(15)
    original = collector.store.write_state

    def other(payload):
        payload["collector"]["symbol"] = "ETHUSDT"
        return original(payload)

    monkeypatch.setattr(collector.store, "write_state", other)
    script.run(1)
    record = last(sink)
    assert record["collector"]["state"] == K.FEED_UNKNOWN
    assert record["collector"]["reasons"][0] == "STATE_SYMBOL_IS_NOT_BTCUSDT"
    assert record["liquidity"]["ASK"]["nearest"] is None
    collector.store.close()
