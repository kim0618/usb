"""BTC_SHORT_SCALP_PREREG_V1 실행. python3 -I run_short_scalp_v1.py"""
import bisect, datetime as dt, hashlib, json, math, statistics as st
H4 = 4 * 3600000
K = json.load(open("klines_4h.json")); F = json.load(open("funding.json"))
T = [k[0] + H4 for k in K]; C = [float(k[4]) for k in K]
ft = [x["fundingTime"] for x in F]; fr = [float(x["fundingRate"]) for x in F]
FEE = 0.0005
UTC = dt.UTC
date_of = lambda t: dt.datetime.fromtimestamp((t - 1) / 1000, UTC).date()
# 일 종가(00 UTC 마감 봉) → SMA200
dclose = {}
for i, t in enumerate(T):
    if t % 86400000 == 0:
        dclose[date_of(t)] = C[i]
ddays = sorted(dclose); dc = [dclose[d] for d in ddays]
sma200 = {ddays[j]: sum(dc[j - 199:j + 1]) / 200 for j in range(199, len(ddays))}
def bear_at(i):
    """봉 i 마감 시점 직전 일 종가와 SMA200 비교."""
    d = date_of(T[i])
    j = bisect.bisect_right(ddays, d) - 1
    while j >= 0 and ddays[j] not in sma200: j -= 1
    if j < 0: return None
    dd = ddays[j]
    return dclose[dd] < sma200[dd]
A = (dt.date(2019, 9, 10), dt.date(2022, 12, 31)); B = (dt.date(2023, 1, 1), dt.date(2026, 10, 31))
def trades(thr, hold, use_bear):
    out = []; busy_until = -1
    for i in range(6, len(T) - hold):
        if i <= busy_until: continue
        if use_bear and not bear_at(i): continue
        if C[i] / C[i - 6] - 1 < thr: continue
        j = i + hold
        f = sum(fr[bisect.bisect_right(ft, T[i]):bisect.bisect_right(ft, T[j])])
        r = -(C[j] / C[i] - 1) + f - 2 * FEE
        out.append((date_of(T[i]), r)); busy_until = j
    return out
def summ(tr, lo, hi):
    x = [r for d, r in tr if lo <= d <= hi]
    if not x: return {"n": 0}
    t = st.mean(x) / (st.stdev(x) / math.sqrt(len(x))) if len(x) > 2 and st.stdev(x) > 0 else float("nan")
    return {"n": len(x), "mean": st.mean(x), "win": sum(v > 0 for v in x) / len(x), "sum": sum(x), "t": t}
def line(lab, tr):
    a, b = summ(tr, *A), summ(tr, *B)
    allx = [r for d, r in tr if A[0] <= d <= B[1]]
    tall = st.mean(allx) / (st.stdev(allx) / math.sqrt(len(allx))) if len(allx) > 2 else float("nan")
    f = lambda s: f"n {s['n']:3d} 평균 {s['mean']*100:+5.2f}% 승률 {s['win']*100:3.0f}% 합 {s['sum']*100:+6.1f}%" if s["n"] else "n   0"
    print(f"{lab:28s} A: {f(a)} | B: {f(b)} | 합산 t {tall:+.2f}")
    return a, b, tall
print("=== 판정 변형: BEAR & 24h ≥ +8% → 8시간 숏")
a, b, tall = line("판정(+8%, 2봉, BEAR)", trades(0.08, 2, True))
verdict = ("INSUFFICIENT" if a["n"] < 15 or b["n"] < 15 else
           "ADOPT" if a["mean"] > 0 and b["mean"] > 0 and tall >= 1.5 else "REJECT")
print("\n=== 묘사 변형")
for thr in (0.05, 0.08, 0.12):
    for hold in (1, 2, 4):
        for ub in (True, False):
            line(f"+{int(thr*100)}% {hold}봉 {'BEAR' if ub else '전체'}", trades(thr, hold, ub))
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
print(f"\n판정: {verdict} | prereg {sha('/home/tjd618/usb/docs/crypto/btc_tsmom_v1/BTC_SHORT_SCALP_PREREG_V1.md')} | code {sha(__file__)}")
