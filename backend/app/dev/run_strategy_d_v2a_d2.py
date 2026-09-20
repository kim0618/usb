"""Run Strategy D V2-A phase D-V2A-2 (structure analog engine) on a frozen snapshot.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_v2a_d2 \
        --snapshot-id USB-HIST-V1 --parent-run-id dv2a1-cec5cc9bc674

The D1 parent is explicit: a phase that binds itself to "the newest run in the directory" would
change study identity when an unrelated run appears. Read-only on the shared store; artifacts go
under ``data/runtime/strategy_d_v2/runs``.

This phase searches for neighbours. It never reads a forward return.
"""

import argparse
import json
from pathlib import Path
import sys

from app.backtest.strategy_d_v2.config import load_rules
from app.backtest.strategy_d_v2.d2 import execute, run_context, write_run
from app.backtest.strategy_d_v2.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root

POINTER = "state/historical/CURRENT_HISTORICAL_SNAPSHOT.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D V2-A D2 structure analog engine")
    parser.add_argument("--snapshot-id", required=True, help="frozen common snapshot")
    parser.add_argument("--parent-run-id", required=True, help="the D-V2A-1 run to bind to")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--c-raw-root", type=Path, default=None)
    parser.add_argument("--full-sort-dates", type=int, default=12)
    parser.add_argument("--truncate-dates", type=int, default=3)
    parser.add_argument("--mutation-dates", type=int, default=3)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    pointer_path = workspace_root / POINTER
    pointer = json.loads(pointer_path.read_text(encoding="utf-8")) if pointer_path.exists() else None
    try:
        result = execute(workspace_root, args.snapshot_id, parent_run_id=args.parent_run_id,
                         c_raw_root=args.c_raw_root, rules=load_rules(),
                         full_sort_dates=args.full_sort_dates,
                         truncate_dates=args.truncate_dates,
                         mutation_dates=args.mutation_dates)
    except HardFail as exc:
        print(f"D-V2A-2 HARD FAIL {exc}", file=sys.stderr)
        return 2
    if not args.no_write:
        run_dir = write_run(result, run_context(workspace_root, args.snapshot_id, pointer))
        print(f"artifacts {run_dir}")
    print(json.dumps({k: v for k, v in result.report.items() if k != "artifacts"},
                     indent=2, sort_keys=True))
    print(f"D-V2A-2 {result.verdict} run {result.identity.run_id}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
