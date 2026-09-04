"""Stage 10B-4.2: the adoption API must serialize `recommendation_rank` and the snapshot momentum.

The Run-2-equivalent fixture below reproduces the real review run's persisted Quant ranks,
snapshot metrics and GPT scores in an isolated temporary database. It asserts what the HTTP
response actually carries; classification and the recommendation rule itself are unchanged.
"""

from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun


# symbol, quant_rank, gpt_rank, overall, catalyst, momentum_score, risk, evidence, fundamental, rvol, momentum, relative_strength
RUN2_ROWS = (
    ("TSLA", 1, 1, 91, 98, 98, 47, 25, 70, 1.83, 0.15954683441304418, 0.040942070617333126),
    ("SPCX", 2, 6, 68, 47, 99, 31, 31, 79, 1.16, 0.30873651235642185, 0.06434406169114704),
    ("AVGO", 3, 5, 70, 95, 38, 53, 36, 95, 2.98, -0.14643396383436558, -0.03711220711976393),
    ("META", 4, 3, 77, 64, 82, 59, 31, 88, 1.28, 0.042125784031191715, 0.07312448400535554),
    ("NVDA", 5, 2, 89, 97, 82, 67, 55, 98, 1.07, 0.049408648796748666, 0.004720055836735515),
    ("AAPL", 6, 7, 59, 35, 65, 69, 28, 93, 0.93, 0.05079863000544149, 0.040243166255214735),
    ("MSFT", 7, 4, 75, 80, 62, 82, 65, 96, 1.00, 0.020665786420197563, 0.006850244967177899),
)


@pytest.fixture
def api(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'adoption_rank.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    async def override():  # type: ignore[no-untyped-def]
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    return app, sessions


def seed_run2(sessions) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 9, 3, 20, tzinfo=timezone.utc)
    with sessions() as db:
        run = ScannerRun(trading_date=date(2026, 9, 3), started_at=now, completed_at=now,
            status="COMPLETED", provider="KIWOOM_REAL", score_version="quant_v0", universe_count=10,
            excluded_count=0, candidate_count=len(RUN2_ROWS), top8_count=len(RUN2_ROWS))
        db.add(run); db.flush()
        analysis = GPTAnalysis(scanner_run_id=run.id, trading_date=run.trading_date, provider="OpenAI",
            model="test", prompt_version="top8_research_v0", schema_version="gpt_research_v0",
            evidence_version="evidence_v0", analysis_at=now, imported_at=now, status="IMPORTED",
            raw_json="{}", payload_hash="b" * 64)
        db.add(analysis); db.flush()
        for symbol, quant_rank, gpt_rank, overall, catalyst, momentum_score, risk, evidence, fundamental, rvol, momentum, relative_strength in RUN2_ROWS:
            quant = ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=quant_rank, is_top8=True,
                score=1.0 / quant_rank, observed_at=now, available_at=now,
                score_components_json={"company_name": f"{symbol} INC", "latest_close": 370.51,
                    "previous_high": 384.04, "previous_return_pct": 0.038,
                    "raw": {"rvol": rvol, "relative_strength": relative_strength, "momentum": momentum}})
            db.add(quant); db.flush()
            db.add(GPTCandidateAnalysis(gpt_analysis_id=analysis.id, scanner_candidate_id=quant.id,
                symbol=symbol, gpt_rank=gpt_rank, overall_score=overall, catalyst_score=catalyst,
                fundamental_score=fundamental, momentum_score=momentum_score, risk_score=risk,
                evidence_confidence=evidence, catalyst_duration="ONE_TO_TWO_DAYS", stop_profile="NORMAL",
                trailing_profile="NORMAL", overnight_suitability="MEDIUM", company_summary=f"{symbol} 회사",
                catalyst_summary="촉매", risk_summary="위험", invalidation_summary="무효화",
                unknown_fields_json=[]))
        db.commit()


async def adoption(app):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/research/adoption")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_recommendation_rank_is_serialized_as_one_to_seven(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    seed_run2(sessions)
    payload = await adoption(app)
    ranks = {item["symbol"]: item["recommendation_rank"] for item in payload["items"]}
    assert ranks == {"TSLA": 1, "NVDA": 2, "MSFT": 3, "META": 4, "AVGO": 5, "SPCX": 6, "AAPL": 7}
    assert all(isinstance(item["recommendation_rank"], int) for item in payload["items"])


@pytest.mark.asyncio
async def test_items_are_returned_in_recommendation_order_not_gpt_order(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    seed_run2(sessions)
    payload = await adoption(app)
    assert [item["symbol"] for item in payload["items"]] == ["TSLA", "NVDA", "MSFT", "META", "AVGO", "SPCX", "AAPL"]
    assert [item["recommendation_rank"] for item in payload["items"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [item["gpt_rank"] for item in payload["items"]] != sorted(item["gpt_rank"] for item in payload["items"])


@pytest.mark.asyncio
async def test_classification_and_counts_stay_three_two_two(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    seed_run2(sessions)
    payload = await adoption(app)
    assert payload["counts"] == {"adoption_candidate": 3, "review_required": 2, "excluded": 2}
    assert {item["symbol"]: item["classification"] for item in payload["items"]} == {
        "TSLA": "ADOPTION_CANDIDATE", "NVDA": "ADOPTION_CANDIDATE", "MSFT": "ADOPTION_CANDIDATE",
        "META": "REVIEW_REQUIRED", "AVGO": "REVIEW_REQUIRED", "SPCX": "EXCLUDED", "AAPL": "EXCLUDED"}
    assert payload["filter_version"] == "adoption_filter_v0"


@pytest.mark.asyncio
async def test_snapshot_momentum_is_surfaced_for_every_item(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    seed_run2(sessions)
    payload = await adoption(app)
    momentum = {item["symbol"]: item["momentum"] for item in payload["items"]}
    assert momentum["TSLA"] == pytest.approx(0.15954683441304418)
    assert momentum["AVGO"] == pytest.approx(-0.14643396383436558)
    assert all(value is not None for value in momentum.values())
    tsla = next(item for item in payload["items"] if item["symbol"] == "TSLA")
    assert tsla["relative_strength"] == pytest.approx(0.040942070617333126)
    assert tsla["rvol"] == pytest.approx(1.83)


@pytest.mark.asyncio
async def test_adoption_read_never_creates_a_human_decision(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    seed_run2(sessions)
    await adoption(app)
    with sessions() as db:
        assert db.query(HumanDecisionRecord).count() == 0
