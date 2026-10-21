"""US-B EQUITY AUTO FACTOR V2 portfolio integrity census (research only).
usage (repo root): python integrity_census_v2.py <runtime_root> <fund.parquet> <out_dir>
Imports the V1 census module unchanged (sha verified) and reuses its data and ranking; only the portfolio accounting differs."""
import sys, os, json, hashlib, collections, importlib.util
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
V2_FROZEN = "c1933d02336b7bc83a1b50dbd49dac76627c0ae9a1c4fb3fb9573dd9c712576a"
K2 = json.load(open(HERE + "/FACTOR_V2_INTEGRITY_CONTRACT.json"))
assert hashlib.sha256(json.dumps(K2, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == V2_FROZEN, "V2 contract changed"
V1_PATH = HERE + "/implementation_census_v1.py"
assert hashlib.sha256(open(V1_PATH, "rb").read()).hexdigest() == K2["amends"]["v1_census_script_sha256"], "V1 script changed"
RT, FUND, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
sys.argv = ["v1", RT, FUND.replace(".parquet", "_sic.parquet"), OUT]
spec = importlib.util.spec_from_file_location("census_v1", V1_PATH)
m1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(m1)   # asserts the V1 contract sha itself
assert m1.FROZEN == K2["amends"]["v1_contract_canonical_sha256"]
C, CA, CAf, adj, R1, S, pos = m1.C, m1.CA, m1.CAf, m1.adj, m1.R1, m1.S, m1.pos
signals, execs, CAP, N, TW = m1.signals, m1.execs, m1.CAP, m1.N, m1.TW
SEC_BP, TAF, TAF_MAX = m1.SEC_BP, m1.TAF, m1.TAF_MAX
B = K2["drift_band"]; LO, HI = B["lower"], B["upper"]

# ---------------- ticker transitions (universe-wide)
first_valid = {tk: C[tk].first_valid_index() for tk in C.columns}
last_valid = {tk: C[tk].last_valid_index() for tk in C.columns}
cik_of = collections.defaultdict(set); tick_of = collections.defaultdict(set)
for d in sorted(m1.snaps):
    for tk, r in m1.snaps[d].items():
        if r.get("cik"): cik_of[tk].add(r["cik"]); tick_of[r["cik"]].add(tk)
TRANS, AMBIG, STOPS = {}, {}, {}
for old in C.columns:
    lo = last_valid[old]
    if lo is None or lo == S[-1] or old not in cik_of: continue
    cands = set()
    for cik in cik_of[old]:
        for new in tick_of[cik]:
            if new == old or new not in C.columns or first_valid[new] is None: continue
            fn = first_valid[new]
            if fn > lo and pos[fn] - pos[lo] - 1 <= 5: cands.add(new)
    if len(cands) == 1:
        new = cands.pop(); TRANS[old] = (new, lo, first_valid[new], round(float(C[new].loc[first_valid[new]] / C[old].loc[lo]), 4))
    elif len(cands) > 1: AMBIG[old] = sorted(cands)
    STOPS[old] = lo
fx = K2["ticker_continuity"]["regression_fixture"]
assert TRANS.get("BREA", (None,))[:3] == ("SLMT", "2025-10-02", "2025-10-03"), ("fixture", TRANS.get("BREA"))

def simulate(factor, bp, band, cont):
    c = bp / 1e4
    cash, hold = float(CAP), {}
    trades, recs, flags, events = [], [], collections.Counter(), []
    nav_series, sig_snap = {}, {}
    prev_low = set()
    def eff(tk, i): h = hold[tk]; return h["shares"] * adj[tk].iloc[i] / h["adj0"]
    def val(tk, i): h = hold[tk]; return h["shares"] * h["px0"] * CAf[tk].iloc[i] / h["ca0"]
    def nav(i): return cash + sum(val(tk, i) for tk in hold)
    def rebase(tk, i, shares):
        hold[tk] = {"shares": shares, "px0": C[tk].iloc[i], "ca0": CA[tk].iloc[i], "adj0": adj[tk].iloc[i]}
    def sell(tk, i, e, sh, full):
        nonlocal cash
        px = C[tk].iloc[i]
        if np.isnan(px):
            notional = val(tk, i) * sh / eff(tk, i); flags["sell_at_last_close_missing_exec_price"] += 1
        else: notional = sh * px
        fee = notional * c + notional * SEC_BP + min(sh * TAF, TAF_MAX)
        cash += notional - fee
        trades.append((e, "SELL", tk, round(sh, 6), round(notional, 6), round(fee, 6)))
        if full: del hold[tk]
        else: rebase(tk, i, eff(tk, i) - sh)
        return notional, fee
    def buy(tk, i, e, sh):
        nonlocal cash
        notional = sh * C[tk].iloc[i]; fee = notional * c
        cash -= notional + fee
        trades.append((e, "BUY", tk, sh, round(notional, 6), round(fee, 6)))
        if tk in hold: rebase(tk, i, eff(tk, i) + sh)
        else: rebase(tk, i, sh)
        return notional, fee
    ex_i = {pos[e]: k for k, e in enumerate(execs)}
    sig_i = {pos[s]: k for k, s in enumerate(signals)}
    for i in range(pos[execs[0]] - 1, len(S)):
        # issuer continuity
        if cont:
            for tk in list(hold):
                t = TRANS.get(tk)
                if t and S[i] == t[2]:
                    new, lo, fn, ratio = t
                    sh = hold[tk]["shares"] * adj[tk].loc[lo] / hold[tk]["adj0"]
                    del hold[tk]; hold[new] = {"shares": sh, "px0": C[new].iloc[i], "ca0": CA[new].iloc[i], "adj0": adj[new].iloc[i]}
                    if tk in sig_snap: sig_snap[new] = sig_snap.pop(tk)
                    events.append({"date": S[i], "old": tk, "new": new, "last_old": lo, "old_close": float(C[tk].loc[lo]), "new_close": float(C[new].iloc[i])})
        for tk in hold:
            lv = last_valid[tk]
            if lv is not None and S[i] > lv:
                cls = "TRANSITION" if tk in TRANS else ("AMBIGUOUS" if tk in AMBIG else "DELISTING")
                flags[f"held_after_price_stop_{cls}"] += 1
                if cls == "TRANSITION" and cont and S[i] >= TRANS[tk][2]: flags["I2_stale_transition"] += 1
        if i in sig_i:
            sig_snap = {tk: val(tk, i) for tk in hold}; sig_snap["__nav__"] = nav(i) if hold else float(CAP)
        if i in ex_i:
            k = ex_i[i]; sig = signals[k]; si = pos[sig]; e = S[i]
            R = m1.get_rank(sig, factor); n = len(R["order"])
            ez, hz = max(1, int(0.10 * n)), max(1, int(0.20 * n))
            nav_sig = sig_snap["__nav__"]; target = TW * nav_sig; nav_pre = nav(i)
            buys = sells = costs = 0.0; ntr = 0; cnt = collections.Counter()
            def sizing_px(tk):  # signal close expressed in execution-day share units
                return C[tk].loc[sig] * adj[tk].loc[sig] / adj[tk].iloc[i]
            for tk in sorted(hold):
                r = R["rank"].get(tk)
                if r is None or r > hz:
                    nt, f = sell(tk, i, e, eff(tk, i), True); sells += nt; costs += f; ntr += 1; cnt["exits"] += 1
            if band:
                for tk in sorted(hold):
                    w = sig_snap.get(tk, np.nan) / nav_sig
                    if w > HI:
                        p = sizing_px(tk)
                        if np.isnan(p) or np.isnan(C[tk].iloc[i]): cnt["band_no_price"] += 1; continue
                        ts = max(1, int(np.floor(target / p))); cur = eff(tk, i)
                        if cur - ts > 1e-9:
                            nt, f = sell(tk, i, e, cur - ts, False); sells += nt; costs += f; ntr += 1; cnt["band_trims"] += 1
                for tk in sorted(hold, key=lambda t: R["rank"].get(t, 10 ** 9)):
                    w = sig_snap.get(tk, np.nan) / nav_sig
                    if w < LO:
                        p = sizing_px(tk)
                        if np.isnan(p) or np.isnan(C[tk].iloc[i]): cnt["band_no_price"] += 1; continue
                        add = int(np.floor(target / p)) - int(np.floor(eff(tk, i) + 1e-9)); need = add
                        while add > 0 and add * C[tk].iloc[i] * (1 + c) > cash: add -= 1
                        if add < need: cnt["band_cash_limited"] += 1
                        if add > 0:
                            nt, f = buy(tk, i, e, add); buys += nt; costs += f; ntr += 1; cnt["band_topups"] += 1
            skipped_yet = False
            for tk in R["order"][:ez]:
                if len(hold) >= N: break
                if tk in hold: continue
                sh = int(np.floor(target / C[tk].loc[sig]))
                if sh == 0: cnt["ws_skips"] += 1; skipped_yet = True; continue
                fill = C[tk].iloc[i]
                if np.isnan(fill): cnt["missing_exec_close_buy"] += 1; skipped_yet = True; continue
                while sh > 0 and sh * fill * (1 + c) > cash: sh -= 1
                if sh == 0 or (band and sh * C[tk].loc[sig] < LO * nav_sig): cnt["cash_stops"] += 1; break
                nt, f = buy(tk, i, e, sh); buys += nt; costs += f; ntr += 1; cnt["entries"] += 1
                if skipped_yet: cnt["replacements"] += 1
            nav_post = nav(i)
            vals = {tk: val(tk, i) for tk in hold}
            w = np.array(list(vals.values())) / nav_post if hold else np.array([0.0])
            wi = w / w.sum() if w.sum() > 0 else w
            sigw = {}
            for tk in hold:
                p = sizing_px(tk)
                sigw[tk] = eff(tk, i) * p / nav_sig if not np.isnan(p) else np.nan
            single = {tk for tk in hold if abs(eff(tk, i) - 1) < 1e-9}
            over = [tk for tk, x in sigw.items() if x > HI + 1e-12 and tk not in single]
            low = {tk for tk, x in sigw.items() if x < LO}
            persist = low & prev_low; prev_low = low
            divs = collections.Counter(m1.sic_div(m1.sic.get((R["snap"].get(tk) or m1.snaps[max(m1.snap_dates)].get(tk) or {}).get("cik") or "")) for tk in hold)
            sic2 = collections.Counter(str(m1.sic.get((R["snap"].get(tk) or m1.snaps[max(m1.snap_dates)].get(tk) or {}).get("cik") or ""))[:2] for tk in hold)
            recs.append({"signal": sig, "exec": e, "ranked": n, "holdings": len(hold), "trade_count": ntr, **{k_: cnt.get(k_, 0) for k_ in
                         ["exits", "entries", "ws_skips", "replacements", "cash_stops", "missing_exec_close_buy", "band_trims", "band_topups", "band_cash_limited", "band_no_price"]},
                         "cash_pct": round(100 * cash / nav_post, 3), "nav_pre": round(nav_pre, 4), "nav_post": round(nav_post, 4),
                         "turnover_one_way": (buys + sells) / (2 * nav_pre) if k > 0 else None, "costs": round(costs, 6),
                         "w_max": round(float(w.max()), 4), "w_min": round(float(w.min()), 4), "hhi": round(float((w ** 2).sum()), 5),
                         "effN_nav": round(float(1 / (w ** 2).sum()), 2), "effN_invested": round(float(1 / (wi ** 2).sum()), 2),
                         "sig_w_max": round(float(np.nanmax(list(sigw.values()))), 4) if sigw else None,
                         "sig_w_min": round(float(np.nanmin(list(sigw.values()))), 4) if sigw else None,
                         "I3_over_band": over, "below_lower": sorted(low), "I4_persistent_dust": sorted(persist),
                         "top_division": divs.most_common(1)[0] if divs else None, "top_sic2": sic2.most_common(1)[0] if sic2 else None,
                         "holdings_list": sorted(hold)})
        if i >= pos[execs[0]]:
            nav_series[S[i]] = nav(i)
            if cash < -1e-9: flags["negative_cash"] += 1
            for tk in hold:
                r = R1[tk].iloc[i]
                if not np.isnan(r) and abs(r) > 0.5 and any(abs(i - p) <= 2 for p in m1.split_pos.get(tk, [])): flags["split_related_jump"] += 1
    log = "\n".join(",".join(map(str, t)) for t in trades)
    # I6 accounting reconciliation from the trade log alone
    rc = float(CAP); fee_diff = 0.0
    for e, side, tk, sh, nt, fee in trades:
        f = nt * c + ((nt * SEC_BP + min(sh * TAF, TAF_MAX)) if side == "SELL" else 0.0)
        fee_diff = max(fee_diff, abs(f - fee))
        rc += (nt - fee) if side == "SELL" else -(nt + fee)
    return {"recs": recs, "nav": pd.Series(nav_series), "flags": dict(flags), "events": events, "trades": trades,
            "log_sha": hashlib.sha256(log.encode()).hexdigest(), "cash_end": cash, "cash_reconciled": rc, "fee_max_diff": fee_diff}

def brief(run):
    recs = run["recs"]; reb = recs[1:]; nav = run["nav"]; e0, e12 = execs[0], execs[-1]
    return {"holdings": [r["holdings"] for r in recs], "cash_pct": [round(r["cash_pct"], 2) for r in recs],
            "w_max": [r["w_max"] for r in recs], "w_min": [r["w_min"] for r in recs],
            "sig_w_max": [r["sig_w_max"] for r in recs], "sig_w_min": [r["sig_w_min"] for r in recs],
            "effN_nav": [r["effN_nav"] for r in recs], "effN_invested": [r["effN_invested"] for r in recs],
            "turnover_annual": round(sum(r["turnover_one_way"] for r in reb), 3),
            "cost_drag_annual_pct": round(100 * sum(r["costs"] for r in reb) / nav.loc[e0:e12].mean(), 3),
            "initial_build_cost": round(recs[0]["costs"], 2),
            "trade_count_total_after_build": sum(r["trade_count"] for r in reb),
            "band_trims": sum(r["band_trims"] for r in recs), "band_topups": sum(r["band_topups"] for r in recs),
            "band_cash_limited": sum(r["band_cash_limited"] for r in recs), "band_no_price": sum(r["band_no_price"] for r in recs),
            "ws_skips": sum(r["ws_skips"] for r in recs), "replacements": sum(r["replacements"] for r in recs), "cash_stops": sum(r["cash_stops"] for r in recs),
            "top_division": [r["top_division"] for r in recs], "top_sic2": [r["top_sic2"] for r in recs],
            "window_return_pct": round(100 * (nav.loc[e12] / CAP - 1), 2), "tail_return_pct": round(100 * (nav.iloc[-1] / CAP - 1), 2),
            "flags": run["flags"], "events": run["events"], "log_sha": run["log_sha"]}

# ---------------- attribution helpers
F = pd.read_parquet(FUND); SHO = F[F.k == "SHO"].groupby(["cik", "accn", "filed", "end"]).val.sum().reset_index().sort_values("filed")
SHO_by = {c_: g[["filed", "end", "val"]].values for c_, g in SHO.groupby("cik")}
def mcap(tk, t):
    snap = m1.snaps[max(d for d in m1.snap_dates if d <= t)]
    cik = (snap.get(tk) or m1.snaps[max(m1.snap_dates)].get(tk) or {}).get("cik"); sh = SHO_by.get(cik)
    if sh is None: return np.nan
    sh = sh[sh[:, 0] < t]
    if not len(sh): return np.nan
    filed, end, n_ = sh[-1]
    after = S[S >= end]
    fac = adj.loc[t, tk] / adj.loc[after[0], tk] if len(after) else 1.0
    return n_ / fac * C.loc[t, tk]

if __name__ == "__main__":
    res = {"v2_contract_sha": V2_FROZEN, "v1_contract_sha": m1.FROZEN,
           "transitions_universe": {"count": len(TRANS), "ambiguous": len(AMBIG), "price_stops": len(STOPS),
                                    "list": {k: v for k, v in sorted(TRANS.items())}, "ambiguous_list": AMBIG}}
    ranked_ever = set()
    for f in ["MOM12_2", "LOWVOL252"]:
        for s in signals: ranked_ever |= set(m1.get_rank(s, f)["order"])
    res["transitions_ranked_universe"] = {"price_stops": sum(1 for t in STOPS if t in ranked_ever),
                                          "transitions": sorted(t for t in TRANS if t in ranked_ever),
                                          "ambiguous": sorted(t for t in AMBIG if t in ranked_ever)}
    v1rec = json.load(open(HERE + "/census_v1/census_v1_result.json"))
    for f, etf in [("MOM12_2", "MTUM"), ("LOWVOL252", "USMV")]:
        v1c = simulate(f, 40, band=False, cont=True)
        runs = {bp: simulate(f, bp, band=True, cont=True) for bp in [0, 20, 30, 40]}
        again = simulate(f, 40, band=True, cont=True)
        v2 = runs[40]
        out = {"V1_recorded": {k: v1rec[f][k] for k in ["holdings", "cash_pct", "w_max", "w_min", "hhi", "turnover_annual", "cost_drag_annual_pct"]} | {"window_return_pct": v1rec[f]["returns_sanity"]["40bp"]["window_return_pct"]},
               "V1C": brief(v1c), "V2": brief(v2),
               "V2_returns_by_cost": {bp: round(100 * (r["nav"].loc[execs[-1]] / CAP - 1), 2) for bp, r in runs.items()}}
        out["V1_recorded"]["effN_nav"] = [round(1 / h, 2) for h in v1rec[f]["hhi"]]
        # held price stops in V2
        held = set(t[2] for t in v2["trades"])
        out["held_price_stops"] = {t: ("TRANSITION->" + TRANS[t][0] if t in TRANS else ("AMBIGUOUS" if t in AMBIG else "DELISTING"), STOPS[t]) for t in sorted(held) if t in STOPS}
        g = {}
        g["I1_transition_fixture"] = (f != "MOM12_2") or any(ev["old"] == "BREA" and ev["new"] == "SLMT" and ev["date"] == "2025-10-03" for ev in v2["events"])
        g["I2_separation"] = v2["flags"].get("I2_stale_transition", 0) == 0 and all(t in TRANS or t in AMBIG or t in STOPS for t in out["held_price_stops"])
        g["I3_max_weight"] = all(not r["I3_over_band"] for r in v2["recs"])
        g["I4_no_pathological_dust"] = all(not r["I4_persistent_dust"] for r in v2["recs"])
        g["I5_effective_N"] = min(out["V2"]["effN_invested"]) > min(out["V1C"]["effN_invested"])
        g["I6_accounting"] = abs(v2["cash_end"] - v2["cash_reconciled"]) < 0.01 and v2["fee_max_diff"] < 0.01
        g["I7_determinism"] = again["log_sha"] == v2["log_sha"]
        g["I8_ranking_unchanged"] = True   # enforced by the sha asserts at import; the ranking object is m1.get_rank
        out["gates"] = g; out["cash_end"] = round(v2["cash_end"], 6); out["cash_reconciled"] = round(v2["cash_reconciled"], 6); out["fee_max_diff"] = v2["fee_max_diff"]
        out["I3_I4_detail"] = [{"exec": r["exec"], "over": r["I3_over_band"], "below": r["below_lower"], "persist": r["I4_persistent_dust"]} for r in v2["recs"] if r["I3_over_band"] or r["below_lower"]]
        # attribution diagnostics
        nav = v2["nav"]; rp = nav.pct_change().dropna(); re_ = C[etf].loc[nav.index].pct_change().dropna(); rs = C["SPY"].loc[nav.index].pct_change().dropna()
        mc = [mcap(tk, r["signal"]) for r in v2["recs"] for tk in r["holdings_list"] if tk in C.columns]
        mc = np.array([x for x in mc if not np.isnan(x)])
        out["attribution"] = {"corr_daily_vs_etf": round(float(rp.corr(re_)), 3), "corr_daily_vs_spy": round(float(rp.corr(rs)), 3),
                              "ann_vol_book": round(float(rp.std() * np.sqrt(252) * 100), 1), "ann_vol_etf": round(float(re_.std() * np.sqrt(252) * 100), 1),
                              "beta_vs_spy": round(float(np.cov(rp, rs)[0, 1] / rs.var()), 2),
                              "holding_mcap_usd_bn_p25_median_p75": [round(float(np.percentile(mc, q)) / 1e9, 2) for q in (25, 50, 75)],
                              "holding_mcap_coverage": len(mc), "holding_obs": sum(len(r["holdings_list"]) for r in v2["recs"]),
                              "mean_cash_pct": round(float(np.mean([r["cash_pct"] for r in v2["recs"]])), 2),
                              "etf_window_return_pct": round(100 * (C[etf].loc[execs[-1]] / C[etf].loc[execs[0]] - 1), 2)}
        out["verdict_factor"] = "PASS" if all(g.values()) else "FAIL"
        res[f] = out
        pd.DataFrame(v2["recs"]).to_csv(f"{OUT}/{f}_v2_rebalances_40bp.csv", index=False)
        pd.DataFrame(v2["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(f"{OUT}/{f}_v2_trades_40bp.csv", index=False)
        pd.DataFrame(v1c["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(f"{OUT}/{f}_v1c_trades_40bp.csv", index=False)
        v2["nav"].rename("nav").to_csv(f"{OUT}/{f}_v2_nav_40bp.csv")
    res["verdict"] = "PORTFOLIO_INTEGRITY_PASS" if all(res[f]["verdict_factor"] == "PASS" for f in ["MOM12_2", "LOWVOL252"]) else "PORTFOLIO_INTEGRITY_FAIL"
    json.dump(res, open(f"{OUT}/census_v2_result.json", "w"), indent=1, default=str)
    print(res["verdict"], {f: res[f]["gates"] for f in ["MOM12_2", "LOWVOL252"]})
