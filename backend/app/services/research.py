"""Atomic GPT JSON import and mutable current human-decision services."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import ValidationError

from app.core.exceptions import ResearchError
from app.market.symbols import normalize_symbol
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, GPTSource, HumanDecisionRecord
from app.models.scanner import ScannerRun
from app.repositories.research import ResearchRepository
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.domain import GPTResearchResult, HumanDecision, ResearchStatus
from app.research.evidence import evidence_confidence
from app.research.versions import ACCEPTED_PROMPT_VERSIONS, EVIDENCE_VERSION, GPT_SCHEMA_VERSION

MAX_JSON_BYTES = 1_000_000


class GPTImportService:
    def __init__(self, repository: ResearchRepository, scanner_repository: ScannerSnapshotRepository, *, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.scanner_repository = scanner_repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def import_json(self, raw_json: str) -> GPTAnalysis:
        if len(raw_json.encode("utf-8")) > MAX_JSON_BYTES:
            raise ResearchError("GPT JSON exceeds the 1 MB import limit")
        try:
            decoded = json.loads(raw_json)
            result = GPTResearchResult.model_validate(decoded)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ResearchError(f"Invalid GPT research JSON: {exc}") from exc
        self._validate_contract(result)
        run = self.repository.session.get(ScannerRun, result.scanner_run_id)
        if run is None:
            raise ResearchError("scanner_run_id does not exist")
        if run.status != "COMPLETED":
            raise ResearchError("scanner run is not COMPLETED")
        if run.trading_date != result.trading_date:
            raise ResearchError("trading_date does not match scanner run")
        scanner_candidates = self.scanner_repository.get_top8(run.id)
        expected = {candidate.symbol: candidate for candidate in scanner_candidates}
        actual = [candidate.ticker for candidate in result.candidates]
        if len(actual) != len(set(actual)):
            raise ResearchError("duplicate ticker")
        if set(actual) != set(expected) or len(actual) != len(expected):
            raise ResearchError("candidate symbols/count must exactly match stored Top candidates")
        ranks = sorted(candidate.gpt_rank for candidate in result.candidates)
        if ranks != list(range(1, len(result.candidates) + 1)):
            raise ResearchError("GPT ranks must be unique and contiguous from 1")
        canonical = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        session = self.repository.session
        if session.in_transaction():
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("GPTImportService requires a clean Session")
            session.commit()
        if self.repository.find_duplicate(run.id, payload_hash) is not None:
            session.rollback()
            raise ResearchError("This payload was already imported for the scanner run")
        try:
            analysis = self.repository.add_analysis(GPTAnalysis(
                scanner_run_id=run.id, trading_date=result.trading_date, provider=result.provider,
                model=result.model, prompt_version=result.prompt_version, schema_version=result.schema_version,
                evidence_version=EVIDENCE_VERSION, analysis_at=result.analysis_at,
                imported_at=self.clock(), status=ResearchStatus.IMPORTED.value,
                raw_json=raw_json, payload_hash=payload_hash,
            ))
            for item in result.candidates:
                candidate_analysis = self.repository.add_candidate(GPTCandidateAnalysis(
                    gpt_analysis_id=analysis.id, scanner_candidate_id=expected[item.ticker].id,
                    symbol=item.ticker, gpt_rank=item.gpt_rank,
                    overall_score=item.overall_score, catalyst_score=item.catalyst_score,
                    fundamental_score=item.fundamental_score, momentum_score=item.momentum_score,
                    risk_score=item.risk_score,
                    evidence_confidence=evidence_confidence(item.sources, item.unknown_fields),
                    catalyst_duration=item.catalyst_duration.value, stop_profile=item.stop_profile.value,
                    trailing_profile=item.trailing_profile.value,
                    overnight_suitability=item.overnight_suitability.value,
                    company_summary=item.company_summary, catalyst_summary=item.catalyst_summary,
                    risk_summary=item.risk_summary, invalidation_summary=item.invalidation_summary,
                    unknown_fields_json=item.unknown_fields,
                ))
                seen: set[tuple[object, ...]] = set()
                sources: list[GPTSource] = []
                for source in item.sources:
                    key = (source.claim.lower(), str(source.url), source.type.value, source.title, source.published_at)
                    if key in seen:
                        continue
                    seen.add(key)
                    sources.append(GPTSource(
                        gpt_candidate_analysis_id=candidate_analysis.id, claim=source.claim.lower(),
                        url=str(source.url), source_type=source.type.value, title=source.title,
                        published_at=source.published_at, source_domain=(source.url.host or "").lower(),
                    ))
                self.repository.add_sources(sources)
            session.commit()
            return analysis
        except Exception:
            session.rollback()
            raise

    @staticmethod
    def _validate_contract(result: GPTResearchResult) -> None:
        if result.schema_version != GPT_SCHEMA_VERSION:
            raise ResearchError("Unsupported schema_version")
        if result.prompt_version not in ACCEPTED_PROMPT_VERSIONS:
            raise ResearchError("Unsupported prompt_version")


class HumanDecisionService:
    def __init__(self, repository: ResearchRepository, *, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def decide(self, analysis_id: int, symbol: str, decision: HumanDecision | str, *, note: str | None = None, decided_at: datetime | None = None) -> HumanDecisionRecord:
        try:
            symbol = normalize_symbol(symbol)
            decision = HumanDecision(decision)
        except ValueError as exc:
            raise ResearchError("Invalid symbol or human decision") from exc
        timestamp = decided_at or self.clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ResearchError("decided_at must be timezone-aware")
        session = self.repository.session
        try:
            analysis = self.repository.get_analysis_with_candidates(analysis_id)
            if analysis is None or analysis.status != "IMPORTED":
                raise ResearchError("GPT analysis does not exist or is not imported")
            candidate = next((item for item in analysis.candidates if item.symbol == symbol), None)
            if candidate is None:
                raise ResearchError("Symbol is not present in this GPT analysis")
            existing = self.repository.get_decision(analysis_id, symbol)
            if decision is HumanDecision.APPROVE and self.repository.count_approvals(analysis_id, excluding_symbol=symbol) >= 2:
                raise ResearchError("At most two candidates may be APPROVED per GPT analysis")
            if existing is None:
                existing = HumanDecisionRecord(gpt_analysis_id=analysis_id, scanner_candidate_id=candidate.scanner_candidate_id, symbol=symbol, decision=decision.value, note=note, decided_at=timestamp)
            else:
                existing.decision, existing.note, existing.decided_at = decision.value, note, timestamp
            self.repository.save_decision(existing)
            session.commit()
            return existing
        except Exception:
            session.rollback()
            raise
