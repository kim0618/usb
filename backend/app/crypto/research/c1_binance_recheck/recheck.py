"""C1 Binance venue recheck V1: same C1 rule, Binance USD-M inputs, compared with the Bybit baseline.

Contract: docs/crypto/c1_binance_recheck_v1/CONTRACT_C1_BINANCE_RECHECK_V1.md (section numbers below).
D5.2, E1 and E2 modules are imported, never modified.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_binance_recheck.recheck
"""
from __future__ import annotations

import contextlib
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
from .. import long_horizon as LH
from ..c1_exit import e1, e2
from ..d6c import contract as D6C
from ..d6c import execution as X
from ..study import FEE_VERIFICATION as D5_FEE, micro_costs
from . import data as BD

CONTRACT = Path("docs/crypto/c1_binance_recheck_v1/CONTRACT_C1_BINANCE_RECHECK_V1.md")
OUTDIR = Path("data/research/crypto/c1_binance_recheck_v1")
FREEZE = OUTDIR / "contract_freeze_v1.json"
BINANCE_FEE_FILE = OUTDIR / "binance_fee_contract_v1.json"

MIN = dataset.MINUTE_MS
HIGH_CUT = 0.0010924950359531157
BYBIT_TAKER, BINANCE_TAKER = 0.00055, 0.0005
SIG0 = LH.ms(LH.SAMPLE_START)                       # 2021-03-04
SIG1 = dataset.RESEARCH_END_MS                      # 2026-09-23 exclusive
OOS0 = e1.ms("2022-01-01T00:00:00Z")
CUT = BD.FUNDING_END_MS                             # 2026-09-01: W_OUT end
MAX_HOLD = e2.MAX_HOLD
TOLS = (0, 1, 5)
EVENT_TOL = 60
E2_MAIN = e2.MAIN


def check_freeze() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract hash mismatch: frozen {frozen} now {now}")
    return now


# ------------------------------------------------------------------ C1 (section 0), venue-agnostic copy
def c1_generic(g: dict, perp5: np.ndarray, spot5: np.ndarray, B: np.ndarray) -> dict[str, np.ndarray]:
    """D5.2 run() C1 lines with the frozen vol cutoff passed in rather than re-estimated."""
    ts = g["ts"]
    n_days = len(ts) // F.DAY_BARS
    with np.errstate(invalid="ignore", divide="ignore"):
        s1 = DF.to_1m(np.log(perp5) - np.log(spot5), B, ts)
        oi_chg = np.log(g["oi"] / F._lag(g["oi"], 60))
    b = F.bucketize(s1, n_days)
    lnC = np.log(g["close"])
    rv = F._rstd(lnC - F._lag(lnC, 1), F.DAY_BARS)
    with np.errstate(invalid="ignore"):
        high = rv > HIGH_CUT
    c1 = (b == 0) & (oi_chg < 0) & high
    return {"c1": c1, "s1": s1, "s1_bucket": b, "oi_chg": oi_chg, "rv24h": rv, "high": high}


def regimes(g: dict) -> dict:
    """D5.2 regime axes; the vol axis uses the frozen F1-train cutoffs (never re-estimated)."""
    ts = g["ts"]
    bounds = np.array([LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)])
    reg = F.regime_labels(g, (ts >= bounds[0]) & (ts < bounds[1]))
    lo, hi = e1.D52_VOL_CUTOFFS
    lnC = np.log(g["close"])
    rv = F._rstd(lnC - F._lag(lnC, 1), F.DAY_BARS)
    vol = np.full(len(ts), -1, dtype=np.int8)
    vol[rv > hi] = 0; vol[rv < lo] = 2
    vol[np.isfinite(rv) & (rv >= lo) & (rv <= hi)] = 1
    reg["vol"], reg["vol_cutoffs"] = vol, np.array([lo, hi])
    return reg


# ------------------------------------------------------------------ fees (section 2)
@contextlib.contextmanager
def fee(taker: float):
    """Swap only the taker rate the D6-C engine reads. Maker is never used by any of these studies."""
    old = X.FEE_VERIFICATION
    if taker != BYBIT_TAKER:
        BINANCE_FEE_FILE.write_text(json.dumps({
            "adopted": {"taker_rate": str(taker), "maker_rate": "0.0002", "vip_level": "ACCOUNT_MEASURED",
                        "product": "Binance USDS-M BTCUSDT"},
            "source": "docs/crypto/binance_live/CRYPTO_BINANCE_LIVE_READ_ONLY_VALIDATION_V2.md line 68, /fapi/v1/commissionRate"},
            indent=1))
        X.FEE_VERIFICATION = BINANCE_FEE_FILE
    try:
        yield
    finally:
        X.FEE_VERIFICATION = old


# ------------------------------------------------------------------ signal parity (sections 5, 7, 8)
def starts(c1: np.ndarray, ts: np.ndarray, lo: int, hi: int) -> np.ndarray:
    s = e1.episode_starts(c1)
    return s[(ts[s] >= lo) & (ts[s] < hi)]


