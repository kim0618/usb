"""D1: data and PIT feasibility of Strategy D on one frozen dataset.

D1 answers, before a single vector is encoded, whether the declared study is runnable on the
data that exists: does the session grid hold (G1~G8), how large is the eligible universe on a
query date, how large is the library on a stride date, how often is a composite FIGI missing,
does any grouped session carry a repeated ticker row (B8), and how long would the D2 search take
on this machine. It records the measurements; it does not adjust a rule to fit them. A rule that
has to change because of what D1 measures becomes a new declared version, never an edit here.

Nothing in D1 reads a forward label, so no result of the study can be seen from this phase.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import time
from typing import Any

import numpy as np

from app.backtest.strategy_d_analog import universe
from app.backtest.strategy_d_analog.config import (
    DECLARED_RULES_CHECKSUM, RULES_PATH, REPO_ROOT, AnalogRules, load_rules,
)
from app.backtest.strategy_d_analog.identity import RunIdentity, query_sample, run_identity
from app.backtest.strategy_d_analog.models import REASON_ORDER, HardFail
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history

PHASE = "D1"
PREFLIGHT_DOC = REPO_ROOT / "docs/backtest/strategy_d/D_D1_PREFLIGHT_CONTRACT_V1.md"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d/runs"
#: A square multiply sized like the real search, used to time this machine rather than guess it.
BENCH_QUERIES = 300
BENCH_LIBRARY = 20_000


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {}
    array = np.array(values)
    return {"min": int(array.min()), "p25": int(np.percentile(array, 25)),
            "median": int(np.percentile(array, 50)), "p75": int(np.percentile(array, 75)),
            "max": int(array.max()), "mean": int(round(float(array.mean())))}


@dataclass
class D1Result:
    identity: RunIdentity
    report: dict[str, Any]
    verdict: str
    run_dir: Path | None = None


def _eligibility_cache(history: DailyHistory, rules: AnalogRules) -> Callable[[int], universe.Eligibility]:
    cache: dict[int, universe.Eligibility] = {}

    def get(index: int) -> universe.Eligibility:
        if index not in cache:
            cache[index] = universe.evaluate(history.panel_view(index), history.membership(index), rules)
        return cache[index]

    return get


def _figi_stats(history: DailyHistory, index: int, tickers: tuple[str, ...]) -> tuple[int, int]:
    """(eligible names, names whose composite FIGI is null in the snapshot that governs the date)."""
    as_of = history.snapshot_as_of(index)
    table = history.figi.get(as_of, {}) if as_of is not None else {}
    return len(tickers), sum(1 for t in tickers if table.get(t) is None)


def _search_cost_estimate(rules: AnalogRules, library_sizes: Mapping[int, int],
                          eval_indices: tuple[int, ...], query_counts: Mapping[int, int]) -> dict[str, Any]:
    """Upper bound on the D2 exact search: every eligible stride window that clears the embargo.

    Label validity will only shrink the library, so this over-counts on purpose; a run that fits
    here fits in the real search. The measured rate is this machine's, not a constant.
    """
    rng = np.random.default_rng(0)
    left = rng.standard_normal((BENCH_QUERIES, 61))
    right = rng.standard_normal((61, BENCH_LIBRARY))
    start = time.perf_counter()
    for _ in range(3):
        left @ right
    elapsed = (time.perf_counter() - start) / 3
    flops_per_second = 2.0 * BENCH_QUERIES * BENCH_LIBRARY * 61 / max(elapsed, 1e-9)

    prefix: list[int] = []
    running = 0
    for index in sorted(library_sizes):
        running += library_sizes[index]
        prefix.append(running)
    ordered = sorted(library_sizes)

    def library_before(limit: int) -> int:
        count = sum(1 for d in ordered if d <= limit)
        return prefix[count - 1] if count else 0

    total_flops = 0.0
    per_test: dict[str, float] = {}
    for window, horizon in rules.combinations:
        flops = 0.0
        for index in eval_indices:
            candidates = library_before(index - window - horizon)
            flops += 2.0 * query_counts.get(index, 0) * candidates * (window + 1)
        per_test[f"W{window}_H{horizon}"] = flops
        total_flops += 2 * flops  # representations A and B share the window count
    return {"measured_gflops_per_second": round(flops_per_second / 1e9, 2),
            "total_gflop": round(total_flops / 1e9, 1),
            "estimated_seconds_all_14_tests": round(total_flops / flops_per_second, 1),
            "per_combination_gflop": {k: round(v / 1e9, 1) for k, v in per_test.items()},
            "library_matrix_mb": {str(w): round(running * (w + 1) * 8 / 1e6, 1)
                                  for w in rules.pattern_windows},
            "note": "upper bound: label validity per horizon can only remove library windows"}


def execute(workspace_root: Path, snapshot_id: str, *, c_raw_root: Path | None = None,
            rules: AnalogRules | None = None, log: Callable[[str], None] = print,
            duplicate_rows_allowed: bool = False) -> D1Result:
    """Run D1 end to end and return its report. Raises ``HardFail`` on any R/F condition."""
    started = time.perf_counter()
    rules = rules or load_rules()
    log(f"rules checksum {rules.checksum[:12]} (declared {DECLARED_RULES_CHECKSUM[:12]})")
    history = load_daily_history(workspace_root, snapshot_id, allowed_exchanges=rules.allowed_exchanges,
                                c_raw_root=c_raw_root, duplicate_rows_allowed=duplicate_rows_allowed)
    grid = history.grid
    load_seconds = time.perf_counter() - started
    log(f"grid {grid.session(0)}..{grid.session(len(grid) - 1)} N={len(grid)} "
        f"digest {grid.digest[:12]} ({load_seconds:.1f}s)")

    eval_start, eval_end = rules.eval_range(len(grid))
    eval_indices = tuple(range(eval_start, eval_end + 1))
    eligibility = _eligibility_cache(history, rules)

    first_snapshot = history.snapshot_dates[0] if history.snapshot_dates else None
    dates_before_first_snapshot = sum(1 for d in grid.dates if first_snapshot and d < first_snapshot)

    library_indices = universe.library_end_indices(rules, 0, eval_end)
    library_sizes: dict[int, int] = {}
    for index in library_indices:
        library_sizes[index] = eligibility(index).count
    log(f"library stride dates {len(library_indices)}, windows {sum(library_sizes.values()):,}")

    query_counts: dict[int, int] = {}
    sampled_total = 0
    figi_eligible = figi_null = 0
    reason_totals = {r.value: 0 for r in REASON_ORDER}
    reason_totals["ELIGIBLE"] = 0
    short_dates: list[str] = []
    below_minimum: list[str] = []
    unique_sampled: set[str] = set()
    for index in eval_indices:
        result = eligibility(index)
        names = result.tickers(history.tickers)
        query_counts[index] = len(names)
        for key, value in result.reason_counts().items():
            reason_totals[key] += value
        total, nulls = _figi_stats(history, index, names)
        figi_eligible += total
        figi_null += nulls
        sample = query_sample(grid.session(index).isoformat(), names, rules.queries_per_date)
        sampled_total += len(sample)
        unique_sampled.update(sample)
        if len(names) < rules.queries_per_date:
            short_dates.append(grid.session(index).isoformat())
        if len(names) < rules.min_valid_queries_per_date:
            below_minimum.append(grid.session(index).isoformat())
    log(f"query dates {len(eval_indices)} ({grid.session(eval_start)}..{grid.session(eval_end)}), "
        f"sampled {sampled_total:,} windows, {len(unique_sampled):,} unique tickers")

    cost = _search_cost_estimate(rules, library_sizes, eval_indices,
                                 {k: min(v, rules.queries_per_date) for k, v in query_counts.items()})
    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze,
        extra={"preflight_contract_sha256": _sha256_file(PREFLIGHT_DOC),
               "rules_file_sha256": _sha256_file(RULES_PATH),
               "eval_range": [eval_start, eval_end],
               "queries_per_date": rules.queries_per_date,
               "library_stride": rules.library_stride,
               "tests": [t.name for t in rules.tests]})

    duplicates = int(history.checks["grouped"]["duplicate_ticker_rows"])
    verdict = "BLOCKED" if duplicates else "PASS"
    report: dict[str, Any] = {
        "phase": PHASE,
        "verdict": verdict,
        "run_id": identity.run_id,
        "freeze_identity": history.freeze.as_dict(),
        "grid": {"first_session": grid.session(0).isoformat(),
                 "last_session": grid.session(len(grid) - 1).isoformat(),
                 "session_count": len(grid), "digest": grid.digest,
                 "checks": {k: v for k, v in history.checks.items() if k.startswith("G")}},
        "dataset": {"panel_tickers": len(history.tickers),
                    "reference_snapshots": history.checks["reference_snapshots"],
                    "snapshot_dates": [d.isoformat() for d in history.snapshot_dates],
                    "split_records": history.checks["split_records"],
                    "grouped_rows_total": history.checks["grouped"]["rows_total"],
                    "grouped_rows_kept": history.checks["grouped"]["rows_kept"],
                    "duplicate_ticker_rows": duplicates,
                    "duplicate_examples": history.checks["grouped"]["duplicate_examples"],
                    "read_locations": history.checks["grouped"]["read_locations"],
                    "dates_before_first_snapshot": dates_before_first_snapshot},
        "universe": {"eval_range": [eval_start, eval_end], "eval_dates": len(eval_indices),
                     "eligible_per_date": _percentiles(list(query_counts.values())),
                     "eligible_ticker_dates": reason_totals["ELIGIBLE"],
                     "reason_totals": reason_totals,
                     "dates_below_queries_per_date": len(short_dates),
                     "dates_below_min_valid_queries": len(below_minimum),
                     "dates_below_min_valid_examples": below_minimum[:10],
                     "eligible_by_date_idx": [query_counts[i] for i in eval_indices]},
        "query_sampling": {"sampled_windows": sampled_total,
                           "unique_sampled_tickers": len(unique_sampled),
                           "hash": "Q|20260917|{D}|{ticker}"},
        "library": {"stride": rules.library_stride, "stride_dates": len(library_indices),
                    "first_end_idx": library_indices[0] if library_indices else None,
                    "last_end_idx": library_indices[-1] if library_indices else None,
                    "windows_total": sum(library_sizes.values()),
                    "windows_per_stride_date": _percentiles(list(library_sizes.values())),
                    "windows_by_end_idx": {str(i): library_sizes[i] for i in library_indices}},
        "figi": {"eligible_ticker_dates": figi_eligible, "composite_figi_null": figi_null,
                 "null_share": round(figi_null / figi_eligible, 6) if figi_eligible else None},
        "cost_estimate": cost,
        "timing": {"load_seconds": round(load_seconds, 1),
                   "total_seconds": round(time.perf_counter() - started, 1)},
    }
    return D1Result(identity, report, verdict)


def run_context(workspace_root: Path, snapshot_id: str, current_pointer: Mapping[str, Any] | None,
                ) -> dict[str, Any]:
    """Non-deterministic facts, kept out of the identity digest on purpose (Pre-flight §7.2)."""
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "workspace_root": str(workspace_root),
            "snapshot_id_requested": snapshot_id, "current_snapshot_pointer": current_pointer,
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS")}


def write_run(result: D1Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    """Local artifacts only. ``COMPLETE.json`` is written last; without it D2 must not read the run."""
    run_dir = runs_dir / result.identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_identity.json").write_text(
        json.dumps(result.identity.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "d1_report.json").write_text(
        json.dumps(result.report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "run_context.json").write_text(
        json.dumps(dict(context), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if result.verdict == "PASS":
        (run_dir / "COMPLETE.json").write_text(json.dumps(
            {"phase": PHASE, "run_id": result.identity.run_id, "verdict": result.verdict,
             "identity_digest": result.identity.digest,
             "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result.run_dir = run_dir
    return run_dir
