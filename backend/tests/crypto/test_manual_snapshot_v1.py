"""Manual Trade Snapshot Store V1: contract section 10."""
from __future__ import annotations

import ast
import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.manual_snapshot_v1 import episodes as EP
from app.crypto.manual_snapshot_v1 import recorder as R
from app.crypto.manual_snapshot_v1.collector import Journal
from app.crypto.manual_snapshot_v1.features import Window, tf_features, wilder_atr
from app.crypto.manual_snapshot_v1.market import Market
from app.crypto.manual_snapshot_v1.sources import read_s1, read_s2
from app.crypto.manual_snapshot_v1.store import Store, StoreLocked

PKG = Path(R.__file__).resolve().parent
T0 = 1_791_000_000_000 // 3_600_000 * 3_600_000      # an hour boundary
MIN = 60_000


# --------------------------------------------------------------------------- fixtures

def price_at(ms: int) -> float:
    return 80_000.0 + ((ms // MIN) % 97) * 3.0


class FakeVenue:
    """Deterministic public data; records every request so tests can count them."""

    def __init__(self, bump_after: int | None = None):
        self.calls: list[tuple] = []
        self.bump_after = bump_after

    def p(self, ms: int) -> float:
        x = price_at(ms)
        return x * 1.5 if self.bump_after is not None and ms >= self.bump_after else x

    def __call__(self, path: str, params: dict) -> list:
        self.calls.append((path, dict(params)))
        a, b = int(params["startTime"]), int(params["endTime"])
        if path in ("/fapi/v1/klines", "/fapi/v1/markPriceKlines", "/fapi/v1/indexPriceKlines"):
            step = {"1m": MIN, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[params["interval"]]
            t = (a + step - 1) // step * step
            out = []
            while t <= b and len(out) < params["limit"]:
                o = self.p(t)
                out.append([t, str(o), str(o + 5), str(o - 5), str(self.p(t + step - MIN)), "2.0"])
                t += step
            return out
        if path == "/fapi/v1/fundingRate":
            t = (a + 28_800_000 - 1) // 28_800_000 * 28_800_000
            out = []
            while t <= b:
                out.append({"fundingTime": t, "fundingRate": "0.0001"})
                t += 28_800_000
            return out
        if path == "/futures/data/openInterestHist":
            t = (a + 300_000 - 1) // 300_000 * 300_000
            out = []
            while t <= b and len(out) < params["limit"]:
                out.append({"timestamp": t, "sumOpenInterestValue": str(1e9 + (t // 300_000) % 50)})
                t += 300_000
            return out
        if path == "/fapi/v1/aggTrades":
            return [{"T": t, "p": str(self.p(t)), "q": "0.1"} for t in range(a, b, 5_000)]
        raise AssertionError(path)


def fill(fid, oid, side, qty, price, t, cid, rpnl="0", fee="0.01"):
    return {"id": fid, "order_id": oid, "symbol": "BTCUSDT", "side": side, "qty": str(qty),
            "price": str(price), "realized_pnl": str(rpnl), "commission": str(fee), "time_ms": t,
            "client_order_id": cid, "manual": cid.startswith("usbm-") if cid else None}


def write_s1(path: Path, fills: list[dict], last_ms: int, funding=()):
    path.write_text(json.dumps({"version": 1, "trades": fills, "funding": [
        {"time_ms": t, "income": str(v), "symbol": "BTCUSDT"} for t, v in funding],
        "last_calculated_ms": last_ms, "classification_complete": True}))


def write_s2(path: Path, sends: list[tuple], first_ms: int):
    lines = [{"seq": 1, "ts_ms": first_ms, "event_type": "LIVE_DISARM"}]
    for cid, ts, perm in sends:
        lines.append({"seq": len(lines) + 1, "ts_ms": ts - 5, "event_type": "LIVE_ORDER_INTENT",
                      "stage": "PLAN", "symbol": "BTCUSDT", "side": "LONG", "intent": "OPEN"})
        row = {"seq": len(lines) + 1, "ts_ms": ts, "event_type": "LIVE_ORDER_SENT",
               "client_order_id": cid, "params": {"symbol": "BTCUSDT"}}
        if perm:
            row["permission"] = perm
        lines.append(row)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def write_journal(root: Path, start: int, end: int, step: int = 1_000, gz: bool = True):
    (root / "context").mkdir(parents=True, exist_ok=True)
    rows = []
    for t in range(start, end, step):
        rows.append(json.dumps({"receive_ms": t, "session_id": "S", "payload": {
            "collector": {"state": "LIVE"}, "book": {"mid": str(price_at(t)), "state": "SYNCED"},
            "liquidity": {"ASK": {"wall_state": "OK", "nearest": {"price": str(price_at(t) + 4)}},
                          "BID": {"wall_state": "NONE"}},
            "flow": {"state": "LIVE", "windows": {"60s": {"buy_usdt": "10", "sell_usdt": "30",
                                                           "state": "LIVE"}}}}}))
    data = ("\n".join(rows) + "\n").encode()
    path = root / "context" / ("context-1-S-00001.jsonl.gz" if gz else "context-1-S-00001.jsonl.open")
    if gz:
        with gzip.open(path, "wb") as fh:
            fh.write(data)
    else:
        path.write_bytes(data)


@pytest.fixture
def env(tmp_path):
    d = {"s1": tmp_path / "s1.json", "s2": tmp_path / "s2.jsonl", "store": tmp_path / "store",
         "journal": tmp_path / "journal"}
    write_journal(d["journal"], T0 - 3_600_000, T0 + 6 * 3_600_000, step=5_000)
    return d


def run(env, now, venue=None, **kw):
    venue = venue or FakeVenue()
    return R.run_once(store_root=env["store"], s1_path=env["s1"], s2_path=env["s2"],
                      journal_root=env["journal"], market=Market(None, venue, now_ms=now),
                      now_ms=now, **kw)


def rows(env, kind):
    return Store(env["store"]).rows(kind)


# --------------------------------------------------------------------------- scenarios A-H

def test_A_long_open_close(env):
    fills = [fill(1, 11, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 12, "SELL", "0.01", 80_300, T0 + 30 * MIN, "usbm-close-2", rpnl="3")]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None), ("usbm-close-2", T0 + 30 * MIN - 1_000, None)], T0)
    run(env, T0 + 3 * 3_600_000)
    ev = rows(env, "events")
    assert [(e["event_type"], e["origin"]) for e in ev] == [("OPEN_LONG", "MANUAL_TERMINAL"), ("CLOSE", "MANUAL_TERMINAL")]
    ep = rows(env, "episodes")
    assert len(ep) == 1 and ep[0]["manual_only"] and ep[0]["direction"] == "LONG"
    assert Decimal(ep[0]["net_usdt"]) == Decimal("3") - Decimal("0.02")
    assert ep[0]["exit_return_bp"] == pytest.approx(300 / 80_000 * 1e4)
    kinds = sorted({s["kind"] for s in rows(env, "snapshots")})
    assert kinds == ["EVENT", "FOLLOWUP", "HOLD"]


def test_B_short_open_close_and_reverse_split(env):
    fills = [fill(1, 21, "SELL", "0.02", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 22, "BUY", "0.05", 79_900, T0 + 20 * MIN, "web_xyz", rpnl="2")]   # reverse
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None)], T0)
    run(env, T0 + 3 * 3_600_000)
    ev = rows(env, "events")
    assert [(e["event_id"], e["event_type"], e["origin"], e["qty"]) for e in ev] == [
        ("21", "OPEN_SHORT", "MANUAL_TERMINAL", "0.02"), ("22", "CLOSE", "EXTERNAL", "0.02"),
        ("22:open", "OPEN_LONG", "EXTERNAL", "0.03")]
    ep = rows(env, "episodes")
    assert len(ep) == 1 and ep[0]["origin_mix"] == ["EXTERNAL", "MANUAL_TERMINAL"]
    assert ep[0]["manual_only"] is False


def test_C_open_add_close_vwap(env):
    fills = [fill(1, 31, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 32, "BUY", "0.03", 80_400, T0 + 10 * MIN, "usbm-open-2"),
             fill(3, 33, "SELL", "0.04", 80_500, T0 + 40 * MIN, "usbm-close-3", rpnl="5")]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [(f["client_order_id"], f["time_ms"] - 500, None) for f in fills], T0)
    run(env, T0 + 3 * 3_600_000)
    ev = rows(env, "events")
    assert [e["event_type"] for e in ev] == ["OPEN_LONG", "ADD", "CLOSE"]
    assert Decimal(ev[1]["vwap_entry_after"]) == Decimal("80300")
    ep = rows(env, "episodes")[0]
    assert Decimal(ep["vwap_entry"]) == Decimal("80300") and ep["events"] == {"ADD": 1, "CLOSE": 1, "OPEN_LONG": 1}


