"""H-V2-D5-D2: fair value ranges and target prices, from observed multiples only.

This is the first step in H-V2 that answers "what is this issuer worth". Everything before it
observed: D5-D1 published the current multiple the market pays today and stopped there by
construction. This step chooses a *target* multiple, applies it to a code-owned per-share metric and
publishes `Bear / Base / Bull` fair values, `TP1`, `TP2` and the upside to each.

The whole design rests on one sentence of D5-D0 §K, which is stricter than a reader might expect:

    value = (code-owned per-share metric for that scenario) x (observed multiple for that scenario)

**Observed.** Not a multiple a model considered fair, not a sector rule of thumb, not a peer average
imported from a prior. Every target multiple published here is a multiple *this issuer's own shares
actually traded at on a named session inside the local panel*, and the session is carried as
provenance. That is why `nearest_rank_percentile` exists instead of the interpolating percentile
every statistics library ships: an interpolated P50 sitting between two observations is a number
nobody ever paid, and §K does not permit it. See `PERCENTILES_ARE_OBSERVATIONS`.

The four traps this module is shaped around:

1. **The dead-regime base.** A two-year window contains one macro regime - D5-D0 §H says so in its
   own words - so the median of a multiple that fell monotonically for two years is not a "normal"
   to revert to, it is the average of a regime that ended. A Base fair value built on it manufactures
   upside out of history. `spearman_rho` and `TrendClass` measure the drift, every window's numbers
   are published side by side, and the window the contract uses is named with its argument. See
   `DEAD_REGIME_IS_THE_CENTRAL_TRAP`.

2. **The price-only range.** If every observation in a window shares one denominator period end, the
   "multiple range" is the price range wearing a multiple's name. `denominator_moved` detects it and
   such a window may be reported but never govern. See `PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE`.

3. **The negative implied equity.** An EV method's implied equity is `implied EV - net debt`, and at a
   low enough target multiple that is negative. A negative fair value per share is the same class of
   defect as D5-D1's negative multiple: it reads like a number and means "the debt exceeds the
   business". It is refused, never published. See `ScenarioRefusal.NEGATIVE_IMPLIED_EQUITY`.

4. **The fitted target.** No return, forward return, realized outcome or price path is an input to
   anything here, and `NO_RETURN_INPUT_HERE` says so for the test that asserts it. A fitted target
   price is indistinguishable from a derived one by inspection, so the only defence is that the
   quantity is absent from the module's inputs entirely.

The arithmetic invariant that ties it together, asserted for every method on every issuer:

    at the current observed multiple, the fair value per share equals the current price, exactly.

`identity_residual` computes it. It holds for all three numerator chains - market cap, price per
share and enterprise value - and it is what makes "the fair value came from its stated operands"
checkable rather than assertable. See `FAIR_VALUE_AT_CURRENT_MULTIPLE_IS_PRICE`.

What this module does not do, and what no later edit may make it do: it produces no buy, no sell, no
approve, watch or reject, no entry, no exit and no position size. D6 combines D4's expectation gap
with this and D6 does not exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Mapping, Sequence

from app.backtest.strategy_h_v2.valuation.multiples import (
    MULTIPLE_SPECS,
    Numerator,
)

FAIR_VALUE_CONTRACT_VERSION = "h_v2_d5_d2_fair_value_v1"

NEVER_PRODUCED: tuple[str, ...] = (
    "a buy, sell, approve, watch or reject verdict",
    "an entry level, an exit level, a stop or a position size",
    "a target multiple that is not an observation of this issuer's own traded multiple",
    "a fair value from an interpolated percentile - see PERCENTILES_ARE_OBSERVATIONS",
    "a fair value or target price on a negative implied equity value",
    "a fair value from a window whose denominator never moved - see denominator_moved",
    "a revenue, EPS, margin or growth forecast authored by a model",
    "a target price fitted to any return, realized or forward",
)

NO_RETURN_INPUT_HERE = (
    "No realized return, forward return, price path, outcome label or performance measure is an "
    "argument to any function in this module, a field on any dataclass in it, or a key in anything "
    "it emits. D5-D0 §L's prohibition on fitting a target price to the return it would have "
    "predicted is the one failure mode no later gate can detect, because a fitted number and a "
    "derived number look identical once written down. The defence is therefore structural: the "
    "quantity is not available to be fitted to. A test asserts the absence by name."
)

PERCENTILES_ARE_OBSERVATIONS = (
    "Every percentile here is nearest-rank: it returns an element of the observed series together "
    "with the session that element was observed on. The interpolating percentile that numpy and "
    "every statistics library default to would return a weighted average of two neighbours, which "
    "is a multiple nobody ever paid for this issuer. D5-D0 §K requires the scenario multiple to be "
    "an OBSERVED value and §L repeats it for TP2, so interpolation is not a precision preference "
    "here - it would break the contract's only constraint on where a target multiple may come from."
)

DEAD_REGIME_IS_THE_CENTRAL_TRAP = (
    "D5-D0 §H: a two-year window starting in September 2024 'contains one macro regime and at most "
    "eight reported quarters, so it cannot show how an issuer was valued in a different one'. The "
    "consequence for THIS step, which D5-D0 did not have to face because it published no target: "
    "when a multiple has drifted in one direction across the whole panel, its full-panel median is "
    "not a level the issuer reverts to, it is the midpoint of a transition. Using it as the Base "
    "multiple creates upside out of the fact that the past was more expensive, which is precisely "
    "the 'excessive re-rating' D5-D2's brief §17 forbids and the heroic assumption §17 names. "
    "This module does not resolve that with a threshold. It measures the drift (`spearman_rho`, "
    "`TrendClass`), publishes every window's fair value side by side so the choice of window is "
    "visible rather than buried, requires the governing window to be named with an argument, and "
    "caps confidence at LOW whenever the drift is strong. A reader who disagrees with the window "
    "can read the other windows' numbers off the same table."
)

PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE = (
    "A window all of whose observations divide by one denominator period end has a multiple range "
    "that is its price range rescaled by a constant. Reporting it as an observed multiple range "
    "would claim the market re-rated the issuer when all that happened is the price moved against a "
    "fundamental that had not yet been restated. `denominator_moved` is False for such a window and "
    "`contract_eligible` is False with it, so the window can be shown as context and can never "
    "govern a published target price."
)

FAIR_VALUE_AT_CURRENT_MULTIPLE_IS_PRICE = (
    "Setting the target multiple to the current observed multiple must return the current price per "
    "share, to floating-point exactness, for every method and every numerator chain. For the equity "
    "and price-per-share chains this is near-trivial. For the enterprise chain it is not: the "
    "implied enterprise value must come back to the observed enterprise value, subtracting net debt "
    "must return the market cap, and dividing by the PIT share count must return the close. That the "
    "same invariant holds across all three chains is what establishes the per-share metric, the net "
    "debt bridge and the share count are all the ones the observed multiple was built from. "
    "`identity_residual` computes it and the runner asserts it on every published scenario."
)

METRIC_HELD_AT_CURRENT_TTM = (
    "In v1 the per-share metric is the code-owned current TTM (or balance-sheet instant) value, and "
    "it is IDENTICAL across Bear, Base and Bull. Scenario separation comes entirely from the "
    "observed multiple. This is not an oversight and it is not a claim that fundamentals cannot "
    "move: D5-D2's brief §15 forbids a model-authored revenue or EPS forecast, D5-D0 §I admits "
    "management guidance only when metric, period, low, high and unit are all present, and no "
    "issuer in this pilot has a complete guidance range comparable to a valuation denominator. "
    "Projecting a measured historical growth rate forward would be a forecast this step is not "
    "authorized to make. So every scenario here is a RE-RATING scenario, `metric_moved` is False on "
    "all of them, and D5-D0 §L's requirement to report which operand moved is satisfied by saying "
    "plainly: the multiple moved, the fundamental did not. The limitation is that a Bear case built "
    "only on multiple compression does not price a fundamental deterioration, and an issuer whose "
    "earnings fall will breach its Bear anchor without the multiple compressing at all."
)


# -------------------------------------------------------------------------------------------------
# Observed multiple panel
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MultipleObservation:
    """One session's observed multiple for one method, with the operands it came from.

    The denominator's period end travels with the observation because `denominator_moved` needs it:
    whether a window's range is a multiple range or a price range is a question about how many
    distinct fundamentals the window divided by, and that cannot be recovered from the ratios alone.
    """

    session: date
    multiple: float
    numerator_value: float
    denominator_value: float
    denominator_period_end: date | None

    def __post_init__(self) -> None:
        assert self.multiple > 0.0, f"{self.session}: a non-positive multiple is not an observation"


class PanelWindow(StrEnum):
    """The candidate observation windows, as slices of the same PIT panel."""

    FULL_2Y = "FULL_2Y"
    RECENT_12M = "RECENT_12M"
    RECENT_6M = "RECENT_6M"


#: Sessions retained per window, counted back from the decision session. `None` means the whole
#: panel. 252 and 126 are the conventional session counts for a year and a half-year of US trading
#: days; they are calendar arithmetic, not values chosen after looking at an issuer.
WINDOW_SESSIONS: Mapping[PanelWindow, int | None] = {
    PanelWindow.FULL_2Y: None,
    PanelWindow.RECENT_12M: 252,
    PanelWindow.RECENT_6M: 126,
}

#: The percentiles a scenario set is drawn from. The extremes of a 500-session panel are single
#: sessions and a single session is an accident; the 10th and 90th are the edges of where the
#: multiple actually spent its time. Conventional, pre-registered, and not tuned to this sample.
BEAR_PERCENTILE = 10
BASE_PERCENTILE = 50
BULL_PERCENTILE = 90

#: A window needs this many observations before a percentile of it means anything. 60 sessions is
#: about one reported quarter of trading days - the shortest span over which a filed fundamental is
#: the current one - so it is the floor below which a "range" is one quarter's price action.
MIN_WINDOW_OBSERVATIONS = 60

#: And this many distinct denominator period ends, for `PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE`.
MIN_DISTINCT_DENOMINATOR_PERIODS = 2

#: Rank-correlation bands for the drift diagnostic. These are the conventional effect-size bands for
#: a monotone rank association, not thresholds fitted to an issuer's panel.
TREND_STRONG_RHO = 0.7
TREND_MODERATE_RHO = 0.4


class TrendClass(StrEnum):
    """How directionally the multiple drifted across the window. Diagnostic, never a gate."""

    RANGE_BOUND = "RANGE_BOUND"
    TRENDING_MODERATE = "TRENDING_MODERATE"
    TRENDING_STRONG = "TRENDING_STRONG"
    UNDETERMINED = "UNDETERMINED"
    """Too few observations to rank, or the multiple is constant."""


@dataclass(frozen=True)
class ObservedPercentile:
    """A percentile that is an actual observation, and the session it was observed on."""

    percentile: int
    multiple: float
    session: date
    rank: int
    n: int

    def to_dict(self) -> dict:
        return {"percentile": self.percentile, "multiple": self.multiple,
                "observed_on": self.session.isoformat(), "rank": self.rank, "n": self.n}


def nearest_rank_percentile(observations: Sequence[MultipleObservation],
                            percentile: int) -> ObservedPercentile | None:
    """The nearest-rank percentile: an element of `observations`, never a blend of two.

    The rank is `ceil(p/100 * n)` over the multiples sorted ascending, clamped into `[1, n]`, which
    is the classical nearest-rank definition. Ties are broken by session so the result is
    deterministic: two sessions at the same multiple are the same number but not the same provenance,
    and a target price whose provenance moved between runs would be a drift the audit should catch.
    """
    if not observations:
        return None
    if not 0 < percentile <= 100:
        raise ValueError(f"percentile out of range: {percentile}")
    ordered = sorted(observations, key=lambda o: (o.multiple, o.session))
    n = len(ordered)
    rank = max(1, min(n, math.ceil(percentile / 100.0 * n)))
    chosen = ordered[rank - 1]
    return ObservedPercentile(percentile=percentile, multiple=chosen.multiple,
                              session=chosen.session, rank=rank, n=n)


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, so ties do not fabricate an ordering the data does not have."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rank correlation, computed here rather than imported.

    Rank correlation rather than a fitted slope because the question is only whether the multiple
    drifted in one direction, and a slope would additionally assume a shape and carry units. Returns
    `None` when either series is constant, because a correlation with a zero-variance series is
    undefined and reporting 0.0 would read as "no drift" rather than "not measurable".
    """
    if len(xs) != len(ys):
        raise ValueError("series lengths differ")
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def trend_class(rho: float | None) -> TrendClass:
    if rho is None:
        return TrendClass.UNDETERMINED
    magnitude = abs(rho)
    if magnitude >= TREND_STRONG_RHO:
        return TrendClass.TRENDING_STRONG
    if magnitude >= TREND_MODERATE_RHO:
        return TrendClass.TRENDING_MODERATE
    return TrendClass.RANGE_BOUND


