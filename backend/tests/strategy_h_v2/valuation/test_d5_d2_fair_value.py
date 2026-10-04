"""H-V2-D5-D2: the fair value framework - percentiles, chains, scenarios, targets, abstention.

Pure tests throughout. Every observation is built by hand, so what is asserted is the contract
rather than the state of `data/runtime/`. The arithmetic in each assertion is computed independently
of the module - written out in the test - rather than by calling the module twice and comparing it
to itself.

The numbers are the measured ones from the pilot: ADBE's 250.50 close, 397,500,000 PIT shares,
10,280,000,000 of TTM free cash flow and -117,000,000 of net debt; WBD's 28.07 close,
2,510,703,314 shares, 36,115,000,000 of TTM revenue and 28,654,000,000 of net debt. A test that
stops holding points at the real row it came from.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.backtest.strategy_h_v2.valuation.fair_value import (
    BASE_PERCENTILE,
    BEAR_PERCENTILE,
    BULL_PERCENTILE,
    FAIR_VALUE_CONTRACT_VERSION,
    MIN_DISTINCT_DENOMINATOR_PERIODS,
    MIN_WINDOW_OBSERVATIONS,
    NEVER_PRODUCED,
    NO_RETURN_INPUT_HERE,
    VALUATION_CONFLICT_RATIO,
    FairValueRange,
    MetricChain,
    MultipleObservation,
    PanelWindow,
    ReconciliationStatus,
    Scenario,
    ScenarioRefusal,
    ScenarioValue,
    TrendClass,
    ValuationConfidence,
    identity_residual,
    methods_are_correlated,
    metric_chain,
    nearest_rank_percentile,
    reconcile,
    scenario_value,
    spearman_rho,
    target_prices,
    trend_class,
    valuation_confidence,
    window_observations,
    window_stats,
)

ADBE_PRICE = 250.50
ADBE_SHARES = 397_500_000.0
ADBE_FCF = 10_280_000_000.0
ADBE_NET_DEBT = -117_000_000.0
ADBE_P_FCF = 9.686162451361868

WBD_PRICE = 28.07
WBD_SHARES = 2_510_703_314.0
WBD_REVENUE = 36_115_000_000.0
WBD_NET_DEBT = 28_654_000_000.0
WBD_EV_SALES = 2.7448274130964974

START = date(2024, 9, 17)


def observations(multiples, *, period_ends=None, start=START):
    """One observation per consecutive day. Period ends cycle quarterly unless given."""
    rows = []
    for i, m in enumerate(multiples):
        if period_ends is None:
            end = date(2026, 6, 30) if i % 2 else date(2026, 3, 31)
        else:
            end = period_ends[i]
        rows.append(MultipleObservation(session=start + timedelta(days=i), multiple=m,
                                        numerator_value=m * 100.0, denominator_value=100.0,
                                        denominator_period_end=end))
    return rows


# -------------------------------------------------------------------------------------------------
# Nearest-rank percentiles are observations, not interpolations
# -------------------------------------------------------------------------------------------------

def test_percentile_is_an_element_of_the_series_never_an_interpolation():
    """D5-D0 §K requires an OBSERVED multiple. An interpolated P50 is a number nobody paid."""
    rows = observations([10.0, 20.0, 30.0, 40.0])
    values = {o.multiple for o in rows}
    for percentile in (BEAR_PERCENTILE, BASE_PERCENTILE, BULL_PERCENTILE, 1, 33, 66, 100):
        chosen = nearest_rank_percentile(rows, percentile)
        assert chosen is not None
        assert chosen.multiple in values, f"p{percentile} returned a value nobody observed"


def test_percentile_median_of_an_even_series_is_not_the_mean_of_the_middle_two():
    """The interpolating default would return 25.0 here. Nearest-rank returns an observation."""
    rows = observations([10.0, 20.0, 30.0, 40.0])
    median = nearest_rank_percentile(rows, 50)
    assert median is not None
    assert median.multiple == 20.0
    assert median.multiple != 25.0


def test_percentile_carries_the_session_it_was_observed_on():
    rows = observations([30.0, 10.0, 20.0])
    median = nearest_rank_percentile(rows, 50)
    assert median is not None
    assert median.multiple == 20.0
    assert median.session == rows[2].session


def test_percentile_ties_break_deterministically_by_session():
    """Two sessions at one multiple are the same number and different provenance, and a target
    price whose provenance moved between runs would be a drift the audit should catch."""
    rows = observations([5.0, 5.0, 5.0, 9.0])
    first = nearest_rank_percentile(rows, 50)
    again = nearest_rank_percentile(list(reversed(rows)), 50)
    assert first is not None and again is not None
    assert first.session == again.session


def test_percentile_of_an_empty_series_is_none_not_zero():
    assert nearest_rank_percentile([], 50) is None


def test_percentile_out_of_range_raises():
    rows = observations([1.0, 2.0])
    for bad in (0, -10, 101):
        with pytest.raises(ValueError):
            nearest_rank_percentile(rows, bad)


def test_a_non_positive_observation_cannot_be_constructed():
    """D5-D1 refuses negative multiples at the source; the panel refuses to carry one."""
    for bad in (0.0, -3.0):
        with pytest.raises(AssertionError):
            MultipleObservation(session=START, multiple=bad, numerator_value=1.0,
                                denominator_value=1.0, denominator_period_end=None)


# -------------------------------------------------------------------------------------------------
# Window eligibility: the price-only range and the short window
# -------------------------------------------------------------------------------------------------

def test_window_slices_in_sessions_counted_back_from_the_newest():
    rows = observations([float(i + 1) for i in range(400)])
    recent = window_observations(rows, PanelWindow.RECENT_12M)
    assert len(recent) == 252
    assert recent[-1].session == rows[-1].session
    assert window_observations(rows, PanelWindow.FULL_2Y) == tuple(rows)


def test_single_denominator_period_is_a_price_only_range_and_may_not_govern():
    """PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE: one denominator means the range is the price."""
    one_period = [date(2026, 6, 30)] * 300
    stats = window_stats(observations([10.0 + i * 0.01 for i in range(300)],
                                      period_ends=one_period),
                         method="P/FCF", window=PanelWindow.FULL_2Y, current_multiple=11.0)
    assert stats.distinct_denominator_periods == 1
    assert stats.denominator_moved is False
    assert stats.contract_eligible is False
    assert "same fundamental" in stats.ineligible_reason


def test_two_denominator_periods_clear_the_price_only_test():
    stats = window_stats(observations([10.0 + i * 0.01 for i in range(300)]),
                         method="P/FCF", window=PanelWindow.FULL_2Y, current_multiple=11.0)
    assert stats.distinct_denominator_periods >= MIN_DISTINCT_DENOMINATOR_PERIODS
    assert stats.denominator_moved is True
    assert stats.contract_eligible is True


def test_a_window_under_the_observation_floor_is_ineligible():
    stats = window_stats(observations([10.0 + i for i in range(MIN_WINDOW_OBSERVATIONS - 1)]),
                         method="P/FCF", window=PanelWindow.FULL_2Y, current_multiple=11.0)
    assert stats.contract_eligible is False
    assert str(MIN_WINDOW_OBSERVATIONS) in stats.ineligible_reason


def test_current_percentile_rank_locates_the_current_multiple_in_its_own_history():
    stats = window_stats(observations([float(i + 1) for i in range(100)]),
                         method="P/FCF", window=PanelWindow.FULL_2Y, current_multiple=10.0)
    assert stats.current_percentile_rank == 10.0


# -------------------------------------------------------------------------------------------------
# The drift diagnostic
# -------------------------------------------------------------------------------------------------

def test_spearman_is_plus_one_on_a_monotone_rise_and_minus_one_on_a_fall():
    rising = [float(i) for i in range(50)]
    assert spearman_rho(list(range(50)), rising) == pytest.approx(1.0)
    assert spearman_rho(list(range(50)), list(reversed(rising))) == pytest.approx(-1.0)


def test_spearman_is_none_rather_than_zero_on_a_constant_series():
    """Reporting 0.0 would read as 'no drift' rather than 'not measurable'."""
    assert spearman_rho(list(range(10)), [7.0] * 10) is None


def test_spearman_is_none_below_three_points():
    assert spearman_rho([0, 1], [1.0, 2.0]) is None


def test_a_sustained_de_rating_is_trending_strong():
    """ADBE's shape: the multiple falls across the panel without the fundamental falling."""
    falling = [33.0 - i * 0.05 for i in range(400)]
    stats = window_stats(observations(falling), method="P/FCF", window=PanelWindow.FULL_2Y,
                         current_multiple=falling[-1])
    assert stats.rho is not None and stats.rho < -0.9
    assert stats.trend is TrendClass.TRENDING_STRONG


