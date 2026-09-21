"""D-AGG-4: backtest evaluation of ``d_agg_trading_rules_v1.json`` (the last D-AGG gate on this window).

Order, enforced by the code path:

    START      contracts, parents, digests, V2-A, documents, run store
    TRADES     every D-AGG-1 universe row priced by ``backtest.execute`` (setup rows are members)
    NOISE      D3 noise-only figures reproduced from these trades; mismatch -> stop before any
               location statistic is formed (D_AGG_BACKTEST_NOISE_MISMATCH)
    PIT        execution audits on 12 fixed dates
    PRIMARY    Mean Delta, bootstrap, blocks, leave-outs, concentration, absolute expectancy
    GATE       ``evaluation4.evaluate`` (PASS / FAIL / INCONCLUSIVE_TECHNICAL, no BORDERLINE)
    SECONDARY  S1 time exit, S2 same-bar TP first, S3 cost 2x, descriptives; verdict re-derived
               afterwards and required identical
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

from app.backtest.strategy_c_selection.rules import canonical_checksum
from app.backtest.strategy_d_agg import backtest, control, evaluation4 as ev, parent as parent_mod, pit4, setup
from app.backtest.strategy_d_agg.config import DOCS_DIR, RUNS_DIR, V2A_LINEAGE, V2A_RUNS_DIR, AggRules, load_rules
from app.backtest.strategy_d_agg.d1 import V2A_DOCS_DIR, V2A_PACKAGE_DIR, _dir_sha, column_digest
from app.backtest.strategy_d_agg.d2 import D1_RUN_ID, load_parent_d1
from app.backtest.strategy_d_agg.identity import RunIdentity, code_digest, run_identity
from app.backtest.strategy_d_agg.models import FreezeIdentity, HardFail
from app.backtest.strategy_d_analog import artifacts as v1_artifacts, resample
from app.backtest.strategy_d_analog.source import load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import structure_features
from app.backtest.strategy_d_v2.d1 import eligibility_matrix
from app.backtest.strategy_d_v2.identity import code_digest as v2a_code_digest

PHASE = "D4"
D2_RUN_ID = "dagg2-588ada9e677f"
TRADING_JSON = DOCS_DIR / "d_agg_trading_rules_v1.json"
TRADING_SHA = DOCS_DIR / "d_agg_trading_rules_v1.sha256"
D3_DOC = DOCS_DIR / "D_AGG_TRADING_CONTROL_CONTRACT_V1.md"
DECISION_DOC = DOCS_DIR / "D_AGG_BORDERLINE_DECISION_V1.md"
TRACKED_DOCS = ("D_AGG_CONCEPT_V1.md", "D_AGG_REUSE_MATRIX_V1.md", "D_AGG_SCREENING_CONTRACT_V1.md",
                "D_AGG_D1_RESULTS_V1.md", "D_AGG_D2_RESULTS_V1.md", "D_AGG_BORDERLINE_DECISION_V1.md",
                "D_AGG_TRADING_CONTROL_CONTRACT_V1.md", "d_agg_rules_v1.json", "d_agg_rules_v1.sha256",
                "d_agg_trading_rules_v1.json", "d_agg_trading_rules_v1.sha256")
SECONDARY_COST = 0.0020
PEAK_RSS_LIMIT_MB = 2048


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def declared_trading_checksum() -> str:
    m = re.search(r"^canonical\s+([0-9a-f]{64})\s", TRADING_SHA.read_text("utf-8"), re.MULTILINE)
    if m is None:
        raise HardFail("R1", "trading .sha256 has no canonical line")
    return m.group(1)


def load_trading_contract() -> tuple[dict[str, Any], str]:
    raw = json.loads(TRADING_JSON.read_text("utf-8"))
    checksum = canonical_checksum(raw)
    if checksum != declared_trading_checksum():
        raise HardFail("R1", f"trading contract checksum {checksum} != declared")
    if raw["trading_rule"]["id"] != backtest.RULE_ID or raw["control"]["id"] != "D_AGG_RV20_DECILE_REWEIGHT_V1":
        raise HardFail("R1", "trading contract ids differ from the implementation")
    if "seed=2026092103" not in raw["statistics"]["draws"] or "seed=2026092100" not in raw["mde_gate"]["se_used"]:
        raise HardFail("R1", "bootstrap seeds differ from the contract")
    if "gross_return - 0.0010" not in raw["cost"]["primary"]:
        raise HardFail("R1", "primary cost differs from the contract")
    return raw, checksum


def primary_draws(count: int) -> np.ndarray:
    return resample.block_indices(count, horizon=5, block_length=20, replicates=10000, seed=ev.PRIMARY_SEED)


def noise_draws(count: int) -> np.ndarray:
    return resample.block_indices(count, horizon=5, block_length=20, replicates=10000, seed=ev.NOISE_SEED)


@dataclass
class D4Result:
    identity: RunIdentity | None
    report: dict[str, Any]
    tables: dict[str, pa.Table]
    verdict: str
    run_dir: Path | None = field(default=None)


def summarize(sd: control.SessionDelta, draws: np.ndarray) -> dict[str, Any]:
    means = sd.delta[draws].mean(axis=1)
    low, high = ev.percentile_ci(means)
    return {"mean_delta": float(sd.delta.mean()), "delta_ci": [low, high],
            "mean_setup_net": float(sd.e_s.mean()), "mean_control_net": float(sd.e_c.mean()),
            "sessions": int(sd.sessions.size)}


def execute(workspace_root: Path, snapshot_id: str, *, c_raw_root: Path | None = None,
            rules: AggRules | None = None, log: Callable[[str], None] = print) -> D4Result:
    started = time.perf_counter()
    rules = rules or load_rules()
    contract, contract_checksum = load_trading_contract()
    spec = ev.spec_from_contract(contract)
    runs_pre = sorted(p.name for p in RUNS_DIR.iterdir())
    docs_pre = {name: _sha(DOCS_DIR / name) for name in TRACKED_DOCS}

    # -- START ---------------------------------------------------------------------------------------
    d1 = load_parent_d1(rules)
    parent = parent_mod.load(rules)
    if parent.a_digest != d1.complete["a_binding_digest"]:
        raise HardFail("R2", "A binding digest mismatch")
    r2 = RUNS_DIR / D2_RUN_ID
    c2 = json.loads((r2 / "COMPLETE.json").read_text("utf-8"))
    s2 = json.loads((r2 / "summary.json").read_text("utf-8"))
    i2 = json.loads((r2 / "identity.json").read_text("utf-8"))
    d2_ok = (c2["verdict"] == "PASS" and c2["screening_verdict"] == "D_AGG_SCREEN_BORDERLINE"
             and hashlib.sha256(json.dumps(s2["gate"], sort_keys=True).encode()).hexdigest() == c2["gate_digest"]
             and i2["parent"] == d1.identity["identity_digest"])
    if not d2_ok:
        raise HardFail("R2", "D-AGG-2 run does not match its record")
    if i2["rules_file_sha256"] != docs_pre["d_agg_rules_v1.json"] or \
            i2["contract_doc_sha256"] != docs_pre["D_AGG_SCREENING_CONTRACT_V1.md"]:
        raise HardFail("R2", "D0 documents changed since D-AGG-2")
    decision_ok = "DECISION: PROCEED_LIMITED" in DECISION_DOC.read_text("utf-8")
    mde_ok = "MDE GATE = MDE_PASS" in D3_DOC.read_text("utf-8")
    if not (decision_ok and mde_ok):
        raise HardFail("R2", "borderline decision or D3 MDE record missing")
    v2a_now = {"docs": _dir_sha(V2A_DOCS_DIR), "code_digest": v2a_code_digest(V2A_PACKAGE_DIR),
               "d4_run": _dir_sha(V2A_RUNS_DIR / V2A_LINEAGE["D4"])}
    if v2a_now != d1.summary["v2a_immutability"]["post"]:
        raise HardFail("R2", "V2-A changed since D-AGG-1")
    pre_read = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    if pre_read != d1.summary["read_set"]["post"]:
        raise HardFail("R2", "read set changed")
    d1_files_pre = {p.name: _sha(p) for p in sorted(d1.run_dir.iterdir()) if p.is_file()}
    d2_files_pre = {p.name: _sha(p) for p in sorted(r2.iterdir()) if p.is_file()}
    start = {"trading_contract_checksum": contract_checksum, "d0_rules": rules.checksum,
             "d_agg_1": D1_RUN_ID, "d_agg_2": D2_RUN_ID, "borderline_decision": "PROCEED_LIMITED",
             "d3_mde_gate": "MDE_PASS", "read_digest": pre_read, "a_binding": parent.a_digest,
             "v2a_unchanged": True, "documents_sha256": docs_pre}
    log(f"START ok: contract {contract_checksum[:12]}, D-AGG-1 {D1_RUN_ID}, D-AGG-2 {D2_RUN_ID}")

    # -- data and trades ------------------------------------------------------------------------------
    history = load_daily_history(workspace_root, snapshot_id, allowed_exchanges=parent.v2a_rules.allowed_exchanges,
                                 c_raw_root=c_raw_root)
    if history.freeze.freeze_digest != rules.data["freeze_digest"] or history.freeze.grid_digest != rules.data["grid_digest"]:
        raise HardFail("R1", "freeze or grid digest mismatch")
    eval_start, eval_end = parent.v2a_rules.eval_range(len(history.grid))
    eligible, _ = eligibility_matrix(history, parent.v2a_rules, eval_end, log)
    features = structure_features.build(history.panel, eligible, parent.v2a_rules, diagnostic_frame=False)
    rv_matrix = features.raw["rv_20"].copy()
    structure_features.release(features)
    del features, eligible

    qt, ut = d1.query, d1.universe
    qs = qt.column("query_date_idx").to_numpy().astype(np.int64)
    qc = qt.column("query_ticker_col").to_numpy().astype(np.int64)
    chosen = setup.select(qs, qt.column("sample_rank").to_numpy().astype(np.int64),
                          qt.column("analog_signal_A").to_numpy().astype(np.float64),
                          np.array([x == "OK" for x in qt.column("signal_status").to_pylist()]),
                          setup.exact_fraction(rules.raw["setup"]["primary_quantile"]))
    us = ut.column("session_idx").to_numpy().astype(np.int64)
    uc = ut.column("ticker_col").to_numpy().astype(np.int64)
    uvalid = ut.column("excursion_valid").to_numpy(zero_copy_only=False).astype(bool)
    ustatus = ut.column("status").to_numpy().astype(np.int64)
    row_of = {(int(a), int(b)): i for i, (a, b) in enumerate(zip(us.tolist(), uc.tolist()))}
    setup_rows = np.array([row_of[(int(a), int(b))] for a, b in zip(qs[chosen], qc[chosen])], dtype=np.int64)
    is_setup = np.zeros(us.size, dtype=bool)
    is_setup[setup_rows] = True
    sessions = np.unique(qs)
    rv20 = rv_matrix[us, uc]
    bucket = control.buckets(us, rv20, sessions)
    prices = backtest.adjusted(history.panel)
    trades = backtest.execute(us, uc, uvalid, prices)
    net = trades.net(backtest.PRIMARY_COST)
    log(f"trades priced: {int(uvalid.sum()):,} valid rows of {us.size:,}")

    # -- NOISE REPRODUCTION (before any location statistic) --------------------------------------------
    sd = control.session_delta(us, bucket, is_setup, uvalid, net, sessions)
    measured = ev.noise(sd.delta, noise_draws(sd.sessions.size))
    reproduction = ev.noise_reproduction(measured, int(sd.sessions.size), int(sd.n_setup.sum()))
    if not reproduction["match"]:
        report = {"phase": "D-AGG-4", "verdict": "D_AGG_BACKTEST_NOISE_MISMATCH", "start": start,
                  "noise_reproduction": reproduction,
                  "note": "stopped before any location statistic was formed"}
        return D4Result(None, report, {}, "D_AGG_BACKTEST_NOISE_MISMATCH")
    log("noise reproduced: sd_delta {:.6f} SE_block {:.6f}".format(measured["sd_delta_session"], measured["se_block"]))

    # -- PIT ------------------------------------------------------------------------------------------
    pit = pit4.run(history.panel, pit4.audit_dates(eval_start, eval_end), us, uc, uvalid, rv20, bucket)
    log(f"PIT audits: {pit['violations']} violations")

    # -- PRIMARY --------------------------------------------------------------------------------------
    draws = primary_draws(sd.sessions.size)
    main = summarize(sd, draws)
    partition = resample.block_partition(sd.sessions.size, 4)
    blocks = [{"sessions": int(p.size), "first": int(sd.sessions[p[0]]), "last": int(sd.sessions[p[-1]]),
               "mean_delta": float(sd.delta[p].mean()), "mean_setup_net": float(sd.e_s[p].mean()),
               "mean_control_net": float(sd.e_c[p].mean())} for p in partition]
    top5 = ev.top_positions(sd.delta, 5)
    rest = np.setdiff1d(np.arange(sd.sessions.size), top5)
    without_top5 = float(sd.delta[rest].mean())
    cols, contrib = control.ticker_contributions(us, uc, is_setup, uvalid, net, sd)
    top10_cols = cols[ev.top_positions(contrib, 10)]
    sd_t = control.session_delta(us, bucket, is_setup, uvalid, net, sessions, keep=~np.isin(uc, top10_cols))
    without_top10 = float(sd_t.delta.mean())
    positive = contrib[contrib > 0]
    single_share = float(positive.max() / positive.sum()) if positive.size else float("nan")
    selected = int(chosen.sum())
    setup_no_trade = int((is_setup & ~uvalid).sum())
    control_rows = ~is_setup
    metrics = {
        "pit_violations": int(pit["violations"]),
        "retained_sessions": int(sd.sessions.size),
        "dropped_sessions": len(sd.dropped_thin) + len(sd.dropped_empty_bucket),
        "valid_setup_trades": int(sd.n_setup.sum()),
        "setup_no_trade_share": setup_no_trade / selected,
        "control_no_trade_share": float((control_rows & ~uvalid).sum() / control_rows.sum()),
        "mean_setup_net": main["mean_setup_net"],
        "mean_control_net": main["mean_control_net"],
        "mean_delta": main["mean_delta"],
        "delta_ci_low": main["delta_ci"][0], "delta_ci_high": main["delta_ci"][1],
        "blocks_delta_positive": int(sum(1 for b in blocks if b["mean_delta"] > 0)),
        "delta_without_top5_sessions": without_top5,
        "delta_without_top10_tickers": without_top10,
        "single_ticker_positive_share": single_share,
    }
    verdict = ev.evaluate(metrics, spec)
    log(f"gate computed: {verdict['verdict']}")

    # -- SECONDARY ------------------------------------------------------------------------------------
    s1_trades = backtest.execute(us, uc, uvalid, prices, brackets=False)
    s2_trades = backtest.execute(us, uc, uvalid, prices, same_bar="TP_FIRST")
    secondary = {
        "S1_time_exit_only": summarize(control.session_delta(us, bucket, is_setup, uvalid,
                                                             s1_trades.net(backtest.PRIMARY_COST), sessions), draws),
        "S2_same_bar_tp_first": summarize(control.session_delta(us, bucket, is_setup, uvalid,
                                                                s2_trades.net(backtest.PRIMARY_COST), sessions), draws),
        "S3_cost_2x": summarize(control.session_delta(us, bucket, is_setup, uvalid,
                                                      trades.net(SECONDARY_COST), sessions), draws),
        "secondary_cannot_overturn": True,
    }
    sv = is_setup & uvalid
    cv = control_rows & uvalid
    setup_net = net[sv]
    reasons = trades.reason[sv]
    curve = np.cumsum(sd.e_s)
    drawdown = float(np.max(np.maximum.accumulate(curve) - curve)) if curve.size else float("nan")
    descriptive = {
        "setup_trades": int(sv.sum()),
        "win_rate": float((setup_net > 0).mean()),
        "profit_factor": float(setup_net[setup_net > 0].sum() / -setup_net[setup_net < 0].sum()),
        "exit_share": {name: float((reasons == i).mean()) for i, name in enumerate(backtest.EXIT_REASONS) if i},
        "mean_gross_pooled": float(trades.gross[sv].mean()),
        "mean_net_pooled": float(setup_net.mean()),
        "median_net_pooled": float(np.median(setup_net)),
        "control_exit_share": {name: float((trades.reason[cv] == i).mean()) for i, name in enumerate(backtest.EXIT_REASONS) if i},
        "research_curve": "cumulative sum of the equal-weight session setup net return E_S(t) over retained sessions; overlapping 5-session cohorts, NOT a production portfolio",
        "research_max_drawdown": drawdown,
        "research_curve_final": float(curve[-1]) if curve.size else float("nan"),
        "top5_sessions": [int(x) for x in sd.sessions[top5]],
        "top10_ticker_cols": [int(x) for x in top10_cols],
    }
    again = ev.evaluate(metrics, spec)
    if again != verdict:
        raise HardFail("A2", "verdict changed after secondaries")

    # -- POST -----------------------------------------------------------------------------------------
    post_read = read_set_digest(workspace_root, snapshot_id, c_raw_root=c_raw_root)
    docs_post = {name: _sha(DOCS_DIR / name) for name in TRACKED_DOCS}
    d1_files_post = {p.name: _sha(p) for p in sorted(d1.run_dir.iterdir()) if p.is_file()}
    d2_files_post = {p.name: _sha(p) for p in sorted(r2.iterdir()) if p.is_file()}
    v2a_post = {"docs": _dir_sha(V2A_DOCS_DIR), "code_digest": v2a_code_digest(V2A_PACKAGE_DIR),
                "d4_run": _dir_sha(V2A_RUNS_DIR / V2A_LINEAGE["D4"])}
    immutable = (post_read == pre_read and docs_post == docs_pre and d1_files_post == d1_files_pre
                 and d2_files_post == d2_files_pre and v2a_post == v2a_now
                 and sorted(p.name for p in RUNS_DIR.iterdir()) == runs_pre)
    if not immutable:
        raise HardFail("F1", "INPUT_MUTATED_DURING_D_AGG_4")

    # -- tables ---------------------------------------------------------------------------------------
    grid = [d.isoformat() for d in history.grid.dates]
    block_of = {}
    for b, p in enumerate(partition, start=1):
        for x in sd.sessions[p]:
            block_of[int(x)] = b
    q_idx = np.flatnonzero(chosen)
    rows = setup_rows
    tickers = qt.column("query_ticker").to_pylist()
    trade_table = pa.table({
        "query_date_idx": qs[q_idx], "sample_rank": qt.column("sample_rank").to_numpy()[q_idx],
        "query_ticker_col": qc[q_idx], "ticker": pa.array([tickers[i] for i in q_idx], type=pa.string()),
        "signal_date": pa.array([grid[s] for s in qs[q_idx]], type=pa.string()),
        "entry_date": pa.array([grid[s + 1] for s in qs[q_idx]], type=pa.string()),
        "entry_price": trades.entry_price[rows],
        "exit_date": pa.array([grid[int(s) + int(k)] if k else None for s, k in zip(qs[q_idx], trades.exit_offset[rows])], type=pa.string()),
        "exit_price": trades.exit_price[rows], "exit_reason": pa.array([backtest.EXIT_REASONS[r] for r in trades.reason[rows]]),
        "gross_return": trades.gross[rows], "cost": np.where(uvalid[rows], backtest.PRIMARY_COST, np.nan),
        "net_return": net[rows], "rv20_decile": bucket[rows].astype(np.int64) + 1,
        "block_id": np.array([block_of.get(int(s), 0) for s in qs[q_idx]], dtype=np.int64),
        "trade_valid": uvalid[rows], "status": ustatus[rows]})
    session_table = pa.table({"session_idx": sd.sessions, "e_s": sd.e_s, "e_c": sd.e_c, "delta": sd.delta,
                              "n_setup": sd.n_setup, "n_control": sd.n_control})
    digests = {
        "trade_table": column_digest(trade_table, ("query_date_idx", "sample_rank", "query_ticker_col", "entry_price",
                                                   "exit_price", "gross_return", "net_return", "rv20_decile", "trade_valid")),
        "session_table": column_digest(session_table, tuple(session_table.column_names)),
        "all_row_trades": hashlib.sha256(np.ascontiguousarray(np.nan_to_num(trades.gross, nan=-9.0)).tobytes()
                                         + trades.reason.tobytes()).hexdigest(),
        "primary_draws": resample.draw_digest(draws),
        "noise_draws": resample.draw_digest(noise_draws(sd.sessions.size)),
        "metrics": hashlib.sha256(json.dumps(metrics, sort_keys=True).encode()).hexdigest(),
        "secondary": hashlib.sha256(json.dumps(secondary, sort_keys=True).encode()).hexdigest(),
        "gate": hashlib.sha256(json.dumps(verdict, sort_keys=True).encode()).hexdigest(),
    }
    identity = run_identity(
        phase=PHASE, rules_checksum=rules.checksum, freeze=FreezeIdentity(**d1.identity["data"]),
        parent=i2["identity_digest"],
        extra={"trading_contract_checksum": contract_checksum, "parent_d1_run_id": D1_RUN_ID,
               "parent_d2_run_id": D2_RUN_ID, "a_binding_digest": parent.a_digest,
               "rule_id": backtest.RULE_ID, "control_id": "D_AGG_RV20_DECILE_REWEIGHT_V1",
               "noise_tolerance": repr(ev.NOISE_TOLERANCE), "documents_sha256": docs_pre})
    elapsed = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    report = {
        "phase": "D-AGG-4", "gate_name": contract["backtest_gate"]["name"], "verdict": verdict["verdict"],
        "start": start, "noise_reproduction": reproduction, "pit": pit, "metrics": metrics, "gate": verdict,
        "primary": main, "blocks": blocks, "leave_out": {"top5_sessions": without_top5, "top10_tickers": without_top10,
                                                          "top10_sessions_retained": int(sd_t.sessions.size)},
        "concentration": {"single_ticker_positive_share": single_share, "positive_tickers": int(positive.size)},
        "counts": {"selected": selected, "setup_no_trade": setup_no_trade, "universe_rows": int(us.size),
                   "control_rows": int(control_rows.sum()), "control_valid": int(cv.sum()),
                   "matched_sessions": int(sd.sessions.size),
                   "median_control_per_session": int(np.median(sd.n_control))},
        "secondary": secondary, "descriptive": descriptive, "digests": digests,
        "performance": {"elapsed_seconds": round(elapsed, 1), "peak_rss_mb": round(peak, 1),
                        "within_limit": peak <= PEAK_RSS_LIMIT_MB},
        "code_digest": code_digest(),
    }
    tables = {"trades.parquet": trade_table, "session_delta.parquet": session_table}
    return D4Result(identity, report, tables, verdict["verdict"])


def write_run(result: D4Result, context: Mapping[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    run_id = result.identity.run_id
    run_dir = runs_dir / run_id
    if (run_dir / v1_artifacts.COMPLETE).exists():
        raise HardFail("F1", f"{run_dir} already holds a finished run")
    run_dir.mkdir(parents=True, exist_ok=True)
    data = result.identity.payload["data"]
    block = {"freeze_id": data["freeze_id"], "freeze_digest": data["freeze_digest"], "grid_digest": data["grid_digest"],
             "rules_checksum": result.identity.payload["rules_checksum"],
             "trading_contract_checksum": result.report["start"]["trading_contract_checksum"],
             "code_digest": result.identity.payload["code"]["code_digest"]}
    r = result.report
    files = {"identity.json": v1_artifacts.write_json(run_dir / "identity.json", result.identity.as_dict(), block)}
    for name, table in result.tables.items():
        files[name] = v1_artifacts.write_table(run_dir / name, table, block)
    for name, key in (("noise_reproduction.json", "noise_reproduction"), ("pit.json", "pit"),
                      ("gate_results.json", "gate"), ("secondary.json", "secondary")):
        files[name] = v1_artifacts.write_json(run_dir / name, {key: r[key]}, block)
    files["primary.json"] = v1_artifacts.write_json(run_dir / "primary.json", {"metrics": r["metrics"], "blocks": r["blocks"],
                                                                              "leave_out": r["leave_out"], "primary": r["primary"]}, block)
    (run_dir / "run_context.json").write_text(json.dumps(dict(context), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    r["artifacts"] = {k: dict(v) for k, v in files.items()}
    r["run_id"] = run_id
    v1_artifacts.write_json(run_dir / "summary.json", r, block)
    v1_artifacts.write_complete(run_dir, {"phase": "D-AGG-4", "run_id": run_id, "verdict": result.verdict,
                                          "identity_digest": result.identity.digest,
                                          "gate_digest": r["digests"]["gate"],
                                          "trade_table_digest": r["digests"]["trade_table"]}, block)
    result.run_dir = run_dir
    return run_dir


def run_context(workspace_root: Path, snapshot_id: str) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": platform.node(),
            "pid": os.getpid(), "python": platform.python_version(), "numpy": np.__version__,
            "workspace_root": str(workspace_root), "snapshot_id_requested": snapshot_id}