@dataclass(frozen=True)
class WindowStats:
    """One window of one method's observed panel: its range, its drift, and whether it may govern."""

    method: str
    window: PanelWindow
    n: int
    first_session: date | None
    last_session: date | None
    minimum: float | None
    maximum: float | None
    bear: ObservedPercentile | None
    base: ObservedPercentile | None
    bull: ObservedPercentile | None
    distinct_denominator_periods: int
    denominator_moved: bool
    rho: float | None
    trend: TrendClass
    current_multiple: float | None
    current_percentile_rank: float | None
    """Where the current multiple sits inside this window, as a percentage of observations at or
    below it. The honest read of "cheap versus its own history", and nothing more."""
    contract_eligible: bool
    ineligible_reason: str | None

    def to_dict(self) -> dict:
        return {
            "method": self.method, "window": self.window.value, "n": self.n,
            "first_session": None if self.first_session is None else self.first_session.isoformat(),
            "last_session": None if self.last_session is None else self.last_session.isoformat(),
            "min": self.minimum, "max": self.maximum,
            "bear_p%d" % BEAR_PERCENTILE: None if self.bear is None else self.bear.to_dict(),
            "base_p%d" % BASE_PERCENTILE: None if self.base is None else self.base.to_dict(),
            "bull_p%d" % BULL_PERCENTILE: None if self.bull is None else self.bull.to_dict(),
            "distinct_denominator_periods": self.distinct_denominator_periods,
            "denominator_moved": self.denominator_moved,
            "spearman_rho": self.rho, "trend": self.trend.value,
            "current_multiple": self.current_multiple,
            "current_percentile_rank": self.current_percentile_rank,
            "contract_eligible": self.contract_eligible,
            "ineligible_reason": self.ineligible_reason,
        }