def match(a: np.ndarray, b: np.ndarray, tol: int) -> list[tuple[int, int]]:
    """1:1 greedy matching of bar indices, smallest |difference| first (ties: earlier a, then b)."""
    cand = []
    for i, x in enumerate(a):
        j0, j1 = np.searchsorted(b, x - tol, "left"), np.searchsorted(b, x + tol, "right")
        cand.extend((abs(int(b[j]) - int(x)), i, j) for j in range(j0, j1))
    cand.sort()
    ua, ub, out = set(), set(), []
    for _, i, j in cand:
        if i not in ua and j not in ub:
            ua.add(i); ub.add(j); out.append((i, j))
    return sorted(out)


def parity_table(cy: np.ndarray, cb: np.ndarray, ts: np.ndarray, lo: int, hi: int) -> dict:
    sy, sb = starts(cy, ts, lo, hi), starts(cb, ts, lo, hi)
    w = (ts >= lo) & (ts < hi)
    inter, union = int((cy & cb & w).sum()), int(((cy | cb) & w).sum())
    out = {"window_utc": [_utc(lo), _utc(hi)], "bybit_signals": int(len(sy)), "binance_signals": int(len(sb)),
           "bybit_long": int(len(sy)), "binance_long": int(len(sb)), "bybit_short": 0, "binance_short": 0,
           "direction_disagreement": 0, "direction_note": "C1 is LONG-only on both venues by contract",
           "bybit_true_bars": int((cy & w).sum()), "binance_true_bars": int((cb & w).sum()),
           "bar_jaccard": inter / union if union else None, "tol": {}}
    for tol in TOLS + (EVENT_TOL,):
        m = match(sy, sb, tol)
        p = len(m) / len(sb) if len(sb) else None
        r = len(m) / len(sy) if len(sy) else None
        f1 = 2 * p * r / (p + r) if p and r else 0.0
        out["tol"][f"{tol}m"] = {"matched": len(m), "bybit_only": int(len(sy) - len(m)), "binance_only": int(len(sb) - len(m)),
                                 "precision": p, "recall": r, "f1": f1, "jaccard_events": len(m) / (len(sy) + len(sb) - len(m))}
    f0, f5 = out["tol"]["0m"]["f1"], out["tol"]["5m"]["f1"]
    out["class"] = ("EXACT_SAME" if f0 >= 0.95 else "TIME_SHIFTED_SAME" if f5 >= 0.80
                    else "VENUE_SENSITIVE" if f5 >= 0.50 else "VENUE_DIVERGENT")
    return out


def timing(sy: np.ndarray, sb: np.ndarray, gy: dict, gb: dict) -> dict:
    m = match(sy, sb, EVENT_TOL)
    d = np.array([int(sb[j]) - int(sy[i]) for i, j in m])          # minutes, + = Binance later
    ent = []
    for i, j in m:
        ey, eb = sy[i] + 1, sb[j] + 1
        ent.append((1e4 * math.log(gb["open"][eb] / gy["open"][ey]),         # what an operator pays
                    1e4 * math.log(gb["open"][ey] / gy["open"][ey]),         # same bar, venue only
                    1e4 * math.log(gb["open"][eb] / gb["open"][ey])))        # timing only, Binance prices
    ent = np.array(ent)
    q = lambda a: {"mean": float(a.mean()), "median": float(np.median(a)), "p10": float(np.percentile(a, 10)),
                   "p90": float(np.percentile(a, 90)), "abs_median": float(np.median(np.abs(a))),
                   "abs_p90": float(np.percentile(np.abs(a), 90)), "abs_max": float(np.abs(a).max())}
    shifted = d != 0
    return {"event_tol_min": EVENT_TOL, "matched_events": len(m), "offset_min": q(d.astype(float)),
            "same_minute_share": float((~shifted).mean()), "binance_later_share": float((d > 0).mean()),
            "binance_earlier_share": float((d < 0).mean()), "direction_flip": 0,
            "entry_diff_bp_binance_entry_vs_bybit_entry": q(ent[:, 0]),
            "entry_diff_bp_same_bar_venue_only": q(ent[:, 1]),
            "entry_diff_bp_timing_only_binance_prices": q(ent[:, 2]),
            "entry_diff_bp_shifted_events_only": q(ent[shifted, 0]) if shifted.any() else None}


def price_diff(sig: np.ndarray, gy: dict, gb: dict) -> dict:
    out = {}
    for w in (5, 15, 60):
        idx = (sig[:, None] + np.arange(-w, w + 1)[None, :]).ravel()
        idx = idx[(idx >= 0) & (idx < len(gy["ts"]))]
        row = {}
        for k in ("close", "high", "low", "open"):
            d = 1e4 * np.log(gb[k][idx] / gy[k][idx])
            a = np.abs(d)
            row[k] = {"median_bp": float(np.median(d)), "abs_median_bp": float(np.median(a)),
                      "abs_p90_bp": float(np.percentile(a, 90)), "abs_p99_bp": float(np.percentile(a, 99)),
                      "abs_max_bp": float(a.max())}
        per = np.abs(1e4 * np.log(gb["close"][idx] / gy["close"][idx])).reshape(-1, 2 * w + 1) if len(idx) == len(sig) * (2 * w + 1) else None
        if per is not None:
            row["per_signal_max_close_div_bp"] = {"median": float(np.median(per.max(1))), "p90": float(np.percentile(per.max(1), 90)),
                                                  "max": float(per.max())}
        out[f"pm{w}m"] = row
    allm = (gy["ts"] >= SIG0) & (gy["ts"] < SIG1)
    d = np.abs(1e4 * np.log(gb["close"][allm] / gy["close"][allm]))
    out["all_minutes_reference"] = {"abs_median_bp": float(np.median(d)), "abs_p99_bp": float(np.percentile(d, 99)),
                                    "abs_max_bp": float(d.max())}
    return out


