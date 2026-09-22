"""``max_pyramid_adds = 0`` disables pyramiding; the default 1 is the V1 behaviour, unchanged.

No new field exists for this: the two existing ``max_pyramid_adds`` fields (strategy and risk)
simply accept 0. At 0 the strategy never produces an ADD decision and risk never approves one,
while every other contract - base sizing, the 80/20 base/reserve split, the entry ceiling, the
execution cost model - is untouched. At the default the configuration snapshot, and therefore
every fingerprint a run identity is built from, is byte-identical to the one the stored
baselines were written with.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.backtest.baseline.config_snapshot import strategy_config_snapshot
from app.backtest.baseline.experiment import config_overrides, identity_lines
from app.execution.config import ExecutionConfig
from app.execution.costs import execution_price
from app.execution.domain import OrderSide
from app.market.domain import MarketSession, MinuteBar
from app.risk.config import RiskConfig
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot, PositionSnapshot,
)
from app.risk.domain import RiskRejectionReason
from app.risk.engine import RiskEngine
from app.strategy.config import VARIANT_CONFIGS, StrategyConfig
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase, StrategyState

NY = ZoneInfo("America/New_York")
DAY = date(2026, 9, 1)
OPEN = datetime(2026, 9, 1, 9, 30, tzinfo=NY)
NOW = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)

#: The default snapshot fingerprints stored in the V2 baseline run csb1-e9f8c2225f4f758c52a2
#: (run.json identity block). A default config that no longer reproduces them would change the
#: identity of every stored run.
STORED_DEFAULT_FINGERPRINTS = {
    "strategy_config_fingerprint": "99fbadb9803fb57f919eac23968bec57023f0fe970921f19c8a91286bb772b8f",
    "execution_risk_config_fingerprint": "dc0a27a01f79fb6bf5a5fc3d047971bbbcc351ef58012d310f11a8dacbe10e63",
    "config_fingerprint": "e1db46f29c0086e7efdb998c615555b080db21bd3843592f1c970289cbceeb8e",
}


def disabled() -> tuple[StrategyConfig, RiskConfig]:
    return StrategyConfig(max_pyramid_adds=0), RiskConfig(max_pyramid_adds=0)


# --- 1-4. validation and defaults ------------------------------------------------------------


def test_zero_is_a_valid_strategy_setting():
    assert StrategyConfig(max_pyramid_adds=0).max_pyramid_adds == 0


def test_zero_is_a_valid_risk_setting():
    assert RiskConfig(max_pyramid_adds=0).max_pyramid_adds == 0


@pytest.mark.parametrize("factory", [lambda: StrategyConfig(max_pyramid_adds=-1),
                                     lambda: RiskConfig(max_pyramid_adds=-1)])
def test_a_negative_count_is_refused(factory):
    with pytest.raises(ValueError):
        factory()


def test_the_defaults_stay_one():
    assert StrategyConfig().max_pyramid_adds == 1
    assert RiskConfig().max_pyramid_adds == 1


def test_the_other_count_limits_are_still_validated():
    with pytest.raises(ValueError):
        StrategyConfig(atr_period=0)
    with pytest.raises(ValueError):
        RiskConfig(max_open_positions=0)


# --- 5-8. snapshot, fingerprints, identity ----------------------------------------------------


def test_the_default_snapshot_reproduces_the_stored_fingerprints():
    snapshot = strategy_config_snapshot(StrategyConfig(), RiskConfig(), ExecutionConfig())
    for name, stored in STORED_DEFAULT_FINGERPRINTS.items():
        assert snapshot[name] == stored


def test_the_default_records_no_override():
    assert config_overrides(StrategyConfig(), RiskConfig(), ExecutionConfig()) == {}


def test_a_gap_only_experiment_keeps_its_override_set():
    # G3-T0 is gap 3% and nothing else; the relaxed validation must not add a line to it.
    overrides = config_overrides(StrategyConfig(premarket_gap_min_pct=Decimal("0.03")),
                                 RiskConfig(), ExecutionConfig())
    assert list(overrides) == ["strategy.premarket_gap_min_pct"]


def test_disabling_is_one_policy_recorded_on_both_layers():
    strategy, risk = disabled()
    overrides = config_overrides(strategy, risk, ExecutionConfig())
    assert overrides == {"risk.max_pyramid_adds": {"baseline": 1, "value": 0},
                         "strategy.max_pyramid_adds": {"baseline": 1, "value": 0}}
    assert identity_lines(overrides) == ("parameter_override=risk.max_pyramid_adds:1->0",
                                         "parameter_override=strategy.max_pyramid_adds:1->0")
    default = strategy_config_snapshot(StrategyConfig(), RiskConfig(), ExecutionConfig())
    off = strategy_config_snapshot(strategy, risk, ExecutionConfig())
    assert off["config_fingerprint"] != default["config_fingerprint"]
    assert off["derived"]["pyramiding"]["max_pyramid_adds"] == 0
    assert default["derived"]["pyramiding"]["max_pyramid_adds"] == 1


# --- 9-11. the add path ---------------------------------------------------------------------


def bar(minute: int, *, close: float, high: float, low: float) -> MinuteBar:
    stamp = OPEN + timedelta(minutes=minute)
    return MinuteBar(symbol="USB", timestamp=stamp, open=close, high=high, low=low, close=close,
                     volume=100, session=MarketSession.REGULAR, observed_at=stamp, available_at=stamp)


def add_ready():
    """A position that meets every add condition (the stage-7 fixture)."""
    state = StrategyState("USB", DAY, phase=StrategyPhase.POSITION_OPEN, entry_trading_date=DAY,
                          entry_price=Decimal("100"), initial_stop=Decimal("99"),
                          active_stop=Decimal("99"), highest_price_since_entry=Decimal("100"),
                          holding_day_number=1)
    bars = [bar(i, close=100 + i * .2, high=100.3 + i * .2, low=99.9 + i * .2) for i in range(16)]
    return state, bars


def evaluate(config: StrategyConfig):
    state, bars = add_ready()
    return StrategyV0Engine(config).evaluate_position(
        state=state, bars=bars, market_open=OPEN, as_of=OPEN + timedelta(minutes=15),
        current_price=Decimal("103"), average_price=Decimal("100"), variant=VARIANT_CONFIGS["C"])


def test_the_default_still_emits_the_existing_add():
    assert evaluate(StrategyConfig()).decision.decision is DecisionType.ADD


def test_zero_adds_emits_no_add_on_the_same_position():
    result = evaluate(StrategyConfig(max_pyramid_adds=0))
    assert result.decision.decision is not DecisionType.ADD
    assert result.state.add_signal_issued is False
    # Everything else the tick computes is the same: the trail and the watermark.
    default = evaluate(StrategyConfig())
    assert result.state.active_stop == default.state.active_stop
    assert result.state.highest_price_since_entry == default.state.highest_price_since_entry


def add_kwargs():
    winner = PositionSnapshot("AAA", Decimal("10"), Decimal("100"), Decimal("110"), Currency.USD,
                              initial_stop=Decimal("98"))
    return dict(decision=StrategyDecision("AAA", DecisionType.ADD, "FIXED_TEST", NOW),
                eligibility=TradingEligibility(True),
                account=AccountSnapshot(Decimal("100000"), Decimal("100000"), Currency.USD, NOW),
                portfolio=PortfolioSnapshot((winner,), Decimal("0"), Decimal("0"), NOW),
                daily_state=DailyTradingState(DAY), created_at=NOW,
                planned_initial_risk=Decimal("5000"))


def test_risk_never_approves_an_add_at_zero():
    assert RiskEngine(RiskConfig()).evaluate_pyramid_add(**add_kwargs()).approved
    refused = RiskEngine(RiskConfig(max_pyramid_adds=0)).evaluate_pyramid_add(**add_kwargs())
    assert not refused.approved
    assert refused.rejection_reason is RiskRejectionReason.PYRAMID_LIMIT
    assert refused.order_intent is None


# --- 12-15. nothing else moves ---------------------------------------------------------------


def base_entry(risk: RiskConfig):
    account = AccountSnapshot(Decimal("10000"), Decimal("10000"), Currency.USD, NOW)
    return RiskEngine(risk).evaluate_base_entry(
        decision=StrategyDecision("AAA", DecisionType.ENTER, "FIXED_TEST", NOW),
        eligibility=TradingEligibility(True), account=account,
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), NOW),
        daily_state=DailyTradingState(NOW.date(), session_equity=Decimal("10000")),
        entry_price=Decimal("100"), stop_price=Decimal("98"), instrument_currency=Currency.USD,
        created_at=NOW, execution_config=ExecutionConfig())


def test_base_entry_sizing_is_unchanged_at_zero():
    on, off = base_entry(RiskConfig()), base_entry(RiskConfig(max_pyramid_adds=0))
    assert on.approved and off.approved
    assert off.metrics == on.metrics
    assert off.order_intent.quantity == on.order_intent.quantity


def test_the_80_20_split_is_unchanged_and_not_reassigned():
    risk = RiskConfig(max_pyramid_adds=0)
    assert (risk.base_capacity_pct, risk.pyramid_reserve_pct) == (Decimal("0.80"), Decimal("0.20"))
    assert base_entry(risk).metrics.base_capacity_remaining == base_entry(RiskConfig()).metrics.base_capacity_remaining


def test_the_entry_ceiling_invariant_is_unchanged():
    off = base_entry(RiskConfig(max_pyramid_adds=0))
    assert off.metrics.max_execution_price == execution_price(Decimal("100"), OrderSide.BUY, ExecutionConfig())


def test_no_execution_or_strategy_contract_other_than_adds_moves():
    strategy, risk = disabled()
    default = strategy_config_snapshot(StrategyConfig(), RiskConfig(), ExecutionConfig())
    off = strategy_config_snapshot(strategy, risk, ExecutionConfig())
    assert off["raw"]["execution"] == default["raw"]["execution"]
    assert off["derived"]["execution"] == default["derived"]["execution"]
    assert off["derived"]["entry_price_ceiling"] == default["derived"]["entry_price_ceiling"]
    moved = {k for k in default["raw"]["strategy"] if default["raw"]["strategy"][k] != off["raw"]["strategy"][k]}
    moved |= {f"risk.{k}" for k in default["raw"]["risk"] if default["raw"]["risk"][k] != off["raw"]["risk"][k]}
    assert moved == {"max_pyramid_adds", "risk.max_pyramid_adds"}
    changed_derived = {k for k in default["derived"] if default["derived"][k] != off["derived"][k]}
    assert changed_derived == {"pyramiding"}
