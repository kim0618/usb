"""D-V2A-2: find each query's Top-K historical structure analogs, exactly and reproducibly.

The phase is a search, not a measurement. It turns the ten-coordinate vectors D1 validated into
neighbour lists, and it proves four things about them: the optimised search returns what an
exhaustive stable sort would return, the answer depends on nothing after the query date, two
runs agree bit for bit, and no forward return was read while any of it happened.

The alpha firewall is structural. A library row exists only if its label for the primary horizon
is *decidable*; what that label is worth is never read, this module never imports the code that
computes one, and the artifact schema carries no return column. D-V2A-3 is where an outcome
first enters the study.

D1 published digests rather than matrices, so this phase recomputes the coordinates and requires
them to hash to exactly what the parent recorded before anything is built on top of them.
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

from app.backtest.strategy_c_selection.panel import Panel, truncate, with_changes
from app.backtest.strategy_d_analog import artifacts as v1_artifacts
from app.backtest.strategy_d_analog import neighbor_search, similarity, universe
from app.backtest.strategy_d_analog.identity import query_sample
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.models import QueryStatus
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import library as structure_library
from app.backtest.strategy_d_v2 import pit, schema, structure_features
from app.backtest.strategy_d_v2.config import (
    CONTRACT_DOC, FEATURE_NAMES, REPO_ROOT, RULES_PATH, V2ARules, declared_checksum, load_rules,
)
from app.backtest.strategy_d_v2.d1 import (
    assert_freeze_binding, eligibility_matrix, matrix_digest, _named_digest,
)
from app.backtest.strategy_d_v2.identity import RunIdentity, run_identity
from app.backtest.strategy_d_v2.models import HardFail, VECTOR_STATUS_ORDER
from app.backtest.strategy_d_v2.structure_encoder import ENCODER_CONTRACT, assert_equal_weights

PHASE = "D2"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d_v2/runs"
#: Every query of these many evenly spaced dates is re-selected by the exhaustive reference.
FULL_SORT_AUDIT_DATES = 12
#: Dates re-run end to end on a physically truncated panel, and on a future-mutated panel.
TRUNCATE_AUDIT_DATES = 3
MUTATION_AUDIT_DATES = 3
#: Queries scored per matrix product. Part of what makes a re-run bit-identical, so it is
#: recorded in the run identity rather than tuned.
#: Queries scored per matrix product. Halved from V1's 50: the block kernel's temporaries are
#: five arrays of ``chunk x library`` doubles, which is the phase's largest transient. The value
#: cannot change any query's answer - each query is selected independently - but it is recorded
#: in the run identity because it is part of what makes a re-run bit-identical.
QUERY_CHUNK = 25
INITIAL_M = neighbor_search.INITIAL_M
#: Declared working-set ceiling for this phase (contract request §26). Not a tuning knob: a run
#: that needs more memory than this reports FAIL rather than quietly growing.
PEAK_RSS_LIMIT_MB = 2048
METRIC = similarity.Metric.EUCLIDEAN
#: Column order of the neighbour digest: the D2 -> D3 handshake.
NEIGHBOR_DIGEST_COLUMNS = ("query_date_idx", "sample_rank", "query_ticker_col", "rank",
                           "library_row", "neighbor_end_idx", "neighbor_ticker_col",
                           "neighbor_figi_code", "distance", "distance_exact", "rank_score",
                           "pool_m")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    if len(values) == 0:
        return {}
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "p25": float(np.percentile(array, 25)),
            "median": float(np.percentile(array, 50)), "p75": float(np.percentile(array, 75)),
            "max": float(array.max()), "mean": float(array.mean())}


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


@dataclass
class D2Result:
    identity: RunIdentity
    report: dict[str, Any]
    tables: dict[str, pa.Table]
    verdict: str
    run_dir: Path | None = None


# -- parent binding -------------------------------------------------------------------------

@dataclass(frozen=True)
class ParentD1:
    run_id: str
    identity_digest: str
    identity: Mapping[str, Any]
    report: Mapping[str, Any]
    digests: Mapping[str, str]
    files_sha256: Mapping[str, str]


def load_parent(runs_dir: Path, run_id: str, rules: V2ARules) -> ParentD1:
    """Bind to one finished D1 run. A directory without a COMPLETE token is not a parent."""
    run_dir = runs_dir / run_id
    complete_path = run_dir / "COMPLETE.json"
    if not complete_path.exists():
        raise HardFail("R2", f"{run_dir} has no COMPLETE.json: D1 did not finish its checks")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    identity = json.loads((run_dir / "run_identity.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "d1_report.json").read_text(encoding="utf-8"))
    if complete.get("verdict") != "PASS":
        raise HardFail("R2", f"parent {run_id} verdict is {complete.get('verdict')!r}")
    if identity.get("phase") != "D1" or complete.get("identity_digest") != identity.get("identity_digest"):
        raise HardFail("R2", f"parent {run_id} identity does not match its own COMPLETE token")
    if identity.get("rules_checksum") != rules.checksum:
        raise HardFail("R1", f"parent rules checksum {identity.get('rules_checksum')}"
                             f" != {rules.checksum}")
    if identity.get("strategy_id") != rules.raw["strategy_id"]:
        raise HardFail("R2", f"parent strategy_id {identity.get('strategy_id')!r}")
    if report.get("verdict") != "PASS":
        raise HardFail("R2", f"parent report verdict is {report.get('verdict')!r}")
    files = {name: _sha256_file(run_dir / name)
             for name in ("COMPLETE.json", "run_identity.json", "d1_report.json")}
    return ParentD1(run_id, str(identity["identity_digest"]), identity, report,
                    dict(report["determinism"]["digests"]), files)


#: What a phase must reproduce from D1 before it builds anything on the coordinates.
REQUIRED_PARENT_DIGESTS = ("raw_feature_matrix", "rank_feature_matrix", "validity_mask",
                           "vector_status", "query_sample", "library_eligibility",
                           "label_validity_primary", "b0_rows")


def verify_parent_matrices(parent: ParentD1, computed: Mapping[str, str],
                           required: Sequence[str] = REQUIRED_PARENT_DIGESTS) -> dict[str, bool]:
    """R1/R2: every matrix D1 hashed must hash the same here before anything is built on it.

    ``required`` is the subset a phase depends on. D-V2A-2 rebuilds the library and so must
    reproduce its digest too; D-V2A-3 does not build one and binds to D2's COMPLETE token for it
    instead, so demanding it there would be asking a phase to recompute something only to check
    a number it already inherits.
    """
    checked: dict[str, bool] = {}
    for name, expected in parent.digests.items():
        if name not in computed:
            continue
        checked[name] = computed[name] == expected
    missing = [name for name in required if name not in checked]
    if missing:
        raise HardFail("R2", f"parent digests missing from the D2 recomputation: {missing}")
    failed = [name for name, ok in checked.items() if not ok]
    if failed:
        raise HardFail("R2", f"D1 parent digest mismatch on {failed}: the coordinate pipeline"
                             " is not the one the parent validated")
    return checked


# -- search ----------------------------------------------------------------------------------

@dataclass
class SearchOutput:
    """Flat neighbour rows plus the per-query counters, preallocated to the declared maximum."""

    columns: dict[str, np.ndarray]
    rows: int
    status: np.ndarray            # (queries,) int8 into STATUS_ORDER
    pool_m: np.ndarray            # (queries,) int32
    accepted: np.ndarray          # (queries,) int16
    candidates_embargo: np.ndarray
    candidates_same_symbol: np.ndarray
    expansions: int
    max_expansion_error: float = 0.0
    rank_inversions: int = 0

    def trimmed(self) -> dict[str, np.ndarray]:
        return {name: values[: self.rows] for name, values in self.columns.items()}


STATUS_ORDER = (QueryStatus.OK, QueryStatus.VECTOR_UNDEFINED, QueryStatus.INSUFFICIENT_NEIGHBORS)


def _allocate(max_rows: int, queries: int) -> SearchOutput:
    columns = {
        "query_date_idx": np.zeros(max_rows, dtype=np.int32),
        "sample_rank": np.zeros(max_rows, dtype=np.int16),
        "query_ticker_col": np.zeros(max_rows, dtype=np.int32),
        "rank": np.zeros(max_rows, dtype=np.int16),
        "library_row": np.zeros(max_rows, dtype=np.int32),
        "neighbor_end_idx": np.zeros(max_rows, dtype=np.int32),
        "neighbor_ticker_col": np.zeros(max_rows, dtype=np.int32),
        "neighbor_figi_code": np.zeros(max_rows, dtype=np.int32),
        "distance": np.zeros(max_rows, dtype=np.float64),
        "distance_exact": np.zeros(max_rows, dtype=np.float64),
        "rank_score": np.zeros(max_rows, dtype=np.float64),
        "pool_m": np.zeros(max_rows, dtype=np.int32),
    }
    return SearchOutput(columns, 0, np.zeros(queries, dtype=np.int8),
                        np.zeros(queries, dtype=np.int32), np.zeros(queries, dtype=np.int16),
                        np.zeros(queries, dtype=np.int32), np.zeros(queries, dtype=np.int32), 0)


def search_date(*, lib: structure_library.StructureLibrary, view: pit.EmbargoView,
                query_vectors: np.ndarray, query_defined: np.ndarray,
                query_ticker_col: np.ndarray, query_figi_code: np.ndarray,
                rules: V2ARules) -> list[neighbor_search.QueryOutcome | None]:
    """One query date: score every defined query against the embargoed prefix and select its K."""
    cut = lib.prefix(view.limit_idx)
    end_idx = lib.end_idx[:cut]
    view.assert_candidates(end_idx, "library prefix")
    ticker_col = lib.ticker_col[:cut]
    figi_code = lib.figi_code[:cut]
    vectors = lib.vectors[:cut]
    sq_norm = lib.sq_norm[:cut]

    outcomes: list[neighbor_search.QueryOutcome | None] = [None] * query_vectors.shape[0]
    live = np.nonzero(query_defined)[0]
    for start in range(0, live.size, QUERY_CHUNK):
        rows = live[start:start + QUERY_CHUNK]
        block = neighbor_search.search_block(
            metric=METRIC, query_vectors=np.ascontiguousarray(query_vectors[rows]),
            library_vectors=vectors, library_sq_norm=sq_norm, ticker_col=ticker_col,
            end_idx=end_idx, figi_code=figi_code,
            query_ticker_col=query_ticker_col[rows], query_figi_code=query_figi_code[rows],
            view_of=lambda _: view, top_k=rules.top_k,
            ticker_cap=rules.max_windows_per_ticker, date_cap=rules.max_neighbors_per_end_date,
            initial_m=INITIAL_M)
        for position, outcome in zip(rows, block):
            neighbor_search.assert_ranks(outcome, rules.top_k)
            outcomes[int(position)] = outcome
    return outcomes


def exact_distance(query_vector: np.ndarray, library_vectors: np.ndarray) -> np.ndarray:
    """``sqrt(sum((q_i - l_i)^2))`` computed directly, without the gram expansion.

    The block kernel scores a whole chunk with ``||q||^2 + ||l||^2 - 2 q.l``, which is what makes
    an exact search over 180,000 windows affordable and which cancels catastrophically when two
    vectors nearly coincide: identical vectors come out at about 3e-08 rather than 0. Recomputing
    the accepted fifty directly costs nothing and turns that into a measured quantity - the run
    reports the largest disagreement and whether it ever reordered a neighbour list.
    """
    difference = library_vectors - query_vector[None, :]
    return np.sqrt(np.einsum("ij,ij->i", difference, difference))


def _record(output: SearchOutput, position: int, date_idx: int, sample_rank: int,
            query_col: int, outcome: neighbor_search.QueryOutcome | None,
            lib: structure_library.StructureLibrary, top_k: int,
            query_vector: np.ndarray | None = None) -> None:
    if outcome is None:
        output.status[position] = STATUS_ORDER.index(QueryStatus.VECTOR_UNDEFINED)
        return
    output.status[position] = STATUS_ORDER.index(
        QueryStatus.OK if outcome.accepted_count == top_k else QueryStatus.INSUFFICIENT_NEIGHBORS)
    output.pool_m[position] = outcome.pool_final_m
    output.accepted[position] = outcome.accepted_count
    output.candidates_embargo[position] = outcome.candidates_after_embargo
    output.candidates_same_symbol[position] = outcome.candidates_after_same_symbol
    count = outcome.rows.size
    if count == 0:
        return
    start, stop = output.rows, output.rows + count
    columns = output.columns
    columns["query_date_idx"][start:stop] = date_idx
    columns["sample_rank"][start:stop] = sample_rank
    columns["query_ticker_col"][start:stop] = query_col
    columns["rank"][start:stop] = np.arange(1, count + 1, dtype=np.int16)
    columns["library_row"][start:stop] = outcome.rows
    columns["neighbor_end_idx"][start:stop] = lib.end_idx[outcome.rows]
    columns["neighbor_ticker_col"][start:stop] = lib.ticker_col[outcome.rows]
    columns["neighbor_figi_code"][start:stop] = lib.figi_code[outcome.rows]
    columns["distance"][start:stop] = outcome.metric_value
    columns["rank_score"][start:stop] = outcome.rank_score
    columns["pool_m"][start:stop] = outcome.pool_final_m
    output.rows = stop
    if query_vector is not None:
        exact = exact_distance(query_vector, lib.vectors[outcome.rows])
        columns["distance_exact"][start:stop] = exact
        error = float(np.abs(exact - outcome.metric_value).max()) if count else 0.0
        output.max_expansion_error = max(output.max_expansion_error, error)
        if count > 1 and bool((np.diff(exact) < 0.0).any()):
            output.rank_inversions += 1


# -- audits ------------------------------------------------------------------------------------

def full_sort_equivalence(*, lib: structure_library.StructureLibrary, view: pit.EmbargoView,
                          query_vectors: np.ndarray, query_ticker_col: np.ndarray,
                          query_figi_code: np.ndarray, expected: Sequence[np.ndarray],
                          rules: V2ARules) -> list[dict[str, Any]]:
    """Re-select one date's queries with the exhaustive reference and compare, row for row."""
    cut = lib.prefix(view.limit_idx)
    end_idx, ticker_col = lib.end_idx[:cut], lib.ticker_col[:cut]
    figi_code, vectors, sq_norm = lib.figi_code[:cut], lib.vectors[:cut], lib.sq_norm[:cut]
    mismatches: list[dict[str, Any]] = []
    for row in range(query_vectors.shape[0]):
        scores = similarity.score_block(METRIC, query_vectors[row: row + 1], vectors, sq_norm)
        drop = neighbor_search.drop_same_symbol(ticker_col, figi_code, int(query_ticker_col[row]),
                                                int(query_figi_code[row]))
        reference = neighbor_search.select_full_sort(
            scores.rank_score[0], drop, ticker_col, end_idx, top_k=rules.top_k,
            ticker_cap=rules.max_windows_per_ticker, date_cap=rules.max_neighbors_per_end_date)
        if not np.array_equal(np.asarray(reference, dtype=np.int64), expected[row]):
            mismatches.append({"query_row": int(row),
                               "optimised": [int(x) for x in expected[row][:5]],
                               "full_sort": [int(x) for x in reference[:5]]})
    return mismatches


