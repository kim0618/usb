"""Local CLI for the Massive historical minute-bar collector: ONE symbol, ONE range.

It clamps the requested range to the last session Stocks Basic publishes (T-1), asks
Massive for the whole range in one long from/to request with bounded pagination,
validates every expected XNYS session while the rows stream past, writes them as Parquet
into the shared Google Drive workspace, and records the result in the existing collector
manifest. Nothing is recorded COMPLETE unless the last expected session is present, every
regular minute of every session is present, and the whole range is free of timestamp and
OHLC corruption.

This is a local job. It refuses to run from a production path or a service manager, it
never touches the production database, and it collects one symbol per run.

    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_historical_massive \\
        --symbol AAPL --years 1
    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_historical_massive \\
        --symbol AAPL --start 2025-01-01 --end 2025-12-31

MASSIVE_API_KEY is read from the repository-root ``.env`` through ``Settings``. Output is
counts, dates, checksums, and verdicts only: never a key, a header, a request URL, or a
payload fragment.
"""

import argparse
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
import time

from app.backtest.collector.collector import COLLECTOR_VERSION, CollectionOutcome, collect
from app.backtest.collector.dataset import DATASET_SCHEMA_VERSION, describe_schema
from app.backtest.collector.errors import CollectorError
from app.backtest.collector.range import CollectionRange, plan_range, plan_years
from app.backtest.workspace.discovery import resolve_workspace_root
from app.backtest.workspace.errors import WorkspaceError
from app.backtest.workspace.layout import Workspace
from app.core.config import Settings
from app.dev.observe_entry_drift import not_run
from app.dev.run_massive_spike import MARKET_TIMEZONE, _yes
from app.integrations.massive.client import MassiveConfigurationError, MassiveError
from app.integrations.massive.minute_bars import ET
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date (YYYY-MM-DD)") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect one symbol of Massive 1-minute history into the shared workspace")
    parser.add_argument("--symbol", required=True, help="exactly one symbol")
    parser.add_argument("--start", type=_parse_date, help="first calendar date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, help="last calendar date; clamped to T-1")
    parser.add_argument("--years", type=int, help="collect this many years back from T-1")
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="override shared workspace discovery")
    parser.add_argument("--save-raw", action="store_true",
                        help="also keep each sanitized response page under market_data/raw/massive/")
    parser.add_argument("--refresh", action="store_true",
                        help="re-collect even when the manifest entry is COMPLETE and verified")
    parser.add_argument("--overwrite-partitions", action="store_true",
                        help="replace a finished Parquet file owned by a different manifest entry")
    parser.add_argument("--plan-only", action="store_true",
                        help="print the effective range and expected sessions; make no request")
    return parser


def plan(args: argparse.Namespace, calendar: MarketCalendar, now: datetime) -> CollectionRange:
    if args.years is not None:
        if args.start or args.end:
            raise SystemExit("NOT RUN: --years cannot be combined with --start or --end")
        return plan_years(calendar, years=args.years, now=now)
    if not args.start or not args.end:
        raise SystemExit("NOT RUN: pass --start and --end, or --years")
    return plan_range(calendar, start=args.start, end=args.end, now=now)


def range_lines(collection_range: CollectionRange, now: datetime) -> list[str]:
    early = [str(window.session_date) for window in collection_range.sessions
             if window.is_early_close]
    return [
        f"collector_version={COLLECTOR_VERSION}",
        f"dataset_schema_version={DATASET_SCHEMA_VERSION}",
        "provider=massive timeframe=1minute adjusted=false sort=asc limit=50000",
        f"now_et={now.astimezone(ET).isoformat()}",
        f"requested_start={collection_range.requested_start} requested_end={collection_range.requested_end}",
        f"effective_start={collection_range.effective_start} effective_end={collection_range.effective_end}",
        f"last_publishable_session={collection_range.last_collectable_session} (XNYS previous trading day)",
        f"end_clamped_to_T-1={_yes(collection_range.end_clamped)} "
        f"start_moved_to_session={_yes(collection_range.start_moved)}",
        f"request_range_et={collection_range.request_start.isoformat()}"
        f"..{collection_range.request_end.isoformat()}",
        f"expected_sessions={collection_range.expected_sessions}",
        f"early_close_sessions_expected={len(early)} dates={','.join(early) or 'none'}",
        f"partition_years={','.join(str(year) for year in collection_range.years)}",
    ]


