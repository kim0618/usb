from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base, create_db_engine
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.risk.domain import AccountSnapshot, Currency, PortfolioSnapshot
from app.risk.domain import PositionSnapshot
from app.risk.engine import RiskEngine
from app.services.strategy import StrategyLifecycleService, StrategyStateService
from app.strategy.config import VARIANT_CONFIGS
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import PremarketContext, StrategyReason, StrategyV0Engine
from app.strategy.indicators import atr_sma, opening_range, session_vwap
from app.strategy.lifecycle import OvernightSuitability, StrategyPhase, StrategyState
from app.strategy.replay import StrategyReplayService
from app.broker.sim import SimBroker

NY = ZoneInfo("America/New_York")
DAY = date(2026, 9, 1)
OPEN = datetime(2026, 9, 1, 9, 30, tzinfo=NY)


def bar(minute: int, *, close: float = 100, high: float | None = None,
        low: float | None = None, volume: int = 100, session=MarketSession.REGULAR,
        available_shift: int = 0) -> MinuteBar:
    stamp = OPEN + timedelta(minutes=minute)
    high = high if high is not None else close + .2
    low = low if low is not None else close - .2
    return MinuteBar(symbol="USB", timestamp=stamp, open=close, high=high, low=low,
                     close=close, volume=volume, session=session, observed_at=stamp,
                     available_at=stamp + timedelta(minutes=available_shift))


def opening_bars() -> list[MinuteBar]:
    return [bar(i, close=100, high=101 if i == 14 else 100.5, low=99) for i in range(15)]


def entry_state() -> StrategyState:
    return StrategyState("USB", DAY, phase=StrategyPhase.OPENING_RANGE_BUILDING)


@pytest.mark.parametrize(("price", "volume", "reason"), [
    (Decimal("101"), Decimal("100000"), StrategyReason.GAP_TOO_LOW),
    (Decimal("120"), Decimal("100000"), StrategyReason.GAP_TOO_HIGH),
    (Decimal("105"), Decimal("40000"), StrategyReason.LOW_PREMARKET_VOLUME),
])
def test_premarket_rejections(price, volume, reason):
    result = StrategyV0Engine().premarket_gate(PremarketContext(
        Decimal("100"), price, volume, Decimal("1000000")), human_approved=True, shadow_mode=False)
    assert not result.passed and result.reason is reason


def test_actual_human_gate_and_quant_only_shadow_share_market_gate():
    context = PremarketContext(Decimal("100"), Decimal("105"), Decimal("100000"), Decimal("1000000"))
    engine = StrategyV0Engine()
    assert engine.premarket_gate(context, human_approved=False, shadow_mode=False).reason is StrategyReason.HUMAN_NOT_APPROVED
    assert engine.premarket_gate(context, human_approved=False, shadow_mode=True).passed


def test_lifecycle_gate_and_fill_acknowledgements():
    state = StrategyState("USB", DAY)
    approved = StrategyLifecycleService.apply_human_gate(state, approved=True, shadow_mode=False)
    gate = StrategyV0Engine().premarket_gate(PremarketContext(
        Decimal("100"), Decimal("105"), Decimal("100000"), Decimal("1000000")),
        human_approved=True, shadow_mode=False)
    opening = StrategyLifecycleService.apply_premarket_gate(approved, gate)
    assert opening.phase is StrategyPhase.OPENING_RANGE_BUILDING


def test_opening_range_excludes_0945_and_entry_is_strict_breakout():
    bars = opening_bars() + [bar(15, close=102, high=105, low=101.5)]
    result = StrategyV0Engine().evaluate_entry(state=entry_state(), bars=bars,
        market_open=OPEN, as_of=OPEN + timedelta(minutes=15))
    assert result.opening_range_high == Decimal("101")
    assert result.decision.decision is DecisionType.ENTER
    assert result.state.initial_stop == Decimal("99")