def why_differ(cy: dict, cb: dict, ts: np.ndarray) -> dict:
    """Which of the three conditions disagrees on bars where exactly one venue is TRUE (W_SIG)."""
    w = (ts >= SIG0) & (ts < SIG1)
    out = {}
    for lab, a, b in (("bybit_only_bars", cy, cb), ("binance_only_bars", cb, cy)):
        m = a["c1"] & ~b["c1"] & w
        out[lab] = {"bars": int(m.sum()),
                    "other_venue_s1_not_B1": int((b["s1_bucket"][m] != 0).sum()),
                    "other_venue_oi_not_falling": int((~(b["oi_chg"][m] < 0)).sum()),
                    "other_venue_vol_not_high": int((~b["high"][m]).sum())}
    agree = {}
    for k in ("s1_b1", "oi_down", "vol_high"):
        ya = {"s1_b1": cy["s1_bucket"] == 0, "oi_down": cy["oi_chg"] < 0, "vol_high": cy["high"]}[k][w]
        ba = {"s1_b1": cb["s1_bucket"] == 0, "oi_down": cb["oi_chg"] < 0, "vol_high": cb["high"]}[k][w]
        agree[k] = {"bybit_true_share": float(ya.mean()), "binance_true_share": float(ba.mean()),
                    "agreement": float((ya == ba).mean()),
                    "jaccard": float((ya & ba).sum() / max((ya | ba).sum(), 1))}
    out["condition_agreement_all_minutes"] = agree
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(cy["s1"]) & np.isfinite(cb["s1"]) & w
    out["s1_corr"] = float(np.corrcoef(cy["s1"][ok], cb["s1"][ok])[0, 1])
    out["s1_level_bp"] = {"bybit_median": float(np.median(cy["s1"][ok]) * 1e4), "binance_median": float(np.median(cb["s1"][ok]) * 1e4)}
    ok = np.isfinite(cy["oi_chg"]) & np.isfinite(cb["oi_chg"]) & w
    out["oi_chg_corr"] = float(np.corrcoef(cy["oi_chg"][ok], cb["oi_chg"][ok])[0, 1])
    ok = np.isfinite(cy["rv24h"]) & np.isfinite(cb["rv24h"]) & w
    out["rv24h_ratio_binance_over_bybit_median"] = float(np.median(cb["rv24h"][ok] / cy["rv24h"][ok]))
    return out


# ------------------------------------------------------------------ outcomes (sections 6, 10-14)
def mark_ok(g: dict, e: int) -> bool:
    s = slice(e, e + MAX_HOLD + 1)
    return bool(np.isfinite(g["mark_close"][s]).all() and np.isfinite(g["mark_low"][s]).all())


def events(t_bars: np.ndarray, g_price: dict, st_sig: dict, book, need_mark: list[dict]) -> tuple[list[dict], dict]:
    """E2 event list on price grid g_price for decision bars t (W_OUT filter, contract section 3)."""
    ts = g_price["ts"]
    fold_b = np.array([e1.ms(d + "T00:00:00Z") for d in D6C.load().fold_boundaries_utc])
    ev, excl = [], {"outside_w_out": 0, "binance_mark_missing": 0}
    for t in t_bars:
        e = int(t) + 1
        if not (OOS0 <= ts[e] and ts[e] + MAX_HOLD * MIN <= CUT):
            excl["outside_w_out"] += 1
            continue
        if not all(mark_ok(gm, e) for gm in need_mark):
            excl["binance_mark_missing"] += 1
            continue
        ref = float(g_price["open"][e])
        ask = float(book.sides(Decimal(str(ref)))[1][0][0])
        ev.append({"signal_id": int(t), "t": int(t), "e": e, "entry_ts": int(ts[e]), "fill": ask,
                   "sig": float(st_sig["rv24h"][t] * math.sqrt(e2.SIGMA_BARS)),
                   "fold": int(np.searchsorted(fold_b, ts[e], side="right"))})
    return ev, excl


def arrays(g: dict, st: dict) -> tuple[dict, callable]:
    """e2.load_all()'s A, built from any venue grid."""
    ts = g["ts"]
    fts = np.asarray(g["funding_ts"], dtype=np.int64); fr = np.asarray(g["funding_rate"], dtype=float)
    o = np.argsort(fts); fts, fr = fts[o], fr[o]
    cum = np.r_[0.0, np.cumsum(fr)]
    cum_at = lambda t: cum[np.searchsorted(fts, t, side="right")]
    A = {"open": g["open"].astype(float), "high": g["high"].astype(float), "low": g["low"].astype(float),
         "close": g["close"].astype(float), "mark_low": g["mark_low"].astype(float),
         "eval5": ((ts + MIN) % e2.FIVE) == 0, "s1_bucket": st["s1_bucket"],
         "oi_chg": st["oi_chg"].astype(float), "rv24h": st["rv24h"].astype(float), "s1": st["s1"].astype(float),
         "cum_close": cum_at(ts + MIN), "ts": ts}
    return A, cum_at


