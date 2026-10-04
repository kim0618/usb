"""H-V2-D5-D1: the seven valuation multiples, computed from the primitives P0/P0.1/P1/P1.1 built.

This is the first step in D5 that publishes a number with a price in it. Everything before it
answered "does the input exist"; this answers "what is the ratio, and when there is none, why".

What it is not. A multiple here is an observation of the current market price against a trailing
fundamental - `NO_FAIR_VALUE_HERE`. No target multiple is chosen, no fair value is formed, no
scenario is run and no target price is computed: those are D5-D2's and are absent from this module
by construction rather than by convention, because there is no field on `MultipleResult` that could
hold one.

The three design decisions, each of which is a trap this step had to avoid:

1. **A refused input must refuse the multiple, with the refusal's own name.** Every numerator and
   denominator arrives as a resolution object carrying a status, and `MultipleStatus` is a mapping of
   those statuses onto the brief's vocabulary rather than a re-derivation. Nothing in this module
   reads a `.value` without first reading the `.status` that qualifies it, which is what makes
   "silent wrong number" a structural impossibility here and not a review item.

2. **A negative denominator is not a cheap multiple.** `denominator_applicable` is D5-D0's frozen
   rule and is called rather than reimplemented: zero, negative, or a denominator whose period is
   not a full year yields `NEGATIVE_DENOMINATOR`, never a negative ratio. A P/E of -14 reads like a
   bargain and means a loss.

3. **P/E is price over EPS, not market cap over EPS.** `market_cap / eps_diluted` is dollars divided
   by dollars-per-share, which is a share count wearing a multiple's name. D5-D0's `METHOD_SPECS`
   defines P/E as `market_cap / net_income`; §13 of this step's brief directs that P/E flow through
   P0.1's TTM EPS contract instead, and the per-share price is the only numerator dimensionally
   compatible with a per-share denominator. See `PE_IS_PRICE_OVER_EPS` for what that costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Mapping

from app.backtest.strategy_h_v2.valuation.capital_structure import (
    CapitalStructureStatus,
    EnterpriseValueResolution,
    InstantResolution,
    InstantStatus,
)
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import (
    MIN_METHODS_FOR_COMPLETE,
    MethodId,
    PeriodFamily,
    ValuationCompleteness,
    denominator_applicable,
    period_family,
    valuation_completeness,
)
from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
    MarketCapResolution,
    MarketCapStatus,
)
from app.backtest.strategy_h_v2.valuation.ttm import (
    EBITDA_DERIVED_LABEL,
    TtmResult,
    TtmStatus,
    ebitda_feasible,
)

MULTIPLES_CONTRACT_VERSION = "h_v2_d5_d1_valuation_multiples_v1"

NO_FAIR_VALUE_HERE = (
    "Every number this module produces is an OBSERVED current multiple: the market's price today "
    "over a trailing fundamental the filer reported. It is not a fair value, not a target multiple "
    "and not a judgement that the issuer is cheap or expensive. D5-D0's NO_UNIVERSAL_MULTIPLE "
    "governs what a later step may do with it - a fair value may use an observed range, never a "
    "multiple supplied from a prior - and this step supplies the observation and stops."
)

NEVER_COMPUTED: tuple[str, ...] = (
    "a multiple from a numerator or denominator whose resolution status is not OK",
    "a negative or zero-denominator multiple, in any method, for any reason",
    "a multiple from a partial-period denominator: see VALID_MULTIPLE_DENOMINATOR_FAMILIES",
    "a multiple whose numerator and denominator units are not dimensionally compatible",
    "an EBITDA from operating income and D&A over two different periods",
    "a target multiple, a fair value, a target price, a scenario or a recommendation",
)


# -------------------------------------------------------------------------------------------------
# Status vocabulary
# -------------------------------------------------------------------------------------------------

class MultipleStatus(StrEnum):
    """One method's outcome for one issuer. The brief's §15 names, and no others."""

    OK = "OK"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """Structurally undefined for this issuer for a reason that is not a missing input."""
    MISSING_INPUT = "MISSING_INPUT"
    """An input does not exist, or is not known at the decision time."""
    STALE_INPUT = "STALE_INPUT"
    """An input exists and its period ended too long before the decision date to describe today."""
    NEGATIVE_DENOMINATOR = "NEGATIVE_DENOMINATOR"
    """The denominator resolved and is <= 0. Reported in its own right rather than folded into
    NOT_APPLICABLE, because the two mean different things to a reader: this issuer HAS the data and
    is loss-making or cash-burning, which is a fact about the business. It buckets as NOT_APPLICABLE
    in the cross-sectional count - see `MultipleResult.bucket` - because the method is structurally
    inapplicable either way and §21 asks for three buckets."""
    MARKET_CAP_UNAVAILABLE = "MARKET_CAP_UNAVAILABLE"
    """Market cap is UNKNOWN for a reason other than share class: shares or price."""
    EV_UNAVAILABLE = "EV_UNAVAILABLE"
    """Enterprise value is UNKNOWN for a reason the finer statuses do not name."""
    MULTI_CLASS_UNRESOLVED = "MULTI_CLASS_UNRESOLVED"
    """The share-class determination the numerator needs is absent or is multi-class."""
    PERIOD_MISMATCH = "PERIOD_MISMATCH"
    """Two denominator legs exist over periods that do not compose. Derived EBITDA only."""
    UNKNOWN = "UNKNOWN"
    """A named refusal with no finer bucket: an ambiguous tag set, a unit or tag mismatch, a sign
    convention contradiction. The reason always carries the originating status."""


class MultipleBucket(StrEnum):
    """§21's three cross-sectional buckets. Every status maps to exactly one."""

    OK = "OK"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MISSING = "MISSING"


