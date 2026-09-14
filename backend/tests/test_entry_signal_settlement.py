"""ENTRY_SIGNALLED settlement and the actual-risk invariant.

A signal settles on exactly one intended execution bar, submitting nothing while it
waits; it never fills a past bar, and a filled entry never carries more stop risk
or cost than Risk approved. Regressions for the stale-retry risk-bypass audit.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.broker.domain import OrderStatus, RejectionReason
from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.execution.config import ExecutionConfig
from app.execution.costs import buy_effective_price, execution_price
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.risk import DailySymbolState
from app.models.strategy import StrategyStateRecord
from app.repositories.risk import DailyRiskRepository
from app.repositories.simulation import SimulationStateRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.risk.engine import RISK_UNIT_TOLERANCE, RiskEngine
from app.services.entry_management_runtime import (
    ApprovedCandidate, EntryAction, EntryLifecycleService, EntryManagementRuntime,
    EntryRiskInvariantError, intended_entry_bar_at,
)
from app.services.simulation import rehydrate_sim_broker
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.config import StrategyConfig
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import OvernightSuitability, StrategyPhase, StrategyState, TrailingProfile

ET = ZoneInfo("America/New_York")
DAY = date(2024, 6, 18)
OPEN = datetime(2024, 6, 18, 9, 30, tzinfo=ET)
CLOSE = datetime(2024, 6, 18, 16, 0, tzinfo=ET)
NEXT_OPEN = datetime(2024, 6, 20, 9, 30, tzinfo=ET)  # 2024-06-19 is an XNYS holiday
CAPITAL = Decimal("10000")
ONE_R = CAPITAL * Decimal("0.005")
SLACK = Decimal("1") + RISK_UNIT_TOLERANCE
TIGHT = {"or_high": 99.95, "or_low": 99.9}
SYMBOLS = ("AAA", "BBB", "CCC", "DDD")


def M(minutes: int) -> datetime:
    return OPEN + timedelta(minutes=minutes)


SIGNAL = M(16)    # decision: the 09:45 breakout bar has just completed
INTENDED = M(17)  # the one bar that entry may fill on
SETTLE = M(18)    # the intended bar has just completed


def utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def bar(symbol: str, at: datetime, open_: float, close: float | None = None, *,
        session: MarketSession = MarketSession.REGULAR, high: float | None = None,
        low: float | None = None) -> MinuteBar:
    close = open_ if close is None else close
    return MinuteBar(symbol=symbol, timestamp=at, open=open_, close=close,
                     high=max(open_, close) + .01 if high is None else high,
                     low=min(open_, close) - .01 if low is None else low,
                     volume=100_000 if session is MarketSession.PREMARKET else 20_000,
                     session=session, observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


def tape(symbol: str, *, or_high: float = 99.0, or_low: float = 95.0, signal: float = 100.0,
         signal_minute: int = 15, fill_open: float | None = None, intended: bool = True,
         later: bool = True) -> list[MinuteBar]:
    """Opening range [or_low, or_high]; flat below it until the ``signal_minute`` bar
    closes at ``signal``; the decision-minute bar is flat; the intended execution bar
    opens at ``fill_open`` (default: the signal price), and later bars trade there."""
    mid = round((or_high + or_low) / 2, 4)
    fill = signal if fill_open is None else fill_open
    bars = [bar(symbol, datetime(2024, 6, 18, 8, 0, tzinfo=ET), 105, session=MarketSession.PREMARKET),
            bar(symbol, datetime(2024, 6, 17, 15, 59, tzinfo=ET), 100)]
    bars += [bar(symbol, M(i), mid, high=or_high, low=or_low) for i in range(15)]
    bars += [bar(symbol, M(i), mid) for i in range(15, signal_minute)]
    bars.append(bar(symbol, M(signal_minute), mid, signal))
    minutes = [signal_minute + 1] + ([signal_minute + 2] if intended else [])
    minutes += list(range(signal_minute + 3, 390)) if later else []
    for minute in minutes:
        if minute < 390:
            bars.append(bar(symbol, M(minute), signal if minute == signal_minute + 1 else fill))
    return bars


class Provider:
    def __init__(self, *tapes: list[MinuteBar]) -> None:
        self.minutes = [item for bars in tapes for item in bars]

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        wanted = set(symbols)
        return [item for item in self.minutes if item.symbol in wanted
                and (start is None or item.timestamp >= start)
                and (end is None or item.timestamp <= end)
                and (session is None or item.session is session)]

    def get_daily_bars(self, symbols, start=None, end=None):  # type: ignore[no-untyped-def]
        at = datetime(2024, 6, 17, 16, 0, tzinfo=ET)
        return [DailyBar(symbol=symbol, trading_date=DAY - timedelta(days=1), open=100, high=101,
                         low=99, close=100, volume=1_000_000, observed_at=at,
                         available_at=at + timedelta(minutes=1)) for symbol in symbols]


class Env:
    def __init__(self, tmp_path: Path, engine: StrategyV0Engine | None = None) -> None:
        database = create_db_engine(f"sqlite:///{tmp_path / 'settlement.sqlite3'}")
        Base.metadata.create_all(database)
        self.factory = sessionmaker(bind=database, expire_on_commit=False)
        with self.factory() as session:
            self.account_id = SimulationStateRepository(session).create_account(
                broker_type="SIM", account_key="operator", base_currency="USD",
                initial_cash=CAPITAL, cash=CAPITAL,
                created_at=datetime(2024, 6, 18, 8, tzinfo=ET)).id
            session.commit()
        self.engine = engine
        self.runtime = SimulationRuntimeContext(SimBroker(CAPITAL), self.account_id, self.factory)
        self.service = EntryLifecycleService(self.runtime, engine=engine)

    def restart(self) -> None:
        with self.factory() as session:
            broker = rehydrate_sim_broker(session, self.account_id)
        self.runtime = SimulationRuntimeContext(broker, self.account_id, self.factory)
        self.service = EntryLifecycleService(self.runtime, engine=self.engine)

    @staticmethod
    def candidate(symbol: str) -> ApprovedCandidate:
        return ApprovedCandidate(1, 1, SYMBOLS.index(symbol) + 1, symbol, "NASDAQ",
                                 TrailingProfile.NORMAL, OvernightSuitability.MEDIUM)

    def evaluate(self, symbol: str, provider: Provider, at: datetime):  # type: ignore[no-untyped-def]
        return self.service.evaluate(self.candidate(symbol), provider, as_of=at)

    def orders(self, symbol: str) -> list[ExecutionOrderRecord]:
        with self.factory() as session:
            return list(session.scalars(select(ExecutionOrderRecord)
                                        .where(ExecutionOrderRecord.symbol == symbol)))

    def fills(self, symbol: str) -> list[ExecutionFillRecord]:
        with self.factory() as session:
            return list(session.scalars(select(ExecutionFillRecord).join(
                ExecutionOrderRecord, ExecutionOrderRecord.id == ExecutionFillRecord.order_id)
                .where(ExecutionOrderRecord.symbol == symbol)))

    def reservations(self) -> dict[str, DailySymbolState]:
        with self.factory() as session:
            return {row.symbol: row for row in session.scalars(select(DailySymbolState))}

    def state(self, symbol: str, day: date = DAY) -> StrategyStateRecord | None:
        with self.factory() as session:
            return session.scalar(select(StrategyStateRecord).where(
                StrategyStateRecord.symbol == symbol, StrategyStateRecord.trading_date == day))


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def fill_risk(fill: ExecutionFillRecord, stop: Decimal) -> Decimal:
    quantity = Decimal(fill.quantity)
    return (Decimal(fill.fill_price) * quantity + Decimal(fill.commission)
            + Decimal(fill.fx_cost) - stop * quantity)


def close_to(left: Decimal, right: Decimal) -> bool:
    return abs(Decimal(left) - Decimal(right)) <= Decimal("1e-6")


def assert_ended(env: Env, symbol: str, reason: StrategyReason, *, orders: int) -> None:
    state = env.state(symbol)
    assert (state.phase, state.phase_reason) == (StrategyPhase.NO_TRADE.value, reason.value)
    assert len(env.orders(symbol)) == orders
    assert env.fills(symbol) == []
    assert symbol not in env.reservations()
    assert env.runtime.broker.get_position(symbol) is None


# --- Settlement contract ---------------------------------------------------------------

def test_intended_bar_is_the_first_bar_after_the_decision_minute() -> None:
    assert intended_entry_bar_at(SIGNAL, 1) == INTENDED
    assert intended_entry_bar_at(SIGNAL + timedelta(seconds=30, microseconds=5), 1) == INTENDED


def test_signal_submits_nothing_until_its_intended_bar_then_settles_once(env: Env) -> None:
    provider = Provider(tape("AAA"))
    first = env.evaluate("AAA", provider, SIGNAL)
    assert (first.action, first.reason) == (EntryAction.HOLD,
                                            StrategyReason.ENTRY_AWAITING_EXECUTION_BAR.value)
    assert env.state("AAA").phase == StrategyPhase.ENTRY_SIGNALLED.value
    assert env.evaluate("AAA", provider, M(17)).action is EntryAction.HOLD
    assert env.orders("AAA") == []

    assert env.evaluate("AAA", provider, SETTLE).action is EntryAction.FILLED
    for later in (M(19), M(30), M(120)):
        assert env.evaluate("AAA", provider, later).action is EntryAction.SKIPPED
    orders = env.orders("AAA")
    assert [(order.side, order.status) for order in orders] == [("BUY", OrderStatus.FILLED.value)]
    assert len(env.fills("AAA")) == 1


def test_a_filled_entry_is_submitted_at_its_decision_and_settles_the_latest_bar(env: Env) -> None:
    provider = Provider(tape("AAA"))
    env.evaluate("AAA", provider, SIGNAL)
    env.evaluate("AAA", provider, SETTLE)
    [order], [fill] = env.orders("AAA"), env.fills("AAA")
    # No retroactive fill: the order exists before the bar it fills on opens, and the
    # settlement is recorded while that bar is still the latest completed one.
    assert utc(order.submitted_at) == utc(SIGNAL) < utc(fill.filled_at) == utc(INTENDED)
    assert utc(order.submitted_at) <= utc(order.completed_at)
    assert utc(env.state("AAA").updated_at) < utc(INTENDED) + timedelta(minutes=2)


# --- Original audit reproduction: signal 100 / stop 95 ---------------------------------

@pytest.mark.parametrize("raw_open", [99.0, 100.0])
def test_next_bar_at_or_below_the_signal_fills_within_the_approved_risk(env: Env, raw_open: float) -> None:
    provider = Provider(tape("AAA", fill_open=raw_open))
    env.evaluate("AAA", provider, SIGNAL)
    assert env.evaluate("AAA", provider, SETTLE).action is EntryAction.FILLED
    [fill] = env.fills("AAA")
    reservation = env.reservations()["AAA"]
    actual = fill_risk(fill, Decimal("95"))
    assert actual <= ONE_R * SLACK
    # The day records what the fill carries and what it cost, not the plan.
    assert close_to(reservation.planned_risk_amount, actual)
    assert close_to(reservation.base_notional_reserved,
                    Decimal(fill.fill_price) * Decimal(fill.quantity))


@pytest.mark.parametrize("raw_open", [101.0, 105.0, 120.0])
def test_a_gap_above_the_ceiling_never_fills(env: Env, raw_open: float) -> None:
    provider = Provider(tape("AAA", fill_open=raw_open))
    env.evaluate("AAA", provider, SIGNAL)
    settled = env.evaluate("AAA", provider, SETTLE)
    assert (settled.action, settled.reason) == (EntryAction.REJECTED,
                                                RejectionReason.PRICE_ABOVE_LIMIT.value)
    assert_ended(env, "AAA", StrategyReason.ENTRY_PRICE_ABOVE_CEILING, orders=1)
    assert env.runtime.broker.cash == CAPITAL
    for later in (M(19), M(60)):
        assert env.evaluate("AAA", provider, later).action is EntryAction.SKIPPED
    assert len(env.orders("AAA")) == 1


def test_a_fill_beyond_the_approval_is_rolled_back_rather_than_recorded(env: Env, monkeypatch) -> None:
    """Were the ceiling ever lost, the fill's own transaction refuses the entry."""
    original = RiskEngine.evaluate_base_entry

    def uncapped(self, **kwargs):  # type: ignore[no-untyped-def]
        result = original(self, **kwargs)
        if result.order_intent is None:
            return result
        return replace(result, order_intent=replace(result.order_intent, max_execution_price=None))

    monkeypatch.setattr(RiskEngine, "evaluate_base_entry", uncapped)
    provider = Provider(tape("AAA", fill_open=120.0))
    env.evaluate("AAA", provider, SIGNAL)
    with pytest.raises(EntryRiskInvariantError):
        env.evaluate("AAA", provider, SETTLE)
    assert env.orders("AAA") == [] and env.fills("AAA") == []
    assert env.runtime.broker.get_position("AAA") is None and env.runtime.broker.cash == CAPITAL
    assert env.evaluate("AAA", provider, M(19)).action is EntryAction.REJECTED
    assert_ended(env, "AAA", StrategyReason.ENTRY_SIGNAL_STALE, orders=0)


