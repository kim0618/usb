"""Run the Strategy E1 premarket / opening-momentum pre-validation.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1

Read-only with respect to every shared store. The B-minute collector may be running; E1 takes no
writer lock, writes nothing under the minute directories, binds itself to the tape observed at
the start, and records the tape digest again at the end so a tape that grew under the study is
visible in the artifact.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import numpy as np

from app.backtest.strategy_d_analog.source import read_set_digest
from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close, load_daily_rows
from app.backtest.strategy_e1_premarket import cohort as C
from app.backtest.strategy_e1_premarket import dataset as D
from app.backtest.strategy_e1_premarket import evaluate, gate, pit_audit, run as run_mod
from app.backtest.strategy_e1_premarket.config import E1HardFail, RulesChanged, load_rules
from app.backtest.workspace.discovery import resolve_workspace_root

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE = REPO_ROOT / "data/runtime/strategy_e_candidate/e1_cache"
PIT_SAMPLE = 40


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy E1 premarket pre-validation")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    try:
        rules = load_rules()
    except (RulesChanged, E1HardFail) as error:
        print(f"E1 STOP {error}", file=sys.stderr)
        return 2
    e0_rules = load_e0_rules()
    if e0_rules.checksum != rules.e0_rules_checksum:
        print(f"E1 STOP the E0 universe declaration moved: {e0_rules.checksum}", file=sys.stderr)
        return 2
    log(f"rules {rules.checksum[:12]} (E0 universe {e0_rules.checksum[:12]})")

    workspace_root = resolve_workspace_root(args.workspace_root)
    binding = json.loads((args.cache / "tape_binding.json").read_text(encoding="utf-8"))
    log(f"tape binding {binding['digest'][:16]} ({binding['files']} files, "
        f"{len(binding['symbols'])} symbols)")

    started = time.time()
    daily, history = load_daily_rows(workspace_root, e0_rules)
    benchmark = load_benchmark_close(workspace_root, e0_rules.snapshot_id, history.panel.sessions)
    log(f"daily rows {len(daily)} in {time.time()-started:.0f}s")

    cohorts = {}
    for tag in ("0925", "0915", "0900"):
        raw = C.load(args.cache / f"premarket_{tag}.npz")
        cohorts[tag] = D.build(raw, daily, benchmark, rules)
        log(f"decision {tag}: {len(cohorts[tag])} rows in the declared universe")
    primary = cohorts["0925"]

    identity = run_mod.run_identity(rules, history.freeze, binding["digest"])
    log(f"run {identity['run_id']}")

    log("study")
    results = run_mod.study(primary, rules, history.panel, history.panel.sessions)
    log("single feature tables")
    single = run_mod.single_feature_tables(primary, results["baseline_primary"], rules)
    log("decision-time robustness")
    robustness = run_mod.decision_time_robustness(
        {k: v for k, v in cohorts.items() if k != "0925"}, rules)

    log("pit audit")
    edges, offsets = M.et_offsets(datetime(2024, 1, 1, tzinfo=timezone.utc),
                                  datetime(2027, 1, 1, tzinfo=timezone.utc))
    sample = sorted(set(binding["symbols"]))[::max(1, len(binding["symbols"]) // PIT_SAMPLE)]
    tapes = [t for t in (M.load_symbol_tape(s, workspace_root, edges, offsets) for s in sample)
             if t is not None]
    audits = [pit_audit.decision_boundary(tapes)]
    raw = C.load(args.cache / "premarket_0925.npz")
    rebuilt = D.build(raw, daily, benchmark, rules)
    grid = list(history.panel.sessions)
    index_of = {s.isoformat(): i for i, s in enumerate(grid)}
    used = np.array([index_of[s.isoformat()] - 1 for s in rebuilt.sessions])
    audits.append(pit_audit.join_boundary(rebuilt.sessions, grid, used, grid))

    verdicts = []
    for name in evaluate.HYPOTHESES:
        block = results["candidates"][name]
        verdicts.append(gate.evaluate(block, rules, block.get("secondary_horizons")))
    verdict = gate.combine(verdicts)
    if any(a["verdict"] != "PASS" for a in audits):
        verdict = "FAIL"
    log(f"verdict {verdict}")

    final_tape, final_files = C.tape_digest(workspace_root, binding["symbols"])
    coverage = {
        "daily": {"snapshot_id": history.freeze.snapshot_id,
                  "grid_start": grid[0].isoformat(), "grid_end": grid[-1].isoformat(),
                  "grid_sessions": len(grid), "daily_rows": len(daily)},
        "premarket": {tag: {"rows": len(rows), **dict(rows.counters)}
                      for tag, rows in cohorts.items()},
        "tape": {"bound_digest": binding["digest"], "bound_files": binding["files"],
                 "final_digest": final_tape, "final_files": final_files,
                 "tape_unchanged": final_tape == binding["digest"]},
    }
    payloads = {
        "run_identity.json": identity,
        "coverage.json": coverage,
        "universe.json": {"counters": dict(primary.counters), "declaration": rules.raw["universe"]},
        "baseline.json": {"primary": results["baseline_primary"],
                          "by_label": run_mod.baseline_block(primary, rules),
                          "by_quarter": results["baseline_by_quarter"],
                          "horizons": results["baseline_horizons"]},
        "single_feature.json": single,
        "candidates.json": results["candidates"],
        "regimes.json": {"regimes": results["regimes"], "halves": results["halves"]},
        "decision_time_robustness.json": robustness,
        "pit_audit.json": audits,
        "gate_results.json": {"verdict": verdict, "primary_label": run_mod.PRIMARY,
                              "hypotheses": verdicts},
    }
    if args.no_write:
        print(json.dumps({"run_id": identity["run_id"], "verdict": verdict, "gate": verdicts},
                         indent=1, default=run_mod.json_default))
        return 0 if verdict != "FAIL" else 1

    run_dir = REPO_ROOT / run_mod.RUNS_DIR / identity["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    digests = {name: run_mod.write(run_dir / name, payload) for name, payload in payloads.items()}
    final_read = read_set_digest(workspace_root, e0_rules.snapshot_id)
    context = {
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": run_mod.git_head(REPO_ROOT), "workspace_root": str(workspace_root),
        "read_set_digest_at_load": history.freeze.d_read_digest,
        "read_set_digest_after_run": final_read,
        "daily_inputs_unchanged": final_read == history.freeze.d_read_digest,
        "premarket_tape_unchanged": coverage["tape"]["tape_unchanged"],
        "provider_calls": 0, "writer_lock_taken": False,
    }
    run_mod.write(run_dir / "run_context.json", context)
    run_mod.write(run_dir / "COMPLETE.json",
                  {"run_id": identity["run_id"], "verdict": verdict, "artifacts": digests,
                   "daily_inputs_unchanged": context["daily_inputs_unchanged"],
                   "premarket_tape_unchanged": context["premarket_tape_unchanged"]})
    log(f"artifacts {run_dir}")
    print(f"E1 {verdict} run {identity['run_id']}")
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
