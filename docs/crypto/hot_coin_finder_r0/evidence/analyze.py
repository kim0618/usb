"""HOT Coin Finder R0 - analysis of snapshots. Gates are fixed a priori (see GATES) and are not tuned
on persistence outcomes."""
import glob, json, statistics as st, sys, time
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
D = sys.argv[1] if len(sys.argv) > 1 else "data"
PERP_DELIVERY = 4133404800000  # Binance's "no delivery" sentinel for perpetuals
GATES = dict(spread_bps=10.0, rt_slip_1000_bps=20.0, qv24h=20e6, qv1h=0.5e6)
EXCLUDE = {"BTCUSDT"}

snaps = [json.load(open(f)) for f in sorted(glob.glob(f"{D}/snap_*.json"))]


def ts(s):
    return datetime.fromtimestamp(s["t_start"], KST).strftime("%H:%M")


def rt(r, n):
    b, s = r.get(f"slip_buy_{n}"), r.get(f"slip_sell_{n}")
    return None if b is None or s is None else b + s


def flags(r, now_ms):
    f = []
    if r.get("deliveryDate") and r["deliveryDate"] != PERP_DELIVERY:
        f.append("DELIVERY_SCHEDULED")
    if r.get("onboardDate") and now_ms - r["onboardDate"] < 7 * 86400e3:
        f.append("NEW_LISTING_7D")
    return f


def reasons(r):
    out = []
    if r.get("spread_bps") is None or r.get("r1h") is None:
        return ["NO_DATA"]
    if r["spread_bps"] > GATES["spread_bps"]:
        out.append("WIDE_SPREAD")
    x = rt(r, 1000)
    if x is None or x > GATES["rt_slip_1000_bps"]:
        out.append("HIGH_SLIPPAGE")
    if r.get("qv24h", 0) < GATES["qv24h"] or r.get("qv1h", 0) < GATES["qv1h"]:
        out.append("LOW_VOLUME")
    if "DELIVERY_SCHEDULED" in r["_flags"]:
        out.append("DELIVERY_SCHEDULED")
    return out


def pct_rank(vals):
    """value -> percentile rank in [0,1], higher value higher rank"""
    s = sorted(vals)
    n = len(s)
    import bisect
    return lambda v: bisect.bisect_left(s, v) / max(n - 1, 1)


def prep(s):
    now_ms = int(s["t_start"] * 1000)
    rows = [r for r in s["rows"] if r["symbol"] not in EXCLUDE]
    for r in rows:
        r["_flags"] = flags(r, now_ms)
        r["_rej"] = reasons(r)
        r["_ok"] = not r["_rej"]
    valid = [r for r in rows if r.get("r1h") is not None and r.get("r24h") is not None]
    p1, p24 = pct_rank([r["r1h"] for r in valid]), pct_rank([r["r24h"] for r in valid])
    for r in valid:
        r["_combo"] = (p1(r["r1h"]) + p24(r["r24h"])) / 2
    by1 = sorted(valid, key=lambda r: -r["r1h"])
    by24 = sorted(valid, key=lambda r: -r["r24h"])
    byqv = sorted(valid, key=lambda r: -r["qv24h"])
    for i, r in enumerate(by1):
        r["_rk1"] = i + 1
    for i, r in enumerate(by24):
        r["_rk24"] = i + 1
    for i, r in enumerate(byqv):
        r["_rkqv"] = i + 1
    ok = [r for r in valid if r["_ok"]]
    s["_valid"], s["_by1"], s["_by24"] = valid, by1, by24
    s["_f1"] = sorted(ok, key=lambda r: -r["r1h"])
    s["_f24"] = sorted(ok, key=lambda r: -r["r24h"])
    s["_fc"] = sorted(ok, key=lambda r: -r["_combo"])
    s["_map"] = {r["symbol"]: r for r in rows}
    return s


for s in snaps:
    prep(s)


def q(vals, ps=(0.1, 0.25, 0.5, 0.75, 0.9, 0.99)):
    v = sorted(x for x in vals if x is not None)
    return " ".join(f"p{int(p*100)}={v[min(int(p*len(v)), len(v)-1)]:.2f}" for p in ps) + f" (n={len(v)})"