# --- Stale settlement ------------------------------------------------------------------

@pytest.mark.parametrize("restart", [False, True])
def test_long_downtime_ends_the_signal_instead_of_filling_its_past_bar(env: Env, restart: bool) -> None:
    provider = Provider(tape("AAA"))
    env.evaluate("AAA", provider, SIGNAL)
    if restart:
        env.restart()
    ended = env.evaluate("AAA", provider, M(76))
    assert (ended.action, ended.reason) == (EntryAction.REJECTED,
                                            StrategyReason.ENTRY_SIGNAL_STALE.value)
    assert_ended(env, "AAA", StrategyReason.ENTRY_SIGNAL_STALE, orders=0)


def test_restart_before_the_intended_bar_settles_it_normally(env: Env) -> None:
    provider = Provider(tape("AAA"))
    env.evaluate("AAA", provider, SIGNAL)
    env.restart()
    assert env.evaluate("AAA", provider, SETTLE).action is EntryAction.FILLED
    [fill] = env.fills("AAA")
    assert utc(fill.filled_at) == utc(INTENDED)
    assert fill_risk(fill, Decimal("95")) <= ONE_R * SLACK


def test_a_missing_intended_bar_ends_the_signal_without_order_rows(env: Env) -> None:
    provider = Provider(tape("AAA", intended=False))
    env.evaluate("AAA", provider, SIGNAL)
    assert env.evaluate("AAA", provider, SETTLE).action is EntryAction.HOLD
    assert env.evaluate("AAA", provider, M(19)).action is EntryAction.REJECTED
    assert_ended(env, "AAA", StrategyReason.ENTRY_SIGNAL_STALE, orders=0)