def window_observations(observations: Sequence[MultipleObservation],
                        window: PanelWindow) -> tuple[MultipleObservation, ...]:
    """The window's slice, counted back in SESSIONS from the newest observation.

    Counted in sessions rather than in calendar days because the panel is a session series and a
    method's observations are not contiguous - a method whose denominator refuses for a stretch has
    gaps - so a calendar slice and a session slice are different windows. The session slice is the
    one `WINDOW_SESSIONS` names.
    """
    ordered = sorted(observations, key=lambda o: o.session)
    keep = WINDOW_SESSIONS[window]
    return tuple(ordered) if keep is None else tuple(ordered[-keep:])


def window_stats(observations: Sequence[MultipleObservation], *, method: str,
                 window: PanelWindow, current_multiple: float | None) -> WindowStats:
    """Everything the scenario set and the confidence rule need about one window. Pure and total."""
    rows = window_observations(observations, window)
    n = len(rows)
    periods = {o.denominator_period_end for o in rows if o.denominator_period_end is not None}
    distinct = len(periods)
    moved = distinct >= MIN_DISTINCT_DENOMINATOR_PERIODS
    multiples = [o.multiple for o in rows]
    rho = spearman_rho([i for i in range(n)], multiples) if n >= 3 else None

    rank_pct: float | None = None
    if n > 0 and current_multiple is not None:
        rank_pct = 100.0 * sum(1 for m in multiples if m <= current_multiple) / n

    reason: str | None = None
    if n < MIN_WINDOW_OBSERVATIONS:
        reason = (f"{n} observations, under the {MIN_WINDOW_OBSERVATIONS}-session floor: a "
                  f"percentile of this window is one quarter's price action")
    elif not moved:
        reason = (f"{distinct} distinct denominator period end(s): every observation divides by the "
                  f"same fundamental, so this range is the price range rescaled - see "
                  f"PRICE_ONLY_RANGE_IS_NOT_A_MULTIPLE_RANGE")

    return WindowStats(
        method=method, window=window, n=n,
        first_session=rows[0].session if rows else None,
        last_session=rows[-1].session if rows else None,
        minimum=min(multiples) if rows else None,
        maximum=max(multiples) if rows else None,
        bear=nearest_rank_percentile(rows, BEAR_PERCENTILE),
        base=nearest_rank_percentile(rows, BASE_PERCENTILE),
        bull=nearest_rank_percentile(rows, BULL_PERCENTILE),
        distinct_denominator_periods=distinct, denominator_moved=moved,
        rho=rho, trend=trend_class(rho),
        current_multiple=current_multiple, current_percentile_rank=rank_pct,
        contract_eligible=reason is None, ineligible_reason=reason,
    )


# -------------------------------------------------------------------------------------------------
# The per-share metric and the three numerator chains
# -------------------------------------------------------------------------------------------------

