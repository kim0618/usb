"""Post verdict attribution. Nothing here feeds the verdict of contract section 14.

Three questions are asked after the verdict is fixed, and all three are "why was this family not
measurable" rather than "is there an edge after all":

1. What happens to a wall that sits within 10 bp of mid? If it is far more often cancelled than
   eaten, then the consumption half of Q2 is not a hypothesis the archive can test at all.
2. How often does a structural approach ever clear its level, and how long does it take? That is
   what decides whether H2 and H4 can fire at an episode's next minute.
3. In plain terms rather than in basis points: at a resistance approach with buy aggression and no
   progress, how often is price lower an hour later than the complement is?
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import families, outcomes
from .layers import LEVEL_TOL_PCT, RT_COST_BP, build_minute_frame, build_second_frame

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/market_context_r0"
MINUTE_MS = 60_000


def wall_fate(sf, eps) -> dict:
    """Held, consumed or cancelled at W = 60 s, per side, over every wall approach episode."""
    out = {}
    for side in ("ASK", "BID"):
        tally = {"held": 0, "gone_traded_through": 0, "gone_not_traded": 0, "no_decision": 0}
        for e in eps:
            if e.side != side:
                continue
            j = sf.index_after(e.anchor_index, 60_000)
            if j is None:
                tally["no_decision"] += 1
                continue
            present = families._bin_present(sf, j, side, e.bin_low)
            path = sf.mids[e.anchor_index + 1:j + 1]
            path = path[np.isfinite(path)]
            through = bool(path.size and (float(path.max()) >= e.bin_high if side == "ASK"
                                          else float(path.min()) <= float(e.bin_low)))
            if present:
                tally["held"] += 1
            elif through:
                tally["gone_traded_through"] += 1
            else:
                tally["gone_not_traded"] += 1
        gone = tally["gone_traded_through"] + tally["gone_not_traded"]
        out[side] = {**tally, "episodes": sum(tally.values()),
                     "consumed_share_of_gone": None if not gone else
                     round(tally["gone_traded_through"] / gone, 4)}
    return out


def clearance_timing(mf, eps) -> dict:
    """For each structural approach, whether and when the close clears the level by RT."""
    out = {}
    for side in ("RES", "SUP"):
        waits, cleared_60, never = [], 0, 0
        n = 0
        for e in eps:
            if e.side != side:
                continue
            n += 1
            gate = e.level_price * (1 + RT_COST_BP / 1e4) if side == "RES" \
                else e.level_price * (1 - RT_COST_BP / 1e4)
            hit = None
            for k in range(1, 61):
                i = e.anchor_index + k
                if i >= len(mf.close):
                    break
                c = float(mf.close[i])
                if (c > gate) if side == "RES" else (c < gate):
                    hit = k
                    break
            if hit is None:
                never += 1
            else:
                waits.append(hit)
                if hit <= 60:
                    cleared_60 += 1
        a = np.asarray(waits) if waits else np.asarray([np.nan])
        out[side] = {"episodes": n, "cleared_within_60m": cleared_60,
                     "cleared_rate": round(cleared_60 / n, 4) if n else None,
                     "never_within_60m": never,
                     "minutes_to_clear": {"median": None if not waits else float(np.median(a)),
                                          "p25": None if not waits else float(np.percentile(a, 25)),
                                          "p75": None if not waits else float(np.percentile(a, 75))},
                     "cleared_at_next_minute": int(sum(1 for w in waits if w == 1))}
    return out


def plain_terms(mf, eps) -> dict:
    """H1 and H3 restated without basis points: lower, higher or flat an hour later."""
    out = {}
    for fam in ("H1", "H3"):
        spec = families.minute_family(mf, eps, fam, "SIGN")
        _, side, lean, _ = families.FAMILIES[fam]
        rows = {"family": [], "complement": []}
        for e in spec["episodes"]:
            o = outcomes.minute_outcome(mf, e["i_dec"], lean, e["level_price"], 60, side)
            if o is None:
                continue
            rows["family" if e["fires"] else "complement"].append(o)
        res = {}
        for g, vals in rows.items():
            if not vals:
                res[g] = {"n": 0}
                continue
            moved = np.asarray([v["close_h"] - v["p0"] for v in vals])
            fm = {}
            for v in vals:
                fm[v["first_meaningful_direction"]] = fm.get(v["first_meaningful_direction"], 0) + 1
            res[g] = {"n": len(vals),
                      "down_after_60m_share": round(float((moved < 0).mean()), 4),
                      "median_move_usdt": round(float(np.median(moved)), 2),
                      "first_meaningful_direction": fm,
                      "reversal_rate": round(float(np.mean([v["reversal"] for v in vals])), 4)}
        out[fam] = res
    return out


def flow_isolated(mf, eps) -> dict:
    """The contrast H1 and H3 should have had: position held fixed, flow varied.

    H1's complement as the contract defines it contains the episodes that *did* clear the level, so
    part of any difference it shows is the breakouts sitting in the comparison group rather than the
    flow. This restricts both groups to approaches that did not clear, and splits them on flow sign
    alone. It is reported as attribution and does not feed the verdict.
    """
    out = {}
    for fam in ("H1", "H3"):
        spec = families.minute_family(mf, eps, fam, "SIGN")
        _, side, lean, _ = families.FAMILIES[fam]
        # Episode order is kept. The rotation null rotates a label against an outcome, so a label
        # sorted into its two groups first would be a single block and the test would be void.
        ordered: list[tuple[bool, dict]] = []
        for e in spec["episodes"]:
            if e["cleared"]:
                continue
            o = outcomes.minute_outcome(mf, e["i_dec"], lean, e["level_price"], 60, side)
            if o is None:
                continue
            ordered.append((bool(e["fires"]), o))
        groups = {"dominant": [o for fl, o in ordered if fl],
                  "not_dominant": [o for fl, o in ordered if not fl]}
        res = {}
        for g, vals in groups.items():
            if not vals:
                res[g] = {"n": 0}
                continue
            net = np.asarray([v["net_move_bp"] for v in vals])
            res[g] = {"n": len(vals), "mean_net_move_bp": round(float(net.mean()), 3),
                      "median_net_move_bp": round(float(np.median(net)), 3),
                      "reversal_rate": round(float(np.mean([v["reversal"] for v in vals])), 4),
                      "mean_reachable_bp": round(float(np.mean(
                          [v["reachable_bp"] for v in vals])), 3)}
        if res["dominant"].get("n") and res["not_dominant"].get("n"):
            from . import stats as st
            x = np.asarray([fl for fl, _ in ordered], dtype=bool)
            y = np.asarray([o["net_move_bp"] for _, o in ordered], dtype=float)
            res["edge_bp"] = round(float(y[x].mean() - y[~x].mean()), 4)
            res["circular_shift"] = st.circular_shift_p(y, x)
            # `reversal` is one of contract section 10's own outcomes, so testing it is not a new
            # test. It is still not the verdict's outcome, which section 14 fixes as net_move_bp,
            # and this line does not feed the verdict.
            rv = np.asarray([float(o["reversal"]) for _, o in ordered], dtype=float)
            res["reversal_circular_shift"] = st.circular_shift_p(rv, x)
        out[fam] = res
    return out


def reversal_confound(mf, eps) -> dict:
    """Is the reversal rate separation a context effect or a starting position effect?

    `reversal` is measured against the level, not against the entry, so an episode that starts
    further below the level is already closer to the gate before anything happens. The two groups
    turn out to start at very different distances, so the rate is also compared inside strata of
    the entry distance, where the gate is the same for both groups.
    """
    out = {}
    rowmap = {r["ts"]: r for r in mf.rows}
    for fam, dkey in (("H1", "res_distance_pct"), ("H3", "sup_distance_pct")):
        spec = families.minute_family(mf, eps, fam, "SIGN")
        _, side, lean, _ = families.FAMILIES[fam]
        recs = []
        for e in spec["episodes"]:
            o = outcomes.minute_outcome(mf, e["i_dec"], lean, e["level_price"], 60, side)
            if o is None:
                continue
            recs.append({"fires": bool(e["fires"]), "dist": abs(rowmap[e["anchor_ts"]][dkey]),
                         "reversal": bool(o["reversal"]), "net": o["net_move_bp"]})
        d = np.asarray([r["dist"] for r in recs])
        x = np.asarray([r["fires"] for r in recs], dtype=bool)
        rv = np.asarray([float(r["reversal"]) for r in recs])
        edges = np.percentile(d, [0, 20, 40, 60, 80, 100])
        strata = []
        weighted, weight = 0.0, 0.0
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (d >= lo) & (d <= hi) if hi == edges[-1] else (d >= lo) & (d < hi)
            if m.sum() < 10 or not (x & m).any() or not ((~x) & m).any():
                strata.append({"lo": round(float(lo), 4), "hi": round(float(hi), 4),
                               "n": int(m.sum()), "diff": None})
                continue
            diff = float(rv[x & m].mean() - rv[(~x) & m].mean())
            strata.append({"lo": round(float(lo), 4), "hi": round(float(hi), 4),
                           "n": int(m.sum()), "n_fire": int((x & m).sum()),
                           "fire_reversal": round(float(rv[x & m].mean()), 4),
                           "comp_reversal": round(float(rv[(~x) & m].mean()), 4),
                           "diff": round(diff, 4)})
            weighted += diff * int(m.sum())
            weight += int(m.sum())
        out[fam] = {
            "entry_distance_pct": {
                "fires_mean": round(float(d[x].mean()), 4),
                "fires_median": round(float(np.median(d[x])), 4),
                "complement_mean": round(float(d[~x].mean()), 4),
                "complement_median": round(float(np.median(d[~x])), 4)},
            "unstratified_reversal_diff": round(float(rv[x].mean() - rv[~x].mean()), 4),
            "stratified_reversal_diff": None if not weight else round(weighted / weight, 4),
            "strata": strata}
    return out


def run() -> dict:
    mf = build_minute_frame()
    sf = build_second_frame()
    m_eps = families.minute_episodes(mf)
    w_eps = families.wall_episodes(sf)
    payload = {"note": "post verdict attribution, does not feed the verdict",
               "wall_fate_at_60s": wall_fate(sf, w_eps),
               "structural_clearance": clearance_timing(mf, m_eps),
               "plain_terms_60m": plain_terms(mf, m_eps),
               "flow_isolated_60m": flow_isolated(mf, m_eps),
               "reversal_confound": reversal_confound(mf, m_eps),
               "level_tolerance_pct": LEVEL_TOL_PCT}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "diagnostics_mc_r0.json").write_text(json.dumps(payload, indent=1, default=str) + "\n")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
