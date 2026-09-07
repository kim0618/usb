"""One market-data-to-durable-fill pass through the real operator composition.

Every layer here is the production object: the Kiwoom HTTP client behind its own
allowlist, the canonical payload mapping, the quant scanner, the GPT import and
human-decision services, the frozen Strategy V0 engine, the risk engine, the
SimBroker, the durable execution transaction, and the FastAPI app's own startup.
Only the network responses are fixtures - the smoke replaces what varies between
runs (HTTP payloads and the acquisition clock), never the code being validated.

Nothing touches the real operator database: the migrated 0009 database lives in
tmp_path and is bound in place of the process session factory, and the Kiwoom
transport fails the test if an order endpoint is ever attempted.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.core import database
from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import MarketDataError
from app.dev.run_real_market_simulation import inspect_readiness, safety_gate
from app.execution.config import ExecutionConfig
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession
from app.market.factory import build_kiwoom_provider
from app.market.universe import KiwoomUniverseSource
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerRun
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)
from app.repositories.research import ResearchRepository
from app.repositories.scanner import ScannerSnapshotRepository
from app.repositories.simulation import SimulationStateRepository
from app.research.domain import HumanDecision
from app.research.prompt import ResearchPromptService
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.scanner.scanner import QuantScanner
from app.services.research import GPTImportService, HumanDecisionService
from app.services.scanner import ScannerService
from app.services.simulation_runtime import (
    activate_operator_simulation_runtime, clear_active_sim_broker, get_active_runtime,
    get_active_sim_broker,
)
from app.services.strategy import StrategyLifecycleService, StrategyStateService
from app.strategy.domain import DecisionType
from app.strategy.engine import PremarketContext, StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import (
    OvernightSuitability, StrategyPhase, StrategyState, TrailingProfile,
)
from app.strategy.runner import StrategyLifecycleRunner
from backend.tests.test_simulation_persistence_schema import REVISION, _migrated_engine

ET = ZoneInfo("America/New_York")
CALENDAR = MarketCalendar()

SCAN_DAY = date(2026, 7, 1)
TRADING_DAY = date(2026, 7, 2)
SCAN_AS_OF = datetime(2026, 7, 1, 17, tzinfo=ET)
# 09:45 is the first bar after the 15-minute opening range; a completed bar is
# only visible one minute later, so the signal lands at 09:46 and the broker's
# next-bar rule fills on 09:47.
SIGNAL_AT = datetime(2026, 7, 2, 9, 46, tzinfo=ET)
FILL_BAR_AT = datetime(2026, 7, 2, 9, 47, tzinfo=ET)
FILL_POLL_AT = datetime(2026, 7, 2, 9, 48, tzinfo=ET)

SYMBOL = "TSLA"
UNIVERSE = (SYMBOL, *(f"S{i:02d}" for i in range(1, 10)))
HISTORY_DAYS = 25

CASH = Decimal("100000")
PREVIOUS_CLOSE = Decimal("100")
PREMARKET_PRICE = Decimal("105")
DAILY_VOLUME = Decimal("3000000")
ENTRY_PRICE = Decimal("102")
INITIAL_STOP = Decimal("99")
OPENING_RANGE_HIGH = Decimal("101")
PLANNED_RISK = Decimal("500")
QUANTITY = Decimal("166.6666666666666666666666667")
FILL_PRICE = Decimal("102.153")
SPREAD_COST = Decimal("17")
SLIPPAGE_COST = Decimal("8.50")
COMMISSION = Decimal("17")
FX_COST = Decimal("0")
TOTAL_COST = Decimal("42.50")
FILLED_CASH = Decimal("82957.50")


# Kiwoom fixture payloads ---------------------------------------------------

def sessions(count: int, end: date) -> list[date]:
    """Real XNYS sessions so the fixture grid is a calendar the code agrees with."""
    days: list[date] = []
    day = end
    while len(days) < count:
        if CALENDAR.is_trading_day(day):
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


HISTORY = sessions(HISTORY_DAYS, SCAN_DAY)


def daily_rows(symbol: str) -> list[dict[str, str]]:
    """TSLA trends into a 100 close on strong volume; the rest stay flat."""
    rows = []
    for index, day in enumerate(HISTORY):
        if symbol == "SPY":
            close, volume = 400.0, 1_000_000
        elif symbol == SYMBOL:
            close = float(PREVIOUS_CLOSE) - (len(HISTORY) - 1 - index) * 0.5
            volume = int(DAILY_VOLUME)
        else:
            close = 50.0 + int(symbol[1:]) * 0.1
            volume = 2_000_000 if index == len(HISTORY) - 1 else 1_000_000
        rows.append({"dt": day.strftime("%Y%m%d"), "open_pric": f"{close - 0.2:.4f}",
                     "high_pric": f"{close + 0.3:.4f}", "low_pric": f"{close - 0.5:.4f}",
                     "cur_prc": f"{close:.4f}", "acc_trde_qty": str(volume)})
    return rows


def minute_row(at: datetime, ohlc: tuple[float, float, float, float], volume: int) -> dict[str, str]:
    open_, high, low, close = ohlc
    return {"bus_dt": at.strftime("%Y%m%d"), "cntr_tm": at.strftime("%H%M%S"),
            "open_pric": f"{open_:.4f}", "high_pric": f"{high:.4f}", "low_pric": f"{low:.4f}",
            "cur_prc": f"{close:.4f}", "trde_qty": str(volume)}


def minute_rows() -> list[dict[str, str]]:
    """The TSLA trading-day tape: 5% premarket gap, 101/99 range, 102 breakout."""
    at = datetime.combine(TRADING_DAY, time(9), ET)
    rows = [minute_row(at + timedelta(minutes=i), (105.0, 105.1, 104.9, 105.0), 10_000)
            for i in range(30)]
    open_at = datetime.combine(TRADING_DAY, time(9, 30), ET)
    # The gap fades into the opening range, which the strategy reads as 101/99.
    rows.append(minute_row(open_at, (100.5, 101.0, 99.0, 100.0), 20_000))
    rows.extend(minute_row(open_at + timedelta(minutes=i), (100.0, 100.5, 99.5, 100.2), 20_000)
                for i in range(1, 15))
    rows.append(minute_row(open_at + timedelta(minutes=15), (100.3, 102.1, 100.2, 102.0), 30_000))
    rows.append(minute_row(open_at + timedelta(minutes=16), (102.0, 102.3, 101.95, 102.1), 25_000))
    rows.append(minute_row(FILL_BAR_AT, (102.0, 102.1, 101.9, 102.0), 25_000))
    return rows


def kiwoom_transport() -> httpx.MockTransport:
    """Serve canonical Kiwoom payloads; any order endpoint fails the smoke."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "smoke", "expires_dt": "20990101000000",
                                             "return_code": 0})
        api_id = request.headers["api-id"]
        if "ordr" in request.url.path or api_id.startswith("usa60"):
            pytest.fail(f"order endpoint attempted: {api_id} {request.url.path}")
        body: dict[str, Any] = json.loads(request.content)
        if api_id == "usa20540":
            return httpx.Response(200, json={"return_code": 0, "result_list": [
                {"stk_cd": symbol, "stex_tp": "2", "stk_enm": f"{symbol} Inc",
                 "trde_prica": "1000000"} for symbol in UNIVERSE]})
        if api_id == "usa20550":
            return httpx.Response(200, json={"return_code": 0, "result_list": [
                {"stk_cd": symbol, "stex_tp": "2", "mac": "800000000000"} for symbol in UNIVERSE]})
        if api_id == "usa06012":
            return httpx.Response(200, json={"return_code": 0,
                                             "result_list": daily_rows(body["stk_cd"])})
        if api_id == "usa06011":
            rows = minute_rows() if body["stk_cd"] == SYMBOL else []
            return httpx.Response(200, json={"return_code": 0, "result_list": rows})
        raise AssertionError(f"unexpected Kiwoom endpoint: {api_id}")

    return httpx.MockTransport(handler)


