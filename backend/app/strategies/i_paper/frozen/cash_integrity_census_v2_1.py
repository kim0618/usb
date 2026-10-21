"""US-B EQUITY AUTO FACTOR V2.1 contract correction + cash integrity census (research only).
usage (repo root): python cash_integrity_census_v2_1.py <runtime_root> <fund.parquet> <out_dir>
Imports the V2 module unchanged (sha verified; it verifies V1) and reuses its data, ranking, transitions and V1C/V2 simulator.
Only the step-5 cash allocation is new."""
import sys, os, json, hashlib, collections, importlib.util
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = "34d8fa7591cbafb753470c02847e00d66fe2a3f53b9e32ae17ff11464ee24e4d"
K = json.load(open(HERE + "/FACTOR_V2_1_CORRECTION_CONTRACT.json"))
assert hashlib.sha256(json.dumps(K, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == FROZEN, "V2.1 contract changed"
V2_PATH = HERE + "/integrity_census_v2.py"
assert hashlib.sha256(open(V2_PATH, "rb").read()).hexdigest() == K["amends"]["v2_script_sha256"], "V2 script changed"
RT, FUND, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
sys.argv = ["v2", RT, FUND, OUT]
spec = importlib.util.spec_from_file_location("census_v2", V2_PATH)
m2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(m2)
assert m2.V2_FROZEN == K["amends"]["v2_contract_canonical_sha256"]
m1 = m2.m1
C, CA, CAf, adj, R1, S, pos = m2.C, m2.CA, m2.CAf, m2.adj, m2.R1, m2.S, m2.pos
signals, execs, CAP, N, TW = m2.signals, m2.execs, m2.CAP, m2.N, m2.TW
SEC_BP, TAF, TAF_MAX, LO, HI, TRANS, AMBIG = m2.SEC_BP, m2.TAF, m2.TAF_MAX, m2.LO, m2.HI, m2.TRANS, m2.AMBIG
last_valid = m2.last_valid

def simulate21(factor, bp):
    """V2 simulator (band + continuity) with step 5 cash allocation and C1/C2 checks."""
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
    def buy(tk, i, e, sh, tag="BUY"):
        nonlocal cash
        notional = sh * C[tk].iloc[i]; fee = notional * c
        cash -= notional + fee
        trades.append((e, tag, tk, sh, round(notional, 6), round(fee, 6)))
        if tk in hold: rebase(tk, i, eff(tk, i) + sh)
        else: rebase(tk, i, sh)
        return notional, fee
    ex_i = {pos[e]: k for k, e in enumerate(execs)}
    sig_i = {pos[s]: k for k, s in enumerate(signals)}
    for i in range(pos[execs[0]] - 1, len(S)):
        for tk in list(hold):
            t = TRANS.get(tk)
            if t and S[i] == t[2]:
                new, lo, fn, ratio = t
                sh = hold[tk]["shares"] * adj[tk].loc[lo] / hold[tk]["adj0"]
                del hold[tk]; hold[new] = {"shares": sh, "px0": C[new].iloc[i], "ca0": CA[new].iloc[i], "adj0": adj[new].iloc[i]}
                if tk in sig_snap: sig_snap[new] = sig_snap.pop(tk)
                events.append({"date": S[i], "old": tk, "new": new})
        for tk in hold:
            lv = last_valid[tk]
            if lv is not None and S[i] > lv:
                cls = "TRANSITION" if tk in TRANS else ("AMBIGUOUS" if tk in AMBIG else "DELISTING")
                flags[f"held_after_price_stop_{cls}"] += 1
                if cls == "TRANSITION" and S[i] >= TRANS[tk][2]: flags["I2_stale_transition"] += 1
        if i in sig_i:
            sig_snap = {tk: val(tk, i) for tk in hold}; sig_snap["__nav__"] = nav(i) if hold else float(CAP)
        if i in ex_i:
            k = ex_i[i]; sig = signals[k]; e = S[i]
            R = m1.get_rank(sig, factor); n = len(R["order"])
            ez, hz = max(1, int(0.10 * n)), max(1, int(0.20 * n))
            nav_sig = sig_snap["__nav__"]; target = TW * nav_sig; nav_pre = nav(i)
            buys = sells = costs = 0.0; ntr = 0; cnt = collections.Counter()
            def sizing_px(tk): return C[tk].loc[sig] * adj[tk].loc[sig] / adj[tk].iloc[i]
            # 1 exits
            for tk in sorted(hold):
                r = R["rank"].get(tk)
                if r is None or r > hz:
                    nt, f = sell(tk, i, e, eff(tk, i), True); sells += nt; costs += f; ntr += 1; cnt["exits"] += 1
            # 2 trims
            for tk in sorted(hold):
                if sig_snap.get(tk, np.nan) / nav_sig > HI:
                    p = sizing_px(tk)
                    if np.isnan(p) or np.isnan(C[tk].iloc[i]): cnt["band_no_price"] += 1; continue
                    ts = max(1, int(np.floor(target / p))); cur = eff(tk, i)
                    if cur - ts > 1e-9:
                        nt, f = sell(tk, i, e, cur - ts, False); sells += nt; costs += f; ntr += 1; cnt["band_trims"] += 1
            # 3 top-ups
            for tk in sorted(hold, key=lambda t: R["rank"].get(t, 10 ** 9)):
                if sig_snap.get(tk, np.nan) / nav_sig < LO:
                    p = sizing_px(tk)
                    if np.isnan(p) or np.isnan(C[tk].iloc[i]): cnt["band_no_price"] += 1; continue
                    add = int(np.floor(target / p)) - int(np.floor(eff(tk, i) + 1e-9)); need = add
                    while add > 0 and add * C[tk].iloc[i] * (1 + c) > cash: add -= 1
                    if add < need: cnt["band_cash_limited"] += 1
                    if add > 0:
                        nt, f = buy(tk, i, e, add); buys += nt; costs += f; ntr += 1; cnt["band_topups"] += 1
            # 4 entries
            skipped_yet = False
            for tk in R["order"][:ez]:
                if len(hold) >= N: break
                if tk in hold: continue
                sh = int(np.floor(target / C[tk].loc[sig]))
                if sh == 0: cnt["ws_skips"] += 1; skipped_yet = True; continue
                fill = C[tk].iloc[i]
                if np.isnan(fill): cnt["missing_exec_close_buy"] += 1; skipped_yet = True; continue
                while sh > 0 and sh * fill * (1 + c) > cash: sh -= 1
                if sh == 0 or sh * C[tk].loc[sig] < LO * nav_sig: cnt["cash_stops"] += 1; break
                nt, f = buy(tk, i, e, sh); buys += nt; costs += f; ntr += 1; cnt["entries"] += 1
                if skipped_yet: cnt["replacements"] += 1
            # 5 cash allocation: held, top-10% zone, signal-basis weight < 5%, rank order, one pass
            c2 = 0
            for tk in sorted(hold, key=lambda t: R["rank"].get(t, 10 ** 9)):
                r = R["rank"].get(tk)
                if r is None or r > ez: continue
                p = sizing_px(tk); fill = C[tk].iloc[i]
                if np.isnan(p) or np.isnan(fill): continue
                cur = eff(tk, i) * p
                if cur >= target - 1e-9: continue
                add = int(np.floor((target - cur) / p + 1e-9))
                while add > 0 and add * fill * (1 + c) > cash: add -= 1
                if add > 0:
                    nt, f = buy(tk, i, e, add, "BUY_ALLOC"); buys += nt; costs += f; ntr += 1; cnt["alloc_trades"] += 1
                    if eff(tk, i) * p > target + 1e-6: c2 += 1
            # C1 completeness
            c1 = 0
            for tk in hold:
                r = R["rank"].get(tk)
                if r is None or r > ez: continue
                p = sizing_px(tk); fill = C[tk].iloc[i]
                if np.isnan(p) or np.isnan(fill): continue
                if (eff(tk, i) + 1) * p <= target + 1e-9 and fill * (1 + c) <= cash: c1 += 1
            nav_post = nav(i)
            w = np.array([val(tk, i) for tk in hold]) / nav_post if hold else np.array([0.0])
            wi = w / w.sum() if w.sum() > 0 else w
            sigw = {tk: (eff(tk, i) * sizing_px(tk) / nav_sig) for tk in hold}
            single = {tk for tk in hold if abs(eff(tk, i) - 1) < 1e-9}
            over = [tk for tk, x in sigw.items() if x > HI + 1e-12 and tk not in single]
            low = {tk for tk, x in sigw.items() if x < LO}
            persist = low & prev_low; prev_low = low
            snapc = lambda tk: (R["snap"].get(tk) or m1.snaps[max(m1.snap_dates)].get(tk) or {}).get("cik") or ""
            sic2 = collections.Counter(str(m1.sic.get(snapc(tk)))[:2] for tk in hold)
            divs = collections.Counter(m1.sic_div(m1.sic.get(snapc(tk))) for tk in hold)
            recs.append({"signal": sig, "exec": e, "holdings": len(hold), "trade_count": ntr, **{k_: cnt.get(k_, 0) for k_ in
                         ["exits", "entries", "ws_skips", "replacements", "cash_stops", "band_trims", "band_topups", "band_cash_limited", "band_no_price", "alloc_trades"]},
                         "C1_violations": c1, "C2_overshoot": c2,
                         "cash_pct": round(100 * cash / nav_post, 3), "nav_post": round(nav_post, 4),
                         "turnover_one_way": (buys + sells) / (2 * nav_pre) if k > 0 else None, "costs": round(costs, 6),
                         "w_max": round(float(w.max()), 4), "w_min": round(float(w.min()), 4),
                         "effN_nav": round(float(1 / (w ** 2).sum()), 2), "effN_invested": round(float(1 / (wi ** 2).sum()), 2),
                         "sig_w_max": round(float(np.nanmax(list(sigw.values()))), 4), "sig_w_min": round(float(np.nanmin(list(sigw.values()))), 4),
                         "I3_over_band": over, "below_lower": sorted(low), "I4_persistent_dust": sorted(persist),
                         "top_sic2": sic2.most_common(1)[0], "top_division": divs.most_common(1)[0], "holdings_list": sorted(hold)})
        if i >= pos[execs[0]]:
            nav_series[S[i]] = nav(i)
            if cash < -1e-9: flags["negative_cash"] += 1
    log = "\n".join(",".join(map(str, t)) for t in trades)
    rc = float(CAP); fee_diff = 0.0
    for e, side, tk, sh, nt, fee in trades:
        f = nt * c + ((nt * SEC_BP + min(sh * TAF, TAF_MAX)) if side == "SELL" else 0.0)
        fee_diff = max(fee_diff, abs(f - fee)); rc += (nt - fee) if side == "SELL" else -(nt + fee)
    return {"recs": recs, "nav": pd.Series(nav_series), "flags": dict(flags), "events": events, "trades": trades,
            "log_sha": hashlib.sha256(log.encode()).hexdigest(), "cash_end": cash, "cash_reconciled": rc, "fee_max_diff": fee_diff}

def view(run):
    recs = run["recs"]; reb = recs[1:]; nav = run["nav"]; e0, e12 = execs[0], execs[-1]
    cp = [r["cash_pct"] for r in recs]
    return {"cash_pct": [round(x, 2) for x in cp], "cash_mean": round(float(np.mean(cp)), 2), "cash_max": round(float(np.max(cp)), 2),
            "holdings": [r["holdings"] for r in recs],
            "turnover_annual": round(sum(r["turnover_one_way"] for r in reb), 3),
            "cost_drag_annual_pct": round(100 * sum(r["costs"] for r in reb) / nav.loc[e0:e12].mean(), 3),
            "effN_invested": [r["effN_invested"] for r in recs], "effN_invested_min": min(r["effN_invested"] for r in recs),
            "w_max": max(r["w_max"] for r in recs), "w_min": min(r["w_min"] for r in recs),
            "sig_w_max": max(r["sig_w_max"] for r in recs), "sig_w_min": min(r["sig_w_min"] for r in recs),
            "top_sic2": [r["top_sic2"] for r in recs], "top_sic2_max": max(r["top_sic2"][1] for r in recs),
            "top_division": [r["top_division"] for r in recs],
            "alloc_trades": sum(r.get("alloc_trades", 0) for r in recs), "band_trims": sum(r["band_trims"] for r in recs), "band_topups": sum(r["band_topups"] for r in recs),
            "trades_after_build": sum(r["trade_count"] for r in reb),
            "window_return_pct": round(100 * (nav.loc[e12] / CAP - 1), 2), "log_sha": run["log_sha"]}

if __name__ == "__main__":
    res = {"v2_1_contract_sha": FROZEN, "v2_contract_sha": m2.V2_FROZEN, "v1_contract_sha": m1.FROZEN}
    for f in ["MOM12_2", "LOWVOL252"]:
        v1c = m2.simulate(f, 40, band=False, cont=True)
        v2 = m2.simulate(f, 40, band=True, cont=True)
        runs = {bp: simulate21(f, bp) for bp in [0, 20, 30, 40]}
        again = simulate21(f, 40)
        x = runs[40]
        V1C, V2v, V21 = view(v1c), view(v2), view(x)
        defect = any(r["I3_over_band"] or r["below_lower"] for r in v1c["recs"])
        if defect:
            i5 = V21["effN_invested_min"] > V1C["effN_invested_min"]; i5_label = "PASS" if i5 else "FAIL"
        else:
            i5 = V21["effN_invested_min"] >= V1C["effN_invested_min"] and all(not r["I3_over_band"] for r in x["recs"]) and V21["top_sic2_max"] <= V1C["top_sic2_max"]
            i5_label = "NOT_APPLICABLE_PASS" if i5 else "FAIL"
        g = {"I1_transition_fixture": (f != "MOM12_2") or any(ev["old"] == "BREA" and ev["new"] == "SLMT" and ev["date"] == "2025-10-03" for ev in x["events"]),
             "I2_separation": x["flags"].get("I2_stale_transition", 0) == 0,
             "I3_max_weight": all(not r["I3_over_band"] for r in x["recs"]),
             "I4_no_pathological_dust": all(not r["I4_persistent_dust"] for r in x["recs"]),
             "I5_effective_N": i5,
             "I6_accounting": bool(abs(x["cash_end"] - x["cash_reconciled"]) < 0.01 and x["fee_max_diff"] < 0.01),
             "I7_determinism": again["log_sha"] == x["log_sha"],
             "I8_ranking_unchanged": True,
             "C1_allocation_complete": all(r["C1_violations"] == 0 for r in x["recs"]),
             "C2_no_overshoot": all(r["C2_overshoot"] == 0 for r in x["recs"]),
             "C3_holdings_le_20": all(r["holdings"] <= N for r in x["recs"])}
        if f == "MOM12_2": g["C4_idle_cash_repaired"] = V21["cash_mean"] < V2v["cash_mean"]
        name = "CASH_INTEGRITY" if f == "MOM12_2" else "PORTFOLIO_INTEGRITY"
        res[f] = {"V1C": V1C, "V2": V2v, "V2_1": V21, "v1c_defect": defect, "I5_label": i5_label, "gates": g,
                  "verdict": f"{name}_PASS" if all(g.values()) else f"{name}_FAIL",
                  "returns_by_cost_V2_1": {bp: round(100 * (r["nav"].loc[execs[-1]] / CAP - 1), 2) for bp, r in runs.items()},
                  "flags": x["flags"], "cash_end": x["cash_end"], "cash_reconciled": x["cash_reconciled"]}
        pd.DataFrame(x["recs"]).to_csv(f"{OUT}/{f}_v2_1_rebalances_40bp.csv", index=False)
        pd.DataFrame(x["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(f"{OUT}/{f}_v2_1_trades_40bp.csv", index=False)
        x["nav"].rename("nav").to_csv(f"{OUT}/{f}_v2_1_nav_40bp.csv")
    json.dump(res, open(f"{OUT}/census_v2_1_result.json", "w"), indent=1, default=str)
    print(json.dumps({f: {"verdict": res[f]["verdict"], "I5": res[f]["I5_label"], "gates": res[f]["gates"]} for f in ["MOM12_2", "LOWVOL252"]}, indent=1))
