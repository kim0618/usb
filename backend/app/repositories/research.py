"""Persistence operations for GPT research and current human decisions."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.research import GPTAnalysis, GPTCandidateAnalysis, GPTSource, HumanDecisionRecord


class ResearchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_analysis(self, analysis: GPTAnalysis) -> GPTAnalysis:
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def add_candidate(self, candidate: GPTCandidateAnalysis) -> GPTCandidateAnalysis:
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def add_sources(self, sources: list[GPTSource]) -> None:
        self.session.add_all(sources)
        self.session.flush()

    def get_analysis(self, analysis_id: int) -> GPTAnalysis | None:
        return self.session.get(GPTAnalysis, analysis_id)

    def get_candidates(self, analysis_id: int) -> list[GPTCandidateAnalysis]:
        statement = select(GPTCandidateAnalysis).where(GPTCandidateAnalysis.gpt_analysis_id == analysis_id).order_by(GPTCandidateAnalysis.gpt_rank)
        return list(self.session.scalars(statement))

    def get_sources(self, candidate_analysis_id: int) -> list[GPTSource]:
        return list(self.session.scalars(select(GPTSource).where(GPTSource.gpt_candidate_analysis_id == candidate_analysis_id).order_by(GPTSource.id)))

    def get_by_scanner_run(self, scanner_run_id: int) -> list[GPTAnalysis]:
        statement = select(GPTAnalysis).where(GPTAnalysis.scanner_run_id == scanner_run_id).order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc())
        return list(self.session.scalars(statement))

    def get_latest_valid_analysis(self, scanner_run_id: int) -> GPTAnalysis | None:
        statement = select(GPTAnalysis).where(GPTAnalysis.scanner_run_id == scanner_run_id, GPTAnalysis.status == "IMPORTED").order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc()).limit(1)
        return self.session.scalar(statement)

    def find_duplicate(self, scanner_run_id: int, payload_hash: str) -> GPTAnalysis | None:
        return self.session.scalar(select(GPTAnalysis).where(GPTAnalysis.scanner_run_id == scanner_run_id, GPTAnalysis.payload_hash == payload_hash, GPTAnalysis.status == "IMPORTED"))

    def get_analysis_with_candidates(self, analysis_id: int) -> GPTAnalysis | None:
        return self.session.scalar(select(GPTAnalysis).where(GPTAnalysis.id == analysis_id).options(selectinload(GPTAnalysis.candidates)))

    def get_decision(self, analysis_id: int, symbol: str) -> HumanDecisionRecord | None:
        return self.session.scalar(select(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == analysis_id, HumanDecisionRecord.symbol == symbol))

    def count_approvals(self, analysis_id: int, *, excluding_symbol: str | None = None) -> int:
        statement = select(func.count()).select_from(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == analysis_id, HumanDecisionRecord.decision == "APPROVE")
        if excluding_symbol is not None:
            statement = statement.where(HumanDecisionRecord.symbol != excluding_symbol)
        return int(self.session.scalar(statement) or 0)

    def save_decision(self, record: HumanDecisionRecord) -> HumanDecisionRecord:
        self.session.add(record)
        self.session.flush()
        return record
