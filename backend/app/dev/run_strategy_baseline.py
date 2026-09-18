"""Run the Current Strategy Baseline V1: Production Strategy V0, unchanged, over a year.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_baseline --preflight-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_baseline
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_baseline --verify-determinism

Every figure is RESEARCH_ONLY: the candidate field is a research scan of a declared
universe, every approval is assumed, overnight is UNKNOWN_CLOSE and trailing is
UNKNOWN_DEFAULT. Starting cash is the declared research unit (10,000 USD,
ASSUMED_RESEARCH); no account is read.

The minute preflight runs first. A symbol whose tape does not cover the planned range
stops the run with the exact collector command to fill it; this CLI never fetches. A run
identity that is already COMPLETE in ``backtest/runs`` is a cache hit and nothing is
replayed or written. No database is opened, no request is made, nothing is deployed.
"""

import argparse
from collections.abc import Sequence
from datetime import date, datetime, timezone
import json
from pathlib import Path
import time

from app.backtest.baseline.binding import SnapshotBinding
from app.backtest.baseline.contract import STARTING_CASH
from app.backtest.baseline.coverage import symbols_needing_collection
from app.backtest.baseline.errors import BaselineError
from app.backtest.baseline.runner import (
    BaselineInputs, baseline_identity, execute, plan,
)
from app.backtest.baseline.storage import assemble, load_marker, write_artifacts
from app.backtest.basis.errors import BasisError
from app.backtest.replay.errors import ReplayError
from app.backtest.research.daily import previous_completed_session
from app.backtest.research.errors import ResearchScannerError
from app.backtest.research.universe_file import load_universe_file
from app.backtest.workspace.discovery import resolve_workspace_root
from app.backtest.workspace.errors import WorkspaceError
from app.backtest.workspace.layout import Workspace
from app.dev.observe_entry_drift import not_run
from app.dev.run_massive_spike import MARKET_TIMEZONE
from app.dev.run_research_scanner_coverage import load_metadata
from app.execution.config import ExecutionConfig
from app.market.calendar import MarketCalendar
from app.risk.config import RiskConfig
from app.strategy.engine import StrategyV0Engine

DEFAULT_UNIVERSE = Path("docs/backtest/research_universe_v1.json")
DEFAULT_METADATA = Path("data/runtime/authority_snapshots/usb_real_market_review.snapshot.sqlite3")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Current Strategy Baseline V1 (RESEARCH_ONLY)")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--metadata-from-recorded", type=Path, default=None,
                        help=f"default: the universe's declared metadata_artifact if it has "
                             f"one, otherwise {DEFAULT_METADATA}")
    parser.add_argument("--recorded-run", type=int, default=2)
    parser.add_argument("--start", type=_parse_date, default=date(2025, 9, 16),
                        help="requested first entry session")
    parser.add_argument("--end", type=_parse_date, default=date(2026, 9, 15),
                        help="requested last entry session")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--historical-snapshot", default=None, metavar="SNAPSHOT_ID",
                        help="read minute and daily tape from this frozen Common Historical "
                             "Store snapshot (A STRICT view) instead of the collector manifest")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--verify-determinism", action="store_true",
                        help="replay again in memory and compare every artifact digest")
    parser.add_argument("--dry-run", type=Path, default=None, metavar="LOCAL_DIR",
                        help="replay and validate, write the artifacts under LOCAL_DIR only; "
                             "nothing is written to the workspace and no cache is read")
    parser.add_argument("--json-out", type=Path, default=None,
                        help="also write the preflight/outcome lines as JSON (local only)")
    return parser


