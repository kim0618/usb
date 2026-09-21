"""Run E-MAX-M1: the three preregistered candidate-strength orderings, once, under the frozen protocol.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_m1

Read-only with respect to every shared store. A failed precheck, invariant or equivalence check
stops the run as ``E-MAX-M1 BLOCKED — INTEGRITY`` before any variant is evaluated. Running again
over the same input writes ``repeat-N`` and records whether every artifact was byte-identical.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_e_d6 import metrics as X, run as R6
from app.backtest.strategy_e_max import m1_replay as MR
from app.backtest.strategy_e_max.prepare import PrecheckBlocked, prepare
from app.dev.run_strategy_e_r3 import trades_csv
from app.strategy_e_max import m0, m1

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = Path("data/runtime/strategy_e_max/m1_runs")
CHAIN = {"E-D6-RESULT": "0fb1edcf75756332c72c31e0c1e6f571b23736aa",
         "E-R1": "0b932e6", "E-R2": "fa4258e", "E-R3": "e977b05", "E-MAX-M0": "b05bbf6",
         "E-MAX-M1-PROTOCOL": "058217f"}
VARIANTS = ("R0", "R1", "R2", "R3")


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def code_digest() -> str:
    digest = hashlib.sha256()
    roots = [REPO_ROOT / "backend/app/strategy_e_max", REPO_ROOT / "backend/app/backtest/strategy_e_max"]
    files = [p for root in roots for p in sorted(root.glob("*.py"))]
    files.append(Path(__file__).resolve())
    for path in files:
        digest.update(f"{path.relative_to(REPO_ROOT).as_posix()}\n".encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-M1 candidate-strength ranking study")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    started = time.time()
    try:
        rules = m1.load_rules()
        m0.load_rules()
    except Exception as error:
        print(f"{'E-MAX-M1 BLOCKED — INTEGRITY'}: frozen contract {error}", file=sys.stderr)
        return 2
    block = rules["verdict_labels"]["BLOCK"]
    try:
        p = prepare(workspace_root=args.workspace_root, workers=args.workers, chain=CHAIN, log=log)
    except PrecheckBlocked as error:
        print(f"{block}: {error}", file=sys.stderr)
        return 2
    sessions = [s.isoformat() for s in p.timeline]

    # R0 = E-R3 path; its artifacts must be E-R3's -------------------------------------------
    base = MR.replay_base(p)
    base_trades, base_daily = trades_csv(base), R6.daily_csv(base, sessions)
    r0_identity = {"trades_csv": hashlib.sha256(base_trades).hexdigest(),
                   "daily_returns_csv": hashlib.sha256(base_daily).hexdigest()}
    if (r0_identity["trades_csv"] != rules["upstream"]["e_r3_trades_csv_sha256"]
            or r0_identity["daily_returns_csv"] != rules["upstream"]["e_r3_daily_returns_csv_sha256"]):
        print(f"{block}: R0 does not reproduce E-R3 {r0_identity}", file=sys.stderr)
        return 2
    if not MR.equivalent(base, MR.replay_variant("R0", p)):
        print(f"{block}: subset replay with R0 order differs from the E-R3 path", file=sys.stderr)
        return 2
    log("R0 reproduces E-R3; subset method equivalent")

    replayed = {"R0": base, **{v: MR.replay_variant(v, p) for v in VARIANTS[1:]}}
    again = {v: MR.replay_variant(v, p) for v in VARIANTS[1:]}
    deterministic = all(R6.replay_digest(replayed[v]) == R6.replay_digest(again[v]) for v in again)
    inv = MR.invariants(p, replayed)
    after = p.verify_after()
    integrity = bool(deterministic and inv["pass"] and all(after.values()))
    if not integrity:
        print(f"{block}: invariants {inv} deterministic {deterministic} after {after}", file=sys.stderr)
        return 2

    base_series = MR.session_series(base, sessions)
    ref_top1 = rules["eligibility"]["top1_share_10bp_le"]
    variants, candidates = {}, []
    for name in VARIANTS:
        ev = R6.evaluate(replayed[name], sessions, p.universe_rows, integrity=integrity)
        primary = ev["scenarios"]["COST_10BP"]
        cagr = {k: m1.cagr(v["cumulative_return"]) for k, v in ev["scenarios"].items()}
        calmar = {k: (cagr[k] / abs(v["maximum_drawdown"]) if v["maximum_drawdown"] else None)
                  for k, v in ev["scenarios"].items()}
        series = MR.session_series(replayed[name], sessions)
        paired = X.bootstrap_mean_ci(series - base_series)
        top1 = ev["concentration"]["cost_10bp"]["top1"]["share_of_total"]
        entry = {
            "evaluation": ev, "cagr": cagr, "calmar": calmar, "extremes": MR.extremes(replayed[name]),
            "paired_vs_r0": paired,
            "selection_delta_vs_r0": MR.selection_delta(base, replayed[name]),
            "periods": MR.period_table(replayed[name], base_series, sessions),
        }
        if name != "R0":
            summary = {"integrity": integrity, "coverage": ev["funnel"]["standard_pnl_coverage"],
                       "mean_10bp": primary["all_session_mean"], "pf_10bp": primary["profit_factor"],
                       "mdd_10bp": primary["maximum_drawdown"], "top1_share_10bp": top1}
            entry["eligibility"] = m1.eligible(summary, rules)
            entry["improvement"] = m1.improved(primary["cumulative_return"],
                                               variants["R0"]["evaluation"]["scenarios"]["COST_10BP"]["cumulative_return"],
                                               paired, rules)
            candidates.append({"id": name, "eligible_pass": entry["eligibility"]["pass"],
                               "improved_pass": entry["improvement"]["pass"],
                               "cagr_10bp": cagr["COST_10BP"], "mean_10bp": primary["all_session_mean"]})
        variants[name] = entry
        log(f"{name} evaluated")
    decision = m1.winner(candidates, rules)
    log(f"verdict {decision['label']}")

    identity = {**p.identity, "m0_rules": m0.RULES_CANONICAL_SHA256,
                "m1_rules": m1.RULES_CANONICAL_SHA256, "code": code_digest()}
    identity_digest = R6.canonical_sha256(identity)
    run_id = f"em1-{identity_digest[:12]}"
    result = {"version": "STRATEGY_E_MAX_M1_RESULT_V1", "run_id": run_id, "identity": identity,
              "identity_digest": identity_digest, "evidence_label": rules["declaration"]["evidence_label"],
              "prechecks": {**p.prechecks, "r0_reproduces_e_r3": r0_identity,
                            "subset_equivalence": True, "invariants": inv,
                            "in_process_deterministic": deterministic, **after},
              "integrity": integrity, "reference_top1_limit": ref_top1,
              "variants": variants, "winner": decision}
    result_bytes = R6.canonical_json(result)
    result_digest = hashlib.sha256(result_bytes).hexdigest()
    files = {"result.json": result_bytes}
    for name in VARIANTS:
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
    summary = {"run_id": run_id, "result_digest": result_digest, "artifacts": digests,
               "verdict": decision["label"]}
    if first is None:
        (run_dir / "COMPLETE.json").write_bytes(R6.canonical_json(summary))
    else:
        check = {"attempt": run_dir.name, "first_result_digest": first["result_digest"],
                 "this_result_digest": result_digest,
                 "identical": first["result_digest"] == result_digest,
                 "artifacts_identical": first["artifacts"] == digests}
        (run_dir / "REPEAT_CHECK.json").write_bytes(R6.canonical_json(check))
        log(f"repeat check {check}")
    log(f"artifacts {run_dir} in {time.time() - started:.0f}s")
    print(f"{decision['label']} run {run_id} digest {result_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
