"""Run Strategy D phase D1 (data and PIT feasibility) against a frozen common snapshot.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_d1 --snapshot-id USB-HIST-V1

The snapshot id is explicit by contract: the CURRENT pointer is recorded in ``run_context.json``
but never followed, because the pointer may move to a later dataset while a D study is bound to
this one. Read-only: D1 reads frozen files and writes its report under ``data/runtime``.
"""

import argparse
import json
from pathlib import Path
import sys

from app.backtest.strategy_d_analog.config import load_rules
from app.backtest.strategy_d_analog.d1 import execute, run_context, write_run
from app.backtest.strategy_d_analog.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root

POINTER = "state/historical/CURRENT_HISTORICAL_SNAPSHOT.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D D1 data / PIT feasibility")
    parser.add_argument("--snapshot-id", required=True, help="frozen common snapshot, e.g. USB-HIST-V1")
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="shared workspace root (default: discovered Google Drive folder)")
    parser.add_argument("--c-raw-root", type=Path, default=None,
                        help="optional C local raw cache, used only where a common file is absent")
    parser.add_argument("--allow-duplicate-rows", action="store_true",
                        help="measure a duplicated grouped ticker row (B8) instead of failing on it")
    parser.add_argument("--no-write", action="store_true", help="print the report without writing it")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    pointer_path = workspace_root / POINTER
    pointer = json.loads(pointer_path.read_text(encoding="utf-8")) if pointer_path.exists() else None
    try:
        result = execute(workspace_root, args.snapshot_id, c_raw_root=args.c_raw_root,
                         rules=load_rules(), duplicate_rows_allowed=args.allow_duplicate_rows)
    except HardFail as exc:
        print(f"D1 HARD FAIL {exc}", file=sys.stderr)
        return 2
    if not args.no_write:
        run_dir = write_run(result, run_context(workspace_root, args.snapshot_id, pointer))
        print(f"artifacts {run_dir}")
    print(json.dumps(result.report, indent=2, sort_keys=True))
    print(f"D1 {result.verdict} run {result.identity.run_id}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