def research_payload(run_id: int, symbols: list[str]) -> str:
    return json.dumps({
        "schema_version": "gpt_research_v0", "prompt_version": "top8_research_v0",
        "provider": "OpenAI", "model": "smoke-fixture",
        "analysis_at": "2026-07-01T23:00:00+00:00", "trading_date": SCAN_DAY.isoformat(),
        "scanner_run_id": run_id,
        "candidates": [{
            "ticker": symbol, "gpt_rank": index + 1,
            "overall_score": 90 - index, "catalyst_score": 80, "fundamental_score": 70,
            "momentum_score": 75, "risk_score": 60, "catalyst_duration": "ONE_TO_TWO_DAYS",
            "stop_profile": "NORMAL", "trailing_profile": "WIDE",
            "overnight_suitability": "MEDIUM", "company_summary": f"{symbol} company",
            "catalyst_summary": "catalyst", "risk_summary": "risk",
            "invalidation_summary": "invalidation", "unknown_fields": [],
            "sources": [{"claim": "catalyst", "url": f"https://sec.gov/{symbol}", "type": "SEC",
                         "title": "filing", "published_at": None}],
        } for index, symbol in enumerate(symbols)],
    })


# Environment ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def ownership():
    assert get_active_sim_broker() is None
    yield
    clear_active_sim_broker()


