"""Direction R0 VALIDATION (contract CONTRACT_DIRECTION_R0_VALIDATION_V1.md). HOLDOUT is never read.

Run: cd backend && PYTHONPATH=. python3 -m app.crypto.research.direction_r0.validation [out.json]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1 import r1_train as R
from . import features as F
from . import metrics as M
from . import models as MO
from . import study as ST
from . import val_data as VD

CONTRACT = PD.ROOT / "docs/crypto/direction_classifier_r0/CONTRACT_DIRECTION_R0_VALIDATION_V1.md"
TRAIN_RESULT = PD.ROOT / "data/research/crypto/direction_r0/direction_r0_v1.json"
OUT = PD.ROOT / "data/research/crypto/direction_r0/validation_v1.json"
CANDIDATES = {"RF": 0.55, "GB": 0.60}
HN, HM = "4h", 240
EMBARGO_MIN = 1440


def econ(tab: dict, idx: np.ndarray, dirs: np.ndarray) -> dict:
    if len(idx) == 0:
        return {"n": 0}
    g = np.array([tab[d]["gross"][k] for k, d in zip(idx, dirs)])
    n = np.array([tab[d]["net"][k] for k, d in zip(idx, dirs)])
    mfe = np.array([tab[d]["mfe"][k] for k, d in zip(idx, dirs)])
    mae = np.array([tab[d]["mae"][k] for k, d in zip(idx, dirs)])
    return {"n": int(len(idx)), "gross_mean": float(g.mean()), "gross_median": float(np.median(g)),
            "net_mean": float(n.mean()), "net_median": float(np.median(n)), "net_pos_share": float((n > 0).mean()),
            "cost_mean": float((g - n).mean()), "pf": R.pf(n), "mfe_mean": float(mfe.mean()), "mae_mean": float(mae.mean())}


def run(out: Path | None = OUT) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    train_res = json.loads(TRAIN_RESULT.read_text())
    log = PD.ReadLog()
    vol, px = VD.load_klines(log)
    funding = VD.load_funding(log)
    basis = VD.load_basis(log)
    met = VD.load_metrics(log)
    ds = F.build(px, vol, met, funding, basis)
    y = ds.y[HN]
    entry = ds.hour * 60 + 1
    val_start = (VD.VAL_START_MS - VD.START_MS) // 60_000
    tr = np.flatnonzero((ds.label_end_min[HN] <= val_start - EMBARGO_MIN) & (y != 0))
    te = np.flatnonzero((entry >= val_start) & (y != 0))
    assert ds.label_end_min[HN][te].max() < VD.N_MIN
    yo = y[te]
    mk = R.make_market(px, funding)
    tab = {}
    for d in (1, -1):
        cols = {k: np.full(len(ds.hour), np.nan) for k in ("gross", "net", "mfe", "mae")}
        cols_s = np.full(len(ds.hour), np.nan)
        for k in te:
            t = R.simulate(mk, int(entry[k]), d, int(entry[k]) + HM, [])
            for f in cols:
                cols[f][k] = t[f]
            cols_s[k] = R.simulate(mk, int(entry[k]), d, int(entry[k]) + HM, [], R.STRESS_FEE_SIDE, R.STRESS_RT)["net"]
        cols["net_stress"] = cols_s
        tab[d] = cols
    boot = M.DayBlockBootstrap(ds.day[te])
    all_cols = np.arange(len(F.FEATURES))
    rep: dict = {"contract_sha256": sha, "train_contract_sha256": train_res["contract_sha256"],
                 "train_n": int(len(tr)), "validation_n": int(len(te)),
                 "validation_first_entry_utc_min": int(entry[te].min()), "validation_last_label_end_min": int(ds.label_end_min[HN][te].max()),
                 "metrics_quality": met.quality, "long_base_rate": float((yo == 1).mean()), "baselines": {}, "models": {}}
    rep["baselines"]["random_50_50"] = {"auc": 0.5, "balanced_accuracy": 0.5}
    for name, c in (("always_long", 1), ("always_short", -1)):
        rep["baselines"][name] = {"accuracy": float((yo == c).mean()), "balanced_accuracy": 0.5,
                                  "econ": econ(tab, te, np.full(len(te), c))}
    base_p = {}
    for fam in ("A_price", "C_flow", "D_oi"):
        _, p = MO.fit_predict("LR", ds.X[tr][:, ST.cols([fam])], y[tr], ds.X[te][:, ST.cols([fam])])
        base_p[fam] = p
        r = M.report(yo, p)
        rep["baselines"][f"{fam}_only_LR"] = {"auc": r["auc"], "balanced_accuracy": r["balanced_accuracy"],
                                              "accuracy": r["accuracy"], "econ_all_calls": econ(tab, te, M.classify(p))}
    reduced = np.array([c for c in all_cols if c not in set(ST.cols(["D_oi", "E_positioning"]))])
    trend = np.where(px.close[ds.hour * 60 - 1] / px.close[np.maximum(ds.hour * 60 - 1 - 30 * 1440, 0)] - 1 > 0, 1, -1)[te]
    rv = ds.X[te, F.FEATURES.index("rv_24h")]
    rv_hi = rv > np.nanmedian(rv)
    for model, thr in CANDIDATES.items():
        mdl, p = MO.fit_predict(model, ds.X[tr], y[tr], ds.X[te], n_jobs=1)
        r = M.report(yo, p)
        r["threshold"] = thr
        r["auc_ci95"] = boot.ci(M.auc, yo, p)["ci95"]
        r["balanced_accuracy_ci95"] = boot.ci(lambda a, b: M.balanced_accuracy(a, M.classify(b)), yo, p)["ci95"]
        d_all = M.classify(p)
        d_conf = M.classify(p, thr)
        conf = d_conf != 0
        r["confident"] = r["thresholds"][str(thr)]
        r["confident"]["accuracy_ci95"] = boot.ci(
            lambda a, b: (M.classify(b, thr)[M.classify(b, thr) != 0] == a[M.classify(b, thr) != 0]).mean()
            if (M.classify(b, thr) != 0).any() else np.nan, yo, p)["ci95"]
        r["econ_all_calls"] = econ(tab, te, d_all)
        r["econ_confident"] = econ(tab, te[conf], d_conf[conf])
        r["econ_confident"]["stress_net_mean"] = float(np.mean([tab[d]["net_stress"][k] for k, d in zip(te[conf], d_conf[conf])])) if conf.any() else None
        gross_conf = np.where(conf, np.array([tab[d]["gross"][k] if d != 0 else 0.0 for k, d in zip(te, d_conf)]), np.nan)
        net_conf = np.where(conf, np.array([tab[d]["net"][k] if d != 0 else 0.0 for k, d in zip(te, d_conf)]), np.nan)
        r["econ_confident"]["gross_mean_ci95"] = boot.ci(lambda v: np.nanmean(v) if (~np.isnan(v)).any() else np.nan, gross_conf)["ci95"]
        r["econ_confident"]["net_mean_ci95"] = boot.ci(lambda v: np.nanmean(v) if (~np.isnan(v)).any() else np.nan, net_conf)["ci95"]
        r["econ_confident"]["by_direction"] = {nm: econ(tab, te[d_conf == dd], d_conf[d_conf == dd]) for nm, dd in (("LONG", 1), ("SHORT", -1))}
        pa = base_p["A_price"]
        r["vs_price_only"] = {
            "auc_diff": r["auc"] - M.auc(yo, pa),
            "auc_diff_ci95": boot.ci(lambda a, x, z: M.auc(a, x) - M.auc(a, z), yo, p, pa)["ci95"],
            "balanced_accuracy_diff": r["balanced_accuracy"] - M.balanced_accuracy(yo, M.classify(pa)),
            "gross_all_calls_diff": r["econ_all_calls"]["gross_mean"] - rep["baselines"]["A_price_only_LR"]["econ_all_calls"]["gross_mean"]}
        _, pr = MO.fit_predict(model, ds.X[tr][:, reduced], y[tr], ds.X[te][:, reduced], n_jobs=1)
        r["incremental_oi_positioning"] = {"auc_reduced": M.auc(yo, pr), "auc_full_minus_reduced": r["auc"] - M.auc(yo, pr),
                                           "ci95": boot.ci(lambda a, x, z: M.auc(a, x) - M.auc(a, z), yo, p, pr)["ci95"]}
        reg = {}
        for nm, mask in (("broad_up_30d", trend == 1), ("broad_down_30d", trend == -1), ("vol_high", rv_hi), ("vol_low", ~rv_hi)):
            cm = mask & conf
            reg[nm] = {"n": int(mask.sum()), "accuracy": float((d_all[mask] == yo[mask]).mean()), "auc": M.auc(yo[mask], p[mask]),
                       "confident_n": int(cm.sum()),
                       "confident_accuracy": float((d_conf[cm] == yo[cm]).mean()) if cm.any() else None,
                       "confident_gross_mean": econ(tab, te[cm], d_conf[cm]).get("gross_mean")}
        r["regime_posthoc"] = reg
        tm = train_res["horizons"][HN]["models"][model]
        r["train_vs_validation"] = {"train_auc": tm["auc"], "train_balanced_accuracy": tm["balanced_accuracy"],
                                    "train_confident_accuracy": tm["thresholds"][str(thr)]["accuracy"],
                                    "train_confident_coverage": tm["thresholds"][str(thr)]["coverage"]}
        c = r["confident"]
        ec = r["econ_confident"]
        gate = {"V1_auc>0.53_ci>0.5": r["auc"] > 0.53 and r["auc_ci95"][0] > 0.5,
                "V2_bal_acc>0.52": r["balanced_accuracy"] > 0.52,
                "V3_conf_acc>0.55": (c["accuracy"] or 0) > 0.55,
                "V4_coverage>=10pct": c["coverage"] >= 0.10,
                "V5_conf_net_mean>0": ec.get("net_mean", -1) > 0,
                "V6_conf_long_and_short_acc>0.5": (c["long_accuracy"] or 0) > 0.5 and (c["short_accuracy"] or 0) > 0.5,
                "V7_auc_minus_price_only_ci>0": r["vs_price_only"]["auc_diff"] > 0 and r["vs_price_only"]["auc_diff_ci95"][0] > 0}
        fail = r["auc"] <= 0.51 or (c["accuracy"] or 0) <= 0.52 or ec.get("gross_mean", -1) <= 0
        r["gate"] = gate
        r["status"] = "PASS" if all(gate.values()) else "FAIL" if fail else "BORDERLINE"
        rep["models"][model] = r
    st = [m["status"] for m in rep["models"].values()]
    rep["holdout_candidates"] = [k for k, m in rep["models"].items() if m["status"] == "PASS"][:2]
    rep["verdict"] = ("C. DIRECTION_VALIDATION_PASS" if "PASS" in st else
                      "B. DIRECTION_VALIDATION_BORDERLINE" if "BORDERLINE" in st else "A. DIRECTION_VALIDATION_FAIL")
    rep["read_log_files"] = len(log.entries)
    rep["read_log_last_months"] = sorted({e["file"].split("-")[-1] for e in log.entries if "metrics" not in e["file"]})[-3:]
    rep["read_log_sha256"] = hashlib.sha256(json.dumps(log.entries, sort_keys=True).encode()).hexdigest()
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=float))
    return rep


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({"verdict": r["verdict"], "status": {k: v["status"] for k, v in r["models"].items()},
                      "holdout_candidates": r["holdout_candidates"]}, indent=1))
