"""BTC_TSMOM_PREREG_V4 실행. python3 -I run_tsmom_v4.py"""
import collections
import datetime as dt
import hashlib
import json
import math
import statistics as st

B = json.load(open("bitstamp_1d.json"))
F = json.load(open("funding.json"))
day = lambda s: dt.datetime.fromtimestamp(int(s), dt.UTC).date()
D = [day(x["timestamp"]) for x in B]
C = [float(x["close"]) for x in B]
fund = collections.defaultdict(float)
for x in F:
    fund[dt.datetime.fromtimestamp((x["fundingTime"] - 1) / 1000, dt.UTC).date()] += float(x["fundingRate"])
FEE = 0.0005
SLOW = (20, 24, 28, 32, 36, 40)
FAST = (10, 12, 14, 16, 18, 20)
n = len(C)


def sma(k, j):
    return None if j + 1 < k else sum(C[j - k + 1:j + 1]) / k


S20 = [sma(20, j) for j in range(n)]
S50 = [sma(50, j) for j in range(n)]
S200 = [sma(200, j) for j in range(n)]


def long_w(Ns, j):
    if j < max(Ns):
        return None
    return sum(C[j] / C[j - k] - 1 > 0 for k in Ns) / len(Ns)


def bear(j):
    return S200[j] is not None and C[j] < S200[j]


def short_signals(rule):
    """일 j 종가 결정 → j+1 적용. True면 숏."""
    out = [False] * n
    on, until = False, -1
    for j in range(n):
        if S200[j] is None or j < 231:
            continue
        if rule == "CASH":
            out[j] = False
        elif rule == "R1":
            if on and (C[j] > S20[j] or not bear(j)):
                on = False
            if not on and bear(j) and S50[j] < S50[j - 10] and C[j - 1] > S20[j - 1] and C[j] < S20[j]:
                on = True
            out[j] = on
        elif rule == "R2":
            if bear(j) and C[j] / C[j - 7] - 1 >= 0.10:
                until = j + 7
            out[j] = j < until
        elif rule == "R3":
            out[j] = C[j] < S200[j] and all(S200[k] < S200[k - 1] for k in range(j - 29, j + 1))
    return out


SH = {r: short_signals(r) for r in ("CASH", "R1", "R2", "R3")}


def run(Ns, rule, lo, hi):
    eq, peak, mdd, prev, rets = 1.0, 1.0, 0.0, 0.0, []
    for i in range(1, n):
        if not (lo <= D[i] <= hi):
            continue
        j = i - 1
        w = long_w(Ns, j)
        if w is None or S200[j] is None:
            continue
        p = w - (1 - w) * SH[rule][j]
        r = p * (C[i] / C[i - 1] - 1) - p * fund[D[i]] - FEE * abs(p - prev)
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        rets.append(r)
        prev = p
    vol = st.pstdev(rets) * math.sqrt(365)
    return {"total": eq - 1, "sharpe": st.mean(rets) * 365 / vol if vol else float("nan"), "mdd": mdd,
            "cagr": eq ** (365 / len(rets)) - 1}


P = {"BEAR14": (dt.date(2014, 1, 1), dt.date(2015, 12, 31)), "BEAR18": (dt.date(2018, 1, 1), dt.date(2018, 12, 31)),
     "BEAR22": (dt.date(2022, 1, 1), dt.date(2022, 12, 31)), "B": (dt.date(2023, 1, 1), dt.date(2026, 10, 31)),
     "2016-17": (dt.date(2016, 1, 1), dt.date(2017, 12, 31)), "2019-21": (dt.date(2019, 1, 1), dt.date(2021, 12, 31)),
     "H1 2013-18": (dt.date(2013, 1, 1), dt.date(2018, 12, 31)), "H2 2019-26": (dt.date(2019, 1, 1), dt.date(2026, 10, 31)),
     "전체": (dt.date(2013, 1, 1), dt.date(2026, 10, 31))}
out = {"prereg_sha256": hashlib.sha256(open("/home/tjd618/usb/docs/crypto/btc_tsmom_v1/BTC_TSMOM_PREREG_V4.md", "rb").read()).hexdigest(),
       "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "res": {}}
def hold(lo, hi):
    eq, peak, mdd = 1.0, 1.0, 0.0
    for i in range(1, n):
        if lo <= D[i] <= hi:
            eq *= 1 + (C[i] / C[i - 1] - 1) - fund[D[i]]
            peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    return eq - 1, mdd
print(f"{'조합':12s} " + " | ".join(f"{k:>18s}" for k in P))
print(f"{'그냥보유':12s} " + " | ".join(f"{hold(*v)[0] * 100:+8.0f}% {hold(*v)[1] * 100:+6.0f}%   " for v in P.values()))
for spd, Ns in (("SLOW", SLOW), ("FAST", FAST)):
    for rule in ("CASH", "R1", "R2", "R3"):
        res = {k: run(Ns, rule, *v) for k, v in P.items()}
        out["res"][f"{spd}_{rule}"] = res
        print(f"{spd + '_' + rule:12s} " + " | ".join(f"{r['total'] * 100:+8.0f}% s{r['sharpe']:5.2f}   " for r in res.values()))
R = out["res"]
fast = all(R[f"FAST_CASH"][h]["sharpe"] > R[f"SLOW_CASH"][h]["sharpe"] for h in ("H1 2013-18", "H2 2019-26"))
spd = "FAST" if fast else "SLOW"
adopt = []
for rule in ("R1", "R2", "R3"):
    a, c = R[f"{spd}_{rule}"], R[f"{spd}_CASH"]
    if all(a[b]["total"] > c[b]["total"] for b in ("BEAR14", "BEAR18", "BEAR22")) and a["B"]["sharpe"] >= c["B"]["sharpe"] - 0.10:
        adopt.append(rule)
out["speed"] = "FAST_PREFERRED" if fast else "SLOW"
out["short_adopt"] = adopt
print(f"\n롱 속도: {out['speed']} | 숏: {'SHORT_ADOPT_CANDIDATE ' + ','.join(adopt) if adopt else '근거 없음(롱·현금 유지)'}")
print("prereg", out["prereg_sha256"][:16], "code", out["code_sha256"][:16])
json.dump(out, open("result_v4.json", "w"), indent=1, default=str)