def fixed_rows(ev: list[dict], A: dict, ex: e1.Executor, h: int, name: str) -> list[dict]:
    rows = []
    for v in ev:
        s = ex.run(v["e"], v["e"] + h)
        e = v["e"]; hi, lo = A["high"][e:e + h], A["low"][e:e + h]
        mfe = (np.nanmax(hi) / s.entry_ref - 1) * 1e4
        rows.append({"signal_id": v["signal_id"], "candidate_id": name, "decision_bar": v["t"], "entry_bar": e,
                     "entry_ts_ms": v["entry_ts"], "entry_price": s.entry_ref, "exit_bar": e + h,
                     "exit_ts_ms": int(A["ts"][e + h]), "exit_price": s.exit_ref, "holding_min": h,
                     "censored_by_max_hold": True, "triggered_at_bar": None,
                     "gross_bp": s.gross_bp, "spread_bp": s.spread_bp, "fee_bp": s.fee_bp, "funding_bp": s.funding_bp,
                     "total_cost_bp": s.gross_bp - s.net_bp, "net_bp": s.net_bp, "net_stress_bp": s.net_bp - e1.STRESS_EXTRA * 1e4,
                     "identity_ok": s.identity_ok, "mfe_before_bp": mfe, "mae_before_bp": (np.nanmin(lo) / s.entry_ref - 1) * 1e4,
                     "mfe_time_min": int(np.nanargmax(hi)), "mae_time_min": int(np.nanargmin(lo)),
                     "capture": s.gross_bp / mfe if mfe > 0 else None, "fold": v["fold"]})
    return rows


def e1_summary(e0: list[dict], refs: dict[int, list[dict]]) -> dict:
    """e1.summarize() over E1-shaped rows (4H + reference horizons)."""
    by = {h: {r["signal_id"]: r for r in rows} for h, rows in refs.items()}
    rows = []
    for r in e0:
        x = {"entry_ts_ms": r["entry_ts_ms"], "exit_ts_ms": r["exit_ts_ms"], "net_bp": r["net_bp"],
             "net_stress_bp": r["net_stress_bp"], "gross_bp": r["gross_bp"], "mfe_4h_bp": r["mfe_before_bp"],
             "mae_4h_bp": r["mae_before_bp"], "mfe_time_min": r["mfe_time_min"], "mae_time_min": r["mae_time_min"],
             "spread_bp": r["spread_bp"], "fee_bp": r["fee_bp"], "funding_bp": r["funding_bp"],
             "total_cost_bp": r["total_cost_bp"], "fold": r["fold"], "identity_ok": r["identity_ok"]}
        for h in e1.REF_H:
            o = by[h][r["signal_id"]]
            x[f"gross_{h}m_bp"], x[f"net_{h}m_bp"], x[f"net_stress_{h}m_bp"] = o["gross_bp"], o["net_bp"], o["net_stress_bp"]
        rows.append(x)
    return e1.summarize(rows)


def ladder_step(label: str, g_price: dict, st_sig: dict, st_price: dict, t_bars: np.ndarray, taker: float,
                need_mark: list[dict], with_e2: bool) -> dict:
    with fee(taker):
        ex = e1.Executor(g_price)
        ev, excl = events(t_bars, g_price, st_sig, ex.book, need_mark)
        A, cum_at = arrays(g_price, st_price)
        fixed = {h: fixed_rows(ev, A, ex, h, "E0" if h == e2.E0_H else f"R{h}") for h in (e2.E0_H,) + e1.REF_H}
        e0 = fixed[e2.E0_H]
        out = {"label": label, "taker": taker, "events": len(ev), "excluded": excl,
               "e1": e1_summary(e0, {h: fixed[h] for h in e1.REF_H}), "rows_e0": e0}
        if with_e2:
            out["e2"], out["rows_e2"] = e2_block(ev, A, cum_at, ex, e0, fixed)
    return out


def e2_block(ev, A, cum_at, ex, e0, fixed) -> tuple[dict, dict]:
    order = sorted(e0, key=lambda r: r["net_bp"], reverse=True)
    winners = {lab: ([r["signal_id"] for r in order[:k]], [r["gross_bp"] for r in order[:k]])
               for lab, k in (("top1pct", math.ceil(.01 * len(e0))), ("top5pct", math.ceil(.05 * len(e0))))}
    rng = np.random.default_rng(20261014)
    cand, audits, results = {}, {}, {}
    for cid in E2_MAIN + tuple(e2.SENS):
        cand[cid] = e2.simulate(cid, ev, A, ex, cum_at)
        audits[cid] = e2.audit_triggers(cid, ev, A, cum_at, rng)
    for h in e1.REF_H:
        cand[f"R{h}"] = fixed[h]
    cand["E0"] = e0
    for cid, rows in cand.items():
        results[cid] = e2.counterfactual(cid, rows, e0, winners)
    for cid in E2_MAIN:
        sens = {k: results[k] for k in e2.SENS if e2.SENS[k][0] == cid}
        results[cid]["status"], results[cid]["status_flags"] = (
            ("NOT_JUDGED_LOOKAHEAD", {}) if not audits[cid]["pass"] else e2.status(results[cid], results["E0"], sens))
    status = {c: results[c]["status"] for c in E2_MAIN}
    keep = {c: {k: results[c][k] for k in ("base", "net_ci_block7", "delta", "holding", "costs", "winner_preservation",
                                            "profit_retention", "positive_folds", "stress")} for c in cand}
    return {"status": status, "promising": [c for c in E2_MAIN if status[c] == "PROMISING"],
            "lookahead_audit_pass": {c: audits[c]["pass"] for c in audits}, "results": keep}, \
        {c: cand[c] for c in E2_MAIN}


