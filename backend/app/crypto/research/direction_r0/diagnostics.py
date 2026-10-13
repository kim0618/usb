"""POST-HOC diagnostics for Direction R0 (not part of the frozen gate; the verdict does not change).

1. Shifted-label placebo: features at H, label = 4 h return starting 24 h later. A pipeline leak shows as AUC > 0.5.
2. Economic size: mean signed gross return of the predicted direction (no costs), all calls and confident calls.
3. Direction of the price features: LR coefficient signs of ret_1h / ret_4h (momentum + vs reversal -).
Run: cd backend && PYTHONPATH=. python3 -m app.crypto.research.direction_r0.diagnostics
"""
from __future__ import annotations

import json

import numpy as np

from ..ptm_r1 import data as PD
from ..positioning_r0 import data as MD
from . import features as F
from . import folds as FD
from . import metrics as M
from . import study as ST

OUT = PD.ROOT / "data/research/crypto/direction_r0/diagnostics_posthoc_v1.json"


def main():
    log = PD.ReadLog()
    vol, _ = PD.load_train(log)
    px = PD.load_train_prices(log)
    ds = F.build(px, vol, MD.load_train(log), PD.load_train_funding(log), PD.load_train_basis(log))
    entry = ds.hour * 60 + 1
    all_cols = np.arange(len(F.FEATURES))
    rep = {"note": "post-hoc diagnostics, outside the frozen contract; verdict unchanged"}
    # signed forward gross returns at the real entry
    fwd = {}
    for hn, hm in F.HORIZONS.items():
        r = np.full(len(entry), np.nan)
        ok = entry + hm < len(px.open)
        r[ok] = px.open[entry[ok] + hm] / px.open[entry[ok]] - 1
        fwd[hn] = r
    for model in ("RF", "GB"):
        res = {}
        # 1. placebo: label shifted +24 h (24 rows ahead on the hourly grid)
        y = ds.y["4h"]
        ysh = np.zeros_like(y)
        ysh[:-24] = y[24:]
        lend = ds.label_end_min["4h"] + 24 * 60
        fl = FD.folds(entry, lend)
        p, _ = ST.oos(model, ds, ysh, fl, all_cols)
        mask = ~np.isnan(p) & (ysh != 0)
        res["placebo_shift24h_auc"] = M.auc(ysh[mask], p[mask])
        res["placebo_shift24h_balanced_accuracy"] = M.balanced_accuracy(ysh[mask], M.classify(p[mask]))
        # 2. economic size at the real labels
        for hn in F.HORIZONS:
            y = ds.y[hn]
            fl = FD.folds(entry, ds.label_end_min[hn])
            p, _ = ST.oos(model, ds, y, fl, all_cols)
            mask = ~np.isnan(p) & (y != 0)
            sig = {}
            for t in (0.5,) + M.THRESHOLDS:
                d = M.classify(p[mask], t)
                m = d != 0
                g = d[m] * fwd[hn][mask][m]
                sig[str(t)] = {"n": int(m.sum()), "mean_signed_gross_bp": float(g.mean() * 1e4) if m.any() else None,
                               "median_signed_gross_bp": float(np.median(g) * 1e4) if m.any() else None,
                               "mean_abs_move_bp": float(np.abs(fwd[hn][mask][m]).mean() * 1e4) if m.any() else None}
            res[f"signed_gross_{hn}"] = sig
        rep[model] = res
    # 3. LR on price only, full TRAIN fit, coefficient signs (standardized)
    from .models import make
    for hn in F.HORIZONS:
        y = ds.y[hn]
        m = y != 0
        c = ST.cols(["A_price"])
        lr = make("LR").fit(ds.X[m][:, c], (y[m] == 1).astype(int))
        rep[f"price_lr_coef_{hn}"] = dict(zip([F.FEATURES[i] for i in c], lr[-1].coef_[0][:len(c)].tolist()))
    OUT.write_text(json.dumps(rep, indent=1, sort_keys=True, default=float))
    print(json.dumps(rep, indent=1, default=float))


if __name__ == "__main__":
    main()
