"""HOT R0 - one-shot backfill of 5m klines (8 days) for the current USDT perpetual universe.
Public REST, no key. 5m bar high/low are exact maxima/minima of the 1m bars inside them, so
hour-aligned 1H ranges rebuilt from 12 bars equal the 1m-based ranges."""
import json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://fapi.binance.com"
OUT = sys.argv[1]
DAYS = 8
os.makedirs(OUT, exist_ok=True)
import threading
lock = threading.Lock(); nxt = [0.0]; wmax = [0]; RPS = 8.0


def get(path):
    for a in range(5):
        with lock:
            now = time.monotonic(); slot = max(now, nxt[0]); nxt[0] = slot + 1 / RPS
        time.sleep(max(0, slot - time.monotonic()))
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "hot-r0"}), timeout=20) as r:
                w = int(r.headers.get("x-mbx-used-weight-1m", "0"))
                wmax[0] = max(wmax[0], w)
                if w > 1500:
                    time.sleep(10)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                print("RATE LIMIT", e.code, flush=True); time.sleep(60)
            elif a == 4:
                raise
        except Exception:
            if a == 4:
                raise
            time.sleep(2)


info = get("/fapi/v1/exchangeInfo")
uni = [s["symbol"] for s in info["symbols"]
       if s["contractType"] == "PERPETUAL" and s["status"] == "TRADING" and s["quoteAsset"] == "USDT"]
end_ms = (int(time.time() * 1000) // 300000) * 300000  # last closed 5m boundary
start_ms = end_ms - DAYS * 86400000


def fetch(sym):
    bars, t = [], start_ms
    while t < end_ms:
        kl = get(f"/fapi/v1/klines?symbol={urllib.parse.quote(sym)}&interval=5m&limit=1000&startTime={t}&endTime={end_ms - 1}")
        if not kl:
            break
        bars += [[int(k[0]), float(k[2]), float(k[3]), float(k[1]), float(k[4]), float(k[7])] for k in kl]
        t = int(kl[-1][0]) + 300000
        if len(kl) < 1000:
            break
    return sym, bars


t0 = time.time()
with ThreadPoolExecutor(6) as ex:
    res = dict(ex.map(fetch, uni))
json.dump({"start_ms": start_ms, "end_ms": end_ms, "fields": ["open_ms", "high", "low", "open", "close", "quote_vol"],
           "universe": uni, "bars": res}, open(os.path.join(OUT, "klines5m.json"), "w"))
print(f"done uni={len(uni)} dur={time.time()-t0:.0f}s maxW={wmax[0]} short={sum(len(v) < DAYS*288 for v in res.values())}", flush=True)
