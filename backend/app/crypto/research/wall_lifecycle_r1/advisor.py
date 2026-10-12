"""Entry Advisor R0 (CONTRACT_ENTRY_ADVISOR_R0): families F1..F6 x timings T0..T3 on the level
episodes, with entry zone, invalidations V1..V3, targets T1..T3 and fixed exits, cost, R and the
gate. Research labels only. `verdict_at` is what the local preview shows for one second."""
from __future__ import annotations

import hashlib
import math
from typing import Any

from . import contract as K
from .outcomes import MIN_MS, Series
from .replay import ASK, BID, Level, Replay, near_far
from .stats import block_bootstrap, concentration, holm, summary
from .study import cluster_of


def _rid(session8: str, sample_ms: int, family: str, timing: str) -> str:
    return hashlib.sha256(f"{session8}|{sample_ms}|{family}|{timing}".encode()).hexdigest()[:16]


def family_base(level: Level) -> str | None:
    """F1/F3 for a touched, held level; F2/F4 for a consumed one. None otherwise."""
    if level.t_touch is None:
        return None
    if level.end_class == K.END_CONSUMED:
        return "F2" if level.side == ASK else "F4"
    if level.end_class in (K.END_TOUCHED_PULLED,) or level.end_ms is None:
        return "F1" if level.side == ASK else "F3"
    return None


def direction_of(family: str, side: str) -> str:
    if family in ("F1", "F4"):
        return "SHORT"
    if family in ("F2", "F3"):
        return "LONG"
    # clusters follow their base family
    return "SHORT" if (family == "F5" and side == ASK) or (family == "F6" and side == BID) else "LONG"


def timing_instants(level: Level, rejection: bool) -> dict[str, int | None]:
    out: dict[str, int | None] = {"T0": level.t_approach, "T1": level.t_touch}
    if rejection:
        t2 = level.t_touch + K.T2_AFTER_TOUCH_MS if level.t_touch is not None else None
        t3 = level.t_touch + K.T3_AFTER_TOUCH_MS if level.t_touch is not None else None
        alive = lambda t: t is not None and (level.end_ms is None or level.end_ms > t)  # noqa: E731
        out["T2"] = t2 if alive(t2) else None
        out["T3"] = t3 if alive(t3) else None
    else:
        out["T2"] = level.t_cross
        out["T3"] = level.end_ms if level.end_class == K.END_CONSUMED else None
    return out


def conditions_hold(series: Series, level: Level, family_base_id: str, t: int, timing: str) -> bool:
    """The family's conditions at the instant (contract section 1); T0 is the early arm."""
    mid = series.mid_at(t)
    if mid is None:
        return False
    near, far = near_far(level.side, level.bin_low, level.bin_high)
    flow = series.flow_side(t)
    into = "BUY" if level.side == ASK else "SELL"
    if family_base_id in ("F1", "F3"):
        if timing != "T0" and (level.t_touch is None or t < level.t_touch):
            return False
        if level.end_ms is not None and level.end_ms <= t:
            return False
        beyond = mid > far if level.side == ASK else mid < far
        if beyond:
            return False
        if timing in ("T2", "T3"):
            inside = mid >= near if level.side == ASK else mid <= near
            if inside:
                return False
        return flow == into
    # breakout
    if timing != "T0":
        beyond = mid > far if level.side == ASK else mid < far
        if not beyond:
            return False
    return flow == into


