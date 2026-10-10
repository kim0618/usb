"""HOT Coin Finder R0 - read-only market snapshot of Binance USD-M perpetuals.

Public REST only, no API key, no order endpoints. Writes one JSON per snapshot.
Usage: python3 snap.py OUT_DIR N_SNAPSHOTS INTERVAL_SEC
"""
import json, os, sys, time, threading, urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor

BASE = "https://fapi.binance.com"
BUCKETS = [100, 500, 1000, 5000]
BANDS = [0.001, 0.0025]
REQ_PER_SEC = 10.0
WEIGHT_SOFT_CAP = 1500

_lock = threading.Lock()
_next_slot = [0.0]
_weight = {"last": 0, "max": 0}
_calls = {}


def _throttle():
    with _lock:
        now = time.monotonic()
        slot = max(now, _next_slot[0])
        _next_slot[0] = slot + 1.0 / REQ_PER_SEC
    d = slot - time.monotonic()
    if d > 0:
        time.sleep(d)


def get(path, tag):
    for attempt in range(4):
        _throttle()
        if _weight["last"] > WEIGHT_SOFT_CAP:
            time.sleep(5)
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "hot-r0-research"})
            with urllib.request.urlopen(req, timeout=15) as r:
                w = int(r.headers.get("x-mbx-used-weight-1m", "0"))
                with _lock:
                    _weight["last"] = w
                    _weight["max"] = max(_weight["max"], w)
                    _calls[tag] = _calls.get(tag, 0) + 1
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                print("RATE LIMIT HIT", e.code, path, flush=True)
                time.sleep(30)
            elif attempt == 3:
                raise
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1)


def walk(levels, notional, mid):
    """Market order walk. levels = [(price, qty)] best first. Returns avg-vs-mid bps or None if exhausted."""
    got_q = 0.0
    spent = 0.0
    for p, q in levels:
        lv = p * q
        if spent + lv >= notional:
            need = (notional - spent) / p
            got_q += need
            spent = notional
            return abs(spent / got_q - mid) / mid * 1e4
        spent += lv
        got_q += q
    return None


def book_metrics(depth):
    bids = [(float(p), float(q)) for p, q in depth["bids"]]
    asks = [(float(p), float(q)) for p, q in depth["asks"]]
    if not bids or not asks:
        return None
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    m = {
        "bid": bb, "ask": ba, "mid": mid,
        "spread_bps": (ba - bb) / mid * 1e4,
        "top_bid_notional": bids[0][0] * bids[0][1],
        "top_ask_notional": asks[0][0] * asks[0][1],
        "book_bid_notional": sum(p * q for p, q in bids),
        "book_ask_notional": sum(p * q for p, q in asks),
        "book_span_bid_pct": (mid - bids[-1][0]) / mid * 100,
        "book_span_ask_pct": (asks[-1][0] - mid) / mid * 100,
    }
    for b in BANDS:
        k = f"{b*100:g}"
        m[f"depth_bid_{k}"] = sum(p * q for p, q in bids if p >= mid * (1 - b))
        m[f"depth_ask_{k}"] = sum(p * q for p, q in asks if p <= mid * (1 + b))
        # truncated = the 50-level book did not reach the band edge -> value is a lower bound
        m[f"depth_trunc_{k}"] = (bids[-1][0] >= mid * (1 - b)) or (asks[-1][0] <= mid * (1 + b))
    for n in BUCKETS:
        m[f"slip_buy_{n}"] = walk(asks, n, mid)
        m[f"slip_sell_{n}"] = walk(bids, n, mid)
    return m


def kline_metrics(kl, now_ms):
    closed = [k for k in kl if int(k[6]) < now_ms]
    closed = closed[-60:]
    if len(closed) < 60:
        return {"bars": len(closed)}
    def rng(bars):
        h = max(float(b[2]) for b in bars)
        l = min(float(b[3]) for b in bars)
        o = float(bars[0][1])
        return (h - l) / o * 100
    o = float(closed[0][1]); c = float(closed[-1][4])
    return {
        "bars": 60,
        "r1h": rng(closed),
        "chg1h": (c - o) / o * 100,
        "qv1h": sum(float(b[7]) for b in closed),
        "r15m": rng(closed[-15:]),
        "r5m": rng(closed[-5:]),
        "qv15m": sum(float(b[7]) for b in closed[-15:]),
        "bar0_open_ms": int(closed[0][0]),
    }


