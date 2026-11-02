"""BTC_TSMOM_PREREG_V2 실행. python3 -I run_tsmom_v2.py"""
import bisect
import datetime as dt
import hashlib
import json
import math
import statistics as st

H4 = 4 * 3600000
K = json.load(open("klines_4h.json"))
F = json.load(open("funding.json"))
close_at = {k[0] + H4: float(k[4]) for k in K}            # 봉 마감 시각(ms) -> 종가
ft = [x["fundingTime"] for x in F]
fr = [float(x["fundingRate"]) for x in F]
FEE = 0.0005
UTC = dt.UTC
A = (dt.date(2019, 11, 5), dt.date(2022, 12, 31))
B = (dt.date(2023, 1, 1), dt.date(2026, 10, 31))


def series(h):
    """기준 시각 h(UTC 시)의 일 종가 목록 [(날짜, ms, 종가)]. 날짜는 그 시각이 속한 UTC 날짜."""
    out = []
    t = min(close_at)
    t = t - t % 86400000 + h * 3600000
    end = max(close_at)
    while t <= end:
        if t in close_at:
            out.append((dt.datetime.fromtimestamp(t / 1000, UTC).date(), t, close_at[t]))
        t += 86400000
    return out


def funding_between(t0, t1):
    i, j = bisect.bisect_right(ft, t0), bisect.bisect_right(ft, t1)
    return sum(fr[i:j])


def backtest(s, rule, lo, hi):
    eq, peak, mdd, rets, prev, trades, expo, n = 1.0, 1.0, 0.0, [], None, 0, 0, 0
    c = [x[2] for x in s]
    for i in range(1, len(s)):
        if not (lo <= s[i][0] <= hi):
            continue
        pos = rule(c, i - 1)
        if pos is None:
            continue
        cost = FEE * abs(pos) if prev is None else (FEE * (abs(prev) + abs(pos)) if pos != prev else 0.0)
        trades += prev is not None and pos != prev
        r = pos * (c[i] / c[i - 1] - 1) - pos * funding_between(s[i - 1][1], s[i][1]) - cost
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        rets.append(r)
        expo += pos != 0
        prev = pos
    vol = st.pstdev(rets) * math.sqrt(365)
    return {"sharpe": st.mean(rets) * 365 / vol, "cagr": eq ** (365 / len(rets)) - 1, "mdd": mdd,
            "trades_per_year": trades * 365 / len(rets), "exposure": expo / len(rets)}


def R(c, j, n):
    return None if j - n < 0 else c[j] / c[j - n] - 1


def lf(n):
    return lambda c, j: None if R(c, j, n) is None or j < 56 else (1 if R(c, j, n) > 0 else 0)


def sma(c, j, n=200):
    return None if j + 1 < n else sum(c[j - n + 1:j + 1]) / n


def s1(c, j):
    if j < 200:
        return None
    r28, r56 = R(c, j, 28), R(c, j, 56)
    return 1 if r28 > 0 else (-1 if r28 < 0 and r56 < 0 else 0)


def s2(c, j):
    if j < 200:
        return None
    r28 = R(c, j, 28)
    return 1 if r28 > 0 else (-1 if r28 < -0.10 else 0)


def s3(c, j):
    if j < 200:
        return None
    r28 = R(c, j, 28)
    return 1 if r28 > 0 else (-1 if c[j] < sma(c, j) else 0)


def lf28_200(c, j):        # 3단계 후보와 같은 시작점(200일 이력)을 쓰는 LF28 비교 기준
    return None if j < 200 else (1 if R(c, j, 28) > 0 else 0)