def _exit_outcome(series: Series, side_sign: int, entry_ms: int, entry: float, stop: float,
                  target: float | None, hold_ms: int) -> dict[str, Any]:
    """side_sign +1 LONG, -1 SHORT. Returns exit kind, exit ms, gross bp, MAE before hit, MFE."""
    stop_hit = series.first_reach(entry_ms, hold_ms, stop, -side_sign)
    tgt_hit = series.first_reach(entry_ms, hold_ms, target, side_sign) if target is not None else None
    w = series.window(entry_ms, hold_ms)
    if w is None:
        return {"usable": False}
    if tgt_hit is not None and (stop_hit is None or tgt_hit <= stop_hit):
        exit_ms, price, kind = tgt_hit, target, "TARGET"
    elif stop_hit is not None:
        exit_ms, price, kind = stop_hit, stop, "INVALIDATED"
    else:
        exit_ms, price, kind = int(series.t[w[1]]), float(series.mid[w[1]]), "TIME"
    gross = side_sign * (price - entry) / entry * 1e4
    exc = series.excursions(entry_ms, max(1000, exit_ms - entry_ms), side_sign)
    return {"usable": True, "exit": kind, "exit_ms": exit_ms, "gross_bp": gross,
            "time_to_exit_s": (exit_ms - entry_ms) / 1000,
            "mfe_bp": exc[0] if exc else None, "mae_bp": exc[1] if exc else None}


def funding_cost_bp(series: Series, side_sign: int, entry_ms: int, exit_ms: int) -> float:
    """Funding paid (+) or received (-) in bp: LONG pays when the rate is positive."""
    total = 0.0
    for _t, rate in series.funding_inside(entry_ms, exit_ms):
        total += side_sign * rate * 1e4
    return total


def build_events(rep: Replay, series: Series) -> list[dict[str, Any]]:
    session8 = (rep.session_id or "")[:8]
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for level in rep.levels:
        if not level.display_qualified:
            continue
        base = family_base(level)
        if base is None:
            continue
        rejection = base in ("F1", "F3")
        instants = timing_instants(level, rejection)
        for timing in K.TIMINGS:
            t = instants.get(timing)
            if t is None:
                continue
            i = series.index_at(t)
            if i is None:
                continue
            disp_same = rep.path.display_ask if level.side == ASK else rep.path.display_bid
            # cluster membership is read one sample before the instant: the level's own V2 bin
            # is CLOSEd in the second the mid enters it (1 bp floor), so at the instant itself it
            # is never in the display set (recorded deviation, same cause as Q2)
            is_cluster = cluster_of(disp_same[max(0, i - 1)], level.side, level.bin_low, series.atr_at(t))
            fams = [base] + (["F5" if rejection else "F6"] if is_cluster else [])
            holds = conditions_hold(series, level, base, t, timing)
            for fam in fams:
                key = (level.episode_id, fam, timing)
                if key in seen:
                    continue
                seen.add(key)
                if not holds:
                    continue
                direction = direction_of(fam, level.side)
                side_sign = 1 if direction == "LONG" else -1
                mid = series.mid_at(t)
                near, far = near_far(level.side, level.bin_low, level.bin_high)
                rng = series.range_before(t, K.RANGE_WINDOW_MS) or 0.0
                if rejection:
                    zone = (near - rng, far) if level.side == ASK else (far, near + rng)
                    v1 = far
                else:
                    zone = (far, far + rng) if level.side == ASK else (far - rng, far)
                    v1 = far   # back inside the bin
                beyond_same = [low for low, _n in disp_same[i]
                               if (low > level.bin_low if level.side == ASK else low < level.bin_low)]
                v3 = None
                if beyond_same:
                    v3 = beyond_same[0] if level.side == ASK else beyond_same[0] + K.BIN_WIDTH_USDT
                # targets (contract section 5): the walls on the side opposing the TRADE, near
                # edges, nearest first: a SHORT aims at BID walls below (bin_high), a LONG at ASK
                # walls above (bin_low). For a breakout that is the level's own side beyond it.
                if direction == "SHORT":
                    targets = [low + K.BIN_WIDTH_USDT for low, _n in rep.path.display_bid[i]]
                else:
                    targets = [low for low, _n in rep.path.display_ask[i]]
                targets = [p for p in targets if (p < mid if direction == "SHORT" else p > mid)][:3]
                ev: dict[str, Any] = {
                    # the contract's join key (section 10: session, second, family, timing) and
                    # the event's own id (two levels can be consumed in the same second)
                    "analysis_request_id": _rid(session8, t, fam, timing),
                    "event_id": hashlib.sha256(f"{level.episode_id}|{fam}|{timing}".encode()).hexdigest()[:16],
                    "episode_id": level.episode_id, "family": fam, "timing": timing,
                    "direction": direction, "side": level.side, "t": t, "entry": mid,
                    "entry_zone": [min(zone), max(zone)], "entry_in_zone": min(zone) <= mid <= max(zone),
                    "zone_width_bp": abs(zone[1] - zone[0]) / mid * 1e4,
                    "v1": v1, "v3": v3, "risk_bp_v1": abs(mid - v1) / mid * 1e4,
                    "targets": targets, "flow": series.flow_side(t),
                    "r15_gross_bp": series.return_bp(t, K.PRIMARY_H_MIN * MIN_MS, side_sign),
                }
                v2_ms = series.first_flow_flip(t, K.HOLD_MAX_MIN * MIN_MS, "SELL" if direction == "LONG" else "BUY")
                ev["v2_flip_ms"] = v2_ms
                hold = K.HOLD_MAX_MIN * MIN_MS
                for k, target in enumerate(targets, 1):
                    o = _exit_outcome(series, side_sign, t, mid, v1, target, hold)
                    if o.get("usable"):
                        o["reward_bp"] = abs(target - mid) / mid * 1e4
                        o["R"] = o["reward_bp"] / ev["risk_bp_v1"] if ev["risk_bp_v1"] > 0 else None
                        o["net_bp"] = o["gross_bp"] - K.RT_COST_BP - funding_cost_bp(series, side_sign, t, o["exit_ms"])
                    ev[f"T{k}"] = o
                for minutes in K.FIXED_EXITS_MIN:
                    o = _exit_outcome(series, side_sign, t, mid, v1, None, minutes * MIN_MS)
                    if o.get("usable"):
                        o["net_bp"] = o["gross_bp"] - K.RT_COST_BP - funding_cost_bp(series, side_sign, t, o["exit_ms"])
                    ev[f"fixed_{minutes}m"] = o
                events.append(ev)
    return events


