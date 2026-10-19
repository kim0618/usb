"""C1 V2 analysis exactly per PREREGISTRATION_C1_V2.md sections 7-9."""
import json, sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

REP = Path(__file__).resolve().parent
CASH = 7428.92
recs = [json.loads(l) for l in open(REP / "c1_raw.jsonl")]
cons = json.load(open(REP / "c0_c1_consistency.json"))["consistency"]


def stats(rs):
    n = len(rs)
    if n == 0: return {"n": 0}
    pos = sum(r for r in rs if r > 0); neg = -sum(r for r in rs if r < 0)
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    return {"n": n, "total_r": sum(rs), "avg_r": sum(rs) / n, "pf": (pos / neg) if neg > 0 else None,
            "win_rate": len(wins) / n, "avg_win_r": sum(wins) / len(wins) if wins else None,
            "avg_loss_r": sum(losses) / len(losses) if losses else None}


def mdd(seq):
    peak = cur = 0.0; dd = 0.0
    for x in seq:
        cur += x; peak = max(peak, cur); dd = max(dd, peak - cur)
    return dd


def family(f):
    rs = [r for r in recs if r["family"] == f]
    out = {}
    syms = {r["symbol"] for r in rs}; sess = {r["D"] for r in rs}
    st = Counter(r["status"] for r in rs)
    ok = [r for r in rs if r["status"] == "OK"]
    gap_pass = [r for r in ok if r.get("gap") is not None and 0.02 <= r["gap"] <= 0.15]
    vol_pass = [r for r in ok if r.get("vol_ratio") is not None and r["vol_ratio"] >= 0.05]
    pm = [r for r in ok if r.get("premarket_passed")]
    orf = [r for r in pm if r.get("or_formed")]
    sig = [r for r in ok if r.get("signal")]
    att = [r for r in ok if r.get("exec_attempted")]
    tr = [(r, t) for r in ok for t in r.get("trades", [])]
    rej = Counter(x for r in ok for x in r.get("fill_rejects", []))
    nE = len({r["E"] for r in ok}) or 1
    funnel = {"c0_candidates": cons[f]["c0_total"], "c1_window": cons[f]["c1_window"],
              "candidate_rows": len(rs), "unique_symbols": len(syms), "candidate_sessions": len(sess),
              "d1_data_eligible": len(ok), "status": dict(st),
              "gap_pass": len(gap_pass), "volume_pass": len(vol_pass), "premarket_pass": len(pm),
              "or_formed": len(orf), "entry_signal": len(sig), "execution_attempted": len(att),
              "order_rejects": dict(rej), "price_above_limit": sum(v for k, v in rej.items() if "PRICE_ABOVE_LIMIT" in k),
              "other_reject": sum(v for k, v in rej.items() if "PRICE_ABOVE_LIMIT" not in k),
              "trades": len(tr), "trade_sessions": len({r["E"] for r, _ in tr}),
              "unique_traded_symbols": len({r["symbol"] for r, _ in tr}),
              "same_day_exits": sum(1 for _, t in tr if t["sessions_held"] in ("1", None)),
              "overnight_day2_exits": sum(1 for _, t in tr if t["sessions_held"] not in ("1", None)),
              "gate_reasons": dict(Counter(r.get("gate_reason") for r in ok)),
              "final_reasons_after_pm": dict(Counter(r.get("final_reason") for r in pm)),
              "exit_reasons": dict(Counter(t["exit_reason"] for _, t in tr)),
              "per_session_avg": {k: v / nE for k, v in {"eligible": len(ok), "premarket_pass": len(pm),
                                  "signal": len(sig), "trades": len(tr)}.items()},
              "validity": {"pit": sum(r.get("pit", 0) for r in rs), "invariants": sum(r.get("invariants", 0) for r in rs),
                           "replay_error": st.get("REPLAY_ERROR", 0), "minute_load_fail": st.get("MINUTE_LOAD_FAIL", 0)}}
    out["funnel"] = funnel
    tr.sort(key=lambda x: x[1]["entry_time"])
    rows = []; mismatch = 0
    for r, t in tr:
        ir = float(t["initial_risk"]); g = float(t["raw_gross"]); c = float(t["commission"]); tc = float(t["total_cost"])
        net = float(t["net_pnl"])
        if abs((g - tc) - net) > 0.02: mismatch += 1
        rows.append({"symbol": r["symbol"], "E": r["E"], "entry_time": t["entry_time"], "exit_time": t["exit_time"],
                     "g": g / ir, "f": (g - c) / ir, "n": net / ir, "net_usd": net, "gross_usd": g,
                     "cost_usd": tc, "comm_usd": c, "turnover": float(t["turnover"]), "ir": ir,
                     "exit_reason": t["exit_reason"], "adds": t["pyramid_adds"]})
    funnel["validity"]["net_identity_mismatch"] = mismatch
    perf = {}
    for layer, k in (("GROSS", "g"), ("FEE_ONLY", "f"), ("EXECUTION_V0_NET", "n")):
        s = stats([x[k] for x in rows])
        by_exit = sorted(rows, key=lambda x: x["exit_time"])
        s["mdd_r"] = mdd([x[k] for x in by_exit])
        usd = {"g": "gross_usd", "f": None, "n": "net_usd"}[k]
        pnl_usd = [x["gross_usd"] - (x["comm_usd"] if k == "f" else x["cost_usd"] if k == "n" else 0) for x in by_exit]
        s["return_pct"] = 100 * sum(pnl_usd) / CASH
        s["mdd_pct"] = 100 * mdd(pnl_usd) / CASH
        perf[layer] = s
    if rows:
        perf["costs"] = {"execution_cost_r": sum(x["cost_usd"] / x["ir"] for x in rows),
                         "fee_only_cost_r": sum(x["comm_usd"] / x["ir"] for x in rows),
                         "execution_cost_r_per_trade": sum(x["cost_usd"] / x["ir"] for x in rows) / len(rows),
                         "cost_over_abs_gross_pnl": sum(x["cost_usd"] for x in rows) / abs(sum(x["gross_usd"] for x in rows)) if sum(x["gross_usd"] for x in rows) else None,
                         "cost_over_turnover_bp": 1e4 * sum(x["cost_usd"] for x in rows) / sum(x["turnover"] for x in rows)}
    out["performance"] = perf
    # robustness
    n = len(rows); h = n // 2
    rob = {}
    if n:
        net_total = sum(x["n"] for x in rows)
        rob["first_half_r"] = sum(x["n"] for x in rows[:h]); rob["second_half_r"] = sum(x["n"] for x in rows[h:])
        rob["first_half_n"], rob["second_half_n"] = h, n - h
        rob["first_half_period"] = [rows[0]["E"], rows[h - 1]["E"]] if h else None
        rob["second_half_period"] = [rows[h]["E"], rows[-1]["E"]]
        top5 = sorted((x["n"] for x in rows), reverse=True)[:5]
        rob["top5_win_contribution"] = sum(top5) / net_total if net_total > 0 else None
        bysym = defaultdict(float); cnt = Counter()
        for x in rows: bysym[x["symbol"]] += x["n"]; cnt[x["symbol"]] += 1
        rob["top5_symbol_contribution"] = sum(sorted(bysym.values(), reverse=True)[:5]) / net_total if net_total > 0 else None
        rob["top5_symbols_by_r"] = sorted(bysym.items(), key=lambda kv: -kv[1])[:5]
        rob["unique_traded_symbols"] = len(cnt); rob["repeat_trade_ratio"] = n / len(cnt)
        rob["most_traded"] = cnt.most_common(8); rob["top_symbol_share"] = cnt.most_common(1)[0][1] / n
        # max consecutive sessions same symbol (consecutive E sessions on grid)
        import os
        grid = sorted(f_[:10] for f_ in os.listdir(Path.home() / "usb/data/runtime/strategy_c/raw/grouped")); gi = {d: i for i, d in enumerate(grid)}
        best = 0
        for s_ in cnt:
            idx = sorted({gi[x["E"]] for x in rows if x["symbol"] == s_}); run = 1
            for a, b in zip(idx, idx[1:]):
                run = run + 1 if b == a + 1 else 1; best = max(best, run)
            best = max(best, 1)
        rob["max_consecutive_sessions_same_symbol"] = best
        rob["repeat_symbols_detail"] = [(s_, cnt[s_], round(bysym[s_], 3)) for s_, _ in cnt.most_common(10)]
    out["robustness"] = rob
    # decision
    G, N = perf.get("GROSS", {}), perf.get("EXECUTION_V0_NET", {})
    v = funnel["validity"]
    if v["pit"] or v["invariants"] or v["net_identity_mismatch"] or v["replay_error"] > 0.05 * max(1, len(rs)):
        dec = "INVALID"
    elif n < 50:
        dec = "INSUFFICIENT"
    elif G["avg_r"] <= 0 or G["total_r"] <= 0:
        dec = "GROSS_FAIL"
    elif N["total_r"] <= 0:
        dec = "COST_BOUND"
    elif (N["pf"] is None or N["pf"] < 1.20 or N["avg_r"] <= 0 or rob["first_half_r"] <= 0 or rob["second_half_r"] <= 0
          or rob["top5_win_contribution"] is None or rob["top5_win_contribution"] >= 0.50):
        dec = "NET_FRAGILE"
    else:
        dec = "EDGE_PASS"
    out["sample"] = "INSUFFICIENT" if n < 50 else "EVALUABLE" if n < 100 else "STRONG_SAMPLE"
    out["decision"] = dec
    return out, rows


