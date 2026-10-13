"""Frozen R0 BTC market-regime research.

This module is research-only.  Its contract is
``docs/crypto/CRYPTO_R0_REGIME_CONTRACT_V1.md``.

Run from the repository root::

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.r0_regime
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import dataset

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "docs/crypto/CRYPTO_R0_REGIME_CONTRACT_V1.md"
OUT = ROOT / "data/research/crypto/r0_regime"
RESULT = OUT / "results_v1.json"
STATES = ("R1", "R2", "R3", "R4", "R5")
HORIZONS = (30, 60, 120, 240, 480)
MINUTE_MS = 60_000
DAY = 1440
TAKER = 0.00055
MICRO = 0.00000118
BOOT_SEED = 20261003


def _lag(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[n:] = x[:-n]
    return out


def _prior_30d_q75(x: np.ndarray) -> np.ndarray:
    """One cutoff per UTC day, estimated strictly before that day."""
    out = np.full(len(x), np.nan)
    days = (len(x) + DAY - 1) // DAY
    for d in range(30, days):
        hist = x[max(0, d - 30) * DAY:d * DAY]
        finite = hist[np.isfinite(hist)]
        if len(finite) >= 15 * DAY:
            out[d * DAY:min((d + 1) * DAY, len(x))] = np.quantile(finite, .75)
    return out


def classify(g: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    close = np.asarray(g["close"], dtype=float)
    logp = np.log(close)
    r4h = logp - _lag(logp, 240)
    r24h = logp - _lag(logp, 1440)
    trend = (pd.Series(logp).ewm(span=720, adjust=False).mean().to_numpy()
             - pd.Series(logp).ewm(span=4320, adjust=False).mean().to_numpy())
    ret1 = logp - _lag(logp, 1)
    vol24h = pd.Series(ret1).rolling(1440, min_periods=1440).std(ddof=0).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        oi4h = np.log(g["oi"] / _lag(g["oi"], 240))
        basis = np.log(g["mark_close"] / g["index_close"])
    funding = np.asarray(g["funding_last"], dtype=float)
    move_q75, vol_q75 = _prior_30d_q75(np.abs(r4h)), _prior_30d_q75(vol24h)
    eligible = np.logical_and.reduce([np.isfinite(v) for v in
        (r4h, r24h, trend, vol24h, oi4h, basis, funding, move_q75, vol_q75)])
    regime = np.full(len(close), "UNCLASSIFIED", dtype="U12")
    r4 = eligible & (r4h < 0) & (vol24h >= vol_q75) & (oi4h < 0)
    regime[r4] = "R4"
    same_basis = np.sign(basis) == np.sign(r4h)
    same_funding = np.sign(funding) == np.sign(r4h)
    r3 = eligible & ~r4 & (np.abs(r4h) >= move_q75) & (vol24h >= vol_q75) & (oi4h > 0) & (same_basis | same_funding)
    regime[r3] = "R3"
    remaining = eligible & ~(r4 | r3)
    r1 = remaining & (r24h > 0) & (trend > 0) & ((oi4h > 0) | (basis > 0) | (funding > 0))
    regime[r1] = "R1"
    r2 = remaining & ~r1 & (r24h < 0) & (trend < 0) & ((oi4h < 0) | (basis < 0) | (funding < 0))
    regime[r2] = "R2"
    regime[remaining & ~(r1 | r2)] = "R5"
    return regime, {"r4h": r4h, "r24h": r24h, "trend": trend, "vol24h": vol24h,
                    "oi4h": oi4h, "basis": basis, "funding": funding,
                    "move_q75": move_q75, "vol_q75": vol_q75, "eligible": eligible}


def _funding_numerator(g: dict[str, np.ndarray]) -> np.ndarray:
    """Cumulative settled rate*mark, aligned just before each bar open."""
    ts, mark = g["ts"], g["mark_close"]
    delta = np.zeros(len(ts) + 1)
    for stamp, rate in zip(g["funding_ts"], g["funding_rate"]):
        i = int(np.searchsorted(ts, stamp, side="left"))
        if i < len(ts):
            delta[i] += float(rate) * float(mark[i])
    # prefix[k] contains settlements at bar indices strictly below k. Therefore
    # prefix[exit] - prefix[entry] implements the frozen [entry, exit) interval exactly.
    return np.r_[0.0, np.cumsum(delta[:-1])]


def forward(g: dict[str, np.ndarray], h: int, funding_cum: np.ndarray) -> dict[str, np.ndarray]:
    n = len(g["ts"])
    valid_n = n - 1 - h
    entry = np.full(n, np.nan); exit_ = np.full(n, np.nan)
    entry[:valid_n] = g["open"][1:1 + valid_n]
    exit_[:valid_n] = g["open"][1 + h:1 + h + valid_n]
    ratio = exit_ / entry
    long_gross, short_gross = ratio - 1, 1 / ratio - 1
    fee = TAKER * (1 + ratio) + MICRO
    fund = np.full(n, np.nan)
    # Settlements in [entry timestamp, exit timestamp); cumulative indices are bar-open aligned.
    fund[:valid_n] = (funding_cum[1 + h:1 + h + valid_n]
                      - funding_cum[1:1 + valid_n]) / entry[:valid_n]
    return {"long_gross": long_gross, "short_gross": short_gross,
            "long_net": long_gross - fee - fund,
            "short_net": short_gross - fee + fund,
            "edge": long_gross - short_gross - 2 * fund}


def _metrics(x: np.ndarray) -> dict:
    x = x[np.isfinite(x)]
    pos, neg = x[x > 0].sum(), -x[x < 0].sum()
    return {"n": int(len(x)), "mean_bp": float(x.mean() * 1e4),
            "median_bp": float(np.median(x) * 1e4), "win_rate": float((x > 0).mean()),
            "profit_factor": float(pos / neg) if neg else None}


def _cluster_ci(values: np.ndarray, day: np.ndarray, draws: int = 2000) -> list[float]:
    ok = np.isfinite(values)
    values, day = values[ok], day[ok]
    ids, inv = np.unique(day, return_inverse=True)
    sums = np.bincount(inv, weights=values); counts = np.bincount(inv)
    rng = np.random.default_rng(BOOT_SEED)
    means = np.empty(draws)
    for i in range(draws):
        pick = rng.integers(0, len(ids), len(ids))
        means[i] = sums[pick].sum() / counts[pick].sum()
    return [float(v * 1e4) for v in np.quantile(means, [.025, .975])]


def _year(ts: np.ndarray) -> np.ndarray:
    return pd.to_datetime(ts, unit="ms", utc=True).year.to_numpy()


def _coverage_and_duration(regime: np.ndarray, ts: np.ndarray) -> tuple[dict, list[dict], dict]:
    eligible = regime != "UNCLASSIFIED"
    coverage = {"start": datetime.fromtimestamp(ts[0] / 1000, timezone.utc).isoformat(),
                "end_exclusive": datetime.fromtimestamp((ts[-1] + MINUTE_MS) / 1000, timezone.utc).isoformat(),
                "minutes_total": int(len(ts)), "minutes_eligible": int(eligible.sum()),
                "minutes_unclassified": int((~eligible).sum())}
    starts = np.r_[0, np.flatnonzero(regime[1:] != regime[:-1]) + 1]
    ends = np.r_[starts[1:], len(regime)]
    episodes = [{"regime": str(regime[s]), "start_index": int(s), "duration_min": int(e-s)}
                for s, e in zip(starts, ends) if regime[s] != "UNCLASSIFIED"]
    frequency = {}
    for state in STATES:
        d = np.array([e["duration_min"] for e in episodes if e["regime"] == state])
        frequency[state] = {"minutes": int((regime == state).sum()),
                            "share_eligible": float((regime == state).sum() / eligible.sum()),
                            "episodes": int(len(d)), "duration_mean_min": float(d.mean()),
                            "duration_median_min": float(np.median(d)),
                            "duration_p90_min": float(np.quantile(d, .9))}
    return coverage, episodes, frequency


def _placement(regime: np.ndarray) -> dict:
    def read(path: Path) -> list[dict]:
        return pq.read_table(path).to_pylist()
    c1 = read(ROOT / "data/research/crypto/c1_exit/e1_trades_v1.parquet")
    c2 = read(ROOT / "data/research/crypto/c2_short/e1_trades_v1.parquet")
    t0 = dataset.RESEARCH_START_MS
    def state_at(ms: int) -> str:
        i = (int(ms) - t0) // MINUTE_MS - 1  # signal timestamp is the decision bar close
        return str(regime[i]) if 0 <= i < len(regime) else "OUTSIDE"
    labels = (*STATES, "UNCLASSIFIED", "OUTSIDE")
    c1_counts = {s: 0 for s in labels}; c1_minutes = dict(c1_counts)
    c1_net = {h: {s: [] for s in labels} for h in HORIZONS}
    c1_fields = {30: "net_30m_bp", 60: "net_60m_bp", 120: "net_120m_bp",
                 240: "net_bp", 480: "net_480m_bp"}
    for row in c1:
        s = state_at(row["signal_ts_ms"]); c1_counts[s] += 1
        for h, field in c1_fields.items(): c1_net[h][s].append(float(row[field]) / 1e4)
        # Frozen E1 carries the post-hoc contiguous C1 length, allowing the original minute-weighted
        # state distribution to be recovered without changing or recalculating C1.
        start = int(row["signal_bar"])
        for i in range(start, min(start + int(row["episode_len_min_POSTHOC"]), len(regime))):
            c1_minutes[str(regime[i])] += 1
    all_counts = {s: 0 for s in (*STATES, "UNCLASSIFIED", "OUTSIDE")}
    loss_counts = dict(all_counts); net_by = {s: [] for s in all_counts}
    for row in c2:
        s = state_at(row["signal_ts_ms"]); all_counts[s] += 1
        net_by[s].append(float(row["net_return"]))
        if row["net_return"] <= 0: loss_counts[s] += 1
    c1_perf = {str(h): {s: _metrics(np.asarray(v)) for s, v in by.items() if v}
               for h, by in c1_net.items()}
    return {"c1_event_starts": {"total": len(c1), "counts": c1_counts},
            "c1_minute_weighted": {"total": int(sum(c1_minutes.values())), "counts": c1_minutes},
            "c1_existing_net_by_regime": c1_perf,
            "existing_c2_short": {"total": len(c2), "all_counts": all_counts,
                "losing_trade_counts": loss_counts,
                "by_regime": {s: _metrics(np.asarray(v)) for s, v in net_by.items() if v}}}


def run(write: bool = True) -> dict:
    g = dataset.load(); ts = g["ts"]
    regime, features = classify(g)
    coverage, episodes, frequency = _coverage_and_duration(regime, ts)
    day, years = ts // 86_400_000, _year(ts)
    funding_cum = _funding_numerator(g)
    results, year_results = {}, {}
    forwards = {}
    for h in HORIZONS:
        f = forward(g, h, funding_cum); forwards[h] = f; results[str(h)] = {}
        for s in STATES:
            m = (regime == s) & np.isfinite(f["long_net"])
            row = {k: _metrics(f[k][m]) for k in ("long_gross", "long_net", "short_gross", "short_net", "edge")}
            for k in ("long_net", "short_net", "edge"):
                row[k]["clustered_ci_mean_bp"] = _cluster_ci(f[k][m], day[m])
            results[str(h)][s] = row
    h = 240; f = forwards[h]
    for s in STATES:
        year_results[s] = {}
        for y in sorted(set(years[regime == s])):
            m = (regime == s) & (years == y) & np.isfinite(f["edge"])
            if m.any(): year_results[s][str(y)] = {k: _metrics(f[k][m]) for k in ("long_net", "short_net", "edge")}
    # Six fixed, equal-duration chronological folds. These estimate temporal stability only; no
    # threshold is fitted within a fold.
    fold_results = {s: {} for s in STATES}
    boundaries = np.linspace(0, len(ts), 7, dtype=int)
    for fold in range(6):
        fm = np.zeros(len(ts), dtype=bool); fm[boundaries[fold]:boundaries[fold + 1]] = True
        for s in STATES:
            m = fm & (regime == s) & np.isfinite(f["edge"])
            fold_results[s][f"F{fold + 1}"] = {k: _metrics(f[k][m])
                                               for k in ("long_net", "short_net", "edge")}
    transitions = {}
    for a, b in zip(episodes[:-1], episodes[1:]):
        key = f'{a["regime"]}->{b["regime"]}'
        i = b["start_index"]
        transitions.setdefault(key, []).append(i)
    transition_results = {}
    for key, indices in transitions.items():
        idx = np.asarray(indices); transition_results[key] = {"n": int(len(idx)), "horizons": {}}
        for h in HORIZONS:
            valid = idx[np.isfinite(forwards[h]["edge"][idx])]
            transition_results[key]["horizons"][str(h)] = {
                k: _metrics(forwards[h][k][valid]) for k in ("long_net", "short_net", "edge")}
    verdicts = {}
    for s in STATES:
        row = results["240"][s]; signs = [v["edge"]["mean_bp"] for v in year_results[s].values()]
        positive_share = sum(v > 0 for v in signs) / len(signs)
        ci = row["edge"]["clustered_ci_mean_bp"]
        if row["edge"]["mean_bp"] > 0 and row["long_net"]["mean_bp"] > 0 and positive_share >= .6:
            verdict = "LONG_DIRECTIONAL"
        elif row["edge"]["mean_bp"] < 0 and row["short_net"]["mean_bp"] > 0 and positive_share <= .4:
            verdict = "SHORT_DIRECTIONAL"
        elif row["long_net"]["mean_bp"] <= 0 and row["short_net"]["mean_bp"] <= 0 and ci[0] <= 0 <= ci[1]:
            verdict = "NO_EDGE"
        else: verdict = "MIXED"
        verdicts[s] = {"verdict": verdict, "positive_edge_year_share": positive_share}
    payload = {"contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        "coverage": coverage, "frequency_duration": frequency, "by_horizon": results,
        "year_stability_4h": year_results, "fold_stability_4h": fold_results,
        "transitions": transition_results,
        "placements": _placement(regime), "verdicts_4h": verdicts,
        "notes": ["NO TRADE baseline is 0 bp at every horizon.",
                  "Horizons are directional benchmarks, not exit rules.",
                  "C1 placement uses frozen event starts; no C1 rule was modified."]}
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(payload, indent=2) + "\n")
        table = pa.table({"ts_ms": ts, "regime": regime,
            **{k: v for k, v in features.items() if k != "eligible"}})
        pq.write_table(table, OUT / "regime_minutes_v1.parquet")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
