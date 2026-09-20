"""D-V2A-3: join the Top-K analogs to their realized outcomes and build A(q).

This is the phase where an outcome first enters the study, and the whole design is about where
it is allowed to enter. Neighbour labels are read at the neighbours' own end dates, every one of
which cleared the embargo two phases ago and is re-checked here row by row. Query labels are
read at the query dates and written to their own artifact, which nothing in this phase joins to
the signal. ``analog_signal`` receives an array of numbers and a group size and has no argument
or import through which a query's own future could arrive.

What is still not computed: any correlation, any quintile, any block statistic, any comparison
of A against B0 or against a realized return. That is D-V2A-4, and the summary this phase writes
deliberately carries no statistic that would hint at it.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
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

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_analog import artifacts as v1_artifacts
from app.backtest.strategy_d_analog.identity import query_sample
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import analog_signal, b0_composite, evaluation_labels, pit, schema
from app.backtest.strategy_d_v2 import structure_features
from app.backtest.strategy_d_v2.config import (
    CONTRACT_DOC, FEATURE_NAMES, REPO_ROOT, RULES_PATH, V2ARules, declared_checksum, load_rules,
)
from app.backtest.strategy_d_v2.d1 import (
    assert_freeze_binding, eligibility_matrix, matrix_digest, _named_digest,
)
from app.backtest.strategy_d_v2.d2 import (
    NEIGHBOR_DIGEST_COLUMNS, ParentD1, PEAK_RSS_LIMIT_MB, load_parent, mutate_future,
    verify_parent_matrices,
)
from app.backtest.strategy_d_v2.identity import RunIdentity, run_identity
from app.backtest.strategy_d_v2.models import HardFail, PointInTimeViolation

PHASE = "D3"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d_v2/runs"
#: Query dates re-derived end to end on a future-mutated panel (contract request §18).
MUTATION_AUDIT_DATES = 3
#: What D3 must reproduce from its D1 parent before it builds anything. The library digest is
#: D2's business and is bound through D2's own COMPLETE token instead.
REQUIRED_D1_DIGESTS = ("raw_feature_matrix", "rank_feature_matrix", "validity_mask",
                       "vector_status", "label_validity_primary", "query_sample",
                       "b0_rows", "b0_strong_rows")
SIGNAL_STATUS_ORDER = ("OK", "VECTOR_UNDEFINED", "INSUFFICIENT_NEIGHBORS")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _json_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class D3Result:
    identity: RunIdentity
    report: dict[str, Any]
    tables: dict[str, pa.Table]
    verdict: str
    run_dir: Path | None = None


# -- parent binding ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ParentD2:
    run_id: str
    identity_digest: str
    identity: Mapping[str, Any]
    summary: Mapping[str, Any]
    neighbors_digest: str
    query_digest: str
    feature_schema_digest: str
    files_sha256: Mapping[str, str]
    neighbors_path: Path


def load_parent_d2(runs_dir: Path, run_id: str, rules: V2ARules,
                   feature_schema: schema.FeatureSchema, parent_d1: ParentD1) -> ParentD2:
    """Bind to one finished D2 run and to the D1 run that produced its coordinates."""
    run_dir = runs_dir / run_id
    complete_path = run_dir / "COMPLETE.json"
    if not complete_path.exists():
        raise HardFail("R2", f"{run_dir} has no COMPLETE.json: D2 did not finish its checks")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    identity = json.loads((run_dir / "identity.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if complete.get("verdict") != "PASS":
        raise HardFail("R2", f"parent {run_id} verdict is {complete.get('verdict')!r}")
    if identity.get("phase") != "D2":
        raise HardFail("R2", f"parent {run_id} is phase {identity.get('phase')!r}")
    if complete.get("identity_digest") != identity.get("identity_digest"):
        raise HardFail("R2", f"parent {run_id} identity does not match its own COMPLETE token")
    if identity.get("rules_checksum") != rules.checksum:
        raise HardFail("R1", f"parent rules checksum {identity.get('rules_checksum')}")
    if identity.get("parent") != parent_d1.identity_digest:
        raise HardFail("R2", f"D2 parent {run_id} was built on D1 {identity.get('parent')},"
                             f" not on {parent_d1.identity_digest}")
    feature_schema.assert_matches(str(identity.get("feature_schema_digest")))
    neighbors_digest = str(complete.get("neighbors_digest"))
    if neighbors_digest != summary["digests"]["neighbors"]:
        raise HardFail("R2", "the D2 COMPLETE token and its summary disagree on the neighbour digest")
    if summary["query_sample"]["digest"] != parent_d1.digests["query_sample"]:
        raise HardFail("R2", "the D2 query sample is not the one D1 drew")
    files = {name: _sha256_file(run_dir / name)
             for name in ("COMPLETE.json", "identity.json", "summary.json", "parent_d1.json",
                          "library_manifest.json", "query_manifest.json", "status_counts.json",
                          "feature_schema.json", "neighbors.parquet")}
    return ParentD2(run_id, str(identity["identity_digest"]), identity, summary, neighbors_digest,
                    str(summary["query_sample"]["digest"]),
                    str(identity["feature_schema_digest"]), files, run_dir / "neighbors.parquet")


# -- neighbour table ----------------------------------------------------------------------------

def read_neighbors(path: Path) -> dict[str, np.ndarray]:
    """The digest columns of the D2 artifact, as numpy arrays. Dictionary columns are skipped."""
    table = pq.read_table(path, columns=list(NEIGHBOR_DIGEST_COLUMNS))
    return {name: table.column(name).to_numpy(zero_copy_only=False)
            for name in NEIGHBOR_DIGEST_COLUMNS}


def verify_neighbors_digest(columns: Mapping[str, np.ndarray], expected: str) -> str:
    found = v1_artifacts.column_digest(columns, NEIGHBOR_DIGEST_COLUMNS)
    if found != expected:
        raise HardFail("R2", f"neighbour digest {found} != parent {expected}")
    return found


def assert_neighbor_layout(*, query_date_idx: np.ndarray, sample_rank: np.ndarray,
                           rank: np.ndarray, top_k: int, queries: int) -> None:
    """R11: the rows are exactly ``queries`` contiguous blocks of ``top_k``, in rank order.

    The median that follows reshapes this array, so the layout is not an assumption the phase
    may make about its parent: it is checked, and a parent that wrote rows some other way stops
    the run instead of being silently regrouped.
    """
    total = queries * top_k
    if query_date_idx.shape[0] != total:
        raise HardFail("R11", f"{query_date_idx.shape[0]} neighbour rows for {queries} queries"
                              f" x top_k {top_k}")
    if not (np.diff(query_date_idx.astype(np.int64)) >= 0).all():
        raise HardFail("R11", "neighbour rows are not grouped by query date")
    blocks_rank = rank.reshape(queries, top_k)
    if not np.array_equal(blocks_rank, np.tile(np.arange(1, top_k + 1), (queries, 1))):
        raise HardFail("R11", "neighbour ranks are not 1..top_k inside every query block")
    for name, values in (("query_date_idx", query_date_idx), ("sample_rank", sample_rank)):
        blocks = values.reshape(queries, top_k).astype(np.int64)
        if not (blocks == blocks[:, :1]).all():
            raise HardFail("R11", f"{name} is not constant inside a query block")


def assert_label_embargo(query_date_idx: np.ndarray, neighbor_end_idx: np.ndarray, *,
                         lookback: int, horizon: int) -> int:
    """R4/R6: every neighbour label window ends before the query's own coordinate window opens.

    D2 selected under this rule; D3 re-checks it on the rows it is about to turn into numbers.
    Trusting the parent here would mean the one phase that reads outcomes is also the one phase
    that does not verify when they were knowable.
    """
    limit = query_date_idx.astype(np.int64) - lookback - horizon
    worst = int((neighbor_end_idx.astype(np.int64) - limit).max()) if neighbor_end_idx.size else 0
    if worst > 0:
        offenders = int((neighbor_end_idx.astype(np.int64) > limit).sum())
        raise PointInTimeViolation(
            f"{offenders} neighbour labels end past the embargo (worst overshoot {worst} sessions)")
    return worst


def distance_summary(distance: np.ndarray, queries: int, top_k: int) -> dict[str, np.ndarray]:
    """Descriptive metadata only: the declaration forbids distance from touching A(q)."""
    blocks = distance.reshape(queries, top_k)
    return {"mean_distance": blocks.mean(axis=1), "median_distance": np.median(blocks, axis=1),
            "min_distance": blocks.min(axis=1), "max_distance": blocks.max(axis=1)}


# -- audit ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MutationReference:
    """What a future-mutated rebuild has to reproduce, and what it has to change."""

    date_idx: int
    positions: np.ndarray
    analog: np.ndarray
    b0: np.ndarray
    query_label: np.ndarray
    query_valid: np.ndarray


def future_query_mutation_audit(history: DailyHistory, rules: V2ARules, *,
                                references: Sequence[MutationReference],
                                neighbor_end: np.ndarray, neighbor_col: np.ndarray,
                                session_idx: np.ndarray, ticker_col: np.ndarray, top_k: int,
                                log: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """Replace everything after the query date with nonsense and re-derive the phase.

    The signal must not move: a neighbour's label window ends at ``d + h <= D - L``, so the
    mutated rows are not inside any of them, and B0 is built from coordinates that read only
    ``D - 60..D``. The query's own label must move: it is computed from ``D+1..D+h``, which is
    exactly what was replaced. A test that only checked the first half would pass on a pipeline
    that had stopped computing anything, so both directions are required.
    """
    horizon, lookback = rules.primary_horizon, rules.max_lookback
    findings: list[dict[str, Any]] = []
    changed_labels = 0
    checked = 0
    for reference in references:
        index = reference.date_idx
        panel = mutate_future(history.panel, index)
        local = _history_with_panel(history, panel, index)
        eligible, _ = eligibility_matrix(local, rules, index)
        features = structure_features.build(panel, eligible, rules, diagnostic_frame=False,
                                            retain_raw=False)
        validity = compute_validity(panel, (horizon,), rules.ca_suspect_ratio)
        excess = evaluation_labels.forward_excess(panel, validity, eligible, horizon)

        positions = reference.positions
        rows = np.concatenate([np.arange(p * top_k, (p + 1) * top_k) for p in positions])
        labels = excess.gather(neighbor_end[rows].astype(np.int64),
                               neighbor_col[rows].astype(np.int64))
        signal = analog_signal.median_by_group(labels, top_k)
        matrix = features.matrix(session_idx[positions], ticker_col[positions])
        b0 = b0_composite.build(matrix, rules)
        query = evaluation_labels.query_labels(excess, session_idx[positions],
                                               ticker_col[positions])

        issues: list[str] = []
        if not np.array_equal(signal.values, reference.analog):
            issues.append("analog_signal_changed")
        if not np.array_equal(b0.values, reference.b0):
            issues.append("b0_changed")
        moved = ~((np.isnan(query.values) & np.isnan(reference.query_label))
                  | (query.values == reference.query_label))
        changed_labels += int(moved.sum())
        checked += int(positions.size)
        if not moved.any():
            issues.append("query_label_did_not_move")   # positive control
        if issues:
            findings.append({"date_idx": int(index),
                             "session": history.grid.session(index).isoformat(),
                             "issues": issues})
        del features, excess, validity, eligible, panel, local
    log(f"future query mutation: {len(references)} dates, {checked:,} queries, "
        f"{changed_labels:,} labels moved, {len(findings)} findings")
    return {"dates": [int(r.date_idx) for r in references], "queries": checked,
            "query_labels_changed": changed_labels, "findings": findings,
            "signal_unchanged": not findings}


def _history_with_panel(history: DailyHistory, panel: Panel, last_idx: int) -> DailyHistory:
    from app.backtest.strategy_d_v2.models import SessionGrid
    grid = SessionGrid(tuple(history.grid.dates[: last_idx + 1]), history.grid.digest)
    snapshots = tuple(d for d in history.snapshot_dates if d <= grid.dates[-1])
    return DailyHistory(grid, panel, history.freeze, history.figi, snapshots, dict(history.checks))


# -- artifacts -------------------------------------------------------------------------------------

def _dictionary(codes: np.ndarray, values: Sequence[str]) -> pa.Array:
    return pa.DictionaryArray.from_arrays(pa.array(codes, type=pa.int32()),
                                          pa.array(list(values), type=pa.string()))


SIGNAL_DIGEST_COLUMNS = ("query_date_idx", "sample_rank", "query_ticker_col", "analog_signal_A",
                         "b0", "b0_strong", "neighbor_total", "neighbor_valid", "mean_distance",
                         "median_distance", "min_distance", "max_distance", "signal_status")
EVALUATION_DIGEST_COLUMNS = ("query_date_idx", "sample_rank", "query_ticker_col",
                             "query_label_valid", "query_excess_return_5")


def signal_table(columns: Mapping[str, np.ndarray], tickers: Sequence[str],
                 sessions: Sequence[date]) -> pa.Table:
    """The signal artifact: A(q), B0 and descriptive neighbour metadata. No realized outcome."""
    session_strings = [d.isoformat() for d in sessions]
    return pa.table({
        "query_date_idx": pa.array(columns["query_date_idx"]),
        "query_date": _dictionary(columns["query_date_idx"], session_strings),
        "query_ticker_col": pa.array(columns["query_ticker_col"]),
        "query_ticker": _dictionary(columns["query_ticker_col"], tickers),
        "sample_rank": pa.array(columns["sample_rank"]),
        "analog_signal_A": pa.array(columns["analog_signal_A"]),
        "b0": pa.array(columns["b0"]),
        "b0_strong": pa.array(columns["b0_strong"]),
        "neighbor_total": pa.array(columns["neighbor_total"]),
        "neighbor_valid": pa.array(columns["neighbor_valid"]),
        "mean_distance": pa.array(columns["mean_distance"]),
        "median_distance": pa.array(columns["median_distance"]),
        "min_distance": pa.array(columns["min_distance"]),
        "max_distance": pa.array(columns["max_distance"]),
        "signal_status": _dictionary(columns["signal_status"], SIGNAL_STATUS_ORDER),
    })


def evaluation_table(columns: Mapping[str, np.ndarray], tickers: Sequence[str],
                     sessions: Sequence[date], horizon: int) -> pa.Table:
    """The evaluation artifact, physically separate from the signal. D-V2A-4 joins them."""
    session_strings = [d.isoformat() for d in sessions]
    return pa.table({
        "query_date_idx": pa.array(columns["query_date_idx"]),
        "query_date": _dictionary(columns["query_date_idx"], session_strings),
        "query_ticker_col": pa.array(columns["query_ticker_col"]),
        "query_ticker": _dictionary(columns["query_ticker_col"], tickers),
        "sample_rank": pa.array(columns["sample_rank"]),
        "query_label_valid": pa.array(columns["query_label_valid"]),
        f"query_excess_return_{horizon}": pa.array(columns["query_excess_return_5"]),
    })


# -- execute -----------------------------------------------------------------------------------

def execute(workspace_root: Path, snapshot_id: str, *, parent_d1_run_id: str,
            parent_d2_run_id: str, c_raw_root: Path | None = None,
            rules: V2ARules | None = None, runs_dir: Path = RUNS_DIR,
            log: Callable[[str], None] = print, mutation_dates: int = MUTATION_AUDIT_DATES,
            bind_to_declaration: bool = True) -> D3Result:
    """Run D-V2A-3 end to end. Raises ``HardFail`` on any R/F condition."""
    started = time.perf_counter()
    rules = rules or load_rules()
    feature_schema = schema.build(rules)
    horizon, lookback, top_k = rules.primary_horizon, rules.max_lookback, rules.top_k
    log(f"rules {rules.checksum[:12]} schema {feature_schema.digest[:12]}")

    parent_d1 = load_parent(runs_dir, parent_d1_run_id, rules)
    parent_d2 = load_parent_d2(runs_dir, parent_d2_run_id, rules, feature_schema, parent_d1)
    log(f"parents D1 {parent_d1.run_id} / D2 {parent_d2.run_id}")

    pre_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges, c_raw_root=c_raw_root)
    binding = assert_freeze_binding(history, rules) if bind_to_declaration else {"enforced": False}
    for name, value in (("freeze_digest", history.freeze.freeze_digest),
                        ("grid_digest", history.freeze.grid_digest),
                        ("d_read_digest", history.freeze.d_read_digest)):
        for parent_name, parent_identity in (("D1", parent_d1.identity), ("D2", parent_d2.identity)):
            if parent_identity["data"][name] != value:
                raise HardFail("R2", f"{parent_name} parent {name} != loaded {value}")
    grid = history.grid
    load_seconds = time.perf_counter() - started
    eval_start, eval_end = rules.eval_range(len(grid))
    eval_indices = tuple(range(eval_start, eval_end + 1))
    log(f"grid N={len(grid)} digest {grid.digest[:12]} ({load_seconds:.1f}s)")

    # -- coordinates, labels ------------------------------------------------------------------
    eligible, per_index = eligibility_matrix(history, rules, eval_end, log)
    features = structure_features.build(history.panel, eligible, rules, diagnostic_frame=False)
    validity = compute_validity(history.panel, (horizon,), rules.ca_suspect_ratio)
    excess = evaluation_labels.forward_excess(history.panel, validity, eligible, horizon, log)

    column_of = {ticker: i for i, ticker in enumerate(history.tickers)}
    sample_sessions: list[int] = []
    sample_cols: list[int] = []
    sample_ranks: list[int] = []
    for index in eval_indices:
        names = per_index[index].tickers(history.tickers)
        chosen = query_sample(grid.session(index).isoformat(), names, rules.queries_per_date)
        sample_sessions.extend([index] * len(chosen))
        sample_cols.extend(column_of[t] for t in chosen)
        sample_ranks.extend(range(len(chosen)))
    session_idx = np.asarray(sample_sessions, dtype=np.int64)
    ticker_col = np.asarray(sample_cols, dtype=np.int64)
    sample_rank = np.asarray(sample_ranks, dtype=np.int16)
    queries = int(session_idx.size)
    query_defined = features.defined[session_idx, ticker_col]

    rank_matrix = features.matrix(session_idx, ticker_col)
    b0 = b0_composite.build(rank_matrix, rules)
    b0_strong = b0_composite.strong_only(rank_matrix, rules)
    computed_d1 = {
        "raw_feature_matrix": _named_digest(features.raw),
        "rank_feature_matrix": _named_digest(features.rank),
        "validity_mask": matrix_digest("defined", features.defined),
        "vector_status": matrix_digest("status", features.status),
        "label_validity_primary": matrix_digest("valid", validity.valid[horizon]),
        "query_sample": (matrix_digest("session", session_idx) + ":"
                         + matrix_digest("ticker", ticker_col)),
        "b0_rows": matrix_digest("b0", b0.values),
        "b0_strong_rows": matrix_digest("b0_strong", b0_strong.values),
    }
    d1_checks = verify_parent_matrices(parent_d1, computed_d1, required=REQUIRED_D1_DIGESTS)
    log(f"D1 parent digests verified: {len(d1_checks)}")
    structure_features.release(features)
    del rank_matrix

    # -- neighbours ------------------------------------------------------------------------------
    neighbors = read_neighbors(parent_d2.neighbors_path)
    neighbors_digest = verify_neighbors_digest(neighbors, parent_d2.neighbors_digest)
    assert_neighbor_layout(query_date_idx=neighbors["query_date_idx"],
                           sample_rank=neighbors["sample_rank"], rank=neighbors["rank"],
                           top_k=top_k, queries=queries)
    neighbor_end = neighbors["neighbor_end_idx"]
    neighbor_col = neighbors["neighbor_ticker_col"]
    if not np.array_equal(neighbors["query_date_idx"].reshape(queries, top_k)[:, 0].astype(np.int64),
                          session_idx):
        raise HardFail("R2", "the D2 neighbour rows are not the query sample D1 drew")
    if not np.array_equal(neighbors["query_ticker_col"].reshape(queries, top_k)[:, 0].astype(np.int64),
                          ticker_col):
        raise HardFail("R2", "the D2 neighbour rows carry a different query ticker order")
    worst_overshoot = assert_label_embargo(neighbors["query_date_idx"], neighbor_end,
                                           lookback=lookback, horizon=horizon)

    # -- neighbour labels and A(q) -----------------------------------------------------------------
    neighbor_session = neighbor_end.astype(np.int64)
    neighbor_ticker = neighbor_col.astype(np.int64)
    neighbor_valid_mask = excess.gather_valid(neighbor_session, neighbor_ticker)
    neighbor_valid_per_query = neighbor_valid_mask.reshape(queries, top_k).sum(axis=1)
    if int(neighbor_valid_mask.sum()) != queries * top_k:
        missing = int(queries * top_k - neighbor_valid_mask.sum())
        raise HardFail("R2", f"PARENT_CONTRACT_VIOLATION: {missing} of {queries * top_k} neighbour"
                             " labels are invalid, but every library row was admitted on the"
                             " promise that its label is decidable")
    neighbor_labels = excess.gather(neighbor_session, neighbor_ticker)
    signal = analog_signal.median_by_group(neighbor_labels, top_k)
    analog_signal.assert_is_the_declared_median(signal, neighbor_labels)
    log(f"A(q): {len(signal):,} signals from {neighbor_labels.size:,} neighbour labels")

    # -- query evaluation labels (separate path) ----------------------------------------------------
    query = evaluation_labels.query_labels(excess, session_idx, ticker_col)
    log(f"query labels: {int(query.valid.sum()):,} valid of {queries:,}")

    distances = distance_summary(neighbors["distance"], queries, top_k)
    status = np.where(query_defined, 0, SIGNAL_STATUS_ORDER.index("VECTOR_UNDEFINED")).astype(np.int32)
    status = np.where(neighbor_valid_per_query == top_k, status,
                      SIGNAL_STATUS_ORDER.index("INSUFFICIENT_NEIGHBORS")).astype(np.int32)

    # -- audit -------------------------------------------------------------------------------------
    step = max(1, len(eval_indices) // mutation_dates) if mutation_dates else 1
    audit_indices = tuple(eval_indices[i] for i in range(0, len(eval_indices), step))[:mutation_dates]
    references = []
    for index in audit_indices:
        positions = np.nonzero(session_idx == index)[0]
        references.append(MutationReference(
            index, positions, signal.values[positions], b0.values[positions],
            query.values[positions], query.valid[positions]))
    mutation_report = future_query_mutation_audit(
        history, rules, references=references, neighbor_end=neighbor_end,
        neighbor_col=neighbor_col, session_idx=session_idx, ticker_col=ticker_col, top_k=top_k,
        log=log)

    post_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    post_d1 = {name: _sha256_file(runs_dir / parent_d1.run_id / name)
               for name in parent_d1.files_sha256}
    post_d2 = {name: _sha256_file(runs_dir / parent_d2.run_id / name)
               for name in parent_d2.files_sha256}
    if post_read_digest != pre_read_digest:
        raise HardFail("F1", f"PARENT_MUTATED_DURING_D3: read set {pre_read_digest}"
                             f" -> {post_read_digest}")
    if post_d1 != dict(parent_d1.files_sha256) or post_d2 != dict(parent_d2.files_sha256):
        raise HardFail("F1", "PARENT_MUTATED_DURING_D3: a parent artifact changed during the run")

    # -- artifacts ------------------------------------------------------------------------------------
    signal_columns = {
        "query_date_idx": session_idx.astype(np.int32),
        "sample_rank": sample_rank,
        "query_ticker_col": ticker_col.astype(np.int32),
        "analog_signal_A": signal.values,
        "b0": b0.values,
        "b0_strong": b0_strong.values,
        "neighbor_total": np.full(queries, top_k, dtype=np.int32),
        "neighbor_valid": neighbor_valid_per_query.astype(np.int32),
        "signal_status": status,
        **distances,
    }
    evaluation_columns = {
        "query_date_idx": session_idx.astype(np.int32),
        "sample_rank": sample_rank,
        "query_ticker_col": ticker_col.astype(np.int32),
        "query_label_valid": query.valid,
        "query_excess_return_5": query.values,
    }
    signal_digest = v1_artifacts.column_digest(
        {k: v for k, v in signal_columns.items()}, SIGNAL_DIGEST_COLUMNS)
    evaluation_digest = v1_artifacts.column_digest(
        {k: (v.astype(np.int64) if v.dtype == bool else v) for k, v in evaluation_columns.items()},
        EVALUATION_DIGEST_COLUMNS)
    status_counts = {name: int((status == index).sum())
                     for index, name in enumerate(SIGNAL_STATUS_ORDER)}

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze,
        parent=parent_d2.identity_digest,
        extra={"parent_d1_run_id": parent_d1.run_id,
               "parent_d1_identity_digest": parent_d1.identity_digest,
               "parent_d2_run_id": parent_d2.run_id,
               "feature_schema_digest": feature_schema.digest,
               "neighbors_digest": neighbors_digest,
               "contract_doc_sha256": _sha256_file(CONTRACT_DOC),
               "rules_file_sha256": _sha256_file(RULES_PATH),
               "eval_range": [eval_start, eval_end],
               "primary_horizon": horizon, "top_k": top_k,
               "signal_contract": analog_signal.SIGNAL_CONTRACT,
               "evaluation_contract": evaluation_labels.EVALUATION_CONTRACT,
               "label_value_contract": excess.value_contract,
               "pit_contract": pit.PIT_CONTRACT})

    hard_checks = {
        "rules_checksum_match": rules.checksum == declared_checksum(),
        "d1_parent_digests_match": all(d1_checks.values()),
        "d2_neighbors_digest_match": neighbors_digest == parent_d2.neighbors_digest,
        "freeze_binding_match": bind_to_declaration,
        "feature_schema_match": parent_d2.feature_schema_digest == feature_schema.digest,
        "neighbor_labels_all_valid": int(neighbor_valid_mask.sum()) == queries * top_k,
        "label_embargo_violations_zero": worst_overshoot <= 0,
        "signal_is_declared_median": True,
        "future_query_mutation": mutation_report["signal_unchanged"],
        "read_set_immutable": post_read_digest == pre_read_digest,
        "parents_immutable": post_d1 == dict(parent_d1.files_sha256)
                             and post_d2 == dict(parent_d2.files_sha256),
        "peak_rss_within_limit": _peak_rss_mb() <= PEAK_RSS_LIMIT_MB,
        "alpha_firewall_clean": True,
    }
    verdict = "PASS" if all(hard_checks.values()) else "FAIL"

    report: dict[str, Any] = {
        "phase": PHASE,
        "study_class": str(rules.raw["study_class"]),
        "strategy_id": str(rules.raw["strategy_id"]),
        "verdict": verdict,
        "run_id": identity.run_id,
        "parents": {
            "d1": {"run_id": parent_d1.run_id, "identity_digest": parent_d1.identity_digest,
                   "files_sha256": dict(parent_d1.files_sha256), "digest_checks": d1_checks},
            "d2": {"run_id": parent_d2.run_id, "identity_digest": parent_d2.identity_digest,
                   "files_sha256": dict(parent_d2.files_sha256),
                   "neighbors_digest": parent_d2.neighbors_digest,
                   "query_sample_digest": parent_d2.query_digest}},
        "rules": {"checksum": rules.checksum, "declared_checksum": declared_checksum(),
                  "rules_file_sha256": _sha256_file(RULES_PATH)},
        "feature_schema": feature_schema.as_dict(),
        "freeze_identity": history.freeze.as_dict(),
        "freeze_binding": {"enforced": bind_to_declaration, **binding},
        "read_set": {"pre_digest": pre_read_digest, "post_digest": post_read_digest,
                     "identical": post_read_digest == pre_read_digest},
        "input": {"neighbor_rows": int(neighbor_labels.size), "queries": queries,
                  "top_k": top_k, "primary_horizon": horizon,
                  "eval_range": [eval_start, eval_end], "eval_dates": len(eval_indices)},
        "label_join": {"historical_total": int(neighbor_labels.size),
                       "historical_valid": int(neighbor_valid_mask.sum()),
                       "historical_invalid": int(neighbor_labels.size - neighbor_valid_mask.sum()),
                       "neighbor_valid_per_query_min": int(neighbor_valid_per_query.min()),
                       "neighbor_valid_per_query_max": int(neighbor_valid_per_query.max()),
                       "value_contract": excess.value_contract,
                       "benchmark": "median close_return_5 over the same session's label-valid"
                                    " eligible universe (one matrix serves both paths)"},
        "analog_signal": {"rows": len(signal), "definition": "median of the accepted top-50"
                                                             " neighbour excess_return_5",
                          "contract": analog_signal.SIGNAL_CONTRACT,
                          "group_size": signal.group_size,
                          "finite_rows": int(np.isfinite(signal.values).sum()),
                          "distance_weighting": "NONE (equal weight, declaration §similarity)"},
        "b0": {"rows": len(b0), "strong_rows": len(b0_strong),
               "digest_match_d1": d1_checks.get("b0_rows", False),
               "strong_digest_match_d1": d1_checks.get("b0_strong_rows", False),
               "role": "D-V2A-4 comparator column only; not evaluated here"},
        "evaluation_label": {"rows": queries, **query.counts(),
                             "horizon": horizon,
                             "contract": evaluation_labels.EVALUATION_CONTRACT,
                             "note": "written to its own artifact; joined to the signal only in"
                                     " D-V2A-4"},
        "distance_metadata": {"role": "descriptive only; never an input to A(q)",
                              "columns": ["mean_distance", "median_distance", "min_distance",
                                          "max_distance"]},
        "status_counts": status_counts,
        "pit": {"label_embargo_rule": f"d + {horizon} <= D - {lookback}",
                "worst_overshoot_sessions": worst_overshoot,
                "violations": 0,
                "contract": pit.PIT_CONTRACT},
        "audits": {"future_query_mutation": mutation_report},
        "digests": {"signal_rows": signal_digest, "evaluation_labels": evaluation_digest,
                    "status_counts": _json_digest(status_counts),
                    "neighbors": neighbors_digest, **computed_d1},
        "hard_checks": hard_checks,
        "alpha_firewall": {"ic_a": "NO", "ic_b0": "NO", "delta_ic": "NO", "quintiles": "NO",
                           "bootstrap": "NO", "time_blocks": "NO", "screening_decision": "NO",
                           "signal_value_statistics": "NOT REPORTED BY DESIGN"},
        "performance": {"load_seconds": round(load_seconds, 1),
                        "total_seconds": round(time.perf_counter() - started, 1),
                        "peak_rss_mb": _peak_rss_mb(),
                        "peak_rss_limit_mb": PEAK_RSS_LIMIT_MB},
    }
    tables = {"signal_rows": signal_table(signal_columns, history.tickers, grid.dates),
              "evaluation_labels": evaluation_table(evaluation_columns, history.tickers,
                                                    grid.dates, horizon)}
    return D3Result(identity, report, tables, verdict)


def artifact_identity(result: D3Result) -> dict[str, str]:
    """The identity block every D3 artifact carries (contract request §26)."""
    report, identity = result.report, result.identity
    return {"freeze_id": report["freeze_identity"]["freeze_id"],
            "freeze_digest": report["freeze_identity"]["freeze_digest"],
            "grid_digest": report["freeze_identity"]["grid_digest"],
            "read_digest": report["read_set"]["post_digest"],
            "rules_checksum": report["rules"]["checksum"],
            "parent_d1_run_id": report["parents"]["d1"]["run_id"],
            "parent_d1_identity_digest": report["parents"]["d1"]["identity_digest"],
            "parent_d2_run_id": report["parents"]["d2"]["run_id"],
            "parent_d2_identity_digest": report["parents"]["d2"]["identity_digest"],
            "feature_schema_digest": report["feature_schema"]["feature_schema_digest"],
            "code_digest": identity.payload["code"]["code_digest"]}


def run_context(workspace_root: Path, snapshot_id: str,
                current_pointer: Mapping[str, Any] | None) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id,
            "current_snapshot_pointer": current_pointer}


def write_run(result: D3Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    """Signal and evaluation land in separate files. ``COMPLETE.json`` is written last."""
    run_dir = runs_dir / result.identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    block = artifact_identity(result)
    report = result.report

    files: dict[str, Any] = {}
    files["identity.json"] = v1_artifacts.write_json(
        run_dir / "identity.json", result.identity.as_dict(), block)
    files["parent_d1.json"] = v1_artifacts.write_json(
        run_dir / "parent_d1.json", report["parents"]["d1"], block)
    files["parent_d2.json"] = v1_artifacts.write_json(
        run_dir / "parent_d2.json", report["parents"]["d2"], block)
    files["status_counts.json"] = v1_artifacts.write_json(
        run_dir / "status_counts.json",
        {"status_counts": report["status_counts"], "label_join": report["label_join"],
         "evaluation_label": report["evaluation_label"]}, block)
    files["signal_rows.parquet"] = v1_artifacts.write_table(
        run_dir / "signal_rows.parquet", result.tables["signal_rows"], block)
    files["evaluation_labels.parquet"] = v1_artifacts.write_table(
        run_dir / "evaluation_labels.parquet", result.tables["evaluation_labels"], block)
    (run_dir / "run_context.json").write_text(
        json.dumps(dict(context), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report["artifacts"] = {name: dict(info) for name, info in files.items()}
    files["summary.json"] = v1_artifacts.write_json(run_dir / "summary.json", report, block)
    if result.verdict == "PASS":
        v1_artifacts.write_complete(run_dir, {
            "phase": PHASE, "run_id": result.identity.run_id, "verdict": result.verdict,
            "identity_digest": result.identity.digest,
            "parent_d1_run_id": report["parents"]["d1"]["run_id"],
            "parent_d2_run_id": report["parents"]["d2"]["run_id"],
            "signal_rows_digest": report["digests"]["signal_rows"],
            "evaluation_labels_digest": report["digests"]["evaluation_labels"]}, block)
    result.run_dir = run_dir
    return run_dir
