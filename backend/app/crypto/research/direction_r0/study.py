"""Direction Classifier R0 TRAIN study (contract sections 4 to 8). Run with system python3:
    cd backend && PYTHONPATH=. python3 -m app.crypto.research.direction_r0.study [out.json]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1 import r1_train as R
from ..positioning_r0 import data as MD
from . import features as F
from . import folds as FD
from . import metrics as M
from . import models as MO

CONTRACT = PD.ROOT / "docs/crypto/direction_classifier_r0/CONTRACT_DIRECTION_R0_V1.md"
OUT = PD.ROOT / "data/research/crypto/direction_r0/direction_r0_v1.json"
PERM_REPEATS = 5
BASELINE_FAMILIES = ("A_price", "C_flow", "D_oi")


def cols(families) -> np.ndarray:
    names = [f for fam in families for f in F.FAMILIES[fam]]
    return np.array([F.FEATURES.index(n) for n in names])


def oos(model: str, ds: F.Dataset, y: np.ndarray, fl, use: np.ndarray, keep_models=False):
    p = np.full(len(y), np.nan)
    fitted = []
    for tr, te in fl:
        tr = tr[y[tr] != 0]
        te = te[y[te] != 0]
        if len(te) == 0:
            continue
        mdl, pp = MO.fit_predict(model, ds.X[tr][:, use], y[tr], ds.X[te][:, use])
        p[te] = pp
        if keep_models:
            fitted.append((mdl, te))
    return p, fitted


def perm_importance(fitted, ds, y, use, base_auc) -> dict:
    rng = np.random.default_rng(MO.SEED)
    out = {}
    for fam in F.FAMILIES:
        fam_cols = [list(use).index(c) for c in cols([fam]) if c in use]
        if not fam_cols:
            continue
        drops = []
        for _ in range(PERM_REPEATS):
            p = np.full(len(y), np.nan)
            for mdl, te in fitted:
                X = ds.X[te][:, use].copy()
                perm = rng.permutation(len(te))
                X[:, fam_cols] = X[perm][:, fam_cols]
                p[te] = mdl.predict_proba(X)[:, 1]
            ok = ~np.isnan(p)
            drops.append(base_auc - M.auc(y[ok], p[ok]))
        out[fam] = {"auc_drop_mean": float(np.mean(drops)), "auc_drop_sd": float(np.std(drops))}
    return out


def run(out: Path | None = OUT) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    log = PD.ReadLog()
    vol, cov = PD.load_train(log)
    px = PD.load_train_prices(log)
    funding = PD.load_train_funding(log)
    basis = PD.load_train_basis(log)
    met = MD.load_train(log)
    ds = F.build(px, vol, met, funding, basis)
    entry = ds.hour * 60 + 1
    all_cols = np.arange(len(F.FEATURES))
    rep: dict = {"contract_sha256": sha, "n_hours": int(len(ds.hour)), "features": list(F.FEATURES),
                 "feature_missing_share": {f: float(np.isnan(ds.X[:, i]).mean()) for i, f in enumerate(F.FEATURES)},
                 "folds": [], "horizons": {}}
    pvals = {}
    for hn in F.HORIZONS:
        y = ds.y[hn]
        fl = FD.folds(entry, ds.label_end_min[hn])
        if hn == "4h":
            rep["folds"] = [{"train_n": int(len(tr)), "test_n": int(len(te))} for tr, te in fl]
        test_mask = np.zeros(len(y), dtype=bool)
        for _, te in fl:
            test_mask[te] = True
        test_mask &= y != 0
        yo = y[test_mask]
        boot = M.DayBlockBootstrap(ds.day[test_mask])
        hres: dict = {"oos_n": int(test_mask.sum()), "long_base_rate": float((yo == 1).mean()),
                      "baselines": {}, "family_only_lr": {}, "models": {}}
        hres["baselines"]["random_50_50"] = {"balanced_accuracy": 0.5, "auc": 0.5}
        for name, const in (("always_long", 1), ("always_short", -1)):
            pred = np.full(len(yo), const)
            hres["baselines"][name] = {"accuracy": float((pred == yo).mean()), "balanced_accuracy": 0.5}
        fam_p = {}
        for fam in F.FAMILIES:
            p, _ = oos("LR", ds, y, fl, cols([fam]))
            fam_p[fam] = p[test_mask]
            r = M.report(yo, fam_p[fam])
            hres["family_only_lr"][fam] = {k: r[k] for k in ("auc", "balanced_accuracy", "accuracy", "brier")}
        for b in BASELINE_FAMILIES:
            hres["baselines"][f"{b}_only_LR"] = hres["family_only_lr"][b]
        for model in MO.MODELS:
            p, fitted = oos(model, ds, y, fl, all_cols, keep_models=True)
            po = p[test_mask]
            r = M.report(yo, po)
            ba = boot.ci(lambda a, b: M.balanced_accuracy(a, M.classify(b)), yo, po)
            r["balanced_accuracy_ci95"] = ba["ci95"]
            r["balanced_accuracy_p_gt_0.5"] = M.one_sided_p_above(ba["boot"], 0.5)
            au = boot.ci(M.auc, yo, po)
            r["auc_ci95"] = au["ci95"]
            for t in M.THRESHOLDS:
                def acc_t(a, b, t=t):
                    pr = M.classify(b, t)
                    m_ = pr != 0
                    return (pr[m_] == a[m_]).mean() if m_.any() else np.nan
                r["thresholds"][str(t)]["accuracy_ci95"] = boot.ci(acc_t, yo, po)["ci95"]
            for b in ("A_price", "C_flow"):
                d = boot.ci(lambda a, x, z: M.auc(a, x) - M.auc(a, z), yo, po, fam_p[b])
                r[f"auc_minus_{b}_only"] = {"diff": r["auc"] - M.auc(yo, fam_p[b]), "ci95": d["ci95"]}
            red = np.array([c for c in all_cols if c not in set(cols(["D_oi", "E_positioning"]))])
            pr_, _ = oos(model, ds, y, fl, red)
            pr_ = pr_[test_mask]
            d = boot.ci(lambda a, x, z: M.auc(a, x) - M.auc(a, z), yo, po, pr_)
            r["incremental_oi_positioning"] = {"auc_reduced": M.auc(yo, pr_), "auc_full_minus_reduced": r["auc"] - M.auc(yo, pr_),
                                               "ci95": d["ci95"]}
            r["permutation_importance_by_family"] = perm_importance(fitted, ds, y, all_cols, M.auc(y[test_mask & ~np.isnan(p)], p[test_mask & ~np.isnan(p)]))
            if model == "LR":
                r["lr_coefficients_last_fold"] = dict(zip(F.FEATURES, MO.lr_coefficients(fitted[-1][0], len(F.FEATURES))))
            r["by_fold"] = []
            for mdl, te in fitted:
                r["by_fold"].append({"n": int(len(te)), "auc": M.auc(y[te], p[te]),
                                     "balanced_accuracy": M.balanced_accuracy(y[te], M.classify(p[te]))})
            hres["models"][model] = r
            pvals[f"{model}:{hn}"] = r["balanced_accuracy_p_gt_0.5"]
        rep["horizons"][hn] = hres
    holm = R.holm(pvals)
    rep["holm_balanced_accuracy"] = holm
    gate, weak = {}, {}
    for hn in F.HORIZONS:
        for model in MO.MODELS:
            r = rep["horizons"][hn]["models"][model]
            lb = r["long_base_rate"]
            th_ok = any(v["coverage"] >= 0.10 and (v["accuracy"] or 0) > 0.55 and v["accuracy_ci95"][0] > 0.50
                        for v in r["thresholds"].values())
            g = {"bal_acc>0.52_ci>0.5_holm<=0.05": r["balanced_accuracy"] > 0.52 and r["balanced_accuracy_ci95"][0] > 0.50
                 and holm[f"{model}:{hn}"] <= 0.05,
                 "confident_acc>0.55_cov>=10pct_ci>0.5": th_ok,
                 "long_and_short_precision_above_base": (r["long_precision"] or 0) > lb and (r["short_precision"] or 0) > 1 - lb,
                 "auc_above_price_and_flow_only_ci>0": r["auc_minus_A_price_only"]["ci95"][0] > 0
                 and r["auc_minus_C_flow_only"]["ci95"][0] > 0}
            gate[f"{model}:{hn}"] = g
            weak[f"{model}:{hn}"] = r["balanced_accuracy_ci95"][0] > 0.50
    passing = sorted([k for k, g in gate.items() if all(g.values())], key=lambda k: holm[k])[:2]
    rep["gate"] = gate
    rep["weak_rule_ci_lower_gt_0.5"] = weak
    rep["validation_candidates"] = passing
    rep["verdict"] = ("C. DIRECTION_TRAIN_CANDIDATE" if passing else
                      "B. DIRECTION_WEAK" if any(weak.values()) else "A. DIRECTION_NO_EDGE")
    rep["read_log_files"] = len(log.entries)
    rep["read_log_sha256"] = hashlib.sha256(json.dumps(log.entries, sort_keys=True).encode()).hexdigest()
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=float))
    return rep


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({"verdict": r["verdict"], "candidates": r["validation_candidates"]}, indent=1))