res = {}; allrows = {}
for f in ("F1", "F3"):
    res[f], allrows[f] = family(f)
d1, d3 = res["F1"]["decision"], res["F3"]["decision"]
if d1 == "EDGE_PASS" and d3 == "EDGE_PASS": final = "F1_F3_EDGE_PASS"
elif d1 == "EDGE_PASS": final = "F1_EDGE_PASS"
elif d3 == "EDGE_PASS": final = "F3_EDGE_PASS"
elif d1 == "INSUFFICIENT" and d3 == "INSUFFICIENT": final = "DATA_INSUFFICIENT"
else: final = "NO_FAMILY_EDGE"
res["FINAL"] = final
json.dump(res, open(REP / "c1_results.json", "w"), indent=1, default=str)
with open(REP / "c1_trades.csv", "w") as fh:
    fh.write("family,symbol,E,entry_time,exit_time,exit_reason,adds,gross_r,fee_r,net_r,net_usd,initial_risk\n")
    for f, rows in allrows.items():
        for x in rows:
            fh.write(f"{f},{x['symbol']},{x['E']},{x['entry_time']},{x['exit_time']},{x['exit_reason']},{x['adds']},{x['g']:.4f},{x['f']:.4f},{x['n']:.4f},{x['net_usd']:.2f},{x['ir']:.2f}\n")
print(json.dumps(res, indent=1, default=str))
