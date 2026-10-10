"""HOT R0 - persistence / one-shot stability from 7 days of 5m bars.
Volume gate only (spread/slippage cannot be rebuilt from bars). Gates identical to analyze.py."""
import json, sys
from datetime import datetime, timezone, timedelta
import numpy as np

KST = timezone(timedelta(hours=9))
H = json.load(open(sys.argv[1]))
QV24, QV1 = 20e6, 0.5e6
start, end = H["start_ms"], H["end_ms"]
T = (end - start) // 300000
syms = [s for s in H["universe"] if s != "BTCUSDT"]
S = len(syms)
hi = np.full((S, T), np.nan); lo = hi.copy(); op = hi.copy(); qv = hi.copy()
for i, s in enumerate(syms):
    for b in H["bars"][s]:
        j = (b[0] - start) // 300000
        if 0 <= j < T:
            hi[i, j], lo[i, j], op[i, j], qv[i, j] = b[1], b[2], b[3], b[5]
btc = np.array([[b[1], b[2], b[3], b[5]] for b in H["bars"]["BTCUSDT"]])


def rng(a, b):
    """range % over bars [a, b) for all symbols; nan if any bar missing"""
    h = hi[:, a:b]; l = lo[:, a:b]
    r = (np.max(h, axis=1) - np.min(l, axis=1)) / op[:, a] * 100
    r[np.isnan(h).any(axis=1)] = np.nan
    return r


grid = list(range(288, T - 12))  # decision time = start of bar j; needs 24h history and 60m future
rows = []
seq = []
for j in grid:
    r1 = rng(j - 12, j); r24 = rng(j - 288, j); r15 = rng(j - 3, j)
    q1 = np.nansum(qv[:, j - 12:j], axis=1); q24 = np.nansum(qv[:, j - 288:j], axis=1)
    ok = (~np.isnan(r1)) & (~np.isnan(r24)) & (q24 >= QV24) & (q1 >= QV1)
    valid = (~np.isnan(r1)) & (~np.isnan(r24))
    idx_ok = np.where(ok)[0]
    if len(idx_ok) < 10:
        seq.append(None); continue
    order = idx_ok[np.argsort(-r1[idx_ok])]
    raw_order = np.where(valid)[0][np.argsort(-r1[valid])]
    fw = {}
    for h, nb in (("5m", 1), ("15m", 3), ("30m", 6), ("60m", 12)):
        f = rng(j, j + nb)
        fq = np.nansum(qv[:, j:j + nb], axis=1) * (12 / nb)  # hourly-rate quote volume
        fok = ok & ~np.isnan(f)
        fidx = np.where(fok)[0]
        franks = {k: n + 1 for n, k in enumerate(fidx[np.argsort(-f[fidx])])}
        med = np.median(f[fidx])
        fw[h] = (f, fq, franks, med, len(fidx))
    seq.append(order[0])
    rows.append(dict(j=j, hot=order[0], top5=order[:5], n_ok=len(idx_ok), n_valid=int(valid.sum()),
                     r1=r1, r15=r15, raw1=raw_order[0], fw=fw, ok=ok,
                     raw_rank_of_hot=int(np.where(raw_order == order[0])[0][0]) + 1,
                     n_r1_3=int((r1[ok] >= 3).sum()), n_r1_5=int((r1[ok] >= 5).sum()),
                     gap12=float(r1[order[0]] - r1[order[1]])))

P = print
t_of = lambda j: datetime.fromtimestamp((start + j * 300000) / 1000, KST)
P(f"window {t_of(grid[0]):%m-%d %H:%M} .. {t_of(grid[-1]):%m-%d %H:%M} KST, decision points={len(rows)}, symbols={S}")
P("gated universe size per point:", np.percentile([r['n_ok'] for r in rows], [0, 10, 50, 90, 100]).round())
P("gated symbols with 1H range >=3% per point:", np.percentile([r['n_r1_3'] for r in rows], [0, 10, 50, 90, 100]).round(),
  " >=5%:", np.percentile([r['n_r1_5'] for r in rows], [0, 10, 50, 90, 100]).round())
P("points with zero gated symbols >=3%:", sum(r['n_r1_3'] == 0 for r in rows), "/", len(rows))
P("HOT#1 1H range %:", np.percentile([r['r1'][r['hot']] for r in rows], [10, 25, 50, 75, 90]).round(2))
P("raw 1H rank of gated HOT#1:", np.percentile([r['raw_rank_of_hot'] for r in rows], [25, 50, 75, 90]).round(),
  " share where raw #1 itself passes:", round(np.mean([r['raw_rank_of_hot'] == 1 for r in rows]), 3))
b1 = []
for j in grid:
    a = btc[j - 12:j]
    if len(a) == 12:
        b1.append((a[:, 0].max() - a[:, 1].min()) / a[0, 2] * 100)
P("BTC 1H range median %.2f" % np.median(b1))

