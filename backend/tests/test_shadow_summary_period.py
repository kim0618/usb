"""Stage 9.14.2 Shadow summary period contract tests."""

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.models.execution import ShadowTradeRecord
from app.models.scanner import ScannerRun


@pytest.fixture
def api(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'period.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    async def override():  # type: ignore[no-untyped-def]
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    return app, sessions


def shadow(record_id: str, variant: str, status: str, *, exit_at: datetime | None = None,
           run_id: int | None = None, net_r: str = "0", holding_days: int = 0) -> ShadowTradeRecord:
    amount = Decimal(net_r)
    return ShadowTradeRecord(
        id=record_id, scanner_run_id=run_id, scanner_candidate_id=None, gpt_analysis_id=None,
        symbol=record_id, variant=variant, variant_version="shadow_variants_v0", is_control=variant == "C",
        initial_planned_risk=Decimal("100"), entry_at=exit_at, exit_at=exit_at,
        average_entry_price=Decimal("10") if status == "CLOSED" else None,
        average_exit_price=Decimal("11") if status == "CLOSED" else None,
        gross_pnl=amount * 100, net_pnl=amount * 100, gross_r=amount, net_r=amount,
        total_cost=Decimal("0"), ambiguous_bar_count=0, status=status,
        holding_days=holding_days,
    )


async def get(app, path: str):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_periods_use_et_exit_date_and_change_results(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    with sessions() as db:
        db.add_all([
            shadow("old", "A", "CLOSED", exit_at=datetime(2026, 7, 25, 16, tzinfo=timezone.utc), net_r="-1"),
            shadow("mid", "A", "CLOSED", exit_at=datetime(2026, 8, 15, 16, tzinfo=timezone.utc), net_r="2"),
            shadow("recent", "A", "CLOSED", exit_at=datetime(2026, 8, 30, 16, tzinfo=timezone.utc), net_r="3", holding_days=2),
        ]); db.commit()
    seven = (await get(app, "/api/v1/shadow/summary?start_date=2026-08-28&end_date=2026-09-03")).json()["variants"][0]
    thirty = (await get(app, "/api/v1/shadow/summary?start_date=2026-08-05&end_date=2026-09-03")).json()["variants"][0]
    all_time = (await get(app, "/api/v1/shadow/summary")).json()["variants"][0]
    assert (seven["trades"], seven["net_r"], seven["overnight"]) == (1, "3", 1)
    assert (thirty["trades"], thirty["net_r"]) == (2, "5")
    assert (all_time["trades"], all_time["net_r"]) == (3, "4")


@pytest.mark.asyncio
async def test_inclusive_boundaries_and_et_timezone(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    with sessions() as db:
        db.add_all([
            shadow("before", "B", "CLOSED", exit_at=datetime(2026, 8, 10, 3, 59, tzinfo=timezone.utc), net_r="1"),
            shadow("start", "B", "CLOSED", exit_at=datetime(2026, 8, 10, 4, tzinfo=timezone.utc), net_r="1"),
            shadow("end", "B", "CLOSED", exit_at=datetime(2026, 8, 12, 3, 59, tzinfo=timezone.utc), net_r="1"),
            shadow("after", "B", "CLOSED", exit_at=datetime(2026, 8, 12, 4, tzinfo=timezone.utc), net_r="1"),
        ]); db.commit()
    result = (await get(app, "/api/v1/shadow/summary?start_date=2026-08-10&end_date=2026-08-11")).json()["variants"][1]
    assert result["trades"] == 2


@pytest.mark.asyncio
async def test_no_trade_uses_scanner_trading_date_and_variants_stay_isolated(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    now = datetime(2026, 8, 30, 20, tzinfo=timezone.utc)
    with sessions() as db:
        old_run = ScannerRun(trading_date=date(2026, 7, 1), started_at=now, completed_at=now, status="COMPLETED", provider="FAKE", score_version="quant_v0")
        recent_run = ScannerRun(trading_date=date(2026, 8, 30), started_at=now, completed_at=now, status="COMPLETED", provider="FAKE", score_version="quant_v0")
        db.add_all([old_run, recent_run]); db.flush()
        db.add_all([
            shadow("a-no-old", "A", "NO_TRADE", run_id=old_run.id),
            shadow("a-no-recent", "A", "NO_TRADE", run_id=recent_run.id),
            shadow("c-win", "C", "CLOSED", exit_at=now, net_r="2"),
            shadow("e-loss", "E", "CLOSED", exit_at=now, net_r="-1"),
        ]); db.commit()
    payload = (await get(app, "/api/v1/shadow/summary?start_date=2026-08-28&end_date=2026-09-03")).json()["variants"]
    assert payload[0]["candidate_paths"] == 1 and payload[0]["no_trade"] == 1
    assert payload[2]["control"] is True and payload[2]["wins"] == 1 and payload[2]["losses"] == 0
    assert payload[4]["wins"] == 0 and payload[4]["losses"] == 1


@pytest.mark.asyncio
async def test_invalid_dates_use_existing_4xx_contract(api) -> None:  # type: ignore[no-untyped-def]
    app, _ = api
    invalid_range = await get(app, "/api/v1/shadow/summary?start_date=2026-09-03&end_date=2026-09-01")
    invalid_format = await get(app, "/api/v1/shadow/summary?start_date=not-a-date")
    assert invalid_range.status_code == 400
    assert invalid_range.json()["error"]["code"] == "INVALID_REQUEST"
    assert invalid_format.status_code == 422
    assert invalid_format.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