def test_trend_bands_are_the_declared_ones():
    assert trend_class(None) is TrendClass.UNDETERMINED
    assert trend_class(0.0) is TrendClass.RANGE_BOUND
    assert trend_class(-0.39) is TrendClass.RANGE_BOUND
    assert trend_class(0.5) is TrendClass.TRENDING_MODERATE
    assert trend_class(-0.5) is TrendClass.TRENDING_MODERATE
    assert trend_class(0.95) is TrendClass.TRENDING_STRONG


# -------------------------------------------------------------------------------------------------
# The three chains, and the invariant that ties them together
# -------------------------------------------------------------------------------------------------

def test_each_method_takes_the_chain_its_numerator_dictates():
    assert metric_chain("P/B") is MetricChain.EQUITY_PER_SHARE
    assert metric_chain("P/FCF") is MetricChain.EQUITY_PER_SHARE
    assert metric_chain("P/E") is MetricChain.ALREADY_PER_SHARE
    for ev_method in ("EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF"):
        assert metric_chain(ev_method) is MetricChain.ENTERPRISE_BRIDGE


def test_fair_value_at_the_current_multiple_is_the_current_price_equity_chain():
    """FAIR_VALUE_AT_CURRENT_MULTIPLE_IS_PRICE, on ADBE's real P/FCF operands."""
    residual = identity_residual(method="P/FCF", current_multiple=ADBE_P_FCF,
                                 denominator_value=ADBE_FCF, shares=ADBE_SHARES,
                                 net_debt=ADBE_NET_DEBT, price=ADBE_PRICE)
    assert residual is not None
    assert residual <= 1e-9 * ADBE_PRICE


