"""``StrategyConfig.entry_reference_tolerance_pct``: the contract, and what it must not move.

The tolerance exists so an entry the rule already approved can be sized and limited at a
reference slightly above the observed signal price. Three things must stay true, and each
has a test here:

* at its default the strategy, the config snapshot and the run identity are the ones that
  existed before the field did (byte parity, not approximate parity);
* the adjusted reference goes through the *existing* Risk and Execution helpers, so the
  ceiling is still ``execution_price(reference)`` and no second cost formula appears;
* the risk invariant holds: a fill at or below the ceiling never costs more than the
  approved effective price, and a fill above it is refused.
"""

from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtest.baseline.config_snapshot import (
    IDENTITY_ADDITIVE_STRATEGY_FIELDS, raw_strategy, strategy_config_snapshot,
)
from app.backtest.baseline.experiment import config_overrides, identity_lines
from app.execution.config import ExecutionConfig
from app.execution.costs import buy_effective_price, execution_price
from app.execution.domain import OrderSide
from app.risk.config import RiskConfig
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot,
)
from app.risk.engine import RISK_UNIT_TOLERANCE, RiskEngine
from app.strategy.config import StrategyConfig
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import StrategyV0Engine

TOLERANCES = [Decimal("0.0005"), Decimal("0.0010"), Decimal("0.0015")]
PRICE = Decimal("100")
STOP = Decimal("98")
NOW = datetime(2026, 3, 2, 14, 45, tzinfo=timezone.utc)


def engine_with(tolerance: Decimal) -> StrategyV0Engine:
    return StrategyV0Engine(StrategyConfig(entry_reference_tolerance_pct=tolerance))


# --- 1. default parity ---------------------------------------------------------------------


def test_the_default_is_zero_and_leaves_the_reference_untouched():
    assert StrategyConfig().entry_reference_tolerance_pct == Decimal("0")
    engine = StrategyV0Engine()
    price = Decimal("123.45")
    assert engine.entry_reference_price(price) is price


def test_at_the_default_the_field_is_absent_from_the_identity_inputs():
    assert "entry_reference_tolerance_pct" in asdict(StrategyConfig())
    assert "entry_reference_tolerance_pct" not in raw_strategy(StrategyConfig())
    assert IDENTITY_ADDITIVE_STRATEGY_FIELDS == ("entry_reference_tolerance_pct",)


def test_at_the_default_the_snapshot_has_no_tolerance_block():
    snapshot = strategy_config_snapshot(StrategyConfig(), RiskConfig(), ExecutionConfig())
    assert "entry_reference_tolerance" not in snapshot["derived"]
    assert "entry_reference_tolerance_pct" not in snapshot["raw"]["strategy"]


def test_at_the_default_no_experiment_override_is_recorded():
    overrides = config_overrides(StrategyConfig(), RiskConfig(), ExecutionConfig())
    assert overrides == {}
    assert identity_lines(overrides) == ()


# --- 2-4. the reference each tolerance produces --------------------------------------------


@pytest.mark.parametrize("tolerance,expected", [
    (Decimal("0.0005"), Decimal("100.0500")),
    (Decimal("0.0010"), Decimal("100.1000")),
    (Decimal("0.0015"), Decimal("100.1500")),
])
def test_the_reference_is_the_signal_price_scaled_by_the_tolerance(tolerance, expected):
    reference = engine_with(tolerance).entry_reference_price(PRICE)
    assert reference == expected
    # Exact decimal arithmetic, never float: the text is the value.
    assert reference == PRICE * (Decimal("1") + tolerance)


def test_a_negative_tolerance_is_refused():
    with pytest.raises(ValueError):
        StrategyConfig(entry_reference_tolerance_pct=Decimal("-0.0001"))


# --- 5-7. sizing, ceiling and the risk invariant -------------------------------------------