def test_D_open_reduce_close(env):
    fills = [fill(1, 41, "SELL", "0.04", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 42, "BUY", "0.01", 79_800, T0 + 10 * MIN, "usbm-close-2", rpnl="2"),
             fill(3, 43, "BUY", "0.03", 79_900, T0 + 50 * MIN, "usbm-close-3", rpnl="3")]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [(f["client_order_id"], f["time_ms"] - 500, None) for f in fills], T0)
    run(env, T0 + 3 * 3_600_000)
    assert [e["event_type"] for e in rows(env, "events")] == ["OPEN_SHORT", "REDUCE", "CLOSE"]
    ep = rows(env, "episodes")[0]
    assert Decimal(ep["gross_usdt"]) == 5 and Decimal(ep["vwap_exit"]) == Decimal("79875")


def test_E_partial_fills_are_one_event_and_wait_for_settlement(env):
    fills = [fill(1, 51, "BUY", "0.004", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 51, "BUY", "0.006", 80_010, T0 + 10_000, "usbm-open-1")]
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None)], T0)
    write_s1(env["s1"], fills[:1], T0 + 30_000)          # cache refreshed before the order settled
    run(env, T0 + 3 * 3_600_000)
    assert rows(env, "events") == []
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    run(env, T0 + 3 * 3_600_000)
    ev = rows(env, "events")
    assert len(ev) == 1 and ev[0]["fills"] == 2 and Decimal(ev[0]["qty"]) == Decimal("0.010")
    assert Decimal(ev[0]["vwap"]) == Decimal("80006")


