"""Run Strategy D phase D3 (forward distribution and signal construction) on a finished D2 run.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_signal \
        --snapshot-id USB-HIST-V1 --parent-run dneigh1-fe0362523739

The neighbours are not re-selected here: the D2 run named by ``--parent-run`` is immutable input
and every table D3 reads is verified against the digest D2 recorded for it. D3 stops at S(q) and
sigma(q); it computes no IC, no baseline and no verdict.
"""

import argparse
from pathlib import Path
import sys

from app.backtest.strategy_d_analog.config import load_rules
from app.backtest.strategy_d_analog.d3 import RUNS_DIR, execute, finish, peak_rss_mb, run_context
from app.backtest.strategy_d_analog.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D D3 signal construction")
    parser.add_argument("--snapshot-id", required=True, help="frozen common snapshot, e.g. USB-HIST-V1")
    parser.add_argument("--parent-run", required=True, help="the finished D2 run to consume")
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="shared workspace root (default: discovered Google Drive folder)")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR, help="where this run is written")
    parser.add_argument("--parent-dir", type=Path, default=RUNS_DIR,
                        help="where the parent D2 run lives")
    parser.add_argument("--c-raw-root", type=Path, default=None,
                        help="optional C local raw cache, used only where a common file is absent")
    parser.add_argument("--test-limit", type=int, default=None,
                        help="smoke run: use only the first N of the 14 tests")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    try:
        result = execute(workspace_root, args.snapshot_id,
                         parent_dir=args.parent_dir / args.parent_run, rules=load_rules(),
                         c_raw_root=args.c_raw_root, runs_dir=args.runs_dir,
                         test_limit=args.test_limit)
        result.summary["performance"]["peak_rss_mb"] = round(peak_rss_mb(), 1)
        run_dir = finish(result, run_context(workspace_root, args.snapshot_id))
    except HardFail as exc:
        print(f"D3 HARD FAIL {exc}", file=sys.stderr)
        return 2
    print(f"artifacts {run_dir}")
    print(f"D3 {result.verdict} run {result.identity.run_id} "
          f"elapsed {result.summary['performance']['elapsed_seconds']}s "
          f"peak RSS {result.summary['performance']['peak_rss_mb']} MB")
    for name, digest in sorted(result.digests.items()):
        print(f"  {name:22s} {digest}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
