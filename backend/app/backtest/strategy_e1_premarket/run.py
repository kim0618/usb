"""The E1 run: every number the report quotes, then the gate.

Artifacts follow the convention the D, C-4 and E0 runs use: a run directory named by an identity
digest, ``run_identity.json`` and ``run_context.json`` beside the results, and ``COMPLETE.json``
written last. Nothing here writes to the common Historical Store, takes the writer lock, or makes
a provider call, which matters more than usual because the B-minute collector is running.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats
from app.backtest.strategy_e1_premarket import evaluate, gate
from app.backtest.strategy_e1_premarket.config import STRATEGY_ID, E1Rules
from app.backtest.strategy_e1_premarket.dataset import PremarketRows

RUNS_DIR = Path("data/runtime/strategy_e_candidate/runs")
PRIMARY = "R_5m"
SECONDARY = ("R_1m", "R_15m")


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


def universe_volatility(panel, window: int = 20) -> np.ndarray:
    """Cross-sectional median of the trailing realized volatility of daily returns per session."""
    close = panel.close
    with np.errstate(invalid="ignore", divide="ignore"):
        returns = np.full(close.shape, np.nan)
        returns[1:] = close[1:] / close[:-1] - 1.0
    out = np.full(close.shape[0], np.nan)
    for t in range(window, close.shape[0]):
        block = returns[t - window + 1:t + 1]
        with np.errstate(invalid="ignore"):
            per_ticker = np.nanstd(block, axis=0)
        usable = per_ticker[np.isfinite(per_ticker) & (per_ticker > 0)]
        if usable.size:
            out[t] = float(np.median(usable))
    return out


def baseline_block(rows: PremarketRows, rules: E1Rules) -> dict[str, Any]:
    out = {}
    for label, series in sorted(rows.labels.items()):
        out[label] = evaluate.summarise(series, rules)
    return out


def single_feature_tables(rows: PremarketRows, baseline: Mapping[str, Any],
                          rules: E1Rules) -> dict[str, Any]:
    values = rows.labels[PRIMARY]
    tables: dict[str, Any] = {}
    for name in rules.bucket_names:
        series = rows.features.get(name)
        if series is None:
            continue
        tables[name] = stats.bucket_table(series, values, rules.bucket_edges(name), baseline)
    return tables


def _regimes(rows: PremarketRows, panel, grid: Sequence[date]) -> dict[str, np.ndarray]:
    """Two regime labels, both knowable before the decision time."""
    spy = rows.features["spy_premarket_return"]
    volatility = universe_volatility(panel)
    index_of = {s: i for i, s in enumerate(grid)}
    previous_index = np.array([index_of.get(s, 0) - 1 for s in rows.sessions])
    trailing = volatility[np.clip(previous_index, 0, volatility.size - 1)]
    finite = trailing[np.isfinite(trailing)]
    cut = float(np.median(finite)) if finite.size else float("nan")
    return {
        "spy_premarket_positive": np.isfinite(spy) & (spy > 0),
        "spy_premarket_negative": np.isfinite(spy) & (spy < 0),
        "trailing_vol_high": np.isfinite(trailing) & (trailing >= cut),
        "trailing_vol_low": np.isfinite(trailing) & (trailing < cut),
    }, cut


def study(rows: PremarketRows, rules: E1Rules, panel, grid: Sequence[date]) -> dict[str, Any]:
    values = rows.labels[PRIMARY]
    sessions, tickers = rows.sessions, rows.tickers
    baseline = evaluate.summarise(values, rules)
    quarter_baseline = evaluate.baseline_by_quarter(values, sessions, rules)
    everyone = np.ones(values.size, dtype=bool)

    blocks: dict[str, Any] = {}
    for name in evaluate.HYPOTHESES + evaluate.CORE_SETS:
        selected = evaluate.mask(name, rows.features)
        block = evaluate.evaluate_candidate(name, selected, values, sessions, tickers,
                                            baseline, quarter_baseline, rules)
        if block.get("summary", {}).get("n"):
            block["horizons"] = evaluate.horizon_table(selected, rows.labels, rules, everyone)
            for bucket in ("premarket_dollar_volume", "previous_day_dollar_volume", "close_price"):
                block[f"by_{bucket}"] = stats.bucket_table(
                    rows.features[bucket][selected], values[selected],
                    rules.bucket_edges(bucket), baseline)
            block["secondary_horizons"] = {
                label: evaluate.evaluate_candidate(
                    f"{name}:{label}", selected, rows.labels[label], sessions, tickers,
                    evaluate.summarise(rows.labels[label], rules),
                    evaluate.baseline_by_quarter(rows.labels[label], sessions, rules), rules)
                for label in SECONDARY}
        blocks[name] = block

    regimes, vol_cut = _regimes(rows, panel, grid)
    regime_table: dict[str, Any] = {"trailing_vol_median_cut": vol_cut}
    for regime, selector in regimes.items():
        regime_table[regime] = {"baseline": evaluate.summarise(values[selector], rules)}
        for name in evaluate.HYPOTHESES:
            combined = selector & evaluate.mask(name, rows.features)
            block = evaluate.summarise(values[combined], rules)
            if block.get("n"):
                block["lift"] = evaluate.lift(block, regime_table[regime]["baseline"])
            regime_table[regime][name] = block

    halves: dict[str, Any] = {}
    order = np.argsort(sessions)
    midpoint = sessions[order[values.size // 2]]
    for tag, selector in (("first_half", sessions < midpoint), ("second_half", sessions >= midpoint)):
        halves[tag] = {"split_at": str(midpoint), "baseline": evaluate.summarise(values[selector], rules)}
        for name in evaluate.HYPOTHESES:
            combined = selector & evaluate.mask(name, rows.features)
            block = evaluate.summarise(values[combined], rules)
            if block.get("n"):
                block["lift"] = evaluate.lift(block, halves[tag]["baseline"])
            halves[tag][name] = block

    return {"baseline_primary": baseline, "baseline_by_quarter": quarter_baseline,
            "candidates": blocks, "regimes": regime_table, "halves": halves,
            "baseline_horizons": evaluate.horizon_table(everyone, rows.labels, rules)}


def decision_time_robustness(cohorts: Mapping[str, PremarketRows], rules: E1Rules) -> dict[str, Any]:
    """The two earlier decision times, on the declared hypotheses and the primary label only."""
    out: dict[str, Any] = {}
    for tag, rows in cohorts.items():
        values = rows.labels[PRIMARY]
        baseline = evaluate.summarise(values, rules)
        block: dict[str, Any] = {"baseline": baseline, "rows": len(rows)}
        for name in evaluate.HYPOTHESES:
            selected = evaluate.mask(name, rows.features)
            candidate = evaluate.summarise(values[selected], rules)
            if candidate.get("n"):
                candidate["lift"] = evaluate.lift(candidate, baseline)
                candidate["volatility_selector"] = evaluate.volatility_selector(
                    candidate, baseline, candidate["lift"])
            block[name] = candidate
        out[tag] = block
    return out


def run_identity(rules: E1Rules, freeze, tape_digest: str) -> dict[str, Any]:
    parts = [rules.checksum, rules.e0_rules_checksum, freeze.freeze_digest,
             freeze.d_read_digest, freeze.grid_digest, tape_digest]
    digest = hashlib.sha256("\t".join(parts).encode("utf-8")).hexdigest()
    return {"run_id": f"e1-{digest[:12]}", "identity_digest": digest,
            "strategy_id": STRATEGY_ID, "rules_checksum": rules.checksum,
            "e0_rules_checksum": rules.e0_rules_checksum,
            "snapshot_id": freeze.snapshot_id, "snapshot_sha256": freeze.snapshot_sha256,
            "freeze_digest": freeze.freeze_digest, "grid_digest": freeze.grid_digest,
            "read_set_digest_at_load": freeze.d_read_digest,
            "premarket_tape_digest": tape_digest}
