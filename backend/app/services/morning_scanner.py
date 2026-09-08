"""Calendar-gated, idempotent morning Scanner orchestration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.market.calendar import MarketCalendar
from app.models.scanner import ScannerRun
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.prompt import ResearchPromptService
from app.scanner.domain import ScannerResult


class MorningJobStatus(StrEnum):
    COMPLETED = "COMPLETED"
    REUSED = "REUSED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class MorningScanExecution:
    run_id: int
    result: ScannerResult
    mapping_issues: dict[str, int]
    order_request_count: int


@dataclass(frozen=True)
class MorningJobResult:
    status: MorningJobStatus
    target_trading_date: date
    scanner_run_id: int | None = None
    prompt_chars: int = 0
    mapping_issues: dict[str, int] | None = None
    latest_bar_missing_symbols: tuple[str, ...] = ()
    order_request_count: int = 0
    reason: str | None = None


class MorningScannerService:
    """Own the non-trading morning workflow around existing Scanner services."""

    def __init__(
        self,
        repository: ScannerSnapshotRepository,
        execute_scan: Callable[[date, datetime], MorningScanExecution],
        *,
        calendar: MarketCalendar | None = None,
        provider_name: str = "KIWOOM_REAL",
        score_version: str = "quant_v0",
    ) -> None:
        self.repository = repository
        self.execute_scan = execute_scan
        self.calendar = calendar or MarketCalendar("America/New_York")
        self.provider_name = provider_name
        self.score_version = score_version

    def run(self, now: datetime) -> MorningJobResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        target = now.astimezone(self.calendar.timezone).date()
        session_window = self.calendar.session(target)
        if session_window is None:
            return MorningJobResult(
                status=MorningJobStatus.SKIPPED,
                target_trading_date=target,
                reason="XNYS_NON_TRADING_DAY",
            )
        if now.astimezone(self.calendar.timezone) < session_window.market_close:
            raise RuntimeError(f"XNYS session {target.isoformat()} has not closed")

        existing = self.repository.get_latest_completed_run(
            target, score_version=self.score_version, provider=self.provider_name
        )
        if existing is not None:
            prompt = self._prepare_prompt(existing)
            return MorningJobResult(
                status=MorningJobStatus.REUSED,
                target_trading_date=target,
                scanner_run_id=existing.id,
                prompt_chars=len(prompt),
                reason="COMPLETED_RUN_EXISTS",
            )

        execution = self.execute_scan(target, now)
        if execution.result.trading_date != target:
            raise RuntimeError("Scanner result trading_date does not match morning target")
        run = self.repository.session.get(ScannerRun, execution.run_id)
        if run is None or run.status != "COMPLETED" or run.trading_date != target:
            raise RuntimeError("Scanner execution did not persist the target COMPLETED run")
        prompt = self._prepare_prompt(run)
        return MorningJobResult(
            status=MorningJobStatus.COMPLETED,
            target_trading_date=target,
            scanner_run_id=run.id,
            prompt_chars=len(prompt),
            mapping_issues=dict(execution.mapping_issues),
            latest_bar_missing_symbols=execution.result.latest_bar_missing_symbols,
            order_request_count=execution.order_request_count,
        )

    def _prepare_prompt(self, run: ScannerRun) -> str:
        return ResearchPromptService(self.repository).generate_top_for_run(run)