_BUCKETS: Mapping[MultipleStatus, MultipleBucket] = {
    MultipleStatus.OK: MultipleBucket.OK,
    MultipleStatus.NOT_APPLICABLE: MultipleBucket.NOT_APPLICABLE,
    MultipleStatus.NEGATIVE_DENOMINATOR: MultipleBucket.NOT_APPLICABLE,
    MultipleStatus.MISSING_INPUT: MultipleBucket.MISSING,
    MultipleStatus.STALE_INPUT: MultipleBucket.MISSING,
    MultipleStatus.MARKET_CAP_UNAVAILABLE: MultipleBucket.MISSING,
    MultipleStatus.EV_UNAVAILABLE: MultipleBucket.MISSING,
    MultipleStatus.MULTI_CLASS_UNRESOLVED: MultipleBucket.MISSING,
    MultipleStatus.PERIOD_MISMATCH: MultipleBucket.MISSING,
    MultipleStatus.UNKNOWN: MultipleBucket.MISSING,
}


class Numerator(StrEnum):
    MARKET_CAP = "MARKET_CAP"
    ENTERPRISE_VALUE = "ENTERPRISE_VALUE"
    PRICE_PER_SHARE = "PRICE_PER_SHARE"
    """P/E's numerator, and only P/E's. See `PE_IS_PRICE_OVER_EPS`."""


class DenominatorKind(StrEnum):
    TTM_FLOW = "TTM_FLOW"
    """A constructed trailing-twelve-month flow from P0.1."""
    INSTANT = "INSTANT"
    """A balance-sheet value at one date from P1, inside P1's staleness bound."""
    DERIVED = "DERIVED"
    """Composed here from two TTM flows over one identical period. EBITDA only."""


# -------------------------------------------------------------------------------------------------
# Method specs
# -------------------------------------------------------------------------------------------------

#: The seven methods, in the order §2 of the brief lists them, which is also the report's column
#: order. The keys are the strings P1's `EV_METHOD_DENOMINATORS` and `method_feasibility` already
#: established, so this step's coverage table and P1.1's feasibility table name the same methods.
METHOD_ORDER: tuple[str, ...] = (
    "P/B", "P/FCF", "EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF", "P/E",
)


@dataclass(frozen=True)
class MultipleSpec:
    """One method's arithmetic, stated once. No method has a second definition anywhere."""

    method: str
    numerator: Numerator
    denominator_kind: DenominatorKind
    denominator_field: str
    numerator_unit: str
    denominator_unit: str
    d0_method: MethodId | None
    """The frozen `METHOD_SPECS` entry this corresponds to, or None where D5-D0 has no entry.

    `EV/FCF` is None: D5-D0's twelve candidates include `FCF_YIELD` and no EV/FCF, and the method
    was introduced by P1's `EV_METHOD_DENOMINATORS`. §2 of this brief names it, so it is computed
    here and the divergence is recorded rather than patched into D5-D0's frozen tuple."""

    def to_dict(self) -> dict:
        return {"method": self.method, "numerator": self.numerator.value,
                "denominator_kind": self.denominator_kind.value,
                "denominator_field": self.denominator_field,
                "numerator_unit": self.numerator_unit, "denominator_unit": self.denominator_unit,
                "d0_method": None if self.d0_method is None else self.d0_method.value}


