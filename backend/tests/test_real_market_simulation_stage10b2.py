from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.broker.sim import SimBroker
from app.core.config import Settings
from app.core.database import Base, create_db_engine
from app.dev.run_real_market_simulation import inspect_readiness, safety_gate
from app.market.domain import MarketSession, MinuteBar
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.lifecycle import StrategyBook, StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner
from app.strategy.session_policy import SessionPolicy

ET = ZoneInfo("America/New_York")
DAY = date(2026, 7, 2)
OPEN = datetime(2026, 7, 2, 9, 30, tzinfo=ET)


def bar(at: datetime, price: float = 102, session: MarketSession = MarketSession.REGULAR) -> MinuteBar:
    return MinuteBar(symbol="ABC", timestamp=at, open=price, high=price + .1, low=price - .1,
                     close=price, volume=1000, session=session, observed_at=at,
                     available_at=at + timedelta(minutes=1))


def entry(at: datetime) -> tuple[StrategyState, StrategyDecision]:
    state = StrategyState("ABC", DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
        book=StrategyBook.ACTUAL, entry_price=Decimal("102"), initial_stop=Decimal("99"),
        active_stop=Decimal("99"), holding_day_number=1)
    return state, StrategyDecision("ABC", DecisionType.ENTER, "ENTRY", at, strategy_version="strategy_v0")


def execute(at: datetime, bars: tuple[MinuteBar, ...]):
    broker = SimBroker("100000")
    state, decision = entry(at)
    result = StrategyLifecycleRunner(broker).execute_entry(
        state=state, decision=decision,
        account=AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, at),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), at), market_bars=bars,
        instrument_currency=Currency.USD, created_at=at,
        actual_risk_state=DailyTradingState(DAY))
    return broker, result


def test_sp1_sp3_sp5_entry_only_regular_and_no_cross_session_fill():
    pre = OPEN - timedelta(minutes=30)
    post = OPEN + timedelta(hours=7)
    assert execute(pre, (bar(OPEN),))[1].order is None
    broker, regular = execute(OPEN + timedelta(minutes=16), (bar(OPEN + timedelta(minutes=17)),))
    assert regular.state.phase is StrategyPhase.POSITION_OPEN
    assert broker.get_position("ABC") is not None
    assert execute(post, (bar(post + timedelta(minutes=1), session=MarketSession.POSTMARKET),))[1].order is None
    _, crossed = execute(OPEN + timedelta(hours=6, minutes=29),
                         (bar(OPEN + timedelta(hours=6, minutes=31), session=MarketSession.POSTMARKET),))
    assert crossed.order is not None and not crossed.order.filled_quantity


def test_sp2_sp4_sp6_pyramid_permissions_and_sp7_monitoring():
    policy = SessionPolicy()
    assert not policy.permissions_at(OPEN - timedelta(minutes=1)).pyramid
    assert policy.permissions_at(OPEN + timedelta(minutes=1)).pyramid
    post = OPEN + timedelta(hours=7)
    permissions = policy.permissions_at(post)
    assert not permissions.pyramid and permissions.position_monitoring and permissions.overnight_review


def test_sp8_sp9_sp10_calendar_dst_holiday_and_early_close():
    policy = SessionPolicy()
    assert policy.session_at(datetime(2026, 3, 9, 13, 31, tzinfo=ZoneInfo("UTC"))) is MarketSession.REGULAR
    assert policy.session_at(datetime(2026, 7, 4, 14, tzinfo=ZoneInfo("UTC"))) is None
    assert policy.session_at(datetime(2026, 11, 27, 13, 1, tzinfo=ET)) is MarketSession.POSTMARKET


def test_pb_safety_gate_defaults_off_and_requires_exact_isolation():
    with pytest.raises(SystemExit):
        safety_gate(Settings(_env_file=None, market_data_provider="kiwoom"))
    ok = Settings(_env_file=None, market_data_provider="kiwoom",
                  run_kiwoom_real_scanner=True, run_real_market_simulation=True)
    safety_gate(ok)
    assert ok.broker_provider == "simulation" and ok.kiwoom_mode == "market_data_only"


def test_rm_fill_position_mark_to_market_and_pnl_truth():
    broker, result = execute(OPEN + timedelta(minutes=16), (bar(OPEN + timedelta(minutes=17), 102),))
    assert result.order is not None and result.order.id.startswith("SIM-")
    position = broker.get_position("ABC")
    assert position is not None and broker.get_fills(result.order.id)
    account = broker.account_snapshot({"ABC": Decimal("105")}, OPEN + timedelta(minutes=18))
    unrealized = position.quantity * (Decimal("105") - position.average_price)
    assert account.equity == broker.cash + position.quantity * Decimal("105")
    assert unrealized > 0


def test_operator_readiness_does_not_create_research_or_decisions(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'operator.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        readiness = inspect_readiness(session)
        assert not readiness.ready and readiness.analysis_id is None
        assert not session.new and not session.dirty and not session.deleted


def test_sim_broker_is_process_local():
    first, second = SimBroker("100000"), SimBroker("100000")
    state, decision = entry(OPEN + timedelta(minutes=16))
    StrategyLifecycleRunner(first).execute_entry(
        state=state, decision=decision,
        account=AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, decision.market_as_of),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), decision.market_as_of),
        market_bars=(bar(OPEN + timedelta(minutes=17)),), instrument_currency=Currency.USD,
        created_at=decision.market_as_of, actual_risk_state=DailyTradingState(DAY))
    assert first.get_position("ABC") is not None
    assert second.get_position("ABC") is None
