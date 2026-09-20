"""D4: the first and only time Strategy D V1 asks whether its signal predicts anything.

Everything D4 measures was fixed before it could be measured. The statistic is D0's (a Spearman
per date, equal-weight mean over dates), the intervals are D0's (moving block bootstrap, block
20, 10000 replicates, percentile, Bonferroni over 14), the baselines are D0's (N1 random analogs,
N2a feature kNN, N2b orthogonalization) and the ten gate conditions are D0's. This module reads
them and reports; it has no branch that adjusts a threshold, drops a period or merges two tests.

D's own signal is never recomputed - ``S(q)`` and ``sigma(q)`` come from the D3 artifact verbatim.
The library *identities* are read from D2 because N1 and N2a are defined against "the same
library the query sees" and cannot exist otherwise; the pattern encoder and the path similarity
engine are not called from here at all.
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

from app.backtest.strategy_d_analog import (
    artifacts, baselines, features, gate, labels, metrics, resample, universe,
)
from app.backtest.strategy_d_analog.config import (
    DECLARED_RULES_CHECKSUM, REPO_ROOT, RULES_PATH, AnalogRules, load_rules,
)
from app.backtest.strategy_d_analog.identity import RunIdentity, run_identity
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.models import FreezeIdentity, HardFail, TestId
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history, read_set_digest

PHASE = "D4"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d/runs"
#: D0 ``evaluation.min_valid_queries_per_date``: below this a date's cross-section is too thin to
#: carry a rank correlation, so it is not an evaluable date.
MIN_VALID_QUERIES_PER_DATE = 100
SIGMA_BUCKETS = metrics.TERCILE_COUNT


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


@dataclass(frozen=True)
class Parents:
    """The two frozen runs D4 consumes, and the digests that pin every table it reads."""

    d3_dir: Path
    d2_dir: Path
    d3_run_id: str
    d3_identity: str
    d3_complete_digest: str
    d2_run_id: str
    d2_identity: str
    d2_complete_digest: str
    freeze: FreezeIdentity
    rules_checksum: str
    d3_digests: Mapping[str, str]
    d2_digests: Mapping[str, str]
    tests: tuple[str, ...]
    eval_range: tuple[int, int]
    top_k: int
    insufficient_share_by_test: Mapping[str, float]
    d2_pit_violations: int

    def as_payload(self) -> dict[str, Any]:
        return {"parent_d3_run_id": self.d3_run_id, "parent_d3_identity": self.d3_identity,
                "parent_d3_complete_digest": self.d3_complete_digest,
                "parent_d2_run_id": self.d2_run_id, "parent_d2_identity": self.d2_identity,
                "parent_d2_complete_digest": self.d2_complete_digest}


def load_parents(d3_dir: Path, d2_dir: Path) -> Parents:
    """Admit the D3 and D2 runs, or refuse. D4 trusts nothing it has not checked here."""
    def _complete(directory: Path, phase: str) -> dict[str, Any]:
        path = directory / "COMPLETE.json"
        if not path.exists():
            raise HardFail("F2", f"{directory} has no COMPLETE.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("phase") != phase or payload.get("verdict") != "PASS":
            raise HardFail("F2", f"{directory} is {payload.get('phase')} / {payload.get('verdict')}")
        return payload

    c3, c2 = _complete(d3_dir, "D3"), _complete(d2_dir, "D2")
    i3 = json.loads((d3_dir / "run_identity.json").read_text(encoding="utf-8"))
    i2 = json.loads((d2_dir / "run_identity.json").read_text(encoding="utf-8"))
    s3 = json.loads((d3_dir / "summary.json").read_text(encoding="utf-8"))
    s2 = json.loads((d2_dir / "summary.json").read_text(encoding="utf-8"))
    p2 = json.loads((d2_dir / "pit_runtime.json").read_text(encoding="utf-8"))

    if i3["rules_checksum"] != DECLARED_RULES_CHECKSUM or i2["rules_checksum"] != DECLARED_RULES_CHECKSUM:
        raise HardFail("R1", "a parent ran under a different rule checksum")
    if i3["parent_d2_identity"] != i2["identity_digest"]:
        raise HardFail("F2", "the D3 run is not bound to this D2 run")
    if i3["data"] != i2["data"]:
        raise HardFail("F1", "the two parents disagree on the frozen dataset")
    for payload, identity in ((c3, i3), (c2, i2)):
        if payload["identity_digest"] != identity["identity_digest"]:
            raise HardFail("F2", "a parent's COMPLETE token and run identity disagree")
    status = s2["status_by_test"]
    shares = {name: (sum(v for k, v in counts.items() if k == "INSUFFICIENT_NEIGHBORS")
                     / max(sum(counts.values()), 1)) for name, counts in status.items()}
    return Parents(
        d3_dir=d3_dir, d2_dir=d2_dir, d3_run_id=i3["run_id"], d3_identity=i3["identity_digest"],
        d3_complete_digest=artifacts.sha256_file(d3_dir / "COMPLETE.json"),
        d2_run_id=i2["run_id"], d2_identity=i2["identity_digest"],
        d2_complete_digest=artifacts.sha256_file(d2_dir / "COMPLETE.json"),
        freeze=FreezeIdentity(**i3["data"]), rules_checksum=i3["rules_checksum"],
        d3_digests=dict(s3["content_digests"]), d2_digests=dict(s2["content_digests"]),
        tests=tuple(i3["tests"]), eval_range=(int(i3["eval_range"][0]), int(i3["eval_range"][1])),
        top_k=int(i3["top_k"]), insufficient_share_by_test=shares,
        d2_pit_violations=int(p2["violations"]) + int(p2["hard_failures"]))


def _check_stamp(path: Path, parents: Parents) -> None:
    stamp = artifacts.read_identity(path)
    if (stamp["freeze_digest"] != parents.freeze.freeze_digest
            or stamp["grid_digest"] != parents.freeze.grid_digest
            or stamp["rules_checksum"] != parents.rules_checksum):
        raise HardFail("F1", f"{path.name} carries a different freeze, grid or rules identity")


@dataclass
class QueryPanel:
    """The D3 signal table and the D3 answer key, joined on ``(date, sample_rank)`` per test."""

    by_test: dict[str, dict[str, np.ndarray]]
    by_horizon: dict[int, dict[str, np.ndarray]]


def read_parent_tables(parents: Parents) -> QueryPanel:
    """Read D3's two tables and verify each against the digest D3 recorded for it."""
    signal_path = parents.d3_dir / "signal_rows.parquet"
    label_path = parents.d3_dir / "evaluation_labels.parquet"
    for path in (signal_path, label_path):
        _check_stamp(path, parents)

    table = pq.read_table(signal_path)
    names = np.asarray(table["test_id"].to_pylist())
    order = {name: index for index, name in enumerate(parents.tests)}
    status_order = ("OK", "INSUFFICIENT_VALID_NEIGHBORS")
    digest_columns = {
        "query_date_idx": np.asarray(table["query_date_idx"]).astype(np.int64),
        "sample_rank": np.asarray(table["sample_rank"]).astype(np.int64),
        "query_ticker_col": np.asarray(table["query_ticker_col"]).astype(np.int64),
        "neighbor_total": np.asarray(table["neighbor_total"]).astype(np.int64),
        "neighbor_valid": np.asarray(table["neighbor_valid"]).astype(np.int64),
        "status": np.array([status_order.index(v) for v in table["signal_status"].to_pylist()],
                           dtype=np.int64),
        "S": np.asarray(table["S"]), "sigma": np.asarray(table["sigma"]),
        "mean": np.asarray(table["distribution_mean"]),
        "median": np.asarray(table["distribution_median"]),
        "std": np.asarray(table["distribution_std"]),
        "positive": np.asarray(table["positive_count"]).astype(np.int64),
        "negative": np.asarray(table["negative_count"]).astype(np.int64),
        "zero": np.asarray(table["zero_count"]).astype(np.int64),
        "hit_rate": np.asarray(table["hit_rate"]),
        "test_id_code": np.array([order[n] for n in names], dtype=np.int64)}
    for quantile in (10, 25, 50, 75, 90):
        digest_columns[f"q{quantile:02d}"] = np.asarray(table[f"distribution_q{quantile:02d}"])
    found = artifacts.column_digest(digest_columns, tuple(sorted(digest_columns)))
    if found != parents.d3_digests["signal_rows"]:
        raise HardFail("F1", f"signal_rows digest {found} != parent record")

    by_test: dict[str, dict[str, np.ndarray]] = {}
    for name in parents.tests:
        rows = np.nonzero(names == name)[0]
        date_idx = np.asarray(table["query_date_idx"])[rows]
        rank = np.asarray(table["sample_rank"])[rows]
        sort = np.lexsort((rank, date_idx))
        picked = rows[sort]
        by_test[name] = {
            "query_date_idx": np.asarray(table["query_date_idx"])[picked].astype(np.int32),
            "sample_rank": np.asarray(table["sample_rank"])[picked].astype(np.int16),
            "query_ticker_col": np.asarray(table["query_ticker_col"])[picked].astype(np.int32),
            "S": np.asarray(table["S"])[picked],
            "sigma": np.asarray(table["sigma"])[picked],
            "hit_rate": np.asarray(table["hit_rate"])[picked],
            "distribution_mean": np.asarray(table["distribution_mean"])[picked],
            "status_ok": np.array([v == "OK" for v in
                                   np.asarray(table["signal_status"].to_pylist())[picked]])}
    del table, digest_columns

    table = pq.read_table(label_path)
    horizons = np.asarray(table["horizon"])
    label_digest = {"horizon": horizons.astype(np.int64),
                    "query_date_idx": np.asarray(table["query_date_idx"]).astype(np.int64),
                    "sample_rank": np.asarray(table["sample_rank"]).astype(np.int64),
                    "query_ticker_col": np.asarray(table["query_ticker_col"]).astype(np.int64),
                    "query_forward_return": np.asarray(table["query_forward_return"]),
                    "query_close_return": np.asarray(table["query_close_return"]),
                    "query_label_valid": np.asarray(table["query_label_valid"]).astype(np.int64)}
    found = artifacts.column_digest(label_digest, tuple(sorted(label_digest)))
    if found != parents.d3_digests["evaluation_labels"]:
        raise HardFail("F1", f"evaluation_labels digest {found} != parent record")

    by_horizon: dict[int, dict[str, np.ndarray]] = {}
    for horizon in sorted(set(horizons.tolist())):
        rows = np.nonzero(horizons == horizon)[0]
        date_idx = np.asarray(table["query_date_idx"])[rows]
        rank = np.asarray(table["sample_rank"])[rows]
        picked = rows[np.lexsort((rank, date_idx))]
        by_horizon[int(horizon)] = {
            "query_date_idx": np.asarray(table["query_date_idx"])[picked].astype(np.int32),
            "sample_rank": np.asarray(table["sample_rank"])[picked].astype(np.int16),
            "query_ticker_col": np.asarray(table["query_ticker_col"])[picked].astype(np.int32),
            "realized": np.asarray(table["query_forward_return"])[picked],
            "valid": np.asarray(table["query_label_valid"])[picked].astype(bool)}
    return QueryPanel(by_test, by_horizon)


