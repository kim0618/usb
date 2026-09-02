import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, create_db_engine
from app.core.exceptions import ResearchError
from app.models.research import GPTAnalysis
from app.models.scanner import ScannerCandidate
from app.repositories.research import ResearchRepository
from app.repositories.scanner import ScannerCandidateData, ScannerSnapshotRepository
from app.research.domain import HumanDecision, ResearchSource, SourceType
from app.research.evidence import evidence_confidence
from app.research.prompt import ResearchPromptService
from app.services.research import GPTImportService, HumanDecisionService

NOW = datetime(2024, 6, 28, 22, tzinfo=timezone.utc)
DAY = date(2024, 6, 28)


def setup_run(session: Session, count: int = 3, *, completed: bool = True):
    scanner = ScannerSnapshotRepository(session)
    run = scanner.create_run(trading_date=DAY, started_at=NOW, provider="fake", score_version="quant_v0")
    scanner.add_candidates(run.id, [ScannerCandidateData(
        symbol=chr(65+i)*3, rank=i+1, is_top8=True, score=3-i,
        score_components={"raw": {"rvol": i+1}, "normalized": {}, "weighted_contributions": {}, "latest_close": 10+i, "latest_volume": 1000, "market_cap": 500_000_000},
        observed_at=NOW, available_at=NOW,
    ) for i in range(count)])
    if completed:
        scanner.complete_run(run.id, completed_at=NOW)
    session.commit()
    return run, scanner


def payload(run_id: int, count: int = 3) -> dict:
    return {"schema_version": "gpt_research_v0", "prompt_version": "top8_research_v0", "provider": "OpenAI", "model": "GPT-test", "analysis_at": "2024-06-29T01:00:00+00:00", "trading_date": DAY.isoformat(), "scanner_run_id": run_id,
            "candidates": [{"ticker": chr(65+i)*3, "gpt_rank": i+1, "overall_score": 80, "catalyst_score": 80, "fundamental_score": 70, "momentum_score": 75, "risk_score": 60, "catalyst_duration": "ONE_TO_TWO_DAYS", "stop_profile": "NORMAL", "trailing_profile": "WIDE", "overnight_suitability": "MEDIUM", "company_summary": "company", "catalyst_summary": "catalyst", "risk_summary": "risk", "invalidation_summary": "invalidate", "unknown_fields": [], "sources": [{"claim": "catalyst", "url": f"https://sec{i}.gov/file", "type": "SEC", "title": "filing", "published_at": None}]} for i in range(count)]}


@pytest.fixture
def db(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path/'research.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_prompt_latest_deterministic_and_detail(db: Session) -> None:
    old, scanner = setup_run(db)
    old.completed_at = NOW - timedelta(minutes=1)
    db.commit()
    latest, _ = setup_run(db)
    service = ResearchPromptService(scanner)
    first = service.generate_top_candidates(DAY)
    assert first == service.generate_top_candidates(DAY)
    assert f"scanner_run_id: {latest.id}" in first and "latest_close" in first
    detail = service.generate_detail(latest, scanner.get_top8(latest.id)[0])
    assert "stock_detail_research_v0" in detail


def test_prompt_rejects_empty_top_candidates(db: Session) -> None:
    run, scanner = setup_run(db, 0)
    with pytest.raises(ResearchError):
        ResearchPromptService(scanner).generate_top_candidates(DAY)


def src(claim: str, kind: SourceType, domain: str) -> ResearchSource:
    return ResearchSource(claim=claim, url=f"https://{domain}/x", type=kind, title="x")


def test_evidence_known_values_caps_and_order() -> None:
    high = [src("catalyst", SourceType.SEC, "sec.gov")] + [src(f"c{i}", SourceType.OFFICIAL, f"d{i}.com") for i in range(4)]
    assert evidence_confidence(high, []) == 100
    news = [src("catalyst", SourceType.NEWS, "reuters.com"), src("risk", SourceType.NEWS, "bloomberg.com")]
    assert evidence_confidence(news, []) == 50
    no_catalyst = [src(f"c{i}", SourceType.SEC, f"d{i}.com") for i in range(4)]
    assert evidence_confidence(no_catalyst, []) == 49
    penalized = [src("catalyst", SourceType.SEC, "sec.gov")]
    assert evidence_confidence(penalized, [str(i) for i in range(8)]) == 25
    repeated = [src("catalyst", SourceType.OTHER, "x.com")] + [src("risk", SourceType.OTHER, f"x{i}.com") for i in range(10)]
    assert evidence_confidence(repeated, []) == 30
    assert evidence_confidence(list(reversed(news)), []) == evidence_confidence(news, [])


def test_atomic_import_validation_duplicate_latest_and_quant_immutable(db: Session) -> None:
    run, scanner = setup_run(db)
    repository = ResearchRepository(db)
    service = GPTImportService(repository, scanner)
    before = [(c.id, c.rank, c.score, c.score_components_json, c.is_top8) for c in scanner.get_top8(run.id)]
    raw = json.dumps(payload(run.id))
    first = service.import_json(raw)
    assert len(repository.get_candidates(first.id)) == 3
    after = [(c.id, c.rank, c.score, c.score_components_json, c.is_top8) for c in scanner.get_top8(run.id)]
    assert before == after
    with pytest.raises(ResearchError):
        service.import_json(raw)
    assert db.scalar(select(func.count()).select_from(GPTAnalysis)) == 1
    later = payload(run.id); later["analysis_at"] = "2024-06-30T01:00:00+00:00"; later["model"] = "new"
    second = service.import_json(json.dumps(later))
    assert repository.get_latest_valid_analysis(run.id).id == second.id
    bad = payload(run.id); bad["candidates"][2]["gpt_rank"] = 2; bad["model"] = "bad"
    with pytest.raises(ResearchError):
        service.import_json(json.dumps(bad))
    assert db.scalar(select(func.count()).select_from(GPTAnalysis)) == 2


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema_version="wrong"),
    lambda p: p.update(prompt_version="wrong"),
    lambda p: p.update(provider=" "),
    lambda p: p.update(model=""),
    lambda p: p.update(analysis_at="2024-01-01T00:00:00"),
    lambda p: p["candidates"][0].update(overall_score=101),
    lambda p: p["candidates"][0].update(stop_profile="BAD"),
    lambda p: p["candidates"][0]["sources"][0].update(url="not-url"),
    lambda p: p["candidates"].pop(),
    lambda p: p["candidates"][0].update(ticker="ZZZ"),
])
def test_invalid_payloads_leave_no_rows(db: Session, mutation) -> None:
    run, scanner = setup_run(db)
    item = payload(run.id); mutation(item)
    with pytest.raises(ResearchError):
        GPTImportService(ResearchRepository(db), scanner).import_json(json.dumps(item))
    assert db.scalar(select(func.count()).select_from(GPTAnalysis)) == 0


def test_human_approval_limit_and_change(db: Session) -> None:
    run, scanner = setup_run(db)
    repo = ResearchRepository(db)
    analysis = GPTImportService(repo, scanner).import_json(json.dumps(payload(run.id)))
    service = HumanDecisionService(repo, clock=lambda: NOW)
    service.decide(analysis.id, "AAA", HumanDecision.APPROVE)
    service.decide(analysis.id, "BBB", "APPROVE")
    with pytest.raises(ResearchError): service.decide(analysis.id, "CCC", "APPROVE")
    service.decide(analysis.id, "AAA", "REJECT")
    assert service.decide(analysis.id, "CCC", "APPROVE").decision == "APPROVE"
    with pytest.raises(ResearchError): service.decide(analysis.id, "ZZZ", "REJECT")
