"""Small, preregistered PAPER experimental LONG/SHORT comparison.

This is intentionally five named candidates, not a threshold search.  All decisions use the
completed minute ``t`` and the existing delayed-OI research grid; fills use ``open[t+1]`` and the
fixed 240 minute benchmark used by C1.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import dataset
from . import features as F
from . import derivatives_flow as DF

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/research/crypto/experimental_ls/results_v1.json"
HOLD = 240
TAKER = 0.00055
MICRO = 1.1841571614073798e-06
SEED = 20261003
DAY_MS = 86_400_000


def _starts(mask: np.ndarray) -> np.ndarray:
    return np.flatnonzero(mask & ~np.r_[False, mask[:-1]])


def _flat_trades(starts: np.ndarray, n: int) -> np.ndarray:
    """At most one position: discard signals arriving through the prior fixed exit."""
    kept: list[int] = []
    busy_until = -1
    for signal in starts:
        if signal + 1 <= busy_until or signal + 1 + HOLD >= n:
            continue
        kept.append(int(signal))
        busy_until = int(signal + 1 + HOLD)
    return np.asarray(kept, dtype=np.int64)


def _summary(x: np.ndarray) -> dict:
    if not len(x):
        return {"trades": 0, "mean_net_bp": None, "median_net_bp": None,
                "profit_factor": None, "win_rate": None}
    gain, loss = x[x > 0].sum(), -x[x < 0].sum()
    return {"trades": int(len(x)), "mean_net_bp": float(x.mean() * 1e4),
            "median_net_bp": float(np.median(x) * 1e4),
            "profit_factor": float(gain / loss) if loss else None,
            "win_rate": float((x > 0).mean())}


def _ci(x: np.ndarray, ts: np.ndarray) -> list[float] | None:
    if not len(x):
        return None
    cluster = ts // (7 * DAY_MS)
    groups = [x[cluster == key] for key in np.unique(cluster)]
    rng = np.random.default_rng(SEED)
    means = np.empty(2_000)
    for i in range(len(means)):
        chosen = rng.integers(0, len(groups), len(groups))
        means[i] = np.concatenate([groups[j] for j in chosen]).mean()
    return [float(v * 1e4) for v in np.quantile(means, [.025, .975])]


def _candidate(name: str, side: str, hypothesis: str, mask: np.ndarray,
               g: dict[str, np.ndarray], targets: dict[str, np.ndarray]) -> dict:
    starts = _starts(mask)
    signals = _flat_trades(starts, len(mask))
    ratio = targets["ratio"][signals]
    funding = targets["fund_long"][signals]
    gross = targets[side.lower()][signals]
    fees = TAKER * (1 + ratio)
    # Positive funding is paid by LONG and received by SHORT.
    funding_cash = -funding if side == "LONG" else funding
    net = gross - fees - MICRO + funding_cash
    entry_ts = g["ts"][signals + 1]
    base = _summary(net)
    base["clustered_ci_mean_net_bp"] = _ci(net, entry_ts)
    start, end = int(entry_ts.min()), int(entry_ts.max()) if len(entry_ts) else (0, 0)
    weeks = max((end - start) / (7 * DAY_MS), 1) if len(entry_ts) else 1
    base["trades_per_week"] = float(len(net) / weeks)
    base["trades_per_month"] = float(base["trades_per_week"] * 365.2425 / 84)
    years = np.array([datetime.fromtimestamp(v / 1000, timezone.utc).year for v in entry_ts])
    folds = {str(y): _summary(net[years == y]) for y in sorted(set(years))}
    midpoint = (start + end) // 2 if len(entry_ts) else 0
    periods = {"first_half": _summary(net[entry_ts < midpoint]),
               "second_half": _summary(net[entry_ts >= midpoint])}
    drop = max(1, int(np.ceil(len(net) * .05))) if len(net) else 0
    keep = np.argsort(net)[:-drop] if drop and drop < len(net) else np.array([], dtype=int)
    top_removed = _summary(net[keep])
    mfe = targets[f"mfe_{side.lower()}"][signals] * 1e4
    mae = targets[f"mae_{side.lower()}"][signals] * 1e4
    ci = base["clustered_ci_mean_net_bp"]
    positive_folds = sum(row["mean_net_bp"] is not None and row["mean_net_bp"] > 0
                         for row in folds.values())
    if len(net) < 100 or base["trades_per_week"] < 1:
        verdict = "TOO_SPARSE"
    elif (base["mean_net_bp"] < 0 and ci is not None and ci[1] < 0) or (
            (base["profit_factor"] or 0) < .8 and positive_folds < 2):
        verdict = "CLEARLY_NEGATIVE"
    elif (base["mean_net_bp"] >= -2 and (base["profit_factor"] or 0) >= .95
          and top_removed["mean_net_bp"] is not None
          and top_removed["mean_net_bp"] >= -5 and positive_folds >= 3):
        verdict = "PAPER_FORWARD"
    else:
        verdict = "CLEARLY_NEGATIVE"
    return {"candidate": name, "side": side, "hypothesis": hypothesis,
            "signal_events": int(len(starts)), "metrics": base, "folds": folds,
            "periods": periods, "top_5pct_winners_removed": top_removed,
            "mfe_bp": {"mean": float(np.mean(mfe)), "median": float(np.median(mfe))},
            "mae_bp": {"mean": float(np.mean(mae)), "median": float(np.median(mae))},
            "cost": {"taker_rate_each_side": TAKER, "micro_return": MICRO,
                     "funding": "actual settlements; paid LONG / received SHORT"},
            "verdict": verdict}


def run(write: bool = True) -> dict:
    g = dataset.load()
    n, ts = len(g["ts"]), g["ts"]
    n_days = n // F.DAY_BARS
    # Exact C1 premium and volatility inputs; only the explicitly named gates below differ.
    x = DF.load_inputs(g)
    f5 = DF.features5(x)
    premium = DF.to_1m(f5["spot_basis_bybit"], x["B"], ts)
    premium_bucket = F.bucketize(premium, n_days)
    train = (ts >= np.datetime64("2021-03-04").astype("datetime64[ms]").astype(np.int64)) & (
        ts < np.datetime64("2022-01-01").astype("datetime64[ms]").astype(np.int64))
    vol = F.regime_labels(g, train)["vol"]  # HIGH=0, MID=1, LOW=2
    oi60 = np.log(g["oi"] / F._lag(g["oi"], 60))
    oi15 = np.log(g["oi"] / F._lag(g["oi"], 15))
    long_masks = {
        "EL_PREMIUM_B2": (premium_bucket <= 1) & (premium_bucket >= 0) & (oi60 < 0) & (vol == 0),
        "EL_VOL_MID": (premium_bucket == 0) & (oi60 < 0) & ((vol == 0) | (vol == 1)),
        "EL_LIMITED_COMBO": (premium_bucket <= 1) & (premium_bucket >= 0) & (oi60 < 0) & ((vol == 0) | (vol == 1)),
    }
    ln_close = np.log(g["close"])
    ret60 = ln_close - F._lag(ln_close, 60)
    ret15 = ln_close - F._lag(ln_close, 15)
    ret240 = ln_close - F._lag(ln_close, 240)
    ret60_bucket = F.bucketize(ret60, n_days)
    ret15_bucket = F.bucketize(ret15, n_days)
    prior_low = np.full(n, np.nan)
    from numpy.lib.stride_tricks import sliding_window_view
    prior_low[60:] = np.min(sliding_window_view(g["low"][:-1], 60), axis=1)[:n-60]
    short_masks = {
        "ES_BREAKDOWN_OI_EXPANSION": (ret60_bucket == 0) & (oi15 > 0) &
            (g["low"] <= prior_low) & (g["close"] < F._lag(g["close"], 1)),
        "ES_FAILED_REBOUND": (ret240 < 0) & (ret15_bucket == 4) & (oi15 < 0) &
            (g["close"] < F._lag(g["close"], 1)),
    }
    targets = F.targets(g, HOLD)
    rows = []
    for name, mask in long_masks.items():
        rows.append(_candidate(name, "LONG", {
            "EL_PREMIUM_B2": "C1 premium B1 relaxed once to B1+B2; OI falling and HIGH retained",
            "EL_VOL_MID": "C1 HIGH volatility relaxed once to MID/HIGH; B1 and OI falling retained",
            "EL_LIMITED_COMBO": "B1+B2 and MID/HIGH; OI falling retained",
        }[name], mask, g, targets))
    for name, mask in short_masks.items():
        rows.append(_candidate(name, "SHORT", {
            "ES_BREAKDOWN_OI_EXPANSION": "lower-tail 60m breakdown with expanding 15m OI",
            "ES_FAILED_REBOUND": "negative 4h trend, top-decile 15m rebound that closes down, falling OI",
        }[name], mask, g, targets))
    eligible = [r for r in rows if r["verdict"] == "PAPER_FORWARD"]
    selected = {}
    for side in ("LONG", "SHORT"):
        pool = [r for r in eligible if r["side"] == side]
        selected[side] = (max(pool, key=lambda r: (r["metrics"]["mean_net_bp"],
                                                   r["metrics"]["profit_factor"] or 0))["candidate"]
                          if pool else None)
    payload = {"schema": "PAPER_EXPERIMENTAL_LS_V1", "as_of": "2026-09-23",
               "pit_safe": True, "hold_minutes": HOLD, "candidates": rows,
               "selected": selected}
    if write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