def read_library(parents: Parents, window: int) -> dict[str, np.ndarray]:
    """D2's library identity table for one window length. Identities and flags, never vectors."""
    path = parents.d2_dir / f"library_meta_W{window}.parquet"
    _check_stamp(path, parents)
    table = pq.read_table(path)
    out = {"end_idx": np.asarray(table["end_idx"]).astype(np.int32),
           "ticker_col": np.asarray(table["ticker_col"]).astype(np.int32),
           "figi_code": np.asarray(table["figi_code"]).astype(np.int32),
           "defined_A": np.asarray(table["defined_A"]).astype(bool),
           "defined_B": np.asarray(table["defined_B"]).astype(bool)}
    for name in table.column_names:
        if name.startswith("valid_h"):
            out[name] = np.asarray(table[name]).astype(bool)
    digest = {"end_idx": out["end_idx"].astype(np.int64),
              "ticker_col": out["ticker_col"].astype(np.int64),
              "figi_code": out["figi_code"].astype(np.int64),
              "defined_A": out["defined_A"].astype(np.int64),
              "defined_B": out["defined_B"].astype(np.int64)}
    for name in sorted(k for k in out if k.startswith("valid_h")):
        digest[name] = out[name].astype(np.int64)
    found = artifacts.column_digest(digest, tuple(sorted(digest)))
    if found != parents.d2_digests[f"library_meta_W{window}"]:
        raise HardFail("F1", f"library_meta_W{window} digest {found} != parent record")
    return out


