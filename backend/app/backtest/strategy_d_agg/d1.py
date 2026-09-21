"""D-AGG-1: excursion geometry, PIT and determinism on V2-A's frozen A(q) - no outcome statistics.

This phase builds, for every V2-A query and for every as-of-D eligible ticker of the 221
evaluation sessions, the five-session window the D0 declaration defines (entry O(D+1), MFE_5,
MAE_5, UP10, DN10, validity) and proves that it can be built honestly: the formula, the timing,
the split basis, the missing-bar contract, the separation from the signal, and reproducibility.

It computes no rate, no lift, no selection and no verdict. Row-level UP10/DN10 flags are written
to the artifacts because D-AGG-2 needs them; no count or share of them is formed anywhere in
this module, and ``assert_no_alpha`` checks the report before it is returned.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

from app.backtest.strategy_d_agg import excursions, parent as parent_mod, pit, universe
from app.backtest.strategy_d_agg.config import (
    CONTRACT_DOC, QUERY_TABLE, RULES_PATH, RUNS_DIR, THRESHOLD_EPS, UNIVERSE_DATES_TABLE,
    UNIVERSE_TABLE, V2A_LINEAGE, V2A_RUNS_DIR, AggRules, load_rules,
)
from app.backtest.strategy_d_agg.identity import RunIdentity, code_digest, run_identity
from app.backtest.strategy_d_agg.models import (
    EXCURSION_STATUS_ORDER, MISSING_SUBCLASS_ORDER, STATUS_CODE, HardFail,
)
from app.backtest.strategy_d_analog import artifacts as v1_artifacts
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import evaluation_labels
from app.backtest.strategy_d_v2.config import REPO_ROOT
from app.backtest.strategy_d_v2.d1 import assert_freeze_binding, eligibility_matrix, matrix_digest
from app.backtest.strategy_d_v2.d3 import read_neighbors, verify_neighbors_digest
from app.backtest.strategy_d_v2.identity import code_digest as v2a_code_digest

PHASE = "D1"
PEAK_RSS_LIMIT_MB = 2048
AUDIT_DATE_COUNT = 12
V2A_DOCS_DIR = REPO_ROOT / "docs/backtest/strategy_d_v2"
V2A_PACKAGE_DIR = REPO_ROOT / "backend/app/backtest/strategy_d_v2"
QUERY_DIGEST_COLUMNS = ("query_date_idx", "sample_rank", "query_ticker_col", "entry_session_idx",
                        "entry_open", "mfe_5", "mae_5", "up10", "dn10", "excursion_valid",
                        "status", "window_bars")
UNIVERSE_DIGEST_COLUMNS = ("session_idx", "ticker_col", "entry_session_idx", "entry_open",
                           "mfe_5", "mae_5", "up10", "dn10", "excursion_valid", "status",
                           "window_bars")
#: Report keys allowed to carry numbers. Anything else numeric in the report is a firewall breach.
ALPHA_TOKENS = ("_rate", "rate_", "lift", "share_up", "share_down", "mean_mfe", "mean_mae", "median_mfe",
                "median_mae", "up10_count", "dn10_count", "tail", "top10", "top_10", "bootstrap")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _dir_sha(directory: Path) -> dict[str, str]:
    return {p.name: _sha256_file(p) for p in sorted(directory.iterdir()) if p.is_file()}


def column_digest(table: pa.Table, order: tuple[str, ...]) -> str:
    columns = {}
    for name in order:
        values = table.column(name).combine_chunks().to_numpy(zero_copy_only=False)
        columns[name] = values.astype(np.int64) if values.dtype == bool else values
    return v1_artifacts.column_digest(columns, order)


def assert_no_alpha(report: Any, where: str = "report") -> None:
    """Refuse a report that names an outcome aggregate, wherever it is nested."""
    if isinstance(report, Mapping):
        for key, value in report.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ALPHA_TOKENS):
                raise HardFail("A1", f"alpha firewall: {where}.{key} is an outcome aggregate key")
            assert_no_alpha(value, f"{where}.{key}")
    elif isinstance(report, (list, tuple)):
        for i, value in enumerate(report):
            assert_no_alpha(value, f"{where}[{i}]")


@dataclass
class D1Result:
    identity: RunIdentity
    report: dict[str, Any]
    tables: dict[str, pa.Table]
    verdict: str
    run_dir: Path | None = field(default=None)


def status_counts(status: np.ndarray) -> dict[str, int]:
    return {name: int((status == code).sum()) for code, name in enumerate(EXCURSION_STATUS_ORDER)}


def geometry_digests(ex: excursions.Excursions) -> dict[str, str]:
    return {name: matrix_digest(name, getattr(ex, name))
            for name in ("entry_open", "mfe", "mae", "up", "down", "valid", "status",
                         "window_bars", "missing_subclass")}


def execute(workspace_root: Path, snapshot_id: str, *, c_raw_root: Path | None = None,
            rules: AggRules | None = None, v2a_runs_dir: Path = V2A_RUNS_DIR,
            log: Callable[[str], None] = print) -> D1Result:
    started = time.perf_counter()
    rules = rules or load_rules()
    log(f"D-AGG rules {rules.checksum[:12]} h={rules.horizon} up={rules.up_threshold}"
        f" down={rules.down_threshold} eps={THRESHOLD_EPS!r}")

    # -- START: V2-A untouched, lineage bound ---------------------------------------------------------
    v2a_verdict = json.loads((v2a_runs_dir / V2A_LINEAGE["D4"] / "verdict.json").read_text("utf-8"))
    if v2a_verdict["screening"]["screening_verdict"] != "SCREENING_FAIL":
        raise HardFail("R2", "V2-A D4 verdict is not SCREENING_FAIL")
    pre_v2a = {"docs": _dir_sha(V2A_DOCS_DIR), "code_digest": v2a_code_digest(V2A_PACKAGE_DIR),
               "d4_run": _dir_sha(v2a_runs_dir / V2A_LINEAGE["D4"])}
    parent = parent_mod.load(rules, runs_dir=v2a_runs_dir)
    v2a_rules = parent.v2a_rules
    log(f"parent D3 {parent.d3.run_id}, A binding {parent.a_digest[:16]}")

    # -- data ------------------------------------------------------------------------------------------
    pre_read = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    history = load_daily_history(workspace_root, snapshot_id,
                                 allowed_exchanges=v2a_rules.allowed_exchanges,
                                 c_raw_root=c_raw_root)
    binding = assert_freeze_binding(history, v2a_rules)
    for name, value in (("freeze_digest", history.freeze.freeze_digest),
                        ("grid_digest", history.freeze.grid_digest),
                        ("source_digest", history.freeze.source_digest)):
        if rules.data[name] != value:
            raise HardFail("R1", f"D-AGG rules {name} != loaded {value}")
    for name, value in (("freeze_digest", history.freeze.freeze_digest),
                        ("grid_digest", history.freeze.grid_digest),
                        ("d_read_digest", history.freeze.d_read_digest)):
        if parent.d3.identity["data"][name] != value:
            raise HardFail("R2", f"parent D3 {name} != loaded {value}")
    sessions = tuple(d.isoformat() for d in history.grid.dates)
    eval_start, eval_end = v2a_rules.eval_range(len(history.grid))

    # -- as-of universe, then future validity ----------------------------------------------------------
    eligible, _ = eligibility_matrix(history, v2a_rules, eval_end, log)
    eval_block = eligible[eval_start: eval_end + 1].sum(axis=1)
    d1_universe = parent.d1.report["universe"]
    universe_checks = {
        "eligible_total_matches_v2a_d1": int(eval_block.sum()) == int(d1_universe["reason_totals"]["ELIGIBLE"]),
        "per_session_min_max_match": (int(eval_block.min()) == int(d1_universe["eligible_per_date"]["min"])
                                      and int(eval_block.max()) == int(d1_universe["eligible_per_date"]["max"])),
        "queries_eligible_as_of_their_date": bool(eligible[parent.rows.date_idx, parent.rows.ticker_col].all()),
        "queries_inside_eval_range": bool(((parent.rows.date_idx >= eval_start)
                                           & (parent.rows.date_idx <= eval_end)).all()),
    }
    if not all(universe_checks.values()):
        raise HardFail("R2", f"universe does not reproduce V2-A D1: {universe_checks}")

    geometry = {"horizon": rules.horizon, "up": rules.up_threshold, "down": rules.down_threshold,
                "ca_ratio": v2a_rules.ca_suspect_ratio}
    validity = compute_validity(history.panel, (rules.horizon,), v2a_rules.ca_suspect_ratio)
    validity_digest = matrix_digest("valid", validity.valid[rules.horizon])
    if validity_digest != parent.d1.digests["label_validity_primary"]:
        raise HardFail("R2", "h=5 label validity does not reproduce V2-A D1")
    ex = excursions.compute(history.panel, validity=validity, **geometry)
    reference = evaluation_labels.forward_extremes(history.panel, validity, rules.horizon)
    same_as_v2a = {
        "mfe_equals_v2a_forward_extremes": bool(np.array_equal(ex.mfe[ex.valid],
                                                               reference.mfe[rules.horizon][ex.valid])),
        "mae_equals_v2a_forward_extremes": bool(np.array_equal(ex.mae[ex.valid],
                                                               reference.mae[rules.horizon][ex.valid])),
    }
    del reference
    if not all(same_as_v2a.values()):
        raise HardFail("R5", f"geometry disagrees with V2-A's MFE/MAE implementation: {same_as_v2a}")
    log("geometry computed; equals V2-A forward_extremes on every valid window")

    # -- query artifact -------------------------------------------------------------------------------
    rows = parent.rows
    got = ex.gather(rows.date_idx, rows.ticker_col)
    evaluation = pq.read_table(parent.d3.evaluation_path, columns=["query_label_valid"])
    parent_label_valid = evaluation.column("query_label_valid").to_numpy(zero_copy_only=False).astype(bool)
    if not np.array_equal(got["valid"], parent_label_valid):
        raise HardFail("R2", "query excursion validity != V2-A D3 query_label_valid")
    signal_ok = rows.signal_ok
    query_table = pa.table({
        "query_date_idx": rows.date_idx, "query_date": pa.array(list(rows.dates), type=pa.string()),
        "query_ticker_col": rows.ticker_col,
        "query_ticker": pa.array(list(rows.tickers), type=pa.string()),
        "sample_rank": rows.sample_rank, "analog_signal_A": rows.analog,
        "signal_status": pa.array(list(rows.signal_status), type=pa.string()),
        "entry_session_idx": rows.date_idx + 1,
        "entry_session": pa.array([sessions[i + 1] for i in rows.date_idx], type=pa.string()),
        "entry_open": got["entry_open"], "mfe_5": got["mfe"], "mae_5": got["mae"],
        "up10": got["up"], "dn10": got["down"], "excursion_valid": got["valid"],
        "usable": got["valid"] & signal_ok, "status": got["status"],
        "window_bars": got["window_bars"], "missing_subclass": got["missing_subclass"]})
    query_geometry = {"rows": len(rows), "signal_ok": int(signal_ok.sum()),
                      "signal_not_ok": int((~signal_ok).sum()),
                      "excursion_valid": int(got["valid"].sum()),
                      "excursion_invalid": int((~got["valid"]).sum()),
                      "usable": int((got["valid"] & signal_ok).sum()),
                      "status": status_counts(got["status"]),
                      "missing_subclass": {n: int((got["missing_subclass"] == i).sum())
                                           for i, n in enumerate(MISSING_SUBCLASS_ORDER)},
                      "dates": int(np.unique(rows.date_idx).size)}

    # -- universe artifact ----------------------------------------------------------------------------
    u_session, u_ticker = universe.rows(eligible, eval_start, eval_end)
    universe_table = universe.table(u_session, u_ticker, history.panel.tickers, ex)
    u_status = universe_table.column("status").to_numpy()
    per_session = universe.per_session(u_session, u_status, eval_start, eval_end, sessions)
    universe_geometry = {"rows": int(u_session.size),
                         "excursion_valid": int(np.isin(u_status, (0, 1)).sum()),
                         "excursion_invalid": int((~np.isin(u_status, (0, 1))).sum()),
                         "status": status_counts(u_status),
                         "sessions": int(per_session.num_rows)}

    # -- PIT --------------------------------------------------------------------------------------------
    dates = pit.audit_dates(eval_start, eval_end, AUDIT_DATE_COUNT)
    geometry_pit = pit.run(history, ex, dates=dates, geometry=geometry, log=log)
    neighbors = read_neighbors(parent.d2.neighbors_path)
    verify_neighbors_digest(neighbors, parent.d2.neighbors_digest)
    top_k = v2a_rules.top_k
    if not (np.array_equal(neighbors["query_date_idx"][::top_k], rows.date_idx)
            and np.array_equal(neighbors["sample_rank"][::top_k], rows.sample_rank)):
        raise HardFail("R11", "D2 neighbour blocks are not aligned with the D3 query rows")
    firewall = []
    as_of = []
    for index in dates:
        positions = np.flatnonzero(rows.date_idx == index)
        firewall.append(pit.signal_firewall(
            history, eligible, index, geometry=geometry, positions=positions,
            artifact_analog=rows.analog[positions], neighbor_end=neighbors["neighbor_end_idx"],
            neighbor_col=neighbors["neighbor_ticker_col"], top_k=top_k,
            query_cols=rows.ticker_col[positions], base_mfe=ex.mfe[index, rows.ticker_col[positions]]))
        as_of.append({"date_idx": int(index),
                      "eligible_unchanged": pit.universe_as_of(history, v2a_rules, index, eligible[index])})
    del neighbors
    firewall_findings = [f for item in firewall for f in item["findings"]]
    as_of_failures = [item for item in as_of if not item["eligible_unchanged"]]
    pit_block = {"geometry": geometry_pit, "signal_firewall": firewall, "universe_as_of": as_of,
                 "violations": geometry_pit["violations"] + len(firewall_findings) + len(as_of_failures)}
    log(f"signal firewall {len(firewall_findings)} findings, universe as-of {len(as_of_failures)} failures")

    # -- determinism (in process) ------------------------------------------------------------------------
    first = geometry_digests(ex)
    again = excursions.compute(history.panel, **geometry)
    second = geometry_digests(again)
    del again
    digests = {"query_excursions": column_digest(query_table, QUERY_DIGEST_COLUMNS),
               "universe_excursions": column_digest(universe_table, UNIVERSE_DIGEST_COLUMNS),
               "universe_geometry": v1_artifacts.table_digest(per_session),
               "geometry": first, "a_binding": parent.a_digest,
               "label_validity_h5": validity_digest}
    determinism = {"geometry_repeat_identical": first == second}

    # -- POST -------------------------------------------------------------------------------------------
    post_read = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if post_read != pre_read:
        raise HardFail("F1", f"INPUT_MUTATED_DURING_D_AGG_1: read set {pre_read} -> {post_read}")
    post_parent_files = parent_mod.parent_files(v2a_runs_dir, V2A_LINEAGE)
    post_a = parent_mod.a_binding_digest(pq.read_table(parent.d3.signal_path),
                                         rules.signal_binding_columns)
    if post_parent_files != parent.files_sha256 or post_a != parent.a_digest:
        raise HardFail("F1", "INPUT_MUTATED_DURING_D_AGG_1: a V2-A parent changed during the run")
    post_v2a = {"docs": _dir_sha(V2A_DOCS_DIR), "code_digest": v2a_code_digest(V2A_PACKAGE_DIR),
                "d4_run": _dir_sha(v2a_runs_dir / V2A_LINEAGE["D4"])}
    if post_v2a != pre_v2a:
        raise HardFail("F1", "V2-A documents, code or D4 result changed during the run")

    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=history.freeze,
        parent=parent.d3.identity_digest,
        extra={"parent_lineage": dict(V2A_LINEAGE), "a_binding_digest": parent.a_digest,
               "query_sample_digest": parent.query_sample_digest,
               "v2a_rules_checksum": v2a_rules.checksum,
               "geometry_contract": excursions.GEOMETRY_CONTRACT,
               "threshold_eps": repr(THRESHOLD_EPS), "horizon": rules.horizon,
               "up_threshold": repr(rules.up_threshold), "down_threshold": repr(rules.down_threshold),
               "audit_dates": list(dates),
               "contract_doc_sha256": _sha256_file(CONTRACT_DOC),
               "rules_file_sha256": _sha256_file(RULES_PATH)})

    elapsed = time.perf_counter() - started
    peak = _peak_rss_mb()
    hard_checks = {
        "rules_checksum_match": True,
        "v2a_verdict_screening_fail": True,
        "parent_binding_match": True,
        "freeze_grid_read_match": True,
        "universe_reproduces_v2a_d1": all(universe_checks.values()),
        "validity_reproduces_v2a_d1": True,
        "geometry_equals_v2a_extremes": all(same_as_v2a.values()),
        "query_validity_equals_parent": True,
        "pit_violations_zero": pit_block["violations"] == 0,
        "determinism_in_process": determinism["geometry_repeat_identical"],
        "input_immutable": True,
        "v2a_unchanged": True,
        "peak_rss_within_limit": peak <= PEAK_RSS_LIMIT_MB,
    }
    verdict = "PASS" if all(hard_checks.values()) else "FAIL"
    report = {
        "phase": "D-AGG-1", "run_id": identity.run_id, "strategy_id": identity.payload["strategy_id"],
        "rules": {"checksum": rules.checksum, "v2a_checksum": v2a_rules.checksum},
        "v2a_official_verdict": "SCREENING_FAIL",
        "parent": parent.summary(),
        "freeze": {**history.freeze.as_dict(), "binding": binding},
        "read_set": {"pre": pre_read, "post": post_read, "identical": True},
        "contract": {
            "entry": "P0 = O(D+1) / F(D+1); signal at D close, D never an entry",
            "window": "grid sessions D+1..D+5 inclusive; D and D+6.. excluded from values",
            "mfe": "max(H(D+1..D+5)) / P0 - 1", "mae": "min(L(D+1..D+5)) / P0 - 1",
            "events": f"UP10 mfe >= 0.10 - {THRESHOLD_EPS!r}; DN10 mae <= -0.10 + {THRESHOLD_EPS!r};"
                      " not exclusive; no first-touch ordering",
            "split_basis": "x(t)/F(t), F cumulative over splits executed <= t (V2-A label basis)",
            "validity": "V2-A h=5: bar on D+1 with open > 0, bar on session D+5, not label CA"
                        " suspect (ratio 3.0 over opens/closes D..D+10)",
            "missing_policy": "no imputation; middle gaps stay valid (VALID_GAP) with the bars that"
                              " exist; D+5 missing is MISSING_HORIZON_BAR; D+5 past the grid is BOUNDARY",
            "delisting": "indistinguishable from a halt inside the window; both invalid when D+5 has"
                         " no bar; missing_subclass (reads later bars) is descriptive only",
        },
        "query_geometry": query_geometry,
        "universe_geometry": universe_geometry,
        "universe_checks": universe_checks,
        "equivalence": same_as_v2a,
        "near_threshold_rows_universe": excursions.eps_band_rows(ex, up=rules.up_threshold,
                                                                 down=rules.down_threshold),
        "pit": pit_block,
        "determinism": determinism,
        "digests": digests,
        "v2a_immutability": {"pre": pre_v2a, "post": post_v2a},
        "performance": {"elapsed_seconds": round(elapsed, 1), "peak_rss_mb": round(peak, 1),
                        "limit_mb": PEAK_RSS_LIMIT_MB},
        "alpha_firewall": {"outcome_aggregates_computed": False,
                           "selection_applied": False, "verdict_computed": False},
        "hard_checks": hard_checks,
        "verdict": verdict,
        "code_digest": code_digest(),
    }
    assert_no_alpha(report)
    tables = {QUERY_TABLE: query_table, UNIVERSE_TABLE: universe_table,
              UNIVERSE_DATES_TABLE: per_session}
    return D1Result(identity, report, tables, verdict)


def artifact_identity(result: D1Result) -> dict[str, str]:
    data = result.identity.payload["data"]
    return {"freeze_id": data["freeze_id"], "freeze_digest": data["freeze_digest"],
            "grid_digest": data["grid_digest"], "rules_checksum": result.report["rules"]["checksum"],
            "a_binding_digest": result.report["parent"]["a_binding_digest"],
            "parent_d3_run_id": result.report["parent"]["lineage"]["d3"],
            "code_digest": result.identity.payload["code"]["code_digest"]}


def run_context(workspace_root: Path, snapshot_id: str) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "pyarrow": pa.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id}


def write_run(result: D1Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    run_dir = runs_dir / result.identity.run_id
    if (run_dir / v1_artifacts.COMPLETE).exists():
        raise HardFail("F1", f"{run_dir} already holds a finished run; refusing to overwrite")
    run_dir.mkdir(parents=True, exist_ok=True)
    block = artifact_identity(result)
    files: dict[str, Any] = {}
    files["identity.json"] = v1_artifacts.write_json(run_dir / "identity.json",
                                                     result.identity.as_dict(), block)
    for name, table in result.tables.items():
        files[name] = v1_artifacts.write_table(run_dir / name, table, block)
    (run_dir / "run_context.json").write_text(json.dumps(dict(context), indent=2, sort_keys=True)
                                              + "\n", encoding="utf-8")
    result.report["artifacts"] = {name: dict(info) for name, info in files.items()}
    files["summary.json"] = v1_artifacts.write_json(run_dir / "summary.json", result.report, block)
    if result.verdict == "PASS":
        v1_artifacts.write_complete(run_dir, {
            "phase": "D-AGG-1", "run_id": result.identity.run_id, "verdict": result.verdict,
            "identity_digest": result.identity.digest,
            "a_binding_digest": result.report["digests"]["a_binding"],
            "query_excursions_digest": result.report["digests"]["query_excursions"],
            "universe_excursions_digest": result.report["digests"]["universe_excursions"],
            "universe_geometry_digest": result.report["digests"]["universe_geometry"]}, block)
    result.run_dir = run_dir
    return run_dir