def fmt(r):
    x = rt(r, 1000)
    return (f"{r['symbol']:<14} 1H {r['r1h']:5.2f}% ({r['chg1h']:+.2f}) 24H {r['r24h']:6.2f}% ({r['chg24h']:+.1f}) "
            f"qv24 {r['qv24h']/1e6:7.1f}M qv1h {r['qv1h']/1e6:6.2f}M spr {r['spread_bps']:5.2f}bp "
            f"rt1k {('%.1f' % x) if x is not None else 'EXH':>5}bp")


P = print
P(f"snapshots={len(snaps)}  {ts(snaps[0])}..{ts(snaps[-1])} KST  gates={GATES}")

# ---------- B. universe
s0 = snaps[0]
P("\n=== B. UNIVERSE (snap 0)")
P("exchange symbols total", s0["exchange_symbols_total"], "status counts", s0["status_counts"])
P("universe sizes per snap", [s["universe_size"] for s in snaps])
fl = {}
for r in s0["rows"]:
    for f in r["_flags"] if "_flags" in r else flags(r, int(s0["t_start"] * 1000)):
        fl.setdefault(f, []).append(r["symbol"])
P("flags", {k: (len(v), v[:12]) for k, v in fl.items()})
P("non-ascii symbols", [r["symbol"] for r in s0["rows"] if not r["symbol"].isascii()])
P("rows missing data", [r["symbol"] for r in s0["rows"] if r.get("r1h") is None or r.get("spread_bps") is None])
P("underlyingSubType counts", {k: sum(1 for r in s0["rows"] if str(r.get("underlyingSubType")) == k) for k in
      sorted({str(r.get("underlyingSubType")) for r in s0["rows"]})})

# ---------- D. volatility distribution
P("\n=== D. VOLATILITY DISTRIBUTION (all snaps pooled, BTC excluded)")
pool = [r for s in snaps for r in s["_valid"]]
P("1H range %  ", q([r["r1h"] for r in pool]))
P("24H range % ", q([r["r24h"] for r in pool]))
P("|24H chg| % ", q([abs(r["chg24h"]) for r in pool]))
P("per-snapshot counts: time | r1h>=2/3/5/10 | r24h>=10/20/30/50 | top1h | top24h")
for s in snaps:
    v = s["_valid"]
    c1 = [sum(r["r1h"] >= t for r in v) for t in (2, 3, 5, 10)]
    c24 = [sum(r["r24h"] >= t for r in v) for t in (10, 20, 30, 50)]
    P(f"  {ts(s)} | {c1} | {c24} | {s['_by1'][0]['symbol']} {s['_by1'][0]['r1h']:.1f} | {s['_by24'][0]['symbol']} {s['_by24'][0]['r24h']:.1f}")
btc = [s["_map"].get("BTCUSDT") or next(r for r in s["rows"] if r["symbol"] == "BTCUSDT") for s in snaps]
P("BTC reference 1H range", q([r.get("r1h") for r in btc], (0.5,)), "24H", q([r.get("r24h") for r in btc], (0.5,)))

# ---------- E. liquidity distribution
P("\n=== E. LIQUIDITY DISTRIBUTION (snap 0, universe excl BTC)")
v = s0["_valid"]
P("spread bps   ", q([r["spread_bps"] for r in v]))
P("qv24h M      ", q([r["qv24h"] / 1e6 for r in v]))
P("qv1h M       ", q([r["qv1h"] / 1e6 for r in v]))
P("top-of-book notional (min side) ", q([min(r["top_bid_notional"], r["top_ask_notional"]) for r in v]))
P("depth ±0.1% min side USDT       ", q([min(r["depth_bid_0.1"], r["depth_ask_0.1"]) for r in v]))
P("depth ±0.25% min side USDT      ", q([min(r["depth_bid_0.25"], r["depth_ask_0.25"]) for r in v]))
P("depth ±0.25% truncated (50lvl didn't reach band)", sum(r["depth_trunc_0.25"] for r in v))
for n in (100, 500, 1000, 5000):
    xs = [rt(r, n) for r in v]
    P(f"round-trip slip {n:>5} USDT bps ", q(xs), f"exhausted={sum(x is None for x in xs)}")
P("gate pass counts per snap (spread / slip / volume / all):")
for s in snaps:
    v = s["_valid"]
    P(f"  {ts(s)} valid={len(v)} spread_ok={sum(r['spread_bps']<=GATES['spread_bps'] for r in v)} "
      f"slip_ok={sum((rt(r,1000) or 1e9)<=GATES['rt_slip_1000_bps'] for r in v)} "
      f"vol_ok={sum(r['qv24h']>=GATES['qv24h'] and r['qv1h']>=GATES['qv1h'] for r in v)} all_ok={sum(r['_ok'] for r in v)}")

