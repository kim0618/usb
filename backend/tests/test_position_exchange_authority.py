"""Every runtime that reads a held symbol's market data reads it on that trade's exchange.

Entry already bound an approved candidate's exchange, but the position it opens is
managed by two other owners, each building its own provider from the shared factory:
the minute driver and the end-of-day owner. Neither knew the venue, so an NYSE
position was read as a NASDAQ listing, Kiwoom answered INVALID_SYMBOL, and its stop
was never tested. The venue now comes from the one durable place it is recorded -
the scanner candidate the trade's open strategy state points at - so a restart finds
it exactly as a live process does.

The provider is the real Kiwoom adapter over a client that routes the way Kiwoom
does: a symbol asked for on the wrong venue does not exist.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.kiwoom import KiwoomMarketDataProvider
from app.models.execution import ExecutionOrderRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.repositories.simulation import SimulationStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.risk.engine import RiskEngine
from app.services.end_of_day_lifecycle import EndOfDayAction
from app.services.end_of_day_runtime import EndOfDayPositionRuntime
from app.services.entry_management_runtime import (
    EntryAction, EntryLifecycleService, EntryManagementRuntime,
)
from app.services.position_lifecycle import PositionAction, strategy_state_sink
from app.services.position_management_runtime import PositionManagementRuntime
from app.services.simulation import rehydrate_sim_broker
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.lifecycle import StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner
from tests.test_entry_management_runtime import seed_session

ET = ZoneInfo("America/New_York")
DAY = date(2024, 6, 18)
ANALYSIS_DAY = date(2024, 6, 17)  # XNYS predecessor: the run the entry session consumes
OPEN = datetime(2024, 6, 18, 9, 30, tzinfo=ET)
SIGNAL_AT = OPEN + timedelta(minutes=16)
AS_OF = datetime(2024, 6, 18, 10, 0, tzinfo=ET)
REVIEW_AS_OF = datetime(2024, 6, 18, 15, 50, tzinfo=ET)
AFTER_CLOSE = datetime(2024, 6, 18, 16, 1, tzinfo=ET)
CASH = Decimal("100000")
CODES = {"NASDAQ": "ND", "NYSE": "NY", "AMEX": "NA"}


# Kiwoom, as it routes -------------------------------------------------------

def _price(value: float) -> str:
    return f"{value:.4f}"


def _minute_row(bar: MinuteBar) -> dict[str, str]:
    local = bar.timestamp.astimezone(ET)
    return {"bus_dt": local.strftime("%Y%m%d"), "cntr_tm": local.strftime("%Y%m%d%H%M%S"),
            "open_pric": _price(bar.open), "high_pric": _price(bar.high),
            "low_pric": _price(bar.low), "cur_prc": _price(bar.close),
            "trde_qty": str(bar.volume)}


def _daily_row(bar: DailyBar) -> dict[str, str]:
    return {"dt": bar.trading_date.strftime("%Y%m%d"), "open_pric": _price(bar.open),
            "high_pric": _price(bar.high), "low_pric": _price(bar.low),
            "cur_prc": _price(bar.close), "acc_trde_qty": str(bar.volume)}


class RoutingClient:
    """A Kiwoom client whose venue routing is real: the wrong venue is an unknown symbol."""

    order_request_count = 0

    def __init__(self, listings: dict[str, str], minutes: dict[str, list[MinuteBar]],
                 daily: dict[str, list[DailyBar]] | None = None) -> None:
        self.listings = listings
        self.minutes = minutes
        self.daily = daily or {}
        self.queries: list[tuple[str, str, str]] = []  # (kind, symbol, exchange)

    def exchanges(self, symbol: str) -> list[str]:
        return [exchange for _, queried, exchange in self.queries if queried == symbol]

    def _route(self, kind: str, symbol: str, exchange: str) -> None:
        self.queries.append((kind, symbol, exchange))
        if self.listings.get(symbol) != exchange:
            raise MarketDataError("INVALID_SYMBOL", "Kiwoom market-data query was rejected")

    def minute_chart(self, symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        self._route("minute", symbol, exchange)
        return SimpleNamespace(rows=tuple(_minute_row(bar) for bar in self.minutes.get(symbol, ())))

    def daily_chart(self, symbol, exchange, start=None):  # type: ignore[no-untyped-def]
        self._route("daily", symbol, exchange)
        return [_daily_row(bar) for bar in self.daily.get(symbol, ())]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def kiwoom_factory(client: RoutingClient, clock: Clock, built: list | None = None):  # type: ignore[no-untyped-def]
    """The shared factory main.py hands every owner: each call is a new provider."""
    def build() -> KiwoomMarketDataProvider:
        provider = KiwoomMarketDataProvider(client, clock=clock)  # type: ignore[arg-type]
        if built is not None:
            built.append(provider)
        return provider
    return build


# Tape ----------------------------------------------------------------------

def minute(symbol: str, at: datetime, close: float, *, low: float | None = None,
           session: MarketSession = MarketSession.REGULAR, volume: int = 20_000,
           open_: float | None = None) -> MinuteBar:
    return MinuteBar(symbol=symbol, timestamp=at, open=close if open_ is None else open_,
                     high=close + 0.2,
                     low=close - 0.2 if low is None else low, close=close, volume=volume,
                     session=session, observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


def entry_tape(symbol: str, *after: MinuteBar) -> list[MinuteBar]:
    """The entry-runtime fixture: exact prior close, a 5% gap, OR at 100, breakout at 09:45."""
    bars = [minute(symbol, datetime(2024, 6, 17, 15, 59, tzinfo=ET), 100),
            minute(symbol, datetime(2024, 6, 18, 8, 0, tzinfo=ET), 105,
                   session=MarketSession.PREMARKET, volume=100_000)]
    bars += [minute(symbol, OPEN + timedelta(minutes=i), 100) for i in range(15)]
    # 09:47 is the 09:46 signal's intended execution bar; it opens at the signal
    # close, inside the Risk-approved price ceiling.
    bars += [minute(symbol, OPEN + timedelta(minutes=15), 102),
             minute(symbol, OPEN + timedelta(minutes=16), 102.1),
             minute(symbol, OPEN + timedelta(minutes=17), 102.2, open_=102)]
    return bars + list(after)


def steady(symbol: str, price: float, start: datetime, until: time = time(15, 59)) -> list[MinuteBar]:
    bars, at = [], start
    while at.astimezone(ET).time() <= until:
        bars.append(minute(symbol, at, price))
        at += timedelta(minutes=1)
    return bars


def daily(symbol: str) -> list[DailyBar]:
    at = datetime(2024, 6, 17, 16, tzinfo=ET)
    return [DailyBar(symbol=symbol, trading_date=ANALYSIS_DAY, open=100, high=101, low=99,
                     close=100, volume=1_000_000, observed_at=at, available_at=at)]


def session_tape(symbol: str, price: float = 102.0) -> list[MinuteBar]:
    return steady(symbol, price, OPEN)


# Ledger --------------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'authority.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        account_id = SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CASH, cash=CASH, created_at=datetime(2024, 6, 18, 8, tzinfo=ET)).id
        session.commit()
    yield SimulationRuntimeContext(SimBroker(CASH), account_id, factory), factory
    engine.dispose()


def restart(runtime: SimulationRuntimeContext, factory) -> SimulationRuntimeContext:  # type: ignore[no-untyped-def]
    """A new process: a broker rebuilt from the database and nothing else carried over."""
    with factory() as session:
        broker = rehydrate_sim_broker(session, runtime.account_id)
    return SimulationRuntimeContext(broker, runtime.account_id, factory)


def candidate_row(factory, symbol: str, exchange: str, run_day: date = ANALYSIS_DAY) -> int:  # type: ignore[no-untyped-def]
    at = datetime.combine(run_day, time(16, 30), ET)
    with factory() as session:
        run = ScannerRun(trading_date=run_day, started_at=at, status="COMPLETED",
                         completed_at=at, provider="KIWOOM_REAL", score_version="v0")
        session.add(run)
        session.flush()
        row = ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=1, is_top8=True,
                               score=1.0, score_components_json={"exchange": exchange},
                               observed_at=at, available_at=at)
        session.add(row)
        session.commit()
        return row.id


def open_position(runtime: SimulationRuntimeContext, symbol: str,
                  candidate_id: int | None) -> None:
    """Open a position the way an earlier process would have left it: no binding involved.

    The entry fill goes straight through the runner, so the only place this trade's
    venue exists afterwards is the scanner candidate its strategy state records.
    """
    state = StrategyState(symbol, DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
                          scanner_candidate_id=candidate_id, entry_price=Decimal("102"),
                          initial_stop=Decimal("99"), active_stop=Decimal("99"),
                          entry_trading_date=DAY, holding_day_number=1)
    decision = StrategyDecision(symbol, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK",
                                SIGNAL_AT, strategy_version="strategy_v0")
    runner = StrategyLifecycleRunner(runtime.broker, runtime=runtime, risk_engine=RiskEngine(),
                                     calendar=MarketCalendar())
    cash = runtime.broker.cash
    result = runner.execute_entry(
        state=state, decision=decision, account=AccountSnapshot(cash, cash, Currency.USD, SIGNAL_AT),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), SIGNAL_AT),
        market_bars=tuple(session_tape(symbol)), instrument_currency=Currency.USD,
        created_at=SIGNAL_AT, actual_risk_state=DailyTradingState(DAY),
        on_state=strategy_state_sink(SIGNAL_AT))
    assert result.order is not None and runtime.broker.get_fills(result.order.id)


def filled_sells(factory) -> list[ExecutionOrderRecord]:  # type: ignore[no-untyped-def]
    with factory() as session:
        return list(session.scalars(select(ExecutionOrderRecord).where(
            ExecutionOrderRecord.side == "SELL", ExecutionOrderRecord.status == "FILLED")))


# O-1, O-2, Q. ORCL end to end, one process, separate providers --------------

@pytest.mark.asyncio
async def test_orcl_is_entered_on_nyse_and_its_stop_is_enforced_on_nyse(ledger) -> None:
    runtime, factory = ledger
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("ORCL",), {"ORCL": "NYSE"})
    breach = [minute("ORCL", OPEN + timedelta(minutes=18), 99.0, low=98.0),
              minute("ORCL", OPEN + timedelta(minutes=19), 98.5),
              minute("ORCL", OPEN + timedelta(minutes=20), 98.4),
              minute("ORCL", OPEN + timedelta(minutes=21), 98.3)]
    client = RoutingClient({"ORCL": "NY"}, {"ORCL": entry_tape("ORCL", *breach)},
                           {"ORCL": daily("ORCL")})
    clock, built = Clock(OPEN), []
    shared_factory = kiwoom_factory(client, clock, built)
    entry = EntryManagementRuntime(runtime, shared_factory, clock=clock)
    positions = PositionManagementRuntime(runtime, shared_factory, clock=clock)

    entered = []
    for at in (OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)):
        clock.now = at
        entered = await entry.run_once(as_of=at)
    assert [(item.symbol, item.action) for item in entered] == [("ORCL", EntryAction.FILLED)]

    actions = []
    for minute_after in (19, 20, 21):
        clock.now = OPEN + timedelta(minutes=minute_after)
        actions += [item.action for item in await positions.run_once(as_of=clock.now)]
    assert actions == [PositionAction.EXIT_UNFILLED, PositionAction.EXIT_UNFILLED,
                       PositionAction.EXIT_FILLED]

    # Two owners, two providers - exactly as main.py wires them - one venue.
    assert len(built) == 2 and built[0] is not built[1]
    assert set(client.exchanges("ORCL")) == {"NY"}
    assert runtime.broker.get_position("ORCL") is None
    assert len(filled_sells(factory)) == 1
    clock.now = OPEN + timedelta(minutes=25)
    assert await positions.run_once(as_of=clock.now) == ()
    assert len(filled_sells(factory)) == 1


# O-2, O-6, O-7, V. Position management on each supported venue --------------

@pytest.mark.asyncio
@pytest.mark.parametrize("exchange", ["NYSE", "AMEX", "NASDAQ"])
async def test_position_management_reads_each_held_symbol_on_its_trades_exchange(
        ledger, exchange: str) -> None:
    runtime, factory = ledger
    open_position(runtime, "HELD", candidate_row(factory, "HELD", exchange))
    client = RoutingClient({"HELD": CODES[exchange]}, {"HELD": session_tape("HELD")})
    clock = Clock(AS_OF)

    outcomes = await PositionManagementRuntime(
        runtime, kiwoom_factory(client, clock), clock=clock).run_once(as_of=AS_OF)

    assert [(item.symbol, item.action) for item in outcomes] == [("HELD", PositionAction.HOLD)]
    # Binding is a database read and an assignment: the only request is the bars.
    assert client.queries == [("minute", "HELD", CODES[exchange])]


# O-3. End-of-day review and closing mark ------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("exchange", ["NYSE", "AMEX"])
async def test_end_of_day_review_and_closing_mark_read_the_trades_exchange(
        ledger, exchange: str) -> None:
    runtime, factory = ledger
    open_position(runtime, "HELD", candidate_row(factory, "HELD", exchange))
    client = RoutingClient({"HELD": CODES[exchange]}, {"HELD": session_tape("HELD")})
    clock = Clock(REVIEW_AS_OF)
    owner = EndOfDayPositionRuntime(runtime, kiwoom_factory(client, clock), clock=clock)

    reviewed = await owner.run_once(as_of=REVIEW_AS_OF)
    clock.now = AFTER_CLOSE
    await owner.run_once(as_of=AFTER_CLOSE)

    assert [item.symbol for item in reviewed] == ["HELD"]
    assert all(item.action is not EndOfDayAction.PROTECTION_UNAVAILABLE for item in reviewed)
    assert client.exchanges("HELD") == [CODES[exchange], CODES[exchange]]


@pytest.mark.asyncio
async def test_a_closing_mark_taken_first_after_a_restart_reads_the_trades_exchange(ledger) -> None:
    """A backend started after the close marks the book before any review has bound it."""
    runtime, factory = ledger
    open_position(runtime, "HELD", candidate_row(factory, "HELD", "NYSE"))
    client = RoutingClient({"HELD": "NY"}, {"HELD": session_tape("HELD")})
    clock = Clock(AFTER_CLOSE)

    await EndOfDayPositionRuntime(restart(runtime, factory), kiwoom_factory(client, clock),
                                  clock=clock).run_once(as_of=AFTER_CLOSE)

    assert client.queries == [("minute", "HELD", "NY")]


# O-4. A restart rebinds position management from the database ---------------

@pytest.mark.asyncio
async def test_a_restart_rebinds_position_management_from_the_database(ledger) -> None:
    runtime, factory = ledger
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("ORCL",), {"ORCL": "NYSE"})
    client = RoutingClient({"ORCL": "NY"},
                           {"ORCL": entry_tape("ORCL", *steady("ORCL", 102.3, OPEN + timedelta(minutes=18)))},
                           {"ORCL": daily("ORCL")})
    clock = Clock(OPEN)
    entry = EntryManagementRuntime(runtime, kiwoom_factory(client, clock), clock=clock)
    for at in (OPEN + timedelta(minutes=16), OPEN + timedelta(minutes=18)):
        clock.now = at
        entered = await entry.run_once(as_of=at)
    assert entered[0].action is EntryAction.FILLED

    restarted = restart(runtime, factory)
    client.queries.clear()
    clock.now = AS_OF
    outcomes = await PositionManagementRuntime(
        restarted, kiwoom_factory(client, clock), clock=clock).run_once(as_of=AS_OF)

    assert restarted.broker.get_position("ORCL") is not None
    assert [(item.symbol, item.action) for item in outcomes] == [("ORCL", PositionAction.HOLD)]
    assert client.exchanges("ORCL") == ["NY"]


# O-5, G. A restart rebinds the entry book's marks from the database -----------

def test_a_restart_marks_a_held_nyse_position_on_nyse_while_entering_a_nasdaq_one(ledger) -> None:
    runtime, factory = ledger
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("ORCL", "AAPL"), {"ORCL": "NYSE", "AAPL": "NASDAQ"})
    after = steady("ORCL", 102.3, OPEN + timedelta(minutes=18))
    client = RoutingClient({"ORCL": "NY", "AAPL": "ND"},
                           {"ORCL": entry_tape("ORCL", *after),
                            "AAPL": entry_tape("AAPL", *steady("AAPL", 102.3, OPEN + timedelta(minutes=18)))},
                           {"ORCL": daily("ORCL"), "AAPL": daily("AAPL")})
    clock = Clock(OPEN)
    first = kiwoom_factory(client, clock)()
    service = EntryLifecycleService(runtime)
    candidates = {item.symbol: item for item in service.approved_candidates_for_entry_session(DAY)}
    clock.now = OPEN + timedelta(minutes=16)
    service.evaluate(candidates["ORCL"], first, as_of=clock.now)
    service.evaluate(candidates["AAPL"], first, as_of=clock.now)
    clock.now = OPEN + timedelta(minutes=18)
    assert service.evaluate(candidates["ORCL"], first, as_of=clock.now).action is EntryAction.FILLED

    restarted = restart(runtime, factory)
    client.queries.clear()
    fresh = kiwoom_factory(client, clock)()
    outcome = EntryLifecycleService(restarted).evaluate(candidates["AAPL"], fresh, as_of=clock.now)

    assert outcome.action is EntryAction.FILLED
    assert client.exchanges("ORCL") == ["NY"]  # the held position's mark
    assert set(client.exchanges("AAPL")) == {"ND"}
    assert {item.symbol for item in restarted.broker.get_positions()} == {"ORCL", "AAPL"}


# O-8. The trade's own candidate, never the symbol's latest row -----------------

@pytest.mark.asyncio
@pytest.mark.parametrize("position_run_is_older", [True, False])
async def test_the_held_trades_candidate_is_the_authority_not_the_symbols_latest_row(
        ledger, position_run_is_older: bool) -> None:
    runtime, factory = ledger
    older, newer = date(2024, 6, 14), ANALYSIS_DAY
    own_day, other_day = (older, newer) if position_run_is_older else (newer, older)
    own = candidate_row(factory, "ORCL", "NYSE", own_day)
    candidate_row(factory, "ORCL", "NASDAQ", other_day)  # the same ticker, another run
    open_position(runtime, "ORCL", own)
    # Kiwoom lists it on both venues here, so only the routing decision can differ.
    client = RoutingClient({"ORCL": "NY"}, {"ORCL": session_tape("ORCL")})
    clock = Clock(AS_OF)

    outcomes = await PositionManagementRuntime(
        runtime, kiwoom_factory(client, clock), clock=clock).run_once(as_of=AS_OF)

    assert client.exchanges("ORCL") == ["NY"]
    assert [item.action for item in outcomes] == [PositionAction.HOLD]


# O-9. Missing or unknown authority is reported, never guessed ------------------

AUTHORITY_CASES = {
    "no candidate link": (lambda factory: None, "EXCHANGE_AUTHORITY_MISSING"),
    "candidate row absent": (lambda factory: 999_999, "EXCHANGE_AUTHORITY_MISSING"),
    "another symbol's row": (lambda factory: candidate_row(factory, "OTHER", "NYSE"),
                             "EXCHANGE_AUTHORITY_MISSING"),
    "empty exchange": (lambda factory: candidate_row(factory, "HELD", ""), "UNSUPPORTED_EXCHANGE"),
    "unknown exchange": (lambda factory: candidate_row(factory, "HELD", "UNKNOWN"),
                         "UNSUPPORTED_EXCHANGE"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(AUTHORITY_CASES))
async def test_a_held_symbol_without_exchange_authority_is_reported_and_never_guessed(
        ledger, case: str) -> None:
    runtime, factory = ledger
    link, code = AUTHORITY_CASES[case]
    open_position(runtime, "HELD", link(factory))
    # HELD is a NASDAQ listing here, so a silent ND default would succeed and hide it.
    client = RoutingClient({"HELD": "ND"}, {"HELD": session_tape("HELD")})
    clock = Clock(AS_OF)
    submitted_before = len(runtime.broker.get_positions())

    outcomes = await PositionManagementRuntime(
        runtime, kiwoom_factory(client, clock), clock=clock).run_once(as_of=AS_OF)

    assert [(item.symbol, item.action) for item in outcomes] == [
        ("HELD", PositionAction.PROTECTION_UNAVAILABLE)]
    assert outcomes[0].reason.startswith(code)
    assert client.queries == []
    assert len(runtime.broker.get_positions()) == submitted_before


@pytest.mark.asyncio
async def test_end_of_day_reports_a_held_symbol_without_exchange_authority(ledger) -> None:
    runtime, _ = ledger
    open_position(runtime, "HELD", None)
    client = RoutingClient({"HELD": "ND"}, {"HELD": session_tape("HELD")})
    clock = Clock(REVIEW_AS_OF)

    outcomes = await EndOfDayPositionRuntime(
        runtime, kiwoom_factory(client, clock), clock=clock).run_once(as_of=REVIEW_AS_OF)

    assert [(item.symbol, item.action) for item in outcomes] == [
        ("HELD", EndOfDayAction.PROTECTION_UNAVAILABLE)]
    assert client.queries == []


def test_entry_refuses_to_value_a_book_it_cannot_route(ledger) -> None:
    """A new entry is not sized against a held position read on a guessed venue."""
    runtime, factory = ledger
    open_position(runtime, "HELD", None)
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("AAPL",), {"AAPL": "NASDAQ"})
    client = RoutingClient({"HELD": "ND", "AAPL": "ND"},
                           {"HELD": session_tape("HELD"), "AAPL": entry_tape("AAPL")},
                           {"AAPL": daily("AAPL")})
    clock = Clock(OPEN + timedelta(minutes=16))
    service = EntryLifecycleService(runtime)
    candidate = service.approved_candidates_for_entry_session(DAY)[0]

    # The signal sizes nothing, so the book is valued only when its bar settles it.
    signalled = service.evaluate(candidate, kiwoom_factory(client, clock)(), as_of=clock.now)
    assert signalled.action is EntryAction.HOLD
    settle = Clock(OPEN + timedelta(minutes=18))
    with pytest.raises(MarketDataError) as error:
        service.evaluate(candidate, kiwoom_factory(client, settle)(), as_of=settle.now)

    assert error.value.code == "EXCHANGE_AUTHORITY_MISSING"
    assert client.exchanges("HELD") == []