def _cell(events: list[dict], key: str) -> dict[str, Any]:
    usable = [e for e in events if e.get(key, {}).get("usable")]
    nets = [e[key]["net_bp"] for e in usable]
    times = [e["t"] for e in usable]
    out = summary(nets)
    out.update(block_bootstrap(nets, times))
    out["gross"] = summary([e[key]["gross_bp"] for e in usable])
    out["hit_rate"] = (sum(e[key]["exit"] == "TARGET" for e in usable) / len(usable)) if usable else None
    out["invalidated_rate"] = (sum(e[key]["exit"] == "INVALIDATED" for e in usable) / len(usable)) if usable else None
    out["time_to_hit_s"] = summary([e[key]["time_to_exit_s"] for e in usable if e[key]["exit"] == "TARGET"])
    out["mae_before_hit_bp"] = summary([e[key]["mae_bp"] for e in usable if e[key]["exit"] == "TARGET"])
    out["mfe_bp"] = summary([e[key]["mfe_bp"] for e in usable])
    if key.startswith("T"):
        out["R"] = summary([e[key]["R"] for e in usable if e[key].get("R") is not None])
        out["reward_bp"] = summary([e[key]["reward_bp"] for e in usable])
    out["concentration_top10_share"] = concentration(nets)
    out["missing_target"] = sum(1 for e in events if key not in e)
    return out