def test_F_exitguard_and_external_are_labelled_not_manual(env):
    fills = [fill(1, 61, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 62, "SELL", "0.01", 80_100, T0 + 20 * MIN, "usbm-close-2", rpnl="1"),
             fill(3, 63, "BUY", "0.01", 80_000, T0 + 40 * MIN, "android_abc"),
             fill(4, 64, "SELL", "0.01", 80_050, T0 + 50 * MIN, "usbm-close-4", rpnl="0.5")]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None),
                         ("usbm-close-2", T0 + 20 * MIN - 100, "AUTO_EXIT_CLOSE_ONLY"),
                         ("usbm-close-4", T0 + 50 * MIN - 100, None)], T0)
    run(env, T0 + 3 * 3_600_000)
    assert [e["origin"] for e in rows(env, "events")] == ["MANUAL_TERMINAL", "EXITGUARD", "EXTERNAL",
                                                          "MANUAL_TERMINAL"]
    assert [e["manual_only"] for e in rows(env, "episodes")] == [False, False]


def test_G_restart_midway_and_torn_line_give_the_same_store(env, tmp_path):
    fills = [fill(1, 71, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 72, "SELL", "0.01", 80_100, T0 + 90 * MIN, "usbm-close-2", rpnl="1")]
    write_s1(env["s1"], fills, T0 + 4 * 3_600_000)
    write_s2(env["s2"], [(f["client_order_id"], f["time_ms"] - 500, None) for f in fills], T0)
    now = T0 + 4 * 3_600_000
    # interrupted: only a few snapshots per run, then a torn line and a lost cursor
    run(env, now, max_snapshots=7)
    with open(env["store"] / "snapshots.jsonl", "ab") as fh:
        fh.write(b'{"snapshot_id": "torn')
    (env["store"] / "cursor.json").unlink()
    (env["store"] / "runtime" / "s2_index.json").unlink()
    for _ in range(6):
        run(env, now, max_snapshots=7)
    run(env, now)
    ref = dict(env, store=tmp_path / "ref")
    run(ref, now)
    strip = lambda rs: sorted(json.dumps({k: v for k, v in r.items() if k not in ("recorded_at_ms", "backfilled")},
                                         sort_keys=True) for r in rs)
    for kind in ("events", "snapshots", "episodes"):
        assert strip(rows(env, kind)) == strip(Store(ref["store"]).rows(kind)), kind


