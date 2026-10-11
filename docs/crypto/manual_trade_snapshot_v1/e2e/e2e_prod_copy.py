"""Offline E2E of Manual Trade Snapshot Store V1 on read-only copies of production S1/S2 and the
Context Collector journal copy (contract section 9 C). Public market data is fetched by GET.

    PYTHONPATH=backend python docs/crypto/manual_trade_snapshot_v1/e2e/e2e_prod_copy.py PROD_COPY JOURNAL WORK
"""
import json
import shutil
import sys
from pathlib import Path

from app.crypto.manual_snapshot_v1 import recorder as R
from app.crypto.manual_snapshot_v1.market import Market
from app.crypto.manual_snapshot_v1.store import Store

PROD, JOURNAL, WORK = (Path(a) for a in sys.argv[1:4])
NOW = 1_791_723_000_000                       # 2026-10-11 21:30 KST, inside the journal copy
CACHE = WORK / "market_cache"
results = {}


def run(store, s1, s2, **kw):
    return R.run_once(store_root=store, s1_path=s1, s2_path=s2, journal_root=JOURNAL,
                      market=Market(CACHE, now_ms=NOW, spacing_s=0.3), now_ms=NOW, **kw)


def rows(store, kind):
    return Store(store).rows(kind)


def strip(rs):
    return sorted(json.dumps({k: v for k, v in r.items() if k not in ("recorded_at_ms", "backfilled")},
                             sort_keys=True) for r in rs)


s1, s2 = PROD / "manual_performance.json", PROD / "binance_live_events.jsonl"
if WORK.exists():
    for p in WORK.iterdir():
        if p.name != "market_cache":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
WORK.mkdir(parents=True, exist_ok=True)

# ---- A, B, C, E: the real history ------------------------------------------------------------
base = WORK / "base"
cur = run(base, s1, s2)
ev = rows(base, "events")
ep = rows(base, "episodes")
types = {}
for e in ev:
    types[e["event_type"]] = types.get(e["event_type"], 0) + 1
s1raw = json.loads(s1.read_text())
results["A_B_C_E_real_history"] = {
    "events": len(ev), "event_types": types, "origins": sorted({e["origin"] for e in ev}),
    "multi_fill_orders": sum(1 for e in ev if e["fills"] > 1),
    "closed_episodes": len(ep), "open_episode": cur["open_episode"],
    "gross_sum_equals_s1_realized": abs(sum(float(x["gross_usdt"]) for x in ep)
                                        - sum(float(t["realized_pnl"]) for t in s1raw["trades"])) < 1e-9,
    "snapshots": len(rows(base, "snapshots")), "status": cur["status"]}

# ---- H: duplicate replay ---------------------------------------------------------------------
again = run(base, s1, s2)
results["H_replay_writes_nothing"] = again["written"] == {"events": 0, "snapshots": 0, "episodes": 0}

# ---- G: restart midway, torn line, lost cursor and index --------------------------------------
g = WORK / "restart"
run(g, s1, s2, max_snapshots=400)
with open(g / "snapshots.jsonl", "ab") as fh:
    fh.write(b'{"snapshot_id":"torn')
(g / "cursor.json").unlink()
(g / "runtime" / "s2_index.json").unlink()
for _ in range(20):
    c = run(g, s1, s2, max_snapshots=400)
    if c["deferred_snapshots"] == 0:
        break
results["G_restart_equals_single_run"] = all(strip(rows(g, k)) == strip(rows(base, k))
                                             for k in ("events", "snapshots", "episodes"))
results["G_torn_bytes_repaired"] = c.get("repaired_torn_bytes")

# ---- F: an ExitGuard close mixed into the real history ---------------------------------------
f = WORK / "exitguard"
f.mkdir()
s2f = f / "mirror.jsonl"
shutil.copy(s2, s2f)
with open(s2f, "ab") as fh:
    fh.write(json.dumps({"ts_ms": 1791339735813, "event_type": "LIVE_ORDER_SENT",
                         "client_order_id": "usbm-close-1791339735811", "permission": "AUTO_EXIT_CLOSE_ONLY",
                         "params": {"symbol": "BTCUSDT"}}).encode() + b"\n")
run(f / "store", s1, s2f)
fev = {e["event_id"]: e for e in rows(f / "store", "events")}
fep = {e["episode_id"]: e for e in rows(f / "store", "episodes")}
results["F_exitguard_separated"] = {
    "event_1156415453502_origin": fev["1156415453502"]["origin"],
    "episode_manual_only": fep["ep-1154650958495"]["manual_only"],
    "other_events_unchanged": all(fev[k]["origin"] == e["origin"] for k, e in
                                  ((e["event_id"], e) for e in ev) if k != "1156415453502")}

# ---- D: reduce then close of the real open LONG 0.108 (synthetic fills appended to a copy) -----
d = WORK / "reduce"
d.mkdir()
raw = json.loads(s1.read_text())
raw["trades"] += [
    {"id": 9_000_000_001, "order_id": 9_100_000_001, "symbol": "BTCUSDT", "side": "SELL", "qty": "0.050",
     "price": "83200.0", "realized_pnl": "-35.655", "commission": "2.08", "time_ms": 1_791_700_000_000,
     "client_order_id": "usbm-close-1791699999990", "manual": True},
    {"id": 9_000_000_002, "order_id": 9_100_000_002, "symbol": "BTCUSDT", "side": "SELL", "qty": "0.058",
     "price": "83300.0", "realized_pnl": "-35.5598", "commission": "2.42", "time_ms": 1_791_710_000_000,
     "client_order_id": "usbm-close-1791709999990", "manual": True}]
raw["last_calculated_ms"] = NOW
s1d = d / "perf.json"
s1d.write_text(json.dumps(raw))
s2d = d / "mirror.jsonl"
shutil.copy(s2, s2d)
with open(s2d, "ab") as fh:
    for cid, ts in (("usbm-close-1791699999990", 1_791_699_999_995), ("usbm-close-1791709999990", 1_791_709_999_995)):
        fh.write(json.dumps({"ts_ms": ts, "event_type": "LIVE_ORDER_SENT", "client_order_id": cid,
                             "params": {"symbol": "BTCUSDT", "reduceOnly": "true"}}).encode() + b"\n")
cd = run(d / "store", s1d, s2d)
dev = rows(d / "store", "events")
dep = {e["episode_id"]: e for e in rows(d / "store", "episodes")}
snaps = {s["snapshot_id"]: s for s in rows(d / "store", "snapshots")}
results["D_reduce_close"] = {
    "last_two_types": [e["event_type"] for e in dev[-2:]],
    "episode": {k: dep["ep-1156416087528"][k] for k in ("events", "gross_usdt", "fees_usdt", "net_usdt",
                                                       "vwap_entry", "vwap_exit", "mfe_bp", "mae_bp")},
    "reduce_snapshot_collector": snaps["ev:9100000001"]["layer_status"]["collector"],
    "reduce_snapshot_15s": snaps["ev:9100000001"]["layer_status"]["timeframes"]["15s"],
    "open_episode_after": cd["open_episode"]}

print(json.dumps(results, indent=1, default=str))
(WORK / "e2e_results.json").write_text(json.dumps(results, indent=1, default=str))
