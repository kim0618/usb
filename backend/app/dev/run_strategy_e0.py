"""Run the Strategy E0 overnight / closing-strength pre-validation.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e0

Read-only with respect to every shared store: the frozen common snapshot and the minute tape
are read, artifacts are written under ``data/runtime/strategy_e_candidate/runs/<run_id>/``, and
no provider API call is made. The snapshot id, the universe and every threshold come from the
frozen declaration, not from the command line - the only options here are where the workspace
is, where the minute cache lives and whether to write.
"""

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

from app.backtest.strategy_d_analog.source import read_set_digest
from app.backtest.strategy_e0_overnight import cohorts as cohort_mod
from app.backtest.strategy_e0_overnight import evaluate, gate, pit_audit, run as run_mod
from app.backtest.strategy_e0_overnight.config import E0HardFail, RulesChanged, load_rules
from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close, load_daily_rows
from app.backtest.workspace.discovery import resolve_workspace_root

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = REPO_ROOT / "data/runtime/strategy_e_candidate/minute_cache"


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def build_or_load_cohorts(workspace_root: Path, sessions, cache: Path, *, workers: int,
                          rebuild: bool) -> dict[str, dict]:
    cache.mkdir(parents=True, exist_ok=True)
    deep = cohort_mod.deep_symbols(workspace_root)
    available = cohort_mod.available_symbols(workspace_root)
    broad = tuple(s for s in available if s not in set(deep))
    out: dict[str, dict] = {}
    for name, symbols in (("M1_DEEP", deep), ("M2_BROAD", broad)):
        path = cache / f"{name}.npz"
        report_path = cache / f"{name}_report.json"
        if path.exists() and report_path.exists() and not rebuild:
            log(f"{name}: reusing cache {path.name}")
            out[name] = {"columns": cohort_mod.load_cohort(path),
                         "report": json.loads(report_path.read_text(encoding="utf-8"))}
            continue
        log(f"{name}: building from {len(symbols)} symbols")
        built = cohort_mod.build_cohort(name, symbols, workspace_root, sessions,
                                        workers=workers, progress=log)
        cohort_mod.save_cohort(built, path)
        report_path.write_text(json.dumps(built.report, indent=1))
        out[name] = {"columns": cohort_mod.load_cohort(path), "report": built.report}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy E0 overnight pre-validation")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--c-raw-root", type=Path, default=None)
    parser.add_argument("--minute-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--rebuild-minute", action="store_true")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    try:
        rules = load_rules()
    except (RulesChanged, E0HardFail) as error:
        print(f"E0 STOP {error}", file=sys.stderr)
        return 2
    log(f"rules {rules.checksum[:12]} snapshot {rules.snapshot_id}")

    workspace_root = resolve_workspace_root(args.workspace_root)
    log(f"workspace {workspace_root}")
    started = time.time()
    rows, history = load_daily_rows(workspace_root, rules, c_raw_root=args.c_raw_root)
    benchmark = load_benchmark_close(workspace_root, rules.snapshot_id, history.panel.sessions,
                                     c_raw_root=args.c_raw_root)
    log(f"daily rows {len(rows)} in {time.time()-started:.0f}s")

    built = build_or_load_cohorts(workspace_root, history.panel.sessions, args.minute_cache,
                                  workers=args.workers, rebuild=args.rebuild_minute)
    joins = {name: cohort_mod.join_to_daily(block["columns"], rows.sessions, rows.tickers,
                                            rows.session_idx, rows.ticker_idx)
             for name, block in built.items()}
    for name, block in joins.items():
        log(f"{name}: {block['joined_rows']}/{block['minute_rows']} minute rows in the "
            f"declared universe ({block['joined_share']*100:.1f}%)")

    identity = run_mod.run_identity(rules, history, cohort_mod.cohort_digest(joins))
    log(f"run {identity['run_id']}")

    log("daily study")
    daily = run_mod.daily_study(rows, rules)
    log("single feature tables")
    single = run_mod.single_feature_tables(rows, daily["baseline"], rules)
    log("minute study")
    minute = run_mod.minute_study(rows, {k: v["columns"] for k, v in joins.items()}, rules)
    log("pit audit")
    audits = [pit_audit.run(history, benchmark, rules, rows, cut=cut) for cut in run_mod.PIT_CUTS]

    verdicts = [gate.evaluate(daily["hypotheses"][name], rules)
                for name in evaluate.DAILY_HYPOTHESES]
    for cohort, block in minute.items():
        for name in evaluate.MINUTE_HYPOTHESES:
            result = gate.evaluate(block[name], rules)
            result["name"] = f"{cohort}:{name}"
            verdicts.append(result)
    verdict = gate.combine(verdicts)
    if any(a["verdict"] != "PASS" for a in audits):
        verdict = "FAIL"
    log(f"verdict {verdict}")

    coverage = run_mod.coverage_report(rows, history, {k: v["report"] for k, v in built.items()})
    for name, block in joins.items():
        coverage["minute"][name].update({k: v for k, v in block.items() if k != "columns"})

    payloads = {
        "run_identity.json": identity,
        "coverage.json": coverage,
        "universe.json": {"counters": dict(rows.counters), "declaration": rules.raw["universe"]},
        "baseline.json": {"overnight": daily["baseline"], "by_quarter": daily["baseline_by_quarter"]},
        "single_feature.json": single,
        "hypotheses_daily.json": daily["hypotheses"],
        "minute_study.json": minute,
        "pit_audit.json": audits,
        "gate_results.json": {"verdict": verdict, "hypotheses": verdicts},
    }
    if args.no_write:
        print(json.dumps({"run_id": identity["run_id"], "verdict": verdict,
                          "gate": verdicts}, indent=1, default=run_mod._json_default))
        return 0 if verdict != "FAIL" else 1

    run_dir = REPO_ROOT / run_mod.RUNS_DIR / identity["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    digests = {name: run_mod._write(run_dir / name, payload) for name, payload in payloads.items()}
    final_read_digest = read_set_digest(workspace_root, rules.snapshot_id,
                                        c_raw_root=args.c_raw_root)
    context = {
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": run_mod._git_head(REPO_ROOT),
        "workspace_root": str(workspace_root),
        "minute_cache": str(args.minute_cache),
        "read_set_digest_at_load": history.freeze.d_read_digest,
        "read_set_digest_after_run": final_read_digest,
        "inputs_unchanged": final_read_digest == history.freeze.d_read_digest,
        "provider_calls": 0,
    }
    run_mod._write(run_dir / "run_context.json", context)
    run_mod._write(run_dir / "COMPLETE.json",
                   {"run_id": identity["run_id"], "verdict": verdict, "artifacts": digests,
                    "inputs_unchanged": context["inputs_unchanged"]})
    log(f"artifacts {run_dir}")
    print(f"E0 {verdict} run {identity['run_id']}")
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
