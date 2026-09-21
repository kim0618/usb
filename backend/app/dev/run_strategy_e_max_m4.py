"""Run E-MAX-M4: R1 / max 3 / B2 with the X1 (09:34) exit versus the X2 conditional 09:44 exit.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_m4

A failed precheck, X1 reproduction, invariant or determinism check stops the run as
``E-MAX-M4 BLOCKED — INTEGRITY`` before X2 is evaluated. A second run writes ``repeat-N``.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_e_d6 import metrics as X, run as R6
from app.backtest.strategy_e_max import m1_replay as MR, m2_replay as M2R, m4_replay as M4R
from app.backtest.strategy_e_max.prepare import PrecheckBlocked, prepare
from app.dev.run_strategy_e_max_m1 import code_digest
from app.dev.run_strategy_e_r3 import trades_csv
from app.strategy_e_max import m0, m1, m2, m3, m4, risk_diag

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = Path("data/runtime/strategy_e_max/m4_runs")
CHAIN = {"E-D6-RESULT": "0fb1edcf75756332c72c31e0c1e6f571b23736aa", "E-R1": "0b932e6",
         "E-R2": "fa4258e", "E-R3": "e977b05", "E-MAX-M0": "b05bbf6",
         "E-MAX-M1-RESULT": "d5bdc6b", "E-MAX-M2-RESULT": "9dc80b1",
         "E-MAX-M3-PROTOCOL": "7e240a8", "E-MAX-M3-RESULT": "7312f45",
         "E-MAX-M4-PROTOCOL": "362b978"}


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def _summary(ev, integrity) -> dict:
    s = ev["scenarios"]["COST_10BP"]
    return {"integrity": integrity, "coverage": ev["funnel"]["standard_pnl_coverage"],
            "mean_10bp": s["all_session_mean"], "pf_10bp": s["profit_factor"],
            "mdd_10bp": s["maximum_drawdown"],
            "top1_share_10bp": ev["concentration"]["cost_10bp"]["top1"]["share_of_total"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-M4 aggressive exit study")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    started = time.time()
    try:
        rules = m4.load_rules()
    except Exception as error:
        print(f"E-MAX-M4 BLOCKED — INTEGRITY: frozen contract {error}", file=sys.stderr)
        return 2
    block = rules["verdict_labels"]["BLOCK"]
    try:
        p = prepare(workspace_root=args.workspace_root, workers=args.workers, chain=CHAIN, log=log)
    except PrecheckBlocked as error:
        print(f"{block}: {error}", file=sys.stderr)
        return 2
    sessions = [s.isoformat() for s in p.timeline]

    chosen = M4R.selections(p)
    bars44 = M4R.fetch_0944(p.view_root, chosen, workers=args.workers)
    x1 = M4R.replay(p, chosen, bars44, continuation=False)
    x1_identity = {"trades_csv": hashlib.sha256(trades_csv(x1)).hexdigest(),
                   "daily_returns_csv": hashlib.sha256(R6.daily_csv(x1, sessions)).hexdigest()}
    if (x1_identity["trades_csv"] != rules["upstream"]["m3_b2_trades_csv_sha256"]
            or x1_identity["daily_returns_csv"] != rules["upstream"]["m3_b2_daily_returns_csv_sha256"]):
        print(f"{block}: X1 does not reproduce the M3 B2 artifacts {x1_identity}", file=sys.stderr)
        return 2
    log("X1 reproduces M3 B2")
    x2 = M4R.replay(p, chosen, bars44, continuation=True)
    deterministic = (R6.replay_digest(x2) == R6.replay_digest(M4R.replay(p, chosen, bars44, continuation=True))
                     and [s["exit_decisions"] for s in x2]
                     == [s["exit_decisions"] for s in M4R.replay(p, chosen, bars44, continuation=True)])
    inv = M4R.invariants(x1, x2)
    after = p.verify_after()
    integrity = bool(deterministic and inv["pass"] and all(after.values()))
    if not integrity:
        print(f"{block}: invariants {inv} deterministic {deterministic} after {after}", file=sys.stderr)
        return 2

    x1_series, x2_series = MR.session_series(x1, sessions), MR.session_series(x2, sessions)
    variants = {}
    for name, replayed, series in (("X1", x1, x1_series), ("X2", x2, x2_series)):
        ev = R6.evaluate(replayed, sessions, p.universe_rows, integrity=integrity)
        cagr = {k: m4.cagr(v["cumulative_return"]) for k, v in ev["scenarios"].items()}
        variants[name] = {
            "evaluation": ev, "cagr": cagr,
            "calmar": {k: (cagr[k] / abs(v["maximum_drawdown"]) if v["maximum_drawdown"] else None)
                       for k, v in ev["scenarios"].items()},
            "extremes": MR.extremes(replayed), "risk": risk_diag.diagnostics(sessions, series),
            "periods": MR.period_table(replayed, x1_series, sessions),
            "blocks": M2R.block_returns(series, sessions, ev["primary_gate"]["blocks"]),
        }
    ev1, ev2 = variants["X1"]["evaluation"], variants["X2"]["evaluation"]
    delta = x2_series - x1_series
    paired = X.bootstrap_mean_ci(delta)
    eligibility = m4.eligible(_summary(ev2, integrity), rules)
    x1_eligibility = m4.eligible(_summary(ev1, integrity), rules)
    s1, s2 = ev1["scenarios"]["COST_10BP"], ev2["scenarios"]["COST_10BP"]
    improvement = m4.improved(s2["cumulative_return"], s1["cumulative_return"], paired, rules)
    verdict = m4.decide(eligibility, improvement, x1_eligibility, rules)
    c1, c2 = variants["X1"]["cagr"]["COST_10BP"], variants["X2"]["cagr"]["COST_10BP"]
    stop_b = {"cagr_ratio": c2 / c1 if c1 else None,
              "mdd_ratio": abs(s2["maximum_drawdown"]) / abs(s1["maximum_drawdown"]) if s1["maximum_drawdown"] else None}
    stop_b["flagged"] = bool(stop_b["cagr_ratio"] is not None and stop_b["mdd_ratio"] is not None
                             and stop_b["mdd_ratio"] > stop_b["cagr_ratio"])
    log(f"verdict {verdict['label']}; {verdict['m5']}")

    identity = {**p.identity, "m0_rules": m0.RULES_CANONICAL_SHA256, "m1_rules": m1.RULES_CANONICAL_SHA256,
                "m2_rules": m2.RULES_CANONICAL_SHA256, "m3_rules": m3.RULES_CANONICAL_SHA256,
                "m4_rules": m4.RULES_CANONICAL_SHA256,
                "code": R6.canonical_sha256({"packages_and_m1_runner": code_digest(),
                                            "m4_runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})}
    identity_digest = R6.canonical_sha256(identity)
    run_id = f"em4-{identity_digest[:12]}"
    decisions = [{"session": s["session"], **d} for s in x2 for d in s["exit_decisions"]]
    result = {"version": "STRATEGY_E_MAX_M4_RESULT_V1", "run_id": run_id, "identity": identity,
              "identity_digest": identity_digest, "evidence_label": rules["declaration"]["evidence_label"],
              "cost_limitation": rules["costs"]["limitation"],
              "prechecks": {**p.prechecks, "x1_reproduces_m3_b2": x1_identity, "invariants": inv,
                            "in_process_deterministic": deterministic, **after},
              "integrity": integrity, "exit_funnel": M4R.exit_funnel(x1, x2),
              "continuation_outcome": M4R.continuation_outcome(x2),
              "changed_sessions": M4R.changed_sessions(delta, sessions, x2, ev1["primary_gate"]["blocks"]),
              "tail_dependence": M4R.tail_dependence(delta, sessions),
              "variants": variants, "x2_eligibility": eligibility, "x2_improvement": improvement,
              "x1_eligibility": x1_eligibility, "paired_x2_minus_x1": paired,
              "cumulative_delta_10bp": s2["cumulative_return"] - s1["cumulative_return"],
              "stop_b": stop_b, "verdict": verdict}
    result_bytes = R6.canonical_json(result)
    result_digest = hashlib.sha256(result_bytes).hexdigest()
    files = {"result.json": result_bytes,
             "X1_trades.csv": trades_csv(x1), "X1_daily_returns.csv": R6.daily_csv(x1, sessions),
             "X2_trades.csv": trades_csv(x2), "X2_daily_returns.csv": R6.daily_csv(x2, sessions),
             "exit_decisions.json": R6.canonical_json(decisions)}
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
    summary_out = {"run_id": run_id, "result_digest": result_digest, "artifacts": digests,
                   "verdict": verdict["label"], "m5": verdict["m5"]}
    if first is None:
        (run_dir / "COMPLETE.json").write_bytes(R6.canonical_json(summary_out))
    else:
        check = {"attempt": run_dir.name, "first_result_digest": first["result_digest"],
                 "this_result_digest": result_digest,
                 "identical": first["result_digest"] == result_digest,
                 "artifacts_identical": first["artifacts"] == digests}
        (run_dir / "REPEAT_CHECK.json").write_bytes(R6.canonical_json(check))
        log(f"repeat check {check}")
    log(f"artifacts {run_dir} in {time.time() - started:.0f}s")
    print(f"{verdict['label']} / {verdict['m5']} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
