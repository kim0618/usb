"""US-B EQUITY AUTO FACTOR V1 implementation census (research only).
usage: python implementation_census_v1.py <runtime_root> <sic.parquet> <out_dir>
Refuses to run unless FACTOR_V1_IMPLEMENTATION_CONTRACT.json matches the frozen canonical sha256."""
import gzip, json, glob, os, sys, hashlib, collections
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = "c253d7868d0680c83d083bf5b5ed69967002452ed592be6aad538cb11474f733"
K = json.load(open(HERE + "/FACTOR_V1_IMPLEMENTATION_CONTRACT.json"))
assert hashlib.sha256(json.dumps(K, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == FROZEN, "contract changed"
RT, SICP, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
RAW = RT + "/strategy_c/raw"
U, P = K["universe"], K["portfolio"]
EXCH, EXCL = set(U["primary_exchange"]), set(U["excluded_tickers"])
BENCH = ["SPY", "RSP", "MTUM", "USMV"]
CAP, N, TW = P["capital_usd"], P["N"], P["target_weight"]
SEC_BP = K["cost"]["regulatory_on_sells"]["sec_section31_bp"] / 1e4
TAF, TAF_MAX = K["cost"]["regulatory_on_sells"]["finra_taf_usd_per_share"], K["cost"]["regulatory_on_sells"]["finra_taf_max_usd"]

# ---------------- data (same loaders as prevalidation_probe.py)
snaps = {}
for p in sorted(glob.glob(RAW + "/tickers/CS_*.json.gz") + glob.glob(RT + "/research_universe_u1/reference/tickers/CS_*.json.gz")):
    j = json.loads(gzip.open(p).read())
    rs = j["results"] if "results" in j else [r for pg in j["pages"] for r in pg["results"]]
    snaps[j.get("as_of") or os.path.basename(p)[3:13]] = {r["ticker"]: r for r in rs}
snap_dates = sorted(snaps)
all_cs = set().union(*[set(s) for s in snaps.values()])
rows = []
for p in sorted(glob.glob(RAW + "/grouped/*.json.gz")):
    j = json.loads(gzip.open(p).read()); b = j.get("body") or {}
    rs = b.get("results") if isinstance(b, dict) else None
    if not rs: continue
    for r in rs:
        if r["T"] in all_cs or r["T"] in BENCH:
            rows.append((j["session"], r["T"], r["c"], r["v"]))
px = pd.DataFrame(rows, columns=["s", "T", "c", "v"])
S = pd.Index(sorted(set(px.s)))
C = px.pivot(index="s", columns="T", values="c").reindex(S)
V = px.pivot(index="s", columns="T", values="v").reindex(S)
DV = C * V
sp = []
for p in glob.glob(RAW + "/splits/*.json.gz"):
    sp += json.loads(gzip.open(p).read())["results"]
sp = pd.DataFrame(sp).drop_duplicates("id")
adj = pd.DataFrame(1.0, index=S, columns=C.columns)
split_pos = collections.defaultdict(list)
for r in sp.itertuples():
    if r.ticker not in adj.columns or r.split_to == 0: continue
    pre = S < r.execution_date
    if pre.all() or not pre.any(): continue
    adj.loc[pre, r.ticker] *= r.split_from / r.split_to
    split_pos[r.ticker].append(int(np.argmax(~pre)))
CA = C * adj
CAf = CA.ffill()
R1 = CA / CA.shift(1) - 1
pos = {s: i for i, s in enumerate(S)}
sd = pd.to_datetime(pd.Series(S, index=S))
month_end = sd.groupby(sd.dt.to_period("M")).max().dt.strftime("%Y-%m-%d").tolist()
cal = K["calendar"]
signals = [m for m in month_end if cal["first_signal"] <= m <= cal["last_signal"]]
execs = [S[pos[s] + 1] for s in signals]
assert len(execs) == cal["executions"], (len(execs), signals)
SIC = pd.read_parquet(SICP); sic = dict(zip(SIC.cik, SIC.sic))

def sic_div(c):
    try: x = int(str(c)[:2])
    except Exception: return "UNKNOWN"
    for lo, hi, name in [(1, 9, "Agri"), (10, 14, "Mining"), (15, 17, "Construction"), (20, 39, "Manufacturing"), (40, 49, "TransUtil"),
                         (50, 51, "Wholesale"), (52, 59, "Retail"), (60, 67, "Finance"), (70, 89, "Services"), (90, 99, "Public")]:
        if lo <= x <= hi: return name
    return "UNKNOWN"

def ranked(t, factor):
    i = pos[t]; snap = snaps[max(d for d in snap_dates if d <= t)]
    uni = [tk for tk, r in snap.items() if r.get("primary_exchange") in EXCH and tk in C.columns and tk not in EXCL]
    c_t = C.loc[t, uni]; adv = DV.iloc[i - 19:i + 1][uni].median()
    elig = [tk for tk in uni if c_t[tk] >= U["min_close_unadjusted_usd"] and adv[tk] >= 5e6]
    if factor == "MOM12_2":
        ok = (R1.iloc[i - 251:i - 20][elig].notna().sum() >= 200) & CA.iloc[i - 252][elig].notna() & CA.iloc[i - 21][elig].notna()
        val = (CA.iloc[i - 21][elig] / CA.iloc[i - 252][elig] - 1).where(ok)
        asc = False
    else:
        w = R1.iloc[i - 251:i + 1][elig]
        val = w.std(ddof=1).where(w.notna().sum() >= 200); asc = True
    v = val.dropna()
    df = pd.DataFrame({"v": v, "tk": v.index}).sort_values(["v", "tk"], ascending=[asc, True])
    order = list(df.tk)
    return {"eligible": len(elig), "order": order, "rank": {tk: k + 1 for k, tk in enumerate(order)}, "snap": snap}

RANK_CACHE = {}
def get_rank(t, f):
    if (t, f) not in RANK_CACHE: RANK_CACHE[(t, f)] = ranked(t, f)
    return RANK_CACHE[(t, f)]

def simulate(factor, bp):
    c = bp / 1e4
    cash, hold = float(CAP), {}           # tk -> dict(shares, px0, ca0, adj0, i0)
    trades, recs, flags = [], [], collections.Counter()
    nav_series = {}
    def val(tk, i):
        h = hold[tk]; return h["shares"] * h["px0"] * CAf[tk].iloc[i] / h["ca0"]
    def nav(i): return cash + sum(val(tk, i) for tk in hold)
    start, stop = pos[execs[0]], len(S) - 1
    ex_i = {pos[e]: k for k, e in enumerate(execs)}
    for i in range(start, stop + 1):
        if i in ex_i:
            k = ex_i[i]; sig = signals[k]; si = pos[sig]; e = S[i]
            R = get_rank(sig, factor); n = len(R["order"])
            ez, hz = max(1, int(0.10 * n)), max(1, int(0.20 * n))
            nav_sig = cash + sum(val(tk, si) for tk in hold) if hold else float(CAP)
            nav_pre = nav(i); buys = sells = costs = 0.0; ntr = 0
            # sells
            for tk in sorted(hold):
                r = R["rank"].get(tk)
                if r is not None and r <= hz: continue
                v = val(tk, i)
                if np.isnan(C[tk].iloc[i]): flags["sell_at_last_close_missing_exec_price"] += 1
                sh_now = hold[tk]["shares"] * adj[tk].iloc[i] / hold[tk]["adj0"]
                fee = v * c + v * SEC_BP + min(sh_now * TAF, TAF_MAX)
                cash += v - fee; sells += v; costs += fee; ntr += 1
                trades.append((e, "SELL", tk, round(sh_now, 6), round(v, 4), round(fee, 6)))
                del hold[tk]
            # buys
            target = TW * nav_sig
            ws_skip = repl = cash_skip = miss = 0; skipped_yet = False
            for tk in R["order"][:ez]:
                if len(hold) >= N: break
                if tk in hold: continue
                sh = int(np.floor(target / C[tk].loc[sig]))
                if sh == 0: ws_skip += 1; skipped_yet = True; continue
                fill = C[tk].iloc[i]
                if np.isnan(fill): miss += 1; skipped_yet = True; continue
                while sh > 0 and sh * fill * (1 + c) > cash: sh -= 1
                if sh == 0: cash_skip += 1; break
                notional = sh * fill; fee = notional * c
                cash -= notional + fee; buys += notional; costs += fee; ntr += 1
                if skipped_yet: repl += 1
                hold[tk] = {"shares": sh, "px0": fill, "ca0": CA[tk].iloc[i], "adj0": adj[tk].iloc[i], "i0": i}
                trades.append((e, "BUY", tk, sh, round(notional, 4), round(fee, 6)))
            nav_post = nav(i)
            w = np.array([val(tk, i) / nav_post for tk in hold]) if hold else np.array([0.0])
            divs = collections.Counter(sic_div(sic.get(R["snap"].get(tk, {}).get("cik") or "")) for tk in hold)
            sic2 = collections.Counter(str(sic.get(R["snap"].get(tk, {}).get("cik") or ""))[:2] for tk in hold)
            pre_w = None
            recs.append({"signal": sig, "exec": e, "eligible": R["eligible"], "ranked": n, "entry_zone": ez, "hold_zone": hz,
                         "holdings": len(hold), "buys_n": sum(1 for t_ in trades if t_[0] == e and t_[1] == "BUY"),
                         "sells_n": sum(1 for t_ in trades if t_[0] == e and t_[1] == "SELL"), "trade_count": ntr,
                         "ws_skips": ws_skip, "replacements": repl, "cash_skips": cash_skip, "missing_exec_close_buy": miss,
                         "cash_pct": round(100 * cash / nav_post, 2), "nav_pre": round(nav_pre, 2), "nav_post": round(nav_post, 2),
                         "turnover_one_way": (buys + sells) / (2 * nav_pre) if k > 0 else None, "costs": round(costs, 4),
                         "w_min": round(float(w.min()), 4), "w_max": round(float(w.max()), 4), "w_std": round(float(w.std()), 4),
                         "hhi": round(float((w ** 2).sum()), 4), "top_division": divs.most_common(1)[0] if divs else None,
                         "top_sic2": sic2.most_common(1)[0] if sic2 else None, "target_usd": round(target, 2)})
        nav_series[S[i]] = nav(i)
        if cash < -1e-9: flags["negative_cash"] += 1
        # G6: large adjusted move near a split for held names
        for tk in hold:
            r = R1[tk].iloc[i]
            if not np.isnan(r) and abs(r) > 0.5:
                flags["held_abs_move_gt50"] += 1
                if any(abs(i - p) <= 2 for p in split_pos.get(tk, [])): flags["G6_split_related_jump"] += 1
            if np.isnan(C[tk].iloc[i]): flags["held_missing_price_days"] += 1
    nav = pd.Series(nav_series)
    # pre-trade top weights at month-end signals
    log = "\n".join(",".join(map(str, t)) for t in trades)
    return {"recs": recs, "nav": nav, "flags": dict(flags), "trades": trades, "log_sha": hashlib.sha256(log.encode()).hexdigest()}

def summarize(factor, runs):
    prim = runs[40]; recs = prim["recs"]
    reb = [r for r in recs[1:]]
    nav = prim["nav"]; e0, e12 = execs[0], execs[-1]
    mean_nav = nav.loc[e0:e12].mean()
    out = {"executions": len(recs),
           "eligible_min": min(r["eligible"] for r in recs), "ranked_min": min(r["ranked"] for r in recs),
           "coverage_min": round(min(r["ranked"] / r["eligible"] for r in recs), 4),
           "holdings": [r["holdings"] for r in recs],
           "ws_skips_total": sum(r["ws_skips"] for r in recs), "replacements_total": sum(r["replacements"] for r in recs),
           "cash_skips_total": sum(r["cash_skips"] for r in recs), "missing_exec_close_buy": sum(r["missing_exec_close_buy"] for r in recs),
           "cash_pct": [r["cash_pct"] for r in recs],
           "turnover_one_way": [round(r["turnover_one_way"], 4) for r in reb],
           "turnover_annual": round(sum(r["turnover_one_way"] for r in reb), 3),
           "trade_count": [r["trade_count"] for r in recs],
           "initial_build_cost": recs[0]["costs"],
           "cost_drag_annual_pct": round(100 * sum(r["costs"] for r in reb) / mean_nav, 3),
           "w_max": [r["w_max"] for r in recs], "w_min": [r["w_min"] for r in recs], "w_std": [r["w_std"] for r in recs], "hhi": [r["hhi"] for r in recs],
           "top_division": [r["top_division"] for r in recs], "top_sic2": [r["top_sic2"] for r in recs],
           "flags": prim["flags"], "log_sha_40bp": prim["log_sha"]}
    ret = {}
    for bp, run in runs.items():
        nv = run["nav"]
        ret[f"{bp}bp"] = {"window_return_pct": round(100 * (nv.loc[e12] / CAP - 1), 2),
                         "tail_to_last_session_pct": round(100 * (nv.iloc[-1] / CAP - 1), 2),
                         "monthly": [round(100 * (nv.loc[execs[k + 1]] / nv.loc[execs[k]] - 1), 2) for k in range(len(execs) - 1)]}
    bench = {b: round(100 * (C[b].loc[e12] / C[b].loc[e0] - 1), 2) for b in BENCH}
    ref = K["benchmarks"]["factor_reference"][factor]
    net = ret["40bp"]["window_return_pct"]
    ret["relative_net40_pct_points"] = {"SPY": round(net - bench["SPY"], 2), "RSP": round(net - bench["RSP"], 2), ref: round(net - bench[ref], 2)}
    ret["benchmarks_pct"] = bench
    out["returns_sanity"] = ret
    # gates
    hs = out["holdings"]; cp = out["cash_pct"]
    G = {"G1_coverage": out["coverage_min"] >= 0.90, "G2_cross_section": out["ranked_min"] >= 500,
         "G3_holdings": sum(h >= 18 for h in hs) >= 11 and min(hs) >= 15,
         "G4_cash_drag": float(np.median(cp)) <= 10 and max(cp) <= 20,
         "G5_turnover": out["turnover_annual"] <= (3.0 if factor == "MOM12_2" else 1.2),
         "G6_corporate_action": out["flags"].get("G6_split_related_jump", 0) == 0,
         "G8_no_collapse": out["flags"].get("negative_cash", 0) == 0 and bool(np.isfinite(nav).all()) and min(hs) > 0}
    out["gates"] = G
    return out

if __name__ == "__main__":
    result = {"contract_sha": FROZEN, "signals": signals, "executions": execs}
    for f in ["MOM12_2", "LOWVOL252"]:
        runs = {bp: simulate(f, bp) for bp in [0, 20, 30, 40]}
        again = simulate(f, 40)
        s = summarize(f, runs)
        s["gates"]["G7_determinism"] = again["log_sha"] == runs[40]["log_sha"]
        s["verdict"] = "IMPLEMENTABLE" if all(s["gates"].values()) else "NOT_IMPLEMENTABLE"
        result[f] = s
        pd.DataFrame(runs[40]["recs"]).to_csv(f"{OUT}/{f}_rebalances_40bp.csv", index=False)
        pd.DataFrame(runs[40]["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(f"{OUT}/{f}_trades_40bp.csv", index=False)
        runs[40]["nav"].rename("nav").to_csv(f"{OUT}/{f}_nav_40bp.csv")
    json.dump(result, open(f"{OUT}/census_v1_result.json", "w"), indent=1, default=str)
    print(json.dumps({f: {k: v for k, v in result[f].items() if k in ("verdict", "gates")} for f in ["MOM12_2", "LOWVOL252"]}, indent=1, default=str))
