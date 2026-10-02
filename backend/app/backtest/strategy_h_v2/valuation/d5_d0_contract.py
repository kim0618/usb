"""H-V2-D5-D0: the Valuation Fundamentals contract, frozen before any valuation runs.

This module produces no fair value, no target price and no decision. It is the frozen answer to the
question D0 §AA.5 left open - "which of the candidate valuation methods are actually implementable"
- and it answers it from what the repository measurably has rather than from what a valuation layer
would like to have.

The short answer, and the reason this file is mostly refusals: on the ten issuers D4's live runs
actually touched, every income-statement field resolves AMBIGUOUS or MISSING, and the canonical
`total_debt` resolves to a single long-term component chosen by tag priority rather than to total
debt. So P/E, P/S, EV/EBITDA, EV/EBIT and EV/Sales are not computable today, and saying otherwise
would be the "forced calculation" §7 of the D5-D0 brief prohibits. What this file freezes is which
method becomes reachable when which named data repair lands, so the pilot measures a contract
instead of discovering one.

Nothing here is wired to a runner. `test_d5_d0_contract.py` asserts that: no `call_opus`, no
`target_price`, no realized-return input anywhere in the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import hashlib
import json
from pathlib import Path

from app.backtest.strategy_h0.facts import (
    ANNUAL_SPAN_DAYS,
    FIELD_SPECS,
    NINE_MONTH_SPAN_DAYS,
    QUARTER_SPAN_DAYS,
    SEMI_SPAN_DAYS,
)
from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS, SHARES_TAG

D5_D0_CONTRACT_VERSION = "h_v2_d5_d0_valuation_fundamentals_v1"

# ---------------------------------------------------------------------------------------------
# A. What D0 actually asked D5 for, and what it never defined
# ---------------------------------------------------------------------------------------------

D0_INHERITED_OBLIGATIONS: tuple[str, ...] = (
    "D0 §N1: 'valuation view supports upside under at least the Base case "
    "(§M/valuation, arithmetic deferred to D5)' - one of seven APPROVE preconditions.",
    "D0 §L WIDE_POSITIVE: 'valuation has not yet re-rated to reflect it' - recorded verbatim on "
    "every WIDE_POSITIVE D4 produces as UNEVALUATED, via "
    "`gap_contract.WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED`.",
    "D0 §N2 WATCH: 'company attractive but current price leaves insufficient margin of safety'.",
    "D0 §N3 REJECT: 'valuation already reflects the full positive case (fully priced)'.",
    "D0 §M: valuation against the company's own historical range, and against a stated peer set "
    "whose relevance must be argued rather than assumed.",
)
"""The real, quotable requirement. Each is a phrase D0 froze with its arithmetic deferred here."""

UNDEFINED_BEFORE_D5: tuple[str, ...] = ("TP1", "TP2", "Entry1", "Entry2", "fair_value", "exit")
"""Named nowhere in this repository except as EXCLUSIONS.

`H_V2_ARCHITECTURE_REDESIGN_V1.md` §B states it directly: H0's prohibition list names Entry1/Entry2
and TP1/TP2 as things it does not implement, "these terms are never defined anywhere else in the
repository", and "there is no prior document that specifies what Entry1/Entry2 or TP1/TP2 actually
mean". H0's own §L repeats "Entry1/Entry2 and TP1/TP2 were not implemented".

