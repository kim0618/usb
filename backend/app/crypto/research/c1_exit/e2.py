"""E2: dynamic exits on the E1 C1 entry universe. Exploratory only.

Frozen contract: docs/crypto/c1_exit/C1_EXIT_E2_DYNAMIC_CONTRACT_V1.md (section numbers below refer to it).

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_exit.e2

Entry, cost accounting and the C1 state come from e1 (unchanged); nothing here is wired to production.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np

from .. import dataset
from .. import derivatives_flow as DF
from .. import features as F
from ..d6c import contract as D6C
from ..d6c import metrics as M
from ..dataset import _read
from . import e1

CONTRACT = Path("docs/crypto/c1_exit/C1_EXIT_E2_DYNAMIC_CONTRACT_V1.md")
OUTDIR = e1.OUTDIR
FREEZE = OUTDIR / "e2_freeze_v1.json"

MIN = dataset.MINUTE_MS
FIVE = 5 * MIN
MAX_HOLD = 480
E0_H = 240
REF_H = (30, 60, 120, 480)
TAKER = 0.00055
STOP_K, STOP_MIN, STOP_MAX, SIGMA_BARS = 2.5, 0.01, 0.10, 240
HIGH_CUT = 0.0010924950359531157
LARGE_LOSS_BP = -300.0
BOOT_ITER = 10_000
SEED_NET, SEED_DELTA, SEED_DAY = 20261011, 20261012, 20261013
E1_EVENTS = 1603
AUDIT_SAMPLE = 20

MAIN = ("B1", "B3", "B4", "C1x", "C2x", "C3x")
SENS = {"B3-G40": ("B3", 1.0, 0.40), "B3-G60": ("B3", 1.0, 0.60), "B3-A05": ("B3", 0.5, 0.50),
        "B3-A15": ("B3", 1.5, 0.50), "B4-A05": ("B4", 0.5, None), "B4-A15": ("B4", 1.5, None)}
CATEGORY = {"B1": "STOP", "B3": "PROFIT_PROTECTION", "B4": "PROFIT_PROTECTION", "C1x": "EDGE", "C2x": "EDGE", "C3x": "EDGE"}


def check_freeze() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract hash mismatch: frozen {frozen} now {now}")
    return now


# ------------------------------------------------------------------ window and triggers (section 3)
# Every trigger reads a window of local arrays w[name][0..MAX_HOLD], index 0 = entry bar e. It returns
# (exit_local, exit_ref_or_None, triggered_local_k_or_None). exit_ref None means open[exit].
def window(A: dict, e: int) -> dict:
    return {k: v[e: e + MAX_HOLD + 1].copy() for k, v in A.items()}


def _first(mask: np.ndarray) -> int | None:
    hit = np.nonzero(mask[:MAX_HOLD])[0]
    return int(hit[0]) if len(hit) else None


def _censored():
    return MAX_HOLD, None, None


def trig_b1(w: dict, fill: float, sig: float):
    """D6-C X2 stop with an 8H horizon: intrabar on mark low, gap at the final open, adverse fill."""
    stop = fill * (1 - min(max(STOP_K * sig, STOP_MIN), STOP_MAX))
    inside = w["mark_low"][:MAX_HOLD]
    hit = np.nonzero(inside <= stop)[0]
    if len(hit):
        j = int(hit[0])
        return j, min(stop, float(w["open"][j])), j
    if float(w["open"][MAX_HOLD]) <= stop:
        return MAX_HOLD, min(stop, float(w["open"][MAX_HOLD])), MAX_HOLD
    return _censored()


def _running(w: dict, fill: float):
    hi = np.where(np.isfinite(w["high"]), w["high"], -np.inf)
    peak_ret = np.maximum.accumulate(hi) / fill - 1
    cur = w["close"] / fill - 1
    return peak_ret, cur


def trig_b3(w: dict, fill: float, sig: float, act: float = 1.0, give: float = 0.50):
    peak_ret, cur = _running(w, fill)
    with np.errstate(invalid="ignore"):
        cond = (peak_ret >= act * sig) & (cur <= (1 - give) * peak_ret)
    k = _first(cond)
    return (k + 1, None, k) if k is not None else _censored()


def trig_b4(w: dict, fill: float, sig: float, act: float = 1.0):
    peak_ret, cur = _running(w, fill)
    with np.errstate(invalid="ignore"):
        est = cur - TAKER * (1 + w["close"] / fill) - w["fund_sofar"]
        cond = (peak_ret >= act * sig) & (est <= 0)
    k = _first(cond)
    return (k + 1, None, k) if k is not None else _censored()


def _confirmed(w: dict, cond: np.ndarray, need: int):
    run = 0
    for j in range(MAX_HOLD):
        if not w["eval5"][j]:
            continue
        run = run + 1 if cond[j] else 0
        if run >= need:
            return j + 1, None, j
    return _censored()


def trig_c1x(w: dict, fill: float, sig: float):
    return _confirmed(w, w["s1_bucket"] >= 2, 2)


def trig_c2x(w: dict, fill: float, sig: float):
    with np.errstate(invalid="ignore"):
        return _confirmed(w, w["oi_chg"] > 0, 2)


def trig_c3x(w: dict, fill: float, sig: float):
    with np.errstate(invalid="ignore"):
        return _confirmed(w, np.isfinite(w["rv24h"]) & (w["rv24h"] <= HIGH_CUT), 1)


def trig_fixed(h: int):
    return lambda w, fill, sig: (h, None, None)


def trigger_fn(cid: str):
    if cid in SENS:
        base, act, give = SENS[cid]
        return (lambda w, f, s: trig_b3(w, f, s, act, give)) if base == "B3" else (lambda w, f, s: trig_b4(w, f, s, act))
    return {"B1": trig_b1, "B3": trig_b3, "B4": trig_b4, "C1x": trig_c1x, "C2x": trig_c2x, "C3x": trig_c3x}[cid]


def mask_after(w: dict, k: int) -> dict:
    """Section 11 audit: everything after local bar k is erased."""
    out = {}
    for name, v in w.items():
        a = v.copy()
        if a.dtype == bool:
            a[k + 1:] = False
        elif np.issubdtype(a.dtype, np.integer):
            a[k + 1:] = -1
        else:
            a[k + 1:] = np.nan
        out[name] = a
    return out


# ------------------------------------------------------------------ inputs
def load_all():
    g = dataset.load()
    x = DF.load_inputs(g)
    st = e1.c1_state(g, x)
    n_days = len(g["ts"]) // F.DAY_BARS
    mark = _read("mark_1m", ["low"])
    idx = np.searchsorted(mark["timestamp_ms"], g["ts"])
    if idx.max() >= len(mark["timestamp_ms"]) or not np.array_equal(mark["timestamp_ms"][idx], g["ts"]):
        raise SystemExit("mark_1m does not cover the grid")
    ts = g["ts"]
    fts = np.asarray(g["funding_ts"], dtype=np.int64); fr = np.asarray(g["funding_rate"], dtype=float)
    o = np.argsort(fts); fts, fr = fts[o], fr[o]
    cum = np.r_[0.0, np.cumsum(fr)]
    cum_at = lambda t: cum[np.searchsorted(fts, t, side="right")]        # sum of rates with settlement <= t
    A = {"open": g["open"].astype(float), "high": g["high"].astype(float), "low": g["low"].astype(float),
         "close": g["close"].astype(float), "mark_low": mark["low"][idx].astype(float),
         "eval5": ((ts + MIN) % FIVE) == 0, "s1_bucket": F.bucketize(st["s1"], n_days),
         "oi_chg": st["oi_chg"].astype(float), "rv24h": st["rv24h"].astype(float), "s1": st["s1"].astype(float),
         "cum_close": cum_at(ts + MIN)}
    return g, st, A, cum_at


# ------------------------------------------------------------------ simulation
def simulate(cid: str, ev: list[dict], A: dict, ex: e1.Executor, cum_at) -> list[dict]:
    fn = trigger_fn(cid)
    H, L = A["high"], A["low"]
    rows = []
    for v in ev:
        e = v["e"]
        w = window(A, e)
        w["fund_sofar"] = w["cum_close"] - cum_at(v["entry_ts"])
        local, ref, k = fn(w, v["fill"], v["sig"])
        xb = e + local
        sim = ex.run(e, xb, ref)
        if abs(sim.entry_fill - v["fill"]) > 1e-9:
            raise SystemExit(f"entry fill mismatch at {e}: {sim.entry_fill} vs {v['fill']}")
        hi, lo = H[e:xb], L[e:xb]
        mfe = (np.nanmax(hi) / sim.entry_ref - 1) * 1e4 if len(hi) else np.nan
        mae = (np.nanmin(lo) / sim.entry_ref - 1) * 1e4 if len(lo) else np.nan
        snap = e + (k if k is not None else MAX_HOLD - 1)
        rows.append({"signal_id": v["signal_id"], "candidate_id": cid, "decision_bar": v["t"], "entry_bar": e,
                     "entry_ts_ms": v["entry_ts"], "entry_price": sim.entry_ref, "exit_bar": xb,
                     "exit_ts_ms": int(A["ts"][xb]), "exit_price": sim.exit_ref,
                     "triggered_at_bar": None if k is None else e + k,
                     "trigger_reason": None if k is None else cid, "censored_by_max_hold": k is None,
                     "holding_min": int(local), "gross_bp": sim.gross_bp, "spread_bp": sim.spread_bp, "fee_bp": sim.fee_bp,
                     "funding_bp": sim.funding_bp, "total_cost_bp": sim.gross_bp - sim.net_bp, "net_bp": sim.net_bp,
                     "net_stress_bp": sim.net_bp - e1.STRESS_EXTRA * 1e4, "identity_ok": sim.identity_ok,
                     "mfe_before_bp": mfe, "mae_before_bp": mae,
                     "capture": (sim.gross_bp / mfe) if (np.isfinite(mfe) and mfe > 0) else None,
                     "s1_at": float(A["s1"][snap]), "s1_bucket_at": int(A["s1_bucket"][snap]),
                     "oi_chg_at": float(A["oi_chg"][snap]), "rv24h_at": float(A["rv24h"][snap]), "fold": v["fold"]})
    return rows


def audit_triggers(cid: str, ev: list[dict], A: dict, cum_at, rng) -> dict:
    fn = trigger_fn(cid)
    pick = rng.choice(len(ev), size=min(AUDIT_SAMPLE, len(ev)), replace=False)
    bad = 0
    for i in pick:
        v = ev[int(i)]
        w = window(A, v["e"]); w["fund_sofar"] = w["cum_close"] - cum_at(v["entry_ts"])
        full = fn(w, v["fill"], v["sig"])
        cut = full[0] if full[2] is None or cid == "B1" else full[2]
        cut = min(cut, MAX_HOLD)
        again = fn(mask_after(w, cut), v["fill"], v["sig"])
        same = again[0] == full[0] and (again[1] is None) == (full[1] is None) and \
            (full[1] is None or abs(again[1] - full[1]) < 1e-9)
        bad += 0 if same else 1
    return {"sampled": int(len(pick)), "mismatches": bad, "pass": bad == 0}


# ------------------------------------------------------------------ statistics (section 7)
def block_samples(entry_ts: np.ndarray, vals: np.ndarray, seed: int) -> tuple[float, np.ndarray]:
    """Same algorithm as d6c.metrics.bootstrap_ci, returning the draws."""
    day = entry_ts // e1.DAY_MS
    days, inv = np.unique(day, return_inverse=True)
    sums = np.bincount(inv, weights=vals); cnts = np.bincount(inv).astype(float)
    n = len(days); rng = np.random.default_rng(seed)
    blocks, draws, off = n - 7 + 1, int(np.ceil(n / 7)), np.arange(7)
    out = np.empty(BOOT_ITER)
    for i in range(BOOT_ITER):
        st = rng.integers(0, blocks, size=draws)
        idx = (st[:, None] + off[None, :]).ravel()[:n]
        out[i] = sums[idx].sum() / cnts[idx].sum()
    return float(sums.sum() / cnts.sum()), out


def ci_of(samples: np.ndarray) -> list[float]:
    return [float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))]


def pf(net: np.ndarray):
    neg = net[net < 0]
    return float(net[net > 0].sum() / abs(neg.sum())) if len(neg) else "INF"


def basic(rows: list[dict], key: str = "net_bp") -> dict:
    net = np.array([r[key] for r in rows])
    loss = net[net <= 0]
    return {"trades": len(rows), "net_mean": float(net.mean()), "net_median": float(np.median(net)),
            "win_rate": float((net > 0).mean()), "pf": pf(net), "total_net_bp": float(net.sum()),
            "p5": float(np.percentile(net, 5)), "p10": float(np.percentile(net, 10)), "worst": float(net.min()),
            "avg_loss": float(loss.mean()) if len(loss) else None, "avg_win": float(net[net > 0].mean()) if (net > 0).any() else None,
            "large_loss_freq": float((net <= LARGE_LOSS_BP).mean())}


def retention(rows: list[dict]) -> float:
    ok = [r for r in rows if np.isfinite(r["mfe_before_bp"]) and r["mfe_before_bp"] > 0]
    return float(sum(r["gross_bp"] for r in ok) / sum(r["mfe_before_bp"] for r in ok))


def top_removal(net: np.ndarray) -> dict:
    o = np.sort(net)[::-1]
    return {lab: float(o[k:].mean()) for lab, k in (("top1", 1), ("top3", 3), ("top1pct", math.ceil(.01 * len(net))),
                                                   ("top5pct", math.ceil(.05 * len(net))))}


def folds_of(rows: list[dict], e0: list[dict]) -> dict:
    out = {}
    for f in range(1, 10):
        c = np.array([r["net_bp"] for r in rows if r["fold"] == f]); b = np.array([r["net_bp"] for r in e0 if r["fold"] == f])
        out[f"F{f}"] = {"trades": int(len(c)), "net": float(c.mean()) if len(c) else None, "e0_net": float(b.mean()) if len(b) else None,
                        "delta": float((c - b).mean()) if len(c) else None}
    return out


def holding(rows: list[dict]) -> dict:
    h = np.array([r["holding_min"] for r in rows])
    return {"mean": float(h.mean()), "median": float(np.median(h)), "p90": float(np.percentile(h, 90)),
            "censored_share": float(np.mean([r["censored_by_max_hold"] for r in rows])),
            "trigger_rate": float(np.mean([not r["censored_by_max_hold"] for r in rows]))}


def costs(rows: list[dict]) -> dict:
    return {k: float(np.mean([r[k] for r in rows])) for k in ("gross_bp", "spread_bp", "fee_bp", "funding_bp", "total_cost_bp")}


def counterfactual(cid: str, rows: list[dict], e0: list[dict], winners: dict) -> dict:
    et = np.array([r["entry_ts_ms"] for r in rows], dtype=np.int64)
    net = np.array([r["net_bp"] for r in rows]); base = np.array([r["net_bp"] for r in e0])
    delta = net - base
    _, ds = block_samples(et, delta, SEED_DELTA)
    _, ns = block_samples(et, net, SEED_NET)
    st = basic(rows, "net_stress_bp")
    mae = np.array([r["mae_before_bp"] for r in rows]); mae = mae[np.isfinite(mae)]
    caps = [r["capture"] for r in rows if r["capture"] is not None]
    gross = {r["signal_id"]: r["gross_bp"] for r in rows}
    win = {lab: float(sum(gross[s] for s in ids) / sum(e0g for e0g in vals)) for lab, (ids, vals) in winners.items()}
    fo = folds_of(rows, e0)
    return {"candidate": cid, "category": CATEGORY.get(cid, CATEGORY.get(cid.split("-")[0], "REFERENCE")),
            "base": basic(rows), "net_ci_block7": ci_of(ns), "stress": {"net_mean": st["net_mean"], "net_median": st["net_median"]},
            "delta": {"mean": float(delta.mean()), "median": float(np.median(delta)), "ci_block7": ci_of(ds),
                      "ci_day_cluster": [e1.day_cluster_ci(et, delta, SEED_DAY)[k] for k in ("lo", "hi")],
                      "p_trade_better": float((delta > 0).mean()), "p_boot_mean_positive": float((ds > 0).mean())},
            "tail": {"mae_before_mean": float(mae.mean()), "mae_before_p5": float(np.percentile(mae, 5))},
            "capture_median": float(np.median(caps)) if caps else None, "capture_defined": len(caps),
            "profit_retention": retention(rows), "winner_preservation": win, "top_removal": top_removal(net),
            "folds": fo, "positive_folds": sum(1 for v in fo.values() if v["net"] is not None and v["net"] > 0),
            "positive_delta_folds": sum(1 for v in fo.values() if v["delta"] is not None and v["delta"] > 0),
            "holding": holding(rows), "costs": costs(rows), "identity_violations": sum(1 for r in rows if not r["identity_ok"])}


def executable(rows: list[dict]) -> tuple[dict, list[dict]]:
    """Section 6: one position. Rows already hold the trade each event would produce."""
    taken, skipped, last_exit = [], 0, -1
    for r in sorted(rows, key=lambda r: r["decision_bar"]):
        if r["decision_bar"] >= last_exit:
            taken.append(r); last_exit = r["exit_bar"]
        else:
            skipped += 1
    net = np.array([r["net_bp"] for r in taken])
    eq = np.cumsum(net); dd = float((np.maximum.accumulate(np.r_[0.0, eq]) - np.r_[0.0, eq]).max())
    oos_min = (dataset.RESEARCH_END_MS - e1.ms("2022-01-01T00:00:00Z")) / MIN
    d = basic(taken)
    d.update({"skipped_signals": skipped, "avg_hold_min": float(np.mean([r["holding_min"] for r in taken])),
              "exposure": float(sum(r["holding_min"] for r in taken) / oos_min), "max_dd_bp": dd,
              "stress_net_mean": float(np.mean([r["net_stress_bp"] for r in taken])),
              "folds": {f"F{f}": float(np.mean([r["net_bp"] for r in taken if r["fold"] == f])) if any(r["fold"] == f for r in taken) else None
                        for f in range(1, 10)}})
    return d, taken


def status(c: dict, e0: dict, sens: dict[str, dict]) -> tuple[str, dict]:
    d = c["delta"]
    not_worse = d["ci_block7"][1] >= 0 and d["mean"] >= -3.0
    A = d["mean"] > 0 and d["p_boot_mean_positive"] >= 0.80
    llf0, llf = e0["base"]["large_loss_freq"], c["base"]["large_loss_freq"]
    B = (c["base"]["p5"] - e0["base"]["p5"] >= 30.0) or (llf0 > 0 and (llf0 - llf) / llf0 >= 0.25)
    C = c["profit_retention"] - e0["profit_retention"] >= 0.05
    G1 = c["top_removal"]["top3"] > 0
    G2 = c["winner_preservation"]["top5pct"] >= 0.50
    G3 = c["positive_folds"] >= 5
    G4 = c["stress"]["net_mean"] > 0
    sens_ok = all(s["delta"]["ci_block7"][1] >= 0 and s["delta"]["mean"] >= -3.0 for s in sens.values())
    flags = {"NOT_WORSE": not_worse, "A_paired": A, "B_tail": B, "C_retention": C, "G1_top3": G1, "G2_winners": G2,
             "G3_folds": G3, "G4_stress": G4, "sensitivity_not_worse": sens_ok if sens else None}
    if d["ci_block7"][1] < 0 or (d["mean"] < 0 and not B) or (not G2 and d["mean"] < 0):
        return "UNPROMISING", flags
    if not_worse and (A or B or C) and G1 and G2 and G3 and G4:
        if sens and not sens_ok:
            return "INCONCLUSIVE", flags
        return "PROMISING", flags
    return "INCONCLUSIVE", flags


def run() -> dict:
    sha = check_freeze()
    g, st, A, cum_at = load_all()
    A["ts"] = g["ts"]
    ex = e1.Executor(g)
    book = ex.book
    starts = e1.episode_starts(st["c1"])
    oos0, end = e1.ms("2022-01-01T00:00:00Z"), dataset.RESEARCH_END_MS
    fold_b = np.array([e1.ms(d + "T00:00:00Z") for d in D6C.load().fold_boundaries_utc])
    ts = g["ts"]
    ev = []
    for sid, t in enumerate(starts):
        e = t + 1
        if e < len(ts) and oos0 <= ts[e] < end:
            ref = float(g["open"][e])
            ask = float(book.sides(Decimal(str(ref)))[1][0][0])
            ev.append({"signal_id": sid, "t": int(t), "e": int(e), "entry_ts": int(ts[e]), "fill": ask,
                       "sig": float(st["rv24h"][t] * math.sqrt(SIGMA_BARS)),
                       "fold": int(np.searchsorted(fold_b, ts[e], side="right"))})
    if len(ev) != E1_EVENTS:
        raise SystemExit(f"event count {len(ev)} != E1 {E1_EVENTS}")
    fixed = {}
    for h in (E0_H,) + REF_H:
        fn_name = f"R{h}" if h != E0_H else "E0"
        rows = []
        for v in ev:
            s = ex.run(v["e"], v["e"] + h)
            e = v["e"]; hi, lo = A["high"][e:e + h], A["low"][e:e + h]
            mfe = (np.nanmax(hi) / s.entry_ref - 1) * 1e4
            rows.append({"signal_id": v["signal_id"], "candidate_id": fn_name, "decision_bar": v["t"], "entry_bar": e,
                         "entry_ts_ms": v["entry_ts"], "exit_bar": e + h, "holding_min": h, "censored_by_max_hold": True,
                         "gross_bp": s.gross_bp, "spread_bp": s.spread_bp, "fee_bp": s.fee_bp, "funding_bp": s.funding_bp,
                         "total_cost_bp": s.gross_bp - s.net_bp, "net_bp": s.net_bp, "net_stress_bp": s.net_bp - e1.STRESS_EXTRA * 1e4,
                         "identity_ok": s.identity_ok, "mfe_before_bp": mfe, "mae_before_bp": (np.nanmin(lo) / s.entry_ref - 1) * 1e4,
                         "capture": s.gross_bp / mfe if mfe > 0 else None, "fold": v["fold"]})
        fixed[fn_name] = rows
    e0 = fixed["E0"]
    # parity with E1 (section 1) and bootstrap parity with D6-C (section 7)
    import pandas as pd
    e1t = pd.read_parquet(OUTDIR / "e1_trades_v1.parquet").sort_values("signal_id")
    e1_max_diff = float(np.max(np.abs(e1t["net_bp"].to_numpy() - np.array([r["net_bp"] for r in sorted(e0, key=lambda r: r["signal_id"])]))))
    et0 = np.array([r["entry_ts_ms"] for r in e0], dtype=np.int64); n0 = np.array([r["net_bp"] for r in e0])
    mine = ci_of(block_samples(et0, n0, SEED_NET)[1])
    from types import SimpleNamespace
    ref = M.bootstrap_ci([SimpleNamespace(entry_ts_ms=int(a), net_bp=float(b)) for a, b in zip(et0, n0)],
                         block_days=7, iterations=BOOT_ITER, seed=SEED_NET)
    boot_parity = abs(mine[0] - ref["lo"]) < 1e-9 and abs(mine[1] - ref["hi"]) < 1e-9
    if e1_max_diff > 1e-9 or not boot_parity:
        raise SystemExit(f"parity failed: e1 diff {e1_max_diff}, bootstrap {mine} vs {ref}")

    # winner cohorts by E0 net (section 8)
    order = sorted(e0, key=lambda r: r["net_bp"], reverse=True)
    winners = {}
    for lab, k in (("top1pct", math.ceil(.01 * len(e0))), ("top5pct", math.ceil(.05 * len(e0)))):
        winners[lab] = ([r["signal_id"] for r in order[:k]], [r["gross_bp"] for r in order[:k]])

    rng = np.random.default_rng(20261014)
    results, all_rows, audits, exe_rows = {}, [], {}, []
    cand_rows = {}
    for cid in MAIN + tuple(SENS):
        rows = simulate(cid, ev, A, ex, cum_at)
        cand_rows[cid] = rows
        audits[cid] = audit_triggers(cid, ev, A, cum_at, rng)
    for cid in ("E0",) + tuple(f"R{h}" for h in REF_H):
        cand_rows[cid] = fixed[cid]
    for cid, rows in cand_rows.items():
        results[cid] = counterfactual(cid, rows, e0, winners)
        exe, taken = executable(rows)
        results[cid]["executable"] = exe
        all_rows.extend(rows)
        exe_rows.extend({**r, "exe_candidate": cid} for r in taken)
    e0res = results["E0"]
    for cid in MAIN:
        sens = {k: results[k] for k in SENS if SENS[k][0] == cid}
        if not audits[cid]["pass"]:
            results[cid]["status"], results[cid]["status_flags"] = "NOT_JUDGED_LOOKAHEAD", {}
        else:
            results[cid]["status"], results[cid]["status_flags"] = status(results[cid], e0res, sens)
    prom = [c for c in MAIN if results[c]["status"] == "PROMISING"]
    e3 = "YES" if len({CATEGORY[c] for c in prom}) >= 2 else "NO"
    out = {"contract_sha256": sha, "events": len(ev), "e1_net_parity_max_abs_diff": e1_max_diff,
           "bootstrap_parity_with_d6c": boot_parity, "lookahead_audit": audits,
           "status": {c: results[c]["status"] for c in MAIN}, "e3_recommendation": e3,
           "forward_shadow": ["E0"] + prom, "results": results}
    (OUTDIR / "e2_results_v1.json").write_text(json.dumps(out, indent=1, default=str))
    pd.DataFrame(all_rows).to_parquet(OUTDIR / "e2_counterfactual_v1.parquet", index=False)
    pd.DataFrame(exe_rows).to_parquet(OUTDIR / "e2_executable_v1.parquet", index=False)
    return out


if __name__ == "__main__":
    import time
    t0 = time.time()
    r = run()
    print(r["status"], r["e3_recommendation"], r["forward_shadow"], r["lookahead_audit"], f"{time.time()-t0:.0f}s")
