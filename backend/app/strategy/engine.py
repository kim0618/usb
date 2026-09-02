"""Common broker/ORM-free Strategy V0 engine for actual and shadow paths."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.market.domain import MarketSession, MinuteBar
from app.strategy.config import StrategyConfig, TrailingMode, VariantConfig
from app.strategy.domain import DecisionType, StrategyContext, StrategyDecision
from app.strategy.indicators import atr_sma, closing_strength, opening_range, session_vwap
from app.strategy.lifecycle import OvernightSuitability, StrategyBook, StrategyPhase, StrategyState, TrailingProfile


class StrategyReason(StrEnum):
    HUMAN_NOT_APPROVED = "HUMAN_NOT_APPROVED"
    PREMARKET_PASS = "PREMARKET_PASS"
    GAP_TOO_LOW = "GAP_TOO_LOW"
    GAP_TOO_HIGH = "GAP_TOO_HIGH"
    LOW_PREMARKET_VOLUME = "LOW_PREMARKET_VOLUME"
    NEGATIVE_CATALYST = "NEGATIVE_CATALYST"
    RESEARCH_BLOCKED = "RESEARCH_BLOCKED"
    INVALID_PREMARKET_DATA = "INVALID_PREMARKET_DATA"
    OPENING_RANGE_BUILDING = "OPENING_RANGE_BUILDING"
    ABOVE_VWAP_AND_OR_BREAK = "ABOVE_VWAP_AND_OR_BREAK"
    ENTRY_DEADLINE_EXPIRED = "ENTRY_DEADLINE_EXPIRED"
    BELOW_VWAP = "BELOW_VWAP"
    NO_BREAKOUT = "NO_BREAKOUT"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    INSUFFICIENT_OPENING_RANGE = "INSUFFICIENT_OPENING_RANGE"
    INITIAL_STOP = "INITIAL_STOP"
    TRAILING_STOP = "TRAILING_STOP"
    PYRAMID_CONFIRMATION = "PYRAMID_CONFIRMATION"
    HOLD = "HOLD"
    OVERNIGHT_HOLD = "OVERNIGHT_HOLD"
    OVERNIGHT_REJECTED = "OVERNIGHT_REJECTED"
    DAY2_MAX_HOLD = "DAY2_MAX_HOLD"
    DAY2_NEGATIVE_CATALYST = "DAY2_NEGATIVE_CATALYST"


@dataclass(frozen=True)
class PremarketContext:
    previous_regular_close: Decimal
    reference_price: Decimal
    premarket_volume: Decimal
    historical_average_daily_volume: Decimal
    has_new_negative_catalyst: bool = False
    research_blocked: bool = False

    @property
    def gap_pct(self) -> Decimal | None:
        if self.previous_regular_close <= 0 or self.reference_price <= 0:
            return None
        return self.reference_price / self.previous_regular_close - Decimal("1")

    @property
    def volume_ratio(self) -> Decimal | None:
        if self.premarket_volume < 0 or self.historical_average_daily_volume <= 0:
            return None
        return self.premarket_volume / self.historical_average_daily_volume


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: StrategyReason
    gap_pct: Decimal | None = None
    volume_ratio: Decimal | None = None


@dataclass(frozen=True)
class BarEvaluation:
    state: StrategyState
    decision: StrategyDecision
    opening_range_high: Decimal | None = None
    opening_range_low: Decimal | None = None
    vwap: Decimal | None = None
    atr: Decimal | None = None
    ambiguous: bool = False


class StrategyEngine(Protocol):
    def decide(self, context: StrategyContext) -> StrategyDecision: ...


class FixedDecisionStrategy:
    """Test/support strategy; it contains no market trading rule."""

    def __init__(self, decision: StrategyDecision) -> None:
        self.decision = decision

    def decide(self, context: StrategyContext) -> StrategyDecision:
        if context.symbol != self.decision.symbol:
            raise ValueError("strategy context symbol does not match fixed decision")
        return self.decision


class StrategyV0Engine:
    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    def premarket_gate(self, context: PremarketContext, *, human_approved: bool,
                       shadow_mode: bool) -> GateResult:
        gap, ratio = context.gap_pct, context.volume_ratio
        if not shadow_mode and not human_approved:
            return GateResult(False, StrategyReason.HUMAN_NOT_APPROVED, gap, ratio)
        if gap is None or ratio is None:
            return GateResult(False, StrategyReason.INVALID_PREMARKET_DATA, gap, ratio)
        if context.research_blocked:
            return GateResult(False, StrategyReason.RESEARCH_BLOCKED, gap, ratio)
        if context.has_new_negative_catalyst:
            return GateResult(False, StrategyReason.NEGATIVE_CATALYST, gap, ratio)
        if gap < self.config.premarket_gap_min_pct:
            return GateResult(False, StrategyReason.GAP_TOO_LOW, gap, ratio)
        if gap > self.config.premarket_gap_max_pct:
            return GateResult(False, StrategyReason.GAP_TOO_HIGH, gap, ratio)
        if ratio < self.config.premarket_volume_ratio_min:
            return GateResult(False, StrategyReason.LOW_PREMARKET_VOLUME, gap, ratio)
        return GateResult(True, StrategyReason.PREMARKET_PASS, gap, ratio)

    def evaluate_entry(self, *, state: StrategyState, bars: Sequence[MinuteBar],
                       market_open: datetime, as_of: datetime,
                       current_reference_price: Decimal | None = None) -> BarEvaluation:
        decision = self._decision(state, DecisionType.HOLD, StrategyReason.OPENING_RANGE_BUILDING, as_of)
        ranges = opening_range(bars, market_open, self.config.opening_range_minutes, as_of)
        evaluation_start = market_open + timedelta(minutes=self.config.opening_range_minutes)
        local_time = as_of.astimezone(market_open.tzinfo).time().replace(tzinfo=None)
        if local_time > self.config.entry_deadline_et and ranges is None:
            new_state = state.transition(StrategyPhase.NO_TRADE)
            return BarEvaluation(new_state, self._decision(new_state, DecisionType.NO_TRADE,
                                 StrategyReason.INSUFFICIENT_OPENING_RANGE, as_of))
        if as_of < evaluation_start or ranges is None:
            return BarEvaluation(state, decision)
        if local_time > self.config.entry_deadline_et:
            new_state = state.transition(StrategyPhase.NO_TRADE)
            return BarEvaluation(new_state, self._decision(new_state, DecisionType.NO_TRADE,
                                 StrategyReason.ENTRY_DEADLINE_EXPIRED, as_of), *ranges)
        visible = [b for b in bars if b.session is MarketSession.REGULAR and b.timestamp <= as_of
                   and b.available_at <= as_of]
        if not visible:
            return BarEvaluation(state, self._decision(state, DecisionType.HOLD,
                                 StrategyReason.INVALID_MARKET_DATA, as_of), *ranges)
        price = current_reference_price or Decimal(str(max(visible, key=lambda b: b.timestamp).close))
        vwap = session_vwap(bars, market_open, as_of)
        if vwap is None:
            return BarEvaluation(state, self._decision(state, DecisionType.HOLD,
                                 StrategyReason.INVALID_MARKET_DATA, as_of), *ranges)
        waiting = state.transition(StrategyPhase.WAITING_ENTRY) if state.phase is StrategyPhase.OPENING_RANGE_BUILDING else state
        if price <= vwap:
            return BarEvaluation(waiting, self._decision(waiting, DecisionType.HOLD,
                                 StrategyReason.BELOW_VWAP, as_of), *ranges, vwap)
        if price <= ranges[0]:
            return BarEvaluation(waiting, self._decision(waiting, DecisionType.HOLD,
                                 StrategyReason.NO_BREAKOUT, as_of), *ranges, vwap)
        initial_stop = ranges[1]
        if initial_stop >= price:
            return BarEvaluation(waiting, self._decision(waiting, DecisionType.NO_TRADE,
                                 StrategyReason.INVALID_MARKET_DATA, as_of), *ranges, vwap)
        signalled = waiting.transition(StrategyPhase.ENTRY_SIGNALLED, entry_price=price,
                                       initial_stop=initial_stop, active_stop=initial_stop,
                                       highest_price_since_entry=price,
                                       entry_trading_date=state.trading_date,
                                       holding_day_number=1, last_market_as_of=as_of)
        return BarEvaluation(signalled, self._decision(signalled, DecisionType.ENTER,
                             StrategyReason.ABOVE_VWAP_AND_OR_BREAK, as_of,
                             {"entry_price": str(price), "initial_stop": str(initial_stop)}),
                             *ranges, vwap)

    def evaluate_position(self, *, state: StrategyState, bars: Sequence[MinuteBar],
                          market_open: datetime, as_of: datetime, current_price: Decimal,
                          average_price: Decimal, variant: VariantConfig) -> BarEvaluation:
        if state.entry_price is None or state.initial_stop is None or state.active_stop is None:
            raise ValueError("open position strategy state is incomplete")
        # Normal position management is regular-session only.  Premarket Day-2
        # catalyst handling is a separate explicit path.
        visible = [b for b in bars if b.session is MarketSession.REGULAR
                   and b.timestamp <= as_of and b.available_at <= as_of]
        current_bar = max(visible, key=lambda b: b.timestamp) if visible else None
        if current_bar is None:
            return BarEvaluation(state, self._decision(state, DecisionType.HOLD,
                                 StrategyReason.INVALID_MARKET_DATA, as_of))
        previous_high = state.highest_price_since_entry or state.entry_price
        atr = atr_sma(bars, self.config.atr_period, as_of)
        initial_r = state.entry_price - state.initial_stop
        previous_active_stop = state.active_stop
        # A stop existing before this OHLC bar is evaluated before this bar's
        # high can raise the trailing stop.  The raised stop starts next bar.
        stop_hit = Decimal(str(current_bar.low)) <= previous_active_stop
        if stop_hit:
            reason = StrategyReason.TRAILING_STOP if previous_active_stop > state.initial_stop else StrategyReason.INITIAL_STOP
            signalled = replace(state, last_market_as_of=as_of).transition(StrategyPhase.EXIT_SIGNALLED)
            return BarEvaluation(signalled, self._decision(signalled, DecisionType.EXIT, reason, as_of,
                                 {"active_stop": str(previous_active_stop)}), atr=atr, ambiguous=False)
        highest = max(previous_high, Decimal(str(current_bar.high)))
        active_stop = previous_active_stop
        multiplier = self._trailing_multiplier(state, variant)
        if (variant.trailing_mode is TrailingMode.ATR and atr is not None and initial_r > 0
                and highest - state.entry_price >= initial_r):
            active_stop = max(active_stop, highest - multiplier * atr)
        updated = replace(state, highest_price_since_entry=highest, active_stop=active_stop,
                          last_market_as_of=as_of)
        vwap = session_vwap(bars, market_open, as_of)
        gain_r = (current_price - state.entry_price) / initial_r if initial_r > 0 else Decimal("0")
        if (not state.add_signal_issued and state.add_count < self.config.max_pyramid_adds
                and current_price > average_price and vwap is not None and current_price > vwap
                and gain_r >= self.config.trailing_activation_r and current_price > previous_high):
            issued = replace(updated, add_signal_issued=True)
            return BarEvaluation(issued, self._decision(issued, DecisionType.ADD,
                                 StrategyReason.PYRAMID_CONFIRMATION, as_of), vwap=vwap, atr=atr)
        return BarEvaluation(updated, self._decision(updated, DecisionType.HOLD,
                             StrategyReason.HOLD, as_of), vwap=vwap, atr=atr)

    def closing_review(self, *, state: StrategyState, current_price: Decimal, vwap: Decimal | None,
                       session_low: Decimal | None = None, session_high: Decimal | None = None,
                       bars: Sequence[MinuteBar] | None = None, market_open: datetime | None = None,
                       variant: VariantConfig,
                       stress_within_limit: bool, overnight_position_available: bool,
                       has_new_negative_catalyst: bool, as_of: datetime) -> StrategyDecision:
        if state.holding_day_number >= variant.max_holding_days:
            return self._decision(state, DecisionType.EXIT, StrategyReason.DAY2_MAX_HOLD, as_of)
        if bars is not None and market_open is not None:
            strength = closing_strength(bars, market_open, as_of, current_price)
        elif session_low is not None and session_high is not None:
            day_range = session_high - session_low
            strength = ((current_price - session_low) / day_range) if day_range > 0 else Decimal("0")
        else:
            raise ValueError("closing review requires PIT bars or explicit reviewed range")
        initial_r = ((state.entry_price or Decimal("0")) - (state.initial_stop or Decimal("0")))
        stop_distance_ok = (initial_r > 0 and state.active_stop is not None
                            and current_price - state.active_stop <= self.config.overnight_max_stop_distance_r * initial_r)
        eligible = (variant.allow_overnight and self.config.overnight_enabled
                    and state.overnight_suitability in {OvernightSuitability.MEDIUM, OvernightSuitability.HIGH}
                    and not has_new_negative_catalyst and vwap is not None and current_price > vwap
                    and strength >= self.config.closing_strength_min and stop_distance_ok
                    and state.active_stop is not None and state.active_stop < current_price and stress_within_limit
                    and overnight_position_available)
        return self._decision(state, DecisionType.OVERNIGHT_HOLD if eligible else DecisionType.EXIT,
                              StrategyReason.OVERNIGHT_HOLD if eligible else StrategyReason.OVERNIGHT_REJECTED,
                              as_of, {"closing_strength": str(strength)})

    def evaluate_day2_premarket(self, *, state: StrategyState,
                                has_new_negative_catalyst: bool,
                                as_of: datetime) -> StrategyDecision:
        if state.phase not in {StrategyPhase.OVERNIGHT_HELD, StrategyPhase.DAY2_ACTIVE}:
            raise ValueError("Day 2 premarket evaluation requires an overnight position")
        if has_new_negative_catalyst:
            return self._decision(state, DecisionType.EXIT,
                                  StrategyReason.DAY2_NEGATIVE_CATALYST, as_of)
        return self._decision(state, DecisionType.HOLD, StrategyReason.HOLD, as_of)

    def _decision(self, state: StrategyState, kind: DecisionType, reason: StrategyReason,
                  as_of: datetime, metadata: dict[str, str] | None = None) -> StrategyDecision:
        return StrategyDecision(state.symbol, kind, reason.value, as_of,
                                strategy_version=self.config.version, metadata=metadata or {})

    def _trailing_multiplier(self, state: StrategyState, variant: VariantConfig) -> Decimal:
        if state.book is StrategyBook.SHADOW:
            return variant.trailing_atr_multiplier or Decimal("0")
        return {
            TrailingProfile.TIGHT: self.config.tight_atr_multiplier,
            TrailingProfile.NORMAL: self.config.normal_atr_multiplier,
            TrailingProfile.WIDE: self.config.wide_atr_multiplier,
            TrailingProfile.UNKNOWN: self.config.normal_atr_multiplier,
        }[state.trailing_profile]