def outcome_compare(a: list[dict], b: list[dict]) -> dict:
    """Same-signal pairs present in both ladders (keyed by signal_id)."""
    ib = {r["signal_id"]: r for r in b}
    pairs = [(r, ib[r["signal_id"]]) for r in a if r["signal_id"] in ib]
    if not pairs:
        return {"pairs": 0}
    d = {k: np.array([y[k] - x[k] for x, y in pairs]) for k in ("gross_bp", "net_bp", "mfe_before_bp", "mae_before_bp",
                                                                 "funding_bp", "fee_bp")}
    sign = np.mean([(x["net_bp"] > 0) != (y["net_bp"] > 0) for x, y in pairs])
    return {"pairs": len(pairs), "win_sign_flip_share": float(sign),
            **{f"delta_{k}": {"mean": float(v.mean()), "median": float(np.median(v)), "abs_p90": float(np.percentile(np.abs(v), 90)),
                              "abs_max": float(np.abs(v).max())} for k, v in d.items()}}


def describe(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0}
    g = np.array([r["gross_bp"] for r in rows]); n = np.array([r["net_bp"] for r in rows])
    mf = np.array([r["mfe_before_bp"] for r in rows]); ma = np.array([r["mae_before_bp"] for r in rows])
    return {"trades": len(rows), "gross_mean": float(g.mean()), "net_mean": float(n.mean()), "net_median": float(np.median(n)),
            "win_rate": float((n > 0).mean()), "mfe_mean": float(mf.mean()), "mfe_median": float(np.median(mf)),
            "mae_mean": float(ma.mean()), "mae_median": float(np.median(ma)),
            "funding_mean_bp": float(np.mean([r["funding_bp"] for r in rows])),
            "fee_mean_bp": float(np.mean([r["fee_bp"] for r in rows]))}


def exit_parity(ry: dict, rb: dict, pairs: list[tuple[int, int]]) -> dict:
    """E2 exits on matched events (Bybit L0 rows vs Binance L3 rows)."""
    out = {}
    for cid in E2_MAIN:
        Y = {r["decision_bar"]: r for r in ry[cid]}; Bn = {r["decision_bar"]: r for r in rb[cid]}
        pp = [(Y[a], Bn[b]) for a, b in pairs if a in Y and b in Bn]
        if not pp:
            continue
        ty = np.array([not x["censored_by_max_hold"] for x, _ in pp]); tb = np.array([not y["censored_by_max_hold"] for _, y in pp])
        both = ty & tb
        dt = np.array([y["holding_min"] - x["holding_min"] for x, y in pp])
        dpx = np.array([1e4 * math.log(y["exit_price"] / x["exit_price"]) for x, y in pp])
        dn = np.array([y["net_bp"] - x["net_bp"] for x, y in pp])
        out[cid] = {"pairs": len(pp), "trigger_bybit": int(ty.sum()), "trigger_binance": int(tb.sum()),
                    "touch_disagreement": int((ty != tb).sum()), "touch_disagreement_share": float((ty != tb).mean()),
                    "both_triggered": int(both.sum()),
                    "hold_diff_min_both_triggered": {"median": float(np.median(dt[both])) if both.any() else None,
                                                     "abs_median": float(np.median(np.abs(dt[both]))) if both.any() else None,
                                                     "abs_p90": float(np.percentile(np.abs(dt[both]), 90)) if both.any() else None},
                    "exit_price_diff_bp": {"median": float(np.median(dpx)), "abs_p90": float(np.percentile(np.abs(dpx), 90)),
                                           "abs_max": float(np.abs(dpx).max())},
                    "net_diff_bp": {"mean": float(dn.mean()), "median": float(np.median(dn)),
                                    "abs_p90": float(np.percentile(np.abs(dn), 90)), "abs_max": float(np.abs(dn).max())}}
    return out


