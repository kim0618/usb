"""R1 post-R0 comparison of frozen C1 E1 trades under three regime gates."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "docs/crypto/C1_REGIME_R1_CONTRACT_V1.md"
OUT = ROOT / "data/research/crypto/c1_regime_r1"
TRADES = ROOT / "data/research/crypto/c1_exit/e1_trades_v1.parquet"
REGIMES = ROOT / "data/research/crypto/r0_regime/regime_minutes_v1.parquet"
MINUTE_MS = 60_000
DAY_MS = 86_400_000
BOOT_DRAWS = 10_000
BOOT_SEED = 20261003


def metrics(net: np.ndarray) -> dict:
    pos, neg = net[net > 0], net[net < 0]
    return {"trades": int(len(net)), "net_mean_bp": float(net.mean()),
            "net_median_bp": float(np.median(net)), "pf": float(pos.sum() / -neg.sum()),
            "win_rate": float((net > 0).mean()), "total_net_bp": float(net.sum())}


def block_ci(ts: np.ndarray, values: np.ndarray) -> list[float]:
    """Exact E1/D6-C bootstrap: moving blocks of seven ordered trade-days."""
    days = ts // DAY_MS
    ordered = np.unique(days)
    sums = np.array([values[days == d].sum() for d in ordered])
    counts = np.array([(days == d).sum() for d in ordered])
    blocks = len(ordered) - 7 + 1
    draws_per_rep = math.ceil(len(ordered) / 7)
    offsets = np.arange(7)
    rng = np.random.default_rng(BOOT_SEED)
    out = np.empty(BOOT_DRAWS)
    for i in range(BOOT_DRAWS):
        starts = rng.integers(0, blocks, draws_per_rep)
        idx = (starts[:, None] + offsets[None, :]).ravel()[:len(ordered)]
        out[i] = sums[idx].sum() / counts[idx].sum()
    return [float(x) for x in np.percentile(out, [2.5, 97.5])]


def top_removal(net: np.ndarray) -> dict:
    ordered = np.sort(net)[::-1]
    specs = (("top1", 1), ("top3", 3), ("top1pct", math.ceil(.01 * len(net))),
             ("top5pct", math.ceil(.05 * len(net))))
    return {name: {"removed": int(k), "net_mean_bp": float(ordered[k:].mean())}
            for name, k in specs}


def stability(frame: pd.DataFrame, mask: np.ndarray) -> tuple[dict, dict]:
    selected = frame.loc[mask]
    folds, years = {}, {}
    for fold in range(1, 10):
        x = selected.loc[selected.fold == fold, "net_bp"].to_numpy()
        folds[f"F{fold}"] = {"trades": int(len(x)), "net_mean_bp": float(x.mean()) if len(x) else None,
                             "positive": bool(len(x) and x.mean() > 0)}
    year_values = pd.to_datetime(selected.entry_ts_ms, unit="ms", utc=True).dt.year
    for year in range(2022, 2027):
        x = selected.loc[year_values == year, "net_bp"].to_numpy()
        years[str(year)] = {"trades": int(len(x)), "net_mean_bp": float(x.mean()) if len(x) else None,
                            "positive": bool(len(x) and x.mean() > 0)}
    return folds, years


def candidate(frame: pd.DataFrame, mask: np.ndarray, baseline_large: set[int]) -> dict:
    x = frame.loc[mask]
    net, ts = x.net_bp.to_numpy(), x.entry_ts_ms.to_numpy(dtype=np.int64)
    base_n = len(frame)
    excluded = frame.loc[~mask]
    excluded_large = baseline_large.intersection(excluded.signal_id.astype(int))
    folds, years = stability(frame, mask)
    out = metrics(net)
    out.update({"retention_pct": float(100 * len(x) / base_n), "clustered_95_ci_bp": block_ci(ts, net),
                "top_removal": top_removal(net),
                "mfe_4h_bp": {"mean": float(x.mfe_4h_bp.mean()), "median": float(x.mfe_4h_bp.median())},
                "mae_4h_bp": {"mean": float(x.mae_4h_bp.mean()), "median": float(x.mae_4h_bp.median())},
                "folds": folds, "positive_folds": sum(v["positive"] for v in folds.values()),
                "years": years, "positive_years": sum(v["positive"] for v in years.values()),
                "excluded": {"trades": int(len(excluded)),
                    "total_opportunity_loss_bp": float(excluded.net_bp.sum()),
                    "large_winners": int(len(excluded_large)),
                    "large_winner_pct_of_excluded": (float(100 * len(excluded_large) / len(excluded))
                                                     if len(excluded) else 0.0),
                    "baseline_large_winner_removal_pct": float(100 * len(excluded_large) / len(baseline_large)),
                    "overall_exclusion_pct": float(100 * len(excluded) / base_n)}})
    paired = np.where(mask, 0.0, -frame.net_bp.to_numpy())
    out["paired_total_net_delta_bp"] = float(paired.sum())
    out["paired_delta_clustered_95_ci_bp"] = block_ci(frame.entry_ts_ms.to_numpy(dtype=np.int64), paired)
    return out


def verdict(row: dict, base: dict) -> tuple[str, list[str]]:
    ex, ci = row["excluded"], row["paired_delta_clustered_95_ci_bp"]
    worse = []
    if row["net_mean_bp"] <= base["net_mean_bp"]: worse.append("mean_not_higher")
    if ex["total_opportunity_loss_bp"] > 0: worse.append("positive_opportunity_loss")
    if ci[1] < 0: worse.append("paired_delta_ci_wholly_unfavorable")
    if worse:
        return "WORSE_THAN_BASELINE", worse
    checks = {
        "retention_at_least_60pct": row["retention_pct"] >= 60,
        "distribution_metrics_no_worse": (row["net_median_bp"] >= base["net_median_bp"] and
            row["pf"] >= base["pf"] and row["win_rate"] >= base["win_rate"]),
        "no_positive_opportunity_loss": ex["total_opportunity_loss_bp"] <= 0,
        "no_large_winner_overremoval": (ex["baseline_large_winner_removal_pct"] <= ex["overall_exclusion_pct"]),
        "paired_delta_ci_positive": ci[0] > 0,
        "fold_year_stability_no_worse": (row["positive_folds"] >= base["positive_folds"] and
                                          row["positive_years"] >= base["positive_years"]),
        "all_top_removals_no_worse": all(row["top_removal"][k]["net_mean_bp"] >=
                                           base["top_removal"][k]["net_mean_bp"]
                                           for k in base["top_removal"]),
    }
    failed = [k for k, ok in checks.items() if not ok]
    return ("IMPROVES_C1" if not failed else "INCONCLUSIVE"), failed


def run(write: bool = True) -> dict:
    frame = pd.read_parquet(TRADES).sort_values("signal_id").reset_index(drop=True)
    regimes = pd.read_parquet(REGIMES, columns=["ts_ms", "regime"])
    joined = frame.merge(regimes, left_on=frame.signal_ts_ms - MINUTE_MS, right_on="ts_ms",
                         how="left", validate="many_to_one")
    if len(joined) != 1603 or joined.regime.isna().any() or (joined.regime == "UNCLASSIFIED").any():
        raise RuntimeError("E1/R0 join parity failed")
    large_n = math.ceil(.05 * len(joined))
    large_ids = set(joined.sort_values(["net_bp", "signal_id"], ascending=[False, True]).head(large_n).signal_id.astype(int))
    masks = {"C1_ALL": np.ones(len(joined), dtype=bool),
             "C1_NO_R5": joined.regime.ne("R5").to_numpy(),
             "C1_R3_R4": joined.regime.isin(["R3", "R4"]).to_numpy()}
    rows = {name: candidate(joined, mask, large_ids) for name, mask in masks.items()}
    decisions = {}
    for name in ("C1_NO_R5", "C1_R3_R4"):
        decisions[name] = dict(zip(("verdict", "reasons"), verdict(rows[name], rows["C1_ALL"])))
    verdicts = [v["verdict"] for v in decisions.values()]
    overall = ("IMPROVES_C1" if "IMPROVES_C1" in verdicts else
               "WORSE_THAN_BASELINE" if all(v == "WORSE_THAN_BASELINE" for v in verdicts) else "INCONCLUSIVE")
    payload = {"contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
               "lineage": "post-R0 reused-data follow-up; not new OOS confirmation",
               "join": {"e1_trades": len(frame), "matched": len(joined),
                        "regime_counts": joined.regime.value_counts().sort_index().to_dict()},
               "large_winner_definition": {"baseline_top_pct": 5, "count": large_n},
               "candidates": rows, "candidate_verdicts": decisions, "overall_verdict": overall}
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "results_v1.json").write_text(json.dumps(payload, indent=2) + "\n")
        joined[["signal_id", "signal_ts_ms", "entry_ts_ms", "regime", "net_bp", "mfe_4h_bp", "mae_4h_bp", "fold"]].to_parquet(OUT / "trades_v1.parquet", index=False)
    return payload


if __name__ == "__main__":
    result = run()
    print(result["overall_verdict"], result["candidate_verdicts"])