@dataclass
class TestEvaluation:
    """One TestId's per-date series. Every number here feeds a declared condition or a report."""

    test_id: str
    window: int
    horizon: int
    representation: str
    date_idx: np.ndarray
    ic: np.ndarray
    ic_n1: np.ndarray
    ic_n2a: np.ndarray
    ic_n2b: np.ndarray
    ic_mean_neighbor: np.ndarray
    ic_hit_rate: np.ndarray
    quintiles: np.ndarray
    spread: np.ndarray
    mfe_quintiles: np.ndarray
    mae_quintiles: np.ndarray
    sigma_tercile_ic: np.ndarray
    valid_per_date: np.ndarray
    valid_queries: int = 0
    unique_tickers: int = 0
    total_queries: int = 0
    n1_short_draws: int = 0
    n2a_short_draws: int = 0
    n2_missing_queries: int = 0
    feature_correlation: dict = field(default_factory=dict)
    name_contribution: dict = field(default_factory=dict)
    date_contribution: dict = field(default_factory=dict)


@dataclass
class D4Result:
    identity: RunIdentity
    summary: dict[str, Any]
    verdicts: list[gate.TestVerdict]
    decision: dict[str, Any]
    digests: dict[str, str]
    tables: dict[str, pa.Table]
    verdict: str
    run_dir: Path


def _date_slices(date_idx: np.ndarray) -> dict[int, tuple[int, int]]:
    """Start and stop offsets of every date in an array already sorted by date."""
    out: dict[int, tuple[int, int]] = {}
    if date_idx.shape[0] == 0:
        return out
    changes = np.nonzero(np.diff(date_idx))[0] + 1
    bounds = np.concatenate([[0], changes, [date_idx.shape[0]]])
    for start, stop in zip(bounds[:-1], bounds[1:]):
        out[int(date_idx[start])] = (int(start), int(stop))
    return out


