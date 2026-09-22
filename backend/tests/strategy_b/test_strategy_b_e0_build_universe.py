"""The universe builder on a synthetic membership matrix: no store, no Drive.

What these pin is the reconciliation: the artifact is derived from the same S(D) matrix as the
collector's plan, so members plus exclusions must equal the planner's symbol set and every fetch
range must be identical. A symbol count is never compared with a request count.
"""

from datetime import date, timedelta

import numpy as np
import pytest

from app.backtest.strategy_b_e0.build_universe import ReconciliationFailed, derive, reconcile, write
from app.backtest.strategy_b_e0.universe import load_universe
from app.dev.historical_v2 import minute_window_ranges

SESSIONS = [date(2026, 4, 1) + timedelta(days=i) for i in range(40)]
START, END = SESSIONS[25], SESSIONS[-1]
WARMUP = 5
NAMES = ["AAA", "BBB", "CON", "EARLY", "LATE"]


def no_reserved(symbol):
    return None


def dos_reserved(symbol):
    return symbol if symbol in {"CON", "PRN", "AUX", "NUL"} else None


def matrix() -> np.ndarray:
    m = np.zeros((len(SESSIONS), len(NAMES)), dtype=bool)
    m[25:40, 0] = True          # AAA: in scope through the whole window
    m[30:33, 1] = True          # BBB: a short run inside the window
    m[27:29, 2] = True          # CON: in scope, but its name cannot be stored
    m[5:20, 3] = True           # EARLY: only before the window, so never a member
    m[39, 4] = True             # LATE: enters on the last session only
    return m


def test_members_are_symbols_in_scope_inside_the_window_only():
    derived = derive(matrix(), NAMES, SESSIONS, scope_start=START, scope_end=END,
                     warmup=WARMUP, reserved=dos_reserved)
    assert [item.symbol for item in derived.members] == ["AAA", "BBB", "LATE"]
    assert "EARLY" not in {item.symbol for item in derived.members}
    assert set(derived.exclusions) == {"CON"}


def test_scope_sessions_are_exactly_the_membership_rows():
    derived = derive(matrix(), NAMES, SESSIONS, scope_start=START, scope_end=END,
                     warmup=WARMUP, reserved=no_reserved)
    bbb = next(item for item in derived.members if item.symbol == "BBB")
    assert bbb.scope_sessions == tuple(SESSIONS[30:33])


def test_fetch_range_carries_the_warmup_before_the_first_window_session():
    derived = derive(matrix(), NAMES, SESSIONS, scope_start=START, scope_end=END,
                     warmup=WARMUP, reserved=no_reserved)
    late = next(item for item in derived.members if item.symbol == "LATE")
    assert late.fetch_start == SESSIONS[39 - WARMUP] and late.fetch_end == SESSIONS[39]


def test_a_symbol_entering_late_is_a_member_but_only_on_its_own_sessions():
    """The union looks forward as a set; the per-session list is what keeps it PIT."""
    derived = derive(matrix(), NAMES, SESSIONS, scope_start=START, scope_end=END,
                     warmup=WARMUP, reserved=no_reserved)
    late = next(item for item in derived.members if item.symbol == "LATE")
    assert late.scope_sessions == (SESSIONS[39],)


def test_reconciliation_matches_the_collectors_own_planner():
    m = matrix()
    derived = derive(m, NAMES, SESSIONS, scope_start=START, scope_end=END, warmup=WARMUP,
                     reserved=dos_reserved)
    planner = minute_window_ranges(m, NAMES, SESSIONS, START, WARMUP)
    result = reconcile(derived, planner)
    assert result["planner_symbols"] == 4                     # AAA BBB CON LATE
    assert result["artifact_members"] == 3 and result["artifact_exclusions"] == 1
    assert result["members_plus_exclusions_equals_planner"] is True


def test_reconciliation_refuses_a_dropped_symbol():
    m = matrix()
    derived = derive(m, NAMES, SESSIONS, scope_start=START, scope_end=END, warmup=WARMUP,
                     reserved=dos_reserved)
    planner = dict(minute_window_ranges(m, NAMES, SESSIONS, START, WARMUP))
    planner["GHOST"] = (SESSIONS[20], SESSIONS[30])
    with pytest.raises(ReconciliationFailed, match="only in plan"):
        reconcile(derived, planner)


def test_reconciliation_refuses_a_different_fetch_range():
    m = matrix()
    derived = derive(m, NAMES, SESSIONS, scope_start=START, scope_end=END, warmup=WARMUP,
                     reserved=dos_reserved)
    planner = dict(minute_window_ranges(m, NAMES, SESSIONS, START, WARMUP))
    planner["AAA"] = (SESSIONS[0], SESSIONS[-1])
    with pytest.raises(ReconciliationFailed, match="range mismatch"):
        reconcile(derived, planner)


def test_the_written_artifact_loads_and_counts_itself(tmp_path):
    derived = derive(matrix(), NAMES, SESSIONS, scope_start=START, scope_end=END,
                     warmup=WARMUP, reserved=dos_reserved)
    universe = write(tmp_path / "u.json", derived, scope_start=START, scope_end=END,
                     provenance={"source_universe_digest": "abc"})
    loaded = load_universe(universe.path)
    assert loaded.symbol_count == 3
    assert loaded.exclusions.keys() == {"CON"}
    assert loaded.symbols_for(SESSIONS[39]) == ("AAA", "LATE")
    assert loaded.built_from["source_universe_digest"] == "abc"


def test_same_matrix_gives_the_same_bytes(tmp_path):
    kwargs = dict(scope_start=START, scope_end=END, warmup=WARMUP, reserved=dos_reserved)
    first = write(tmp_path / "a.json", derive(matrix(), NAMES, SESSIONS, **kwargs),
                  scope_start=START, scope_end=END, provenance={"x": 1})
    second = write(tmp_path / "b.json", derive(matrix(), NAMES, SESSIONS, **kwargs),
                   scope_start=START, scope_end=END, provenance={"x": 1})
    assert first.sha256 == second.sha256


def test_a_shape_mismatch_is_refused():
    with pytest.raises(ValueError, match="does not match"):
        derive(np.zeros((3, 2), dtype=bool), NAMES, SESSIONS, scope_start=START,
               scope_end=END, warmup=WARMUP, reserved=no_reserved)
