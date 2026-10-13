"""PTM-R2: recovery, profit protection and leverage feasibility, TRAIN only.

Contract: docs/crypto/personal_trading_method_r2/CONTRACT_PTM_R2_V1.md
Execution, costs and bootstrap are R1's (`r1_train`), reused unchanged.

Run: python -m app.crypto.research.ptm_r1.r2_train [out.json]  (from backend/)
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
from . import r1_train as R
from .states import MAX_HOLD_MIN, NONE, next_true

CONTRACT = data.ROOT / "docs/crypto/personal_trading_method_r2/CONTRACT_PTM_R2_V1.md"
OUT = data.ROOT / "data/research/crypto/ptm_r2/r2_train_v1.json"
ANCHORS = (("R0", 0.005), ("R1", 0.010), ("R2", 0.015))
LEVERAGES = (5, 10, 20, 50, 100)
PRIMARY_LEV = (5, 10, 20, 50)
MARGIN_FRACTION = 0.10
SPLITS = ("S1_W1", "S2_H1_opp", "S3_M_opp", "S4_align_ge2", "S5_funding_paying",
          "S6_basis_favourable", "S7_thesis_age_ge24h", "S8_since_entry_le4h", "S9_long")
PROTECT = ("P1", "P2", "P3")
DIAG_EVENTS = ("M_opp", "H4_opp_W1", "W3")      # H1_opp = P1 and W2 = P2 are not repeated
DIR_NAME = {1: "LONG", -1: "SHORT"}


def liq_dist(lev: int) -> float:
    return 1.0 / lev - R.MMR


def safety_dist(lev: int) -> float:
    return 0.40 * liq_dist(lev)


# --------------------------------------------------------------------------- population


@dataclass(frozen=True)
class Entry:
    b: int          # grid boundary (close of minute b)
    e: int          # entry minute
    dir: int
    x0: int         # P0 exit minute (I1 or MAX HOLD)
    day: int
    episode: int

    @property
    def is_noon(self) -> bool:
        return self.e % 1440 == 720


def grid_entries(st: dict[str, np.ndarray], last: int) -> list[Entry]:
    d = st["D"]
    episode = v3.thesis_episode_ids(d)
    nxt = {dirn: next_true(d == -dirn) for dirn in (1, -1)}
    out = []
    for b in range(239, len(d), 240):
        dirn = int(d[b])
        if dirn not in (1, -1):
            continue
        e = b + 1
        if e + MAX_HOLD_MIN > last:
            continue
        j = int(nxt[dirn][b + 1]) if b + 1 < len(d) else len(d)
        x0 = min(j + 1, e + MAX_HOLD_MIN) if j <= last else e + MAX_HOLD_MIN
        out.append(Entry(b=b, e=e, dir=dirn, x0=x0, day=e // 1440, episode=int(episode[b])))
    return out


def run_starts(arr: np.ndarray) -> np.ndarray:
    """Index where the current constant run of `arr` began, for every position."""
    change = np.concatenate(([True], arr[1:] != arr[:-1]))
    idx = np.where(change, np.arange(len(arr)), 0)
    return np.maximum.accumulate(idx)


# --------------------------------------------------------------------------- per-trade pieces


def anchor_info(r_adv: np.ndarray, r_fav: np.ndarray, a: float) -> dict | None:
    hit = np.flatnonzero(r_adv <= -a)
    if len(hit) == 0:
        return None
    k_a = int(hit[0])
    rec = np.flatnonzero(r_fav[k_a + 1:] >= 0)
    k_r = k_a + 1 + int(rec[0]) if len(rec) else None
    end = k_r if k_r is not None else len(r_adv)
    further = float(-r_adv[k_a:end].min())
    before = np.flatnonzero(r_fav[:k_a] >= 0)
    adv_dur = k_a - (int(before[-1]) + 1) if len(before) else k_a
    return {"k_a": k_a, "k_r": k_r, "recovered": k_r is not None,
            "t_recover": (k_r - k_a) if k_r is not None else None,
            "further_adverse": further, "adverse_duration": adv_dur}


def p3_exit(h4: np.ndarray, dirn: int, start: int, x0: int) -> int:
    """H4 opposite at `start` or a 4 h completion and still opposite at the next completion."""
    prev = h4[start]
    c = start + (239 - start % 240) if start % 240 != 239 else start + 240
    while c < x0 - 1:
        s = h4[c]
        if s == -dirn and prev == -dirn:
            return c + 1
        prev = s
        c += 240
    return x0


def protect_exits(ctx, en: Entry, start: int) -> dict[str, int]:
    """Exit minutes for P0..P3 and the diagnostics, from boundary `start` (= e + k_r)."""
    out = {"P0": en.x0}
    for name, nt in (("P1", ctx["nt"]["H1_opp"]), ("P2", ctx["nt"]["H1H4_opp"]),
                     ("M_opp", ctx["nt"]["M_opp"]), ("H4_opp_W1", ctx["nt"]["H4_opp"]),
                     ("W3", ctx["nt"]["W3"])):
        j = int(nt[en.dir][start])
        out[name] = min(j + 1, en.x0) if j < len(nt[en.dir]) else en.x0
    out["P3"] = p3_exit(ctx["st"]["H4"], en.dir, start, en.x0)
    return out


def policy_outcome(mk: R.Market, en: Entry, base_t: dict, r_fav: np.ndarray, k_r: int,
                   x: int) -> dict:
    """Outcome of exiting at minute x for a trade that recovered at bar k_r."""
    t = base_t if x == en.x0 else R.simulate(mk, en.e, en.dir, x, [])
    seg = r_fav[k_r:x - en.e]
    mfe_after = max(float(seg.max()) if len(seg) else 0.0, t["gross"], 0.0)
    gb = mfe_after - t["gross"]
    return {"net": t["net"], "gross": t["gross"], "mfe_after": mfe_after, "giveback": gb,
            "giveback_ratio": gb / mfe_after if mfe_after > 0 else None, "hold": t["hold"],
            "fired": x < en.x0}


def lev_trade(mk: R.Market, en: Entry, x: int, lev: int, mode: str) -> dict:
    liq, saf = liq_dist(lev), safety_dist(lev)
    level = saf if mode == "SAFETY_ON" else liq
    t = R.simulate(mk, en.e, en.dir, x, [level])
    if t["kind"] == "FIXED_STOP":
        t["kind"] = "LIQUIDATION" if (mode == "LIQ_ONLY" or t["gross"] <= -liq) else "SAFETY"
    elif t["kind"] == "TIME":
        t["kind"] = "I1" if x < en.e + MAX_HOLD_MIN else "MAX_HOLD"
    return t


def account(t: dict, lev: int, scenario: str) -> dict:
    if scenario == "A_FIXED_NOTIONAL":
        notional, margin = 1.0, 1.0 / lev
    else:
        margin, notional = MARGIN_FRACTION, MARGIN_FRACTION * lev
    if t["kind"] == "LIQUIDATION":
        pnl = -margin - notional * (R.FEE_SIDE + t["funding"])
    else:
        pnl = notional * t["net"]
    return {"pnl": pnl, "max_adverse": max(-notional * t["mae"], -margin),
            "margin": margin, "notional": notional,
            "liq_proximity": t["mae"] / liq_dist(lev)}


# --------------------------------------------------------------------------- statistics


def dist(v) -> dict | None:
    a = np.asarray(v, dtype=float)
    if len(a) == 0:
        return None
    return {"n": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)),
            "p5": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)),
            "min": float(a.min()), "max": float(a.max())}


def cluster_diff(clusters, values, groups, reps=R.N_BOOT, seed=R.SEED) -> dict:
    """Thesis-episode cluster bootstrap of mean(group 1) - mean(group 0)."""
    clusters = np.asarray(clusters)
    values = np.asarray(values, dtype=float)
    groups = np.asarray(groups, dtype=bool)
    ids, inv = np.unique(clusters, return_inverse=True)
    s1 = np.bincount(inv, weights=values * groups, minlength=len(ids))
    k1 = np.bincount(inv, weights=groups.astype(float), minlength=len(ids))
    s0 = np.bincount(inv, weights=values * ~groups, minlength=len(ids))
    k0 = np.bincount(inv, weights=(~groups).astype(float), minlength=len(ids))
    pick = np.random.default_rng(seed).integers(0, len(ids), size=(reps, len(ids)))
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = s1[pick].sum(1) / k1[pick].sum(1) - s0[pick].sum(1) / k0[pick].sum(1)
    boot = boot[np.isfinite(boot)]
    est = float(values[groups].mean() - values[~groups].mean())
    se = float(boot.std(ddof=1))
    z = est / se if se > 0 else float("nan")
    return {"estimate": est, "se": se,
            "p": float(math.erfc(abs(z) / math.sqrt(2))) if np.isfinite(z) else None}


# --------------------------------------------------------------------------- study


def build_context(vol, mk: R.Market, basis: np.ndarray) -> dict:
    st = v3.states_v3(vol)
    nt = {}
    for name, fn in (("H1_opp", lambda d: st["H1"] == -d), ("H4_opp", lambda d: st["H4"] == -d),
                     ("M_opp", lambda d: st["M"] == -d),
                     ("H1H4_opp", lambda d: (st["H1"] == -d) & (st["H4"] == -d)),
                     ("W3", lambda d: (st["M"] == -d) & (st["H1"] == -d) & (st["H4"] == -d))):
        nt[name] = {dirn: next_true(fn(dirn)) for dirn in (1, -1)}
    n = len(st["D"])
    fund_rate_at = np.full(n, np.nan)
    if len(mk.fund_idx):
        pos = np.searchsorted(mk.fund_idx, np.arange(n), side="right") - 1
        ok = pos >= 0
        fund_rate_at[ok] = mk.fund_rate[pos[ok]]
    return {"st": st, "nt": nt, "d_start": run_starts(st["D"]), "fund_at": fund_rate_at,
            "basis": basis}


def anchor_state(ctx, en: Entry, k_a: int, info: dict) -> dict:
    B = en.e + k_a - 1
    st = ctx["st"]
    rel = {k: int(st[k][B]) * en.dir if st[k][B] != NONE else None for k in ("H4", "H1", "M")}
    align = sum(1 for k in ("H4", "H1", "M") if rel[k] == 1)
    fund = ctx["fund_at"][B]
    bas = ctx["basis"][B]
    return {
        "H4_rel": rel["H4"], "H1_rel": rel["H1"], "M_rel": rel["M"], "align_count": align,
        "funding_signed": None if np.isnan(fund) else float(en.dir * fund),
        "basis_signed": None if np.isnan(bas) else float(en.dir * bas),
        "dir": en.dir, "adverse_duration": info["adverse_duration"], "since_entry": k_a,
        "thesis_age": int(B - ctx["d_start"][B] + 1),
        "W1": rel["H4"] == -1, "W2": rel["H4"] == -1 and rel["H1"] == -1,
        "W3": rel["H4"] == -1 and rel["H1"] == -1 and rel["M"] == -1}


def split_flags(s: dict) -> dict[str, bool | None]:
    return {"S1_W1": s["W1"], "S2_H1_opp": s["H1_rel"] == -1, "S3_M_opp": s["M_rel"] == -1,
            "S4_align_ge2": s["align_count"] >= 2,
            "S5_funding_paying": None if s["funding_signed"] is None else s["funding_signed"] > 0,
            "S6_basis_favourable": None if s["basis_signed"] is None else s["basis_signed"] > 0,
            "S7_thesis_age_ge24h": s["thesis_age"] >= 1440,
            "S8_since_entry_le4h": s["since_entry"] <= 240, "S9_long": s["dir"] == 1}


def run(out: Path | None = OUT) -> dict:
    sha_before = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    log = data.ReadLog()
    par = data.parity(log)
    vol, cov = data.load_train(log)
    px = data.load_train_prices(log)
    funding = data.load_train_funding(log)
    basis = data.load_train_basis(log)
    mk = R.make_market(px, funding)
    ctx = build_context(vol, mk, basis)
    entries = grid_entries(ctx["st"], mk.last)
    boot = R.Bootstrap(len(px.open) // 1440)

    # P0 base trades and per-anchor records
    base = [R.simulate(mk, en.e, en.dir, en.x0, []) for en in entries]
    paths = [R.path_arrays(mk, en.e, en.dir, en.x0) for en in entries]
    anchors: dict[str, list[dict]] = {}
    for name, a in ANCHORS:
        recs = []
        for i, (en, (r_adv, r_fav)) in enumerate(zip(entries, paths)):
            info = anchor_info(r_adv, r_fav, a)
            if info is None:
                continue
            ended_i1 = (not info["recovered"]) and en.x0 < en.e + MAX_HOLD_MIN
            recs.append({"i": i, **info, "ended_by_I1_unrecovered": ended_i1,
                         "state": anchor_state(ctx, en, info["k_a"], info)})
        anchors[name] = recs

    # ---------------- Q1
    q1: dict = {}
    q1_p: dict[str, float] = {}
    for name, _ in ANCHORS:
        recs = anchors[name]
        rec_flag = np.array([r["recovered"] for r in recs], dtype=float)
        days = np.array([entries[r["i"]].day for r in recs])
        eps = np.array([entries[r["i"]].episode for r in recs])
        dirs = np.array([entries[r["i"]].dir for r in recs])
        cohort = {"n": len(recs), "share_of_entries": len(recs) / len(entries),
                  "recovery_rate": float(rec_flag.mean()) if len(recs) else None,
                  "recovery_rate_ci": boot.infer(days, rec_flag)["ci95"] if len(recs) else None,
                  "recovery_rate_long": float(rec_flag[dirs == 1].mean()),
                  "recovery_rate_short": float(rec_flag[dirs == -1].mean()),
                  "recovery_rate_dedup_noon": float(np.mean([r["recovered"] for r in recs
                                                             if entries[r["i"]].is_noon])),
                  "recovery_rate_overlap48h": float(np.mean([r["recovered"] for r in recs
                                                             if entries[r["i"]].is_noon
                                                             and entries[r["i"]].day % 2 == 0])),
                  "time_to_recovery_min": dist([r["t_recover"] for r in recs if r["recovered"]]),
                  "further_adverse_recovered": dist([r["further_adverse"] for r in recs if r["recovered"]]),
                  "further_adverse_unrecovered": dist([r["further_adverse"] for r in recs
                                                       if not r["recovered"]]),
                  "ended_by_I1_unrecovered_share": float(np.mean([r["ended_by_I1_unrecovered"]
                                                                  for r in recs])),
                  "state_means": {}}
        for key in ("align_count", "adverse_duration", "since_entry", "thesis_age"):
            for flag in (True, False):
                vals = [r["state"][key] for r in recs if r["recovered"] == flag]
                cohort["state_means"][f"{key}_{'rec' if flag else 'unrec'}"] = (
                    float(np.median(vals)) if vals else None)
        splits = {}
        for sp in SPLITS:
            flags = [split_flags(r["state"])[sp] for r in recs]
            keep = [f is not None for f in flags]
            g = np.array([bool(f) for f, k in zip(flags, keep) if k])
            v = rec_flag[np.array(keep)]
            dd, ee, dr = days[np.array(keep)], eps[np.array(keep)], dirs[np.array(keep)]
            if g.sum() == 0 or (~g).sum() == 0:
                splits[sp] = {"n1": int(g.sum()), "n0": int((~g).sum()), "estimate": None}
                continue
            inf = boot.infer(dd[g], v[g], ref=(dd[~g], v[~g]))
            cl = cluster_diff(ee, v, g)
            by_dir = {}
            for dn, dv in DIR_NAME.items():
                m = dr == dn
                if (g & m).sum() and (~g & m).sum():
                    by_dir[dv] = float(v[g & m].mean() - v[~g & m].mean())
            splits[sp] = {"n1": int(g.sum()), "n0": int((~g).sum()),
                          "episodes1": int(len(set(ee[g]))), "episodes0": int(len(set(ee[~g]))),
                          "rate1": float(v[g].mean()), "rate0": float(v[~g].mean()),
                          "estimate": inf["mean"], "se": inf["se"], "ci95": inf["ci95"],
                          "p": inf["p"], "cluster": cl, "by_direction": by_dir}
            q1_p[f"{name}:{sp}"] = inf["p"]
        cohort["splits"] = splits
        q1[name] = cohort
    q1_adj = R.holm(q1_p)

    # ---------------- Q2
    q2: dict = {}
    q2_p: dict[str, float] = {}
    q2_trades: dict[str, list] = {}
    for name, _ in ANCHORS:
        rows = []
        for r in anchors[name]:
            if not r["recovered"]:
                continue
            en = entries[r["i"]]
            start = en.e + r["k_r"]
            ex = protect_exits(ctx, en, start)
            _, r_fav = paths[r["i"]]
            row = {"i": r["i"], "exits": ex}
            for pol, x in ex.items():
                row[pol] = policy_outcome(mk, en, base[r["i"]], r_fav, r["k_r"], x)
            rows.append(row)
        q2_trades[name] = rows
        days = np.array([entries[w["i"]].day for w in rows])
        dirs = np.array([entries[w["i"]].dir for w in rows])
        res = {"n_recovered": len(rows)}
        for pol in ("P0",) + PROTECT + DIAG_EVENTS:
            net = np.array([w[pol]["net"] for w in rows])
            gbs = np.array([w[pol]["giveback"] for w in rows])
            ratios = [w[pol]["giveback_ratio"] for w in rows if w[pol]["giveback_ratio"] is not None]
            res[pol] = {"net": dist(net), "gross_mean": float(np.mean([w[pol]["gross"] for w in rows])),
                        "mfe_after": dist([w[pol]["mfe_after"] for w in rows]),
                        "giveback": dist(gbs), "giveback_ratio_median": float(np.median(ratios)) if ratios else None,
                        "fired_share": float(np.mean([w[pol]["fired"] for w in rows])),
                        "hold_min_median": float(np.median([w[pol]["hold"] for w in rows]))}
            if pol != "P0":
                dn = net - np.array([w["P0"]["net"] for w in rows])
                dg = gbs - np.array([w["P0"]["giveback"] for w in rows])
                inf = boot.infer(days, dn)
                res[pol]["paired_net_vs_P0"] = {**inf, "long": float(dn[dirs == 1].mean()),
                                                "short": float(dn[dirs == -1].mean())}
                res[pol]["paired_giveback_vs_P0_mean"] = float(dg.mean())
                if pol in PROTECT:
                    q2_p[f"{name}:{pol}"] = inf["p"]
        res["P4"] = "IDENTICAL_TO_P0 (I1 is the 1D invalidation under V3)"
        q2[name] = res
    q2_adj = R.holm(q2_p)

    # ---------------- Q3
    q3: dict = {}
    q3_p: dict[str, float] = {}
    r0 = {r["i"]: r for r in anchors["R0"]}
    for lev in LEVERAGES:
        q3[f"{lev}x"] = {"liq_dist": liq_dist(lev), "safety_dist": safety_dist(lev)}
        for mode in ("SAFETY_ON", "LIQ_ONLY"):
            trades = [lev_trade(mk, en, en.x0, lev, mode) for en in entries]
            kinds: dict[str, int] = {}
            for t in trades:
                kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
            stopped = [t["kind"] in ("SAFETY", "LIQUIDATION") for t in trades]
            # recovery survival and protection reach on the R0 cohort
            surv = before = 0
            reach = {p: 0 for p in PROTECT}
            for i, r in r0.items():
                t = trades[i]
                stop_bar = t["exit_bar"] if t["kind"] in ("SAFETY", "LIQUIDATION") else None
                rec_ok = r["recovered"] and (stop_bar is None or stop_bar > entries[i].e + r["k_r"])
                surv += rec_ok
                before += (stop_bar is not None) and not rec_ok
                if rec_ok:
                    ex = protect_exits(ctx, entries[i], entries[i].e + r["k_r"])
                    for p in PROTECT:
                        if ex[p] < entries[i].x0 and (stop_bar is None or ex[p] <= stop_bar):
                            reach[p] += 1
            block = {"exit_kinds": dict(sorted(kinds.items())),
                     "safety_or_liq_rate": float(np.mean(stopped)),
                     "underlying_net": dist([t["net"] for t in trades]),
                     "mae_over_liq": dist([t["mae"] / liq_dist(lev) for t in trades]),
                     "R0_cohort": len(r0),
                     "R0_recovered_before_stop_share": surv / len(r0) if r0 else None,
                     "R0_stopped_before_recovery_share": before / len(r0) if r0 else None,
                     "R0_recovered_then_reached_protection_share":
                         {p: reach[p] / len(r0) for p in PROTECT}}
            for sc in ("A_FIXED_NOTIONAL", "B_FIXED_MARGIN"):
                acc = [account(t, lev, sc) for t in trades]
                pnl = np.array([a["pnl"] for a in acc])
                p5 = float(np.percentile(pnl, 5))
                block[sc] = {"margin": acc[0]["margin"], "notional": acc[0]["notional"],
                             "account_pnl": dist(pnl), "max_adverse_account": dist([a["max_adverse"] for a in acc]),
                             "gain_per_tail_risk": float(pnl.mean() / abs(p5)) if p5 < 0 else None,
                             "by_direction": {DIR_NAME[dn]: float(pnl[[en.dir == dn for en in entries]].mean())
                                              for dn in (1, -1)}}
                if sc == "B_FIXED_MARGIN" and mode == "SAFETY_ON":
                    inf = boot.infer(np.array([en.day for en in entries]), pnl)
                    block[sc]["inference"] = inf
                    if lev in PRIMARY_LEV:
                        q3_p[f"{lev}x"] = inf["p"]
            q3[f"{lev}x"][mode] = block
    q3_adj = R.holm(q3_p)

    # 20x SAFETY_ON safety rate on the recovered cohorts per protection policy (gate input)
    for name, _ in ANCHORS:
        for pol in ("P0",) + PROTECT:
            rate = np.mean([lev_trade(mk, entries[w["i"]], w["exits"][pol], 20, "SAFETY_ON")["kind"]
                            in ("SAFETY", "LIQUIDATION") for w in q2_trades[name]])
            q2[name][pol]["safety_rate_20x"] = float(rate)

    # ---------------- gates
    rec_cands = []
    for name, _ in ANCHORS:
        for sp, s in q1[name]["splits"].items():
            if s.get("estimate") is None:
                continue
            ok = {"N": s["n1"] >= 300 and s["n0"] >= 300 and s["episodes1"] >= 30
                  and s["episodes0"] >= 30,
                  "effect_ge_10pp": abs(s["estimate"]) >= 0.10,
                  "holm": q1_adj[f"{name}:{sp}"] < 0.05,
                  "both_directions_same_sign": sp == "S9_long" or (
                      len(s["by_direction"]) == 2 and
                      all(np.sign(v) == np.sign(s["estimate"]) for v in s["by_direction"].values())),
                  "cluster_p": (s["cluster"]["p"] or 1) < 0.05}
            s["holm_p"] = q1_adj[f"{name}:{sp}"]
            s["gate"] = ok
            if all(ok.values()):
                rec_cands.append((q1_adj[f"{name}:{sp}"], f"{name}:{sp}"))
    prot_cands = []
    for name, _ in ANCHORS:
        for pol in PROTECT:
            r_ = q2[name][pol]
            pn = r_["paired_net_vs_P0"]
            ok = {"net_up_holm": pn["mean"] > 0 and q2_adj[f"{name}:{pol}"] < 0.05,
                  "giveback_down": r_["paired_giveback_vs_P0_mean"] < 0,
                  "safety_not_worse_20x": r_["safety_rate_20x"] <= q2[name]["P0"]["safety_rate_20x"],
                  "both_directions": pn["long"] > 0 and pn["short"] > 0}
            r_["holm_p"] = q2_adj[f"{name}:{pol}"]
            r_["gate"] = ok
            if all(ok.values()):
                prot_cands.append((q2_adj[f"{name}:{pol}"], f"{name}:{pol}"))
    profiles = {}
    for lev in PRIMARY_LEV:
        r_ = q3[f"{lev}x"]["SAFETY_ON"]["safety_or_liq_rate"]
        lab = ("SAFE" if r_ <= 0.10 else "BALANCED" if r_ <= 0.30 else
               "AGGRESSIVE" if r_ <= 0.50 else "INFEASIBLE")
        q3[f"{lev}x"]["profile"] = lab
        q3[f"{lev}x"]["holm_p_B_P0"] = q3_adj[f"{lev}x"]
        if lab != "INFEASIBLE":
            profiles[lab] = lev                              # ascending: keeps the highest
    base_method = [f"{lev}x" for lev in PRIMARY_LEV
                   if q3_adj[f"{lev}x"] < 0.05
                   and q3[f"{lev}x"]["SAFETY_ON"]["B_FIXED_MARGIN"]["account_pnl"]["mean"] > 0]
    rec_cands = [c for _, c in sorted(rec_cands)[:2]]
    prot_cands = [c for _, c in sorted(prot_cands)[:2]]
    method_exists = bool(base_method or prot_cands)

    result = {
        "study": "PTM-R2", "split": "TRAIN", "contract_sha256": sha_before,
        "parity": par, "train_coverage": cov, "files_read": log.entries,
        "basis_na_minutes": int(np.isnan(basis).sum()), "funding_settlements": len(funding),
        "population": {"raw": len(entries), "dedup_noon": sum(en.is_noon for en in entries),
                       "overlap48h": sum(en.is_noon and en.day % 2 == 0 for en in entries),
                       "long": sum(en.dir == 1 for en in entries),
                       "short": sum(en.dir == -1 for en in entries),
                       "base_P0": R.summarize(base)},
        "q1_recovery": q1, "q2_protection": q2, "q3_leverage": q3,
        "holm": {"q1": q1_adj, "q2": q2_adj, "q3": q3_adj,
                 "tests": {"q1": len(q1_p), "q2": len(q2_p), "q3": len(q3_p)}},
        "validation_candidates": {
            "recovery_filter": rec_cands, "profit_protection": prot_cands,
            "leverage_profiles": profiles if method_exists else {},
            "leverage_profiles_descriptive": profiles,
            "base_method_leverages_passing_q3": base_method},
    }
    if hashlib.sha256(CONTRACT.read_bytes()).hexdigest() != sha_before:
        raise RuntimeError("contract changed during the run")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, sort_keys=True, default=R._json) + "\n")
    return result


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    r = run(target)
    json.dump(r["validation_candidates"], sys.stdout, indent=1)
    print()