# ------------------------------------------------------------------ D5.2 C1 cell (section 7, C1 original)
def d52_cell(g: dict, c1: np.ndarray, reg: dict, taker: float, cut: int | None) -> dict:
    """derivatives_flow.run() for the one C1-EVENT-240m-LONG cell, same bootstrap draws."""
    micro = micro_costs()
    ts = g["ts"]; n = len(ts); n_days = n // F.DAY_BARS
    day = (ts - ts[0]) // DF.DAY_MS
    bounds = np.array([LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)])
    period = np.searchsorted(bounds, ts, side="right") - 1
    next_bound = bounds[np.minimum(period + 1, len(bounds) - 1)]
    day_period = period[:: F.DAY_BARS][:n_days]
    rng = np.random.default_rng(DF.BOOT_SEED)
    fold_days = {k: np.where(day_period == k)[0] for k in range(1, 10)}
    oos_days = np.where((day_period >= 1) & (day_period <= 9))[0]
    W_fold = {k: LH._weights(len(d), rng) for k, d in fold_days.items()}
    W_oos = LH._weights(len(oos_days), rng)
    h, side, NB, k = 240, "LONG", 1, 0
    T = F.targets(g, h)
    exit_ts = np.full(n, np.iinfo(np.int64).max)
    exit_ts[: n - 1 - h] = ts[1 + h:]
    base = (period >= 0) & (period <= 9) & (exit_ts < next_bound) & np.isfinite(T["ratio"])
    if cut is not None:
        base &= exit_ts <= cut
    vals = DF.net_values(T, side, taker, micro)
    b = np.where(c1, 0, -1).astype(np.int8)
    sel = base & (b >= 0)
    key = day[sel] * NB + b[sel]
    size = n_days * NB
    cnt = np.bincount(key, minlength=size).reshape(n_days, NB).astype(float)
    oos_sel = sel & (period >= 1)
    bo = b[oos_sel]
    S = {sc: np.bincount(key, weights=vals[sc][sel], minlength=size).reshape(n_days, NB) for sc in DF.SCENARIOS}
    WIN = {sc: np.bincount(key, weights=(vals[sc][sel] > 0).astype(float), minlength=size).reshape(n_days, NB)
           for sc in ("ZERO", "VIP0_BASE")}
    MAE = np.bincount(key, weights=T["mae_long"][sel], minlength=size).reshape(n_days, NB)
    MFE = np.bincount(key, weights=T["mfe_long"][sel], minlength=size).reshape(n_days, NB)
    med = {sc: [float(np.median(vals[sc][oos_sel][bo == 0])) if (bo == 0).any() else None] for sc in ("ZERO", "VIP0_BASE")}
    regs = {}
    for axis, labels in LH.AXES.items():
        lab = reg[axis]
        ok = oos_sel & (lab >= 0)
        L = len(labels)
        rk = b[ok].astype(np.int64) * L + lab[ok]
        regs[axis] = (labels, np.bincount(rk, minlength=NB * L).reshape(NB, L),
                      np.bincount(rk, weights=vals["VIP0_BASE"][ok], minlength=NB * L).reshape(NB, L),
                      np.bincount(rk, weights=vals["ZERO"][ok], minlength=NB * L).reshape(NB, L))
    c = DF.summarize("C1-EVENT-240m-LONG", "C1", "dislocation_event", k, h, side, S, WIN, MAE, MFE, cnt, med, regs,
                     fold_days, oos_days, W_fold, W_oos, day_period)
    c["verdict"], c["reasons"] = DF.verdict(c)
    o = c["oos"]
    return {"verdict": c["verdict"], "reasons": c["reasons"], "oos_N": o["N"], "oos_N_eff": o["N_eff"],
            "gross_mean": o["ZERO"]["mean"], "net_mean_VIP0_BASE": o["VIP0_BASE"]["mean"],
            "net_ci95": o["VIP0_BASE"].get("ci95"), "net_median": o["VIP0_BASE"]["median"],
            "win_rate": o["VIP0_BASE"]["win_rate"], "mfe_mean": o["mfe_mean"], "mae_mean": o["mae_mean"],
            "cost_ratio": c["cost_ratio"]["VIP0_BASE"]["ratio"], "stress_mean": o["VIP0_STRESS"]["mean"]}


# ------------------------------------------------------------------ helpers
def _utc(ms: int) -> str:
    return str(np.datetime64(int(ms), "ms")) + "Z"


def label(before: str, after: str, metric_delta: float) -> str:
    if before != after:
        return "CONCLUSION_CHANGED"
    return "NUMERICALLY_CHANGED_ONLY" if abs(metric_delta) > 1.0 else "UNCHANGED"


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if not k.startswith("rows_")}
    return o


