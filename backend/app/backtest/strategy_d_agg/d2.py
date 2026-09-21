"""D-AGG-2: tail screening of V2-A's frozen A(q) against the D0 gate.

Reads only the authoritative D-AGG-1 artifacts for the primary statistics (no MFE/MAE is
recomputed). The panel is loaded for two reasons only: to prove the frozen read set is unchanged,
and to build the secondary inputs D0 names (rv_20 for X-6/X-7, close and excess returns for X-8).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import time
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.strategy_d_agg import excursions, gate as gate_mod, parent as parent_mod, scoring, setup
from app.backtest.strategy_d_agg.config import (
    CONTRACT_DOC, QUERY_TABLE, RULES_PATH, RUNS_DIR, UNIVERSE_DATES_TABLE, UNIVERSE_TABLE,
    V2A_LINEAGE, V2A_RUNS_DIR, AggRules, load_rules,
)
from app.backtest.strategy_d_agg.d1 import (
    QUERY_DIGEST_COLUMNS, UNIVERSE_DIGEST_COLUMNS, V2A_DOCS_DIR, V2A_PACKAGE_DIR, _dir_sha, column_digest,
)
from app.backtest.strategy_d_agg.identity import RunIdentity, code_digest, run_identity
from app.backtest.strategy_d_agg.models import HardFail
from app.backtest.strategy_d_analog import artifacts as v1_artifacts, resample
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import structure_features
from app.backtest.strategy_d_v2.d1 import eligibility_matrix
from app.backtest.strategy_d_v2.identity import code_digest as v2a_code_digest

PHASE = "D2"
D1_RUN_ID = "dagg1-e90649a1e15a"
PEAK_RSS_LIMIT_MB = 2048
BOOTSTRAP_EXPECTED = {"horizon": 5, "block_length": 20, "replicates": 10000, "seed": 20260921}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def bootstrap_params(rules: AggRules) -> dict[str, int]:
    text = rules.raw["statistics"]["bootstrap_draws"]
    found = {k: int(v) for k, v in re.findall(r"(horizon|block_length|replicates|seed)=(\d+)", text)}
    if found != BOOTSTRAP_EXPECTED:
        raise HardFail("R1", f"bootstrap parameters {found} != declared {BOOTSTRAP_EXPECTED}")
    if "block_partition(T, 4)" not in rules.raw["statistics"]["time_blocks"]:
        raise HardFail("R1", "time block rule is not block_partition(T, 4)")
    return found


def minimum_setup_rows(rules: AggRules) -> int:
    return int(rules.raw["setup"]["min_valid_setup_per_session"])


def draws_fn(params: Mapping[str, int]) -> Callable[[int], np.ndarray]:
    return lambda count: resample.block_indices(count, horizon=params["horizon"],
                                                block_length=params["block_length"],
                                                replicates=params["replicates"], seed=params["seed"])


def partition_fn(count: int):
    return resample.block_partition(count, 4)


def _col(table: pa.Table, name: str) -> np.ndarray:
    return table.column(name).combine_chunks().to_numpy(zero_copy_only=False)


@dataclass
class ParentD1:
    run_dir: Path
    complete: Mapping[str, Any]
    identity: Mapping[str, Any]
    summary: Mapping[str, Any]
    files: Mapping[str, str]
    query: pa.Table
    universe: pa.Table


def load_parent_d1(rules: AggRules, runs_dir: Path = RUNS_DIR, run_id: str = D1_RUN_ID) -> ParentD1:
    run_dir = runs_dir / run_id
    complete = v1_artifacts.load_complete(run_dir)
    identity = json.loads((run_dir / "identity.json").read_text("utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text("utf-8"))
    if complete.get("verdict") != "PASS" or complete.get("phase") != "D-AGG-1":
        raise HardFail("R2", f"{run_id} is not a PASSED D-AGG-1 run")
    if complete["identity_digest"] != identity["identity_digest"] or identity["run_id"] != run_id:
        raise HardFail("R2", "D-AGG-1 identity does not match its COMPLETE token")
    if complete["rules_checksum"] != rules.checksum or identity["rules_checksum"] != rules.checksum:
        raise HardFail("R1", "D-AGG-1 was run under other rules")
    if "alpha_firewall" not in summary or summary["alpha_firewall"].get("outcome_aggregates_computed") is not False:
        raise HardFail("R2", "D-AGG-1 alpha firewall record missing")
    files = {p.name: _sha256_file(p) for p in sorted(run_dir.iterdir()) if p.is_file()}
    query = pq.read_table(run_dir / QUERY_TABLE)
    universe = pq.read_table(run_dir / UNIVERSE_TABLE)
    if column_digest(query, QUERY_DIGEST_COLUMNS) != complete["query_excursions_digest"]:
        raise HardFail("R2", "query_excursions digest mismatch")
    if column_digest(universe, UNIVERSE_DIGEST_COLUMNS) != complete["universe_excursions_digest"]:
        raise HardFail("R2", "universe_excursions digest mismatch")
    geometry = pq.read_table(run_dir / UNIVERSE_DATES_TABLE)
    if v1_artifacts.table_digest(geometry) != complete["universe_geometry_digest"]:
        raise HardFail("R2", "universe_geometry digest mismatch")
    for name in (QUERY_TABLE, UNIVERSE_TABLE):
        if v1_artifacts.read_identity(run_dir / name).get("rules_checksum") != rules.checksum:
            raise HardFail("R1", f"{name} carries other rules")
    return ParentD1(run_dir, complete, identity, summary, files, query, universe)


@dataclass
class D2Result:
    identity: RunIdentity
    report: dict[str, Any]
    tables: dict[str, pa.Table]
    verdict: str
    screening: str
    run_dir: Path | None = field(default=None)


def execute(workspace_root: Path, snapshot_id: str, *, c_raw_root: Path | None = None,
            rules: AggRules | None = None, log: Callable[[str], None] = print) -> D2Result:
    started = time.perf_counter()
    rules = rules or load_rules()
    spec = gate_mod.spec_from_rules(rules.raw)
    params = bootstrap_params(rules)
    minimum = minimum_setup_rows(rules)
    quantile = setup.exact_fraction(rules.raw["setup"]["primary_quantile"])
    if quantile != setup.exact_fraction("0.10"):
        raise HardFail("R1", f"primary quantile {quantile}")
    runs_listing_pre = sorted(p.name for p in RUNS_DIR.iterdir())

    # -- START --------------------------------------------------------------------------------------
    d1 = load_parent_d1(rules)
    parent = parent_mod.load(rules)
    if parent.a_digest != d1.complete["a_binding_digest"]:
        raise HardFail("R2", "A binding digest differs from the D-AGG-1 token")
    v2a_now = {"docs": _dir_sha(V2A_DOCS_DIR), "code_digest": v2a_code_digest(V2A_PACKAGE_DIR),
               "d4_run": _dir_sha(V2A_RUNS_DIR / V2A_LINEAGE["D4"])}
    if v2a_now != d1.summary["v2a_immutability"]["post"]:
        raise HardFail("R2", "V2-A documents, code or D4 result changed since D-AGG-1")
    v2a_verdict = json.loads((V2A_RUNS_DIR / V2A_LINEAGE["D4"] / "verdict.json").read_text("utf-8"))
    if v2a_verdict["screening"]["screening_verdict"] != "SCREENING_FAIL":
        raise HardFail("R2", "V2-A verdict is not SCREENING_FAIL")
    pre_read = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if pre_read != d1.summary["read_set"]["post"]:
        raise HardFail("R2", f"read set {pre_read} != D-AGG-1 {d1.summary['read_set']['post']}")
    log(f"START ok: D-AGG-1 {D1_RUN_ID}, A {parent.a_digest[:12]}, read {pre_read[:12]}")

    # -- inputs ---------------------------------------------------------------------------------------
    qt, ut = d1.query, d1.universe
    if not np.array_equal(_col(qt, "analog_signal_A"), parent.rows.analog) or \
            not np.array_equal(_col(qt, "sample_rank"), parent.rows.sample_rank):
        raise HardFail("R2", "D-AGG-1 query rows do not carry the bound A(q)")
    q = scoring.Queries(
        session=_col(qt, "query_date_idx").astype(np.int64),
        ticker_col=_col(qt, "query_ticker_col").astype(np.int64),
        valid=_col(qt, "excursion_valid").astype(bool), status=_col(qt, "status").astype(np.int64),
        up=_col(qt, "up10").astype(bool), down=_col(qt, "dn10").astype(bool),
        mfe=_col(qt, "mfe_5").astype(np.float64), mae=_col(qt, "mae_5").astype(np.float64),
        sample_rank=_col(qt, "sample_rank").astype(np.int64),
        analog=_col(qt, "analog_signal_A").astype(np.float64),
        signal_ok=np.array([s == "OK" for s in qt.column("signal_status").to_pylist()], dtype=bool),
        b0=parent.signal_table.column("b0").to_numpy().astype(np.float64))
    u = scoring.Rows(
        session=_col(ut, "session_idx").astype(np.int64), ticker_col=_col(ut, "ticker_col").astype(np.int64),
        valid=_col(ut, "excursion_valid").astype(bool), status=_col(ut, "status").astype(np.int64),
        up=_col(ut, "up10").astype(bool), down=_col(ut, "dn10").astype(bool),
        mfe=_col(ut, "mfe_5").astype(np.float64), mae=_col(ut, "mae_5").astype(np.float64))
    pit_violations = int(d1.summary["pit"]["violations"])

    # -- primary and gate (before any secondary) --------------------------------------------------------
    t0 = time.perf_counter()
    prim = scoring.primary(q, u, quantile=quantile, minimum=minimum, draws_fn=draws_fn(params),
                           partition_fn=partition_fn, spec=spec, pit_violations=pit_violations)
    primary_seconds = time.perf_counter() - t0
    verdict = prim["gate"]
    log(f"gate computed: {verdict['verdict']}")
    # setup.select receives no outcome column at all (signature + import test), so the selection
    # cannot depend on validity or on an outcome; selected-before-validity counts are in detail.

    # -- secondary inputs from the frozen panel ---------------------------------------------------------
    v2a_rules = parent.v2a_rules
    history = load_daily_history(workspace_root, snapshot_id, allowed_exchanges=v2a_rules.allowed_exchanges,
                                 c_raw_root=c_raw_root)
    if history.freeze.freeze_digest != rules.data["freeze_digest"] or \
            history.freeze.grid_digest != rules.data["grid_digest"]:
        raise HardFail("R1", "freeze or grid digest differs from the declaration")
    eval_start, eval_end = v2a_rules.eval_range(len(history.grid))
    eligible, _ = eligibility_matrix(history, v2a_rules, eval_end, log)
    universe_rows_ok = bool(eligible[u.session, u.ticker_col].all()) and int(
        eligible[eval_start: eval_end + 1].sum()) == len(u)
    if not universe_rows_ok:
        raise HardFail("R2", "D-AGG-1 universe rows are not the as-of eligible universe")
    features = structure_features.build(history.panel, eligible, v2a_rules, diagnostic_frame=False)
    rv20 = features.raw["rv_20"]
    q.rv20, u.rv20 = rv20[q.session, q.ticker_col].copy(), rv20[u.session, u.ticker_col].copy()
    structure_features.release(features)
    del features, rv20
    validity = compute_validity(history.panel, (rules.horizon,), v2a_rules.ca_suspect_ratio)
    close, excess = excursions.close_and_excess(history.panel, eligible, validity, rules.horizon)
    q.close_return, q.excess_return = close[q.session, q.ticker_col], excess[q.session, q.ticker_col]
    u.close_return, u.excess_return = close[u.session, u.ticker_col], excess[u.session, u.ticker_col]
    parent_excess = pq.read_table(parent.d3.evaluation_path, columns=["query_excess_return_5"]).column(0)
    parent_excess = parent_excess.to_numpy(zero_copy_only=False)
    same = (np.isnan(parent_excess) & ~q.valid) | (parent_excess == q.excess_return)
    if not same[q.valid].all():
        raise HardFail("R2", "recomputed excess_return_5 differs from V2-A D3")
    del close, excess, validity, eligible, history

    t1 = time.perf_counter()
    sec = scoring.secondary(q, u, prim, minimum=minimum)
    secondary_seconds = time.perf_counter() - t1
    again = gate_mod.evaluate(prim["metrics"], spec)
    if again != verdict:
        raise HardFail("A2", "gate verdict changed after secondaries: secondary_cannot_overturn broken")

    # -- POST -------------------------------------------------------------------------------------------
    post_read = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if post_read != pre_read:
        raise HardFail("F1", f"INPUT_MUTATED_DURING_D_AGG_2: read set {pre_read} -> {post_read}")
    post_files = {p.name: _sha256_file(p) for p in sorted(d1.run_dir.iterdir()) if p.is_file()}
    if post_files != d1.files:
        raise HardFail("F1", "D-AGG-1 artifacts changed during D-AGG-2")
    post_a = parent_mod.a_binding_digest(pq.read_table(parent.d3.signal_path), rules.signal_binding_columns)
    if post_a != parent.a_digest or parent_mod.parent_files(V2A_RUNS_DIR, V2A_LINEAGE) != parent.files_sha256:
        raise HardFail("F1", "V2-A parent changed during D-AGG-2")
    v2a_post = {"docs": _dir_sha(V2A_DOCS_DIR), "code_digest": v2a_code_digest(V2A_PACKAGE_DIR),
                "d4_run": _dir_sha(V2A_RUNS_DIR / V2A_LINEAGE["D4"])}
    if v2a_post != v2a_now:
        raise HardFail("F1", "V2-A changed during D-AGG-2")
    runs_listing_post = sorted(p.name for p in RUNS_DIR.iterdir())
    if runs_listing_post != runs_listing_pre:
        raise HardFail("F1", f"another strategy_d_agg run appeared: {runs_listing_post}")

    # -- tables and digests -----------------------------------------------------------------------------
    chosen = prim["chosen"]
    tickers = qt.column("query_ticker").to_pylist()
    idx = np.flatnonzero(chosen)
    setup_rows = pa.table({"query_date_idx": q.session[idx], "sample_rank": q.sample_rank[idx],
                           "query_ticker_col": q.ticker_col[idx],
                           "query_ticker": pa.array([tickers[i] for i in idx], type=pa.string()),
                           "analog_signal_A": q.analog[idx], "excursion_valid": q.valid[idx],
                           "up10": q.up[idx], "dn10": q.down[idx]})
    s, c = prim["setup_stats"], prim["universe_stats"]
    session_metrics = pa.table({"session_idx": prim["sessions"], "n_setup": s.n, "k_up_setup": s.k_up,
                                "k_dn_setup": s.k_dn, "m_up_setup": s.m_up, "m_dn_setup": s.m_dn,
                                "n_universe": c.n, "k_up_universe": c.k_up, "k_dn_universe": c.k_dn,
                                "m_up_universe": c.m_up, "m_dn_universe": c.m_dn})
    digests = {
        "setup_rows": column_digest(setup_rows, ("query_date_idx", "sample_rank", "query_ticker_col",
                                                 "analog_signal_A", "excursion_valid", "up10", "dn10")),
        "session_metrics": column_digest(session_metrics, tuple(session_metrics.column_names)),
        "bootstrap_draws": resample.draw_digest(prim["draws"]),
        "metrics": hashlib.sha256(json.dumps(prim["metrics"], sort_keys=True).encode()).hexdigest(),
        "secondary": hashlib.sha256(json.dumps(sec, sort_keys=True, default=str).encode()).hexdigest(),
        "gate": hashlib.sha256(json.dumps(verdict, sort_keys=True).encode()).hexdigest(),
    }
    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=parent_mod_freeze(d1),
        parent=d1.identity["identity_digest"],
        extra={"parent_d1_run_id": D1_RUN_ID, "a_binding_digest": parent.a_digest,
               "query_excursions_digest": d1.complete["query_excursions_digest"],
               "universe_excursions_digest": d1.complete["universe_excursions_digest"],
               "v2a_lineage": dict(V2A_LINEAGE), "bootstrap": dict(params),
               "gate_name": str(rules.raw["gate"]["gate_name"]),
               "contract_doc_sha256": _sha256_file(CONTRACT_DOC), "rules_file_sha256": _sha256_file(RULES_PATH)})
    elapsed = time.perf_counter() - started
    peak = _peak_rss_mb()
    hard_checks = {"start_gate": True, "selection_before_validity": True, "secondary_cannot_overturn": True,
                   "input_immutable": True, "v2a_unchanged": True, "peak_rss_within_limit": peak <= PEAK_RSS_LIMIT_MB}
    phase_verdict = "PASS" if all(hard_checks.values()) else "FAIL"
    report = {
        "phase": "D-AGG-2", "run_id": identity.run_id, "gate_name": rules.raw["gate"]["gate_name"],
        "screening_verdict": verdict["verdict"], "phase_verdict": phase_verdict,
        "rules": {"checksum": rules.checksum}, "parent_d1": D1_RUN_ID,
        "parent_v2a": parent.summary(), "read_set": {"pre": pre_read, "post": post_read},
        "metrics": prim["metrics"], "gate": verdict, "detail": prim["detail"], "secondary": sec,
        "formulas": {k: rules.raw["metrics"]["primary"]["formula"] if k == "TL" else rules.raw["metrics"]["required"][k]
                     for k in ("TL", "DL", "NTL", "AG")},
        "conventions": scoring.__doc__.split("Conventions", 1)[1].strip(),
        "digests": digests, "hard_checks": hard_checks,
        "performance": {"elapsed_seconds": round(elapsed, 1), "primary_with_bootstrap_seconds": round(primary_seconds, 2),
                        "secondary_seconds": round(secondary_seconds, 2), "peak_rss_mb": round(peak, 1)},
        "code_digest": code_digest(),
        "next": {"D_AGG_SCREEN_PASS": rules.raw["on_verdict"]["PASS"],
                 "D_AGG_SCREEN_BORDERLINE": rules.raw["on_verdict"]["BORDERLINE"],
                 "D_AGG_SCREEN_FAIL": rules.raw["on_verdict"]["FAIL"],
                 "INVERSE_EFFECT": rules.raw["on_verdict"]["FAIL"]}[verdict["verdict"]],
    }
    tables = {"setup_rows.parquet": setup_rows, "session_metrics.parquet": session_metrics}
    return D2Result(identity, report, tables, phase_verdict, verdict["verdict"])


def parent_mod_freeze(d1: ParentD1):
    from app.backtest.strategy_d_agg.models import FreezeIdentity
    return FreezeIdentity(**d1.identity["data"])


def write_run(result: D2Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    run_dir = runs_dir / result.identity.run_id
    if (run_dir / v1_artifacts.COMPLETE).exists():
        raise HardFail("F1", f"{run_dir} already holds a finished run")
    run_dir.mkdir(parents=True, exist_ok=True)
    r = result.report
    block = {"freeze_id": result.identity.payload["data"]["freeze_id"],
             "freeze_digest": result.identity.payload["data"]["freeze_digest"],
             "grid_digest": result.identity.payload["data"]["grid_digest"],
             "rules_checksum": r["rules"]["checksum"], "parent_d1_run_id": r["parent_d1"],
             "code_digest": result.identity.payload["code"]["code_digest"]}
    files: dict[str, Any] = {}
    files["identity.json"] = v1_artifacts.write_json(run_dir / "identity.json", result.identity.as_dict(), block)
    files["parent.json"] = v1_artifacts.write_json(run_dir / "parent.json", {"d1": r["parent_d1"], "v2a": r["parent_v2a"]}, block)
    for name, table in result.tables.items():
        files[name] = v1_artifacts.write_table(run_dir / name, table, block)
    files["bootstrap.json"] = v1_artifacts.write_json(run_dir / "bootstrap.json", r["detail"]["bootstrap"] | {"draw_digest": r["digests"]["bootstrap_draws"]}, block)
    files["block_results.json"] = v1_artifacts.write_json(run_dir / "block_results.json", {"blocks": r["detail"]["blocks"]}, block)
    files["concentration.json"] = v1_artifacts.write_json(run_dir / "concentration.json", r["detail"]["concentration"], block)
    files["secondary.json"] = v1_artifacts.write_json(run_dir / "secondary.json", r["secondary"], block)
    files["gate_results.json"] = v1_artifacts.write_json(run_dir / "gate_results.json", {"metrics": r["metrics"], "gate": r["gate"]}, block)
    (run_dir / "run_context.json").write_text(json.dumps(dict(context), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    r["artifacts"] = {k: dict(v) for k, v in files.items()}
    files["summary.json"] = v1_artifacts.write_json(run_dir / "summary.json", r, block)
    if result.verdict == "PASS":
        v1_artifacts.write_complete(run_dir, {"phase": "D-AGG-2", "run_id": result.identity.run_id,
                                              "verdict": result.verdict, "screening_verdict": result.screening,
                                              "identity_digest": result.identity.digest,
                                              "gate_digest": r["digests"]["gate"]}, block)
    result.run_dir = run_dir
    return run_dir


def run_context(workspace_root: Path, snapshot_id: str) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "numpy": np.__version__, "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id}
