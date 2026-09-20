"""The E0 run: one pass that produces every number the report quotes, then the gate.

Artifacts follow the convention the D and C-4 runs use - a run directory named by an identity
digest, ``run_identity.json`` and ``run_context.json`` beside the results, and ``COMPLETE.json``
written last so a half-finished run is visibly half-finished. Nothing here writes to the common
Historical Store, and no provider call is made at any point.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from app.backtest.strategy_d_analog.source import read_set_digest
from app.backtest.strategy_e0_overnight import evaluate, gate, pit_audit, stats
from app.backtest.strategy_e0_overnight.cohorts import (
    available_symbols, build_cohort, deep_symbols,
)
from app.backtest.strategy_e0_overnight.config import E0Rules, STRATEGY_ID
from app.backtest.strategy_e0_overnight.dataset import (
    DailyRows, load_benchmark_close, load_daily_rows,
)

RUNS_DIR = Path("data/runtime/strategy_e_candidate/runs")
PIT_CUTS = (300, 450)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"{type(value)!r} is not JSON serialisable")


def _write(path: Path, payload: Any) -> str:
    body = json.dumps(payload, indent=1, sort_keys=True, default=_json_default).encode("utf-8")
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _git_head(repo_root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return "UNKNOWN"


@dataclass
class E0Result:
    run_id: str
    verdict: str
    payloads: dict[str, Any]


def coverage_report(rows: DailyRows, history, cohorts: Mapping[str, Any]) -> dict[str, Any]:
    sessions = rows.sessions
    used = np.unique(rows.session_idx)
    return {
        "daily": {
            "authority": "MASSIVE_GROUPED_DAILY adjusted=false",
            "snapshot_id": history.freeze.snapshot_id,
            "grid_start": sessions[0].isoformat(),
            "grid_end": sessions[-1].isoformat(),
            "grid_sessions": len(sessions),
            "eval_start": sessions[int(used.min())].isoformat(),
            "eval_end": sessions[int(used.max())].isoformat(),
            "eval_sessions": int(used.size),
            "panel_tickers": len(rows.tickers),
            "universe_tickers_used": int(np.unique(rows.ticker_idx).size),
            "rows": len(rows),
            "reference_snapshots": len(history.snapshot_dates),
        },
        "minute": {name: block for name, block in cohorts.items()},
    }


def single_feature_tables(rows: DailyRows, baseline: Mapping[str, Any], rules: E0Rules,
                          ) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in rules.bucket_names:
        series = rows.features.get(name)
        if series is None:
            continue
        out[name] = stats.bucket_table(series, rows.overnight, rules.bucket_edges(name), baseline)
    return out


def _attach_buckets(block: dict[str, Any], mask: np.ndarray, rows: DailyRows,
                    rules: E0Rules) -> None:
    baseline = stats.summarise(rows.overnight)
    for name in ("dollar_volume", "close_price"):
        block[f"by_{name}"] = stats.bucket_table(
            rows.features[name][mask], rows.overnight[mask], rules.bucket_edges(name), baseline)


def daily_study(rows: DailyRows, rules: E0Rules) -> dict[str, Any]:
    values = rows.overnight
    sessions = rows.session_dates()
    tickers = rows.ticker_names()
    baseline = stats.summarise(values)
    quarter_baseline = evaluate.baseline_by_quarter(values, sessions)
    blocks: dict[str, Any] = {}
    for name in evaluate.DAILY_HYPOTHESES:
        mask = evaluate.daily_mask(name, rows.features)
        block = evaluate.evaluate_candidate(name, mask, values, sessions, tickers, baseline,
                                            quarter_baseline, rules)
        if block.get("summary", {}).get("n"):
            _attach_buckets(block, mask, rows, rules)
        blocks[name] = block
    return {"baseline": baseline,
            "baseline_by_quarter": quarter_baseline,
            "hypotheses": blocks}


def minute_study(rows: DailyRows, cohort_rows: Mapping[str, Mapping[str, np.ndarray]],
                 rules: E0Rules) -> dict[str, Any]:
    """H4 / H5, the incremental-alpha comparison and the horizon table, per cohort.

    The comparison that answers the incremental question is H2 restricted to the cohort versus
    H4 and H5 on the same rows: same universe, same period, same daily rule, one extra
    closing-strength condition.
    """
    out: dict[str, Any] = {}
    for cohort, columns in cohort_rows.items():
        index = columns["daily_index"]
        values = rows.overnight[index]
        sessions = rows.session_dates()[index]
        tickers = rows.ticker_names()[index]
        baseline = stats.summarise(values)
        quarter_baseline = evaluate.baseline_by_quarter(values, sessions)
        features = {k: v[index] for k, v in rows.features.items()}
        h2 = evaluate.daily_mask("H2", features)
        blocks: dict[str, Any] = {
            "cohort_baseline": baseline,
            "H2_in_cohort": evaluate.evaluate_candidate(
                "H2_in_cohort", h2, values, sessions, tickers, baseline, quarter_baseline, rules),
        }
        for name in evaluate.MINUTE_HYPOTHESES:
            mask = evaluate.minute_mask(name, h2, columns)
            blocks[name] = evaluate.evaluate_candidate(
                name, mask, values, sessions, tickers, baseline, quarter_baseline, rules)
            blocks[name]["incremental_vs_H2"] = {
                "h2_n": int(h2.sum()),
                "candidate_n": int(mask.sum()),
                "h2_mean": blocks["H2_in_cohort"]["summary"].get("mean"),
                "candidate_mean": blocks[name]["summary"].get("mean"),
                "mean_delta": (blocks[name]["summary"].get("mean", float("nan"))
                               - blocks["H2_in_cohort"]["summary"].get("mean", float("nan")))
                if blocks[name].get("summary", {}).get("n") else None,
            }
        blocks["closing_strength_buckets"] = {
            name: stats.bucket_table(columns[name], values, edges, baseline)
            for name, edges in (
                ("return_1530_to_close", (None, -0.01, -0.002, 0.0, 0.002, 0.01, None)),
                ("return_1545_to_close", (None, -0.01, -0.002, 0.0, 0.002, 0.01, None)),
                ("close_to_day_high", (None, -0.02, -0.01, -0.005, -0.002, 0.0)),
                ("close_vs_session_vwap", (None, -0.01, -0.002, 0.0, 0.002, 0.01, None)),
                ("last30m_volume_share", (0.0, 0.10, 0.15, 0.20, 0.30, None)),
            )}
        for flag in ("last30m_high_break", "last15m_high_break"):
            blocks["closing_strength_buckets"][flag] = [
                {"bucket": f"{flag}={int(value)}", **stats.summarise(values[columns[flag] == value])}
                for value in (0.0, 1.0)]
        everyone = np.ones(values.size, dtype=bool)
        blocks["horizons"] = {
            "cohort_all": evaluate.horizon_table(everyone, columns),
            "H2_in_cohort": evaluate.horizon_table(h2, columns, everyone),
        }
        for name in evaluate.MINUTE_HYPOTHESES:
            blocks["horizons"][name] = evaluate.horizon_table(
                evaluate.minute_mask(name, h2, columns), columns, everyone)
        blocks["minute_vs_daily_check"] = {
            "rows": int(values.size),
            "minute_overnight_corr_official": float(np.corrcoef(
                columns["minute_overnight"][np.isfinite(columns["minute_overnight"])],
                values[np.isfinite(columns["minute_overnight"])])[0, 1]),
            "median_abs_close_gap": float(np.nanmedian(np.abs(
                columns["minute_close"] / rows.features["close_price"][index] - 1.0))),
        }
        out[cohort] = blocks
    return out


def run_identity(rules: E0Rules, history, cohort_digest: str) -> dict[str, Any]:
    parts = [rules.checksum, history.freeze.freeze_digest, history.freeze.d_read_digest,
             history.freeze.grid_digest, cohort_digest]
    digest = hashlib.sha256("\t".join(parts).encode("utf-8")).hexdigest()
    return {
        "run_id": f"e0-{digest[:12]}",
        "identity_digest": digest,
        "strategy_id": STRATEGY_ID,
        "rules_checksum": rules.checksum,
        "snapshot_id": history.freeze.snapshot_id,
        "snapshot_sha256": history.freeze.snapshot_sha256,
        "freeze_id": history.freeze.freeze_id,
        "freeze_digest": history.freeze.freeze_digest,
        "read_set_digest_at_load": history.freeze.d_read_digest,
        "grid_digest": history.freeze.grid_digest,
        "cohort_digest": cohort_digest,
    }