def test_entry_deadline_inclusive_then_no_trade_after():
    engine = StrategyV0Engine()
    bars = opening_bars() + [bar(60, close=102, high=102.2, low=101.5)]
    assert engine.evaluate_entry(state=entry_state(), bars=bars, market_open=OPEN,
        as_of=OPEN + timedelta(minutes=60)).decision.decision is DecisionType.ENTER
    state = entry_state()
    result = engine.evaluate_entry(state=state, bars=bars, market_open=OPEN,
        as_of=OPEN + timedelta(minutes=61))
    assert result.state.phase is StrategyPhase.NO_TRADE


def test_vwap_atr_and_opening_range_are_point_in_time_safe():
    bars = opening_bars() + [bar(15, close=102), bar(16, close=1000, volume=1_000_000)]
    cutoff = OPEN + timedelta(minutes=15)
    vwap = session_vwap(bars, OPEN, cutoff)
    assert vwap == session_vwap(bars[:-1], OPEN, cutoff)
    assert opening_range(bars, OPEN, 15, cutoff) == (Decimal("101"), Decimal("99"))
    long = [bar(i, close=100 + i / 10) for i in range(18)]
    assert atr_sma(long, 14, OPEN + timedelta(minutes=15)) == atr_sma(long[:16], 14, OPEN + timedelta(minutes=15))


def test_trailing_non_decreasing_activation_add_once_and_ambiguous_stop():
    engine = StrategyV0Engine()
    state = StrategyState("USB", DAY, phase=StrategyPhase.POSITION_OPEN,
        entry_trading_date=DAY, entry_price=Decimal("100"), initial_stop=Decimal("99"),
        active_stop=Decimal("99"), highest_price_since_entry=Decimal("100"), holding_day_number=1)
    bars = [bar(i, close=100 + i * .2, high=100.3 + i * .2, low=99.9 + i * .2) for i in range(16)]
    result = engine.evaluate_position(state=state, bars=bars, market_open=OPEN,
        as_of=OPEN + timedelta(minutes=15), current_price=Decimal("103"),
        average_price=Decimal("100"), variant=VARIANT_CONFIGS["C"])
    assert result.state.active_stop >= Decimal("99")
    assert result.decision.decision is DecisionType.ADD
    again = engine.evaluate_position(state=result.state, bars=bars, market_open=OPEN,
        as_of=OPEN + timedelta(minutes=15), current_price=Decimal("103"),
        average_price=Decimal("100"), variant=VARIANT_CONFIGS["C"])
    assert again.decision.decision is not DecisionType.ADD
    stop_bar = bar(16, close=102, high=110, low=float(again.state.active_stop - 1))
    stopped = engine.evaluate_position(state=again.state, bars=bars + [stop_bar], market_open=OPEN,
        as_of=stop_bar.timestamp, current_price=Decimal("102"), average_price=Decimal("100"),
        variant=VARIANT_CONFIGS["C"])
    assert stopped.decision.decision is DecisionType.EXIT
    assert stopped.state.phase is StrategyPhase.EXIT_SIGNALLED
    # The stop existed before this bar, so both possible OHLC paths stop out;
    # the favorable high does not make the result ambiguous.
    assert not stopped.ambiguous


def test_overnight_variants_and_day2_mandatory_exit():
    state = StrategyState("USB", DAY, phase=StrategyPhase.POSITION_OPEN,
        entry_price=Decimal("100"), initial_stop=Decimal("99"), active_stop=Decimal("102"),
        highest_price_since_entry=Decimal("105"), holding_day_number=1,
        overnight_suitability=OvernightSuitability.HIGH)
    engine = StrategyV0Engine()
    args = dict(state=state, current_price=Decimal("104"), vwap=Decimal("102"),
        session_low=Decimal("95"), session_high=Decimal("105"), stress_within_limit=True,
        overnight_position_available=True, has_new_negative_catalyst=False, as_of=OPEN)
    assert engine.closing_review(variant=VARIANT_CONFIGS["A"], **args).decision is DecisionType.EXIT
    assert engine.closing_review(variant=VARIANT_CONFIGS["C"], **args).decision is DecisionType.OVERNIGHT_HOLD
    day2 = replace(state, phase=StrategyPhase.DAY2_ACTIVE, holding_day_number=2)
    assert engine.closing_review(variant=VARIANT_CONFIGS["C"], **(args | {"state": day2})).reason_code == "DAY2_MAX_HOLD"


