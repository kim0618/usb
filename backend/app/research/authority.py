"""Single resolver and mutation boundary for GPT trading authority."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResearchError
from app.models.research import GPTAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerRun
from app.repositories.research import ResearchRepository
from app.research import current_run as CR


class CurrentAuthorityStatus(StrEnum):
    NO_SCANNER_RUN = "NO_SCANNER_RUN"
    NO_ACTIVE_ANALYSIS = "NO_ACTIVE_ANALYSIS"
    NO_APPROVALS = "NO_APPROVALS"
    READY = "READY"


@dataclass(frozen=True)
class CurrentAuthority:
    """The current chain: latest COMPLETED ScannerRun -> its active analysis -> APPROVEs."""

    status: CurrentAuthorityStatus
    run: ScannerRun | None
    analysis: GPTAnalysis | None
    approved_count: int


def latest_completed_run(session: Session) -> ScannerRun | None:
    """The newest COMPLETED run of any source. Kept under its own name; see below."""
    return CR.newest_completed_run(session)


def current_run(session: Session) -> ScannerRun | None:
    """The run this service resolves authority for.

    Not ``latest_completed_run``: with the live candidate source on, the newest run overall is
    the legacy trade-value run for most of the day, and an analysis activated on it is never
    read by the entry runtime. ``app.research.current_run`` states that in full.
    """
    return CR.current_run(session)


class ResearchAuthorityService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ResearchRepository(session)

    def resolve(self, scanner_run_id: int | None = None) -> GPTAnalysis | None:
        """Active analysis of one run; without an id, of the current run only.

        An older run's active analysis is never a fallback for the current run: a new
        ScannerRun without its own analysis has no current analysis at all.
        """
        if scanner_run_id is None:
            run = current_run(self.session)
            if run is None:
                return None
            scanner_run_id = run.id
        return self.repository.get_active_analysis(scanner_run_id)

    def current(self) -> CurrentAuthority:
        run = current_run(self.session)
        if run is None:
            return CurrentAuthority(CurrentAuthorityStatus.NO_SCANNER_RUN, None, None, 0)
        analysis = self.repository.get_active_analysis(run.id)
        if analysis is None:
            return CurrentAuthority(CurrentAuthorityStatus.NO_ACTIVE_ANALYSIS, run, None, 0)
        approved = int(self.session.scalar(
            select(func.count()).select_from(HumanDecisionRecord).where(
                HumanDecisionRecord.gpt_analysis_id == analysis.id,
                HumanDecisionRecord.decision == "APPROVE",
            )
        ) or 0)
        status = CurrentAuthorityStatus.READY if approved else CurrentAuthorityStatus.NO_APPROVALS
        return CurrentAuthority(status, run, analysis, approved)

    def activate(self, scanner_run_id: int, analysis_id: int) -> GPTAnalysis:
        run = self.session.get(ScannerRun, scanner_run_id)
        analysis = self.session.get(GPTAnalysis, analysis_id)
        if run is None or analysis is None:
            raise ResearchError("Scanner run or GPT analysis does not exist")
        if analysis.scanner_run_id != run.id:
            raise ResearchError("GPT analysis belongs to a different scanner run")
        if analysis.status != "IMPORTED":
            raise ResearchError("Only IMPORTED GPT analysis can be activated")
        run.active_gpt_analysis_id = analysis.id
        self.session.commit()
        return analysis