def evaluate(events: list[dict], rep: Replay, series: Series) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    pvals: dict[str, float | None] = {}
    for fam in K.FAMILIES:
        for timing in K.TIMINGS:
            sub = [e for e in events if e["family"] == fam and e["timing"] == timing]
            cell: dict[str, Any] = {"n": len(sub), "stage": K.stage(len(sub)),
                                    "direction": direction_of(fam, ASK) if fam in ("F1", "F2") else
                                    direction_of(fam, BID) if fam in ("F3", "F4") else "by side"}
            for key in ("T1", "T2", "T3", "fixed_5m", "fixed_15m", "fixed_30m", "fixed_60m"):
                cell[key] = _cell(sub, key)
            cell["risk_bp_v1"] = summary([e["risk_bp_v1"] for e in sub])
            cell["zone_width_bp"] = summary([e["zone_width_bp"] for e in sub])
            cell["entry_in_zone_rate"] = (sum(e["entry_in_zone"] for e in sub) / len(sub)) if sub else None
            cell["v2_flip_within_60m_rate"] = (sum(e["v2_flip_ms"] is not None for e in sub) / len(sub)) if sub else None
            cell["r15_gross"] = summary([e["r15_gross_bp"] for e in sub])
            cells[f"{fam}:{timing}"] = cell
            pvals[f"{fam}:{timing}"] = cell["T1"].get("p") if timing != "T0" else None
    adj = holm(pvals)
    for k, cell in cells.items():
        cell["holm_p_T1_net"] = adj.get(k)
        cell["gate"] = gate(cell)
    # NO_TRADE share: seconds with no event starting and touch instants without an event
    starts = {e["t"] for e in events}
    seconds = len(rep.path.sample_ms)
    touches = [lv.t_touch for lv in rep.levels if lv.display_qualified and lv.t_touch is not None]
    no_trade = {"share_of_seconds": 1 - len(starts) / seconds if seconds else None,
                "share_of_touch_instants": (sum(t not in starts for t in touches) / len(touches)) if touches else None,
                "event_seconds": len(starts), "seconds": seconds, "touch_instants": len(touches)}
    return {"cells": cells, "no_trade": no_trade, "events": len(events), "verdict": advisor_verdict(cells)}


def gate(cell: dict[str, Any]) -> dict[str, Any]:
    t1 = cell["T1"]
    checks = {
        "stage": cell["stage"],
        "direction_alignment": (cell["r15_gross"]["mean"] or 0) > 0 if cell["r15_gross"]["n"] else False,
        "mean_net_positive": (t1.get("mean") or 0) > 0 if t1.get("n") else False,
        "median_net_positive": (t1.get("median") or 0) > 0 if t1.get("n") else False,
        "holm_p_le_0_05": cell["holm_p_T1_net"] is not None and cell["holm_p_T1_net"] <= 0.05,
        "concentration_ok": t1.get("concentration_top10_share") is not None
        and t1["concentration_top10_share"] <= K.CONCENTRATION_MAX,
        "p5_net": t1.get("p5"),
    }
    eligible = cell["stage"] in ("REVIEW_ELIGIBLE", "STRONGER_EVIDENCE")
    checks["passes_review"] = bool(eligible and checks["direction_alignment"] and checks["mean_net_positive"]
                                   and checks["median_net_positive"] and checks["holm_p_le_0_05"]
                                   and checks["concentration_ok"])
    return checks


def advisor_verdict(cells: dict[str, Any]) -> str:
    gated = {k: c for k, c in cells.items() if not k.endswith(":T0")}
    if not any(c["n"] >= 50 for c in gated.values()):
        return "A. NOT_ENOUGH_DATA"
    passing = [k for k, c in gated.items() if c["gate"]["passes_review"]]
    if not passing:
        return "B. NO_VALID_ENTRY_FAMILY"
    return "C. ENTRY_ADVISOR_CANDIDATES_FOUND"


def verdict_at(rep: Replay, series: Series, sample_ms: int, results: dict[str, Any] | None) -> dict[str, Any]:
    """What the [진입분석] preview says for one second: a direction only when a family that
    passed the gate holds at that second; otherwise NO_TRADE with the reason."""
    passing = {k for k, c in (results or {}).get("cells", {}).items() if c.get("gate", {}).get("passes_review")}
    i = series.index_at(sample_ms)
    out = {"sample_ms": sample_ms, "verdict": "NO_TRADE", "reason": "NO_VALIDATED_ENTRY_FAMILY",
           "passing_cells": sorted(passing), "analysis_request_id": _rid((rep.session_id or "")[:8], sample_ms, "NONE", "NONE")}
    if i is None or not passing:
        return out
    return out


__all__ = ["build_events", "evaluate", "verdict_at", "gate", "advisor_verdict", "family_base",
           "direction_of", "timing_instants", "conditions_hold", "funding_cost_bp"]
