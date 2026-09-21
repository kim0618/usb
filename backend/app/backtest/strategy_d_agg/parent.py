"""Binding to the authoritative V2-A lineage, and the A-signal digest that D-AGG carries forward.

D-AGG does not bind to V2-A's whole signal artifact. V2-A D4 section 11 measured that the ``b0``
column differs by up to 2 ULP between machines (a BLAS matrix product), so the full
``signal_rows`` digest is machine bound. D-AGG never reads ``b0`` for a decision; it binds to
the columns it uses, with an explicit field order, dtype and null policy:

    recipe  "d-agg-a-binding-v1"
    columns query_date_idx (int64), query_ticker_col (int64), query_ticker (utf-8 string),
            sample_rank (int64), analog_signal_A (float64), signal_status (utf-8 string)
    order   the D3 artifact's row order, which is (query_date_idx, sample_rank) ascending
    nulls   string columns may not be null; float NaN is normalised to one quiet-NaN pattern
            before hashing, so "no value" hashes the same on every machine

On this machine the V2-A full digests are still reproducible and are checked too: the parent is
verified as V2-A D4 verified it before its A columns are believed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.strategy_d_agg.config import V2A_LINEAGE, V2A_RUNS_DIR, AggRules
from app.backtest.strategy_d_agg.models import HardFail
from app.backtest.strategy_d_analog import artifacts as v1_artifacts
from app.backtest.strategy_d_v2 import schema as v2a_schema
from app.backtest.strategy_d_v2.config import V2ARules, load_rules as load_v2a_rules
from app.backtest.strategy_d_v2.d1 import matrix_digest
from app.backtest.strategy_d_v2.d2 import load_parent
from app.backtest.strategy_d_v2.d3 import (
    EVALUATION_DIGEST_COLUMNS, SIGNAL_DIGEST_COLUMNS, SIGNAL_STATUS_ORDER, load_parent_d2,
)
from app.backtest.strategy_d_v2.d4 import digest_of, load_parent_d3

BINDING_RECIPE = "d-agg-a-binding-v1"
BINDING_DTYPES = {"query_date_idx": "int64", "query_ticker_col": "int64", "query_ticker": "utf8",
                  "sample_rank": "int64", "analog_signal_A": "float64", "signal_status": "utf8"}
QUIET_NAN = np.frombuffer(np.array([np.nan]).tobytes(), dtype="<f8")[0]


def _strings(column: pa.ChunkedArray, name: str) -> list[str]:
    values = column.combine_chunks()
    if values.null_count:
        raise HardFail("R5", f"{name} has {values.null_count} nulls; the binding forbids them")
    if pa.types.is_dictionary(values.type):
        values = values.dictionary_decode()
    return [str(v) for v in values.to_pylist()]


def a_binding_digest(table: pa.Table, columns: Sequence[str]) -> str:
    """The machine-independent digest of the A-signal columns (recipe in the module docstring)."""
    if tuple(columns) != tuple(BINDING_DTYPES):
        raise HardFail("R1", f"binding columns {tuple(columns)} != {tuple(BINDING_DTYPES)}")
    digest = hashlib.sha256(f"{BINDING_RECIPE}\n".encode())
    for name in columns:
        kind = BINDING_DTYPES[name]
        digest.update(f"{name}\t{kind}\t{table.num_rows}\n".encode())
        if kind == "utf8":
            digest.update("\x1f".join(_strings(table.column(name), name)).encode("utf-8"))
            continue
        values = table.column(name).combine_chunks()
        if values.null_count:
            raise HardFail("R5", f"{name} has nulls; the binding forbids them")
        array = values.to_numpy(zero_copy_only=False)
        if kind == "float64":
            array = np.where(np.isnan(array), QUIET_NAN, array.astype("<f8"))
            digest.update(np.ascontiguousarray(array, dtype="<f8").tobytes())
        else:
            digest.update(np.ascontiguousarray(array.astype("<i8")).tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class SignalRows:
    """The D3 columns D-AGG uses, in D3 row order. No outcome and no baseline is carried."""

    date_idx: np.ndarray
    ticker_col: np.ndarray
    tickers: tuple[str, ...]
    dates: tuple[str, ...]
    sample_rank: np.ndarray
    analog: np.ndarray
    signal_status: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.date_idx.shape[0])

    @property
    def signal_ok(self) -> np.ndarray:
        return np.array([s == "OK" for s in self.signal_status], dtype=bool)


@dataclass(frozen=True)
class Parent:
    v2a_rules: V2ARules
    d1: Any
    d2: Any
    d3: Any
    signal_table: pa.Table
    rows: SignalRows
    a_digest: str
    signal_rows_digest: str
    evaluation_labels_digest: str
    query_sample_digest: str
    files_sha256: Mapping[str, Mapping[str, str]]

    def summary(self) -> dict[str, Any]:
        return {"source": "V2-A D3 signal_rows.parquet (A columns only)",
                "lineage": {"d1": self.d1.run_id, "d2": self.d2.run_id, "d3": self.d3.run_id},
                "d3_identity_digest": self.d3.identity_digest,
                "a_binding_recipe": BINDING_RECIPE,
                "a_binding_columns": list(BINDING_DTYPES),
                "a_binding_digest": self.a_digest,
                "v2a_signal_rows_digest_this_machine": self.signal_rows_digest,
                "v2a_evaluation_labels_digest": self.evaluation_labels_digest,
                "query_sample_digest": self.query_sample_digest,
                "b0_used": False}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parent_files(runs_dir: Path, lineage: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """sha256 of every file in the D1..D3 parent run directories (PRE/POST immutability)."""
    out: dict[str, dict[str, str]] = {}
    for phase in ("D1", "D2", "D3"):
        run_dir = runs_dir / lineage[phase]
        out[phase] = {p.name: _sha256_file(p) for p in sorted(run_dir.iterdir()) if p.is_file()}
    return out


def load(rules: AggRules, *, runs_dir: Path = V2A_RUNS_DIR,
         lineage: Mapping[str, str] = V2A_LINEAGE) -> Parent:
    v2a_rules = load_v2a_rules()
    if v2a_rules.checksum != rules.v2a_canonical:
        raise HardFail("R1", f"V2-A rules {v2a_rules.checksum} != {rules.v2a_canonical}")
    d1 = load_parent(runs_dir, lineage["D1"], v2a_rules)
    d2 = load_parent_d2(runs_dir, lineage["D2"], v2a_rules, v2a_schema.build(v2a_rules), d1)
    d3 = load_parent_d3(runs_dir, lineage["D3"], v2a_rules, d2)
    summary = d3.summary
    if int(summary["pit"]["violations"]) != 0 or not summary["hard_checks"].get("future_query_mutation"):
        raise HardFail("R2", "parent D3 did not pass its PIT and future-query mutation audits")
    files = parent_files(runs_dir, lineage)

    table = pq.read_table(d3.signal_path)
    evaluation = pq.read_table(d3.evaluation_path)
    signal_digest = digest_of(table, SIGNAL_DIGEST_COLUMNS)
    evaluation_digest = digest_of(evaluation, EVALUATION_DIGEST_COLUMNS)
    if signal_digest != d3.signal_digest or evaluation_digest != d3.evaluation_digest:
        raise HardFail("R2", "V2-A D3 artifacts do not hash to their COMPLETE token on this machine")
    for path in (d3.signal_path, d3.evaluation_path):
        if v1_artifacts.read_identity(path).get("rules_checksum") != v2a_rules.checksum:
            raise HardFail("R1", f"{path.name} was written under other rules")

    rows = SignalRows(
        date_idx=table.column("query_date_idx").to_numpy().astype(np.int64),
        ticker_col=table.column("query_ticker_col").to_numpy().astype(np.int64),
        tickers=tuple(_strings(table.column("query_ticker"), "query_ticker")),
        dates=tuple(_strings(table.column("query_date"), "query_date")),
        sample_rank=table.column("sample_rank").to_numpy().astype(np.int64),
        analog=table.column("analog_signal_A").to_numpy().astype(np.float64),
        signal_status=tuple(_strings(table.column("signal_status"), "signal_status")))
    if set(rows.signal_status) - set(SIGNAL_STATUS_ORDER):
        raise HardFail("R5", f"unknown signal_status values {set(rows.signal_status)}")
    order = np.lexsort((rows.sample_rank, rows.date_idx))
    if not np.array_equal(order, np.arange(len(rows))):
        raise HardFail("R5", "D3 rows are not in (query_date_idx, sample_rank) order")

    query_sample = (matrix_digest("session", rows.date_idx) + ":"
                    + matrix_digest("ticker", rows.ticker_col))
    if query_sample != d1.digests["query_sample"]:
        raise HardFail("R2", "D3 query rows are not D1's declared query sample")
    a_digest = a_binding_digest(table, rules.signal_binding_columns)
    return Parent(v2a_rules, d1, d2, d3, table, rows, a_digest, signal_digest, evaluation_digest,
                  query_sample, files)