def test_an_intended_bar_that_arrives_late_is_never_filled(env: Env) -> None:
    """Staleness is time, not only what a provider happens to return."""
    provider = Provider(tape("AAA", later=False))
    env.evaluate("AAA", provider, SIGNAL)
    assert env.evaluate("AAA", provider, M(19)).action is EntryAction.REJECTED
    assert_ended(env, "AAA", StrategyReason.ENTRY_SIGNAL_STALE, orders=0)


def test_an_outage_ticking_every_minute_creates_no_order_rows(env: Env) -> None:
    provider = Provider(tape("AAA", intended=False, later=False))
    outcomes = [env.evaluate("AAA", provider, M(minute)) for minute in range(16, 77)]
    assert [outcome.action for outcome in outcomes[:3]] == [EntryAction.HOLD] * 3
    assert outcomes[3].reason == StrategyReason.ENTRY_SIGNAL_STALE.value
    assert all(outcome.action is EntryAction.SKIPPED for outcome in outcomes[4:])
    assert_ended(env, "AAA", StrategyReason.ENTRY_SIGNAL_STALE, orders=0)


# --- Entry deadline --------------------------------------------------------------------

@pytest.mark.parametrize("decision_minute", [59, 60])  # decided 10:29 and 10:30:00
def test_a_signal_at_or_before_the_deadline_settles_on_its_one_following_bar(
        env: Env, decision_minute: int) -> None:
    """The deadline bounds the decision; fill_delay_bars=1 makes a 10:30 decision fill on
    the 10:31 bar, the only bar after the deadline an entry can ever fill on."""
    provider = Provider(tape("AAA", signal_minute=decision_minute - 1))
    assert env.evaluate("AAA", provider, M(decision_minute)).action is EntryAction.HOLD
    assert env.evaluate("AAA", provider, M(decision_minute + 2)).action is EntryAction.FILLED
    [fill] = env.fills("AAA")
    assert utc(fill.filled_at) == utc(M(decision_minute + 1))


