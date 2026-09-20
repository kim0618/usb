"""Run the Strategy E1-H5 confirmation.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_h5_confirm

Read-only. Takes no writer lock, makes no provider call, and does not touch the running B-minute
collector. H5 is evaluated by importing the E1 mask unchanged, so the confirmation block is
processed by exactly the code that produced the development result.
"""

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

from app.backtest.strategy_d_analog.source import read_set_digest
from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close, load_daily_rows
from app.backtest.strategy_e1_h5_confirm import gate, run as run_mod
from app.backtest.strategy_e1_h5_confirm.config import (
    ConfirmHardFail, RulesChanged, load_rules,
)
from app.backtest.strategy_e1_premarket import cohort as C, dataset as D
from app.backtest.strategy_e1_premarket.config import load_rules as load_e1_rules
from app.backtest.workspace.discovery import resolve_workspace_root

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE = REPO_ROOT / "data/runtime/strategy_e_candidate/e1_cache"


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy E1-H5 confirmation")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    try:
        rules = load_rules()
    except (RulesChanged, ConfirmHardFail) as error:
        print(f"CONFIRMATION STOP {error}", file=sys.stderr)
        return 2
    e1_rules, e0_rules = load_e1_rules(), load_e0_rules()
    if e1_rules.checksum != rules.e1_rules_checksum or e0_rules.checksum != rules.e0_rules_checksum:
        print("CONFIRMATION STOP an upstream declaration moved", file=sys.stderr)
        return 2
    log(f"rules {rules.checksum[:12]} (E1 {e1_rules.checksum[:12]}, E0 {e0_rules.checksum[:12]})")

    workspace_root = resolve_workspace_root(args.workspace_root)
    development_binding = json.loads((args.cache / "tape_binding.json").read_text(encoding="utf-8"))
    confirmation_binding = json.loads(
        (args.cache / "confirmation_binding.json").read_text(encoding="utf-8"))
    log(f"development {len(development_binding['symbols'])} symbols; "
        f"confirmation {confirmation_binding['count']} symbols "
        f"({confirmation_binding['digest'][:16]})")

    started = time.time()
    daily, history = load_daily_rows(workspace_root, e0_rules)
    benchmark = load_benchmark_close(workspace_root, e0_rules.snapshot_id, history.panel.sessions)
    log(f"daily rows {len(daily)} in {time.time()-started:.0f}s")

    blocks = {}
    for label, filename in (("DEVELOPMENT", "premarket_0925.npz"),
                            ("CONFIRMATION", "confirmation_premarket_0925.npz")):
        raw = C.load(args.cache / filename)
        blocks[label] = D.build(raw, daily, benchmark, e1_rules)
        selected = run_mod.h5_mask(blocks[label])
        log(f"{label}: {len(blocks[label])} universe rows, {int(selected.sum())} H5 rows")

    identity = run_mod.run_identity(rules, history.freeze, development_binding["digest"],
                                    confirmation_binding["digest"])
    log(f"run {identity['run_id']}")

    results = {}
    for label, rows in blocks.items():
        log(f"analysing {label}")
        results[label] = run_mod.analyse(rows, rules, label)
        results[label]["regimes"] = run_mod.regimes(rows, rules)

    # A final holdout would have to be a chronological block of genuinely new data. The collector
    # advances alphabetically over a fixed window and the daily snapshot is frozen, so no such
    # block exists; the declaration says to record that rather than fake one from a symbol split.
    confirmation_sessions = sorted({s.isoformat() for s in blocks["CONFIRMATION"].sessions})
    development_sessions = sorted({s.isoformat() for s in blocks["DEVELOPMENT"].sessions})
    holdout = {
        "constructible": False,
        "reason": "the confirmation block covers no session the development block did not: "
                  f"{len(set(confirmation_sessions) - set(development_sessions))} of "
                  f"{len(confirmation_sessions)} confirmation sessions are new",
        "confirmation_session_range": [confirmation_sessions[0], confirmation_sessions[-1]]
        if confirmation_sessions else None,
        "development_session_range": [development_sessions[0], development_sessions[-1]]
        if development_sessions else None,
        "daily_snapshot_frozen_at": history.freeze.last_session,
    }
    verdict_block = gate.evaluate(results["CONFIRMATION"], rules, holdout_available=False)
    log(f"verdict {verdict_block['verdict']}")

    yield_per_symbol = (results["DEVELOPMENT"]["h5_rows"]
                        / max(len(development_binding["symbols"]), 1))
    shortfall = {
        "confirmation_h5_rows": results["CONFIRMATION"]["h5_rows"],
        "required_rows": rules.min_confirmation_rows,
        "development_yield_h5_rows_per_symbol": yield_per_symbol,
        "additional_symbols_required": (
            int(np.ceil((rules.min_confirmation_rows - results["CONFIRMATION"]["h5_rows"])
                        / yield_per_symbol)) if yield_per_symbol > 0 else None),
        "note": "a symbol count, not a session count. New symbols do not create a chronological "
                "holdout, which is a separate and currently unmet requirement.",
    }

    payloads = {
        "run_identity.json": identity,
        "splits.json": {"development": {"symbols": len(development_binding["symbols"]),
                                        "digest": development_binding["digest"],
                                        "universe_rows": results["DEVELOPMENT"]["rows"],
                                        "h5_rows": results["DEVELOPMENT"]["h5_rows"]},
                        "confirmation": {"symbols": confirmation_binding["count"],
                                         "digest": confirmation_binding["digest"],
                                         "universe_rows": results["CONFIRMATION"]["rows"],
                                         "h5_rows": results["CONFIRMATION"]["h5_rows"]},
                        "final_holdout": holdout},
        "development.json": results["DEVELOPMENT"],
        "confirmation.json": results["CONFIRMATION"],
        "sample_shortfall.json": shortfall,
        "gate_results.json": verdict_block,
    }
    if args.no_write:
        print(json.dumps({"run_id": identity["run_id"], **verdict_block}, indent=1,
                         default=run_mod.json_default))
        return 0

    run_dir = REPO_ROOT / run_mod.RUNS_DIR / identity["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    digests = {name: run_mod.write(run_dir / name, payload) for name, payload in payloads.items()}
    final_read = read_set_digest(workspace_root, e0_rules.snapshot_id)
    final_conf, final_files = C.tape_digest(workspace_root,
                                            confirmation_binding["confirmation_symbols"])
    context = {
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": run_mod.git_head(REPO_ROOT), "workspace_root": str(workspace_root),
        "read_set_digest_at_load": history.freeze.d_read_digest,
        "read_set_digest_after_run": final_read,
        "daily_inputs_unchanged": final_read == history.freeze.d_read_digest,
        "confirmation_tape_unchanged": final_conf == confirmation_binding["digest"],
        "provider_calls": 0, "writer_lock_taken": False,
    }
    run_mod.write(run_dir / "run_context.json", context)
    run_mod.write(run_dir / "COMPLETE.json",
                  {"run_id": identity["run_id"], "verdict": verdict_block["verdict"],
                   "promotes_to_strategy_e": verdict_block["promotes_to_strategy_e"],
                   "artifacts": digests, **{k: context[k] for k in
                                            ("daily_inputs_unchanged",
                                             "confirmation_tape_unchanged")}})
    log(f"artifacts {run_dir}")
    print(f"E1-H5 CONFIRMATION {verdict_block['verdict']} run {identity['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