MULTIPLE_SPECS: Mapping[str, MultipleSpec] = {
    spec.method: spec for spec in (
        MultipleSpec("P/B", Numerator.MARKET_CAP, DenominatorKind.INSTANT, "equity",
                     "USD", "USD", MethodId.PB),
        MultipleSpec("P/FCF", Numerator.MARKET_CAP, DenominatorKind.TTM_FLOW, "free_cash_flow",
                     "USD", "USD", MethodId.P_FCF),
        MultipleSpec("EV/Sales", Numerator.ENTERPRISE_VALUE, DenominatorKind.TTM_FLOW, "revenue",
                     "USD", "USD", MethodId.EV_SALES),
        MultipleSpec("EV/EBIT", Numerator.ENTERPRISE_VALUE, DenominatorKind.TTM_FLOW,
                     "operating_income", "USD", "USD", MethodId.EV_EBIT),
        MultipleSpec("EV/EBITDA", Numerator.ENTERPRISE_VALUE, DenominatorKind.DERIVED,
                     EBITDA_DERIVED_LABEL, "USD", "USD", MethodId.EV_EBITDA),
        MultipleSpec("EV/FCF", Numerator.ENTERPRISE_VALUE, DenominatorKind.TTM_FLOW,
                     "free_cash_flow", "USD", "USD", None),
        MultipleSpec("P/E", Numerator.PRICE_PER_SHARE, DenominatorKind.TTM_FLOW, "eps_diluted",
                     "USD/shares", "USD/shares", MethodId.PE),
    )
}

assert tuple(MULTIPLE_SPECS) == METHOD_ORDER, "the spec table and the report order must agree"

PE_IS_PRICE_OVER_EPS = (
    "P/E is the unadjusted regular-session close divided by P0.1's TTM diluted EPS. Market cap over "
    "EPS is dimensionally a share count, and net income over a reconstructed share count is "
    "forbidden by §13 of this brief and by `ttm.NEVER_CONSTRUCTED` before it. Two consequences are "
    "recorded rather than repaired. First, D5-D0's `METHOD_SPECS` states P/E's denominator as net "
    "income, so D5-D1's P/E is a narrower method than D5-D0 described, and D5-D0's frozen tuple is "
    "left untouched. Second, the gate below is stricter than this ratio's own inputs: price over EPS "
    "needs a price and a share-class determination and NO share count, yet P/E is gated on the "
    "market cap being OK, which also requires PIT shares inside 135 days. That is fail-closed and "
    "consistent with P1's `method_feasibility`, and it makes P/E coverage here a LOWER bound - "
    "`pe_blocked_only_by_shares` counts the issuers it costs."
)


# -------------------------------------------------------------------------------------------------
# Numerator and denominator gates
# -------------------------------------------------------------------------------------------------

_MARKET_CAP_REFUSALS: Mapping[MarketCapStatus, MultipleStatus] = {
    MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED: MultipleStatus.MULTI_CLASS_UNRESOLVED,
    MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES: MultipleStatus.MULTI_CLASS_UNRESOLVED,
    MarketCapStatus.UNKNOWN_SHARES: MultipleStatus.MARKET_CAP_UNAVAILABLE,
    MarketCapStatus.UNKNOWN_PRICE: MultipleStatus.MARKET_CAP_UNAVAILABLE,
}

#: An EV refusal reported under the name of the leg that caused it. Reporting every one of these as
#: EV_UNAVAILABLE would be true and useless: it would hide that EV/Sales is blocked on a stale
#: balance sheet for one issuer and on an undetermined share class for another, which are different
#: problems with different owners.
_EV_REFUSALS: Mapping[CapitalStructureStatus, MultipleStatus] = {
    CapitalStructureStatus.MULTI_CLASS_UNKNOWN: MultipleStatus.MULTI_CLASS_UNRESOLVED,
    CapitalStructureStatus.MARKET_CAP_UNKNOWN: MultipleStatus.MARKET_CAP_UNAVAILABLE,
    CapitalStructureStatus.MISSING_CASH: MultipleStatus.MISSING_INPUT,
    CapitalStructureStatus.MISSING_DEBT: MultipleStatus.MISSING_INPUT,
    CapitalStructureStatus.STALE_CASH: MultipleStatus.STALE_INPUT,
    CapitalStructureStatus.STALE_DEBT: MultipleStatus.STALE_INPUT,
    CapitalStructureStatus.INCOMPLETE_DEBT_COMPONENTS: MultipleStatus.MISSING_INPUT,
    CapitalStructureStatus.AMBIGUOUS_DEBT_TAGS: MultipleStatus.UNKNOWN,
    CapitalStructureStatus.AMBIGUOUS_CASH_TAGS: MultipleStatus.UNKNOWN,
    CapitalStructureStatus.COMPONENT_DATE_MISMATCH: MultipleStatus.PERIOD_MISMATCH,
    CapitalStructureStatus.UNKNOWN: MultipleStatus.EV_UNAVAILABLE,
}

