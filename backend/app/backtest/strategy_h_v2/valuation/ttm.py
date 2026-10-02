"""H-V2-D5-P0.1: deterministic, PIT-safe TTM construction over the repaired period primitive.

This module produces no multiple, no fair value, no target price and no decision. It produces a
trailing-twelve-month flow and the provenance of every fact that went into it.

P0 repaired the primitive: a fact now carries a `DurationFamily` and `resolve_fact` narrows to one
family instead of colliding a discrete quarter with a year to date. P0's own §L then named TTM as
"the only thing between the repaired primitive and a P/FCF or P/E numerator", because no canonical
field is TTM and every multiple needs an annual denominator.

Three constructions, and the measured data decides which one carries the weight:

  REPORTED_FISCAL_YEAR                a fiscal year that is itself the trailing twelve months
  FOUR_DISCRETE_QUARTERS              Q(t) + Q(t-1) + Q(t-2) + Q(t-3)
  FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD prior FY + current YTD - prior comparable YTD

The first is not a construction at all, which is why it is first. When a filer's fiscal year ended
recently enough to still describe the present, the 10-K's annual figure already is a trailing twelve
months and adding anything to it would be arithmetic in search of a problem. Leaving it out was a real
defect in this step's first draft: COHR's fiscal year ends in June, its 10-K for 2025-07-01..2026-06-30
was filed 90 days before the decision time, and every one of its TTM fields came back refused as stale
because the search only looked for quarters and year-to-date figures. Four of the frozen D5-D1 twelve
have a non-December fiscal year, so this is a common shape and not a curiosity.

Measured over the ten issuers at the stored D2.1 cutoff, the quarter chain closes for exactly ONE of
them (COLL, whose 10-K reports a discrete fourth quarter). For the other nine the chain breaks after
two quarters, because a 10-K reports the fiscal year and not its own Q4 as a separate figure. And for
`operating_cash_flow`/`capex` it breaks after ONE quarter on all ten: cash-flow statements report Q1
discretely and then switch to year to date, which is P0's measured finding and §6 of this step's
brief. So the YTD-difference path is not a fallback here, it is the normal case.

Nothing is annualised, interpolated, or extrapolated. A missing component is a missing TTM:

    annualize(current 6M) x 2          not implemented, and refused by name in `NEVER_CONSTRUCTED`
    quarter x 4                        same
    interpolate the absent quarter     same

The arithmetic of the YTD path is exact rather than approximate, and the two equalities below are
the whole proof. Given a prior fiscal year FY = [fy.start, fy.end], a current year to date
YTDc = [ytd_c.start, ytd_c.end] and a prior year to date YTDp = [ytd_p.start, ytd_p.end]:

    require ytd_c.start == fy.end + 1 day      (the current YTD accumulates from the fiscal year
                                                that begins the day the prior one ended)
    require ytd_p.start == fy.start            (the prior YTD accumulates from the prior FY's start)

    then  FY - YTDp = [ytd_p.end + 1, fy.end]
          + YTDc    = [fy.end + 1, ytd_c.end]
          --------------------------------------
          = exactly  [ytd_p.end + 1, ytd_c.end]

So the constructed period is known exactly, not estimated from day counts, and the day-count windows
are used only to confirm afterwards that the result really does span a year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Iterable, Mapping, Sequence

from app.backtest.strategy_h0.facts import (
    ANNUAL_SPAN_DAYS,
    COMPARABLE_DURATION_TOLERANCE_DAYS,
    YEAR_APART_DAYS,
    CanonicalFact,
    DurationFamily,
    FactStatus,
    FieldSpec,
    resolve_fact,
    resolve_period_aligned,
)
from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS
from app.backtest.strategy_h_v2.valuation.fundamental_fields import VALUATION_FIELD_SPECS

TTM_CONTRACT_VERSION = "h_v2_d5_p0_1_ttm_construction_v1"

QUARTERS_PER_YEAR = 4

#: How old the period a TTM covers may be, relative to the decision date.
#:
#: This bound exists because the first measurement without it was wrong in a way that looked right.
#: Searching period ends newest-first and taking the first one that constructs will happily walk back
#: years when the recent filings do not support a construction: DORM's TTM D&A came out OK over
#: 2013-09-29..2014-09-27 and VRRM's, IDCC's and FRPT's TTM diluted EPS over 2020-10-01..2021-09-30,
#: at a 2026-09-28 decision time. Each of those is internally consistent and each is a correct
#: trailing twelve months - of the wrong twelve months. A multiple formed from one against a 2026
#: price is exactly the silent wrongness P0 was written to end, so a TTM whose period is older than
#: this refuses instead of reporting a number.
#:
#: The number is `h0_5.MAX_SHARES_STALENESS_DAYS`, reused rather than reinvented. It is the only
#: validated staleness bound in this repository, it answers the identical question - how far may a
#: fact's period end lag the decision date before it stops describing the present - and it is applied
#: the identical way, `(decision_date - end).days > bound`. D5-D0 recorded that nothing bounded the
#: balance-sheet fields; this bounds the constructed flows, and §24 leaves `cash`/`total_debt` to
#: D5-P1 where they belong.
MAX_TTM_PERIOD_AGE_DAYS = MAX_SHARES_STALENESS_DAYS

NEVER_CONSTRUCTED: tuple[str, ...] = (
    "a six-month figure is never doubled to stand in for a year",
    "a quarter is never multiplied by four",
    "an absent quarter is never interpolated from its neighbours",
    "an absent component is never replaced by the same field from another period family",
    "a TTM is never reported as a number when any component is missing: it is reported UNKNOWN",
    "a missing or unknown TTM is never substituted with zero",
    "diluted EPS is never recomputed as TTM net income divided by a share count",
)
"""Each prohibition states itself, so quoting one cannot turn it into its opposite.

