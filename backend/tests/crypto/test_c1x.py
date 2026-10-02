"""C1x, the premium-normalization diagnostic, against the E2 rule it is transcribed from.

The rule is E2's and this file holds the operational code to it: 5m evaluation bars only, bucket
B3 or higher, two consecutive confirmations, reset on a false bar or a missing input, trigger at
the second confirmation, executable at the next open, censored after 8 h.

And the thing that matters more than any of that: C1x is not an exit. The last group of tests
asserts that as a property of the code rather than a claim in a docstring.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.crypto.c1 import c1x as C
from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine
from app.crypto.c1.grid import Grid
from app.crypto.c1.models import C1xEvent, ShadowTrade, Signal
from app.crypto.c1.store import C1Store
from tests.crypto.c1_fixtures import basis_dipping, build, falling_oi, window_bars

LAST = window_bars() - 1
TRIGGER = LAST - 600 - ((LAST - 600) % 5) + 4


def _signal(grid: Grid, engine: C1Engine):
    return next(iter(engine.advance(grid, start_index=TRIGGER - 2, stop_index=TRIGGER + 1)))


def _fire(**kwargs):
    grid, _ = build(basis=basis_dipping([TRIGGER]), oi=falling_oi(TRIGGER), **kwargs)
    engine = C1Engine()
    signal = _signal(grid, engine)
    return grid, engine, signal


# S1 is a small negative number (the perpetual trades under spot), so a cutoff tuple that puts a
# bar at B3 has to sit *below* it. PASS gives bucket 2, FAIL gives bucket 0.
PASS_EDGES = (-9.0, -9.0, 9.0, 9.0)
FAIL_EDGES = (9.0, 9.0, 9.0, 9.0)


def boundary(passing):
    """A cutoff lookup that puts exactly `passing` bar indices at B3 and the rest at B1."""
    chosen = set(passing)
    return lambda _grid, index: PASS_EDGES if index in chosen else FAIL_EDGES


def evaluations_after(grid, signal):
    entry = grid.index_of(signal.official_entry_at_ms)
    return entry, [i for i in range(entry, entry + K.C1X_MAX_HOLD_MIN)
                   if C.is_evaluation_bar(grid.ts(i))]


# ----------------------------------------------------------------- the E2 rule
def test_the_evaluation_cadence_is_the_bar_whose_close_lands_on_a_five_minute_boundary() -> None:
    """E2's `eval5 = ((ts + 1m) % 5m) == 0`. It is the close, not the open, that has to land."""
    assert C.is_evaluation_bar(4 * 60_000) is True          # closes at 05:00
    assert C.is_evaluation_bar(9 * 60_000) is True
    for minute in (0, 1, 2, 3, 5):
        assert C.is_evaluation_bar(minute * 60_000) is False
    assert K.C1X_EVAL_CADENCE_MIN == 5


def test_a_premium_that_never_normalises_does_not_trigger() -> None:
    """The discount persists: the premium stays below the 30th percentile for the whole window."""
    grid, engine, signal = _fire()
    event = C.evaluate(signal, grid, boundary(()), [])
    assert event.status == "EXPIRED_MAX_HOLD"
    assert event.triggered_at_ms is None
    assert event.net_if_exited is None


def test_one_confirming_evaluation_is_not_enough() -> None:
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    # Exactly one evaluation bar above the boundary, the rest below it.
    event = C.evaluate(signal, grid, boundary([evaluations[3]]), [])
    assert event.status in ("NOT_TRIGGERED", "EXPIRED_MAX_HOLD", "CONFIRM_1")
    assert event.triggered_at_ms is None


def test_a_failing_bar_between_two_passing_ones_resets_the_run() -> None:
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    # One evaluation apart, so the run never reaches two.
    assert C.evaluate(signal, grid, boundary([evaluations[2], evaluations[4]]),
                      []).triggered_at_ms is None
    # The same two, now adjacent, do trigger.
    event = C.evaluate(signal, grid, boundary([evaluations[2], evaluations[3]]), [])
    assert event.status == "TRIGGERED"
    assert event.triggered_at_ms == grid.ts(evaluations[3]) + K.MINUTE_MS


