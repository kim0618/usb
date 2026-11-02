"""BTC_TSMOM_PREREG_V5 실행. V4 정의를 그대로 가져온다. python3 -I run_tsmom_v5.py"""
import collections
import datetime as dt
import hashlib
import json

src = open("run_tsmom_v4.py", encoding="utf-8").read().split("P = {")[0]
exec(src)

LOW30 = [None if j < 30 else min(C[j - 29:j + 1]) for j in range(n)]
R3 = SH["R3"]
R3V = [R3[j] and LOW30[j] is not None and C[j] < 1.25 * LOW30[j] for j in range(n)]
CAND = {"SLOW_CASH": ([False] * n, 0.0), "R3": (R3, 1.0), "R3H": (R3, 0.5), "R3V": (R3V, 1.0), "R3VH": (R3V, 0.5)}


def run5(sig, k, lo, hi, contrib=None):
    eq, peak, mdd, prev, rets = 1.0, 1.0, 0.0, 0.0, []
    for i in range(1, n):
        if not (lo <= D[i] <= hi):
            continue
        j = i - 1
        w = long_w(SLOW, j)
        if w is None or S200[j] is None:
            continue
        s = -k * (1 - w) * sig[j]
        p = w + s
        r = p * (C[i] / C[i - 1] - 1) - p * fund[D[i]] - FEE * abs(p - prev)
        if contrib is not None and s:
            contrib[D[i].year][0] += 1
            contrib[D[i].year][1] += s * (C[i] / C[i - 1] - 1) - s * fund[D[i]]
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        rets.append(r)
        prev = p
    vol = st.pstdev(rets) * math.sqrt(365)
    return {"total": eq - 1, "sharpe": st.mean(rets) * 365 / vol, "mdd": mdd}


P = {"BEAR14": (dt.date(2014, 1, 1), dt.date(2015, 12, 31)), "BEAR18": (dt.date(2018, 1, 1), dt.date(2018, 12, 31)),
     "BEAR22": (dt.date(2022, 1, 1), dt.date(2022, 12, 31)), "B": (dt.date(2023, 1, 1), dt.date(2026, 10, 31)),
     "2019-21": (dt.date(2019, 1, 1), dt.date(2021, 12, 31)), "전체": (dt.date(2013, 1, 1), dt.date(2026, 10, 31))}
out = {"prereg_sha256": hashlib.sha256(open("/home/tjd618/usb/docs/crypto/btc_tsmom_v1/BTC_TSMOM_PREREG_V5.md", "rb").read()).hexdigest(),
       "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "res": {}, "pass": []}
print(f"{'후보':10s} " + " | ".join(f"{k:>22s}" for k in P))
for name, (sig, k) in CAND.items():
    res = {p: run5(sig, k, *v) for p, v in P.items()}
    out["res"][name] = res
    print(f"{name:10s} " + " | ".join(f"{r['total'] * 100:+8.0f}% s{r['sharpe']:5.2f} m{r['mdd'] * 100:+4.0f}%" for r in res.values()))
c = out["res"]["SLOW_CASH"]
for name in ("R3", "R3H", "R3V", "R3VH"):
    r = out["res"][name]
    ok = (all(r[b]["total"] > c[b]["total"] for b in ("BEAR14", "BEAR18", "BEAR22"))
          and r["B"]["sharpe"] >= c["B"]["sharpe"] - 0.10 and r["2019-21"]["sharpe"] >= c["2019-21"]["sharpe"] - 0.10)
    if ok:
        out["pass"].append(name)
print("\n연도별 숏 기여(단순 합, %p):")
for name in ("R3", "R3H", "R3V", "R3VH"):
    cb = collections.defaultdict(lambda: [0, 0.0])
    run5(CAND[name][0], CAND[name][1], dt.date(2013, 1, 1), dt.date(2026, 10, 31), cb)
    print(f"  {name:5s} " + ", ".join(f"{y} {v[0]}일 {v[1] * 100:+.0f}" for y, v in sorted(cb.items())))
print("\nPASS:", out["pass"] or "없음", "| prereg", out["prereg_sha256"][:16], "| code", out["code_sha256"][:16])
json.dump(out, open("result_v5.json", "w"), indent=1, default=str)
