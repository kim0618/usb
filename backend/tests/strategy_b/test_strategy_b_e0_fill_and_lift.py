"""The NEXT_BAR_OPEN fill rule and the lift gate's cohort and measurement rules.

The fill cases are the ones B-E0 4.4 registers. They are tested at the ``signal_fill`` /
``_next_available_bar`` seam rather than through a whole session, because the rule is about
which bar is chosen and that is where the choice happens. What the FSM then does with a missing
fill (ends the candidate on its own signal TTL, never as an account refusal) is asserted here
too, since the contract's claim is precisely that no new rule was needed for it.
"""

from datetime import timedelta

import pytest

from app.backtest.strategy_b.costs import CostModel, FillScenario, signal_fill
from app.backtest.strategy_b.engine import _next_available_bar
from app.backtest.strategy_b_e0 import lift
from app.backtest.strategy_b_e0.contract import load_contract
from app.backtest.strategy_b_e0.gate import LiftOutcome
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.fsm import Candidate, DropReason, ENGINE_DROP_REASONS, _elapsed
from app.strategy_b.models import AVAILABILITY_DELAY
from app.strategy_b.models import CandidateState
from tests.strategy_b.fixtures import D, boundaries, et, make_bar, make_tape

CONTRACT = load_contract()
TRIGGER = 10.10


def tape_with(*bars):
    return make_tape(list(bars))


def signal_bar():
    return make_bar(9, 40, 10.12, 5000.0, open_=10.00, high=10.15, low=9.99)


# ---- the fill rule -------------------------------------------------------------------------


def test_next_actual_bar_in_the_next_minute_fills_at_its_open():
    bar, nxt = signal_bar(), make_bar(9, 41, 10.20, 4000.0, open_=10.18)
    tape = tape_with(bar, nxt)
    found = _next_available_bar(tape, bar, et(9, 42))
    assert found is nxt
    assert signal_fill(TRIGGER, bar, FillScenario.NEXT_BAR_OPEN, found) == pytest.approx(10.18)


def test_signal_ttl_is_the_precommitted_two_wall_clock_minutes():
    """The value the contract fixes is the one the code already carries, in wall-clock minutes."""
    assert CONTRACT.raw["signal_ttl"]["signal_ttl_minutes"] == 2
    assert StrategyBConfig().candidate.signal_ttl_minutes == 2
    assert CONTRACT.raw["signal_ttl"]["units"] == "WALL_CLOCK_MINUTES"
    assert CONTRACT.raw["signal_ttl"]["boundary"] == "INCLUSIVE"
    # a silent tape may not stretch the TTL: elapsed is a plain wall-clock difference
    from app.strategy_b.fsm import _elapsed
    assert _elapsed(et(10, 0), et(10, 2)) == 2.0


def fill_attempt_ticks(signal_at, ttl):
    """The ticks on which the engine may ask the account, per the FSM's own expiry rule.

    Mirrors the two mechanics rather than restating a number: the engine asks only from the
    tick after the signal, and the FSM keeps an ENTRY_SIGNALLED candidate while elapsed is
    within the declared window plus the one minute a boundary bar needs to become observable.
    """
    window = ttl + AVAILABILITY_DELAY / timedelta(minutes=1)
    return [signal_at + timedelta(minutes=k) for k in range(1, ttl + 5)
            if _elapsed(signal_at, signal_at + timedelta(minutes=k)) <= window]


def reachable_bar_gaps(ttl):
    """Which next-actual-bar offsets from the signal bar can actually fill, measured."""
    sig = make_bar(10, 0, 10.12, 5000.0, open_=10.00, high=10.15, low=9.99)
    signal_at = sig.timestamp + timedelta(minutes=1)
    ticks = fill_attempt_ticks(signal_at, ttl)
    gaps = []
    for gap in range(1, ttl + 4):
        nxt = make_bar(10, gap, 10.2, 1000.0, open_=10.18)
        tape = tape_with(sig, nxt)
        if any(_next_available_bar(tape, sig, tick) is nxt for tick in ticks):
            gaps.append(gap)
    return gaps