def test_a_missing_premium_resets_the_run_rather_than_passing_it() -> None:
    """E2 section 2: a false condition *or* a NaN input sets the count back to zero."""
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    passing = {evaluations[2], evaluations[3], evaluations[4]}
    # The middle bar has no bucket at all. Were a missing input treated as neutral, the run would
    # reach two across it; E2 resets instead, so it must not trigger.
    def cutoffs(_grid, index):
        if index == evaluations[3]:
            return None
        return PASS_EDGES if index in passing else FAIL_EDGES
    assert C.evaluate(signal, grid, cutoffs, []).triggered_at_ms is None


def test_two_consecutive_evaluations_trigger_at_the_second_one() -> None:
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    first, second = evaluations[1], evaluations[2]
    event = C.evaluate(signal, grid, boundary([first, second]), [])
    assert event.status == "TRIGGERED"
    assert event.confirmation_count == K.C1X_CONFIRMATIONS_REQUIRED
    assert event.triggered_at_ms == grid.ts(second) + K.MINUTE_MS
    assert event.observed_price == grid.closes[second]
    # E2 prices the candidate at the next executable open, never at the confirming bar's close.
    assert event.executable_at_ms == grid.ts(second + 1)
    assert event.hypothetical_exit_price == grid.opens[second + 1]


def test_the_bucket_boundary_is_b3_the_thirtieth_percentile() -> None:
    assert K.C1X_MIN_BUCKET == 2
    assert K.BUCKET_QUANTILES[K.C1X_MIN_BUCKET - 1] == 0.30


def test_the_window_is_capped_at_eight_hours_and_then_censored() -> None:
    grid, engine, signal = _fire()
    event = C.evaluate(signal, grid, boundary(()), [])
    assert K.C1X_MAX_HOLD_MIN == 480
    assert event.status == "EXPIRED_MAX_HOLD"
    assert event.censored_by_max_hold is True
    assert event.triggered_at_ms is None


# ----------------------------------------------------------------- accounting
def test_the_hypothetical_result_uses_the_same_cost_as_the_benchmark() -> None:
    """The pairing is only meaningful if both sides are priced the same way."""
    grid, engine, signal = _fire()
    entry, evaluations = evaluations_after(grid, signal)
    pair = (evaluations[1], evaluations[2])
    event = C.evaluate(signal, grid, boundary(pair), [])
    ratio = event.hypothetical_exit_price / event.entry_price
    assert event.gross_if_exited == ratio - 1
    assert event.cost_if_exited == K.TAKER_RATE * (1 + ratio) + K.MICRO_BASE_FRAC
    assert event.net_if_exited == event.gross_if_exited - event.cost_if_exited
    assert event.holding_minutes == pair[1] + 1 - entry


def test_funding_settled_while_hypothetically_held_is_charged() -> None:
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    pair = (evaluations[1], evaluations[2])
    schedule = [(signal.official_entry_at_ms + 60_000, 0.0004)]
    event = C.evaluate(signal, grid, boundary(pair), schedule)
    assert event.funding_if_exited == 0.0004
    assert event.cost_if_exited == pytest.approx(
        K.TAKER_RATE * (1 + event.hypothetical_exit_price / event.entry_price)
        + K.MICRO_BASE_FRAC + 0.0004)


# ----------------------------------------------------------------- E0 pairing
def test_the_delta_is_pending_until_the_four_hour_benchmark_finishes() -> None:
    event = C1xEvent(signal_id="C1-LONG-1", status="TRIGGERED", net_if_exited=0.01)
    C.pair_with_e0(event, ShadowTrade(signal_id="C1-LONG-1", status="ACTIVE"))
    assert event.e0_status == "PENDING"
    assert event.delta_net is None and event.e0_net is None
    C.pair_with_e0(event, None)
    assert event.e0_status == "PENDING"


