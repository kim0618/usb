"""Strategy B candidate FSM: the transitions declared in B-F0 section 3.

One candidate is one symbol's attempt at one trade on one day. The FSM moves it from
"something is moving here" to "buy it at this price with this stop", or drops it with a
reason. It decides nothing about money: sizing, fills and exits belong to the engine, and
the engine answers back through ``Tick.fill``.

Rules the whole module obeys:

* one tick advances a candidate at most once, and a tick is a bar-availability time;
* a terminal drop beats an advance, with one deliberate exception (see ``_setup_ready``):
  a bar that touches the trigger produces the entry signal even if the same bar also
  breaks the setup, because the entry happened first inside that minute and the loss must
  be taken, not avoided;
* every threshold comes from ``StrategyBConfig``;
* the same inputs always produce the same transition. Nothing here reads a clock, a random
  source or a dict order.

``QUALIFIED`` and ``WATCHING`` are both stamped on the qualifying tick. They are one
transition split into two timestamps: ``QUALIFIED`` records "it passed the score bar",
``WATCHING`` records "setup search started", and no tick can observe a candidate resting in
``QUALIFIED``.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.strategy_b.config import StrategyBConfig
from app.strategy_b.eligibility import EligibilityDecision, IneligibleReason, evaluate_eligibility
from app.strategy_b.errors import InvalidTransition, PointInTimeViolation
from app.strategy_b.features import SessionTape
from app.strategy_b.models import (
    TERMINAL_CANDIDATE_STATES, Availability, CandidateState, FeatureSnapshot,
)
from app.strategy_b.scanner import ScanDecision
from app.strategy_b.scope import ScopeDecision
from app.strategy_b.setups import HodBreakoutSetup, detect_hod_breakout


class DropReason(StrEnum):
    """Why a candidate left the pool. Every terminal state carries exactly one."""

    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"
    INELIGIBLE = "INELIGIBLE"
    CANDIDATE_TTL = "CANDIDATE_TTL"
    SETUP_TTL = "SETUP_TTL"
    SIGNAL_TTL = "SIGNAL_TTL"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    PRICE_DRIFT = "PRICE_DRIFT"
    SIZE_ZERO = "SIZE_ZERO"
    """Engine answer: the risk budget bought zero shares."""
    MAX_POSITIONS = "MAX_POSITIONS"
    """Engine answer: the portfolio already holds ``risk.max_open_positions``."""
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    """Engine answer: the day is done taking new risk."""


ENGINE_DROP_REASONS = frozenset({
    DropReason.SIZE_ZERO, DropReason.MAX_POSITIONS, DropReason.DAILY_LOSS_LIMIT,
})


@dataclass(frozen=True, slots=True)
class FillOutcome:
    """The engine's answer to an entry signal: filled at a price, or refused with a reason."""

    filled: bool
    price: float | None = None
    reason: DropReason | None = None

    def __post_init__(self) -> None:
        if self.filled and (self.price is None or self.reason is not None):
            raise ValueError("a fill carries a price and no reason")
        if not self.filled and (self.price is not None or self.reason not in ENGINE_DROP_REASONS):
            raise ValueError(f"a refusal carries no price and one of {sorted(ENGINE_DROP_REASONS)}")


