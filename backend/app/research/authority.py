"""Single resolver and mutation boundary for GPT trading authority."""

from sqlalchemy.orm import Session

from app.core.exceptions import ResearchError
from app.models.research import GPTAnalysis
from app.models.scanner import ScannerRun
from app.repositories.research import ResearchRepository


class ResearchAuthorityService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ResearchRepository(session)

    def resolve(self, scanner_run_id: int | None = None) -> GPTAnalysis | None:
        if scanner_run_id is None:
            return self.repository.get_latest_active_analysis()
        return self.repository.get_active_analysis(scanner_run_id)

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