P("\n=== H. FORWARD PERSISTENCE (non-overlapping future window, gated universe)")
P("h | HOT#1 fwd-rank median | in fwd top5 | top10 | top20 | fwd range / univ median (HOT#1 median) | same for a random gated symbol | same for gated #20 | fwd hourly qv still>=0.5M | fwd/trailing-15m ratio")
rng_ = np.random.default_rng(0)
for h in ("5m", "15m", "30m", "60m"):
    fr, t5, t10, t20, rel, relr, rel20, liq, cont = [], [], [], [], [], [], [], [], []
    for r in rows:
        f, fq, franks, med, n = r["fw"][h]
        k = r["hot"]
        if k not in franks:
            continue
        fr.append(franks[k]); t5.append(franks[k] <= 5); t10.append(franks[k] <= 10); t20.append(franks[k] <= 20)
        rel.append(f[k] / med); liq.append(fq[k] >= QV1)
        okidx = np.where(r["ok"] & ~np.isnan(f))[0]
        rr = rng_.choice(okidx); relr.append(f[rr] / med)
        o = okidx[np.argsort(-r["r1"][okidx])]
        if len(o) >= 20:
            rel20.append(f[o[19]] / med)
        if h == "15m":
            cont.append(f[k] / r["r15"][k] if r["r15"][k] > 0 else np.nan)
    P(f"{h:>3} | {np.median(fr):.0f} (n={len(fr)}) | {np.mean(t5):.2f} | {np.mean(t10):.2f} | {np.mean(t20):.2f} | "
      f"{np.median(rel):.2f}x (p25 {np.percentile(rel,25):.2f}) | {np.median(relr):.2f}x | {np.median(rel20):.2f}x | {np.mean(liq):.2f}"
      + (f" | 15m fwd/trailing median {np.nanmedian(cont):.2f}, share <0.5 (movement faded) {np.nanmean(np.array(cont) < 0.5):.2f}" if cont else ""))

P("\n=== same-definition rank persistence (trailing 1H rank of selected symbol later; overlapping windows)")
pos = {r["j"]: r for r in rows}
for h, nb in (("5m", 1), ("15m", 3), ("30m", 6), ("60m", 12)):
    top1 = top5 = top10 = n = 0
    for r in rows:
        later = pos.get(r["j"] + nb)
        if later is None:
            continue
        o = np.where(later["ok"])[0]; o = list(o[np.argsort(-later["r1"][o])])
        n += 1
        rk = o.index(r["hot"]) + 1 if r["hot"] in o else 999
        top1 += rk == 1; top5 += rk <= 5; top10 += rk <= 10
    P(f" +{h}: still #1 {top1/n:.2f}, top5 {top5/n:.2f}, top10 {top10/n:.2f} (n={n})")

P("\n=== Q5. ONE-SHOT STABILITY")
hs = [r["hot"] for r in rows]
ch = np.mean([a != b for a, b in zip(hs, hs[1:])])
P(f"HOT#1 changes between consecutive 5m points: {ch:.2f}")
for span, nb in (("15m", 3), ("30m", 6), ("60m", 12), ("4h", 48)):
    P(f"  #1 differs from #1 {span} later: {np.mean([a != b for a, b in zip(hs, hs[nb:])]):.2f}")
from collections import Counter
P("distinct HOT#1 per day:", [len(set(hs[d * 288:(d + 1) * 288])) for d in range(len(hs) // 288 + 1)])
P("most frequent HOT#1:", [(syms[k], c) for k, c in Counter(hs).most_common(10)])
P("#1-#2 1H-range gap (pct points):", np.percentile([r['gap12'] for r in rows], [10, 25, 50, 75, 90]).round(2))
P("share of points where #1 leads #2 by <0.5pp (coin-flip):", round(np.mean([r['gap12'] < 0.5 for r in rows]), 3))
tops = [set(r["top5"]) for r in rows]
P("top5 overlap with 5m later (mean of 5):", round(np.mean([len(a & b) for a, b in zip(tops, tops[1:])]), 2),
  " 30m later:", round(np.mean([len(a & b) for a, b in zip(tops, tops[6:])]), 2))

P("\n=== HOUR OF DAY (KST): median gated symbols with 1H range >=3%, median HOT#1 1H range")
by = {}
for r in rows:
    by.setdefault(t_of(r["j"]).hour, []).append((r["n_r1_3"], r["r1"][r["hot"]]))
P(" ".join(f"{h:02d}:{int(np.median([x[0] for x in v]))}/{np.median([x[1] for x in v]):.1f}" for h, v in sorted(by.items())))

# spike check: share of HOT#1 trailing hour range made by a single 5m bar
sp = []
for r in rows:
    k, j = r["hot"], r["j"]
    br = (hi[k, j - 12:j] - lo[k, j - 12:j]) / op[k, j - 12] * 100
    sp.append(br.max() / r["r1"][k])
P("\nsingle-5m-bar share of HOT#1 1H range:", np.percentile(sp, [25, 50, 75, 90]).round(2), " share >0.7:", round(np.mean(np.array(sp) > 0.7), 3))
json.dump({"times": [int(start + r["j"] * 300000) for r in rows], "hot": [syms[r["hot"]] for r in rows],
           "top5": [[syms[k] for k in r["top5"]] for r in rows]}, open("hist_hot_seq.json", "w"))
