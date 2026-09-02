from pathlib import Path
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, create_db_engine
from app.models.scanner import ScannerCandidate, ScannerRun
from app.repositories.scanner import ScannerSnapshotRepository
from app.scanner.scanner import QuantScanner
from app.services.scanner import ScannerService
from tests.scanner_fixtures import SCAN_AS_OF, TRADING_DATE, scanner_providers


def test_scanner_e2e_persists_full_pool_and_components(tmp_path: Path) -> None:
    symbols = [f"S{index:03d}" for index in range(12)]
    market, reference = scanner_providers(symbols)
    engine = create_db_engine(f"sqlite:///{tmp_path / 'scanner.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = ScannerSnapshotRepository(session)
        run_id, result = ScannerService(
            QuantScanner(market, reference), repository, provider_name="fake"
        ).run(symbols, trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
        run = session.get(ScannerRun, run_id)
        candidates = repository.get_candidates(run_id)
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.score_version == "quant_v0"
        assert (run.universe_count, run.excluded_count, run.candidate_count, run.top8_count) == (
            12, 0, 12, 8
        )
        assert len(candidates) == result.candidate_count
        assert [item.rank for item in candidates] == list(range(1, 13))
        assert sum(item.is_top8 for item in candidates) == 8
        assert candidates[0].score_components_json["raw"]["rvol"] == 1.0
        assert candidates[0].score_components_json["final_score"] == candidates[0].score
    engine.dispose()


def test_service_handles_autobegin_and_two_consecutive_runs(tmp_path: Path) -> None:
    market, reference = scanner_providers(["AAA"])
    engine = create_db_engine(f"sqlite:///{tmp_path / 'reuse.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.scalar(select(func.count()).select_from(ScannerRun))
        service = ScannerService(
            QuantScanner(market, reference), ScannerSnapshotRepository(session),
            provider_name="fake",
        )
        first_id, _ = service.run(["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
        second_id, _ = service.run(["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
        assert second_id > first_id
        assert session.scalar(select(func.count()).select_from(ScannerRun)) == 2
    engine.dispose()


def test_operational_timestamps_are_distinct_from_scan_as_of(tmp_path: Path) -> None:
    market, reference = scanner_providers(["AAA"])
    engine = create_db_engine(f"sqlite:///{tmp_path / 'timestamps.sqlite3'}")
    Base.metadata.create_all(engine)
    ticks = iter([
        datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, 1, tzinfo=timezone.utc),
    ])
    with Session(engine) as session:
        run_id, result = ScannerService(
            QuantScanner(market, reference), ScannerSnapshotRepository(session),
            provider_name="fake", clock=lambda: next(ticks),
        ).run(["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
        run = session.get(ScannerRun, run_id)
        assert run is not None
        assert run.started_at != SCAN_AS_OF and run.completed_at != SCAN_AS_OF
        assert run.completed_at > run.started_at
        assert result.scan_as_of == SCAN_AS_OF
    engine.dispose()


def test_persistence_failure_rolls_back_run_and_partial_candidates(tmp_path: Path) -> None:
    class FailingRepository(ScannerSnapshotRepository):
        def add_candidates(self, run_id, candidates):  # type: ignore[no-untyped-def]
            super().add_candidates(run_id, candidates[:1])
            raise RuntimeError("injected persistence failure")

    market, reference = scanner_providers(["AAA", "BBB"])
    engine = create_db_engine(f"sqlite:///{tmp_path / 'rollback.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = ScannerService(
            QuantScanner(market, reference), FailingRepository(session), provider_name="fake"
        )
        with pytest.raises(RuntimeError, match="injected"):
            service.run(
                ["AAA", "BBB"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
            )
        assert session.scalar(select(func.count()).select_from(ScannerRun)) == 0
        assert session.scalar(select(func.count()).select_from(ScannerCandidate)) == 0
    engine.dispose()
