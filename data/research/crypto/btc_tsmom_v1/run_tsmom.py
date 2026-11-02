"""BTC_TSMOM_PREREG_V1 실행. python3 -I run_tsmom.py (같은 폴더의 klines_1d.json, funding.json)"""
import collections
import datetime as dt
import hashlib
import json
import math
import statistics as st

DAY = 86400000
K = json.load(open("klines_1d.json"))
F = json.load(open("funding.json"))
d = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).date()
days = [d(k[0]) for k in K]
close = [float(k[4]) for k in K]
fund = collections.defaultdict(float)          # day -> 그날(00:00 초과 ~ 다음날 00:00 이하) 펀딩 합
for x in F:
    t = x["fundingTime"]
    dd = d(t - 1)                                # 00:00 정각 펀딩은 전날 보유분
    fund[dd] += float(x["fundingRate"])

FEE = 0.0005
NS = (7, 14, 28, 56)
A = (dt.date(2019, 11, 5), dt.date(2022, 12, 31))
B = (dt.date(2023, 1, 1), dt.date(2026, 10, 31))


def run(N, mode, lo, hi):
    """mode: LS, LF, BH. 일 i 수익 = 종가 i-1→i, 포지션은 종가 i-1에 정함."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    rets, pos_prev, trades, expo = [], None, 0, 0
    path = []
    for i in range(1, len(days)):
        if not (lo <= days[i] <= hi):
            continue
        if mode == "BH":
            pos = 1
        else:
            if i - 1 - N < 0:
                continue
            R = close[i - 1] / close[i - 1 - N] - 1
            pos = 1 if R > 0 else (-1 if mode == "LS" else 0)
        cost = 0.0
        if pos_prev is None:
            cost = FEE * abs(pos)
            trades += pos != 0
        elif pos != pos_prev:
            cost = FEE * (abs(pos_prev) + abs(pos))
            trades += 1
        r = pos * (close[i] / close[i - 1] - 1) - pos * fund[days[i]] - cost
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        rets.append(r)
        expo += pos != 0
        pos_prev = pos
        path.append((days[i], eq))
    n = len(rets)
    yrs = n / 365.0
    vol = st.pstdev(rets) * math.sqrt(365)
    mu = st.mean(rets) * 365
    by_year = {}
    last = 1.0
    for y in sorted({p[0].year for p in path}):
        e = [p[1] for p in path if p[0].year == y][-1]
        by_year[y] = e / last - 1
        last = e
    return {"cagr": eq ** (1 / yrs) - 1, "vol": vol, "sharpe": mu / vol if vol else float("nan"), "mdd": mdd,
            "total": eq - 1, "trades_per_year": trades / yrs, "exposure": expo / n, "days": n, "years": by_year}


out = {"prereg_sha256": hashlib.sha256(open("/home/tjd618/usb/docs/crypto/btc_tsmom_v1/BTC_TSMOM_PREREG_V1.md", "rb").read()).hexdigest(),
       "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "periods": {}}
for name, (lo, hi) in (("A", A), ("B", B)):
    res = {"BH": run(0, "BH", lo, hi)}
    for N in NS:
        for m in ("LF", "LS"):
            res[f"{m}{N}"] = run(N, m, lo, hi)
    out["periods"][name] = res
pa, pb = out["periods"]["A"], out["periods"]["B"]
ok = lambda p: p["LF28"]["sharpe"] > p["BH"]["sharpe"] and p["LF28"]["mdd"] > p["BH"]["mdd"]
sh = lambda p: p["LF28"]["sharpe"] > p["BH"]["sharpe"]
out["verdict"] = ("PASS" if ok(pa) and ok(pb) else "FAIL" if not sh(pa) and not sh(pb) else "PARTIAL")
json.dump(out, open("result_v1.json", "w"), indent=1, default=str)
for name in ("A", "B"):
    p = out["periods"][name]
    print(f"\n=== 구간 {name} {A if name == 'A' else B}")
    print(f"{'변형':6s} {'CAGR':>8s} {'변동성':>7s} {'샤프':>6s} {'MDD':>8s} {'누적':>9s} {'거래/년':>7s} {'노출':>5s}")
    for k in ["BH"] + [f"{m}{N}" for m in ("LF", "LS") for N in NS]:
        r = p[k]
        print(f"{k:6s} {r['cagr'] * 100:+7.1f}% {r['vol'] * 100:6.1f}% {r['sharpe']:6.2f} {r['mdd'] * 100:+7.1f}% {r['total'] * 100:+8.0f}% {r['trades_per_year']:7.1f} {r['exposure'] * 100:4.0f}%")
    print("연도별(BH / LF28 / LS28): " + " | ".join(
        f"{y} {p['BH']['years'][y] * 100:+.0f}/{p['LF28']['years'].get(y, 0) * 100:+.0f}/{p['LS28']['years'].get(y, 0) * 100:+.0f}" for y in p["BH"]["years"]))
print("\n판정:", out["verdict"], "| prereg", out["prereg_sha256"][:16], "| code", out["code_sha256"][:16])