def test_fair_value_at_the_current_multiple_is_the_current_price_enterprise_chain():
    """The enterprise bridge must come back through net debt to the same close. On WBD, whose net
    debt is 29% of enterprise value, so a sign error or a dropped bridge cannot pass."""
    residual = identity_residual(method="EV/Sales", current_multiple=WBD_EV_SALES,
                                 denominator_value=WBD_REVENUE, shares=WBD_SHARES,
                                 net_debt=WBD_NET_DEBT, price=WBD_PRICE)
    assert residual is not None
    assert residual <= 1e-9 * WBD_PRICE


def test_fair_value_at_the_current_multiple_is_the_current_price_per_share_chain():
    eps, pe = 4.12, 70.37135922330097
    residual = identity_residual(method="P/E", current_multiple=pe, denominator_value=eps,
                                 shares=None, net_debt=None, price=289.93)
    assert residual is not None
    assert residual <= 1e-9 * 289.93


def test_the_identity_catches_a_wrong_share_count():
    """The defect class the invariant exists for: every operand individually plausible."""
    residual = identity_residual(method="P/FCF", current_multiple=ADBE_P_FCF,
                                 denominator_value=ADBE_FCF, shares=ADBE_SHARES * 1.01,
                                 net_debt=ADBE_NET_DEBT, price=ADBE_PRICE)
    assert residual is not None
    assert residual > 1e-9 * ADBE_PRICE


# -------------------------------------------------------------------------------------------------
# Scenario arithmetic
# -------------------------------------------------------------------------------------------------

def _target(multiple, percentile=BASE_PERCENTILE, session=START):
    return nearest_rank_percentile(
        [MultipleObservation(session=session, multiple=multiple, numerator_value=1.0,
                             denominator_value=1.0, denominator_period_end=None)], percentile)


