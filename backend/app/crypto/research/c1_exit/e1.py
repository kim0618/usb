"""E1: C1 entry as executable trades, fixed 4H exit, D6-C accounting.

Frozen contract: docs/crypto/c1_exit/C1_EXIT_E1_BASELINE_CONTRACT_V1.md (section numbers below refer to it).

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_exit.e1 parity   # counts only, no returns
    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_exit.e1 run      # post-freeze study

D5.2 and D6-C modules are imported, never modified.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .. import dataset
from .. import derivatives_flow as DF
from .. import features as F
from .. import long_horizon as LH
from ..d6 import sizing as SIZING
from ..d6c import contract as D6C
from ..d6c import execution as X
from ..d6c import metrics as M
from ...paper.historical import SyntheticBook

CONTRACT = Path("docs/crypto/c1_exit/C1_EXIT_E1_BASELINE_CONTRACT_V1.md")
OUTDIR = Path("data/research/crypto/c1_exit")
FREEZE = OUTDIR / "e1_freeze_v1.json"

MIN = dataset.MINUTE_MS
DAY_MS = 86_400_000
BENCH_H = 240
REF_H = (30, 60, 120, 480)
TARGET_NOTIONAL = 1000.0
STRESS_EXTRA = 0.0002537159730588128 - 0.0000011841571614073798
D52_C1_OOS_BARS = 18_498
D52_VOL_CUTOFFS = (0.0007592730942805516, 0.0010924950359531157)
BOOT_ITER = 10_000
SEED_BLOCK, SEED_DAY, SEED_IID = 20261002, 20261003, 20261004
TRUNC_CUTS = ("2023-05-17T13:07:00Z", "2025-11-03T06:41:00Z")
SMALL_FOLD = 20


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)


def check_freeze() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract hash mismatch: frozen {frozen} now {now}")
    return now


# ------------------------------------------------------------------ C1 (section 1), D5.2 run() lines verbatim
def c1_state(g: dict, x: dict) -> dict[str, np.ndarray]:
    ts = g["ts"]
    n_days = len(ts) // F.DAY_BARS
    f5 = DF.features5(x)
    s1 = DF.to_1m(f5["spot_basis_bybit"], x["B"], ts)
    bounds = np.array([LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)])
    f1_train = (ts >= bounds[0]) & (ts < bounds[1])
    reg = F.regime_labels(g, f1_train)
    b = F.bucketize(s1, n_days)
    with np.errstate(invalid="ignore", divide="ignore"):
        oi_chg = np.log(g["oi"] / F._lag(g["oi"], 60))
    c1 = (b == 0) & (oi_chg < 0) & (reg["vol"] == 0)
    lnC = np.log(g["close"])
    rv = F._rstd(lnC - F._lag(lnC, 1), F.DAY_BARS)
    return {"c1": c1, "s1": s1, "oi_chg": oi_chg, "rv24h": rv, "vol": reg["vol"],
            "vol_cutoffs": reg["vol_cutoffs"], "bounds": bounds}


def episode_starts(c1: np.ndarray) -> np.ndarray:
    """Section 2: FALSE -> TRUE transitions. Bar 0 counts as a start if it is TRUE."""
    prev = np.r_[False, c1[:-1]]
    return np.nonzero(c1 & ~prev)[0]


def episode_lengths(c1: np.ndarray, starts: np.ndarray) -> np.ndarray:
    ends = np.nonzero(c1 & ~np.r_[c1[1:], False])[0]
    return ends - starts + 1


def parity(g: dict, st: dict) -> dict:
    """Section 1 pre-run check: D5.2's own OOS mask at 240m, counted without any return."""
    ts, c1, bounds = g["ts"], st["c1"], st["bounds"]
    n, h = len(ts), BENCH_H
    period = np.searchsorted(bounds, ts, side="right") - 1
    next_bound = bounds[np.minimum(period + 1, len(bounds) - 1)]
    exit_ts = np.full(n, np.iinfo(np.int64).max)
    exit_ts[: n - 1 - h] = ts[1 + h:]
    O = g["open"]
    fin = np.zeros(n, bool)
    fin[: n - 1 - h] = np.isfinite(O[1: n - h]) & np.isfinite(O[1 + h:])
    base = (period >= 0) & (period <= 9) & (exit_ts < next_bound) & fin
    cnt = int((c1 & base & (period >= 1)).sum())
    cut = [float(v) for v in st["vol_cutoffs"]]
    ok = cnt == D52_C1_OOS_BARS and np.allclose(cut, D52_VOL_CUTOFFS, rtol=0, atol=1e-15)
    return {"c1_oos_bars_d52_mask": cnt, "expected": D52_C1_OOS_BARS, "vol_cutoffs": cut, "pass": bool(ok)}