def _history_with_panel(history: DailyHistory, panel: Panel, last_idx: int) -> DailyHistory:
    """A history object serving one cut or mutated panel, with the same freeze identity."""
    from app.backtest.strategy_d_v2.models import SessionGrid
    grid = SessionGrid(tuple(history.grid.dates[: last_idx + 1]), history.grid.digest)
    snapshots = tuple(d for d in history.snapshot_dates if d <= grid.dates[-1])
    return DailyHistory(grid, panel, history.freeze, history.figi, snapshots, dict(history.checks))


def rebuild_for_date(history: DailyHistory, rules: V2ARules, date_idx: int, *,
                     panel: Panel) -> tuple[structure_features.StructureFeatures,
                                            structure_library.StructureLibrary, np.ndarray]:
    """Recompute coordinates, label validity and the library this query date would see.

    ``panel`` is either the physically truncated panel or the future-mutated one; everything
    else is the declared pipeline, unchanged.
    """
    local = _history_with_panel(history, panel, date_idx)
    eligible, _ = eligibility_matrix(local, rules, date_idx)
    features = structure_features.build(panel, eligible, rules, diagnostic_frame=False,
                                        retain_raw=False)
    validity = compute_validity(panel, (rules.primary_horizon,), rules.ca_suspect_ratio)
    valid = validity.valid[rules.primary_horizon]
    lib = structure_library.build(local, rules, features, valid, eval_end_idx=date_idx,
                                  horizon=rules.primary_horizon)
    return features, lib, valid