def test_equity_chain_is_denominator_per_share_times_the_multiple():
    """Computed by hand in the assertion, not by calling the module twice."""
    scenario = scenario_value(
        scenario=Scenario.BASE, method="P/FCF", target=_target(12.0),
        window=PanelWindow.FULL_2Y, denominator_value=ADBE_FCF,
        denominator_field="free_cash_flow", denominator_period_end=date(2026, 5, 29),
        shares=ADBE_SHARES, net_debt=ADBE_NET_DEBT)
    assert scenario.ok
    assert scenario.metric_per_share == ADBE_FCF / ADBE_SHARES
    assert scenario.value_per_share == (ADBE_FCF / ADBE_SHARES) * 12.0
    assert scenario.implied_enterprise_value is None, "the equity chain has no enterprise leg"


def test_per_share_chain_uses_no_share_count():
    scenario = scenario_value(
        scenario=Scenario.BASE, method="P/E", target=_target(20.0), window=PanelWindow.FULL_2Y,
        denominator_value=4.12, denominator_field="eps_diluted",
        denominator_period_end=date(2026, 6, 30), shares=None, net_debt=None)
    assert scenario.ok
    assert scenario.value_per_share == 4.12 * 20.0
    assert scenario.shares is None


def test_enterprise_chain_bridges_through_net_debt():
    scenario = scenario_value(
        scenario=Scenario.BASE, method="EV/Sales", target=_target(3.0),
        window=PanelWindow.FULL_2Y, denominator_value=WBD_REVENUE,
        denominator_field="revenue", denominator_period_end=date(2026, 6, 30),
        shares=WBD_SHARES, net_debt=WBD_NET_DEBT)
    assert scenario.ok
    assert scenario.implied_enterprise_value == WBD_REVENUE * 3.0
    assert scenario.implied_equity_value == WBD_REVENUE * 3.0 - WBD_NET_DEBT
    assert scenario.value_per_share == (WBD_REVENUE * 3.0 - WBD_NET_DEBT) / WBD_SHARES


def test_enterprise_chain_refuses_rather_than_publishing_a_negative_fair_value():
    """At a low enough multiple the implied enterprise value does not cover the debt. A negative
    fair value per share is the same defect class as D5-D1's negative multiple."""
    scenario = scenario_value(
        scenario=Scenario.BEAR, method="EV/Sales", target=_target(0.5),
        window=PanelWindow.FULL_2Y, denominator_value=WBD_REVENUE,
        denominator_field="revenue", denominator_period_end=date(2026, 6, 30),
        shares=WBD_SHARES, net_debt=WBD_NET_DEBT)
    assert not scenario.ok
    assert scenario.refusal is ScenarioRefusal.NEGATIVE_IMPLIED_EQUITY
    assert scenario.value_per_share is None


def test_enterprise_chain_refuses_without_net_debt():
    """NATR's case: no borrowing balance resolves at the cash date, so there is no bridge."""
    scenario = scenario_value(
        scenario=Scenario.BASE, method="EV/Sales", target=_target(3.0),
        window=PanelWindow.FULL_2Y, denominator_value=492_023_000.0,
        denominator_field="revenue", denominator_period_end=date(2026, 6, 30),
        shares=17_595_520.0, net_debt=None)
    assert scenario.refusal is ScenarioRefusal.MISSING_NET_DEBT


def test_equity_chain_refuses_without_shares():
    scenario = scenario_value(
        scenario=Scenario.BASE, method="P/FCF", target=_target(12.0),
        window=PanelWindow.FULL_2Y, denominator_value=ADBE_FCF,
        denominator_field="free_cash_flow", denominator_period_end=None,
        shares=None, net_debt=None)
    assert scenario.refusal is ScenarioRefusal.MISSING_SHARES


