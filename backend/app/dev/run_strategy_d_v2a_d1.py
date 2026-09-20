"""Run Strategy D V2-A phase D-V2A-1 (data and PIT pre-validation) on a frozen snapshot.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_v2a_d1 --snapshot-id USB-HIST-V1

The snapshot id is explicit by contract: the CURRENT pointer is recorded in ``run_context.json``
but never followed. Read-only on the shared store; artifacts go under ``data/runtime``.

This phase computes coordinates, ranks, B0 and eligibility. It never searches for a neighbour
and never reads a label value.
"""

import argparse
import json
from pathlib import Path
import sys

from app.backtest.strategy_d_v2.config import load_rules
from app.backtest.strategy_d_v2.d1 import execute, run_context, write_run
from app.backtest.strategy_d_v2.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root

POINTER = "state/historical/CURRENT_HISTORICAL_SNAPSHOT.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D V2-A D1 data / PIT pre-validation")
    parser.add_argument("--snapshot-id", required=True, help="frozen common snapshot, e.g. USB-HIST-V1")
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="shared workspace root (default: discovered Google Drive folder)")
    parser.add_argument("--c-raw-root", type=Path, default=None,
                        help="optional C local raw cache, used only where a common file is absent")
    parser.add_argument("--audit-dates", type=int, default=12,
                        help="how many query dates the truncation audit re-derives")
    parser.add_argument("--no-write", action="store_true", help="print the report without writing it")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    pointer_path = workspace_root / POINTER
    pointer = json.loads(pointer_path.read_text(encoding="utf-8")) if pointer_path.exists() else None
    try:
        result = execute(workspace_root, args.snapshot_id, c_raw_root=args.c_raw_root,
                         rules=load_rules(), audit_dates=args.audit_dates)
    except HardFail as exc:
        print(f"D-V2A-1 HARD FAIL {exc}", file=sys.stderr)
        return 2
    if not args.no_write:
        run_dir = write_run(result, run_context(workspace_root, args.snapshot_id, pointer))
        print(f"artifacts {run_dir}")
    print(json.dumps(result.report, indent=2, sort_keys=True))
    print(f"D-V2A-1 {result.verdict} run {result.identity.run_id}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