So §L of this contract DEFINES TP1/TP2. It does not restore an intent, and a document claiming to
recover their original meaning would be inventing a provenance. `valuation_view` is the one that
does have a prior home, and it is an empty object: D0's Decision JSON carries `"valuation_view": {}`
with "structure is deferred to §M/D5 and is not implemented here"."""

D4_BANNED_FIELDS_ARE_A_LAYER_BOUNDARY = (
    "`analysis_schema.BANNED_D4_FIELD_NAMES` forbids valuation, valuation_view, fair_value, upside, "
    "tp1, tp2, entry and exit in a D4 OUTPUT. That ban is a layer boundary, not a prohibition on D5 "
    "existing: D5 writes its own artifact under its own schema and D4's banned list is unchanged. A "
    "D5 field appearing inside a D4 response is still a defect."
)


# ---------------------------------------------------------------------------------------------
# B. Period families - the dominant correctness problem (brief §7)
# ---------------------------------------------------------------------------------------------
#
# D1.1 already measured this: `FUNDAMENTAL_FACT_AMBIGUOUS` fired on 2,143 of 5,192 securities (41%)
# and sampling 200 showed EVERY instance was one 10-Q legitimately reporting the same line item for
# a discrete quarter AND for the year to date. `facts.resolve_fact` narrows by tag, acceptance and
# accession but NOT by duration family, so it reports those as AMBIGUOUS - truthfully, under its own
# promise, and uselessly for valuation.
#
# The second half of the problem is the opposite failure. A field reported only once, as year to
# date, resolves OK and carries no warning that it is a partial-year figure. Measured: AEYE's
# `operating_cash_flow` at the D2.1 cutoff resolves OK with start 2026-01-01, end 2026-06-30 - a
# 180-day number. A price divided by that is a price-to-half-year-FCF.
#
# REPAIRED BY D5-P0, and the measurements above are left as the D5-D0-time record rather than
# rewritten. `facts.DurationFamily` / `facts.classify_duration` now classify a fact's period from
# its own start/end/form/fiscal-period, `resolve_fact(..., duration_family=...)` narrows to one
# family and returns MISSING rather than another family, and the D2.1 bundle carries `start`,
# `duration_days` and `duration_family`. The `BLOCKED_DURATION_AMBIGUITY` availabilities below
# therefore describe the UNNARROWED call, which is still what D1-D4 make; see
# `H_V2_D5_P0_FUNDAMENTAL_PERIOD_PRIMITIVE_REPAIR_V1.md` for the re-measured 10-issuer coverage.


class PeriodFamily(StrEnum):
    INSTANT = "INSTANT"
    QUARTER = "QUARTER"
    YTD = "YTD"
    TTM = "TTM"
    ANNUAL = "ANNUAL"
    UNKNOWN = "UNKNOWN"
    """A duration this contract will not classify. Never silently treated as any of the others."""


#: Day-count windows. Owned by `facts.py` since D5-P0, which adopted the frozen H-PV2/H-PV3
#: tolerances, so the repository holds ONE set of duration windows rather than three that disagree.
#: Re-exported here under their original names because this contract quotes them by name.
QUARTER_DAYS = QUARTER_SPAN_DAYS
SEMI_DAYS = SEMI_SPAN_DAYS
NINE_MONTH_DAYS = NINE_MONTH_SPAN_DAYS
ANNUAL_DAYS = ANNUAL_SPAN_DAYS

def period_family(start: date | None, end: date | None, *, fiscal_year_start: date | None = None
                  ) -> PeriodFamily:
    """Classify one fact's period. Pure, and refuses rather than guesses.

    A duration whose start coincides with the fiscal year start is year-to-date even when its span
    happens to be a quarter: the first quarter of a fiscal year is both, and calling it QUARTER
    would make Q1 silently comparable with a Q3 year-to-date figure. When the fiscal year start is
    not supplied - the D2.1 bundle does not carry one - a quarter-length span is QUARTER and the
    ambiguity is recorded in `YTD_Q1_INDISTINGUISHABLE` rather than resolved by assumption.

    Since D5-P0 the day-count windows are `facts.py`'s, so this contract and the resolver can no
    longer drift apart on what "a quarter" spans. The mapping stays here because it is coarser on
    purpose and because this signature carries neither `form` nor `fp`: a bare six-month span is
    year-to-date *as far as a multiple is concerned* - unusable as a denominator either way -
    whereas `facts.classify_duration` will not call it YTD_Q2 without the fiscal-period metadata
    that says which quarter it accumulates to. A caller holding a `CanonicalFact` should read
    `fact.duration_family` instead and get that stricter answer.
    """
    if end is None:
        return PeriodFamily.UNKNOWN
    if start is None:
        return PeriodFamily.INSTANT
    span = (end - start).days
    if fiscal_year_start is not None and start == fiscal_year_start:
        return PeriodFamily.YTD
    for (low, high), family in (
        (QUARTER_DAYS, PeriodFamily.QUARTER),
        (SEMI_DAYS, PeriodFamily.YTD),
        (NINE_MONTH_DAYS, PeriodFamily.YTD),
        (ANNUAL_DAYS, PeriodFamily.ANNUAL),
    ):
        if low <= span <= high:
            return family
    return PeriodFamily.UNKNOWN


YTD_Q1_INDISTINGUISHABLE = (
    "Without a fiscal-year start, a first-quarter figure and a first-quarter year-to-date figure "
    "are the same span and cannot be told apart. D5-D1 must supply the fiscal-year start from the "
    "filing's own fiscal period metadata, or report the field NOT_COMPARABLE."
)

#: Which families may be divided into each other. A multiple's numerator is a market value at an
#: instant and its denominator is a flow, so the pairing that matters is flow-to-flow within a
#: growth rate and instant-to-flow within a multiple. Both sides of a growth rate must be the SAME
#: family: a quarter over a year-to-date is not a growth rate, it is an arithmetic accident.
COMPARABLE_FOR_GROWTH: frozenset[tuple[PeriodFamily, PeriodFamily]] = frozenset({
    (PeriodFamily.QUARTER, PeriodFamily.QUARTER),
    (PeriodFamily.YTD, PeriodFamily.YTD),
    (PeriodFamily.TTM, PeriodFamily.TTM),
    (PeriodFamily.ANNUAL, PeriodFamily.ANNUAL),
    (PeriodFamily.INSTANT, PeriodFamily.INSTANT),
})

#: The only flow families a multiple may use as a denominator. A partial-year denominator produces a
#: number that looks like a multiple and is not one, which is exactly the failure mode that makes
#: this a frozen list rather than a warning.
VALID_MULTIPLE_DENOMINATOR_FAMILIES: frozenset[PeriodFamily] = frozenset({
    PeriodFamily.TTM, PeriodFamily.ANNUAL,
})


def growth_periods_comparable(current: PeriodFamily, prior: PeriodFamily) -> bool:
    return (current, prior) in COMPARABLE_FOR_GROWTH


def denominator_period_valid(family: PeriodFamily) -> bool:
    return family in VALID_MULTIPLE_DENOMINATOR_FAMILIES


TTM_CONSTRUCTION_REQUIRED = (
    "No canonical field is TTM today. A trailing-twelve-month flow must be constructed by code from "
    "four non-overlapping QUARTER facts, or from an ANNUAL fact plus the difference of two "
    "year-to-date facts covering the same fiscal-year start. Interpolating a missing quarter, "
    "annualizing a partial year, or multiplying a quarter by four are all forbidden: each invents a "
    "number the filings do not contain."
)


# ---------------------------------------------------------------------------------------------
# C. PIT field contract (brief §6)
# ---------------------------------------------------------------------------------------------


class FieldAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    """A canonical field exists and resolved OK on the measured spot-check."""
    BLOCKED_DURATION_AMBIGUITY = "BLOCKED_DURATION_AMBIGUITY"
    """The field exists but `resolve_fact` cannot narrow quarter from year-to-date."""
    BLOCKED_COMPOSITION = "BLOCKED_COMPOSITION"
    """The field resolves OK and the value is not the quantity its name claims."""
    NOT_CANONICAL = "NOT_CANONICAL"
    """The filings carry it; `FIELD_SPECS` has no entry, so the pipeline cannot see it."""
    DERIVED = "DERIVED"
    """Code-owned arithmetic over other fields. Never supplied by a model."""


@dataclass(frozen=True)
class ValuationFieldSpec:
    name: str
    source: str
    period_type: str
    unit: str
    availability: FieldAvailability
    missing_behavior: str
    note: str = ""

    def to_dict(self) -> dict:
        return {"field": self.name, "source": self.source, "period_type": self.period_type,
                "unit": self.unit, "availability": self.availability.value,
                "missing_behavior": self.missing_behavior, "note": self.note}


_SEC = "SEC companyfacts, accession joined to submissions acceptanceDateTime (H0 facts.py)"
_PANEL = "local unadjusted daily grouped-daily panel, 501 sessions 2024-09-17..2026-09-16"
_UNKNOWN_BEHAVIOR = "UNKNOWN; no substitution, no interpolation, no current-value fallback"

VALUATION_FIELDS: tuple[ValuationFieldSpec, ...] = (
    ValuationFieldSpec("revenue", _SEC, "duration", "USD",
                       FieldAvailability.BLOCKED_DURATION_AMBIGUITY, _UNKNOWN_BEHAVIOR,
                       "0 of 10 spot-check issuers resolved OK"),
    ValuationFieldSpec("gross_profit", _SEC, "duration", "USD",
                       FieldAvailability.BLOCKED_DURATION_AMBIGUITY, _UNKNOWN_BEHAVIOR,
                       "1 of 10 OK, 5 AMBIGUOUS, 4 MISSING; H0 already called it the weakest "
                       "common margin input at 29 of 40"),
    ValuationFieldSpec("operating_income", _SEC, "duration", "USD",
                       FieldAvailability.BLOCKED_DURATION_AMBIGUITY, _UNKNOWN_BEHAVIOR,
                       "0 of 10 OK; also the EBIT proxy, so EV/EBIT inherits this"),
    ValuationFieldSpec("net_income", _SEC, "duration", "USD",
                       FieldAvailability.BLOCKED_DURATION_AMBIGUITY, _UNKNOWN_BEHAVIOR,
                       "0 of 10 OK, 10 of 10 AMBIGUOUS"),
    ValuationFieldSpec("eps_diluted", _SEC, "duration", "USD/shares",
                       FieldAvailability.BLOCKED_DURATION_AMBIGUITY, _UNKNOWN_BEHAVIOR,
                       "0 of 10 OK, 10 of 10 AMBIGUOUS"),
    ValuationFieldSpec("operating_cash_flow", _SEC, "duration", "USD",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR,
                       "10 of 10 OK, and the resolved span is YTD (AEYE: 180 days). OK means "
                       "resolvable, not annual"),
    ValuationFieldSpec("capex", _SEC, "duration", "USD",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR,
                       "8 of 10 OK, 2 MISSING; sign convention cash_outflow_positive"),
    ValuationFieldSpec("free_cash_flow", "derived: operating_cash_flow - capex", "duration", "USD",
                       FieldAvailability.DERIVED, _UNKNOWN_BEHAVIOR,
                       "both operands must share one period family and one period end"),
    ValuationFieldSpec("cash", _SEC, "instant", "USD",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR,
                       "10 of 10 OK. Tag 2 of 2 includes RESTRICTED cash; when it is the resolved "
                       "tag the value overstates cash available to net against debt"),
    ValuationFieldSpec("total_debt", _SEC, "instant", "USD",
                       FieldAvailability.BLOCKED_COMPOSITION, _UNKNOWN_BEHAVIOR,
                       "9 of 10 resolve OK and the value is not total debt: 8 of 10 resolve to "
                       "LongTermDebtCurrent, the current portion alone, while the same filer also "
                       "reports LongTermDebt. No short-term borrowing tag is canonical at all"),
    ValuationFieldSpec("net_debt", "derived: total_debt - cash", "instant", "USD",
                       FieldAvailability.DERIVED, _UNKNOWN_BEHAVIOR,
                       "inherits total_debt's composition defect; UNKNOWN until it is repaired"),
    ValuationFieldSpec("assets", _SEC, "instant", "USD",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR, "10 of 10 OK"),
    ValuationFieldSpec("equity", _SEC, "instant", "USD",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR, "10 of 10 OK"),
    ValuationFieldSpec("shares_outstanding", f"{_SEC}; dei tag {SHARES_TAG}", "instant", "shares",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR,
                       f"10 of 10 OK. H0.5 staleness bound {MAX_SHARES_STALENESS_DAYS} days and "
                       "split consistency apply; multi-class is fail-closed but UNDETECTED"),
    ValuationFieldSpec("depreciation_amortization", "SEC: DepreciationDepletionAndAmortization or "
                       "DepreciationAndAmortization", "duration", "USD",
                       FieldAvailability.NOT_CANONICAL, _UNKNOWN_BEHAVIOR,
                       "every one of the 10 spot-check issuers reports one of these tags, and "
                       "FIELD_SPECS has no entry. EBITDA's blocker is operating_income, not D&A"),
    ValuationFieldSpec("interest_expense", "SEC: InterestExpense / InterestExpenseNonoperating",
                       "duration", "USD", FieldAvailability.NOT_CANONICAL, _UNKNOWN_BEHAVIOR,
                       "9 of 10 issuers report it; H0 recorded it as absent from the canonical "
                       "pilot and it still is"),
    ValuationFieldSpec("unadjusted_close", _PANEL, "instant", "USD",
                       FieldAvailability.AVAILABLE, _UNKNOWN_BEHAVIOR,
                       "all 501 sessions carry adjusted=false, which is what H0.5 requires"),
    ValuationFieldSpec("market_cap", "derived: H0.5 primitive", "instant", "USD",
                       FieldAvailability.DERIVED, _UNKNOWN_BEHAVIOR,
                       "unadjusted close x valid PIT raw shares; see §F"),
    ValuationFieldSpec("enterprise_value", "derived: market_cap + total_debt - cash", "instant",
                       "USD", FieldAvailability.DERIVED, _UNKNOWN_BEHAVIOR,
                       "UNKNOWN in v1: blocked on total_debt composition"),
)

#: Every field this contract claims to read from SEC companyfacts must exist in H0's `FIELD_SPECS`,
#: or the name is a guess rather than a field. Derived fields, panel fields and the two
#: NOT_CANONICAL tags are exempt by construction - the last of those are named precisely BECAUSE
#: they are absent from FIELD_SPECS.
_CANONICAL_NAMED = {f.name for f in VALUATION_FIELDS
                    if f.source.startswith(_SEC)
                    and f.availability in (FieldAvailability.AVAILABLE,
                                           FieldAvailability.BLOCKED_DURATION_AMBIGUITY,
                                           FieldAvailability.BLOCKED_COMPOSITION)}
assert _CANONICAL_NAMED <= set(FIELD_SPECS), (
    f"named fields absent from H0 FIELD_SPECS: {sorted(_CANONICAL_NAMED - set(FIELD_SPECS))}"
)
assert _CANONICAL_NAMED == set(FIELD_SPECS), (
    "D5 must account for every canonical field or say why it is irrelevant; unaccounted: "
    f"{sorted(set(FIELD_SPECS) - _CANONICAL_NAMED)}"
)


# ---------------------------------------------------------------------------------------------
# D. Market cap and enterprise value (brief §8, §9)
# ---------------------------------------------------------------------------------------------

MARKET_CAP_PRIMITIVE = (
    "MarketCap(T) = unadjusted regular-session close(T) x raw PIT shares(T), H0.5 frozen. "
    "Implemented as `h0_5.resolve_pit_shares` + `h0_5.historical_market_cap`, reused unchanged. "
    "UNKNOWN on missing/stale/ambiguous/multi-class shares, on a split between the shares instant "
    "and T without a reported post-split fact, and on a missing unadjusted close. Current "
    "shares/market-cap fallback is forbidden, including for a current-date decision."
)

MULTI_CLASS_IS_UNDETECTED = (
    "`resolve_pit_shares(multiple_share_classes=True)` returns UNKNOWN, and nothing in the "
    "production pipeline ever sets that flag - its only True caller is a test. So multi-class is "
    "fail-closed in capability and unwired in practice, exactly as D4's M_GATES were. D5-D1 must "
    "either supply the detector or report multi-class as an unmeasured risk; it may not treat the "
    "capability as coverage."
)

DEBT_COMPOSITION_CONTRACT = "NOT_IMPLEMENTED"
"""Why EV is UNKNOWN in v1, stated as the mechanism rather than as a worry.

