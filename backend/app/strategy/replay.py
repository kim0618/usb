"""Small point-in-time lifecycle replay orchestrator (not a backtester)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.market.domain import MinuteBar
from app.strategy.config import VariantConfig
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase, StrategyState


@dataclass(frozen=True)
class ReplayResult:
    state: StrategyState
    decisions: tuple[StrategyDecision, ...]
    ambiguous_bar_count: int


class StrategyReplayService:
    """Runs visible prefixes; Risk and SimBroker remain separate execution dependencies."""

    def __init__(self, engine: StrategyV0Engine | None = None) -> None:
        self.engine = engine or StrategyV0Engine()

    def run_entry_window(self, *, state: StrategyState, bars: Sequence[MinuteBar],
                         market_open: datetime) -> ReplayResult:
        decisions: list[StrategyDecision] = []
        current = state
        ordered = tuple(sorted(bars, key=lambda item: item.timestamp))
        for index, bar in enumerate(ordered):
            result = self.engine.evaluate_entry(state=current, bars=ordered[:index + 1],
                                                market_open=market_open, as_of=bar.available_at)
            current = result.state
            decisions.append(result.decision)
            if result.decision.decision in {DecisionType.ENTER, DecisionType.NO_TRADE}:
                break
        return ReplayResult(current, tuple(decisions), 0)

    def run_open_position(self, *, state: StrategyState, bars: Sequence[MinuteBar],
                          market_open: datetime, average_price: Decimal,
                          variant: VariantConfig) -> ReplayResult:
        if state.phase not in {StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED,
                               StrategyPhase.DAY2_ACTIVE}:
            raise ValueError("position replay requires an open-position phase")
        decisions: list[StrategyDecision] = []
        current, ambiguous = state, 0
        ordered = tuple(sorted(bars, key=lambda item: item.timestamp))
        for index, bar in enumerate(ordered):
            result = self.engine.evaluate_position(
                state=current, bars=ordered[:index + 1], market_open=market_open,
                as_of=bar.available_at, current_price=Decimal(str(bar.close)),
                average_price=average_price, variant=variant)
            current = result.state
            decisions.append(result.decision)
            ambiguous += int(result.ambiguous)
            if result.decision.decision is DecisionType.EXIT:
                break
        return ReplayResult(current, tuple(decisions), ambiguous)