def test_no_observed_multiple_refuses_rather_than_defaulting():
    scenario = scenario_value(
        scenario=Scenario.BASE, method="P/FCF", target=None, window=PanelWindow.FULL_2Y,
        denominator_value=ADBE_FCF, denominator_field="free_cash_flow",
        denominator_period_end=None, shares=ADBE_SHARES, net_debt=None)
    assert scenario.refusal is ScenarioRefusal.NO_OBSERVED_MULTIPLE


def test_a_refusal_cannot_carry_a_value_and_an_acceptance_must_have_one():
    """The structural gate, mirroring MultipleResult.__post_init__: a refusal with a number
    attached raises rather than reports, so "silent wrong fair value" is unrepresentable."""
    common = {"scenario": Scenario.BASE, "method": "P/FCF",
              "chain": MetricChain.EQUITY_PER_SHARE}
    with pytest.raises(AssertionError):
        ScenarioValue(**common, refusal=ScenarioRefusal.MISSING_SHARES, value_per_share=10.0)
    with pytest.raises(AssertionError):
        ScenarioValue(**common, value_per_share=None)
    with pytest.raises(AssertionError):
        ScenarioValue(**common, value_per_share=-5.0)


def test_the_metric_is_identical_across_the_three_scenarios():
    """METRIC_HELD_AT_CURRENT_TTM. Scenario separation comes from the multiple alone in v1."""
    built = [scenario_value(scenario=s, method="P/FCF", target=_target(m),
                            window=PanelWindow.FULL_2Y, denominator_value=ADBE_FCF,
                            denominator_field="free_cash_flow", denominator_period_end=None,
                            shares=ADBE_SHARES, net_debt=ADBE_NET_DEBT)
             for s, m in ((Scenario.BEAR, 8.0), (Scenario.BASE, 10.0), (Scenario.BULL, 13.0))]
    assert len({s.metric_per_share for s in built}) == 1
    assert all(s.metric_moved is False and s.multiple_moved is True for s in built)


# -------------------------------------------------------------------------------------------------
# TP1 / TP2 / upside
# -------------------------------------------------------------------------------------------------

def _range(bear, base, bull, *, method="P/FCF", denominator=ADBE_FCF, shares=ADBE_SHARES,
           net_debt=ADBE_NET_DEBT):
    legs = {}
    for scenario, multiple, percentile in ((Scenario.BEAR, bear, BEAR_PERCENTILE),
                                           (Scenario.BASE, base, BASE_PERCENTILE),
                                           (Scenario.BULL, bull, BULL_PERCENTILE)):
        legs[scenario] = scenario_value(
            scenario=scenario, method=method, target=_target(multiple, percentile),
            window=PanelWindow.FULL_2Y, denominator_value=denominator,
            denominator_field="free_cash_flow", denominator_period_end=None,
            shares=shares, net_debt=net_debt)
    return FairValueRange(method=method, window=PanelWindow.FULL_2Y, bear=legs[Scenario.BEAR],
                          base=legs[Scenario.BASE], bull=legs[Scenario.BULL])


def test_tp1_is_the_base_leg_and_tp2_is_the_bull_leg():
    fv = _range(8.0, 10.0, 13.0)
    tp = target_prices(fv, current_price=ADBE_PRICE)
    per_share = ADBE_FCF / ADBE_SHARES
    assert tp.tp1 == per_share * 10.0
    assert tp.tp2 == per_share * 13.0
    assert tp.bear_anchor == per_share * 8.0


def test_tp2_is_not_tp1_plus_a_margin_and_names_the_operand_that_moved():
    fv = _range(8.0, 10.0, 13.0)
    tp = target_prices(fv, current_price=ADBE_PRICE)
    assert tp.tp2_operand_that_moved == "multiple"
    assert tp.tp2 / tp.tp1 == pytest.approx(13.0 / 10.0)


def test_upside_is_the_contract_formula_against_the_unadjusted_close():
    fv = _range(8.0, 10.0, 13.0)
    tp = target_prices(fv, current_price=ADBE_PRICE)
    assert tp.upside_to_tp1 == tp.tp1 / ADBE_PRICE - 1.0
    assert tp.upside_to_tp2 == tp.tp2 / ADBE_PRICE - 1.0
    assert tp.downside_to_bear == tp.bear_anchor / ADBE_PRICE - 1.0