`facts.resolve_fact` selects ONE tag by priority - `best_tag = min(spec.tags.index(...))` - and never
sums. `total_debt`'s priority order begins with two current-portion-only tags:

    LongTermDebtAndFinanceLeaseObligationsCurrent, LongTermDebtCurrent,
    LongTermDebtNoncurrent, LongTermDebt

Demonstrated on a fixture: a filer reporting current 50M, noncurrent 900M and total 950M resolves to
`LongTermDebtCurrent` = 50M with status OK. Measured on the ten issuers D4 touched: 8 of 10 report
all three tags and therefore resolve to the current portion while the full amount sits in
`LongTermDebt`; DORM resolves to 0; SPSC reports none of the four and resolves MISSING while
reporting `LineOfCreditFacilityAmountOutstanding`.

A debt-composition contract must define the summation set (short-term borrowings, current and
noncurrent long-term debt, finance leases, drawn revolver), whether operating leases are included,
and what to do when a component is absent - and it must do so before any EV exists. Until then
`enterprise_value` is UNKNOWN and no method whose name starts with EV is applicable."""

DEBT_STALENESS_UNBOUNDED = (
    "`resolve_fact` applies no staleness bound to balance-sheet fields, unlike shares. Measured: "
    "COLL resolves total_debt from a 2019-12-31 period end, 2,463 days before the D2.1 cutoff, with "
    "status OK; FRPT from 2022-09-30, 1,459 days. 3 of 10 issuers have a cash period end that does "
    "not match their debt period end. A net-debt figure built from two different balance sheets is "
    "not a net-debt figure."
)


# ---------------------------------------------------------------------------------------------
# E. Valuation methods (brief §10, §11, §12)
# ---------------------------------------------------------------------------------------------


class MethodStatus(StrEnum):
    APPLICABLE = "APPLICABLE"
    BLOCKED_DATA = "BLOCKED_DATA"
    """A named field defect blocks it. The blocker is recorded, not the hope."""
    BLOCKED_NO_PROVIDER = "BLOCKED_NO_PROVIDER"
    """Needs a PIT consensus/estimate feed, which this repository has never had."""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """Structurally inapplicable to this candidate, e.g. a negative denominator."""


class MethodId(StrEnum):
    PE = "P/E"
    PS = "P/SALES"
    PB = "P/BOOK"
    P_FCF = "P/FCF"
    FCF_YIELD = "FCF_YIELD"
    EV_EBITDA = "EV/EBITDA"
    EV_EBIT = "EV/EBIT"
    EV_SALES = "EV/SALES"
    PEG_CONTEXT = "GROWTH_CONTEXTUALIZED_MULTIPLE"
    OWN_HISTORICAL_RANGE = "OWN_RECENT_2Y_MULTIPLE_RANGE"
    PEER_RANGE = "PEER_COMPARABLE_MULTIPLE_RANGE"
    FORWARD_ANY = "ANY_FORWARD_MULTIPLE"


@dataclass(frozen=True)
class MethodSpec:
    method: MethodId
    required_fields: tuple[str, ...]
    denominator: str
    appropriate_when: str
    forbidden_when: str
    v1_status: MethodStatus
    blocker: str = ""

    def to_dict(self) -> dict:
        return {"method": self.method.value, "required_fields": list(self.required_fields),
                "denominator": self.denominator, "appropriate_when": self.appropriate_when,
                "forbidden_when": self.forbidden_when, "v1_status": self.v1_status.value,
                "blocker": self.blocker}


_POSITIVE = "denominator <= 0, or its period family is not TTM/ANNUAL"
_DURATION_BLOCKER = "income-statement duration ambiguity (field availability above)"
_DEBT_BLOCKER = "total_debt composition; see DEBT_COMPOSITION_CONTRACT"

METHOD_SPECS: tuple[MethodSpec, ...] = (
    MethodSpec(MethodId.PE, ("market_cap", "net_income"), "net income, TTM or annual",
               "persistently profitable, moderate capital intensity, stable share count",
               _POSITIVE, MethodStatus.BLOCKED_DATA, _DURATION_BLOCKER),
    MethodSpec(MethodId.PS, ("market_cap", "revenue"), "revenue, TTM or annual",
               "pre-profit or margin-inflecting business where revenue is the stable anchor",
               _POSITIVE + "; also forbidden as the only method for a mature profitable issuer",
               MethodStatus.BLOCKED_DATA, _DURATION_BLOCKER),
    MethodSpec(MethodId.PB, ("market_cap", "equity"), "common equity, instant",
               "balance-sheet-driven economics where book value is the operating asset",
               "equity <= 0; or an asset-light issuer whose book value is not the earning asset",
               MethodStatus.APPLICABLE,
               "inputs resolve OK 10 of 10; applicability is narrow and must be argued per issuer"),
    MethodSpec(MethodId.P_FCF, ("market_cap", "operating_cash_flow", "capex"),
               "free cash flow, TTM or annual",
               "cash-generative business where accrual earnings understate or overstate cash",
               _POSITIVE, MethodStatus.BLOCKED_DATA,
               "inputs resolve OK but only as YTD; needs TTM construction "
               "(TTM_CONSTRUCTION_REQUIRED)"),
    MethodSpec(MethodId.FCF_YIELD, ("market_cap", "operating_cash_flow", "capex"),
               "free cash flow / market cap, TTM or annual",
               "same as P/FCF, expressed as a yield for comparison against a required return",
               "free cash flow <= 0, where a yield is not interpretable",
               MethodStatus.BLOCKED_DATA, "same TTM construction gap as P/FCF"),
    MethodSpec(MethodId.EV_EBITDA, ("enterprise_value", "operating_income",
                                    "depreciation_amortization"), "EBITDA, TTM or annual",
               "capital-intensive or leveraged issuer where capital structure differs across peers",
               _POSITIVE, MethodStatus.BLOCKED_DATA,
               f"{_DEBT_BLOCKER}; and D&A is NOT_CANONICAL, though 10 of 10 issuers report it"),
    MethodSpec(MethodId.EV_EBIT, ("enterprise_value", "operating_income"), "EBIT, TTM or annual",
               "as EV/EBITDA where D&A is economically real and should not be added back",
               _POSITIVE, MethodStatus.BLOCKED_DATA,
               f"{_DEBT_BLOCKER}; and {_DURATION_BLOCKER}"),
    MethodSpec(MethodId.EV_SALES, ("enterprise_value", "revenue"), "revenue, TTM or annual",
               "pre-profit issuer carrying real debt, where P/S would ignore the capital structure",
               _POSITIVE, MethodStatus.BLOCKED_DATA, f"{_DEBT_BLOCKER}; and {_DURATION_BLOCKER}"),
    MethodSpec(MethodId.PEG_CONTEXT, ("market_cap", "net_income", "revenue"),
               "a multiple read against a same-family growth rate, never a ratio of the two",
               "contextualizing an already-computed multiple against measured growth",
               "growth periods not in COMPARABLE_FOR_GROWTH; or published as a single PEG number, "
               "which hides which multiple and which growth window produced it",
               MethodStatus.BLOCKED_DATA, _DURATION_BLOCKER),
    MethodSpec(MethodId.OWN_HISTORICAL_RANGE, ("market_cap", "unadjusted_close"),
               "the issuer's own multiple over the available panel",
               "an issuer whose business has not changed character within the window",
               "naming the output a long-term range; see HISTORICAL_RANGE_LABEL",
               MethodStatus.BLOCKED_DATA,
               "the multiple itself is blocked; the price side is available for 501 sessions"),
    MethodSpec(MethodId.PEER_RANGE, ("market_cap", "net_income", "revenue"),
               "peer multiples computed from code-owned peer fundamentals",
               "at least three eligible peers under PEER_ELIGIBILITY, each with the same method "
               "computable on the same period family",
               "peers selected by sector code alone; or a peer multiple supplied by the model",
               MethodStatus.BLOCKED_DATA,
               "every peer inherits the same field defects as the subject"),
    MethodSpec(MethodId.FORWARD_ANY, ("pit_consensus",), "a forward estimate",
               "never, in v1",
               "always in v1: no PIT consensus provider has ever been proven in this repository",
               MethodStatus.BLOCKED_NO_PROVIDER,
               "D0 §AA.4 and D4's consensus_status=SOURCE_NOT_AVAILABLE both say so"),
)

NO_UNIVERSAL_MULTIPLE = (
    "There is no default multiple. A fixed 'PER 20x' applied to every issuer is forbidden, and so is "
    "a multiple the model supplies from its own prior. Every multiple used in a fair-value "
    "computation must be an OBSERVED value - the issuer's own measured range, or a measured peer "
    "range - carried with the provenance that produced it. The model's role is to argue which "
    "framework and which part of an observed range fits the business; it never names the number."
)

VALUATION_IS_NOT_A_RANKING_ENGINE = (
    "D5 answers 'how much of this is already in the price' for a candidate D3 and D4 have already "
    "characterised. It is not a stock screen, it does not rank, and a cheap multiple is not a "
    "finding on its own - which is the difference between this layer and H-PV1's value factor."
)


def method_status(spec: MethodSpec, available: frozenset[str]) -> MethodStatus:
    """A method is applicable only if every required field is available. Pure and total."""
    if spec.v1_status is MethodStatus.BLOCKED_NO_PROVIDER:
        return spec.v1_status
    missing = [f for f in spec.required_fields if f not in available]
    return MethodStatus.BLOCKED_DATA if missing else MethodStatus.APPLICABLE


def denominator_applicable(value: float | None, family: PeriodFamily) -> bool:
    """§11. A negative or zero denominator makes the multiple NOT_APPLICABLE, never a negative
    multiple: a P/E of -14 reads like a cheap stock and means a loss."""
    return value is not None and value > 0 and denominator_period_valid(family)


# ---------------------------------------------------------------------------------------------
# F. Peer and historical context (brief §13, §14)
# ---------------------------------------------------------------------------------------------

PEER_ELIGIBILITY: tuple[str, ...] = (
    "same or adjacent business: what the company sells, to whom, under what revenue model",
    "comparable margin regime, measured rather than assumed",
    "comparable growth regime, measured on the same period family",
    "comparable capital intensity (capex / revenue, on the same period family)",
    "the same valuation method computable on the peer, from code-owned fields",
)
PEER_SECTOR_CODE_INSUFFICIENT = (
    "A shared sector or SIC code is not peer eligibility and may not be the only argument. D0 §M "
    "already froze this: peer relevance must be argued, not assumed. The model may PROPOSE peers "
    "and must argue each against the list above; code computes every peer fundamental and every "
    "peer multiple, and a peer whose own fields are blocked is not a peer for that method."
)
PEER_MIN_ELIGIBLE = 3
"""Below three, the output is a comparison to a named company, not a peer range, and must be
labelled that way rather than presented as a range."""

HISTORICAL_PANEL_FIRST_SESSION = date(2024, 9, 17)
HISTORICAL_PANEL_LAST_SESSION = date(2026, 9, 16)
HISTORICAL_PANEL_SESSIONS = 501
HISTORICAL_RANGE_LABEL = "RECENT_2Y_RANGE"
"""§14. The local panel is 501 unadjusted sessions, 2024-09-17 to 2026-09-16. That is a two-year
window, and an own-multiple range computed from it is `RECENT_2Y_RANGE`.

