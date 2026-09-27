"""F0 outcome statistics and per-hypothesis gates, exactly as declared.

Inputs are the baseline rows (one per resolved symbol-session) with their session index,
symbol, gross return, MFE, MAE and hypothesis masks. Every threshold comes from ``Params``.
Percentiles use numpy's 'linear' method; the bootstrap draw matrix is built once and shared by
every hypothesis and endpoint.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_f0_regular_after.params import Params, top_pct_count

NAN = float("nan")


def _f(x) -> float:
    return float(x) if np.isfinite(x) else NAN


def metrics(g: np.ndarray, mfe: np.ndarray, mae: np.ndarray, p: Params) -> dict:
    n = int(g.size)
    out = {"N": n}
    if n == 0:
        return out
    out.update(mean=_f(g.mean()), median=_f(np.median(g)), win_rate=_f((g > 0).mean()),
               std=_f(g.std(ddof=1)) if n > 1 else NAN)
    for q in (5, 25, 75, 95):
        out[f"p{q}"] = _f(np.percentile(g, q))
    for k in p.positive_tails:
        out[f"P(r>={k})"] = _f((g >= k).mean())
    for k in p.negative_tails:
        out[f"P(r<={k})"] = _f((g <= k).mean())
    out.update(mfe_mean=_f(mfe.mean()), mfe_median=_f(np.median(mfe)),
               mae_mean=_f(mae.mean()), mae_median=_f(np.median(mae)))
    for k in p.mfe_thresholds:
        out[f"P(MFE>={k})"] = _f((mfe >= k).mean())
    for q in (5, 25, 50, 75, 95):
        out[f"mae_p{q}"] = _f(np.percentile(mae, q))
    return out


def tail_rate(mfe: np.ndarray, p: Params) -> float:
    return _f((mfe >= p.primary_mfe).mean()) if mfe.size else NAN


def downside_rate(g: np.ndarray, p: Params) -> float:
    return _f((g <= p.downside_threshold).mean()) if g.size else NAN


def lifts(gH, mH, gB, mB, p: Params) -> tuple[float, float]:
    """(mean lift, tail lift) of the H rows against the baseline rows."""
    if gH.size == 0 or gB.size == 0:
        return NAN, NAN
    return _f(gH.mean() - gB.mean()), _f(tail_rate(mH, p) - tail_rate(mB, p))


# -- bootstrap ------------------------------------------------------------------------------------

def draw_counts(p: Params, sessions: int) -> np.ndarray:
    """(iterations, sessions) multiplicity matrix of the declared draw."""
    draws = np.random.default_rng(p.bootstrap_seed).integers(0, sessions, size=(p.bootstrap_iterations, sessions))
    counts = np.zeros((p.bootstrap_iterations, sessions), dtype=np.int64)
    np.add.at(counts, (np.repeat(np.arange(p.bootstrap_iterations), sessions), draws.ravel()), 1)
    return counts


def _per_session(day: np.ndarray, values: np.ndarray, sessions: int) -> np.ndarray:
    return np.bincount(day, weights=values, minlength=sessions)


def bootstrap(counts: np.ndarray, dayH, gH, mH, dayB, gB, mB, p: Params) -> dict:
    s = counts.shape[1]
    W = counts.astype(np.float64)
    nH = W @ _per_session(dayH, np.ones(gH.size), s)
    nB = W @ _per_session(dayB, np.ones(gB.size), s)
    with np.errstate(invalid="ignore", divide="ignore"):
        tH = (W @ _per_session(dayH, (mH >= p.primary_mfe).astype(float), s)) / nH
        tB = (W @ _per_session(dayB, (mB >= p.primary_mfe).astype(float), s)) / nB
        rH = (W @ _per_session(dayH, gH, s)) / nH
        rB = (W @ _per_session(dayB, gB, s)) / nB
    tail, mean = tH - tB, rH - rB
    out = {"iterations": int(counts.shape[0]), "undefined_replicates": int(np.count_nonzero(~np.isfinite(tail)))}
    for name, rep in (("tail_lift", tail), ("mean_lift", mean)):
        rep = rep[np.isfinite(rep)]
        if rep.size == 0:
            out[name] = None
            continue
        lo99, hi99, lo95, hi95 = np.percentile(rep, [0.5, 99.5, 2.5, 97.5])
        out[name] = {"ci99": [float(lo99), float(hi99)], "ci95": [float(lo95), float(hi95)],
                     "ci99_half_width": float((hi99 - lo99) / 2)}
    return out


# -- robustness ------------------------------------------------------------------------------------

def removal_order(sym: np.ndarray, day: np.ndarray, g: np.ndarray) -> np.ndarray:
    """H trade positions with gross > 0, by gross descending, ties symbol then session ascending."""
    pos = np.flatnonzero(g > 0)
    return pos[np.lexsort((day[pos], sym[pos], -g[pos]))]


@dataclass
class Group:
    sym: np.ndarray
    day: np.ndarray
    g: np.ndarray
    mfe: np.ndarray
    mae: np.ndarray
    key: np.ndarray       # row id shared with the baseline


def extreme_removal(H: Group, B: Group, p: Params) -> dict:
    order = removal_order(H.sym, H.day, H.g)
    out = {}
    for label, k in (("original", 0), ("top1", 1), ("top5", 5), ("top1pct", top_pct_count(p, H.g.size))):
        drop_keys = set(H.key[order[:k]].tolist())
        kh = ~np.isin(H.key, list(drop_keys)) if drop_keys else np.ones(H.g.size, bool)
        kb = ~np.isin(B.key, list(drop_keys)) if drop_keys else np.ones(B.g.size, bool)
        ml, tl = lifts(H.g[kh], H.mfe[kh], B.g[kb], B.mfe[kb], p)
        out[label] = {"removed": int(min(k, order.size)), "N": int(kh.sum()),
                      "mean": _f(H.g[kh].mean()) if kh.any() else NAN,
                      "mean_lift": ml, "tail_rate": tail_rate(H.mfe[kh], p), "tail_lift": tl}
    after = out["top1pct"]
    out["gate"] = bool(np.isfinite(after["mean_lift"]) and after["mean_lift"] > 0
                       and np.isfinite(after["tail_lift"]) and after["tail_lift"] > 0)
    return out


def concentration(H: Group, B: Group, p: Params) -> dict:
    net = H.g - p.cost_primary / 10000.0
    tickers, inverse = np.unique(H.sym, return_inverse=True)
    sums = np.bincount(inverse, weights=net, minlength=tickers.size) if tickers.size else np.array([])
    positive = np.clip(sums, 0, None)
    total = float(positive.sum())
    order = np.lexsort((tickers, -sums)) if tickers.size else np.array([], dtype=int)
    out = {"basis": f"net at {p.cost_primary} bp COST STRESS ASSUMPTION", "unique_symbols": int(tickers.size),
           "positive_sum": total}
    if total <= 0:
        out.update(top1_share=NAN, top5_share=NAN, top10_share=NAN, hhi=NAN, gate=False,
                   reason="no positive ticker sum")
    else:
        shares = positive[order] / total
        out.update(top1_share=float(shares[:1].sum()), top5_share=float(shares[:5].sum()),
                   top10_share=float(shares[:10].sum()), hhi=float((shares ** 2).sum()),
                   top_tickers=[[str(tickers[i]), float(sums[i])] for i in order[:10]])
    leave = set(tickers[order[:p.conc_leave]].tolist())
    kh, kb = ~np.isin(H.sym, list(leave)), ~np.isin(B.sym, list(leave))
    ml, _ = lifts(H.g[kh], H.mfe[kh], B.g[kb], B.mfe[kb], p)
    out["leave_top10"] = {"tickers": sorted(leave), "N": int(kh.sum()), "mean_lift": ml}
    if total > 0:
        out["gate"] = bool(out["top1_share"] <= p.conc_top1 and out["top5_share"] <= p.conc_top5
                           and np.isfinite(ml) and ml > 0)
    return out


def downside(H: Group, B: Group, p: Params) -> dict:
    out = {"H": {f"P(r<={k})": _f((H.g <= k).mean()) if H.g.size else NAN for k in p.negative_tails},
           "baseline": {f"P(r<={k})": _f((B.g <= k).mean()) for k in p.negative_tails}}
    ph, pb = downside_rate(H.g, p), downside_rate(B.g, p)
    worse = _f(ph - pb) if H.g.size else NAN
    ratio = _f(ph / pb) if pb > 0 else (float("inf") if ph > 0 else NAN)
    fail = (np.isfinite(worse) and worse >= p.downside_abs) or (pb > 0 and ph > p.downside_ratio * pb) or (pb == 0 and ph > 0)
    out.update(primary_metric=f"P(r<={p.downside_threshold})", H_rate=ph, baseline_rate=pb,
               absolute_worsening=worse, ratio=ratio if np.isfinite(ratio) else str(ratio),
               gate=bool(H.g.size and not fail))
    out["mae_H"] = {f"p{q}": _f(np.percentile(H.mae, q)) for q in (5, 25, 50, 75, 95)} if H.g.size else {}
    out["mae_baseline"] = {f"p{q}": _f(np.percentile(B.mae, q)) for q in (5, 25, 50, 75, 95)}
    return out


def time_consistency(H: Group, B: Group, p: Params, sessions: list[str]) -> dict:
    blocks = np.array_split(np.arange(len(sessions)), p.blocks)
    rows, pos_mean, pos_tail = [], 0, 0
    for i, idx in enumerate(blocks):
        kh, kb = np.isin(H.day, idx), np.isin(B.day, idx)
        ml, tl = lifts(H.g[kh], H.mfe[kh], B.g[kb], B.mfe[kb], p)
        row = {"block": i + 1, "sessions": [sessions[idx[0]], sessions[idx[-1]]], "N": int(kh.sum()),
               "mean": _f(H.g[kh].mean()) if kh.any() else NAN, "baseline_mean": _f(B.g[kb].mean()) if kb.any() else NAN,
               "mean_lift": ml, "median": _f(np.median(H.g[kh])) if kh.any() else NAN,
               "win_rate": _f((H.g[kh] > 0).mean()) if kh.any() else NAN,
               "tail_rate": tail_rate(H.mfe[kh], p), "baseline_tail_rate": tail_rate(B.mfe[kb], p), "tail_lift": tl}
        pos_mean += bool(np.isfinite(ml) and ml > 0)
        pos_tail += bool(np.isfinite(tl) and tl > 0)
        rows.append(row)
    return {"blocks": rows, "positive_mean_lift_blocks": pos_mean, "positive_tail_lift_blocks": pos_tail,
            "gate": bool(pos_mean >= p.blocks_mean and pos_tail >= p.blocks_tail)}


def cost_stress(H: Group, p: Params) -> dict:
    out = {"label": "COST STRESS ASSUMPTION (no quote/spread data; not a cost estimate)",
           "gross_mean": _f(H.g.mean()) if H.g.size else NAN}
    for bp in p.cost_grid:
        net = H.g - bp / 10000.0
        out[f"{bp}bp"] = {"net_mean": _f(net.mean()) if net.size else NAN,
                          "net_win_rate": _f((net > 0).mean()) if net.size else NAN}
    be = _f(10000.0 * H.g.mean()) if H.g.size else NAN
    out["break_even_cost_bp"] = be
    out["primary_bp"] = p.cost_primary
    out["gate"] = bool(np.isfinite(be) and be >= p.break_even_min)
    return out


# -- per-hypothesis gate evaluation ---------------------------------------------------------------

def evaluate_hypothesis(H: Group, B: Group, counts: np.ndarray, p: Params, sessions: list[str]) -> dict:
    n_sym, n_ses = int(np.unique(H.sym).size), int(np.unique(H.day).size)
    sample_ok = H.g.size >= p.sample["trades"] and n_sym >= p.sample["symbols"] and n_ses >= p.sample["sessions"]
    res = {"N": int(H.g.size), "unique_symbols": n_sym, "unique_sessions": n_ses,
           "sample_gate": "PASS" if sample_ok else "INSUFFICIENT_SAMPLE",
           "metrics": metrics(H.g, H.mfe, H.mae, p)}
    if H.g.size == 0:
        res["gates"] = {k: "N/A" for k in ("Statistical", "Tail", "Mean/Median", "Downside", "Extreme",
                                            "Concentration", "Time Consistency", "Cost")}
        res["gates"]["Sample"] = "INSUFFICIENT_SAMPLE"
        return res
    pH, pB = tail_rate(H.mfe, p), tail_rate(B.mfe, p)
    abs_lift = pH - pB
    rel = pH / pB if pB > 0 else (float("inf") if pH > 0 else NAN)
    ml = _f(H.g.mean() - B.g.mean())
    boot = bootstrap(counts, H.day, H.g, H.mfe, B.day, B.g, B.mfe, p)
    tl = boot["tail_lift"]
    stat_pass = bool(tl and tl["ci99"][0] > 0)
    tail_point = bool(abs_lift >= p.tail_abs and rel >= p.tail_rel)   # NaN (0/0) fails, inf passes
    median = float(np.median(H.g))
    mm = bool(ml > 0 and median >= p.median_floor)
    res.update(primary_tail={"H_rate": pH, "baseline_rate": pB, "absolute_lift": abs_lift,
                             "relative_lift": rel if np.isfinite(rel) else str(rel)},
               mean_lift=ml, median=median, bootstrap=boot,
               extreme_removal=extreme_removal(H, B, p), concentration=concentration(H, B, p),
               downside=downside(H, B, p), time_consistency=time_consistency(H, B, p, sessions),
               cost_stress=cost_stress(H, p))
    underpowered_width = bool(tl and tl["ci99_half_width"] >= p.underpowered_half_width)
    gates = {
        "Sample": "PASS" if sample_ok else "INSUFFICIENT_SAMPLE",
        "Statistical": "PASS" if stat_pass else ("UNDERPOWERED" if underpowered_width else "FAIL"),
        "Tail": "PASS" if tail_point else "FAIL",
        "Mean/Median": "PASS" if mm else "FAIL",
        "Downside": "PASS" if res["downside"]["gate"] else "FAIL",
        "Extreme": "PASS" if res["extreme_removal"]["gate"] else "FAIL",
        "Concentration": "PASS" if res["concentration"]["gate"] else "FAIL",
        "Time Consistency": "PASS" if res["time_consistency"]["gate"] else "FAIL",
        "Cost": "PASS" if res["cost_stress"]["gate"] else "FAIL",
    }
    res["gates"] = gates
    return res


def bucket_edges(values: np.ndarray, p: Params) -> list[float]:
    x = values[np.isfinite(values)]
    return [float(e) for e in np.quantile(x, p.quantiles)] if x.size else []


def bucket_of(values: np.ndarray, edges: list[float]) -> np.ndarray:
    out = np.searchsorted(np.asarray(edges), values, side="right") + 1
    return np.where(np.isfinite(values), out, 0)
