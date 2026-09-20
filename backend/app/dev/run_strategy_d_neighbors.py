"""Run Strategy D phase D2 (pattern library and Top-K neighbour search) on a frozen snapshot.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_neighbors \
        --snapshot-id USB-HIST-V1 --parent-run dpit1-2ec7d609bf56

The dataset is not chosen here: it is read out of the D1 run named by ``--parent-run`` and the
load refuses anything whose freeze, source, read-set or grid digest differs. D2 writes neighbour
identities under ``data/runtime`` and touches no production path.
"""

import argparse
from pathlib import Path
import sys

from app.backtest.strategy_d_analog import pit_audit
from app.backtest.strategy_d_analog.config import load_rules
from app.backtest.strategy_d_analog.d2 import (
    RUNS_DIR, execute, finish, freeze_from_d1, peak_rss_mb, run_context,
)
from app.backtest.strategy_d_analog.models import HardFail
from app.backtest.workspace.discovery import resolve_workspace_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy D D2 neighbour search")
    parser.add_argument("--snapshot-id", required=True, help="frozen common snapshot, e.g. USB-HIST-V1")
    parser.add_argument("--parent-run", required=True, help="the D1 run whose dataset D2 must match")
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="shared workspace root (default: discovered Google Drive folder)")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR,
                        help="where this D2 run is written")
    parser.add_argument("--parent-dir", type=Path, default=RUNS_DIR,
                        help="where the parent D1 run lives (default: the same runs directory)")
    parser.add_argument("--c-raw-root", type=Path, default=None,
                        help="optional C local raw cache, used only where a common file is absent")
    parser.add_argument("--eval-date-limit", type=int, default=None,
                        help="smoke run: use only the first N evaluation dates")
    parser.add_argument("--audit-dates", type=int, default=12,
                        help="query dates re-run on a physically truncated dataset")
    parser.add_argument("--skip-truncation-audit", action="store_true",
                        help="skip the truncated re-run (the run is then not gate evidence)")
    args = parser.parse_args(argv)

    workspace_root = resolve_workspace_root(args.workspace_root)
    try:
        expected, parent = freeze_from_d1(args.parent_dir / args.parent_run)
        result = execute(workspace_root, args.snapshot_id, expected=expected, parent_run=parent,
                         rules=load_rules(), c_raw_root=args.c_raw_root, runs_dir=args.runs_dir,
                         eval_date_limit=args.eval_date_limit, audit_count=args.audit_dates)
        if args.skip_truncation_audit:
            audit = {"passed": None, "skipped": True}
        else:
            audit = pit_audit.audit_run(result.context.history, result.context.rules,
                                        result.context.audit_dates, result.context.capture,
                                        log=print)
            if not audit["passed"]:
                raise HardFail("R4", f"truncated re-run disagrees: {audit['mismatched']}")
        result.summary["truncation_audit"] = audit
        result.summary["performance"]["peak_rss_mb"] = round(peak_rss_mb(), 1)
        run_dir = finish(result, run_context(workspace_root, args.snapshot_id))
    except HardFail as exc:
        print(f"D2 HARD FAIL {exc}", file=sys.stderr)
        return 2
    print(f"artifacts {run_dir}")
    print(f"D2 {result.verdict} run {result.identity.run_id} "
          f"elapsed {result.summary['performance']['elapsed_seconds']}s "
          f"peak RSS {result.summary['performance']['peak_rss_mb']} MB")
    for name, digest in sorted(result.digests.items()):
        print(f"  {name:28s} {digest}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
