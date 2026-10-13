"""C1 post-entry lifecycle research on the frozen 1,603 E1 trades.

Research-only.  This module does not import or modify runtime, PAPER, or LIVE code.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_exit.lifecycle
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import e1, e2
from .. import features as F

OUTDIR = e1.OUTDIR
SNAPSHOTS = (5, 10, 20, 30, 60)
REMAINING = (30, 60, 120, 240)
SEED = 20261003


def classify(*, above: bool, premium_normalized: bool, oi_recovered: bool,
             new_high: bool, new_low: bool) -> str:
    """Three deliberately sparse, semantic states; everything else stays unclassified."""
    if above and new_high:
        return "HOLD"
    adverse = int(not above) + int(premium_normalized) + int(oi_recovered)
    if adverse == 3:
        return "THESIS_BROKEN"
    if not above and adverse == 2:
        return "EXIT_WATCH"
    return "UNCLASSIFIED"


def _recent_extreme(values: np.ndarray, end: int, *, high: bool) -> bool:
    start = max(0, end - 5)
    recent = values[start:end]
    earlier = values[:start]
    if not len(recent) or not np.isfinite(recent).any():
        return False
    if not len(earlier) or not np.isfinite(earlier).any():
        return True
    return bool(np.nanmax(recent) >= np.nanmax(earlier)) if high else bool(np.nanmin(recent) <= np.nanmin(earlier))


def _ci(ts: np.ndarray, vals: np.ndarray, seed: int) -> list[float]:
    if len(vals) < 2:
        return [None, None]
    _, draws = e2.block_samples(ts, vals, seed)
    return e2.ci_of(draws)


def _pf(vals: np.ndarray) -> float | str:
    loss = vals[vals < 0]
    return float(vals[vals > 0].sum() / -loss.sum()) if len(loss) else "INF"


def _stats(frame: pd.DataFrame, value: str, seed: int) -> dict:
    v = frame[value].to_numpy(float)
    ts = frame.entry_ts_ms.to_numpy(np.int64)
    if not len(v):
        return {"n": 0}
    return {
        "n": int(len(v)), "mean": float(v.mean()), "median": float(np.median(v)),
        "win_rate": float((v > 0).mean()), "pf": _pf(v), "clustered_ci": _ci(ts, v, seed),
        "p5": float(np.percentile(v, 5)), "p10": float(np.percentile(v, 10)),
        "worst": float(v.min()), "top5_removed_mean": float(np.sort(v)[::-1][int(np.ceil(.05 * len(v))):].mean()),
    }


def build_snapshots(g: dict, st: dict, A: dict, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for tr in trades.itertuples(index=False):
        e = int(tr.signal_bar) + 1
        entry = float(tr.entry_price)
        entry_s1 = float(st["s1"][int(tr.signal_bar)])
        entry_oi = float(st["oi_chg"][int(tr.signal_bar)])
        for minute in SNAPSHOTS:
            k = e + minute - 1  # snapshot after this completed one-minute bar
            hi = g["high"][e:k + 1]
            lo = g["low"][e:k + 1]
            current_s1 = float(st["s1"][k])
            current_oi = float(st["oi_chg"][k])
            above = bool(g["close"][k] >= entry)
            normalized = bool(A["s1_bucket"][k] >= 2)
            oi_recovered = bool(np.isfinite(current_oi) and current_oi >= 0)
            new_high = _recent_extreme(hi, len(hi), high=True)
            new_low = _recent_extreme(lo, len(lo), high=False)
            state = classify(above=above, premium_normalized=normalized,
                             oi_recovered=oi_recovered, new_high=new_high, new_low=new_low)
            row = {
                "signal_id": int(tr.signal_id), "entry_ts_ms": int(tr.entry_ts_ms), "fold": int(tr.fold),
                "snapshot_min": minute, "snapshot_bar": k, "snapshot_ts_ms": int(g["ts"][k]) + e1.MIN,
                "current_return_bp": float((g["close"][k] / entry - 1) * 1e4),
                "running_mfe_bp": float((np.nanmax(hi) / entry - 1) * 1e4),
                "running_mae_bp": float((np.nanmin(lo) / entry - 1) * 1e4),
                "premium_current": current_s1, "premium_change_from_entry": current_s1 - entry_s1,
                "premium_normalized": normalized, "oi_1h_current": current_oi,
                "oi_1h_change_from_entry": current_oi - entry_oi,
                "price_above_entry": above, "recent_high_renewed": new_high,
                "recent_low_renewed": new_low, "state": state,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def add_outcomes(snaps: pd.DataFrame, g: dict, ex: e1.Executor) -> pd.DataFrame:
    out = snaps.copy()
    immediate_gross, immediate_net = [], []
    for r in out.itertuples(index=False):
        entry_bar = int(r.snapshot_bar) - int(r.snapshot_min) + 1
        decision_exit = int(r.snapshot_bar) + 1
        sim = ex.run(entry_bar, decision_exit)
        immediate_gross.append(sim.gross_bp); immediate_net.append(sim.net_bp)
    out["gross_at_snapshot_bp"] = immediate_gross
    out["net_at_snapshot_bp"] = immediate_net
    for h in REMAINING:
        gross, net, remaining = [], [], []
        for r in out.itertuples(index=False):
            e = int(r.snapshot_bar) + 1  # next executable open after snapshot
            x = e + h
            sim = ex.run(int(r.snapshot_bar) - int(r.snapshot_min) + 1, x)
            gross.append(sim.gross_bp); net.append(sim.net_bp)
            remaining.append(float((g["open"][x] / g["open"][e] - 1) * 1e4))
        out[f"remaining_{h}m_bp"] = remaining
        out[f"gross_exit_{h}m_bp"] = gross
        out[f"net_exit_{h}m_bp"] = net
    return out


def summarize(snaps: pd.DataFrame, e1_trades: pd.DataFrame, e2cf: pd.DataFrame) -> dict:
    by_snapshot: dict = {}
    for minute in SNAPSHOTS:
        sm = snaps[snaps.snapshot_min == minute]
        states = {}
        for state in ("HOLD", "EXIT_WATCH", "THESIS_BROKEN", "UNCLASSIFIED"):
            sf = sm[sm.state == state]
            horizons = {}
            for h in REMAINING:
                horizons[str(h)] = {
                    "remaining": _stats(sf, f"remaining_{h}m_bp", SEED + minute + h),
                    "net_if_exit": _stats(sf, f"net_exit_{h}m_bp", SEED + 1000 + minute + h),
                } if len(sf) else None
            folds = {f"F{f}": {"n": int(len(x)), "remaining_240m_mean": float(x.remaining_240m_bp.mean())}
                     for f in range(1, 10) if len(x := sf[sf.fold == f])}
            years = pd.to_datetime(sf.entry_ts_ms, unit="ms", utc=True).dt.year if len(sf) else pd.Series(dtype=int)
            year_stats = {str(y): {"n": int((years == y).sum()),
                                   "remaining_240m_mean": float(sf.loc[years == y, "remaining_240m_bp"].mean())}
                          for y in sorted(years.unique())}
            states[state] = {"n": int(len(sf)), "share": float(len(sf) / len(sm)), "horizons": horizons,
                             "folds": folds, "years": year_stats}
        primitives = {}
        for field in ("price_above_entry", "premium_normalized", "oi_1h_current", "recent_high_renewed", "recent_low_renewed"):
            if field == "oi_1h_current":
                groups = [("recovered", sm[field] >= 0), ("not_recovered", sm[field] < 0)]
            else:
                groups = [("true", sm[field].astype(bool)), ("false", ~sm[field].astype(bool))]
            primitives[field] = {label: _stats(sm[mask], "remaining_240m_bp", SEED + minute)
                                 for label, mask in groups}
        by_snapshot[str(minute)] = {"states": states, "primitive_4h": primitives}

    # A sparse lifecycle policy: only THESIS_BROKEN exits, at the first scheduled observation.
    first = (snaps[snaps.state == "THESIS_BROKEN"].sort_values(["signal_id", "snapshot_min"])
             .drop_duplicates("signal_id"))
    base = e1_trades.set_index("signal_id")
    policy = []
    for sid, tr in base.iterrows():
        hit = first[first.signal_id == sid]
        if len(hit):
            r = hit.iloc[0]
            policy.append((sid, float(r.net_at_snapshot_bp), float(r.gross_at_snapshot_bp), int(r.snapshot_min), True))
        else:
            policy.append((sid, float(tr.net_bp), float(tr.gross_bp), 240, False))
    pol = pd.DataFrame(policy, columns=["signal_id", "net_bp", "gross_bp", "exit_min", "triggered"]).set_index("signal_id")
    pol = pol.join(base[["entry_ts_ms", "fold"]])
    c1x = e2cf[e2cf.candidate_id == "C1x"].set_index("signal_id").loc[base.index]
    top = set(base.nlargest(int(np.ceil(.05 * len(base))), "net_bp").index)
    denom = float(base.loc[list(top), "gross_bp"].sum())
    def policy_summary(frame: pd.DataFrame) -> dict:
        vals = frame.net_bp.to_numpy(float); ts = frame.entry_ts_ms.to_numpy(np.int64)
        years = pd.to_datetime(frame.entry_ts_ms, unit="ms", utc=True).dt.year
        return {"net_mean": float(vals.mean()), "pf": _pf(vals), "win_rate": float((vals > 0).mean()),
                "clustered_ci": _ci(ts, vals, SEED + 9000), "p5": float(np.percentile(vals, 5)),
                "worst": float(vals.min()), "positive_folds": int(sum(frame[frame.fold == f].net_bp.mean() > 0 for f in range(1, 10))),
                "fold_net": {f"F{f}": float(frame[frame.fold == f].net_bp.mean()) for f in range(1, 10)},
                "year_net": {str(y): float(frame.loc[years == y, "net_bp"].mean()) for y in sorted(years.unique())}}
    psummary = policy_summary(pol)
    psummary.update({"trigger_rate": float(pol.triggered.mean()), "mean_exit_min_triggered": float(pol[pol.triggered].exit_min.mean()),
                     "top5_winner_preservation": float(pol.loc[list(top), "gross_bp"].sum() / denom)})
    cframe = pd.DataFrame({"net_bp": c1x.net_bp, "entry_ts_ms": base.entry_ts_ms, "fold": base.fold})
    csummary = policy_summary(cframe)
    csummary["top5_winner_preservation"] = float(c1x.loc[list(top), "gross_bp"].sum() / denom)
    delta = pol.net_bp.to_numpy() - c1x.net_bp.to_numpy()
    _, draws = e2.block_samples(base.entry_ts_ms.to_numpy(np.int64), delta, SEED + 9100)
    delta_e0 = pol.net_bp.to_numpy() - base.net_bp.to_numpy()
    _, draws_e0 = e2.block_samples(base.entry_ts_ms.to_numpy(np.int64), delta_e0, SEED + 9200)
    comparison = {"mean_delta_vs_c1x_bp": float(delta.mean()), "ci_delta_vs_c1x": e2.ci_of(draws),
                  "p_boot_delta_vs_c1x_positive": float((draws > 0).mean()),
                  "mean_delta_vs_e0_bp": float(delta_e0.mean()), "ci_delta_vs_e0": e2.ci_of(draws_e0),
                  "p_boot_delta_vs_e0_positive": float((draws_e0 > 0).mean())}
    return {"events": int(len(base)), "snapshots": list(SNAPSHOTS), "remaining_horizons": list(REMAINING),
            "state_definitions": {
                "HOLD": "price at/above entry AND a running high was renewed in the latest 5m",
                "EXIT_WATCH": "price below entry AND exactly one of premium normalization / OI recovery",
                "THESIS_BROKEN": "price below entry AND premium normalized AND OI 1h change recovered to >=0",
            }, "by_snapshot": by_snapshot,
            "policy_sanity": {"thesis_broken_exit_next_open": psummary, "c1x": csummary, "comparison": comparison}}


def run() -> dict:
    g, st, A, _ = e2.load_all()
    A["ts"] = g["ts"]
    trades = pd.read_parquet(OUTDIR / "e1_trades_v1.parquet").sort_values("signal_id")
    if len(trades) != e2.E1_EVENTS:
        raise SystemExit(f"expected {e2.E1_EVENTS} E1 trades, got {len(trades)}")
    snaps = build_snapshots(g, st, A, trades)
    snaps = add_outcomes(snaps, g, e1.Executor(g))
    if len(snaps) != len(trades) * len(SNAPSHOTS):
        raise SystemExit("snapshot cardinality failure")
    e2cf = pd.read_parquet(OUTDIR / "e2_counterfactual_v1.parquet")
    result = summarize(snaps, trades, e2cf)
    snaps.to_parquet(OUTDIR / "c1_lifecycle_snapshots_v1.parquet", index=False)
    (OUTDIR / "c1_lifecycle_results_v1.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(run()["policy_sanity"], indent=2))
