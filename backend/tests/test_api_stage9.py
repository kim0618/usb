"""Stage 9 HTTP contract tests against an isolated fresh SQLite database."""

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
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
async def test_decimal_execution_serialization(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api; now = datetime(2026, 9, 1, 20, tzinfo=timezone.utc)
    with sessions() as db:
        db.add(ExecutionOrderRecord(id="O1", broker_type="SIM", symbol="AAA", side="BUY",
            requested_quantity=Decimal("2.0000"), filled_quantity=Decimal("2.0000"), status="FILLED",
            reference_price=Decimal("10.2500"), submitted_at=now, completed_at=now, execution_version="execution_v0")); db.flush()
        db.add(ExecutionFillRecord(id="F1", order_id="O1", quantity=Decimal("2.0000"), raw_market_price=Decimal("10"),
            fill_price=Decimal("10.2500"), spread_cost=Decimal("0.1"), slippage_cost=Decimal("0.1"),
            commission=Decimal("0.1"), fx_cost=Decimal("0"), total_cost=Decimal("0.3"), filled_at=now)); db.commit()
    assert (await get(app, "/api/v1/trading/orders")).json()[0]["reference_price"] == "10.2500"
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
