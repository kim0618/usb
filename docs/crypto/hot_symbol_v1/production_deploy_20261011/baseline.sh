set -u
[ "$(hostname)" = "trader-j" ] || { echo '{"error":"WRONG HOST"}'; exit 1; }
/root/usb/.venv/bin/python - <<'PY'
import hashlib, json, os, subprocess, urllib.request
B = "http://127.0.0.1:8100"
R = "/root/usb_runtime/crypto_paper"

def get(path):
    try:
        with urllib.request.urlopen(B + path, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}

def sha(path):
    try:
        data = open(path, "rb").read()
        return {"sha256": hashlib.sha256(data).hexdigest()[:16], "bytes": len(data),
                "lines": data.count(b"\n")}
    except Exception as e:
        return {"_error": str(e)}

out = {}
pos = get("/api/crypto/binance/positions")
out["positions"] = [{k: p.get(k) for k in ("symbol", "signed_qty", "entry_price", "leverage", "update_time_ms", "available")}
                    for p in pos.get("positions", [])] if "positions" in pos else pos
out["others"] = [{k: o.get(k) for k in ("symbol", "signed_qty")} for o in pos.get("others", [])]
arm = get("/api/crypto/binance/arm")
out["arm"] = {k: arm.get(k) for k in ("armed", "state", "expires_at_ms", "env_armed")} if isinstance(arm, dict) else arm
eg = get("/api/crypto/binance/exit-guard?symbol=BTCUSDT")
out["exit_guard"] = {k: eg.get(k) for k in ("enabled", "state", "status", "watching", "symbol")} if isinstance(eg, dict) else eg
lev = {}
for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
    v = get(f"/api/crypto/binance/leverage?symbol={s}")
    lev[s] = {k: v.get(k) for k in ("current", "current_leverage", "leverage", "maximum")} if isinstance(v, dict) else v
out["leverage"] = lev
fills = get("/api/crypto/binance/fills?symbol=BTCUSDT&limit=100")
out["btc_fills_count"] = len(fills.get("fills", fills.get("trades", []))) if isinstance(fills, dict) else None
out["c1_auto"] = {k: v for k, v in get("/api/crypto/paper/c1-auto").items() if k in ("enabled", "state", "mode", "owns_position", "status")}
st = get("/api/crypto/binance/status")
rest = st.get("rest") or {}
out["status"] = {"ready": st.get("ready"), "gates": st.get("gates"),
                 "rest": {k: rest.get(k) for k in rest if any(t in k for t in ("order", "count", "request", "sent", "weight", "calls"))}}
mirror = st.get("mirror") or {}
out["mirror"] = {k: mirror.get(k) for k in mirror if not isinstance(mirror.get(k), (dict, list))}
ps = get("/api/crypto/state?symbol=BTCUSDT")
acct = ps.get("account") or {}
out["paper_btc"] = {"position_side": acct.get("position_side"), "position_qty": acct.get("position_qty"),
                    "ledger_event_count": ps.get("ledger_event_count"), "mode": (ps.get("state") or {}).get("mode")}
files = {}
for root, _, names in os.walk(f"{R}/data/runtime/crypto/live"):
    for n in names:
        files[os.path.relpath(os.path.join(root, n), R)] = sha(os.path.join(root, n))
for d in sorted(os.listdir(f"{R}/run")):
    p = f"{R}/run/{d}/ledger.jsonl"
    if os.path.exists(p):
        files[f"run/{d}/ledger.jsonl"] = sha(p)
out["files"] = files
svc = {}
for u in ("usb-crypto-paper", "usb-frontend", "usb-backend", "usb-crypto-context", "usb-crypto-liqfwd", "usb-e-rvol", "nginx"):
    v = subprocess.run(["systemctl", "show", u, "-p", "MainPID", "-p", "NRestarts", "-p", "ActiveState",
                        "-p", "ActiveEnterTimestamp"], capture_output=True, text=True).stdout
    svc[u] = dict(line.split("=", 1) for line in v.strip().splitlines())
out["services"] = svc
pid = svc["usb-crypto-paper"].get("MainPID")
hot_env = None
try:
    for item in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
        if item.startswith(b"BINANCE_LIVE_HOT_ORDERS="):
            hot_env = item.split(b"=", 1)[1].decode()
except Exception as e:
    hot_env = f"_error {e}"
envfile = "/root/usb_runtime/crypto_paper/secrets/binance_live.env"
out["hot_switch"] = {"process_env": hot_env if hot_env is not None else "UNSET",
                     "env_file_mentions": sum(1 for l in open(envfile) if "BINANCE_LIVE_HOT_ORDERS" in l),
                     "unit_mentions": subprocess.run(["systemctl", "cat", "usb-crypto-paper"], capture_output=True, text=True).stdout.count("BINANCE_LIVE_HOT_ORDERS")}
out["disk"] = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.splitlines()[-1]
out["mem"] = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines()[1:3]
out["frontend_build_id"] = open("/root/usb/frontend/.next-build/BUILD_ID").read().strip()
h = hashlib.sha256()
base = f"{R}/src/backend"
for root, dirs, names in sorted(os.walk(base)):
    dirs.sort()
    for n in sorted(names):
        if n.endswith(".py"):
            p = os.path.join(root, n); h.update(os.path.relpath(p, base).encode()); h.update(open(p, "rb").read())
out["runtime_src_sha"] = h.hexdigest()[:16]
out["repo_head"] = subprocess.run(["git", "-C", "/root/usb", "log", "-1", "--format=%h %s"], capture_output=True, text=True).stdout.strip()
out["repo_branch"] = subprocess.run(["git", "-C", "/root/usb", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
out["repo_dirty"] = len(subprocess.run(["git", "-C", "/root/usb", "status", "--short"], capture_output=True, text=True).stdout.splitlines())
out["listeners"] = sorted(set(l.split()[3] for l in subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout.splitlines()[1:]))
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
PY