That phrasing is the house rule D4-E7R left behind: a prohibition whose negation lives only in the
name of the constant holding it trips this project's own decision-leakage detector."""


class TtmMethod(StrEnum):
    REPORTED_FISCAL_YEAR = "REPORTED_FISCAL_YEAR"
    """One FY fact, used as reported. The trailing twelve months the filer itself published."""
    FOUR_DISCRETE_QUARTERS = "FOUR_DISCRETE_QUARTERS"
    """Four discrete QUARTER facts, each adjacent to the next, together spanning a year."""
    FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD = "FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD"
    """A prior FY fact plus the current year to date less the prior comparable year to date."""


class TtmStatus(StrEnum):
    OK = "OK"
    MISSING_COMPONENT = "MISSING_COMPONENT"
    """At least one component does not exist, or is not known at the decision time."""
    PERIOD_MISMATCH = "PERIOD_MISMATCH"
    """Components exist but their periods do not compose into a year."""
    UNIT_MISMATCH = "UNIT_MISMATCH"
    """Components are reported in different units, so the arithmetic is not defined."""
    TAG_MISMATCH = "TAG_MISMATCH"
    """Components come from different XBRL tags.

    Not in the brief's status list, and added because the data demands it. `FIELD_SPECS` maps several
    tags to one field, and a filer can move between them: FRPT reports D&A as both
    `DepreciationDepletionAndAmortization` (47.9m for the six months to 2026-06-30) and
    `DepreciationAndAmortization` (50.0m for the same six months). Adding a fiscal year stated under
    one tag to a year to date stated under another, and subtracting a third, produces a number no
    filing contains. Refusing is the only honest answer available without a completeness claim."""
    SIGN_CONVENTION_MISMATCH = "SIGN_CONVENTION_MISMATCH"
    """A field declared `cash_outflow_positive` resolved to a negative total, or the reverse.

    Also added. `capex`'s `FieldSpec.sign` is `cash_outflow_positive` and every measured value is a
    positive outflow, which is why free cash flow is `OCF - CapEx` here exactly as in
    `change_detection.fcf_trend`. A filer using the negative convention would make that subtraction
    *add* capital expenditure, silently and with the right-looking sign. So the declared convention
    is checked rather than assumed, and a contradiction refuses instead of double-negating."""
    AMBIGUOUS_COMPONENT = "AMBIGUOUS_COMPONENT"
    """A component resolved AMBIGUOUS: conflicting facts share the winning provenance."""
    STALE_PERIOD = "STALE_PERIOD"
    """A construction exists, and the twelve months it covers ended too long before the decision
    date to describe the present. See `MAX_TTM_PERIOD_AGE_DAYS`, which is where the measured examples
    live. Reported separately from MISSING_COMPONENT because the distinction is actionable: the facts
    are there and the filer stopped reporting that field in a usable shape, which is a different
    problem from never having reported it."""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """The construction is not defined for this field. See `YTD_DIFFERENCE_IS_UNSAFE_FOR`."""


#: Fields for which the YTD-difference path is refused, with the reason measured rather than asserted.
#:
#: Diluted EPS is a ratio whose denominator is a weighted average share count over the period being
#: reported, so it is not additive across periods, and the audit quantifies that on stored data:
#:
#:   COLL fiscal 2025:  reported diluted EPS 1.73, sum of its four discrete quarters 1.71
#:   TG   fiscal 2024:  reported diluted EPS -1.88, sum of its four discrete quarters -1.87
#:   COLL TTM to 2026-06-30:  four discrete quarters 1.24,  FY + YTD - YTD 1.27
#:
#: The first two are the non-additivity itself: a cent or two, arising from each quarter carrying its
#: own weighted share count and its own rounding. The third is the consequence - the two paths
#: disagree by 3 cents, 2.4%, on the one issuer where both are available.
#:
#: Summing four reported quarters is kept, because every term is a reported diluted EPS for a period
#: inside the trailing year and the residual is bounded by what the filer rounded. The difference of
#: two year-to-date EPS figures is refused, because subtraction of two differently-weighted
#: denominators has no bound derivable from the filing: a mid-year share issuance moves the current
#: YTD's weighting and the prior year's not at all, and nothing in companyfacts says by how much.
#: `TTM net income / shares` is refused outright by `NEVER_CONSTRUCTED` - §12's absolute.
YTD_DIFFERENCE_IS_UNSAFE_FOR: frozenset[str] = frozenset({"eps_diluted"})

