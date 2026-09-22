"""An explicit ASSUMED_HIGH overnight declaration, for RESEARCH experiment runs only.

The baseline authority is unchanged: ``baseline_modes`` still fixes UNKNOWN_CLOSE and
``assert_baseline_modes`` still refuses every other overnight mode (its own test in
``test_current_strategy_baseline`` is untouched). The one way to a different overnight
authority is ``runner.with_research_overnight``, which accepts only ASSUMED_HIGH - MEDIUM
reads exactly as HIGH at the carry gate and LOW exactly as UNKNOWN, so neither is a
distinct run. Such a run says so in its identity (``overnight_mode`` and one
``authority_override`` line), its authority block (source ASSUMED) and its run id prefix;
a plan without the declaration is byte-for-byte the baseline it was.
"""

from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtest.authority.contract import OvernightMode, OvernightSuitability
from app.backtest.baseline.contract import (
    RESEARCH_OVERNIGHT_OVERRIDES, assert_baseline_modes, authority_block, baseline_modes,
    research_authority_block, research_overnight_modes,
)
from app.backtest.baseline.errors import BaselineAuthorityRefused
from app.backtest.baseline.runner import BaselinePlan, baseline_identity, with_research_overnight
from app.backtest.replay.dataset import ReplayDataset
from app.broker.sim import SimBroker
from app.execution.config import ExecutionConfig
from app.execution.costs import execution_price
from app.execution.domain import OrderSide
from app.risk.config import RiskConfig
from app.strategy.config import StrategyConfig
from app.strategy.domain import DecisionType
from app.strategy.engine import StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase, StrategyState
from app.strategy.config import VARIANT_CONFIGS
from tests.test_current_strategy_baseline import Synthetic, synthetic_plan
from tests.test_historical_position_replay import NEXT, _climb, override, session_at
from tests.test_historical_replay_core import ENTRY, PREVIOUS, flat_session
from tests.test_multi_symbol_portfolio_replay import session

HIGH = OvernightMode.ASSUMED_HIGH
BASELINE_OVERNIGHT_WARNING = ("overnight UNKNOWN_CLOSE: the closing review reads UNKNOWN "
                              "suitability, so Day 2 and overnight carry are unreachable in this run")


def carry_tapes(next_session: list) -> dict[str, dict[date, list]]:  # type: ignore[type-arg]
    """XXX climbs into the close (the only tape whose closing review carries)."""
    return {"XXX": {PREVIOUS: flat_session(PREVIOUS),
                    ENTRY: override(session(), ENTRY, _climb()),
                    NEXT: next_session}}


def run(root: Path, next_session: list, overnight: OvernightMode | None):  # type: ignore[no-untyped-def,type-arg]
    synthetic = Synthetic(root, carry_tapes(next_session), ("XXX",))
    if overnight is not None:
        synthetic.plan = with_research_overnight(synthetic.plan, overnight)
    result, document = synthetic.run()
    return synthetic, result, document


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """The same tape under the baseline authority and under the ASSUMED_HIGH experiment."""
    o0 = run(tmp_path_factory.mktemp("o0"), session_at(NEXT, 107.0), None)
    o1 = run(tmp_path_factory.mktemp("o1"), session_at(NEXT, 107.0), HIGH)
    return o0, o1


def only_trade(document: dict) -> dict:  # type: ignore[type-arg]
    trades = document["trades"]
    assert len(trades) == 1, trades
    return trades[0]


# --- 1-4. the default and the refusal --------------------------------------------------------------

def test_the_baseline_default_is_still_unknown_close(tmp_path: Path) -> None:
    plan = synthetic_plan(tmp_path, ("AAA",))
    assert plan.overnight_override is None
    assert plan.modes == baseline_modes(("AAA",))
    assert plan.modes.overnight is OvernightMode.UNKNOWN_CLOSE
    assert plan.authority() == authority_block(baseline_modes(("AAA",)))
    assert plan.authority_override_lines() == ()


def test_the_baseline_path_still_refuses_an_assumed_mode() -> None:
    assumed = research_overnight_modes(("AAA",), HIGH)
    with pytest.raises(BaselineAuthorityRefused):
        assert_baseline_modes(assumed)
    with pytest.raises(BaselineAuthorityRefused):
        authority_block(assumed)


@pytest.mark.parametrize("mode", [OvernightMode.ASSUMED_MEDIUM, OvernightMode.ASSUMED_LOW,
                                  OvernightMode.RECORDED, OvernightMode.UNKNOWN_CLOSE])