def main(argv: Sequence[str] | None = None,
         clock=lambda: datetime.now(timezone.utc), timer=time.monotonic) -> int:  # type: ignore[no-untyped-def]
    args = build_parser().parse_args(argv)
    calendar = MarketCalendar(MARKET_TIMEZONE)
    if args.metadata is not None:
        args.metadata_from_recorded = None
    try:
        workspace = Workspace(resolve_workspace_root(args.workspace_root, must_exist=True))
    except WorkspaceError as error:
        raise not_run(str(error)) from None
    try:
        universe = load_universe_file(args.universe)
        declared = (args.metadata is None and args.metadata_from_recorded is None
                    and universe.metadata_artifact is not None)
        if args.metadata is None and args.metadata_from_recorded is None and not declared:
            args.metadata_from_recorded = DEFAULT_METADATA
        metadata = load_metadata(args, universe=universe, workspace=workspace)
    except (ResearchScannerError, BasisError) as error:
        print(f"refused: {type(error).__name__}: {error}")
        return 1
    if declared:
        artifact = universe.metadata_artifact
        source = (f"declared metadata artifact {artifact.workspace_path} "  # type: ignore[union-attr]
                  f"(sha256 {artifact.sha256[:12]})")  # type: ignore[union-attr]
    else:
        source = (f"recorded scanner run {args.recorded_run} ({args.metadata_from_recorded.name})"
                  if args.metadata_from_recorded is not None else f"declared file {args.metadata}")
    binding = None
    if args.historical_snapshot is not None:
        try:
            binding = SnapshotBinding.open(workspace, args.historical_snapshot, calendar)
        except ReplayError as error:
            print(f"refused: {getattr(error, 'code', type(error).__name__)}: {error}")
            return 1
        print(f"historical_snapshot={args.historical_snapshot} "
              f"sha256={binding.snapshot.snapshot_sha256} view={binding.view}")
    inputs = BaselineInputs(workspace=workspace, universe=universe, metadata=metadata,
                            metadata_source=source, requested_start=args.start,
                            requested_end=args.end,
                            completed_through=previous_completed_session(clock(), calendar),
                            calendar=calendar, starting_cash=STARTING_CASH, binding=binding)
    report: dict[str, object] = {}
    try:
        planned = plan(inputs)
    except (BaselineError, ReplayError, ResearchScannerError) as error:
        print(f"refused: {getattr(error, 'code', type(error).__name__)}: {error}")
        return 1
    print(f"workspace_root={workspace.root}")
    print("=== A. MINUTE COVERAGE PREFLIGHT")
    for key, value in planned.range.as_dict().items():
        print(f"range.{key}={value}")
    print(f"daily_scan_coverage={planned.daily_scan_start}..{planned.daily_scan_end}")
    print(f"declared={','.join(planned.declared)}")
    print("not_scanned=" + (",".join(f"{k}={v}" for k, v in planned.not_scanned.items()) or "none"))
    print("metadata_excluded=" + (",".join(f"{k}={v}" for k, v
                                            in planned.metadata_excluded.items()) or "none"))
    for item in planned.coverage:
        row = item.as_dict()
        print(" ".join(f"{key}={value}" for key, value in row.items()))
    report["preflight"] = {"range": planned.range.as_dict(),
                           "coverage": [item.as_dict() for item in planned.coverage]}
    missing = symbols_needing_collection(planned.coverage)
    if missing:
        print("MINUTE COVERAGE: NOT READY")
        if binding is not None:
            # A frozen snapshot is never filled in place: a gap is a view decision or a new
            # snapshot id, not a collector command.
            for item in planned.coverage:
                if not item.covered:
                    print(f"  snapshot gap: {item.symbol} {item.detail} "
                          f"first={item.missing_sessions[0] if item.missing_sessions else None}")
            return 1
        for symbol in missing:
            print(f"  collect: PYTHONPATH=backend .venv/bin/python -m "
                  f"app.dev.collect_historical_massive --symbol {symbol} "
                  f"--start {planned.range.minute_required_start} "
                  f"--end {planned.range.minute_required_end} --overwrite-partitions")
        return 1
    print("MINUTE COVERAGE: READY")
    if args.preflight_only:
        return 0
    engine, risk, execution = StrategyV0Engine(), RiskConfig(), ExecutionConfig()
    try:
        identity = baseline_identity(planned,
                                     inputs.data_binding().minute_identities(planned.required),
                                     engine=engine, risk=risk, execution=execution)
        print(f"run_id={identity.run_id}")
        print(f"run_identity={identity.run_identity}")
        if args.dry_run is not None:
            run = execute(planned, engine=engine, risk=risk, execution=execution, timer=timer)
            print(f"dry_run=YES replay_seconds={run.elapsed_seconds:.2f} workspace_written=NO")
            for relative, body in sorted(run.artifacts.files.items()):
                target = args.dry_run / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                print(f"local {target} sha256={run.artifacts.digests()[relative]}")
            print_outputs(run.document)
            return 0
        marker = load_marker(workspace, identity.run_id, run_identity=identity.run_identity)
        if marker is not None:
            print("cache_hit=YES replayed=NO written=NO")
            document = assemble(workspace, identity.run_id)
            digests = dict(marker["artifacts"])  # type: ignore[call-overload]
        else:
            print("cache_hit=NO")
            started = timer()
            run = execute(planned, engine=engine, risk=risk, execution=execution, timer=timer)
            print(f"replay_seconds={run.elapsed_seconds:.2f} total_seconds={timer() - started:.2f}")
            marker = write_artifacts(workspace, run.artifacts,
                                     run_identity=identity.run_identity)
            document = assemble(workspace, identity.run_id)
            if document != run.document:
                raise BaselineError("the stored result does not read back as the built document")
            digests = dict(marker["artifacts"])  # type: ignore[call-overload]
            print(f"written={len(digests)} artifacts")
        for path, digest in sorted(digests.items()):
            print(f"artifact {path} sha256={digest}")
        if args.verify_determinism:
            again = execute(planned, engine=StrategyV0Engine(), risk=RiskConfig(),
                            execution=ExecutionConfig(), timer=timer)
            same = again.artifacts.digests() == digests
            print(f"determinism_rerun_identity_same="
                  f"{again.identity.run_identity == identity.run_identity}")
            print(f"determinism_artifact_digests_same={same}")
            if not same:
                for path, digest in sorted(again.artifacts.digests().items()):
                    if digests.get(path) != digest:
                        print(f"  differs {path}")
                return 1
    except (BaselineError, ReplayError, ResearchScannerError, WorkspaceError) as error:
        print(f"FAILED {getattr(error, 'code', type(error).__name__)}: {error}")
        return 1
    print_outputs(document)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        report["run_id"] = identity.run_id
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