@pytest.fixture
def operator(tmp_path, monkeypatch):
    """A migrated 0009 database, the operator profile, and no real DB binding."""
    engine = _migrated_engine(tmp_path / "smoke.sqlite3", monkeypatch, REVISION)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setenv("RUNTIME_PROFILE", "real_market_operator")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kiwoom")
    monkeypatch.setenv("KIWOOM_APP_KEY", "smoke-key")
    monkeypatch.setenv("KIWOOM_APP_SECRET", "smoke-secret")
    monkeypatch.setenv("RUN_KIWOOM_REAL_SCANNER", "1")
    monkeypatch.setenv("RUN_REAL_MARKET_SIMULATION", "1")
    get_settings.cache_clear()
    try:
        yield factory
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
def provider(operator):
    """The real factory-built Kiwoom provider over a fixture transport."""
    http = httpx.Client(transport=kiwoom_transport())
    built = build_kiwoom_provider(get_settings(), http=http)
    try:
        yield built
    finally:
        http.close()


def seed_account(factory, cash: Decimal = CASH) -> int:
    with factory() as session:
        account = SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CASH, cash=cash, created_at=SCAN_AS_OF)
        session.commit()
        return account.id


def counts(session: Session) -> dict[str, int]:
    return {name: session.scalar(select(func.count()).select_from(model)) for name, model in (
        ("accounts", SimulationAccountRecord), ("orders", ExecutionOrderRecord),
        ("fills", ExecutionFillRecord), ("positions", SimulationPositionRecord),
        ("trades", SimulationTradeRecord), ("analyses", GPTAnalysis),
        ("decisions", HumanDecisionRecord))}


def app_with(factory):
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# Upstream: scanner, research, human decision -------------------------------

def run_scanner(provider, factory) -> tuple[int, list[str]]:
    """The evening command's own path: acquire, scan, persist, prompt."""
    provider._clock = lambda: SCAN_AS_OF
    source = KiwoomUniverseSource(provider)
    universe = source.acquire(len(UNIVERSE))
    source.prime_provider(universe, SCAN_AS_OF)
    scanner = QuantScanner(provider, provider)
    result = scanner.scan([item.symbol for item in universe], trading_date=SCAN_DAY,
                          scan_as_of=SCAN_AS_OF)
    with factory() as session:
        repository = ScannerSnapshotRepository(session)
        run_id, _ = ScannerService(scanner, repository, provider_name="KIWOOM_REAL",
                                   clock=lambda: SCAN_AS_OF).persist_result(result)
        run = session.get(ScannerRun, run_id)
        prompt = ResearchPromptService(repository).generate_top_for_run(run)
        assert SYMBOL in prompt and f"scanner_run_id: {run_id}" in prompt
        top = [candidate.symbol for candidate in repository.get_top8(run_id)]
    return run_id, top