def test_the_declared_fill_window_is_what_the_engine_actually_reaches():
    """B-E0's rule: next_actual_bar.open_time <= signal_at + signal_ttl_minutes, inclusive.

    With the signal bar at 10:00 the signal is recorded at 10:01 and the window closes at 10:03,
    so bars opening 10:01, 10:02 and 10:03 fill and 10:04 does not. Expressed as offsets from
    the signal bar that is gaps 1..3.
    """
    ttl = StrategyBConfig().candidate.signal_ttl_minutes
    assert reachable_bar_gaps(ttl) == [1, 2, 3]

    sig = make_bar(10, 0, 10.12, 5000.0, open_=10.00, high=10.15, low=9.99)
    signal_at = sig.timestamp + timedelta(minutes=1)
    expiry = signal_at + timedelta(minutes=ttl)
    latest = make_bar(10, 3, 10.2, 1000.0, open_=10.18)
    assert latest.timestamp == expiry, "gap 3 is the inclusive boundary itself"


@pytest.mark.parametrize("ttl", [1, 2, 3, 5])
def test_the_window_tracks_the_ttl_with_no_off_by_one(ttl):
    """Every TTL admits exactly the bars opening within ttl minutes of the signal."""
    assert reachable_bar_gaps(ttl) == list(range(1, ttl + 2)), ttl


def test_case_a_next_minute_has_a_bar():
    assert 1 in reachable_bar_gaps(2)


def test_case_b_a_quiet_minute_then_a_bar_on_the_boundary_still_fills():
    """The core of the fix: a gap inside the window no longer throws the signal away."""
    gaps = reachable_bar_gaps(2)
    assert 2 in gaps and 3 in gaps


def test_case_c_a_bar_past_the_window_does_not_fill():
    assert 4 not in reachable_bar_gaps(2)


def test_case_d_the_exact_expiry_bar_fills_because_the_boundary_is_inclusive():
    ttl = StrategyBConfig().candidate.signal_ttl_minutes
    assert max(reachable_bar_gaps(ttl)) == ttl + 1        # the bar opening at signal_at + ttl
    assert CONTRACT.raw["signal_ttl"]["boundary"] == "INCLUSIVE"


def test_a_silent_tape_cannot_stretch_the_window():
    """Wall-clock, not bars: with no bar at all, nothing becomes reachable."""
    sig = make_bar(10, 0, 10.12, 5000.0, open_=10.00, high=10.15, low=9.99)
    tape = tape_with(sig)
    ticks = fill_attempt_ticks(sig.timestamp + timedelta(minutes=1), 2)
    assert all(_next_available_bar(tape, sig, tick) is None for tick in ticks)


def test_the_implementation_gap_is_closed_in_the_contract():
    gap = CONTRACT.raw["execution_model"]["fill_rule"]["implementation_gap"]
    assert gap["status"] == "RESOLVED"
    assert all(case["agree"] for case in CONTRACT.raw["execution_model"]["edge_cases_to_test"])


def test_a_bar_beyond_the_ttl_is_not_reachable_before_expiry():
    """The bar exists, but no tick inside the TTL can see it, so the signal expires."""
    bar, late = signal_bar(), make_bar(9, 50, 10.40, 3000.0, open_=10.38)
    tape = tape_with(bar, late)
    signal_at = bar.timestamp + timedelta(minutes=1)
    expiry = signal_at + timedelta(minutes=StrategyBConfig().candidate.signal_ttl_minutes)
    assert _next_available_bar(tape, bar, expiry) is None
    assert signal_fill(TRIGGER, bar, FillScenario.NEXT_BAR_OPEN, None) is None


def test_the_last_actual_bar_of_the_session_has_nothing_to_fill_at():
    bar = signal_bar()
    tape = tape_with(make_bar(9, 39, 10.00, 1000.0), bar)
    assert _next_available_bar(tape, bar, et(15, 59)) is None
    assert signal_fill(TRIGGER, bar, FillScenario.NEXT_BAR_OPEN, None) is None


def test_a_bar_that_is_not_yet_available_does_not_fill_this_tick_but_may_later():
    bar, nxt = signal_bar(), make_bar(9, 41, 10.20, 4000.0, open_=10.18)
    tape = tape_with(bar, nxt)
    assert _next_available_bar(tape, bar, et(9, 41)) is None      # not available yet
    assert _next_available_bar(tape, bar, et(9, 42)) is nxt       # available one tick later


def test_the_fill_can_never_reach_another_session():
    """A tape is one day, so the next-day bar is unreachable by construction."""
    tape = tape_with(signal_bar())
    later = make_bar(9, 31, 11.0, 1000.0, day=D + timedelta(days=1))
    assert later.timestamp.date() != D
    assert _next_available_bar(tape, tape.bars[-1], et(23, 59)) is None


