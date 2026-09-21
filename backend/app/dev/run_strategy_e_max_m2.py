"""Run E-MAX-M2: R1 at max 3 (C1) versus max 5 (C2), once, under the frozen protocol.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_m2

A failed precheck, C1 reproduction, invariant or determinism check stops the run as
``E-MAX-M2 BLOCKED — INTEGRITY`` before C2 is evaluated. A second run writes ``repeat-N``.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_e_d6 import metrics as X, run as R6
from app.backtest.strategy_e_max import m1_replay as MR, m2_replay as M2R
from app.backtest.strategy_e_max.prepare import PrecheckBlocked, prepare
from app.dev.run_strategy_e_max_m1 import code_digest
from app.dev.run_strategy_e_r3 import trades_csv
from app.strategy_e_max import m0, m1, m2

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = Path("data/runtime/strategy_e_max/m2_runs")
CHAIN = {"E-D6-RESULT": "0fb1edcf75756332c72c31e0c1e6f571b23736aa", "E-R1": "0b932e6",
         "E-R2": "fa4258e", "E-R3": "e977b05", "E-MAX-M0": "b05bbf6",
         "E-MAX-M1-PROTOCOL": "058217f", "E-MAX-M1-RESULT": "d5bdc6b",
         "E-MAX-M2-PROTOCOL": "cf5a6cb"}


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-M2 capacity study")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    started = time.time()
    try:
        rules = m2.load_rules()
    except Exception as error:
        print(f"E-MAX-M2 BLOCKED — INTEGRITY: frozen contract {error}", file=sys.stderr)
        return 2
    block = rules["verdict_labels"]["BLOCK"]
    try:
        p = prepare(workspace_root=args.workspace_root, workers=args.workers, chain=CHAIN, log=log)
    except PrecheckBlocked as error:
        print(f"{block}: {error}", file=sys.stderr)
        return 2
    sessions = [s.isoformat() for s in p.timeline]

    c1 = M2R.replay(3, p)
    c1_identity = {"trades_csv": hashlib.sha256(trades_csv(c1)).hexdigest(),
                   "daily_returns_csv": hashlib.sha256(R6.daily_csv(c1, sessions)).hexdigest()}
    if (c1_identity["trades_csv"] != rules["upstream"]["m1_r1_trades_csv_sha256"]
            or c1_identity["daily_returns_csv"] != rules["upstream"]["m1_r1_daily_returns_csv_sha256"]):
        print(f"{block}: C1 does not reproduce the M1 R1 artifacts {c1_identity}", file=sys.stderr)
        return 2
    log("C1 reproduces M1 R1")
    c2 = M2R.replay(5, p)
    deterministic = (R6.replay_digest(c1) == R6.replay_digest(M2R.replay(3, p))
                     and R6.replay_digest(c2) == R6.replay_digest(M2R.replay(5, p)))
    inv = M2R.invariants(p, c1, c2)
    after = p.verify_after()
    integrity = bool(deterministic and inv["pass"] and all(after.values()))
    if not integrity:
        print(f"{block}: invariants {inv} deterministic {deterministic} after {after}", file=sys.stderr)
        return 2

    c1_series = MR.session_series(c1, sessions)
    variants = {}
    for name, replayed in (("C1", c1), ("C2", c2)):
        ev = R6.evaluate(replayed, sessions, p.universe_rows, integrity=integrity)
        series = MR.session_series(replayed, sessions)
        cagr = {k: m2.cagr(v["cumulative_return"]) for k, v in ev["scenarios"].items()}
        variants[name] = {
            "evaluation": ev, "cagr": cagr,
            "calmar": {k: (cagr[k] / abs(v["maximum_drawdown"]) if v["maximum_drawdown"] else None)
                       for k, v in ev["scenarios"].items()},
            "extremes": MR.extremes(replayed),
            "periods": MR.period_table(replayed, c1_series, sessions),
            "blocks": M2R.block_returns(series, sessions, ev["primary_gate"]["blocks"]),
        }
    ev1, ev2 = variants["C1"]["evaluation"], variants["C2"]["evaluation"]
    s1, s2 = ev1["scenarios"]["COST_10BP"], ev2["scenarios"]["COST_10BP"]
    paired = X.bootstrap_mean_ci(MR.session_series(c2, sessions) - c1_series)
    summary = {"integrity": integrity, "coverage": ev2["funnel"]["standard_pnl_coverage"],
               "mean_10bp": s2["all_session_mean"], "pf_10bp": s2["profit_factor"],
               "mdd_10bp": s2["maximum_drawdown"],
               "top1_share_10bp": ev2["concentration"]["cost_10bp"]["top1"]["share_of_total"]}
    eligibility = m2.eligible(summary, rules)
    improvement = m2.improved(s2["cumulative_return"], s1["cumulative_return"], paired, rules)
    verdict = m2.decide(eligibility, improvement, rules)
    c1_cagr, c2_cagr = variants["C1"]["cagr"]["COST_10BP"], variants["C2"]["cagr"]["COST_10BP"]
    stop_b = {"cagr_ratio": (c2_cagr / c1_cagr) if c1_cagr else None,
              "mdd_ratio": (abs(s2["maximum_drawdown"]) / abs(s1["maximum_drawdown"]))
              if s1["maximum_drawdown"] else None}
    stop_b["flagged"] = (stop_b["cagr_ratio"] is not None and stop_b["mdd_ratio"] is not None
                         and stop_b["mdd_ratio"] > stop_b["cagr_ratio"])
    log(f"verdict {verdict['label']}")

    identity = {**p.identity, "m0_rules": m0.RULES_CANONICAL_SHA256, "m1_rules": m1.RULES_CANONICAL_SHA256,
                "m2_rules": m2.RULES_CANONICAL_SHA256,
                "code": R6.canonical_sha256({"packages_and_m1_runner": code_digest(),
                                            "m2_runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})}
    identity_digest = R6.canonical_sha256(identity)
    run_id = f"em2-{identity_digest[:12]}"
    result = {"version": "STRATEGY_E_MAX_M2_RESULT_V1", "run_id": run_id, "identity": identity,
              "identity_digest": identity_digest, "evidence_label": rules["declaration"]["evidence_label"],
              "risk_status": rules["risk_status"]["c2"],
              "prechecks": {**p.prechecks, "c1_reproduces_m1_r1": c1_identity, "invariants": inv,
                            "in_process_deterministic": deterministic, **after},
              "integrity": integrity, "opportunity": M2R.opportunity(c1, c2), "variants": variants,
              "c2_eligibility": eligibility, "c2_improvement": improvement, "paired_c2_minus_c1": paired,
              "cumulative_delta_10bp": s2["cumulative_return"] - s1["cumulative_return"],
              "stop_b": stop_b, "verdict": verdict}
    result_bytes = R6.canonical_json(result)
    result_digest = hashlib.sha256(result_bytes).hexdigest()
    files = {"result.json": result_bytes,
             "C1_trades.csv": trades_csv(c1), "C1_daily_returns.csv": R6.daily_csv(c1, sessions),
             "C2_trades.csv": trades_csv(c2), "C2_daily_returns.csv": R6.daily_csv(c2, sessions)}
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
                   "verdict": verdict["label"]}
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
    print(f"{verdict['label']} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
