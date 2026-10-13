"""PTM-R1 V3 historical study, TRAIN only (contract CONTRACT_PTM_R1_V3_TRAIN_STUDY.md).

Execution: setup at boundary t -> entry at open[t+1]. Time exits fill at the open of the exit
minute; stops fill at the nearest level or the bar open if already beyond (adverse first inside a
bar). Costs: fee 5.0 bp/side on entry and exit notional, BASE spread, actual funding settlements.

Run: python -m app.crypto.research.ptm_r1.r1_train [out.json]  (from backend/)
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import data, v3
from .states import MAX_HOLD_MIN, next_true

DOCS = data.ROOT / "docs/crypto/personal_trading_method_r1"
CONTRACT = DOCS / "CONTRACT_PTM_R1_V3_TRAIN_STUDY.md"
CONTRACT_V3 = DOCS / "CONTRACT_PTM_R1_V3.md"
OUT = data.ROOT / "data/research/crypto/ptm_r1/r1_train_v1.json"

FEE_SIDE = 0.0005
BASE_RT = 1.184e-6
STRESS_FEE_SIDE = 0.00055
STRESS_RT = 2.537e-4
MMR = 0.004
LEVERAGES = {"L20": 20, "L10": 10}
HORIZONS_H = (1, 4, 8, 12, 24, 48)
SEED = 20261013
N_BOOT = 2000
BLOCK_DAYS = 7
SHIFT_DAYS = (7, 14, 30)
STUDIED = ("F1", "F2", "F3")
DIR_NAME = {1: "LONG", -1: "SHORT"}


def s_cat(lev: int) -> float:
    return 0.40 * (1.0 / lev - MMR)


def policy_stops(lev: int) -> dict[str, list[float]]:
    c = s_cat(lev)
    return {"A1": [0.005, c], "A2": [0.010, c], "A3": [0.020, c], "B": [c], "C": [c]}


# --------------------------------------------------------------------------- market


@dataclass
class Market:
    px: data.Prices
    fund_idx: np.ndarray       # minute index of each settlement
    fund_rate: np.ndarray

    @property
    def last(self) -> int:
        return len(self.px.open) - 1


def make_market(px: data.Prices, funding: list[tuple[int, float]]) -> Market:
    idx = np.array([(t - data.TRAIN_START_MS) // data.MINUTE_MS for t, _ in funding], dtype=np.int64)
    rate = np.array([r for _, r in funding], dtype=float)
    return Market(px=px, fund_idx=idx, fund_rate=rate)


# --------------------------------------------------------------------------- one trade


def simulate(mk: Market, e: int, dirn: int, x_time: int, stops: list[float],
             fee_side: float = FEE_SIDE, spread_rt: float = BASE_RT, cat: float | None = None) -> dict:
    """One trade from open[e]. `x_time` = minute whose open is the time exit (> e)."""
    if not (e < x_time <= mk.last):
        raise ValueError("bad window")
    px = mk.px
    p0 = px.open[e]
    sl = slice(e, x_time)
    adv_px = px.low[sl] if dirn == 1 else px.high[sl]
    fav_px = px.high[sl] if dirn == 1 else px.low[sl]
    r_adv = dirn * (adv_px / p0 - 1.0)
    r_fav = dirn * (fav_px / p0 - 1.0)
    r_open = dirn * (px.open[sl] / p0 - 1.0)
    kind = "TIME"
    ambiguous = False
    if stops:
        s_near = min(stops)
        hit = np.flatnonzero(r_adv <= -s_near)
    else:
        hit = np.zeros(0, dtype=np.int64)
    if len(hit):
        k = int(hit[0])
        gross = min(float(r_open[k]), -s_near)
        exit_bar = e + k
        kind = "CATASTROPHIC" if cat is not None and s_near == cat else "FIXED_STOP"
        fav_before = float(r_fav[:k].max()) if k > 0 else 0.0
        adv_before = float(r_adv[:k].min()) if k > 0 else 0.0
        mfe = max(0.0, fav_before)
        mae = max(0.0, -adv_before, -gross)
        ambiguous = bool(r_fav[k] > mfe)
        t_mfe = int(np.argmax(r_fav[:k])) if k > 0 and fav_before > 0 else 0
        t_mae = k
        if k > 0 and -adv_before > -gross:
            t_mae = int(np.argmin(r_adv[:k]))
        hold = k                                           # minutes from entry to the stop bar open
    else:
        exit_bar = x_time
        gross = float(dirn * (px.open[x_time] / p0 - 1.0))
        mfe = max(0.0, float(r_fav.max()), gross)
        mae = max(0.0, float(-r_adv.min()), -gross)
        t_mfe = int(np.argmax(r_fav)) if mfe > 0 else 0
        t_mae = int(np.argmin(r_adv)) if mae > 0 else 0
        hold = x_time - e
    ratio = 1.0 + dirn * gross                             # exit price / entry price
    fee = fee_side * (1.0 + ratio)
    a, b = np.searchsorted(mk.fund_idx, [e, exit_bar + 1])
    funding = float(np.sum(dirn * mk.fund_rate[a:b] * px.open[mk.fund_idx[a:b]] / p0))
    net = gross - fee - spread_rt - funding
    return {"e": e, "dir": dirn, "exit_bar": int(exit_bar), "kind": kind, "gross": gross,
            "fee": fee, "spread": spread_rt, "funding": funding, "cost": fee + spread_rt + funding,
            "net": net, "mfe": mfe, "mae": mae, "t_mfe": t_mfe, "t_mae": t_mae, "hold": int(hold),
            "settlements": int(b - a), "ambiguous": ambiguous}


def path_arrays(mk: Market, e: int, dirn: int, end: int, start: int | None = None
                ) -> tuple[np.ndarray, np.ndarray]:
    """r_adv, r_fav relative to the entry open[e], over bars start (default e) .. end-1."""
    p0 = mk.px.open[e]
    a = e if start is None else start
    adv = mk.px.low[a:end] if dirn == 1 else mk.px.high[a:end]
    fav = mk.px.high[a:end] if dirn == 1 else mk.px.low[a:end]
    return dirn * (adv / p0 - 1.0), dirn * (fav / p0 - 1.0)


def recovery(r_adv: np.ndarray, r_fav: np.ndarray, depth: float, be: float) -> dict:
    """Did the adverse excursion reach `depth` (strictly below entry for 0), and did a later bar
    print the entry price (gross) / the net break-even?"""
    reached = np.flatnonzero(r_adv < 0) if depth == 0 else np.flatnonzero(r_adv <= -depth)
    if len(reached) == 0:
        return {"reached": False, "gross": False, "net": False}
    k = int(reached[0])
    later = r_fav[k + 1:]
    return {"reached": True, "gross": bool((later >= 0).any()), "net": bool((later >= be).any())}


# --------------------------------------------------------------------------- statistics


def _q(v) -> dict | None:
    a = np.asarray(v, dtype=float)
    if len(a) == 0:
        return None
    return {"n": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)),
            "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90))}


def pf(net: np.ndarray) -> float | None:
    pos, neg = net[net > 0].sum(), -net[net < 0].sum()
    if neg == 0:
        return None if pos == 0 else math.inf
    return float(pos / neg)


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    g = np.array([t["gross"] for t in trades])
    nt = np.array([t["net"] for t in trades])
    kinds: dict[str, int] = {}
    for t in trades:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    mfe = np.array([t["mfe"] for t in trades])
    gb = mfe - g
    gbp = gb[mfe > 0] / mfe[mfe > 0]
    return {"n": len(trades), "gross_mean": float(g.mean()), "gross_median": float(np.median(g)),
            "net_mean": float(nt.mean()), "net_median": float(np.median(nt)),
            "net_pos_share": float((nt > 0).mean()), "pf": pf(nt),
            "mfe_mean": float(mfe.mean()), "mfe_median": float(np.median(mfe)),
            "mae_mean": float(np.mean([t["mae"] for t in trades])),
            "mae_median": float(np.median([t["mae"] for t in trades])),
            "hold_min_mean": float(np.mean([t["hold"] for t in trades])),
            "hold_min_median": float(np.median([t["hold"] for t in trades])),
            "funding_mean": float(np.mean([t["funding"] for t in trades])),
            "cost_mean": float(np.mean([t["cost"] for t in trades])),
            "catastrophic_rate": kinds.get("CATASTROPHIC", 0) / len(trades),
            "exit_kinds": dict(sorted(kinds.items())),
            "giveback_abs_mean": float(gb.mean()), "giveback_abs_median": float(np.median(gb)),
            "giveback_pct_median": float(np.median(gbp)) if len(gbp) else None,
            "giveback_pct_mean": float(gbp.mean()) if len(gbp) else None,
            "ambiguous_stop_bars": int(sum(t["ambiguous"] for t in trades))}


class Bootstrap:
    """Moving-block bootstrap over consecutive UTC days (ratio estimator)."""

    def __init__(self, n_days: int, block: int = BLOCK_DAYS, reps: int = N_BOOT, seed: int = SEED):
        self.n_days, self.block = n_days, block
        nb = math.ceil(n_days / block)
        rng = np.random.default_rng(seed)
        self.starts = rng.integers(0, n_days - block + 1, size=(reps, nb))

    def _block_sums(self, per_day: np.ndarray) -> np.ndarray:
        c = np.concatenate(([0.0], np.cumsum(per_day)))
        return c[self.block:] - c[:-self.block]          # sum over [i, i+block)

    def ratio(self, days: np.ndarray, values: np.ndarray) -> np.ndarray:
        s = np.bincount(days, weights=values, minlength=self.n_days)
        k = np.bincount(days, minlength=self.n_days).astype(float)
        bs, bk = self._block_sums(s), self._block_sums(k)
        num, den = bs[self.starts].sum(axis=1), bk[self.starts].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return num / den

    def infer(self, days, values, ref=None) -> dict:
        days = np.asarray(days, dtype=np.int64)
        values = np.asarray(values, dtype=float)
        mean = float(values.mean()) if len(values) else float("nan")
        boot = self.ratio(days, values)
        if ref is not None:                               # difference of two samples, same days
            rd, rv = (np.asarray(ref[0], dtype=np.int64), np.asarray(ref[1], dtype=float))
            boot = boot - self.ratio(rd, rv)
            mean = mean - float(rv.mean())
        boot = boot[np.isfinite(boot)]
        se = float(boot.std(ddof=1))
        z = mean / se if se > 0 else float("nan")
        p = float(math.erfc(abs(z) / math.sqrt(2))) if np.isfinite(z) else None
        return {"mean": mean, "se": se, "ci95": [float(np.percentile(boot, 2.5)),
                                                 float(np.percentile(boot, 97.5))], "p": p}


def cluster_infer(clusters, values, reps: int = N_BOOT, seed: int = SEED) -> dict:
    clusters = np.asarray(clusters)
    values = np.asarray(values, dtype=float)
    ids, inv = np.unique(clusters, return_inverse=True)
    s = np.bincount(inv, weights=values)
    k = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ids), size=(reps, len(ids)))
    boot = s[pick].sum(axis=1) / k[pick].sum(axis=1)
    se = float(boot.std(ddof=1))
    mean = float(values.mean())
    z = mean / se if se > 0 else float("nan")
    return {"clusters": int(len(ids)), "mean": mean, "se": se,
            "p": float(math.erfc(abs(z) / math.sqrt(2))) if np.isfinite(z) else None}


def holm(pvals: dict[str, float]) -> dict[str, float]:
    keys = sorted(pvals, key=lambda k: pvals[k])
    m = len(keys)
    out, run = {}, 0.0
    for i, k in enumerate(keys):
        run = max(run, min(1.0, (m - i) * pvals[k]))
        out[k] = run
    return out


# --------------------------------------------------------------------------- study


def entries_for(setups, keep, last: int) -> tuple[list, int]:
    out, tail = [], 0
    for s, ok in zip(setups, keep):
        if not ok:
            continue
        e = s.t + 1
        if e + MAX_HOLD_MIN > last:
            tail += 1
            continue
        out.append(s)
    return out, tail


def i1_exit(nxt_i1: dict, s, last: int) -> int:
    e = s.t + 1
    j = int(nxt_i1[s.direction][s.t + 1])
    return min(j + 1, e + MAX_HOLD_MIN) if j <= last else e + MAX_HOLD_MIN


def run_policies(mk: Market, setups, nxt_i1, lev: int, fee=FEE_SIDE, spread=BASE_RT) -> dict:
    cat = s_cat(lev)
    res: dict[str, list[dict]] = {p: [] for p in policy_stops(lev)}
    for s in setups:
        e = s.t + 1
        mh = e + MAX_HOLD_MIN
        xc = i1_exit(nxt_i1, s, mk.last)
        for p, stops in policy_stops(lev).items():
            x = xc if p == "C" else mh
            t = simulate(mk, e, s.direction, x, stops, fee, spread, cat)
            if p == "C" and t["kind"] == "TIME":
                t["kind"] = "I1" if xc < mh else "MAX_HOLD"
            elif t["kind"] == "TIME":
                t["kind"] = "MAX_HOLD"
            t.update(family=s.family, t=s.t)
            res[p].append(t)
    return res


def user_metrics(mk: Market, setups, pol: dict) -> dict:
    be = 2 * FEE_SIDE + BASE_RT
    out = {}
    lr = {}
    for d_name, depth in (("any", 0.0), ("0.5pct", 0.005), ("1.0pct", 0.01)):
        reached = g = nt = 0
        for s, c in zip(setups, pol["C"]):
            r_adv, r_fav = path_arrays(mk, s.t + 1, s.direction, c["exit_bar"])
            r = recovery(r_adv, r_fav, depth, be)
            reached += r["reached"]
            g += r["gross"]
            nt += r["net"]
        lr[d_name] = {"reached": reached, "recovered_entry": g, "recovered_net_be": nt,
                      "recovered_entry_share": g / reached if reached else None,
                      "recovered_net_be_share": nt / reached if reached else None}
    out["loss_then_recovery"] = lr
    fe = {}
    for p in ("A1", "A2"):
        stopped = g = nt = 0
        for s, a, c in zip(setups, pol[p], pol["C"]):
            if a["kind"] != "FIXED_STOP":
                continue
            stopped += 1
            if c["exit_bar"] > a["exit_bar"] + 1:            # C still open after the stop bar
                _, r_fav = path_arrays(mk, s.t + 1, s.direction, c["exit_bar"],
                                       start=a["exit_bar"] + 1)
                g += bool((r_fav >= 0).any())
                nt += bool((r_fav >= be).any())
        fe[p] = {"fixed_stop_exits": stopped, "later_recovered_entry": g, "later_recovered_net_be": nt,
                 "recovered_entry_share": g / stopped if stopped else None,
                 "recovered_net_be_share": nt / stopped if stopped else None}
    out["fixed_stop_false_exit"] = fe
    inv = [c for c in pol["C"] if c["kind"] == "I1"]
    neg = [c["net"] for c in inv if c["net"] < 0]
    out["invalidation_loss"] = {
        "i1_exits": len(inv),
        "gross_negative_share": (sum(c["gross"] < 0 for c in inv) / len(inv)) if inv else None,
        "net_negative_share": (len(neg) / len(inv)) if inv else None,
        "net_of_negative": _q(neg), "net_all_i1": _q([c["net"] for c in inv])}
    out["hold_value"] = {f"C_minus_{p}": _q([c["net"] - a["net"] for c, a in zip(pol["C"], pol[p])])
                         for p in ("A1", "A2", "A3", "B")}
    return out


def horizons(mk: Market, setups) -> dict:
    out = {}
    for h in HORIZONS_H:
        tr = [simulate(mk, s.t + 1, s.direction, s.t + 1 + h * 60, []) for s in setups]
        out[f"{h}h"] = {k: v for k, v in summarize(tr).items()
                        if k in ("n", "gross_mean", "gross_median", "net_mean", "net_median",
                                 "net_pos_share", "mfe_median", "mae_median", "pf")}
    return out


def random_control(mk: Market, st, raw_setups, nxt_i1, cell_counts: dict) -> dict[str, list[dict]]:
    """Cells in the order F1 L, F1 S, F2 L, F2 S, F3 L, F3 S; seed SEED + cell index."""
    d = st["D"]
    n = len(d)
    raw_t = np.zeros(n, dtype=bool)
    raw_t[[s.t for s in raw_setups]] = True
    out: dict[str, list[dict]] = {}
    cell = 0
    cat = s_cat(20)
    for fam in STUDIED:
        trades = []
        for dirn in (1, -1):
            pool = np.flatnonzero((d == dirn) & ~raw_t & (np.arange(n) + 1 + MAX_HOLD_MIN <= mk.last))
            k = cell_counts[(fam, dirn)]
            rng = np.random.default_rng(SEED + cell)
            pick = np.sort(rng.choice(pool, size=k, replace=False)) if k else []
            for t in pick:
                s = v3.SetupV3(t=int(t), direction=dirn, family=fam, tag="CONTROL")
                tr = simulate(mk, s.t + 1, dirn, i1_exit(nxt_i1, s, mk.last), [cat], cat=cat)
                tr.update(family=fam, t=int(t))
                trades.append(tr)
            cell += 1
        out[fam] = trades
    return out


def shifted_control(mk: Market, st, shift_days: int) -> dict[str, list[dict]]:
    k = shift_days * 1440
    sh = {name: np.roll(arr, k) for name, arr in st.items()}
    setups = v3.detect_setups(sh)
    keep = v3.dedup_primary(setups, v3.thesis_episode_ids(sh["D"]))
    ent, _ = entries_for(setups, keep, mk.last)
    nxt = {dirn: next_true(sh["D"] == -dirn) for dirn in (1, -1)}
    cat = s_cat(20)
    out: dict[str, list[dict]] = {f: [] for f in STUDIED}
    for s in ent:
        if s.family not in out:
            continue
        tr = simulate(mk, s.t + 1, s.direction, i1_exit(nxt, s, mk.last), [cat], cat=cat)
        tr.update(family=s.family, t=s.t)
        out[s.family].append(tr)
    return out


def placebo(mk: Market, setups, pol_c: list[dict]) -> list[dict]:
    cat = s_cat(20)
    out = []
    for s, c in zip(setups, pol_c):
        e = s.t + 1
        x = max(c["exit_bar"], e + 1)
        tr = simulate(mk, e, -s.direction, x, [cat], cat=cat)
        tr.update(family=s.family, t=s.t)
        out.append(tr)
    return out


def run(out: Path | None = OUT, trades_out: Path | None = None) -> dict:
    sha_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (CONTRACT, CONTRACT_V3)}
    log = data.ReadLog()
    par = data.parity(log)
    vol, cov = data.load_train(log)
    px = data.load_train_prices(log)
    funding = data.load_train_funding(log)
    mk = make_market(px, funding)
    st = v3.states_v3(vol)
    raw = v3.detect_setups(st)
    keep = v3.dedup_primary(raw, v3.thesis_episode_ids(st["D"]))
    ent_all, tail = entries_for(raw, keep, mk.last)
    nxt_i1 = {dirn: next_true(st["D"] == -dirn) for dirn in (1, -1)}
    n_days = len(px.open) // 1440
    boot = Bootstrap(n_days)
    episode = v3.thesis_episode_ids(st["D"])

    fam_res: dict = {}
    pvals: dict[str, float] = {}
    cell_counts = {}
    all_trades = []
    for fam in STUDIED:
        ent = [s for s in ent_all if s.family == fam]
        for dirn in (1, -1):
            cell_counts[(fam, dirn)] = sum(1 for s in ent if s.direction == dirn)
        pol20 = run_policies(mk, ent, nxt_i1, 20)
        pol10 = run_policies(mk, ent, nxt_i1, 10)
        polst = run_policies(mk, ent, nxt_i1, 20, STRESS_FEE_SIDE, STRESS_RT)
        days = np.array([(s.t + 1) // 1440 for s in ent])
        c_net = np.array([t["net"] for t in pol20["C"]])
        p1 = boot.infer(days, c_net)
        p1_cluster = cluster_infer([episode[s.t] for s in ent], c_net)
        p2 = {p: boot.infer(days, c_net - np.array([t["net"] for t in pol20[p]])) for p in ("A1", "A2")}
        pvals[f"{fam}_P1"] = p1["p"]
        pvals[f"{fam}_P2a"] = p2["A1"]["p"]
        pvals[f"{fam}_P2b"] = p2["A2"]["p"]
        by_dir = {}
        for dirn in (1, -1):
            idx = [i for i, s in enumerate(ent) if s.direction == dirn]
            by_dir[DIR_NAME[dirn]] = {
                "L20": {p: summarize([pol20[p][i] for i in idx]) for p in pol20},
                "user_metrics": user_metrics(mk, [ent[i] for i in idx],
                                             {p: [pol20[p][i] for i in idx] for p in pol20})}
        # 48 h overlap-adjusted (raw setups, per family, LONG+SHORT merged), policy C
        raw_f = [s for s in raw if s.family == fam]
        ov_keep = v3.dedup_48h(raw_f, per_family=True)
        ov, _ = entries_for(raw_f, ov_keep, mk.last)
        ov_c = [simulate(mk, s.t + 1, s.direction, i1_exit(nxt_i1, s, mk.last), [s_cat(20)],
                         cat=s_cat(20)) for s in ov]
        fam_res[fam] = {
            "entries": {"raw": len(raw_f), "primary": sum(keep[i] for i, s in enumerate(raw)
                                                          if s.family == fam),
                        "evaluated": len(ent), "long": cell_counts[(fam, 1)],
                        "short": cell_counts[(fam, -1)]},
            "pooled": {"L20": {p: summarize(v) for p, v in pol20.items()},
                       "L10": {p: summarize(v) for p, v in pol10.items()},
                       "stress_L20": {p: summarize(v) for p, v in polst.items()}},
            "by_direction": by_dir,
            "user_metrics_pooled": user_metrics(mk, ent, pol20),
            "horizons": horizons(mk, ent),
            "overlap_adjusted_C": summarize(ov_c),
            "inference": {"P1_C_net": p1, "P1_cluster_by_thesis_episode": p1_cluster,
                          "P2a_C_minus_A1": p2["A1"], "P2b_C_minus_A2": p2["A2"]},
            "_days": days, "_c_net": c_net, "_pol_c": pol20["C"],
        }
        for i, s in enumerate(ent):
            for p in pol20:
                t = pol20[p][i]
                all_trades.append({"family": fam, "policy": p, **{k: t[k] for k in (
                    "t", "e", "dir", "exit_bar", "kind", "gross", "fee", "spread", "funding",
                    "net", "mfe", "mae", "hold")}})

    adj = holm(pvals)
    rnd = random_control(mk, st, raw, nxt_i1, cell_counts)
    shifts = {d: shifted_control(mk, st, d) for d in SHIFT_DAYS}
    verdicts = {}
    for fam in STUDIED:
        f = fam_res[fam]
        ent = [s for s in ent_all if s.family == fam]
        pl = placebo(mk, ent, f["_pol_c"])
        rd = np.array([(t["t"] + 1) // 1440 for t in rnd[fam]])
        rv = np.array([t["net"] for t in rnd[fam]])
        diff_rnd = boot.infer(f["_days"], f["_c_net"], ref=(rd, rv))
        f["controls"] = {
            "random_timestamp": {"summary": summarize(rnd[fam]), "C_minus_control": diff_rnd},
            "time_shift": {f"{d}d": summarize(shifts[d][fam]) for d in SHIFT_DAYS},
            "direction_placebo": summarize(pl)}
        c = f["pooled"]["L20"]["C"]
        ld = f["by_direction"]["LONG"]["L20"]["C"].get("net_mean", -1)
        sd = f["by_direction"]["SHORT"]["L20"]["C"].get("net_mean", -1)
        gates = {
            "1_P1_positive_holm": c["net_mean"] > 0 and adj[f"{fam}_P1"] < 0.05,
            "2_PF_gt_1": (c["pf"] or 0) > 1,
            "3_beats_random_control_lb": diff_rnd["ci95"][0] > 0,
            "4_catastrophic_le_10pct": c["catastrophic_rate"] <= 0.10,
            "5_both_directions_positive": ld > 0 and sd > 0,
            "6_overlap_adjusted_positive": f["overlap_adjusted_C"].get("net_mean", -1) > 0,
            "7_not_sig_worse_than_A": not any(
                adj[f"{fam}_{k}"] < 0.05 and f["inference"][n]["mean"] < 0
                for k, n in (("P2a", "P2a_C_minus_A1"), ("P2b", "P2b_C_minus_A2"))),
            "8_stress_positive": f["pooled"]["stress_L20"]["C"]["net_mean"] > 0,
        }
        if c["net_mean"] <= 0 or (c["pf"] or 0) <= 1:
            v = "A_TRAIN_FAIL"
        elif all(gates.values()):
            v = "C_TRAIN_PASS_VALIDATION_CANDIDATE"
        else:
            v = "B_TRAIN_WEAK"
        verdicts[fam] = {"gates": gates, "verdict": v, "holm_p": {k: adj[f"{fam}_{k}"]
                                                                  for k in ("P1", "P2a", "P2b")}}
        del f["_days"], f["_c_net"], f["_pol_c"]
    passing = [f for f in STUDIED if verdicts[f]["verdict"].startswith("C")]
    passing.sort(key=lambda f: (verdicts[f]["holm_p"]["P1"],
                                -fam_res[f]["inference"]["P1_C_net"]["ci95"][0]))
    candidates = passing[:2]
    for f in passing[2:]:
        verdicts[f]["verdict"] = "C_PASS_NOT_SELECTED_MAX2"

    result = {"study": "PTM-R1 V3", "split": "TRAIN", "contract_sha256": sha_before,
              "parity": par, "train_coverage": cov, "files_read": log.entries,
              "price_minutes": int(len(px.open)), "funding_settlements": len(funding),
              "train_tail_excluded_primary": tail, "catastrophic_fraction": {k: s_cat(v)
                                                                            for k, v in LEVERAGES.items()},
              "ordering_diagnostic": "NOT_RUN (aggTrades lie in the HOLDOUT window)",
              "holm": {"raw_p": pvals, "adjusted_p": adj, "tests": len(pvals)},
              "families": fam_res, "verdicts": verdicts, "validation_candidates": candidates}
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (CONTRACT, CONTRACT_V3)}
    if after != sha_before:
        raise RuntimeError("contract changed during the run")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, sort_keys=True, default=_json) + "\n")
    if trades_out is not None:
        trades_out.write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in all_trades))
    return result


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    trades = target.with_name(target.stem + "_trades.jsonl")
    r = run(target, trades)
    json.dump({"verdicts": {f: v["verdict"] for f, v in r["verdicts"].items()},
               "candidates": r["validation_candidates"]}, sys.stdout, indent=1)
    print()
