"""Stage 6 deterministic execution, accounting, shadow, and persistence tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.broker.domain import OrderStatus, RejectionReason, TradeStatus, execution_gap_bps
from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.execution.config import ExecutionConfig
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.repositories.execution import ExecutionRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.risk.engine import RiskEngine
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.market.replay import ReplayMarketDataProvider
from app.market.fake import FakeMarketDataProvider
from app.recorder.market_recorder import MarketRecorder
from app.recorder.parquet import ParquetMarketDataStorage
from app.shadow.domain import ShadowResult, ShadowStatus, ShadowVariant, evaluation_eligible
from app.shadow.service import ShadowService

T0 = datetime(2026, 8, 31, 13, 45, tzinfo=timezone.utc)


def intent(side=OrderSide.BUY, qty="10", market_as_of=T0, price="99", risk="100", reason="TEST"):
    q, p = Decimal(qty), Decimal(price)
    return OrderIntent("AAA", side, IntentType.BASE_ENTRY if side is OrderSide.BUY else IntentType.EXIT,
                       q, p, q * p, q * p, "USD", "USD", "fixed_test_v0",
                       Decimal(risk), Decimal("90"), market_as_of, market_as_of, reason)


def bar(minutes: int, opening: float, session=MarketSession.REGULAR):
    ts = T0 + timedelta(minutes=minutes)
    return MinuteBar(symbol="AAA", timestamp=ts, open=opening, high=opening + 1,
                     low=opening - 1, close=opening, volume=1000, session=session,
                     observed_at=ts + timedelta(minutes=1), available_at=ts + timedelta(minutes=1))


def test_next_bar_known_cost_and_closed_trade_r() -> None:
    broker = SimBroker("10000")
    bars = [bar(0, 99), bar(1, 100), bar(2, 101)]
    buy = broker.submit_order(intent(), bars)
    fill = broker.get_fills(buy.id)[0]
    assert buy.filled_at == T0 + timedelta(minutes=1)
    assert fill.fill_price == Decimal("100.15")
    assert fill.spread_cost == Decimal("1")
    assert fill.slippage_cost == Decimal("0.5")
    assert fill.commission == Decimal("1")
    sell_intent = intent(OrderSide.SELL, market_as_of=T0 + timedelta(minutes=1), price="101")
    sell = broker.submit_order(sell_intent, [bar(2, 110)])
    exit_fill = broker.get_fills(sell.id)[0]
    assert exit_fill.fill_price == Decimal("109.835")
    trade = broker.get_trade("AAA")
    assert trade and trade.status is TradeStatus.CLOSED
    assert trade.gross_pnl == Decimal("96.850")
    # Direct expected values, not execution helpers: entry 2.5 + exit (1.1+0.55+1.1).
    assert trade.total_cost == Decimal("5.250")
    assert trade.net_pnl == Decimal("91.600")
    assert trade.gross_r == Decimal("0.96850")
    assert trade.net_r == Decimal("0.916")


def test_multiple_buy_weighted_average_partial_sells_cash_and_mtm() -> None:
    broker = SimBroker("10000", config=ExecutionConfig(default_spread_bps=Decimal("0"),
        default_slippage_bps=Decimal("0"), commission_bps=Decimal("0")))
    broker.submit_order(intent(qty="10"), [bar(1, 100)])
    broker.submit_order(intent(qty="5", market_as_of=T0 + timedelta(minutes=1)), [bar(2, 110)])
    position = broker.get_position("AAA")
    assert position and position.quantity == 15 and position.average_price == Decimal("1550") / 15
    broker.submit_order(intent(OrderSide.SELL, qty="4", market_as_of=T0 + timedelta(minutes=2)), [bar(3, 120)])
    assert broker.get_position("AAA").quantity == 11  # type: ignore[union-attr]
    snapshot = broker.account_snapshot({"AAA": "121"}, T0 + timedelta(minutes=3))
    assert snapshot.cash == Decimal("8930")
    assert snapshot.equity == Decimal("10261")
    broker.submit_order(intent(OrderSide.SELL, qty="11", market_as_of=T0 + timedelta(minutes=3)), [bar(4, 125)])
    assert broker.get_position("AAA") is None
    assert broker.get_trade("AAA").gross_pnl == Decimal("305")  # type: ignore[union-attr]


def test_cash_missing_bar_partial_fill_cross_session_and_invalid_operations() -> None:
    assert SimBroker("1000").submit_order(intent(qty="10"), [bar(1, 100)]).rejection_reason is RejectionReason.INSUFFICIENT_CASH
    assert SimBroker("1000").submit_order(intent(), [bar(0, 99)]).rejection_reason is RejectionReason.NO_NEXT_BAR
    partial = SimBroker("10000", config=ExecutionConfig(partial_fill_enabled=True))
    order = partial.submit_order(intent(), [bar(0, 99, MarketSession.REGULAR), bar(1, 100, MarketSession.POSTMARKET)])
    assert order.status is OrderStatus.PARTIALLY_FILLED and order.filled_quantity == 5
    assert order.fill_session is MarketSession.POSTMARKET and order.crossed_session
    oversized = partial.submit_order(intent(OrderSide.SELL, qty="6", market_as_of=T0 + timedelta(minutes=1)), [bar(2, 101)])
    assert oversized.rejection_reason is RejectionReason.SELL_EXCEEDS_POSITION
    assert partial.cancel_order(order.id).status is OrderStatus.CANCELLED
    assert partial.cancel_order(order.id).rejection_reason is RejectionReason.ORDER_ALREADY_FINAL


def test_ambiguity_determinism_reset_and_gap_helper() -> None:
    config = ExecutionConfig()
    first, second = SimBroker("10000", config=config), SimBroker("10000", config=config)
    for broker in (first, second):
        broker.submit_order(intent(), [bar(1, 100)])
        broker.record_ambiguity("AAA", 2)
    assert first.get_fills() == second.get_fills()
    assert first.get_trade("AAA").ambiguous_bar_count == 2  # type: ignore[union-attr]
    assert execution_gap_bps(Decimal("101"), Decimal("100")) == Decimal("100")
    first.reset()
    assert first.cash == Decimal("10000") and not first.get_fills()


def test_shadow_top8_fanout_control_no_trade_and_freeze_guard() -> None:
    class Candidate:
        def __init__(self, symbol, is_top8=True): self.symbol, self.is_top8 = symbol, is_top8
    candidates = [Candidate(f"S{i}") for i in range(8)] + [Candidate("X", False)]
    results = ShadowService().fan_out(candidates)
    assert len(results) == 40
    assert sum(r.variant.is_control for r in results) == 8
    assert all(r.status is ShadowStatus.NO_TRADE for r in results)
    assert not evaluation_eligible(299, 60) and evaluation_eligible(300, 60)


def test_execution_persistence_decimal_roundtrip_and_atomic_no_trade(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'execution.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        broker = SimBroker("10000")
        order = broker.submit_order(intent(), [bar(1, 100)])
        shadow = ShadowResult("AAA", ShadowVariant.C, status=ShadowStatus.OPEN)
        ExecutionRepository(session).persist_execution(order, broker.get_fills(order.id), broker.get_trade("AAA"), shadow)
        assert session.scalar(select(func.count()).select_from(ExecutionOrderRecord)) == 1
        stored = session.scalar(select(ExecutionFillRecord))
        assert stored and stored.total_cost == Decimal("2.500")
        trade = session.scalar(select(ShadowTradeRecord))
        assert trade and trade.net_r == Decimal("-0.025") and trade.is_control
        ExecutionRepository(session).persist_no_trade(
            ShadowResult("BBB", ShadowVariant.A), record_id="NO-BBB-A")
        assert session.scalar(select(func.count()).select_from(ShadowTradeRecord)) == 2
    engine.dispose()


def test_broker_dependency_direction() -> None:
    import app.broker.sim as sim_module
    import app.risk.engine as risk_module
    import app.strategy.engine as strategy_module
    assert "SimBroker" not in risk_module.__dict__ and "SimBroker" not in strategy_module.__dict__
    assert "GPTAnalysis" not in sim_module.__dict__ and "RiskRepository" not in sim_module.__dict__


def test_risk_intent_replay_to_sim_broker_preserves_sized_quantity() -> None:
    decision = StrategyDecision("AAA", DecisionType.ENTER, "FIXED", T0)
    evaluation = RiskEngine().evaluate_base_entry(
        decision=decision, eligibility=TradingEligibility(True),
        account=AccountSnapshot(Decimal("10000"), Decimal("10000"), Currency.USD, T0),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), T0),
        daily_state=DailyTradingState(T0.date()), entry_price="100", stop_price="98",
        instrument_currency=Currency.USD, created_at=T0,
    )
    assert evaluation.order_intent is not None
    replay = ReplayMarketDataProvider(T0 + timedelta(minutes=3), minute_bars=[bar(1, 100), bar(2, 101)])
    bars = replay.get_minute_bars(["AAA"])
    broker = SimBroker("10000")
    order = broker.submit_order(evaluation.order_intent, bars)
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == evaluation.order_intent.quantity


def test_recorder_parquet_replay_sim_broker_is_repeatable(tmp_path) -> None:
    source_bars = [bar(0, 99), bar(1, 100), bar(2, 110)]
    storage = ParquetMarketDataStorage(tmp_path / "market", source="stage6_fixture")
    recorder = MarketRecorder(FakeMarketDataProvider(minute_bars=source_bars), storage)
    assert recorder.record_minute(["AAA"], T0, T0 + timedelta(minutes=2)).minute_bars == 3
    replay = ReplayMarketDataProvider(T0 + timedelta(minutes=5), storage=storage)
    replayed = replay.get_minute_bars(["AAA"])
    outcomes = []
    for _ in range(2):
        broker = SimBroker("10000")
        buy = broker.submit_order(intent(), replayed)
        broker.submit_order(intent(OrderSide.SELL, market_as_of=buy.filled_at, price="100"), replayed)
        trade = broker.get_trade("AAA")
        outcomes.append((trade.gross_pnl, trade.net_pnl, trade.gross_r, trade.net_r))  # type: ignore[union-attr]
    assert outcomes[0] == outcomes[1]