class MetricChain(StrEnum):
    """How a target multiple becomes a value per share. One chain per `Numerator`."""

    EQUITY_PER_SHARE = "EQUITY_PER_SHARE"
    """Market-cap numerator: the denominator is an equity-claim total, so per-share is
    `denominator / shares` and the fair value is that times the multiple. P/B, P/FCF."""
    ALREADY_PER_SHARE = "ALREADY_PER_SHARE"
    """Price-per-share numerator: the denominator is already per-share and no share count enters.
    P/E, and only P/E - see `multiples.PE_IS_PRICE_OVER_EPS`."""
    ENTERPRISE_BRIDGE = "ENTERPRISE_BRIDGE"
    """Enterprise-value numerator: the multiple implies an enterprise value, net debt bridges it to
    an equity value, and the share count divides. EV/Sales, EV/EBIT, EV/EBITDA, EV/FCF."""


_CHAIN_FOR_NUMERATOR: Mapping[Numerator, MetricChain] = {
    Numerator.MARKET_CAP: MetricChain.EQUITY_PER_SHARE,
    Numerator.PRICE_PER_SHARE: MetricChain.ALREADY_PER_SHARE,
    Numerator.ENTERPRISE_VALUE: MetricChain.ENTERPRISE_BRIDGE,
}


def metric_chain(method: str) -> MetricChain:
    """The chain for a method, read off `MULTIPLE_SPECS` rather than listed again here.

    Derived from D5-D1's spec table so a method cannot have one numerator there and a different
    bridge here. A new method added to `MULTIPLE_SPECS` with an unmapped numerator raises instead of
    silently taking a default chain.
    """
    spec = MULTIPLE_SPECS[method]
    return _CHAIN_FOR_NUMERATOR[spec.numerator]


class ScenarioRefusal(StrEnum):
    """Why one scenario produced no value. A refusal is an output; a wrong number is not."""

    NO_OBSERVED_MULTIPLE = "NO_OBSERVED_MULTIPLE"
    MISSING_SHARES = "MISSING_SHARES"
    MISSING_NET_DEBT = "MISSING_NET_DEBT"
    NEGATIVE_IMPLIED_EQUITY = "NEGATIVE_IMPLIED_EQUITY"
    """The implied enterprise value does not cover net debt. A negative fair value per share is the
    same class of defect as D5-D1's negative multiple and is refused for the same reason."""


class Scenario(StrEnum):
    BEAR = "BEAR"
    BASE = "BASE"
    BULL = "BULL"


_PERCENTILE_FOR_SCENARIO: Mapping[Scenario, int] = {
    Scenario.BEAR: BEAR_PERCENTILE,
    Scenario.BASE: BASE_PERCENTILE,
    Scenario.BULL: BULL_PERCENTILE,
}


@dataclass(frozen=True)
class ScenarioValue:
    """One scenario's fair value per share, or a named refusal. Never both.

    Mirrors `MultipleResult`'s discipline deliberately: `__post_init__` makes a refusal carrying a
    number unrepresentable rather than merely absent, so the "silent wrong value" class that D5-D1
    closed on multiples is closed the same way on fair values.
    """

    scenario: Scenario
    method: str
    chain: MetricChain
    refusal: ScenarioRefusal | None = None
    reason: str = ""
    target_multiple: float | None = None
    target_observed_on: date | None = None
    target_percentile: int | None = None
    target_window: PanelWindow | None = None
    metric_field: str | None = None
    metric_total: float | None = None
    metric_per_share: float | None = None
    metric_period_end: date | None = None
    shares: float | None = None
    net_debt: float | None = None
    implied_enterprise_value: float | None = None
    implied_equity_value: float | None = None
    value_per_share: float | None = None
    #: The brief's §14 and D5-D0 §L: which operand moved. In v1 the metric never does, because
    #: `METRIC_HELD_AT_CURRENT_TTM` holds the fundamental identical across the three scenarios.
    metric_moved: bool = False
    multiple_moved: bool = True

    def __post_init__(self) -> None:
        if self.refusal is None:
            assert self.value_per_share is not None, (
                f"{self.method} {self.scenario.value}: accepted with no value")
            assert self.value_per_share > 0.0, (
                f"{self.method} {self.scenario.value}: non-positive fair value published")
        else:
            assert self.value_per_share is None, (
                f"{self.method} {self.scenario.value}: {self.refusal.value} carries a value")

    @property
    def ok(self) -> bool:
        return self.refusal is None and self.value_per_share is not None

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario.value, "method": self.method, "chain": self.chain.value,
            "refusal": None if self.refusal is None else self.refusal.value,
            "reason": self.reason,
            "target_multiple": self.target_multiple,
            "target_observed_on": (None if self.target_observed_on is None
                                   else self.target_observed_on.isoformat()),
            "target_percentile": self.target_percentile,
            "target_window": None if self.target_window is None else self.target_window.value,
            "metric_field": self.metric_field, "metric_total": self.metric_total,
            "metric_per_share": self.metric_per_share,
            "metric_period_end": (None if self.metric_period_end is None
                                  else self.metric_period_end.isoformat()),
            "shares": self.shares, "net_debt": self.net_debt,
            "implied_enterprise_value": self.implied_enterprise_value,
            "implied_equity_value": self.implied_equity_value,
            "fair_value_per_share": self.value_per_share,
            "metric_moved": self.metric_moved, "multiple_moved": self.multiple_moved,
        }


