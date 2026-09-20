"""D2: build the pattern library and find each query's Top-K historical analogues.

This is the first phase of Strategy D that computes something a researcher could be tempted to
read a result out of, and it is built so that there is nothing to read. D2 ends at the *identity*
of the 50 most similar past windows. It never asks what happened next, so no forward return, win
rate, MFE, MAE, IC or baseline exists anywhere in this module, and none can be derived from what
it writes. The one thing D2 takes from the future of a library window is whether that window's
label was decidable at all - a boolean - and even that is gated by ``pit.EmbargoView``.

The shape of the loop follows the memory budget and the determinism argument in equal measure.
One window length is resident at a time; within it, one test materialises its compacted matrix
once and every query date slices a prefix of it. Because the prefix, the query chunking and the
matrix shapes depend only on ``(W, h, D)`` and never on the data's content, a re-run on a
physically truncated dataset feeds ``gemm`` the identical bytes and gets the identical answer.
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

from app.backtest.strategy_d_analog import (
    artifacts, encoder, library, neighbor_search, pit, similarity, universe,
)
from app.backtest.strategy_d_analog.config import (
    DECLARED_RULES_CHECKSUM, REPO_ROOT, RULES_PATH, AnalogRules, load_rules,
)
from app.backtest.strategy_d_analog.identity import (
    SERIALIZATION, RunIdentity, run_identity,
)
from app.backtest.strategy_d_analog.label_extension import LabelValidity, compute_validity
from app.backtest.strategy_d_analog.models import (
    REPRESENTATIONS, FreezeIdentity, HardFail, QueryStatus, TestId,
)
from app.backtest.strategy_d_analog.sampling import QuerySample, sample_date
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history, read_set_digest

PHASE = "D2"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d/runs"
#: Query dates re-run on a physically truncated dataset (D0 PIT mutation #1, D2 §22.1 P5).
AUDIT_DATE_COUNT = 12
STATUS_ORDER = (QueryStatus.OK.value, QueryStatus.VECTOR_UNDEFINED.value,
                QueryStatus.INSUFFICIENT_NEIGHBORS.value)
#: The label contract D2 depends on: D0 validity plus the 20-day CA extension of
#: ``label_extension.py``. Label *values* are a separate contract and belong to D3
#: (D1 Pre-flight §10.3).
LABEL_VALIDITY_CONTRACT = "d-label-validity-v1"


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _dictionary(indices: np.ndarray, values: Sequence, value_type) -> pa.Array:
    """A dictionary column from integer codes, so millions of repeated strings cost their code."""
    return pa.DictionaryArray.from_arrays(pa.array(indices, pa.int32()),
                                          pa.array(list(values), value_type))


@dataclass
class QueryVectors:
    """One date's sampled queries, encoded once per window length and shared by its tests."""

    sample: QuerySample
    figi_code: np.ndarray
    eligible_count: int
    encoded: dict[str, encoder.Encoded]