def test_a_base_multiple_under_the_current_one_yields_negative_upside_not_an_error():
    """D0 §N3's case: the base is already more than reflected. An honest negative number."""
    fv = _range(7.0, 8.0, 9.0)
    tp = target_prices(fv, current_price=ADBE_PRICE)
    assert tp.upside_to_tp1 is not None and tp.upside_to_tp1 < 0.0


def test_a_non_positive_close_cannot_anchor_an_upside():
    with pytest.raises(ValueError):
        target_prices(_range(8.0, 10.0, 13.0), current_price=0.0)


def test_the_range_is_ordered_bear_base_bull():
    assert _range(8.0, 10.0, 13.0).ordered is True
    assert _range(8.0, 10.0, 13.0).complete is True


def test_the_enterprise_chain_preserves_scenario_order_through_the_debt_bridge():
    fv = _range(2.0, 2.75, 3.5, method="EV/Sales", denominator=WBD_REVENUE,
                shares=WBD_SHARES, net_debt=WBD_NET_DEBT)
    assert fv.ordered is True
    assert fv.bear.value_per_share < fv.base.value_per_share < fv.bull.value_per_share


# -------------------------------------------------------------------------------------------------
# Reconciliation, correlation, confidence, abstention
# -------------------------------------------------------------------------------------------------

def test_two_methods_on_different_denominators_are_independent():
    """D5-D1 measured this on ADBE: five distinct denominators, only the FCF pair duplicated."""
    correlated, _ = methods_are_correlated("P/FCF", "EV/EBIT", net_debt=ADBE_NET_DEBT,
                                           enterprise_value=99_456_750_000.0)
    assert correlated is False


def test_the_same_denominator_with_immaterial_net_debt_is_one_reading_twice():
    """ADBE's EV/FCF 9.6748x against its P/FCF 9.6862x, at 0.1% net debt."""
    correlated, reason = methods_are_correlated("P/FCF", "EV/FCF", net_debt=ADBE_NET_DEBT,
                                                enterprise_value=99_456_750_000.0)
    assert correlated is True
    assert "0.1%" in reason


def test_the_same_denominator_with_material_net_debt_is_a_second_reading():
    """WBD's P/FCF 32.33x against its EV/FCF 45.47x, at 29% net debt: the leverage differs them."""
    correlated, reason = methods_are_correlated("P/FCF", "EV/FCF", net_debt=WBD_NET_DEBT,
                                                enterprise_value=99_129_442_023.98)
    assert correlated is False
    assert "capital structure" in reason


def test_unresolvable_net_debt_fails_closed_to_correlated():
    correlated, _ = methods_are_correlated("P/FCF", "EV/FCF", net_debt=None,
                                           enterprise_value=None)
    assert correlated is True


def test_a_primary_and_secondary_within_the_ratio_corroborate():
    rec = reconcile(primary=_range(8.0, 10.0, 13.0), secondary=_range(8.0, 11.0, 13.0),
                    net_debt=ADBE_NET_DEBT, enterprise_value=99_456_750_000.0)
    assert rec.status is ReconciliationStatus.CORROBORATES
    assert rec.ratio == pytest.approx(1.1)


def test_a_wide_disagreement_raises_valuation_conflict_and_is_never_averaged():
    rec = reconcile(primary=_range(8.0, 10.0, 13.0), secondary=_range(8.0, 20.0, 13.0),
                    net_debt=ADBE_NET_DEBT, enterprise_value=99_456_750_000.0)
    assert rec.status is ReconciliationStatus.VALUATION_CONFLICT
    assert rec.ratio == pytest.approx(2.0)
    assert rec.ratio > VALUATION_CONFLICT_RATIO
    # NEVER_AVERAGED: both values are carried, and no blended figure is produced anywhere.
    assert rec.primary_base is not None and rec.secondary_base is not None
    assert not hasattr(rec, "blended")


def test_no_secondary_is_reported_as_its_own_state():
    rec = reconcile(primary=_range(8.0, 10.0, 13.0), secondary=None, net_debt=None,
                    enterprise_value=None)
    assert rec.status is ReconciliationStatus.NO_SECONDARY