def test_overnight_risk_reduces_40pct_to_25pct():
    now = OPEN
    account = AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, now)
    portfolio = PortfolioSnapshot((), Decimal("0"), Decimal("0"), now)
    result = RiskEngine().evaluate_overnight_notional(account=account, portfolio=portfolio,
                                                       proposed_notional=Decimal("40000"))
    assert result.maximum_notional == Decimal("25000")
    assert result.reduce_notional == Decimal("15000")


def test_calendar_weekend_holiday_and_early_close_review():
    calendar = MarketCalendar()
    assert calendar.next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)
    assert calendar.holding_day_number(date(2026, 7, 2), date(2026, 7, 6)) == 2
    session = calendar.session(date(2026, 11, 27))
    assert session and session.is_early_close
    assert session.market_close - timedelta(minutes=10) == datetime(2026, 11, 27, 12, 50, tzinfo=NY)


def test_strategy_state_persistence_reload(tmp_path):
    db = create_db_engine(f"sqlite:///{tmp_path / 'strategy.sqlite'}")
    Base.metadata.create_all(db)
    saved = StrategyState("USB", DAY, phase=StrategyPhase.POSITION_OPEN,
        entry_trading_date=DAY, entry_price=Decimal("100"), initial_stop=Decimal("99"),
        active_stop=Decimal("101.2"), highest_price_since_entry=Decimal("105"),
        add_count=1, holding_day_number=2, overnight=True, last_market_as_of=OPEN)
    with Session(db) as session:
        StrategyStateService(session).save(saved, updated_at=OPEN)
    with Session(db) as session:
        loaded = StrategyStateService(session).load("USB", DAY)
    assert loaded == saved


def test_invalid_transition_rejected_and_replay_deterministic():
    state = StrategyState("USB", DAY)
    with pytest.raises(ValueError):
        state.transition(StrategyPhase.POSITION_OPEN)
    bars = opening_bars() + [bar(15, close=102)]
    engine = StrategyV0Engine()
    first = engine.evaluate_entry(state=entry_state(), bars=bars, market_open=OPEN, as_of=bars[-1].timestamp)
    second = engine.evaluate_entry(state=entry_state(), bars=bars, market_open=OPEN, as_of=bars[-1].timestamp)
    assert first == second
    replay = StrategyReplayService()
    assert replay.run_entry_window(state=entry_state(), bars=bars, market_open=OPEN) == replay.run_entry_window(
        state=entry_state(), bars=bars, market_open=OPEN)


def test_stop_gap_exit_intent_uses_next_bar_open_not_stop_price():
    engine = RiskEngine()
    decision = StrategyDecision("USB", DecisionType.EXIT, "TRAILING_STOP",
                                OPEN + timedelta(minutes=20), strategy_version="strategy_v0")
    position = PositionSnapshot("USB", Decimal("10"), Decimal("100"), Decimal("95"),
                                Currency.USD, initial_stop=Decimal("100"))
    intent = engine.build_exit_intent(decision=decision, position=position,
                                      created_at=decision.market_as_of)
    broker = SimBroker("10000")
    buy_decision = StrategyDecision("USB", DecisionType.ENTER, "ENTRY", OPEN)
    from app.risk.domain import DailyTradingState
    entered = engine.evaluate_base_entry(decision=buy_decision,
        eligibility=TradingEligibility(True),
        account=AccountSnapshot(Decimal("10000"), Decimal("10000"), Currency.USD, OPEN),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), OPEN),
        daily_state=DailyTradingState(DAY), entry_price=Decimal("100"), stop_price=Decimal("99"),
        instrument_currency=Currency.USD, created_at=OPEN)
    assert entered.order_intent
    entry_fill_bar = bar(1, close=100)
    broker.submit_order(entered.order_intent, [entry_fill_bar])
    # The exit signal references stop/current state, but execution at the next 95 open is conservative.
    exit_bar = bar(21, close=95)
    broker.submit_order(intent, [exit_bar])
    assert broker.get_fills()[-1].raw_market_price == Decimal("95")