@dataclass
class TestOutput:
    """One test's rows, collected per date as arrays and concatenated once at the end."""

    neighbor: list[dict[str, np.ndarray]] = field(default_factory=list)
    query: list[dict[str, np.ndarray]] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    pool_m_counts: dict[int, int] = field(default_factory=dict)
    insufficient_accepted: list[int] = field(default_factory=list)

    def stack(self, rows: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
        if not rows:
            return {}
        return {name: np.concatenate([part[name] for part in rows]) for name in rows[0]}


@dataclass
class RunContextData:
    """What the truncation audit needs after the main pass, kept out of the artifact payloads."""

    history: DailyHistory
    rules: AnalogRules
    validity: LabelValidity
    eval_indices: tuple[int, ...]
    audit_dates: tuple[int, ...]
    capture: dict
    elapsed: float


@dataclass
class D2Result:
    identity: RunIdentity
    summary: dict[str, Any]
    library_manifest: dict[str, Any]
    query_manifest: dict[str, Any]
    pit_runtime: dict[str, Any]
    digests: dict[str, str]
    verdict: str
    run_dir: Path
    context: RunContextData


def freeze_from_d1(run_dir: Path) -> tuple[FreezeIdentity, str]:
    """Read the dataset a finished D1 run bound itself to; D2 accepts that dataset and no other."""
    identity_path = run_dir / "run_identity.json"
    if not (run_dir / "COMPLETE.json").exists() or not identity_path.exists():
        raise HardFail("R2", f"{run_dir} is not a finished D1 run")
    payload = json.loads(identity_path.read_text(encoding="utf-8"))
    if payload.get("phase") != "D1":
        raise HardFail("R2", f"{run_dir} is a {payload.get('phase')} run, not D1")
    if payload.get("rules_checksum") != DECLARED_RULES_CHECKSUM:
        raise HardFail("R1", f"{run_dir} was run under rules {payload.get('rules_checksum')}")
    return FreezeIdentity(**payload["data"]), str(payload["run_id"])


class Eligibility:
    """Universe masks, computed once per session index and shared by the library and the queries."""

    def __init__(self, history: DailyHistory, rules: AnalogRules, log: pit.InvariantLog) -> None:
        self._history, self._rules, self._log = history, rules, log
        self._cache: dict[int, universe.Eligibility] = {}

    def __call__(self, index: int) -> universe.Eligibility:
        if index not in self._cache:
            self._log.record("R4")
            self._cache[index] = universe.evaluate(
                self._history.panel_view(index), self._history.membership(index), self._rules)
        return self._cache[index]


def encode_queries(history: DailyHistory, eligibility: Eligibility, rules: AnalogRules,
                   window: int, eval_indices: Sequence[int], figi: library.FigiCoder,
                   log: pit.InvariantLog) -> dict[int, QueryVectors]:
    """One window length's query vectors for every evaluation date, sampled before any label."""
    column_of = {ticker: i for i, ticker in enumerate(history.tickers)}
    out: dict[int, QueryVectors] = {}
    for index in eval_indices:
        eligible = eligibility(index)
        sample = sample_date(index, history.grid.session(index),
                             eligible.tickers(history.tickers), column_of, rules.queries_per_date)
        view = history.panel_view(index)
        log.record("R4")
        prices = encoder.gather(view.price, np.full(len(sample), index), sample.ticker_col,
                                window, as_of_idx=index)
        as_of = history.snapshot_as_of(index)
        table = history.figi.get(as_of, {}) if as_of is not None else {}
        out[index] = QueryVectors(
            sample=sample,
            figi_code=np.array([figi.code(table.get(t)) for t in sample.tickers], dtype=np.int32),
            eligible_count=eligible.count,
            encoded={rep: encoder.encode(rep, prices) for rep in REPRESENTATIONS})
    return out


def run_test(*, test: TestId, lib: library.PatternLibrary, queries: Mapping[int, QueryVectors],
             eval_indices: Sequence[int], rules: AnalogRules, log: pit.InvariantLog,
             audit_dates: Sequence[int], capture: dict | None = None) -> TestOutput:
    """Search every query date of one ``(W, h, representation)`` test against one library prefix."""
    representation, horizon, window = test.representation, test.horizon, test.window
    compact = lib.compact_rows(representation, horizon)
    vectors = np.ascontiguousarray(lib.vectors[representation][compact])
    sq_norm = np.ascontiguousarray(lib.sq_norm[representation][compact])
    end_idx = np.ascontiguousarray(lib.end_idx[compact])
    ticker_col = np.ascontiguousarray(lib.ticker_col[compact])
    figi_code = np.ascontiguousarray(lib.figi_code[compact])
    metric = similarity.METRIC_OF[representation]
    audit = set(audit_dates)
    out = TestOutput()

    for index in eval_indices:
        entry = queries[index]
        sample, encoded = entry.sample, entry.encoded[representation]
        view = pit.EmbargoView(index, window, horizon)
        cut = neighbor_search.embargo_cut(end_idx, view)
        log.record("R6")

        q_date, q_rank, q_ticker, q_figi = [], [], [], []
        q_status, q_accepted, q_embargo, q_after_symbol, q_pool, q_limit = [], [], [], [], [], []
        n_rank, n_row, n_end, n_ticker, n_figi, n_metric, n_score = [], [], [], [], [], [], []
        n_date, n_sample = [], []

        def record_query(position: int, status: str, accepted: int, after_symbol: int,
                         pool_m: int) -> None:
            q_date.append(index)
            q_rank.append(position)
            q_ticker.append(int(sample.ticker_col[position]))
            q_figi.append(int(entry.figi_code[position]))
            q_status.append(STATUS_ORDER.index(status))
            q_accepted.append(accepted)
            q_embargo.append(cut)
            q_after_symbol.append(after_symbol)
            q_pool.append(pool_m)
            q_limit.append(view.limit_idx)

        for position in np.nonzero(~encoded.defined)[0]:
            record_query(int(position), QueryStatus.VECTOR_UNDEFINED.value, 0, 0, 0)
            log.record("R15")

        rows = np.nonzero(encoded.defined)[0]
        for start in range(0, rows.size, neighbor_search.QUERY_CHUNK):
            block = rows[start:start + neighbor_search.QUERY_CHUNK]
            log.record("R12")
            outcomes = neighbor_search.search_block(
                metric=metric, query_vectors=encoded.vectors[block],
                library_vectors=vectors[:cut], library_sq_norm=sq_norm[:cut],
                ticker_col=ticker_col[:cut], end_idx=end_idx[:cut], figi_code=figi_code[:cut],
                query_ticker_col=sample.ticker_col[block], query_figi_code=entry.figi_code[block],
                view_of=lambda _: view, top_k=rules.top_k,
                ticker_cap=rules.max_windows_per_ticker,
                date_cap=rules.max_neighbors_per_end_date)
            log.record("R7", len(outcomes))
            log.record("R9", len(outcomes))
            log.record("R10", len(outcomes))
            for offset, outcome in enumerate(outcomes):
                position = int(block[offset])
                neighbor_search.assert_ranks(outcome, rules.top_k)
                record_query(position, outcome.status.value, outcome.accepted_count,
                             outcome.candidates_after_same_symbol, outcome.pool_final_m)
                out.pool_m_counts[outcome.pool_final_m] = \
                    out.pool_m_counts.get(outcome.pool_final_m, 0) + 1
                if outcome.status is QueryStatus.INSUFFICIENT_NEIGHBORS:
                    log.record("R16")
                    out.insufficient_accepted.append(outcome.accepted_count)
                if outcome.accepted_count == 0:
                    continue
                chosen = outcome.rows
                ends = end_idx[chosen]
                n_date.append(np.full(chosen.size, index, dtype=np.int32))
                n_sample.append(np.full(chosen.size, position, dtype=np.int16))
                n_rank.append(np.arange(1, chosen.size + 1, dtype=np.int8))
                n_row.append(compact[chosen].astype(np.int32))
                n_end.append(ends.astype(np.int32))
                n_ticker.append(ticker_col[chosen].astype(np.int32))
                n_figi.append(figi_code[chosen].astype(np.int32))
                n_metric.append(outcome.metric_value.astype(np.float64))
                n_score.append(outcome.rank_score.astype(np.float64))
                if capture is not None and index in audit:
                    capture[(test.name, index, position)] = (
                        ends.astype(np.int32).copy(), ticker_col[chosen].astype(np.int32).copy(),
                        outcome.metric_value.copy())

        out.query.append({
            "query_date_idx": np.array(q_date, dtype=np.int32),
            "sample_rank": np.array(q_rank, dtype=np.int16),
            "query_ticker_col": np.array(q_ticker, dtype=np.int32),
            "query_figi_code": np.array(q_figi, dtype=np.int32),
            "status_code": np.array(q_status, dtype=np.int8),
            "accepted_count": np.array(q_accepted, dtype=np.int16),
            "candidates_after_embargo": np.array(q_embargo, dtype=np.int32),
            "candidates_after_same_symbol": np.array(q_after_symbol, dtype=np.int32),
            "pool_final_m": np.array(q_pool, dtype=np.int32),
            "embargo_limit_idx": np.array(q_limit, dtype=np.int32)})
        if n_rank:
            out.neighbor.append({
                "query_date_idx": np.concatenate(n_date), "sample_rank": np.concatenate(n_sample),
                "rank": np.concatenate(n_rank), "library_row": np.concatenate(n_row),
                "neighbor_end_idx": np.concatenate(n_end),
                "neighbor_ticker_col": np.concatenate(n_ticker),
                "neighbor_figi_code": np.concatenate(n_figi),
                "metric_value": np.concatenate(n_metric), "rank_score": np.concatenate(n_score)})
    for status in q_status_totals(out):
        out.status_counts[status[0]] = status[1]
    return out


def q_status_totals(out: TestOutput) -> list[tuple[str, int]]:
    if not out.query:
        return []
    codes = np.concatenate([part["status_code"] for part in out.query])
    return [(name, int((codes == i).sum())) for i, name in enumerate(STATUS_ORDER)]


def neighbor_table(history: DailyHistory, columns: Mapping[str, np.ndarray],
                   figi: library.FigiCoder, test: TestId,
                   horizon: int) -> tuple[pa.Table, dict[str, np.ndarray]]:
    """The artifact D3 joins its labels onto. Identity and similarity only, never an outcome."""
    size = columns["rank"].shape[0]
    label_end = columns["neighbor_end_idx"].astype(np.int32) + horizon
    table = pa.table({
        "test_id": pa.array([test.name] * size, pa.dictionary(pa.int32(), pa.string())),
        "query_date_idx": pa.array(columns["query_date_idx"], pa.int32()),
        "sample_rank": pa.array(columns["sample_rank"], pa.int16()),
        "rank": pa.array(columns["rank"], pa.int8()),
        "library_row": pa.array(columns["library_row"], pa.int32()),
        "neighbor_end_idx": pa.array(columns["neighbor_end_idx"], pa.int32()),
        "neighbor_end_date": _dictionary(columns["neighbor_end_idx"], history.grid.dates,
                                         pa.date32()),
        "neighbor_ticker": _dictionary(columns["neighbor_ticker_col"], history.tickers, pa.string()),
        "neighbor_ticker_col": pa.array(columns["neighbor_ticker_col"], pa.int32()),
        "neighbor_figi": _dictionary(columns["neighbor_figi_code"] + 1, ("",) + figi.strings(),
                                     pa.string()),
        "neighbor_figi_code": pa.array(columns["neighbor_figi_code"], pa.int32()),
        "metric_value": pa.array(columns["metric_value"], pa.float64()),
        "rank_score": pa.array(columns["rank_score"], pa.float64()),
        "label_end_idx": pa.array(label_end, pa.int32())})
    digest_columns = {name: columns[name].astype(np.int64)
                      for name in ("query_date_idx", "sample_rank", "rank", "library_row",
                                   "neighbor_end_idx", "neighbor_ticker_col", "neighbor_figi_code")}
    digest_columns["metric_value"] = columns["metric_value"]
    digest_columns["rank_score"] = columns["rank_score"]
    digest_columns["label_end_idx"] = label_end.astype(np.int64)
    return table, digest_columns


def query_result_table(columns: Mapping[str, np.ndarray],
                       test_names: np.ndarray, names: Sequence[str]) -> pa.Table:
    return pa.table({
        "test_id": _dictionary(test_names, names, pa.string()),
        "query_date_idx": pa.array(columns["query_date_idx"], pa.int32()),
        "sample_rank": pa.array(columns["sample_rank"], pa.int16()),
        "query_ticker_col": pa.array(columns["query_ticker_col"], pa.int32()),
        "query_figi_code": pa.array(columns["query_figi_code"], pa.int32()),
        "status": _dictionary(columns["status_code"].astype(np.int32), STATUS_ORDER, pa.string()),
        "accepted_count": pa.array(columns["accepted_count"], pa.int16()),
        "candidates_after_embargo": pa.array(columns["candidates_after_embargo"], pa.int32()),
        "candidates_after_same_symbol": pa.array(columns["candidates_after_same_symbol"], pa.int32()),
        "pool_final_m": pa.array(columns["pool_final_m"], pa.int32()),
        "embargo_limit_idx": pa.array(columns["embargo_limit_idx"], pa.int32())})


def library_table(history: DailyHistory, lib: library.PatternLibrary,
                  figi: library.FigiCoder) -> tuple[pa.Table, dict[str, np.ndarray]]:
    columns: dict[str, Any] = {
        "library_row": pa.array(np.arange(len(lib), dtype=np.int32), pa.int32()),
        "end_idx": pa.array(lib.end_idx, pa.int32()),
        "end_date": _dictionary(lib.end_idx, history.grid.dates, pa.date32()),
        "ticker": _dictionary(lib.ticker_col, history.tickers, pa.string()),
        "ticker_col": pa.array(lib.ticker_col, pa.int32()),
        "figi": _dictionary(lib.figi_code + 1, ("",) + figi.strings(), pa.string()),
        "figi_code": pa.array(lib.figi_code, pa.int32())}
    digest: dict[str, np.ndarray] = {"end_idx": lib.end_idx.astype(np.int64),
                                     "ticker_col": lib.ticker_col.astype(np.int64),
                                     "figi_code": lib.figi_code.astype(np.int64)}
    for representation in REPRESENTATIONS:
        columns[f"defined_{representation}"] = pa.array(lib.defined[representation], pa.bool_())
        digest[f"defined_{representation}"] = lib.defined[representation].astype(np.int64)
    for horizon in lib.horizons:
        columns[f"valid_h{horizon}"] = pa.array(lib.valid[horizon], pa.bool_())
        digest[f"valid_h{horizon}"] = lib.valid[horizon].astype(np.int64)
    return pa.table(columns), digest


def query_sample_table(history: DailyHistory, queries: Mapping[int, QueryVectors],
                       eval_indices: Sequence[int], figi: library.FigiCoder) -> pa.Table:
    date_idx, rank, ticker_col, figi_code, eligible = [], [], [], [], []
    for index in eval_indices:
        entry = queries[index]
        count = len(entry.sample)
        date_idx.append(np.full(count, index, dtype=np.int32))
        rank.append(np.arange(count, dtype=np.int16))
        ticker_col.append(entry.sample.ticker_col.astype(np.int32))
        figi_code.append(entry.figi_code.astype(np.int32))
        eligible.append(np.full(count, entry.eligible_count, dtype=np.int32))
    stacked = [np.concatenate(part) for part in (date_idx, rank, ticker_col, figi_code, eligible)]
    return pa.table({"query_date_idx": pa.array(stacked[0], pa.int32()),
                     "query_date": _dictionary(stacked[0], history.grid.dates, pa.date32()),
                     "sample_rank": pa.array(stacked[1], pa.int16()),
                     "query_ticker": _dictionary(stacked[2], history.tickers, pa.string()),
                     "query_ticker_col": pa.array(stacked[2], pa.int32()),
                     "query_figi": _dictionary(stacked[3] + 1, ("",) + figi.strings(), pa.string()),
                     "query_figi_code": pa.array(stacked[3], pa.int32()),
                     "eligible_count": pa.array(stacked[4], pa.int32())})


def execute(workspace_root: Path, snapshot_id: str, *, expected: FreezeIdentity, parent_run: str,
            rules: AnalogRules | None = None, c_raw_root: Path | None = None,
            runs_dir: Path | None = None, log: Callable[[str], None] = print,
            eval_date_limit: int | None = None, audit_count: int = AUDIT_DATE_COUNT,
            capture_audit: bool = True) -> D2Result:
    """Run D2 end to end. Any broken contract raises ``HardFail`` before ``COMPLETE`` is written."""
    started = time.perf_counter()
    rules = rules or load_rules()
    invariants = pit.InvariantLog()
    invariants.record("R1")
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges, expected=expected,
                                 c_raw_root=c_raw_root)
    invariants.record("R2")
    invariants.record("R3")
    pre_digest = history.freeze.d_read_digest
    grid = history.grid
    eval_start, eval_end = rules.eval_range(len(grid))
    eval_indices = tuple(range(eval_start, eval_end + 1))
    if eval_date_limit is not None:
        eval_indices = eval_indices[:eval_date_limit]
    step = max(1, len(eval_indices) // max(audit_count, 1))
    audit_dates = tuple(eval_indices[::step][:audit_count])

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze, parent=parent_run,
        extra={"rules_file_sha256": artifacts.sha256_file(RULES_PATH),
               "eval_range": [int(eval_indices[0]), int(eval_indices[-1])],
               "eval_dates": len(eval_indices), "queries_per_date": rules.queries_per_date,
               "library_policy": {"stride": rules.library_stride, "anchor": 0,
                                  "min_end_idx": rules.seasoning_sessions,
                                  "ticker_cap": rules.max_windows_per_ticker,
                                  "date_cap": rules.max_neighbors_per_end_date,
                                  "same_symbol": "ticker+composite_figi",
                                  "embargo": "d + h <= D - W"},
               "top_k": rules.top_k,
               "label_validity_contract": LABEL_VALIDITY_CONTRACT,
               "implementation": {"initial_m": neighbor_search.INITIAL_M,
                                  "query_chunk": neighbor_search.QUERY_CHUNK,
                                  "openblas_num_threads":
                                      os.environ.get("OPENBLAS_NUM_THREADS", "unset"),
                                  "numpy": np.__version__, "pyarrow": pa.__version__},
               "tests": [t.name for t in rules.tests]})
    run_dir = (runs_dir or RUNS_DIR) / identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = artifacts.identity_block(history.freeze, rules.checksum)
    log(f"run {identity.run_id}: grid N={len(grid)} digest {grid.digest[:12]}, eval "
        f"{eval_indices[0]}..{eval_indices[-1]} ({len(eval_indices)} dates) -> {run_dir}")

    horizons = tuple(sorted({h for _, h in rules.combinations}))
    mark = time.perf_counter()
    validity = compute_validity(history.panel, horizons, rules.ca_suspect_ratio)
    log(f"label validity for horizons {horizons} in {time.perf_counter() - mark:.1f}s")

    eligibility = Eligibility(history, rules, invariants)
    figi = library.FigiCoder()
    files: dict[str, Any] = {}
    digests: dict[str, str] = {}
    manifests: dict[str, Any] = {}
    status_totals: dict[str, dict[str, int]] = {}
    pool_stats: dict[str, dict[str, int]] = {}
    insufficient: dict[str, dict[str, int]] = {}
    capture: dict = {}
    query_rows: list[dict[str, np.ndarray]] = []
    query_test_codes: list[np.ndarray] = []
    test_names = [t.name for t in rules.tests]
    samples_written = False

    for window in rules.pattern_windows:
        window_horizons = tuple(sorted({h for w, h in rules.combinations if w == window}))
        lib = library.build(history, rules, window=window, horizons=window_horizons,
                            validity=validity.valid, eligibility=eligibility,
                            eval_end_idx=int(eval_indices[-1]), figi=figi, log=log)
        invariants.record("R11")
        queries = encode_queries(history, eligibility, rules, window, eval_indices, figi, invariants)
        if not samples_written:
            table = query_sample_table(history, queries, eval_indices, figi)
            files["query_samples.parquet"] = artifacts.write_table(
                run_dir / "query_samples.parquet", table, stamp)
            digests["query_samples"] = artifacts.table_digest(table)
            query_manifest = {
                "rows": table.num_rows, "dates": len(eval_indices),
                "queries_per_date": rules.queries_per_date,
                "eval_range": [int(eval_indices[0]), int(eval_indices[-1])],
                "hash_string": SERIALIZATION["query_hash"],
                "content_digest": digests["query_samples"],
                "unique_tickers": int(np.unique(np.concatenate(
                    [queries[i].sample.ticker_col for i in eval_indices])).size),
                "sampled_by_date": {str(i): len(queries[i].sample) for i in eval_indices},
                "dates_below_queries_per_date": sum(
                    1 for i in eval_indices if len(queries[i].sample) < rules.queries_per_date),
                "dates_below_min_valid_queries": sum(
                    1 for i in eval_indices
                    if queries[i].eligible_count < rules.min_valid_queries_per_date)}
            samples_written = True
        for test in [t for t in rules.tests if t.window == window]:
            mark = time.perf_counter()
            output = run_test(test=test, lib=lib, queries=queries, eval_indices=eval_indices,
                              rules=rules, log=invariants, audit_dates=audit_dates,
                              capture=capture if capture_audit else None)
            stacked = output.stack(output.neighbor)
            table, digest_columns = neighbor_table(history, stacked, figi, test, test.horizon)
            name = f"neighbors_{test.name}.parquet"
            files[name] = artifacts.write_table(run_dir / name, table, stamp)
            digests[f"neighbors_{test.name}"] = artifacts.column_digest(
                digest_columns, artifacts.NEIGHBOR_DIGEST_COLUMNS)
            query_part = output.stack(output.query)
            query_rows.append(query_part)
            query_test_codes.append(np.full(query_part["query_date_idx"].shape[0],
                                            test_names.index(test.name), dtype=np.int32))
            status_totals[test.name] = dict(output.status_counts)
            pool_stats[test.name] = {str(k): v for k, v in sorted(output.pool_m_counts.items())}
            insufficient[test.name] = {
                "queries": len(output.insufficient_accepted),
                "min_accepted": min(output.insufficient_accepted, default=0),
                "max_accepted": max(output.insufficient_accepted, default=0)}
            log(f"  {test.name}: {table.num_rows:,} neighbours, "
                f"{query_part['query_date_idx'].shape[0]:,} queries, "
                f"{time.perf_counter() - mark:.1f}s, rss {peak_rss_mb():.0f} MB")
            del output, stacked, table, digest_columns
        table, digest_columns = library_table(history, lib, figi)
        name = f"library_meta_W{window}.parquet"
        files[name] = artifacts.write_table(run_dir / name, table, stamp)
        digests[f"library_meta_W{window}"] = artifacts.column_digest(
            digest_columns, tuple(sorted(digest_columns)))
        manifests[f"W{window}"] = lib.manifest()
        del lib, queries, table, digest_columns

    combined = {name: np.concatenate([part[name] for part in query_rows]) for name in query_rows[0]}
    table = query_result_table(combined, np.concatenate(query_test_codes), test_names)
    files["query_results.parquet"] = artifacts.write_table(
        run_dir / "query_results.parquet", table, stamp)
    digest_columns = {name: combined[name].astype(np.int64) for name in sorted(combined)}
    digest_columns["test_id_code"] = np.concatenate(query_test_codes).astype(np.int64)
    digests["query_results"] = artifacts.column_digest(digest_columns, tuple(sorted(digest_columns)))
    del combined, table, query_rows

    post_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    invariants.record("R2")
    if post_digest != pre_digest:
        raise HardFail("R2", f"INPUT_MUTATED_DURING_D2: the D read set changed while the run was"
                             f" in flight ({pre_digest} -> {post_digest})")

    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "phase": PHASE, "run_id": identity.run_id, "parent_run": parent_run,
        "freeze_identity": history.freeze.as_dict(),
        "grid": {"first_session": grid.session(0).isoformat(),
                 "last_session": grid.session(len(grid) - 1).isoformat(),
                 "session_count": len(grid), "digest": grid.digest},
        "evaluation": {"eval_range": [int(eval_indices[0]), int(eval_indices[-1])],
                       "dates": len(eval_indices), "queries_per_date": rules.queries_per_date},
        "label_validity": validity.counts(),
        "library": manifests,
        "status_by_test": status_totals,
        "insufficient_by_test": insufficient,
        "pool_m_by_test": pool_stats,
        "content_digests": dict(sorted(digests.items())),
        "input_read_set": {"pre": pre_digest, "post": post_digest, "match": post_digest == pre_digest},
        "audit_dates": [int(i) for i in audit_dates],
        "files": files,
        "performance": {"elapsed_seconds": round(elapsed, 1),
                        "peak_rss_mb": round(peak_rss_mb(), 1)},
    }
    runtime = {"invariant_checks": dict(invariants.as_dict()), "hard_failures": 0, "violations": 0,
               "normal_ineligible": {key: value["exclusions"] for key, value in manifests.items()},
               "query_status": status_totals}
    summary["artifact_identity"] = stamp
    summary["query_manifest"] = query_manifest
    context = RunContextData(history, rules, validity, eval_indices, audit_dates, capture, elapsed)
    return D2Result(identity, summary, manifests, query_manifest, runtime, digests, "PASS",
                    run_dir, context)