`LONG_TERM_HISTORICAL_RANGE` is a forbidden label in v1, and the reason is not modesty: a two-year
window starting in September 2024 contains one macro regime and at most eight reported quarters, so
it cannot show how the issuer was valued in a different one. H0 measured the same bound and the
paid-plan paths that would extend it (five, ten, twenty years) were priced but never purchased."""

PRICE_PANEL_STALENESS = (
    "The panel's last session is 2026-09-16. A decision time after it has no close and therefore no "
    "market cap, which is UNKNOWN rather than the last available close carried forward. D5-D1 must "
    "set its decision time inside the panel or extend the panel first."
)


# ---------------------------------------------------------------------------------------------
# G. Guidance as a scenario input (brief §16)
# ---------------------------------------------------------------------------------------------

GUIDANCE_OPERAND_CONTRACT = (
    "Reuses D4-BR's operand completeness unchanged: `expectation.comparability.REQUIRED_OPERANDS`. "
    "Management guidance becomes a code-owned scenario input only when metric, period, low, high "
    "and unit are all present and the period is comparable to the denominator the scenario uses. A "
    "one-sided floor ('at least 48%') is not a range and may not be completed; a missing bound may "
    "not be interpolated from the other one. Incomplete guidance yields UNKNOWN for that scenario "
    "input, and UNKNOWN is a valid scenario state."
)
GUIDANCE_IS_NOT_CONSENSUS = (
    "Guidance is the company's own statement, not the market's expectation. Using it as a stand-in "
    "for consensus would manufacture the provider D4 established does not exist."
)


# ---------------------------------------------------------------------------------------------
# H. Scenarios, fair value range, TP1/TP2, upside (brief §17-§20)
# ---------------------------------------------------------------------------------------------


class Scenario(StrEnum):
    BEAR = "BEAR"
    BASE = "BASE"
    BULL = "BULL"


SCENARIO_INPUT_PROVENANCE: tuple[str, ...] = (
    "code-owned current fundamentals, named field by field with period end and period family",
    "an explicit assumption source: a complete guidance range, a measured own/peer multiple "
    "observation, or a measured historical growth rate",
    "the direction and magnitude of every deviation from the code-owned current value",
)
SCENARIO_ASSUMPTIONS_ARE_NOT_FREE = (
    "A scenario whose operating assumption has no entry in SCENARIO_INPUT_PROVENANCE is not a "
    "scenario, and the model may not supply one. Three numbers the model chose are three guesses "
    "wearing a framework."
)

FAIR_VALUE_IS_A_RANGE = (
    "D5 produces lower / base / upper per-share values, never a single point. The arithmetic is "
    "code-owned: value = (code-owned per-share metric for that scenario) x (observed multiple for "
    "that scenario), with both operands carried as provenance. A point estimate implies a precision "
    "that neither the multiple nor the period data supports."
)


@dataclass(frozen=True)
class TargetPriceDefinition:
    name: str
    scenario: Scenario
    definition: str
    arithmetic: str
    provenance_required: tuple[str, ...]


TP_DEFINITIONS: tuple[TargetPriceDefinition, ...] = (
    TargetPriceDefinition(
        "TP1", Scenario.BASE,
        "The price at which the BASE case is fully reflected: base-case per-share metric at the "
        "multiple the base case assumes. Reaching TP1 means the thesis was right and the market has "
        "finished agreeing with it. This is the level D0 §N1's 'supports upside under at least the "
        "Base case' is measured against, and D0 §N3's 'already reflects the full positive case' is "
        "the same level reached before entry rather than after.",
        "TP1 = base_scenario_per_share_metric x base_multiple",
        ("the per-share metric's field, period end and period family",
         "the base multiple's observed source: own RECENT_2Y_RANGE or peer range",
         "where in that observed range the base multiple sits, and the argument for it"),
    ),
    TargetPriceDefinition(
        "TP2", Scenario.BULL,
        "The upper-scenario level: the bull-case per-share metric at a multiple that is still an "
        "observed value, reflecting either a stronger operating outcome or a re-rating toward the "
        "upper end of the observed range. TP2 is not TP1 plus a margin.",
        "TP2 = bull_scenario_per_share_metric x upper_multiple",
        ("everything TP1 requires, for the bull scenario",
         "which of the two moved - the metric, the multiple, or both - stated separately",
         "the observed upper bound that the upper multiple does not exceed"),
    ),
    TargetPriceDefinition(
        "BEAR_ANCHOR", Scenario.BEAR,
        "The downside reference for DownsideToBear. Same construction, bear inputs.",
        "BEAR_ANCHOR = bear_scenario_per_share_metric x lower_multiple",
        ("everything TP1 requires, for the bear scenario",),
    ),
)

TP_IS_DEFINED_HERE_NOT_RESTORED = (
    "TP1 and TP2 have no prior definition in this repository - see UNDEFINED_BEFORE_D5. These "
    "definitions are new, chosen to match the only prior language that does exist (D0 §N1's Base "
    "case, §N2's margin of safety, §N3's fully-priced), and they are not a reconstruction of an "
    "earlier intent."
)

NO_RETURN_FITTING = (
    "No target price, multiple, scenario or range may be chosen by looking at what the stock went "
    "on to do. There is no realized-return, forward-return or price-outcome input anywhere in the "
    "D5 contract, and a test asserts the package contains none. No target price may be fitted to "
    "the return it would have predicted: that is not a valuation but a curve fit, and it is the one "
    "failure mode no later gate can detect, because the fitted number looks exactly like a derived "
    "one."
)

UPSIDE_MEASURES: tuple[str, ...] = (
    "UpsideToTP1 = TP1 / current_unadjusted_close - 1",
    "UpsideToTP2 = TP2 / current_unadjusted_close - 1",
    "DownsideToBear = BEAR_ANCHOR / current_unadjusted_close - 1",
)
UPSIDE_IS_NOT_A_DECISION = (
    "D5 stops at these three numbers. It does not convert them into buy, sell, approve, watch, "
    "reject, an entry level, an exit level or a position size. D6 combines D4's expectation gap "
    "with D5's valuation, and D6 does not exist."
)


# ---------------------------------------------------------------------------------------------
# I. D4 integration (brief §21, §22)
# ---------------------------------------------------------------------------------------------

D4_INTEGRATION_RULES: tuple[str, ...] = (
    "D5 reads D4's output and never rewrites it. D4's expectation_gap, confidence, "
    "priced_in_assessment and d6_approve_precondition are inputs to D6, not to D5's arithmetic.",
    "A cheap valuation does not promote a NEGATIVE expectation gap. The two layers can disagree, "
    "and a disagreement is a finding for D6, not something D5 resolves.",
    "An expensive valuation does not demote a POSITIVE gap either. D5 reports insufficient upside; "
    "it does not restate the gap.",
    "D0 §L's WIDE_POSITIVE valuation conjunct is D5's to evaluate. Until D5 runs it stays "
    "UNEVALUATED, and D4 records that verbatim on every WIDE_POSITIVE it produces.",
)

D4_LIMITATIONS_CARRIED_FORWARD: dict[str, str] = {
    "C1": "NOT_EVALUATED in Tier A V3, Tier B and D4-BR-C - three consecutive graded runs with a "
          "zero denominator, because none produced a POSITIVE-family gap. D5 does not resolve it "
          "and must not be read as having done so. It is not a D5 blocker: D5's arithmetic does not "
          "depend on the gap state.",
    "R3": "OBSERVED / DEFERRED. Compound set-valued numeric ownership, exposure 1 (Tier B's CRK), "
          "wired to no gate. Unchanged by D5. Not a D5 blocker for the same reason.",
    "E7R": "The decision-leakage detector is repaired and replayed, and D5 inherits it: D5's own "
           "text fields are subject to the same single authority, which is what keeps a valuation "
           "narrative from becoming a recommendation.",
}


# ---------------------------------------------------------------------------------------------
# J. Completeness state (brief §23)
# ---------------------------------------------------------------------------------------------


class ValuationCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    """Market cap valid, at least two applicable methods, and a scenario set with full provenance."""
    PARTIAL = "PARTIAL"
    """Market cap valid and exactly one applicable method. A range may be published, labelled."""
    NOT_READY = "NOT_READY"
    """No safely computable method. No fair value, no TP1, no TP2, and no narrative substitute."""


MIN_METHODS_FOR_COMPLETE = 2
"""One method is a number; two agreeing or disagreeing methods are a valuation. A single method
cannot be cross-checked, and cross-checking is most of what makes a multiple trustworthy."""


def valuation_completeness(*, market_cap_valid: bool, applicable_methods: int,
                           scenarios_have_provenance: bool) -> ValuationCompleteness:
    if not market_cap_valid or applicable_methods < 1:
        return ValuationCompleteness.NOT_READY
    if applicable_methods >= MIN_METHODS_FOR_COMPLETE and scenarios_have_provenance:
        return ValuationCompleteness.COMPLETE
    return ValuationCompleteness.PARTIAL


NOT_READY_IS_AN_OUTPUT = (
    "NOT_READY is a valid, complete D5 output and is not a failure of the candidate. The model is "
    "never asked to produce a target price for a NOT_READY candidate, and a narrative that implies "
    "one anyway is a defect. This is D4-BR's lesson applied before it has to be learned again: an "
    "honest abstention is an output, and only an empty output is a failure."
)


# ---------------------------------------------------------------------------------------------
# K. Responsibility split (brief §5)
# ---------------------------------------------------------------------------------------------

CODE_OWNED: tuple[str, ...] = (
    "market cap", "enterprise value", "every multiple", "every growth rate", "every margin",
    "net debt", "free cash flow", "TTM construction", "period-family classification",
    "any DCF arithmetic", "valuation range arithmetic", "target-price arithmetic",
    "upside and downside arithmetic", "peer fundamentals and peer multiples",
    "the observed own-multiple range",
)
AI_OWNED: tuple[str, ...] = (
    "which valuation framework fits this business and why",
    "whether a proposed peer is economically comparable, argued against PEER_ELIGIBILITY",
    "future-business maturity interpretation carried from D3",
    "the rationale for multiple expansion or contraction, and where in an OBSERVED range to sit",
    "valuation risk explanation and what would invalidate the framework choice",
    "which scenario assumptions are plausible given the evidence, never their numbers",
)
AI_MUST_NOT_COMPUTE = (
    "Every item in CODE_OWNED. A model-supplied value for any of them is rejected rather than "
    "checked, the same way D4 handles code-owned facts: `extra='forbid'` plus a code-filled field. "
    "The D4 precedent is explicit - a response that writes a code-owned field is a defect, not a "
    "disagreement."
)


# ---------------------------------------------------------------------------------------------
# L. Gates (brief §27), coarse on purpose
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ValuationGate:
    gate: str
    name: str
    threshold: str
    core: bool


V_GATES: tuple[ValuationGate, ...] = (
    ValuationGate("V1", "PIT completeness: every used field has source, period end, period family "
                        "and acceptance time", "== 0 fields used without full provenance", True),
    ValuationGate("V2", "period and unit consistency across every numerator, denominator, growth "
                        "input and peer multiple", "== 0 incompatible pairs computed", True),
    ValuationGate("V3", "market-cap validity under the H0.5 primitive",
                  "== 0 market caps from stale, ambiguous, multi-class or adjusted inputs", True),
    ValuationGate("V4", "enterprise-value validity",
                  "== 0 EV values published while DEBT_COMPOSITION_CONTRACT is NOT_IMPLEMENTED",
                  True),
    ValuationGate("V5", "at least one applicable valuation method, or NOT_READY",
                  ">= 1 applicable method per COMPLETE/PARTIAL candidate", True),
    ValuationGate("V6", "no fabricated estimate: no consensus, no forward multiple, no "
                        "model-supplied multiple", "== 0 occurrences", True),
    ValuationGate("V7", "deterministic arithmetic: recomputing from stored inputs reproduces every "
                        "published number", "== 0 mismatches", True),
    ValuationGate("V8", "target-price provenance: every TP carries its metric and its observed "
                        "multiple with a source", "== 0 TPs without full provenance", True),
)
assert len({g.gate for g in V_GATES}) == len(V_GATES) == 8

GATE_PHILOSOPHY = (
    "Arithmetic, period and unit errors are forced by code; qualitative reading is the model's; "
    "UNKNOWN stays UNKNOWN. These eight gates are not subdivided further, and D5 does not repeat "
    "D4's pattern of growing a validator one micro-rule per finding. A finding that is not an "
    "arithmetic, period or provenance error is reported as a limitation, not converted into a gate."
)


# ---------------------------------------------------------------------------------------------
# M. D5-D1 pilot (brief §24, §26), proposed and NOT executed
# ---------------------------------------------------------------------------------------------

D5_D1_PURPOSE = (
    "Measure valuation-data completeness, period consistency and method applicability on a "
    "deterministic, performance-blind sample. It produces no valuation, no multiple published as a "
    "finding, no fair value and no target price - it counts what is computable."
)

D5_D1_MEASURES: tuple[str, ...] = (
    "market-cap validity rate, with the UNKNOWN reason for each failure",
    "multi-class detection rate, once a detector exists, or UNMEASURED if it does not",
    "per-field OK / AMBIGUOUS / MISSING counts over the 12 canonical fields",
    "duration-family distribution of every resolved duration field, which the D2.1 bundle omits",
    "TTM constructibility: how many issuers have four non-overlapping quarters, or an annual plus "
    "two year-to-date facts, for each flow field",
    "debt-composition exposure: which canonical tag resolves, and whether the filer also reports a "
    "higher-priority-absent total",
    "cash-versus-debt period alignment, and balance-sheet staleness in days",
    "D&A and interest-expense tag availability, since neither is canonical",
    "per-method applicability counts under METHOD_SPECS",
    "price-panel coverage at the chosen decision time",
)

D5_D1_FORBIDDEN: tuple[str, ...] = (
    "no realized return and no forward return is read, at any point, for any purpose",
    "no issuer is selected or excluded by its D3 or D4 outcome",
    "no multiple is published as a finding, and no valuation of any issuer is published",
    "no live model call is made: the pilot is a data measurement and costs $0",
)
"""Each item is phrased as its own denial rather than as a label on a list.