_TTM_REFUSALS: Mapping[TtmStatus, MultipleStatus] = {
    TtmStatus.MISSING_COMPONENT: MultipleStatus.MISSING_INPUT,
    TtmStatus.STALE_PERIOD: MultipleStatus.STALE_INPUT,
    TtmStatus.PERIOD_MISMATCH: MultipleStatus.PERIOD_MISMATCH,
    TtmStatus.NOT_APPLICABLE: MultipleStatus.NOT_APPLICABLE,
    TtmStatus.UNIT_MISMATCH: MultipleStatus.UNKNOWN,
    TtmStatus.TAG_MISMATCH: MultipleStatus.UNKNOWN,
    TtmStatus.SIGN_CONVENTION_MISMATCH: MultipleStatus.UNKNOWN,
    TtmStatus.AMBIGUOUS_COMPONENT: MultipleStatus.UNKNOWN,
}

_INSTANT_REFUSALS: Mapping[InstantStatus, MultipleStatus] = {
    InstantStatus.MISSING: MultipleStatus.MISSING_INPUT,
    InstantStatus.STALE: MultipleStatus.STALE_INPUT,
    InstantStatus.AMBIGUOUS_TAGS: MultipleStatus.UNKNOWN,
}


def _refusal(table: Mapping, status, fallback: MultipleStatus) -> MultipleStatus:
    """Total by construction: an enum member absent from a table refuses rather than passes."""
    return table.get(status, fallback)


def _flow_family(start: date | None, end: date | None) -> PeriodFamily:
    """The period family of a flow denominator, MEASURED from its own dates.

    Hardcoding `PeriodFamily.TTM` here because P0.1's constructions are twelve-month by design would
    make D5-D0's `denominator_applicable` a sign check and nothing more, and the frozen rule that
    refuses a partial-year denominator would never fire. Classifying the actual span instead means a
    six-month figure arriving in a TTM slot is refused by the contract rather than by a convention,
    and an unclassifiable period yields UNKNOWN, which is not a valid denominator family.
    """
    if start is None or end is None:
        return PeriodFamily.UNKNOWN
    return period_family(start, end)


# -------------------------------------------------------------------------------------------------
# Derived EBITDA
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DerivedEbitda:
    """TTM operating income + TTM D&A over one identical period, or a named refusal.

    The name is `EBITDA_DERIVED`, reserved by P0.1 and emitted here for the first time. It is not a
    reported line item and it is not any issuer's own "adjusted EBITDA", which is computed under a
    different definition - usually a more flattering one.
    """

    status: MultipleStatus
    reason: str
    value: float | None = None
    unit: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    operating_income: float | None = None
    depreciation_amortization: float | None = None
    provenance: str = EBITDA_DERIVED_LABEL

    @property
    def ok(self) -> bool:
        return self.status is MultipleStatus.OK and self.value is not None

    def to_dict(self) -> dict:
        return {"status": self.status.value, "reason": self.reason, "value": self.value,
                "unit": self.unit, "provenance": self.provenance,
                "period_start": None if self.period_start is None else self.period_start.isoformat(),
                "period_end": None if self.period_end is None else self.period_end.isoformat(),
                "operating_income": self.operating_income,
                "depreciation_amortization": self.depreciation_amortization}


def derive_ebitda(operating_income: TtmResult | None,
                  depreciation_amortization: TtmResult | None) -> DerivedEbitda:
    """§11. Both legs OK, same unit, one identical period, and the sum - in that order.

    `ebitda_feasible` is P0.1's verdict and is asked rather than re-derived; this function adds the
    addition and the provenance label, so the question "may these two be added" has exactly one
    answer in the repository.
    """
    oi, da = operating_income, depreciation_amortization
    if oi is None or da is None:
        absent = "operating_income" if oi is None else "depreciation_amortization"
        return DerivedEbitda(MultipleStatus.MISSING_INPUT, f"{absent} absent from the TTM bundle")
    for leg in (oi, da):
        if not leg.ok:
            return DerivedEbitda(_refusal(_TTM_REFUSALS, leg.status, MultipleStatus.UNKNOWN),
                                 f"{leg.field}={leg.status.value}: {leg.reason}")
    if oi.unit != da.unit:
        return DerivedEbitda(MultipleStatus.UNKNOWN,
                             f"operating income is {oi.unit} and D&A is {da.unit}")
    if not ebitda_feasible(oi, da):
        return DerivedEbitda(
            MultipleStatus.PERIOD_MISMATCH,
            f"operating income covers {oi.period_start}..{oi.period_end} and D&A covers "
            f"{da.period_start}..{da.period_end}; EBITDA is one period or it is nothing")
    assert oi.value is not None and da.value is not None
    return DerivedEbitda(
        MultipleStatus.OK,
        "TTM operating income + TTM D&A over one identical constructed period",
        value=oi.value + da.value, unit=oi.unit,
        period_start=oi.period_start, period_end=oi.period_end,
        operating_income=oi.value, depreciation_amortization=da.value)