def test_H_duplicate_source_replay_and_rewrite_write_nothing_twice(env):
    fills = [fill(1, 81, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 82, "SELL", "0.01", 80_100, T0 + 20 * MIN, "usbm-close-2", rpnl="1")]
    write_s1(env["s1"], fills + fills, T0 + 3 * 3_600_000)          # duplicated trade ids
    write_s2(env["s2"], [(f["client_order_id"], f["time_ms"] - 500, None) for f in fills], T0)
    run(env, T0 + 3 * 3_600_000)
    before = {k: len(rows(env, k)) for k in ("events", "snapshots", "episodes")}
    write_s1(env["s1"], list(reversed(fills)), T0 + 3 * 3_600_000)   # rewritten, reordered
    run(env, T0 + 3 * 3_600_000)
    write_s1(env["s1"], fills[:1], T0 + 3 * 3_600_000)              # history lost
    cur = run(env, T0 + 3 * 3_600_000)
    after = {k: len(rows(env, k)) for k in ("events", "snapshots", "episodes")}
    assert before == after and before["events"] == 2
    assert cur["status"] == "HALTED_S1_DISAGREES_WITH_STORE"
    assert cur["anomalies"]["events_no_longer_in_s1"] == ["82"]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)                 # S1 agrees again
    assert run(env, T0 + 3 * 3_600_000)["status"] == "OK"
    keys = [r["snapshot_id"] for r in rows(env, "snapshots")]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- properties

def test_no_future_leak_in_any_snapshot(env, tmp_path):
    fills = [fill(1, 91, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1")]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None)], T0)
    now = T0 + 3 * 3_600_000
    run(env, now)
    bumped = dict(env, store=tmp_path / "b")
    # Data after the event's as_of is changed; the EVENT snapshot must not move.
    venue = FakeVenue(bump_after=T0 + 10_000)
    run(bumped, now, venue=venue)
    a = next(s for s in rows(env, "snapshots") if s["snapshot_id"] == "ev:91")
    b = next(s for s in Store(bumped["store"]).rows("snapshots") if s["snapshot_id"] == "ev:91")
    drop = lambda s: {k: v for k, v in s.items() if k not in ("recorded_at_ms", "timeframes")}
    assert drop(a) == drop(b)
    for tf in ("1m", "10m", "1h", "4h", "1d"):
        assert a["timeframes"][tf] == b["timeframes"][tf], tf


def test_journal_as_of_is_newest_row_at_or_before(tmp_path):
    write_journal(tmp_path, T0, T0 + 60_000, step=10_000, gz=False)
    j = Journal(tmp_path)
    got = j.rows_at([T0 - 1, T0, T0 + 15_000, T0 + 10 * 60_000])
    assert got[T0 - 1] is None
    assert got[T0]["receive_ms"] == T0 and got[T0 + 15_000]["receive_ms"] == T0 + 10_000
    assert got[T0 + 10 * 60_000]["receive_ms"] == T0 + 50_000       # stale, kept with its age


def test_missing_collector_and_missing_market_are_marked_not_guessed(env):
    fills = [fill(1, 95, "BUY", "0.01", 80_000, T0 - 2 * 3_600_000, "usbm-open-1")]   # before journal
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 - 2 * 3_600_000 - 500, None)], T0 - 3 * 3_600_000)

    def broken(path, params):
        raise OSError("venue down")
    with pytest.raises(OSError):
        run(env, T0 + 3 * 3_600_000, venue=broken)       # nothing half-written on a venue outage
    assert rows(env, "snapshots") == []
    run(env, T0 + 3 * 3_600_000)
    ev = next(s for s in rows(env, "snapshots") if s["snapshot_id"] == "ev:95")
    assert ev["layer_status"]["collector"]["status"] == "NOT_AVAILABLE" and ev["collector"] == {}
    assert ev["liquidation"]["status"] == "NOT_COLLECTED" and ev["c1"]["status"] == "NOT_COLLECTED"


def test_mfe_mae_and_atr_hand_checks():
    bars = [(i * MIN, 100.0, 102.0, 99.0, 101.0, 1.0) for i in range(16)]
    assert wilder_atr(bars) == pytest.approx(3.0)
    f = tf_features([(0, 10, 12, 9, 10, 1), (MIN, 10, 11, 8, 11, 2)])
    assert f["ret_bp"] == pytest.approx(1000.0) and f["range_bp"] == pytest.approx(4 / 11 * 1e4)
    assert f["status"] == "PARTIAL" and f["atr14"] is None
    v = FakeVenue()
    w = Window(Market(None, v, now_ms=T0 + 86_400_000), T0, T0 + 3_600_000)
    p = w.path(T0 + 5 * MIN, T0 + 9 * MIN + 1, 80_000.0, "LONG")
    hi = max(price_at(T0 + k * MIN) for k in range(5, 10)) + 5
    lo = min(price_at(T0 + k * MIN) for k in range(5, 10)) - 5
    assert p["mfe_bp"] == pytest.approx(max((hi - 80_000) / 80_000 * 1e4, 0))
    assert p["mae_bp"] == pytest.approx(max((80_000 - lo) / 80_000 * 1e4, 0))


