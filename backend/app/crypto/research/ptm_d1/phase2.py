"""PTM-D1 phase 2: TRAIN market-state census (contract section 4). States and excursions only.

Run: python -m app.crypto.research.ptm_d1.phase2  (from backend/)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data
from ..ptm_r1.r1_train import make_market
from ..ptm_r1.states import NONE, run_lengths
from . import features as F
from . import entries as E

DOCS = data.ROOT / "docs/crypto/personal_trading_method_discovery_v1"
CONTRACT = DOCS / "CONTRACT_PTM_D1_V1.md"
OUT = data.ROOT / "data/research/crypto/ptm_d1/phase2_census_v1.json"
GRID = 60                                   # hourly boundaries for excursion sampling


def q(v) -> dict | None:
    a = np.asarray(v, dtype=float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return None
    return {"n": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)),
            "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90))}


def share(mask: np.ndarray, base: np.ndarray) -> float | None:
    return float(mask[base].mean()) if base.any() else None


def excursions(f: F.Features, px: data.Prices) -> list[dict]:
    """At every hourly boundary with D != 0 and all features defined: excursion to the D episode end."""
    d = f.st["D"]
    n = len(d)
    ends = np.concatenate((np.flatnonzero(d[1:] != d[:-1]) + 1, [n]))     # first index of next run
    out = []
    ok = F.defined(f) & (d != 0)
    for t in range(GRID - 1, n - 1, GRID):
        if not ok[t]:
            continue
        e = int(ends[np.searchsorted(ends, t, side="right")])             # episode end (exclusive)
        if e >= n:
            continue                                                       # run touches series end
        dirn = int(d[t])
        p0 = px.close[t]
        seg_h, seg_l = px.high[t + 1:e + 1], px.low[t + 1:e + 1]
        fav = (seg_h / p0 - 1.0) if dirn == 1 else (1.0 - seg_l / p0)
        adv = (1.0 - seg_l / p0) if dirn == 1 else (seg_h / p0 - 1.0)
        disp = dirn * (px.close[e] / p0 - 1.0)
        v = f.V[t]
        align = sum(int(f.st[k][t] == dirn) for k in ("H4", "H1", "M"))
        out.append({"t": t, "dir": dirn, "pr_rel": int(f.PR[t]) * dirn, "vr": int(f.VR[t]),
                    "align": align, "h4_rel": int(f.st["H4"][t]) * dirn,
                    "age_h": float(f.thesis_age[t] / 60), "to_end_h": float((e - t) / 60),
                    "mfe_v": float(fav.max() / v), "mae_v": float(adv.max() / v),
                    "disp_v": float(disp / v), "pbz": float(f.pbz[t])})
    return out


def group(rows: list[dict], key, label) -> dict:
    res = {}
    for r in rows:
        res.setdefault(label(r), []).append(r)
    out = {}
    for k, rs in sorted(res.items(), key=lambda kv: str(kv[0])):
        out[str(k)] = {"n": len(rs), "share": len(rs) / len(rows),
                       "disp_v": q([r["disp_v"] for r in rs]),
                       "disp_pos_share": float(np.mean([r["disp_v"] > 0 for r in rs])),
                       "mfe_v": q([r["mfe_v"] for r in rs]), "mae_v": q([r["mae_v"] for r in rs]),
                       "to_end_h": q([r["to_end_h"] for r in rs])}
    return out


def h4_return_rate(f: F.Features) -> dict:
    d, h4 = f.st["D"], f.st["H4"]
    n = len(d)
    ends = np.concatenate((np.flatnonzero(d[1:] != d[:-1]) + 1, [n]))
    onsets = ret = 0
    for dirn in (1, -1):
        opp = (d == dirn) & (h4 == -dirn)
        on = np.flatnonzero(opp & ~np.concatenate(([False], opp[:-1])))
        for t in on:
            e = int(ends[np.searchsorted(ends, t, side="right")])
            onsets += 1
            if (h4[t:e] == dirn).any():
                ret += 1
    return {"h4_opposite_onsets": onsets, "returned_to_D_in_episode": ret,
            "return_share": ret / onsets if onsets else None}


def run(out: Path | None = OUT) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    log = data.ReadLog()
    vol, cov = data.load_train(log)
    px = data.load_train_prices(log)
    funding = data.load_train_funding(log)
    basis = data.load_train_basis(log)
    f = F.build(vol, px, funding, basis)
    st = f.st
    n = len(px.open)
    d = st["D"]
    defined = F.defined(f)
    rep: dict = {"contract_sha256": sha, "coverage": cov, "defined_share": float(defined.mean()),
                 "first_defined_boundary": int(np.argmax(defined))}
    rep["run_lengths_h"] = {k: q(run_lengths(st[k]) / 60) for k in ("D", "H4", "H1", "M")}
    rep["flips_per_day"] = {k: float(len(run_lengths(st[k])) / (n / 1440)) for k in ("D", "H4", "H1", "M")}
    base = defined & (d != 0)
    rel = lambda arr: np.where(d != 0, arr * d, NONE)       # noqa: E731
    rep["time_share"] = {
        "D_even": float((d[defined] == 0).mean()),
        "H4_rel_D": {k: share(rel(st["H4"]) == v, base) for k, v in (("agree", 1), ("even", 0), ("opp", -1))},
        "H1_rel_D": {k: share(rel(st["H1"]) == v, base) for k, v in (("agree", 1), ("even", 0), ("opp", -1))},
        "PR_rel_D": {k: share(rel(f.PR) == v, base) for k, v in (("agree", 1), ("flat", 0), ("conflict", -1))},
        "VR_high": share(f.VR == 1, defined),
        "no_trade": float(F.no_trade(f)[defined].mean()),
        "deep_pullback_now": share(f.pbz >= F.PB_DEEP, base),
    }
    rep["h4_return"] = h4_return_rate(f)
    rows = excursions(f, px)
    rep["excursion_samples"] = len(rows)
    rep["excursion_all"] = group(rows, None, lambda r: "all")
    rep["excursion_by_pr_rel"] = group(rows, None, lambda r: {1: "agree", 0: "flat", -1: "conflict"}[r["pr_rel"]])
    rep["excursion_by_vr"] = group(rows, None, lambda r: "HIGH" if r["vr"] == 1 else "LOW")
    rep["excursion_by_align"] = group(rows, None, lambda r: r["align"])
    rep["excursion_by_h4_rel"] = group(rows, None, lambda r: {1: "agree", 0: "even", -1: "opp"}[r["h4_rel"]])
    rep["excursion_by_pr_x_vr"] = group(rows, None, lambda r: f"pr{r['pr_rel']}_vr{r['vr']}")
    rep["excursion_by_age"] = group(rows, None, lambda r: "age<24h" if r["age_h"] < 24 else "age>=24h")
    rep["excursion_by_deep_pb"] = group(rows, None, lambda r: "deep" if r["pbz"] >= F.PB_DEEP else "shallow")
    # entry draft onset counts (states only)
    mk = make_market(px, funding)
    counts = {}
    for fam in E.FAMILIES:
        ons = E.onsets(f, fam)
        kept = E.one_position_count(ons, f, mk.last)
        counts[fam] = {"raw_onsets": len(ons), "long": sum(1 for o in ons if o[1] == 1),
                       "short": sum(1 for o in ons if o[1] == -1),
                       "non_overlapping_upper_bound_48h": kept}
    rep["entry_draft_onsets"] = counts
    rep["read_log"] = log.entries
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True))
    return rep


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({k: r[k] for k in ("defined_share", "run_lengths_h", "flips_per_day", "time_share",
                                        "h4_return", "excursion_samples", "entry_draft_onsets")}, indent=1))
