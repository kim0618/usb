"""Kline studies of contract R0.1: structure reaction (s3), entry cohorts (s5), exit families (s6),
hold states and entry context (s7). Everything is computed from a `StructureRun` plus the
derivatives series, and only from values known at or before the decision bar's close.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import stats as ST
from .bars import Derivatives
from .structure import StructureRun

HORIZONS = (5, 20, 60)
PRIMARY_H = 20
HMAX = 60
COST_BP = 11.25
DIST_BINS = (0.0, 0.5, 1.0, 2.0, 4.0, np.inf)
PLACEBO_OFFSET_ATR = 0.5
PLACEBO_CLEAR_ATR = 0.25
REARM_ATR = 1.0


# --------------------------------------------------------------------------- s3 touch episodes

@dataclass
class Episode:
    side: str            # "RES" | "SUP"
    kind: str            # "REAL" | "PLACEBO"
    bar: int             # touch bar index
    level: float
    level_id: int
    atr: float           # ATR at the bar before the touch (known when the level was in force)


def _touch_scan(run: StructureRun, side: str, prices: np.ndarray, ids: np.ndarray,
                kind: str) -> list[Episode]:
    """Touch episodes of a per-bar level series `prices[i]` (the level in force during bar i,
    i.e. published at close i-1). Re-arm per level id after a close beyond REARM_ATR on the
    near side."""
    eps: list[Episode] = []
    armed: dict[int, bool] = {}
    hi, lo, cl, atr = run.high, run.low, run.close, run.atr
    n = len(run)
    for i in range(1, n):
        L = prices[i]
        lid = int(ids[i])
        if not np.isfinite(L) or lid < 0 or not np.isfinite(atr[i - 1]):
            continue
        a = armed.get(lid, True)
        if side == "RES":
            if not a:
                if cl[i] < L - REARM_ATR * atr[i]:
                    armed[lid] = True
                continue
            if cl[i - 1] < L <= hi[i]:
                eps.append(Episode(side, kind, i, float(L), lid, float(atr[i - 1])))
                armed[lid] = False
        else:
            if not a:
                if cl[i] > L + REARM_ATR * atr[i]:
                    armed[lid] = True
                continue
            if cl[i - 1] > L >= lo[i]:
                eps.append(Episode(side, kind, i, float(L), lid, float(atr[i - 1])))
                armed[lid] = False
    return eps


def level_in_force(run: StructureRun, side: str) -> tuple[np.ndarray, np.ndarray]:
    """Level and id in force during bar i: the one published at close i-1 (and only inside the
    publish window)."""
    src = run.res if side == "RES" else run.sup
    sid = run.res_id if side == "RES" else run.sup_id
    p = np.full(len(run), np.nan)
    q = np.full(len(run), -1, dtype=np.int64)
    ok = run.published[:-1]
    p[1:][ok] = src[:-1][ok]
    q[1:][ok] = sid[:-1][ok]
    return p, q


def placebo_in_force(run: StructureRun, side: str, real: np.ndarray, ids: np.ndarray
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Placebo level per bar: each real level id's price shifted 0.5 ATR outward (ATR at the bar
    where the id first came into force), alive over the same bars as the real level."""
    sign = 1.0 if side == "RES" else -1.0
    offset: dict[int, float] = {}
    out = np.full(len(run), np.nan)
    for i in range(1, len(run)):
        lid = int(ids[i])
        if lid < 0 or not np.isfinite(real[i]):
            continue
        if lid not in offset:
            a = run.atr[i - 1]
            if not np.isfinite(a):
                continue
            offset[lid] = sign * PLACEBO_OFFSET_ATR * float(a)
        out[i] = real[i] + offset[lid]
    return out, ids.copy()


def _clear_of_levels(run: StructureRun, ep: Episode) -> bool:
    lv = run.levels[ep.bar - 1] if ep.bar >= 1 else ()
    return all(abs(p - ep.level) > PLACEBO_CLEAR_ATR * ep.atr for p in lv)


def episodes(run: StructureRun) -> list[Episode]:
    out: list[Episode] = []
    for side in ("RES", "SUP"):
        real, ids = level_in_force(run, side)
        out += _touch_scan(run, side, real, ids, "REAL")
        pl, pids = placebo_in_force(run, side, real, ids)
        out += [e for e in _touch_scan(run, side, pl, pids, "PLACEBO") if _clear_of_levels(run, e)]
    return sorted(out, key=lambda e: (e.bar, e.side, e.kind))