That is not a style choice. D4-BR's whole convergence defect was a lexical hit read as an assertion,
and E7R's repair reads denial at the clause level - so a prohibition whose negation lives only in the
name of the constant it sits in would trip the project's own detector, which is the mistake this
project has now made twice. A prohibition that states itself is one that survives being quoted."""

D5_D1_SEED = "H_V2_D5_D1_VALUATION_FEASIBILITY_V1"
D5_D1_N = 12

#: Frozen by `regenerate_d5_d1_sample()` over the D2.1 package universe, before any pilot exists.
#: The literal is checked against a fresh computation rather than against a hash of itself.
D5_D1_SAMPLE: tuple[tuple[str, str], ...] = (
    ("ADBE", "0000796343"), ("DALN", "0001413898"), ("CHRS", "0001512762"),
    ("FET", "0001401257"), ("NATR", "0000275053"), ("STAA", "0000718937"),
    ("CHWY", "0001766502"), ("BRY", "0001705873"), ("WBD", "0001437107"),
    ("FULT", "0000700564"), ("COHR", "0000820318"), ("GNW", "0001276520"),
)
D5_D1_CHECKSUM = "0ef2bb56f1affa202b5db33b28b9afb6ebeb78ac28655231bb570ba92d7a6732"


def regenerate_d5_d1_sample(packages_dir: Path | None = None) -> tuple[tuple[str, str], ...]:
    """The pilot sample: deterministic, offline, performance-blind, 0 model calls.

    Seeded hash over the D2.1 package universe, the same convention D3.1 Batch 2, D3.3, Tier B and
    D4-BR all used. No exclusion list and no stratification by D3/D4 outcome: the question is
    whether valuation DATA exists, which has nothing to do with how a candidate scored, and
    excluding the issuers D4 touched would also throw away the only candidates whose D4 output a
    later D6 could join to.
    """
    from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR

    rows: list[tuple[str, str]] = []
    for path in sorted((packages_dir or PACKAGES_DIR).glob("*.json")):
        bundle = json.loads(path.read_text())["evidence_bundle"]
        rows.append((bundle["identity"]["ticker"], bundle["identity"]["cik"]))

    def key(row: tuple[str, str]) -> str:
        return hashlib.sha256(f"{D5_D1_SEED}:{row[0]}".encode()).hexdigest()

    return tuple(sorted(rows, key=key)[:D5_D1_N])


def d5_d1_checksum(sample: tuple[tuple[str, str], ...]) -> str:
    return hashlib.sha256("|".join(t for t, _ in sample).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# N. What must land before any method is reachable
# ---------------------------------------------------------------------------------------------

BLOCKING_DATA_REPAIRS: tuple[tuple[str, str, tuple[MethodId, ...]], ...] = (
    ("duration-family narrowing",
     "`resolve_fact` must narrow duration facts by period family before selecting, so a discrete "
     "quarter and a year-to-date figure in one filing stop colliding. This single repair is what "
     "turns 0 of 10 income-statement fields into a measurable number.",
     (MethodId.PE, MethodId.PS, MethodId.EV_EBIT, MethodId.EV_SALES, MethodId.EV_EBITDA,
      MethodId.PEG_CONTEXT, MethodId.OWN_HISTORICAL_RANGE, MethodId.PEER_RANGE)),
    ("TTM construction",
     TTM_CONSTRUCTION_REQUIRED,
     (MethodId.P_FCF, MethodId.FCF_YIELD, MethodId.PE, MethodId.PS)),
    ("debt composition",
     "A summation contract over the real debt tags, replacing single-tag priority selection, plus a "
     "balance-sheet staleness bound and a cash/debt same-period requirement.",
     (MethodId.EV_EBITDA, MethodId.EV_EBIT, MethodId.EV_SALES)),
    ("D&A as a canonical field",
     "`DepreciationDepletionAndAmortization` / `DepreciationAndAmortization`, reported by 10 of 10 "
     "spot-check issuers and absent from FIELD_SPECS.",
     (MethodId.EV_EBITDA,)),
    ("multi-class detection",
     MULTI_CLASS_IS_UNDETECTED,
     tuple(MethodId)),
)
"""Ordered by how many methods each one unblocks: duration-family narrowing unblocks eight, TTM
construction four, debt composition three, D&A one.