@pytest.mark.parametrize("decision_at", [M(61), M(60) + timedelta(microseconds=1)])
def test_a_signal_after_the_deadline_is_never_made(env: Env, decision_at: datetime) -> None:
    provider = Provider(tape("AAA", signal_minute=59))
    ended = env.evaluate("AAA", provider, decision_at)
    # The engine's own deadline NO_TRADE is unchanged: it records the phase, and the
    # reason travels on the decision.
    assert (ended.action, ended.reason) == (EntryAction.REJECTED,
                                            StrategyReason.ENTRY_DEADLINE_EXPIRED.value)
    assert env.state("AAA").phase == StrategyPhase.NO_TRADE.value
    assert env.orders("AAA") == [] and env.fills("AAA") == []


def test_a_persisted_signal_past_the_deadline_ends_at_settlement(env: Env) -> None:
    """The retry path re-checks the deadline rather than trusting a stored signal."""
    provider = Provider(tape("AAA", signal_minute=60))
    with env.factory() as session:
        StrategyStateRepository(session).save(StrategyState(
            "AAA", DAY, phase=StrategyPhase.ENTRY_SIGNALLED, scanner_candidate_id=1,
            entry_trading_date=DAY, entry_price=Decimal("100"), initial_stop=Decimal("95"),
            active_stop=Decimal("95"), highest_price_since_entry=Decimal("100"),
            holding_day_number=1, last_market_as_of=M(61)), updated_at=M(61))
        session.commit()
    assert env.evaluate("AAA", provider, M(63)).reason == StrategyReason.ENTRY_DEADLINE_EXPIRED.value
    assert_ended(env, "AAA", StrategyReason.ENTRY_DEADLINE_EXPIRED, orders=0)


