"""Run E-MAX-M5: global exposure 1.0x / 1.5x / 2.0x on R1 / max 3 / B2 / X1, once.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_m5

A failed precheck, E1 reproduction, invariant or determinism check stops the run as
``E-MAX-M5 BLOCKED — INTEGRITY`` before E15 / E20 are evaluated. A second run writes ``repeat-N``.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_e_d6 import metrics as X, run as R6
from app.backtest.strategy_e_max import m1_replay as MR, m4_replay as M4R, m5_replay as M5R
from app.backtest.strategy_e_max.prepare import PrecheckBlocked, prepare
from app.dev.run_strategy_e_max_m1 import code_digest
from app.dev.run_strategy_e_r3 import trades_csv
from app.strategy_e_max import exposure, m0, m1, m2, m3, m4, m5, risk_diag

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = Path("data/runtime/strategy_e_max/m5_runs")
CHAIN = {"E-D6-RESULT": "0fb1edcf75756332c72c31e0c1e6f571b23736aa", "E-R1": "0b932e6",
         "E-R2": "fa4258e", "E-R3": "e977b05", "E-MAX-M0": "b05bbf6",
         "E-MAX-M1-RESULT": "d5bdc6b", "E-MAX-M2-RESULT": "9dc80b1", "E-MAX-M3-RESULT": "7312f45",
         "E-MAX-M4-PROTOCOL": "362b978", "E-MAX-M4-RESULT": "0c74c22", "E-MAX-M5-PROTOCOL": "d413da0"}
NAMES = ("E1", "E15", "E20")


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-M5 exposure scaling study")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    started = time.time()
    try:
        rules = m5.load_rules()
    except Exception as error:
        print(f"E-MAX-M5 BLOCKED — INTEGRITY: frozen contract {error}", file=sys.stderr)
        return 2
    block = rules["verdict_labels"]["BLOCK"]
    try:
        p = prepare(workspace_root=args.workspace_root, workers=args.workers, chain=CHAIN, log=log)
    except PrecheckBlocked as error:
        print(f"{block}: {error}", file=sys.stderr)
        return 2
    sessions = [s.isoformat() for s in p.timeline]

    chosen = M4R.selections(p)
    x1 = M4R.replay(p, chosen, {}, continuation=False)
    replayed = {name: M5R.scale_all(x1, exposure.MULTIPLIERS[name]) for name in NAMES}
    e1_identity = {"trades_csv": hashlib.sha256(trades_csv(replayed["E1"])).hexdigest(),
                   "daily_returns_csv": hashlib.sha256(R6.daily_csv(replayed["E1"], sessions)).hexdigest()}
    if (e1_identity["trades_csv"] != rules["upstream"]["m4_x1_trades_csv_sha256"]
            or e1_identity["daily_returns_csv"] != rules["upstream"]["m4_x1_daily_returns_csv_sha256"]):
        print(f"{block}: E1 does not reproduce the M4 X1 artifacts {e1_identity}", file=sys.stderr)
        return 2
    log("E1 reproduces M4 X1")
    again = M5R.scale_all(M4R.replay(p, chosen, {}, continuation=False), exposure.MULTIPLIERS["E20"])
    deterministic = R6.replay_digest(again) == R6.replay_digest(replayed["E20"])
    inv = M5R.invariants(replayed["E1"], {n: replayed[n] for n in ("E15", "E20")})
    after = p.verify_after()
    integrity = bool(deterministic and inv["pass"] and all(after.values()))
    if not integrity:
        print(f"{block}: invariants {inv} deterministic {deterministic} after {after}", file=sys.stderr)
        return 2

    e1_series = MR.session_series(replayed["E1"], sessions)
    base_ref = rules["upstream"]["e_base_reference"]
    variants, candidates, eligibility = {}, [], {}
    for name in NAMES:
        ev = R6.evaluate(replayed[name], sessions, p.universe_rows, integrity=integrity)
        s = ev["scenarios"]["COST_10BP"]
        series = MR.session_series(replayed[name], sessions)
        cagr = {k: m5.cagr(v["cumulative_return"]) for k, v in ev["scenarios"].items()}
        catastrophic = exposure.catastrophic(replayed[name])
        summary = {"integrity": integrity, "coverage": ev["funnel"]["standard_pnl_coverage"],
                   "mean_10bp": s["all_session_mean"], "pf_10bp": s["profit_factor"],
                   "mdd_10bp": s["maximum_drawdown"], "catastrophic": bool(catastrophic),
                   "top1_share_10bp": ev["concentration"]["cost_10bp"]["top1"]["share_of_total"]}
        eligibility[name] = m5.eligible(summary, rules)
        paired = X.bootstrap_mean_ci(series - e1_series)
        entry = {
            "evaluation": ev, "cagr": cagr,
            "calmar": {k: (cagr[k] / abs(v["maximum_drawdown"]) if v["maximum_drawdown"] else None)
                       for k, v in ev["scenarios"].items()},
            "eligibility": eligibility[name], "catastrophic_sessions": catastrophic,
            "risk": {**risk_diag.diagnostics(sessions, series),
                     "maximum_single_session_loss": float(series.min()),
                     "equity_le_zero": bool((1.0 + series).cumprod().min() <= 0)},
            "extremes": MR.extremes(replayed[name]),
            "breadth_interaction": M5R.breadth_interaction(sessions, series, replayed[name]),
            "tail": M5R.tail(series, sessions),
            "blocks": M5R.blocks(series, sessions, ev["primary_gate"]["blocks"]),
            "calendar": M5R.calendar(ev["stability"]),
            "periods": MR.period_table(replayed[name], e1_series, sessions),
            "stop_b_vs_e1": None, "stop_b_vs_e_base": M5R.stop_b(cagr["COST_10BP"], s["maximum_drawdown"],
                                                                 base_ref["cagr_10bp"], base_ref["mdd_10bp"]),
        }
        if name != "E1":
            e1 = variants["E1"]
            entry["paired_vs_e1"] = paired
            entry["improvement"] = m5.improved(s["cumulative_return"],
                                               e1["evaluation"]["scenarios"]["COST_10BP"]["cumulative_return"],
                                               paired, rules)
            entry["stop_b_vs_e1"] = M5R.stop_b(cagr["COST_10BP"], s["maximum_drawdown"],
                                               e1["cagr"]["COST_10BP"],
                                               e1["evaluation"]["scenarios"]["COST_10BP"]["maximum_drawdown"])
            entry["calmar_delta_vs_e1"] = entry["calmar"]["COST_10BP"] - e1["calmar"]["COST_10BP"]
            candidates.append({"id": name, "eligible_pass": eligibility[name]["pass"],
                               "improved_pass": entry["improvement"]["pass"],
                               "cagr_10bp": cagr["COST_10BP"], "mean_10bp": s["all_session_mean"]})
        variants[name] = entry
        log(f"{name} evaluated")
    verdict = m5.winner(candidates, rules)
    verdict["m6"] = m5.m6(eligibility[verdict["winner"]], rules)
    log(f"verdict {verdict['label']}; {verdict['m6']}")

    identity = {**p.identity, "m0_rules": m0.RULES_CANONICAL_SHA256, "m1_rules": m1.RULES_CANONICAL_SHA256,
                "m2_rules": m2.RULES_CANONICAL_SHA256, "m3_rules": m3.RULES_CANONICAL_SHA256,
                "m4_rules": m4.RULES_CANONICAL_SHA256, "m5_rules": m5.RULES_CANONICAL_SHA256,
                "code": R6.canonical_sha256({"packages_and_m1_runner": code_digest(),
                                            "m5_runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})}
    identity_digest = R6.canonical_sha256(identity)
    run_id = f"em5-{identity_digest[:12]}"
    result = {"version": "STRATEGY_E_MAX_M5_RESULT_V1", "run_id": run_id, "identity": identity,
              "identity_digest": identity_digest, "evidence_label": rules["declaration"]["evidence_label"],
              "financing": rules["financing"], "scaling_caveat": rules["improvement"]["scaling_caveat"],
              "prechecks": {**p.prechecks, "e1_reproduces_m4_x1": e1_identity, "invariants": inv,
                            "in_process_deterministic": deterministic, **after},
              "integrity": integrity, "variants": variants, "verdict": verdict}
    result_bytes = R6.canonical_json(result)
    result_digest = hashlib.sha256(result_bytes).hexdigest()
    files = {"result.json": result_bytes}
    for name in NAMES:
        files[f"{name}_trades.csv"] = trades_csv(replayed[name])
        files[f"{name}_daily_returns.csv"] = R6.daily_csv(replayed[name], sessions)
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
                   "verdict": verdict["label"], "m6": verdict["m6"]}
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
    print(f"{verdict['label']} / {verdict['m6']} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
