"""The confirmation run: H5 on an unseen symbol block, matched, cost-stressed and bootstrapped."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats
from app.backtest.strategy_e1_h5_confirm import matching
from app.backtest.strategy_e1_h5_confirm.config import STUDY_ID, ConfirmRules
from app.backtest.strategy_e1_premarket import evaluate as e1_evaluate
from app.backtest.strategy_e1_premarket.dataset import PremarketRows

RUNS_DIR = Path("data/runtime/strategy_e_candidate/confirmation_runs")
PRIMARY = "R_5m"


def json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"{type(value)!r} is not JSON serialisable")


def write(path: Path, payload: Any) -> str:
    body = json.dumps(payload, indent=1, sort_keys=True, default=json_default).encode("utf-8")
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def git_head(repo_root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return "UNKNOWN"


def h5_mask(rows: PremarketRows) -> np.ndarray:
    """H5 exactly as E1 declares it, produced by E1's own mask function."""
    return e1_evaluate.mask("H5", rows.features)


def block_summary(rows: PremarketRows, selected: np.ndarray, rules) -> dict[str, Any]:
    values = rows.labels[PRIMARY]
    return {"h5": stats.summarise(values[selected]),
            "non_h5": stats.summarise(values[~selected]),
            "all": stats.summarise(values)}


def cost_stress(gross_lift: float, rules: ConfirmRules) -> dict[str, Any]:
    """net = gross - assumed round trip, plus the break-even the declaration asks for."""
    grid = {f"{cost}bp": gross_lift - cost / 10_000.0 for cost in rules.cost_grid_bp}
    declared = gross_lift - rules.declared_cost_bp / 10_000.0
    return {"gross_matched_lift": gross_lift,
            "break_even_cost_bp": gross_lift * 10_000.0,
            "net_by_assumed_cost": grid,
            "declared_realistic_cost_bp": rules.declared_cost_bp,
            "net_at_declared_cost": declared,
            "net_positive_at_declared_cost": bool(declared > 0)}


