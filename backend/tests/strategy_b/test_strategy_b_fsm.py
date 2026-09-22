"""Candidate FSM transitions (B-F0 section 3) and the PIT rules that bound them."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.strategy_b.config import StrategyBConfig
from app.strategy_b.eligibility import IneligibleReason
from app.strategy_b.errors import InvalidTransition, PointInTimeViolation
from app.strategy_b.models import (
    Availability, CandidateState, CorporateActionFlag, HaltStatus, Measured, SetupType,
)
from app.strategy_b.fsm import Candidate, DropReason, FillOutcome, Tick, advance, open_candidate
from app.strategy_b.scanner import scan
from tests.strategy_b.fixtures import (
    SYMBOL, breakout_bars, et, in_scope, make_bar, make_tape, passing_snapshot,
)

CONFIG = StrategyBConfig()
TRIGGER = 11.00 * 1.001
STOP = 10.75


def tape_with(*extra):
    return make_tape([*breakout_bars(), *extra])


def breakout_bar(high: float = 11.10, *, close: float = 11.05, low: float = 10.90,
                 open_: float = 10.92, minute: int = 50):
    return make_bar(9, minute, close, 6000.0, open_=open_, high=high, low=low)


def tick(tape, as_of, *, price: float = 11.02, fill: FillOutcome | None = None,
         scope=None, **snapshot_overrides) -> Tick:
    snapshot = passing_snapshot(as_of=as_of, price=Measured.of(price), **snapshot_overrides)
    return Tick(as_of=as_of, snapshot=snapshot, tape=tape,
                scope=in_scope() if scope is None else scope, fill=fill)


def detected(as_of=et(9, 49), **snapshot_overrides) -> Candidate:
    decision = scan(passing_snapshot(as_of=as_of, **snapshot_overrides),
                    scanner=CONFIG.scanner, candidate=CONFIG.candidate)
    return open_candidate(decision)


def run(tape, candidate: Candidate, *steps) -> Candidate:
    for step in steps:
        candidate = advance(candidate, step, CONFIG)
    return candidate


# ---- the intended path -------------------------------------------------------------------

def test_the_full_path_from_detection_to_a_filled_entry() -> None:
    tape = tape_with(breakout_bar())
    candidate = detected()
    assert candidate.state is CandidateState.DETECTED

    candidate = advance(candidate, tick(tape, et(9, 49)), CONFIG)
    assert candidate.state is CandidateState.WATCHING
    assert candidate.qualified_at == candidate.watching_at == et(9, 49)
    assert candidate.setup is None

    candidate = advance(candidate, tick(tape, et(9, 50)), CONFIG)
    assert candidate.state is CandidateState.SETUP_READY
    assert candidate.setup is not None and candidate.setup.setup is SetupType.HOD_BREAKOUT
    assert candidate.setup.trigger_price == pytest.approx(TRIGGER)
    assert candidate.setup.initial_stop == STOP

    candidate = advance(candidate, tick(tape, et(9, 51)), CONFIG)
    assert candidate.state is CandidateState.ENTRY_SIGNALLED and candidate.signal_at == et(9, 51)

    filled = FillOutcome(filled=True, price=TRIGGER)
    candidate = advance(candidate, tick(tape, et(9, 52), fill=filled), CONFIG)
    assert candidate.state is CandidateState.ENTERED
    assert candidate.entry_price == pytest.approx(TRIGGER) and candidate.entered_at == et(9, 52)
    assert candidate.is_terminal and candidate.drop_reason is None


def test_one_tick_advances_a_candidate_at_most_once() -> None:
    """Everything is ready at 09:50, and the candidate still only reaches WATCHING there."""
    tape = tape_with(breakout_bar())
    candidate = advance(detected(et(9, 50)), tick(tape, et(9, 50)), CONFIG)
    assert candidate.state is CandidateState.WATCHING and candidate.setup is None


# ---- drops -------------------------------------------------------------------------------

def test_a_score_below_the_bar_expires_the_detection() -> None:
    weak = detected(return_1m=Measured.of(2.0), return_3m=Measured.of(0.1),
                    return_5m=Measured.of(0.1), rvol=Measured.of(3.0),
                    rolling_dollar_volume=Measured.of(250_000.0))
    assert weak.score == pytest.approx(0.5)  # every leg exactly at its gate
    dropped = advance(weak, tick(tape_with(), et(9, 49)), CONFIG)
    assert dropped.state is CandidateState.EXPIRED
    assert dropped.drop_reason is DropReason.SCORE_BELOW_THRESHOLD


def test_ineligibility_rejects_at_detection_and_again_while_watching() -> None:
    tape = tape_with()
    halted = advance(detected(), tick(tape, et(9, 49), halt_inferred=HaltStatus.HALT_INFERRED),
                     CONFIG)
    assert halted.state is CandidateState.REJECTED
    assert halted.drop_reason is DropReason.INELIGIBLE
    assert halted.eligibility_reasons == (IneligibleReason.HALT_INFERRED,)

    watching = advance(detected(), tick(tape, et(9, 49)), CONFIG)
    split = frozenset({CorporateActionFlag.SPLIT_ON_DAY})
    rejected = advance(watching, tick(tape, et(9, 50), corporate_action_flags=split), CONFIG)
    assert rejected.state is CandidateState.REJECTED
    assert rejected.eligibility_reasons == (IneligibleReason.CORPORATE_ACTION,)


def test_each_ttl_expires_its_own_state() -> None:
    tape = tape_with(breakout_bar())
    watching = advance(detected(), tick(tape, et(9, 49)), CONFIG)
    expired = advance(watching, tick(tape, et(10, 19)), CONFIG)
    assert (expired.state, expired.drop_reason) == (CandidateState.EXPIRED,
                                                    DropReason.CANDIDATE_TTL)

    armed = advance(watching, tick(make_tape(breakout_bars()), et(9, 50)), CONFIG)
    assert armed.state is CandidateState.SETUP_READY
    setup_expired = advance(armed, tick(make_tape(breakout_bars()), et(10, 0)), CONFIG)
    assert (setup_expired.state, setup_expired.drop_reason) == (CandidateState.EXPIRED,
                                                                DropReason.SETUP_TTL)

    # The signal TTL bounds the order's working window, so it also allows the one extra tick in
    # which a bar opening on the boundary becomes observable: alive at 9:54, gone at 9:55.
    signalled = advance(armed, tick(tape, et(9, 51)), CONFIG)
    assert advance(signalled, tick(tape, et(9, 54)), CONFIG).state is (
        CandidateState.ENTRY_SIGNALLED)
    signal_expired = advance(signalled, tick(tape, et(9, 55)), CONFIG)
    assert (signal_expired.state, signal_expired.drop_reason) == (CandidateState.EXPIRED,
                                                                  DropReason.SIGNAL_TTL)


def test_a_breakout_that_already_ran_away_is_not_chased() -> None:
    tape = tape_with(breakout_bar())
    signalled = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)),
                    tick(tape, et(9, 51)))
    drifted = advance(signalled, tick(tape, et(9, 52), price=TRIGGER * 1.0101), CONFIG)
    assert (drifted.state, drifted.drop_reason) == (CandidateState.CANCELLED,
                                                    DropReason.PRICE_DRIFT)

    just_inside = advance(signalled, tick(tape, et(9, 52), price=TRIGGER * 1.0099,
                                          fill=FillOutcome(filled=True, price=TRIGGER)), CONFIG)
    assert just_inside.state is CandidateState.ENTERED


def test_a_missing_price_does_not_invent_a_drift() -> None:
    tape = tape_with(breakout_bar())
    signalled = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)),
                    tick(tape, et(9, 51)))
    blind = Tick(as_of=et(9, 52), tape=tape, scope=in_scope(),
                 snapshot=passing_snapshot(as_of=et(9, 52),
                                           price=Measured.missing(Availability.NO_DATA)))
    assert advance(signalled, blind, CONFIG).state is CandidateState.ENTRY_SIGNALLED


def test_the_engine_can_refuse_a_signal_and_the_reason_is_kept() -> None:
    tape = tape_with(breakout_bar())
    signalled = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)),
                    tick(tape, et(9, 51)))
    refused = FillOutcome(filled=False, reason=DropReason.MAX_POSITIONS)
    dropped = advance(signalled, tick(tape, et(9, 52), fill=refused), CONFIG)
    assert (dropped.state, dropped.drop_reason) == (CandidateState.CANCELLED,
                                                    DropReason.MAX_POSITIONS)

    with pytest.raises(ValueError):
        FillOutcome(filled=True)
    with pytest.raises(ValueError):
        FillOutcome(filled=False, reason=DropReason.SIGNAL_TTL)


# ---- setup lifecycle inside SETUP_READY ----------------------------------------------------

def test_a_bar_that_triggers_and_then_breaks_the_stop_still_enters() -> None:
    """Worst case first: the trade was entered inside that minute, so the loss is taken."""
    tape = tape_with(breakout_bar(11.10, close=10.60, low=10.55))
    armed = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)))
    assert armed.state is CandidateState.SETUP_READY
    signalled = advance(armed, tick(tape, et(9, 51)), CONFIG)
    assert signalled.state is CandidateState.ENTRY_SIGNALLED


def test_a_close_under_the_stop_without_a_trigger_cancels_the_setup() -> None:
    tape = tape_with(breakout_bar(10.90, close=10.60, low=10.55, open_=10.85))
    armed = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)))
    cancelled = advance(armed, tick(tape, et(9, 51)), CONFIG)
    assert (cancelled.state, cancelled.drop_reason) == (CandidateState.CANCELLED,
                                                        DropReason.SETUP_INVALIDATED)


def test_a_new_high_under_the_trigger_sends_the_candidate_back_to_watching() -> None:
    """11.005 is a new high but not a breakout: the old window is gone, a new one is too short."""
    tape = tape_with(breakout_bar(11.005, close=10.95, low=10.90))
    armed = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)))
    back = advance(armed, tick(tape, et(9, 51)), CONFIG)
    assert back.state is CandidateState.WATCHING
    assert back.setup is None and back.setup_ready_at is None and back.drop_reason is None


def test_a_new_window_replaces_the_armed_setup_and_restarts_its_ttl() -> None:
    extra = [breakout_bar(11.005, close=10.95, low=10.90),
             *(make_bar(9, 51 + k, 10.94, 800.0, open_=10.95, high=10.99, low=10.88)
               for k in range(4))]
    tape = tape_with(*extra)
    armed = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)))
    assert armed.setup is not None and armed.setup.hod == 11.00

    rearmed = advance(armed, tick(tape, et(9, 55)), CONFIG)
    assert rearmed.state is CandidateState.SETUP_READY
    assert rearmed.setup is not None and rearmed.setup.hod == 11.005
    assert rearmed.setup.trigger_price == pytest.approx(11.005 * 1.001)
    assert rearmed.setup_ready_at == et(9, 55)


def test_a_skipped_tick_does_not_lose_the_trigger() -> None:
    """The trigger is judged over every bar that became available since the setup armed."""
    tape = tape_with(breakout_bar())
    armed = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)))
    late = advance(armed, tick(tape, et(9, 55)), CONFIG)
    assert late.state is CandidateState.ENTRY_SIGNALLED


# ---- point in time and refusals -------------------------------------------------------------

def test_a_bar_that_is_not_available_yet_cannot_change_a_transition() -> None:
    quiet, loud = tape_with(), tape_with(breakout_bar(12.00, close=11.90, low=10.90))
    at_50 = [advance(advance(detected(), tick(t, et(9, 49)), CONFIG), tick(t, et(9, 50)), CONFIG)
             for t in (quiet, loud)]
    assert [c.state for c in at_50] == [CandidateState.SETUP_READY] * 2
    assert at_50[0].setup == at_50[1].setup


def test_the_fsm_refuses_to_go_backwards_or_to_advance_a_terminal_candidate() -> None:
    tape = tape_with()
    watching = advance(detected(), tick(tape, et(9, 49)), CONFIG)
    with pytest.raises(PointInTimeViolation):
        advance(watching, tick(tape, et(9, 48)), CONFIG)

    expired = advance(watching, tick(tape, et(10, 19)), CONFIG)
    with pytest.raises(InvalidTransition):
        advance(expired, tick(tape, et(10, 20)), CONFIG)


def test_a_tick_must_describe_one_symbol_at_one_moment() -> None:
    tape = tape_with()
    with pytest.raises(PointInTimeViolation):
        Tick(as_of=et(9, 50), snapshot=passing_snapshot(as_of=et(9, 49)), tape=tape,
             scope=in_scope())
    with pytest.raises(ValueError):
        Tick(as_of=et(9, 50), snapshot=passing_snapshot(as_of=et(9, 50), symbol="OTHER"),
             tape=tape, scope=in_scope())
    with pytest.raises(ValueError):
        advance(replace(detected(), symbol="OTHER"), tick(tape, et(9, 49)), CONFIG)


def test_only_a_passing_scan_decision_opens_a_candidate() -> None:
    failing = scan(passing_snapshot(rvol=Measured.missing(Availability.NO_DATA)),
                   scanner=CONFIG.scanner, candidate=CONFIG.candidate)
    with pytest.raises(InvalidTransition):
        open_candidate(failing)


def test_a_terminal_state_always_carries_exactly_one_reason() -> None:
    base = detected()
    with pytest.raises(ValueError):
        replace(base, state=CandidateState.EXPIRED)
    with pytest.raises(ValueError):
        replace(base, drop_reason=DropReason.CANDIDATE_TTL)
    with pytest.raises(ValueError):
        replace(base, state=CandidateState.ENTERED, entered_at=et(9, 50))


def test_an_armed_state_cannot_exist_without_its_setup() -> None:
    base = detected()
    with pytest.raises(ValueError):
        replace(base, state=CandidateState.SETUP_READY, setup_ready_at=et(9, 50))
    armed = run(tape_with(breakout_bar()), base, tick(tape_with(breakout_bar()), et(9, 49)),
                tick(tape_with(breakout_bar()), et(9, 50)))
    with pytest.raises(ValueError):
        replace(armed, setup=None)
    with pytest.raises(ValueError):
        replace(armed, state=CandidateState.ENTRY_SIGNALLED)


def test_the_same_inputs_always_produce_the_same_transitions() -> None:
    tape = tape_with(breakout_bar())
    steps = [et(9, 49), et(9, 50), et(9, 51)]
    first = run(tape, detected(), *(tick(tape, s) for s in steps))
    second = run(tape, detected(), *(tick(tape, s) for s in steps))
    assert first == second


def test_wall_clock_ttls_are_not_stretched_by_a_silent_tape() -> None:
    """Two consolidation bars never make a setup, and the silence does not extend the TTL."""
    tape = make_tape(breakout_bars(2))
    watching = advance(detected(), tick(tape, et(9, 49)), CONFIG)
    assert advance(watching, tick(tape, et(10, 18, 59)), CONFIG).state is CandidateState.WATCHING
    on_the_minute = advance(watching, tick(tape, et(9, 49) + timedelta(minutes=30)), CONFIG)
    assert (on_the_minute.state, on_the_minute.drop_reason) == (CandidateState.EXPIRED,
                                                                DropReason.CANDIDATE_TTL)


def test_a_signal_is_dropped_when_the_symbol_stops_being_eligible() -> None:
    """A halt inferred after the signal must stop the entry, not be outrun by it."""
    tape = tape_with(breakout_bar())
    signalled = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)),
                    tick(tape, et(9, 51)))
    assert signalled.state is CandidateState.ENTRY_SIGNALLED
    halted = advance(signalled, tick(tape, et(9, 52), halt_inferred=HaltStatus.HALT_INFERRED,
                                     fill=FillOutcome(filled=True, price=TRIGGER)), CONFIG)
    assert (halted.state, halted.drop_reason) == (CandidateState.REJECTED, DropReason.INELIGIBLE)


def test_ineligibility_outranks_an_expiring_ttl_on_the_same_tick() -> None:
    tape = make_tape(breakout_bars(2))
    watching = advance(detected(), tick(tape, et(9, 49)), CONFIG)
    both = advance(watching, tick(tape, et(10, 19), halt_inferred=HaltStatus.HALT_INFERRED), CONFIG)
    assert (both.state, both.drop_reason) == (CandidateState.REJECTED, DropReason.INELIGIBLE)


def test_the_signal_records_the_bar_that_crossed_the_trigger() -> None:
    """The engine fills from this bar; letting it re-derive the bar would duplicate the rule."""
    tape = tape_with(breakout_bar())
    signalled = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)),
                    tick(tape, et(9, 51)))
    assert signalled.signal_bar_timestamp == et(9, 50)

    # a skipped tick still names the first bar that crossed, not the latest one
    later = tape_with(breakout_bar(), breakout_bar(11.40, close=11.30, low=11.00, open_=11.05,
                                                   minute=51))
    armed = run(later, detected(), tick(later, et(9, 49)), tick(later, et(9, 50)))
    caught_up = advance(armed, tick(later, et(9, 55)), CONFIG)
    assert caught_up.signal_bar_timestamp == et(9, 50)


def test_a_setup_that_disappears_takes_its_signal_bar_with_it() -> None:
    tape = tape_with(breakout_bar(11.005, close=10.95, low=10.90))
    armed = run(tape, detected(), tick(tape, et(9, 49)), tick(tape, et(9, 50)))
    back = advance(armed, tick(tape, et(9, 51)), CONFIG)
    assert back.state is CandidateState.WATCHING and back.signal_bar_timestamp is None
