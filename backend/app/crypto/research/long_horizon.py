"""D5.1 long-horizon study: 24 single-feature cells x bucket x side x 1h/2h/4h(/8h), expanding walk-forward.

Contract: docs/crypto/CRYPTO_D5_1_LONG_HORIZON_CONTRACT_V1.md. Refuses to run if its sha256 differs from the
freeze record. D5 modules are imported unchanged; the six new features live here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.crypto.paper.fees import load_scenarios

from . import dataset, features as F
from .study import FEE_VERIFICATION, micro_costs

CONTRACT = Path("docs/crypto/CRYPTO_D5_1_LONG_HORIZON_CONTRACT_V1.md")
FREEZE = Path("data/runtime/crypto/d5_1/contract_freeze_v1.json")
OUT = Path("data/runtime/crypto/d5_1/cells_v1.json")

MIN = dataset.MINUTE_MS
DAY_MS = 86_400_000
SAMPLE_START = "2021-03-04"
FOLD_STARTS = ("2022-01-01", "2022-07-01", "2023-01-01", "2023-07-01", "2024-01-01",
               "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01")
END = "2026-09-23"
HORIZONS = (60, 120, 240, 480)
PRIMARY = (60, 120, 240)
SIDES = ("LONG", "SHORT")
SCENARIOS = ("ZERO", "VIP0_FEE", "VIP0_BASE", "VIP0_STRESS", "HYPOTHETICAL_MAKER_COST")
NB = 5
BOOT_B, BLOCK, SEED = 1000, 7, 20260925
NEW_FEATURES = (("G1", "basis_mark_index"), ("G2", "basis_mark_index_mean60"), ("M1", "mtf5_trend_1h"),
                ("M2", "mtf15_trend_4h"), ("M3", "mtf30_trend_24h"), ("M4", "mtf1h_vol_24h"))
ALL_FEATURES = F.FEATURES + NEW_FEATURES
AXES = {"trend": ("BULL", "BEAR", "SIDEWAYS"), "vol": ("HIGH", "MID", "LOW"),
        "session": ("00-06", "06-12", "12-18", "18-24"),
        "year": ("2021", "2022", "2023", "2024", "2025", "2026"),
        "hour": tuple(f"{x:02d}" for x in range(24))}


def ms(day: str) -> int:
    return int(np.datetime64(day + "T00:00:00", "ms").astype(np.int64))


def check_freeze() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"D5.1 contract changed after freeze: {now} != {frozen}")
    return now


def last_closed_index(ts: np.ndarray, k: int) -> np.ndarray:
    """Row index of the 1m bar that closes the last fully closed k-minute bar at the close of each row."""
    close_ms = ts + MIN
    boundary = (close_ms // (k * MIN)) * (k * MIN)
    return (boundary - MIN - ts[0]) // MIN


def new_features(g: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    ts, C = g["ts"], g["close"]
    lnC = np.log(C)
    n = len(C)

    def at(idx):
        out = np.full(n, np.nan)
        ok = idx >= 0
        out[ok] = lnC[idx[ok]]
        return out

    basis = np.log(g["mark_close"] / g["index_close"])
    c = np.concatenate([[0.0], np.cumsum(basis)])
    mean60 = np.full(n, np.nan)
    mean60[59:] = (c[60:] - c[:-60]) / 60
    j5, j15, j30, j60 = (last_closed_index(ts, k) for k in (5, 15, 30, 60))
    # 1h grid returns, then trailing std of 24 of them, looked up at the last closed hour
    hour_rows = np.arange(59, n, 60)  # rows closing each hour (grid starts at 00:00Z)
    hr = np.diff(lnC[hour_rows], prepend=np.nan)
    hv = np.full(len(hr), np.nan)
    if len(hr) >= 24:
        from numpy.lib.stride_tricks import sliding_window_view
        hv[23:] = np.std(sliding_window_view(hr, 24), axis=1)
    hpos = (j60 - 59) // 60
    vol = np.full(n, np.nan)
    ok = (j60 >= 59)
    vol[ok] = hv[hpos[ok]]
    f = {
        "basis_mark_index": basis,
        "basis_mark_index_mean60": mean60,
        "mtf5_trend_1h": at(j5) - at(j5 - 12 * 5),
        "mtf15_trend_4h": at(j15) - at(j15 - 16 * 15),
        "mtf30_trend_24h": at(j30) - at(j30 - 48 * 30),
        "mtf1h_vol_24h": vol,
    }
    for v in f.values():
        v[~np.isfinite(v)] = np.nan
    return f


def _weights(n_days: int, rng) -> np.ndarray:
    k = int(np.ceil(n_days / BLOCK))
    starts = rng.integers(0, max(n_days - BLOCK + 1, 1), size=(BOOT_B, k))
    idx = (starts[:, :, None] + np.arange(BLOCK)).reshape(BOOT_B, -1)[:, :n_days]
    W = np.zeros((BOOT_B, n_days))
    np.add.at(W, (np.repeat(np.arange(BOOT_B), idx.shape[1]), idx.ravel()), 1)
    return W


def run() -> dict:
    sha = check_freeze()
    fees = load_scenarios(FEE_VERIFICATION)
    taker, maker = float(fees["VIP_0"].taker_rate), float(fees["VIP_0"].maker_rate)
    micro = micro_costs()
    g = dataset.load()
    ts = g["ts"]
    n = len(ts)
    n_days = n // F.DAY_BARS
    day = (ts - ts[0]) // DAY_MS
    bounds = np.array([ms(SAMPLE_START)] + [ms(d) for d in FOLD_STARTS] + [ms(END)])
    period = np.searchsorted(bounds, ts, side="right") - 1  # 0 = train-only prefix, 1..9 = folds, 10 = after END
    next_bound = bounds[np.minimum(period + 1, len(bounds) - 1)]
    day_period = period[:: F.DAY_BARS][:n_days]

    feats = F.compute_features(g)
    feats.update(new_features(g))
    buckets = {name: F.bucketize(feats[name], n_days) for _, name in ALL_FEATURES}
    del feats
    f1_train = (ts >= bounds[0]) & (ts < bounds[1])
    reg = F.regime_labels(g, f1_train)

    rng = np.random.default_rng(SEED)
    fold_days = {k: np.where(day_period == k)[0] for k in range(1, 10)}
    oos_days = np.where((day_period >= 1) & (day_period <= 9))[0]
    W_fold = {k: _weights(len(d), rng) for k, d in fold_days.items()}
    W_oos = _weights(len(oos_days), rng)

    cells: dict[str, dict] = {}
    for h in HORIZONS:
        T = F.targets(g, h)
        exit_ts = np.full(n, np.iinfo(np.int64).max)
        exit_ts[: n - 1 - h] = ts[1 + h:]
        base = (period >= 0) & (period <= 9) & (exit_ts < next_bound) & np.isfinite(T["ratio"])
        vals = {}
        for side in SIDES:
            gross = T["long"] if side == "LONG" else T["short"]
            fund = T["fund_long"] if side == "LONG" else -T["fund_long"]
            tk = taker * (1 + T["ratio"])
            vals[(side, "ZERO")] = gross
            vals[(side, "VIP0_FEE")] = gross - tk - fund
            vals[(side, "VIP0_BASE")] = gross - tk - micro["BASE"] - fund
            vals[(side, "VIP0_STRESS")] = gross - tk - micro["STRESS"] - fund
            vals[(side, "HYPOTHETICAL_MAKER_COST")] = gross - maker * (1 + T["ratio"]) - fund
        for fcode, fname in ALL_FEATURES:
            b = buckets[fname]
            sel = base & (b >= 0)
            key = day[sel] * NB + b[sel]
            size = n_days * NB
            cnt = np.bincount(key, minlength=size).reshape(n_days, NB).astype(float)
            oos_sel = sel & (period >= 1)
            bo = b[oos_sel]
            for side in SIDES:
                sd = side.lower()
                S = {sc: np.bincount(key, weights=vals[(side, sc)][sel], minlength=size).reshape(n_days, NB) for sc in SCENARIOS}
                WIN = {sc: np.bincount(key, weights=(vals[(side, sc)][sel] > 0).astype(float), minlength=size).reshape(n_days, NB) for sc in ("ZERO", "VIP0_BASE")}
                MAE = np.bincount(key, weights=T[f"mae_{sd}"][sel], minlength=size).reshape(n_days, NB)
                MFE = np.bincount(key, weights=T[f"mfe_{sd}"][sel], minlength=size).reshape(n_days, NB)
                med = {}
                for sc in ("ZERO", "VIP0_BASE"):
                    vo = vals[(side, sc)][oos_sel]
                    med[sc] = [float(np.median(vo[bo == k])) if (bo == k).any() else None for k in range(NB)]
                # regimes on OOS samples
                regs = {}
                for axis, labels in AXES.items():
                    lab = reg[axis]
                    ok = oos_sel & (lab >= 0)
                    L = len(labels)
                    rk = b[ok].astype(np.int64) * L + lab[ok]
                    rc = np.bincount(rk, minlength=NB * L).reshape(NB, L)
                    rn = np.bincount(rk, weights=vals[(side, "VIP0_BASE")][ok], minlength=NB * L).reshape(NB, L)
                    rg = np.bincount(rk, weights=vals[(side, "ZERO")][ok], minlength=NB * L).reshape(NB, L)
                    regs[axis] = (labels, rc, rn, rg)
                for k in range(NB):
                    cid = f"{fcode}-B{k+1}-{h//60}h-{side}"
                    cells[cid] = summarize(cid, fcode, fname, k, h, side, S, WIN, MAE, MFE, cnt, med, regs,
                                           fold_days, oos_days, W_fold, W_oos, day_period)
        del T, vals
    for c in cells.values():
        c["verdict"], c["reasons"] = verdict(c)
    gate = decision_gate(cells)
    out = {"contract_sha256": sha, "taker": taker, "maker": maker, "micro": micro,
           "vol_cutoffs_f1_train": reg["vol_cutoffs"].tolist(), "gate": gate, "cells": cells}
    OUT.write_text(json.dumps(out))
    return out


def _stat(S, cnt, days, k, W=None):
    n_ = cnt[days, k].sum()
    res = {"N": int(n_), "days": int((cnt[days, k] > 0).sum())}
    for sc, arr in S.items():
        s = arr[days, k]
        m = float(s.sum() / n_) if n_ else None
        d = {"mean": m}
        if W is not None and n_:
            with np.errstate(invalid="ignore", divide="ignore"):
                boot = (W @ s) / (W @ cnt[days, k])
            boot = boot[np.isfinite(boot)]
            d["se"] = float(boot.std())
            d["ci95"] = [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))]
        res[sc] = d
    return res


def summarize(cid, fcode, fname, k, h, side, S, WIN, MAE, MFE, cnt, med, regs, fold_days, oos_days, W_fold, W_oos, day_period):
    folds = {}
    for f, days in fold_days.items():
        st = _stat(S, cnt, days, k, W_fold[f])
        train_days = np.where((day_period >= 0) & (day_period < f))[0]
        tn = cnt[train_days, k].sum()
        st["train_VIP0_BASE_mean"] = float(S["VIP0_BASE"][train_days, k].sum() / tn) if tn else None
        st["train_N"] = int(tn)
        st["selected"] = bool(st["train_VIP0_BASE_mean"] is not None and st["train_VIP0_BASE_mean"] > 0)
        folds[f"F{f}"] = st
    oos = _stat(S, cnt, oos_days, k, W_oos)
    n_ = oos["N"]
    for sc in ("ZERO", "VIP0_BASE"):
        oos[sc]["median"] = med[sc][k]
        oos[sc]["win_rate"] = float(WIN[sc][oos_days, k].sum() / n_) if n_ else None
    oos["mae_mean"] = float(MAE[oos_days, k].sum() / n_) if n_ else None
    oos["mfe_mean"] = float(MFE[oos_days, k].sum() / n_) if n_ else None
    oos["N_eff"] = n_ / h
    gross = oos["ZERO"]["mean"]
    ratios = {}
    for sc in ("VIP0_FEE", "VIP0_BASE", "VIP0_STRESS", "HYPOTHETICAL_MAKER_COST"):
        net = oos[sc]["mean"]
        cost = None if gross is None or net is None else gross - net
        ratios[sc] = {"cost": cost, "ratio": None if cost is None else ("INF_COST_NONPOSITIVE" if cost <= 0 else gross / cost)}
    sel = [f for f in fold_days if folds[f"F{f}"]["selected"]]
    wn = sum(cnt[fold_days[f], k].sum() for f in sel)
    wf = {"selected_folds": [f"F{f}" for f in sel], "N": int(wn),
          "VIP0_BASE_mean": float(sum(S["VIP0_BASE"][fold_days[f], k].sum() for f in sel) / wn) if wn else None,
          "ZERO_mean": float(sum(S["ZERO"][fold_days[f], k].sum() for f in sel) / wn) if wn else None}
    regimes = {}
    for axis, (labels, rc, rn, rg) in regs.items():
        regimes[axis] = {labels[j]: {"N": int(rc[k, j]), "net_sum": float(rn[k, j]),
                                     "net": float(rn[k, j] / rc[k, j]) if rc[k, j] else None,
                                     "gross": float(rg[k, j] / rc[k, j]) if rc[k, j] else None}
                         for j in range(len(labels))}
    return {"feature": fcode, "feature_name": fname, "bucket": f"B{k+1}", "horizon_min": h, "side": side,
            "folds": folds, "oos": oos, "cost_ratio": ratios, "wf_selected": wf, "regimes": regimes}


def _robust(c):
    reasons = []
    rule = {"trend": lambda p, m: p >= 2, "vol": lambda p, m: p >= 2, "session": lambda p, m: p >= 3,
            "year": lambda p, m: m > 0 and p * 3 >= 2 * m}
    ok = True
    for axis, fn in rule.items():
        v = [x["net"] for x in c["regimes"][axis].values() if x["N"] >= 200]
        p = sum(x > 0 for x in v)
        if not fn(p, len(v)):
            ok = False
            reasons.append(f"R_{axis.upper()}_{p}/{len(v)}")
    for axis, tag in (("year", "R5_LOYO"), ("trend", "R6_LOTO")):
        items = [x for x in c["regimes"][axis].values() if x["N"] >= 200]
        if not items:
            ok = False
            reasons.append(tag)
            continue
        top = max(items, key=lambda x: x["net_sum"])
        rest_n = sum(x["N"] for x in items) - top["N"]
        rest_s = sum(x["net_sum"] for x in items) - top["net_sum"]
        if not (rest_n > 0 and rest_s / rest_n > 0):
            ok = False
            reasons.append(tag)
    return ok, reasons


def verdict(c):
    folds = [f for f in c["folds"].values() if f["N"] >= 500 and f["days"] >= 20]
    nf = len(folds)
    o = c["oos"]
    g_ok = sum(f["ZERO"]["mean"] > 0 for f in folds)
    n_ok = sum(f["VIP0_BASE"]["mean"] > 0 for f in folds)
    s1 = nf > 0 and g_ok * 3 >= 2 * nf
    s2 = nf > 0 and n_ok * 2 >= nf
    ci = o["VIP0_BASE"].get("ci95")
    s3 = o["VIP0_BASE"]["mean"] is not None and o["VIP0_BASE"]["mean"] > 0 and ci is not None and ci[0] > 0
    wf = c["wf_selected"]
    s4 = len(wf["selected_folds"]) >= 3 and wf["VIP0_BASE_mean"] is not None and wf["VIP0_BASE_mean"] > 0
    r = c["cost_ratio"]["VIP0_BASE"]["ratio"]
    s5 = r == "INF_COST_NONPOSITIVE" or (r is not None and r >= 1.2)
    s6 = o["VIP0_STRESS"]["mean"] is not None and o["VIP0_STRESS"]["mean"] > 0
    s7 = o["N_eff"] >= 200 and nf >= 6
    s8, rr = _robust(c)
    fails = [nm for ok, nm in ((s1, f"S1_FOLD_DIR_{g_ok}/{nf}"), (s2, f"S2_FOLD_NET_{n_ok}/{nf}"), (s3, "S3_OOS_NET_CI"),
                               (s4, "S4_WF_SELECT"), (s5, "S5_RATIO"), (s6, "S6_STRESS"), (s7, "S7_SAMPLE"),
                               (s8, "S8_ROBUST")) if not ok] + rr
    gci = o["ZERO"].get("ci95")
    if all((s1, s2, s3, s4, s5, s6, s7, s8)):
        v = "SURVIVE"
    elif s3:
        v, fails = "WEAK", ["W1"] + fails
    elif gci and gci[0] > 0 and s1 and s7:
        v, fails = "WEAK", ["W2_COST_KILLED_GROSS_EDGE"] + fails
    else:
        v = "REJECT"
    if c["horizon_min"] not in PRIMARY and v != "REJECT":
        fails, v = [f"REFERENCE_ONLY({v})"] + fails, "REFERENCE_ONLY"
    return v, fails


def decision_gate(cells):
    prim = [c for c in cells.values() if c["horizon_min"] in PRIMARY]
    survive = [c for c in prim if c["verdict"] == "SURVIVE"]
    maker = []
    for c in prim:
        if c["verdict"] == "WEAK" and "W2_COST_KILLED_GROSS_EDGE" in c["reasons"]:
            mr = c["cost_ratio"]["HYPOTHETICAL_MAKER_COST"]["ratio"]
            mci = c["oos"]["HYPOTHETICAL_MAKER_COST"]["ci95"]
            if (mr == "INF_COST_NONPOSITIVE" or (mr is not None and mr >= 1.2)) and mci and mci[0] > 0:
                maker.append(f"{c['feature']}-{c['bucket']}-{c['horizon_min']//60}h-{c['side']}")
    case = "A" if survive else ("B" if maker else "C")
    return {"case": case, "survive": len(survive), "maker_rescuable_w2": maker}


if __name__ == "__main__":
    import time
    from collections import Counter
    t0 = time.time()
    r = run()
    print(Counter(c["verdict"] for c in r["cells"].values()), r["gate"], f"{time.time()-t0:.0f}s")
