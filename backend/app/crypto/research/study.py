"""D5 study runner: every feature x bucket x horizon x side cell, per split, per cost scenario.

Refuses to run unless the contract file still hashes to the frozen value. Writes one JSON with
every cell (no filtering), then applies the frozen verdict rules to that JSON.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np

from app.crypto.paper.fees import load_scenarios

from . import dataset, features as F

CONTRACT = Path("docs/crypto/CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1.md")
FREEZE = Path("data/runtime/crypto/d5/contract_freeze_v1.json")
FEE_VERIFICATION = Path("data/runtime/crypto/reference/fee_source_verification_v1.json")
MICRO = Path("data/runtime/crypto/d5/realtime_cost_measurement_v1.json")
OUT = Path("data/runtime/crypto/d5/cells_v1.json")

DAY_MS = 86_400_000
SPLITS = (
    ("DISCOVERY", "2021-03-04", "2024-07-01"),  # contract s2: warm-up -> samples from 2021-03-04
    ("VALIDATION", "2024-07-01", "2025-10-01"),
    ("HOLDOUT", "2025-10-01", "2026-09-23"),
)
SIDES = ("LONG", "SHORT")
SCENARIOS = ("ZERO", "VIP0_BASE", "VIP0_STRESS")
N_BUCKETS = 5
BOOT_B = 1000
BOOT_BLOCK_DAYS = 7
SEED = 20260924
MAX_H = max(F.HORIZONS)

REGIME_AXES = {"trend": ("BULL", "BEAR", "SIDEWAYS"), "vol": ("HIGH", "MID", "LOW"),
               "session": ("00-06", "06-12", "12-18", "18-24"),
               "year": ("2021", "2022", "2023", "2024", "2025", "2026")}


def check_freeze() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract changed after freeze: {now} != {frozen}")
    return now


def micro_costs() -> dict[str, float]:
    """Contract s8: BASE = p50 spread + p50 buy + p50 sell impact at Q_ref; STRESS = observed max of each."""
    m = json.loads(MICRO.read_text())
    imp = m["impact"]["0.1"]
    return {
        "BASE": m["spread_frac"]["p50"] + imp["buy_frac"]["p50"] + imp["sell_frac"]["p50"],
        "STRESS": m["spread_frac"]["max"] + imp["buy_frac"]["max"] + imp["sell_frac"]["max"],
    }


def _ms(day: str) -> int:
    return int(np.datetime64(day + "T00:00:00", "ms").astype(np.int64))


def split_masks(ts: np.ndarray) -> dict[str, np.ndarray]:
    n = len(ts)
    out = {}
    for name, a, b in SPLITS:
        s, e = _ms(a), _ms(b)
        inside = (ts >= s) & (ts < e)
        # contract s2 embargo: t and t+1+30 inside the same split
        exit_ts = np.full(n, np.iinfo(np.int64).max)
        exit_ts[: n - 1 - MAX_H] = ts[1 + MAX_H:]
        out[name] = inside & (exit_ts < e)
    return out


def _block_weights(n_days: int, rng: np.random.Generator) -> np.ndarray:
    """Moving-block bootstrap weights: W[b, d] = how often day d is drawn in replicate b."""
    k = int(np.ceil(n_days / BOOT_BLOCK_DAYS))
    starts = rng.integers(0, max(n_days - BOOT_BLOCK_DAYS + 1, 1), size=(BOOT_B, k))
    idx = (starts[:, :, None] + np.arange(BOOT_BLOCK_DAYS)).reshape(BOOT_B, -1)[:, :n_days]
    W = np.zeros((BOOT_B, n_days))
    np.add.at(W, (np.repeat(np.arange(BOOT_B), idx.shape[1]), idx.ravel()), 1)
    return W


def run() -> dict:
    contract_sha = check_freeze()
    fees = load_scenarios(FEE_VERIFICATION)
    taker = float(fees["VIP_0"].taker_rate)
    assert fees["ZERO"].taker_rate == Decimal(0)
    micro = micro_costs()
    g = dataset.load()
    ts = g["ts"]
    n = len(ts)
    n_days = n // F.DAY_BARS
    day = ((ts - ts[0]) // DAY_MS).astype(np.int64)
    masks = split_masks(ts)
    feats = F.compute_features(g)
    buckets = {k: F.bucketize(v, n_days) for k, v in feats.items()}
    del feats
    regimes = F.regime_labels(g, masks["DISCOVERY"])
    rng = np.random.default_rng(SEED)
    split_days = {}
    for name, m in masks.items():
        d = np.unique(day[m])
        split_days[name] = (d, _block_weights(len(d), rng))

    cells: dict[str, dict] = {}
    pooled_regime: dict[str, dict] = {}
    for h in F.HORIZONS:
        T = F.targets(g, h)
        fee = taker * (1 + T["ratio"])
        vals = {}
        for side in SIDES:
            gross = T["long"] if side == "LONG" else T["short"]
            fund = T["fund_long"] if side == "LONG" else -T["fund_long"]
            vals[(side, "ZERO")] = gross
            vals[(side, "VIP0_BASE")] = gross - fee - micro["BASE"] - fund
            vals[(side, "VIP0_STRESS")] = gross - fee - micro["STRESS"] - fund
        for fcode, fname in F.FEATURES:
            b = buckets[fname]
            for sname, m in masks.items():
                sel = m & (b >= 0) & np.isfinite(T["ratio"])
                dsel, W = split_days[sname]
                dpos = np.searchsorted(dsel, day[sel])
                key = dpos * N_BUCKETS + b[sel]
                size = len(dsel) * N_BUCKETS
                cnt = np.bincount(key, minlength=size).reshape(len(dsel), N_BUCKETS).astype(float)
                bsel = b[sel]
                for side in SIDES:
                    sd = side.lower()
                    mae = np.bincount(bsel, weights=T[f"mae_{sd}"][sel], minlength=N_BUCKETS)
                    mfe = np.bincount(bsel, weights=T[f"mfe_{sd}"][sel], minlength=N_BUCKETS)
                    for scen in SCENARIOS:
                        v = vals[(side, scen)][sel]
                        s = np.bincount(key, weights=v, minlength=size).reshape(len(dsel), N_BUCKETS)
                        win = np.bincount(bsel, weights=(v > 0).astype(float), minlength=N_BUCKETS)
                        boot = (W @ s) / (W @ cnt)
                        med = None
                        if scen != "VIP0_STRESS":
                            med = [float(np.median(v[bsel == k])) if (bsel == k).any() else None for k in range(N_BUCKETS)]
                        tot_n = cnt.sum(0)
                        tot_s = s.sum(0)
                        for k in range(N_BUCKETS):
                            cid = f"{fcode}-B{k+1}-{h}m-{side}"
                            c = cells.setdefault(cid, {"feature": fcode, "feature_name": fname, "bucket": f"B{k+1}",
                                                        "horizon": h, "side": side, "splits": {}})
                            sp = c["splits"].setdefault(sname, {"N": int(tot_n[k]), "days": int((cnt[:, k] > 0).sum()),
                                                                "mae_mean": float(mae[k] / tot_n[k]) if tot_n[k] else None,
                                                                "mfe_mean": float(mfe[k] / tot_n[k]) if tot_n[k] else None,
                                                                "scen": {}, "_daily": {}})
                            bk = boot[:, k]
                            bk = bk[np.isfinite(bk)]
                            sp["scen"][scen] = {
                                "mean": float(tot_s[k] / tot_n[k]) if tot_n[k] else None,
                                "median": med[k] if med else None,
                                "win_rate": float(win[k] / tot_n[k]) if tot_n[k] else None,
                                "se": float(bk.std()) if len(bk) else None,
                                "ci95": [float(np.quantile(bk, .025)), float(np.quantile(bk, .975))] if len(bk) else None,
                                "ci90": [float(np.quantile(bk, .05)), float(np.quantile(bk, .95))] if len(bk) else None,
                            }
                            sp["_daily"][scen] = float(tot_s[k])
            # pooled regime decomposition (VIP0_BASE), all three splits together
            allm = (masks["DISCOVERY"] | masks["VALIDATION"] | masks["HOLDOUT"]) & (b >= 0) & np.isfinite(T["ratio"])
            for axis, labels in list(REGIME_AXES.items()) + [("hour", tuple(f"{x:02d}" for x in range(24)))]:
                lab = regimes[axis]
                ok = allm & (lab >= 0)
                L = len(labels)
                key = b[ok].astype(np.int64) * L + lab[ok]
                cnt = np.bincount(key, minlength=N_BUCKETS * L).reshape(N_BUCKETS, L)
                for side in SIDES:
                    s = np.bincount(key, weights=vals[(side, "VIP0_BASE")][ok], minlength=N_BUCKETS * L).reshape(N_BUCKETS, L)
                    g0 = np.bincount(key, weights=vals[(side, "ZERO")][ok], minlength=N_BUCKETS * L).reshape(N_BUCKETS, L)
                    for k in range(N_BUCKETS):
                        cid = f"{fcode}-B{k+1}-{h}m-{side}"
                        pooled_regime.setdefault(cid, {})[axis] = {
                            labels[j]: {"N": int(cnt[k, j]),
                                        "net": float(s[k, j] / cnt[k, j]) if cnt[k, j] else None,
                                        "gross": float(g0[k, j] / cnt[k, j]) if cnt[k, j] else None}
                            for j in range(L)}
        del T, vals, fee
    for cid, c in cells.items():
        c["regimes"] = pooled_regime[cid]
        v, ho = c["splits"]["VALIDATION"], c["splits"]["HOLDOUT"]
        nvh = v["N"] + ho["N"]
        c["stress_vh_mean"] = (v["_daily"]["VIP0_STRESS"] + ho["_daily"]["VIP0_STRESS"]) / nvh if nvh else None
        for sp in c["splits"].values():
            sp.pop("_daily")
        c["verdict"], c["reasons"] = verdict(c)
    # unconditional baseline per split/horizon/side (every in-split bar), for context only
    baseline = {}
    for h in F.HORIZONS:
        T = F.targets(g, h)
        for sname, m in masks.items():
            sel = m & np.isfinite(T["ratio"])
            baseline[f"{sname}-{h}m"] = {"N": int(sel.sum()), "long_gross_mean": float(T["long"][sel].mean()),
                                         "long_hit": float((T["long"][sel] > 0).mean())}
    result = {
        "contract_sha256": contract_sha, "taker_vip0": taker, "micro": micro,
        "splits": {k: {"start": a, "end": b, "N_bars": int(masks[k].sum())} for k, a, b in SPLITS},
        "vol_cutoffs": regimes["vol_cutoffs"].tolist(),
        "baseline": baseline, "cells": cells,
    }
    OUT.write_text(json.dumps(result))
    return result


def _regime_pass(c: dict) -> tuple[bool, list[str]]:
    reasons = []
    need = {"trend": lambda k, n: k >= 2, "vol": lambda k, n: k >= 2,
            "session": lambda k, n: k >= 3, "year": lambda k, n: n > 0 and k * 3 >= 2 * n}
    ok_all = True
    for axis, rule in need.items():
        vals = [x["net"] for x in c["regimes"][axis].values() if x["N"] >= 200 and x["net"] is not None]
        pos = sum(v > 0 for v in vals)
        if not rule(pos, len(vals)):
            ok_all = False
            reasons.append(f"ROBUST_{axis.upper()}_{pos}/{len(vals)}")
    return ok_all, reasons


def verdict(c: dict) -> tuple[str, list[str]]:
    D, V, H = (c["splits"][s] for s in ("DISCOVERY", "VALIDATION", "HOLDOUT"))

    def m(sp, sc="VIP0_BASE"):
        return sp["scen"][sc]["mean"]

    def lo(sp, ci, sc="VIP0_BASE"):
        x = sp["scen"][sc][ci]
        return x[0] if x else None

    fails = []
    s1 = m(D) is not None and m(D) > 0 and (lo(D, "ci95") or -1) > 0
    s2 = m(V) is not None and m(V) > 0 and (lo(V, "ci90") or -1) > 0
    s3 = m(H) is not None and m(H) > 0
    s4 = all(sp["scen"]["ZERO"]["mean"] is not None and sp["scen"]["ZERO"]["mean"] > 0 for sp in (D, V, H))
    s5 = all(sp["N"] >= 1000 and sp["days"] >= 30 for sp in (D, V, H))
    s6 = c["stress_vh_mean"] is not None and c["stress_vh_mean"] > 0
    s7, rreasons = _regime_pass(c)
    for flag, name in ((s1, "S1_DISCOVERY"), (s2, "S2_VALIDATION"), (s3, "S3_HOLDOUT"), (s4, "S4_DIRECTION"),
                       (s5, "S5_SAMPLE"), (s6, "S6_STRESS"), (s7, "S7_ROBUST")):
        if not flag:
            fails.append(name)
    fails += rreasons
    core = s1 and s2 and s3 and s4 and s5
    if core and s6 and s7:
        v = "SURVIVE"
    elif core:
        v = "WEAK"
        fails.insert(0, "W1")
    elif s4 and s5 and (lo(D, "ci95", "ZERO") or -1) > 0 and (lo(V, "ci90", "ZERO") or -1) > 0:
        v = "WEAK"
        fails.insert(0, "W2_COST_KILLED_GROSS_EDGE")
    else:
        v = "REJECT"
    if c["horizon"] not in F.PRIMARY_HORIZONS and v != "REJECT":
        fails.insert(0, f"REFERENCE_ONLY({v})")
        v = "REFERENCE_ONLY"
    return v, fails


if __name__ == "__main__":
    import time
    t0 = time.time()
    r = run()
    from collections import Counter
    print(Counter(c["verdict"] for c in r["cells"].values()), f"{time.time()-t0:.0f}s")