def outcome_lines(outcome: CollectionOutcome, workspace: Workspace) -> list[str]:
    lines = [
        "=== COLLECTION",
        f"cache_hit={_yes(outcome.cache_hit)}"
        + (f" detail={outcome.cache_detail}" if outcome.cache_hit else ""),
        f"http_requests={outcome.http_requests}",
        f"pages={outcome.pages}",
        f"retries={outcome.retries}",
        f"response_bytes={outcome.response_bytes}",
        f"elapsed_seconds={outcome.elapsed_seconds:.2f}",
        f"raw_pages_saved={outcome.raw_pages_saved}",
    ]
    validation = outcome.validation
    if validation is not None:
        coverage = validation.coverage
        worst = coverage.worst_regular_session
        lines += [
            "=== SESSIONS",
            f"expected_sessions={coverage.expected_sessions}",
            f"actual_sessions={coverage.sessions_present}",
            f"last_expected_session={validation.last_expected_session} "
            f"present={_yes(validation.last_session_present)}",
            f"sessions_missing_entirely={len(validation.missing_sessions)} "
            f"dates={','.join(str(day) for day in validation.missing_sessions[:10]) or 'none'}",
            f"regular_complete_sessions={coverage.sessions_with_complete_regular_minutes}",
            f"sessions_with_missing_regular_minutes={len(validation.sessions_with_missing_regular_minutes)}"
            f" worst={'none' if worst is None else f'{worst[0]}:{worst[1]}'}",
            f"premarket_sessions={coverage.sessions_with_premarket}",
            f"premarket_first_bar_0400_sessions={coverage.sessions_with_premarket_0400}",
            f"postmarket_sessions={coverage.sessions_with_postmarket}",
            f"early_close_sessions={len(coverage.early_close_sessions)} "
            f"valid={len(coverage.early_close_sessions_valid)} "
            f"dates={','.join(str(day) for day in coverage.early_close_sessions) or 'none'}",
            "=== DATA QUALITY",
            f"rows={validation.quality.total_rows}",
            f"duplicate_timestamps={validation.quality.duplicate_timestamps}",
            f"non_monotonic_timestamps={validation.quality.non_monotonic_timestamps}",
            f"ohlc_invariant_violations={validation.quality.ohlc_violations}",
            f"null_ohlcv_rows={validation.quality.null_ohlcv_rows}",
            f"negative_volume_rows={validation.quality.negative_volume_rows}",
            f"zero_volume_rows={validation.quality.zero_volume_rows}",
            f"unaligned_timestamps={validation.quality.unaligned_timestamps}",
            f"off_date_rows={validation.quality.off_date_rows}",
            f"outside_extended_hours_rows={validation.quality.outside_extended_hours_rows}",
            f"data_quality={validation.quality.verdict}",
            "=== NUMERIC PRECISION",
            f"numeric_literals_checked={outcome.precision.values_checked}",
            f"max_decimal_places={outcome.precision.max_decimal_places}",
            f"max_significant_digits={outcome.precision.max_significant_digits}",
            f"float64_roundtrip_violations={len(outcome.precision.violations)}",
        ]
    lines += ["=== STORAGE", f"rows_written={outcome.row_count}",
              f"files={len(outcome.partitions)}"]
    for partition in outcome.partitions:
        path = workspace.root / partition.relative_path
        lines.append(f"partition year={partition.year} path={partition.relative_path} "
                     f"rows={partition.row_count} bytes={partition.byte_size} "
                     f"sha256={partition.checksum} exists={_yes(path.is_file())}")
    lines += [
        f"final_path_root={workspace.root}",
        f"combined_checksum={outcome.checksum}",
        f"total_bytes={outcome.byte_size}",
        "=== MANIFEST",
        f"manifest_entry_id={outcome.entry_id}",
        f"manifest_status={outcome.manifest_status}",
        f"manifest_path={workspace.relative(workspace.manifest_path)}",
        "displaced_entries_demoted=" + (",".join(str(entry) for entry in outcome.displaced_entries)
                                        or "none"),
    ]
    return lines


def main(argv: Sequence[str] | None = None, *,
         settings_factory: Callable[[], Settings] = Settings,
         clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
         timer: Callable[[], float] = time.monotonic,
         **collect_kwargs: object) -> int:
    args = build_parser().parse_args(argv)
    if " " in args.symbol.strip() or "," in args.symbol:
        raise SystemExit("NOT RUN: --symbol takes exactly one symbol; bulk collection is out of scope")
    try:
        symbol = normalize_symbol(args.symbol)
    except ValueError as exc:
        raise not_run(f"symbol {args.symbol!r} is invalid: {exc}") from exc
    settings = settings_factory()
    now = clock()
    calendar = MarketCalendar(MARKET_TIMEZONE)
    try:
        collection_range = plan(args, calendar, now)
    except CollectorError as exc:
        print(f"FAILED code={exc.code} reason={exc.reason}")
        return 1
    print(f"symbol={symbol}")
    for line in range_lines(collection_range, now):
        print(line)
    print("schema=" + " ".join(describe_schema()))
    if args.plan_only:
        print("plan_only=YES http_requests=0")
        return 0
    try:
        workspace = Workspace(resolve_workspace_root(args.workspace_root))
    except WorkspaceError as exc:
        print("http_requests=0")
        raise not_run(str(exc)) from None
    print(f"workspace_root={workspace.root}")
    try:
        outcome = collect(workspace, symbol=symbol, collection_range=collection_range,
                          settings=settings, now=now, save_raw=args.save_raw,
                          refresh=args.refresh, overwrite_partitions=args.overwrite_partitions,
                          timer=timer, **collect_kwargs)  # type: ignore[arg-type]
    except MassiveConfigurationError as exc:
        print("http_requests=0")
        raise not_run(f"{exc.code}: {exc}") from None
    except (CollectorError, WorkspaceError) as exc:
        print(f"FAILED code={exc.code} reason={getattr(exc, 'reason', exc)}")
        return 1
    except MassiveError as exc:
        print(f"FAILED code={exc.code} reason={exc}")
        return 1
    for line in outcome.environment.checks:
        print(f"environment {line}")
    for line in outcome_lines(outcome, workspace):
        print(line)
    print("VERDICT=COMPLETE" if outcome.manifest_status == "COMPLETE" else "VERDICT=INCOMPLETE")
    print("hard_stop=one symbol per run; no replay core, no backtester, no scheduled run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