#: The families a current year-to-date component may have, newest-shape first. `YTD_Q1` is the
#: request-only identity P0 established: the first quarter of a fiscal year and the first-quarter
#: year to date are one fact, so a Q1 10-Q supports this path exactly as a Q2 or Q3 10-Q does.
_YTD_FAMILIES: tuple[DurationFamily, ...] = (
    DurationFamily.YTD_Q3, DurationFamily.YTD_Q2, DurationFamily.YTD_Q1,
)

#: The families `construct_ttm_aligned` will align several fields on, in preference order. FY leads
#: for the same reason it leads inside `construct_ttm`: a filer whose fiscal year just closed reports
#: both legs of free cash flow annually, and that needs no arithmetic to become a trailing year.
_ALIGNMENT_FAMILIES: tuple[DurationFamily, ...] = (DurationFamily.FY,) + _YTD_FAMILIES

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class TtmComponent:
    """One fact inside a TTM, with the sign it enters the sum with."""

    role: str
    sign: int
    fact: CanonicalFact

    @property
    def fact_id(self) -> str:
        """A fact's identity in companyfacts: there is no row id, so this is the tuple that is one.

        Accession plus tag plus unit plus period is exactly what `resolve_fact` narrows on, so two
        facts sharing this string are the duplicate rows the resolver already collapses."""
        return (f"{self.fact.accession}:{self.fact.tag}:{self.fact.unit}:"
                f"{self.fact.start.isoformat() if self.fact.start else 'instant'}:"
                f"{self.fact.end.isoformat()}")

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "sign": self.sign,
            "fact_id": self.fact_id,
            "tag": self.fact.tag,
            "unit": self.fact.unit,
            "value": self.fact.value,
            "start": self.fact.start.isoformat() if self.fact.start else None,
            "end": self.fact.end.isoformat(),
            "duration_days": self.fact.duration_days,
            "duration_family": self.fact.duration_family.value,
            "form": self.fact.form,
            "accession": self.fact.accession,
            "acceptance_time": self.fact.accepted_at.isoformat(),
        }