# ------------------------------------------------------------------ lookahead truncation test (section 11.1)
def truncation_test(g: dict, x: dict, full_c1: np.ndarray) -> list[dict]:
    out = []
    for cut_text in TRUNC_CUTS:
        cut = ms(cut_text)
        gt = dict(g)
        late = g["ts"] >= cut
        for k in ("open", "high", "low", "close", "mark_close", "index_close", "oi", "funding_last"):
            a = g[k].astype(float).copy(); a[late] = np.nan; gt[k] = a
        fk = g["funding_ts"] <= cut
        gt["funding_ts"], gt["funding_rate"] = g["funding_ts"][fk], g["funding_rate"][fk]
        xt = {}
        for k, v in x.items():
            if k == "B":
                xt[k] = v
            else:
                a = v.astype(float).copy(); a[x["B"] > cut] = np.nan; xt[k] = a
        c1t = c1_state(gt, xt)["c1"]
        w = (g["ts"] >= cut - 7 * DAY_MS) & (g["ts"] < cut)
        diff = int((c1t[w] != full_c1[w]).sum())
        out.append({"t_cut": cut_text, "bars_compared": int(w.sum()), "c1_true_in_window": int(full_c1[w].sum()),
                    "mismatches": diff, "pass": diff == 0})
    return out


# ------------------------------------------------------------------ execution (section 4), D6-C engine
@dataclass
class Sim:
    entry_ref: float
    exit_ref: float
    entry_fill: float
    exit_fill: float
    entry_notional: float
    gross_bp: float
    spread_bp: float
    fee_bp: float
    funding_bp: float
    net_bp: float
    identity_ok: bool


class Executor:
    def __init__(self, g: dict):
        self.g = g
        self.backtest = D6C.load()
        fill = self.backtest.fill_model
        self.book = SyntheticBook(spread=Decimal(fill["spread_usdt"]), depth_btc=Decimal(fill["depth_btc_per_side"]),
                                  tick=Decimal(fill["tick"]))
        self.step = Decimal(str(self.backtest.strategy.qty_step))
        self.min_qty = self.backtest.strategy.min_order_qty
        self.req = 0

    def run(self, entry_i: int, exit_i: int, exit_ref: float | None = None) -> Sim | None:
        """exit_ref None = exit at open[exit_i] (E1). E2 B1 passes its stop fill reference."""
        g, book = self.g, self.book
        ts, open_, close, mark = g["ts"], g["open"], g["close"], g["mark_close"]
        fts, frate = g["funding_ts"], g["funding_rate"]
        engine = X.build_engine(self.backtest)
        ref = float(open_[entry_i])
        qty = float(SIZING.floor_to_step(Decimal(str(TARGET_NOTIONAL)) / Decimal(str(ref)), self.step))
        if qty < self.min_qty:
            return None
        mark0 = Decimal(str(mark[entry_i]))
        eq0 = float(engine.account.equity(mark0))
        entry_ts = int(ts[entry_i])
        engine.apply_market(X._quote(book, entry_ts, ref, float(mark[entry_i]), X._funding_rate_at(fts, frate, entry_ts)))
        self.req += 1
        engine.submit_order(ts_ms=entry_ts, side="LONG", qty=Decimal(str(qty)), intent="OPEN",
                            request_id=f"e1-{self.req}", reason="C1_E1")
        entry_fill = float(engine.account.position.avg_entry)
        exit_ts = int(ts[exit_i])
        boundary = (entry_ts // X.FUNDING_INTERVAL_MS + 1) * X.FUNDING_INTERVAL_MS
        while boundary <= exit_ts:
            bar = int(np.searchsorted(ts, boundary, side="left"))
            if bar >= len(ts):
                break
            engine.apply_market(X._quote(book, int(ts[bar]), float(close[bar]), float(mark[bar]),
                                         X._funding_rate_at(fts, frate, int(ts[bar]))))
            boundary += X.FUNDING_INTERVAL_MS
        exit_ref = float(open_[exit_i]) if exit_ref is None else float(exit_ref)
        engine.apply_market(X._quote(book, exit_ts, exit_ref, float(mark[exit_i]), X._funding_rate_at(fts, frate, exit_ts)))
        first = len(engine.ledger.events)
        self.req += 1
        engine.submit_order(ts_ms=exit_ts, side="LONG", qty=Decimal(str(qty)), intent="CLOSE",
                            request_id=f"e1-{self.req}", reason="TIME_EXIT")
        closed = [e for e in engine.ledger.events[first:] if e["event_type"] == X.EventType.POSITION_CLOSE]
        exit_fill = float(closed[-1]["exit_price"])
        gross = float(engine.account.realized_pnl)
        fees = float(engine.account.cumulative_fees)
        funding = float(engine.account.cumulative_funding_paid)
        net = gross - fees - funding
        eq1 = float(engine.account.equity(Decimal(str(mark[exit_i]))))
        notional = qty * entry_fill
        gross_bp = (exit_ref / ref - 1) * 1e4
        return Sim(ref, exit_ref, entry_fill, exit_fill, notional, gross_bp, gross_bp - gross / notional * 1e4,
                   fees / notional * 1e4, funding / notional * 1e4, net / notional * 1e4, abs((eq1 - eq0) - net) <= 1e-6)


# ------------------------------------------------------------------ metrics (sections 6-10)
def describe(net: np.ndarray, gross: np.ndarray | None = None) -> dict:
    if len(net) == 0:
        return {"trades": 0}
    pos, neg = net[net > 0], net[net <= 0]
    pf = float(pos.sum() / abs(neg[neg < 0].sum())) if (neg < 0).any() else "INF"
    d = {"trades": int(len(net)), "net_mean": float(net.mean()), "net_median": float(np.median(net)),
         "win_rate": float((net > 0).mean()), "pf": pf, "total_net_bp": float(net.sum()),
         "avg_win": float(pos.mean()) if len(pos) else None, "avg_loss": float(neg.mean()) if len(neg) else None,
         "p5": float(np.percentile(net, 5)), "p10": float(np.percentile(net, 10)), "worst": float(net.min())}
    if gross is not None:
        d["gross_mean"] = float(gross.mean())
    return d


def block_ci(entry_ts: np.ndarray, net: np.ndarray, seed: int) -> dict:
    trades = [SimpleNamespace(entry_ts_ms=int(t), net_bp=float(v)) for t, v in zip(entry_ts, net)]
    return M.bootstrap_ci(trades, block_days=7, iterations=BOOT_ITER, seed=seed)


def day_cluster_ci(entry_ts: np.ndarray, net: np.ndarray, seed: int) -> dict:
    day = entry_ts // DAY_MS
    days, inv = np.unique(day, return_inverse=True)
    sums = np.bincount(inv, weights=net)
    cnts = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(BOOT_ITER, len(days)))
    s = sums[idx].sum(1) / cnts[idx].sum(1)
    return {"mean": float(net.mean()), "lo": float(np.percentile(s, 2.5)), "hi": float(np.percentile(s, 97.5)),
            "clusters": int(len(days))}