# ------------------------------------------------------------------ run
def run() -> dict:
    import pandas as pd
    sha = check_freeze()
    out: dict = {"contract_sha256": sha}

    # ---- Bybit baseline (section 4)
    gy = dict(dataset.load())
    x = DF.load_inputs(gy)
    mk = dataset._read("mark_1m", ["low"])
    gy["mark_low"] = mk["low"][np.searchsorted(mk["timestamp_ms"], gy["ts"])]
    st_e1 = e1.c1_state(gy, x)
    par = e1.parity(gy, st_e1)
    cy = c1_generic(gy, x["bybit_last"], x["spot"], x["B"])
    ts = gy["ts"]
    sy_all = starts(cy["c1"], ts, SIG0, SIG1)
    e1t = pd.read_parquet(e1.OUTDIR / "e1_trades_v1.parquet").sort_values("signal_bar")
    bars_oos = starts(cy["c1"], ts, 0, SIG1 + 1)
    bars_oos = bars_oos[(bars_oos + 1 < len(ts)) & (ts[np.minimum(bars_oos + 1, len(ts) - 1)] >= OOS0)]
    base = {"d52_parity": par, "generic_equals_e1_c1_state": bool(np.array_equal(cy["c1"], st_e1["c1"])),
            "w_sig_episodes": int(len(sy_all)), "w_sig_episodes_expected": 2579,
            "oos_episodes": int(len(bars_oos)), "e1_trades": int(len(e1t)),
            "e1_signal_bars_equal": bool(np.array_equal(bars_oos, e1t["signal_bar"].to_numpy())),
            "e1_entry_ts_equal": bool(np.array_equal(ts[bars_oos + 1], e1t["entry_ts_ms"].to_numpy()))}
    with fee(BYBIT_TAKER):
        exy = e1.Executor(gy)
        chk = [exy.run(int(b) + 1, int(b) + 1 + 240).net_bp for b in bars_oos]
    base["e1_net_max_abs_diff"] = float(np.max(np.abs(np.array(chk) - e1t["net_bp"].to_numpy())))
    base["pass"] = (par["pass"] and base["generic_equals_e1_c1_state"] and len(sy_all) == 2579
                    and base["e1_signal_bars_equal"] and base["e1_entry_ts_equal"] and base["e1_net_max_abs_diff"] <= 1e-9)
    out["baseline"] = base
    if not base["pass"]:
        out["decision"] = "E. C1_RECHECK_BLOCKED"
        return out

    # ---- Binance (sections 2, 6)
    BD.SOURCES_READ.clear()
    gb, qc = BD.load()
    x5 = BD.five_minute_inputs(gb)
    cb = c1_generic(gb, x5["perp_last"], x5["spot"], x5["B"])
    out["binance_data"] = {"qc": qc, "spot_5m_identical_to_bybit_path": bool(np.array_equal(x5["spot"], x["spot"], equal_nan=True)),
                           "perp_5m_identical_to_d52_binance_last": bool(np.array_equal(x5["perp_last"], x["binance_last"], equal_nan=True)),
                           "files_read": len(BD.SOURCES_READ),
                           "bybit_files_read": sum("BTCUSDT/historical" in s for s in BD.SOURCES_READ),
                           "rv24h_nan_minutes_in_w_sig": int((~np.isfinite(cb["rv24h"]) & (ts >= SIG0) & (ts < SIG1)).sum()),
                           "high_vol_share_w_sig": {"bybit": float(cy["high"][(ts >= SIG0) & (ts < SIG1)].mean()),
                                                    "binance": float(cb["high"][(ts >= SIG0) & (ts < SIG1)].mean())}}

    # ---- signal parity (sections 7-9)
    sb_all = starts(cb["c1"], ts, SIG0, SIG1)
    out["signal_parity"] = {"w_sig": parity_table(cy["c1"], cb["c1"], ts, SIG0, SIG1),
                            "oos": parity_table(cy["c1"], cb["c1"], ts, OOS0, SIG1),
                            "w_out_entries": parity_table(cy["c1"], cb["c1"], ts, OOS0, CUT - MAX_HOLD * MIN - MIN)}
    out["why_signals_differ"] = why_differ(cy, cb, ts)
    out["timing"] = timing(sy_all, sb_all, gy, gb)
    out["price_diff_around_bybit_signals"] = price_diff(sy_all, gy, gb)

    # ---- outcomes ladder (sections 10-14), W_OUT
    sy = starts(cy["c1"], ts, OOS0, SIG1)
    sb = starts(cb["c1"], ts, OOS0, SIG1)
    L0full = ladder_step("L0_bybit_no_binance_mark_filter", gy, cy, cy, sy, BYBIT_TAKER, [], with_e2=True)
    L0 = ladder_step("L0", gy, cy, cy, sy, BYBIT_TAKER, [gb], with_e2=True)
    L1 = ladder_step("L1", gb, cy, cb, sy, BYBIT_TAKER, [gb], with_e2=False)
    L2 = ladder_step("L2", gb, cb, cb, sb, BYBIT_TAKER, [gb], with_e2=True)
    L3 = ladder_step("L3", gb, cb, cb, sb, BINANCE_TAKER, [gb], with_e2=True)
    lad = {k: v for k, v in (("L0_no_mark_filter", L0full), ("L0", L0), ("L1", L1), ("L2", L2), ("L3", L3))}
    out["ladder"] = {k: {"events": v["events"], "excluded": v["excluded"], "taker": v["taker"],
                         "e1_status": v["e1"]["status"], "e1_base_4h": v["e1"]["base_4h"], "e1_path_4h": v["e1"]["path_4h"],
                         "e1_positive_folds": v["e1"]["positive_folds"], "e1_warnings": v["e1"]["warnings"],
                         "e1_reference_horizons": {h: {kk: r[kk] for kk in ("trades", "net_mean", "net_median", "win_rate")}
                                                   for h, r in v["e1"]["reference_horizons"].items()},
                         "e1_top_removal": v["e1"]["top_removal"], "describe_4h": describe(v["rows_e0"])}
                     for k, v in lad.items()}
    out["ladder_steps_4h_net_mean"] = {
        "L0": L0["e1"]["base_4h"]["net_mean"],
        "price_funding_venue_L1_minus_L0": L1["e1"]["base_4h"]["net_mean"] - L0["e1"]["base_4h"]["net_mean"],
        "signal_L2_minus_L1": L2["e1"]["base_4h"]["net_mean"] - L1["e1"]["base_4h"]["net_mean"],
        "fee_L3_minus_L2": L3["e1"]["base_4h"]["net_mean"] - L2["e1"]["base_4h"]["net_mean"],
        "L3": L3["e1"]["base_4h"]["net_mean"]}
    out["same_signal_L1_vs_L0"] = outcome_compare(L0["rows_e0"], L1["rows_e0"])
    pairs5 = [(int(sy[i]), int(sb[j])) for i, j in match(sy, sb, 5)]
    rb3 = {r["signal_id"]: r for r in L3["rows_e0"]}; ry0 = {r["signal_id"]: r for r in L0["rows_e0"]}
    mp = [(ry0[a], rb3[b]) for a, b in pairs5 if a in ry0 and b in rb3]
    my = {a for a, _ in pairs5}; mb = {b for _, b in pairs5}
    out["matched_vs_venue_specific_4h"] = {
        "matched_tol_min": 5,
        "matched_bybit_L0": describe([a for a, _ in mp]), "matched_binance_L3": describe([b for _, b in mp]),
        "bybit_only_L0": describe([r for r in L0["rows_e0"] if r["signal_id"] not in my]),
        "binance_only_L3": describe([r for r in L3["rows_e0"] if r["signal_id"] not in mb])}
    out["exit_parity_matched_L0_vs_L3"] = exit_parity(L0["rows_e2"], L3["rows_e2"], pairs5)
    out["exit_parity_same_signal_L0_vs_L1_note"] = "L1 has no E2 run: E2 triggers read venue state, measured via L0 vs L3 on matched events"

    # ---- study labels (section 7)
    e1_lab = label(L0["e1"]["status"], L3["e1"]["status"], L3["e1"]["base_4h"]["net_mean"] - L0["e1"]["base_4h"]["net_mean"])
    e2_changed = {c: (L0["e2"]["status"][c], L3["e2"]["status"][c]) for c in E2_MAIN}
    e2_lab = ("CONCLUSION_CHANGED" if any(a != b for a, b in e2_changed.values()) else
              label("x", "x", max(abs(L3["e2"]["results"][c]["base"]["net_mean"] - L0["e2"]["results"][c]["base"]["net_mean"])
                                  for c in E2_MAIN)))
    regy, regb = regimes(gy), regimes(gb)
    d52 = {"authoritative_full_bybit": d52_cell(gy, cy["c1"], regy, BYBIT_TAKER, None),
           "bybit_w_out": d52_cell(gy, cy["c1"], regy, BYBIT_TAKER, CUT),
           "binance_w_out_bybit_fee": d52_cell(gb, cb["c1"], regb, BYBIT_TAKER, CUT),
           "binance_w_out_binance_fee": d52_cell(gb, cb["c1"], regb, BINANCE_TAKER, CUT)}
    d52_lab = label(d52["bybit_w_out"]["verdict"] + "/" + ("W1" if "W1" in d52["bybit_w_out"]["reasons"] else ""),
                    d52["binance_w_out_binance_fee"]["verdict"] + "/" + ("W1" if "W1" in d52["binance_w_out_binance_fee"]["reasons"] else ""),
                    d52["binance_w_out_binance_fee"]["net_mean_VIP0_BASE"] * 1e4 - d52["bybit_w_out"]["net_mean_VIP0_BASE"] * 1e4)
    out["d52_c1_cell"] = d52
    out["e2_status"] = {"L0": L0["e2"]["status"], "L0_no_mark_filter": L0full["e2"]["status"],
                        "L2": L2["e2"]["status"], "L3": L3["e2"]["status"]}
    out["e2_candidates"] = {lab: {c: {"net_mean": v["e2"]["results"][c]["base"]["net_mean"],
                                      "delta_mean": v["e2"]["results"][c]["delta"]["mean"],
                                      "delta_ci": v["e2"]["results"][c]["delta"]["ci_block7"],
                                      "winner_top5": v["e2"]["results"][c]["winner_preservation"]["top5pct"],
                                      "trigger_rate": v["e2"]["results"][c]["holding"]["trigger_rate"]}
                                  for c in E2_MAIN + ("E0",)} for lab, v in (("L0", L0), ("L3", L3))}
    out["study_labels_partial"] = {"C1_original_D52": d52_lab, "C1_E1": e1_lab, "C1_E2": e2_lab}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{**r, "ladder": k} for k, v in lad.items() for r in v["rows_e0"]]).to_parquet(OUTDIR / "e0_rows_v1.parquet", index=False)
    return out


if __name__ == "__main__":
    import time
    t0 = time.time()
    r = run()
    (OUTDIR / "recheck_v1.json").write_text(json.dumps(strip(r), indent=1, default=str, sort_keys=True))
    print(json.dumps({"baseline": r["baseline"], "labels": r.get("study_labels_partial"),
                      "class": r.get("signal_parity", {}).get("w_sig", {}).get("class")}, default=str), f"{time.time()-t0:.0f}s")