def classify(run: StructureRun, ep: Episode, h: int) -> dict | None:
    """Reaction of one episode over the touch bar and the h bars after it. None if incomplete."""
    i = ep.bar
    if i + h >= len(run):
        return None
    cl, hi, lo = run.close, run.high, run.low
    seg = slice(i, i + h + 1)
    beyond = cl[seg] > ep.level if ep.side == "RES" else cl[seg] < ep.level
    broke = bool(beyond.any())
    latency = int(np.argmax(beyond)) if broke else None
    retest = None
    if broke:
        b = i + latency
        after = slice(b + 1, i + h + 1)
        retest = bool((lo[after] <= ep.level).any() if ep.side == "RES" else (hi[after] >= ep.level).any())
    ref = cl[i]
    fwd = slice(i + 1, i + h + 1)
    if ep.side == "RES":           # rejection direction is down
        mfe = (ref - lo[fwd].min()) / ref * 1e4
        mae = (hi[fwd].max() - ref) / ref * 1e4
        mfe_atr = (ref - lo[fwd].min()) / ep.atr
        mae_atr = (hi[fwd].max() - ref) / ep.atr
    else:
        mfe = (hi[fwd].max() - ref) / ref * 1e4
        mae = (ref - lo[fwd].min()) / ref * 1e4
        mfe_atr = (hi[fwd].max() - ref) / ep.atr
        mae_atr = (ref - lo[fwd].min()) / ep.atr
    return {"hold": not broke, "break": broke, "latency": latency, "retest": retest,
            "mfe_bp": float(mfe), "mae_bp": float(mae), "mfe_atr": float(mfe_atr),
            "mae_atr": float(mae_atr)}