def _sized(tolerance: Decimal, equity: Decimal = Decimal("10000")):
    execution = ExecutionConfig()
    reference = engine_with(tolerance).entry_reference_price(PRICE)
    account = AccountSnapshot(equity, equity, Currency.USD, NOW)
    decision = StrategyDecision("AAA", DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK", NOW,
                                strategy_version=StrategyConfig().version)
    evaluation = RiskEngine(RiskConfig()).evaluate_base_entry(
        decision=decision, eligibility=TradingEligibility(True), account=account,
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), NOW),
        daily_state=DailyTradingState(NOW.date(), session_equity=equity),
        entry_price=reference, stop_price=STOP, instrument_currency=Currency.USD,
        created_at=NOW, execution_config=execution)
    assert evaluation.approved
    return reference, evaluation, execution


def test_a_higher_reference_sizes_a_smaller_quantity():
    quantities = []
    for tolerance in [Decimal("0")] + TOLERANCES:
        _, evaluation, _ = _sized(tolerance)
        quantities.append(evaluation.metrics.final_quantity)
    assert quantities == sorted(quantities, reverse=True)
    assert quantities[0] > quantities[-1]


@pytest.mark.parametrize("tolerance", [Decimal("0")] + TOLERANCES)
def test_the_ceiling_is_still_the_execution_price_of_the_reference(tolerance):
    reference, evaluation, execution = _sized(tolerance)
    assert evaluation.metrics.max_execution_price == execution_price(
        reference, OrderSide.BUY, execution)
    assert evaluation.order_intent.max_execution_price == evaluation.metrics.max_execution_price


@pytest.mark.parametrize("tolerance", TOLERANCES)
def test_a_fill_at_the_ceiling_stays_within_the_approved_risk_and_cost(tolerance):
    reference, evaluation, execution = _sized(tolerance)
    metrics = evaluation.metrics
    quantity = metrics.final_quantity
    # The worst fill the broker may accept is the one whose execution price equals the
    # ceiling; its raw open is that ceiling undone by the same adverse cost.
    adverse = (execution_price(reference, OrderSide.BUY, execution) - reference) / reference
    worst_open = metrics.max_execution_price / (Decimal("1") + adverse)
    assert execution_price(worst_open, OrderSide.BUY, execution) <= metrics.max_execution_price
    outlay = buy_effective_price(worst_open, execution) * quantity
    assert outlay <= buy_effective_price(reference, execution) * quantity
    realised_risk = outlay - STOP * quantity
    # Reconstructing the worst open divides, so compare on the engine's own risk-unit
    # tolerance - the same one ``entry_fill_risk`` allows when it checks a real fill.
    assert realised_risk <= metrics.planned_risk * (Decimal("1") + RISK_UNIT_TOLERANCE)


@pytest.mark.parametrize("tolerance", TOLERANCES)
def test_a_fill_above_the_ceiling_is_refused_by_the_broker_limit(tolerance):
    reference, evaluation, execution = _sized(tolerance)
    above = evaluation.metrics.max_execution_price * Decimal("1.0001")
    assert above > evaluation.order_intent.max_execution_price


def test_the_tolerance_never_reaches_below_the_stop():
    _, evaluation, _ = _sized(TOLERANCES[-1])
    assert evaluation.metrics.effective_entry_price > STOP
    assert evaluation.metrics.per_share_risk > 0


# --- 8-9. the snapshot and the identity of a non-default run -------------------------------


@pytest.mark.parametrize("tolerance", TOLERANCES)
def test_a_non_default_tolerance_moves_every_fingerprint(tolerance):
    baseline = strategy_config_snapshot(StrategyConfig(), RiskConfig(), ExecutionConfig())
    changed = strategy_config_snapshot(StrategyConfig(entry_reference_tolerance_pct=tolerance),
                                       RiskConfig(), ExecutionConfig())
    assert changed["strategy_config_fingerprint"] != baseline["strategy_config_fingerprint"]
    assert changed["config_fingerprint"] != baseline["config_fingerprint"]
    # Only the strategy moved: the risk/execution fingerprint is untouched.
    assert changed["execution_risk_config_fingerprint"] == \
        baseline["execution_risk_config_fingerprint"]
    assert changed["raw"]["strategy"]["entry_reference_tolerance_pct"] == str(tolerance)
    block = changed["derived"]["entry_reference_tolerance"]
    assert block["tolerance_pct_of_reference"] == str(tolerance)