def compare_library_prefix(reference: structure_library.StructureLibrary,
                           rebuilt: structure_library.StructureLibrary,
                           limit_idx: int) -> list[str]:
    """The rebuilt library must be the reference's prefix, identity for identity and bit for bit."""
    cut = reference.prefix(limit_idx)
    issues: list[str] = []
    if len(rebuilt) != cut:
        issues.append(f"rows {len(rebuilt)} != prefix {cut}")
        return issues
    for name, left, right in (("end_idx", reference.end_idx[:cut], rebuilt.end_idx),
                              ("ticker_col", reference.ticker_col[:cut], rebuilt.ticker_col),
                              ("figi_code", reference.figi_code[:cut], rebuilt.figi_code)):
        if not np.array_equal(left, right):
            issues.append(name)
    if not np.array_equal(reference.vectors[:cut], rebuilt.vectors):
        issues.append("vectors")
    return issues


def mutate_future(panel: Panel, date_idx: int) -> Panel:
    """Everything after the query date replaced by values no real market would produce."""
    close, high, low = panel.close.copy(), panel.high.copy(), panel.low.copy()
    open_, volume = panel.open.copy(), panel.volume.copy()
    after = slice(date_idx + 1, None)
    close[after] *= 100.0
    high[after] *= 100.0
    low[after] *= 0.01
    open_[after] *= 100.0
    volume[after] *= 500.0
    return with_changes(panel, close=close, high=high, low=low, open=open_, volume=volume)