# ---------- F. volatility vs executability
P("\n=== F. VOLATILITY vs EXECUTABILITY")
for N in (5, 10, 20, 50):
    a = [sum(r["_ok"] for r in s["_by1"][:N]) / N for s in snaps]
    b = [sum(r["_ok"] for r in s["_by24"][:N]) / N for s in snaps]
    P(f"  top{N:>2} by 1H range: pass-rate mean {st.mean(a):.2f} min {min(a):.2f} | by 24H: mean {st.mean(b):.2f} min {min(b):.2f}")
rk = [s["_fc"][0]["_rk1"] for s in snaps if s["_fc"]]
P("  raw 1H rank of filtered-combined HOT#1:", rk)
rk = [s["_f1"][0]["_rk1"] for s in snaps if s["_f1"]]
P("  raw 1H rank of filtered-by-1H HOT#1:", rk)
rej = {}
for s in snaps:
    for r in s["_by1"][:20] + s["_by24"][:20]:
        for x in r["_rej"]:
            rej[x] = rej.get(x, 0) + 1
P("  rejection reason counts among (top20 1H + top20 24H) pooled:", rej)
# category labels
cat = {}
for s in snaps:
    top = {r["symbol"]: r for r in s["_by1"][:20] + s["_by24"][:20]}
    for r in top.values():
        k = "HIGH_VOL_GOOD_LIQUIDITY" if r["_ok"] else ("HIGH_VOL_BUT_THIN" if ("HIGH_SLIPPAGE" in r["_rej"] or "LOW_VOLUME" in r["_rej"]) else "HIGH_VOL_" + "+".join(r["_rej"]))
        cat[k] = cat.get(k, 0) + 1
    for r in sorted(s["_valid"], key=lambda r: -r["qv24h"])[:20]:
        if r["_rk1"] > 50 and r["_rk24"] > 50:
            cat["HIGH_VOLUME_LOW_VOL"] = cat.get("HIGH_VOLUME_LOW_VOL", 0) + 1
P("  category counts (symbol-snapshots):", cat)
hv = sorted({r["symbol"] for s in snaps for r in s["_valid"] if r["_rkqv"] <= 20 and r["_rk1"] > 50 and r["_rk24"] > 50})
P("  HIGH_VOLUME_LOW_VOL symbols seen:", hv)

# ---------- C/G. per snapshot report
P("\n=== C/G. SNAPSHOT REPORTS")
for s in snaps:
    P(f"\nTIME {ts(s)} KST  universe={s['universe_size']} valid={len(s['_valid'])} pass={len(s['_fc'])} scan={s['t_end']-s['t_start']:.0f}s maxW={s['max_used_weight_1m']}")
    P(" Raw volatility (by 1H range):")
    for i, r in enumerate(s["_by1"][:5]):
        P(f"  {i+1}. {fmt(r)}  {'OK' if r['_ok'] else 'REJ ' + ','.join(r['_rej'])} {' '.join(r['_flags'])}")
    P(" Raw volatility (by 24H range):")
    for i, r in enumerate(s["_by24"][:3]):
        P(f"  {i+1}. {fmt(r)}  {'OK' if r['_ok'] else 'REJ ' + ','.join(r['_rej'])}")
    P(" After liquidity, by 1H range:", ", ".join(r["symbol"] for r in s["_f1"][:5]))
    P(" After liquidity, by 24H range:", ", ".join(r["symbol"] for r in s["_f24"][:5]))
    P(" Combined (mean pct-rank 1H & 24H, liquidity-passed):")
    for i, r in enumerate(s["_fc"][:5]):
        P(f"  {i+1}. {fmt(r)} combo={r['_combo']:.3f} rk1={r['_rk1']} rk24={r['_rk24']} {' '.join(r['_flags'])}")

