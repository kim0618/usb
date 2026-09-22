"""Repository for scanner run and full candidate-pool snapshots."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scanner import ScannerCandidate, ScannerRun, ScannerUniverseInput


@dataclass(frozen=True)
class ScannerCandidateData:
    symbol: str
    rank: int | None
    is_top8: bool
    score: float | None
    observed_at: datetime
    available_at: datetime
    score_components: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScannerUniverseInputData:
    """One recorded universe symbol: its provider ranking place and its scanner outcome."""

    position: int
    symbol: str
    exchange_code: str | None
    company_name: str | None
    market_cap: str | None
    outcome: str
    scanner_rank: int | None = None
    exclusion_reason: str | None = None


def universe_checksum(items: Sequence[ScannerUniverseInputData]) -> str:
    """One digest over the ordered universe and each symbol's outcome."""
    lines = "\n".join(
        f"{item.position}|{item.symbol}|{item.exchange_code or ''}|{item.market_cap or ''}|"
        f"{item.outcome}|{item.scanner_rank if item.scanner_rank is not None else ''}|"
        f"{item.exclusion_reason or ''}"
        for item in sorted(items, key=lambda value: value.position))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


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

    def add_universe_inputs(self, run_id: int, items: Sequence[ScannerUniverseInputData], *,
                            source: str, acquired_at: datetime) -> list[ScannerUniverseInput]:
        digest = universe_checksum(items)
        models = [
            ScannerUniverseInput(
                scanner_run_id=run_id, position=item.position,
                symbol=item.symbol.strip().upper(), exchange_code=item.exchange_code,
                company_name=item.company_name, market_cap=item.market_cap, source=source,
                acquired_at=acquired_at, outcome=item.outcome, scanner_rank=item.scanner_rank,
                exclusion_reason=item.exclusion_reason, universe_checksum=digest)
            for item in items
        ]
        self.session.add_all(models)
        self.session.flush()
        return models

    def get_universe_inputs(self, run_id: int) -> list[ScannerUniverseInput]:
        statement = (select(ScannerUniverseInput)
                     .where(ScannerUniverseInput.scanner_run_id == run_id)
                     .order_by(ScannerUniverseInput.position))
        return list(self.session.scalars(statement))

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
        self,
        trading_date: date,
        *,
        score_version: str | None = None,
        provider: str | None = None,
    ) -> ScannerRun | None:
        statement = select(ScannerRun).where(
            ScannerRun.trading_date == trading_date,
            ScannerRun.status == "COMPLETED",
            ScannerRun.completed_at.is_not(None),
        )
        if score_version is not None:
            statement = statement.where(ScannerRun.score_version == score_version)
        if provider is not None:
            statement = statement.where(ScannerRun.provider == provider)
        statement = statement.order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1)
        return self.session.scalar(statement)