@dataclass(frozen=True)
class TtmResult:
    """A constructed TTM, or a named refusal. `value` is None unless `status` is OK."""

    field: str
    status: TtmStatus
    decision_time: datetime
    reason: str
    value: float | None = None
    unit: str | None = None
    method: TtmMethod | None = None
    period_start: date | None = None
    period_end: date | None = None
    duration_days: int | None = None
    components: tuple[TtmComponent, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is TtmStatus.OK and self.value is not None

    def to_dict(self) -> dict:
        """The §16 provenance record: every component's fact id, period and acceptance time."""
        return {
            "contract_version": TTM_CONTRACT_VERSION,
            "field": self.field,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "construction_method": self.method.value if self.method else None,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "duration_days": self.duration_days,
            "decision_time": self.decision_time.isoformat(),
            "reason": self.reason,
            "components": [c.to_dict() for c in self.components],
        }


def _registry(specs: Mapping[str, FieldSpec] | None) -> Mapping[str, FieldSpec]:
    return VALUATION_FIELD_SPECS if specs is None else specs


def _unresolved(field: str, decision_time: datetime, status: TtmStatus, reason: str) -> TtmResult:
    return TtmResult(field=field, status=status, decision_time=decision_time, reason=reason)


def _component_consistency(components: Sequence[TtmComponent]) -> tuple[TtmStatus, str] | None:
    """Unit and tag agreement across components. Returns None when they agree."""
    units = {c.fact.unit for c in components}
    if len(units) > 1:
        return TtmStatus.UNIT_MISMATCH, f"components report different units {sorted(units)}"
    tags = {c.fact.tag for c in components}
    if len(tags) > 1:
        return TtmStatus.TAG_MISMATCH, f"components come from different tags {sorted(tags)}"
    return None


def _sign_convention_violated(field: str, value: float,
                              specs: Mapping[str, FieldSpec]) -> str | None:
    spec = specs.get(field)
    if spec is not None and spec.sign == "cash_outflow_positive" and value < 0:
        return (f"{field} is declared cash_outflow_positive and the constructed total is {value}; "
                "subtracting a negative outflow would add it")
    return None


# -------------------------------------------------------------------------------------------------
# D0. A reported fiscal year, used as reported
# -------------------------------------------------------------------------------------------------


def _reported_fiscal_year(rows: Sequence[CanonicalFact], field: str, decision_time: datetime,
                          period_end: date,
                          specs: Mapping[str, FieldSpec]) -> TtmResult:
    """The FY fact ending `period_end`, with no arithmetic performed on it.

    P0's classifier already separated a fiscal year from the discrete fourth quarter a 10-K reports
    under the same `end`, so asking for the FY family here cannot pick up the quarter. The span is
    re-checked anyway, because this result is about to be described as a trailing twelve months and
    that claim should rest on the period rather than on the label.
    """
    resolved = resolve_fact(rows, field, decision_time, report_end=period_end,
                            duration_family=DurationFamily.FY, specs=specs)
    if resolved.status is FactStatus.AMBIGUOUS:
        return _unresolved(field, decision_time, TtmStatus.AMBIGUOUS_COMPONENT,
                           f"fiscal year ending {period_end}: {resolved.reason}")
    if resolved.status is not FactStatus.OK or resolved.fact is None:
        return _unresolved(field, decision_time, TtmStatus.MISSING_COMPONENT,
                           f"no fiscal year ends {period_end}")
    fact = resolved.fact
    assert fact.start is not None
    span = fact.duration_days
    assert span is not None
    if not ANNUAL_SPAN_DAYS[0] <= span <= ANNUAL_SPAN_DAYS[1]:
        return _unresolved(field, decision_time, TtmStatus.PERIOD_MISMATCH,
                           f"the reported fiscal year spans {span} days, outside {ANNUAL_SPAN_DAYS}")
    violation = _sign_convention_violated(field, fact.value, specs)
    if violation is not None:
        return _unresolved(field, decision_time, TtmStatus.SIGN_CONVENTION_MISMATCH, violation)
    return TtmResult(
        field=field, status=TtmStatus.OK, decision_time=decision_time,
        reason="the fiscal year as reported; no arithmetic",
        value=fact.value, unit=fact.unit, method=TtmMethod.REPORTED_FISCAL_YEAR,
        period_start=fact.start, period_end=fact.end, duration_days=span,
        components=(TtmComponent("fiscal_year", +1, fact),),
    )


# -------------------------------------------------------------------------------------------------
# D. Four discrete quarters
# -------------------------------------------------------------------------------------------------


def _four_discrete_quarters(rows: Sequence[CanonicalFact], field: str, decision_time: datetime,
                            period_end: date,
                            specs: Mapping[str, FieldSpec]) -> TtmResult:
    """Walk back from `period_end`, taking each quarter that ends the day before the last one began.

    Adjacency is exact by construction rather than by tolerance: each step pins `report_end` to the
    previous component's `start` minus one day, so the four periods cannot overlap and cannot leave a
    gap. If the filer does not report a discrete quarter ending on that exact date - which is what a
    10-K reporting only the fiscal year looks like - the chain stops and the result is MISSING.
    """
    chain: list[CanonicalFact] = []
    target: date | None = period_end
    while len(chain) < QUARTERS_PER_YEAR:
        resolved = resolve_fact(rows, field, decision_time, report_end=target,
                                duration_family=DurationFamily.QUARTER, specs=specs)
        if resolved.status is FactStatus.AMBIGUOUS:
            return _unresolved(field, decision_time, TtmStatus.AMBIGUOUS_COMPONENT,
                               f"quarter ending {target} is ambiguous: {resolved.reason}")
        if resolved.status is not FactStatus.OK or resolved.fact is None:
            return _unresolved(
                field, decision_time, TtmStatus.MISSING_COMPONENT,
                f"only {len(chain)} of {QUARTERS_PER_YEAR} adjacent discrete quarters are reported "
                f"back from {period_end}; none ends {target}")
        chain.append(resolved.fact)
        assert resolved.fact.start is not None  # a QUARTER fact has a start by classification
        target = resolved.fact.start - _ONE_DAY

    newest, oldest = chain[0], chain[-1]
    assert oldest.start is not None
    components = tuple(TtmComponent(f"quarter_minus_{i}", +1, fact)
                       for i, fact in enumerate(chain))
    clash = _component_consistency(components)
    if clash is not None:
        return _unresolved(field, decision_time, *clash)

    span = (newest.end - oldest.start).days
    if not ANNUAL_SPAN_DAYS[0] <= span <= ANNUAL_SPAN_DAYS[1]:
        return _unresolved(field, decision_time, TtmStatus.PERIOD_MISMATCH,
                           f"four adjacent quarters span {span} days, outside {ANNUAL_SPAN_DAYS}")

    value = sum(fact.value for fact in chain)
    violation = _sign_convention_violated(field, value, specs)
    if violation is not None:
        return _unresolved(field, decision_time, TtmStatus.SIGN_CONVENTION_MISMATCH, violation)
    return TtmResult(
        field=field, status=TtmStatus.OK, decision_time=decision_time,
        reason="four adjacent reported discrete quarters",
        value=value, unit=newest.unit, method=TtmMethod.FOUR_DISCRETE_QUARTERS,
        period_start=oldest.start, period_end=newest.end, duration_days=span,
        components=components,
    )


# -------------------------------------------------------------------------------------------------
# E. FY + current YTD - prior YTD
# -------------------------------------------------------------------------------------------------


def _ytd_difference(rows: Sequence[CanonicalFact], field: str, decision_time: datetime,
                    period_end: date, family: DurationFamily,
                    specs: Mapping[str, FieldSpec]) -> TtmResult:
    """prior FY + current YTD - prior comparable YTD, with both anchors required exactly.

    The prior FY is not searched for: it is the fiscal year whose `end` is the day before the current
    year to date begins, so it is pinned to that date. The prior comparable YTD is likewise anchored -
    it must be the same family, the same tag and unit, and must accumulate from the prior FY's own
    start - rather than chosen as "the nearest thing about a year back".
    """
    if field in YTD_DIFFERENCE_IS_UNSAFE_FOR:
        return _unresolved(
            field, decision_time, TtmStatus.NOT_APPLICABLE,
            f"{field} is a weighted-average-per-share figure; a difference of two year-to-date "
            "values has no bound derivable from the filing (YTD_DIFFERENCE_IS_UNSAFE_FOR)")

    current = resolve_fact(rows, field, decision_time, report_end=period_end,
                           duration_family=family, specs=specs)
    if current.status is FactStatus.AMBIGUOUS:
        return _unresolved(field, decision_time, TtmStatus.AMBIGUOUS_COMPONENT,
                           f"current {family.value} ending {period_end}: {current.reason}")
    if current.status is not FactStatus.OK or current.fact is None:
        return _unresolved(field, decision_time, TtmStatus.MISSING_COMPONENT,
                           f"no {family.value} fact ends {period_end}")
    assert current.fact.start is not None

    fy_end = current.fact.start - _ONE_DAY
    prior_fy = resolve_fact(rows, field, decision_time, report_end=fy_end,
                            duration_family=DurationFamily.FY, specs=specs)
    if prior_fy.status is FactStatus.AMBIGUOUS:
        return _unresolved(field, decision_time, TtmStatus.AMBIGUOUS_COMPONENT,
                           f"prior fiscal year ending {fy_end}: {prior_fy.reason}")
    if prior_fy.status is not FactStatus.OK or prior_fy.fact is None:
        return _unresolved(
            field, decision_time, TtmStatus.MISSING_COMPONENT,
            f"no fiscal year ends {fy_end}, the day before the current {family.value} begins")
    assert prior_fy.fact.start is not None
    fy_start = prior_fy.fact.start

    # The prior comparable YTD accumulates from the prior fiscal year's own start. That anchor, not a
    # date search, is what makes it comparable.
    # YTD_Q1 is P0's request-only identity, so the prior candidate is matched on the family actually
    # stored - QUARTER - plus the Q1 fiscal period, exactly as `resolve_fact` matches it.
    prior_ends = sorted({f.end for f in rows
                         if f.field == field and f.start == fy_start and f.end < period_end
                         and f.duration_family is current.fact.duration_family
                         and (family is not DurationFamily.YTD_Q1 or f.fiscal_period == "Q1")},
                        reverse=True)
    if not prior_ends:
        return _unresolved(
            field, decision_time, TtmStatus.MISSING_COMPONENT,
            f"no prior {family.value} accumulating from {fy_start}, the prior fiscal year's start")
    if len(prior_ends) > 1:
        return _unresolved(
            field, decision_time, TtmStatus.AMBIGUOUS_COMPONENT,
            f"{len(prior_ends)} prior {family.value} periods start {fy_start}: {prior_ends}")
    prior_ytd = resolve_fact(rows, field, decision_time, report_end=prior_ends[0],
                             duration_family=family, specs=specs)
    if prior_ytd.status is FactStatus.AMBIGUOUS:
        return _unresolved(field, decision_time, TtmStatus.AMBIGUOUS_COMPONENT,
                           f"prior {family.value} ending {prior_ends[0]}: {prior_ytd.reason}")
    if prior_ytd.status is not FactStatus.OK or prior_ytd.fact is None:
        return _unresolved(field, decision_time, TtmStatus.MISSING_COMPONENT,
                           f"prior {family.value} ending {prior_ends[0]} is not resolvable: "
                           f"{prior_ytd.reason}")

    components = (
        TtmComponent("prior_fy", +1, prior_fy.fact),
        TtmComponent("current_ytd", +1, current.fact),
        TtmComponent("prior_ytd", -1, prior_ytd.fact),
    )
    clash = _component_consistency(components)
    if clash is not None:
        return _unresolved(field, decision_time, *clash)

    # Comparability, on the frozen H-PV2 tolerances rather than new ones.
    apart = (current.fact.end - prior_ytd.fact.end).days
    if not YEAR_APART_DAYS[0] <= apart <= YEAR_APART_DAYS[1]:
        return _unresolved(
            field, decision_time, TtmStatus.PERIOD_MISMATCH,
            f"the two year-to-date periods end {apart} days apart, outside {YEAR_APART_DAYS}")
    assert prior_ytd.fact.duration_days is not None and current.fact.duration_days is not None
    drift = abs(current.fact.duration_days - prior_ytd.fact.duration_days)
    if drift > COMPARABLE_DURATION_TOLERANCE_DAYS:
        return _unresolved(
            field, decision_time, TtmStatus.PERIOD_MISMATCH,
            f"the two year-to-date spans differ by {drift} days, over "
            f"{COMPARABLE_DURATION_TOLERANCE_DAYS}")

    period_start = prior_ytd.fact.end + _ONE_DAY
    span = (current.fact.end - period_start).days
    if not ANNUAL_SPAN_DAYS[0] <= span <= ANNUAL_SPAN_DAYS[1]:
        return _unresolved(field, decision_time, TtmStatus.PERIOD_MISMATCH,
                           f"the constructed period spans {span} days, outside {ANNUAL_SPAN_DAYS}")

    value = prior_fy.fact.value + current.fact.value - prior_ytd.fact.value
    violation = _sign_convention_violated(field, value, specs)
    if violation is not None:
        return _unresolved(field, decision_time, TtmStatus.SIGN_CONVENTION_MISMATCH, violation)
    return TtmResult(
        field=field, status=TtmStatus.OK, decision_time=decision_time,
        reason=f"prior fiscal year to {prior_fy.fact.end} plus {family.value} difference",
        value=value, unit=current.fact.unit,
        method=TtmMethod.FY_PLUS_CURRENT_YTD_MINUS_PRIOR_YTD,
        period_start=period_start, period_end=current.fact.end, duration_days=span,
        components=components,
    )


# -------------------------------------------------------------------------------------------------
# C. The entry point
# -------------------------------------------------------------------------------------------------


def _candidate_attempts(rows: Sequence[CanonicalFact], field: str, period_end: date | None,
                        oldest_allowed: date) -> tuple[list[tuple[date, DurationFamily | None]],
                                                       date | None]:
    """Every (period end, construction) worth trying, newest end first.

    `DurationFamily.FY` means the reported fiscal year and `None` means the discrete-quarter chain;
    the remaining values are the year-to-date families. At one period end the order is fiscal year,
    then quarter chain, then year-to-date difference, by how much arithmetic each performs on the
    filer's own figures: none, a sum of four non-overlapping reported periods whose adjacency this
    module verifies, and a difference whose correctness additionally needs the three figures to be
    stated on one basis - which no filing asserts and this module cannot check.

    Ends older than `oldest_allowed` are not candidates, and the newest of them is returned alongside
    so a caller that finds no live candidate can say the field went stale rather than say it is
    absent. Returns `(attempts, newest_excluded_end)`.
    """
    attempts: list[tuple[date, DurationFamily | None]] = []
    quarter_ends = {f.end for f in rows if f.field == field
                    and f.duration_family is DurationFamily.QUARTER}
    fy_ends = {f.end for f in rows if f.field == field
               and f.duration_family is DurationFamily.FY}
    ytd_ends: dict[date, list[DurationFamily]] = {}
    for fact in rows:
        if fact.field != field:
            continue
        for family in _YTD_FAMILIES:
            if family is DurationFamily.YTD_Q1:
                matches = (fact.duration_family is DurationFamily.QUARTER
                           and fact.fiscal_period == "Q1")
            else:
                matches = fact.duration_family is family
            if matches:
                ytd_ends.setdefault(fact.end, [])
                if family not in ytd_ends[fact.end]:
                    ytd_ends[fact.end].append(family)
    excluded: date | None = None
    for end in sorted(quarter_ends | fy_ends | set(ytd_ends), reverse=True):
        if period_end is not None and end != period_end:
            continue
        if end < oldest_allowed:
            excluded = end if excluded is None else max(excluded, end)
            continue
        if end in fy_ends:
            attempts.append((end, DurationFamily.FY))
        if end in quarter_ends:
            attempts.append((end, None))
        for family in _YTD_FAMILIES:
            if family in ytd_ends.get(end, ()):
                attempts.append((end, family))
    return attempts, excluded


def construct_ttm(
    facts: Iterable[CanonicalFact],
    field: str,
    decision_time: datetime,
    *,
    period_end: date | None = None,
    specs: Mapping[str, FieldSpec] | None = None,
    max_period_age_days: int = MAX_TTM_PERIOD_AGE_DAYS,
) -> TtmResult:
    """A trailing-twelve-month total for one field, from facts known at `decision_time`.

    PIT safety is inherited rather than re-implemented: every component goes through `resolve_fact`,
    which admits a fact only when `accepted_at <= decision_time` and which already applies the
    amendment-versioning rule, so an amendment filed after the decision time cannot reach a
    component and one filed before it supersedes the original within its own period family.

    `period_end` pins the construction to one period end; left `None`, the newest period end at which
    any construction succeeds wins. When a field resolves at no period end the result carries the
    refusal from the newest attempt, because that is the one the caller asked about.

    `max_period_age_days` bounds how far back that search may reach - see `MAX_TTM_PERIOD_AGE_DAYS`
    for why it is not optional by default. It applies to a pinned `period_end` too: a caller asking
    for a twelve months that ended years ago is asking a historical question this layer does not
    answer, and raising the bound explicitly is how to ask it.
    """
    registry = _registry(specs)
    if field not in registry:
        raise KeyError(field)
    rows = [f for f in facts if f.field == field]
    oldest_allowed = decision_time.date() - timedelta(days=max_period_age_days)
    attempts, newest_stale = _candidate_attempts(rows, field, period_end, oldest_allowed)
    if not attempts and newest_stale is not None:
        age = (decision_time.date() - newest_stale).days
        return _unresolved(
            field, decision_time, TtmStatus.STALE_PERIOD,
            f"the newest period this field reports ends {newest_stale}, {age} days before the "
            f"decision date, over the {max_period_age_days}-day bound")
    if not attempts:
        return _unresolved(field, decision_time, TtmStatus.MISSING_COMPONENT,
                           "no quarter or year-to-date fact of this field is reported"
                           + (f" ending {period_end}" if period_end else ""))
    first_refusal: TtmResult | None = None
    for end, family in attempts:
        if family is DurationFamily.FY:
            result = _reported_fiscal_year(rows, field, decision_time, end, registry)
        elif family is None:
            result = _four_discrete_quarters(rows, field, decision_time, end, registry)
        else:
            result = _ytd_difference(rows, field, decision_time, end, family, registry)
        if result.status is TtmStatus.OK:
            return result
        if first_refusal is None:
            first_refusal = result
    assert first_refusal is not None
    return first_refusal


def construct_ttm_aligned(
    facts: Iterable[CanonicalFact],
    fields: Sequence[str],
    decision_time: datetime,
    *,
    specs: Mapping[str, FieldSpec] | None = None,
) -> tuple[dict[str, TtmResult], str]:
    """TTM for several fields over ONE shared period, or a named refusal for each.

    This is `resolve_period_aligned`'s first production consumer, which is what P0 left it for. The
    reason it is needed is measured: cash-flow statements report no discrete second quarter, so
    resolving `operating_cash_flow` and `capex` independently can land them on different period ends,
    and a free cash flow built from cash flow of one period and capital expenditure of another is not
    a ratio of anything. So the shared period end is chosen first, for all fields at once, and every
    field is then constructed pinned to it.

    The alignment is on the period end the constructed TTM inherits - a reported fiscal year's own
    end, or the current year-to-date's. The prior-FY and prior-YTD anchors follow from the latter
    exactly, per this module's docstring, so aligning the one aligns the three.
    """
    registry = _registry(specs)
    wanted = list(fields)
    if not wanted:
        raise ValueError("at least one field is required")
    rows = [f for f in facts if f.field in set(wanted)]
    best: tuple[date, DurationFamily] | None = None
    for family in _ALIGNMENT_FAMILIES:
        aligned, _ = resolve_period_aligned(rows, wanted, decision_time, family, specs=registry)
        if aligned is None:
            continue
        end = max(fact.end for fact in aligned.values())
        if best is None or end > best[0]:
            best = (end, family)
    if best is None:
        return ({name: _unresolved(name, decision_time, TtmStatus.MISSING_COMPONENT,
                                   "no period end where every requested field reports the same "
                                   "annual or year-to-date family")
                 for name in wanted},
                "no aligned period end")
    end, family = best
    return ({name: construct_ttm(rows, name, decision_time, period_end=end, specs=registry)
             for name in wanted},
            f"aligned on the {family.value} ending {end}")


# -------------------------------------------------------------------------------------------------
# I. Free cash flow
# -------------------------------------------------------------------------------------------------

FREE_CASH_FLOW_FIELDS: tuple[str, str] = ("operating_cash_flow", "capex")

CAPEX_SIGN_CONTRACT = (
    "`capex`'s FieldSpec declares sign `cash_outflow_positive`, and every capex value in the stored "
    "ten-issuer companyfacts is a positive outflow. So free cash flow is OCF - CapEx, with one "
    "subtraction and no sign flip - byte-for-byte the convention "
    "`change_detection.fcf_trend` already uses, so the valuation layer and the change layer cannot "
    "disagree about what free cash flow is. A negative constructed capex contradicts the declared "
    "convention and refuses as SIGN_CONVENTION_MISMATCH rather than being negated twice."
)


def construct_ttm_free_cash_flow(
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    *,
    specs: Mapping[str, FieldSpec] | None = None,
) -> TtmResult:
    """TTM free cash flow = TTM operating cash flow - TTM capital expenditure, code-owned.

    Both legs are built over one aligned period by `construct_ttm_aligned`, and the period, method
    and unit are re-checked afterwards rather than assumed, so a free cash flow can only exist when
    its two legs describe the same trailing year.
    """
    registry = _registry(specs)
    parts, reason = construct_ttm_aligned(facts, FREE_CASH_FLOW_FIELDS, decision_time,
                                          specs=registry)
    ocf, capex = parts[FREE_CASH_FLOW_FIELDS[0]], parts[FREE_CASH_FLOW_FIELDS[1]]
    field = "free_cash_flow"
    for leg in (ocf, capex):
        if leg.status is not TtmStatus.OK:
            return _unresolved(field, decision_time, leg.status,
                               f"{leg.field} is {leg.status.value}: {leg.reason}")
    if (ocf.period_start, ocf.period_end, ocf.method) != (capex.period_start, capex.period_end,
                                                          capex.method):
        return _unresolved(
            field, decision_time, TtmStatus.PERIOD_MISMATCH,
            f"operating cash flow covers {ocf.period_start}..{ocf.period_end} by {ocf.method} and "
            f"capex covers {capex.period_start}..{capex.period_end} by {capex.method}")
    if ocf.unit != capex.unit:
        return _unresolved(field, decision_time, TtmStatus.UNIT_MISMATCH,
                           f"operating cash flow is {ocf.unit} and capex is {capex.unit}")
    assert ocf.value is not None and capex.value is not None
    components = tuple(TtmComponent(f"operating_cash_flow:{c.role}", c.sign, c.fact)
                       for c in ocf.components)
    components += tuple(TtmComponent(f"capex:{c.role}", -c.sign, c.fact)
                        for c in capex.components)
    return TtmResult(
        field=field, status=TtmStatus.OK, decision_time=decision_time,
        reason=f"TTM operating cash flow less TTM capex, {reason}",
        value=ocf.value - capex.value, unit=ocf.unit, method=ocf.method,
        period_start=ocf.period_start, period_end=ocf.period_end,
        duration_days=ocf.duration_days, components=components,
    )


# -------------------------------------------------------------------------------------------------
# §14. EBITDA feasibility, reported and not computed
# -------------------------------------------------------------------------------------------------

EBITDA_CANDIDATE_DEFINITION = (
    "TTM operating income + TTM depreciation and amortisation, over one identical constructed "
    "period. Structurally available wherever both legs are OK and their periods match."
)

EBITDA_IS_NOT_CONSTRUCTED_HERE = (
    "No EBITDA value is produced by this step and no EBITDA multiple is formed. `ebitda_feasible` "
    "answers only whether the two legs exist over one period. When a later step does construct it, "
    "the field is named EBITDA_DERIVED so it cannot be read as a figure any filer reported: "
    "operating income plus D&A is a definition this repository chose, not a disclosed line item, and "
    "a company's own 'adjusted EBITDA' is a different number computed under a different definition."
)

EBITDA_DERIVED_LABEL = "EBITDA_DERIVED"
"""Reserved by this step and emitted by none of it."""


def ebitda_feasible(operating_income: TtmResult, depreciation_amortization: TtmResult) -> bool:
    """Whether a TTM EBITDA candidate could be formed. It returns a verdict, never a value."""
    return (operating_income.ok and depreciation_amortization.ok
            and operating_income.unit == depreciation_amortization.unit
            and (operating_income.period_start, operating_income.period_end)
            == (depreciation_amortization.period_start, depreciation_amortization.period_end))


#: The flow fields this step constructs, in the order the coverage table reports them.
TTM_FIELDS: tuple[str, ...] = (
    "revenue", "operating_income", "net_income", "eps_diluted",
    "operating_cash_flow", "capex", "depreciation_amortization",
)


def construct_ttm_bundle(
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    *,
    specs: Mapping[str, FieldSpec] | None = None,
) -> dict[str, TtmResult]:
    """Every TTM field this step defines, plus free cash flow. No multiple, no valuation, no price.

    Each field is constructed at its own newest workable period end, because requiring one period end
    across the income statement and the cash-flow statement would refuse both rather than report
    either: a 10-Q's income statement carries a discrete quarter that its cash-flow statement does
    not. Free cash flow is the one figure whose two legs must share a period, and
    `construct_ttm_free_cash_flow` aligns them rather than hoping.
    """
    registry = _registry(specs)
    rows = list(facts)
    bundle = {name: construct_ttm(rows, name, decision_time, specs=registry)
              for name in TTM_FIELDS}
    bundle["free_cash_flow"] = construct_ttm_free_cash_flow(rows, decision_time, specs=registry)
    return bundle


# -------------------------------------------------------------------------------------------------
# §24. What this step does not do, recorded for D5-P1
# -------------------------------------------------------------------------------------------------

INSTANT_FIELD_STALENESS_IS_STILL_UNBOUNDED = (
    "D5-P1 needs a staleness contract for the instant balance-sheet fields, which this step does not "
    "touch because it constructs flows. The measured case is unchanged from D5-D0: COLL resolves "
    "`total_debt` OK from a period end 2,463 days before the cutoff, because no instant field has a "
    "staleness bound at all - the asymmetry being that H0.5 bounded `shares_outstanding` at 135 days "
    "and nothing bounded `cash`, `total_debt`, `assets` or `equity`. A TTM flow cannot hide this "
    "defect, but a net debt or an enterprise value built on those fields would inherit it."
)

DEBT_AND_EV_REMAIN_BLOCKED = (
    "`total_debt` composition and enterprise value stay where D5-D0 and P0 left them, deferred to "
    "D5-P1: `resolve_fact` selects one debt tag by priority and never sums, so 7 of 10 issuers "
    "resolve to LongTermDebtCurrent - the current portion - while the same filer also reports "
    "LongTermDebt, and DORM resolves to 0. No debt or enterprise-value work happens in this step."
)