# ---------- H. persistence
P("\n=== H. PERSISTENCE of combined HOT#1 (and filtered-by-1H HOT#1)")
offs = {1: "5m", 3: "15m", 6: "30m", 12: "60m"}
for key in ("_fc", "_f1"):
    P(f"\n -- HOT#1 definition {key}")
    agg = {o: [] for o in offs}
    for i, s in enumerate(snaps):
        if not s[key]:
            continue
        h = s[key][0]
        line = f"  {ts(s)} {h['symbol']:<12} r1h={h['r1h']:.2f} r15m={h['r15m']:.2f} rk15m?"
        for o, lab in offs.items():
            if i + o >= len(snaps):
                continue
            t = snaps[i + o]
            r = t["_map"].get(h["symbol"])
            if not r or r.get("r1h") is None:
                agg[o].append(None); line += f" | {lab}: GONE"; continue
            fl_ = [x["symbol"] for x in t[key]]
            frank = fl_.index(h["symbol"]) + 1 if h["symbol"] in fl_ else None
            r15 = sorted(t["_valid"], key=lambda x: -x["r15m"])
            rk15 = [x["symbol"] for x in r15].index(h["symbol"]) + 1
            med15 = st.median(x["r15m"] for x in t["_valid"])
            d = dict(top1=frank == 1, top5=frank is not None and frank <= 5, top10=frank is not None and frank <= 10,
                     raw1h_top20=r["_rk1"] <= 20, liq_ok=r["_ok"],
                     spr_ratio=r["spread_bps"] / max(h["spread_bps"], 1e-9),
                     slip_ratio=(rt(r, 1000) or 999) / max(rt(h, 1000), 1e-9),
                     r15m_ratio=r["r15m"] / max(h["r15m"], 1e-9), rk15=rk15, r15_vs_med=r["r15m"] / med15)
            agg[o].append(d)
            line += f" | {lab}: f#{frank} raw#{r['_rk1']} {'ok' if r['_ok'] else 'REJ'} 15m#{rk15} r15x{d['r15m_ratio']:.2f}"
        P(line)
    P("  summary (n, top1, top5, top10, raw1H top20, liq ok, median spread ratio, median slip ratio, median r15m ratio, r15m rank<=20, r15m >= 2x universe median):")
    for o, lab in offs.items():
        ds = [d for d in agg[o] if d]
        if not ds:
            continue
        n = len(agg[o])
        P(f"   {lab:>3}: n={n} gone={n-len(ds)} top1={sum(d['top1'] for d in ds)} top5={sum(d['top5'] for d in ds)} "
          f"top10={sum(d['top10'] for d in ds)} raw20={sum(d['raw1h_top20'] for d in ds)} liq={sum(d['liq_ok'] for d in ds)} "
          f"spr×{st.median(d['spr_ratio'] for d in ds):.2f} slip×{st.median(d['slip_ratio'] for d in ds):.2f} "
          f"r15m×{st.median(d['r15m_ratio'] for d in ds):.2f} rk15≤20={sum(d['rk15']<=20 for d in ds)} "
          f"r15≥2×med={sum(d['r15_vs_med']>=2 for d in ds)}")

# ---------- Q5. one-shot stability
P("\n=== Q5. ONE-SHOT STABILITY")
for key in ("_fc", "_f1"):
    h1 = [s[key][0]["symbol"] for s in snaps if s[key]]
    ch = sum(a != b for a, b in zip(h1, h1[1:]))
    P(f" {key}: HOT#1 sequence: {h1}")
    P(f"   distinct={len(set(h1))} changes between consecutive={ch}/{len(h1)-1}")
    gaps = [s[key][0]["_combo" if key == "_fc" else "r1h"] - s[key][1]["_combo" if key == "_fc" else "r1h"] for s in snaps if len(s[key]) > 1]
    P(f"   #1-#2 gap: {q(gaps, (0.1, 0.5, 0.9))}")
    t5 = [len({r['symbol'] for r in s[key][:5]} & {r['symbol'] for r in t[key][:5]}) for s, t in zip(snaps, snaps[1:])]
    P(f"   top5 overlap with next snapshot (of 5): {t5}")