def scenario_value(
    *,
    scenario: Scenario,
    method: str,
    target: ObservedPercentile | None,
    window: PanelWindow | None,
    denominator_value: float | None,
    denominator_field: str | None,
    denominator_period_end: date | None,
    shares: float | None,
    net_debt: float | None,
) -> ScenarioValue:
    """One scenario's fair value per share, by the chain the method's numerator dictates.

    The arithmetic is the whole point of the function and is written out rather than factored, so
    that a reader checking a published target price against the document sees the same three lines
    the code executes:

        EQUITY_PER_SHARE   value = (denominator / shares) x target
        ALREADY_PER_SHARE  value = denominator x target
        ENTERPRISE_BRIDGE  implied_ev = denominator x target
                           implied_equity = implied_ev - net_debt
                           value = implied_equity / shares

    `denominator_value` is the code-owned current fundamental, identical across the three scenarios -
    see `METRIC_HELD_AT_CURRENT_TTM`. It is never adjusted here, and this function has no parameter
    through which it could be.
    """
    chain = metric_chain(method)
    common = {"scenario": scenario, "method": method, "chain": chain}

    if target is None:
        return ScenarioValue(**common, refusal=ScenarioRefusal.NO_OBSERVED_MULTIPLE,
                             reason="no observed multiple at this percentile in the chosen window")
    if denominator_value is None:
        return ScenarioValue(**common, refusal=ScenarioRefusal.NO_OBSERVED_MULTIPLE,
                             reason="the current fundamental denominator did not resolve")

    provenance = {
        "target_multiple": target.multiple, "target_observed_on": target.session,
        "target_percentile": target.percentile, "target_window": window,
        "metric_field": denominator_field, "metric_total": denominator_value,
        "metric_period_end": denominator_period_end,
    }

    if chain is MetricChain.ALREADY_PER_SHARE:
        value = denominator_value * target.multiple
        return ScenarioValue(**common, **provenance, metric_per_share=denominator_value,
                             value_per_share=value,
                             reason="per-share denominator times the observed multiple")

    if shares is None or shares <= 0.0:
        return ScenarioValue(**common, refusal=ScenarioRefusal.MISSING_SHARES,
                             reason="no PIT share count, so a total cannot become a per-share value")

    if chain is MetricChain.EQUITY_PER_SHARE:
        per_share = denominator_value / shares
        return ScenarioValue(**common, **provenance, shares=shares, metric_per_share=per_share,
                             value_per_share=per_share * target.multiple,
                             reason="equity-claim denominator per share times the observed multiple")

    if net_debt is None:
        return ScenarioValue(**common, refusal=ScenarioRefusal.MISSING_NET_DEBT,
                             reason="an enterprise multiple implies an enterprise value, and "
                                    "without net debt it cannot be bridged to an equity value")

    implied_ev = denominator_value * target.multiple
    implied_equity = implied_ev - net_debt
    if implied_equity <= 0.0:
        return ScenarioValue(**common, refusal=ScenarioRefusal.NEGATIVE_IMPLIED_EQUITY,
                             reason=f"implied enterprise value {implied_ev:,.0f} does not cover net "
                                    f"debt {net_debt:,.0f}; a non-positive fair value per share is "
                                    f"refused rather than published")
    return ScenarioValue(**common, **provenance, shares=shares, net_debt=net_debt,
                         metric_per_share=denominator_value / shares,
                         implied_enterprise_value=implied_ev, implied_equity_value=implied_equity,
                         value_per_share=implied_equity / shares,
                         reason="implied enterprise value less net debt, per share")


def identity_residual(*, method: str, current_multiple: float, denominator_value: float,
                      shares: float | None, net_debt: float | None,
                      price: float) -> float | None:
    """`FAIR_VALUE_AT_CURRENT_MULTIPLE_IS_PRICE`, as a number the audit can assert on.

    Runs the real chain at `current_multiple` and returns the absolute difference from `price`.
    `None` when the chain cannot run at all. A residual above a few multiples of machine epsilon
    times the price means the per-share metric, the net debt bridge or the share count is not the one
    the observed multiple was built from - which is the only way a fair value can be wrong while
    every individual operand is right.
    """
    chain = metric_chain(method)
    if chain is MetricChain.ALREADY_PER_SHARE:
        return abs(denominator_value * current_multiple - price)
    if shares is None or shares <= 0.0:
        return None
    if chain is MetricChain.EQUITY_PER_SHARE:
        return abs((denominator_value / shares) * current_multiple - price)
    if net_debt is None:
        return None
    implied = denominator_value * current_multiple - net_debt
    return abs(implied / shares - price)


# -------------------------------------------------------------------------------------------------
# Fair value range, TP1 / TP2, upside
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FairValueRange:
    """D5-D0 §K: a range, never a point. The three scenarios of ONE method."""

    method: str
    window: PanelWindow
    bear: ScenarioValue
    base: ScenarioValue
    bull: ScenarioValue

    @property
    def scenarios(self) -> tuple[ScenarioValue, ScenarioValue, ScenarioValue]:
        return (self.bear, self.base, self.bull)

    @property
    def complete(self) -> bool:
        """All three scenarios valued. A range missing a leg is not a range."""
        return all(s.ok for s in self.scenarios)

    @property
    def ordered(self) -> bool:
        """Bear <= Base <= Bull. Guaranteed by the percentiles for every chain except the enterprise
        bridge, where subtracting a constant net debt preserves order too - so a violation means an
        operand differed between scenarios, which `METRIC_HELD_AT_CURRENT_TTM` forbids."""
        if not self.complete:
            return True
        values = [s.value_per_share for s in self.scenarios]
        return values[0] <= values[1] <= values[2]

    def to_dict(self) -> dict:
        return {"method": self.method, "window": self.window.value,
                "complete": self.complete, "ordered": self.ordered,
                "bear": self.bear.to_dict(), "base": self.base.to_dict(),
                "bull": self.bull.to_dict(),
                "range_low": self.bear.value_per_share, "range_base": self.base.value_per_share,
                "range_high": self.bull.value_per_share}