# -------------------------------------------------------------------------------------------------
# One multiple
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MultipleResult:
    """One method for one issuer: a ratio, or a named reason there is none. Never both."""

    method: str
    status: MultipleStatus
    reason: str
    value: float | None = None
    numerator_kind: Numerator | None = None
    numerator_value: float | None = None
    numerator_unit: str | None = None
    denominator_field: str | None = None
    denominator_value: float | None = None
    denominator_unit: str | None = None
    denominator_period_start: date | None = None
    denominator_period_end: date | None = None
    denominator_provenance: str | None = None

    def __post_init__(self) -> None:
        # The gate that makes "silent wrong number" unrepresentable rather than merely unlikely.
        if self.status is MultipleStatus.OK:
            assert self.value is not None, f"{self.method} is OK with no value"
        else:
            assert self.value is None, f"{self.method} is {self.status.value} with a value"

    @property
    def ok(self) -> bool:
        return self.status is MultipleStatus.OK and self.value is not None

    @property
    def bucket(self) -> MultipleBucket:
        return _BUCKETS[self.status]

    def to_dict(self) -> dict:
        return {
            "contract_version": MULTIPLES_CONTRACT_VERSION,
            "method": self.method,
            "status": self.status.value,
            "bucket": self.bucket.value,
            "reason": self.reason,
            "multiple": self.value,
            "numerator": None if self.numerator_kind is None else self.numerator_kind.value,
            "numerator_value": self.numerator_value,
            "numerator_unit": self.numerator_unit,
            "denominator_field": self.denominator_field,
            "denominator_value": self.denominator_value,
            "denominator_unit": self.denominator_unit,
            "denominator_period_start": (None if self.denominator_period_start is None
                                         else self.denominator_period_start.isoformat()),
            "denominator_period_end": (None if self.denominator_period_end is None
                                       else self.denominator_period_end.isoformat()),
            "denominator_provenance": self.denominator_provenance,
        }


def _numerator(spec: MultipleSpec, market_cap: MarketCapResolution | None,
               ev: EnterpriseValueResolution | None,
               price: float | None) -> tuple[MultipleStatus, str] | tuple[None, float]:
    """Either a refusal with its reason, or the numerator value. Never a value on a refusal."""
    if spec.numerator is Numerator.ENTERPRISE_VALUE:
        if ev is None:
            return MultipleStatus.EV_UNAVAILABLE, "no enterprise value resolution supplied"
        if not ev.ok:
            return (_refusal(_EV_REFUSALS, ev.status, MultipleStatus.EV_UNAVAILABLE),
                    f"enterprise_value={ev.status.value}: {ev.reason}")
        assert ev.value is not None
        return None, ev.value
    if market_cap is None:
        return MultipleStatus.MARKET_CAP_UNAVAILABLE, "no market cap resolution supplied"
    if not market_cap.valuation_ready:
        return (_refusal(_MARKET_CAP_REFUSALS, market_cap.status,
                         MultipleStatus.MARKET_CAP_UNAVAILABLE),
                f"market_cap={market_cap.status.value}: {market_cap.reason}")
    if spec.numerator is Numerator.MARKET_CAP:
        assert market_cap.value is not None
        return None, market_cap.value
    # PRICE_PER_SHARE. The market-cap gate above has already cleared share class, PIT shares and the
    # presence of a price; what is divided by EPS is the price itself, not the cap. See
    # PE_IS_PRICE_OVER_EPS for why the shares leg of that gate is stricter than this ratio needs.
    if price is None:
        return MultipleStatus.MARKET_CAP_UNAVAILABLE, "no unadjusted close at the decision date"
    return None, price


