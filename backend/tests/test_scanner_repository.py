from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, create_db_engine
from app.repositories.scanner import ScannerCandidateData, ScannerSnapshotRepository


def test_scanner_snapshot_full_pool_order_top8_and_json(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'scanner.sqlite3'}")
    Base.metadata.create_all(engine)
    now = datetime(2024, 6, 18, 20, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        repository = ScannerSnapshotRepository(session)
        run = repository.create_run(
            trading_date=date(2024, 6, 18),
            started_at=now,
            provider="fake",
            score_version="fixture-v1",
        )
        repository.add_candidates(
            run.id,
            [
                ScannerCandidateData(
                    symbol="CCC", rank=3, is_top8=True, score=70.0,
                    score_components={"rvol": 30.5}, observed_at=now,
                    available_at=now + timedelta(seconds=1),
                ),
                ScannerCandidateData(
                    symbol="AAA", rank=1, is_top8=True, score=90.0,
                    score_components={"rvol": 40.5}, observed_at=now,
                    available_at=now + timedelta(seconds=1),
                ),
                ScannerCandidateData(
                    symbol="BBB", rank=2, is_top8=False, score=80.0,
                    score_components={"rvol": 35.5}, observed_at=now,
                    available_at=now + timedelta(seconds=1),
                ),
            ],
        )
        completed = repository.complete_run(
            run.id, completed_at=now + timedelta(minutes=1)
        )
        session.commit()
        pool = repository.get_candidates(run.id)
        top8 = repository.get_top8(run.id)
        assert completed.status == "COMPLETED"
        assert [candidate.symbol for candidate in pool] == ["AAA", "BBB", "CCC"]
        assert [candidate.symbol for candidate in top8] == ["AAA", "CCC"]
        assert pool[0].score_components_json == {"rvol": 40.5}
        assert pool[0].observed_at.tzinfo is not None
    engine.dispose()


def test_latest_completed_run_filters_date_and_score_version(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'latest.sqlite3'}")
    Base.metadata.create_all(engine)
    now = datetime(2024, 6, 18, 20, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        repository = ScannerSnapshotRepository(session)
        first = repository.create_run(trading_date=date(2024, 6, 18), started_at=now, provider="fake", score_version="v1")
        repository.complete_run(first.id, completed_at=now + timedelta(minutes=1))
        repository.create_run(trading_date=date(2024, 6, 18), started_at=now, provider="fake", score_version="v1", status="FAILED")
        latest = repository.create_run(trading_date=date(2024, 6, 18), started_at=now, provider="fake", score_version="v1")
        repository.complete_run(latest.id, completed_at=now + timedelta(minutes=2))
        tied = repository.create_run(trading_date=date(2024, 6, 18), started_at=now, provider="fake", score_version="v1")
        repository.complete_run(tied.id, completed_at=now + timedelta(minutes=2))
        other = repository.create_run(trading_date=date(2024, 6, 18), started_at=now, provider="fake", score_version="v2")
        repository.complete_run(other.id, completed_at=now + timedelta(minutes=3))
        repository.create_run(trading_date=date(2024, 6, 19), started_at=now, provider="fake", score_version="v1")
        session.commit()
        selected = repository.get_latest_completed_run(date(2024, 6, 18), score_version="v1")
        unfiltered = repository.get_latest_completed_run(date(2024, 6, 18))
        assert selected is not None and selected.id == tied.id
        assert unfiltered is not None and unfiltered.id == other.id
    engine.dispose()
