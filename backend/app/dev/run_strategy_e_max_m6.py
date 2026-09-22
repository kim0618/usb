"""Run E-MAX-M6: the frozen E-MAX V1 (R1 / max 3 / X1 / B2 / 2.0x) integrated replay, once.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_m6

A failed precheck, stage identity, independent check, determinism check or M5 metric identity
gives ``E-MAX-M6 BLOCKED — INTEGRITY``. A second run writes ``repeat-N`` and REPEAT_CHECK.json.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_e_d6 import run as R6
from app.backtest.strategy_e_max import m1_replay as MR, m5_replay as M5R, v1_replay as V
from app.backtest.strategy_e_max.prepare import PrecheckBlocked, prepare
from app.dev.run_strategy_e_max_m1 import code_digest
from app.dev.run_strategy_e_r3 import trades_csv
from app.strategy_e_max import exposure, m0, m1, m2, m3, m4, m5, risk_diag, v1

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = Path("data/runtime/strategy_e_max/m6_runs")
CHAIN = {"E-D6-RESULT": "0fb1edcf75756332c72c31e0c1e6f571b23736aa", "E-R1": "0b932e6",
         "E-R2": "fa4258e", "E-R3": "e977b05", "E-MAX-M0": "b05bbf6",
         "E-MAX-M1-PROTOCOL": "058217f", "E-MAX-M1-RESULT": "d5bdc6b",
         "E-MAX-M2-PROTOCOL": "cf5a6cb", "E-MAX-M2-RESULT": "9dc80b1",
         "E-MAX-M3-PROTOCOL": "7e240a8", "E-MAX-M3-RESULT": "7312f45",
         "E-MAX-M4-PROTOCOL": "362b978", "E-MAX-M4-RESULT": "0c74c22",
         "E-MAX-M5-PROTOCOL": "d413da0", "E-MAX-M5-RESULT": "9d4b1f1", "E-MAX-M6-PROTOCOL": "dedae54"}
M5_RESULT = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m5_result_v1.json"


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def _hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-M6 integrated frozen replay")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    started = time.time()
    try:
        rules = v1.load_rules()
    except Exception as error:
        print(f"E-MAX-M6 BLOCKED — INTEGRITY: frozen contract {error}", file=sys.stderr)
        return 2
    block = rules["verdict_labels"]["BLOCK"]
    try:
        p = prepare(workspace_root=args.workspace_root, workers=args.workers, chain=CHAIN, log=log)
    except PrecheckBlocked as error:
        print(f"{block}: {error}", file=sys.stderr)
        return 2
    sessions = [s.isoformat() for s in p.timeline]
    if (len(sessions), sessions[0], sessions[-1]) != (rules["dataset"]["sessions"], rules["dataset"]["first_session"],
                                                      rules["dataset"]["last_session"]):
        print(f"{block}: timeline {len(sessions)} {sessions[0]}..{sessions[-1]}", file=sys.stderr)
        return 2

    # 1. integrated replay: S1 (R1 / max 3 / X1 at 1.0x) -> S2 (+ B2) -> S3 (+ 2.0x) ----------------
    chosen = V.selections(p)
    s1, s2, s3 = V.replay(p, chosen)
    files = {"S1_trades.csv": trades_csv(s1), "S1_daily_returns.csv": R6.daily_csv(s1, sessions),
             "S2_trades.csv": trades_csv(s2), "S2_daily_returns.csv": R6.daily_csv(s2, sessions),
             "S2_exposure_map.json": R6.canonical_json(V.exposure_map(s2)),
             "V1_trades.csv": trades_csv(s3), "V1_daily_returns.csv": R6.daily_csv(s3, sessions)}
    art = rules["upstream"]["stage_artifacts"]
    stage_identity = {
        "S1_R1_MAX3_X1_1X": {"trades": _hash(files["S1_trades.csv"]) == art["S1_R1_MAX3_X1_1X"]["trades_csv_sha256"],
                             "daily": _hash(files["S1_daily_returns.csv"])
                             == art["S1_R1_MAX3_X1_1X"]["daily_returns_csv_sha256"]},
        "S2_PLUS_B2": {"trades": _hash(files["S2_trades.csv"]) == art["S2_PLUS_B2"]["trades_csv_sha256"],
                       "daily": _hash(files["S2_daily_returns.csv"]) == art["S2_PLUS_B2"]["daily_returns_csv_sha256"],
                       "exposure_map": _hash(files["S2_exposure_map.json"])
                       == art["S2_PLUS_B2"]["exposure_map_json_sha256"]},
        "S3_PLUS_GLOBAL_2X": {"trades": _hash(files["V1_trades.csv"]) == art["S3_PLUS_GLOBAL_2X"]["trades_csv_sha256"],
                              "daily": _hash(files["V1_daily_returns.csv"])
                              == art["S3_PLUS_GLOBAL_2X"]["daily_returns_csv_sha256"]},
    }
    stage_pass = all(all(v.values()) for v in stage_identity.values())
    log(f"stage identities {stage_identity}")

    # 2. determinism, independent checks, inputs unchanged ---------------------------------------
    again = V.replay(p, V.selections(p))[2]
    deterministic = (R6.replay_digest(again) == R6.replay_digest(s3)
                     and trades_csv(again) == files["V1_trades.csv"])
    independent = V.independent_checks(p, chosen, s3)
    after = p.verify_after()
    integrity = bool(stage_pass and deterministic and independent["pass"] and all(after.values()))
    log(f"deterministic {deterministic}; independent {independent}; after {after}")

    # 3. evaluation (the M5 E20 recipe, section for section) --------------------------------------
    ev = R6.evaluate(s3, sessions, p.universe_rows, integrity=integrity)
    s = ev["scenarios"]["COST_10BP"]
    series = MR.session_series(s3, sessions)
    cagr = {k: m1.cagr(v["cumulative_return"]) for k, v in ev["scenarios"].items()}
    calmar = {k: (cagr[k] / abs(v["maximum_drawdown"]) if v["maximum_drawdown"] else None)
              for k, v in ev["scenarios"].items()}
    catastrophic = exposure.catastrophic(s3)
    sections = {
        "evaluation": ev, "cagr": cagr, "calmar": calmar,
        "risk": {**risk_diag.diagnostics(sessions, series), "maximum_single_session_loss": float(series.min()),
                 "equity_le_zero": bool((1.0 + series).cumprod().min() <= 0)},
        "tail": M5R.tail(series, sessions),
        "blocks": M5R.blocks(series, sessions, ev["primary_gate"]["blocks"]),
        "calendar": M5R.calendar(ev["stability"]),
        "breadth_interaction": M5R.breadth_interaction(sessions, series, s3),
        "extremes": MR.extremes(s3), "catastrophic_sessions": catastrophic,
    }
    committed = json.loads(M5_RESULT.read_text(encoding="utf-8"))["variants"]["E20"]
    m5_identity = {}
    for name in rules["pipeline"]["m5_metric_identity"]["sections"]:
        mine = json.loads(R6.canonical_json(sections[name]))
        m5_identity[name] = {"identical": mine == committed[name],
                             "first_difference": V.first_difference(mine, committed[name])}
    m5_pass = all(v["identical"] for v in m5_identity.values())
    log(f"M5 identity {'PASS' if m5_pass else m5_identity}")

    # 4. gate ----------------------------------------------------------------------------------
    e_base = v1.e_base_reference()
    m5_e1 = json.loads(M5_RESULT.read_text(encoding="utf-8"))["variants"]["E1"]
    stop_b = v1.stop_b(cagr["COST_10BP"], s["maximum_drawdown"], e_base["cagr_10bp"], e_base["mdd_10bp"])
    stop_b_e1 = v1.stop_b(cagr["COST_10BP"], s["maximum_drawdown"], m5_e1["cagr"]["COST_10BP"],
                          m5_e1["evaluation"]["scenarios"]["COST_10BP"]["maximum_drawdown"])
    stops = {"A": v1.stop_a(), "B": stop_b["flagged"], "C": None, "D": v1.stop_d(s["maximum_drawdown"])}
    top1 = ev["concentration"]["cost_10bp"]["top1"]["share_of_total"]
    summary = {"coverage": ev["funnel"]["standard_pnl_coverage"], "mean_10bp": s["all_session_mean"],
               "pf_10bp": s["profit_factor"], "mdd_10bp": s["maximum_drawdown"], "top1_share_10bp": top1,
               "catastrophic": bool(catastrophic), "stop_a": stops["A"], "stop_b_flagged": stops["B"],
               "stop_d": stops["D"]}
    gate = v1.gate(summary, rules)
    stops["C"] = not gate["concentration_top1"]
    label = v1.verdict(integrity=integrity, reproducible=deterministic, m5_identity=m5_pass,
                       gate_checks=gate, rules=rules)
    mdd_by = {k: v["maximum_drawdown"] for k, v in ev["scenarios"].items()}
    log(f"verdict {label}")

    identity = {**p.identity, "m0_rules": m0.RULES_CANONICAL_SHA256, "m1_rules": m1.RULES_CANONICAL_SHA256,
                "m2_rules": m2.RULES_CANONICAL_SHA256, "m3_rules": m3.RULES_CANONICAL_SHA256,
                "m4_rules": m4.RULES_CANONICAL_SHA256, "m5_rules": m5.RULES_CANONICAL_SHA256,
                "v1_rules": v1.RULES_CANONICAL_SHA256,
                "code": R6.canonical_sha256({"packages_and_m1_runner": code_digest(),
                                            "m6_runner": _hash(Path(__file__).read_bytes())})}
    identity_digest = R6.canonical_sha256(identity)
    run_id = f"em6-{identity_digest[:12]}"
    result = {
        "version": "STRATEGY_E_MAX_V1_RESULT", "strategy_id": v1.STRATEGY_ID, "run_id": run_id,
        "identity": identity, "identity_digest": identity_digest,
        "evidence_label": rules["declaration"]["evidence_label"],
        "definition": rules["definition"], "financing_and_live": rules["financing_and_live"],
        "pass_meaning": rules["pass_meaning"],
        "prechecks": {**p.prechecks, **after, "in_process_deterministic": deterministic},
        "stage_identity": stage_identity, "independent_checks": independent,
        "m5_identity": m5_identity, "integrity": integrity,
        "sections": sections,
        "funnel": V.funnel(ev, s3),
        "cost_table": V.cost_table(ev, cagr, calmar, rules["gate"]["mdd_10bp_ge"]),
        "execution_cost_sensitivity": v1.cost_sensitivity(mdd_by, rules),
        "stop_b": {"authoritative_vs_e_base": {**stop_b, "reference": e_base},
                   "diagnostic_vs_m5_e1": {**stop_b_e1, "reference": {
                       "cagr_10bp": m5_e1["cagr"]["COST_10BP"],
                       "mdd_10bp": m5_e1["evaluation"]["scenarios"]["COST_10BP"]["maximum_drawdown"]}}},
        "stop_conditions": stops,
        "tail_symbols": {k: ev["concentration"]["cost_10bp"][k] for k in ("top1", "top5", "top10",
                                                                         "hhi_trade_count", "unique_symbols")},
        "breadth_dependence": V.breadth_dependence(series, sessions, s3),
        "coverage_regimes": V.regimes(series, sessions, s3, rules),
        "quarters_of_interest": V.quarters(series, sessions, ("2026Q2", "2026Q3")),
        "drawdown": V.drawdown(series, sessions),
        "bootstrap_10bp": ev["primary_gate"]["bootstrap"],
        "gate": gate,
        "verdict": {"label": label, "summary": summary},
    }
    result_bytes = R6.canonical_json(result)
    result_digest = _hash(result_bytes)
    files["result.json"] = result_bytes
    base_dir = REPO_ROOT / RUNS / run_id
    first = None
    if (base_dir / "COMPLETE.json").exists():
        first = json.loads((base_dir / "COMPLETE.json").read_text(encoding="utf-8"))
        run_dir = base_dir / f"repeat-{2 + sum(1 for q in base_dir.glob('repeat-*') if q.is_dir())}"
    else:
        run_dir = base_dir
    digests = R6.write_artifacts(run_dir, files)
    manifest = {"started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "git_head": R6.git_head(), "workers": args.workers, "provider_calls": 0,
                "artifacts": digests, "result_digest": result_digest}
    (run_dir / "run_manifest.json").write_bytes(R6.canonical_json(manifest))
    out = {"run_id": run_id, "result_digest": result_digest, "artifacts": digests, "verdict": label}
    if first is None:
        (run_dir / "COMPLETE.json").write_bytes(R6.canonical_json(out))
    else:
        check = {"attempt": run_dir.name, "first_result_digest": first["result_digest"],
                 "this_result_digest": result_digest, "identical": first["result_digest"] == result_digest,
                 "artifacts_identical": first["artifacts"] == digests}
        (run_dir / "REPEAT_CHECK.json").write_bytes(R6.canonical_json(check))
        log(f"repeat check {check}")
    log(f"artifacts {run_dir} in {time.time() - started:.0f}s")
    print(f"{label} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