@dataclass(frozen=True)
class TargetPrices:
    """D5-D0 §L, applied. TP1 and TP2 are the Base and Bull legs of the PRIMARY method's range.

    `TP2 is not TP1 plus a margin` is structural here rather than promised: TP2 is a different
    observed multiple applied to the same metric, and `tp2_operand_that_moved` names which operand
    differs - in v1 always the multiple, because the metric is held.
    """

    primary_method: str
    window: PanelWindow
    current_price: float
    tp1: float | None
    tp2: float | None
    bear_anchor: float | None
    upside_to_tp1: float | None
    upside_to_tp2: float | None
    downside_to_bear: float | None
    tp1_multiple: float | None
    tp2_multiple: float | None
    bear_multiple: float | None
    tp1_observed_on: date | None
    tp2_observed_on: date | None
    bear_observed_on: date | None
    tp2_operand_that_moved: str

    def to_dict(self) -> dict:
        return {
            "primary_method": self.primary_method, "window": self.window.value,
            "current_price": self.current_price,
            "TP1": self.tp1, "TP2": self.tp2, "BEAR_ANCHOR": self.bear_anchor,
            "upside_to_TP1": self.upside_to_tp1, "upside_to_TP2": self.upside_to_tp2,
            "downside_to_bear": self.downside_to_bear,
            "TP1_multiple": self.tp1_multiple, "TP2_multiple": self.tp2_multiple,
            "bear_multiple": self.bear_multiple,
            "TP1_multiple_observed_on": (None if self.tp1_observed_on is None
                                         else self.tp1_observed_on.isoformat()),
            "TP2_multiple_observed_on": (None if self.tp2_observed_on is None
                                         else self.tp2_observed_on.isoformat()),
            "bear_multiple_observed_on": (None if self.bear_observed_on is None
                                          else self.bear_observed_on.isoformat()),
            "TP2_operand_that_moved": self.tp2_operand_that_moved,
        }


def target_prices(fv: FairValueRange, *, current_price: float) -> TargetPrices:
    """D5-D0 §L's three levels and their three upsides. Arithmetic only.

    `upside = level / current_unadjusted_close - 1`, exactly as §L writes it. The close is the
    unadjusted regular-session close the observed multiple was computed from, so the upside is
    measured against the same price the denominator was divided into - mixing an adjusted close in
    here would silently rescale every target by the issuer's dividend history.
    """
    if current_price <= 0.0:
        raise ValueError("a non-positive close cannot anchor an upside")

    def upside(level: float | None) -> float | None:
        return None if level is None else level / current_price - 1.0

    moved: list[str] = []
    if fv.base.ok and fv.bull.ok:
        if fv.bull.target_multiple != fv.base.target_multiple:
            moved.append("multiple")
        if fv.bull.metric_per_share != fv.base.metric_per_share:
            moved.append("metric")
    return TargetPrices(
        primary_method=fv.method, window=fv.window, current_price=current_price,
        tp1=fv.base.value_per_share, tp2=fv.bull.value_per_share,
        bear_anchor=fv.bear.value_per_share,
        upside_to_tp1=upside(fv.base.value_per_share),
        upside_to_tp2=upside(fv.bull.value_per_share),
        downside_to_bear=upside(fv.bear.value_per_share),
        tp1_multiple=fv.base.target_multiple, tp2_multiple=fv.bull.target_multiple,
        bear_multiple=fv.bear.target_multiple,
        tp1_observed_on=fv.base.target_observed_on, tp2_observed_on=fv.bull.target_observed_on,
        bear_observed_on=fv.bear.target_observed_on,
        tp2_operand_that_moved="+".join(moved) if moved else "neither",
    )


# -------------------------------------------------------------------------------------------------
# Primary / secondary reconciliation (§22)
# -------------------------------------------------------------------------------------------------

#: Above this ratio between two methods' Base fair values, the two are not cross-checking each other
#: - they are contradicting. 1.5x is a statement about what a valuation is for: two methods that
#: disagree by half again on what a share is worth cannot both inform the same decision, and
#: averaging them would produce a number neither method supports. Pre-registered, not tuned.
VALUATION_CONFLICT_RATIO = 1.5

NEVER_AVERAGED = (
    "A primary and a secondary fair value are reported side by side and are never averaged, blended "
    "or reconciled into a single number. The brief's §22 forbids it and the reason is that the mean "
    "of two methods is supported by neither: if P/FCF says 120 and EV/Sales says 240, the business "
    "is not worth 180 on any argument either method makes. The primary governs TP1 and TP2, the "
    "secondary is a cross-check that either corroborates or raises VALUATION_CONFLICT, and a "
    "conflict is a finding reported to D6 rather than an arithmetic problem to be smoothed away."
)


class ReconciliationStatus(StrEnum):
    NO_SECONDARY = "NO_SECONDARY"
    CORROBORATES = "CORROBORATES"
    VALUATION_CONFLICT = "VALUATION_CONFLICT"


@dataclass(frozen=True)
class Reconciliation:
    status: ReconciliationStatus
    primary_method: str
    secondary_method: str | None
    primary_base: float | None
    secondary_base: float | None
    ratio: float | None
    correlated: bool
    correlation_reason: str | None
    reason: str

    def to_dict(self) -> dict:
        return {"status": self.status.value, "primary_method": self.primary_method,
                "secondary_method": self.secondary_method, "primary_base": self.primary_base,
                "secondary_base": self.secondary_base, "ratio": self.ratio,
                "correlated": self.correlated, "correlation_reason": self.correlation_reason,
                "reason": self.reason}


