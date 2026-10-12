"""Preview entry stores in the recorder's row shapes.

real_long : the open LIVE LONG (S1 fill 2026-10-07 11:22:58 KST, order 1156416087528 @ 83913.1).
            Collector NOT_AVAILABLE (the collector started 2026-10-10 21:47), market as of the fill.
sim_short / sim_long : simulated entries at 2026-10-12 08:20:00 KST at the collector mid, with the
            collector row from the production journal copy, read by the recorder's own reader.
Market fields: public Binance REST as of each time (8 GETs total), computed as features.market_fields does.
"""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path
from app.crypto.manual_snapshot_v1.collector import Journal, collector_fields

S = Path(sys.argv[1]); JOURNAL = S / "journal"; OUT = S / "stores"
BASE = "https://fapi.binance.com"

def get(path, **params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "usb-preview"}), timeout=10) as r:
        time.sleep(0.4)
        return json.loads(r.read())

def market(as_of):
    m1 = 60_000
    start = (as_of // m1 - 3) * m1
    mk = [k for k in get("/fapi/v1/markPriceKlines", symbol="BTCUSDT", interval="1m", startTime=start, endTime=as_of, limit=5) if k[6] < as_of]
    ix = [k for k in get("/fapi/v1/indexPriceKlines", pair="BTCUSDT", interval="1m", startTime=start, endTime=as_of, limit=5) if k[6] < as_of]
    fr = [r for r in get("/fapi/v1/fundingRate", symbol="BTCUSDT", startTime=as_of - 9 * 3_600_000, endTime=as_of, limit=5) if r["fundingTime"] <= as_of]
    oi = [r for r in get("/futures/data/openInterestHist", symbol="BTCUSDT", period="5m", startTime=as_of - 2 * 3_600_000, endTime=as_of, limit=30)
          if int(r["timestamp"]) + 300_000 <= as_of]
    out = {"mark": float(mk[-1][4]), "index": float(ix[-1][4])}
    out["basis_bp"] = round((out["mark"] - out["index"]) / out["index"] * 1e4, 4)
    out["funding_rate_last"] = float(fr[-1]["fundingRate"]) if fr else None
    out["oi_last_value_usdt"] = float(oi[-1]["sumOpenInterestValue"]) if oi else None
    hour = [r for r in oi if int(r["timestamp"]) <= int(oi[-1]["timestamp"]) - 3_600_000]
    out["oi_change_1h_pct"] = round((float(oi[-1]["sumOpenInterestValue"]) / float(hour[-1]["sumOpenInterestValue"]) - 1) * 100, 4) if hour else None
    out["last_1m_close"] = out["mark"]
    return out

def event(eid, etype, direction, ms, qty_after, vwap, episode):
    return {"event_id": eid, "order_id": int(eid), "event_type": etype, "origin": "MANUAL_TERMINAL",
            "direction": direction, "episode_id": episode, "first_fill_ms": ms, "last_fill_ms": ms,
            "qty": qty_after.lstrip("-"), "vwap": vwap, "fills": 1, "commission": None, "realized_pnl": "0",
            "client_order_id": None, "qty_before": "0", "qty_after": qty_after,
            "vwap_entry_before": None, "vwap_entry_after": vwap, "episode_realized_before": "0"}

def write(name, ev, as_of, mkt, cfields, cstatus, creason):
    root = OUT / name; root.mkdir(parents=True, exist_ok=True)
    snap = {"snapshot_id": f"ev:{ev['event_id']}", "kind": "EVENT", "as_of_ms": as_of,
            "market": mkt, "collector": cfields, "episode_id": ev["episode_id"], "event_id": ev["event_id"],
            "layer_status": {"market": {"status": "OK", "reason": None},
                             "collector": {"status": cstatus, "reason": creason}},
            "preview_note": "built for the V3 preview in the recorder's row shapes; not a recorder output"}
    (root / "events.jsonl").write_text(json.dumps(ev) + "\n")
    (root / "snapshots.jsonl").write_text(json.dumps(snap) + "\n")
    (root / "episodes.jsonl").write_text("")
    print(name, ev["direction"], ev["vwap"], cstatus, json.dumps(mkt)[:200])

# real LONG
REAL_MS = 1791339778021
cf, cs, cr = collector_fields(None, REAL_MS)
write("real_long", event("1156416087528", "OPEN_LONG", "LONG", REAL_MS, "0.108", "83913.1", "ep-1156416087528"),
      REAL_MS, market(REAL_MS), cf, cs, cr)
# simulated entries at 08:20:00 KST
SIM_MS = 1791760800000
row = Journal(JOURNAL).rows_at([SIM_MS])[SIM_MS]
cf, cs, cr = collector_fields(row, SIM_MS)
mid = cf["book"]["mid"]
mkt = market(SIM_MS)
print("journal row", cs, cr, "mid", mid, "flow60", cf["flow"]["60s"], "walls", cf["liquidity"])
price = f"{float(mid):.1f}"
write("sim_short", event("9000000000001", "OPEN_SHORT", "SHORT", SIM_MS, "-0.010", price, "ep-9000000000001"), SIM_MS, mkt, cf, cs, cr)
write("sim_long", event("9000000000002", "OPEN_LONG", "LONG", SIM_MS, "0.010", price, "ep-9000000000002"), SIM_MS, mkt, cf, cs, cr)