def _cluster_bootstrap(differences: np.ndarray, sessions: np.ndarray, *, resamples: int,
                       seed: int) -> dict[str, Any]:
    """Resample whole sessions; rows of one morning move together."""
    keys, inverse = np.unique(sessions, return_inverse=True)
    by_session = [differences[inverse == i] for i in range(keys.size)]
    sums = np.array([block.sum() for block in by_session])
    counts = np.array([block.size for block in by_session], dtype=float)
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    for i in range(resamples):
        picks = rng.integers(0, keys.size, keys.size)
        total, n = sums[picks].sum(), counts[picks].sum()
        means[i] = total / n if n else np.nan
    means = means[np.isfinite(means)]
    return {"unit": "session_cluster", "resamples": int(means.size),
            "mean": float(np.mean(means)),
            "ci": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p_le_zero": float(np.mean(means <= 0))}


def _iid_bootstrap(differences: np.ndarray, *, resamples: int, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed + 1)
    n = differences.size
    means = differences[rng.integers(0, n, size=(resamples, n))].mean(axis=1)
    return {"unit": "symbol_session_iid", "resamples": int(resamples),
            "mean": float(np.mean(means)),
            "ci": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p_le_zero": float(np.mean(means <= 0))}


def bootstrap(result: matching.MatchResult, rules: ConfirmRules) -> dict[str, Any]:
    settings = rules.bootstrap
    resamples, seed = int(settings["resamples"]), int(settings["seed"])
    if result.differences.size < 2:
        return {"insufficient": True, "rows": int(result.differences.size)}
    return {"session_cluster": _cluster_bootstrap(result.differences, result.matched_sessions,
                                                  resamples=resamples, seed=seed),
            "iid": _iid_bootstrap(result.differences, resamples=resamples, seed=seed)}


def extreme_removal(result: matching.MatchResult) -> list[dict[str, Any]]:
    differences = result.differences
    if differences.size == 0:
        return []
    order = np.argsort(differences)[::-1]
    out = []
    for name, k in (("drop_top_1", 1), ("drop_top_5", 5),
                    ("drop_top_1pct", max(1, int(round(differences.size * 0.01))))):
        if k >= differences.size:
            out.append({"removal": name, "rows": 0})
            continue
        kept = differences[np.sort(order[k:])]
        out.append({"removal": name, "removed": int(k), "rows": int(kept.size),
                    "mean_lift": float(np.mean(kept)), "median_lift": float(np.median(kept))})
    return out


def concentration(result: matching.MatchResult) -> dict[str, Any]:
    differences, symbols = result.differences, result.matched_symbols
    if differences.size == 0:
        return {}
    names, inverse = np.unique(symbols, return_inverse=True)
    sums = np.bincount(inverse, weights=differences, minlength=names.size)
    counts = np.bincount(inverse, minlength=names.size)
    total = float(sums.sum())
    order = np.argsort(sums)[::-1]
    shares = sums / total if total else np.full(sums.shape, np.nan)
    leaders = [{"symbol": str(names[i]), "rows": int(counts[i]), "excess": float(sums[i]),
                "share": float(shares[i])} for i in order[:10]]
    return {"distinct_symbols": int(names.size), "total_matched_excess": total,
            "top_1_share": float(np.sum(shares[order[:1]])),
            "top_5_share": float(np.sum(shares[order[:5]])),
            "top_10_share": float(np.sum(shares[order[:10]])),
            "herfindahl": float(np.sum(shares ** 2)) if total else float("nan"),
            "leaders": leaders}


def monthly(result: matching.MatchResult) -> list[dict[str, Any]]:
    if result.differences.size == 0:
        return []
    months = np.array([f"{s.year}-{s.month:02d}" for s in result.matched_sessions], dtype=object)
    out = []
    for month in sorted(set(months.tolist())):
        block = result.differences[months == month]
        out.append({"month": month, "rows": int(block.size),
                    "mean_lift": float(np.mean(block)),
                    "median_lift": float(np.median(block)),
                    "win_rate": float(np.mean(block > 0))})
    return out


def analyse(rows: PremarketRows, rules: ConfirmRules, label: str) -> dict[str, Any]:
    """Everything the report needs about one block."""
    values = rows.labels[PRIMARY]
    selected = h5_mask(rows)
    cells, _ = matching.cell_ids(rows.features, rules.match_edges)
    sessions, symbols = rows.sessions, rows.tickers

    primary = matching.session_demeaned(values, sessions, symbols, cells, selected)
    secondary = matching.same_session(values, sessions, symbols, cells, selected)
    block: dict[str, Any] = {
        "block": label,
        "rows": len(rows),
        "h5_rows": int(selected.sum()),
        "raw": block_summary(rows, selected, rules),
        "matched_primary": primary.to_dict(),
        "matched_secondary": secondary.to_dict(),
        "extreme_removal": extreme_removal(primary),
        "concentration": concentration(primary),
        "monthly": monthly(primary),
        "cost_stress": cost_stress(primary.mean_lift, rules),
        "bootstrap": bootstrap(primary, rules),
        "by_price": matching.by_bucket(values, sessions, symbols, cells, selected,
                                       rows.features["close_price"], rules.price_buckets,
                                       rules.min_bucket_rows),
        "by_liquidity": matching.by_bucket(values, sessions, symbols, cells, selected,
                                           rows.features["previous_day_dollar_volume"],
                                           rules.liquidity_buckets, rules.min_bucket_rows),
        "horizons": {name: {"h5": stats.summarise(series[selected]),
                            "non_h5": stats.summarise(series[~selected])}
                     for name, series in sorted(rows.labels.items())},
    }
    return block


def regimes(rows: PremarketRows, rules: ConfirmRules) -> dict[str, Any]:
    """SPY-based and trailing-volatility regimes, all knowable at the decision time."""
    values = rows.labels[PRIMARY]
    selected = h5_mask(rows)
    cells, _ = matching.cell_ids(rows.features, rules.match_edges)
    spy = rows.features["spy_premarket_return"]
    previous = rows.features["previous_day_return"]
    out: dict[str, Any] = {}
    for name, mask in (("spy_premarket_positive", np.isfinite(spy) & (spy > 0)),
                       ("spy_premarket_negative", np.isfinite(spy) & (spy < 0)),
                       ("previous_day_up", np.isfinite(previous) & (previous > 0)),
                       ("previous_day_down", np.isfinite(previous) & (previous <= 0))):
        result = matching.session_demeaned(values, rows.sessions, rows.tickers, cells,
                                           selected, subset=mask)
        out[name] = result.to_dict()
    return out


def run_identity(rules: ConfirmRules, freeze, development_digest: str,
                 confirmation_digest: str) -> dict[str, Any]:
    parts = [rules.checksum, rules.e1_rules_checksum, rules.e0_rules_checksum,
             freeze.freeze_digest, development_digest, confirmation_digest]
    digest = hashlib.sha256("\t".join(parts).encode("utf-8")).hexdigest()
    return {"run_id": f"e1h5-{digest[:12]}", "identity_digest": digest, "study_id": STUDY_ID,
            "rules_checksum": rules.checksum, "e1_rules_checksum": rules.e1_rules_checksum,
            "e0_rules_checksum": rules.e0_rules_checksum,
            "snapshot_id": freeze.snapshot_id, "freeze_digest": freeze.freeze_digest,
            "development_tape_digest": development_digest,
            "confirmation_tape_digest": confirmation_digest}