@dataclass(frozen=True, slots=True)
class Tick:
    """Everything the FSM may look at for one symbol at one availability time."""

    as_of: datetime
    snapshot: FeatureSnapshot
    tape: SessionTape
    scope: ScopeDecision | None
    fill: FillOutcome | None = None
    """Only read in ``ENTRY_SIGNALLED``; ``None`` means the engine has not answered yet."""

    def __post_init__(self) -> None:
        if self.snapshot.as_of != self.as_of:
            raise PointInTimeViolation("the snapshot must be computed at the tick's as_of")
        if self.snapshot.symbol != self.tape.symbol:
            raise ValueError("snapshot and tape must describe the same symbol")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One symbol's lifecycle record. Immutable: every transition returns a new value."""

    symbol: str
    state: CandidateState
    score: float
    detected_at: datetime
    updated_at: datetime
    qualified_at: datetime | None = None
    watching_at: datetime | None = None
    setup_ready_at: datetime | None = None
    signal_at: datetime | None = None
    entered_at: datetime | None = None
    setup: HodBreakoutSetup | None = None
    signal_bar_timestamp: datetime | None = None
    """The bar that crossed the trigger. The engine fills from it and must not guess it again."""
    entry_price: float | None = None
    drop_reason: DropReason | None = None
    eligibility_reasons: tuple[IneligibleReason, ...] = ()

    def __post_init__(self) -> None:
        terminal = self.state in TERMINAL_CANDIDATE_STATES
        if terminal and self.state is not CandidateState.ENTERED and self.drop_reason is None:
            raise ValueError(f"{self.state} must carry a drop reason")
        if self.state is CandidateState.ENTERED and (self.entry_price is None
                                                     or self.entered_at is None):
            raise ValueError("ENTERED must carry an entry price and time")
        if not terminal and self.drop_reason is not None:
            raise ValueError("a live candidate carries no drop reason")
        armed = (CandidateState.SETUP_READY, CandidateState.ENTRY_SIGNALLED)
        if self.state in armed and (self.setup is None or self.setup_ready_at is None):
            raise ValueError(f"{self.state} must carry an armed setup and its arming time")
        if self.state is CandidateState.ENTRY_SIGNALLED and (self.signal_at is None
                                                              or self.signal_bar_timestamp is None):
            raise ValueError("ENTRY_SIGNALLED must carry a signal time and the bar that crossed")

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_CANDIDATE_STATES


def open_candidate(decision: ScanDecision) -> Candidate:
    """Turn a passing scan decision into a ``DETECTED`` candidate."""
    if not decision.passed or decision.score is None:
        raise InvalidTransition("only a passing scan decision opens a candidate")
    return Candidate(
        symbol=decision.symbol,
        state=CandidateState.DETECTED,
        score=decision.score,
        detected_at=decision.as_of,
        updated_at=decision.as_of,
    )


def advance(candidate: Candidate, tick: Tick, config: StrategyBConfig) -> Candidate:
    """Apply one tick to one candidate and return the resulting candidate."""
    if candidate.is_terminal:
        raise InvalidTransition(f"{candidate.symbol} is already {candidate.state}")
    if candidate.symbol != tick.snapshot.symbol:
        raise ValueError("tick and candidate describe different symbols")
    if tick.as_of < candidate.updated_at:
        raise PointInTimeViolation(
            f"tick {tick.as_of.isoformat()} is before the candidate's last update "
            f"{candidate.updated_at.isoformat()}")

    eligibility = evaluate_eligibility(tick.snapshot, tick.scope)
    if candidate.state is CandidateState.DETECTED:
        return _detected(candidate, tick, config, eligibility)
    if not eligibility.eligible:
        return _reject(candidate, tick, eligibility)
    if candidate.state is CandidateState.WATCHING:
        return _watching(candidate, tick, config)
    if candidate.state is CandidateState.SETUP_READY:
        return _setup_ready(candidate, tick, config)
    if candidate.state is CandidateState.ENTRY_SIGNALLED:
        return _entry_signalled(candidate, tick, config)
    raise InvalidTransition(f"no transition defined for {candidate.state}")


# ---- per-state transitions --------------------------------------------------------------

def _detected(candidate: Candidate, tick: Tick, config: StrategyBConfig,
              eligibility: EligibilityDecision) -> Candidate:
    """Eligibility first, then the score bar. A score miss may come back on a later tick."""
    if not eligibility.eligible:
        return _reject(candidate, tick, eligibility)
    if candidate.score < config.candidate.score_threshold:
        return _drop(candidate, tick, CandidateState.EXPIRED, DropReason.SCORE_BELOW_THRESHOLD)
    return replace(candidate, state=CandidateState.WATCHING, qualified_at=tick.as_of,
                   watching_at=tick.as_of, updated_at=tick.as_of)


def _watching(candidate: Candidate, tick: Tick, config: StrategyBConfig) -> Candidate:
    if _elapsed(candidate.detected_at, tick.as_of) >= config.candidate.candidate_ttl_minutes:
        return _drop(candidate, tick, CandidateState.EXPIRED, DropReason.CANDIDATE_TTL)
    setup = _detect(tick, config).setup
    if setup is None:
        return replace(candidate, updated_at=tick.as_of)
    return replace(candidate, state=CandidateState.SETUP_READY, setup=setup,
                   setup_ready_at=tick.as_of, updated_at=tick.as_of)