# --- Session boundary ------------------------------------------------------------------

def test_a_signal_whose_bar_is_past_the_close_ends_at_once(tmp_path: Path) -> None:
    # A test-only deadline at the close; production thresholds are not changed.
    env = Env(tmp_path, engine=StrategyV0Engine(StrategyConfig(entry_deadline_et=time(16, 0))))
    provider = Provider(tape("AAA", signal_minute=389))
    assert env.evaluate("AAA", provider, CLOSE).reason == StrategyReason.ENTRY_SESSION_ENDED.value
    assert_ended(env, "AAA", StrategyReason.ENTRY_SESSION_ENDED, orders=0)


@pytest.mark.asyncio
async def test_the_close_ends_an_unsettled_signal_and_the_next_session_never_fills_it(env: Env) -> None:
    provider = Provider(tape("AAA"))
    env.evaluate("AAA", provider, SIGNAL)  # then the process is down for the session
    owner = EntryManagementRuntime(env.runtime, lambda: provider, lifecycle=env.service)
    await owner.run_once(as_of=CLOSE)
    assert env.state("AAA").phase == StrategyPhase.ENTRY_SIGNALLED.value
    assert await owner.run_once(as_of=CLOSE + timedelta(minutes=1)) == ()
    assert_ended(env, "AAA", StrategyReason.ENTRY_SESSION_ENDED, orders=0)
    await owner.run_once(as_of=NEXT_OPEN + timedelta(minutes=18))
    with env.factory() as session:
        assert StrategyStateRepository(session).list_in_phase(StrategyPhase.ENTRY_SIGNALLED) == ()
    assert env.fills("AAA") == []