def evaluate_combination(*, window: int, horizon: int, representations: Sequence[str],
                         parents: Parents, panel: QueryPanel, library: Mapping[str, np.ndarray],
                         feature_set: features.FeatureSet, excess: np.ndarray,
                         mfe: np.ndarray, mae: np.ndarray, history: DailyHistory,
                         query_figi: Mapping[tuple[int, int], int], rules: AnalogRules,
                         log: Callable[[str], None]) -> dict[str, TestEvaluation]:
    """Walk one ``(W, h)`` combination date by date, building D's series and both baselines.

    N1 and N2a depend on the library and the query, never on which representation selected D's
    neighbours, so they are computed once per combination and shared by its A and B tests. That
    sharing is asserted, not assumed: if the two representations ever had different library rows
    the function computes them separately.
    """
    valid_key = f"valid_h{horizon}"
    compacts = {rep: np.nonzero(library[f"defined_{rep}"] & library[valid_key])[0]
                for rep in representations}
    shared = all(np.array_equal(compacts[representations[0]], compacts[rep])
                 for rep in representations)
    if not shared:
        raise HardFail("F1", f"W{window} h{horizon}: representations see different library rows;"
                             " the shared-baseline assumption does not hold")
    rows = compacts[representations[0]]
    context = baselines.LibraryContext(
        window=window, horizon=horizon, row=rows,
        end_idx=library["end_idx"][rows], ticker_col=library["ticker_col"][rows],
        figi_code=library["figi_code"][rows],
        excess=excess[library["end_idx"][rows], library["ticker_col"][rows]],
        quintile=feature_set.volatility_quintile[library["end_idx"][rows],
                                                 library["ticker_col"][rows]],
        features=feature_set.matrix(library["end_idx"][rows].astype(np.int64),
                                    library["ticker_col"][rows].astype(np.int64)),
        finite=feature_set.finite[library["end_idx"][rows], library["ticker_col"][rows]])

    horizon_rows = panel.by_horizon[horizon]
    label_slices = _date_slices(horizon_rows["query_date_idx"])
    test_rows = {rep: panel.by_test[TestId(window, horizon, rep).name] for rep in representations}
    test_slices = {rep: _date_slices(test_rows[rep]["query_date_idx"]) for rep in representations}

    series: dict[str, dict[str, list]] = {
        rep: {"date": [], "ic": [], "ic_n1": [], "ic_n2a": [], "ic_n2b": [], "ic_mean": [],
              "ic_hit": [], "quintiles": [], "spread": [], "mfe": [], "mae": [],
              "sigma_ic": [], "valid": [], "names": [], "contrib": []}
        for rep in representations}
    diagnostics = {rep: {"n1_short": 0, "n2a_short": 0, "n2_missing": 0, "total": 0,
                         "tickers": set()} for rep in representations}

    for date in sorted(label_slices):
        if date not in test_slices[representations[0]]:
            continue
        lo, hi = label_slices[date]
        realized_all = horizon_rows["realized"][lo:hi]
        label_valid = horizon_rows["valid"][lo:hi]
        ticker_all = horizon_rows["query_ticker_col"][lo:hi]
        rank_all = horizon_rows["sample_rank"][lo:hi]

        base_rep = representations[0]
        blo, bhi = test_slices[base_rep][date]
        status_ok = test_rows[base_rep]["status_ok"][blo:bhi]
        if not np.array_equal(test_rows[base_rep]["sample_rank"][blo:bhi], rank_all):
            raise HardFail("F2", f"date {date}: D3 signal and label rows are not aligned")
        usable = np.nonzero(label_valid & status_ok)[0]
        for rep in representations:
            diagnostics[rep]["total"] += int(bhi - blo)
        if usable.shape[0] < MIN_VALID_QUERIES_PER_DATE:
            continue

        realized = realized_all[usable]
        ticker_col = ticker_all[usable].astype(np.int64)
        tie_break = rank_all[usable].astype(np.int64)
        session = history.grid.session(date)
        tickers = [history.tickers[int(c)] for c in ticker_col]
        figi = np.array([query_figi[(date, int(r))] for r in rank_all[usable]], dtype=np.int32)
        session_row = np.full(usable.shape[0], date, dtype=np.int64)
        query_features = feature_set.matrix(session_row, ticker_col)
        query_quintile = feature_set.volatility_quintile[date, ticker_col]

        cut = context.cut_for(date)
        pools = baselines.quintile_pools(context, cut)
        n1_signals, n1_counts = baselines.n1_signal(
            context=context, cut=cut, pools=pools, query_session=session.isoformat(),
            query_tickers=tickers, query_ticker_col=ticker_col, query_figi_code=figi,
            query_quintile=query_quintile, top_k=parents.top_k,
            ticker_cap=rules.max_windows_per_ticker, date_cap=rules.max_neighbors_per_end_date)
        n2a_signal, n2a_counts = baselines.n2a_signal(
            context=context, cut=cut, query_features=query_features, query_ticker_col=ticker_col,
            query_figi_code=figi, top_k=parents.top_k, ticker_cap=rules.max_windows_per_ticker,
            date_cap=rules.max_neighbors_per_end_date)

        n1_ic = float(np.nanmean([metrics.spearman(n1_signals[r][np.isfinite(n1_signals[r])],
                                                   realized[np.isfinite(n1_signals[r])])
                                  for r in range(n1_signals.shape[0])]))
        n2a_finite = np.isfinite(n2a_signal)
        n2a_ic = metrics.spearman(n2a_signal[n2a_finite], realized[n2a_finite]) \
            if n2a_finite.sum() >= 2 else float("nan")

        for rep in representations:
            tlo, thi = test_slices[rep][date]
            signal_values = test_rows[rep]["S"][tlo:thi][usable]
            sigma_values = test_rows[rep]["sigma"][tlo:thi][usable]
            mean_values = test_rows[rep]["distribution_mean"][tlo:thi][usable]
            hit_values = test_rows[rep]["hit_rate"][tlo:thi][usable]
            store = series[rep]
            store["date"].append(date)
            store["ic"].append(metrics.spearman(signal_values, realized))
            store["ic_n1"].append(n1_ic)
            store["ic_n2a"].append(n2a_ic)
            store["ic_mean"].append(metrics.spearman(mean_values, realized))
            store["ic_hit"].append(metrics.spearman(hit_values, realized))

            feature_ok = np.isfinite(query_features).all(axis=1)
            if feature_ok.sum() > query_features.shape[1] + 1:
                residual = baselines.orthogonalize(
                    metrics.average_rank(signal_values[feature_ok]), query_features[feature_ok])
                store["ic_n2b"].append(metrics.spearman(residual, realized[feature_ok]))
            else:
                store["ic_n2b"].append(float("nan"))
            diagnostics[rep]["n2_missing"] += int((~feature_ok).sum())

            baskets = metrics.quintile_baskets(signal_values, realized, tie_break)
            store["quintiles"].append(baskets)
            store["spread"].append(baskets[-1] - baskets[0])
            bucket = metrics.bucket_by_rank(signal_values, metrics.QUINTILE_COUNT, tie_break)
            query_mfe = mfe[date, ticker_col]
            query_mae = mae[date, ticker_col]
            store["mfe"].append(np.array([np.nanmean(query_mfe[bucket == q]) if (bucket == q).any()
                                          else np.nan for q in range(metrics.QUINTILE_COUNT)]))
            store["mae"].append(np.array([np.nanmean(query_mae[bucket == q]) if (bucket == q).any()
                                          else np.nan for q in range(metrics.QUINTILE_COUNT)]))
            sigma_bucket = metrics.bucket_by_rank(sigma_values, SIGMA_BUCKETS, tie_break)
            store["sigma_ic"].append(np.array([
                metrics.spearman(signal_values[sigma_bucket == b], realized[sigma_bucket == b])
                if (sigma_bucket == b).sum() >= 2 else np.nan for b in range(SIGMA_BUCKETS)]))
            store["valid"].append(usable.shape[0])
            store["names"].append(ticker_col.copy())
            store["contrib"].append(realized.copy())
            diagnostics[rep]["tickers"].update(int(c) for c in ticker_col)
            diagnostics[rep]["n1_short"] += int((n1_counts < parents.top_k).sum())
            diagnostics[rep]["n2a_short"] += int((n2a_counts < parents.top_k).sum())

    out: dict[str, TestEvaluation] = {}
    for rep in representations:
        store = series[rep]
        test_id = TestId(window, horizon, rep).name
        stacked = TestEvaluation(
            test_id=test_id, window=window, horizon=horizon, representation=rep,
            date_idx=np.asarray(store["date"], dtype=np.int32),
            ic=np.asarray(store["ic"]), ic_n1=np.asarray(store["ic_n1"]),
            ic_n2a=np.asarray(store["ic_n2a"]), ic_n2b=np.asarray(store["ic_n2b"]),
            ic_mean_neighbor=np.asarray(store["ic_mean"]),
            ic_hit_rate=np.asarray(store["ic_hit"]),
            quintiles=np.asarray(store["quintiles"]) if store["quintiles"] else np.empty((0, 5)),
            spread=np.asarray(store["spread"]),
            mfe_quintiles=np.asarray(store["mfe"]) if store["mfe"] else np.empty((0, 5)),
            mae_quintiles=np.asarray(store["mae"]) if store["mae"] else np.empty((0, 5)),
            sigma_tercile_ic=np.asarray(store["sigma_ic"]) if store["sigma_ic"]
            else np.empty((0, SIGMA_BUCKETS)),
            valid_per_date=np.asarray(store["valid"], dtype=np.int32),
            valid_queries=int(np.sum(store["valid"])),
            unique_tickers=len(diagnostics[rep]["tickers"]),
            total_queries=diagnostics[rep]["total"],
            n1_short_draws=diagnostics[rep]["n1_short"],
            n2a_short_draws=diagnostics[rep]["n2a_short"],
            n2_missing_queries=diagnostics[rep]["n2_missing"])
        out[test_id] = stacked
    log(f"  W{window} h{horizon}: {len(out[TestId(window, horizon, representations[0]).name].ic)}"
        f" evaluable dates, rss {peak_rss_mb():.0f} MB")
    return out