def _setup_ready(candidate: Candidate, tick: Tick, config: StrategyBConfig) -> Candidate:
    """TTL, then the trigger, then invalidation, then re-arming.

    The trigger is checked before invalidation on purpose. A bar that reaches the trigger and
    closes under the stop is a trade that was entered and stopped out inside one minute; the
    engine books that loss (B-F0 10.4). Treating it as "never entered" would quietly remove
    the worst fills from the study.
    """
    setup, armed_at = candidate.setup, candidate.setup_ready_at  # the state guarantees both
    if _elapsed(armed_at, tick.as_of) >= config.candidate.setup_ttl_minutes:
        return _drop(candidate, tick, CandidateState.EXPIRED, DropReason.SETUP_TTL)

    scope = config.features.hod_scope
    window = tick.tape.scope_range(tick.as_of, scope)
    if window is not None:
        _, lo, hi = window
        start, stop = tick.tape.window_range(lo, armed_at, tick.as_of)
        if stop > start and tick.tape.high(start, stop) >= setup.trigger_price:
            crossed = next(index for index in range(start, stop)
                           if tick.tape.bars[index].high >= setup.trigger_price)
            return replace(candidate, state=CandidateState.ENTRY_SIGNALLED, signal_at=tick.as_of,
                           signal_bar_timestamp=tick.tape.bars[crossed].timestamp,
                           updated_at=tick.as_of)
        if stop > start and tick.tape.bars[stop - 1].close < setup.initial_stop:
            return _drop(candidate, tick, CandidateState.CANCELLED, DropReason.SETUP_INVALIDATED)

    rearmed = _detect(tick, config).setup
    if rearmed is None:
        return replace(candidate, state=CandidateState.WATCHING, setup=None, setup_ready_at=None,
                       signal_bar_timestamp=None, updated_at=tick.as_of)
    if rearmed.trigger_price != setup.trigger_price or rearmed.initial_stop != setup.initial_stop:
        return replace(candidate, setup=rearmed, setup_ready_at=tick.as_of, updated_at=tick.as_of)
    return replace(candidate, setup=rearmed, updated_at=tick.as_of)


def _entry_signalled(candidate: Candidate, tick: Tick, config: StrategyBConfig) -> Candidate:
    setup, signalled_at = candidate.setup, candidate.signal_at  # the state guarantees both
    if _elapsed(signalled_at, tick.as_of) >= config.candidate.signal_ttl_minutes:
        return _drop(candidate, tick, CandidateState.EXPIRED, DropReason.SIGNAL_TTL)

    price = tick.snapshot.price
    if price.status is Availability.AVAILABLE and price.value is not None:
        trigger = setup.trigger_price
        drift_pct = abs(price.value - trigger) / trigger * 100
        if drift_pct > config.candidate.price_drift_tolerance_pct:
            return _drop(candidate, tick, CandidateState.CANCELLED, DropReason.PRICE_DRIFT)

    if tick.fill is None:
        return replace(candidate, updated_at=tick.as_of)
    if not tick.fill.filled:
        return _drop(candidate, tick, CandidateState.CANCELLED, tick.fill.reason)
    return replace(candidate, state=CandidateState.ENTERED, entered_at=tick.as_of,
                   entry_price=tick.fill.price, updated_at=tick.as_of)


# ---- helpers ----------------------------------------------------------------------------

def _detect(tick: Tick, config: StrategyBConfig):
    return detect_hod_breakout(tick.tape, tick.as_of, scope=config.features.hod_scope,
                               config=config.hod_breakout)


def _reject(candidate: Candidate, tick: Tick, eligibility: EligibilityDecision) -> Candidate:
    return replace(candidate, state=CandidateState.REJECTED, drop_reason=DropReason.INELIGIBLE,
                   eligibility_reasons=eligibility.reasons, updated_at=tick.as_of)


def _drop(candidate: Candidate, tick: Tick, state: CandidateState,
          reason: DropReason) -> Candidate:
    return replace(candidate, state=state, drop_reason=reason, updated_at=tick.as_of)


def _elapsed(since: datetime, as_of: datetime) -> float:
    """Wall-clock minutes. A silent tape cannot stretch a TTL (README, Time Units)."""
    return (as_of - since) / timedelta(minutes=1)
