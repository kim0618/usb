"""Contract section 8: descriptive statistics, R0's tests unchanged, Holm over the pre-registered grid.

A firing question is compared with the admitted, valid, non-firing episodes of the same side (R0
section 17.6). Tests run only where R0's own floor (30 firing episodes, section 12) is met; below
it the entry says NOT_MEASURABLE and carries its count.
"""
from __future__ import annotations

import numpy as np

from ..market_context_r0 import stats as R0S
from ..market_context_r0.replay import MIN_EPISODES
from . import contract as K


def describe(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    net = np.asarray([r["net_move_bp"] for r in rows], dtype=float)
    mfe = np.asarray([r["mfe_bp"] for r in rows], dtype=float)
    mae = np.asarray([r["mae_bp"] for r in rows], dtype=float)
    pos = net[net > 0]
    k1 = max(1, int(np.ceil(0.01 * net.size)))
    k10 = max(1, int(np.ceil(0.10 * net.size)))
    srt = np.sort(net)
    total = float(net.sum())
    return {"n": int(net.size), "win_rate": round(float((net > 0).mean()), 4),
            "mean_net_bp": round(float(net.mean()), 3),
            "median_net_bp": round(float(np.median(net)), 3),
            "p25_net_bp": round(float(np.percentile(net, 25)), 3),
            "p75_net_bp": round(float(np.percentile(net, 75)), 3),
            "median_mfe_bp": round(float(np.median(mfe)), 3),
            "median_mae_bp": round(float(np.median(mae)), 3),
            "positive_net_share": round(float((net > 0).mean()), 4),
            "worst_decile_mean_bp": round(float(srt[:k10].mean()), 3),
            "top1pct_share_of_positive_sum": (round(float(np.sort(pos)[-k1:].sum() / pos.sum()), 4)
                                              if pos.size else None),
            "sum_net_bp": round(total, 3)}


def summarise(records: list[dict], outcomes: list[dict]) -> dict:
    by_id = {}
    for o in outcomes:
        if o["status"] == "MEASURED":
            by_id[(o["event_id"], o["horizon_min"])] = o
    out: dict = {"questions": {}, "grid": []}
    for q in K.QUESTIONS:
        recs = [r for r in records if r["question"] == q]
        valid = [r for r in recs if r["validity"] == "VALID"]
        entry = {"admitted_episodes": len(recs), "valid_episodes": len(valid),
                 "firing_valid": sum(r["fires"] for r in valid),
                 "firing_r0_literal": sum(r["fires_r0_literal"] for r in recs),
                 "invalid_reasons": {}, "horizons": {}}
        for r in recs:
            if r["invalid_reason"]:
                entry["invalid_reasons"][r["invalid_reason"]] = \
                    entry["invalid_reasons"].get(r["invalid_reason"], 0) + 1
        for h in K.HORIZONS_MIN:
            have = [(r, by_id[(r["event_id"], h)]) for r in valid if (r["event_id"], h) in by_id]
            fire = [o for r, o in have if r["fires"]]
            comp = [o for r, o in have if not r["fires"]]
            he = {"firing": describe(fire), "complement": describe(comp),
                  "pending_or_missing_firing": sum(r["fires"] for r in valid) - len(fire)}
            if len(fire) >= MIN_EPISODES and comp:
                y = np.asarray([o["net_move_bp"] for _, o in have], dtype=float)
                x = np.asarray([r["fires"] for r, _ in have], dtype=bool)
                he["edge_vs_complement_bp"] = round(float(y[x].mean() - y[~x].mean()), 4)
                he["test"] = R0S.circular_shift_p(y, x)
                he["bootstrap_ci"] = R0S.bootstrap_ci(y, x)
                he["measurable"] = True
            else:
                he["measurable"] = False
                he["test"] = {"p_value": None}
            if h in K.TEST_GRID_HORIZONS_MIN:
                out["grid"].append({"question": q, "horizon_min": h,
                                    "p_value": he["test"].get("p_value"),
                                    "lean_sign_ok": (he.get("edge_vs_complement_bp") or 0) > 0,
                                    "own_mean_net_bp": he["firing"].get("mean_net_bp")})
            entry["horizons"][str(h)] = he
        n_stage = entry["horizons"][str(K.STAGE_HORIZON_MIN)]["firing"]["n"]
        entry["stage"] = K.stage(n_stage)
        entry["stage_n"] = n_stage
        out["questions"][q] = entry
    R0S.holm(out["grid"])
    return out
