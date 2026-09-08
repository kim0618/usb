"""Production morning Scanner oneshot; market data only, never orders."""

import argparse
import fcntl
import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import create_db_engine
from app.dev.run_real_scanner import _safety_gate
from app.market.factory import build_kiwoom_provider
from app.market.universe import KiwoomUniverseSource
from app.repositories.scanner import ScannerSnapshotRepository
from app.scanner.scanner import QuantScanner
from app.services.morning_scanner import (
    MorningJobResult,
    MorningJobStatus,
    MorningScanExecution,
    MorningScannerService,
)
from app.services.scanner import ScannerService

LOGGER = logging.getLogger("usb.morning_scanner")
DEFAULT_LIMIT = 10


def _paper_database_url(settings: Settings) -> str:
    if settings.runtime_profile != "real_market_operator" or not settings.paper_database_url:
        raise SystemExit(
            "NOT RUN: real_market_operator with explicit PAPER_DATABASE_URL is required"
        )
    return settings.resolved_database_url


@contextmanager
def _database_job_lock(database_url: str) -> Iterator[None]:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        yield
        return
    db_path = Path(database_url.removeprefix(prefix)).resolve()
    lock_path = db_path.with_suffix(db_path.suffix + ".morning.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def _execute_scan(
    settings: Settings, session: Session, limit: int, target: date, now: datetime
) -> MorningScanExecution:
    provider = build_kiwoom_provider(settings)
    provider._clock = lambda: now
    source = KiwoomUniverseSource(provider)
    try:
        universe = source.acquire(limit)
        source.prime_provider(universe, now)
        scanner = QuantScanner(provider, provider)
        result = scanner.scan(
            [item.symbol for item in universe], trading_date=target, scan_as_of=now
        )
        run_id, _ = ScannerService(
            scanner,
            ScannerSnapshotRepository(session),
            provider_name="KIWOOM_REAL",
        ).persist_result(result)
    except Exception:
        LOGGER.exception(
            "Morning Scanner failed target_trading_date=%s kiwoom_order_requests=%d",
            target,
            provider.client.order_request_count,
        )
        raise
    return MorningScanExecution(
        run_id=run_id,
        result=result,
        mapping_issues=provider.mapping_issues,
        order_request_count=provider.client.order_request_count,
    )


def _log_completed_outcome(outcome: MorningJobResult) -> None:
    LOGGER.info(
        "Morning Scanner %s target_trading_date=%s scanner_run_id=%s prompt_chars=%d",
        outcome.status,
        outcome.target_trading_date,
        outcome.scanner_run_id,
        outcome.prompt_chars,
    )
    if outcome.mapping_issues:
        for symbol, count in sorted(outcome.mapping_issues.items()):
            LOGGER.warning(
                "Scanner mapping issues target_trading_date=%s symbol=%s count=%d",
                outcome.target_trading_date,
                symbol,
                count,
            )
    missing = outcome.latest_bar_missing_symbols
    LOGGER.info(
        "Scanner latest-bar-missing target_trading_date=%s count=%d symbols=%s",
        outcome.target_trading_date,
        len(missing),
        ",".join(missing) if missing else "none",
    )
    LOGGER.info("Kiwoom order requests executed=%d", outcome.order_request_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="XNYS-gated morning Scanner oneshot")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, choices=range(1, 101))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    _safety_gate(settings)
    database_url = _paper_database_url(settings)
    now = datetime.now(timezone.utc)
    engine = create_db_engine(database_url)
    try:
        with _database_job_lock(database_url), Session(engine) as session:
            repository = ScannerSnapshotRepository(session)
            service = MorningScannerService(
                repository,
                lambda target, scan_as_of: _execute_scan(
                    settings, session, args.limit, target, scan_as_of
                ),
            )
            try:
                outcome = service.run(now)
            except Exception:
                LOGGER.exception("Morning Scanner or prompt preparation failed")
                raise
            if outcome.status is MorningJobStatus.SKIPPED:
                LOGGER.info(
                    "Morning Scanner SKIPPED target_trading_date=%s reason=%s",
                    outcome.target_trading_date,
                    outcome.reason,
                )
                return 0
            _log_completed_outcome(outcome)
            return 0 if outcome.order_request_count == 0 else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
