"""Run Strategy D-AGGRESSIVE phase D-AGG-4 (backtest evaluation).

    cd backend && ../.venv/bin/python -m app.dev.run_strategy_d_agg_d4 --snapshot-id USB-HIST-V1

Parents are fixed (D-AGG-1, D-AGG-2, trading contract). A backtest FAIL is a result, not an error;
the exit code is non-zero only on a HARD FAIL or a noise mismatch (no artifacts are written then).
"""

import argparse
import json
from pathlib import Path
import sys

from app.backtest.strategy_d_agg.config import load_rules
from app.backtest.strategy_d_agg.d4 import execute, run_context, write_run
from app.backtest.strategy_d_agg.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D-AGGRESSIVE D-AGG-4")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--c-raw-root", type=Path, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    workspace_root = resolve_workspace_root(args.workspace_root)
    try:
        result = execute(workspace_root, args.snapshot_id, c_raw_root=args.c_raw_root,
                         rules=load_rules())
    except HardFail as exc:
        print(f"D-AGG-4 HARD FAIL {exc}", file=sys.stderr)
        return 2
    if result.identity is None:
        print(json.dumps(result.report, indent=2, sort_keys=True))
        print(f"D-AGG-4 STOP {result.verdict}")
        return 3
    if not args.no_write:
        print(f"artifacts {write_run(result, run_context(workspace_root, args.snapshot_id))}")
    print(json.dumps({k: v for k, v in result.report.items() if k != "artifacts"},
                     indent=2, sort_keys=True))
    print(f"D-AGG-4 {result.verdict} run {result.identity.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