def snapshot(idx):
    t_start = time.time()
    _calls.clear()
    _weight["max"] = 0
    info = get("/fapi/v1/exchangeInfo", "exchangeInfo")
    all_syms = info["symbols"]
    status_counts = {}
    uni = []
    for s in all_syms:
        key = f'{s["contractType"]}|{s["quoteAsset"]}|{s["status"]}'
        status_counts[key] = status_counts.get(key, 0) + 1
        if s["contractType"] == "PERPETUAL" and s["status"] == "TRADING" and s["quoteAsset"] == "USDT":
            f = {x["filterType"]: x for x in s["filters"]}
            uni.append({
                "symbol": s["symbol"],
                "onboardDate": s.get("onboardDate"),
                "deliveryDate": s.get("deliveryDate"),
                "underlyingType": s.get("underlyingType"),
                "underlyingSubType": s.get("underlyingSubType"),
                "tickSize": f.get("PRICE_FILTER", {}).get("tickSize"),
                "stepSize": f.get("LOT_SIZE", {}).get("stepSize"),
                "mktMaxQty": f.get("MARKET_LOT_SIZE", {}).get("maxQty"),
                "minNotional": f.get("MIN_NOTIONAL", {}).get("notional"),
                "pricePrecision": s.get("pricePrecision"),
                "quantityPrecision": s.get("quantityPrecision"),
            })
    tick = {t["symbol"]: t for t in get("/fapi/v1/ticker/24hr", "ticker24hr_all")}
    book = {t["symbol"]: t for t in get("/fapi/v1/ticker/bookTicker", "bookTicker_all")}
    prem = {t["symbol"]: t for t in get("/fapi/v1/premiumIndex", "premiumIndex_all")}
    t_batch = time.time()

    def per_symbol(u):
        sym = u["symbol"]
        qsym = urllib.parse.quote(sym)
        now_ms = int(time.time() * 1000)
        row = dict(u)
        t = tick.get(sym)
        if t:
            o, h, l = float(t["openPrice"]), float(t["highPrice"]), float(t["lowPrice"])
            row.update({
                "last": float(t["lastPrice"]),
                "chg24h": float(t["priceChangePercent"]),
                "r24h": (h - l) / o * 100 if o > 0 else None,
                "qv24h": float(t["quoteVolume"]),
                "count24h": int(t["count"]),
            })
        b = book.get(sym)
        if b:
            row["bt_bid"] = float(b["bidPrice"]); row["bt_ask"] = float(b["askPrice"])
        p = prem.get(sym)
        if p:
            row["funding"] = float(p["lastFundingRate"])
            row["mark"] = float(p["markPrice"])
        try:
            row.update(kline_metrics(get(f"/fapi/v1/klines?symbol={qsym}&interval=1m&limit=62", "klines_1m62"), now_ms))
        except Exception as e:
            row["kl_err"] = str(e)[:80]
        try:
            bm = book_metrics(get(f"/fapi/v1/depth?symbol={qsym}&limit=50", "depth50"))
            if bm:
                row.update(bm)
        except Exception as e:
            row["depth_err"] = str(e)[:80]
        return row

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(per_symbol, uni))
    t_sym = time.time()
    # OI only diagnostic, only for top 25 by 1h range
    top = sorted([r for r in rows if r.get("r1h") is not None], key=lambda r: -r["r1h"])[:25]
    for r in top:
        try:
            oi = get(f"/fapi/v1/openInterest?symbol={urllib.parse.quote(r['symbol'])}", "openInterest")
            r["oi_qty"] = float(oi["openInterest"])
            r["oi_notional"] = r["oi_qty"] * r.get("mark", r.get("last", 0))
        except Exception as e:
            r["oi_err"] = str(e)[:80]
    out = {
        "idx": idx,
        "t_start": t_start,
        "t_batch_done": t_batch,
        "t_sym_done": t_sym,
        "t_end": time.time(),
        "serverTime_info": info.get("serverTime"),
        "rateLimits": info.get("rateLimits"),
        "exchange_symbols_total": len(all_syms),
        "status_counts": status_counts,
        "universe_size": len(uni),
        "calls": dict(_calls),
        "max_used_weight_1m": _weight["max"],
        "rows": rows,
    }
    return out


def main():
    out_dir, n, interval = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    for i in range(n):
        target = t0 + i * interval
        d = target - time.time()
        if d > 0:
            time.sleep(d)
        try:
            s = snapshot(i)
            fn = os.path.join(out_dir, f"snap_{i:02d}.json")
            with open(fn, "w") as f:
                json.dump(s, f)
            print(time.strftime("%H:%M:%S"), f"snap {i} uni={s['universe_size']} dur={s['t_end']-s['t_start']:.0f}s "
                  f"maxW={s['max_used_weight_1m']} calls={s['calls']}", flush=True)
        except Exception as e:
            print(time.strftime("%H:%M:%S"), f"snap {i} FAILED {e}", flush=True)


if __name__ == "__main__":
    main()