def test_the_delta_is_the_diagnostic_minus_the_benchmark_once_settled() -> None:
    event = C1xEvent(signal_id="C1-LONG-1", status="TRIGGERED", net_if_exited=0.0082)
    settled = ShadowTrade(signal_id="C1-LONG-1", status="SETTLED", net_return=0.0047)
    C.pair_with_e0(event, settled)
    assert event.e0_status == "SETTLED"
    assert event.e0_net == 0.0047
    assert event.delta_net == pytest.approx(0.0035)
    assert K.E0_HORIZON_MIN == 240


def test_the_linkage_to_its_c1_signal_is_carried_and_unique(tmp_path) -> None:
    grid, engine, signal = _fire()
    event = C.evaluate(signal, grid, engine.cutoffs.get, [])
    assert event.signal_id == signal.signal_id
    assert event.parent_signal_id == signal.signal_id
    assert event.c1x_event_id == f"C1X-{signal.signal_id}"
    store = C1Store(tmp_path)
    store.append_c1x([event, event, event])
    assert len(store.c1x()) == 1                    # at most one diagnostic per signal, ever


# ----------------------------------------------------------------- recovery
def test_a_restart_reloads_the_trigger_instead_of_making_a_second_one(tmp_path) -> None:
    """The failure this guards against: a restart re-deriving the diagnostic and publishing a
    new trigger instant for a signal that already had one."""
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    event = C.evaluate(signal, grid, boundary([evaluations[1], evaluations[2]]), [])
    store = C1Store(tmp_path)
    store.append_c1x([event])

    reloaded = store.c1x()[signal.signal_id]
    assert reloaded.status == "TRIGGERED"
    assert reloaded.triggered_at_ms == event.triggered_at_ms
    assert reloaded.c1x_event_id == event.c1x_event_id
    assert reloaded.parent_signal_id == signal.signal_id
    assert reloaded.to_json() == event.to_json()


def test_recomputing_the_same_bars_gives_the_same_trigger() -> None:
    grid, engine, signal = _fire()
    _, evaluations = evaluations_after(grid, signal)
    cutoffs = boundary([evaluations[1], evaluations[2]])
    first = C.evaluate(signal, grid, cutoffs, [])
    again = C.evaluate(signal, grid, cutoffs, [])
    assert first.to_json() == again.to_json()


# ----------------------------------------------------------------- not an exit
def test_the_record_says_it_is_not_an_exit_and_cannot_be_loaded_as_one() -> None:
    event = C1xEvent(signal_id="C1-LONG-1")
    assert event.is_exit is False
    assert event.meaning == "PREMIUM_NORMALIZATION_DIAGNOSTIC"
    assert K.C1X_IS_EXIT is False
    # Even a file that claims otherwise is loaded as a diagnostic.
    forged = event.to_json() | {"is_exit": True}
    assert C1xEvent.from_json(forged).is_exit is False


def test_the_diagnostic_module_reaches_no_order_path() -> None:
    backend = Path(__file__).resolve().parents[2]
    tree = ast.parse((backend / "app/crypto/c1/c1x.py").read_text(), filename="c1x.py")
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(("." * node.level) + (node.module or ""))
    forbidden = ("live", "orders", "arm", "leverage", "exit_guard", "sizing", "paper")
    assert [n for n in names if any(part in forbidden for part in n.replace("..", ".").split("."))] == []


def test_no_exit_vocabulary_leaks_into_the_records() -> None:
    """The word matters: a field called `exit_price` on a diagnostic would be read as an exit.
    Everything priced here is named for what it is - what *would* have happened."""
    body = C1xEvent(signal_id="C1-LONG-1").to_json()
    for field in ("exit_price", "closed_at", "sell_price", "stop_price"):
        assert field not in body
    assert "hypothetical_exit_price" in body
    assert {"gross_if_exited", "cost_if_exited", "net_if_exited"} <= set(body)


def test_the_research_verdict_is_carried_and_says_inconclusive() -> None:
    """C1x failed E2's winner-preservation gate: it cuts more than half the large winners short.
    Anything that quotes C1x has to be able to quote that too."""
    assert K.C1X_RESEARCH["status"] == "INCONCLUSIVE"
    assert K.C1X_RESEARCH["failed_gate"] == "G2_WINNER_PRESERVATION"
    assert K.C1X_RESEARCH["winner_preservation"] == 0.488