def test_btc_only_gate(tmp_path):
    p = tmp_path / "s1.json"
    f = fill(1, 1, "BUY", "1", 2_000, T0, "usbm-open-1")
    write_s1(p, [f, {**f, "id": 2, "symbol": "ETHUSDT"}], T0)
    assert [x.id for x in read_s1(p).fills] == [1]


def test_s2_incremental_index_and_rotation(tmp_path):
    p, idx = tmp_path / "m.jsonl", tmp_path / "idx.json"
    write_s2(p, [("usbm-open-1", T0, None)], T0 - 1)
    a = read_s2(p, index_path=idx)
    with open(p, "ab") as fh:
        fh.write(json.dumps({"ts_ms": T0 + 5, "event_type": "LIVE_ORDER_SENT", "client_order_id": "usbm-close-2",
                             "params": {"symbol": "BTCUSDT"}, "permission": "AUTO_EXIT_CLOSE_ONLY"}).encode() + b"\n")
        fh.write(b'{"ts_ms": 1, "event_type": "LIVE_ORDER_SE')          # torn tail
    b = read_s2(p, index_path=idx)
    assert set(b.sent_ms) == {"usbm-open-1", "usbm-close-2"} and b.auto_exit_clients == {"usbm-close-2"}
    assert b.lines == a.lines + 1
    write_s2(p, [("usbm-open-9", T0, None)], T0 + 100)               # rotated / replaced
    c = read_s2(p, index_path=idx)
    assert set(c.sent_ms) == {"usbm-open-9"} and c.first_ms == T0 + 100


def test_single_writer_lock(tmp_path):
    a, b = Store(tmp_path), Store(tmp_path)
    a.lock()
    with pytest.raises(StoreLocked):
        b.lock()
    a.unlock()
    b.lock()
    b.unlock()


def test_episode_math_reverse_free_position_tracking():
    s1 = read_s1.__globals__["S1"]([], [], T0, True)
    assert EP.derive(s1, read_s2(Path("/nonexistent"))) == ([], [])


# --------------------------------------------------------------------------- isolation

FORBIDDEN = ("app.crypto.live", "app.crypto.terminal", "app.crypto.paper", "app.crypto.context_collector_v1",
             "app.crypto.research.q123_forward_v1", "app.crypto.hot", "app.crypto.c1")


def test_package_imports_nothing_that_trades_or_mutates():
    for f in PKG.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for n in names:
                assert not n.startswith(FORBIDDEN), (f.name, n)


def test_package_writes_only_through_store_or_its_cache():
    """Static: open(..., 'w'/'a') and write_text/replace appear only in store.py and market.py
    (the market cache) and sources.py (the S2 index under the store's runtime dir)."""
    allowed = {"store.py", "market.py", "sources.py"}
    for f in PKG.glob("*.py"):
        text = f.read_text()
        writes = any(s in text for s in ('"ab"', '"wb"', "'w'", '"w"', "write_text(", "os.replace("))
        assert not writes or f.name in allowed, f.name


def test_http_is_get_only():
    text = (PKG / "market.py").read_text() + (PKG / "recorder.py").read_text()
    assert 'method="GET"' in text and "POST" not in text and "DELETE" not in text and "PUT" not in text


def test_unclassified_order_waits_instead_of_freezing_an_unknown_origin(env):
    fills = [fill(1, 101, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             {**fill(2, 102, "SELL", "0.01", 80_100, T0 + 20 * MIN, "x", rpnl="1"), "client_order_id": None},
             fill(3, 103, "BUY", "0.01", 80_000, T0 + 40 * MIN, "usbm-open-3")]
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None), ("usbm-close-2", T0 + 20 * MIN - 100, None),
                         ("usbm-open-3", T0 + 40 * MIN - 100, None)], T0)
    cur = run(env, T0 + 3 * 3_600_000)
    assert [e["event_id"] for e in rows(env, "events")] == ["101"]
    assert cur["waiting_for_s1_classification"] == [102]
    fills[1]["client_order_id"] = "usbm-close-2"                     # S1's retry classified it
    write_s1(env["s1"], fills, T0 + 3 * 3_600_000)
    cur = run(env, T0 + 3 * 3_600_000)
    assert cur["status"] == "OK"
    assert [(e["event_id"], e["origin"]) for e in rows(env, "events")] == [
        ("101", "MANUAL_TERMINAL"), ("102", "MANUAL_TERMINAL"), ("103", "MANUAL_TERMINAL")]