def read_query_figi(parents: Parents) -> dict[tuple[int, int], int]:
    """D2's query manifest: the composite FIGI code the baselines need for same-symbol exclusion."""
    path = parents.d2_dir / "query_samples.parquet"
    _check_stamp(path, parents)
    table = pq.read_table(path)
    found = artifacts.table_digest(table)
    if found != parents.d2_digests["query_samples"]:
        raise HardFail("F1", f"query_samples digest {found} != parent record")
    dates = np.asarray(table["query_date_idx"])
    ranks = np.asarray(table["sample_rank"])
    codes = np.asarray(table["query_figi_code"])
    return {(int(d), int(r)): int(c) for d, r, c in zip(dates, ranks, codes)}


def empty_intervals() -> dict[str, Any]:
    """What a test with no evaluable date reports.

    D0 sends that case to INCONCLUSIVE - "the data yields fewer than 150 evaluable dates" is a
    declared decision, not a crash - so the intervals come back empty and the gate does the rest.
    """
    blank = resample.Interval(float("nan"), float("nan"), float("nan"), "bonferroni")
    nominal = resample.Interval(float("nan"), float("nan"), float("nan"), "nominal_95")
    pair = {"bonferroni": blank, "nominal": nominal}
    names = ("ic", "delta_vs_n1", "delta_vs_n2a", "n2b_residual_ic", "quintile_spread")
    payload: dict[str, Any] = {name: resample.describe(pair) for name in names}
    payload["_objects"] = {"ic": pair, "delta_n1": pair, "delta_n2a": pair, "n2b": pair,
                           "spread": pair}
    return payload


def bootstrap_test(evaluation: TestEvaluation, indices: np.ndarray) -> dict[str, Any]:
    """Every interval one test needs, all drawn on the same resampled days (D0 ``paired``)."""
    if evaluation.date_idx.shape[0] == 0:
        return empty_intervals()
    finite = np.isfinite(evaluation.ic)
    if not finite.all():
        raise HardFail("R12", f"{evaluation.test_id}: a date produced a non-finite IC")
    ic = resample.both_levels(evaluation.ic, indices)
    delta_n1 = resample.paired_difference(evaluation.ic, evaluation.ic_n1, indices)
    delta_n2a = resample.paired_difference(evaluation.ic, np.nan_to_num(
        evaluation.ic_n2a, nan=0.0), indices)
    n2b = resample.both_levels(np.nan_to_num(evaluation.ic_n2b, nan=0.0), indices)
    spread = resample.both_levels(evaluation.spread, indices)
    return {"ic": resample.describe(ic), "delta_vs_n1": resample.describe(delta_n1),
            "delta_vs_n2a": resample.describe(delta_n2a), "n2b_residual_ic": resample.describe(n2b),
            "quintile_spread": resample.describe(spread),
            "_objects": {"ic": ic, "delta_n1": delta_n1, "delta_n2a": delta_n2a, "n2b": n2b,
                         "spread": spread}}


