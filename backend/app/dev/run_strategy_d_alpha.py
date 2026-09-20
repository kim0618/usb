"""Run Strategy D phase D4 (GATE-D-ALPHA) on a finished D3 run and its D2 parent.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_alpha \
        --snapshot-id USB-HIST-V1 --d3-run dsig1-eaeb6df9dea6 --d2-run dneigh1-fe0362523739

D4 judges the pre-registered conditions and nothing else. It does not re-select neighbours, does
not recompute S(q), and has no path that adjusts a threshold after seeing a number.
"""

import argparse
from pathlib import Path
import sys

from app.backtest.strategy_d_analog.config import load_rules
from app.backtest.strategy_d_analog.d4 import (
    RUNS_DIR, execute, finish, peak_rss_mb, run_context,
)
from app.backtest.strategy_d_analog.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D D4 alpha pre-validation")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--d3-run", required=True, help="the finished D3 run to evaluate")
    parser.add_argument("--d2-run", required=True, help="its D2 parent, for the library identities")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--parent-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--c-raw-root", type=Path, default=None)
    parser.add_argument("--combination-limit", type=int, default=None,
                        help="smoke run: use only the first N of the 7 (W, h) combinations")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    try:
        result = execute(workspace_root, args.snapshot_id,
                         d3_dir=args.parent_dir / args.d3_run,
                         d2_dir=args.parent_dir / args.d2_run, rules=load_rules(),
                         c_raw_root=args.c_raw_root, runs_dir=args.runs_dir,
                         combination_limit=args.combination_limit)
        result.summary["performance"]["peak_rss_mb"] = round(peak_rss_mb(), 1)
        run_dir = finish(result, run_context(workspace_root, args.snapshot_id))
    except HardFail as exc:
        print(f"D4 HARD FAIL {exc}", file=sys.stderr)
        return 2
    print(f"artifacts {run_dir}")
    print(f"GATE-D-ALPHA = {result.decision['decision']}: {result.decision['reason']}")
    print(f"elapsed {result.summary['performance']['elapsed_seconds']}s "
          f"peak RSS {result.summary['performance']['peak_rss_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
