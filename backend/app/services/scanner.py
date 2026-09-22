"""Transactional persistence boundary for completed scanner calculations."""

from collections.abc import Sequence
from datetime import date, datetime, timezone
from collections.abc import Callable

from app.repositories.scanner import (
    ScannerCandidateData, ScannerSnapshotRepository, ScannerUniverseInputData,
)
from app.scanner.domain import ScannerResult, ScannerRunStatus
from app.scanner.scanner import QuantScanner

UNIVERSE_OUTCOME_CANDIDATE = "SCANNER_CANDIDATE"
UNIVERSE_OUTCOME_EXCLUDED = "EXCLUDED"
UNIVERSE_OUTCOME_NOT_EVALUATED = "NOT_EVALUATED"


def universe_inputs(universe: Sequence[object], result: ScannerResult) -> list[ScannerUniverseInputData]:
    """Each provider universe row, in provider order, with what the scanner made of it.

    ``universe`` holds the provider's rows (symbol, exchange_code, company_name,
    market_cap); a symbol the scanner neither ranked nor excluded is NOT_EVALUATED rather
    than silently dropped.
    """
    ranked = {candidate.symbol: candidate.rank for candidate in result.candidates}
    excluded = {item.symbol: item.reason.value for item in result.excluded}
    rows = []
    for position, item in enumerate(universe, start=1):
        symbol = str(getattr(item, "symbol")).strip().upper()
        cap = getattr(item, "market_cap", None)
        if symbol in ranked:
            outcome, rank, reason = UNIVERSE_OUTCOME_CANDIDATE, ranked[symbol], None
        elif symbol in excluded:
            outcome, rank, reason = UNIVERSE_OUTCOME_EXCLUDED, None, excluded[symbol]
        else:
            outcome, rank, reason = UNIVERSE_OUTCOME_NOT_EVALUATED, None, None
        rows.append(ScannerUniverseInputData(
            position=position, symbol=symbol, exchange_code=getattr(item, "exchange_code", None),
            company_name=getattr(item, "company_name", None),
            market_cap=None if cap is None else repr(cap), outcome=outcome,
            scanner_rank=rank, exclusion_reason=reason))
    return rows


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
        return self.persist_result(result, started_at=started_at)

    def persist_result(
        self, result: ScannerResult, *, started_at: datetime | None = None,
        universe: Sequence[object] | None = None, universe_source: str | None = None,
        universe_acquired_at: datetime | None = None,
    ) -> tuple[int, ScannerResult]:
        """Persist an already-fetched immutable snapshot without another provider call.

        With ``universe`` the provider's universe rows are recorded in the same transaction
        (``scanner_universe_inputs``, RECORDED_PAPER_UNIVERSE); without it nothing changes.
        """
        if universe is not None and (universe_source is None or universe_acquired_at is None):
            raise ValueError("a recorded universe needs its source and acquisition time")
        session = self.repository.session
        if session.in_transaction():
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("ScannerService requires a clean Session before taking transaction ownership")
            session.commit()
        started_at = started_at or self.clock()
        try:
            run = self.repository.create_run(
                trading_date=result.trading_date,
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
            if universe is not None:
                self.repository.add_universe_inputs(
                    run.id, universe_inputs(universe, result), source=str(universe_source),
                    acquired_at=universe_acquired_at)  # type: ignore[arg-type]
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
