"""Run Strategy D V2-A phase D-V2A-4 (screening verdict) on a frozen snapshot.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_v2a_d4 \
        --snapshot-id USB-HIST-V1 \
        --parent-d1-run-id dv2a1-... --parent-d2-run-id dv2a2-... --parent-d3-run-id dv2a3-...

All three parents are explicit, as in D3: a phase that picked "the newest run" would change study
identity the moment an unrelated run appeared. This is the phase that first compares the signal
to the outcome, so the exit code carries two different facts - the process exits non-zero when
the phase itself failed a hard check, never because the screening verdict was FAIL. A FAIL is a
result, and a runner that treated it as an error would make "no advantage found" look like a bug.
"""

import argparse
import json
from pathlib import Path
import sys

from app.backtest.strategy_d_v2.config import load_rules
from app.backtest.strategy_d_v2.d4 import execute, run_context, write_run
from app.backtest.strategy_d_v2.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root

POINTER = "state/historical/CURRENT_HISTORICAL_SNAPSHOT.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D V2-A D4 screening verdict")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--parent-d1-run-id", required=True)
    parser.add_argument("--parent-d2-run-id", required=True)
    parser.add_argument("--parent-d3-run-id", required=True)
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--c-raw-root", type=Path, default=None)
    parser.add_argument("--no-secondaries", action="store_true",
                        help="skip the slots that need the panel; the gate is unaffected")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    pointer_path = workspace_root / POINTER
    pointer = json.loads(pointer_path.read_text(encoding="utf-8")) if pointer_path.exists() else None
    try:
        result = execute(workspace_root, args.snapshot_id,
                         parent_d1_run_id=args.parent_d1_run_id,
                         parent_d2_run_id=args.parent_d2_run_id,
                         parent_d3_run_id=args.parent_d3_run_id,
                         c_raw_root=args.c_raw_root, rules=load_rules(),
                         secondaries=not args.no_secondaries)
    except HardFail as exc:
        print(f"D-V2A-4 HARD FAIL {exc}", file=sys.stderr)
        return 2
    if not args.no_write:
        run_dir = write_run(result, run_context(workspace_root, args.snapshot_id, pointer))
        print(f"artifacts {run_dir}")
    print(json.dumps({k: v for k, v in result.report.items() if k != "artifacts"},
                     indent=2, sort_keys=True))
    print(f"D-V2A-4 phase {result.verdict} / {result.screening} run {result.identity.run_id}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
