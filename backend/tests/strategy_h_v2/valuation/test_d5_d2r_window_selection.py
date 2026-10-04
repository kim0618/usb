"""H-V2-D5-D2R: the recent-regime window-selection repair.

Pure tests throughout. Every panel is constructed by hand from a stated shape - a cancelling round
trip, a monotone collapse, a flat panel, a strong recent expansion - so what is asserted is the
selection contract rather than the state of `data/runtime/`.

Two tests carry most of the weight. `test_a_cancelling_round_trip_is_detected_under_d2r_and_missed_under_v1`
is the VRRM shape and the reason this step exists. `test_the_override_is_symmetric_in_direction` is the
proof that §8 holds: the mirrored panel, where the recent regime is MORE expensive than the full panel,
must select the recent window on identical terms - so the rule cannot be reading the upside.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.backtest.strategy_h_v2.valuation.fair_value import (
    MIN_WINDOW_OBSERVATIONS,
    RECENT_REGIME_CONFLICT_RATIO,
    RECENT_REGIME_EVIDENCE_IS_NOT_ONLY_THE_FULL_PANEL,
    RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT,
    TREND_STRONG_RHO,
    VALUATION_CONFLICT_RATIO,
    MultipleObservation,
    PanelWindow,
    TrendClass,
    WindowSelectionContract,
    base_multiple_ratio,
    select_contract_window,
    window_stats,
)

V1 = WindowSelectionContract.D5_D2_V1
D2R = WindowSelectionContract.D5_D2R_V1

START = date(2024, 9, 17)


def observations(multiples: list[float]) -> list[MultipleObservation]:
    """A panel whose denominator moves every 63 sessions, so no window fails `denominator_moved`."""
    out: list[MultipleObservation] = []
    for i, multiple in enumerate(multiples):
        out.append(MultipleObservation(
            session=START + timedelta(days=i),
            multiple=multiple,
            numerator_value=multiple * 100.0,
            denominator_value=100.0,
            denominator_period_end=date(2024, 6, 30) + timedelta(days=90 * (i // 63))))
    return out


def windows_for(multiples: list[float], *, method: str = "EV/EBIT") -> dict:
    return {w: window_stats(observations(multiples), method=method, window=w,
                            current_multiple=multiples[-1]) for w in PanelWindow}


# -------------------------------------------------------------------------------------------------
# The shapes
# -------------------------------------------------------------------------------------------------

def round_trip(peak: float = 36.0, start: float = 27.0, end: float = 11.0,
               n: int = 474, fall_sessions: int = 111, plateau: int = 126) -> list[float]:
    """VRRM's shape: a rise into a peak, a sharper collapse, then a plateau at the new level.

    The plateau matters and is not decoration. It is why RECENT_6M is itself RANGE_BOUND while
    RECENT_12M is TRENDING_STRONG, which is the real VRRM configuration: a window that sits entirely
    inside the new regime has no strong trend precisely because the transition is behind it. So the
    window that supplies the evidence and the window that governs are different windows, and a rule
    that required the governing window to be strongly trending would find nothing here.
    """
    rise_sessions = n - fall_sessions - plateau
    rise = [start + (peak - start) * i / (rise_sessions - 1) for i in range(rise_sessions)]
    fall = [peak + (end - peak) * i / (fall_sessions - 1) for i in range(fall_sessions)]
    flat = [end + (i % 5) * 0.02 for i in range(plateau)]
    return rise + fall + flat


def monotone_collapse(start: float = 33.0, end: float = 9.7, n: int = 474) -> list[float]:
    """ADBE's shape: one direction across the whole panel, which V1 already catches."""
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def flat_with_noise(level: float = 13.0, n: int = 474) -> list[float]:
    """A level the issuer stayed at. Nothing should shorten the window."""
    return [level + (i % 7) * 0.02 for i in range(n)]


def recent_shift_only(level: float = 13.0, shifted: float = 30.0, n: int = 474,
                      recent: int = 120) -> list[float]:
    """A flat panel whose last stretch steps up strongly: the mirrored VRRM case."""
    body = [level + (i % 7) * 0.02 for i in range(n - recent)]
    tail = [level + (shifted - level) * i / (recent - 1) for i in range(recent)]
    return body + tail


# -------------------------------------------------------------------------------------------------
# The repair
# -------------------------------------------------------------------------------------------------