def print_outputs(document: dict[str, object]) -> None:
    summary = document["summary"]
    funnel = document["funnel"]
    validation = document["validation"]
    drawdown = summary["drawdown"]  # type: ignore[index]
    print("=== BASELINE")
    print(f"CURRENT STRATEGY BASELINE RETURN: {summary['total_return']}")  # type: ignore[index]
    print(f"NET PNL: {summary['net_pnl']}")  # type: ignore[index]
    print(f"TOTAL TRADES: {summary['total_trades']}")  # type: ignore[index]
    print(f"TOTAL R: {summary['total_realized_r']}")  # type: ignore[index]
    print(f"PROFIT FACTOR: {summary['profit_factor']} ({summary['profit_factor_status']})")  # type: ignore[index]
    print(f"MAX DRAWDOWN: {drawdown['max_drawdown_pct']} ({drawdown['max_drawdown_usd_at_max_pct']} USD)")
    print(f"TOP FUNNEL BOTTLENECK: {funnel['top_bottleneck']}")  # type: ignore[index]
    print(f"PIT VIOLATIONS: {validation['pit_violations']}")  # type: ignore[index]
    print(f"ACCOUNTING VIOLATIONS: {validation['accounting_violations']}")  # type: ignore[index]
    print(f"INVARIANT VIOLATIONS: {validation['invariant_violations']}")  # type: ignore[index]
    print(f"FUNNEL RECONCILIATION FAILURES: {validation['funnel_reconciliation_failures']}")  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
