"""Durable premarket rejection reasons.

The gate already decided why a symbol never reached an entry decision; before this
contract that reason was dropped and only the terminal phase survived. These tests
pin the reason to persistence, to the API projection, and to the transition rule
that stops one phase's reason leaking into the next.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.models.strategy import StrategyStateRecord
from app.repositories.strategy import StrategyStateRepository
from app.services.strategy import StrategyLifecycleService
from app.strategy.engine import PremarketContext, StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase, StrategyState, TERMINAL_PHASES

AT = datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc)
DAY = date(2026, 9, 8)

# Contexts chosen against the frozen thresholds (gap 0.02-0.15, volume ratio >= 0.05).
# The thresholds themselves are never asserted here; only which reason they produce.
CONTEXTS: dict[StrategyReason, PremarketContext] = {
    StrategyReason.INVALID_PREMARKET_DATA: PremarketContext(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
    StrategyReason.GAP_TOO_LOW: PremarketContext(
        Decimal("100"), Decimal("101"), Decimal("100"), Decimal("1000")),
    StrategyReason.GAP_TOO_HIGH: PremarketContext(
        Decimal("100"), Decimal("120"), Decimal("100"), Decimal("1000")),
    StrategyReason.LOW_PREMARKET_VOLUME: PremarketContext(
        Decimal("100"), Decimal("105"), Decimal("1"), Decimal("1000")),
}
PASSING = PremarketContext(Decimal("100"), Decimal("105"), Decimal("100"), Decimal("1000"))


def approved(symbol: str = "NVDA") -> StrategyState:
    state = StrategyState(symbol, DAY, scanner_candidate_id=1)
    return StrategyLifecycleService.apply_human_gate(state, approved=True, shadow_mode=False)


def gated(context: PremarketContext, symbol: str = "NVDA") -> StrategyState:
    gate = StrategyV0Engine().premarket_gate(context, human_approved=True, shadow_mode=False)
    return StrategyLifecycleService.apply_premarket_gate(approved(symbol), gate)


@pytest.fixture
def durable(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'reason.sqlite3'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.mark.parametrize("reason", list(CONTEXTS))
def test_each_premarket_rejection_reason_is_persisted_as_its_code(durable, reason) -> None:
    state = gated(CONTEXTS[reason])
    assert state.phase is StrategyPhase.PREMARKET_REJECTED
    assert state.phase_reason == reason.value

    with durable() as session:
        StrategyStateRepository(session).save(state, updated_at=AT)
        session.commit()
    # A restart reads the reason back rather than recomputing the gate.
    with durable() as session:
        reloaded = StrategyStateRepository(session).load("NVDA", DAY)
    assert reloaded is not None
    assert reloaded.phase is StrategyPhase.PREMARKET_REJECTED
    assert reloaded.phase_reason == reason.value


def test_a_passing_gate_records_no_reason(durable) -> None:
    state = gated(PASSING)
    assert state.phase is StrategyPhase.OPENING_RANGE_BUILDING
    assert state.phase_reason is None

    with durable() as session:
        StrategyStateRepository(session).save(state, updated_at=AT)
        session.commit()
    with durable() as session:
        assert StrategyStateRepository(session).load("NVDA", DAY).phase_reason is None


def test_premarket_rejection_stays_terminal() -> None:
    """Recording a reason does not make the phase reachable or re-enterable."""
    state = gated(CONTEXTS[StrategyReason.LOW_PREMARKET_VOLUME])
    assert state.phase in TERMINAL_PHASES
    with pytest.raises(ValueError, match="invalid strategy transition"):
        state.transition(StrategyPhase.OPENING_RANGE_BUILDING)


def test_a_reason_never_leaks_into_the_next_phase() -> None:
    """A transition clears the reason unless it states its own."""
    rejected = gated(CONTEXTS[StrategyReason.GAP_TOO_LOW])
    assert rejected.phase_reason == StrategyReason.GAP_TOO_LOW.value
    # Same state shape, but advanced through the passing path: nothing carries over.
    advanced = gated(PASSING)
    assert advanced.phase_reason is None
    carried = advanced.transition(StrategyPhase.WAITING_ENTRY)
    assert carried.phase_reason is None
    explicit = advanced.transition(StrategyPhase.WAITING_ENTRY, phase_reason="EXPLICIT")
    assert explicit.phase_reason == "EXPLICIT"


def test_rows_written_before_this_revision_load_with_a_null_reason(durable) -> None:
    """Production already holds PREMARKET_REJECTED rows whose reason was never captured."""
    with durable() as session:
        session.add(StrategyStateRecord(
            symbol="AAPL", trading_date=DAY, book="ACTUAL", variant="ACTUAL",
            phase=StrategyPhase.PREMARKET_REJECTED.value, phase_reason=None,
            add_count=0, add_signal_issued=False, holding_day=0, overnight=False,
            strategy_version="strategy_v0", trailing_profile="WIDE",
            overnight_suitability="MEDIUM", created_at=AT, updated_at=AT))
        session.commit()
    with durable() as session:
        loaded = StrategyStateRepository(session).load("AAPL", DAY)
    assert loaded is not None
    assert loaded.phase is StrategyPhase.PREMARKET_REJECTED
    assert loaded.phase_reason is None


def test_reasons_are_aggregatable_for_later_paper_analytics(durable) -> None:
    """The stored shape supports a plain GROUP BY; no analytics surface is built here."""
    from sqlalchemy import func, select
    rows = {"NVDA": StrategyReason.LOW_PREMARKET_VOLUME, "AAPL": StrategyReason.GAP_TOO_LOW,
            "TSLA": StrategyReason.LOW_PREMARKET_VOLUME}
    with durable() as session:
        repository = StrategyStateRepository(session)
        for symbol, reason in rows.items():
            repository.save(gated(CONTEXTS[reason], symbol), updated_at=AT)
        session.commit()
    with durable() as session:
        counts = dict(session.execute(
            select(StrategyStateRecord.phase_reason, func.count())
            .where(StrategyStateRecord.phase == StrategyPhase.PREMARKET_REJECTED.value)
            .group_by(StrategyStateRecord.phase_reason)).all())
    assert counts == {StrategyReason.LOW_PREMARKET_VOLUME.value: 2,
                      StrategyReason.GAP_TOO_LOW.value: 1}


@pytest.mark.asyncio
async def test_trading_projection_exposes_the_reason_without_touching_a_provider(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'api.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        repository = StrategyStateRepository(session)
        repository.save(gated(CONTEXTS[StrategyReason.LOW_PREMARKET_VOLUME], "NVDA"), updated_at=AT)
        repository.save(gated(PASSING, "AAPL"), updated_at=AT)
        session.commit()

    app = create_app()

    async def override():  # type: ignore[no-untyped-def]
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override

    def explode() -> None:  # pragma: no cover - a read path must never reach this
        raise AssertionError("the trading projection must not construct a market data provider")

    import app.market.factory as market_factory
    original = market_factory.build_kiwoom_provider
    market_factory.build_kiwoom_provider = lambda *a, **k: explode()  # type: ignore[assignment]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.get("/api/v1/trading")
    finally:
        market_factory.build_kiwoom_provider = original  # type: ignore[assignment]

    assert response.status_code == 200
    states = {row["symbol"]: row for row in response.json()["strategy_states"]}
    assert states["NVDA"]["phase"] == StrategyPhase.PREMARKET_REJECTED.value
    assert states["NVDA"]["phase_reason"] == StrategyReason.LOW_PREMARKET_VOLUME.value
    assert states["AAPL"]["phase"] == StrategyPhase.OPENING_RANGE_BUILDING.value
    assert states["AAPL"]["phase_reason"] is None
    engine.dispose()
