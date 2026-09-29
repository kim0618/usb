"""Score, decision, exit evaluator and sizing.

No expected value in this file comes from a forward return. Scores are checked against the
contract's own level table, decisions against the threshold, exits against the clock and the stop
price, and sizes against the risk budget arithmetic. Nothing here knows whether a trade would
have made money, and D6-B must not be able to find out.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.crypto.research.d6 import decision as DEC
from app.crypto.research.d6 import exits as EXITS
from app.crypto.research.d6 import features as F
from app.crypto.research.d6 import score as SCORE
from app.crypto.research.d6 import sizing as SIZING
from app.crypto.research.d6.contract import load as load_contract
from app.crypto.research.d6.model import (BucketAssignment, ENTRY_INTENT_LONG, EXIT_INTENT,
                                          FeatureValues, HISTORICAL, HOLD, HOLD_POSITION, LONG,
                                          NO_ENTRY_INTENT, VirtualPosition)

from tests.crypto.d6_fixtures import (MINUTE_MS, clean_market, clean_state, force_last_bar,
                                      synthetic_grid, window)


@pytest.fixture(scope="module")
def contract():
    return load_contract()


def buckets(basis: int | None, oi: int | None, drop: int | None,
            cutoffs=(-1.0, -0.5, 0.5, 1.0)) -> dict[str, BucketAssignment]:
    def one(b):
        return BucketAssignment(bucket=b, cutoffs=None if b is None else cutoffs,
                                valid_fraction=1.0 if b is not None else 0.1)
    return {"f_basis": one(basis), "f_oi1h": one(oi), "f_drop1h": one(drop)}


def features(rv: float = 9.0e-4, oi: float = -0.01) -> FeatureValues:
    return FeatureValues(f_basis=-0.001, f_oi1h=oi, f_drop1h=-0.005, f_rv24h=rv)


# --- score categories -------------------------------------------------------------------------

def test_all_four_categories_are_scored_in_contract_order(contract):
    cats = SCORE.long_categories(contract, features(), buckets(1, 1, 1))
    assert [c.name for c in cats] == [c["name"] for c in contract.score_components]
    assert [c.max_points for c in cats] == [25, 25, 25, 25]


def test_every_category_at_its_maximum_totals_the_scale(contract):
    cats = SCORE.long_categories(contract, features(rv=2e-3), buckets(1, 1, 1))
    assert [c.points for c in cats] == [25, 25, 25, 25]
    assert SCORE.long_score(cats) == contract.scale_max == 100


def test_every_category_at_zero_totals_zero(contract):
    cats = SCORE.long_categories(contract, features(rv=1e-4, oi=0.02), buckets(3, 3, 3))
    assert [c.points for c in cats] == [0, 0, 0, 0]
    assert SCORE.long_score(cats) == 0


@pytest.mark.parametrize("bucket,expected", [(1, 25), (2, 12), (3, 0), (4, 0), (5, 0), (None, 0)])
def test_market_structure_levels_follow_the_contract(contract, bucket, expected):
    cats = SCORE.long_categories(contract, features(), buckets(bucket, 3, 3))
    assert next(c for c in cats if c.name == "market_structure").points == expected


@pytest.mark.parametrize("bucket,expected", [(1, 25), (2, 12), (3, 0), (5, 0), (None, 0)])
def test_price_exhaustion_levels_follow_the_contract(contract, bucket, expected):
    cats = SCORE.long_categories(contract, features(), buckets(3, 3, bucket))
    assert next(c for c in cats if c.name == "price_exhaustion").points == expected


def test_positioning_uses_b1_then_the_sign_test(contract):
    """The contract gives this one category a `negative` level on top of the bucket levels."""
    b1 = SCORE.long_categories(contract, features(oi=-0.05), buckets(3, 1, 3))
    assert next(c for c in b1 if c.name == "positioning").points == 25

    negative = SCORE.long_categories(contract, features(oi=-0.001), buckets(3, 3, 3))
    positioning = next(c for c in negative if c.name == "positioning")
    assert positioning.points == 12 and positioning.level == "negative"

    positive = SCORE.long_categories(contract, features(oi=0.001), buckets(3, 3, 3))
    assert next(c for c in positive if c.name == "positioning").points == 0


def test_positioning_zero_is_not_negative(contract):
    cats = SCORE.long_categories(contract, features(oi=0.0), buckets(3, 3, 3))
    assert next(c for c in cats if c.name == "positioning").points == 0


@pytest.mark.parametrize("rv,expected,label", [
    (2e-3, 25, "HIGH"), (9e-4, 12, "MID"), (1e-4, 0, "LOW"), (float("nan"), 0, "UNKNOWN")])
def test_volatility_levels_follow_the_frozen_cutoffs(contract, rv, expected, label):
    cats = SCORE.long_categories(contract, features(rv=rv), buckets(3, 3, 3))
    category = next(c for c in cats if c.name == "volatility_exhaustion")
    assert (category.points, category.level) == (expected, label)


def test_category_detail_carries_the_value_and_the_cutoffs(contract):
    cats = SCORE.long_categories(contract, features(), buckets(1, 1, 1))
    structure = next(c for c in cats if c.name == "market_structure")
    assert structure.detail["value"] == -0.001
    assert structure.detail["bucket"] == "B1"
    assert structure.detail["cutoffs"] == (-1.0, -0.5, 0.5, 1.0)


# --- SHORT is disabled ------------------------------------------------------------------------

def test_short_score_is_none_not_zero(contract):
    assert SCORE.short_score(contract) is None


def test_separation_is_vacuous_while_short_is_disabled(contract):
    assert SCORE.separation_ok(contract, 50, None) is True
    assert SCORE.separation_ok(contract, 0, None) is True


def test_separation_applies_once_a_short_score_exists(contract):
    assert SCORE.separation_ok(contract, 51, 46) is False   # the contract's own example
    assert SCORE.separation_ok(contract, 60, 46) is True
    assert SCORE.separation_ok(contract, 56, 46) is True    # exactly 10 passes


# --- mandatory minimums -----------------------------------------------------------------------

def test_mandatory_minimums_are_satisfied_by_twelve_point_levels(contract):
    cats = SCORE.long_categories(contract, features(rv=9e-4), buckets(2, 3, 3))
    assert SCORE.mandatory_failures(contract, cats) == []


def test_market_structure_below_minimum_is_reported(contract):
    cats = SCORE.long_categories(contract, features(rv=2e-3), buckets(3, 1, 1))
    assert SCORE.mandatory_failures(contract, cats) == ["M1_MARKET_STRUCTURE_BELOW_MIN"]


def test_volatility_below_minimum_is_reported(contract):
    cats = SCORE.long_categories(contract, features(rv=1e-4), buckets(1, 1, 1))
    assert SCORE.mandatory_failures(contract, cats) == ["M2_VOLATILITY_EXHAUSTION_BELOW_MIN"]


# --- threshold boundary -----------------------------------------------------------------------

def scene(contract, *, basis, oi_bucket, drop, rv, oi_value=-0.01):
    grid = synthetic_grid(days=32)
    win = window(grid)
    return {
        "window": win,
        "features": FeatureValues(f_basis=-0.001, f_oi1h=oi_value, f_drop1h=-0.005, f_rv24h=rv),
        "buckets": buckets(basis, oi_bucket, drop),
    }


def decide_with(contract, *, score_buckets, rv, oi_value=-0.01, **kwargs):
    """Drive `decide` with hand-made features so the boundary is exact, not approximate."""
    grid = synthetic_grid(days=32)
    win = window(grid)
    values = FeatureValues(f_basis=-0.001, f_oi1h=oi_value, f_drop1h=-0.005, f_rv24h=rv)
    monkey = kwargs.pop("monkeypatch")
    monkey.setattr(DEC.F, "compute_series", lambda w: {
        "f_basis": np.array([values.f_basis]), "f_oi1h": np.array([values.f_oi1h]),
        "f_drop1h": np.array([values.f_drop1h]), "f_rv24h": np.array([values.f_rv24h])})
    monkey.setattr(DEC, "build_buckets", lambda c, w, s: score_buckets)
    market = kwargs.pop("market", None) or clean_market(win)
    state = kwargs.pop("state", None) or clean_state()
    return DEC.decide(contract, win, market, state, mode=HISTORICAL)


def test_score_exactly_at_the_threshold_enters(contract, monkeypatch):
    """25 + 12 + 12 + 0 = 49 holds; 25 + 12 + 12 + 12 = 61 and 25+0+25+0=50 enter."""
    result = decide_with(contract, score_buckets=buckets(1, 3, 3), rv=2e-3,
                         oi_value=0.01, monkeypatch=monkeypatch)
    assert result.long_score == 50
    assert result.decision == LONG and result.entry_intent == ENTRY_INTENT_LONG


def test_score_one_level_below_the_threshold_holds(contract, monkeypatch):
    result = decide_with(contract, score_buckets=buckets(2, 3, 2), rv=2e-3,
                         oi_value=0.01, monkeypatch=monkeypatch)
    assert result.long_score == 49
    assert result.decision == HOLD
    assert "SCORE_BELOW_THRESHOLD" in result.reason_codes


def test_score_above_the_threshold_enters(contract, monkeypatch):
    result = decide_with(contract, score_buckets=buckets(1, 1, 1), rv=2e-3,
                         monkeypatch=monkeypatch)
    assert result.long_score == 100
    assert result.decision == LONG


def test_the_threshold_sits_on_a_gap_in_the_reachable_scores(contract):
    """The contract's section 5.5 claim, checked by enumeration rather than taken on trust.

    With four ordinal categories of 0/12/25 the total cannot take every value: it can be 48, 49,
    50, 61 and so on, but nothing between 50 and 61. So the threshold of 50 is not a cut through a
    dense distribution, it is exactly the first reachable total that clears it, and every
    combination that clears it while meeting M1 and M2 contains at least one 25-point category.

    If a later version changes a level, this test fails and the threshold has to be re-examined.
    """
    from itertools import product
    levels = {c["name"]: sorted({int(v) for v in c["levels"].values()})
              for c in contract.score_components}
    mandatory = {m["component"]: int(m["min"]) for m in contract.mandatory_minimums}
    threshold = contract.long_entry_min_score

    reachable, eligible = set(), []
    for combo in product(*levels.values()):
        points = dict(zip(levels, combo))
        reachable.add(sum(combo))
        if sum(combo) >= threshold and all(points[k] >= v for k, v in mandatory.items()):
            eligible.append(combo)

    assert max(t for t in reachable if t < threshold) == 49
    assert min(sum(c) for c in eligible) == threshold == 50
    assert all(max(combo) == 25 for combo in eligible)


def test_high_score_without_the_mandatory_minimum_holds(contract, monkeypatch):
    """75 points, but the basis category is empty, so M1 blocks it."""
    result = decide_with(contract, score_buckets=buckets(3, 1, 1), rv=2e-3,
                         monkeypatch=monkeypatch)
    assert result.long_score == 75
    assert result.decision == HOLD
    assert "M1_MARKET_STRUCTURE_BELOW_MIN" in result.reason_codes


# --- decision assembly ------------------------------------------------------------------------

def test_a_failing_filter_overrides_a_perfect_score(contract, monkeypatch):
    result = decide_with(contract, score_buckets=buckets(1, 1, 1), rv=2e-3,
                         state=clean_state(position_open=True), monkeypatch=monkeypatch)
    assert result.long_score == 100
    assert result.decision == HOLD
    assert result.filter_pass is False
    assert "H9_POSITION_OPEN" in result.reason_codes


def test_decision_always_reports_short_as_disabled(contract, monkeypatch):
    result = decide_with(contract, score_buckets=buckets(1, 1, 1), rv=2e-3,
                         monkeypatch=monkeypatch)
    assert result.short_score is None
    assert result.short_state == "SHORT_DISABLED_FOR_V1"
    assert "SHORT_DISABLED_FOR_V1" in result.reason_codes


def test_the_realtime_only_skips_do_not_block_entry(contract, monkeypatch):
    result = decide_with(contract, score_buckets=buckets(1, 1, 1), rv=2e-3,
                         monkeypatch=monkeypatch)
    assert result.decision == LONG
    assert "H6_NO_HISTORICAL_ORDERBOOK" in result.reason_codes


def test_decision_output_has_every_contracted_field(contract, monkeypatch):
    payload = decide_with(contract, score_buckets=buckets(1, 1, 1), rv=2e-3,
                          monkeypatch=monkeypatch).as_dict()
    for key in ("timestamp_ms", "strategy_id", "strategy_version", "contract_hash", "decision",
                "long_score", "short_score", "category_scores", "hard_filters", "filter_pass",
                "entry_allowed", "entry_intent", "reason_codes", "market_context",
                "required_inputs_available", "data_age", "features", "buckets", "sizing"):
        assert key in payload, key
    assert payload["contract_hash"] == contract.sha256
    assert payload["strategy_id"] == "FDN-V1"
    assert len(payload["hard_filters"]) == 13
    assert len(payload["category_scores"]) == 4


def test_unfeasible_sizing_turns_an_entry_into_a_hold(contract, monkeypatch):
    tiny = clean_state(equity=1.0)   # 0.5% of 1 USDT cannot buy the minimum quantity
    result = decide_with(contract, score_buckets=buckets(1, 1, 1), rv=2e-3,
                         state=tiny, monkeypatch=monkeypatch)
    assert result.long_score == 100
    assert result.decision == HOLD
    assert any(code.startswith("SIZING_") for code in result.reason_codes)


def test_unknown_mode_is_refused(contract):
    grid = synthetic_grid(days=32)
    win = window(grid)
    with pytest.raises(ValueError, match="unknown mode"):
        DEC.decide(contract, win, clean_market(win), clean_state(), mode="BACKTEST")


# --- exit evaluator ---------------------------------------------------------------------------

def position(entry_ts: int = 1_000_000, stop: float = 96_000.0) -> VirtualPosition:
    return VirtualPosition(side="LONG", entry_ts_ms=entry_ts, entry_price=100_000.0,
                           qty=0.01, stop_price=stop)


def test_exit_holds_before_the_clock_and_above_the_stop(contract):
    result = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 60 * MINUTE_MS,
                            mark=99_000.0)
    assert result.action == HOLD_POSITION and result.reason == EXITS.HOLDING
    assert result.held_minutes == 60


def test_exit_fires_on_the_time_rule_at_exactly_240_minutes(contract):
    just_before = EXITS.evaluate(contract, position(),
                                 now_ms=1_000_000 + 239 * MINUTE_MS, mark=99_000.0)
    exactly = EXITS.evaluate(contract, position(),
                             now_ms=1_000_000 + 240 * MINUTE_MS, mark=99_000.0)
    assert just_before.action == HOLD_POSITION
    assert exactly.action == EXIT_INTENT and exactly.reason == EXITS.TIME_EXIT


def test_exit_fires_on_the_stop_at_exactly_the_stop_price(contract):
    above = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 10 * MINUTE_MS,
                           mark=96_000.01)
    at = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 10 * MINUTE_MS, mark=96_000.0)
    below = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 10 * MINUTE_MS, mark=95_999.9)
    assert above.action == HOLD_POSITION
    assert at.action == EXIT_INTENT and at.reason == EXITS.VOLATILITY_STOP
    assert below.action == EXIT_INTENT and below.reason == EXITS.VOLATILITY_STOP


def test_the_stop_wins_when_the_same_bar_also_runs_out_the_clock(contract):
    """The contract's `same_bar_rule`: a 1m bar does not order its own extremes, so take the
    unfavourable reading."""
    result = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 240 * MINUTE_MS,
                            mark=99_000.0, mark_low=95_000.0)
    assert result.action == EXIT_INTENT and result.reason == EXITS.VOLATILITY_STOP


def test_the_bar_low_triggers_the_stop_even_when_the_close_recovered(contract):
    result = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 30 * MINUTE_MS,
                            mark=99_500.0, mark_low=95_900.0)
    assert result.action == EXIT_INTENT and result.reason == EXITS.VOLATILITY_STOP


def test_a_missing_mark_does_not_claim_the_position_is_safe(contract):
    result = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 30 * MINUTE_MS, mark=None)
    assert result.action == HOLD_POSITION and result.reason == EXITS.MARK_UNKNOWN


def test_the_time_exit_still_fires_without_a_mark(contract):
    result = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 240 * MINUTE_MS, mark=None)
    assert result.action == EXIT_INTENT and result.reason == EXITS.TIME_EXIT


def test_the_exit_evaluator_reports_no_pnl(contract):
    payload = EXITS.evaluate(contract, position(), now_ms=1_000_000 + 10 * MINUTE_MS,
                             mark=99_000.0).as_dict()
    text = str(payload).lower()
    for banned in ("pnl", "profit", "return", "win"):
        assert banned not in text


# --- sizing -----------------------------------------------------------------------------------

def test_stop_distance_is_the_contract_formula(contract):
    rv = 6.0e-4
    expected = 2.5 * rv * math.sqrt(240)
    assert SIZING.stop_distance(contract, rv) == pytest.approx(expected)


def test_stop_distance_is_clipped_at_both_ends(contract):
    assert SIZING.stop_distance(contract, 1e-9) == 0.01
    assert SIZING.stop_distance(contract, 1.0) == 0.10


def test_stop_distance_is_none_for_a_nan_volatility(contract):
    assert SIZING.stop_distance(contract, float("nan")) is None


def test_risk_budget_sizing_loses_about_half_a_percent_at_the_stop(contract):
    """Flooring the quantity to the 0.001 step can only shrink the position, so the loss at the
    stop lands at or just under the budget, never above it."""
    equity, price, rv = 10_000.0, 100_000.0, 6.0e-4
    result = SIZING.size(contract, equity=equity, entry_price=price, rv24h=rv)
    budget = equity * 0.005
    loss_at_stop = result.final_notional * result.stop_distance
    one_step_of_risk = contract.qty_step * price * result.stop_distance
    assert loss_at_stop <= budget
    assert budget - loss_at_stop < one_step_of_risk
    assert result.feasible


def test_leverage_is_fixed_at_one_and_does_not_scale_the_size(contract):
    result = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4)
    assert result.leverage == 1.0
    assert result.final_notional < 10_000.0 * contract.sizing["notional_cap_over_equity"]


def test_the_notional_cap_cannot_bind_under_the_contract_constants(contract):
    """A standing fact about V1, worth pinning: the 1.0x cap is unreachable.

    The risk budget is 0.5% and the stop distance floor is 1%, so the largest notional the sizing
    rule can ask for is 0.005 / 0.01 = 0.5x equity. The cap is a bound that the risk budget
    already dominates, which is why D6-A measured `capped_share` at 0. If a later version raises
    the budget or lowers the floor, this test fails and the cap starts mattering.
    """
    equity = 10_000.0
    tightest = SIZING.size(contract, equity=equity, entry_price=100_000.0, rv24h=1e-9)
    assert tightest.stop_distance == contract.stop["dist_min"]
    largest_possible = contract.sizing["risk_budget"] / contract.stop["dist_min"]
    assert largest_possible == pytest.approx(0.5)
    assert largest_possible < contract.sizing["notional_cap_over_equity"]
    assert tightest.theoretical_notional == pytest.approx(equity * largest_possible)
    assert tightest.risk_limited_notional == tightest.theoretical_notional


def test_safe_max_is_a_ceiling_and_nothing_else(contract):
    uncapped = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4)
    capped = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4,
                         safe_max_qty=0.001)
    assert capped.final_qty == 0.001 < uncapped.final_qty
    assert capped.safe_max_cap_qty == 0.001
    assert capped.risk_limited_notional == uncapped.risk_limited_notional


def test_a_large_safe_max_does_not_raise_the_size(contract):
    plain = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4)
    generous = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4,
                           safe_max_qty=100.0)
    assert generous.final_qty == plain.final_qty


def test_quantity_is_floored_to_the_step(contract):
    result = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4)
    scaled = result.final_qty / contract.qty_step
    assert scaled == pytest.approx(round(scaled))


@pytest.mark.parametrize("rv", [0.0, -1.0, float("nan"), float("inf")])
def test_an_invalid_volatility_yields_no_size(contract, rv):
    result = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=rv)
    if rv == 0.0:
        assert result.stop_distance == 0.01   # clipped, still a usable stop
    else:
        assert result.feasible is False
        assert result.reason == SIZING.STOP_DISTANCE_UNKNOWN


def test_missing_equity_yields_no_size(contract):
    result = SIZING.size(contract, equity=None, entry_price=100_000.0, rv24h=6.0e-4)
    assert result.feasible is False and result.reason == SIZING.EQUITY_UNKNOWN


def test_missing_entry_price_yields_no_size(contract):
    result = SIZING.size(contract, equity=10_000.0, entry_price=None, rv24h=6.0e-4)
    assert result.feasible is False and result.reason == SIZING.ENTRY_PRICE_UNKNOWN


def test_a_tiny_account_cannot_reach_the_minimum_quantity(contract):
    result = SIZING.size(contract, equity=1.0, entry_price=100_000.0, rv24h=6.0e-4)
    assert result.feasible is False and result.reason == SIZING.QTY_BELOW_MIN


def test_the_minimum_notional_is_enforced(contract):
    """A size above the quantity floor can still be below the 5 USDT minimum order value."""
    result = SIZING.size(contract, equity=80.0, entry_price=1_000.0, rv24h=1.0)
    assert result.final_qty >= contract.min_order_qty
    assert result.final_notional < contract.min_notional_usdt
    assert result.feasible is False and result.reason == SIZING.NOTIONAL_BELOW_MIN


def test_sizing_reports_every_intermediate_step(contract):
    payload = SIZING.size(contract, equity=10_000.0, entry_price=100_000.0, rv24h=6.0e-4,
                          safe_max_qty=5.0).as_dict()
    for key in ("theoretical_notional", "risk_limited_notional", "safe_max_cap_qty",
                "final_qty", "final_notional", "stop_distance", "stop_price", "leverage"):
        assert key in payload, key


def test_stop_price_sits_one_stop_distance_below_entry(contract):
    price, rv = 100_000.0, 6.0e-4
    distance = SIZING.stop_distance(contract, rv)
    assert SIZING.stop_price(contract, price, rv) == pytest.approx(price * (1 - distance))


# --- real data sanity, still no PnL -----------------------------------------------------------

def test_a_real_grid_bar_produces_a_complete_decision(contract):
    """Real D2 numbers are not needed for this; a synthetic 32 day grid exercises the same path."""
    grid = synthetic_grid(days=32, oi_slope=-1e-7, vol=9.0e-4)
    force_last_bar(grid, basis=-0.002, drop_1h=-0.01, oi_change_1h=-0.02)
    win = window(grid)
    result = DEC.decide(contract, win, clean_market(win), clean_state(), mode=HISTORICAL)
    assert result.decision in (LONG, HOLD)
    assert 0 <= result.long_score <= 100
    assert set(result.buckets) == set(contract.bucketed_features)
    assert result.volatility_label in ("LOW", "MID", "HIGH")
    assert result.sizing is not None
