"""PTM-D1 TRAIN study: components, composition, controls, leverage, verdict (contract sections 6-14).

Run: python -m app.crypto.research.ptm_d1.study [out.json]  (from backend/)
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data
from ..ptm_r1 import r1_train as R
from ..ptm_r1.r2_train import cluster_diff
from . import entries as E
from . import features as F
from . import sim as S

DOCS = data.ROOT / "docs/crypto/personal_trading_method_discovery_v1"
CONTRACT = DOCS / "CONTRACT_PTM_D1_V1.md"
OUT = data.ROOT / "data/research/crypto/ptm_d1/d1_train_v1.json"
COMPONENT_LEV = 10
LEVERAGES = (5, 10, 20, 50, 100)
PRIMARY_LEV = (5, 10, 20, 50)
MARGIN_FRACTION = 0.10
ANCHORS = (("A05", 0.005), ("A10", 0.010))
N_RANDOM = 200
SHIFT_DAYS = (1, 3, 7)
MIN_N_METHOD = 60
P_SELECT = 0.10
GATE = {"n_c": 100, "n_b": 60, "pf_c": 1.25, "pf_b": 1.10, "wl_c": 1.5, "random_c": 0.05,
        "capture_c": 0.35, "safety_c": 0.10}
DIR_NAME = {1: "LONG", -1: "SHORT"}


def ref_stops(lev: int = COMPONENT_LEV) -> list[tuple[str, float]]:
    return [("CATASTROPHIC", S.s_cat(lev))]


# --------------------------------------------------------------------------- populations


def sequential(ctx: S.Ctx, ons: list[tuple[int, int]], x_rule, p_rule, stops, censor=S.CENSOR_MIN,
               **cost) -> list[dict]:
    """One position at a time: an onset while a trade is open is skipped."""
    out, busy_until = [], -1
    for t, dirn in ons:
        e = t + 1
        if e < busy_until:
            continue
        if e + censor > ctx.last:
            break
        p = S.make_path(ctx, e, dirn, censor)
        tr = S.simulate(ctx, p, x_rule, p_rule, stops, **cost)
        tr["t"] = t
        out.append(tr)
        busy_until = tr["exit_bar"]
    return out


def resim(ctx: S.Ctx, trades: list[dict], x_rule, p_rule, stops, censor=S.CENSOR_MIN, flip=False,
          shift=0, **cost) -> list[dict]:
    """Same entries (optionally direction-flipped / time-shifted) under another policy."""
    out = []
    for tr in trades:
        e = tr["e"] + shift
        dirn = -tr["dir"] if flip else tr["dir"]
        if e + censor > ctx.last or e < 1:
            continue
        p = S.make_path(ctx, e, dirn, censor)
        r = S.simulate(ctx, p, x_rule, p_rule, stops, **cost)
        r["t"] = e - 1
        out.append(r)
    return out


def mirror(ctx: S.Ctx, trades: list[dict]) -> list[dict]:
    """Opposite-direction placebo: same entry and the same exit bar, P&L of the reversed direction.

    State exits are defined relative to the trade direction, so a reversed trade would see its thesis
    already invalid at entry; mirroring the exit timing keeps the comparison about direction only.
    """
    out = []
    mk = ctx.mk
    for tr in trades:
        dirn = -tr["dir"]
        gross = -tr["gross"]
        ratio = 1.0 + dirn * gross
        fee = R.FEE_SIDE * (1.0 + ratio)
        a, b = np.searchsorted(mk.fund_idx, [tr["e"], tr["exit_bar"] + 1])
        p0 = mk.px.open[tr["e"]]
        funding = float(np.sum(dirn * mk.fund_rate[a:b] * mk.px.open[mk.fund_idx[a:b]] / p0))
        net = gross - fee - R.BASE_RT - funding
        mfe, mae = tr["mae"], tr["mfe"]
        out.append({**tr, "dir": dirn, "gross": gross, "fee": fee, "funding": funding, "net": net,
                    "mfe": mfe, "mae": mae, "giveback": mfe - gross,
                    "giveback_ratio": (mfe - gross) / mfe if mfe > 0 else None, "kind": "MIRROR"})
    return out


# --------------------------------------------------------------------------- statistics


def dist(v) -> dict | None:
    a = np.asarray([x for x in v if x is not None], dtype=float)
    if len(a) == 0:
        return None
    return {"n": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)),
            "p5": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)),
            "min": float(a.min()), "max": float(a.max())}


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    net = np.array([t["net"] for t in trades])
    g = np.array([t["gross"] for t in trades])
    mfe = np.array([t["mfe"] for t in trades])
    kinds: dict[str, int] = {}
    for t in trades:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    win, lose = net[net > 0], net[net <= 0]
    aw = float(win.mean()) if len(win) else 0.0
    al = float(-lose.mean()) if len(lose) else 0.0
    return {"n": len(trades), "long": int(sum(t["dir"] == 1 for t in trades)),
            "net_mean": float(net.mean()), "net_median": float(np.median(net)),
            "net_sum": float(net.sum()), "pf": R.pf(net), "win_rate": float((net > 0).mean()),
            "avg_winner": aw, "avg_loser": al, "winner_loser_ratio": (aw / al) if al > 0 else None,
            "gross_mean": float(g.mean()), "mfe_mean": float(mfe.mean()), "mfe_median": float(np.median(mfe)),
            "mae_mean": float(np.mean([t["mae"] for t in trades])),
            "mae_median": float(np.median([t["mae"] for t in trades])),
            "mfe_capture": float(g.mean() / mfe.mean()) if mfe.mean() > 0 else None,
            "giveback_mean": float(np.mean([t["giveback"] for t in trades])),
            "giveback_ratio_median": (float(np.median([t["giveback_ratio"] for t in trades
                                                        if t["giveback_ratio"] is not None]))
                                      if any(t["giveback_ratio"] is not None for t in trades) else None),
            "recovery_rate": float(np.mean([t["recovered"] for t in trades])),
            "tail_p5": float(np.percentile(net, 5)), "tail_min": float(net.min()),
            "hold_h_median": float(np.median([t["hold"] for t in trades]) / 60),
            "hold_h_mean": float(np.mean([t["hold"] for t in trades]) / 60),
            "exit_kinds": dict(sorted(kinds.items())),
            "catastrophic_rate": kinds.get("CATASTROPHIC", 0) / len(trades),
            "censor_rate": kinds.get("CENSOR", 0) / len(trades),
            "funding_mean": float(np.mean([t["funding"] for t in trades]))}


def days_of(trades) -> np.ndarray:
    return np.array([t["e"] // 1440 for t in trades], dtype=np.int64)


def infer(boot: R.Bootstrap, ctx: S.Ctx, trades: list[dict], key="net") -> dict:
    v = np.array([t[key] for t in trades], dtype=float)
    r = boot.infer(days_of(trades), v)
    eps = ctx.f.episode[np.array([t["e"] - 1 for t in trades])]
    r["cluster"] = R.cluster_infer(eps, v)
    return r


def paired(boot: R.Bootstrap, ctx: S.Ctx, a: list[dict], b: list[dict]) -> dict:
    """a minus b on the same entries (both lists aligned)."""
    assert len(a) == len(b) and all(x["e"] == y["e"] for x, y in zip(a, b))
    d = np.array([x["net"] - y["net"] for x, y in zip(a, b)])
    r = boot.infer(days_of(a), d)
    eps = ctx.f.episode[np.array([t["e"] - 1 for t in a])]
    r["cluster"] = R.cluster_infer(eps, d)
    r["share_changed"] = float(np.mean([x["exit_bar"] != y["exit_bar"] for x, y in zip(a, b)]))
    return r


# --------------------------------------------------------------------------- hold / recovery


def anchor_records(ctx: S.Ctx, trades: list[dict], a: float) -> list[dict]:
    f = ctx.f
    be = 2 * R.FEE_SIDE + R.BASE_RT
    out = []
    for tr in trades:
        p = S.make_path(ctx, tr["e"], tr["dir"])
        k_end = tr["exit_bar"] - tr["e"]
        hit = np.flatnonzero(p.r_adv[:k_end] <= -a)
        if len(hit) == 0:
            continue
        k_a = int(hit[0])
        B = tr["e"] + k_a - 1
        dirn = tr["dir"]
        rec = np.flatnonzero(p.r_fav[k_a + 1:k_end] >= 0)
        recovered = len(rec) > 0
        rel = {h: int(f.st[h][B]) * dirn for h in ("H4", "H1", "M")}
        # H4 realigns after the anchor, before exit
        h4_re = bool(ctx.rel[dirn]["H4_agree"][B + 1:tr["exit_bar"]].any()) if rel["H4"] == -1 else None
        # recovery attempts before the anchor: favourable swings >= 0.5 V from a running adverse extreme
        seg_adv, seg_fav = p.r_adv[:k_a + 1], p.r_fav[:k_a + 1]
        runmin = np.minimum.accumulate(seg_adv)
        prev_min = np.concatenate(([0.0], runmin[:-1]))
        attempts = int(np.sum((seg_fav - prev_min >= S.ATT_V * p.V) & (prev_min < 0) & (seg_fav < 0)))
        stop_gross = -a
        stop_net = stop_gross - R.FEE_SIDE * (2.0 + dirn * stop_gross) - R.BASE_RT   # funding ignored
        out.append({"e": tr["e"], "dir": dirn, "k_a": k_a, "recovered": recovered,
                    "recovered_net_be": bool((p.r_fav[k_a + 1:k_end] >= be).any()),
                    "net_wait": tr["net"], "net_stop": stop_net, "value_of_waiting": tr["net"] - stop_net,
                    "H4_rel": rel["H4"], "H1_rel": rel["H1"], "M_rel": rel["M"],
                    "align": sum(1 for h in rel.values() if h == 1), "h4_realigned_after": h4_re,
                    "thesis_age_h": float(f.thesis_age[B] / 60), "VR": int(f.VR[B]),
                    "pbz": float(f.pbz[B]) if not np.isnan(f.pbz[B]) else None,
                    "funding_signed": float(dirn * f.fund[B]) if not np.isnan(f.fund[B]) else None,
                    "basis_signed": float(dirn * f.basis[B]) if not np.isnan(f.basis[B]) else None,
                    "adverse_depth_v": float(-p.r_adv[:k_a + 1].min() / p.V), "attempts": attempts,
                    "exit_kind": tr["kind"]})
    return out


def hold_study(boot: R.Bootstrap, ctx: S.Ctx, trades: list[dict]) -> dict:
    out = {}
    for name, a in ANCHORS:
        recs = anchor_records(ctx, trades, a)
        if not recs:
            out[name] = {"n": 0}
            continue
        groups = {
            "A_D_held_all": [True] * len(recs),
            "B_H4_opposite": [r["H4_rel"] == -1 for r in recs],
            "C_H4_realigns_after": [r["h4_realigned_after"] is True for r in recs],
            "C0_H4_opp_no_realign": [r["h4_realigned_after"] is False for r in recs],
            "D_all_opposite": [r["H4_rel"] == -1 and r["H1_rel"] == -1 and r["M_rel"] == -1 for r in recs],
            "E_H4_agree": [r["H4_rel"] == 1 for r in recs],
        }
        res = {"n": len(recs), "share_of_trades": len(recs) / len(trades),
               "recovery_rate": float(np.mean([r["recovered"] for r in recs])),
               "value_of_waiting": dist([r["value_of_waiting"] for r in recs]), "groups": {}}
        rec_flag = np.array([r["recovered"] for r in recs], dtype=float)
        vow = np.array([r["value_of_waiting"] for r in recs])
        days = np.array([r["e"] // 1440 for r in recs])
        eps = ctx.f.episode[np.array([r["e"] - 1 for r in recs])]
        for g, flags in groups.items():
            m = np.array(flags, dtype=bool)
            item = {"n": int(m.sum()), "recovery_rate": float(rec_flag[m].mean()) if m.any() else None,
                    "value_of_waiting_mean": float(vow[m].mean()) if m.any() else None,
                    "value_of_waiting_median": float(np.median(vow[m])) if m.any() else None,
                    "net_wait_mean": float(np.mean([r["net_wait"] for r, mm in zip(recs, m) if mm])) if m.any() else None}
            if m.any() and (~m).any() and g != "A_D_held_all":
                item["recovery_diff_vs_rest"] = boot.infer(days[m], rec_flag[m], ref=(days[~m], rec_flag[~m]))
                item["vow_diff_vs_rest"] = boot.infer(days[m], vow[m], ref=(days[~m], vow[~m]))
                item["cluster_recovery_diff"] = cluster_diff(eps, rec_flag, m)
            res["groups"][g] = item
        desc = {}
        for key in ("thesis_age_h", "pbz", "funding_signed", "basis_signed", "adverse_depth_v",
                    "attempts", "align", "VR"):
            desc[key] = {"recovered": dist([r[key] for r in recs if r["recovered"]]),
                         "not_recovered": dist([r[key] for r in recs if not r["recovered"]])}
        res["descriptive"] = desc
        out[name] = res
    return out


# --------------------------------------------------------------------------- leverage / account


def account(t: dict, lev: int, scenario: str) -> float:
    if scenario == "A":
        notional, margin = 1.0, 1.0 / lev
    else:
        margin, notional = MARGIN_FRACTION, MARGIN_FRACTION * lev
    if t["kind"] == "LIQUIDATION":
        return -margin - notional * (R.FEE_SIDE + t["funding"])
    return notional * t["net"]


def curve_stats(pnl: np.ndarray) -> dict:
    c = np.concatenate(([0.0], np.cumsum(pnl)))
    peak = np.maximum.accumulate(c)
    dd = c - peak
    i = int(np.argmin(dd))
    rec = np.flatnonzero(c[i:] >= peak[i])
    return {"final": float(c[-1]), "max_drawdown": float(dd.min()),
            "recovered_after_trades": int(rec[0]) if len(rec) else None,
            "max_equity_peak": float(peak[-1])}


def leverage_study(ctx: S.Ctx, trades: list[dict], x_rule, p_rule) -> dict:
    out = {}
    for lev in LEVERAGES:
        liq = 1.0 / lev - S.MMR
        saf = S.s_cat(lev)
        with_saf = resim(ctx, trades, x_rule, p_rule, [("SAFETY", saf)])
        liq_only = resim(ctx, trades, x_rule, p_rule, [("LIQUIDATION", liq)])
        for tr in with_saf:
            if tr["kind"] == "SAFETY" and tr["gross"] <= -liq:
                tr["kind"] = "LIQUIDATION"
        s = summarize(with_saf)
        kinds = s["exit_kinds"]
        item = {"leverage": lev, "liq_distance": liq, "safety_distance": saf,
                "safety_rate": kinds.get("SAFETY", 0) / len(with_saf),
                "liquidation_rate_with_safety": kinds.get("LIQUIDATION", 0) / len(with_saf),
                "liquidation_rate_no_safety": sum(t["kind"] == "LIQUIDATION" for t in liq_only) / len(liq_only),
                "trade": {k: s[k] for k in ("n", "net_mean", "pf", "win_rate", "avg_winner", "avg_loser",
                                             "winner_loser_ratio", "mfe_capture", "tail_p5", "tail_min",
                                             "exit_kinds")}}
        for sc in ("A", "B"):
            pnl = np.array([account(t, lev, sc) for t in with_saf])
            item[f"account_{sc}"] = {"pnl_mean": float(pnl.mean()), "pnl_sum": float(pnl.sum()),
                                     "p5": float(np.percentile(pnl, 5)), "min": float(pnl.min()),
                                     "max_single_loss": float(-pnl.min()), "curve": curve_stats(pnl),
                                     "margin": (1.0 / lev) if sc == "A" else MARGIN_FRACTION,
                                     "notional": 1.0 if sc == "A" else MARGIN_FRACTION * lev}
        item["diagnostic_only"] = lev == 100
        out[f"L{lev}"] = item
    return out


# --------------------------------------------------------------------------- controls


def random_control(ctx: S.Ctx, n: int, x_rule, p_rule, stops, observed: float, first: int,
                   reps: int = N_RANDOM, seed: int = R.SEED) -> dict:
    rng = np.random.default_rng(seed)
    means = []
    hi = ctx.last - S.CENSOR_MIN - 1
    tradable = np.flatnonzero(~F.no_trade(ctx.f)[first:hi]) + first
    d = ctx.f.st["D"]
    for _ in range(reps):
        ts = rng.choice(tradable, size=n, replace=True)
        dirs = d[ts]                                   # direction = completed-day flow at that boundary
        nets = []
        for t, dirn in zip(ts, dirs):
            p = S.make_path(ctx, int(t) + 1, int(dirn))
            nets.append(S.simulate(ctx, p, x_rule, p_rule, stops)["net"])
        means.append(float(np.mean(nets)))
    means = np.array(means)
    return {"reps": reps, "n_per_rep": n, "mean_of_means": float(means.mean()),
            "p5": float(np.percentile(means, 5)), "p95": float(np.percentile(means, 95)),
            "share_ge_observed": float((means >= observed).mean()), "observed": observed}


def controls(boot, ctx: S.Ctx, trades: list[dict], x_rule, p_rule, stops, first: int) -> dict:
    s = summarize(trades)
    out = {"random_entry": random_control(ctx, len(trades), x_rule, p_rule, stops, s["net_mean"], first)}
    for k in SHIFT_DAYS:
        sh = resim(ctx, trades, x_rule, p_rule, stops, shift=k * 1440)
        out[f"time_shift_{k}d"] = {"n": len(sh), "net_mean": summarize(sh)["net_mean"] if sh else None,
                                   "pf": summarize(sh)["pf"] if sh else None}
    opp = mirror(ctx, trades)
    out["opposite_placebo"] = {"summary": {k: summarize(opp)[k] for k in ("n", "net_mean", "pf", "win_rate")},
                               "paired_method_minus_placebo": paired(boot, ctx, trades, opp)}
    dirn = resim(ctx, trades, None, "P0", stops)
    out["directional_baseline_censor_only"] = {k: summarize(dirn)[k] for k in
                                               ("n", "net_mean", "pf", "win_rate", "censor_rate", "catastrophic_rate")}
    out["directional_paired_method_minus_baseline"] = paired(boot, ctx, trades, dirn)
    for name, lvl in (("fixed_stop_0.5pct", 0.005), ("fixed_stop_1.0pct", 0.010)):
        fs = resim(ctx, trades, "X1", "P0", stops + [("FIXED_STOP", lvl)])
        out[name] = {"summary": {k: summarize(fs)[k] for k in ("n", "net_mean", "pf", "win_rate", "avg_winner",
                                                                 "avg_loser", "exit_kinds")},
                     "paired_method_minus_fixed": paired(boot, ctx, trades, fs)}
    return out


# --------------------------------------------------------------------------- verdict


def verdict(m: dict, holm_p: float) -> tuple[str, dict]:
    s, c, lev = m["summary"], m["controls"], m["leverage"]
    rec = m["recommended_leverage"]
    saf = lev[f"L{rec}"]["safety_rate"] + lev[f"L{rec}"]["liquidation_rate_with_safety"] if rec else 1.0
    ci = m["inference"]["ci95"]
    opp_ci = c["opposite_placebo"]["paired_method_minus_placebo"]["ci95"]
    gates_c = {
        "n>=100": s["n"] >= GATE["n_c"], "net_mean>0": s["net_mean"] > 0,
        "ci_excludes_0_after_holm": ci[0] > 0 and holm_p <= 0.05,
        "pf>=1.25": (s["pf"] or 0) >= GATE["pf_c"],
        "winner_loser>=1.5": (s["winner_loser_ratio"] or 0) >= GATE["wl_c"],
        "random_share<=0.05": c["random_entry"]["share_ge_observed"] <= GATE["random_c"],
        "opposite_ci_excludes_0": opp_ci[0] > 0,
        "mfe_capture>=0.35": (s["mfe_capture"] or 0) >= GATE["capture_c"],
        "safety_liq<=10pct_at_rec_lev": saf <= GATE["safety_c"],
    }
    gates_b = {
        "n>=60": s["n"] >= GATE["n_b"], "net_mean>0": s["net_mean"] > 0,
        "pf>=1.10": (s["pf"] or 0) >= GATE["pf_b"],
        "above_random_mean": s["net_mean"] > c["random_entry"]["mean_of_means"],
        "above_opposite": s["net_mean"] > c["opposite_placebo"]["summary"]["net_mean"],
    }
    if all(gates_c.values()):
        v = "C. PERSONAL_METHOD_TRAIN_CANDIDATE"
    elif all(gates_b.values()):
        v = "B. WEAK_METHOD_CANDIDATE"
    else:
        v = "A. NO_PERSONAL_METHOD_FOUND"
    return v, {"C": gates_c, "B": gates_b}


# --------------------------------------------------------------------------- run


def run(out: Path | None = OUT, random_reps: int = N_RANDOM) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    log = data.ReadLog()
    par = data.parity(log)
    vol, cov = data.load_train(log)
    px = data.load_train_prices(log)
    funding = data.load_train_funding(log)
    basis = data.load_train_basis(log)
    f = F.build(vol, px, funding, basis)
    mk = R.make_market(px, funding)
    ctx = S.Ctx(f=f, mk=mk)
    first = int(np.argmax(F.defined(f)))
    boot = R.Bootstrap(len(px.open) // 1440)
    stops = ref_stops()
    rep: dict = {"contract_sha256": sha, "parity": par, "coverage": cov, "component_leverage": COMPONENT_LEV,
                 "censor_min": S.CENSOR_MIN, "first_defined_boundary": first}

    # ---- entries under the reference policy X1/P0
    fam_trades: dict[str, list[dict]] = {}
    ent: dict = {}
    for fam in E.FAMILIES:
        ons = E.onsets(f, fam)
        trades = sequential(ctx, ons, "X1", "P0", stops)
        fam_trades[fam] = trades
        s = summarize(trades)
        ent[fam] = {"name": E.NAMES[fam], "raw_onsets": len(ons), "summary": s,
                    "inference": infer(boot, ctx, trades) if trades else None,
                    "by_direction": {DIR_NAME[d]: summarize([t for t in trades if t["dir"] == d])
                                     for d in (1, -1)}}
    rep["entries"] = ent

    # ---- reference population (union, dedup by (e, dir))
    seen, ref = set(), []
    for fam in E.FAMILIES:
        for t in fam_trades[fam]:
            if (t["e"], t["dir"]) not in seen:
                seen.add((t["e"], t["dir"]))
                ref.append(dict(t))
    ref.sort(key=lambda t: t["e"])
    rep["reference_population"] = {"n": len(ref), "summary": summarize(ref), "inference": infer(boot, ctx, ref)}

    # ---- hold / recovery
    rep["hold_recovery"] = hold_study(boot, ctx, ref)

    # ---- loss exits
    xs: dict = {}
    for x in ("X2", "X3", "X4", "X5"):
        tr = resim(ctx, ref, x, "P0", stops)
        s = summarize(tr)
        xs[x] = {"summary": s, "paired_vs_X1": paired(boot, ctx, tr, ref),
                 "avg_winner_change": (s["avg_winner"] / rep["reference_population"]["summary"]["avg_winner"] - 1.0)
                 if rep["reference_population"]["summary"]["avg_winner"] else None}
    rep["loss_exits"] = xs

    # ---- protections
    ps: dict = {}
    for p_ in ("P1", "P2", "P3", "P4"):
        tr = resim(ctx, ref, "X1", p_, stops)
        s = summarize(tr)
        ps[p_] = {"summary": s, "paired_vs_P0": paired(boot, ctx, tr, ref),
                  "fired_share": sum(t["kind"] == "PROTECT" for t in tr) / len(tr),
                  "mfe_capture_change": (s["mfe_capture"] or 0) - (rep["reference_population"]["summary"]["mfe_capture"] or 0)}
        armed = [t for t in ref if t["mfe"] >= S.ARM_V * f.V[t["e"] - 1]]
        ps[p_]["armed_share_in_reference"] = len(armed) / len(ref)
    rep["protections"] = ps

    # ---- quality splits (reference population, X1/P0)
    qs: dict = {}
    net = np.array([t["net"] for t in ref])
    days = days_of(ref)
    dirs = np.array([t["dir"] for t in ref])
    eps = f.episode[np.array([t["e"] - 1 for t in ref])]
    qflags = {
        "Qa_full_alignment": np.array([all(f.st[h][t["e"] - 1] == t["dir"] for h in ("H4", "H1", "M")) for t in ref]),
        "Qb_price_regime_agrees": np.array([f.PR[t["e"] - 1] == t["dir"] for t in ref]),
        "Qc_vol_high": np.array([f.VR[t["e"] - 1] == 1 for t in ref]),
    }
    for name, g in qflags.items():
        item = {"n1": int(g.sum()), "n0": int((~g).sum()), "net_mean1": float(net[g].mean()) if g.any() else None,
                "net_mean0": float(net[~g].mean()) if (~g).any() else None}
        if g.any() and (~g).any():
            item["diff"] = boot.infer(days[g], net[g], ref=(days[~g], net[~g]))
            item["cluster"] = cluster_diff(eps, net, g)
            bd = {}
            for d in (1, -1):
                m = dirs == d
                if (g & m).any() and (~g & m).any():
                    bd[DIR_NAME[d]] = float(net[g & m].mean() - net[~g & m].mean())
            item["by_direction"] = bd
            item["adopted"] = bool(item["diff"]["p"] is not None and item["diff"]["p"] <= P_SELECT
                                   and len(bd) == 2 and np.sign(list(bd.values())[0]) == np.sign(list(bd.values())[1])
                                   and np.sign(item["diff"]["mean"]) == np.sign(list(bd.values())[0]))
        else:
            item["adopted"] = False
        qs[name] = item
    rep["quality_splits"] = qs

    # ---- composition
    ref_aw = rep["reference_population"]["summary"]["avg_winner"]
    x_choice, best = "X1", 0.0
    for x, item in xs.items():
        d = item["paired_vs_X1"]
        if d["mean"] > best and d["p"] is not None and d["p"] <= P_SELECT and item["summary"]["avg_winner"] >= 0.9 * ref_aw:
            x_choice, best = x, d["mean"]
    p_choice, best = "P0", 0.0
    for p_, item in ps.items():
        d = item["paired_vs_P0"]
        if d["mean"] > best and d["p"] is not None and d["p"] <= P_SELECT and item["mfe_capture_change"] > 0:
            p_choice, best = p_, d["mean"]
    ranked = sorted([fam for fam in E.FAMILIES if ent[fam]["summary"]["n"] >= MIN_N_METHOD],
                    key=lambda fam: -ent[fam]["summary"]["net_mean"])[:3]
    rep["composition"] = {"loss_exit": x_choice, "protection": p_choice, "families_ranked": ranked,
                          "rule": "contract section 11"}

    # ---- methods
    methods: dict = {}
    pvals: dict[str, float] = {}
    for i, fam in enumerate(ranked, 1):
        name = f"METHOD_{i}"
        ons = E.onsets(f, fam)
        trades = sequential(ctx, ons, x_choice, p_choice, stops)
        s = summarize(trades)
        inf = infer(boot, ctx, trades)
        pvals[name] = inf["p"] if inf["p"] is not None else 1.0
        ctl = controls(boot, ctx, trades, x_choice, p_choice, stops, first)
        lev = leverage_study(ctx, trades, x_choice, p_choice)
        rec = None
        for L in PRIMARY_LEV:
            if lev[f"L{L}"]["safety_rate"] + lev[f"L{L}"]["liquidation_rate_with_safety"] <= GATE["safety_c"]:
                rec = L
        stress = resim(ctx, trades, x_choice, p_choice, stops, fee_side=R.STRESS_FEE_SIDE, spread_rt=R.STRESS_RT)
        c48 = sequential(ctx, ons, x_choice, p_choice, stops, censor=2880)
        methods[name] = {"family": fam, "family_name": E.NAMES[fam], "loss_exit": x_choice, "protection": p_choice,
                         "summary": s, "inference": inf,
                         "by_direction": {DIR_NAME[d]: summarize([t for t in trades if t["dir"] == d]) for d in (1, -1)},
                         "controls": ctl, "leverage": lev, "recommended_leverage": rec,
                         "stress_costs": {k: summarize(stress)[k] for k in ("n", "net_mean", "pf")},
                         "censor_48h": {k: summarize(c48)[k] for k in ("n", "net_mean", "pf", "censor_rate")},
                         "trades": [{k: t[k] for k in ("e", "dir", "exit_bar", "kind", "gross", "net", "mfe", "mae", "hold")}
                                    for t in trades]}
    hp = R.holm(pvals) if pvals else {}
    for name, m in methods.items():
        m["holm_p"] = hp.get(name)
        m["verdict"], m["gates"] = verdict(m, hp.get(name, 1.0))
    rep["methods"] = methods
    order = {"C": 0, "B": 1, "A": 2}
    passing = sorted([n for n, m in methods.items() if m["verdict"][0] in ("B", "C")],
                     key=lambda n: (order[methods[n]["verdict"][0]], -methods[n]["summary"]["net_mean"]))[:2]
    rep["frozen_for_validation"] = passing
    rep["overall_verdict"] = (min((m["verdict"] for m in methods.values()), key=lambda v: order[v[0]])
                              if methods else "A. NO_PERSONAL_METHOD_FOUND")
    rep["multiple_testing"] = {"planned_comparisons": 19, "holm_over_methods": hp,
                               "families_examined": len(E.FAMILIES), "loss_exits": 4, "protections": 4,
                               "quality_splits": 3, "methods": len(methods)}
    rep["read_log"] = log.entries
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=_json))
    return rep


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and math.isinf(o):
        return "inf"
    raise TypeError(type(o))


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({"composition": r["composition"], "verdict": r["overall_verdict"],
                      "frozen": r["frozen_for_validation"]}, indent=1))
