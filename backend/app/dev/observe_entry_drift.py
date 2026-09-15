"""Local CLI for the NON-TRADING entry drift observer.

``--date`` is the ENTRY session: the XNYS session approved candidates would have
traded, whose authority is the scanner run stamped with the session before it.
Read-only unless ``--write`` is given. The database is always named explicitly, must
already exist, and is never created, migrated, or re-journaled here.
"""

import argparse
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.market.calendar import MarketCalendar
from app.market.factory import build_kiwoom_provider
from app.market.provider import MarketDataProvider
from app.services.entry_drift_observer import (
    OBSERVER_SCHEMA_REVISION, EntryDriftObserver, entry_drift_report,
)

# Columns the authority chain reads; a database without them predates the contract.
AUTHORITY_COLUMNS = {
    "scanner_runs": {"id", "trading_date", "status", "completed_at", "active_gpt_analysis_id"},
    "scanner_candidates": {"id", "scanner_run_id", "symbol", "score_components_json"},
    "gpt_analyses": {"id", "scanner_run_id", "status"},
    "gpt_candidate_analyses": {"gpt_analysis_id", "scanner_candidate_id", "symbol", "gpt_rank",
                               "trailing_profile", "overnight_suitability"},
    "human_decisions": {"gpt_analysis_id", "scanner_candidate_id", "symbol", "decision"},
}
OBSERVATION_TABLE = "entry_drift_observations"


def not_run(reason: str) -> SystemExit:
    return SystemExit(f"NOT RUN: {reason}")


def open_database(path: Path, *, write: bool) -> Engine:
    if not path.is_file():
        raise not_run(f"database {path} does not exist")
    if not write:
        # Read-only at the SQLite layer: nothing this process does can change the file.
        return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        # journal_mode is the database owner's persistent setting and is left alone.
        cursor.close()

    return engine


def check_schema(engine: Engine, *, write: bool, report: bool) -> None:
    """Refuse before any market-data request when the database cannot hold the run."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in AUTHORITY_COLUMNS.items():
        present = {column["name"] for column in inspector.get_columns(table)} if table in tables else set()
        if not columns <= present:
            raise not_run(f"{table} lacks authority columns {sorted(columns - present)}")
    if report and OBSERVATION_TABLE not in tables:
        raise not_run(f"{OBSERVATION_TABLE} does not exist")
    if write:
        revision: list[str] = []
        if "alembic_version" in tables:
            with engine.connect() as connection:
                revision = list(connection.execute(
                    text("SELECT version_num FROM alembic_version")).scalars())
        if revision != [OBSERVER_SCHEMA_REVISION]:
            raise not_run(f"write requires schema revision {OBSERVER_SCHEMA_REVISION}, "
                          f"found {revision or 'none'}")


def kiwoom_provider() -> MarketDataProvider:
    settings = get_settings()
    if settings.market_data_provider != "kiwoom" or settings.kiwoom_mode != "market_data_only":
        raise not_run("Kiwoom MARKET_DATA_ONLY configuration is required")
    return build_kiwoom_provider(settings)


def main(argv: Sequence[str] | None = None, *,
         provider_factory: Callable[[], MarketDataProvider] = kiwoom_provider,
         clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    parser = argparse.ArgumentParser(description="NON-TRADING StrategyV0 entry drift observer")
    parser.add_argument("--date", type=date.fromisoformat,
                        help="ENTRY session date (America/New_York)")
    parser.add_argument("--database", type=Path, required=True,
                        help="existing SQLite authority database")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="observe without persisting (default)")
    mode.add_argument("--write", action="store_true", help="persist observations")
    mode.add_argument("--report", action="store_true",
                      help="authority-filtered statistics; no market-data request")
    parser.add_argument("--replace-versions", action="store_true",
                        help="with --write: replace rows of another strategy/risk/observer version")
    args = parser.parse_args(argv)
    if args.replace_versions and not args.write:
        parser.error("--replace-versions requires --write")
    if not args.report and args.date is None:
        parser.error("--date is required unless --report is used")

    engine = open_database(args.database.resolve(), write=args.write)
    try:
        check_schema(engine, write=args.write, report=args.report)
        with Session(engine) as session:
            if args.report:
                print(entry_drift_report(session, trading_date=args.date))
                return 0
            calendar = MarketCalendar()
            window = calendar.session(args.date)
            if window is None:
                raise not_run(f"{args.date} is not an XNYS session")
            if clock() < window.market_close:
                raise not_run(f"entry session {args.date} has not completed")
            provider = provider_factory()
            observer = EntryDriftObserver(session, provider, calendar=calendar, clock=clock)
            run = observer.observe_date(args.date, persist=args.write,
                                        replace_versions=args.replace_versions)
            writes = run.writes or (None,) * len(run.results)
            for result, write in zip(run.results, writes):
                print(result.candidate.symbol, result.status.value, result.quality_reason or "",
                      "" if write is None else write.value)
            print(f"entry_session={args.date} observations={len(run.results)} persisted={args.write}")
            orders = getattr(getattr(provider, "client", None), "order_request_count", 0)
            print(f"Kiwoom order requests executed = {orders}")
            return 0 if orders == 0 else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
