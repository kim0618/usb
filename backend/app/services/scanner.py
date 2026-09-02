"""Transactional persistence boundary for completed scanner calculations."""

from collections.abc import Sequence
from datetime import date, datetime, timezone
from collections.abc import Callable

from app.repositories.scanner import ScannerCandidateData, ScannerSnapshotRepository
from app.scanner.domain import ScannerResult, ScannerRunStatus
from app.scanner.scanner import QuantScanner


class ScannerService:
    def __init__(
        self,
        scanner: QuantScanner,
        repository: ScannerSnapshotRepository,
        *,
        provider_name: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.scanner = scanner
        self.repository = repository
        self.provider_name = provider_name
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        universe: Sequence[str],
        *,
        trading_date: date,
        scan_as_of: datetime,
    ) -> tuple[int, ScannerResult]:
        """Calculate first, then atomically persist a completed run and full pool."""

        session = self.repository.session
        if session.in_transaction():
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("ScannerService requires a clean Session before taking transaction ownership")
            session.commit()
        started_at = self.clock()
        result = self.scanner.scan(universe, trading_date=trading_date, scan_as_of=scan_as_of)
        try:
            run = self.repository.create_run(
                trading_date=trading_date,
                started_at=started_at,
                provider=self.provider_name,
                score_version=result.score_version,
                status=ScannerRunStatus.RUNNING.value,
                universe_count=result.universe_count,
                excluded_count=result.excluded_count,
                candidate_count=result.candidate_count,
                top8_count=len(result.top8),
            )
            self.repository.add_candidates(
                run.id,
                [
                    ScannerCandidateData(
                        symbol=candidate.symbol,
                        rank=candidate.rank,
                        is_top8=candidate.is_top8,
                        score=candidate.final_score,
                        score_components=candidate.score_components(),
                        observed_at=candidate.observed_at,
                        available_at=candidate.available_at,
                    )
                    for candidate in result.candidates
                ],
            )
            self.repository.complete_run(
                run.id,
                completed_at=self.clock(),
                status=ScannerRunStatus.COMPLETED.value,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        return run.id, result
