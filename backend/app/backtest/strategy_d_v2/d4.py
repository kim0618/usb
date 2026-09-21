"""D-V2A-4: join the signal to the outcome and read the screening verdict off the declaration.

Three phases were built so that this one could be honest. D1 fixed the coordinates, D2 found the
neighbours without ever reading a return, and D3 built A(q) from the neighbours' realized labels
while writing each query's own label to a file it never opened. Everything the study has
deliberately not known until now becomes knowable here, and the only protection left is that the
conditions were written down first: ``pass_fail_policy`` in ``d_v2a_rules_v1.json`` was frozen on
2026-09-20 with its checksum recorded, and this phase computes the numbers those conditions name
before any of them is compared to a threshold.

That separation is enforced in the code rather than promised in a comment. ``screening_gate``
receives a mapping of already computed statistics and the rules, and no function that produces a
statistic takes a threshold as an argument. There is no branch anywhere that can change how a
number is made according to how it came out.

The phase computes no strategy, sizes no position and simulates no fill. A PASS is a claim about
one correlation difference in one two-year window, which the declaration (§13) turns into a data
purchase question and nothing else.
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

from app.backtest.strategy_d_analog import artifacts as v1_artifacts
from app.backtest.strategy_d_analog import metrics, resample
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import DailyHistory, load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import b0_composite, evaluation_labels, pit, schema
from app.backtest.strategy_d_v2 import structure_features
from app.backtest.strategy_d_v2.config import (
    CONTRACT_DOC, FEATURE_NAMES, REPO_ROOT, RULES_PATH, V2ARules, declared_checksum, load_rules,
)
from app.backtest.strategy_d_v2.d1 import (
    assert_freeze_binding, eligibility_matrix, matrix_digest, _named_digest,
)
from app.backtest.strategy_d_v2.d2 import (
    ParentD1, PEAK_RSS_LIMIT_MB, load_parent, verify_parent_matrices,
)
from app.backtest.strategy_d_v2.d3 import (
    EVALUATION_DIGEST_COLUMNS, SIGNAL_DIGEST_COLUMNS, SIGNAL_STATUS_ORDER, ParentD2, load_parent_d2,
)
from app.backtest.strategy_d_v2.identity import RunIdentity, run_identity
from app.backtest.strategy_d_v2.models import FreezeIdentity, HardFail

PHASE = "D4"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d_v2/runs"
#: The D1 digests this phase reproduces before it uses the coordinates for anything.
REQUIRED_D1_DIGESTS = ("raw_feature_matrix", "rank_feature_matrix", "validity_mask",
                       "vector_status", "label_validity_primary", "query_sample",
                       "b0_rows", "b0_strong_rows")
#: Secondary slots that need a Top-K search this phase does not run.
DEFERRED_SECONDARY = {
    "S-1..S-4": "horizons 1/3/10/20: the embargo d + h <= D - 60 makes the neighbour set"
                " horizon dependent, so each horizon is a separate D2-scale search",
    "S-8": "leave-one-family-out ablation: four more searches over nine coordinates each",
    "S-9": "B1 random analog: V1's N1 draw over this library, 20 replicates",
    "S-10": "B2 five-feature kNN: a search over a different coordinate set",
    "S-13 (V1 S(q) part)": "needs V1's W60_H10_B artifact, which this machine's run store does"
                           " not hold (the V1 runs were produced elsewhere)",
}
QUINTILES = metrics.QUINTILE_COUNT
TERCILES = metrics.TERCILE_COUNT
#: Rolling window of the market volatility stratification in S-13. Descriptive slot, declared
#: here because the declaration names the stratifier without fixing its window.
MARKET_VOL_WINDOW = 20


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _json_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _finite(value: float) -> float | None:
    """JSON has no NaN. A statistic that does not exist is written as null, never as 0.0."""
    value = float(value)
    return value if np.isfinite(value) else None


def _finite_list(values: Sequence[float]) -> list[float | None]:
    return [_finite(v) for v in values]


@dataclass
class D4Result:
    identity: RunIdentity
    report: dict[str, Any]
    tables: dict[str, pa.Table]
    verdict: str
    screening: str
    run_dir: Path | None = None


# -- parent binding ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ParentD3:
    run_id: str
    identity_digest: str
    identity: Mapping[str, Any]
    summary: Mapping[str, Any]
    signal_digest: str
    evaluation_digest: str
    files_sha256: Mapping[str, str]
    run_dir: Path

    @property
    def signal_path(self) -> Path:
        return self.run_dir / "signal_rows.parquet"

    @property
    def evaluation_path(self) -> Path:
        return self.run_dir / "evaluation_labels.parquet"


def load_parent_d3(runs_dir: Path, run_id: str, rules: V2ARules, parent_d2: ParentD2) -> ParentD3:
    """Bind to one finished D3 run, and to the D2 run whose neighbours it summarised.

    The two digests this returns are the handshake. D3 recorded what its artifacts hashed to at
    the moment it wrote them, and D4 refuses to score anything that does not hash the same now:
    a phase that scored a signal file someone had regenerated in between would be measuring a
    different study while quoting this one's lineage.
    """
    run_dir = runs_dir / run_id
    complete_path = run_dir / "COMPLETE.json"
    if not complete_path.exists():
        raise HardFail("R2", f"{run_dir} has no COMPLETE.json: D3 did not finish its checks")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    identity = json.loads((run_dir / "identity.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if complete.get("verdict") != "PASS":
        raise HardFail("R2", f"parent {run_id} verdict is {complete.get('verdict')!r}")
    if identity.get("phase") != "D3":
        raise HardFail("R2", f"parent {run_id} is phase {identity.get('phase')!r}")
    if complete.get("identity_digest") != identity.get("identity_digest"):
        raise HardFail("R2", f"parent {run_id} identity does not match its own COMPLETE token")
    if identity.get("rules_checksum") != rules.checksum:
        raise HardFail("R1", f"parent rules checksum {identity.get('rules_checksum')}")
    if identity.get("parent") != parent_d2.identity_digest:
        raise HardFail("R2", f"D3 parent {run_id} was built on D2 {identity.get('parent')},"
                             f" not on {parent_d2.identity_digest}")
    if summary["digests"]["neighbors"] != parent_d2.neighbors_digest:
        raise HardFail("R2", "the D3 summary and the D2 token disagree on the neighbour digest")
    signal_digest = str(complete.get("signal_rows_digest"))
    evaluation_digest = str(complete.get("evaluation_labels_digest"))
    for name, token in (("signal_rows", signal_digest),
                        ("evaluation_labels", evaluation_digest)):
        if token != summary["digests"][name]:
            raise HardFail("R2", f"the D3 COMPLETE token and its summary disagree on {name}")
    files = {name: _sha256_file(run_dir / name)
             for name in ("COMPLETE.json", "identity.json", "summary.json", "parent_d1.json",
                          "parent_d2.json", "status_counts.json", "signal_rows.parquet",
                          "evaluation_labels.parquet")}
    return ParentD3(run_id, str(identity["identity_digest"]), identity, summary, signal_digest,
                    evaluation_digest, files, run_dir)


# -- the joined sample ------------------------------------------------------------------------------

#: The three columns that identify a query. Equality on all three is what "joined" means here.
KEY_COLUMNS = ("query_date_idx", "sample_rank", "query_ticker_col")


def _column(table: pa.Table, name: str) -> np.ndarray:
    """One column as numpy, with dictionary columns returned as their integer codes.

    D3 wrote ``signal_status`` and the date and ticker labels as dictionary arrays and digested
    the codes, not the strings. Reading the codes back is therefore not an optimisation: it is
    what makes a recomputed digest comparable to the recorded one.
    """
    column = table.column(name).combine_chunks()
    if pa.types.is_dictionary(column.type):
        return column.indices.to_numpy(zero_copy_only=False)
    return column.to_numpy(zero_copy_only=False)


def digest_of(table: pa.Table, order: Sequence[str]) -> str:
    """Recompute an artifact's column digest with D3's recipe (bools counted as integers)."""
    columns: dict[str, np.ndarray] = {}
    for name in order:
        values = _column(table, name)
        columns[name] = values.astype(np.int64) if values.dtype == bool else values
    return v1_artifacts.column_digest(columns, order)


@dataclass(frozen=True)
class JoinedSample:
    """One row per query: the signal, the baselines, the realized outcome, and whose it is."""

    date_idx: np.ndarray
    sample_rank: np.ndarray
    ticker_col: np.ndarray
    analog: np.ndarray
    b0: np.ndarray
    b0_strong: np.ndarray
    realized: np.ndarray
    label_valid: np.ndarray
    signal_status: np.ndarray
    mean_distance: np.ndarray
    horizon: int

    def __len__(self) -> int:
        return int(self.date_idx.shape[0])

    @property
    def usable(self) -> np.ndarray:
        """A query is scored when it has a signal and a decidable outcome, and not otherwise.

        The exclusions are the ones S1 counts: a query with a non-finite coordinate and a query
        whose Top-K could not be filled never reach a correlation, and a query whose own forward
        window is undecidable has nothing to correlate against.
        """
        ok = self.signal_status == SIGNAL_STATUS_ORDER.index("OK")
        return ok & self.label_valid & np.isfinite(self.realized) & np.isfinite(self.analog)


def join_artifacts(signal: pa.Table, evaluation: pa.Table, horizon: int) -> JoinedSample:
    """Bring the two D3 artifacts together on the query key.

    They are not merged. D3 wrote both files from the same arrays in the same order, so the join
    is an identity that must hold exactly; checking it is stricter than a merge, which would
    silently tolerate a reordering or a dropped row. A pair that fails here is a pair whose rows
    no longer describe the same queries.
    """
    if signal.num_rows != evaluation.num_rows:
        raise HardFail("R5", f"signal has {signal.num_rows} rows, evaluation {evaluation.num_rows}")
    for name in KEY_COLUMNS:
        left, right = _column(signal, name), _column(evaluation, name)
        if not np.array_equal(left, right):
            raise HardFail("R5", f"the two D3 artifacts disagree on {name} in"
                                 f" {int((left != right).sum())} rows: they are not row-aligned"
                                 " and must not be scored together")
    label = f"query_excess_return_{horizon}"
    if label not in evaluation.column_names:
        raise HardFail("R1", f"the evaluation artifact carries no {label} column")
    return JoinedSample(
        date_idx=_column(signal, "query_date_idx").astype(np.int64),
        sample_rank=_column(signal, "sample_rank").astype(np.int64),
        ticker_col=_column(signal, "query_ticker_col").astype(np.int64),
        analog=_column(signal, "analog_signal_A").astype(np.float64),
        b0=_column(signal, "b0").astype(np.float64),
        b0_strong=_column(signal, "b0_strong").astype(np.float64),
        realized=_column(evaluation, label).astype(np.float64),
        label_valid=_column(evaluation, "query_label_valid").astype(bool),
        signal_status=_column(signal, "signal_status").astype(np.int64),
        mean_distance=_column(signal, "mean_distance").astype(np.float64),
        horizon=horizon)


# -- per-date statistics -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DateGroups:
    """Row positions of each query date, in the declaration's sample order.

    Built once. Every per-date statistic in the phase - primary and secondary alike - walks these
    same groups, so a date that is evaluable for the gate is evaluable for the diagnostics too.
    """

    date_idx: np.ndarray
    rows: tuple[np.ndarray, ...]

    def __len__(self) -> int:
        return int(self.date_idx.shape[0])


def group_by_date(sample: JoinedSample, *, usable_only: bool = True) -> DateGroups:
    order = np.lexsort((sample.sample_rank, sample.date_idx))
    sorted_dates = sample.date_idx[order]
    dates, starts = np.unique(sorted_dates, return_index=True)
    ends = np.append(starts[1:], sorted_dates.shape[0])
    usable = sample.usable
    rows = []
    for start, end in zip(starts, ends):
        block = order[start:end]
        rows.append(block[usable[block]] if usable_only else block)
    return DateGroups(dates.astype(np.int64), tuple(rows))


@dataclass(frozen=True)
class DateSeries:
    """The per-date evidence: one row per evaluable date, chronological."""

    date_idx: np.ndarray
    valid_queries: np.ndarray
    ic_a: np.ndarray
    ic_b0: np.ndarray
    rows: tuple[np.ndarray, ...]
    dropped_thin: tuple[int, ...]
    dropped_degenerate: tuple[int, ...]

    @property
    def delta(self) -> np.ndarray:
        return self.ic_a - self.ic_b0

    def __len__(self) -> int:
        return int(self.date_idx.shape[0])

    def daily(self, values: np.ndarray) -> metrics.DailySeries:
        return metrics.DailySeries(self.date_idx, values)


def per_date_ic(sample: JoinedSample, groups: DateGroups, *, min_valid_queries: int,
                log: Callable[[str], None] = lambda _: None) -> DateSeries:
    """``IC_A(t)`` and ``IC_B0(t)``, each over that date's usable queries and no other set.

    Both correlations are computed on one query set per date, which is what makes ``delta(t)`` a
    paired difference rather than a comparison of two samples. A date that cannot support the
    statistic is dropped whole, never partly: dropping it for A and keeping it for B0 would put
    the difference on two different date sets.
    """
    kept_dates, kept_counts, kept_a, kept_b0, kept_rows = [], [], [], [], []
    thin: list[int] = []
    degenerate: list[int] = []
    for index, rows in zip(groups.date_idx, groups.rows):
        if rows.size < min_valid_queries:
            thin.append(int(index))
            continue
        ic_a = metrics.spearman(sample.analog[rows], sample.realized[rows])
        ic_b0 = metrics.spearman(sample.b0[rows], sample.realized[rows])
        if not (np.isfinite(ic_a) and np.isfinite(ic_b0)):
            degenerate.append(int(index))
            continue
        kept_dates.append(int(index))
        kept_counts.append(int(rows.size))
        kept_a.append(ic_a)
        kept_b0.append(ic_b0)
        kept_rows.append(rows)
    log(f"per-date IC: {len(kept_dates)} evaluable dates,"
        f" {len(thin)} thin, {len(degenerate)} degenerate")
    return DateSeries(np.asarray(kept_dates, dtype=np.int64),
                      np.asarray(kept_counts, dtype=np.int64),
                      np.asarray(kept_a, dtype=np.float64),
                      np.asarray(kept_b0, dtype=np.float64),
                      tuple(kept_rows), tuple(thin), tuple(degenerate))


def mean_ic_of(sample_values: np.ndarray, realized: np.ndarray,
               series: DateSeries) -> tuple[float, np.ndarray]:
    """Mean per-date Spearman IC of any signal over the evaluable dates the primary test uses.

    Every secondary comparison goes through this function, on the primary's date set, so a
    diagnostic can never look better than the primary by being measured on friendlier days.
    """
    per_date = np.array([metrics.spearman(sample_values[rows], realized[rows])
                         for rows in series.rows], dtype=np.float64)
    finite = per_date[np.isfinite(per_date)]
    return (float(finite.mean()) if finite.size else float("nan")), per_date


# -- bootstrap -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BootstrapResult:
    """The declared interval, the draws' digest, and the same interval under V1's spawn recipe."""

    indices: np.ndarray
    draw_digest: str
    delta: resample.Interval
    ic_a: resample.Interval
    ic_b0: resample.Interval
    delta_se: float
    alternate_delta: resample.Interval
    alternate_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {"delta": self.delta.as_dict(), "ic_a": self.ic_a.as_dict(),
                "ic_b0": self.ic_b0.as_dict(),
                "bootstrap_se_delta": _finite(self.delta_se),
                "draw_digest": self.draw_digest,
                "alternate_seed_spawn": {"delta": self.alternate_delta.as_dict(),
                                         "draw_digest": self.alternate_digest,
                                         "role": "disclosure only; the gate reads the literal"
                                                 " seed above"}}


#: V1's ``block_indices`` spawns its generator from ``[seed, horizon]``. The declaration names a
#: seed and not a spawn, so the gate reads the literal (spawn value 0) and the horizon-spawned
#: draw is reported beside it. If the two ever disagreed about S6, the result has to say so.
LITERAL_SPAWN = 0


def bootstrap(series: DateSeries, *, block_length: int, replicates: int, seed: int,
              horizon: int, alpha: float = 0.05) -> BootstrapResult:
    """Moving block bootstrap over dates, with A, B0 and delta resampled on the same days."""
    count = len(series)
    indices = resample.block_indices(count, horizon=LITERAL_SPAWN, block_length=block_length,
                                     replicates=replicates, seed=seed)
    alternate = resample.block_indices(count, horizon=horizon, block_length=block_length,
                                       replicates=replicates, seed=seed)
    means = np.mean(series.delta[indices], axis=1)
    return BootstrapResult(
        indices, resample.draw_digest(indices),
        resample.interval(series.delta, indices, alpha=alpha, level="nominal_95"),
        resample.interval(series.ic_a, indices, alpha=alpha, level="nominal_95"),
        resample.interval(series.ic_b0, indices, alpha=alpha, level="nominal_95"),
        float(np.std(means, ddof=1)),
        resample.interval(series.delta, alternate, alpha=alpha, level="nominal_95"),
        resample.draw_digest(alternate))


# -- quintiles ------------------------------------------------------------------------------------------

def quintile_profile(sample: JoinedSample, series: DateSeries, values: np.ndarray,
                     realized: np.ndarray | None = None) -> dict[str, Any]:
    """Mean realized outcome of five equal-count baskets, split inside each date then averaged.

    The declaration words S8 as "Q5 - Q1 of realized ``excess_return_5`` by A(q) quintile" without
    saying whether the baskets are cut inside a date or over the pooled sample. This study is
    per-date everywhere else - the IC, the aggregation, the bootstrap unit - and V1's quintile
    code cuts inside the date, so that is what the gate reads. The pooled cut is computed too and
    reported beside it, because the wording does permit the other reading and a reader is
    entitled to see whether the choice mattered.
    """
    outcome = sample.realized if realized is None else realized
    per_date = np.array([metrics.quintile_baskets(values[rows], outcome[rows],
                                                  sample.sample_rank[rows].astype(np.float64))
                         for rows in series.rows], dtype=np.float64)
    within = np.nanmean(per_date, axis=0) if per_date.size else np.full(QUINTILES, np.nan)
    pooled_rows = np.concatenate(series.rows) if series.rows else np.empty(0, dtype=np.int64)
    pooled = metrics.quintile_baskets(values[pooled_rows], outcome[pooled_rows],
                                      sample.sample_rank[pooled_rows].astype(np.float64))
    return {"within_date_baskets": _finite_list(within),
            "within_date_spread": _finite(within[-1] - within[0]),
            "pooled_baskets": _finite_list(pooled),
            "pooled_spread": _finite(pooled[-1] - pooled[0]),
            "monotonic_violations": metrics.monotonic_violations(within),
            "basket_trend": _finite(metrics.basket_trend(within))}


# -- the gate ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Condition:
    name: str
    holds: bool
    observed: Any
    threshold: Any
    declared: str

    def as_dict(self) -> dict[str, Any]:
        return {"holds": self.holds, "observed": self.observed, "threshold": self.threshold,
                "declared": self.declared}


def screening_gate(stats: Mapping[str, Any], rules: V2ARules) -> dict[str, Condition]:
    """S1..S8, each read from the declaration and compared against an already computed number.

    Nothing is computed here. The separation is the point: a gate that also produced its inputs
    could choose how to produce them once it saw where the threshold was.
    """
    declared = rules.raw["pass_fail_policy"]["conditions"]
    gate = rules.sample_gate
    sample = stats["sample"]
    sample_holds = (sample["evaluable_dates"] >= gate.min_evaluable_dates
                    and sample["valid_queries"] >= gate.min_valid_queries
                    and sample["unique_tickers"] >= gate.min_unique_tickers
                    and sample["insufficient_neighbors_share"] <= gate.max_insufficient_neighbor_share
                    and sample["vector_undefined_share"] <= gate.max_vector_undefined_share)
    mean_ic_a, mean_ic_b0 = stats["mean_ic_a"], stats["mean_ic_b0"]
    mean_delta = stats["mean_delta"]
    return {
        "S1": Condition("S1_sample", bool(sample_holds),
                        {k: sample[k] for k in ("evaluable_dates", "valid_queries",
                                                "unique_tickers", "insufficient_neighbors_share",
                                                "vector_undefined_share")},
                        gate.as_dict(), str(declared["S1_sample"])),
        "S2": Condition("S2_pit_violations", stats["pit_violations"] == 0,
                        stats["pit_violations"], 0, str(declared["S2_pit_violations"])),
        "S3": Condition("S3_analog_ic_point", bool(mean_ic_a > 0.0), _finite(mean_ic_a), 0.0,
                        str(declared["S3_analog_ic_point"])),
        "S4": Condition("S4_sign_guard", bool(mean_ic_a > abs(mean_ic_b0)), _finite(mean_ic_a),
                        _finite(abs(mean_ic_b0)), str(declared["S4_sign_guard"])),
        "S5": Condition("S5_delta_point", bool(mean_delta >= rules.delta_threshold),
                        _finite(mean_delta), rules.delta_threshold,
                        str(declared["S5_delta_point"])),
        "S6": Condition("S6_delta_ci_low",
                        bool(stats["delta_ci_low"] > rules.delta_ci_low_threshold),
                        _finite(stats["delta_ci_low"]), rules.delta_ci_low_threshold,
                        str(declared["S6_delta_ci_low"])),
        "S7": Condition("S7_block_stability",
                        bool(stats["blocks_delta_positive"] >= rules.block_stability_minimum
                             and stats["blocks_ic_a_positive"] >= rules.block_stability_minimum),
                        {"delta_positive_blocks": stats["blocks_delta_positive"],
                         "ic_a_positive_blocks": stats["blocks_ic_a_positive"],
                         "blocks": rules.time_blocks},
                        rules.block_stability_minimum, str(declared["S7_block_stability"])),
        "S8": Condition("S8_quintile_spread", bool(stats["quintile_spread"] > 0.0),
                        _finite(stats["quintile_spread"]), 0.0,
                        str(declared["S8_quintile_spread"])),
    }


def decide(conditions: Mapping[str, Condition], stats: Mapping[str, Any]) -> dict[str, Any]:
    """PASS / BORDERLINE / FAIL exactly as ``pass_fail_policy.decision`` words it.

    INVERSE_EFFECT is reported next to the verdict rather than instead of it. The declaration
    says a significantly negative mean is never a PASS, and keeping both labels visible stops a
    FAIL that is really a reversed signal from reading like a FAIL that is noise.
    """
    holds = {name: condition.holds for name, condition in conditions.items()}
    core = all(holds[name] for name in ("S1", "S2", "S3", "S4", "S8"))
    optional_failed = [name for name in ("S5", "S6", "S7") if not holds[name]]
    if all(holds.values()):
        verdict = "SCREENING_PASS"
    elif core and stats["mean_delta"] > 0.0 and len(optional_failed) == 1:
        verdict = "SCREENING_BORDERLINE"
    else:
        verdict = "SCREENING_FAIL"
    inverse = [name for name, high in (("mean_ic_a", stats["ic_a_ci_high"]),
                                       ("mean_delta", stats["delta_ci_high"]))
               if np.isfinite(high) and high < 0.0]
    if inverse:
        verdict = "SCREENING_FAIL"
    purchase = {"SCREENING_PASS": "YES", "SCREENING_BORDERLINE": "YES_CONDITIONAL",
                "SCREENING_FAIL": "NO"}[verdict]
    return {"screening_verdict": verdict,
            "conditions_failed": sorted(name for name, ok in holds.items() if not ok),
            "optional_failed": optional_failed,
            "inverse_effect": inverse,
            "long_data_purchase_candidate": purchase,
            "next_action": str(rules_next_action(verdict))}


def rules_next_action(verdict: str) -> str:
    return {"SCREENING_PASS": "on_PASS", "SCREENING_BORDERLINE": "on_BORDERLINE",
            "SCREENING_FAIL": "on_FAIL"}[verdict]


# -- mandatory disclosure ------------------------------------------------------------------------------------

def variance_disclosure(series: DateSeries, boot: BootstrapResult,
                        rules: V2ARules) -> dict[str, Any]:
    """``statistics.mandatory_disclosure``: whether pairing helped, and how the SE came out.

    The declaration requires this block and forbids it from touching the verdict, which is why
    the S5 and S6 thresholds were fixed without reference to the realized correlation. It is
    reported so that a FAIL can be read with its power, and a PASS with its precision.
    """
    delta = series.delta
    count = len(series)
    sd_a = float(np.std(series.ic_a, ddof=1)) if count > 1 else float("nan")
    sd_b0 = float(np.std(series.ic_b0, ddof=1)) if count > 1 else float("nan")
    sd_delta = float(np.std(delta, ddof=1)) if count > 1 else float("nan")
    rho = (float(np.corrcoef(series.ic_a, series.ic_b0)[0, 1])
           if count > 1 and np.std(series.ic_a) > 0 and np.std(series.ic_b0) > 0
           else float("nan"))
    power = rules.power_inputs
    predicted_sd = power["sd_ic"] * np.sqrt(2.0 * (1.0 - rho)) if np.isfinite(rho) else np.nan
    predicted_se = (predicted_sd / np.sqrt(power["declared_dates"]) * power["se_inflation"]
                    if np.isfinite(predicted_sd) else np.nan)
    iid_se = sd_delta / np.sqrt(count) if count else float("nan")
    return {
        "realized_rho_ic_a_ic_b0": _finite(rho),
        "sd_ic_a": _finite(sd_a), "sd_ic_b0": _finite(sd_b0), "sd_delta": _finite(sd_delta),
        "pairing_reduced_variance": bool(np.isfinite(sd_delta) and np.isfinite(sd_a)
                                         and sd_delta <= sd_a),
        "pairing_statement": ("pairing did not reduce variance: sd(delta) exceeds sd(IC_A)"
                              if np.isfinite(sd_delta) and np.isfinite(sd_a) and sd_delta > sd_a
                              else "pairing reduced variance: sd(delta) is at or below sd(IC_A)"),
        "se_realized_iid": _finite(iid_se),
        "se_realized_bootstrap": _finite(boot.delta_se),
        "se_predicted_at_realized_rho": _finite(predicted_se),
        "se_predicted_inputs": {k: float(v) for k, v in power.items()},
        "effect_on_verdict": str(rules.raw["statistics"]["disclosure_effect_on_verdict"]),
    }


# -- secondary diagnostics ---------------------------------------------------------------------------------

def _bucket_ic(sample: JoinedSample, series: DateSeries, key: np.ndarray, buckets: int,
               values: np.ndarray) -> list[float | None]:
    """Mean per-date IC inside each equal-count bucket of ``key``, cut inside the date."""
    totals = np.zeros(buckets)
    counts = np.zeros(buckets, dtype=np.int64)
    for rows in series.rows:
        assignment = metrics.bucket_by_rank(key[rows], buckets,
                                            sample.sample_rank[rows].astype(np.float64))
        for bucket in range(buckets):
            selected = rows[assignment == bucket]
            if selected.size < 2:
                continue
            ic = metrics.spearman(values[selected], sample.realized[selected])
            if np.isfinite(ic):
                totals[bucket] += ic
                counts[bucket] += 1
    return [_finite(totals[b] / counts[b]) if counts[b] else None for b in range(buckets)]


def secondary_diagnostics(sample: JoinedSample, series: DateSeries, rules: V2ARules, *,
                          rank_matrix: np.ndarray | None, rv_20: np.ndarray | None,
                          mfe: np.ndarray | None, mae: np.ndarray | None,
                          market_vol: np.ndarray | None,
                          log: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """The slots this phase can fill from its own inputs. None of them can move the verdict.

    They are reported whatever their sign, which is the declaration's requirement and also the
    only way they are worth anything: a diagnostic set that is only shown when it agrees with the
    primary is decoration.
    """
    out: dict[str, Any] = {"role": str(rules.raw["secondary_diagnostics"]["role"]),
                           "deferred": dict(DEFERRED_SECONDARY)}
    mean_ic_a = float(np.mean(series.ic_a))

    if rv_20 is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            normalized = np.where(rv_20 > 0.0, sample.realized / rv_20, np.nan)
        mean_ic, _ = mean_ic_of(sample.analog, normalized, series)
        mean_b0, _ = mean_ic_of(sample.b0, normalized, series)
        out["S-5"] = {"response": "excess_return_5 / rv_20", "mean_ic_a": _finite(mean_ic),
                      "mean_ic_b0": _finite(mean_b0), "mean_delta": _finite(mean_ic - mean_b0)}

    if rank_matrix is not None:
        univariate = {}
        for position, name in enumerate(FEATURE_NAMES):
            mean_ic, _ = mean_ic_of(rank_matrix[:, position], sample.realized, series)
            univariate[name] = {"mean_ic": _finite(mean_ic),
                                "declared_sign": int(rules.b0_signs[name]),
                                "sign_agrees": bool(np.isfinite(mean_ic)
                                                    and np.sign(mean_ic) == rules.b0_signs[name])}
        out["S-6"] = {"note": "univariate cross-sectional IC of each coordinate's percentile rank"
                              " against excess_return_5; the declared sign is the B0 sign, so a"
                              " disagreement is a wrong prior, not an error",
                      "coordinates": univariate,
                      "signs_agreeing": sum(1 for v in univariate.values() if v["sign_agrees"])}

    mean_strong, _ = mean_ic_of(sample.b0_strong, sample.realized, series)
    out["S-7"] = {"baseline": "B0-strong (the six STRONG-prior coordinates)",
                  "mean_ic_b0_strong": _finite(mean_strong),
                  "mean_delta_vs_b0_strong": _finite(mean_ic_a - mean_strong)}

    if rank_matrix is not None:
        out["S-11"] = orthogonalized_residual_ic(sample, series, rank_matrix)

    out["S-12"] = {"ic_by_mean_distance_quintile":
                       _bucket_ic(sample, series, sample.mean_distance, QUINTILES, sample.analog),
                   "note": "distance is descriptive metadata; it was never an input to A(q)"}
    if mfe is not None and mae is not None:
        out["S-12"]["mfe_by_analog_quintile"] = quintile_profile(sample, series, sample.analog,
                                                                 realized=mfe)["within_date_baskets"]
        out["S-12"]["mae_by_analog_quintile"] = quintile_profile(sample, series, sample.analog,
                                                                 realized=mae)["within_date_baskets"]

    stratification: dict[str, Any] = {
        "regime_policy": str(rules.raw["secondary_diagnostics"]["regime_policy"]),
        "time_blocks": {"ic_a": _finite_list(metrics.block_means(series.daily(series.ic_a),
                                                                 rules.time_blocks)),
                        "ic_b0": _finite_list(metrics.block_means(series.daily(series.ic_b0),
                                                                  rules.time_blocks)),
                        "delta": _finite_list(metrics.block_means(series.daily(series.delta),
                                                                  rules.time_blocks))},
    }
    if rv_20 is not None:
        stratification["ic_by_rv20_quintile"] = _bucket_ic(sample, series, rv_20, QUINTILES,
                                                           sample.analog)
    if market_vol is not None:
        stratification["ic_by_market_vol_tercile"] = market_vol_terciles(series, market_vol)
    out["S-13"] = stratification
    log(f"secondary diagnostics: {sorted(k for k in out if k.startswith('S-'))}")
    return out


def orthogonalized_residual_ic(sample: JoinedSample, series: DateSeries,
                               rank_matrix: np.ndarray) -> dict[str, Any]:
    """S-11 (B3): per date, regress rank(A) on the ten coordinates, then score the residual.

    If the analog is only a re-expression of its own coordinates, the residual carries nothing
    and its IC collapses toward zero. The regression is per date because that is the unit the
    whole study evaluates on, and it is least squares with an intercept exactly as declared.

    The declaration says "standardized coordinates" and this regresses on the percentile ranks
    themselves. That is the same regression: with an intercept in the design, OLS residuals are
    invariant under any invertible linear rescaling of the regressors, so centering and scaling
    ten columns would change the coefficients and leave the residual - the only thing scored -
    exactly where it was.
    """
    residual_ic = []
    r_squared = []
    for rows in series.rows:
        coordinates = rank_matrix[rows]
        if not np.isfinite(coordinates).all() or rows.size <= coordinates.shape[1] + 1:
            continue
        target = metrics.average_rank(sample.analog[rows])
        design = np.column_stack([np.ones(rows.size), coordinates])
        solution, *_ = np.linalg.lstsq(design, target, rcond=None)
        fitted = design @ solution
        residual = target - fitted
        total = float(((target - target.mean()) ** 2).sum())
        if total > 0.0:
            r_squared.append(1.0 - float((residual ** 2).sum()) / total)
        ic = metrics.spearman(residual, sample.realized[rows])
        if np.isfinite(ic):
            residual_ic.append(ic)
    return {"definition": "per-date OLS of rank(A(q)) on the ten coordinate ranks plus intercept;"
                          " Spearman IC of the residual against excess_return_5",
            "dates": len(residual_ic),
            "mean_residual_ic": _finite(np.mean(residual_ic)) if residual_ic else None,
            "mean_r_squared_of_A_on_coordinates": _finite(np.mean(r_squared)) if r_squared else None}


def market_vol_terciles(series: DateSeries, market_vol: np.ndarray) -> dict[str, Any]:
    """S-13: mean IC inside terciles of the market's own rolling volatility.

    The stratifier is cut over the evaluation window's dates, so it splits this window's regimes
    and makes no claim about regimes outside it - which, as the declaration says, this data
    cannot test.
    """
    values = market_vol[series.date_idx]
    usable = np.isfinite(values)
    if usable.sum() < TERCILES:
        return {"available": False}
    edges = np.quantile(values[usable], [1 / 3, 2 / 3])
    assignment = np.digitize(values, edges)
    out = []
    for tercile in range(TERCILES):
        selected = usable & (assignment == tercile)
        out.append({"dates": int(selected.sum()),
                    "mean_ic_a": _finite(np.mean(series.ic_a[selected])) if selected.any() else None,
                    "mean_delta": _finite(np.mean(series.delta[selected])) if selected.any() else None})
    return {"available": True, "window_sessions": MARKET_VOL_WINDOW,
            "definition": "rolling std of the cross-sectional median one-session close return,"
                          f" {MARKET_VOL_WINDOW} sessions, cut into terciles over the evaluation"
                          " window",
            "terciles": out}


def market_volatility(panel, eligible: np.ndarray, window: int = MARKET_VOL_WINDOW) -> np.ndarray:
    """The market series S-13 stratifies on: rolling volatility of the median close return."""
    close = np.asarray(panel.close, dtype=np.float64)
    previous = np.roll(close, 1, axis=0)
    previous[0] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = close / previous - 1.0
    returns = np.where(eligible & np.isfinite(returns), returns, np.nan)
    with np.errstate(invalid="ignore"):
        market = np.nanmedian(returns, axis=1)
    out = np.full(market.shape[0], np.nan)
    for index in range(window, market.shape[0]):
        block = market[index - window + 1: index + 1]
        block = block[np.isfinite(block)]
        if block.size == window:
            out[index] = float(np.std(block, ddof=1))
    return out


# -- artifacts ---------------------------------------------------------------------------------------------

PER_DATE_DIGEST_COLUMNS = ("date_idx", "valid_queries", "ic_a", "ic_b0", "delta")


def per_date_table(series: DateSeries, sessions: Sequence[str], blocks: int) -> pa.Table:
    """The per-date evidence, so the mean can be recomputed by anyone without rerunning D3."""
    block_of = np.zeros(len(series), dtype=np.int32)
    for index, part in enumerate(metrics.time_blocks(series.date_idx, blocks)):
        block_of[part] = index
    return pa.table({
        "date_idx": pa.array(series.date_idx.astype(np.int32)),
        "session": pa.array([sessions[i] for i in series.date_idx]),
        "time_block": pa.array(block_of),
        "valid_queries": pa.array(series.valid_queries.astype(np.int32)),
        "ic_a": pa.array(series.ic_a),
        "ic_b0": pa.array(series.ic_b0),
        "delta": pa.array(series.delta),
    })


def per_date_digest(series: DateSeries) -> str:
    return v1_artifacts.column_digest(
        {"date_idx": series.date_idx, "valid_queries": series.valid_queries,
         "ic_a": series.ic_a, "ic_b0": series.ic_b0, "delta": series.delta},
        PER_DATE_DIGEST_COLUMNS)


# -- execute ---------------------------------------------------------------------------------------------------

def execute(workspace_root: Path, snapshot_id: str, *, parent_d1_run_id: str,
            parent_d2_run_id: str, parent_d3_run_id: str, c_raw_root: Path | None = None,
            rules: V2ARules | None = None, runs_dir: Path = RUNS_DIR,
            log: Callable[[str], None] = print, secondaries: bool = True,
            bind_to_declaration: bool = True) -> D4Result:
    """Run D-V2A-4 end to end and return the screening verdict with everything behind it."""
    started = time.perf_counter()
    rules = rules or load_rules()
    feature_schema = schema.build(rules)
    horizon = rules.primary_horizon
    boot_rules = rules.bootstrap
    log(f"rules {rules.checksum[:12]} horizon {horizon} bootstrap {boot_rules}")

    parent_d1 = load_parent(runs_dir, parent_d1_run_id, rules)
    parent_d2 = load_parent_d2(runs_dir, parent_d2_run_id, rules, feature_schema, parent_d1)
    parent_d3 = load_parent_d3(runs_dir, parent_d3_run_id, rules, parent_d2)
    log(f"parents D1 {parent_d1.run_id} / D2 {parent_d2.run_id} / D3 {parent_d3.run_id}")

    # -- the two artifacts, verified before they are believed ------------------------------------
    signal_table = pq.read_table(parent_d3.signal_path)
    evaluation_table = pq.read_table(parent_d3.evaluation_path)
    signal_digest = digest_of(signal_table, SIGNAL_DIGEST_COLUMNS)
    evaluation_digest = digest_of(evaluation_table, EVALUATION_DIGEST_COLUMNS)
    if signal_digest != parent_d3.signal_digest:
        raise HardFail("R2", f"signal_rows digest {signal_digest} != parent"
                             f" {parent_d3.signal_digest}")
    if evaluation_digest != parent_d3.evaluation_digest:
        raise HardFail("R2", f"evaluation_labels digest {evaluation_digest} != parent"
                             f" {parent_d3.evaluation_digest}")
    for name, path in (("signal_rows", parent_d3.signal_path),
                       ("evaluation_labels", parent_d3.evaluation_path)):
        identity_block = v1_artifacts.read_identity(path)
        if identity_block.get("rules_checksum") != rules.checksum:
            raise HardFail("R1", f"{name} was written under rules"
                                 f" {identity_block.get('rules_checksum')}")
    sample = join_artifacts(signal_table, evaluation_table, horizon)
    log(f"joined {len(sample):,} queries on {'+'.join(KEY_COLUMNS)}")

    # -- the sample the gate counts ----------------------------------------------------------------
    groups = group_by_date(sample)
    series = per_date_ic(sample, groups, min_valid_queries=rules.min_valid_queries_per_date,
                         log=log)
    if len(series) == 0:
        raise HardFail("F4", "no evaluable dates survived the per-date statistic")
    status_counts = {name: int((sample.signal_status == index).sum())
                     for index, name in enumerate(SIGNAL_STATUS_ORDER)}
    scored_rows = np.concatenate(series.rows)
    sample_block = {
        "queries": len(sample),
        "evaluable_dates": len(series),
        "query_dates": int(groups.date_idx.shape[0]),
        "valid_queries": int(scored_rows.size),
        "unique_tickers": int(np.unique(sample.ticker_col[scored_rows]).size),
        "insufficient_neighbors_share": status_counts["INSUFFICIENT_NEIGHBORS"] / len(sample),
        "vector_undefined_share": status_counts["VECTOR_UNDEFINED"] / len(sample),
        "label_invalid_queries": int((~sample.label_valid).sum()),
        "dates_dropped_thin": list(series.dropped_thin),
        "dates_dropped_degenerate": list(series.dropped_degenerate),
        "min_valid_queries_per_date": rules.min_valid_queries_per_date,
        "valid_queries_per_date_min": int(series.valid_queries.min()),
        "valid_queries_per_date_median": int(np.median(series.valid_queries)),
    }

    # -- the primary statistic ------------------------------------------------------------------------
    boot = bootstrap(series, block_length=boot_rules["block_length"],
                     replicates=boot_rules["replicates"], seed=boot_rules["seed"],
                     horizon=horizon)
    delta_blocks = metrics.block_means(series.daily(series.delta), rules.time_blocks)
    ic_a_blocks = metrics.block_means(series.daily(series.ic_a), rules.time_blocks)
    quintiles = quintile_profile(sample, series, sample.analog)
    b0_quintiles = quintile_profile(sample, series, sample.b0)

    pit_violations = int(parent_d3.summary["pit"]["violations"])
    stats = {
        "sample": sample_block,
        "mean_ic_a": float(np.mean(series.ic_a)),
        "mean_ic_b0": float(np.mean(series.ic_b0)),
        "mean_delta": float(np.mean(series.delta)),
        "delta_ci_low": boot.delta.low,
        "delta_ci_high": boot.delta.high,
        "ic_a_ci_high": boot.ic_a.high,
        "blocks_delta_positive": int(sum(1 for value in delta_blocks if value > 0)),
        "blocks_ic_a_positive": int(sum(1 for value in ic_a_blocks if value > 0)),
        "quintile_spread": quintiles["within_date_spread"] if quintiles["within_date_spread"]
                           is not None else float("nan"),
        "pit_violations": pit_violations,
    }
    conditions = screening_gate(stats, rules)
    decision = decide(conditions, stats)
    log(f"mean IC(A) {stats['mean_ic_a']:+.5f} / IC(B0) {stats['mean_ic_b0']:+.5f}"
        f" / delta {stats['mean_delta']:+.5f} -> {decision['screening_verdict']}")

    # -- determinism of this phase's own arithmetic --------------------------------------------------
    repeat_series = per_date_ic(sample, groups, min_valid_queries=rules.min_valid_queries_per_date)
    repeat_boot = bootstrap(repeat_series, block_length=boot_rules["block_length"],
                            replicates=boot_rules["replicates"], seed=boot_rules["seed"],
                            horizon=horizon)
    determinism = {
        "per_date_digest": per_date_digest(series),
        "repeat_per_date_digest": per_date_digest(repeat_series),
        "draw_digest": boot.draw_digest,
        "repeat_draw_digest": repeat_boot.draw_digest,
        "delta_ci_identical": (repeat_boot.delta.low == boot.delta.low
                               and repeat_boot.delta.high == boot.delta.high),
    }
    determinism["per_date_identical"] = (determinism["per_date_digest"]
                                         == determinism["repeat_per_date_digest"])

    # -- secondary slots that need the panel -----------------------------------------------------------
    secondary: dict[str, Any] = {"computed": False}
    read_set = {"loaded": False}
    d1_checks: dict[str, bool] = {}
    if secondaries:
        pre_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
        history = load_daily_history(workspace_root, snapshot_id,
                                     allowed_exchanges=rules.allowed_exchanges,
                                     c_raw_root=c_raw_root)
        binding = assert_freeze_binding(history, rules) if bind_to_declaration else {"enforced": False}
        for name, value in (("freeze_digest", history.freeze.freeze_digest),
                            ("grid_digest", history.freeze.grid_digest),
                            ("d_read_digest", history.freeze.d_read_digest)):
            if parent_d3.identity["data"][name] != value:
                raise HardFail("R2", f"D3 parent {name} != loaded {value}")
        eval_start, eval_end = rules.eval_range(len(history.grid))
        eligible, _ = eligibility_matrix(history, rules, eval_end, log)
        features = structure_features.build(history.panel, eligible, rules, diagnostic_frame=False)
        validity = compute_validity(history.panel, (horizon,), rules.ca_suspect_ratio)
        excess = evaluation_labels.forward_excess(history.panel, validity, eligible, horizon, log)
        rank_matrix = features.matrix(sample.date_idx, sample.ticker_col)
        b0_here = b0_composite.build(rank_matrix, rules)
        b0_strong_here = b0_composite.strong_only(rank_matrix, rules)
        d1_checks = verify_parent_matrices(parent_d1, {
            "raw_feature_matrix": _named_digest(features.raw),
            "rank_feature_matrix": _named_digest(features.rank),
            "validity_mask": matrix_digest("defined", features.defined),
            "vector_status": matrix_digest("status", features.status),
            "label_validity_primary": matrix_digest("valid", validity.valid[horizon]),
            "query_sample": (matrix_digest("session", sample.date_idx) + ":"
                             + matrix_digest("ticker", sample.ticker_col)),
            "b0_rows": matrix_digest("b0", b0_here.values),
            "b0_strong_rows": matrix_digest("b0_strong", b0_strong_here.values),
        }, required=REQUIRED_D1_DIGESTS)
        if not np.array_equal(b0_here.values, sample.b0):
            raise HardFail("R2", "the B0 column of the D3 artifact is not the B0 these"
                                 " coordinates produce")
        realized_here = excess.gather(sample.date_idx, sample.ticker_col)
        realized_here = np.where(excess.gather_valid(sample.date_idx, sample.ticker_col),
                                 realized_here, np.nan)
        agree = ((np.isnan(realized_here) & np.isnan(sample.realized))
                 | (realized_here == sample.realized))
        if not agree.all():
            raise HardFail("R2", f"{int((~agree).sum())} evaluation labels differ from the"
                                 " labels these panels produce")
        extremes = evaluation_labels.forward_extremes(history.panel, validity, horizon)
        rv_20 = features.raw["rv_20"][sample.date_idx, sample.ticker_col]
        mfe = extremes.mfe[horizon][sample.date_idx, sample.ticker_col]
        mae = extremes.mae[horizon][sample.date_idx, sample.ticker_col]
        vol = market_volatility(history.panel, eligible)
        secondary = secondary_diagnostics(sample, series, rules, rank_matrix=rank_matrix,
                                          rv_20=rv_20, mfe=mfe, mae=mae, market_vol=vol, log=log)
        secondary["computed"] = True
        structure_features.release(features)
        post_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
        if post_read_digest != pre_read_digest:
            raise HardFail("F1", f"INPUT_MUTATED_DURING_D4: read set {pre_read_digest}"
                                 f" -> {post_read_digest}")
        read_set = {"loaded": True, "pre_digest": pre_read_digest, "post_digest": post_read_digest,
                    "identical": True, "freeze_binding": {"enforced": bind_to_declaration,
                                                          **binding}}
    else:
        secondary = secondary_diagnostics(sample, series, rules, rank_matrix=None, rv_20=None,
                                          mfe=None, mae=None, market_vol=None, log=log)
        secondary["computed"] = "PARTIAL"

    post_parents = {
        "d1": {name: _sha256_file(runs_dir / parent_d1.run_id / name)
               for name in parent_d1.files_sha256},
        "d2": {name: _sha256_file(runs_dir / parent_d2.run_id / name)
               for name in parent_d2.files_sha256},
        "d3": {name: _sha256_file(parent_d3.run_dir / name) for name in parent_d3.files_sha256},
    }
    parents_immutable = (post_parents["d1"] == dict(parent_d1.files_sha256)
                         and post_parents["d2"] == dict(parent_d2.files_sha256)
                         and post_parents["d3"] == dict(parent_d3.files_sha256))
    if not parents_immutable:
        raise HardFail("F1", "PARENT_MUTATED_DURING_D4: a parent artifact changed during the run")

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=parent_freeze(parent_d3),
        parent=parent_d3.identity_digest,
        extra={"parent_d1_run_id": parent_d1.run_id,
               "parent_d2_run_id": parent_d2.run_id,
               "parent_d3_run_id": parent_d3.run_id,
               "parent_d3_identity_digest": parent_d3.identity_digest,
               "feature_schema_digest": feature_schema.digest,
               "signal_rows_digest": signal_digest,
               "evaluation_labels_digest": evaluation_digest,
               "contract_doc_sha256": _sha256_file(CONTRACT_DOC),
               "rules_file_sha256": _sha256_file(RULES_PATH),
               "primary_horizon": horizon,
               "bootstrap": {k: int(v) for k, v in boot_rules.items()},
               "gate_name": str(rules.raw["pass_fail_policy"]["gate_name"]),
               "pit_contract": pit.PIT_CONTRACT})

    hard_checks = {
        "rules_checksum_match": rules.checksum == declared_checksum(),
        "signal_digest_match": signal_digest == parent_d3.signal_digest,
        "evaluation_digest_match": evaluation_digest == parent_d3.evaluation_digest,
        "artifacts_row_aligned": True,
        "pit_violations_zero": pit_violations == 0,
        "per_date_deterministic": determinism["per_date_identical"],
        "bootstrap_deterministic": determinism["delta_ci_identical"]
                                   and determinism["draw_digest"] == determinism["repeat_draw_digest"],
        "parents_immutable": parents_immutable,
        "d1_digests_match": all(d1_checks.values()) if d1_checks else "NOT_RECOMPUTED",
        "read_set_immutable": read_set.get("identical", "NOT_LOADED"),
        "peak_rss_within_limit": _peak_rss_mb() <= PEAK_RSS_LIMIT_MB,
    }
    blocking = [name for name, value in hard_checks.items() if value is False]
    verdict = "PASS" if not blocking else "FAIL"

    report: dict[str, Any] = {
        "phase": PHASE,
        "study_class": str(rules.raw["study_class"]),
        "strategy_id": str(rules.raw["strategy_id"]),
        "gate_name": str(rules.raw["pass_fail_policy"]["gate_name"]),
        "verdict": verdict,
        "screening": decision,
        "run_id": identity.run_id,
        "parents": {"d1": parent_d1.run_id, "d2": parent_d2.run_id, "d3": parent_d3.run_id,
                    "d3_identity_digest": parent_d3.identity_digest,
                    "files_sha256": {"d1": dict(parent_d1.files_sha256),
                                     "d2": dict(parent_d2.files_sha256),
                                     "d3": dict(parent_d3.files_sha256)}},
        "rules": {"checksum": rules.checksum, "declared_checksum": declared_checksum(),
                  "rules_file_sha256": _sha256_file(RULES_PATH),
                  "contract_doc_sha256": _sha256_file(CONTRACT_DOC)},
        "input": {"signal_rows_digest": signal_digest,
                  "evaluation_labels_digest": evaluation_digest,
                  "join_keys": list(KEY_COLUMNS), "rows": len(sample),
                  "primary_horizon": horizon,
                  "status_counts": status_counts},
        "sample": sample_block,
        "primary": {
            "definition": str(rules.raw["statistics"]["primary_metric"]),
            "test_count": int(rules.raw["statistics"]["primary_test_count"]),
            "family_wise": str(rules.raw["statistics"]["family_wise"]),
            "mean_ic_a": _finite(stats["mean_ic_a"]),
            "mean_ic_b0": _finite(stats["mean_ic_b0"]),
            "mean_delta": _finite(stats["mean_delta"]),
            "median_delta": _finite(np.median(series.delta)),
            "delta_positive_date_share": _finite(np.mean(series.delta > 0)),
            "ic_a_positive_date_share": _finite(np.mean(series.ic_a > 0)),
            "bootstrap": boot.as_dict(),
            "time_blocks": {"blocks": rules.time_blocks,
                            "delta": _finite_list(delta_blocks),
                            "ic_a": _finite_list(ic_a_blocks),
                            "ic_b0": _finite_list(metrics.block_means(series.daily(series.ic_b0),
                                                                      rules.time_blocks))},
            "quintiles_by_analog": quintiles,
            "quintiles_by_b0": b0_quintiles,
        },
        "gate": {name: condition.as_dict() for name, condition in conditions.items()},
        "disclosure": variance_disclosure(series, boot, rules),
        "secondary": secondary,
        "determinism": determinism,
        "read_set": read_set,
        "pit": {"violations": pit_violations,
                "inherited_from": parent_d3.run_id,
                "own_checks": ["artifact row alignment", "label re-derivation from the panel",
                               "B0 re-derivation from the coordinates"] if secondaries
                              else ["artifact row alignment"],
                "contract": pit.PIT_CONTRACT},
        "digests": {"per_date_ic": determinism["per_date_digest"],
                    "bootstrap_draws": boot.draw_digest,
                    "gate": _json_digest({k: v.as_dict() for k, v in conditions.items()})},
        "hard_checks": hard_checks,
        "policy": {"on_verdict": str(rules.raw["pass_fail_policy"][rules_next_action(
                       decision["screening_verdict"])]),
                   "pass_meaning": str(rules.raw["pass_fail_policy"]["pass_meaning"]),
                   "secondary_cannot_overturn":
                       str(rules.raw["pass_fail_policy"]["secondary_cannot_overturn"])},
        "performance": {"total_seconds": round(time.perf_counter() - started, 1),
                        "peak_rss_mb": _peak_rss_mb(), "peak_rss_limit_mb": PEAK_RSS_LIMIT_MB},
    }
    tables = {"per_date_ic": per_date_table(series, session_labels(signal_table),
                                            rules.time_blocks)}
    return D4Result(identity, report, tables, verdict, decision["screening_verdict"])


def session_labels(signal: pa.Table) -> list[str]:
    """The grid's session dates, taken from the dictionary D3 stamped on ``query_date``.

    Reading them here rather than reloading the panel keeps the per-date artifact writable when
    the phase runs without the secondary slots: the dates a result is indexed by should not
    depend on whether an optional diagnostic was asked for.
    """
    column = signal.column("query_date").combine_chunks()
    if not pa.types.is_dictionary(column.type):
        raise HardFail("R5", "the signal artifact's query_date is not a dictionary column")
    return [str(value) for value in column.dictionary.to_pylist()]


def parent_freeze(parent: ParentD3) -> FreezeIdentity:
    """The dataset identity, rebuilt from the parent's own identity payload.

    D4 does not have to load the panel, so it cannot produce a ``FreezeIdentity`` from the data
    the way earlier phases do. Taking it from the parent is the correct source anyway: this run
    is bound to exactly the dataset D3 was bound to, and when the panel *is* loaded for the
    secondary slots the loaded freeze is checked against these same three digests.
    """
    data = dict(parent.identity["data"])
    return FreezeIdentity(
        snapshot_id=str(data["snapshot_id"]), snapshot_sha256=str(data["snapshot_sha256"]),
        freeze_id=str(data["freeze_id"]), freeze_digest=str(data["freeze_digest"]),
        source_digest=str(data["source_digest"]), d_read_digest=str(data["d_read_digest"]),
        daily_authority=str(data["daily_authority"]), first_session=str(data["first_session"]),
        last_session=str(data["last_session"]), session_count=int(data["session_count"]),
        grid_digest=str(data["grid_digest"]))


def artifact_identity(result: D4Result) -> dict[str, str]:
    """The identity block every D4 artifact carries."""
    report, identity = result.report, result.identity
    return {"freeze_id": identity.payload["data"]["freeze_id"],
            "freeze_digest": identity.payload["data"]["freeze_digest"],
            "grid_digest": identity.payload["data"]["grid_digest"],
            "rules_checksum": report["rules"]["checksum"],
            "parent_d3_run_id": report["parents"]["d3"],
            "parent_d3_identity_digest": report["parents"]["d3_identity_digest"],
            "signal_rows_digest": report["input"]["signal_rows_digest"],
            "evaluation_labels_digest": report["input"]["evaluation_labels_digest"],
            "code_digest": identity.payload["code"]["code_digest"]}


def run_context(workspace_root: Path, snapshot_id: str,
                current_pointer: Mapping[str, Any] | None) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id,
            "current_snapshot_pointer": current_pointer}


def write_run(result: D4Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    """Write the verdict and everything it rests on. ``COMPLETE.json`` is written last.

    The screening verdict goes into its own small file as well as into the summary. A reader who
    wants to know what this study concluded should not have to parse a 40 KB report to find out,
    and a verdict that lives in one named place is harder to quote selectively.
    """
    run_dir = runs_dir / result.identity.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    block = artifact_identity(result)
    report = result.report

    files: dict[str, Any] = {}
    files["identity.json"] = v1_artifacts.write_json(
        run_dir / "identity.json", result.identity.as_dict(), block)
    files["verdict.json"] = v1_artifacts.write_json(run_dir / "verdict.json", {
        "gate_name": report["gate_name"], "screening": report["screening"],
        "primary": {k: report["primary"][k] for k in ("mean_ic_a", "mean_ic_b0", "mean_delta")},
        "gate": report["gate"], "phase_verdict": result.verdict,
        "policy": report["policy"]}, block)
    files["per_date_ic.parquet"] = v1_artifacts.write_table(
        run_dir / "per_date_ic.parquet", result.tables["per_date_ic"], block)
    files["secondary.json"] = v1_artifacts.write_json(
        run_dir / "secondary.json", report["secondary"], block)
    (run_dir / "run_context.json").write_text(
        json.dumps(dict(context), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report["artifacts"] = {name: dict(info) for name, info in files.items()}
    files["summary.json"] = v1_artifacts.write_json(run_dir / "summary.json", report, block)
    if result.verdict == "PASS":
        v1_artifacts.write_complete(run_dir, {
            "phase": PHASE, "run_id": result.identity.run_id, "verdict": result.verdict,
            "screening_verdict": result.screening,
            "identity_digest": result.identity.digest,
            "parent_d3_run_id": report["parents"]["d3"],
            "per_date_ic_digest": report["digests"]["per_date_ic"],
            "gate_digest": report["digests"]["gate"]}, block)
    result.run_dir = run_dir
    return run_dir