def _spaced(indices: Sequence[int], count: int) -> tuple[int, ...]:
    """``count`` evenly spaced values of ``indices``, chosen before any result is seen."""
    if count <= 0:
        return ()
    step = max(1, len(indices) // count)
    return tuple(indices[i] for i in range(0, len(indices), step))[:count]


@dataclass(frozen=True)
class _AuditContext:
    """Everything the equivalence audits compare against: the reference run's own answers."""

    history: DailyHistory
    rules: V2ARules
    lib: structure_library.StructureLibrary
    query_vectors: np.ndarray
    ticker_col: np.ndarray
    session_idx: np.ndarray
    rows_by_date: Mapping[int, np.ndarray]
    accepted_rows: Mapping[int, Sequence[np.ndarray]]
    accepted_distance: Mapping[int, Sequence[np.ndarray]]


def equivalence_audit(context: _AuditContext, indices: Sequence[int], *, kind: str,
                      log: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """Rebuild a query date from a cut or mutated panel and require the same answer.

    ``truncate`` physically removes every session after the query date, every split executed
    after it and every snapshot dated after it. ``future_mutation`` keeps the panel's shape and
    replaces the future with values no market would produce. Either way the coordinates, the
    library prefix, the neighbour identities, the distances and their order must come back
    unchanged; the two differ in what they would catch, so both run.
    """
    rules, lib = context.rules, context.lib
    horizon, lookback = rules.primary_horizon, rules.max_lookback
    findings: list[dict[str, Any]] = []
    checked_queries = 0
    for index in indices:
        session = context.history.grid.session(index)
        panel = (truncate(context.history.panel, session) if kind == "truncate"
                 else mutate_future(context.history.panel, index))
        features, rebuilt, _ = rebuild_for_date(context.history, rules, index, panel=panel)
        view = pit.EmbargoView(index, lookback, horizon)
        issues = compare_library_prefix(lib, rebuilt, view.limit_idx)
        positions = context.rows_by_date[index]
        session_rows = context.session_idx[positions]
        ticker_rows = context.ticker_col[positions]
        vectors = features.matrix(session_rows, ticker_rows)
        if not np.array_equal(vectors, context.query_vectors[positions], equal_nan=True):
            issues.append("query_vectors")
        figi = structure_library.query_figi_codes(context.history, session_rows, ticker_rows,
                                                  rebuilt.figi_strings)
        outcomes = search_date(lib=rebuilt, view=view, query_vectors=vectors,
                               query_defined=features.defined[session_rows, ticker_rows],
                               query_ticker_col=ticker_rows.astype(np.int64),
                               query_figi_code=figi, rules=rules)
        for offset, outcome in enumerate(outcomes):
            checked_queries += 1
            expected_rows = context.accepted_rows[index][offset]
            found_rows = outcome.rows if outcome is not None else np.empty(0, dtype=np.int64)
            if not np.array_equal(found_rows, expected_rows):
                issues.append(f"neighbors@offset{offset}")
                break
            expected_distance = context.accepted_distance[index][offset]
            found_distance = outcome.metric_value if outcome is not None else np.empty(0)
            if not np.array_equal(found_distance, expected_distance):
                issues.append(f"distance@offset{offset}")
                break
        if issues:
            findings.append({"date_idx": int(index), "session": session.isoformat(),
                             "issues": issues})
    log(f"{kind} equivalence: {len(indices)} dates, {checked_queries:,} queries, "
        f"{len(findings)} findings")
    return {"kind": kind, "dates": [int(i) for i in indices], "queries": checked_queries,
            "findings": findings, "equivalent": not findings}


def verify_b0_digests(features: structure_features.StructureFeatures, session_idx: np.ndarray,
                      ticker_col: np.ndarray, defined: np.ndarray,
                      rules: V2ARules) -> dict[str, str]:
    """Recompute the parent's B0 digests and return only digests.

    B0 is the D3/D4 comparator, not a search input. It is recomputed here for one reason - the
    START GATE asks whether the parent's B0 rows still reproduce - and the values are discarded
    inside this function. Nothing in the search path imports this module's caller chain.
    """
    from app.backtest.strategy_d_v2 import b0_composite

    matrix = features.matrix(session_idx, ticker_col)[defined]
    composite = b0_composite.build(matrix, rules)
    strong = b0_composite.strong_only(matrix, rules)
    return {"b0_rows": matrix_digest("b0", composite.values),
            "b0_strong_rows": matrix_digest("b0_strong", strong.values)}


def _dictionary(codes: np.ndarray, values: Sequence[str]) -> pa.Array:
    """A dictionary-encoded column: the code array plus the shared value list."""
    return pa.DictionaryArray.from_arrays(pa.array(codes, type=pa.int32()),
                                          pa.array(list(values), type=pa.string()))


def neighbor_table(columns: Mapping[str, np.ndarray], tickers: Sequence[str],
                   sessions: Sequence[date]) -> pa.Table:
    """The neighbour artifact. Identity and similarity only: no outcome column exists."""
    session_strings = [d.isoformat() for d in sessions]
    fields = {
        "query_date_idx": pa.array(columns["query_date_idx"]),
        "query_date": _dictionary(columns["query_date_idx"], session_strings),
        "query_ticker_col": pa.array(columns["query_ticker_col"]),
        "query_ticker": _dictionary(columns["query_ticker_col"], tickers),
        "sample_rank": pa.array(columns["sample_rank"]),
        "rank": pa.array(columns["rank"]),
        "library_row": pa.array(columns["library_row"]),
        "neighbor_end_idx": pa.array(columns["neighbor_end_idx"]),
        "neighbor_date": _dictionary(columns["neighbor_end_idx"], session_strings),
        "neighbor_ticker_col": pa.array(columns["neighbor_ticker_col"]),
        "neighbor_ticker": _dictionary(columns["neighbor_ticker_col"], tickers),
        "neighbor_figi_code": pa.array(columns["neighbor_figi_code"]),
        "distance": pa.array(columns["distance"]),
        "distance_exact": pa.array(columns["distance_exact"]),
        "rank_score": pa.array(columns["rank_score"]),
        "pool_m": pa.array(columns["pool_m"]),
    }
    return pa.table(fields)


def execute(workspace_root: Path, snapshot_id: str, *, parent_run_id: str,
            c_raw_root: Path | None = None, rules: V2ARules | None = None,
            runs_dir: Path = RUNS_DIR, log: Callable[[str], None] = print,
            full_sort_dates: int = FULL_SORT_AUDIT_DATES,
            truncate_dates: int = TRUNCATE_AUDIT_DATES,
            mutation_dates: int = MUTATION_AUDIT_DATES,
            bind_to_declaration: bool = True) -> D2Result:
    """Run D-V2A-2 end to end. Raises ``HardFail`` on any R/F condition."""
    started = time.perf_counter()
    rules = rules or load_rules()
    feature_schema = schema.build(rules)
    assert_equal_weights(rules)
    log(f"rules {rules.checksum[:12]} schema {feature_schema.digest[:12]}")

    parent = load_parent(runs_dir, parent_run_id, rules)
    log(f"parent D1 {parent.run_id} identity {parent.identity_digest[:12]}")

    pre_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges, c_raw_root=c_raw_root)
    binding = assert_freeze_binding(history, rules) if bind_to_declaration else {"enforced": False}
    for name, value in (("freeze_digest", history.freeze.freeze_digest),
                        ("grid_digest", history.freeze.grid_digest),
                        ("d_read_digest", history.freeze.d_read_digest)):
        if parent.identity["data"][name] != value:
            raise HardFail("R2", f"parent {name} {parent.identity['data'][name]} != loaded {value}")
    grid = history.grid
    load_seconds = time.perf_counter() - started
    log(f"grid N={len(grid)} digest {grid.digest[:12]} ({load_seconds:.1f}s)")

    eval_start, eval_end = rules.eval_range(len(grid))
    eval_indices = tuple(range(eval_start, eval_end + 1))
    horizon, lookback = rules.primary_horizon, rules.max_lookback

    eligible, per_index = eligibility_matrix(history, rules, eval_end, log)
    features = structure_features.build(history.panel, eligible, rules, diagnostic_frame=False)
    validity = compute_validity(history.panel, (horizon,), rules.ca_suspect_ratio)
    label_valid = validity.valid[horizon]
    log(f"coordinates {int(features.defined.sum()):,} defined")

    # -- query sample, rebuilt exactly as D1 drew it ---------------------------------------
    column_of = {ticker: i for i, ticker in enumerate(history.tickers)}
    sample_sessions: list[int] = []
    sample_cols: list[int] = []
    sample_ranks: list[int] = []
    unique_tickers: set[str] = set()
    for index in eval_indices:
        names = per_index[index].tickers(history.tickers)
        chosen = query_sample(grid.session(index).isoformat(), names, rules.queries_per_date)
        unique_tickers.update(chosen)
        sample_sessions.extend([index] * len(chosen))
        sample_cols.extend(column_of[t] for t in chosen)
        sample_ranks.extend(range(len(chosen)))
    session_idx = np.asarray(sample_sessions, dtype=np.int64)
    ticker_col = np.asarray(sample_cols, dtype=np.int64)
    sample_rank = np.asarray(sample_ranks, dtype=np.int16)
    query_defined = features.defined[session_idx, ticker_col]

    lib = structure_library.build(history, rules, features, label_valid,
                                  eval_end_idx=eval_end, horizon=horizon, log=log)

    # -- parent verification (before anything is built on the coordinates) -------------------
    computed_digests = {
        "raw_feature_matrix": _named_digest(features.raw),
        "rank_feature_matrix": _named_digest(features.rank),
        "validity_mask": matrix_digest("defined", features.defined),
        "vector_status": matrix_digest("status", features.status),
        "label_validity_primary": matrix_digest("valid", label_valid),
        "query_sample": (matrix_digest("session", session_idx) + ":"
                         + matrix_digest("ticker", ticker_col)),
        "library_eligibility": (
            matrix_digest("end", np.asarray(lib.stride_end_indices, dtype=np.int64)) + ":"
            + matrix_digest("rows", np.asarray([lib.rows_by_end[e] for e in lib.stride_end_indices],
                                               dtype=np.int64))),
        **verify_b0_digests(features, session_idx, ticker_col, query_defined, rules),
    }
    parent_checks = verify_parent_matrices(parent, computed_digests)
    log(f"parent digests verified: {len(parent_checks)} of {len(parent.digests)}")
    features.raw.clear()  # digested and checked; the search needs only the rank frame

    query_figi = structure_library.query_figi_codes(history, session_idx, ticker_col,
                                                    lib.figi_strings)
    query_vectors = features.matrix(session_idx, ticker_col)

    # -- search ------------------------------------------------------------------------------
    search_started = time.perf_counter()
    output = _allocate(session_idx.size * rules.top_k, session_idx.size)
    rows_by_date = {index: np.nonzero(session_idx == index)[0] for index in eval_indices}
    audit_full_sort = _spaced(eval_indices, full_sort_dates)
    audit_truncate = _spaced(eval_indices, truncate_dates)
    audit_mutation = _spaced(eval_indices, mutation_dates)
    audit_all = tuple(sorted(set(audit_full_sort) | set(audit_truncate) | set(audit_mutation)))
    accepted_rows: dict[int, list[np.ndarray]] = {}
    accepted_distance: dict[int, list[np.ndarray]] = {}
    pool_expansions = 0
    for index in eval_indices:
        positions = rows_by_date[index]
        view = pit.EmbargoView(index, lookback, horizon)
        outcomes = search_date(lib=lib, view=view, query_vectors=query_vectors[positions],
                               query_defined=query_defined[positions],
                               query_ticker_col=ticker_col[positions].astype(np.int64),
                               query_figi_code=query_figi[positions], rules=rules)
        for offset, position in enumerate(positions):
            outcome = outcomes[offset]
            _record(output, int(position), index, int(sample_rank[position]),
                    int(ticker_col[position]), outcome, lib, rules.top_k,
                    query_vector=query_vectors[position])
            if outcome is not None and outcome.pool_final_m > INITIAL_M:
                pool_expansions += 1
        if index in audit_all:
            accepted_rows[index] = [o.rows if o is not None else np.empty(0, dtype=np.int64)
                                    for o in outcomes]
            accepted_distance[index] = [o.metric_value if o is not None else np.empty(0)
                                        for o in outcomes]
    output.expansions = pool_expansions
    search_seconds = time.perf_counter() - search_started
    log(f"search: {output.rows:,} neighbour rows in {search_seconds:.1f}s")

    # -- audits --------------------------------------------------------------------------------
    # The audits rebuild the coordinates on their own panels and never read the reference
    # feature object again; releasing it here is what keeps the peak working set inside the
    # declared ceiling.
    structure_features.release(features)
    del validity, label_valid

    full_sort_mismatches: list[dict[str, Any]] = []
    full_sort_queries = 0
    for index in audit_full_sort:
        positions = rows_by_date[index]
        full_sort_queries += int(positions.size)
        found = full_sort_equivalence(
            lib=lib, view=pit.EmbargoView(index, lookback, horizon),
            query_vectors=query_vectors[positions], query_ticker_col=ticker_col[positions],
            query_figi_code=query_figi[positions], expected=accepted_rows[index], rules=rules)
        for item in found:
            item["date_idx"] = int(index)
        full_sort_mismatches.extend(found)
    log(f"full-sort equivalence: {full_sort_queries:,} queries, "
        f"{len(full_sort_mismatches)} mismatches")

    audit_context = _AuditContext(history=history, rules=rules, lib=lib,
                                  query_vectors=query_vectors, ticker_col=ticker_col,
                                  session_idx=session_idx, rows_by_date=rows_by_date,
                                  accepted_rows=accepted_rows,
                                  accepted_distance=accepted_distance)
    truncate_report = equivalence_audit(audit_context, audit_truncate, kind="truncate", log=log)
    mutation_report = equivalence_audit(audit_context, audit_mutation, kind="future_mutation",
                                        log=log)

    post_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    post_parent_files = {name: _sha256_file(runs_dir / parent.run_id / name)
                         for name in parent.files_sha256}
    if post_read_digest != pre_read_digest:
        raise HardFail("F1", f"INPUT_MUTATED_DURING_V2A2: read set {pre_read_digest}"
                             f" -> {post_read_digest}")
    if post_parent_files != dict(parent.files_sha256):
        raise HardFail("F1", "INPUT_MUTATED_DURING_V2A2: the D1 parent artifacts changed")

    # -- artifacts ------------------------------------------------------------------------------
    columns = output.trimmed()
    table = neighbor_table(columns, history.tickers, grid.dates)
    neighbors_digest = v1_artifacts.column_digest(columns, NEIGHBOR_DIGEST_COLUMNS)
    status_counts = {status.value: int((output.status == index).sum())
                     for index, status in enumerate(STATUS_ORDER)}
    insufficient_share = status_counts[QueryStatus.INSUFFICIENT_NEIGHBORS.value] / session_idx.size

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze,
        parent=parent.identity_digest,
        extra={"parent_run_id": parent.run_id,
               "feature_schema_digest": feature_schema.digest,
               "contract_doc_sha256": _sha256_file(CONTRACT_DOC),
               "rules_file_sha256": _sha256_file(RULES_PATH),
               "eval_range": [eval_start, eval_end],
               "coordinate_lookback": lookback, "primary_horizon": horizon,
               "top_k": rules.top_k, "initial_m": INITIAL_M, "query_chunk": QUERY_CHUNK,
               "metric": rules.metric, "library_contract": structure_library.LIBRARY_CONTRACT,
               "encoder_contract": ENCODER_CONTRACT, "pit_contract": pit.PIT_CONTRACT})

    hard_checks = {
        "rules_checksum_match": rules.checksum == declared_checksum(),
        "parent_digests_match": all(parent_checks.values()),
        "freeze_binding_match": bind_to_declaration,
        "feature_schema_match": feature_schema.digest == schema.build(rules).digest,
        "full_sort_equivalence": not full_sort_mismatches,
        "truncate_equivalence": truncate_report["equivalent"],
        "future_mutation_equivalence": mutation_report["equivalent"],
        "read_set_immutable": post_read_digest == pre_read_digest,
        "parent_artifacts_immutable": post_parent_files == dict(parent.files_sha256),
        "insufficient_within_limit": insufficient_share <= rules.sample_gate.max_insufficient_neighbor_share,
        "pit_violations_zero": True,
        "no_neighbor_list_reordered": output.rank_inversions == 0,
        "peak_rss_within_limit": _peak_rss_mb() <= PEAK_RSS_LIMIT_MB,
        "alpha_firewall_clean": True,
    }
    verdict = "PASS" if all(hard_checks.values()) else "FAIL"

    distances = columns["distance"]
    report: dict[str, Any] = {
        "phase": PHASE,
        "study_class": str(rules.raw["study_class"]),
        "strategy_id": str(rules.raw["strategy_id"]),
        "verdict": verdict,
        "run_id": identity.run_id,
        "parent": {"run_id": parent.run_id, "identity_digest": parent.identity_digest,
                   "files_sha256": dict(parent.files_sha256),
                   "digest_checks": parent_checks},
        "rules": {"checksum": rules.checksum, "declared_checksum": declared_checksum(),
                  "rules_file_sha256": _sha256_file(RULES_PATH)},
        "feature_schema": feature_schema.as_dict(),
        "freeze_identity": history.freeze.as_dict(),
        "freeze_binding": {"enforced": bind_to_declaration, **binding},
        "read_set": {"pre_digest": pre_read_digest, "post_digest": post_read_digest,
                     "identical": post_read_digest == pre_read_digest},
        "library": lib.manifest(),
        "query_sample": {"dates": len(eval_indices), "rows": int(session_idx.size),
                         "unique_tickers": len(unique_tickers),
                         "vectors_defined": int(query_defined.sum()),
                         "digest": computed_digests["query_sample"]},
        "search": {"metric": rules.metric, "dimension": len(FEATURE_NAMES),
                   "top_k": rules.top_k, "initial_m": INITIAL_M, "query_chunk": QUERY_CHUNK,
                   "pool_expansions": output.expansions,
                   "pool_m_distribution": {str(int(v)): int(c) for v, c in
                                           zip(*np.unique(output.pool_m, return_counts=True))},
                   "neighbor_rows": int(output.rows),
                   "accepted_per_query": _percentiles(output.accepted),
                   "candidates_after_embargo": _percentiles(output.candidates_embargo),
                   "candidates_after_same_symbol": _percentiles(output.candidates_same_symbol),
                   "distance": _percentiles(distances) if distances.size else {},
                   "exactness": {
                       "kernel": "gram expansion ||q||^2 + ||l||^2 - 2 q.l (V1 similarity block)",
                       "max_abs_error_vs_direct": output.max_expansion_error,
                       "neighbor_lists_reordered_vs_direct": output.rank_inversions,
                       "note": "distance is the value the engine ranked on; distance_exact is the"
                               " same pair recomputed directly, and the two columns bound the"
                               " kernel's error on every accepted neighbour"},
                   "rank_score_is_negative_distance": bool(
                       np.array_equal(columns["rank_score"], -distances))},
        "status_counts": status_counts,
        "insufficient_share": round(insufficient_share, 8),
        "audits": {"full_sort": {"dates": [int(i) for i in audit_full_sort],
                                 "queries": full_sort_queries,
                                 "mismatches": full_sort_mismatches},
                   "truncate": truncate_report,
                   "future_mutation": mutation_report},
        "digests": {"neighbors": neighbors_digest,
                    "library_manifest": _json_digest(lib.manifest()),
                    "status_counts": _json_digest(status_counts),
                    **computed_digests},
        "hard_checks": hard_checks,
        "alpha_firewall": {"future_labels_read": "NO", "ic_calculated": "NO", "quintiles": "NO",
                           "b0_evaluation": "NO", "bootstrap": "NO", "screening_decision": "NO"},
        "performance": {"load_seconds": round(load_seconds, 1),
                        "search_seconds": round(search_seconds, 1),
                        "total_seconds": round(time.perf_counter() - started, 1),
                        "peak_rss_mb": _peak_rss_mb(),
                        "peak_rss_limit_mb": PEAK_RSS_LIMIT_MB},
    }
    pit.assert_no_label_values(_leaf_keys(report))
    tables = {"neighbors": table}
    return D2Result(identity, report, tables, verdict)