# --- Daily 3R --------------------------------------------------------------------------

def settle_all(env: Env, provider: Provider, symbols: tuple[str, ...]) -> None:
    for at in (SIGNAL, SETTLE):
        for symbol in symbols:
            env.evaluate(symbol, provider, at)


def test_three_normal_entries_stay_within_three_r_of_actual_risk(env: Env) -> None:
    provider = Provider(*(tape(symbol) for symbol in SYMBOLS[:3]))
    settle_all(env, provider, SYMBOLS[:3])
    reservations = env.reservations()
    assert sorted(reservations) == list(SYMBOLS[:3])
    for symbol in SYMBOLS[:3]:
        [fill] = env.fills(symbol)
        assert close_to(reservations[symbol].planned_risk_amount, fill_risk(fill, Decimal("95")))
        assert reservations[symbol].planned_risk_amount <= ONE_R * SLACK
    assert sum(row.planned_risk_amount for row in reservations.values()) <= 3 * ONE_R * SLACK


def test_a_gap_beyond_the_ceiling_ends_one_entry_and_the_rest_still_enter(env: Env) -> None:
    provider = Provider(tape("AAA"), tape("BBB", fill_open=105.0), tape("CCC"), tape("DDD"))
    settle_all(env, provider, SYMBOLS)
    assert_ended(env, "BBB", StrategyReason.ENTRY_PRICE_ABOVE_CEILING, orders=1)
    assert sorted(p.symbol for p in env.runtime.broker.get_positions()) == ["AAA", "CCC", "DDD"]
    assert sum(row.planned_risk_amount for row in env.reservations().values()) <= 3 * ONE_R * SLACK


def test_settlement_counts_risk_the_day_took_after_the_signal(env: Env) -> None:
    """Sized when it settles, against the day's actual risk as it stands then."""
    provider = Provider(tape("AAA"), tape("BBB"))
    for symbol in ("AAA", "BBB"):
        env.evaluate(symbol, provider, SIGNAL)
    with env.factory() as session:
        DailyRiskRepository(session).reserve_entry(
            trading_date=DAY, symbol="XXX", issued_at=M(17), planned_risk=2 * ONE_R,
            base_notional=Decimal("100"), risk_version="risk_v1", strategy_version="strategy_v0")
        session.commit()
    assert env.evaluate("AAA", provider, SETTLE).action is EntryAction.FILLED
    ended = env.evaluate("BBB", provider, SETTLE)
    assert ended.reason == "DAILY_RISK_LIMIT"
    assert_ended(env, "BBB", StrategyReason.ENTRY_RISK_REJECTED, orders=0)
    assert sum(row.planned_risk_amount for row in env.reservations().values()) <= 3 * ONE_R * SLACK


# --- Base capacity ---------------------------------------------------------------------

def base_used(env: Env) -> Decimal:
    return sum((p.cost_basis for p in env.runtime.broker.get_positions()), Decimal("0"))