def methods_are_correlated(method_a: str, method_b: str, *, net_debt: float | None,
                           enterprise_value: float | None) -> tuple[bool, str | None]:
    """§25. Whether two methods are one reading reported twice rather than two independent ones.

    The test is on the DENOMINATOR first, because that is where a method observes the business.
    D5-D1 measured this precisely on ADBE and the finding is the rule here: "ADBE's six multiples
    use five distinct denominators - equity, FCF, revenue, operating income and derived EBITDA - and
    only the FCF pair is duplicated. So six methods is not six independent readings, but the overlap
    is on the numerator side and the denominators remain genuinely different."

    So:

    - **Different denominator fields -> independent.** EV/EBIT against P/FCF reads operating income
      against free cash flow. Those are different facts about the business even when the two
      numerators are nearly the same number, and calling them one reading would discard the
      cross-check that actually exists.
    - **Same denominator field and the same numerator -> the same method.**
    - **Same denominator field, different numerators -> correlated only when net debt is immaterial.**
      This is the real §25 case. ADBE's P/FCF 9.6862x and EV/FCF 9.6748x divide the identical free
      cash flow by an enterprise value that is its market cap to within 0.1%, so the pair is one
      number printed twice. WBD divides the identical free cash flow too, and reports P/FCF 32.33x
      against EV/FCF 45.47x, because its net debt is 29% of enterprise value: there the pair is one
      fundamental read at two different points in the capital structure, which is a genuine second
      reading of how the leverage is carried rather than a duplicate.

    `MATERIAL_NET_DEBT_SHARE_OF_EV` is D5-D1's constant, imported rather than restated. It governs
    the suitability diagnostic there and the independence test here, and those are two conclusions
    from one measurement rather than two thresholds that could drift apart.
    """
    from app.backtest.strategy_h_v2.valuation.multiples import MATERIAL_NET_DEBT_SHARE_OF_EV

    spec_a, spec_b = MULTIPLE_SPECS[method_a], MULTIPLE_SPECS[method_b]
    if spec_a.denominator_field != spec_b.denominator_field:
        return False, None
    if spec_a.numerator is spec_b.numerator:
        return True, (f"both are {spec_a.numerator.value} over {spec_a.denominator_field}: the same "
                      f"method under two names")
    if net_debt is None or not enterprise_value:
        return True, (f"both divide by {spec_a.denominator_field} and net debt does not resolve, so "
                      f"whether the two numerators differ materially cannot be established - "
                      f"fail-closed to correlated")
    share = abs(net_debt) / abs(enterprise_value)
    if share < MATERIAL_NET_DEBT_SHARE_OF_EV:
        return True, (f"both divide by {spec_a.denominator_field}, and net debt is {share:.1%} of "
                      f"enterprise value - under the {MATERIAL_NET_DEBT_SHARE_OF_EV:.0%} "
                      f"materiality line - so the two numerators are the same number and this is "
                      f"one reading printed twice")
    return False, (f"both divide by {spec_a.denominator_field}, but net debt is {share:.1%} of "
                   f"enterprise value, so the equity and enterprise numerators are materially "
                   f"different: one fundamental read at two points in the capital structure")


def reconcile(*, primary: FairValueRange, secondary: FairValueRange | None,
              net_debt: float | None, enterprise_value: float | None) -> Reconciliation:
    """§22. Report both, average neither, and name a conflict when there is one."""
    if secondary is None or not secondary.base.ok or not primary.base.ok:
        return Reconciliation(
            status=ReconciliationStatus.NO_SECONDARY, primary_method=primary.method,
            secondary_method=None if secondary is None else secondary.method,
            primary_base=primary.base.value_per_share,
            secondary_base=None if secondary is None else secondary.base.value_per_share,
            ratio=None, correlated=False, correlation_reason=None,
            reason="no secondary method produced a Base fair value, so there is nothing to "
                   "cross-check against; see VALUATION_CONFIDENCE for what that costs")

    a = primary.base.value_per_share
    b = secondary.base.value_per_share
    assert a is not None and b is not None
    ratio = max(a, b) / min(a, b)
    correlated, correlation_reason = methods_are_correlated(
        primary.method, secondary.method, net_debt=net_debt, enterprise_value=enterprise_value)
    conflict = ratio > VALUATION_CONFLICT_RATIO
    return Reconciliation(
        status=(ReconciliationStatus.VALUATION_CONFLICT if conflict
                else ReconciliationStatus.CORROBORATES),
        primary_method=primary.method, secondary_method=secondary.method,
        primary_base=a, secondary_base=b, ratio=ratio,
        correlated=correlated, correlation_reason=correlation_reason,
        reason=(f"the two Base fair values differ by {ratio:.2f}x, over the "
                f"{VALUATION_CONFLICT_RATIO:.2f}x line: reported separately and never averaged"
                if conflict else
                f"the two Base fair values agree to within {ratio:.2f}x"),
    )


# -------------------------------------------------------------------------------------------------
# Confidence and the issuer-level verdict (§23, §28, §30)
# -------------------------------------------------------------------------------------------------