def compute_multiple(
    spec: MultipleSpec,
    *,
    market_cap: MarketCapResolution | None,
    enterprise_value: EnterpriseValueResolution | None,
    ttm: Mapping[str, TtmResult],
    equity: InstantResolution | None,
    ebitda: DerivedEbitda | None,
    price: float | None,
) -> MultipleResult:
    """One method, one issuer. Pure, total, and deterministic given its inputs."""
    refusal, numerator = _numerator(spec, market_cap, enterprise_value, price)
    if refusal is not None:
        # `numerator` carries the refusal's reason string on this branch, never a value.
        assert isinstance(numerator, str)
        return MultipleResult(spec.method, refusal, numerator, numerator_kind=spec.numerator)
    assert isinstance(numerator, float)

    den_value: float | None
    den_unit: str | None
    den_start: date | None = None
    den_end: date | None = None
    provenance: str

    if spec.denominator_kind is DenominatorKind.INSTANT:
        if equity is None:
            return MultipleResult(spec.method, MultipleStatus.MISSING_INPUT,
                                  f"no {spec.denominator_field} resolution supplied",
                                  numerator_kind=spec.numerator, numerator_value=numerator,
                                  numerator_unit=spec.numerator_unit,
                                  denominator_field=spec.denominator_field)
        if not equity.ok:
            return MultipleResult(
                spec.method, _refusal(_INSTANT_REFUSALS, equity.status, MultipleStatus.UNKNOWN),
                f"{equity.field}={equity.status.value}: {equity.reason}",
                numerator_kind=spec.numerator, numerator_value=numerator,
                numerator_unit=spec.numerator_unit, denominator_field=spec.denominator_field,
                denominator_period_end=equity.period_end)
        # The unit is read off the resolved fact rather than assumed to be USD from the FieldSpec:
        # a hardcoded unit would make the compatibility check below unable to fail on this branch.
        den_value, den_unit = equity.value, equity.fact.unit
        den_end = equity.period_end
        # An instant has no span. D5-D0's denominator-period rule is about partial-year FLOWS; a book
        # value is a point and its family is INSTANT, which is not in
        # VALID_MULTIPLE_DENOMINATOR_FAMILIES. So the family check is skipped here by design and the
        # freshness bound that replaces it is `resolve_valuation_instant`'s, already applied above.
        family = PeriodFamily.INSTANT
        provenance = f"{equity.fact.tag} at {equity.period_end}" if equity.fact else "instant"
    elif spec.denominator_kind is DenominatorKind.DERIVED:
        if ebitda is None:
            return MultipleResult(spec.method, MultipleStatus.MISSING_INPUT,
                                  "no derived EBITDA supplied", numerator_kind=spec.numerator,
                                  numerator_value=numerator, numerator_unit=spec.numerator_unit,
                                  denominator_field=spec.denominator_field)
        if not ebitda.ok:
            return MultipleResult(spec.method, ebitda.status, ebitda.reason,
                                  numerator_kind=spec.numerator, numerator_value=numerator,
                                  numerator_unit=spec.numerator_unit,
                                  denominator_field=spec.denominator_field,
                                  denominator_provenance=ebitda.provenance)
        den_value, den_unit = ebitda.value, ebitda.unit
        den_start, den_end = ebitda.period_start, ebitda.period_end
        family = _flow_family(den_start, den_end)
        provenance = ebitda.provenance
    else:
        leg = ttm.get(spec.denominator_field)
        if leg is None:
            return MultipleResult(spec.method, MultipleStatus.MISSING_INPUT,
                                  f"{spec.denominator_field} absent from the TTM bundle",
                                  numerator_kind=spec.numerator, numerator_value=numerator,
                                  numerator_unit=spec.numerator_unit,
                                  denominator_field=spec.denominator_field)
        if not leg.ok:
            return MultipleResult(
                spec.method, _refusal(_TTM_REFUSALS, leg.status, MultipleStatus.UNKNOWN),
                f"{leg.field}={leg.status.value}: {leg.reason}",
                numerator_kind=spec.numerator, numerator_value=numerator,
                numerator_unit=spec.numerator_unit, denominator_field=spec.denominator_field,
                denominator_period_start=leg.period_start, denominator_period_end=leg.period_end)
        den_value, den_unit = leg.value, leg.unit
        den_start, den_end = leg.period_start, leg.period_end
        family = _flow_family(den_start, den_end)
        provenance = (f"{leg.method.value} over {leg.period_start}..{leg.period_end}"
                      if leg.method else "TTM")

    partial = dict(numerator_kind=spec.numerator, numerator_value=numerator,
                   numerator_unit=spec.numerator_unit, denominator_field=spec.denominator_field,
                   denominator_value=den_value, denominator_unit=den_unit,
                   denominator_period_start=den_start, denominator_period_end=den_end,
                   denominator_provenance=provenance)

    if den_unit != spec.denominator_unit:
        return MultipleResult(spec.method, MultipleStatus.UNKNOWN,
                              f"{spec.denominator_field} is {den_unit}, and {spec.method} requires "
                              f"{spec.denominator_unit}", **partial)

    # D5-D0's frozen rule, called rather than reimplemented. For a FLOW it checks both the sign and
    # that the period is a full year; for the INSTANT case the family check is the one that cannot
    # apply, so the sign is checked on its own and the freshness bound above stands in for it.
    applicable = (den_value is not None and den_value > 0 if family is PeriodFamily.INSTANT
                  else denominator_applicable(den_value, family))
    if not applicable:
        return MultipleResult(spec.method, MultipleStatus.NEGATIVE_DENOMINATOR,
                              f"{spec.denominator_field}={den_value} over family {family.value}: a "
                              f"multiple is not formed from a zero, negative or partial-period "
                              f"denominator", **partial)

    assert den_value is not None and den_value > 0
    return MultipleResult(spec.method, MultipleStatus.OK,
                          f"{spec.numerator.value} / {spec.denominator_field}",
                          value=numerator / den_value, **partial)