def import_research(factory, run_id: int, symbols: list[str]) -> int:
    with factory() as session:
        analysis = GPTImportService(ResearchRepository(session), ScannerSnapshotRepository(session),
                                    clock=lambda: SCAN_AS_OF).import_json(
            research_payload(run_id, symbols))
        return analysis.id


def approve(factory, analysis_id: int, symbol: str = SYMBOL) -> None:
    with factory() as session:
        HumanDecisionService(ResearchRepository(session)).decide(
            analysis_id, symbol, HumanDecision.APPROVE, decided_at=SCAN_AS_OF)
        session.commit()


# Trading morning -----------------------------------------------------------

def premarket_context(provider):
    """Compose the gate inputs from mapped Kiwoom data, not from constants."""
    from app.services.market_context import MarketContextService

    provider._clock = lambda: SIGNAL_AT
    daily = provider.get_daily_bars([SYMBOL], end=SCAN_DAY)
    previous_close = Decimal(str(daily[-1].close))
    average_volume = Decimal(str(sum(bar.volume for bar in daily[-20:]))) / Decimal(20)
    context = MarketContextService(provider, clock=lambda: SIGNAL_AT).compose(
        SYMBOL, float(previous_close), trading_date=TRADING_DAY, as_of=SIGNAL_AT)
    bars = provider.get_minute_bars(
        [SYMBOL], start=datetime.combine(TRADING_DAY, time(4), ET), end=SIGNAL_AT)
    premarket_volume = Decimal(str(sum(bar.volume for bar in bars
                                       if bar.session is MarketSession.PREMARKET)))
    return PremarketContext(
        previous_regular_close=previous_close,
        reference_price=Decimal(str(context["premarket"]["price"])),
        premarket_volume=premarket_volume,
        historical_average_daily_volume=average_volume,
    ), context, bars


def signal(provider, factory, analysis_id: int):
    """Human gate to ENTER through the frozen engine; returns the evaluation."""
    engine = StrategyV0Engine()
    context, composed, bars = premarket_context(provider)
    with factory() as session:
        decision = ResearchRepository(session).get_decision(analysis_id, SYMBOL)
        research = session.scalars(select(GPTCandidateAnalysis).where(
            GPTCandidateAnalysis.gpt_analysis_id == analysis_id,
            GPTCandidateAnalysis.symbol == SYMBOL)).one()
        candidate_id = research.scanner_candidate_id
        profile = TrailingProfile(research.trailing_profile)
        suitability = OvernightSuitability(research.overnight_suitability)
    approved = decision is not None and decision.decision == HumanDecision.APPROVE.value
    state = StrategyState(SYMBOL, TRADING_DAY, scanner_candidate_id=candidate_id,
                          trailing_profile=profile, overnight_suitability=suitability)
    state = StrategyLifecycleService.apply_human_gate(state, approved=approved, shadow_mode=False)
    gate = engine.premarket_gate(context, human_approved=approved, shadow_mode=False)
    state = StrategyLifecycleService.apply_premarket_gate(state, gate)
    market_open = CALENDAR.regular_market_open(TRADING_DAY)
    evaluated = engine.evaluate_entry(state=state, bars=bars, market_open=market_open,
                                      as_of=SIGNAL_AT)
    return gate, evaluated, composed


def enter(runner: StrategyLifecycleRunner, evaluated, bars):
    return runner.execute_entry(
        state=evaluated.state, decision=evaluated.decision,
        account=AccountSnapshot(CASH, CASH, Currency.USD, SIGNAL_AT),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), SIGNAL_AT),
        market_bars=tuple(bars), instrument_currency=Currency.USD, created_at=SIGNAL_AT,
        actual_risk_state=DailyTradingState(TRADING_DAY))