def _json_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _leaf_keys(payload: Any) -> list[str]:
    out: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            out.append(str(key))
            out.extend(_leaf_keys(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            out.extend(_leaf_keys(value))
    return out


def artifact_identity(result: D2Result) -> dict[str, str]:
    """The seven values every D2 artifact carries, whatever its format (contract §21).

    The first four are V1's artifact identity block, so ``artifacts.write_table`` accepts it
    unchanged; the last three are what V2-A adds - which parent produced the coordinates, what a
    stored vector means, and which code wrote it.
    """
    report, identity = result.report, result.identity
    return {"freeze_id": report["freeze_identity"]["freeze_id"],
            "freeze_digest": report["freeze_identity"]["freeze_digest"],
            "grid_digest": report["freeze_identity"]["grid_digest"],
            "read_digest": report["read_set"]["post_digest"],
            "rules_checksum": report["rules"]["checksum"],
            "parent_run_id": report["parent"]["run_id"],
            "parent_identity_digest": report["parent"]["identity_digest"],
            "feature_schema_digest": report["feature_schema"]["feature_schema_digest"],
            "code_digest": identity.payload["code"]["code_digest"]}


def run_context(workspace_root: Path, snapshot_id: str,
                current_pointer: Mapping[str, Any] | None) -> dict[str, Any]:
    """Non-deterministic facts, kept out of the identity digest on purpose."""
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id,
            "current_snapshot_pointer": current_pointer}


def write_run(result: D2Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    """Write the artifact set. ``COMPLETE.json`` is written last and only on PASS."""
    run_dir = runs_dir / result.identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    identity_block = artifact_identity(result)
    report = result.report

    files: dict[str, Any] = {}
    files["identity.json"] = v1_artifacts.write_json(
        run_dir / "identity.json", result.identity.as_dict(), identity_block)
    files["parent_d1.json"] = v1_artifacts.write_json(
        run_dir / "parent_d1.json", report["parent"], identity_block)
    files["feature_schema.json"] = v1_artifacts.write_json(
        run_dir / "feature_schema.json", report["feature_schema"], identity_block)
    files["library_manifest.json"] = v1_artifacts.write_json(
        run_dir / "library_manifest.json", report["library"], identity_block)
    files["query_manifest.json"] = v1_artifacts.write_json(
        run_dir / "query_manifest.json", report["query_sample"], identity_block)
    files["status_counts.json"] = v1_artifacts.write_json(
        run_dir / "status_counts.json",
        {"status_counts": report["status_counts"],
         "insufficient_share": report["insufficient_share"],
         "search": report["search"]}, identity_block)
    files["neighbors.parquet"] = v1_artifacts.write_table(
        run_dir / "neighbors.parquet", result.tables["neighbors"], identity_block)
    (run_dir / "run_context.json").write_text(
        json.dumps(dict(context), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ``summary.json`` carries the manifest of the others and is therefore written last and
    # exactly once. It cannot list itself: a file's own hash is not a value it can contain, and
    # an earlier draft that tried recorded a digest that changed with the run's clock.
    report["artifacts"] = {name: dict(info) for name, info in files.items()}
    files["summary.json"] = v1_artifacts.write_json(
        run_dir / "summary.json", report, identity_block)
    if result.verdict == "PASS":
        v1_artifacts.write_complete(run_dir, {
            "phase": PHASE, "run_id": result.identity.run_id, "verdict": result.verdict,
            "identity_digest": result.identity.digest,
            "parent_run_id": report["parent"]["run_id"],
            "neighbors_digest": report["digests"]["neighbors"]}, identity_block)
    result.run_dir = run_dir
    return run_dir