def run_context(workspace_root: Path, snapshot_id: str) -> dict[str, Any]:
    """Facts that differ between two identical runs, kept out of every deterministic digest."""
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id,
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
            "peak_rss_mb": round(peak_rss_mb(), 1)}


def finish(result: D2Result, context: Mapping[str, Any]) -> Path:
    """Write the manifests and, only when every check has passed, the token D3 looks for."""
    run_dir = result.run_dir
    stamp = result.summary["artifact_identity"]
    artifacts.write_json(run_dir / "run_identity.json", result.identity.as_dict(), stamp)
    artifacts.write_json(run_dir / "library_manifest.json", result.library_manifest, stamp)
    artifacts.write_json(run_dir / "query_manifest.json", result.query_manifest, stamp)
    artifacts.write_json(run_dir / "pit_runtime.json", result.pit_runtime, stamp)
    artifacts.write_json(run_dir / "run_context.json", dict(context), stamp)
    artifacts.write_json(run_dir / "summary.json", result.summary, stamp)
    if result.verdict == "PASS":
        artifacts.write_complete(run_dir, {"phase": PHASE, "run_id": result.identity.run_id,
                                           "verdict": result.verdict,
                                           "identity_digest": result.identity.digest,
                                           "parent_run": result.summary["parent_run"]}, stamp)
    return run_dir