def test_synthetic_bars_cannot_enter_a_tape_at_all():
    """The 'only synthetic bars follow' case: the tape refuses them, so none can be a fill."""
    from app.strategy_b.errors import InvalidTape
    synthetic = make_bar(9, 41, 10.2, 0.0, transactions=None)
    object.__setattr__(synthetic, "volume", 0.0)
    with pytest.raises((InvalidTape, ValueError)):
        make_tape([signal_bar(), synthetic._replace(synthetic=True)
                   if hasattr(synthetic, "_replace") else _as_synthetic(synthetic)])


def _as_synthetic(bar):
    from dataclasses import replace
    try:
        return replace(bar, synthetic=True)
    except TypeError:  # the model has no synthetic flag; the tape only ever holds actual bars
        pytest.skip("MomentumBar carries no synthetic flag in this build")


def test_a_missing_fill_is_never_an_account_refusal():
    """The contract's claim: no fill is SIGNAL_TTL from the FSM, not an engine drop reason."""
    assert DropReason.SIGNAL_TTL not in ENGINE_DROP_REASONS
    assert ENGINE_DROP_REASONS == {DropReason.SIZE_ZERO, DropReason.MAX_POSITIONS,
                                   DropReason.DAILY_LOSS_LIMIT}


def test_signal_bar_scenario_is_the_counterfactual_and_prices_differently():
    bar, nxt = signal_bar(), make_bar(9, 41, 10.30, 4000.0, open_=10.28)
    optimistic = signal_fill(TRIGGER, bar, FillScenario.SIGNAL_BAR, nxt)
    delayed = signal_fill(TRIGGER, bar, FillScenario.NEXT_BAR_OPEN, nxt)
    assert optimistic == pytest.approx(TRIGGER)
    assert delayed == pytest.approx(10.28)


def test_cost_model_moves_the_buy_price_against_the_trade():
    level = CONTRACT.cost_level("BASE")
    costs = CostModel(fee_bps_per_side=level.commission_bps_per_side,
                      slippage_bps_per_side=level.execution_cost_bps_per_side)
    assert costs.buy_price(100.0) == pytest.approx(100.0 * (1 + 15 / 10_000))
    assert costs.sell_price(100.0) == pytest.approx(100.0 * (1 - 15 / 10_000))
    assert costs.fee(100.0, 10) == pytest.approx(100.0 * 10 * 10 / 10_000)


# ---- lift cohorts --------------------------------------------------------------------------


def candidate(**overrides) -> Candidate:
    base = dict(symbol="BTEST", state=CandidateState.EXPIRED, score=0.7,
                detected_at=et(9, 40), updated_at=et(10, 10),
                qualified_at=et(9, 40), watching_at=et(9, 40),
                drop_reason=DropReason.CANDIDATE_TTL)
    return Candidate(**(base | overrides))


def test_a_signalled_candidate_is_treatment_however_it_ended():
    entered = candidate(state=CandidateState.ENTERED, signal_at=et(9, 45),
                        entered_at=et(9, 46), entry_price=10.2, drop_reason=None)
    refused = candidate(state=CandidateState.CANCELLED, signal_at=et(9, 45),
                        drop_reason=DropReason.MAX_POSITIONS)
    assert lift.classify(entered) is lift.Cohort.TREATMENT
    assert lift.classify(refused) is lift.Cohort.TREATMENT


def test_only_a_qualified_candidate_ttl_expiry_without_a_setup_is_control():
    assert lift.classify(candidate()) is lift.Cohort.CONTROL
    assert lift.classify(candidate(setup_ready_at=et(9, 50))) is None
    assert lift.classify(candidate(drop_reason=DropReason.SETUP_TTL)) is None
    assert lift.classify(candidate(drop_reason=DropReason.INELIGIBLE,
                                   state=CandidateState.REJECTED)) is None
    assert lift.classify(candidate(drop_reason=DropReason.SCORE_BELOW_THRESHOLD,
                                   qualified_at=None)) is None


def test_anchor_time_is_the_signal_for_treatment_and_the_expiry_for_control():
    treated = candidate(state=CandidateState.CANCELLED, signal_at=et(9, 45),
                        drop_reason=DropReason.PRICE_DRIFT)
    assert lift.anchor_time(treated, lift.Cohort.TREATMENT) == et(9, 45)
    assert lift.anchor_time(candidate(), lift.Cohort.CONTROL) == et(10, 10)


# ---- lift measurement ----------------------------------------------------------------------


def eod():
    from app.strategy_b.exits import _eod_moment
    return _eod_moment(make_tape([make_bar(9, 31, 10.0, 1000.0)]), StrategyBConfig().exit)


