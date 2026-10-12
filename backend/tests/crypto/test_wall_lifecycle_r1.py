"""wall-lifecycle.r1 / entry-advisor.r0: reproducibility on a synthetic journal (contract §9).

The synthetic journal is written in the collector's own record shapes (envelope + payload) with
a scripted mid path, so every expected instant and class is known in advance."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.wall_lifecycle_r1 import advisor as A
from app.crypto.research.wall_lifecycle_r1 import contract as K
from app.crypto.research.wall_lifecycle_r1 import run as RUN
from app.crypto.research.wall_lifecycle_r1 import stats as ST
from app.crypto.research.wall_lifecycle_r1 import study as S
from app.crypto.research.wall_lifecycle_r1.outcomes import MIN_MS, Series
from app.crypto.research.wall_lifecycle_r1.replay import ASK, BID, display_set, price_bin, replay

SID = "aaaaaaaa-0000-0000-0000-000000000000"
T0 = 1_791_700_000_000


class Journal:
    """Writes records in seq order into kind files, sealed as .jsonl.gz like the collector."""

    def __init__(self, root: Path, session_id: str = SID):
        self.root = root
        self.sid = session_id
        self.seq = 0
        self.v0_seq = 0
        self.records: dict[str, list[dict]] = {"context": [], "wall_v2": [], "wall_r0": []}

    def _env(self, kind: str, ms: int, payload: dict) -> dict:
        self.seq += 1
        return {"version": "btc-ms.v0.1", "collector_version": "t", "exchange": "binance_usdm",
                "symbol": "BTCUSDT", "session_id": self.sid, "seq": self.seq, "kind": kind,
                "receive_ms": ms, "mono_ns": ms * 1_000_000, "connection_id": None, "payload": payload}

    def r0(self, ms: int, side: str, price: float, event: str, notional: float = 600_000,
           multiple: float = 10, persistence_ms: int | None = None, first_seen_ms: int | None = None):
        self.v0_seq += 1
        status = {"OPENED": "ACTIVE", "ENDED": "ENDED", "UNKNOWN": "UNKNOWN"}[event]
        self.records["wall_r0"].append(self._env("wall_r0", ms, {
            "event": event, "status": status, "side": side, "price": str(price), "qty": "1",
            "notional": str(notional), "multiple": str(multiple), "first_seen_ms": first_seen_ms or ms,
            "persistence_ms": persistence_ms if persistence_ms is not None else 0,
            "coverage": "PARTIAL", "generation": 1, "local_average": "1", "neighbours": 10,
            "v0_seq": self.v0_seq}))

    def v2(self, ms: int, side: str, bin_low: float, event: str, notional: float = 600_000,
           reason: str = "NOT_SELECTED", sample_index: int = 0):
        payload = {"event": event, "side": side, "bin_low": str(bin_low), "bin_high": str(bin_low + 5),
                   "sample_ms": ms, "sample_index": sample_index, "schema": "ctx-collector.v1.1",
                   "record": "CTX_V1_WALL_V2"}
        if event == "OPEN":
            payload.update(notional_usdt=str(notional), price=str(bin_low + 1), continuity_status="NEW",
                           first_seen_ms=ms)
        elif event == "CHANGE":
            payload.update(notional_usdt=str(notional), changed=["bin_members"], first_seen_ms=ms)
        else:
            payload.update(reason=reason, last_notional_usdt=str(notional), last_price=str(bin_low + 1))
        self.records["wall_v2"].append(self._env("wall_v2", ms, payload))

    def context(self, ms: int, mid: float, buy: float = 60, sell: float = 40, index: int = 0,
                vanished: list | None = None):
        self.records["context"].append(self._env("context", ms, {
            "sample_ms": ms, "sample_index": index, "v0_derived_seq": 10 ** 9,
            "book": {"mid": str(mid), "best_bid": str(mid - 0.05), "best_ask": str(mid + 0.05),
                     "known_low": str(mid - 1000), "known_high": str(mid + 1000), "state": "SYNCED"},
            "flow": {"state": "LIVE", "windows": {"60s": {"state": "LIVE", "buy_usdt": str(buy),
                                                          "sell_usdt": str(sell)}}},
            "vanished": vanished or [], "display_exits": [], "collector": {"state": "LIVE"},
            "liquidity": {"state": "LIVE"}}))

    def write(self) -> Path:
        for kind, rows in self.records.items():
            d = self.root / kind
            d.mkdir(parents=True, exist_ok=True)
            with gzip.open(d / f"{kind}-20261012-{self.sid[:8]}-00001.jsonl.gz", "wt") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
        return self.root


def mid_path(n: int, base: float = 83_000.0, step: float = 0.0) -> list[float]:
    return [base + step * i for i in range(n)]


def klines(start_ms: int, n: int, close: float = 83_000.0, rng: float = 10.0) -> list:
    out = []
    for i in range(n):
        o = start_ms + i * MIN_MS
        out.append([o, str(close), str(close + rng), str(close - rng), str(close), "1", o + MIN_MS - 1])
    return out


@pytest.fixture()
def scripted(tmp_path: Path) -> dict:
    """An ASK level at 83,010 (bin 83,010..83,015) opened at second 10. The mid walks up from
    83,000, enters the bin at second 20 (touch), stays, and the level ENDs at second 32 (held 12 s,
    then pulled). A BID level at 82,990 is opened at second 5 and ENDs at second 8 untouched
    (cancel-like). A second ASK level at 83,030 opens at second 10 and is consumed at second 60
    when the mid walks through 83,035. 4,000 seconds of path so 60 min outcomes exist."""
    j = Journal(tmp_path / "journal")
    n = 4_100
    mids = [83_000.0] * n
    for i in range(20, n):
        mids[i] = 83_011.0                # inside the ASK bin from second 20
    for i in range(60, n):
        mids[i] = 83_036.0                # through the second level at second 60
    for i in range(400, n):
        mids[i] = 83_020.0                # back down later: a reversal for the second level
    v2_open = False
    for i in range(n):
        ms = T0 + i * 1000
        if i == 5:
            j.r0(ms, BID, 82_990.0, "OPENED")
        if i == 8:
            j.r0(ms, BID, 82_990.0, "ENDED", persistence_ms=3000)
        if i == 10:
            j.r0(ms, ASK, 83_010.0, "OPENED")
            j.r0(ms, ASK, 83_030.0, "OPENED", notional=900_000)
            j.v2(ms, ASK, 83_010.0, "OPEN", notional=600_000)
            j.v2(ms, ASK, 83_030.0, "OPEN", notional=900_000)
            j.v2(ms, BID, 82_900.0, "OPEN", notional=800_000)
            j.v2(ms, BID, 82_800.0, "OPEN", notional=700_000)
            j.v2(ms, BID, 82_700.0, "OPEN", notional=1_200_000)
            v2_open = True
        if i == 15:
            j.v2(ms, ASK, 83_010.0, "CHANGE", notional=480_000)   # drops below the display filter
        if i == 20 and v2_open:
            j.v2(ms, ASK, 83_010.0, "CLOSE", notional=480_000)
        if i == 32:
            j.r0(ms, ASK, 83_010.0, "ENDED", persistence_ms=22_000)
        if i == 60:
            j.v2(ms, ASK, 83_030.0, "CLOSE", notional=900_000)
            j.r0(ms, ASK, 83_030.0, "ENDED", persistence_ms=50_000)
        j.context(ms, mids[i], index=i)
    root = j.write()
    binance = tmp_path / "binance"
    binance.mkdir()
    (binance / "klines_1m.json").write_text(json.dumps(klines(T0 - 30 * MIN_MS, 120)))
    (binance / "oi_5m.json").write_text(json.dumps([{"timestamp": T0 - 3_600_000 - 300_000 * k,
                                                     "sumOpenInterestValue": str(7e9 + k)} for k in range(20)][::-1]))
    (binance / "funding.json").write_text(json.dumps([{"fundingTime": T0 + 1_800_000, "fundingRate": "0.0001"}]))
    (binance / "premium_1m.json").write_text(json.dumps(klines(T0 - 30 * MIN_MS, 120, close=0.0001, rng=0.0)))
    return {"journal": root, "binance": binance, "mids": mids}


def test_episodes_instants_and_end_classes(scripted):
    rep = replay(scripted["journal"])
    by_price = {(lv.side, lv.price): lv for lv in rep.levels}
    ask = by_price[(ASK, 83_010.0)]
    assert ask.bin_low == 83_010.0 and ask.bin_high == 83_015.0 and ask.display_qualified
    assert ask.t_touch == T0 + 20_000 and ask.t_approach == T0 + 11_000   # 1.2 bp away: first observed sample after the open
    assert ask.end_ms == T0 + 32_000 and ask.end_class == K.END_TOUCHED_PULLED
    assert ask.survival_ms == 12_000 and S.survival_bin(ask.survival_ms) == "5-15s"
    assert ask.rank_evicted_v2_ms == T0 + 20_000     # its V2 bin closed NOT_SELECTED while resting
    bid = by_price[(BID, 82_990.0)]
    assert bid.t_touch is None and bid.end_class == K.END_CANCEL_LIKE
    second = by_price[(ASK, 83_030.0)]
    assert second.t_touch == T0 + 60_000 and second.t_reach_far == T0 + 60_000
    assert second.end_class == K.END_CONSUMED and second.survival_ms == 0
    assert price_bin(83_031.7) == (83_030.0, 83_035.0)


def test_display_set_is_nearest_first_and_filtered(scripted):
    rep = replay(scripted["journal"])
    i = 12
    assert [low for low, _ in rep.path.display_ask[i]] == [83_010.0, 83_030.0]
    assert [low for low, _ in rep.path.display_bid[i]] == [82_900.0, 82_800.0, 82_700.0]
    # after the CHANGE to 480k the 83,010 bin leaves the display set
    assert [low for low, _ in rep.path.display_ask[16]] == [83_030.0]


def test_outcomes_use_only_the_path_after_the_instant(scripted):
    rep = replay(scripted["journal"])
    bn = RUN.load_binance(scripted["binance"])
    series = Series.build(rep.path, bn["klines_1m"], bn["oi_5m"], bn["funding"], bn["premium_1m"])
    ask = next(lv for lv in rep.levels if lv.price == 83_010.0)
    # rejection sign for ASK is down; the mid rose from 83,011 to 83,036 within 15 min: negative
    r15 = series.return_bp(ask.t_touch, 15 * MIN_MS, -1)
    later = scripted["mids"][20 + 15 * 60]
    assert r15 == pytest.approx(-(later - 83_011) / 83_011 * 1e4)
    # a window past the end of the data is None, not a partial figure
    assert series.return_bp(T0 + 4_050_000, 60 * MIN_MS, 1) is None
    # external series at or before the instant only
    assert series.atr_at(T0 - 31 * MIN_MS) is None and series.atr_at(T0) is not None
    assert series.funding_at(T0) is None and series.funding_at(T0 + 1_800_000) == 0.0001
    assert series.funding_inside(T0, T0 + 3_600_000) == [(T0 + 1_800_000, 0.0001)]


def test_full_run_is_deterministic_and_prefix_invariant(scripted, tmp_path):
    a = RUN.run(scripted["journal"], scripted["binance"], tmp_path / "a")
    b = RUN.run(scripted["journal"], scripted["binance"], tmp_path / "b")
    for name in ("results.json", "levels.jsonl", "advisor_events.jsonl"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert a["counts"]["levels_total"] == 3 and a["counts"]["levels_touched"] == 2
    # prefix invariance: episodes that ended before the cut are identical in the prefix run
    cut = T0 + 100_000
    p = RUN.run(scripted["journal"], scripted["binance"], tmp_path / "p", cut_ms=cut)
    full = {json.loads(l)["episode_id"]: json.loads(l) for l in (tmp_path / "a" / "levels.jsonl").read_text().splitlines()}
    pre = {json.loads(l)["episode_id"]: json.loads(l) for l in (tmp_path / "p" / "levels.jsonl").read_text().splitlines()}
    for eid, row in pre.items():
        if row["end_ms"] is not None and row["end_ms"] <= cut:
            for key in ("end_class", "t_touch", "t_approach", "t_cross", "survival_ms", "rank_evicted_v2_ms"):
                assert row[key] == full[eid][key], key
    assert p["coverage"]["cut_ms"] == cut


def test_session_boundary_resets_state(tmp_path):
    j = Journal(tmp_path / "journal")
    j.r0(T0, ASK, 83_010.0, "OPENED")
    j.context(T0, 83_000.0)
    j.context(T0 + 1000, 83_000.0)
    j2 = Journal(tmp_path / "journal", session_id="bbbbbbbb-0000-0000-0000-000000000000")
    j2.seq = j.seq
    j2.r0(T0 + 5000, ASK, 83_010.0, "ENDED", persistence_ms=5000)
    j2.context(T0 + 5000, 83_000.0)
    for kind in j.records:
        j.records[kind].extend(j2.records[kind])
    # write under distinct session file names
    for kind, rows in j.records.items():
        d = tmp_path / "journal" / kind
        d.mkdir(parents=True, exist_ok=True)
        for sid in (SID, j2.sid):
            with gzip.open(d / f"{kind}-20261012-{sid[:8]}-00001.jsonl.gz", "wt") as handle:
                for row in rows:
                    if row["session_id"] == sid:
                        handle.write(json.dumps(row) + "\n")
    rep = replay(tmp_path / "journal")
    assert rep.counters.get("session_changes") == 1
    assert rep.counters.get("r0_end_without_open") == 1          # the end in session 2 has no open there
    assert any(lv.end_event == "SESSION_END" for lv in rep.levels)


def test_next_wall_ordering_targets_and_cost(scripted, tmp_path):
    a = RUN.run(scripted["journal"], scripted["binance"], tmp_path / "a")
    events = [json.loads(l) for l in (tmp_path / "a" / "advisor_events.jsonl").read_text().splitlines()]
    # the consumed ASK level at 83,030 is an F2 LONG breakout: targets are ASK walls beyond? none
    # remain, so T1 is missing; a SHORT from the held level has BID targets nearest first
    f1 = [e for e in events if e["family"] == "F1" and e["timing"] == "T1"]
    assert len(f1) == 1
    ev = f1[0]
    assert ev["direction"] == "SHORT" and ev["targets"] == [82_905.0, 82_805.0, 82_705.0]
    assert ev["v1"] == 83_015.0 and ev["v3"] == 83_030.0
    assert ev["entry_in_zone"] and ev["entry_zone"][0] <= ev["entry"] <= ev["entry_zone"][1]
    # the mid rises through 83,015 at second 60: invalidated, gross = -(83015-83011)/83011
    t1 = ev["T1"]
    assert t1["exit"] == "INVALIDATED"
    assert t1["gross_bp"] == pytest.approx(-(83_015 - 83_011) / 83_011 * 1e4)
    assert t1["net_bp"] == pytest.approx(t1["gross_bp"] - K.RT_COST_BP)   # no funding inside 40 s
    assert len({e["event_id"] for e in events}) == len(events)              # unique per event
    assert all(len(e["analysis_request_id"]) == 16 for e in events)
    assert a["advisor"]["verdict"] == "A. NOT_ENOUGH_DATA"
    assert a["advisor"]["no_trade"]["share_of_seconds"] > 0.99


def test_target_hit_and_funding_cost(tmp_path):
    """A LONG whose target is hit after a funding settlement pays that funding."""
    j = Journal(tmp_path / "journal")
    n = 4_100
    for i in range(n):
        ms = T0 + i * 1000
        if i == 5:
            j.r0(ms, BID, 82_990.0, "OPENED")
            j.v2(ms, BID, 82_990.0, "OPEN")
            j.v2(ms, ASK, 83_100.0, "OPEN")
        if i == 40:
            j.r0(ms, BID, 82_990.0, "ENDED", persistence_ms=35_000)
        mid = 83_000.0 if i < 20 else 82_993.0 if i < 2_000 else 83_101.0
        j.context(ms, mid, buy=40, sell=60, index=i)       # SELL flow pushes into the BID wall
    root = j.write()
    binance = tmp_path / "binance"
    binance.mkdir()
    (binance / "klines_1m.json").write_text(json.dumps(klines(T0 - 30 * MIN_MS, 120)))
    (binance / "oi_5m.json").write_text("[]")
    (binance / "funding.json").write_text(json.dumps([{"fundingTime": T0 + 1_000_000, "fundingRate": "0.0002"}]))
    (binance / "premium_1m.json").write_text("[]")
    RUN.run(root, binance, tmp_path / "out")
    events = [json.loads(l) for l in (tmp_path / "out" / "advisor_events.jsonl").read_text().splitlines()]
    ev = next(e for e in events if e["family"] == "F3" and e["timing"] == "T1")
    assert ev["direction"] == "LONG" and ev["targets"] == [83_100.0]
    t1 = ev["T1"]
    assert t1["exit"] == "TARGET" and t1["time_to_exit_s"] == pytest.approx(2_000 - 20)
    gross = (83_100 - 82_993) / 82_993 * 1e4
    assert t1["gross_bp"] == pytest.approx(gross)
    assert t1["net_bp"] == pytest.approx(gross - K.RT_COST_BP - 0.0002 * 1e4)
    assert t1["R"] == pytest.approx(t1["reward_bp"] / ev["risk_bp_v1"])


def test_stats_holm_and_bootstrap_are_deterministic():
    adj = ST.holm({"a": 0.01, "b": 0.04, "c": None})
    assert adj == {"a": 0.02, "b": 0.04, "c": None}
    values = list(np.random.default_rng(1).normal(3, 1, 200))
    times = [T0 + i * 60_000 for i in range(200)]
    x = ST.block_bootstrap(values, times)
    y = ST.block_bootstrap(values, times)
    assert x == y and x["p"] < 0.05 and x["ci95"][0] > 0
    assert ST.concentration([1, 1, 1, 1, 1, 1, 1, 1, 1, 10]) == pytest.approx(10 / 19)


def test_preview_verdict_is_no_trade_without_a_passing_cell(scripted, tmp_path):
    rep = replay(scripted["journal"])
    bn = RUN.load_binance(scripted["binance"])
    series = Series.build(rep.path, bn["klines_1m"], bn["oi_5m"], bn["funding"], bn["premium_1m"])
    results = RUN.run(scripted["journal"], scripted["binance"], tmp_path / "a")
    out = A.verdict_at(rep, series, T0 + 25_000, results["advisor"])
    assert out["verdict"] == "NO_TRADE" and out["passing_cells"] == []
    assert len(out["analysis_request_id"]) == 16
