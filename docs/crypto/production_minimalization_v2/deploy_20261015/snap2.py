"""Read-only pre/post snapshot for the C1 production removal (no signed calls, no writes)."""
import hashlib, json, pathlib, subprocess, time, urllib.request

def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

def get(url, timeout=30):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return type(e).__name__, None

P = pathlib.Path("/root/usb_runtime/crypto_paper")
out = {"taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

runs = {}
for d in sorted(p for p in (P / "run").iterdir() if p.is_dir() and p.name.startswith("paper-server-001")):
    led = d / "ledger.jsonl"
    if not led.exists():
        continue
    raw = led.read_bytes(); lines = raw.splitlines()
    last = json.loads(lines[-1]) if lines else {}
    orders = sum(1 for l in lines if b'"ORDER_' in l or b'"FILL"' in l)
    runs[d.name] = {"ledger_sha256": hashlib.sha256(raw).hexdigest(), "events": len(lines),
                    "last_seq": last.get("seq"), "last_event": last.get("event_type"),
                    "order_like_lines": orders}
    tape = d / "input.jsonl"
    if d.name == "paper-server-001" and tape.exists():
        runs[d.name]["active_tape_bytes"] = tape.stat().st_size
out["paper_runs"] = runs
st = P / "run/paper-server-001/c1_auto_state.json"
out["c1_auto_state"] = json.loads(st.read_text()) if st.exists() else None
c1s = P / "data/runtime/crypto/c1/state.json"
out["c1_runtime_state"] = json.loads(c1s.read_text()) if c1s.exists() else None
if c1s.exists():
    out["c1_runtime_state_mtime"] = time.strftime("%H:%M:%S", time.localtime(c1s.stat().st_mtime))

code, s = get("http://127.0.0.1:8100/api/crypto/state")
out["state_http"] = code
if s:
    a = s.get("account") or {}
    out["paper_state"] = {"paper_source": s.get("paper_source"), "has_c1_auto_key": "c1_auto" in s,
        "has_c1_available_key": "c1_available" in s, "symbol": s.get("symbol"), "leverage": s.get("leverage"),
        "ledger_event_count": s.get("ledger_event_count"),
        "account": {k: a.get(k) for k in ("position_side", "position_qty", "avg_entry", "leverage",
            "equity", "wallet_balance", "available_balance", "used_margin", "realized_pnl",
            "unrealized_pnl", "cumulative_fees", "cumulative_funding_paid")}}
code, _ = get("http://127.0.0.1:8100/api/crypto/c1/state", 15)
out["route_c1_state_http"] = code
code, _ = get("http://127.0.0.1:8100/api/crypto/paper/c1-auto", 15)
out["route_paper_c1_auto_http"] = code

L = P / "data/runtime/crypto/live"
mirror = L / "487ea08e/binance_live_events.jsonl"
last = None; tail_last = None
with mirror.open("rb") as fh:
    fh.seek(max(0, mirror.stat().st_size - 300_000))
    rows = fh.read().splitlines()[1:]
for line in rows:
    ev = json.loads(line); tail_last = ev
    if ev.get("event_type") == "LIVE_RECONCILE":
        last = ev
pos = last["position"]
out["live"] = {"mirror_bytes": mirror.stat().st_size, "mirror_sha256": sha(mirror),
    "mirror_last_seq": tail_last.get("seq"), "mirror_last_event": tail_last.get("event_type"),
    "last_reconcile_seq": last["seq"], "wallet_balance": last["wallet_balance"],
    "position": {k: pos.get(k) for k in ("symbol", "side", "qty", "entry_price", "is_flat", "initial_margin", "notional")},
    "exit_guard": json.loads((L / "exit_guard.json").read_text()),
    "exit_guard_sha256": sha(L / "exit_guard.json"),
    "leverage_capability_sha256": sha(L / "leverage_capability.json"),
    "order_events_in_tail": sum(1 for l in rows if b"ORDER" in l)}

svc = {}
for u in ("usb-crypto-paper", "usb-crypto-context", "usb-crypto-q123-forward", "usb-btc-context-ui", "usb-frontend"):
    v = subprocess.run(["systemctl", "show", "-p", "MainPID,NRestarts,ActiveState", "--value", u],
                       capture_output=True, text=True).stdout.split()
    svc[u] = v
out["services"] = svc
q = json.loads(pathlib.Path("/root/usb_runtime/q123_forward/runtime/status.json").read_text())
out["q123"] = {"state": q["state"], "lag_ms": q["lag_ms"], "tail_sample_ms": q["tail_sample_ms"],
               "store_counts": q["store_counts"], "samples_read": q["counters"]["samples_read"],
               "kline_error": q["kline_error"]}
ql = pathlib.Path("/root/usb_runtime/q123_forward/store/quality.jsonl").read_text().splitlines()
out["q123_last_quality"] = json.loads(ql[-1])
f = json.loads(pathlib.Path("/root/usb_runtime/btc_context_ui/flow/flow_horizons.json").read_text())
out["flow"] = {"written_ms": f["written_ms"], "age_s": round(time.time() - f["written_ms"] / 1000),
               "quality": {k: v["quality"] for k, v in f["horizons"].items()}}
print(json.dumps(out, indent=1, sort_keys=True, default=str, ensure_ascii=False))
