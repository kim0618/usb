"""D-V2A-1: can the ten structure coordinates be built, ranked and composed, point-in-time?

This phase answers a feasibility question and nothing else. It computes every coordinate on the
frozen two-year store, ranks them inside each date's own cross-section, assembles B0, measures
how much of the declared sample survives, re-derives the same values on truncated panels, and
re-checks that the input bytes never moved. It does not search for a neighbour, does not touch
a label value, and does not compute a single correlation with a forward return.

The alpha firewall of this phase is structural: no function here imports ``labels`` (the module
that turns future bars into numbers), and ``pit.assert_no_label_values`` guards the artifact
schema, so a return cannot reach an artifact even by accident.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time
from typing import Any

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel, truncate
from app.backtest.strategy_d_analog import universe
from app.backtest.strategy_d_analog.identity import query_sample
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import (
    AsOfView, DailyHistory, load_daily_history, read_set_digest,
)
from app.backtest.strategy_d_v2 import b0_composite, pit, structure_encoder, structure_features
from app.backtest.strategy_d_v2.config import (
    CONTRACT_DOC, FEATURE_NAMES, REPO_ROOT, RULES_PATH, V2ARules, declared_checksum, load_rules,
)
from app.backtest.strategy_d_v2.identity import RunIdentity, run_identity
from app.backtest.strategy_d_v2.models import (
    REASON_ORDER, VECTOR_STATUS_ORDER, HardFail, VectorStatus,
)

PHASE = "D1"
RUNS_DIR = REPO_ROOT / "data/runtime/strategy_d_v2/runs"
#: How many evenly spaced query dates the truncation audit re-derives from a cut panel.
AUDIT_DATES = 12
#: Correlation scenarios the declared power formula is re-evaluated at (contract §8.1).
RHO_SCENARIOS = (0.0, 0.5, 0.8, 0.9)
#: Two-sided 95% normal quantile and the one-sided 80% power quantile, as in the contract.
Z_TWO_SIDED_95 = 1.959963984540054
Z_POWER_80 = 0.8416212335729143


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentiles(values: Sequence[int]) -> dict[str, int]:
    if not values:
        return {}
    array = np.asarray(values)
    return {"min": int(array.min()), "p25": int(np.percentile(array, 25)),
            "median": int(np.percentile(array, 50)), "p75": int(np.percentile(array, 75)),
            "max": int(array.max()), "mean": int(round(float(array.mean())))}


def matrix_digest(name: str, values: np.ndarray) -> str:
    """sha256 over one array's little-endian bytes, tagged with its name and shape.

    NaN has a stable bit pattern here (every NaN this pipeline produces comes from numpy's own
    quiet NaN), so an undefined coordinate contributes to the digest exactly like a number does.
    """
    kind = "<f8" if values.dtype.kind == "f" else "<i8"
    digest = hashlib.sha256()
    digest.update(f"{name}\t{values.shape}\t{kind}\n".encode())
    digest.update(np.ascontiguousarray(values.astype(kind)).tobytes())
    return digest.hexdigest()


def _named_digest(items: Mapping[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name in sorted(items):
        digest.update(f"{name}\t{matrix_digest(name, items[name])}\n".encode())
    return digest.hexdigest()


@dataclass
class D1Result:
    identity: RunIdentity
    report: dict[str, Any]
    verdict: str
    run_dir: Path | None = None


def assert_freeze_binding(history: DailyHistory, rules: V2ARules) -> dict[str, Any]:
    """R2: the loaded dataset is the one the declaration names, digest for digest."""
    freeze = history.freeze
    checks = {
        "freeze_digest": (freeze.freeze_digest, rules.freeze_digest),
        "source_digest": (freeze.source_digest, rules.source_digest),
        "grid_digest": (freeze.grid_digest, rules.grid_digest),
        "session_count": (str(freeze.session_count), str(rules.grid_count)),
        "first_session": (freeze.first_session, rules.data_range[0]),
        "last_session": (freeze.last_session, rules.data_range[1]),
    }
    for name, (found, declared) in checks.items():
        if found != declared:
            raise HardFail("R2", f"{name}: loaded {found!r} != declared {declared!r}")
    return {name: found for name, (found, _) in checks.items()}


def eligibility_matrix(history: DailyHistory, rules: V2ARules, last_idx: int,
                       log: Callable[[str], None] = lambda _: None,
                       ) -> tuple[np.ndarray, dict[int, universe.Eligibility]]:
    """The universe rule at every session up to ``last_idx``, as one ``(T, N)`` mask."""
    sessions, width = history.panel.close.shape
    eligible = np.zeros((sessions, width), dtype=bool)
    per_index: dict[int, universe.Eligibility] = {}
    for index in range(0, last_idx + 1):
        result = universe.evaluate(history.panel_view(index), history.membership(index), rules)
        eligible[index] = result.eligible
        per_index[index] = result
    log(f"universe: {int(eligible.sum()):,} eligible ticker-dates over {last_idx + 1} sessions")
    return eligible, per_index


def _truncated_view(panel: Panel, index: int) -> tuple[AsOfView, np.ndarray]:
    factor, counts, _ = panel.split_arrays()
    view = AsOfView(index, panel.open, panel.high, panel.low, panel.close, panel.volume,
                    factor, counts)
    return view, panel.membership()[index]


def truncation_audit(history: DailyHistory, rules: V2ARules, features,
                     indices: Sequence[int], log: Callable[[str], None] = lambda _: None,
                     ) -> dict[str, Any]:
    """Re-derive one row from a panel that physically ends at that session (PIT #2).

    The cut removes later bars, later splits and later reference snapshots, so any coordinate
    that had peeked would change. Equality is required bit for bit, not within a tolerance.
    """
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for index in indices:
        as_of = history.grid.session(index)
        cut = truncate(history.panel, as_of)
        if len(cut.sessions) != index + 1:
            raise HardFail("R3", f"truncation at {as_of} produced {len(cut.sessions)} sessions")
        view, member = _truncated_view(cut, index)
        eligible_row = universe.evaluate(view, member, rules).eligible
        raw, zero, bar_complete = structure_features.compute_raw(cut)
        for name in FEATURE_NAMES:
            full = features.raw[name][index]
            small = raw[name][index]
            same = np.array_equal(full, small, equal_nan=True)
            checked += 1
            if not same:
                delta = np.nanmax(np.abs(full - small)) if np.isfinite(full).any() else float("nan")
                mismatches.append({"session": as_of.isoformat(), "feature": name,
                                   "max_abs_delta": float(delta)})
        if not np.array_equal(eligible_row, features.eligible[index]):
            mismatches.append({"session": as_of.isoformat(), "feature": "ELIGIBILITY",
                               "max_abs_delta": float((eligible_row != features.eligible[index]).sum())})
        # The vector rule of the audited date must hold on the cut panel alone as well.
        _, cut_defined = structure_features.vector_status(
            raw, zero, _row_mask(raw["return_5"].shape, index, eligible_row), bar_complete,
            rules.max_lookback)
        if not np.array_equal(cut_defined[index], features.defined[index]):
            mismatches.append(
                {"session": as_of.isoformat(), "feature": "VECTOR_DEFINED",
                 "max_abs_delta": float((cut_defined[index] != features.defined[index]).sum())})
    log(f"truncation audit: {len(indices)} dates, {checked} coordinate rows, "
        f"{len(mismatches)} mismatches")
    return {"dates": [history.grid.session(i).isoformat() for i in indices],
            "date_indices": list(indices), "coordinate_rows_checked": checked,
            "mismatches": mismatches, "bit_identical": not mismatches}


def _row_mask(shape: tuple[int, int], index: int, row: np.ndarray) -> np.ndarray:
    """An eligibility mask that carries the audited row and nothing else (the cut panel's rows
    before ``index`` are not re-derived here; only the audited row is compared)."""
    out = np.zeros(shape, dtype=bool)
    out[index] = row
    return out


def power_table(n_dates: int, rules: V2ARules) -> dict[str, Any]:
    """Re-evaluate the declared SE formula at the measured date count (contract §8.1).

    No alpha result enters this: the inputs are the declared noise constants and the number of
    evaluation dates the data actually yields.
    """
    inputs = rules.power_inputs
    sd_ic, inflation = inputs["sd_ic"], inputs["se_inflation"]
    threshold = rules.delta_threshold
    rows: dict[str, dict[str, float]] = {}
    for rho in RHO_SCENARIOS:
        sd_delta = sd_ic * math.sqrt(2.0 * (1.0 - rho))
        se = sd_delta / math.sqrt(n_dates) * inflation
        rows[f"rho_{rho}"] = {"sd_delta": round(sd_delta, 6), "se_delta": round(se, 6),
                              "mde_significance": round(Z_TWO_SIDED_95 * se, 6),
                              "delta80_screening": round(threshold + Z_POWER_80 * se, 6)}
    declared_mde = rules.declared_mde
    declared_delta80 = rules.declared_delta80
    drift = {key: {"mde_delta": round(rows[key]["mde_significance"] - declared_mde[key], 6),
                   "delta80_delta": round(rows[key]["delta80_screening"] - declared_delta80[key], 6)}
             for key in rows if key in declared_mde}
    return {"n_dates_measured": n_dates, "n_dates_declared": int(inputs["declared_dates"]),
            "sd_ic": sd_ic, "se_inflation": inflation, "s5_threshold": threshold,
            "scenarios": rows, "declared_mde": declared_mde, "declared_delta80": declared_delta80,
            "drift_vs_declared": drift,
            "mde_range": [min(r["mde_significance"] for r in rows.values()),
                          max(r["mde_significance"] for r in rows.values())],
            "delta80_range": [min(r["delta80_screening"] for r in rows.values()),
                              max(r["delta80_screening"] for r in rows.values())]}


def execute(workspace_root: Path, snapshot_id: str, *, c_raw_root: Path | None = None,
            rules: V2ARules | None = None, log: Callable[[str], None] = print,
            audit_dates: int = AUDIT_DATES, bind_to_declaration: bool = True) -> D1Result:
    """Run D-V2A-1 end to end. Raises ``HardFail`` on any R/F condition.

    ``bind_to_declaration`` exists for synthetic fixtures, which cannot carry the frozen store's
    digests. It is never a way to run the real study unbound: with it off the freeze check is
    recorded as failed, so such a run can only ever report FAIL.
    """
    started = time.perf_counter()
    rules = rules or load_rules()
    log(f"rules checksum {rules.checksum[:12]} (declared {declared_checksum()[:12]})")

    pre_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges, c_raw_root=c_raw_root)
    binding = assert_freeze_binding(history, rules) if bind_to_declaration else {
        "enforced": False, "freeze_digest": history.freeze.freeze_digest,
        "grid_digest": history.freeze.grid_digest}
    grid = history.grid
    load_seconds = time.perf_counter() - started
    log(f"grid {grid.session(0)}..{grid.session(len(grid) - 1)} N={len(grid)} "
        f"digest {grid.digest[:12]} ({load_seconds:.1f}s)")

    eval_start, eval_end = rules.eval_range(len(grid))
    eval_indices = tuple(range(eval_start, eval_end + 1))
    horizon = rules.primary_horizon
    lookback = rules.max_lookback
    pit.assert_volume_window_protected(lookback, rules.seasoning_sessions)
    structure_features.coordinate_lookback_check(FEATURE_NAMES, lookback)
    structure_encoder.assert_equal_weights(rules)

    eligible, per_index = eligibility_matrix(history, rules, eval_end, log)
    feature_started = time.perf_counter()
    features = structure_features.build(history.panel, eligible, rules)
    feature_seconds = time.perf_counter() - feature_started
    log(f"coordinates: {int(features.defined.sum()):,} defined vectors ({feature_seconds:.1f}s)")

    validity = compute_validity(history.panel, rules.all_horizons, rules.ca_suspect_ratio)

    # -- query sample -----------------------------------------------------------------------
    column_of = {ticker: i for i, ticker in enumerate(history.tickers)}
    sample_sessions: list[int] = []
    sample_cols: list[int] = []
    eligible_per_date: list[int] = []
    defined_per_date: list[int] = []
    reason_totals = {r.value: 0 for r in REASON_ORDER}
    reason_totals["ELIGIBLE"] = 0
    dates_below_sample: list[str] = []
    dates_below_minimum: list[str] = []
    unique_tickers: set[str] = set()
    for index in eval_indices:
        result = per_index[index]
        names = result.tickers(history.tickers)
        eligible_per_date.append(len(names))
        for key, value in result.reason_counts().items():
            reason_totals[key] += value
        chosen = query_sample(grid.session(index).isoformat(), names, rules.queries_per_date)
        unique_tickers.update(chosen)
        sample_sessions.extend([index] * len(chosen))
        sample_cols.extend(column_of[t] for t in chosen)
        defined_per_date.append(int(features.defined[index, eligible[index]].sum()))
        if len(names) < rules.queries_per_date:
            dates_below_sample.append(grid.session(index).isoformat())
        if len(names) < rules.min_valid_queries_per_date:
            dates_below_minimum.append(grid.session(index).isoformat())

    session_idx = np.asarray(sample_sessions, dtype=np.int64)
    ticker_col = np.asarray(sample_cols, dtype=np.int64)
    vectors = structure_encoder.encode(features, session_idx, ticker_col)
    query_defined = vectors.defined
    query_label_valid = validity.valid[horizon][session_idx, ticker_col]
    sampled_status = features.status[session_idx, ticker_col]
    query_status_counts = {status.value: int((sampled_status == index).sum())
                           for index, status in enumerate(VECTOR_STATUS_ORDER)}
    query_status_counts[VectorStatus.OK.value] = int(query_defined.sum())
    log(f"queries {session_idx.size:,}, vectors defined {int(query_defined.sum()):,}, "
        f"label valid (h={horizon}) {int(query_label_valid.sum()):,}")

    b0 = b0_composite.build(vectors.vectors[query_defined], rules)
    b0_strong = b0_composite.strong_only(vectors.vectors[query_defined], rules)

    # -- historical library eligibility -------------------------------------------------------
    stride_all = universe.library_end_indices(rules, 0, eval_end)
    last_library_end = eval_end - lookback - horizon
    stride_used = universe.library_end_indices(rules, 0, last_library_end)
    rows_by_end: dict[int, int] = {}
    exclusions = {"NOT_ELIGIBLE": 0, "VECTOR_UNDEFINED": 0, "LABEL_INVALID": 0}
    label_valid_primary = validity.valid[horizon]
    for end in stride_used:
        eligible_row = eligible[end]
        defined_row = features.defined[end]
        valid_row = label_valid_primary[end]
        exclusions["NOT_ELIGIBLE"] += int((~eligible_row).sum())
        exclusions["VECTOR_UNDEFINED"] += int((eligible_row & ~defined_row).sum())
        exclusions["LABEL_INVALID"] += int((eligible_row & defined_row & ~valid_row).sum())
        rows_by_end[end] = int((eligible_row & defined_row & valid_row).sum())

    ends = np.asarray(stride_used, dtype=np.int64)
    counts = np.asarray([rows_by_end[e] for e in stride_used], dtype=np.int64)
    prefix = np.cumsum(counts)
    per_query_date: list[int] = []
    for index in eval_indices:
        view = pit.EmbargoView(index, lookback, horizon)
        usable = int(np.searchsorted(ends, view.limit_idx, side="right"))
        if usable:
            view.assert_candidates(ends[:usable], "library prefix")
        per_query_date.append(int(prefix[usable - 1]) if usable else 0)
    log(f"library (h={horizon}): {int(counts.sum()):,} rows over {len(stride_used)} stride dates, "
        f"min per query date {min(per_query_date):,}")

    # -- determinism ---------------------------------------------------------------------------
    digests = {
        "raw_feature_matrix": _named_digest(features.raw),
        "validity_mask": matrix_digest("defined", features.defined),
        "vector_status": matrix_digest("status", features.status),
        "rank_feature_matrix": _named_digest(features.rank),
        "query_sample": matrix_digest("session", session_idx) + ":" + matrix_digest("ticker", ticker_col),
        "b0_rows": matrix_digest("b0", b0.values),
        "b0_strong_rows": matrix_digest("b0_strong", b0_strong.values),
        "library_eligibility": matrix_digest("end", ends) + ":" + matrix_digest("rows", counts),
        "label_validity_primary": matrix_digest("valid", label_valid_primary),
    }
    repeat_started = time.perf_counter()
    repeat = structure_features.build(history.panel, eligible, rules)
    repeat_vectors = structure_encoder.encode(repeat, session_idx, ticker_col)
    repeat_b0 = b0_composite.build(repeat_vectors.vectors[repeat_vectors.defined], rules)
    repeat_digests = {
        "raw_feature_matrix": _named_digest(repeat.raw),
        "validity_mask": matrix_digest("defined", repeat.defined),
        "vector_status": matrix_digest("status", repeat.status),
        "rank_feature_matrix": _named_digest(repeat.rank),
        "b0_rows": matrix_digest("b0", repeat_b0.values),
    }
    determinism = {name: digests[name] == value for name, value in repeat_digests.items()}
    log(f"determinism: second pass {'identical' if all(determinism.values()) else 'DIFFERS'} "
        f"({time.perf_counter() - repeat_started:.1f}s)")
    del repeat, repeat_vectors, repeat_b0

    # -- point in time -------------------------------------------------------------------------
    step = max(1, len(eval_indices) // audit_dates)
    audit_indices = tuple(eval_indices[i] for i in range(0, len(eval_indices), step))[:audit_dates]
    audit = truncation_audit(history, rules, features, audit_indices, log)

    post_read_digest = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if post_read_digest != pre_read_digest:
        raise HardFail("F1", f"INPUT_MUTATED: read-set digest {pre_read_digest} -> {post_read_digest}")

    # -- sample gate ----------------------------------------------------------------------------
    gate = rules.sample_gate
    valid_queries = int((query_defined & query_label_valid).sum())
    vector_undefined_share = float((~query_defined).sum() / query_defined.size)
    sample_checks = {
        "evaluable_dates": {"value": len(eval_indices), "threshold": gate.min_evaluable_dates,
                            "pass": len(eval_indices) >= gate.min_evaluable_dates},
        "valid_queries": {"value": valid_queries, "threshold": gate.min_valid_queries,
                          "pass": valid_queries >= gate.min_valid_queries},
        "unique_query_tickers": {"value": len(unique_tickers), "threshold": gate.min_unique_tickers,
                                 "pass": len(unique_tickers) >= gate.min_unique_tickers},
        "vector_undefined_share": {"value": round(vector_undefined_share, 6),
                                   "threshold": gate.max_vector_undefined_share,
                                   "pass": vector_undefined_share <= gate.max_vector_undefined_share},
        "insufficient_neighbors_share": {"value": None,
                                         "threshold": gate.max_insufficient_neighbor_share,
                                         "pass": None,
                                         "note": "measurable only once neighbours are searched"
                                                 " (D-V2A-2); not a D1 verdict input"},
    }

    power = power_table(len(eval_indices), rules)
    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze,
        extra={"contract_doc_sha256": _sha256_file(CONTRACT_DOC),
               "rules_file_sha256": _sha256_file(RULES_PATH),
               "eval_range": [eval_start, eval_end],
               "coordinate_lookback": lookback,
               "primary_horizon": horizon,
               "queries_per_date": rules.queries_per_date,
               "library_stride": rules.library_stride,
               "coordinates": list(FEATURE_NAMES),
               "encoder_contract": structure_encoder.ENCODER_CONTRACT,
               "b0_contract": b0_composite.B0_CONTRACT,
               "pit_contract": pit.PIT_CONTRACT})

    hard_checks = {
        "rules_checksum_match": rules.checksum == declared_checksum(),
        "freeze_binding_match": bind_to_declaration,
        "read_set_immutable": post_read_digest == pre_read_digest,
        "truncation_bit_identical": audit["bit_identical"],
        "determinism": all(determinism.values()),
        "sample_gate": all(item["pass"] for item in sample_checks.values()
                           if item["pass"] is not None),
        "alpha_firewall_clean": True,
    }
    verdict = "PASS" if all(hard_checks.values()) else "FAIL"

    report: dict[str, Any] = {
        "phase": PHASE,
        "study_class": str(rules.raw["study_class"]),
        "strategy_id": str(rules.raw["strategy_id"]),
        "verdict": verdict,
        "run_id": identity.run_id,
        "rules": {"checksum": rules.checksum, "declared_checksum": declared_checksum(),
                  "rules_file_sha256": _sha256_file(RULES_PATH),
                  "contract_doc_sha256": _sha256_file(CONTRACT_DOC)},
        "freeze_identity": history.freeze.as_dict(),
        "freeze_binding": {"enforced": bind_to_declaration, **binding},
        "grid": {"first_session": grid.session(0).isoformat(),
                 "last_session": grid.session(len(grid) - 1).isoformat(),
                 "session_count": len(grid), "digest": grid.digest,
                 "checks": {k: v for k, v in history.checks.items() if k.startswith("G")}},
        "read_set": {"pre_digest": pre_read_digest, "post_digest": post_read_digest,
                     "identical": post_read_digest == pre_read_digest},
        "coordinates": {
            "names": list(FEATURE_NAMES),
            "lookback": {name: structure_features.FEATURE_LOOKBACK[name] for name in FEATURE_NAMES},
            "max_lookback": lookback,
            "formulas": {f.name: f.formula for f in rules.features},
            "status_counts": features.status_counts(),
            "zero_denominator_counts": features.zero_denominator_counts(),
            "zero_denominator_note": "panel counts include cells with no bar; the eligible count"
                                     " is the number of usable windows a guard removed",
            "defined_vectors": int(features.defined.sum()),
            "eligible_ticker_dates": int(eligible.sum()),
            "defined_share_of_eligible": round(float(features.defined.sum() / eligible.sum()), 6),
            "rank_frame_max_difference": structure_features.rank_frame_difference(features),
            "scaling": {"formula": rules.scaling_formula, "population": rules.scaling_population,
                        "implemented_population": "date's eligible names carrying all ten"
                                                  " coordinates (D1 declared interpretation)"},
        },
        "universe": {"eval_range": [eval_start, eval_end], "eval_dates": len(eval_indices),
                     "eligible_per_date": _percentiles(eligible_per_date),
                     "vector_defined_per_date": _percentiles(defined_per_date),
                     "reason_totals": reason_totals,
                     "dates_below_queries_per_date": len(dates_below_sample),
                     "dates_below_min_valid_queries": len(dates_below_minimum)},
        "query_sample": {"dates": len(eval_indices), "rows": int(session_idx.size),
                         "unique_tickers": len(unique_tickers),
                         "vectors_defined": int(query_defined.sum()),
                         "vector_undefined": int((~query_defined).sum()),
                         "label_valid_primary": int(query_label_valid.sum()),
                         "valid_queries": valid_queries,
                         "hash": "Q|20260917|{D}|{ticker}",
                         "status_counts": query_status_counts},
        "b0": {"rows": len(b0), "signs": dict(zip(FEATURE_NAMES, b0.signs)),
               "strong_names": list(rules.b0_strong_names), "strong_rows": len(b0_strong),
               "contract": b0_composite.B0_CONTRACT,
               "future_label_access": "NO",
               "summary": {"min": round(float(b0.values.min()), 6),
                           "median": round(float(np.median(b0.values)), 6),
                           "max": round(float(b0.values.max()), 6)}},
        "library": {"horizon": horizon, "stride": rules.library_stride,
                    "stride_dates_full_grid": len(stride_all),
                    "stride_dates_usable": len(stride_used),
                    "last_usable_end_idx": last_library_end,
                    "first_end_idx": stride_used[0] if stride_used else None,
                    "last_end_idx": stride_used[-1] if stride_used else None,
                    "rows_total": int(counts.sum()),
                    "rows_per_stride_date": _percentiles([int(c) for c in counts]),
                    "exclusions": exclusions,
                    "rows_visible_per_query_date": _percentiles(per_query_date),
                    "min_rows_visible": min(per_query_date),
                    "top_k": rules.top_k,
                    "min_over_top_k": min(per_query_date) >= rules.top_k},
        "label_validity": {"horizons": list(rules.all_horizons),
                           "counts": validity.counts(),
                           "note": "validity only; no label value is computed in this phase"},
        "pit": {"truncation_audit": audit,
                "embargo_rule": f"d + h <= D - {lookback}",
                "volume_window_protected": True,
                "contract": pit.PIT_CONTRACT},
        "determinism": {"second_pass": determinism, "digests": digests},
        "sample_gate": {"thresholds": gate.as_dict(), "checks": sample_checks},
        "power": power,
        "hard_checks": hard_checks,
        "alpha_firewall": {"ic_calculated": "NO", "quintiles": "NO", "analog_search": "NO",
                           "neighbor_selection": "NO", "labels_read": "NO"},
        "timing": {"load_seconds": round(load_seconds, 1),
                   "feature_seconds": round(feature_seconds, 1),
                   "total_seconds": round(time.perf_counter() - started, 1)},
    }
    pit.assert_no_label_values(_leaf_keys(report))
    return D1Result(identity, report, verdict)


def _leaf_keys(payload: Any, prefix: str = "") -> list[str]:
    """Every key name in the report, so the firewall guard can read the schema, not the values."""
    out: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            out.append(str(key))
            out.extend(_leaf_keys(value, prefix))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            out.extend(_leaf_keys(value, prefix))
    return out


def run_context(workspace_root: Path, snapshot_id: str,
                current_pointer: Mapping[str, Any] | None) -> dict[str, Any]:
    """Non-deterministic facts, kept out of the identity digest on purpose."""
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "workspace_root": str(workspace_root),
            "snapshot_id_requested": snapshot_id, "current_snapshot_pointer": current_pointer}


def write_run(result: D1Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    """Local artifacts only. ``COMPLETE.json`` is written last and only on PASS."""
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
