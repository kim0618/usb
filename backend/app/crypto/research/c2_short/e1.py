"""Frozen E1 for C2 Leveraged Blow-off Failure.

Run: PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c2_short.e1
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import dataset

ROOT = Path(__file__).resolve().parents[5]
CONTRACT = ROOT / "docs/crypto/CRYPTO_C2_SHORT_V1_E0_FREEZE.md"
FREEZE = ROOT / "data/research/crypto/c2_short/e0_freeze_v1.json"
RESULT = ROOT / "data/research/crypto/c2_short/e1_results_v1.json"
TRADES = ROOT / "data/research/crypto/c2_short/e1_trades_v1.parquet"
EXPECTED = "6113e698163401f5fdda00fa95e99137f5f6567f5519cf067fd43245df0033d8"
MIN = 60_000
DAY = 1440
HOLD = 240
TAKER = 0.00055


def _daily_pit_q99(values: np.ndarray) -> np.ndarray:
    """For day D, use [D-30,D), never any observation from D."""
    out = np.full(len(values), np.nan)
    days = len(values) // DAY
    for day in range(30, days + 1):
        lo, hi = (day - 30) * DAY, day * DAY
        finite = values[lo:hi][np.isfinite(values[lo:hi])]
        if len(finite) >= 15 * DAY:
            end = min((day + 1) * DAY, len(values))
            out[hi:end] = np.quantile(finite, 0.99)
    return out


def _summary(x: np.ndarray) -> dict:
    pos, neg = x[x > 0].sum(), -x[x < 0].sum()
    return {"trades": int(len(x)), "mean_net_bp": float(x.mean() * 1e4),
            "median_net_bp": float(np.median(x) * 1e4),
            "profit_factor": float(pos / neg) if neg else None,
            "win_rate": float((x > 0).mean())}


def _cluster_ci(net: np.ndarray, entry_ts: np.ndarray) -> list[float]:
    cluster = (entry_ts // (7 * 86_400_000)).astype(int)
    ids = np.unique(cluster)
    groups = [net[cluster == key] for key in ids]
    rng = np.random.default_rng(20261003)
    means = np.empty(10_000)
    for i in range(len(means)):
        chosen = rng.integers(0, len(groups), len(groups))
        means[i] = np.concatenate([groups[j] for j in chosen]).mean()
    return [float(v * 1e4) for v in np.quantile(means, [0.025, 0.975])]


def run(write: bool = True) -> dict:
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if digest != EXPECTED:
        raise RuntimeError(f"C2 contract hash mismatch: {digest}")
    g = dataset.load()
    ts, op, hi, lo, cl, oi = (g[k] for k in ("ts", "open", "high", "low", "close", "oi"))
    r15 = np.full(len(ts), np.nan); r15[15:] = np.log(cl[15:] / cl[:-15])
    oi15 = np.full(len(ts), np.nan); oi15[15:] = np.log(oi[15:] / oi[:-15])
    prior_high = np.full(len(ts), np.nan)
    # Deliberately excludes t; current high must challenge the prior completed 60 bars.
    for i in range(60, len(ts)):
        prior_high[i] = np.max(hi[i-60:i])
    q99 = _daily_pit_q99(r15)
    mask = (r15 > q99) & (oi15 > 0) & (hi >= prior_high) & (cl < np.r_[np.nan, cl[:-1]])
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]])

    rows, busy_until = [], -1
    fts, fr = g["funding_ts"], g["funding_rate"]
    for signal in starts:
        entry, exit_ = signal + 1, signal + 1 + HOLD
        if entry <= busy_until or exit_ >= len(ts):
            continue
        ep, xp = float(op[entry]), float(op[exit_])
        fmask = (fts >= ts[entry]) & (fts < ts[exit_])
        settlement = fts[fmask]
        mark_at_settlement = []
        for stamp in settlement:
            index = int(np.searchsorted(ts, stamp, side="left"))
            if index < len(ts):
                mark_at_settlement.append(float(g["mark_close"][index]))
        # Positive funding is received by a SHORT. Express every cash flow over entry notional.
        funding = float(np.sum(fr[fmask][:len(mark_at_settlement)]
                               * np.asarray(mark_at_settlement) / ep))
        gross = (ep - xp) / ep
        fees = TAKER * (1.0 + xp / ep)
        net = gross - fees + funding
        window_hi, window_lo = hi[entry:exit_], lo[entry:exit_]
        rows.append({"signal_ts_ms": int(ts[signal] + MIN), "entry_ts_ms": int(ts[entry]),
                     "exit_ts_ms": int(ts[exit_]), "entry": ep, "exit": xp,
                     "gross_return": gross, "fees_return": fees, "funding_return": funding,
                     "net_return": net,
                     "mfe_bp": float((ep - window_lo.min()) / ep * 1e4),
                     "mae_bp": float((ep - window_hi.max()) / ep * 1e4),
                     "r15": float(r15[signal]), "oi15": float(oi15[signal]),
                     "q99": float(q99[signal])})
        busy_until = exit_
    net = np.array([r["net_return"] for r in rows])
    ets = np.array([r["entry_ts_ms"] for r in rows], dtype=np.int64)
    base = _summary(net)
    base["clustered_ci_mean_net_bp"] = _cluster_ci(net, ets)
    years = np.array([datetime.fromtimestamp(v/1000, timezone.utc).year for v in ets])
    folds = {str(y): _summary(net[years == y]) for y in sorted(set(years))}
    mid = (ets.min() + ets.max()) // 2
    halves = {"first": _summary(net[ets < mid]), "second": _summary(net[ets >= mid])}
    keep = np.argsort(np.abs(net))[:max(1, int(np.floor(len(net) * .95)))]
    top5_removed = _summary(net[keep])
    full_year_negative = sum(folds.get(str(y), {"mean_net_bp": 0})["mean_net_bp"] < 0
                             for y in range(2022, 2027))
    ci = base["clustered_ci_mean_net_bp"]
    pf = base["profit_factor"] or 999.0
    if len(net) >= 100 and ((base["mean_net_bp"] <= 0 and ci[1] <= 0)
                            or (pf < .8 and full_year_negative >= 4)):
        verdict = "CLEARLY_NEGATIVE"
    elif ci[0] > 0 and pf > 1 and full_year_negative <= 2:
        verdict = "POSITIVE_PREVALIDATION"
    else:
        verdict = "INCONCLUSIVE_BUT_PLAUSIBLE"
    payload = {"contract_sha256": digest, "candidate": "LEVERAGED_BLOW_OFF_FAILURE",
               "side": "SHORT", "benchmark_exit_minutes": HOLD,
               "coverage": {"start_ms": int(ts[0]), "end_ms": int(ts[-1] + MIN),
                            "raw_signal_events": int(len(starts))},
               "metrics": base, "folds": folds, "halves": halves,
               "top_5pct_abs_net_removed": top5_removed,
               "mfe_bp": {"mean": float(np.mean([r["mfe_bp"] for r in rows])),
                          "median": float(np.median([r["mfe_bp"] for r in rows]))},
               "mae_bp": {"mean": float(np.mean([r["mae_bp"] for r in rows])),
                          "median": float(np.median([r["mae_bp"] for r in rows]))},
               "verdict": verdict}
    if write:
        import pyarrow as pa
        import pyarrow.parquet as pq
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        FREEZE.write_text(json.dumps({"contract": str(CONTRACT.relative_to(ROOT)),
            "sha256": digest, "frozen_before_e1": True}, indent=2) + "\n")
        RESULT.write_text(json.dumps(payload, indent=2) + "\n")
        pq.write_table(pa.Table.from_pylist(rows), TRADES)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