def test_the_round_trip_really_does_cancel():
    """The premise, measured rather than assumed. Without this the next test proves nothing."""
    windows = windows_for(round_trip())
    full = windows[PanelWindow.FULL_2Y]
    twelve = windows[PanelWindow.RECENT_12M]

    assert abs(full.rho) < TREND_STRONG_RHO, full.rho
    assert full.trend is not TrendClass.TRENDING_STRONG
    assert abs(twelve.rho) >= TREND_STRONG_RHO, twelve.rho
    assert twelve.trend is TrendClass.TRENDING_STRONG

    # And the window that will GOVERN is not the one that supplies the evidence.
    assert windows[PanelWindow.RECENT_6M].trend is not TrendClass.TRENDING_STRONG


def test_a_cancelling_round_trip_is_detected_under_d2r_and_missed_under_v1():
    """VRRM. The one finding this step exists to fix, asserted as a difference between contracts."""
    windows = windows_for(round_trip())

    old_window, old_why = select_contract_window(windows, contract=V1)
    new_window, new_why = select_contract_window(windows, contract=D2R)

    assert old_window is PanelWindow.FULL_2Y
    assert "not TRENDING_STRONG" in old_why
    assert new_window is PanelWindow.RECENT_6M
    assert "cancels a rise against a fall" in new_why
    assert "RECENT_12M" in new_why, "the reason must name the window that supplied the evidence"


def test_a_monotone_collapse_still_takes_the_shortest_window_under_both_contracts():
    """ADBE regression. §11: returning to the dead historical regime is a FAIL."""
    windows = windows_for(monotone_collapse())

    old_window, _ = select_contract_window(windows, contract=V1)
    new_window, new_why = select_contract_window(windows, contract=D2R)

    assert old_window is PanelWindow.RECENT_6M
    assert new_window is PanelWindow.RECENT_6M
    assert "different regime" in new_why


def test_a_flat_panel_keeps_the_full_window_under_both_contracts():
    """The repair must not shorten a window just because it can."""
    windows = windows_for(flat_with_noise())

    assert select_contract_window(windows, contract=V1)[0] is PanelWindow.FULL_2Y
    new_window, new_why = select_contract_window(windows, contract=D2R)
    assert new_window is PanelWindow.FULL_2Y
    assert "no contract-eligible window trends strongly" in new_why


def test_the_override_is_symmetric_in_direction():
    """§8's proof. A recent regime that is MORE expensive overrides on identical terms.

    If the rule were reading the upside it would prefer whichever window raised the target. Here the
    mirrored panel - flat, then a strong recent EXPANSION - selects the recent window exactly as the
    collapsing panel does, and the selected window's Base multiple is HIGHER than the full panel's.
    """
    windows = windows_for(recent_shift_only())
    full = windows[PanelWindow.FULL_2Y]

    new_window, new_why = select_contract_window(windows, contract=D2R)

    assert select_contract_window(windows, contract=V1)[0] is PanelWindow.FULL_2Y
    assert new_window is PanelWindow.RECENT_6M
    assert "disagrees with FULL_2Y's" in new_why
    assert windows[new_window].base.multiple > full.base.multiple, (
        "the mirrored case must raise the target, which is what makes the rule direction-blind")


def test_a_strong_recent_window_that_agrees_about_the_level_does_not_override():
    """`RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT`: drift is not by itself a different level.

    A recent window can be strongly monotone and still be centred where the full panel is, because a
    rise inside the recent window is a round trip from the full panel's point of view. SCCO and COLL
    are both this case on real data.
    """
    # Flat body, then a recent stretch that oscillates up strongly but stays around the same level.
    n, recent = 474, 130
    body = [13.0 + (i % 5) * 0.02 for i in range(n - recent)]
    tail = [12.2 + 1.6 * i / (recent - 1) for i in range(recent)]
    windows = windows_for(body + tail)

    shortest = min((w for w in PanelWindow if windows[w].contract_eligible),
                   key=lambda w: windows[w].n)
    ratio = base_multiple_ratio(windows[PanelWindow.FULL_2Y], windows[shortest])

    assert windows[PanelWindow.RECENT_6M].trend is TrendClass.TRENDING_STRONG
    assert windows[PanelWindow.FULL_2Y].trend is not TrendClass.TRENDING_STRONG
    assert ratio is not None and ratio <= RECENT_REGIME_CONFLICT_RATIO, ratio

    chosen, why = select_contract_window(windows, contract=D2R)
    assert chosen is PanelWindow.FULL_2Y
    assert "without leaving the panel's level" in why


# -------------------------------------------------------------------------------------------------
# Determinism, priority and totality
# -------------------------------------------------------------------------------------------------

