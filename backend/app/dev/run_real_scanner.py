"""Explicit, bounded Kiwoom real-market Scanner command (never orders)."""

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import REAL_MARKET_DATABASE_URL, Settings, get_settings
from app.core.database import Base, create_db_engine
from app.market.calendar import MarketCalendar
from app.market.factory import build_kiwoom_provider
from app.market.universe import KiwoomUniverseSource
from app.models.scanner import ScannerRun
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.prompt import ResearchPromptService
from app.scanner.scanner import QuantScanner
from app.services.scanner import ScannerService

ET = ZoneInfo("America/New_York")
DEFAULT_DB = REAL_MARKET_DATABASE_URL.removeprefix("sqlite:///")


def _latest_closed_session(now: datetime) -> date:
    calendar = MarketCalendar("America/New_York")
    day = now.astimezone(ET).date()
    while True:
        window = calendar.session(day)
        if window is not None and now.astimezone(ET) >= window.market_close:
            return day
        day -= timedelta(days=1)


def _safety_gate(settings: Settings) -> None:
    if not settings.run_kiwoom_real_scanner:
        raise SystemExit("NOT RUN: RUN_KIWOOM_REAL_SCANNER=1 is required")
    if not (
        settings.market_data_provider == "kiwoom"
        and settings.broker_provider == "simulation"
        and settings.kiwoom_mode == "market_data_only"
    ):
        raise SystemExit("NOT RUN: kiwoom/simulation/market_data_only safety gate failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Kiwoom real market Scanner")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--trading-date", type=date.fromisoformat)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--database", help=f"SQLite path (default: runtime profile, otherwise {DEFAULT_DB})")
    args = parser.parse_args()
    settings = get_settings()
    _safety_gate(settings)
    now = datetime.now(timezone.utc)
    trading_date = args.trading_date or _latest_closed_session(now)
    provider = build_kiwoom_provider(settings)
    # One immutable acquisition timestamp makes point-in-time filtering and a
    # repeated calculation over the fetched snapshot deterministic.
    provider._clock = lambda: now
    source = KiwoomUniverseSource(provider)
    try:
        universe = source.acquire(args.limit)
        source.prime_provider(universe, now)
        scanner = QuantScanner(provider, provider)
        result = scanner.scan([item.symbol for item in universe], trading_date=trading_date, scan_as_of=now)
    except Exception:
        print(f"Kiwoom order requests executed = {provider.client.order_request_count}")
        raise
    print("Market data: KIWOOM_REAL")
    print("Broker: SIMULATION")
    print(f"Trading date (ET): {trading_date}")
    print(f"Universe: {result.universe_count}; eligible: {result.candidate_count}; TOP: {len(result.top8)}")
    print("Exclusions: " + str(dict(sorted(Counter(item.reason.value for item in result.excluded).items()))))
    print("Mapping issues: " + str(provider.mapping_issues))
    print("Provider failures: " + str(provider.provider_failures))
    print("TOP: " + ", ".join(f"{item.rank}:{item.symbol}" for item in result.top8))
    print("Market-data HTTP attempts: " + str(provider.client.request_counts))
    if args.persist:
        database = args.database or (
            settings.resolved_database_url.removeprefix("sqlite:///")
            if settings.runtime_profile == "real_market_operator"
            else DEFAULT_DB
        )
        db_path = Path(database)
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parents[3] / db_path
        engine = create_db_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            repository = ScannerSnapshotRepository(session)
            run_id, _ = ScannerService(scanner, repository, provider_name="KIWOOM_REAL").persist_result(result)
            run = session.get(ScannerRun, run_id)
            assert run is not None
            prompt = ResearchPromptService(repository).generate_top_for_run(run)
            print(f"Persisted scanner_run_id: {run_id}; Research Prompt chars: {len(prompt)}")
    print(f"Kiwoom order requests executed = {provider.client.order_request_count}")
    return 0 if provider.client.order_request_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
