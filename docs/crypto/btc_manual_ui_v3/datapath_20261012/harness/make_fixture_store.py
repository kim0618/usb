"""DEBUG-ONLY: a hand-built entry store (class C, preview fixture) in the recorder's row shapes, for the
labelled '검증용 기록' preview only. Entry = simulated LONG 0.010 at the fixed snapshot's collector mid,
collector fields from that same cached snapshot, market fields from the local API's derivatives read.
Carries `preview_note`, so the production API refuses it unless --allow-fixture-entry."""
import json, sys
latest = json.load(open(sys.argv[1])); api = json.load(open(sys.argv[2])); out = sys.argv[3]
import os; os.makedirs(out, exist_ok=True)
ms = latest["written_ms"]; mid = latest["liquidity"]["book"]["mid"]
w60 = latest["flow"]["windows"]["60s"]
def wall(side):
    s = latest["liquidity"]["sides"][side]
    return {"wall_state": s["wall_state"], "price": (s.get("nearest_wall") or {}).get("price")}
d = api["derivatives"]
ev = {"event_id": "9000000000003", "order_id": 9000000000003, "event_type": "OPEN_LONG", "origin": "MANUAL_TERMINAL",
      "direction": "LONG", "episode_id": "ep-preview-long", "first_fill_ms": ms, "last_fill_ms": ms, "qty": "0.010",
      "vwap": f"{float(mid):.1f}", "fills": 1, "commission": None, "realized_pnl": "0", "client_order_id": None,
      "qty_before": "0", "qty_after": "0.010", "vwap_entry_before": None, "vwap_entry_after": f"{float(mid):.1f}",
      "episode_realized_before": "0"}
snap = {"snapshot_id": "ev:9000000000003", "kind": "EVENT", "as_of_ms": ms,
        "market": {"last_1m_close": float(mid), "mark": float(mid), "index": float(mid),
                   "basis_bp": float(d["basis_pct"]) * 100 if d["basis_pct"] else None,
                   "funding_rate_last": float(d["funding_pct"]) / 100 if d["funding_pct"] else None,
                   "oi_last_value_usdt": float(d["oi_value_usdt"]) if d["oi_value_usdt"] else None,
                   "oi_change_1h_pct": float(d["oi_change_pct"]) if d["oi_change_pct"] else None},
        "collector": {"row_ms": ms, "age_ms": 0, "book": {"mid": mid},
                      "liquidity": {"ASK": wall("ASK"), "BID": wall("BID")},
                      "flow": {"60s": {"buy_usdt": w60["buy_usdt"], "sell_usdt": w60["sell_usdt"], "state": w60["state"]}}},
        "episode_id": "ep-preview-long", "event_id": "9000000000003",
        "layer_status": {"market": {"status": "OK", "reason": None}, "collector": {"status": "OK", "reason": None}},
        "preview_note": "hand-built for the V3 data-path preview (class C fixture); not a recorder output"}
open(f"{out}/events.jsonl", "w").write(json.dumps(ev) + "\n")
open(f"{out}/snapshots.jsonl", "w").write(json.dumps(snap) + "\n")
open(f"{out}/episodes.jsonl", "w").write("")
print("fixture store", out, ev["vwap"], w60["buy_usdt"], w60["sell_usdt"])
