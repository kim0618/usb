"""Direction Execution R1 main study (contract sections 1 to 8). HOLDOUT never read (direction_r0.val_data guard).

Run: cd backend && PYTHONPATH=. python3 -m app.crypto.research.direction_execution_r1.r1 [out.json]
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1 import r1_train as R
from ..direction_r0 import features as F
from ..direction_r0 import metrics as M
from ..direction_r0 import models as MO
from ..direction_r0 import study as ST
from ..direction_r0 import val_data as VD
from . import execution as X

CONTRACT = PD.ROOT / "docs/crypto/direction_execution_r1/CONTRACT_DIRECTION_EXECUTION_R1_V1.md"
R0_VAL = PD.ROOT / "data/research/crypto/direction_r0/validation_v1.json"
DIAG = PD.ROOT / "data/research/crypto/direction_execution_r1/aggtrade_diag_v1.json"
OUT = PD.ROOT / "data/research/crypto/direction_execution_r1/r1_v1.json"
MODELS = {"RF": 0.55, "GB": 0.60}
EMBARGO_MIN = 1440


def boot_mean(boot: M.DayBlockBootstrap | None, v: np.ndarray) -> dict:
    if boot is None or len(v) < 2:
        return {"ci95": [float("nan"), float("nan")], "p_le_0": float("nan")}
    c = boot.ci(lambda a: a.mean(), v)
    b = c["boot"]
    return {"ci95": c["ci95"], "p_le_0": float((np.sum(b <= 0) + 1) / (len(b) + 1))}


def summarize(res: list[dict], days: np.ndarray, taker_gross: np.ndarray) -> dict:
    filled = np.array([r["filled"] for r in res])
    n = len(res)
    net_sig = np.array([r["net"] for r in res])
    out = {"signals": n, "filled": int(filled.sum()), "fill_rate": float(filled.mean()) if n else None,
           "unfilled": int((~filled).sum()), "maker_fills": int(sum(r["maker"] for r in res)),
           "net_per_signal": float(net_sig.mean()) if n else None}
    if filled.any():
        g = np.array([r["gross"] for r in res])[filled]
        nt = net_sig[filled]
        fee = np.array([r["fee"] for r in res])[filled]
        out.update({"gross_mean": float(g.mean()), "gross_median": float(np.median(g)), "fee_mean": float(fee.mean()),
                    "funding_mean": float(np.mean([r["funding"] for r in np.array(res)[filled]])),
                    "net_per_filled": float(nt.mean()), "net_median": float(np.median(nt)), "net_pos_share": float((nt > 0).mean()),
                    "pf": R.pf(nt), "mfe_mean": float(np.mean([r["mfe"] for r in np.array(res)[filled]])),
                    "mae_mean": float(np.mean([r["mae"] for r in np.array(res)[filled]]))})
    if (~filled).any():
        tg = taker_gross[~filled]
        out["missed_winner_rate"] = float((tg > 0).mean())
        out["missed_loser_rate"] = float((tg < 0).mean())
        out["adverse_selection_taker_gross_filled_minus_unfilled"] = float(taker_gross[filled].mean() - tg.mean()) if filled.any() else None
    if filled.any():
        out["maker_minus_taker_gross_same_signals"] = float(np.mean(np.array([r["gross"] for r in res])[filled] - taker_gross[filled]))
    return out


def side_stats(y: np.ndarray, d: np.ndarray, g: np.ndarray, mfe: np.ndarray, mae: np.ndarray) -> dict:
    if len(d) == 0:
        return {"n": 0}
    right = d == y
    win, lose = g[g > 0], g[g <= 0]
    aw = float(win.mean()) if len(win) else 0.0
    al = float(-lose.mean()) if len(lose) else 0.0
    return {"n": int(len(d)), "accuracy": float(right.mean()), "gross_mean": float(g.mean()), "gross_median": float(np.median(g)),
            "mfe_mean": float(mfe.mean()), "mae_mean": float(mae.mean()), "avg_winner": aw, "avg_loser": al,
            "payoff_ratio": aw / al if al > 0 else None,
            "move_when_right_bp": float(np.abs(g[right]).mean() * 1e4) if right.any() else None,
            "move_when_wrong_bp": float(np.abs(g[~right]).mean() * 1e4) if (~right).any() else None}


def run(out: Path | None = OUT) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    r0 = json.loads(R0_VAL.read_text())
    log = PD.ReadLog()
    vol, px = VD.load_klines(log)
    funding = VD.load_funding(log)
    ds = F.build(px, vol, VD.load_metrics(log), funding, VD.load_basis(log))
    y = ds.y["4h"]
    entry = ds.hour * 60 + 1
    vs = (VD.VAL_START_MS - VD.START_MS) // 60_000
    tr = np.flatnonzero((ds.label_end_min["4h"] <= vs - EMBARGO_MIN) & (y != 0))
    te = np.flatnonzero((entry >= vs) & (y != 0))
    assert ds.label_end_min["4h"][te].max() + X.WINDOW < VD.N_MIN
    mk = R.make_market(px, funding)
    yo = y[te]
    probs = {}
    for m in MODELS:
        _, probs[m] = MO.fit_predict(m, ds.X[tr], y[tr], ds.X[te], n_jobs=1)
    _, probs["LR_price"] = MO.fit_predict("LR", ds.X[tr][:, ST.cols(["A_price"])], y[tr], ds.X[te][:, ST.cols(["A_price"])])
    rep: dict = {"contract_sha256": sha, "validation_hours": int(len(te)), "train_hours": int(len(tr)),
                 "costs": {"taker": X.TAKER, "maker": X.MAKER, "half_spread_taker_leg": X.HALF_SPREAD,
                           "schedule": "Binance USD-M standard VIP 0 (no account tier on file)"},
                 "window_min": X.WINDOW, "tick": X.TICK}
    # taker outcomes for every validation hour, both directions (for buckets, random baseline, missed winners)
    taker = {d: [X.execute(mk, int(entry[k]), d, "TAKER", "BASE") for k in te] for d in (1, -1)}
    tg = {d: np.array([r["gross"] for r in taker[d]]) for d in (1, -1)}
    tn = {d: np.array([r["net"] for r in taker[d]]) for d in (1, -1)}
    # ---- parity with R0 validation
    parity = {}
    for m, thr in MODELS.items():
        dc = M.classify(probs[m], thr)
        c = dc != 0
        g = np.where(dc[c] == 1, tg[1][c], tg[-1][c])
        nt = np.where(dc[c] == 1, tn[1][c], tn[-1][c])
        r0m = r0["models"][m]
        got = {"n": int(c.sum()), "accuracy": float((dc[c] == yo[c]).mean()), "gross": float(g.mean()), "net": float(nt.mean())}
        want = {"n": r0m["confident"]["n"], "accuracy": r0m["confident"]["accuracy"], "gross": r0m["econ_confident"]["gross_mean"],
                "net": r0m["econ_confident"]["net_mean"]}
        ok = got["n"] == want["n"] and all(abs(got[k] - want[k]) <= 1e-9 for k in ("accuracy", "gross", "net"))
        parity[m] = {"reproduced": got, "r0": want, "match": ok}
        if not ok:
            raise RuntimeError(f"R0 parity failed for {m}: {got} vs {want}")
    rep["r0_parity"] = parity
    # ---- execution grid
    grid, holm_in = {}, {}
    hour_net = {}
    for m, thr in list(MODELS.items()) + [("LR_price@0.60", 0.60), ("LR_price@0.55", 0.55)]:
        p = probs["LR_price"] if m.startswith("LR") else probs[m]
        dc = M.classify(p, thr)
        sig = np.flatnonzero(dc != 0)
        days = ds.day[te][sig]
        tgs = np.where(dc[sig] == 1, tg[1][sig], tg[-1][sig])
        rand_net = (tn[1][sig] + tn[-1][sig]) / 2
        mres = {"threshold": thr, "signals": int(len(sig)), "long_signals": int((dc[sig] == 1).sum()),
                "accuracy": float((dc[sig] == yo[sig]).mean()), "random_direction_net_per_signal": float(rand_net.mean()),
                "variants": {}}
        boot_sig = M.DayBlockBootstrap(days) if len(sig) > 10 else None
        for v in X.VARIANTS:
            for fm in (("BASE",) if v == "TAKER" else X.FILL_MODELS):
                res = [X.execute(mk, int(entry[te[k]]), int(dc[k]), v, fm) for k in sig]
                s = summarize(res, days, tgs)
                filled = np.array([r["filled"] for r in res])
                if fm == "BASE":
                    st = [X.execute(mk, int(entry[te[k]]), int(dc[k]), v, fm, stress=True) for k in sig]
                    s["stress_net_per_signal"] = float(np.mean([r["net"] for r in st]))
                    net_sig = np.array([r["net"] for r in res])
                    s["net_per_signal_ci"] = boot_mean(boot_sig, net_sig)
                    nf = net_sig[filled]
                    bf = M.DayBlockBootstrap(days[filled]) if filled.sum() > 10 else None
                    s["net_per_filled_ci"] = boot_mean(bf, nf)
                    s["accuracy_filled"] = float((dc[sig][filled] == yo[sig][filled]).mean()) if filled.any() else None
                    # random direction under the same variant (paired by timestamp)
                    opp = [X.execute(mk, int(entry[te[k]]), -int(dc[k]), v, fm) for k in sig]
                    s["random_direction_same_variant_net_per_signal"] = float(np.mean([(a["net"] + b["net"]) / 2 for a, b in zip(res, opp)]))
                    by = {}
                    for nm, dd in (("LONG", 1), ("SHORT", -1)):
                        mask = dc[sig] == dd
                        rr = [r for r, mm in zip(res, mask) if mm]
                        ff = np.array([r["filled"] for r in rr], dtype=bool)
                        vals = np.array([r["net"] for r in rr])[ff] if len(rr) else np.array([])
                        bb = M.DayBlockBootstrap(days[mask][ff]) if len(vals) > 10 else None
                        by[nm] = {"signals": int(mask.sum()), "filled": int(ff.sum()),
                                  "net_per_filled": float(vals.mean()) if len(vals) else None,
                                  "gross_per_filled": float(np.array([r["gross"] for r in rr])[ff].mean()) if ff.any() else None,
                                  "net_per_filled_ci": boot_mean(bb, vals)}
                    s["by_direction"] = by
                    hn = np.zeros(len(te))
                    hn[sig] = net_sig
                    hour_net[(m, v)] = hn
                    if not m.startswith("LR"):
                        holm_in[f"{m}+{v}"] = s["net_per_filled_ci"]["p_le_0"] if np.isfinite(s["net_per_filled_ci"]["p_le_0"]) else 1.0
                mres["variants"][f"{v}|{fm}"] = s
        grid[m] = mres
    rep["grid"] = grid
    holm = R.holm(holm_in)
    rep["holm_net_per_filled"] = holm
    # ---- paired comparison vs price-only LR (per validation hour, same variant)
    boot_h = M.DayBlockBootstrap(ds.day[te])
    paired = {}
    for m, thr in MODELS.items():
        lr = "LR_price@0.60" if thr == 0.60 else "LR_price@0.55"
        for v in X.VARIANTS:
            d = hour_net[(m, v)] - hour_net[(lr, v)]
            paired[f"{m}+{v}"] = {"vs": lr, "net_per_hour_diff_bp": float(d.mean() * 1e4), **boot_mean(boot_h, d)}
    rep["paired_vs_price_only_lr"] = paired
    # ---- Q2 / Q3 on all validation hours (TAKER reference)
    q = {}
    for m in ("RF", "GB", "LR_price"):
        p = probs[m]
        d = M.classify(p)
        g = np.where(d == 1, tg[1], tg[-1])
        mfe = np.where(d == 1, [r["mfe"] for r in taker[1]], [r["mfe"] for r in taker[-1]])
        mae = np.where(d == 1, [r["mae"] for r in taker[1]], [r["mae"] for r in taker[-1]])
        conf = np.maximum(p, 1 - p)
        thr = MODELS.get(m, 0.60)
        c = M.classify(p, thr) != 0
        sides = {}
        for nm, dd in (("LONG", 1), ("SHORT", -1)):
            mk_ = (d == dd) & c
            sides[nm] = side_stats(yo[mk_], d[mk_], g[mk_], mfe[mk_], mae[mk_])
            sides[nm + "_all_calls"] = side_stats(yo[d == dd], d[d == dd], g[d == dd], mfe[d == dd], mae[d == dd])
        buckets = {}
        for i in range(len(X.BUCKETS)):
            bm = np.array([X.bucket_of(float(x)) == i for x in conf])
            if not bm.any():
                buckets[X.bucket_label(i)] = {"n": 0}
                continue
            gg = g[bm]
            buckets[X.bucket_label(i)] = {"n": int(bm.sum()), "accuracy": float((d[bm] == yo[bm]).mean()),
                                          "gross_mean": float(gg.mean()), "gross_median": float(np.median(gg)),
                                          "abs_move_mean": float(np.abs(tg[1][bm]).mean()),
                                          "mfe_mean": float(mfe[bm].mean()), "mae_mean": float(mae[bm].mean()),
                                          "avg_winner": float(gg[gg > 0].mean()) if (gg > 0).any() else None,
                                          "avg_loser": float(-gg[gg <= 0].mean()) if (gg <= 0).any() else None,
                                          "long_share": float((d[bm] == 1).mean())}
        q[m] = {"threshold_used_for_sides": thr, "sides_confident": {k: v for k, v in sides.items() if not k.endswith("_all_calls")},
                "sides_all_calls": {k[:-10]: v for k, v in sides.items() if k.endswith("_all_calls")}, "confidence_buckets": buckets}
    q["drift"] = {"always_long_gross_mean": float(tg[1].mean()), "long_base_rate": float((yo == 1).mean()),
                  "abs_move_mean": float(np.abs(tg[1]).mean())}
    rep["q2_q3"] = q
    # ---- gate
    diag = json.loads(DIAG.read_text())
    gate = {}
    for m in MODELS:
        for v in X.VARIANTS:
            b = grid[m]["variants"][f"{v}|BASE"]
            c_ = grid[m]["variants"].get(f"{v}|CONSERVATIVE", b)
            by = b["by_direction"]
            pr = paired[f"{m}+{v}"]
            g = {"G-A_accuracy_filled>0.55": (b.get("accuracy_filled") or 0) > 0.55,
                 "G-B_net_per_filled>0": (b.get("net_per_filled") or -1) > 0,
                 "G-C_median_net>=-5bp": (b.get("net_median") or -1) >= -0.0005,
                 "G-D_net_per_signal>0_base_and_conservative": b["net_per_signal"] > 0 and c_["net_per_signal"] > 0,
                 "G-E_above_price_only_and_random": pr["net_per_hour_diff_bp"] > 0
                 and b["net_per_signal"] > b["random_direction_same_variant_net_per_signal"],
                 "G-F_both_sides_net>=-5bp": all((by[s]["net_per_filled"] if by[s]["net_per_filled"] is not None else -1) >= -0.0005
                                                  for s in ("LONG", "SHORT")) and (b.get("net_per_filled") or -1) > 0,
                 "G-G_holm<=0.05": holm[f"{m}+{v}"] <= 0.05}
            gate[f"{m}+{v}"] = g
    rep["gate"] = gate
    passing = sorted([k for k, g in gate.items() if all(g.values())], key=lambda k: holm[k])
    asym = [k for k in gate if (lambda b: (b["by_direction"]["LONG"]["net_per_filled"] or -1) > 0
                                and b["by_direction"]["LONG"]["net_per_filled_ci"]["ci95"][0] > 0
                                and (b["by_direction"]["SHORT"]["net_per_filled"] if b["by_direction"]["SHORT"]["net_per_filled"] is not None else 1) <= 0)
            (grid[k.split("+")[0]]["variants"][f"{k.split('+')[1]}|BASE"])]
    weak = [k for k in gate if (grid[k.split("+")[0]]["variants"][f"{k.split('+')[1]}|BASE"].get("net_per_filled") or -1) > 0]
    rep["asymmetry_rules"] = asym
    rep["weak_rules"] = weak
    rep["final_candidate"] = passing[:1]
    rep["verdict"] = ("D. DIRECTION_EXECUTION_READY_FOR_HOLDOUT" if passing else "C. DIRECTION_ASYMMETRY_FOUND" if asym
                      else "B. DIRECTION_EXECUTION_WEAK" if weak else "A. DIRECTION_EDGE_NOT_TRADABLE")
    maker_pos = [f"{m}+{v}" for m in MODELS for v in ("M1", "M2", "M3")
                 if grid[m]["variants"][f"{v}|BASE"]["net_per_signal"] > 0 and grid[m]["variants"][f"{v}|CONSERVATIVE"]["net_per_signal"] > 0]
    maker_opt = [f"{m}+{v}" for m in MODELS for v in ("M1", "M2", "M3") if grid[m]["variants"][f"{v}|OPTIMISTIC"]["net_per_signal"] > 0]
    rep["maker_label"] = ("MAKER_POTENTIALLY_VIABLE" if maker_pos else "MAKER_DATA_INSUFFICIENT" if maker_opt else "MAKER_NOT_ENOUGH")
    rep["aggtrade_diag_sha256"] = hashlib.sha256(DIAG.read_bytes()).hexdigest()
    rep["read_log_files"] = len(log.entries)
    rep["read_log_max_date"] = max(e["file"] for e in log.entries)[-16:]
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=_json))
    return rep


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and math.isinf(o):
        return "inf"
    raise TypeError(type(o))


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({"verdict": r["verdict"], "maker": r["maker_label"], "candidate": r["final_candidate"],
                      "parity": {k: v["match"] for k, v in r["r0_parity"].items()}}, indent=1))