def count_submissions(broker) -> list:
    seen: list = []
    original = broker.submit_order

    def counted(intent, market_bars):
        seen.append(intent)
        return original(intent, market_bars)

    broker.submit_order = counted
    return seen


# The smoke -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_to_end_real_market_simulation_smoke(operator, provider) -> None:
    factory = operator
    safety_gate(get_settings())
    account_id = seed_account(factory)

    # 1. Evening: Kiwoom market data to a persisted scanner run and research prompt.
    run_id, top8 = run_scanner(provider, factory)
    assert len(top8) == 8 and SYMBOL in top8

    # 2. Research import and the single human approval; no GPT call is made.
    analysis_id = import_research(factory, run_id, top8)
    approve(factory, analysis_id)
    with factory() as session:
        readiness = inspect_readiness(session)
        assert readiness.ready and readiness.scanner_run_id == run_id
        assert readiness.analysis_id == analysis_id and readiness.research_candidates == 8
        assert (readiness.approved, readiness.rejected, readiness.undecided) == (1, 0, 7)
        decisions = list(session.scalars(select(HumanDecisionRecord)))
        assert len(decisions) == 1 and decisions[0].symbol == SYMBOL
        assert decisions[0].decision == "APPROVE"

    # 3. Trading morning: the app's own startup owns the rehydrated broker.
    app = app_with(factory)
    async with app.router.lifespan_context(app):
        runtime = get_active_runtime()
        assert runtime is not None and runtime.durable and runtime.account_id == account_id
        assert runtime.broker.cash == CASH and runtime.broker.get_positions() == ()

        # 4. Strategy: premarket gate then the frozen entry rule.
        gate, evaluated, composed = signal(provider, factory, analysis_id)
        assert gate.passed and gate.gap_pct == Decimal("0.05")
        assert gate.volume_ratio == Decimal("0.10")
        assert composed["premarket"]["price"] == float(PREMARKET_PRICE)
        assert evaluated.decision.decision is DecisionType.ENTER
        assert evaluated.decision.reason_code == StrategyReason.ABOVE_VWAP_AND_OR_BREAK
        assert evaluated.state.phase is StrategyPhase.ENTRY_SIGNALLED
        assert (evaluated.opening_range_high, evaluated.opening_range_low) == (
            OPENING_RANGE_HIGH, INITIAL_STOP)
        assert evaluated.state.entry_price == ENTRY_PRICE
        assert evaluated.state.initial_stop == INITIAL_STOP
        assert evaluated.vwap is not None and evaluated.vwap < ENTRY_PRICE
        with factory() as session:
            StrategyStateService(session).save(evaluated.state, updated_at=SIGNAL_AT)

        # 5. Execution: the next completed bar arrives and the runner submits once.
        provider._clock = lambda: FILL_POLL_AT
        fill_bars = provider.get_minute_bars(
            [SYMBOL], start=datetime.combine(TRADING_DAY, time(4), ET), end=FILL_POLL_AT)
        assert max(bar.timestamp for bar in fill_bars) == FILL_BAR_AT
        runner = StrategyLifecycleRunner.for_active_runtime()
        assert runner.broker is get_active_sim_broker()
        submissions = count_submissions(runner.broker)
        result = enter(runner, evaluated, fill_bars)
        assert len(submissions) == 1

        assert result.risk is not None and result.risk.approved
        assert result.risk.metrics.one_r == PLANNED_RISK
        assert result.risk.metrics.final_quantity == QUANTITY
        # 500/3 does not terminate, so planned risk keeps the quotient's tail
        # rather than being re-rounded to the 1R it was derived from.
        assert result.risk.metrics.planned_risk == QUANTITY * Decimal("3")
        assert result.risk.metrics.planned_risk.quantize(Decimal("0.01")) == PLANNED_RISK
        assert result.order is not None and result.order.status is OrderStatus.FILLED
        assert result.order.id.startswith("SIM-") and result.order.rejection_reason is None
        assert result.order.filled_quantity == QUANTITY == result.order.requested_quantity
        assert result.order.filled_at == FILL_BAR_AT
        assert result.state.phase is StrategyPhase.POSITION_OPEN
        fill = runner.broker.get_fills(result.order.id)[0]
        assert (fill.fill_price, fill.raw_market_price) == (FILL_PRICE, ENTRY_PRICE)
        assert (fill.spread_cost, fill.slippage_cost) == (SPREAD_COST, SLIPPAGE_COST)
        assert (fill.commission, fill.fx_cost, fill.total_cost) == (COMMISSION, FX_COST, TOTAL_COST)
        assert runner.broker.cash == FILLED_CASH
        with factory() as session:
            StrategyStateService(session).save(result.state, updated_at=FILL_POLL_AT)

        # 6. Durable state mirrors the broker exactly.
        with factory() as session:
            assert counts(session) == {"accounts": 1, "orders": 1, "fills": 1, "positions": 1,
                                       "trades": 1, "analyses": 1, "decisions": 1}
            repository = SimulationStateRepository(session)
            stored = repository.get_account_by_id(account_id)
            assert stored.cash == FILLED_CASH and stored.state_version == 1
            position = repository.get_position(account_id, SYMBOL)
            assert position == runner.broker.get_position(SYMBOL)
            assert position.quantity == QUANTITY and position.average_price == FILL_PRICE
            assert position.realized_pnl == Decimal("0")
            trade = repository.get_open_trade(account_id, SYMBOL)
            assert trade == runner.broker.get_trade(SYMBOL) and trade.status is TradeStatus.OPEN
            order = session.scalars(select(ExecutionOrderRecord)).one()
            assert order.broker_type == "SIM" and order.status == "FILLED"
            assert order.rejection_reason is None and order.filled_quantity == QUANTITY

        # 7. The API projects the live broker and the durable history.
        async with client_for(app) as client:
            assert (await client.get("/health")).status_code == 200
            body = (await client.get("/api/v1/trading")).json()
            assert body["availability"] == "AVAILABLE" and body["broker_mode"] == "SIMULATION"
            assert Decimal(body["account"]["cash"]) == FILLED_CASH
            projected = body["open_positions"][0]
            assert projected["symbol"] == SYMBOL
            assert Decimal(projected["quantity"]) == QUANTITY
            assert Decimal(projected["average_price"]) == FILL_PRICE
            assert body["open_orders"] == []
            assert {row["symbol"] for row in body["strategy_states"]} == {SYMBOL}
            detail = await client.get(f"/api/v1/trading/positions/{SYMBOL.lower()}")
            assert detail.status_code == 200 and detail.json() == projected
            orders = (await client.get("/api/v1/trading/orders")).json()
            assert len(orders) == 1 and orders[0]["status"] == "FILLED"
            assert Decimal(orders[0]["filled_quantity"]) == QUANTITY
            fills = (await client.get("/api/v1/trading/fills")).json()
            assert len(fills) == 1 and fills[0]["order_id"] == orders[0]["order_id"]
            assert Decimal(fills[0]["fill_price"]) == FILL_PRICE
            assert Decimal(fills[0]["total_cost"]) == TOTAL_COST
        first_scope = runtime.broker.execution_scope
    assert get_active_sim_broker() is None

    # 8. Restart: a fresh broker rebuilt from the same database, replaying nothing.
    restarted = app_with(factory)
    async with restarted.router.lifespan_context(restarted):
        runtime = get_active_runtime()
        assert runtime is not None and runtime.broker.execution_scope != first_scope
        broker = runtime.broker
        assert broker._sequence == 0
        assert broker.cash == FILLED_CASH
        assert broker.get_position(SYMBOL).quantity == QUANTITY
        assert broker.get_position(SYMBOL).average_price == FILL_PRICE
        assert broker.get_trade(SYMBOL).status is TradeStatus.OPEN
        assert broker.get_open_orders() == () and broker.get_fills() == ()
        with factory() as session:
            assert counts(session)["orders"] == 1 and counts(session)["fills"] == 1
            assert SimulationStateRepository(session).get_account_by_id(
                account_id).state_version == 1
        async with client_for(restarted) as client:
            body = (await client.get("/api/v1/trading")).json()
            assert body["availability"] == "AVAILABLE"
            assert Decimal(body["account"]["cash"]) == FILLED_CASH
            assert Decimal(body["open_positions"][0]["quantity"]) == QUANTITY
            assert len((await client.get("/api/v1/trading/orders")).json()) == 1
            assert len((await client.get("/api/v1/trading/fills")).json()) == 1

    # 9. Kiwoom stayed a market-data provider for the whole pass.
    assert provider.client.order_request_count == 0
    assert set(provider.client.request_counts) == {"/api/us/rkinfo", "/api/us/chart"}
    assert provider.provider_failures == {} and provider.mapping_issues == {}