def test_a_single_method_valuation_is_allowed_but_is_low_confidence():
    """§23: one method does not force NOT_READY; it forces a stated limitation. GNW's case."""
    assessment = valuation_confidence(
        valued=True, independent_method_count=1, total_method_count=1,
        reconciliation=ReconciliationStatus.NO_SECONDARY, trend=TrendClass.RANGE_BOUND,
        window=PanelWindow.FULL_2Y, stale_inputs=(), share_class_high_confidence=True,
        peer_context_available=True)
    assert assessment.confidence is ValuationConfidence.LOW
    assert any("single method" in d for d in assessment.drivers)


def test_a_correlated_secondary_does_not_buy_a_second_method():
    assessment = valuation_confidence(
        valued=True, independent_method_count=1, total_method_count=2,
        reconciliation=ReconciliationStatus.CORROBORATES, trend=TrendClass.RANGE_BOUND,
        window=PanelWindow.FULL_2Y, stale_inputs=(), share_class_high_confidence=True,
        peer_context_available=True)
    assert assessment.confidence is ValuationConfidence.LOW
    assert any("correlated" in d for d in assessment.drivers)


def test_a_strongly_trending_window_caps_confidence_at_low():
    """DEAD_REGIME_IS_THE_CENTRAL_TRAP, as a confidence consequence."""
    assessment = valuation_confidence(
        valued=True, independent_method_count=2, total_method_count=2,
        reconciliation=ReconciliationStatus.CORROBORATES, trend=TrendClass.TRENDING_STRONG,
        window=PanelWindow.FULL_2Y, stale_inputs=(), share_class_high_confidence=True,
        peer_context_available=True)
    assert assessment.confidence is ValuationConfidence.LOW


def test_missing_peer_context_costs_a_demotion_rather_than_passing_silently():
    assessment = valuation_confidence(
        valued=True, independent_method_count=2, total_method_count=2,
        reconciliation=ReconciliationStatus.CORROBORATES, trend=TrendClass.RANGE_BOUND,
        window=PanelWindow.FULL_2Y, stale_inputs=(), share_class_high_confidence=True,
        peer_context_available=False)
    assert assessment.confidence is ValuationConfidence.MEDIUM
    assert any("PEER_CONTEXT_UNAVAILABLE" in d for d in assessment.drivers)


def test_an_unvalued_issuer_is_not_ready_and_states_why():
    assessment = valuation_confidence(
        valued=False, independent_method_count=0, total_method_count=1,
        reconciliation=ReconciliationStatus.NO_SECONDARY, trend=TrendClass.UNDETERMINED,
        window=PanelWindow.FULL_2Y, stale_inputs=(), share_class_high_confidence=True,
        peer_context_available=False)
    assert assessment.confidence is ValuationConfidence.NOT_READY
    assert assessment.drivers


def test_every_confidence_carries_at_least_one_stated_driver():
    """A confidence with no stated driver is a judgement wearing a label."""
    for trend in TrendClass:
        assessment = valuation_confidence(
            valued=True, independent_method_count=2, total_method_count=2,
            reconciliation=ReconciliationStatus.CORROBORATES, trend=trend,
            window=PanelWindow.RECENT_6M, stale_inputs=(), share_class_high_confidence=True,
            peer_context_available=False)
        assert assessment.drivers


# -------------------------------------------------------------------------------------------------
# What must be absent
# -------------------------------------------------------------------------------------------------