# ---------- I. API cost alternative: batch-only shortlist capture
P("\n=== I. SHORTLIST CAPTURE (batch-ticker prefilter then top-K detail)")
for K in (20, 30, 50, 80):
    hit1 = hit5 = n = 0
    for s in snaps:
        if not s["_fc"]:
            continue
        # prefilter using only batch data: qv24h gate + bookTicker spread gate, rank by 24H range
        pre = [r for r in s["_valid"] if r["qv24h"] >= GATES["qv24h"] and "DELIVERY_SCHEDULED" not in r["_flags"]
               and (r["bt_ask"] - r["bt_bid"]) / ((r["bt_ask"] + r["bt_bid"]) / 2) * 1e4 <= GATES["spread_bps"]]
        sl = {r["symbol"] for r in sorted(pre, key=lambda r: -r["r24h"])[:K]}
        sl2 = {r["symbol"] for r in sorted(pre, key=lambda r: -abs(r["chg24h"]))[:K]}
        n += 1
        hit1 += s["_fc"][0]["symbol"] in sl
        hit5 += sum(r["symbol"] in sl for r in s["_fc"][:5])
        s.setdefault("_pre", len(pre))
    P(f" K={K}: combined HOT#1 inside shortlist {hit1}/{n}, top5 captured {hit5}/{5*n}; prefilter size {[s.get('_pre') for s in snaps][:3]}...")
for K in (20, 30, 50, 80):
    hit1 = n = 0
    for s in snaps:
        if not s["_f1"]:
            continue
        pre = [r for r in s["_valid"] if r["qv24h"] >= GATES["qv24h"]]
        sl = {r["symbol"] for r in sorted(pre, key=lambda r: -r["r24h"])[:K]}
        n += 1
        hit1 += s["_f1"][0]["symbol"] in sl
    P(f" K={K}: filtered-by-1H HOT#1 inside 24H-range shortlist {hit1}/{n}")

P("\n=== API calls per snapshot", [(s["calls"], s["max_used_weight_1m"], round(s["t_end"] - s["t_start"])) for s in snaps][:2])
P("scan durations s:", [round(s["t_end"] - s["t_start"]) for s in snaps], "batch phase s:", [round(s["t_batch_done"] - s["t_start"], 1) for s in snaps][:5])
P("max weight per snap:", [s["max_used_weight_1m"] for s in snaps])

# ---------- OI/funding diag (not used for ranking)
P("\n=== DIAG funding/OI of combined top5 (not used in ranking)")
for s in snaps[:: max(1, len(snaps) // 4)]:
    P(" ", ts(s), [(r["symbol"], round(r.get("funding", 0) * 100, 4), round(r.get("oi_notional", 0) / 1e6, 1) if r.get("oi_notional") else None) for r in s["_fc"][:5]])

# ---------- SENSITIVITY (report only, not used to pick the contract)
P("\n=== SENSITIVITY: volume gate variants (spread+slip gates unchanged)")
def alt_ok(r, mode):
    rj = [x for x in r["_rej"] if x != "LOW_VOLUME"]
    if rj:
        return False
    if mode == "qv1h>=2M only":
        return r["qv1h"] >= 2e6
    if mode == "qv24h>=50M":
        return r["qv24h"] >= 50e6 and r["qv1h"] >= 0.5e6
    return r["_ok"]
for mode in ("base", "qv1h>=2M only", "qv24h>=50M"):
    seq = []
    for s in snaps:
        ok = sorted([r for r in s["_valid"] if alt_ok(r, mode)], key=lambda r: -r["r1h"])
        seq.append(ok[0]["symbol"] if ok else None)
    P(f" {mode:<15} pass(snap0)={sum(alt_ok(r, mode) for r in snaps[0]['_valid'])} HOT#1(by 1H) seq={seq}")

P("\n=== VOLUME-GATE SUFFICIENCY: among symbols passing the volume gate, share failing spread/slip")
for s in snaps:
    vp = [r for r in s["_valid"] if r["qv24h"] >= GATES["qv24h"] and r["qv1h"] >= GATES["qv1h"]]
    bad = [r for r in vp if not r["_ok"]]
    vt = [r for r in s["_by1"][:20] if r in vp]
    P(f"  {ts(s)} vol-pass={len(vp)} then-fail={len(bad)} {[(r['symbol'], r['_rej']) for r in bad][:4]} | among 1H-top20 vol-pass={len(vt)} then-fail={sum(not r['_ok'] for r in vt)}")
P("  among liquidity-passed HOT top5 (combined+f1), round-trip slip 1000/5000 USDT bps:")
xs1 = [rt(r, 1000) for s in snaps for r in s["_f1"][:5]]
xs5 = [rt(r, 5000) for s in snaps for r in s["_f1"][:5]]
P("   1000:", q(xs1, (0.5, 0.9, 1.0)), " 5000:", q(xs5, (0.5, 0.9, 1.0)), "exhausted5000=", sum(x is None for x in xs5))