class ValuationConfidence(StrEnum):
    """§28. How much weight the valuation carries, which is not how cheap the issuer looks."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NOT_READY = "NOT_READY"


class ValuationStatus(StrEnum):
    """§30. Whether a fair value exists for this issuer at all."""

    VALUED = "VALUED"
    VALUATION_NOT_READY = "VALUATION_NOT_READY"


CONFIDENCE_IS_NOT_ATTRACTIVENESS = (
    "`ValuationConfidence` says how much to trust the fair value, never how good the investment is. "
    "A HIGH-confidence valuation can say the issuer is expensive and a LOW-confidence one can show "
    "large upside; reading the label as a score would make D5 the ranking engine D5-D0's "
    "`VALUATION_IS_NOT_A_RANKING_ENGINE` forbids. It is also not a probability: nothing in D5 is "
    "calibrated against an outcome, because D5 reads no outcomes."
)

NOT_READY_IS_AN_OUTPUT = (
    "`VALUATION_NOT_READY` is a complete, correct D5-D2 output for an issuer and is not a failure "
    "of the issuer or of the pilot. D5-D0 §N froze the sentence and D4-BR's abstention repair is "
    "where the lesson was learned: an honest abstention is an output, and only an empty output is a "
    "failure. The model is never asked for a target price on a NOT_READY issuer, and a narrative "
    "that implies one anyway is the defect this sentence exists to name."
)


@dataclass(frozen=True)
class ConfidenceAssessment:
    confidence: ValuationConfidence
    drivers: tuple[str, ...]
    """Every rule that fired, in the order evaluated. A confidence with no stated driver is a
    judgement wearing a label, which is what this field exists to prevent."""

    def to_dict(self) -> dict:
        return {"confidence": self.confidence.value, "drivers": list(self.drivers)}


def valuation_confidence(
    *,
    valued: bool,
    independent_method_count: int,
    total_method_count: int,
    reconciliation: ReconciliationStatus,
    trend: TrendClass,
    window: PanelWindow,
    stale_inputs: Sequence[str],
    share_class_high_confidence: bool,
    peer_context_available: bool,
) -> ConfidenceAssessment:
    """§28's inputs, each as an explicit demotion with a stated reason. Pure and total.

    Built as a ceiling that each adverse finding lowers, rather than as a score, because a score
    would let two unrelated weaknesses cancel an unrelated strength. The ceiling starts at HIGH and
    only ever falls; the drivers list is the audit trail of why it landed where it did.

    `peer_context_available` is False for every issuer in this pilot and the parameter exists anyway,
    because a later step that acquires peers must not have to change this signature to use them -
    and because a confidence rule that silently omits the missing input would report HIGH on
    evidence it never had.
    """
    if not valued:
        return ConfidenceAssessment(ValuationConfidence.NOT_READY,
                                    ("no economically suitable method produced a fair value",))

    ceiling = ValuationConfidence.HIGH
    drivers: list[str] = []
    order = [ValuationConfidence.HIGH, ValuationConfidence.MEDIUM, ValuationConfidence.LOW]

    def demote(to: ValuationConfidence, why: str) -> None:
        nonlocal ceiling
        drivers.append(why)
        if order.index(to) > order.index(ceiling):
            ceiling = to

    if total_method_count < 2:
        demote(ValuationConfidence.LOW,
               "a single method: D5-D0 §N's 'one method is a number, two are a valuation' - there "
               "is nothing to cross-check this fair value against")
    elif independent_method_count < 2:
        demote(ValuationConfidence.LOW,
               "every available secondary is correlated with the primary (§25), so the second "
               "number is the first one restated and the cross-check is cosmetic")

    if reconciliation is ReconciliationStatus.VALUATION_CONFLICT:
        demote(ValuationConfidence.LOW,
               "primary and secondary disagree past VALUATION_CONFLICT_RATIO: the methods do not "
               "agree on what the share is worth and neither is confirmed")

    if trend is TrendClass.TRENDING_STRONG:
        demote(ValuationConfidence.LOW,
               "the observed multiple drifted strongly in one direction across the window, so its "
               "percentiles describe a transition rather than a level the issuer returns to - see "
               "DEAD_REGIME_IS_THE_CENTRAL_TRAP")
    elif trend is TrendClass.TRENDING_MODERATE:
        demote(ValuationConfidence.MEDIUM,
               "the observed multiple drifted moderately across the window, so its median is a "
               "weaker statement about a normal level than a range-bound panel's would be")
    elif trend is TrendClass.UNDETERMINED:
        demote(ValuationConfidence.LOW,
               "the drift is not measurable on this window, so whether its percentiles describe a "
               "level or a transition is unknown rather than favourable")

    if stale_inputs:
        demote(ValuationConfidence.MEDIUM,
               f"stale input(s) in the valuation chain: {', '.join(sorted(stale_inputs))}")

    if not share_class_high_confidence:
        demote(ValuationConfidence.MEDIUM,
               "the share-class determination is not HIGH confidence, and every per-share figure "
               "divides by the share count it gates")

    if not peer_context_available:
        demote(ValuationConfidence.MEDIUM,
               "PEER_CONTEXT_UNAVAILABLE: the only external check on whether this issuer's own "
               "multiple range is itself reasonable is absent, so the valuation is anchored "
               "entirely to the issuer's own two-year history")

    if window is PanelWindow.FULL_2Y:
        drivers.append("window FULL_2Y: the longest history the local panel holds, which D5-D0 §H "
                       "bounds at one macro regime - LONG_TERM_HISTORICAL_RANGE is not claimed")
    else:
        drivers.append(f"window {window.value}: a deliberate restriction of the observed range, "
                       f"argued per issuer rather than defaulted to")

    return ConfidenceAssessment(ceiling, tuple(drivers))


RECENT_2Y_CONTEXT_ONLY = (
    "Every historical statement this module makes is labelled by the window it came from - "
    "FULL_2Y, RECENT_12M or RECENT_6M - over a panel of at most 501 sessions from 2024-09-17 to "
    "2026-09-16. `LONG_TERM_HISTORICAL_RANGE` is a forbidden label in v1 and `LONG_TERM "
    "HISTORICAL NORMAL` is forbidden phrasing in the document, both per D5-D0 §H and §12 of this "
    "step's brief: the paths that would extend the panel to five or ten years were priced and never "
    "purchased, and no amount of care with a two-year window turns it into a cycle."
)

PEER_CONTEXT_UNAVAILABLE = (
    "No issuer in this pilot has a peer set. D5-D0 §H requires peer eligibility to be argued against "
    "five measured criteria - business, margin regime, growth regime on the same period family, "
    "capital intensity, and the same method computable on the peer from code-owned fields - and "
    "`PEER_MIN_ELIGIBLE = 3`. The twelve-issuer sample is a seeded hash over the whole package "
    "universe and contains no two issuers in the same business, so a peer set would have to come "
    "from resolving multiples across a far wider universe. That is a new project, §13 of this "
    "step's brief forbids building a peer engine here, and §33 forbids opening it as a rabbit hole. "
    "So the label is PEER_CONTEXT_UNAVAILABLE, it is a stated absence rather than a silent one, and "
    "it costs every issuer a confidence demotion in `valuation_confidence`."
)

GUIDANCE_UNAVAILABLE = (
    "No scenario input in this pilot comes from management guidance. D5-D0 §I admits guidance only "
    "when metric, period, low, high and unit are all present and the period is comparable to the "
    "denominator the scenario uses, reusing D4-BR's `REQUIRED_OPERANDS` unchanged. The guidance "
    "extraction this repository owns serves D4's expectation gap - guidance RANGE CHANGES and "
    "results versus prior guidance - and does not produce a forward revenue or EPS LEVEL on a "
    "period comparable to a TTM valuation denominator. A one-sided floor is not a range and a "
    "missing bound may not be interpolated, so the honest state is UNKNOWN, which D5-D0 §I names as "
    "a valid scenario state."
)
