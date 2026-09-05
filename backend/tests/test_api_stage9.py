"""Stage 9 HTTP contract tests against an isolated fresh SQLite database."""

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun


@pytest.fixture
def api(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'api.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()
    async def override():  # type: ignore[no-untyped-def]
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    return app, sessions


async def get(app, path: str):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_fresh_database_control_plane_is_null_safe(api) -> None:  # type: ignore[no-untyped-def]
    app, _ = api
    for path in ("/health", "/api/v1/dashboard", "/api/v1/runtime", "/api/v1/settings", "/api/v1/capabilities", "/openapi.json"):
        assert (await get(app, path)).status_code == 200
    dashboard = (await get(app, "/api/v1/dashboard")).json()
    assert dashboard["scanner"]["latest_run_id"] is None
    assert dashboard["trading"]["broker_mode"] == "SIMULATION"


@pytest.mark.asyncio
async def test_scanner_latest_detail_history_and_ordering(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api; now = datetime(2026, 9, 1, 20, tzinfo=timezone.utc)
    with sessions() as db:
        run = ScannerRun(trading_date=date(2026, 9, 1), started_at=now, completed_at=now,
            status="COMPLETED", provider="FAKE", score_version="quant_v0", universe_count=2,
            excluded_count=0, candidate_count=2, top8_count=2)
        db.add(run); db.flush()
        db.add_all([ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=rank, is_top8=True,
            score=score, score_components_json={"latest_close": close, "raw": {"rvol": score}},
            observed_at=now, available_at=now) for symbol, rank, score, close in
            (("BBB", 2, 1.0, 20.0), ("AAA", 1, 2.0, 10.0))]); db.commit(); run_id = run.id
    latest = await get(app, "/api/v1/scanner/latest")
    assert latest.status_code == 200
    assert [row["symbol"] for row in latest.json()["top8"]] == ["AAA", "BBB"]
    assert (await get(app, f"/api/v1/scanner/runs/{run_id}")).status_code == 200
    assert len((await get(app, "/api/v1/scanner/runs?limit=1")).json()) == 1


@pytest.mark.asyncio
async def test_missing_resources_use_common_error(api) -> None:  # type: ignore[no-untyped-def]
    response = await get(api[0], "/api/v1/scanner/latest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_adoption_endpoint_is_versioned_and_read_only(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api; now = datetime(2026, 9, 1, 20, tzinfo=timezone.utc)
    with sessions() as db:
        run = ScannerRun(trading_date=date(2026, 9, 1), started_at=now, completed_at=now,
            status="COMPLETED", provider="FAKE", score_version="quant_v0", universe_count=1,
            excluded_count=0, candidate_count=1, top8_count=1)
        db.add(run); db.flush()
        quant = ScannerCandidate(scanner_run_id=run.id, symbol="AAA", rank=1, is_top8=True, score=1.0,
            score_components_json={"company_name": "AAA Inc", "raw": {"rvol": 1.6, "relative_strength": .02}}, observed_at=now, available_at=now)
        db.add(quant); db.flush()
        analysis = GPTAnalysis(scanner_run_id=run.id, trading_date=run.trading_date, provider="OpenAI", model="test",
            prompt_version="top8_research_v0", schema_version="gpt_research_v0", evidence_version="evidence_v0",
            analysis_at=now, imported_at=now, status="IMPORTED", raw_json="{}", payload_hash="a" * 64)
        db.add(analysis); db.flush()
        db.add(GPTCandidateAnalysis(gpt_analysis_id=analysis.id, scanner_candidate_id=quant.id, symbol="AAA", gpt_rank=1,
            overall_score=80, catalyst_score=90, fundamental_score=95, momentum_score=80, risk_score=70,
            evidence_confidence=60, catalyst_duration="ONE_TO_TWO_DAYS", stop_profile="NORMAL", trailing_profile="WIDE",
            overnight_suitability="MEDIUM", company_summary="company", catalyst_summary="catalyst", risk_summary="risk",
            invalidation_summary="invalidate", unknown_fields_json=[]))
        db.commit()
    response = await get(app, "/api/v1/research/adoption")
    assert response.status_code == 200
    payload = response.json()
    assert payload["filter_version"] == "adoption_filter_v0"
    assert payload["counts"] == {"adoption_candidate": 1, "review_required": 0, "excluded": 0}
    assert payload["items"][0]["classification"] == "ADOPTION_CANDIDATE"
    assert payload["items"][0]["recommendation_rank"] == 1
    assert payload["items"][0]["company_summary"] == "company"
    assert (payload["items"][0]["previous_close"], payload["items"][0]["rvol"]) == (None, 1.6)
    assert (payload["items"][0]["relative_strength"], payload["items"][0]["momentum"]) == (.02, None)
    with sessions() as db:
        assert db.query(HumanDecisionRecord).count() == 0


@pytest.mark.asyncio
async def test_decimal_execution_serialization(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api; now = datetime(2026, 9, 1, 20, tzinfo=timezone.utc)
    with sessions() as db:
        db.add(ExecutionOrderRecord(id="O1", broker_type="SIM", symbol="AAA", side="BUY",
            requested_quantity=Decimal("2.0000"), filled_quantity=Decimal("2.0000"), status="FILLED",
            reference_price=Decimal("10.2500"), submitted_at=now, completed_at=now, execution_version="execution_v0")); db.flush()
        db.add(ExecutionOrderRecord(id="O2", broker_type="SIM", symbol="TSLA", side="BUY",
            requested_quantity=Decimal("166.6667"), filled_quantity=Decimal("0"), status="REJECTED",
            rejection_reason="NO_NEXT_BAR", reference_price=Decimal("102"), submitted_at=now,
            completed_at=None, execution_version="execution_v0")); db.flush()
        db.add(ExecutionFillRecord(id="F1", order_id="O1", quantity=Decimal("2.0000"), raw_market_price=Decimal("10"),
            fill_price=Decimal("10.2500"), spread_cost=Decimal("0.1"), slippage_cost=Decimal("0.1"),
            commission=Decimal("0.1"), fx_cost=Decimal("0"), total_cost=Decimal("0.3"), filled_at=now)); db.commit()
    orders = {item["order_id"]: item for item in (await get(app, "/api/v1/trading/orders")).json()}
    assert orders["O1"] == {
        "order_id": "O1", "broker_type": "SIM", "symbol": "AAA", "side": "BUY",
        "requested_quantity": "2.0000", "filled_quantity": "2.0000", "status": "FILLED",
        "rejection_reason": None, "reference_price": "10.2500",
        "submitted_at": "2026-09-01T20:00:00Z", "completed_at": "2026-09-01T20:00:00Z",
        "execution_version": "execution_v0",
    }
    assert orders["O2"]["broker_type"] == "SIM"
    assert orders["O2"]["status"] == "REJECTED"
    assert orders["O2"]["rejection_reason"] == "NO_NEXT_BAR"
    assert orders["O2"]["filled_quantity"] == "0"
    assert (await get(app, "/api/v1/trading/fills")).json()[0]["total_cost"] == "0.3"


@pytest.mark.asyncio
async def test_shadow_empty_has_stable_a_to_e_control(api) -> None:  # type: ignore[no-untyped-def]
    payload = (await get(api[0], "/api/v1/shadow/summary")).json()
    assert [item["variant"] for item in payload["variants"]] == list("ABCDE")
    assert [item["control"] for item in payload["variants"]] == [False, False, True, False, False]
    assert payload["source"] == "SIMULATION"


@pytest.mark.asyncio
async def test_replay_report_contract(api) -> None:  # type: ignore[no-untyped-def]
    response = await get(api[0], "/api/v1/replay-smoke/latest")
    assert response.status_code == 200
    assert isinstance(response.json()["available"], bool)


def test_replay_report_missing_is_available_false(api, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.api.service import APIQueryService
    from app.core.config import Settings
    _, sessions = api
    with sessions() as db:
        assert APIQueryService(db, Settings(data_dir=tmp_path)).replay_report()["available"] is False


@pytest.mark.asyncio
async def test_runtime_transitions_and_kill_switch_confirmation(api) -> None:  # type: ignore[no-untyped-def]
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/runtime/safe-mode", json={"reason": "operator"})).json()["mode"] == "SAFE_MODE"
        assert (await client.post("/api/v1/runtime/halt", json={"reason": "operator"})).json()["mode"] == "HALTED"
        denied = await client.post("/api/v1/runtime/kill-switch", json={"confirm": False, "reason": "test"})
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_settings_excludes_secrets(api) -> None:  # type: ignore[no-untyped-def]
    text = (await get(api[0], "/api/v1/settings")).text.lower()
    for forbidden in ("app_secret", "app_key", "database_url", "password", "token"):
        assert forbidden not in text


@pytest.mark.asyncio
async def test_cors_is_explicit(api) -> None:  # type: ignore[no-untyped-def]
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options("/api/v1/dashboard", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-origin"] != "*"
