"""Q1 to Q6 of CONTRACT_WALL_LIFECYCLE_R1 on a replay. Every cell is reported with n, stage,
mean, median, p5, p95, a block-bootstrap CI and p, and Holm within the question's family."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import contract as K
from .outcomes import MIN_MS, Series, classify_reaction, rejection_sign
from .replay import ASK, BID, Level, Replay, near_far
from .stats import block_bootstrap, holm, quartile_bins, spearman_perm, summary


def survival_bin(ms: int) -> str:
    s = ms / 1000.0
    for (lo, hi), name in zip(K.SURVIVAL_BINS_S, K.SURVIVAL_BIN_NAMES):
        if lo <= s < hi:
            return name
    return K.SURVIVAL_BIN_NAMES[-1]


def _cell(values: list[float], times: list[int]) -> dict[str, Any]:
    out = summary(values)
    out.update(block_bootstrap(values, times))
    return out


def _outcomes(series: Series, level: Level, t_c: int) -> dict[str, Any]:
    sign = rejection_sign(level.side)
    near, far = near_far(level.side, level.bin_low, level.bin_high)
    out: dict[str, Any] = {"t_c": t_c}
    for h in K.OUTCOME_MIN:
        out[f"r{h}"] = series.return_bp(t_c, h * MIN_MS, sign)
    exc = series.excursions(t_c, K.PRIMARY_H_MIN * MIN_MS, sign)
    out["mfe15"], out["mae15"] = exc if exc else (None, None)
    out.update(classify_reaction(series, level.side, t_c, near, far))
    return out


def display_index(path_display: list[tuple], i: int, side_bin: float) -> int | None:
    for pos, (low, _n) in enumerate(path_display[i]):
        if low == side_bin:
            return pos
    return None


def cluster_of(series_display: tuple, side: str, bin_low: float, atr: float | None) -> bool:
    """Whether `bin_low` belongs to a chain of >= 2 display walls with gaps <= 0.5 x ATR14."""
    if atr is None or not series_display:
        return False
    lows = [low for low, _n in series_display]
    if bin_low not in lows:
        return False
    i = lows.index(bin_low)
    limit = K.CLUSTER_ATR_FRACTION * atr
    left = i > 0 and abs(lows[i] - lows[i - 1]) <= limit
    right = i + 1 < len(lows) and abs(lows[i + 1] - lows[i]) <= limit
    return left or right


def annotate(rep: Replay, series: Series) -> list[dict[str, Any]]:
    """Per level episode: the row plus touch outcomes, flow side, cluster flag, derivatives."""
    rows = []
    for level in rep.levels:
        row = level.row()
        if level.t_touch is not None:
            row["outcome"] = _outcomes(series, level, level.t_touch)
            row["flow_at_touch"] = series.flow_side(level.t_touch)
            i = series.index_at(level.t_touch)
            disp = rep.path.display_ask if level.side == ASK else rep.path.display_bid
            # one sample before the touch: the level's V2 bin is CLOSEd in the touch second
            # (1 bp floor), so at t_touch itself it is never in the display set (recorded
            # deviation from "at t_touch", same cause as Q2)
            row["cluster"] = cluster_of(disp[max(0, i - 1)], level.side, level.bin_low, series.atr_at(level.t_touch)) \
                if i is not None else False
            row["cluster_read_at"] = "sample before touch"
            row["survival_bin"] = survival_bin(level.survival_ms) if level.survival_ms is not None else None
            oi, oi_ch = series.oi_at(level.t_touch)
            row["oi_value"], row["oi_change_1h_pct"] = oi, oi_ch
            row["funding"] = series.funding_at(level.t_touch)
            row["basis_bp"] = series.basis_at(level.t_touch)
            row["atr14"] = series.atr_at(level.t_touch)
        rows.append(row)
    return rows


def _group(rows: list[dict], key, value="r15", pred=None) -> dict[str, dict[str, Any]]:
    cells: dict[str, tuple[list, list]] = {}
    for row in rows:
        if "outcome" not in row or (pred and not pred(row)):
            continue
        k = key(row)
        if k is None:
            continue
        v = row["outcome"].get(value)
        if v is None:
            continue
        vals, times = cells.setdefault(str(k), ([], []))
        vals.append(v)
        times.append(row["t_touch"])
    return {k: _cell(v, t) for k, (v, t) in sorted(cells.items())}


def _with_holm(cells: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    adj = holm({k: c.get("p") for k, c in cells.items()})
    for k, c in cells.items():
        c["holm_p"] = adj[k]
    return cells


def _reaction_rates(rows: list[dict], key, pred=None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        if "outcome" not in row or (pred and not pred(row)):
            continue
        k = key(row)
        r = row["outcome"].get("reaction")
        if k is None or r is None:
            continue
        cell = out.setdefault(str(k), {"n": 0, "CONTINUATION": 0, "REVERSAL": 0, "NEITHER": 0})
        cell["n"] += 1
        cell[r] += 1
    for cell in out.values():
        n = cell["n"]
        cell["continuation_rate"] = cell["CONTINUATION"] / n if n else None
        cell["reversal_rate"] = cell["REVERSAL"] / n if n else None
        cell["stage"] = K.stage(n)
    return dict(sorted(out.items()))


def q1_persistence(rows: list[dict]) -> dict[str, Any]:
    pre = _with_holm(_group(rows, lambda r: f"{r['side']}:{r['survival_bin']}"))
    rates = _reaction_rates(rows, lambda r: f"{r['side']}:{r['survival_bin']}")
    mfe = _group(rows, lambda r: f"{r['side']}:{r['survival_bin']}", value="mfe15")
    mae = _group(rows, lambda r: f"{r['side']}:{r['survival_bin']}", value="mae15")
    dist = {}
    for side in (ASK, BID):
        s = [r["survival_ms"] for r in rows if r["side"] == side and r.get("survival_ms") is not None]
        dist[side] = summary([v / 1000.0 for v in s])
        dist[side]["bins"] = {name: sum(1 for v in s if survival_bin(v) == name) for name in K.SURVIVAL_BIN_NAMES}
    return {"r15_by_survival_bin": pre, "reaction_by_survival_bin": rates, "mfe15": mfe, "mae15": mae,
            "survival_distribution_s": dist, "tests": len(pre)}


def q2_notional(rep: Replay, series: Series, rows: list[dict] | None = None) -> dict[str, Any]:
    """V2 bin episodes: size ratios over the whole window; and, for touched level episodes, the
    bin's pre-touch size ratio (last observation at or before the level's touch over the bin's
    OPEN notional) against the level's reaction, in terciles.

    Unit note (deviation recorded in the results): the contract named the V2 bin's own touch, but
    a V2 bin is CLOSEd in the very second the mid enters its bin (1 bp distance floor), so no V2
    bin is ever observed touched (0 in this window). The level episode (same bin, same second) is
    the touched unit; the bin supplies the notional observations. Terciles, rates, Spearman and
    the Holm family of 2 are as frozen."""
    bins = [b.row() for b in rep.bins]
    out: dict[str, Any] = {"n_bins": len(bins), "v2_bins_touched_by_their_own_rule": sum(1 for b in rep.bins if b.t_touch is not None)}
    for name in ("ratio_last_first", "ratio_max_first", "ratio_min_first", "observations"):
        out[name] = summary([b[name] for b in bins if b.get(name) is not None])
    index: dict[tuple[str, float], list] = {}
    for b in rep.bins:
        index.setdefault((b.side, b.bin_low), []).append(b)
    touched = []
    for row in rows or []:
        if "outcome" not in row or row["outcome"].get("reaction") is None:
            continue
        t = row["t_touch"]
        cands = [b for b in index.get((row["side"], row["bin_low"]), [])
                 if b.open_ms <= t and (b.close_ms is None or b.close_ms >= t - 2_000)]
        if not cands:
            continue
        b = max(cands, key=lambda x: x.open_ms)
        pre = [n for ts, n in b.notionals if ts <= t]
        first = b.notionals[0][1]
        if not pre or not first:
            continue
        touched.append({"side": row["side"], "ratio": pre[-1] / first, "reaction": row["outcome"]["reaction"],
                        "t": t, "r15": row["outcome"].get("r15"), "observations_pre_touch": len(pre)})
    out["touched_levels_with_bin"] = len(touched)
    out["pre_touch_observations"] = summary([t["observations_pre_touch"] for t in touched])
    terciles: dict[str, Any] = {}
    pvals: dict[str, float | None] = {}
    for side in (ASK, BID):
        sub = [t for t in touched if t["side"] == side]
        ratios = [t["ratio"] for t in sub]
        if len(ratios) < 6:
            terciles[side] = {"n": len(sub), "stage": K.stage(len(sub))}
            pvals[side] = None
            continue
        cut = [float(np.percentile(ratios, 100 / 3)), float(np.percentile(ratios, 200 / 3))]
        cells = {}
        for name, lo, hi in (("T1_low", -math.inf, cut[0]), ("T2_mid", cut[0], cut[1]), ("T3_high", cut[1], math.inf)):
            part = [t for t in sub if lo < t["ratio"] <= hi]
            n = len(part)
            cells[name] = {"n": n, "stage": K.stage(n),
                           "continuation_rate": sum(t["reaction"] == "CONTINUATION" for t in part) / n if n else None,
                           "reversal_rate": sum(t["reaction"] == "REVERSAL" for t in part) / n if n else None,
                           "r15": _cell([t["r15"] for t in part if t["r15"] is not None],
                                        [t["t"] for t in part if t["r15"] is not None])}
        sp = spearman_perm([t["ratio"] for t in sub], [1.0 if t["reaction"] == "CONTINUATION" else 0.0 for t in sub])
        terciles[side] = {"n": len(sub), "stage": K.stage(len(sub)), "cuts": cut, "cells": cells,
                          "spearman_ratio_vs_continuation": sp,
                          "ratio_changed_share": sum(1 for t in sub if abs(t["ratio"] - 1) > 1e-9) / len(sub)}
        pvals[side] = sp["p"]
    adj = holm(pvals)
    for side in terciles:
        terciles[side]["holm_p"] = adj.get(side)
    out["pre_touch_ratio_terciles"] = terciles
    out["unit_deviation"] = ("contract Q2 named the V2 bin's own touch; 0 such events exist by construction "
                             "(1 bp floor closes the bin in the touch second); the touched level joined to its bin's "
                             "notional observations is used instead; everything else as frozen")
    return out


def q3_end_class(rows: list[dict]) -> dict[str, Any]:
    dist = {}
    for side in (ASK, BID):
        dist[side] = {c: sum(1 for r in rows if r["side"] == side and r["end_class"] == c) for c in K.END_CLASSES}
    cells = _with_holm(_group(rows, lambda r: f"{r['side']}:{r['end_class']}"))
    rates = _reaction_rates(rows, lambda r: f"{r['side']}:{r['end_class']}")
    evicted = {side: sum(1 for r in rows if r["side"] == side and r.get("rank_evicted_v2_ms") is not None)
               for side in (ASK, BID)}
    return {"end_class_counts": dist, "r15_touched_by_end_class": cells,
            "reaction_touched_by_end_class": rates, "rank_evicted_v2_attribute": evicted}


def flow_class(row: dict) -> str | None:
    """A to D of CONTRACT section Q4 (R0.1 I-A..I-D, mirrored for BID)."""
    flow = row.get("flow_at_touch")
    if flow is None:
        return None
    into = "BUY" if row["side"] == ASK else "SELL"      # the side that pushes into the wall
    held = row["end_class"] == K.END_TOUCHED_PULLED and (row.get("survival_ms") or 0) >= 60_000
    consumed = row["end_class"] == K.END_CONSUMED
    if flow == into and held:
        return "A"
    if flow == into and consumed:
        return "B"
    if flow != into and held:
        return "C"
    return None


def q4_flow(rows: list[dict]) -> dict[str, Any]:
    touched = [r for r in rows if "outcome" in r]
    for r in touched:
        r["flow_class"] = flow_class(r)
    cells = _group(touched, lambda r: f"{r['side']}:{r['flow_class']}" if r.get("flow_class") else None)
    # class B's natural reading is through the wall: flip the sign for reporting
    for key, cell in cells.items():
        if key.endswith(":B"):
            cell["mean"] = -cell["mean"] if cell.get("mean") is not None else None
            cell["median"] = -cell["median"] if cell.get("median") is not None else None
            cell["ci95"] = [-cell["ci95"][1], -cell["ci95"][0]] if cell.get("ci95") else None
            cell["sign"] = "through the wall (+)"
        else:
            cell["sign"] = "away from the wall (+)"
    cells = _with_holm(cells)
    untouched_cancel = {side: sum(1 for r in rows if r["side"] == side and r["end_class"] == K.END_CANCEL_LIKE)
                        for side in (ASK, BID)}
    size_change = {}
    return {"r15_by_flow_class": cells, "class_D_cancel_like_without_touch": untouched_cancel,
            "flow_side_counts_at_touch": {s: {str(f): sum(1 for r in touched if r["side"] == s and r.get("flow_at_touch") == f)
                                             for f in ("BUY", "SELL", None)} for s in (ASK, BID)},
            "size_change": size_change}


def q5_next_wall(rep: Replay, series: Series) -> dict[str, Any]:
    events = []
    for level in rep.levels:
        if not level.display_qualified or level.end_ms is None or level.end_class not in (
                K.END_CONSUMED, K.END_TOUCHED_PULLED, K.END_CANCEL_LIKE):
            continue
        i = series.index_at(level.end_ms)
        if i is None or i == 0:
            continue
        disp = rep.path.display_ask if level.side == ASK else rep.path.display_bid
        before = disp[i - 1]
        if not before or before[0][0] != level.bin_low:
            continue                                   # not wall 1 at its end
        beyond = [low for low, _n in disp[i] if (low > level.bin_low if level.side == ASK else low < level.bin_low)]
        if not beyond:
            continue
        mid0 = series.mid_at(level.end_ms)
        if mid0 is None:
            continue
        w2 = beyond[0]
        w3 = beyond[1] if len(beyond) > 1 else None
        sign = -rejection_sign(level.side)             # continuation direction (through)
        near2 = w2 if level.side == ASK else w2 + K.BIN_WIDTH_USDT
        ev: dict[str, Any] = {"episode_id": level.episode_id, "side": level.side, "end_class": level.end_class,
                              "t": level.end_ms, "gap_1_2_bp": abs(near2 - level.price) / mid0 * 1e4}
        if w3 is not None:
            near3 = w3 if level.side == ASK else w3 + K.BIN_WIDTH_USDT
            ev["gap_2_3_bp"] = abs(near3 - near2) / mid0 * 1e4
        for h in (15, 60):
            reach2 = series.first_reach(level.end_ms, h * MIN_MS, near2, sign)
            ev[f"reach_w2_{h}"] = reach2 is not None if series.window(level.end_ms, h * MIN_MS) else None
            ev[f"time_to_w2_{h}_s"] = (reach2 - level.end_ms) / 1000 if reach2 else None
            if w3 is not None:
                near3 = w3 if level.side == ASK else w3 + K.BIN_WIDTH_USDT
                reach3 = series.first_reach(level.end_ms, h * MIN_MS, near3, sign)
                ev[f"reach_w3_{h}"] = reach3 is not None if series.window(level.end_ms, h * MIN_MS) else None
        back = mid0 * (1 - sign * K.MEANINGFUL_BP / 1e4)
        rev = series.first_reach(level.end_ms, 15 * MIN_MS, back, -sign)
        reach2_15 = series.first_reach(level.end_ms, 15 * MIN_MS, near2, sign)
        ev["intermediate_reversal_15"] = rev is not None and (reach2_15 is None or rev < reach2_15)
        exc = series.excursions(level.end_ms, 15 * MIN_MS, sign)
        ev["max_move_before_reversal_bp"] = exc[0] if exc else None
        events.append(ev)
    out: dict[str, Any] = {"events": len(events), "by_side": {}}
    pvals = {}
    for side in (ASK, BID):
        sub = [e for e in events if e["side"] == side]
        cell: dict[str, Any] = {"n": len(sub), "stage": K.stage(len(sub))}
        for h in (15, 60):
            known = [e for e in sub if e.get(f"reach_w2_{h}") is not None]
            cell[f"reach_w2_{h}_rate"] = sum(e[f"reach_w2_{h}"] for e in known) / len(known) if known else None
            known3 = [e for e in sub if e.get(f"reach_w3_{h}") is not None]
            cell[f"reach_w3_{h}_rate"] = sum(e[f"reach_w3_{h}"] for e in known3) / len(known3) if known3 else None
            cell[f"time_to_w2_{h}_s"] = summary([e[f"time_to_w2_{h}_s"] for e in sub if e.get(f"time_to_w2_{h}_s") is not None])
        cell["intermediate_reversal_15_rate"] = (sum(e["intermediate_reversal_15"] for e in sub) / len(sub)) if sub else None
        cell["max_move_before_reversal_bp"] = summary([e["max_move_before_reversal_bp"] for e in sub])
        cell["gap_1_2_bp"] = summary([e["gap_1_2_bp"] for e in sub])
        cell["gap_2_3_bp"] = summary([e["gap_2_3_bp"] for e in sub if "gap_2_3_bp" in e])
        known = [e for e in sub if e.get("reach_w2_15") is not None]
        cuts = quartile_bins([e["gap_1_2_bp"] for e in known])
        quart = {}
        if cuts:
            edges = [-math.inf] + cuts + [math.inf]
            for q in range(4):
                part = [e for e in known if edges[q] < e["gap_1_2_bp"] <= edges[q + 1]]
                quart[f"Q{q + 1}"] = {"n": len(part), "gap_hi": edges[q + 1] if q < 3 else None,
                                      "continuation_rate": sum(e["reach_w2_15"] for e in part) / len(part) if part else None}
        cell["continuation_by_gap_quartile"] = quart
        sp = spearman_perm([e["gap_1_2_bp"] for e in known], [1.0 if e["reach_w2_15"] else 0.0 for e in known])
        cell["spearman_gap_vs_continuation"] = sp
        pvals[side] = sp["p"]
        by_class = {}
        for c in (K.END_CONSUMED, K.END_TOUCHED_PULLED, K.END_CANCEL_LIKE):
            part = [e for e in known if e["end_class"] == c]
            by_class[c] = {"n": len(part), "reach_w2_15_rate": sum(e["reach_w2_15"] for e in part) / len(part) if part else None}
        cell["reach_w2_15_by_end_class"] = by_class
        out["by_side"][side] = cell
    adj = holm(pvals)
    for side in out["by_side"]:
        out["by_side"][side]["holm_p"] = adj.get(side)
    return out


def q6_cluster(rows: list[dict]) -> dict[str, Any]:
    touched = [r for r in rows if "outcome" in r and r.get("display_qualified")]
    out: dict[str, Any] = {}
    pvals: dict[str, float | None] = {}
    for side in (ASK, BID):
        cell: dict[str, Any] = {}
        for label, pred in (("cluster", lambda r: r.get("cluster")), ("single", lambda r: not r.get("cluster"))):
            part = [r for r in touched if r["side"] == side and pred(r)]
            rates = _reaction_rates(part, lambda r: "all")
            cell[label] = {"n": len(part), "stage": K.stage(len(part)),
                           "reaction": rates.get("all"),
                           "r15": _cell([r["outcome"]["r15"] for r in part if r["outcome"].get("r15") is not None],
                                        [r["t_touch"] for r in part if r["outcome"].get("r15") is not None]),
                           "mfe15": summary([r["outcome"]["mfe15"] for r in part]),
                           "mae15": summary([r["outcome"]["mae15"] for r in part])}
        # difference cluster - single on r15, bootstrap on the pooled labelled sample
        a = [r for r in touched if r["side"] == side and r.get("cluster") and r["outcome"].get("r15") is not None]
        b = [r for r in touched if r["side"] == side and not r.get("cluster") and r["outcome"].get("r15") is not None]
        if len(a) >= 2 and len(b) >= 2:
            diff = _diff_bootstrap([r["outcome"]["r15"] for r in a], [r["t_touch"] for r in a],
                                   [r["outcome"]["r15"] for r in b], [r["t_touch"] for r in b])
        else:
            diff = {"n_a": len(a), "n_b": len(b), "p": None}
        cell["r15_cluster_minus_single"] = diff
        pvals[f"{side}:r15"] = diff.get("p")
        out[side] = cell
    adj = holm(pvals)
    for side in (ASK, BID):
        out[side]["holm_p_r15"] = adj.get(f"{side}:r15")
    out["unit_deviation"] = ("cluster membership is read one sample before the touch, because the touched "
                             "level's V2 bin is CLOSEd in the touch second (1 bp floor); rule and 0.5 x ATR14 as frozen")
    return out


def _diff_bootstrap(va, ta, vb, tb) -> dict[str, Any]:
    rng = np.random.default_rng(K.SEED)
    ba = block_bootstrap(va, ta)
    bb = block_bootstrap(vb, tb)
    # approximate the difference's bootstrap by resampling both groups independently per block
    def draws(values, times):
        pairs = sorted(zip(times, values))
        blocks: dict[int, list[float]] = {}
        for t, v in pairs:
            blocks.setdefault(int(t // K.BLOCK_MS), []).append(v)
        arrays = [np.asarray(blocks[k]) for k in sorted(blocks)]
        out = np.empty(K.BOOTSTRAP_N)
        for i in range(K.BOOTSTRAP_N):
            pick = rng.integers(0, len(arrays), size=len(arrays))
            out[i] = np.concatenate([arrays[j] for j in pick]).mean()
        return out
    d = draws(va, ta) - draws(vb, tb)
    p = min(1.0, 2 * min(float(np.mean(d <= 0)), float(np.mean(d >= 0))))
    return {"n_a": len(va), "n_b": len(vb), "mean_a": ba["mean"], "mean_b": bb["mean"],
            "diff": ba["mean"] - bb["mean"], "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))],
            "p": p}


def derivatives_by_class(rows: list[dict]) -> dict[str, Any]:
    out = {}
    touched = [r for r in rows if "outcome" in r]
    for side in (ASK, BID):
        for c in K.END_CLASSES:
            part = [r for r in touched if r["side"] == side and r["end_class"] == c]
            if len(part) < 20:
                out[f"{side}:{c}"] = {"n": len(part), "stage": K.stage(len(part)), "note": "n < 20: not interpreted"}
                continue
            out[f"{side}:{c}"] = {"n": len(part), "stage": K.stage(len(part)),
                                  "oi_change_1h_pct": summary([r.get("oi_change_1h_pct") for r in part]),
                                  "funding": summary([r.get("funding") for r in part]),
                                  "basis_bp": summary([r.get("basis_bp") for r in part])}
    return out


def wall_verdict(q1, q3, q4, q6) -> str:
    cells: list[dict] = []
    cells += list(q1["r15_by_survival_bin"].values())
    cells += list(q3["r15_touched_by_end_class"].values())
    cells += list(q4["r15_by_flow_class"].values())
    eligible = [c for c in cells if c.get("n", 0) >= 50]
    if not eligible and all(q6[side]["cluster"]["n"] < 50 and q6[side]["single"]["n"] < 50 for side in (ASK, BID)):
        return "A. NOT_ENOUGH_DATA"
    for c in eligible:
        if c.get("holm_p") is not None and c["holm_p"] <= 0.05 and (c.get("mean") or 0) > K.RT_COST_BP:
            return "C. WALL_EDGE_CANDIDATES_FOUND"
    return "B. WALL_INTERACTION_R1_PASS"


__all__ = ["annotate", "q1_persistence", "q2_notional", "q3_end_class", "q4_flow", "q5_next_wall",
           "q6_cluster", "derivatives_by_class", "wall_verdict", "survival_bin", "cluster_of", "flow_class"]
