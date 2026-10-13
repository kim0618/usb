"""R2 final HOLDOUT test. Phase 1 (pre-open): fit on TRAIN, verify VALIDATION parity, write provenance.
Phase 2: open HOLDOUT once, predict with the same fitted objects, evaluate T1..T5.

Run: cd backend && PYTHONPATH=. python3 -m app.crypto.research.direction_asymmetry_r2.holdout [out_dir]
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
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
from . import hdata as HD

CONTRACT = PD.ROOT / "docs/crypto/direction_asymmetry_r2/CONTRACT_DIRECTION_R2_FINAL_HOLDOUT_V1.md"
R0_VAL = PD.ROOT / "data/research/crypto/direction_r0/validation_v1.json"
OUT_DIR = PD.ROOT / "data/research/crypto/direction_asymmetry_r2"
CODE = [Path(__file__), Path(HD.__file__), Path(F.__file__), Path(MO.__file__), Path(M.__file__), Path(ST.__file__),
        Path(VD.__file__), Path(R.__file__), Path(PD.__file__)]
THR = {"GB": 0.60, "RF": 0.55, "LR_price": 0.60}
BUCKETS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.0001))
MIN_BUCKET = 100
EMBARGO = 1440


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ranks(x: np.ndarray) -> np.ndarray:
    _, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    return (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = ranks(a), ranks(b)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def one_sided(boot: np.ndarray) -> float:
    return float((np.sum(boot <= 0) + 1) / (len(boot) + 1))


def fit_all(ds, y, tr):
    fitted = {}
    for m in ("GB", "RF"):
        mdl = MO.make(m, n_jobs=1)
        mdl.fit(ds.X[tr], (y[tr] == 1).astype(int))
        fitted[m] = mdl
    c = ST.cols(["A_price"])
    lr = MO.make("LR")
    lr.fit(ds.X[tr][:, c], (y[tr] == 1).astype(int))
    fitted["LR_price"] = lr
    return fitted


def predict(fitted, X):
    out = {m: fitted[m].predict_proba(X)[:, 1] for m in ("GB", "RF")}
    out["LR_price"] = fitted["LR_price"].predict_proba(X[:, ST.cols(["A_price"])])[:, 1]
    return out


def outcomes(mk, entry, idx):
    tab = {}
    for d in (1, -1):
        rows = [R.simulate(mk, int(entry[k]), d, int(entry[k]) + 240, []) for k in idx]
        tab[d] = {f: np.array([r[f] for r in rows]) for f in ("gross", "net", "mfe", "mae")}
    return tab


def side_take(tab, d, f):
    return np.where(d == 1, tab[1][f], tab[-1][f])


def run(out_dir: Path = OUT_DIR) -> dict:
    contract_sha = sha(CONTRACT.read_bytes())
    # ---------------- phase 1: pre-open
    log1 = PD.ReadLog()
    vol, px = VD.load_klines(log1)
    funding = VD.load_funding(log1)
    ds = F.build(px, vol, VD.load_metrics(log1), funding, VD.load_basis(log1))
    y = ds.y["4h"]
    entry = ds.hour * 60 + 1
    vs = (VD.VAL_START_MS - VD.START_MS) // 60_000
    tr = np.flatnonzero((ds.label_end_min["4h"] <= vs - EMBARGO) & (y != 0))
    te = np.flatnonzero((entry >= vs) & (y != 0))
    fitted = fit_all(ds, y, tr)
    pv = predict(fitted, ds.X[te])
    mk_v = R.make_market(px, funding)
    r0 = json.loads(R0_VAL.read_text())
    tab_v = outcomes(mk_v, entry, te)
    parity = {}
    for m, thr in (("GB", 0.60), ("RF", 0.55)):
        d = M.classify(pv[m], thr)
        c = d != 0
        got = {"n": int(c.sum()), "accuracy": float((d[c] == y[te][c]).mean()),
               "gross": float(side_take(tab_v, d, "gross")[c].mean()), "net": float(side_take(tab_v, d, "net")[c].mean())}
        want = r0["models"][m]
        ok = (got["n"] == want["confident"]["n"] and abs(got["accuracy"] - want["confident"]["accuracy"]) <= 1e-9
              and abs(got["gross"] - want["econ_confident"]["gross_mean"]) <= 1e-9
              and abs(got["net"] - want["econ_confident"]["net_mean"]) <= 1e-9)
        parity[m] = {"got": got, "match": ok}
        if not ok:
            raise RuntimeError(f"VALIDATION parity failed for {m}; HOLDOUT not opened")
    prov = {"contract_sha256": contract_sha,
            "code_sha256": {p.name if p.parent.name != "direction_asymmetry_r2" else "r2/" + p.name: sha(p.read_bytes()) for p in CODE},
            "feature_config_sha256": sha(json.dumps({"families": F.FAMILIES, "horizons": F.HORIZONS}, sort_keys=True).encode()),
            "cost_config": {"fee_side": R.FEE_SIDE, "base_rt": R.BASE_RT, "funding": "Binance actual settlements"},
            "cost_config_sha256": sha(json.dumps([R.FEE_SIDE, R.BASE_RT]).encode()),
            "model_pickle_sha256": {m: sha(pickle.dumps(fitted[m])) for m in fitted},
            "validation_prediction_sha256": {m: sha(np.asarray(pv[m]).tobytes()) for m in pv},
            "validation_parity": parity, "train_hours": int(len(tr)),
            "phase1_files_read": len(log1.entries),
            "phase1_latest_file": sorted(e["file"].split("/")[-1] for e in log1.entries if "metrics" not in e["file"])[-1]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "provenance_v1.json").write_text(json.dumps(prov, indent=1, sort_keys=True))
    # ---------------- phase 2: HOLDOUT (opened once)
    log2 = PD.ReadLog()
    vol2, px2 = HD.load_klines(log2)
    funding2 = HD.load_funding(log2)
    ds2 = F.build(px2, vol2, HD.load_metrics(log2), funding2, HD.load_basis(log2))
    y2 = ds2.y["4h"]
    entry2 = ds2.hour * 60 + 1
    hs = (HD.HOLDOUT_START_MS - HD.START_MS) // 60_000
    ho = np.flatnonzero((entry2 >= hs) & (y2 != 0) & (ds2.label_end_min["4h"] < HD.N_MIN))
    # causal features: the pre-holdout rows must equal phase 1 features exactly
    common = min(len(ds.hour), len(ds2.hour))
    feat_same = bool(np.array_equal(np.nan_to_num(ds.X[:common - 300], nan=-9e9), np.nan_to_num(ds2.X[:common - 300], nan=-9e9)))
    if not feat_same:
        raise RuntimeError("features of pre-HOLDOUT hours changed when HOLDOUT was appended (look-ahead)")
    ph = predict(fitted, ds2.X[ho])
    mk = R.make_market(px2, funding2)
    tab = outcomes(mk, entry2, ho)
    yo = y2[ho]
    boot = M.DayBlockBootstrap(ds2.day[ho])
    up_move = tab[1]["gross"]
    rep: dict = {"contract_sha256": contract_sha, "provenance_sha256": sha((out_dir / "provenance_v1.json").read_bytes()),
                 "holdout_hours": int(len(ho)), "features_causal_check": feat_same,
                 "first_entry_min": int(entry2[ho].min()), "last_label_end_min": int(ds2.label_end_min["4h"][ho].max()),
                 "n_min_total": int(HD.N_MIN), "phase2_files_read": len(log2.entries),
                 "phase2_read_log_sha256": sha(json.dumps(log2.entries, sort_keys=True).encode()), "models": {}}
    rep["phase2_latest_month_files"] = sorted({e["file"] for e in log2.entries if "metrics" not in e["file"]})[-4:]
    rep["phase2_latest_metrics_file"] = sorted(e["file"] for e in log2.entries if "metrics" in e["file"])[-1]
    pvals, tests = {}, {}
    for m in ("GB", "RF", "LR_price"):
        p = ph[m]
        d = M.classify(p)
        conf = np.maximum(p, 1 - p)
        correct = (d == yo).astype(float)
        g = side_take(tab, d, "gross")
        nt = side_take(tab, d, "net")
        absm = np.abs(up_move)
        r = M.report(yo, p)
        r["auc_ci95"] = boot.ci(M.auc, yo, p)["ci95"]
        trends = {}
        for name, v in (("T1_accuracy", correct), ("T2_magnitude", absm), ("T3_gross", g)):
            bt = boot.ci(spearman, conf, v)
            trends[name] = {"spearman": spearman(conf, v), "ci95": bt["ci95"], "p_one_sided": one_sided(bt["boot"])}
        r["trends"] = trends
        bk = {}
        means = {"accuracy": [], "abs_move": [], "gross": []}
        for a, b in BUCKETS:
            mb = (conf >= a) & (conf < b)
            lab = f"{a:.2f}-{min(b, 1.0):.2f}"
            if not mb.any():
                bk[lab] = {"n": 0, "status": "EMPTY"}
                continue
            yy = yo[mb]
            dd = d[mb]
            item = {"n": int(mb.sum()), "coverage": float(mb.mean()), "status": "OK" if mb.sum() >= MIN_BUCKET else "INSUFFICIENT",
                    "accuracy": float(correct[mb].mean()), "balanced_accuracy": M.balanced_accuracy(yy, dd),
                    "abs_move_mean": float(absm[mb].mean()), "gross_mean": float(g[mb].mean()), "gross_median": float(np.median(g[mb])),
                    "net_mean": float(nt[mb].mean()), "net_median": float(np.median(nt[mb])),
                    "mfe_mean": float(side_take(tab, d, "mfe")[mb].mean()), "mae_mean": float(side_take(tab, d, "mae")[mb].mean()),
                    "long_share": float((dd == 1).mean())}
            if mb.sum() >= 2:
                bb = M.DayBlockBootstrap(ds2.day[ho][mb]) if mb.sum() >= 30 else None
                item["net_ci95"] = bb.ci(lambda v: v.mean(), nt[mb])["ci95"] if bb else None
                item["gross_ci95"] = bb.ci(lambda v: v.mean(), g[mb])["ci95"] if bb else None
            bk[lab] = item
            if item["status"] == "OK":
                means["accuracy"].append(item["accuracy"]); means["abs_move"].append(item["abs_move_mean"]); means["gross"].append(item["gross_mean"])
        r["buckets"] = bk
        r["ordered_bucket_increasing_pairs"] = {k: f"{sum(b > a for a, b in zip(v, v[1:]))}/{max(0, len(v) - 1)}" for k, v in means.items()}
        thr = THR[m]
        dc = M.classify(p, thr)
        c = dc != 0
        gc, nc = side_take(tab, dc, "gross")[c], side_take(tab, dc, "net")[c]
        days_c = ds2.day[ho][c]
        bc = M.DayBlockBootstrap(days_c) if c.sum() >= 30 else None
        econ = {"threshold": thr, "n": int(c.sum()), "coverage": float(c.mean()),
                "accuracy": float((dc[c] == yo[c]).mean()) if c.any() else None}
        if c.any():
            win, lose = nc[nc > 0], nc[nc <= 0]
            econ.update({"gross_mean": float(gc.mean()), "gross_median": float(np.median(gc)), "net_mean": float(nc.mean()),
                         "net_median": float(np.median(nc)), "pf": R.pf(nc), "win_rate": float((nc > 0).mean()),
                         "avg_winner": float(win.mean()) if len(win) else None, "avg_loser": float(-lose.mean()) if len(lose) else None})
            if bc:
                bn = bc.ci(lambda v: v.mean(), nc)
                econ["net_ci95"], econ["net_p_one_sided"] = bn["ci95"], one_sided(bn["boot"])
                econ["gross_ci95"] = bc.ci(lambda v: v.mean(), gc)["ci95"]
            sides = {}
            for nm, sd in (("LONG", 1), ("SHORT", -1)):
                ms = dc[c] == sd
                yy, gg = yo[c][ms], gc[ms]
                right = yy == sd
                sides[nm] = {"n": int(ms.sum()), "accuracy": float(right.mean()) if ms.any() else None,
                             "correct_move_bp": float(np.abs(gg[right]).mean() * 1e4) if right.any() else None,
                             "wrong_move_bp": float(np.abs(gg[~right]).mean() * 1e4) if (~right).any() else None,
                             "gross_mean": float(gg.mean()) if ms.any() else None, "net_mean": float(nc[ms].mean()) if ms.any() else None,
                             "mfe_mean": float(side_take(tab, dc, "mfe")[c][ms].mean()) if ms.any() else None,
                             "mae_mean": float(side_take(tab, dc, "mae")[c][ms].mean()) if ms.any() else None}
            econ["sides"] = sides
            lmask = (dc[c] == 1).astype(float)

            def asym(gv, lm):
                l, s = lm == 1, lm == 0
                return gv[l].mean() - gv[s].mean() if l.any() and s.any() else np.nan
            if bc:
                ba = bc.ci(asym, gc, lmask)
                econ["asymmetry_long_minus_short_gross"] = {"diff": float(asym(gc, lmask)), "ci95": ba["ci95"],
                                                            "p_one_sided": one_sided(ba["boot"])}
        r["confident_econ"] = econ
        rep["models"][m] = r
        if m == "GB":
            for k in ("T1_accuracy", "T2_magnitude", "T3_gross"):
                pvals[k] = trends[k]["p_one_sided"]
            pvals["T4_asymmetry"] = econ["asymmetry_long_minus_short_gross"]["p_one_sided"]
            pvals["T5_net"] = econ["net_p_one_sided"]
    holm = R.holm(pvals)
    rep["holm"] = holm
    # paired GB vs LR
    gbp, lrp = ph["GB"], ph["LR_price"]
    rep["gb_minus_lr"] = {"auc_diff": M.auc(yo, gbp) - M.auc(yo, lrp),
                          "auc_diff_ci95": boot.ci(lambda a, x, z: M.auc(a, x) - M.auc(a, z), yo, gbp, lrp)["ci95"]}
    hn = {}
    for m in ("GB", "LR_price"):
        dc = M.classify(ph[m], 0.60)
        hn[m] = np.where(dc != 0, side_take(tab, dc, "net"), 0.0)
    dn = hn["GB"] - hn["LR_price"]
    rep["gb_minus_lr"]["net_per_hour_diff_bp"] = float(dn.mean() * 1e4)
    rep["gb_minus_lr"]["net_per_hour_diff_ci95_bp"] = [x * 1e4 for x in boot.ci(lambda v: v.mean(), dn)["ci95"]]
    # drift
    c30 = px2.close[ds2.hour[ho] * 60 - 1] / px2.close[ds2.hour[ho] * 60 - 1 - 30 * 1440] - 1
    first, last = int(entry2[ho].min()), int(ds2.label_end_min["4h"][ho].max())
    gbd = M.classify(ph["GB"], 0.60)
    rep["drift"] = {"btc_return_window": float(px2.open[last] / px2.open[first] - 1),
                    "share_hours_30d_up": float((c30 > 0).mean()), "long_base_rate": float((yo == 1).mean()),
                    "always_long_gross_mean": float(tab[1]["gross"].mean()),
                    "gb_conf_long_share": float((gbd[gbd != 0] == 1).mean()),
                    "gb_all_calls_long_share": float((M.classify(ph["GB"]) == 1).mean())}
    # verdict
    gb = rep["models"]["GB"]
    e = gb["confident_econ"]
    sig = {k: holm[k] <= 0.05 for k in holm}
    pred_ok = gb["auc"] > 0.52 and gb["auc_ci95"][0] > 0.50
    a = e["asymmetry_long_minus_short_gross"]
    if e["sides"]["LONG"]["n"] < 100 or e["sides"]["SHORT"]["n"] < 100:
        asym_label = "INSUFFICIENT"
    elif sig["T4_asymmetry"]:
        asym_label = "ASYMMETRY_PERSISTS"
    elif a["diff"] < 0 and a["ci95"][1] < 0:
        asym_label = "ASYMMETRY_REVERSES"
    else:
        asym_label = "ASYMMETRY_DISAPPEARS"
    hc = [gb["buckets"][k] for k in ("0.65-0.70", "0.70-1.00")]
    hc_ok = all(b.get("n", 0) >= 100 and b.get("net_mean", -1) > 0 and (b.get("net_ci95") or [-1])[0] > 0 for b in hc)
    if not pred_ok or not sig["T1_accuracy"]:
        verdict = "A. DIRECTION_EDGE_REJECTED"
    elif all(sig[k] for k in ("T1_accuracy", "T2_magnitude", "T3_gross", "T5_net")):
        verdict = "C. DIRECTION_HIGH_CONFIDENCE_EFFECT_CONFIRMED"
    else:
        verdict = "B. DIRECTION_EDGE_REAL_BUT_NOT_TRADABLE"
    rep["pred_ok"] = pred_ok
    rep["significant"] = sig
    rep["asymmetry_label"] = asym_label
    rep["high_confidence_label"] = "HIGH_CONFIDENCE_EFFECT_CONFIRMED" if hc_ok else "NOT_CONFIRMED"
    rep["tradability_label"] = "NOT_TRADABLE_AS_IS" if e["net_mean"] <= 0 else "NET_POSITIVE"
    rep["verdict"] = verdict
    rep["asymmetry_flag_D"] = asym_label == "ASYMMETRY_PERSISTS"
    rep["contract_sha256_after"] = sha(CONTRACT.read_bytes())
    (out_dir / "holdout_v1.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=_json))
    return rep


def _json(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and math.isinf(o):
        return "inf"
    raise TypeError(type(o))


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR)
    print(json.dumps({"verdict": r["verdict"], "D": r["asymmetry_flag_D"], "asym": r["asymmetry_label"],
                      "hc": r["high_confidence_label"], "trade": r["tradability_label"]}, indent=1))