@pytest.mark.asyncio
async def test_no_next_bar_persists_a_rejection_and_moves_nothing(operator, provider) -> None:
    """The durable rejection contract still holds on the real market-data path."""
    factory = operator
    account_id = seed_account(factory)
    run_id, top8 = run_scanner(provider, factory)
    analysis_id = import_research(factory, run_id, top8)
    approve(factory, analysis_id)
    app = app_with(factory)
    async with app.router.lifespan_context(app):
        _, evaluated, _ = signal(provider, factory, analysis_id)
        assert evaluated.decision.decision is DecisionType.ENTER
        runner = StrategyLifecycleRunner.for_active_runtime()
        submissions = count_submissions(runner.broker)
        # The signal minute is the last bar anyone has: there is nothing to fill on.
        provider._clock = lambda: SIGNAL_AT
        bars = provider.get_minute_bars(
            [SYMBOL], start=datetime.combine(TRADING_DAY, time(4), ET), end=SIGNAL_AT)
        result = enter(runner, evaluated, bars)
        assert len(submissions) == 1
        assert result.order is not None and result.order.status is OrderStatus.REJECTED
        assert result.order.rejection_reason is RejectionReason.NO_NEXT_BAR
        assert runner.broker.cash == CASH and runner.broker.get_positions() == ()
    with factory() as session:
        assert counts(session)["orders"] == 1
        assert counts(session)["fills"] == 0
        assert counts(session)["positions"] == 0 and counts(session)["trades"] == 0
        account = SimulationStateRepository(session).get_account_by_id(account_id)
        assert account.cash == CASH and account.state_version == 0
        order = session.scalars(select(ExecutionOrderRecord)).one()
        assert order.status == "REJECTED" and order.rejection_reason == "NO_NEXT_BAR"
    assert provider.client.order_request_count == 0


def test_kiwoom_ordering_is_blocked_before_it_reaches_the_network(operator, provider) -> None:
    """MARKET_DATA_ONLY is enforced by the client's allowlist, not by convention."""
    settings = get_settings()
    assert (settings.broker_provider, settings.kiwoom_mode) == ("simulation", "market_data_only")
    assert not hasattr(provider.client, "submit_order")
    with pytest.raises(MarketDataError) as error:
        provider.client.request("usa60001", "/api/us/ordr", {"stk_cd": SYMBOL})
    assert error.value.code == "ENDPOINT_BLOCKED"
    assert provider.client.order_request_count == 0
    assert provider.client.request_counts == {}


def test_startup_without_an_operator_account_stays_inactive(operator, provider) -> None:
    """No account means no trading and no seeding, even with research approved."""
    factory = operator
    run_id, top8 = run_scanner(provider, factory)
    approve(factory, import_research(factory, run_id, top8))
    assert activate_operator_simulation_runtime(get_settings(), config=ExecutionConfig()) is None
    assert get_active_sim_broker() is None
    with factory() as session:
        assert counts(session)["accounts"] == 0
