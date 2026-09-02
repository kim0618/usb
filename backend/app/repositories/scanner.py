"""Repository for scanner run and full candidate-pool snapshots."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scanner import ScannerCandidate, ScannerRun


@dataclass(frozen=True)
class ScannerCandidateData:
    symbol: str
    rank: int | None
    is_top8: bool
    score: float | None
    observed_at: datetime
    available_at: datetime
    score_components: dict[str, Any] = field(default_factory=dict)


class ScannerSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(
        self,
        *,
        trading_date: date,
        started_at: datetime,
        provider: str,
        score_version: str,
        status: str = "RUNNING",
        universe_count: int = 0,
        excluded_count: int = 0,
        candidate_count: int = 0,
        top8_count: int = 0,
    ) -> ScannerRun:
        run = ScannerRun(
            trading_date=trading_date,
            started_at=started_at,
            status=status,
            provider=provider,
            score_version=score_version,
            universe_count=universe_count,
            excluded_count=excluded_count,
            candidate_count=candidate_count,
            top8_count=top8_count,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def complete_run(
        self, run_id: int, *, completed_at: datetime, status: str = "COMPLETED"
    ) -> ScannerRun:
        run = self.session.get(ScannerRun, run_id)
        if run is None:
            raise LookupError(f"Scanner run {run_id} does not exist")
        run.completed_at = completed_at
        run.status = status
        self.session.flush()
        return run

    def add_candidates(
        self, run_id: int, candidates: Sequence[ScannerCandidateData]
    ) -> list[ScannerCandidate]:
        models = [
            ScannerCandidate(
                scanner_run_id=run_id,
                symbol=item.symbol.strip().upper(),
                rank=item.rank,
                is_top8=item.is_top8,
                score=item.score,
                score_components_json=item.score_components,
                observed_at=item.observed_at,
                available_at=item.available_at,
            )
            for item in candidates
        ]
        self.session.add_all(models)
        self.session.flush()
        return models

    def get_candidates(self, run_id: int) -> list[ScannerCandidate]:
        nulls_last_rank = ScannerCandidate.rank.is_(None)
        statement = (
            select(ScannerCandidate)
            .where(ScannerCandidate.scanner_run_id == run_id)
            .order_by(nulls_last_rank, ScannerCandidate.rank, ScannerCandidate.symbol)
        )
        return list(self.session.scalars(statement))

    def get_top8(self, run_id: int) -> list[ScannerCandidate]:
        nulls_last_rank = ScannerCandidate.rank.is_(None)
        statement = (
            select(ScannerCandidate)
            .where(
                ScannerCandidate.scanner_run_id == run_id,
                ScannerCandidate.is_top8.is_(True),
            )
            .order_by(nulls_last_rank, ScannerCandidate.rank, ScannerCandidate.symbol)
        )
        return list(self.session.scalars(statement))

    def get_latest_completed_run(
        self, trading_date: date, *, score_version: str | None = None
    ) -> ScannerRun | None:
        statement = select(ScannerRun).where(
            ScannerRun.trading_date == trading_date,
            ScannerRun.status == "COMPLETED",
            ScannerRun.completed_at.is_not(None),
        )
        if score_version is not None:
            statement = statement.where(ScannerRun.score_version == score_version)
        statement = statement.order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1)
        return self.session.scalar(statement)