def test_no_return_or_outcome_is_an_input_anywhere_in_the_module():
    """NO_RETURN_INPUT_HERE, asserted by name over every signature and field in the module."""
    import inspect

    from app.backtest.strategy_h_v2.valuation import fair_value

    forbidden = ("return", "forward", "realized", "outcome", "performance", "pnl",
                 "alpha", "excess", "benchmark", "future_price", "subsequent")
    offenders: list[str] = []
    for name, obj in vars(fair_value).items():
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) and obj.__module__ == fair_value.__name__:
            for parameter in inspect.signature(obj).parameters:
                if any(word in parameter.lower() for word in forbidden):
                    offenders.append(f"{name}({parameter})")
        if inspect.isclass(obj) and hasattr(obj, "__dataclass_fields__"):
            for field_name in obj.__dataclass_fields__:
                if any(word in field_name.lower() for word in forbidden):
                    offenders.append(f"{obj.__name__}.{field_name}")
    assert offenders == [], offenders
    assert "not available to be fitted to" in NO_RETURN_INPUT_HERE


def test_no_dataclass_in_the_module_can_hold_a_recommendation():
    """D5 produces no buy, sell, approve, watch, reject, entry, exit or size."""
    import inspect

    from app.backtest.strategy_h_v2.valuation import fair_value

    forbidden = ("buy", "sell", "approve", "watch", "reject", "verdict", "entry", "exit",
                 "position", "size", "weight", "signal", "recommend", "action")
    offenders: list[str] = []
    for name, obj in vars(fair_value).items():
        if inspect.isclass(obj) and hasattr(obj, "__dataclass_fields__"):
            for field_name in obj.__dataclass_fields__:
                if any(word in field_name.lower() for word in forbidden):
                    offenders.append(f"{obj.__name__}.{field_name}")
    assert offenders == [], offenders
    assert any("buy, sell, approve, watch or reject" in item for item in NEVER_PRODUCED)


def test_the_contract_version_is_the_declared_one():
    assert FAIR_VALUE_CONTRACT_VERSION == "h_v2_d5_d2_fair_value_v1"


# -------------------------------------------------------------------------------------------------
# The window rule
# -------------------------------------------------------------------------------------------------

def test_window_rule_takes_the_full_panel_when_it_does_not_trend_strongly():
    from app.dev.run_strategy_h_v2_d5_d2 import select_contract_window

    rising = [10.0 + (i % 40) * 0.05 for i in range(400)]
    windows = {w: window_stats(observations(rising), method="P/FCF", window=w,
                               current_multiple=rising[-1]) for w in PanelWindow}
    chosen, why = select_contract_window(windows)
    assert chosen is PanelWindow.FULL_2Y
    assert "not TRENDING_STRONG" in why


def test_window_rule_takes_the_shortest_window_on_a_strong_drift():
    """DEAD_REGIME_IS_THE_CENTRAL_TRAP: ADBE's shape, where the full panel is a transition."""
    from app.dev.run_strategy_h_v2_d5_d2 import select_contract_window

    falling = [33.0 - i * 0.05 for i in range(400)]
    windows = {w: window_stats(observations(falling), method="P/FCF", window=w,
                               current_multiple=falling[-1]) for w in PanelWindow}
    chosen, why = select_contract_window(windows)
    assert chosen is PanelWindow.RECENT_6M
    assert "different regime" in why


def test_window_rule_does_not_crash_when_the_drift_is_not_measurable():
    """A constant multiple makes rho None and the trend UNDETERMINED, which is not
    TRENDING_STRONG and so takes the FULL_2Y branch. Formatting that None as a signed float
    raised TypeError before this was guarded."""
    from app.dev.run_strategy_h_v2_d5_d2 import select_contract_window

    windows = {w: window_stats(observations([5.0] * 300), method="P/FCF", window=w,
                               current_multiple=5.0) for w in PanelWindow}
    assert windows[PanelWindow.FULL_2Y].trend is TrendClass.UNDETERMINED
    assert windows[PanelWindow.FULL_2Y].rho is None
    chosen, why = select_contract_window(windows)
    assert chosen is PanelWindow.FULL_2Y
    assert "not measurable" in why


def test_window_rule_returns_none_when_no_window_is_eligible():
    from app.dev.run_strategy_h_v2_d5_d2 import select_contract_window

    windows = {w: window_stats(observations([10.0, 11.0, 12.0]), method="P/FCF", window=w,
                               current_multiple=12.0) for w in PanelWindow}
    chosen, why = select_contract_window(windows)
    assert chosen is None
    assert "no window is contract-eligible" in why