def test_the_research_path_accepts_only_assumed_high(tmp_path: Path, mode: OvernightMode) -> None:
    plan = synthetic_plan(tmp_path, ("AAA",))
    with pytest.raises(BaselineAuthorityRefused):
        with_research_overnight(plan, mode)
    with pytest.raises(BaselineAuthorityRefused):
        research_overnight_modes(("AAA",), mode)


def test_a_research_experiment_can_declare_assumed_high(tmp_path: Path) -> None:
    plan = with_research_overnight(synthetic_plan(tmp_path, ("AAA",)), HIGH)
    assert plan.modes.overnight is HIGH
    # Only the overnight declaration moved: candidate, approval and trailing are the baseline's.
    assert replace(plan.modes, overnight=OvernightMode.UNKNOWN_CLOSE) == baseline_modes(("AAA",))
    block = plan.authority()
    assert block == research_authority_block(plan.modes)
    assert (block["overnight_mode"], block["overnight_source"]) == ("ASSUMED_HIGH", "ASSUMED")
    assert block["authority_label"] == "RESEARCH_ONLY"
    other = {k: v for k, v in block.items() if k not in {"overnight_mode", "overnight_source"}}
    assert other == {k: v for k, v in authority_block(baseline_modes(("AAA",))).items()
                     if k not in {"overnight_mode", "overnight_source"}}


def test_no_default_moves_silently() -> None:
    assert RESEARCH_OVERNIGHT_OVERRIDES == frozenset({HIGH})
    assert [f.default for f in fields(BaselinePlan) if f.name == "overnight_override"] == [None]
    config = StrategyConfig()
    assert (config.overnight_enabled, config.max_holding_trading_days,
            config.overnight_max_positions) == (True, 2, 1)
    assert RiskConfig().max_overnight_positions == 1
    assert VARIANT_CONFIGS["C"].allow_overnight and VARIANT_CONFIGS["C"].max_holding_days == 2


# --- 5-6. identity -----------------------------------------------------------------------------------

def test_the_run_identity_differs_only_on_the_overnight_declaration(pair) -> None:  # type: ignore[no-untyped-def]
    (s0, _, d0), (s1, _, d1) = pair
    lines0, lines1 = s0.identity.block.split("\n"), s1.identity.block.split("\n")
    assert set(lines0) - set(lines1) == {"mode.overnight_mode=UNKNOWN_CLOSE"}
    assert set(lines1) - set(lines0) == {
        "mode.overnight_mode=ASSUMED_HIGH",
        "authority_override=overnight_mode:UNKNOWN_CLOSE->ASSUMED_HIGH"}
    assert s0.identity.run_identity != s1.identity.run_identity
    assert s0.identity.run_id.startswith("csb1-") and s1.identity.run_id.startswith("exp1-")
    for key in ("strategy_config_fingerprint", "execution_risk_config_fingerprint",
                "config_fingerprint"):
        assert d0["run"][key] == d1["run"][key]
    assert d0["config"] == d1["config"]


def test_the_default_identity_and_document_are_the_baselines(pair) -> None:  # type: ignore[no-untyped-def]
    (s0, _, d0), _ = pair
    assert not any(line.startswith("authority_override=") for line in s0.identity.block.split("\n"))
    assert "authority_override" not in d0["run"]
    assert d0["authority"] == authority_block(baseline_modes(s0.symbols))
    assert BASELINE_OVERNIGHT_WARNING in d0["warnings"]
    # Planning the same baseline twice gives the same identity: nothing hidden varies.
    datasets = {symbol: ReplayDataset.load(s0.workspace, symbol).identity for symbol in s0.symbols}
    again = baseline_identity(s0.plan, datasets, engine=StrategyV0Engine(), risk=RiskConfig(),
                              execution=ExecutionConfig())
    assert again.block == s0.identity.block and again.run_id == s0.identity.run_id


def test_the_experiment_document_declares_itself(pair) -> None:  # type: ignore[no-untyped-def]
    _, (_, _, d1) = pair
    assert d1["run"]["authority_override"]["overnight_mode"] == {
        "baseline": "UNKNOWN_CLOSE", "value": "ASSUMED_HIGH"}
    assert d1["run"]["authority_override"]["production_config_modified"] is False
    assert BASELINE_OVERNIGHT_WARNING not in d1["warnings"]
    assert any(item.startswith("overnight ASSUMED_HIGH: a RESEARCH experiment declaration")
               for item in d1["warnings"])


# --- 7-8. the gate reads MEDIUM as HIGH and LOW as UNKNOWN ------------------------------------------

