"""Local CLI for the NON-TRADING premarket volume V2 history collector.

``--scanner-date`` is a completed Scanner run's trading date. The collector fills the
exact 20-session PREMARKET baseline of the entry session that run feeds (the next
XNYS session) for the run's TOP8 symbols, fetching only sessions not stored yet.
``--symbol`` limits the run to one of those TOP8 candidates, with the exchange its
scanner snapshot stored; a symbol outside the TOP8 is refused before any request.

``--dry-run`` here is PLAN ONLY: it lists what is missing and makes zero market-data
requests. This differs from the entry drift observer, whose ``--dry-run`` replays real
market data and only skips writing. Only ``--write`` fetches and persists. The
database is always named explicitly, must already exist at the V2 schema revision,
and is never created or migrated here. Every run ends with request accounting:
counts only, never a token, credential, or payload.
"""

import argparse
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.core.exceptions import MarketDataError
from app.dev.observe_entry_drift import kiwoom_provider, not_run, open_database
from app.integrations.kiwoom.mapping import exchange_code
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol
from app.services.premarket_volume_history import (
    BASELINE_SESSIONS, HISTORY_SCHEMA_REVISION, KiwoomPremarketSessionSource,
    PremarketSessionSource, PremarketVolumeHistoryService, SessionQuality, history_statistics,
    scanner_collection_targets,
)

HISTORY_TABLE = "premarket_volume_sessions"
AUTHORITY_TABLES = ("scanner_runs", "scanner_candidates")
CHART_PATH = "/api/us/chart"


def kiwoom_source() -> PremarketSessionSource:
    return KiwoomPremarketSessionSource(kiwoom_provider())


def check_schema(engine: Engine, *, write: bool) -> None:
    """Refuse before any market-data request when the database cannot hold the run."""
    tables = set(inspect(engine).get_table_names())
    for table in (*AUTHORITY_TABLES, HISTORY_TABLE):
        if table not in tables:
            raise not_run(f"{table} does not exist")
    if write:
        with engine.connect() as connection:
            revision = list(connection.execute(
                text("SELECT version_num FROM alembic_version")).scalars()) \
                if "alembic_version" in tables else []
        if revision != [HISTORY_SCHEMA_REVISION]:
            raise not_run(f"write requires schema revision {HISTORY_SCHEMA_REVISION}, "
                          f"found {revision or 'none'}")


def select_symbol(targets: Sequence[tuple[str, str]], symbol: str,
                  scanner_date: date) -> tuple[tuple[str, str], ...]:
    """The one TOP8 candidate named, carrying its stored exchange; refused otherwise."""
    try:
        wanted = normalize_symbol(symbol)
    except ValueError as exc:
        raise not_run(f"--symbol {symbol!r} is invalid: {exc}") from exc
    matches = tuple(item for item in targets if normalize_symbol(item[0]) == wanted)
    if len(matches) != 1:
        raise not_run(f"--symbol {wanted} is not exactly one TOP8 candidate of the "
                      f"{scanner_date} scanner run (matches={len(matches)})")
    return matches


def request_counts(source: PremarketSessionSource) -> dict[str, int] | None:
    """The Kiwoom client's HTTP attempt counts per path; None for a source without one."""
    client = getattr(getattr(source, "provider", None), "client", None)
    counts: Any = getattr(client, "request_counts", None)
    return None if counts is None else dict(counts)


def main(argv: Sequence[str] | None = None, *,
         source_factory: Callable[[], PremarketSessionSource] = kiwoom_source,
         clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    parser = argparse.ArgumentParser(description="NON-TRADING premarket volume V2 history")
    parser.add_argument("--scanner-date", type=date.fromisoformat,
                        help="completed Scanner run trading date (America/New_York)")
    parser.add_argument("--database", type=Path, required=True,
                        help="existing SQLite authority database")
    parser.add_argument("--symbol",
                        help="only this one TOP8 candidate of the scanner run, with its stored "
                             "exchange; any other symbol fails before a market-data request")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="PLAN ONLY: list missing sessions, market-data request 0 (default)")
    mode.add_argument("--write", action="store_true", help="fetch and persist missing sessions")
    mode.add_argument("--report", action="store_true", help="history statistics; no request")
    args = parser.parse_args(argv)
    if not args.report and args.scanner_date is None:
        parser.error("--scanner-date is required unless --report is used")
    if args.report and args.symbol is not None:
        parser.error("--symbol cannot be used with --report")

    engine = open_database(args.database.resolve(), write=args.write)
    try:
        check_schema(engine, write=args.write)
        with Session(engine) as session:
            if args.report:
                print(history_statistics(session))
                return 0
            calendar = MarketCalendar()
            window = calendar.session(args.scanner_date)
            if window is None:
                raise not_run(f"{args.scanner_date} is not an XNYS session")
            if clock() < window.market_close:
                raise not_run(f"scanner session {args.scanner_date} has not completed")
            entry, targets = scanner_collection_targets(session, args.scanner_date, calendar)
            if args.symbol is not None:
                targets = select_symbol(targets, args.symbol, args.scanner_date)
            service = PremarketVolumeHistoryService(session, calendar=calendar, clock=clock)
            if not args.write:
                return _plan(service, targets, entry)
            source = source_factory()
            service.source = source
            before = request_counts(source)
            run = service.collect(targets, entry)
            after = request_counts(source)
            for item in run.symbols:
                reached = [record.target_reached for record in item.records]
                print(item.symbol, item.exchange, item.status.value,
                      f"fetched_sessions={len(item.fetched_sessions)}",
                      f"complete={0 if item.baseline is None else item.baseline.valid_sessions}"
                      f"/{BASELINE_SESSIONS}",
                      f"target_reached={sum(1 for value in reached if value)}/{len(reached)}",
                      f"target_not_reached={item.quality_count(SessionQuality.TARGET_NOT_REACHED)}",
                      f"pages_used={sum(record.pages_used or 0 for record in item.records)}",
                      item.failure or "")
            summary = run.summary()
            http = (None if before is None or after is None else
                    {path: after[path] - before.get(path, 0) for path in after
                     if after[path] != before.get(path, 0)})
            print(" ".join(f"{name}={value}" for name, value in summary.items()),
                  f"http_chart_requests={'unavailable' if http is None else http.get(CHART_PATH, 0)}",
                  f"http_requests={'unavailable' if http is None else sum(http.values())}")
            print(f"entry_session={entry} targets={len(targets)} "
                  f"market_data_sessions={run.market_data_sessions} persisted=True")
            client = getattr(getattr(source, "provider", None), "client", None)
            orders = getattr(client, "order_request_count", 0)
            print(f"Kiwoom order requests executed = {orders}")
            return 0 if orders == 0 else 1
    finally:
        engine.dispose()


def _plan(service: PremarketVolumeHistoryService, targets: Sequence[tuple[str, str]],
          entry: date) -> int:
    """The dry run: what ``--write`` would fetch, from stored rows only."""
    requested = cached = 0
    for symbol, exchange in targets:
        try:
            code = exchange_code(exchange)
        except MarketDataError as exc:
            print(symbol, exchange or "MISSING", exc.code)
            continue
        missing = len(service.missing_sessions(symbol, code, entry))
        requested, cached = requested + missing, cached + BASELINE_SESSIONS - missing
        print(symbol, code, f"missing_sessions={missing}")
    print(f"symbols_requested={len(targets)} sessions_requested={requested} "
          f"cache_hits={cached} market_data_requests=0")
    print(f"entry_session={entry} targets={len(targets)} persisted=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