def test_selection_is_deterministic_under_both_contracts():
    """§14 E. Same inputs, same window, every time - including the reason string."""
    for shape in (round_trip(), monotone_collapse(), flat_with_noise(), recent_shift_only()):
        windows = windows_for(shape)
        for contract in (V1, D2R):
            first = select_contract_window(windows, contract=contract)
            for _ in range(3):
                assert select_contract_window(windows, contract=contract) == first


def test_the_governing_window_is_the_shortest_eligible_one_not_the_strong_one():
    """§7. The evidence may come from RECENT_12M and the window that governs is still the shortest
    contract-eligible one, which is the priority V1 already used and D2R does not change."""
    windows = windows_for(round_trip())

    assert windows[PanelWindow.RECENT_12M].trend is TrendClass.TRENDING_STRONG
    assert windows[PanelWindow.RECENT_6M].trend is not TrendClass.TRENDING_STRONG
    assert select_contract_window(windows, contract=D2R)[0] is PanelWindow.RECENT_6M


def test_a_panel_shorter_than_the_shortest_window_collapses_to_one_window():
    """`window_observations` slices `ordered[-keep:]`, so on a panel of 65 sessions all three windows
    are the SAME 65 observations. Nothing is shortened because there is nothing shorter, and the
    tie in `n` resolves by enum order rather than arbitrarily."""
    n = MIN_WINDOW_OBSERVATIONS + 5
    windows = windows_for(monotone_collapse(n=n))

    assert {windows[w].n for w in PanelWindow} == {n}
    assert all(windows[w].contract_eligible for w in PanelWindow)
    assert base_multiple_ratio(windows[PanelWindow.FULL_2Y],
                               windows[PanelWindow.RECENT_6M]) == 1.0
    for contract in (V1, D2R):
        chosen, _ = select_contract_window(windows, contract=contract)
        assert chosen is PanelWindow.FULL_2Y


def test_full_2y_is_eligible_whenever_any_window_is():
    """An invariant worth stating, because it makes one branch of the rule provably defensive.

    The windows are NESTED - RECENT_6M is a suffix of RECENT_12M is a suffix of FULL_2Y - so FULL_2Y
    has at least as many observations and at least as many distinct denominator periods as any
    shorter window. Both eligibility clauses are monotone in those two quantities, so FULL_2Y cannot
    be ineligible while a shorter window is eligible. The rule's `not full.contract_eligible` branch
    is therefore unreachable for any panel this repository can build, and it is kept as a guard
    rather than relied on - if `window_observations` ever became a calendar slice instead of a
    session slice, nesting would no longer hold.
    """
    shapes = (round_trip(), monotone_collapse(), flat_with_noise(), recent_shift_only(),
              monotone_collapse(n=MIN_WINDOW_OBSERVATIONS + 5), [10.0, 11.0, 12.0], [5.0] * 300)
    for shape in shapes:
        windows = windows_for(shape)
        if any(windows[w].contract_eligible for w in PanelWindow):
            assert windows[PanelWindow.FULL_2Y].contract_eligible, shape[:3]
        for shorter in (PanelWindow.RECENT_12M, PanelWindow.RECENT_6M):
            assert (windows[PanelWindow.FULL_2Y].n >= windows[shorter].n)
            assert (windows[PanelWindow.FULL_2Y].distinct_denominator_periods
                    >= windows[shorter].distinct_denominator_periods)


def test_no_eligible_window_returns_none_under_both_contracts():
    windows = windows_for([10.0, 11.0, 12.0])

    for contract in (V1, D2R):
        chosen, why = select_contract_window(windows, contract=contract)
        assert chosen is None
        assert "no window is contract-eligible" in why


def test_an_unmeasurable_drift_keeps_the_full_window_under_both_contracts():
    """A constant multiple makes rho None and the trend UNDETERMINED, which is not TRENDING_STRONG.
    Formatting that None as a signed float raised TypeError before V1 guarded it, and D2R inherits
    the guard rather than reimplementing it."""
    windows = windows_for([5.0] * 300)

    assert windows[PanelWindow.FULL_2Y].trend is TrendClass.UNDETERMINED
    assert windows[PanelWindow.FULL_2Y].rho is None
    for contract in (V1, D2R):
        chosen, why = select_contract_window(windows, contract=contract)
        assert chosen is PanelWindow.FULL_2Y
        assert "not measurable" in why


