"""BTC_TSMOM_PREREG_V3 실행. python3 -I run_tsmom_v3.py"""
import bisect
import datetime as dt
import hashlib
import json
import math
import statistics as st

H4, D = 4 * 3600000, 86400000
K = json.load(open("klines_4h.json"))
F = json.load(open("funding.json"))
T = [k[0] + H4 for k in K]                 # 봉 마감 시각
C = [float(k[4]) for k in K]
pos_of_t = {t: i for i, t in enumerate(T)}
ft = [x["fundingTime"] for x in F]
fr = [float(x["fundingRate"]) for x in F]
FEE = 0.0005
HS, NS = (0, 4, 8, 12, 16, 20), (20, 24, 28, 32, 36, 40)
A = (dt.date(2019, 11, 5), dt.date(2022, 12, 31))
B = (dt.date(2023, 1, 1), dt.date(2026, 10, 31))
date_of = lambda t: dt.datetime.fromtimestamp((t - 1) / 1000, dt.UTC).date()   # 봉 마감 00:00은 전날 봉


def sub_positions(h, n):
    """봉 i마다 하위 전략 (h, n)의 보유 포지션(그 봉 수익에 적용). 결정 시각 = 매일 h시 봉 마감."""
    pos = [None] * len(T)
    cur = None
    for i, t in enumerate(T):
        pos[i] = cur                                      # 봉 i 동안의 포지션 = 직전까지 정한 값
        if (t // 3600000) % 24 == h:                      # h시 마감 봉 → 다음 봉부터 적용할 결정
            j = pos_of_t.get(t - n * D)
            cur = None if j is None else (1 if C[i] / C[j] - 1 > 0 else 0)
    return pos


SUB = {(h, n): sub_positions(h, n) for h in HS for n in NS}


def weight(i):
    ps = [SUB[k][i] for k in SUB]
    return None if any(p is None for p in ps) else sum(ps) / len(ps)


def lf28_00(i):
    return SUB[(0, 28)][i]


def run(wfun, lo, hi, funding=True):
    eq, peak, mdd, rets, prev, turn, ws, n = 1.0, 1.0, 0.0, [], None, 0.0, [], 0
    for i in range(1, len(T)):
        if not (lo <= date_of(T[i]) <= hi):
            continue
        w = wfun(i)
        if w is None:
            continue
        dw = abs(w - (prev if prev is not None else 0.0))
        f = sum(fr[bisect.bisect_right(ft, T[i - 1]):bisect.bisect_right(ft, T[i])]) if funding else 0.0
        r = w * (C[i] / C[i - 1] - 1) - w * f - FEE * dw
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        rets.append(r)
        turn += dw
        ws.append(w)
        prev = w
    yrs = len(rets) / 2190.0
    vol = st.pstdev(rets) * math.sqrt(2190)
    return {"cagr": eq ** (1 / yrs) - 1, "sharpe": st.mean(rets) * 2190 / vol, "mdd": mdd, "total": eq - 1,
            "avg_weight": st.mean(ws), "turnover_per_year": turn / yrs}


out = {"prereg_sha256": hashlib.sha256(open("/home/tjd618/usb/docs/crypto/btc_tsmom_v1/BTC_TSMOM_PREREG_V3.md", "rb").read()).hexdigest(),
       "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "periods": {}}
bh = lambda i: 1.0
ok_all, mdd_all = True, True
for name, (lo, hi) in (("A", A), ("B", B)):
    res = {"BH": run(bh, lo, hi), "ENS": run(weight, lo, hi), "LF28_00": run(lf28_00, lo, hi),
           "BH_spot": run(bh, lo, hi, False), "ENS_spot": run(weight, lo, hi, False)}
    out["periods"][name] = res
    ok_all &= res["ENS"]["sharpe"] > res["BH"]["sharpe"] and res["ENS"]["mdd"] > res["BH"]["mdd"]
    mdd_all &= res["ENS"]["mdd"] > res["BH"]["mdd"]
    print(f"\n=== 구간 {name} {lo}~{hi}")
    for k, lab in (("BH", "선물 계속보유"), ("ENS", "앙상블(판정)"), ("LF28_00", "V1 LF28 00h"), ("BH_spot", "현물 계속보유"), ("ENS_spot", "현물 앙상블")):
        r = res[k]
        print(f"{lab:12s} CAGR {r['cagr'] * 100:+6.1f}% 샤프 {r['sharpe']:5.2f} MDD {r['mdd'] * 100:+6.1f}% 누적 {r['total'] * 100:+6.0f}% 평균비중 {r['avg_weight'] * 100:3.0f}% 회전/년 {r['turnover_per_year']:5.1f}")
out["verdict"] = "PASS" if ok_all else ("PARTIAL" if mdd_all else "FAIL")
# 연도별
print("\n연도별 (선물 계속보유 / 앙상블):")
for y in range(2019, 2027):
    lo, hi = max(dt.date(y, 1, 1), A[0]), min(dt.date(y, 12, 31), B[1])
    if lo > hi:
        continue
    a, e = run(bh, lo, hi), run(weight, lo, hi)
    print(f"  {y}: {a['total'] * 100:+5.0f}% / {e['total'] * 100:+5.0f}%  (MDD {a['mdd'] * 100:.0f}% / {e['mdd'] * 100:.0f}%)")
print("\n판정:", out["verdict"], "| prereg", out["prereg_sha256"][:16], "| code", out["code_sha256"][:16])
json.dump(out, open("result_v3.json", "w"), indent=1, default=str)