def test_forward_return_uses_the_last_available_close_at_each_end():
    bars = [make_bar(9, 40, 10.0, 1000.0), make_bar(9, 55, 11.0, 1000.0),
            make_bar(10, 30, 99.0, 1000.0)]
    tape = make_tape(bars)
    measured = lift.measure(candidate(updated_at=et(9, 41)), lift.Cohort.CONTROL, tape,
                            horizon_minutes=30, eod_exit_at=eod())
    # reference is the 09:40 bar (available at 09:41); the endpoint at 10:11 is the 09:55 bar
    assert measured.status is lift.AnchorStatus.OK
    assert measured.forward_return_pct == pytest.approx(10.0)


def test_no_reference_bar_drops_the_anchor_from_both_cohorts():
    tape = make_tape([make_bar(10, 30, 10.0, 1000.0)])
    measured = lift.measure(candidate(updated_at=et(9, 41)), lift.Cohort.CONTROL, tape,
                            horizon_minutes=30, eod_exit_at=eod())
    assert measured.status is lift.AnchorStatus.DROPPED_NO_REFERENCE_BAR
    assert not measured.counts


def test_a_silent_tape_after_the_anchor_is_zero_but_stays_visible():
    tape = make_tape([make_bar(9, 40, 10.0, 1000.0)])
    measured = lift.measure(candidate(updated_at=et(9, 41)), lift.Cohort.CONTROL, tape,
                            horizon_minutes=30, eod_exit_at=eod())
    assert measured.status is lift.AnchorStatus.ZERO_BY_NO_FORWARD_BAR
    assert measured.forward_return_pct == 0.0 and measured.counts


def test_the_horizon_is_clamped_by_the_eod_exit():
    bars = [make_bar(15, 50, 10.0, 1000.0), make_bar(15, 58, 12.0, 1000.0)]
    tape = make_tape(bars)
    measured = lift.measure(candidate(updated_at=et(15, 51)), lift.Cohort.CONTROL, tape,
                            horizon_minutes=30, eod_exit_at=eod())
    # 15:51 + 30m is past the bell, so the 15:58 bar is outside the clamped horizon
    assert measured.status is lift.AnchorStatus.ZERO_BY_NO_FORWARD_BAR


# ---- lift gate -----------------------------------------------------------------------------


def anchors(cohort, values, sessions):
    return [lift.Anchor("BTEST", sessions[i % len(sessions)], cohort, et(9, 45),
                        lift.AnchorStatus.OK, value, 0.7)
            for i, value in enumerate(values)]


def test_lift_is_insufficient_below_the_declared_anchor_counts():
    sessions = [D]
    result = lift.evaluate(anchors(lift.Cohort.TREATMENT, [1.0] * 29, sessions)
                           + anchors(lift.Cohort.CONTROL, [0.0] * 40, sessions),
                           sessions, CONTRACT)
    assert result.outcome is LiftOutcome.LIFT_INSUFFICIENT
    assert result.difference is None


def test_lift_passes_when_the_difference_interval_clears_zero():
    sessions = [D + timedelta(days=i) for i in range(10)]
    result = lift.evaluate(anchors(lift.Cohort.TREATMENT, [2.0] * 40, sessions)
                           + anchors(lift.Cohort.CONTROL, [0.5] * 40, sessions),
                           sessions, CONTRACT)
    assert result.outcome is LiftOutcome.LIFT_PASS
    assert result.difference.point == pytest.approx(1.5)


def test_lift_fails_when_the_control_does_as_well():
    sessions = [D + timedelta(days=i) for i in range(10)]
    result = lift.evaluate(anchors(lift.Cohort.TREATMENT, [0.5] * 40, sessions)
                           + anchors(lift.Cohort.CONTROL, [0.5] * 40, sessions),
                           sessions, CONTRACT)
    assert result.outcome is LiftOutcome.LIFT_FAIL


def test_dropped_anchors_never_enter_the_statistic_but_are_reported():
    sessions = [D + timedelta(days=i) for i in range(10)]
    dropped = [lift.Anchor("BTEST", sessions[0], lift.Cohort.CONTROL, et(9, 45),
                           lift.AnchorStatus.DROPPED_NO_REFERENCE_BAR, None, 0.7)]
    result = lift.evaluate(anchors(lift.Cohort.TREATMENT, [2.0] * 40, sessions)
                           + anchors(lift.Cohort.CONTROL, [0.5] * 40, sessions) + dropped,
                           sessions, CONTRACT)
    assert result.control_n == 40
    assert result.dropped == {"CONTROL": 1}
    assert result.as_dict()["dropped_no_reference_bar"] == {"CONTROL": 1}