Multi-class detection is listed against every method for a different reason. It unblocks nothing; it
gates market cap, which every method divides by, so an undetected multi-class issuer produces a
wrong number in all of them rather than an UNKNOWN in any.

Stated as a dependency list rather than as a plan: D5-D0 does not authorize any of these repairs,
and each is a change to a primitive H0, D1 and D4 all already depend on, so each needs its own
contract and its own regression evidence before it touches anything."""

ONLY_METHOD_APPLICABLE_TODAY = MethodId.PB
"""Measured, not assumed: P/B is the one method in METHOD_SPECS whose every required field resolved
OK on all ten spot-check issuers. It is also the method with the narrowest valid application, so
"one method is applicable" is not the same as "D5 can value these issuers" - under
`MIN_METHODS_FOR_COMPLETE` a P/B-only candidate is PARTIAL at best, and for an asset-light issuer
P/B is NOT_APPLICABLE on its own terms."""


def v1_applicable_methods() -> tuple[MethodId, ...]:
    return tuple(s.method for s in METHOD_SPECS if s.v1_status is MethodStatus.APPLICABLE)


def contract_summary() -> dict:
    """The contract as data, for the pilot's preflight to assert against."""
    return {
        "contract_version": D5_D0_CONTRACT_VERSION,
        "fields": [f.to_dict() for f in VALUATION_FIELDS],
        "methods": [m.to_dict() for m in METHOD_SPECS],
        "gates": [{"gate": g.gate, "name": g.name, "threshold": g.threshold, "core": g.core}
                  for g in V_GATES],
        "v1_applicable_methods": [m.value for m in v1_applicable_methods()],
        "blocking_repairs": [name for name, _, _ in BLOCKING_DATA_REPAIRS],
        "historical_range_label": HISTORICAL_RANGE_LABEL,
        "panel": {"first": HISTORICAL_PANEL_FIRST_SESSION.isoformat(),
                  "last": HISTORICAL_PANEL_LAST_SESSION.isoformat(),
                  "sessions": HISTORICAL_PANEL_SESSIONS},
        "debt_composition_contract": DEBT_COMPOSITION_CONTRACT,
        "d4_limitations_carried_forward": dict(D4_LIMITATIONS_CARRIED_FORWARD),
        "executes_valuation": False,
    }