def build_verdicts(evaluations: Mapping[str, TestEvaluation],
                   bootstraps: Mapping[str, dict], parents: Parents,
                   rules: AnalogRules) -> list[gate.TestVerdict]:
    """Apply the ten conditions once every test's numbers exist, so condition 8 can see them all."""
    ic_points = {name: float(np.mean(e.ic)) if e.ic.size else float("nan")
                 for name, e in evaluations.items()}
    verdicts: list[gate.TestVerdict] = []
    for name in sorted(evaluations):
        evaluation = evaluations[name]
        boot = bootstraps[name]["_objects"]
        others = {other: ic_points[other] for other, value in evaluations.items()
                  if value.window == evaluation.window
                  and value.representation == evaluation.representation
                  and value.horizon != evaluation.horizon}
        verdicts.append(gate.evaluate_test(
            test_id=name, ic_point=ic_points[name],
            ic_ci_low=boot["ic"]["bonferroni"].low, ic_ci_high=boot["ic"]["bonferroni"].high,
            delta_n1_ci_low=boot["delta_n1"]["bonferroni"].low,
            n2b_ci_low=boot["n2b"]["bonferroni"].low,
            delta_n2a_point=boot["delta_n2a"]["bonferroni"].point,
            quintile_spread_point=boot["spread"]["bonferroni"].point,
            block_ic_points=metrics.block_means(
                metrics.DailySeries(evaluation.date_idx, evaluation.ic), rules.raw["evaluation"]["time_blocks"]),
            other_horizon_ic_points=others, evaluable_dates=int(evaluation.date_idx.shape[0]),
            valid_queries=evaluation.valid_queries, unique_tickers=evaluation.unique_tickers,
            insufficient_share=float(parents.insufficient_share_by_test.get(name, 0.0)),
            pit_violations=parents.d2_pit_violations))
    return verdicts


def result_tables(evaluations: Mapping[str, TestEvaluation], bootstraps: Mapping[str, dict],
                  verdicts: Sequence[gate.TestVerdict], history: DailyHistory,
                  ) -> tuple[dict[str, pa.Table], dict[str, dict[str, np.ndarray]]]:
    """The four artifact tables, plus the numeric columns their content digests are taken over."""
    by_name = {v.test_id: v for v in verdicts}
    rows: dict[str, list] = {key: [] for key in
                             ("test_id", "window", "horizon", "representation", "evaluable_dates",
                              "valid_queries", "total_queries", "unique_tickers", "mean_ic",
                              "median_ic", "std_ic", "positive_ic_ratio", "ic_ci_low",
                              "ic_ci_high", "ic_ci_low_95", "ic_ci_high_95", "mean_ic_n1",
                              "delta_n1_point", "delta_n1_ci_low", "mean_ic_n2a",
                              "delta_n2a_point", "delta_n2a_ci_low", "n2b_point", "n2b_ci_low",
                              "q1", "q2", "q3", "q4", "q5", "spread_point", "spread_ci_low",
                              "monotonic_violations", "basket_trend", "status")}
    daily: dict[str, list] = {k: [] for k in ("test_id", "date_idx", "ic", "ic_n1", "ic_n2a",
                                              "ic_n2b", "spread", "valid_queries")}
    quint: dict[str, list] = {k: [] for k in ("test_id", "date_idx", "quintile", "realized",
                                              "mfe", "mae")}
    sigma: dict[str, list] = {k: [] for k in ("test_id", "date_idx", "tercile", "ic")}

    for name in sorted(evaluations):
        e, boot, verdict = evaluations[name], bootstraps[name]["_objects"], by_name[name]
        series = metrics.DailySeries(e.date_idx, e.ic)
        with np.errstate(invalid="ignore"):
            baskets = (np.nanmean(e.quintiles, axis=0) if e.quintiles.size
                       else np.full(5, np.nan))
        rows["test_id"].append(name)
        rows["window"].append(e.window)
        rows["horizon"].append(e.horizon)
        rows["representation"].append(e.representation)
        rows["evaluable_dates"].append(len(series))
        rows["valid_queries"].append(e.valid_queries)
        rows["total_queries"].append(e.total_queries)
        rows["unique_tickers"].append(e.unique_tickers)
        rows["mean_ic"].append(series.mean)
        rows["median_ic"].append(series.median)
        rows["std_ic"].append(series.std)
        rows["positive_ic_ratio"].append(series.positive_ratio)
        rows["ic_ci_low"].append(boot["ic"]["bonferroni"].low)
        rows["ic_ci_high"].append(boot["ic"]["bonferroni"].high)
        rows["ic_ci_low_95"].append(boot["ic"]["nominal"].low)
        rows["ic_ci_high_95"].append(boot["ic"]["nominal"].high)
        rows["mean_ic_n1"].append(float(np.mean(e.ic_n1)) if e.ic_n1.size else float("nan"))
        rows["delta_n1_point"].append(boot["delta_n1"]["bonferroni"].point)
        rows["delta_n1_ci_low"].append(boot["delta_n1"]["bonferroni"].low)
        rows["mean_ic_n2a"].append(float(np.nanmean(e.ic_n2a)) if e.ic_n2a.size else float("nan"))
        rows["delta_n2a_point"].append(boot["delta_n2a"]["bonferroni"].point)
        rows["delta_n2a_ci_low"].append(boot["delta_n2a"]["bonferroni"].low)
        rows["n2b_point"].append(boot["n2b"]["bonferroni"].point)
        rows["n2b_ci_low"].append(boot["n2b"]["bonferroni"].low)
        for index in range(5):
            rows[f"q{index + 1}"].append(float(baskets[index]))
        rows["spread_point"].append(boot["spread"]["bonferroni"].point)
        rows["spread_ci_low"].append(boot["spread"]["bonferroni"].low)
        rows["monotonic_violations"].append(metrics.monotonic_violations(baskets))
        rows["basket_trend"].append(metrics.basket_trend(baskets))
        rows["status"].append(verdict.status)

        count = e.date_idx.shape[0]
        daily["test_id"].extend([name] * count)
        daily["date_idx"].extend(e.date_idx.tolist())
        daily["ic"].extend(e.ic.tolist())
        daily["ic_n1"].extend(e.ic_n1.tolist())
        daily["ic_n2a"].extend(e.ic_n2a.tolist())
        daily["ic_n2b"].extend(e.ic_n2b.tolist())
        daily["spread"].extend(e.spread.tolist())
        daily["valid_queries"].extend(e.valid_per_date.tolist())
        for index in range(5):
            quint["test_id"].extend([name] * count)
            quint["date_idx"].extend(e.date_idx.tolist())
            quint["quintile"].extend([index + 1] * count)
            quint["realized"].extend(e.quintiles[:, index].tolist())
            quint["mfe"].extend(e.mfe_quintiles[:, index].tolist())
            quint["mae"].extend(e.mae_quintiles[:, index].tolist())
        for index in range(SIGMA_BUCKETS):
            sigma["test_id"].extend([name] * count)
            sigma["date_idx"].extend(e.date_idx.tolist())
            sigma["tercile"].extend([index + 1] * count)
            sigma["ic"].extend(e.sigma_tercile_ic[:, index].tolist())

    order = {name: index for index, name in enumerate(sorted(evaluations))}
    tables = {
        "testid_results": pa.table({k: pa.array(v) for k, v in rows.items()}),
        "ic_daily": pa.table({k: pa.array(v) for k, v in daily.items()}),
        "quintile_daily": pa.table({k: pa.array(v) for k, v in quint.items()}),
        "sigma_daily": pa.table({k: pa.array(v) for k, v in sigma.items()})}
    digests = {
        "testid_results": {**{k: np.asarray(v, dtype=np.float64) for k, v in rows.items()
                              if k not in ("test_id", "representation", "status")},
                           "test_id_code": np.array([order[n] for n in rows["test_id"]],
                                                    dtype=np.int64)},
        "ic_daily": {**{k: np.asarray(v, dtype=np.float64) for k, v in daily.items()
                        if k != "test_id"},
                     "test_id_code": np.array([order[n] for n in daily["test_id"]],
                                              dtype=np.int64)},
        "quintile_daily": {**{k: np.asarray(v, dtype=np.float64) for k, v in quint.items()
                              if k != "test_id"},
                           "test_id_code": np.array([order[n] for n in quint["test_id"]],
                                                    dtype=np.int64)},
        "sigma_daily": {**{k: np.asarray(v, dtype=np.float64) for k, v in sigma.items()
                           if k != "test_id"},
                        "test_id_code": np.array([order[n] for n in sigma["test_id"]],
                                                 dtype=np.int64)}}
    return tables, digests