def compute_multiples(
    *,
    market_cap: MarketCapResolution | None,
    enterprise_value: EnterpriseValueResolution | None,
    ttm: Mapping[str, TtmResult],
    equity: InstantResolution | None,
    price: float | None,
) -> dict[str, MultipleResult]:
    """All seven methods for one issuer, in `METHOD_ORDER`."""
    ebitda = derive_ebitda(ttm.get("operating_income"), ttm.get("depreciation_amortization"))
    return {
        method: compute_multiple(spec, market_cap=market_cap,
                                 enterprise_value=enterprise_value, ttm=ttm, equity=equity,
                                 ebitda=ebitda, price=price)
        for method, spec in MULTIPLE_SPECS.items()
    }


# -------------------------------------------------------------------------------------------------
# Company readiness
# -------------------------------------------------------------------------------------------------

class ValuationDataReadiness(StrEnum):
    """§16. Valuation-DATA readiness, and nothing else.

    It is not an investment score, not a quality ranking and not a prediction. An issuer is READY
    when enough of its fundamentals resolved to cross-check one multiple against another, and a
    NOT_READY issuer is one this repository cannot value today - which says nothing about the
    business. D5-D0's `NOT_READY_IS_AN_OUTPUT` is the governing sentence.
    """

    READY = "READY"
    READY_WITH_LIMITATIONS = "READY_WITH_LIMITATIONS"
    NOT_READY = "NOT_READY"


#: §16's boundary is D5-D0's `MIN_METHODS_FOR_COMPLETE`, imported rather than restated: one method
#: is a number, two are a valuation, because two can be cross-checked and one cannot. The brief's
#: thresholds and the frozen contract's agree, and importing the constant is what keeps them agreeing.


def usable_methods(results: Mapping[str, MultipleResult]) -> tuple[str, ...]:
    """The methods that produced a number. `METHOD_ORDER`, filtered - deterministic."""
    return tuple(m for m in METHOD_ORDER if m in results and results[m].ok)


def data_readiness(usable_method_count: int) -> ValuationDataReadiness:
    if usable_method_count <= 0:
        return ValuationDataReadiness.NOT_READY
    if usable_method_count >= MIN_METHODS_FOR_COMPLETE:
        return ValuationDataReadiness.READY
    return ValuationDataReadiness.READY_WITH_LIMITATIONS


def d0_completeness(*, market_cap_valid: bool, usable_method_count: int) -> ValuationCompleteness:
    """D5-D0 §N's authoritative classification, evaluated honestly at this stage.

    `scenarios_have_provenance` is False for every issuer because D5-D1 produces no scenarios at all
    - they are D5-D2's and §4 forbids them here. So COMPLETE is unreachable today, and every issuer
    with a valid market cap and at least one method is PARTIAL. That is a fact about the stage, not
    about the data, which is exactly why this step also reports `ValuationDataReadiness`: the two
    measure different things and collapsing them would read a staging constraint as a data defect.
    """
    return valuation_completeness(market_cap_valid=market_cap_valid,
                                  applicable_methods=usable_method_count,
                                  scenarios_have_provenance=False)


READINESS_IS_NOT_ATTRACTIVENESS = (
    "`ValuationDataReadiness` answers 'can this repository form a trustworthy multiple for this "
    "issuer today'. It is not an investment score. A READY issuer may be a terrible business and a "
    "NOT_READY issuer may be an excellent one; the label is about this repository's data, and "
    "reading it as a ranking would make D5 the stock screen that "
    "`VALUATION_IS_NOT_A_RANKING_ENGINE` forbids."
)


# -------------------------------------------------------------------------------------------------
# Method suitability (§17)
# -------------------------------------------------------------------------------------------------

class MethodSuitability(StrEnum):
    """Whether a COMPUTED multiple is economically worth leaning on. Diagnostic, never a gate.

    Computability and suitability are different questions, and conflating them is how a P/B gets
    used as the primary anchor for a software company. These four labels are the brief's §17 minimum
    and deliberately no finer: a sector ontology is not built here.
    """

    DATA_APPLICABLE = "DATA_APPLICABLE"
    """The multiple computed, and the inputs the economic judgement needs did not resolve."""
    ECONOMICALLY_REASONABLE = "ECONOMICALLY_REASONABLE"
    SECONDARY_ONLY = "SECONDARY_ONLY"
    """Computable and informative, but not to be leaned on as the primary anchor."""
    NOT_SUITABLE = "NOT_SUITABLE"
    """Reserved and emitted by this module in zero cases. See `NOT_SUITABLE_IS_AI_OWNED`."""