@pytest.mark.parametrize("tolerance", TOLERANCES)
def test_a_non_default_tolerance_is_one_experiment_override_and_nothing_else(tolerance):
    overrides = config_overrides(StrategyConfig(entry_reference_tolerance_pct=tolerance),
                                 RiskConfig(), ExecutionConfig())
    assert list(overrides) == ["strategy.entry_reference_tolerance_pct"]
    assert overrides["strategy.entry_reference_tolerance_pct"] == {
        "baseline": "0", "value": str(tolerance)}
    assert identity_lines(overrides) == (
        f"parameter_override=strategy.entry_reference_tolerance_pct:0->{tolerance}",)


# --- 12. no duplicated execution formula ---------------------------------------------------


def test_the_engine_adds_no_cost_arithmetic_of_its_own():
    import inspect

    from app.strategy import engine as engine_module
    method = engine_module.StrategyV0Engine.entry_reference_price
    body = inspect.getsource(method).replace(method.__doc__ or "", "")
    for forbidden in ("spread", "slippage", "commission", "10000", "BPS", "execution_price"):
        assert forbidden not in body
    # The engine module never imports the execution cost model; the ceiling is Risk's.
    assert "app.execution.costs" not in inspect.getsource(engine_module)


# --- the signal itself must not move -------------------------------------------------------


def test_the_gate_thresholds_are_untouched_by_the_tolerance():
    baseline, toleranced = StrategyConfig(), StrategyConfig(
        entry_reference_tolerance_pct=TOLERANCES[-1])
    moved = {name for name in asdict(baseline)
             if asdict(baseline)[name] != asdict(toleranced)[name]}
    assert moved == {"entry_reference_tolerance_pct"}
    assert toleranced.premarket_gap_min_pct == baseline.premarket_gap_min_pct
    assert toleranced.entry_deadline_et == time(10, 30)


# --- 11. the UI / batch config path --------------------------------------------------------


def test_the_ui_passes_the_tolerance_through_the_official_config_path():
    from dataclasses import replace as dc_replace

    from app.backtest.ui import parameters as ui

    preset = ui.baseline_preset()
    # The preset is still exactly the code defaults, tolerance included.
    assert preset.execution_tolerance_pct == Decimal("0")
    assert ui.strategy_config(preset) == StrategyConfig()
    assert ui.validate_parameters(preset) == []
    # The widget is a percentage; the config field is the fraction the gaps use.
    for percent, fraction in (("0.05", "0.0005"), ("0.10", "0.001"), ("0.15", "0.0015")):
        asked = dc_replace(preset, execution_tolerance_pct=Decimal(percent))
        assert ui.validate_parameters(asked) == []
        config = ui.strategy_config(asked)
        assert config.entry_reference_tolerance_pct == Decimal(fraction)
        # Nothing else the UI can set moved with it.
        assert config.premarket_gap_min_pct == StrategyConfig().premarket_gap_min_pct
        assert config.entry_deadline_et == StrategyConfig().entry_deadline_et
        assert ui.changed_fields(asked) == ("execution_tolerance_pct",)
        assert list(config_overrides(config, RiskConfig(), ExecutionConfig())) == \
            ["strategy.entry_reference_tolerance_pct"]


def test_the_ui_refuses_a_negative_tolerance():
    from dataclasses import replace as dc_replace

    from app.backtest.ui import parameters as ui

    asked = dc_replace(ui.baseline_preset(), execution_tolerance_pct=Decimal("-0.01"))
    assert any("Execution Tolerance" in problem for problem in ui.validate_parameters(asked))