bh = lambda c, j: 1 if j >= 56 else None
bh200 = lambda c, j: 1 if j >= 200 else None
out = {"prereg_sha256": hashlib.sha256(open("/home/tjd618/usb/docs/crypto/btc_tsmom_v1/BTC_TSMOM_PREREG_V2.md", "rb").read()).hexdigest(),
       "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest()}
S = {h: series(h) for h in (0, 4, 8, 12, 16, 20)}

# 1부
print("=== 1부: 롱·현금 견고성 (샤프, BH 대비)")
part1 = {}
for name, (lo, hi) in (("A", A), ("B", B)):
    grid, wins = {}, 0
    bhs = {h: backtest(S[h], bh, lo, hi)["sharpe"] for h in S}
    for h in S:
        for n in (20, 24, 28, 32, 36, 40):
            r = backtest(S[h], lf(n), lo, hi)
            grid[(h, n)] = r
            wins += r["sharpe"] > bhs[h]
    med = st.median(r["sharpe"] for r in grid.values())
    robust = wins >= 29 and med > st.median(bhs.values())
    part1[name] = {"wins": wins, "median_sharpe": med, "bh_sharpe_by_h": bhs, "robust": robust}
    print(f"구간 {name}: BH 샤프(기준시각별) " + " ".join(f"{h:02d}h {v:.2f}" for h, v in bhs.items()))
    print(f"   N\\h  " + " ".join(f"{h:02d}h " for h in S))
    for n in (20, 24, 28, 32, 36, 40):
        print(f"   N={n:2d} " + " ".join(f"{grid[(h, n)]['sharpe']:.2f} " for h in S))
    mdds = [r["mdd"] for r in grid.values()]
    print(f"   BH보다 샤프 높은 조합 {wins}/36, 샤프 중앙값 {med:.2f}, MDD 범위 {min(mdds) * 100:.0f}%~{max(mdds) * 100:.0f}% → {'ROBUST' if robust else 'FRAGILE'}")
out["part1"] = {k: {**v, "bh_sharpe_by_h": {str(h): s for h, s in v["bh_sharpe_by_h"].items()}} for k, v in part1.items()}
out["part1_verdict"] = "ROBUST" if all(v["robust"] for v in part1.values()) else "FRAGILE"

# 2부
print("\n=== 2부: 3단계 후보 vs LF28 (기준시각 00, 200일 이력 확보 후)")
part2 = {}
for name, (lo, hi) in (("A", A), ("B", B)):
    base = backtest(S[0], lf28_200, lo, hi)
    b = backtest(S[0], bh200, lo, hi)
    part2[name] = {"LF28": base, "BH": b}
    print(f"구간 {name}: BH 샤프 {b['sharpe']:.2f} MDD {b['mdd'] * 100:.0f}% | LF28 샤프 {base['sharpe']:.2f} CAGR {base['cagr'] * 100:+.1f}% MDD {base['mdd'] * 100:.0f}%")
    for k, f in (("S1", s1), ("S2", s2), ("S3", s3)):
        r = backtest(S[0], f, lo, hi)
        part2[name][k] = r
        print(f"   {k}: 샤프 {r['sharpe']:.2f} CAGR {r['cagr'] * 100:+.1f}% MDD {r['mdd'] * 100:.0f}% 거래/년 {r['trades_per_year']:.0f} 노출 {r['exposure'] * 100:.0f}%")
adopt = [k for k in ("S1", "S2", "S3") if all(part2[p][k]["sharpe"] > part2[p]["LF28"]["sharpe"] for p in ("A", "B"))]
out["part2"] = part2
out["part2_adopt"] = adopt
if adopt:
    print("\n묘사: 통과 후보의 기준시각별 LF28 대비 샤프 차이")
    for k in adopt:
        f = {"S1": s1, "S2": s2, "S3": s3}[k]
        for name, (lo, hi) in (("A", A), ("B", B)):
            print(f"   {k} {name}: " + " ".join(f"{h:02d}h {backtest(S[h], f, lo, hi)['sharpe'] - backtest(S[h], lf28_200, lo, hi)['sharpe']:+.2f}" for h in S))
print(f"\n1부 판정: {out['part1_verdict']} | 2부: {'ADOPT_CANDIDATE ' + ','.join(adopt) if adopt else '숏 미채택(LF 유지)'}")
print("prereg", out["prereg_sha256"][:16], "code", out["code_sha256"][:16])
json.dump(out, open("result_v2.json", "w"), indent=1, default=str)