def reaction_study(run: StructureRun, eps: list[Episode]) -> dict:
    out: dict = {}
    for side in ("RES", "SUP"):
        cell: dict = {}
        for h in HORIZONS:
            rows = {k: [] for k in ("REAL", "PLACEBO")}
            for e in eps:
                if e.side != side:
                    continue
                c = classify(run, e, h)
                if c is not None:
                    rows[e.kind].append((e.bar, c))
            res = {}
            for kind, rr in rows.items():
                cs = [c for _, c in rr]
                res[kind] = {
                    "n": len(cs), "gate": ST.gate(len(cs)),
                    "reaction_prob": ST.rate(np.array([c["hold"] for c in cs])),
                    "break_prob": ST.rate(np.array([c["break"] for c in cs])),
                    "retest_given_break": ST.rate(np.array([c["retest"] for c in cs if c["break"]])),
                    "median_mfe_bp": ST.median(np.array([c["mfe_bp"] for c in cs])),
                    "median_mae_bp": ST.median(np.array([c["mae_bp"] for c in cs])),
                    "median_mfe_atr": ST.median(np.array([c["mfe_atr"] for c in cs])),
                    "median_mae_atr": ST.median(np.array([c["mae_atr"] for c in cs])),
                    "median_break_latency_bars": ST.median(np.array(
                        [c["latency"] for c in cs if c["break"]], float)),
                }
            allrows = [(b, 1.0 if k == "REAL" else 0.0, float(c["hold"]))
                       for k, rr in rows.items() for b, c in rr]
            if allrows and res["REAL"]["n"] and res["PLACEBO"]["n"]:
                arr = np.array(allrows)
                diff = ST.mean_diff(arr[:, 2], arr[:, 1] == 1, arr[:, 1] == 0,
                                    (arr[:, 0] // HMAX).astype(int), f"{run.tf}|{side}|h{h}|react")
            else:
                diff = ST.summarize(float("nan"), np.array([]))
            res["real_minus_placebo_reaction"] = diff
            cell[f"h{h}"] = res
        out[side] = cell
    return out


def distance_bins(run: StructureRun) -> dict:
    """Share of published bars per distance bin (ATR units) and how often the level is touched
    within PRIMARY_H bars from there."""
    out: dict = {}
    n = len(run)
    for side, src in (("RES", run.res), ("SUP", run.sup)):
        d = (src - run.close) / run.atr if side == "RES" else (run.close - src) / run.atr
        ok = run.published & np.isfinite(d) & np.isfinite(run.atr)
        cells = []
        for a, b in zip(DIST_BINS[:-1], DIST_BINS[1:]):
            m = ok & (d >= a) & (d < b)
            idx = np.flatnonzero(m)
            idx = idx[idx + PRIMARY_H < n]
            touched = []
            for i in idx:
                L = src[i]
                seg = slice(i + 1, i + PRIMARY_H + 1)
                touched.append((run.high[seg] >= L).any() if side == "RES" else (run.low[seg] <= L).any())
            cells.append({"bin_atr": [a, None if np.isinf(b) else b],
                          "share_of_bars": ST.rate(m[ok]) if ok.any() else None,
                          "touch_within_h20": ST.rate(np.array(touched))})
        out[side] = cells
    return out


# --------------------------------------------------------------------------- s5/s6 cohorts, exits

@dataclass
class Trade:
    cohort: str          # FADE | BREAK
    side: str            # LONG | SHORT
    entry: int           # entry bar
    price: float
    target: float
    inval: float
    target_id: int
    atr: float
    level_side: str      # RES | SUP of the touched level
    exits: dict = field(default_factory=dict)


def _dir(side: str) -> int:
    return 1 if side == "LONG" else -1


def cohort_entries(run: StructureRun, eps: list[Episode]) -> list[Trade]:
    """FADE at a touch bar that closes back on the near side; BREAK at the first close beyond a
    level within the episode. Levels for the exits come from the snapshot at the entry close.
    One position per cohort x side at a time: an entry closes no later than HMAX bars after it."""
    n = len(run)
    raw: list[tuple[str, str, int, str]] = []
    for e in eps:
        if e.kind != "REAL":
            continue
        i = e.bar
        near = run.close[i] < e.level if e.side == "RES" else run.close[i] > e.level
        if near:
            raw.append(("FADE", "SHORT" if e.side == "RES" else "LONG", i, e.side))
        seg_end = min(n, i + HMAX + 1)
        for b in range(i, seg_end):
            beyond = run.close[b] > e.level if e.side == "RES" else run.close[b] < e.level
            if beyond:
                raw.append(("BREAK", "LONG" if e.side == "RES" else "SHORT", b, e.side))
                break
    raw.sort(key=lambda r: (r[2], r[0], r[1]))
    busy: dict[tuple[str, str], int] = {}
    out: list[Trade] = []
    for cohort, side, i, lvl_side in raw:
        key = (cohort, side)
        if busy.get(key, -1) >= i:
            continue
        if not run.published[i] or i + HMAX >= n:
            continue
        price = float(run.close[i])
        up, dn = run.res[i], run.sup[i]
        if side == "LONG":
            target, inval, tid = up, dn, int(run.res_id[i])
        else:
            target, inval, tid = dn, up, int(run.sup_id[i])
        if not (np.isfinite(target) and np.isfinite(inval)) or not np.isfinite(run.atr[i]):
            continue
        busy[key] = i + HMAX
        out.append(Trade(cohort, side, i, price, float(target), float(inval), tid,
                         float(run.atr[i]), lvl_side))
    return out


FAMILIES = ("A", "C", "S3", "F", "G", "H5", "H20", "H60", "I")


def simulate(run: StructureRun, t: Trade, der: Derivatives | None) -> dict:
    """Every kline exit family on one entry. Fills: target/TP/SL at the level price on touch;
    invalidation at the close of the bar that closes beyond. Same-bar conflicts resolve to the
    loss side."""
    d = _dir(t.side)
    E, T, V = t.price, t.target, t.inval
    hi, lo, cl = run.high, run.low, run.close
    last = t.entry + HMAX

    def touched(k: int, level: float, profit_side: bool) -> bool:
        # profit side for LONG = above
        up = (d == 1) == profit_side
        return hi[k] >= level if up else lo[k] <= level

    def closed_beyond(k: int, level: float) -> bool:
        return cl[k] < level if d == 1 else cl[k] > level

    res: dict[str, tuple[int, float, str]] = {}
    # A: target only
    for k in range(t.entry + 1, last + 1):
        if touched(k, T, True):
            res["A"] = (k, T, "TARGET"); break
    else:
        res["A"] = (last, float(cl[last]), "TIMEOUT")
    # C: invalidation only
    for k in range(t.entry + 1, last + 1):
        if closed_beyond(k, V):
            res["C"] = (k, float(cl[k]), "INVALID"); break
    else:
        res["C"] = (last, float(cl[last]), "TIMEOUT")
    # S3, F, G
    for fam in ("S3", "F", "G"):
        v = V
        mid = E + (T - E) / 2.0
        be_armed = False
        out = None
        for k in range(t.entry + 1, last + 1):
            if closed_beyond(k, v):
                out = (k, float(cl[k]), "INVALID" if v == V else "STOP_MOVED"); break
            if touched(k, T, True):
                out = (k, T, "TARGET"); break
            if fam == "F" and not be_armed and touched(k, mid, True):
                be_armed = True
                v = E
            if fam == "G":
                cand = run.sup[k] if d == 1 else run.res[k]
                if run.published[k] and np.isfinite(cand):
                    if d == 1 and v < cand < cl[k]:
                        v = float(cand)
                    if d == -1 and cl[k] < cand < v:
                        v = float(cand)
        res[fam] = out or (last, float(cl[last]), "TIMEOUT")
    # H: fixed time
    for h in (5, 20, 60):
        k = t.entry + h
        res[f"H{h}"] = (k, float(cl[k]), "TIME")
    # I: 1 ATR TP / SL, touch fills, loss side first
    tp, sl = E + d * t.atr, E - d * t.atr
    for k in range(t.entry + 1, last + 1):
        if touched(k, sl, False):
            res["I"] = (k, sl, "SL"); break
        if touched(k, tp, True):
            res["I"] = (k, tp, "TP"); break
    else:
        res["I"] = (last, float(cl[last]), "TIMEOUT")

    known = run.known_ms
    out: dict = {}
    for fam, (k, px, why) in res.items():
        gross = d * (px - E) / E * 1e4
        fund = 0.0
        if der is not None:
            fund = der.funding_between(int(known[t.entry]), int(known[k])) * 1e4 * d
        seg = slice(t.entry + 1, k + 1)
        mfe = (hi[seg].max() - E) / E * 1e4 if d == 1 else (E - lo[seg].min()) / E * 1e4
        mae = (E - lo[seg].min()) / E * 1e4 if d == 1 else (hi[seg].max() - E) / E * 1e4
        out[fam] = {"exit_bar": int(k), "why": why, "gross_bp": float(gross),
                    "net_bp": float(gross - COST_BP - fund), "funding_bp": float(fund),
                    "mfe_bp": float(mfe), "mae_bp": float(mae), "hold_bars": int(k - t.entry),
                    "opportunity_bp": float(mfe - gross)}
    return out


def family_table(trades: list[Trade]) -> dict:
    out: dict = {}
    for fam in FAMILIES:
        net = np.array([t.exits[fam]["net_bp"] for t in trades])
        row = {"n": len(trades), "gate": ST.gate(len(trades)),
               "mean_net_bp": ST.mean(net), "median_net_bp": ST.median(net),
               "p5_net_bp": ST.pct(net, 5), "win_rate": ST.rate(net > 0),
               "worst_net_bp": ST.finite_or_none(float(net.min())) if len(net) else None,
               "median_mfe_bp": ST.median(np.array([t.exits[fam]["mfe_bp"] for t in trades])),
               "median_mae_bp": ST.median(np.array([t.exits[fam]["mae_bp"] for t in trades])),
               "median_hold_bars": ST.median(np.array([t.exits[fam]["hold_bars"] for t in trades], float)),
               "median_opportunity_bp": ST.median(np.array([t.exits[fam]["opportunity_bp"] for t in trades])),
               "timeout_share": ST.rate(np.array([t.exits[fam]["why"] in ("TIMEOUT",) for t in trades]))}
        out[fam] = row
    return out


def paired(trades: list[Trade], a: str, b: str, label: str) -> dict:
    if not trades:
        return ST.summarize(float("nan"), np.array([]))
    diff = np.array([t.exits[a]["net_bp"] - t.exits[b]["net_bp"] for t in trades])
    blocks = np.array([t.entry // HMAX for t in trades])
    return ST.mean_ci(diff, blocks, label)


# --------------------------------------------------------------------------- s7 hold & context

def _oi_aligned(run: StructureRun, der: Derivatives, k: int, d: int) -> bool | None:
    if k < 1:
        return None
    a, b = der.oi_at(int(run.known_ms[k - 1])), der.oi_at(int(run.known_ms[k]))
    if not (np.isfinite(a) and np.isfinite(b)) or a == b:
        return None
    moved = d * (run.close[k] - run.close[k - 1])
    return bool(b > a and moved > 0)


def _funding_favours(der: Derivatives, at_ms: int, d: int) -> bool | None:
    r = der.last_funding(at_ms)
    if not np.isfinite(r) or r == 0:
        return None
    return bool((r < 0) if d == 1 else (r > 0))


def _basis_favours(run: StructureRun, der: Derivatives, k: int, d: int) -> bool | None:
    x = der.index_at(int(run.known_ms[k]))
    if not np.isfinite(x) or x == 0:
        return None
    basis = (run.close[k] - x) / x
    if basis == 0:
        return None
    return bool((basis < 0) if d == 1 else (basis > 0))


HOLD_STATES = ("a_invalidation_intact", "b_target_not_reached", "c_new_level_before_target",
               "d_oi_aligned", "e_funding_favours", "f_basis_favours")


def hold_rows(run: StructureRun, t: Trade, der: Derivatives | None) -> list[dict]:
    """One row per bar 1..HMAX-1 of the fixed-time path: states at that close, forward moves."""
    d = _dir(t.side)
    cl, hi, lo = run.close, run.high, run.low
    rows = []
    intact = True
    reached = False
    for s in range(1, HMAX):
        k = t.entry + s
        if (cl[k] < t.inval) if d == 1 else (cl[k] > t.inval):
            intact = False
        if (hi[k] >= t.target) if d == 1 else (lo[k] <= t.target):
            reached = True
        nxt = run.res[k] if d == 1 else run.sup[k]
        nid = run.res_id[k] if d == 1 else run.sup_id[k]
        new_level = bool(run.published[k] and np.isfinite(nxt) and nid != t.target_id
                         and ((cl[k] < nxt < t.target) if d == 1 else (t.target < nxt < cl[k])))
        f5 = min(k + 5, t.entry + HMAX)
        rows.append({
            "a_invalidation_intact": intact, "b_target_not_reached": not reached,
            "c_new_level_before_target": new_level,
            "d_oi_aligned": _oi_aligned(run, der, k, d) if der is not None else None,
            "e_funding_favours": _funding_favours(der, int(run.known_ms[k]), d) if der is not None else None,
            "f_basis_favours": _basis_favours(run, der, k, d) if der is not None else None,
            "fwd5_bp": d * (cl[f5] - cl[k]) / cl[k] * 1e4,
            "fwd_end_bp": d * (cl[t.entry + HMAX] - cl[k]) / cl[k] * 1e4,
        })
    return rows


ENTRY_FEATURES = ("rr_ge_1", "oi_up_last_bar", "funding_positive", "basis_positive")


def entry_features(run: StructureRun, t: Trade, der: Derivatives | None) -> dict:
    k = t.entry
    dt_ = abs(t.target - t.price)
    dv = abs(t.price - t.inval)
    feat = {"rr_ge_1": bool(dt_ >= dv) if dv > 0 else None,
            "target_dist_atr": dt_ / t.atr if t.atr > 0 else None}
    if der is not None and k >= 1:
        a, b = der.oi_at(int(run.known_ms[k - 1])), der.oi_at(int(run.known_ms[k]))
        feat["oi_up_last_bar"] = None if not (np.isfinite(a) and np.isfinite(b)) or a == b else bool(b > a)
        r = der.last_funding(int(run.known_ms[k]))
        feat["funding_positive"] = None if not np.isfinite(r) or r == 0 else bool(r > 0)
        x = der.index_at(int(run.known_ms[k]))
        feat["basis_positive"] = (None if not np.isfinite(x) or x == 0 or run.close[k] == x
                                  else bool(run.close[k] > x))
    else:
        feat.update({"oi_up_last_bar": None, "funding_positive": None, "basis_positive": None})
    return feat