def review(suitability: OvernightSuitability):  # type: ignore[no-untyped-def]
    state = StrategyState("AAA", ENTRY, phase=StrategyPhase.POSITION_OPEN, entry_trading_date=ENTRY,
                          entry_price=Decimal("100"), initial_stop=Decimal("99"),
                          active_stop=Decimal("101"), highest_price_since_entry=Decimal("103"),
                          holding_day_number=1, overnight_suitability=suitability)
    from datetime import timezone
    return StrategyV0Engine().closing_review(
        state=state, current_price=Decimal("103"), vwap=Decimal("102"),
        session_low=Decimal("99"), session_high=Decimal("103.5"), variant=VARIANT_CONFIGS["C"],
        stress_within_limit=True, overnight_position_available=True,
        has_new_negative_catalyst=False, as_of=datetime(2024, 6, 18, 19, 50, tzinfo=timezone.utc))


def test_high_and_medium_read_the_same_at_the_carry_gate() -> None:
    high, medium = review(OvernightSuitability.HIGH), review(OvernightSuitability.MEDIUM)
    assert high.decision is medium.decision is DecisionType.OVERNIGHT_HOLD
    assert (high.reason_code, high.metadata) == (medium.reason_code, medium.metadata)


def test_low_and_unknown_read_the_same_at_the_carry_gate() -> None:
    low, unknown = review(OvernightSuitability.LOW), review(OvernightSuitability.UNKNOWN)
    assert low.decision is unknown.decision is DecisionType.EXIT
    assert low.reason_code == unknown.reason_code == "OVERNIGHT_REJECTED"
    assert low.metadata == unknown.metadata


# --- 9-12. the experiment run crosses the session -----------------------------------------------------

def test_the_experiment_carries_across_the_session_and_the_baseline_does_not(pair) -> None:  # type: ignore[no-untyped-def]
    (_, _, d0), (_, _, d1) = pair
    t0, t1 = only_trade(d0), only_trade(d1)
    assert (t0["exit_reason"], t0["exit_date"], t0["day2_reached"]) == (
        "OVERNIGHT_REJECTED", ENTRY.isoformat(), False)
    assert t1["exit_date"] == NEXT.isoformat() and t1["day2_reached"] is True
    assert t1["sessions_held"] == 2
    assert t1["overnight_authority_source"] == "ASSUMED"
    assert t0["overnight_authority_source"] == "UNKNOWN"
    # Same signal, same entry.
    for key in ("entry_time", "entry_price", "initial_stop"):
        assert t0[key] == t1[key]


def test_day_two_is_closed_by_the_maximum_hold(pair) -> None:  # type: ignore[no-untyped-def]
    _, (_, _, d1) = pair
    assert only_trade(d1)["exit_reason"] == "DAY2_MAX_HOLD"


def test_a_day_two_gap_below_the_stop_fills_at_the_next_bars_open(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fills = []
    submit = SimBroker.submit_order

    def recording(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        order = submit(self, *args, **kwargs)
        fills.extend(self.get_fills(order.id))
        return order

    monkeypatch.setattr(SimBroker, "submit_order", recording)
    _, _, document = run(tmp_path, session_at(NEXT, 98.0), HIGH)
    trade = only_trade(document)
    assert trade["exit_date"] == NEXT.isoformat() and trade["day2_reached"] is True
    assert trade["exit_reason"] in {"INITIAL_STOP", "TRAILING_STOP"}
    day2 = [fill for fill in fills
            if fill.side is OrderSide.SELL and fill.filled_at.date() == NEXT]
    assert len(day2) == 1
    # No stop-price fill: the whole session trades at 98, below every stop, so the stop
    # signals on the first bar and the sell settles on the open of the next bar, moved
    # adversely by the unchanged execution cost model.
    fill = day2[0]
    signal = datetime.fromisoformat(trade["exit_signal_time"])
    assert fill.raw_market_price == Decimal("98.0")
    assert fill.fill_price == execution_price(Decimal("98.0"), OrderSide.SELL, ExecutionConfig())
    assert fill.filled_at.astimezone(signal.tzinfo) == signal + timedelta(minutes=1)
    assert fill.fill_price < Decimal(trade["initial_stop"])


def test_the_point_in_time_audit_stays_active(pair) -> None:  # type: ignore[no-untyped-def]
    for _, result, document in pair:
        assert result.pit_violations == 0
        assert document["validation"] == {
            "pit_violations": 0, "entry_pit_violations": 0, "scanner_pit_violations": 0,
            "invariant_violations": 0, "accounting_violations": 0,
            "funnel_reconciliation_failures": 0, "authority_mismatches": 0}
    _, (_, result, _) = pair
    # The carried position was protected on the next session, so Day 2 bars were served.
    assert any(tick.as_of.date() >= NEXT for tick in getattr(result, "ticks", ())) or \
        any(event.trading_date == NEXT for event in result.ledger.events)
