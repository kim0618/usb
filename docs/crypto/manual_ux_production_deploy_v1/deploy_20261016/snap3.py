"""Read-only pre/post snapshot, crypto manual UX deploy (no signed calls, no writes)."""
import hashlib, json, pathlib, subprocess, time, urllib.request, sys

def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def get(url, timeout=30):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, None
    except Exception as e: return type(e).__name__, None

P = pathlib.Path("/root/usb_runtime/crypto_paper")
out = {"taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
runs = {}
for d in sorted(p for p in (P / "run").iterdir() if p.is_dir()):
    led = d / "ledger.jsonl"
    if not led.exists(): continue
    raw = led.read_bytes(); lines = raw.splitlines()
    last = json.loads(lines[-1]) if lines else {}
    runs[d.name] = {"ledger_sha256": hashlib.sha256(raw).hexdigest(), "events": len(lines),
                    "last_seq": last.get("seq"), "last_event": last.get("event_type"),
                    "order_like_lines": sum(1 for l in lines if b'"ORDER_' in l or b'"FILL"' in l)}
out["paper_runs"] = runs
rc = json.loads((P / "run/run_config.json").read_text())
out["run_config_depth_keys"] = {k: v for k, v in rc.items() if "depth" in k.lower()}
acc_keys = ("position_side", "position_qty", "avg_entry", "leverage", "equity", "wallet_balance",
            "available_balance", "used_margin", "realized_pnl", "cumulative_fees")
out["paper_state"] = {}
for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "STRKUSDT"):
    code, s = get(f"http://127.0.0.1:8100/api/crypto/state?symbol={sym}")
    if not s: out["paper_state"][sym] = code; continue
    a = s.get("account") or {}
    out["paper_state"][sym] = {"http": code, "symbol": s.get("symbol"), "leverage": s.get("leverage"),
        "ledger_event_count": s.get("ledger_event_count"), "has_c1_key": any("c1" in k for k in s),
        "account": {k: a.get(k) for k in acc_keys}}
for r in ("/api/crypto/c1/state", "/api/crypto/paper/c1-auto"):
    out["route" + r] = get("http://127.0.0.1:8100" + r, 15)[0]
L = P / "data/runtime/crypto/live"
mirror = next(L.glob("*/binance_live_events.jsonl"))
with mirror.open("rb") as fh:
    fh.seek(max(0, mirror.stat().st_size - 400_000)); rows = fh.read().splitlines()[1:]
evs = [json.loads(l) for l in rows]
rec = [e for e in evs if e.get("event_type") == "LIVE_RECONCILE"]
arm = [e for e in evs if e.get("event_type") in ("LIVE_ARM", "LIVE_DISARM")]
last = rec[-1] if rec else {}
pos = last.get("position") or {}
out["live"] = {"mirror": str(mirror), "mirror_bytes": mirror.stat().st_size, "mirror_sha256": sha(mirror),
    "mirror_last_seq": evs[-1].get("seq"), "mirror_last_event": evs[-1].get("event_type"),
    "last_reconcile_seq": last.get("seq"), "wallet_balance": last.get("wallet_balance"),
    "positions": last.get("positions"),
    "position": {k: pos.get(k) for k in ("symbol", "side", "qty", "entry_price", "leverage", "is_flat", "notional")},
    "last_arm_event": {k: arm[-1].get(k) for k in ("seq", "event_type", "reason", "ts", "recorded_at")} if arm else None,
    "exit_guard_sha256": sha(L / "exit_guard.json"), "exit_guard": json.loads((L / "exit_guard.json").read_text()),
    "leverage_capability_sha256": sha(L / "leverage_capability.json") if (L / "leverage_capability.json").exists() else None,
    "order_events_in_tail": sum(1 for l in rows if b"ORDER" in l)}
svc = {}
for u in ("usb-crypto-paper", "usb-frontend", "usb-backend", "usb-crypto-context", "usb-crypto-q123-forward", "usb-btc-context-ui", "usb-crypto-liqfwd", "usb-crypto-liqfwd-daily.timer"):
    v = subprocess.run(["systemctl", "show", "-p", "MainPID,NRestarts,ActiveState,UnitFileState", "--value", u], capture_output=True, text=True).stdout.split()
    m = {}
    pid = v[0] if v else "0"
    if pid not in ("0", ""):
        try:
            st = pathlib.Path(f"/proc/{pid}/status").read_text()
            m["rss_k"] = int([l for l in st.splitlines() if l.startswith("VmRSS")][0].split()[1])
            sr = pathlib.Path(f"/proc/{pid}/smaps_rollup").read_text()
            for l in sr.splitlines():
                if l.startswith("Pss:"): m["pss_k"] = int(l.split()[1])
                if l.startswith("Swap:"): m["swap_k"] = int(l.split()[1])
        except Exception as e: m["err"] = str(e)
    svc[u] = {"show": v, **m}
out["services"] = svc
mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemAvailable", "SwapTotal", "SwapFree")}
out["host"] = {**mi, "swap_used_k": mi["SwapTotal"] - mi["SwapFree"], "load": open("/proc/loadavg").read().strip(),
    "psi_mem": open("/proc/pressure/memory").read().split("\n")[:2], "psi_io": open("/proc/pressure/io").read().split("\n")[:2]}
q = json.loads(pathlib.Path("/root/usb_runtime/q123_forward/runtime/status.json").read_text())
out["q123"] = {"state": q["state"], "lag_ms": q["lag_ms"], "store_counts": q["store_counts"],
               "samples_read": q["counters"]["samples_read"], "kline_error": q["kline_error"]}
f = json.loads(pathlib.Path("/root/usb_runtime/btc_context_ui/flow/flow_horizons.json").read_text())
out["flow"] = {"age_s": round(time.time() - f["written_ms"] / 1000), "quality": {k: v["quality"] for k, v in f["horizons"].items()}}
try: out["recorder_tail"] = open("/root/usb_runtime/manual_trade_research/logs/recorder.log").read().splitlines()[-1][:160]
except Exception as e: out["recorder_tail"] = str(e)
print(json.dumps(out, indent=1, sort_keys=True, default=str, ensure_ascii=False))