def execute(workspace_root: Path, snapshot_id: str, *, d3_dir: Path, d2_dir: Path,
            rules: AnalogRules | None = None, c_raw_root: Path | None = None,
            runs_dir: Path | None = None, log: Callable[[str], None] = print,
            combination_limit: int | None = None) -> D4Result:
    """Run GATE-D-ALPHA end to end on two frozen parents. Raises before writing on any breach."""
    started = time.perf_counter()
    rules = rules or load_rules()
    parents = load_parents(d3_dir, d2_dir)
    log(f"parents verified: D3 {parents.d3_run_id} / D2 {parents.d2_run_id}")
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges,
                                 expected=parents.freeze, c_raw_root=c_raw_root)
    pre_digest = history.freeze.d_read_digest
    grid = history.grid
    horizons = labels.horizons_of(rules.combinations)
    # The evaluated combinations go into the identity: a smoke run over a subset must not be
    # able to claim - and overwrite - the run id of the full family.
    combinations = sorted(set(rules.combinations))
    if combination_limit is not None:
        combinations = combinations[:combination_limit]

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze,
        parent=parents.d3_run_id,
        extra={**parents.as_payload(), "rules_file_sha256": artifacts.sha256_file(RULES_PATH),
               "label_value_contract": labels.LABEL_VALUE_CONTRACT,
               "eval_range": [parents.eval_range[0], parents.eval_range[1]],
               "top_k": parents.top_k,
               "statistics": {"bootstrap": "moving block over evaluable dates",
                              "block_length": resample.BLOCK_LENGTH,
                              "replicates": resample.REPLICATES, "seed": resample.SEED,
                              "ci": "percentile", "family_size": resample.FAMILY_SIZE,
                              "bonferroni_level": "1 - 0.05/14"},
               "baselines": dict(baselines.contracts()),
               "min_valid_queries_per_date": MIN_VALID_QUERIES_PER_DATE,
               "evaluated_combinations": [f"W{w}_H{h}" for w, h in combinations],
               "implementation": {"numpy": np.__version__, "pyarrow": pa.__version__,
                                  "openblas_num_threads":
                                      os.environ.get("OPENBLAS_NUM_THREADS", "unset")},
               "tests": list(parents.tests)})
    run_dir = (runs_dir or RUNS_DIR) / identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = artifacts.identity_block(history.freeze, rules.checksum)
    log(f"run {identity.run_id} -> {run_dir}")

    mark = time.perf_counter()
    panel = read_parent_tables(parents)
    query_figi = read_query_figi(parents)
    validity = compute_validity(history.panel, horizons, rules.ca_suspect_ratio)
    eligible = labels.eligibility_matrix(len(grid), len(history.tickers), {
        index: universe.evaluate(history.panel_view(index), history.membership(index),
                                 rules).eligible for index in range(len(grid))})
    forward = labels.compute_labels(history.panel, validity, eligible)
    extremes = labels.compute_extremes(history.panel, validity, horizons)
    log(f"parents read and labels built in {time.perf_counter() - mark:.1f}s")

    evaluations: dict[str, TestEvaluation] = {}
    feature_cache: dict[int, features.FeatureSet] = {}
    library_cache: dict[int, Mapping[str, np.ndarray]] = {}
    for window, horizon in combinations:
        if window not in feature_cache:
            mark = time.perf_counter()
            feature_cache[window] = features.build(history.panel, window, eligible)
            library_cache[window] = read_library(parents, window)
            log(f"W{window} features + library in {time.perf_counter() - mark:.1f}s")
        mark = time.perf_counter()
        evaluations.update(evaluate_combination(
            window=window, horizon=horizon, representations=("A", "B"), parents=parents,
            panel=panel, library=library_cache[window], feature_set=feature_cache[window],
            excess=forward.excess_for(horizon), mfe=extremes.mfe[horizon],
            mae=extremes.mae[horizon], history=history, query_figi=query_figi, rules=rules,
            log=log))
        log(f"  W{window} h{horizon} evaluated in {time.perf_counter() - mark:.1f}s")

    bootstraps: dict[str, dict] = {}
    draw_digests: dict[str, str] = {}
    for name in sorted(evaluations):
        evaluation = evaluations[name]
        if evaluation.date_idx.shape[0] == 0:
            log(f"  {name}: no evaluable date; empty intervals (D0 sends this to INCONCLUSIVE)")
            bootstraps[name] = empty_intervals()
            continue
        indices = resample.block_indices(evaluation.date_idx.shape[0], horizon=evaluation.horizon)
        draw_digests[str(evaluation.horizon)] = resample.draw_digest(indices)
        bootstraps[name] = bootstrap_test(evaluation, indices)
    verdicts = build_verdicts(evaluations, bootstraps, parents, rules)
    evaluable = max((e.date_idx.shape[0] for e in evaluations.values()), default=0)
    decision = gate.strategy_decision(verdicts, evaluable)

    tables, digest_columns = result_tables(evaluations, bootstraps, verdicts, history)
    digests: dict[str, str] = {}
    files: dict[str, Any] = {}
    for name, table in tables.items():
        files[f"{name}.parquet"] = artifacts.write_table(run_dir / f"{name}.parquet", table, stamp)
        digests[name] = artifacts.column_digest(digest_columns[name],
                                                tuple(sorted(digest_columns[name])))

    bootstrap_payload = {name: {k: v for k, v in bootstraps[name].items() if k != "_objects"}
                         for name in sorted(bootstraps)}
    gate_payload = {"conditions": {k: v for k, v in gate.CONDITIONS},
                    "decision": decision,
                    "verdicts": [v.as_dict() for v in verdicts]}
    digests["bootstrap"] = artifacts.write_json(run_dir / "bootstrap.json",
                                                {"draw_digests_by_horizon": draw_digests,
                                                 "intervals": bootstrap_payload}, stamp)["content_sha256"]
    digests["gate_results"] = artifacts.write_json(run_dir / "gate_results.json",
                                                   gate_payload, stamp)["content_sha256"]

    post_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if post_digest != pre_digest:
        raise HardFail("R2", f"INPUT_MUTATED_DURING_D4 ({pre_digest} -> {post_digest})")

    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "phase": PHASE, "run_id": identity.run_id, **parents.as_payload(),
        "freeze_identity": history.freeze.as_dict(),
        "grid": {"session_count": len(grid), "digest": grid.digest},
        "evaluation": {"eval_range": list(parents.eval_range), "top_k": parents.top_k,
                       "min_valid_queries_per_date": MIN_VALID_QUERIES_PER_DATE,
               "evaluated_combinations": [f"W{w}_H{h}" for w, h in combinations],
                       "evaluable_dates_max": evaluable},
        "sample": {name: {"evaluable_dates": int(e.date_idx.shape[0]),
                          "valid_queries": e.valid_queries, "total_queries": e.total_queries,
                          "unique_tickers": e.unique_tickers,
                          "n1_short_draws": e.n1_short_draws,
                          "n2a_short_draws": e.n2a_short_draws,
                          "n2_missing_queries": e.n2_missing_queries}
                   for name, e in sorted(evaluations.items())},
        "decision": decision,
        "content_digests": dict(sorted(digests.items())),
        "bootstrap_draw_digests": draw_digests,
        "input_read_set": {"pre": pre_digest, "post": post_digest, "match": True},
        "artifact_identity": stamp, "files": files,
        "performance": {"elapsed_seconds": round(elapsed, 1),
                        "peak_rss_mb": round(peak_rss_mb(), 1)},
    }
    return D4Result(identity, summary, verdicts, decision, digests, tables,
                    decision["decision"], run_dir)


def run_context(workspace_root: Path, snapshot_id: str) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id,
            "peak_rss_mb": round(peak_rss_mb(), 1)}


def finish(result: D4Result, context: Mapping[str, Any]) -> Path:
    """Manifests, then COMPLETE. A gate that returned FAIL still completes - it is a real result."""
    run_dir = result.run_dir
    stamp = result.summary["artifact_identity"]
    artifacts.write_json(run_dir / "run_identity.json", result.identity.as_dict(), stamp)
    artifacts.write_json(run_dir / "parent.json",
                         {k: v for k, v in result.summary.items() if k.startswith("parent_")}, stamp)
    artifacts.write_json(run_dir / "run_context.json", dict(context), stamp)
    artifacts.write_json(run_dir / "summary.json", result.summary, stamp)
    artifacts.write_complete(run_dir, {"phase": PHASE, "run_id": result.identity.run_id,
                                       "verdict": result.verdict,
                                       "identity_digest": result.identity.digest,
                                       "parent_d3_run_id": result.summary["parent_d3_run_id"],
                                       "parent_d2_run_id": result.summary["parent_d2_run_id"]},
                            stamp)
    return run_dir