#: A margin below which an earnings or cash-flow multiple is arithmetically explosive: at a 1% margin
#: a tenth of a point of margin moves the multiple by a tenth of itself, so the ratio reports noise.
#: Generic and not tuned to this sample - it is a statement about division, not about these twelve.
MIN_MARGIN_FOR_PRIMARY = 0.02

#: Below this share of enterprise value, net debt does not change the story and the EV method says
#: what its equity-side counterpart already said. Not a defect - a reason to prefer the simpler one.
MATERIAL_NET_DEBT_SHARE_OF_EV = 0.10

_EV_METHODS: frozenset[str] = frozenset({"EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF"})
_MARGIN_SENSITIVE: frozenset[str] = frozenset({"P/E", "EV/EBIT", "EV/EBITDA", "P/FCF", "EV/FCF"})


def method_suitability(
    method: str,
    *,
    net_debt: float | None,
    enterprise_value: float | None,
    ttm_revenue: float | None,
    ttm_operating_income: float | None,
) -> tuple[MethodSuitability, str]:
    """§17's diagnostic for one computed multiple. Pure, total, and pre-registered.

    Every rule is a statement about arithmetic or about D5-D0's own `METHOD_SPECS` text, and none was
    chosen after seeing an issuer's result. The rules are deliberately few: §17 forbids an elaborate
    sector ontology, and §17 also forbids removing or adding a method because of what this returns.
    """
    if method == "P/B":
        return (MethodSuitability.SECONDARY_ONLY,
                "D5-D0's P/B spec says its applicability is narrow and must be argued per issuer; "
                "whether book value is this issuer's earning asset is not a question the balance "
                "sheet answers about itself, so code does not promote it")

    if method in _EV_METHODS and net_debt is not None and enterprise_value not in (None, 0):
        assert enterprise_value is not None
        if abs(net_debt) / abs(enterprise_value) < MATERIAL_NET_DEBT_SHARE_OF_EV:
            return (MethodSuitability.SECONDARY_ONLY,
                    f"net debt is {abs(net_debt) / abs(enterprise_value):.1%} of enterprise value, "
                    f"under the {MATERIAL_NET_DEBT_SHARE_OF_EV:.0%} materiality line: EV is market "
                    f"cap in disguise here and the equity-side method carries the same information")

    if ttm_revenue is None or ttm_revenue <= 0 or ttm_operating_income is None:
        return (MethodSuitability.DATA_APPLICABLE,
                "the multiple computed; TTM revenue or operating income did not resolve, so no "
                "margin regime is measurable and no economic judgement is offered")

    margin = ttm_operating_income / ttm_revenue

    if method in ("EV/Sales",):
        if margin >= MIN_MARGIN_FOR_PRIMARY:
            return (MethodSuitability.SECONDARY_ONLY,
                    f"operating margin {margin:.1%} is a real one, and D5-D0 forbids a sales "
                    f"multiple as the only method for a mature profitable issuer: the earnings and "
                    f"cash-flow methods are the primary anchors where they compute")
        return (MethodSuitability.ECONOMICALLY_REASONABLE,
                f"operating margin {margin:.1%}: a pre-profit or thin-margin issuer carrying real "
                f"debt is exactly D5-D0's appropriate_when for EV/Sales")

    if method in _MARGIN_SENSITIVE and margin < MIN_MARGIN_FOR_PRIMARY:
        return (MethodSuitability.SECONDARY_ONLY,
                f"operating margin {margin:.1%} is under {MIN_MARGIN_FOR_PRIMARY:.0%}: a multiple "
                f"on a near-zero denominator moves by its own size on a rounding of the margin")

    return (MethodSuitability.ECONOMICALLY_REASONABLE,
            f"operating margin {margin:.1%} supports an earnings or cash-flow multiple on this "
            f"issuer")


NOT_SUITABLE_IS_AI_OWNED = (
    "`MethodSuitability.NOT_SUITABLE` exists in the vocabulary and this module never returns it. "
    "Asserting that a computable multiple is unsuitable requires knowing what the business earns "
    "its money with - whether book value is the operating asset, whether D&A is economically real - "
    "and that is D5-D0's AI_OWNED column, not something the balance sheet states about itself. Code "
    "declining to promote a method (SECONDARY_ONLY) and code asserting a method is wrong "
    "(NOT_SUITABLE) are different claims, and only the first is supportable from the data here. "
    "D5-D2 is where the argument gets made, and §17 forbids tuning the method list to what the "
    "diagnostic returns."
)

SUITABILITY_IS_NOT_A_GATE = (
    "No multiple is withheld, and no issuer's readiness changes, because of a suitability label. "
    "`usable_methods` counts every method that produced a number, SECONDARY_ONLY included, because "
    "readiness is a question about data and suitability is a question about economics. A step that "
    "let the diagnostic subtract from coverage would be choosing the method list by its results, "
    "which is what §17 forbids."
)