def test_hold_path_covers_the_whole_episode_even_when_runs_are_chunked(env, tmp_path):
    fills = [fill(1, 111, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1")]
    write_s1(env["s1"], fills, T0 + 20 * 3_600_000)
    write_s2(env["s2"], [("usbm-open-1", T0 + 9_000, None)], T0)
    now = T0 + 20 * 3_600_000
    for _ in range(40):
        if run(env, now, max_snapshots=9)["deferred_snapshots"] == 0:
            break
    ref = dict(env, store=tmp_path / "one")
    run(ref, now)
    late = "hold:ep-111:%d" % (T0 + 19 * 3_600_000)
    a = next(s for s in rows(env, "snapshots") if s["snapshot_id"] == late)
    b = next(s for s in Store(ref["store"]).rows("snapshots") if s["snapshot_id"] == late)
    assert a["path_bars"] == b["path_bars"] and a["path_bars"] >= 19 * 60
    assert (a["path_mfe_bp"], a["path_mae_bp"]) == (b["path_mfe_bp"], b["path_mae_bp"])


def _fills_one_episode(env):
    fills = [fill(1, 121, "BUY", "0.01", 80_000, T0 + 10_000, "usbm-open-1"),
             fill(2, 122, "SELL", "0.01", 80_100, T0 + 90 * MIN, "usbm-close-2", rpnl="1")]
    write_s1(env["s1"], fills, T0 + 4 * 3_600_000)
    write_s2(env["s2"], [(f["client_order_id"], f["time_ms"] - 500, None) for f in fills], T0)


def test_request_budget_defers_and_converges_to_the_unbudgeted_store(env, tmp_path):
    _fills_one_episode(env)
    now = T0 + 4 * 3_600_000
    cache = tmp_path / "cache"
    statuses = []
    for _ in range(60):
        cur = R.run_once(store_root=env["store"], s1_path=env["s1"], s2_path=env["s2"],
                         journal_root=env["journal"], now_ms=now,
                         market=Market(cache, FakeVenue(), now_ms=now, budget=3))
        statuses.append(cur["market_status"])
        if cur["market_status"] == "OK" and cur["deferred_snapshots"] == 0:
            break
    assert statuses[0] == "DEFERRED_REQUEST_BUDGET" and statuses[-1] == "OK"
    ref = dict(env, store=tmp_path / "ref")
    run(ref, now)
    strip = lambda rs: sorted(json.dumps({k: v for k, v in r.items() if k not in ("recorded_at_ms", "backfilled")},
                                         sort_keys=True) for r in rs)
    for kind in ("events", "snapshots", "episodes"):
        assert strip(rows(env, kind)) == strip(Store(ref["store"]).rows(kind)), kind


def test_venue_backoff_is_persisted_and_respected(env):
    from app.crypto.manual_snapshot_v1.market import VenueBackoff
    _fills_one_episode(env)
    now = T0 + 4 * 3_600_000

    def limited(path, params):
        raise VenueBackoff(429, 120)
    cur = run(env, now, venue=limited)
    assert cur["market_status"] == "VENUE_BACKOFF_429" and cur["backoff_until_ms"] == now + 120_000
    assert rows(env, "snapshots") == [] and len(rows(env, "events")) == 2   # events need no market
    v = FakeVenue()
    cur = run(env, now + 60_000, venue=v)
    assert cur["market_status"] == "BACKOFF_ACTIVE" and v.calls == []
    cur = run(env, now + 121_000, venue=v)
    assert cur["market_status"] == "OK" and len(rows(env, "snapshots")) > 0


def test_open_chunk_is_extended_by_its_tail_only(tmp_path):
    v = FakeVenue()
    now = T0 + 3_600_000
    Market(tmp_path, v, now_ms=now).klines("1m", T0, now)
    first = len(v.calls)
    Market(tmp_path, v, now_ms=now + 5 * MIN).klines("1m", T0, now + 5 * MIN)
    tail = v.calls[first:]
    assert len(tail) == 1 and tail[0][1]["startTime"] == now and tail[0][1]["limit"] <= 7