def iid_ci(net: np.ndarray, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    s = net[rng.integers(0, len(net), size=(BOOT_ITER, len(net)))].mean(1)
    return {"mean": float(net.mean()), "lo": float(np.percentile(s, 2.5)), "hi": float(np.percentile(s, 97.5))}


def top_removal(net: np.ndarray) -> dict:
    order = np.sort(net)[::-1]
    out = {}
    for label, k in (("top1", 1), ("top3", 3), ("top1pct", math.ceil(0.01 * len(net))), ("top5pct", math.ceil(0.05 * len(net)))):
        rest = order[k:]
        out[label] = {"removed": int(k), "net_mean": float(rest.mean()) if len(rest) else None,
                      "sign": None if not len(rest) else ("+" if rest.mean() > 0 else "-")}
    return out


def gate(ci: dict) -> tuple[str, str]:
    if ci["hi"] <= 0:
        return "E1_NEGATIVE", "NO"
    if ci["lo"] > 0:
        return "E1_POSITIVE_BASELINE", "YES"
    return "E1_INCONCLUSIVE", "EXPLORATORY_ONLY"


# ------------------------------------------------------------------ run
def build_trades(g: dict, st: dict, ex: Executor) -> tuple[list[dict], dict]:
    ts, H, L = g["ts"], g["high"], g["low"]
    n = len(ts)
    c1 = st["c1"]
    starts = episode_starts(c1)
    lens = episode_lengths(c1, starts)
    oos0, end = ms("2022-01-01T00:00:00Z"), dataset.RESEARCH_END_MS
    fold_b = np.array([ms(d + "T00:00:00Z") for d in D6C.load().fold_boundaries_utc])
    rows, excluded = [], {"exit_beyond_data": 0, "price_missing": 0, "qty_below_min": 0}
    s1 = st["s1"]
    for sid, (t, ln) in enumerate(zip(starts, lens)):
        e = t + 1
        if e >= n or not (oos0 <= ts[e] < end):
            continue
        x4 = e + BENCH_H
        if x4 >= n:
            excluded["exit_beyond_data"] += 1
            continue
        if not (np.isfinite(g["open"][e]) and np.isfinite(g["open"][x4])):
            excluded["price_missing"] += 1
            continue
        sim = ex.run(e, x4)
        if sim is None:
            excluded["qty_below_min"] += 1
            continue
        hi, lo = H[e:x4], L[e:x4]
        d = int(ts[t] // DAY_MS) - int(ts[0] // DAY_MS)
        hist = s1[(d - 30) * F.DAY_BARS: d * F.DAY_BARS]
        hist = hist[np.isfinite(hist)]
        row = {"signal_id": sid, "signal_bar": int(t), "signal_ts_ms": int(ts[t]) + MIN, "entry_ts_ms": int(ts[e]),
               "entry_price": sim.entry_ref, "entry_fill": sim.entry_fill, "exit_ts_ms": int(ts[x4]),
               "exit_price": sim.exit_ref, "exit_fill": sim.exit_fill,
               "gross_bp": sim.gross_bp, "spread_bp": sim.spread_bp, "fee_bp": sim.fee_bp, "funding_bp": sim.funding_bp,
               "total_cost_bp": sim.gross_bp - sim.net_bp, "net_bp": sim.net_bp, "net_stress_bp": sim.net_bp - STRESS_EXTRA * 1e4,
               "identity_ok": sim.identity_ok,
               "mfe_4h_bp": (np.nanmax(hi) / sim.entry_ref - 1) * 1e4, "mae_4h_bp": (np.nanmin(lo) / sim.entry_ref - 1) * 1e4,
               "mfe_time_min": int(np.nanargmax(hi)), "mae_time_min": int(np.nanargmin(lo)),
               "fold": int(np.searchsorted(fold_b, ts[e], side="right")), "utc_date": str(np.datetime64(int(ts[e]), "ms").astype("datetime64[D]")),
               "vol_regime": int(st["vol"][t]), "s1": float(s1[t]), "s1_cut10": float(np.quantile(hist, 0.1)) if len(hist) else None,
               "oi_chg_1h": float(st["oi_chg"][t]), "rv24h": float(st["rv24h"][t]), "episode_len_min_POSTHOC": int(ln)}
        for h in REF_H:
            xh = e + h
            if xh < n and np.isfinite(g["open"][xh]):
                r = ex.run(e, xh)
                row[f"gross_{h}m_bp"], row[f"net_{h}m_bp"] = r.gross_bp, r.net_bp
                row[f"net_stress_{h}m_bp"] = r.net_bp - STRESS_EXTRA * 1e4
                row["identity_ok"] = row["identity_ok"] and r.identity_ok
            else:
                row[f"gross_{h}m_bp"] = row[f"net_{h}m_bp"] = row[f"net_stress_{h}m_bp"] = None
        rows.append(row)
    return rows, excluded


def summarize(rows: list[dict]) -> dict:
    et = np.array([r["entry_ts_ms"] for r in rows], dtype=np.int64)
    net = np.array([r["net_bp"] for r in rows]); stress = np.array([r["net_stress_bp"] for r in rows])
    gross = np.array([r["gross_bp"] for r in rows])
    mfe = np.array([r["mfe_4h_bp"] for r in rows]); mae = np.array([r["mae_4h_bp"] for r in rows])
    base = describe(net, gross); base["ci_block7"] = block_ci(et, net, SEED_BLOCK)
    base["ci_day_cluster"] = day_cluster_ci(et, net, SEED_DAY); base["ci_iid_REFERENCE"] = iid_ci(net, SEED_IID)
    base["cost_components_mean_bp"] = {k: float(np.mean([r[k] for r in rows])) for k in ("spread_bp", "fee_bp", "funding_bp", "total_cost_bp")}
    st = describe(stress, gross); st["ci_block7"] = block_ci(et, stress, SEED_BLOCK)
    path = {"mfe_mean": float(mfe.mean()), "mfe_median": float(np.median(mfe)), "mae_mean": float(mae.mean()),
            "mae_median": float(np.median(mae)),
            "mfe_time_median_min": float(np.median([r["mfe_time_min"] for r in rows])),
            "mae_time_median_min": float(np.median([r["mae_time_min"] for r in rows]))}
    folds = {}
    for f in range(1, 10):
        v = net[np.array([r["fold"] for r in rows]) == f]
        folds[f"F{f}"] = {"trades": int(len(v)), "net_mean": float(v.mean()) if len(v) else None,
                          "median": float(np.median(v)) if len(v) else None,
                          "sign": None if not len(v) else ("+" if v.mean() > 0 else "-"), "small": bool(len(v) < SMALL_FOLD)}
    pos_folds = sum(1 for v in folds.values() if v["sign"] == "+")
    ref = {}
    for h in REF_H:
        ok = [r for r in rows if r[f"net_{h}m_bp"] is not None]
        nh = np.array([r[f"net_{h}m_bp"] for r in ok]); gh = np.array([r[f"gross_{h}m_bp"] for r in ok])
        d = describe(nh, gh); d["net_stress_mean"] = float(np.mean([r[f"net_stress_{h}m_bp"] for r in ok]))
        d["ci_block7"] = block_ci(np.array([r["entry_ts_ms"] for r in ok]), nh, SEED_BLOCK)
        ref[f"{h}m"] = d
    rem = top_removal(net)
    warnings = []
    if st["net_mean"] <= 0: warnings.append("W1_STRESS_NET_NONPOSITIVE")
    if rem["top3"]["sign"] != ("+" if net.mean() > 0 else "-"): warnings.append("W2_TOP3_REMOVAL_FLIPS_SIGN")
    if rem["top1pct"]["sign"] != ("+" if net.mean() > 0 else "-"): warnings.append("W3_TOP1PCT_REMOVAL_FLIPS_SIGN")
    if pos_folds < 6: warnings.append(f"W4_POSITIVE_FOLDS_{pos_folds}_OF_9")
    if base["net_median"] <= 0: warnings.append("W5_MEDIAN_NONPOSITIVE")
    if base["pf"] != "INF" and base["pf"] <= 1: warnings.append("W6_PF_LE_1")
    status, e2 = gate(base["ci_block7"])
    # single-position reference (section 2), not judged
    last_exit, sp = -1, []
    for r in sorted(rows, key=lambda r: r["entry_ts_ms"]):
        if r["entry_ts_ms"] > last_exit:
            sp.append(r["net_bp"]); last_exit = r["exit_ts_ms"]
    overlap = int(sum(1 for a, b in zip(sorted(et), sorted(et)[1:]) if b - a < BENCH_H * MIN))
    return {"base_4h": base, "stress_4h": st, "path_4h": path, "folds": folds, "positive_folds": pos_folds,
            "top_removal": rem, "reference_horizons": ref, "warnings": warnings, "status": status, "e2_authorization": e2,
            "single_position_REFERENCE": {"trades": len(sp), "net_mean": float(np.mean(sp)) if sp else None},
            "consecutive_entries_within_4h": overlap,
            "identity_violations": int(sum(1 for r in rows if not r["identity_ok"]))}


def run() -> dict:
    sha = check_freeze()
    g = dataset.load()
    x = DF.load_inputs(g)
    st = c1_state(g, x)
    par = parity(g, st)
    if not par["pass"]:
        raise SystemExit(f"parity failed: {par}")
    trunc = truncation_test(g, x, st["c1"])
    c1 = st["c1"]
    starts = episode_starts(c1)
    oos = (g["ts"] >= ms("2022-01-01T00:00:00Z"))
    rows, excluded = build_trades(g, st, Executor(g))
    summary = summarize(rows)
    if not all(t["pass"] for t in trunc):
        summary["status"], summary["e1_note"] = "NOT_JUDGED_LOOKAHEAD", "truncation test failed"
    out = {"contract_sha256": sha, "parity": par, "truncation_test": trunc,
           "counts": {"raw_true_bars_all": int(c1.sum()), "raw_true_bars_oos": int((c1 & oos).sum()),
                      "episodes_all": int(len(starts)), "episodes_oos_decision": int(oos[starts].sum()),
                      "executable_trades": len(rows), "excluded": excluded,
                      "pre_oos_episodes_not_used": int((~oos[starts]).sum())},
           "episode_len_min_POSTHOC": {q: float(np.percentile([r["episode_len_min_POSTHOC"] for r in rows], p))
                                       for q, p in (("p50", 50), ("p90", 90), ("max", 100))},
           "trade_days": int(len({r["utc_date"] for r in rows})), **summary}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "e1_results_v1.json").write_text(json.dumps(out, indent=1, default=str))
    import pandas as pd
    pd.DataFrame(rows).to_parquet(OUTDIR / "e1_trades_v1.parquet", index=False)
    return out


if __name__ == "__main__":
    import time
    t0 = time.time()
    if sys.argv[1:] == ["parity"]:
        g = dataset.load(); st = c1_state(g, DF.load_inputs(g))
        print(json.dumps(parity(g, st)), f"{time.time()-t0:.0f}s")
    else:
        r = run()
        print(r["status"], r["e2_authorization"], r["counts"], f"{time.time()-t0:.0f}s")