def test_a_price_only_window_is_refused_by_both_contracts():
    """`PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE`: one denominator period means the range is the
    price range rescaled, and no selection rule may promote such a window."""
    multiples = flat_with_noise(n=200)
    obs = [MultipleObservation(
        session=START + timedelta(days=i), multiple=m, numerator_value=m * 100.0,
        denominator_value=100.0, denominator_period_end=date(2024, 6, 30))
        for i, m in enumerate(multiples)]
    windows = {w: window_stats(obs, method="EV/EBIT", window=w, current_multiple=multiples[-1])
               for w in PanelWindow}

    assert not any(windows[w].contract_eligible for w in PanelWindow)
    for contract in (V1, D2R):
        assert select_contract_window(windows, contract=contract)[0] is None


# -------------------------------------------------------------------------------------------------
# Nothing was retuned
# -------------------------------------------------------------------------------------------------

def test_the_window_set_and_the_trend_threshold_are_unchanged():
    """§4 and §5. The repair changed which windows are asked, not the windows or the threshold."""
    assert [w.value for w in PanelWindow] == ["FULL_2Y", "RECENT_12M", "RECENT_6M"]
    assert TREND_STRONG_RHO == 0.7
    assert MIN_WINDOW_OBSERVATIONS == 60


def test_the_conflict_ratio_is_the_pre_registered_constant_and_not_a_new_one():
    """§5. D2R reuses D5-D2's conflict ratio rather than introducing a second threshold."""
    assert RECENT_REGIME_CONFLICT_RATIO is VALUATION_CONFLICT_RATIO
    assert RECENT_REGIME_CONFLICT_RATIO == 1.5


def test_the_contract_argument_is_required():
    """A call site that did not state its contract would be a silent choice between two rules."""
    windows = windows_for(flat_with_noise())

    with pytest.raises(TypeError):
        select_contract_window(windows)


def test_v1_is_preserved_exactly_so_published_reports_reproduce():
    """`contract_window_reason` is a field in the stored D5-D2 and D6 reports, so V1's strings are
    part of those artifacts and not just prose."""
    windows = windows_for(flat_with_noise())
    full = windows[PanelWindow.FULL_2Y]
    assert select_contract_window(windows, contract=V1)[1] == (
        f"FULL_2Y trend is RANGE_BOUND (rho {full.rho:+.3f}), not TRENDING_STRONG, so the whole "
        f"panel is the observation range")

    window, why = select_contract_window(windows_for(monotone_collapse()), contract=V1)
    assert window is PanelWindow.RECENT_6M
    assert why == ("FULL_2Y trend is TRENDING_STRONG (rho -1.000): the early panel is a different "
                   "regime from the late panel, so the shortest contract-eligible window "
                   "(RECENT_6M, n=126) governs rather than the median of a transition")


def test_both_published_steps_are_pinned_to_v1():
    """A step that published a report keeps the rule it published under, stated at its call site."""
    import inspect

    from app.dev.run_strategy_h_v2_d5_d2 import WINDOW_CONTRACT as D5_D2_PIN, value_issuer
    from app.dev.run_strategy_h_v2_d6 import WINDOW_CONTRACT as D6_PIN

    assert D5_D2_PIN is V1
    assert D6_PIN is V1
    assert inspect.signature(value_issuer).parameters["window_contract"].default is V1


def test_the_d2r_runner_is_the_only_one_that_uses_the_repaired_rule():
    from app.dev.run_strategy_h_v2_d5_d2r import NEW, OLD

    assert OLD is V1
    assert NEW is D2R


# -------------------------------------------------------------------------------------------------
# The ratio helper
# -------------------------------------------------------------------------------------------------

def test_the_ratio_is_symmetric_and_refuses_what_it_cannot_measure():
    class _W:
        def __init__(self, multiple):
            self.base = None if multiple is None else type("_P", (), {"multiple": multiple})()

    assert base_multiple_ratio(_W(27.0), _W(12.0)) == base_multiple_ratio(_W(12.0), _W(27.0))
    assert base_multiple_ratio(_W(27.0), _W(12.0)) == pytest.approx(2.25)
    assert base_multiple_ratio(_W(None), _W(12.0)) is None
    assert base_multiple_ratio(_W(0.0), _W(12.0)) is None, "a ratio against zero is not agreement"
    assert base_multiple_ratio(_W(-3.0), _W(12.0)) is None


def test_the_contract_prose_names_the_defect_and_the_discipline():
    assert "MONOTONICITY" in RECENT_REGIME_EVIDENCE_IS_NOT_ONLY_THE_FULL_PANEL
    assert "+0.830" in RECENT_REGIME_EVIDENCE_IS_NOT_ONLY_THE_FULL_PANEL
    assert "symmetric" in RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT
    assert "never between their fair" in RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT
