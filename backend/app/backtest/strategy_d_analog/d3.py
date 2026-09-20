"""D3: join the frozen NeighborSet to real forward labels and build S(q) and sigma(q).

D3 adds exactly one thing to D2: what actually happened to each analogue. It does not re-select,
re-score or re-rank anything - the parent D2 run is immutable input, verified by digest before a
single label is read, and a query keeps the 50 neighbours D2 gave it even if some of them turn
out to be unusable. Reaching past rank 50 to refill K would silently change the D2 result.

The file is organised around the firewall the phase exists to maintain. ``build_signals`` sees
neighbour labels only, through an embargo check it re-runs itself; ``build_evaluation`` produces
the query's own realized return into a separate table. Nothing here computes an IC, a quintile,
a baseline or a verdict - D3 cannot tell you whether the signal is any good, and that is the
point of stopping here.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import resource
import time
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.strategy_d_analog import artifacts, evaluation, labels, signal, universe
from app.backtest.strategy_d_analog.config import (
    DECLARED_RULES_CHECKSUM, REPO_ROOT, RULES_PATH, AnalogRules, load_rules,
)
from app.backtest.strategy_d_analog.identity import RunIdentity, run_identity
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.labels import LABEL_VALUE_CONTRACT
from app.backtest.strategy_d_analog.models import FreezeIdentity, HardFail, TestId
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history, read_set_digest

PHASE = "D3"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d/runs"
#: Columns of the parent neighbour table D3 reads. The set is the parent's digest column set, so
#: reading is also verifying: a changed parent cannot be consumed unnoticed (D3 §3).
NEIGHBOR_COLUMNS = ("query_date_idx", "sample_rank", "rank", "library_row", "neighbor_end_idx",
                    "neighbor_ticker_col", "neighbor_figi_code", "metric_value", "rank_score",
                    "label_end_idx")


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


@dataclass(frozen=True)
class ParentD2:
    """The D2 run this D3 is bound to, and the digests that pin every artifact it will read."""

    run_dir: Path
    run_id: str
    identity_digest: str
    complete_digest: str
    freeze: FreezeIdentity
    rules_checksum: str
    content_digests: Mapping[str, str]
    tests: tuple[str, ...]
    eval_range: tuple[int, int]
    top_k: int

    def as_payload(self) -> dict[str, Any]:
        return {"parent_d2_run_id": self.run_id, "parent_d2_identity": self.identity_digest,
                "parent_d2_complete_digest": self.complete_digest}


def load_parent(run_dir: Path) -> ParentD2:
    """Admit a finished D2 run, or refuse. This is the whole of D3's trust in its input."""
    complete_path = run_dir / "COMPLETE.json"
    if not complete_path.exists():
        raise HardFail("F2", f"{run_dir} has no COMPLETE.json: D3 may not read an unfinished run")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    if complete.get("phase") != "D2" or complete.get("verdict") != "PASS":
        raise HardFail("F2", f"{run_dir} is a {complete.get('phase')} run with verdict "
                             f"{complete.get('verdict')}")
    identity = json.loads((run_dir / "run_identity.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if identity.get("rules_checksum") != DECLARED_RULES_CHECKSUM:
        raise HardFail("R1", f"parent ran under rules {identity.get('rules_checksum')}")
    if identity["identity_digest"] != complete["identity_digest"]:
        raise HardFail("F2", "the parent's COMPLETE token and run identity disagree")
    freeze = FreezeIdentity(**identity["data"])
    for name, value in (("freeze_id", freeze.freeze_id), ("freeze_digest", freeze.freeze_digest),
                        ("grid_digest", freeze.grid_digest)):
        if complete.get(name) != value:
            raise HardFail("F1", f"parent COMPLETE {name} disagrees with its run identity")
    return ParentD2(
        run_dir=run_dir, run_id=str(identity["run_id"]),
        identity_digest=str(identity["identity_digest"]),
        complete_digest=artifacts.sha256_file(complete_path), freeze=freeze,
        rules_checksum=str(identity["rules_checksum"]),
        content_digests=dict(summary["content_digests"]), tests=tuple(identity["tests"]),
        eval_range=(int(identity["eval_range"][0]), int(identity["eval_range"][1])),
        top_k=int(identity["top_k"]))


def read_neighbors(parent: ParentD2, test: TestId) -> dict[str, np.ndarray]:
    """Read one parent neighbour table and prove it is byte-for-byte the one D2 wrote."""
    path = parent.run_dir / f"neighbors_{test.name}.parquet"
    stamp = artifacts.read_identity(path)
    if stamp["freeze_digest"] != parent.freeze.freeze_digest \
            or stamp["grid_digest"] != parent.freeze.grid_digest \
            or stamp["rules_checksum"] != parent.rules_checksum:
        raise HardFail("F1", f"{path.name} carries a different freeze, grid or rules identity")
    table = pq.read_table(path, columns=list(NEIGHBOR_COLUMNS))
    columns = {name: np.asarray(table[name]) for name in NEIGHBOR_COLUMNS}
    del table
    digest_columns = {name: (values if values.dtype.kind == "f" else values.astype(np.int64))
                      for name, values in columns.items()}
    found = artifacts.column_digest(digest_columns, artifacts.NEIGHBOR_DIGEST_COLUMNS)
    expected = parent.content_digests[f"neighbors_{test.name}"]
    if found != expected:
        raise HardFail("F1", f"{path.name} digest {found} != parent record {expected}")
    del digest_columns
    return columns


def read_query_results(parent: ParentD2, test: TestId) -> dict[str, np.ndarray]:
    """The parent's per-query row for this test, ordered by ``(query_date_idx, sample_rank)``."""
    table = pq.read_table(parent.run_dir / "query_results.parquet",
                          columns=["test_id", "query_date_idx", "sample_rank", "query_ticker_col",
                                   "query_figi_code", "status", "accepted_count"])
    names = np.asarray(table["test_id"].to_pylist())
    keep = np.nonzero(names == test.name)[0]
    if keep.size == 0:
        raise HardFail("F2", f"the parent has no query rows for {test.name}")
    out = {name: np.asarray(table[name])[keep] for name in
           ("query_date_idx", "sample_rank", "query_ticker_col", "query_figi_code",
            "accepted_count")}
    out["status"] = np.asarray(table["status"].to_pylist())[keep]
    order = np.lexsort((out["sample_rank"], out["query_date_idx"]))
    return {name: values[order] for name, values in out.items()}


def align(neighbors: Mapping[str, np.ndarray], queries: Mapping[str, np.ndarray]) -> np.ndarray:
    """Check the neighbour rows expand the query rows exactly, and return the per-query counts.

    Aligning by position rather than by a join keeps the pass linear, but only if the parent's
    row order really is ``(query, rank)``; that is asserted here instead of assumed.
    """
    counts = queries["accepted_count"].astype(np.int64)
    if int(counts.sum()) != neighbors["rank"].shape[0]:
        raise HardFail("F2", f"parent query rows claim {int(counts.sum())} neighbours but the "
                             f"neighbour table has {neighbors['rank'].shape[0]}")
    expected_date = np.repeat(queries["query_date_idx"], counts)
    expected_rank = np.repeat(queries["sample_rank"], counts)
    if not (np.array_equal(expected_date, neighbors["query_date_idx"])
            and np.array_equal(expected_rank, neighbors["sample_rank"])):
        raise HardFail("F2", "the parent neighbour table is not ordered by (query, rank)")
    positions = np.arange(neighbors["rank"].shape[0]) - np.repeat(
        np.concatenate([[0], np.cumsum(counts)[:-1]]), counts)
    if not np.array_equal(neighbors["rank"].astype(np.int64), positions + 1):
        raise HardFail("R10", "neighbour ranks are not 1..n within every query")
    return counts


def build_signals(*, test: TestId, neighbors: Mapping[str, np.ndarray],
                  queries: Mapping[str, np.ndarray], counts: np.ndarray,
                  excess: np.ndarray, top_k: int) -> signal.ForwardDistribution:
    """The signal path. Takes neighbour labels; never receives the query's own future.

    ``excess`` is the full label matrix, but every value this function reads is addressed by a
    *neighbour's* window end, and ``signal.build`` refuses any neighbour the embargo does not
    clear - so a query date's own row is unreachable from here by construction.
    """
    neighbor_excess = labels.gather(excess, neighbors["neighbor_end_idx"].astype(np.int64),
                                    neighbors["neighbor_ticker_col"].astype(np.int64))
    distribution = signal.build(
        representation=test.representation, window=test.window, horizon=test.horizon,
        query_end_idx=queries["query_date_idx"].astype(np.int64),
        neighbor_end_idx=neighbors["neighbor_end_idx"].astype(np.int64),
        neighbor_excess=neighbor_excess, metric_value=neighbors["metric_value"],
        counts=counts, top_k=top_k)
    signal.assert_signal_is_the_declared_median(distribution)
    return distribution


def signal_table(test: TestId, queries: Mapping[str, np.ndarray],
                 distribution: signal.ForwardDistribution, history: DailyHistory,
                 ) -> tuple[pa.Table, dict[str, np.ndarray]]:
    size = len(distribution)
    dates = pa.DictionaryArray.from_arrays(
        pa.array(queries["query_date_idx"], pa.int32()),
        pa.array(list(history.grid.dates), pa.date32()))
    tickers = pa.DictionaryArray.from_arrays(
        pa.array(queries["query_ticker_col"], pa.int32()),
        pa.array(list(history.tickers), pa.string()))
    columns: dict[str, Any] = {
        "test_id": pa.array([test.name] * size, pa.dictionary(pa.int32(), pa.string())),
        "query_date_idx": pa.array(queries["query_date_idx"], pa.int32()),
        "query_date": dates,
        "query_ticker": tickers,
        "query_ticker_col": pa.array(queries["query_ticker_col"], pa.int32()),
        "sample_rank": pa.array(queries["sample_rank"], pa.int16()),
        "window": pa.array(np.full(size, test.window, dtype=np.int16), pa.int16()),
        "horizon": pa.array(np.full(size, test.horizon, dtype=np.int16), pa.int16()),
        "representation": pa.array([test.representation] * size,
                                   pa.dictionary(pa.int32(), pa.string())),
        "neighbor_total": pa.array(distribution.n_total, pa.int16()),
        "neighbor_valid": pa.array(distribution.n_valid, pa.int16()),
        "S": pa.array(distribution.signal, pa.float64()),
        "sigma": pa.array(distribution.sigma, pa.float64()),
        "distribution_mean": pa.array(distribution.mean, pa.float64()),
        "distribution_median": pa.array(distribution.median, pa.float64()),
        "distribution_std": pa.array(distribution.std, pa.float64()),
        "positive_count": pa.array(distribution.positive_count, pa.int16()),
        "negative_count": pa.array(distribution.negative_count, pa.int16()),
        "zero_count": pa.array(distribution.zero_count, pa.int16()),
        "hit_rate": pa.array(distribution.hit_rate, pa.float64()),
        "signal_status": pa.DictionaryArray.from_arrays(
            pa.array(distribution.status.astype(np.int32), pa.int32()),
            pa.array(list(signal.STATUS_ORDER), pa.string())),
    }
    for quantile in signal.QUANTILES:
        columns[f"distribution_q{int(quantile * 100):02d}"] = pa.array(
            distribution.quantiles[quantile], pa.float64())
    digest = {"query_date_idx": queries["query_date_idx"].astype(np.int64),
              "sample_rank": queries["sample_rank"].astype(np.int64),
              "query_ticker_col": queries["query_ticker_col"].astype(np.int64),
              "neighbor_total": distribution.n_total.astype(np.int64),
              "neighbor_valid": distribution.n_valid.astype(np.int64),
              "status": distribution.status.astype(np.int64),
              "S": distribution.signal, "sigma": distribution.sigma,
              "mean": distribution.mean, "median": distribution.median, "std": distribution.std,
              "positive": distribution.positive_count.astype(np.int64),
              "negative": distribution.negative_count.astype(np.int64),
              "zero": distribution.zero_count.astype(np.int64), "hit_rate": distribution.hit_rate}
    for quantile in signal.QUANTILES:
        digest[f"q{int(quantile * 100):02d}"] = distribution.quantiles[quantile]
    return pa.table(columns), digest


def evaluation_table(rows: Sequence[evaluation.EvaluationLabels], history: DailyHistory,
                     ) -> tuple[pa.Table, dict[str, np.ndarray]]:
    horizon = np.concatenate([np.full(len(r), r.horizon, dtype=np.int16) for r in rows])
    date_idx = np.concatenate([r.query_date_idx for r in rows])
    rank = np.concatenate([r.sample_rank for r in rows])
    ticker = np.concatenate([r.ticker_col for r in rows])
    forward = np.concatenate([r.forward_return for r in rows])
    close = np.concatenate([r.close_return for r in rows])
    valid = np.concatenate([r.label_valid for r in rows])
    table = pa.table({
        "horizon": pa.array(horizon, pa.int16()),
        "query_date_idx": pa.array(date_idx, pa.int32()),
        "query_date": pa.DictionaryArray.from_arrays(
            pa.array(date_idx, pa.int32()), pa.array(list(history.grid.dates), pa.date32())),
        "query_ticker": pa.DictionaryArray.from_arrays(
            pa.array(ticker, pa.int32()), pa.array(list(history.tickers), pa.string())),
        "query_ticker_col": pa.array(ticker, pa.int32()),
        "sample_rank": pa.array(rank, pa.int16()),
        "query_forward_return": pa.array(forward, pa.float64()),
        "query_close_return": pa.array(close, pa.float64()),
        "query_label_valid": pa.array(valid, pa.bool_())})
    digest = {"horizon": horizon.astype(np.int64), "query_date_idx": date_idx.astype(np.int64),
              "sample_rank": rank.astype(np.int64), "query_ticker_col": ticker.astype(np.int64),
              "query_forward_return": forward, "query_close_return": close,
              "query_label_valid": valid.astype(np.int64)}
    return table, digest


@dataclass
class D3Result:
    identity: RunIdentity
    summary: dict[str, Any]
    status_counts: dict[str, Any]
    parent: dict[str, Any]
    digests: dict[str, str]
    verdict: str
    run_dir: Path
    elapsed: float = 0.0
    extras: dict = field(default_factory=dict, repr=False)


def execute(workspace_root: Path, snapshot_id: str, *, parent_dir: Path,
            rules: AnalogRules | None = None, c_raw_root: Path | None = None,
            runs_dir: Path | None = None, log: Callable[[str], None] = print,
            test_limit: int | None = None) -> D3Result:
    """Run D3 end to end: verify the parent, join labels, build signals, write artifacts."""
    started = time.perf_counter()
    rules = rules or load_rules()
    parent = load_parent(parent_dir)
    log(f"parent D2 {parent.run_id} identity {parent.identity_digest[:12]} verified")
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges,
                                 expected=parent.freeze, c_raw_root=c_raw_root)
    pre_digest = history.freeze.d_read_digest
    grid = history.grid
    eval_start, eval_end = parent.eval_range
    horizons = labels.horizons_of(rules.combinations)

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze, parent=parent.run_id,
        extra={**parent.as_payload(),
               "rules_file_sha256": artifacts.sha256_file(RULES_PATH),
               "label_value_contract": LABEL_VALUE_CONTRACT,
               "label_validity_contract": "d-label-validity-v1",
               "eval_range": [eval_start, eval_end], "top_k": parent.top_k,
               "signal": {"S": "median neighbour excess_return_h over the accepted top_k",
                          "sigma_A": "mean rho over the accepted top_k",
                          "sigma_B": "-mean(d) / sqrt(W) over the accepted top_k",
                          "excess": "close_return_h minus the date's eligible label-valid median",
                          "std_ddof": signal.STD_DDOF},
               "label_horizons": [int(h) for h in horizons],
               "implementation": {"numpy": np.__version__, "pyarrow": pa.__version__,
                                  "openblas_num_threads":
                                      os.environ.get("OPENBLAS_NUM_THREADS", "unset")},
               "tests": list(parent.tests)})
    run_dir = (runs_dir or RUNS_DIR) / identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = artifacts.identity_block(history.freeze, rules.checksum)
    log(f"run {identity.run_id} -> {run_dir}")

    mark = time.perf_counter()
    validity = compute_validity(history.panel, horizons, rules.ca_suspect_ratio)
    masks = {index: universe.evaluate(history.panel_view(index), history.membership(index),
                                      rules).eligible for index in range(len(grid))}
    eligible = labels.eligibility_matrix(len(grid), len(history.tickers), masks)
    del masks
    forward = labels.compute_labels(history.panel, validity, eligible, log=log)
    log(f"labels built in {time.perf_counter() - mark:.1f}s")

    tests = [t for t in rules.tests if t.name in parent.tests]
    if test_limit is not None:
        tests = tests[:test_limit]
    signal_tables: list[pa.Table] = []
    signal_digests: dict[str, np.ndarray] = {}
    status_totals: dict[str, dict[str, int]] = {}
    join_totals = {"neighbor_rows": 0, "neighbor_valid": 0, "neighbor_invalid": 0}
    query_keys: dict[tuple, np.ndarray] | None = None

    for test in tests:
        mark = time.perf_counter()
        neighbors = read_neighbors(parent, test)
        queries = read_query_results(parent, test)
        counts = align(neighbors, queries)
        distribution = build_signals(test=test, neighbors=neighbors, queries=queries,
                                     counts=counts, excess=forward.excess_for(test.horizon),
                                     top_k=parent.top_k)
        join_totals["neighbor_rows"] += int(counts.sum())
        join_totals["neighbor_valid"] += int(distribution.n_valid.sum())
        join_totals["neighbor_invalid"] += int(counts.sum() - distribution.n_valid.sum())
        table, digest = signal_table(test, queries, distribution, history)
        signal_tables.append(table)
        for name, values in digest.items():
            signal_digests.setdefault(name, []).append(values)
        status_totals[test.name] = signal.status_counts(distribution)
        if query_keys is None:
            query_keys = {"query_date_idx": queries["query_date_idx"].copy(),
                          "sample_rank": queries["sample_rank"].copy(),
                          "query_ticker_col": queries["query_ticker_col"].copy()}
        log(f"  {test.name}: {int(counts.sum()):,} neighbour labels, "
            f"{status_totals[test.name]} , {time.perf_counter() - mark:.1f}s, "
            f"rss {peak_rss_mb():.0f} MB")
        del neighbors, queries, counts, distribution, table

    digests: dict[str, str] = {}
    files: dict[str, Any] = {}
    combined = pa.concat_tables(signal_tables)
    files["signal_rows.parquet"] = artifacts.write_table(run_dir / "signal_rows.parquet",
                                                         combined, stamp)
    stacked = {name: np.concatenate(parts) for name, parts in signal_digests.items()}
    stacked["test_id_code"] = np.repeat(np.arange(len(tests), dtype=np.int64),
                                        [t.num_rows for t in signal_tables])
    digests["signal_rows"] = artifacts.column_digest(stacked, tuple(sorted(stacked)))
    del combined, signal_tables, signal_digests, stacked

    if query_keys is None:
        raise HardFail("F4", "no test produced a signal table")
    evaluation_rows = [evaluation.build(forward, horizon, query_keys["query_date_idx"],
                                        query_keys["sample_rank"], query_keys["query_ticker_col"])
                       for horizon in horizons]
    table, digest = evaluation_table(evaluation_rows, history)
    files["evaluation_labels.parquet"] = artifacts.write_table(
        run_dir / "evaluation_labels.parquet", table, stamp)
    digests["evaluation_labels"] = artifacts.column_digest(digest, tuple(sorted(digest)))
    label_counts = {str(r.horizon): r.counts() for r in evaluation_rows}
    del table, digest, evaluation_rows

    post_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if post_digest != pre_digest:
        raise HardFail("R2", f"INPUT_MUTATED_DURING_D3: the D read set changed while the run was"
                             f" in flight ({pre_digest} -> {post_digest})")

    status_payload = {"signal_status_by_test": status_totals,
                      "neighbor_label_join": join_totals,
                      "query_label_rows_by_horizon": label_counts}
    digests["status_counts"] = artifacts.write_json(
        run_dir / "status_counts.json", status_payload, stamp)["content_sha256"]
    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "phase": PHASE, "run_id": identity.run_id, "parent_run": parent.run_id,
        **parent.as_payload(),
        "freeze_identity": history.freeze.as_dict(),
        "grid": {"first_session": grid.session(0).isoformat(),
                 "last_session": grid.session(len(grid) - 1).isoformat(),
                 "session_count": len(grid), "digest": grid.digest},
        "evaluation": {"eval_range": [eval_start, eval_end], "top_k": parent.top_k,
                       "tests": [t.name for t in tests]},
        "label_value_counts": forward.counts(),
        "signal_status_by_test": status_totals,
        "neighbor_label_join": join_totals,
        "query_label_rows_by_horizon": label_counts,
        "content_digests": dict(sorted(digests.items())),
        "input_read_set": {"pre": pre_digest, "post": post_digest, "match": True},
        "artifact_identity": stamp,
        "files": files,
        "performance": {"elapsed_seconds": round(elapsed, 1),
                        "peak_rss_mb": round(peak_rss_mb(), 1)},
    }
    return D3Result(identity, summary, status_payload, parent.as_payload(), digests, "PASS",
                    run_dir, elapsed, {"history": history, "rules": rules, "labels": forward})


def run_context(workspace_root: Path, snapshot_id: str) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id,
            "peak_rss_mb": round(peak_rss_mb(), 1)}


def finish(result: D3Result, context: Mapping[str, Any]) -> Path:
    """Manifests, then the COMPLETE token - only after every check in ``execute`` has passed."""
    run_dir = result.run_dir
    stamp = result.summary["artifact_identity"]
    artifacts.write_json(run_dir / "run_identity.json", result.identity.as_dict(), stamp)
    artifacts.write_json(run_dir / "parent.json", result.parent, stamp)
    artifacts.write_json(run_dir / "run_context.json", dict(context), stamp)
    artifacts.write_json(run_dir / "summary.json", result.summary, stamp)
    if result.verdict == "PASS":
        artifacts.write_complete(run_dir, {"phase": PHASE, "run_id": result.identity.run_id,
                                           "verdict": result.verdict,
                                           "identity_digest": result.identity.digest,
                                           **result.parent}, stamp)
    return run_dir