def test_actual_base_notional_stays_within_the_capacity_caps(env: Env) -> None:
    provider = Provider(tape("AAA", **TIGHT), tape("BBB", **TIGHT))
    settle_all(env, provider, ("AAA", "BBB"))
    assert len(env.runtime.broker.get_positions()) == 2
    assert env.runtime.broker.get_position("AAA").cost_basis <= CAPITAL * Decimal("0.60")
    assert base_used(env) <= CAPITAL * Decimal("0.80")


@pytest.mark.parametrize("gapped", [("AAA",), ("BBB",), ("AAA", "BBB")])
def test_the_audit_base_capacity_gap_never_fills(env: Env, gapped: tuple[str, ...]) -> None:
    """Audit: planned 67.75% became 81.42% of equity when both opened at 120."""
    provider = Provider(*(tape(symbol, fill_open=120.0 if symbol in gapped else None, **TIGHT)
                          for symbol in ("AAA", "BBB")))
    settle_all(env, provider, ("AAA", "BBB"))
    for symbol in gapped:
        assert_ended(env, symbol, StrategyReason.ENTRY_PRICE_ABOVE_CEILING, orders=1)
    assert base_used(env) <= CAPITAL * Decimal("0.80")


# --- Risk ceiling and execution cost authority ----------------------------------------

def base_entry(execution_config: ExecutionConfig | None):  # type: ignore[no-untyped-def]
    at = SIGNAL
    return RiskEngine().evaluate_base_entry(
        decision=StrategyDecision("AAA", DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK", at),
        eligibility=TradingEligibility(True, book="ACTUAL"),
        account=AccountSnapshot(CAPITAL, CAPITAL, Currency.USD, at),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), at),
        daily_state=DailyTradingState(DAY), entry_price=Decimal("100"), stop_price=Decimal("95"),
        instrument_currency=Currency.USD, created_at=at, execution_config=execution_config)


def test_risk_sizes_with_the_broker_cost_model_and_prices_its_ceiling() -> None:
    config = ExecutionConfig()
    costed = base_entry(config)
    effective = buy_effective_price(Decimal("100"), config)
    ceiling = execution_price(Decimal("100"), OrderSide.BUY, config)
    assert (effective, ceiling) == (Decimal("100.25"), Decimal("100.15"))
    assert costed.metrics.effective_entry_price == effective
    assert costed.order_intent.max_execution_price == costed.metrics.max_execution_price == ceiling
    assert costed.order_intent.quantity == ONE_R / (effective - Decimal("95"))
    assert close_to(costed.metrics.planned_risk, ONE_R)
    bare = base_entry(None)
    assert (bare.order_intent.quantity, bare.order_intent.max_execution_price) == (
        Decimal("10"), Decimal("100"))


def test_the_sim_broker_treats_the_ceiling_as_a_buy_limit() -> None:
    config = ExecutionConfig()

    def intent(ceiling: Decimal) -> OrderIntent:
        return OrderIntent("AAA", OrderSide.BUY, IntentType.BASE_ENTRY, Decimal("9"),
                           Decimal("100"), Decimal("900"), Decimal("900"), "USD", "USD", "v",
                           Decimal("45"), Decimal("95"), SIGNAL, SIGNAL, "r",
                           max_execution_price=ceiling)

    ceiling = execution_price(Decimal("100"), OrderSide.BUY, config)
    above = SimBroker(CAPITAL, config=config)
    rejected = above.submit_order(intent(ceiling), [bar("AAA", INTENDED, 100.01)])
    assert rejected.rejection_reason is RejectionReason.PRICE_ABOVE_LIMIT
    assert above.get_positions() == () and above.cash == CAPITAL
    at = SimBroker(CAPITAL, config=config)
    filled = at.submit_order(intent(ceiling), [bar("AAA", INTENDED, 100.0)])
    assert filled.status is OrderStatus.FILLED and filled.filled_quantity == Decimal("9")
    assert at.get_fills(filled.id)[0].fill_price == ceiling
