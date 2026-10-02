"""H-V2-D5-P1: PIT-safe cash, total debt, net debt and enterprise value, constructed deterministically.

This module produces no multiple, no fair value, no target price and no decision. It produces four
balance-sheet-dated quantities and the provenance of every fact behind them.

P0 repaired the period primitive, P0.1 built trailing-twelve-month flows on top of it, and both left
the instant side of a valuation exactly where D5-D0 found it. D5-D0's finding was not that debt was
missing: it was that `total_debt` resolved OK and was not total debt. Three mechanisms, each measured
on the ten issuers D4 contacted:

  1. `resolve_fact` selects ONE tag by priority and never sums, and `FIELD_SPECS["total_debt"]`
     lists `LongTermDebtCurrent` above `LongTermDebt`. So a filer reporting current 850k, noncurrent
     15,568k and total 16,418k resolved to 850k with status OK (AEYE, 2026-06-30). DORM resolved to
     0 the same way: its `LongTermDebtCurrent` at 2026-06-27 is 0 while `LongTermDebtNoncurrent` is
     440,479k.
  2. No instant field had a staleness bound at all, so COLL's `total_debt` resolved from a
     2019-12-31 period end, 2,463 days before the cutoff, with status OK.
  3. The canonical tag set is not the tag set filers use. COLL reports no current long-term-debt tag
     and instead reports `LoansPayableCurrent`, `LongTermLoansPayable` and
     `ConvertibleLongTermNotesPayable`; SPSC reports no borrowing balance at all while having
     reported a credit facility in 2013.

So debt here is a composition with a declared hierarchy, not a tag lookup, and every instant fact is
bounded in age. The four prohibitions in `NEVER_CONSTRUCTED` are the shape of the refusals.

What this module will not do, stated so that quoting a line cannot turn it into its opposite:

    a missing component is reported as UNKNOWN            (never as zero)
    a stale balance sheet is reported as STALE            (never used)
    two tags that may overlap are reported as AMBIGUOUS   (never added together)
    cash and debt from two balance-sheet dates are        (never netted)
        reported as COMPONENT_DATE_MISMATCH

The one place a number is invented in a valuation pipeline is the place where a component is absent
and zero is close enough. There is no such place below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Iterable, Mapping, Sequence

from app.backtest.strategy_h0.facts import (
    CanonicalFact,
    DurationFamily,
    FactStatus,
    FieldSpec,
    resolve_fact,
)
from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS
from app.backtest.strategy_h_v2.valuation.fundamental_fields import VALUATION_FIELD_SPECS
from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
    MarketCapResolution,
    MarketCapStatus,
)
from app.backtest.strategy_h_v2.valuation.ttm import TtmResult, ebitda_feasible

CAPITAL_STRUCTURE_CONTRACT_VERSION = "h_v2_d5_p1_capital_structure_v1"

#: How far a balance-sheet instant's `end` may lag the decision date before the fact stops
#: describing the present.
#:
#: The number is `h0_5.MAX_SHARES_STALENESS_DAYS`, reused rather than invented, for the third time in
#: this program - H0.5 bounded `shares_outstanding` with it, P0.1 bounded the constructed TTM period
#: with it, and it bounds the instant fields here. It is the only validated staleness bound in this
#: repository; it answers the identical question; it is applied the identical way,
#: `(decision_date - end).days > bound`. D5-D0 recorded the asymmetry that made this necessary:
#: shares were bounded and `cash`, `total_debt`, `assets` and `equity` were not, so a 2019 balance
#: sheet netted against a 2026 market capitalisation produced an enterprise value that was wrong and
#: carried status OK. A second number for the same question would be a second thing to keep in
#: agreement, which is the drift P0 spent its whole length removing.
MAX_INSTANT_STALENESS_DAYS = MAX_SHARES_STALENESS_DAYS

NEVER_CONSTRUCTED: tuple[str, ...] = (
    "an absent debt component is reported UNKNOWN, and a debt of zero requires a fact that says zero",
    "a reported total debt is used as reported, and is never added to the components inside it",
    "two tags that may describe the same borrowing are reported AMBIGUOUS, and are never summed",
    "a balance-sheet fact older than the staleness bound is reported STALE, and is never used",
    "cash and debt are netted only when both are dated the same balance-sheet day",
    "an enterprise value requires every input valid: one UNKNOWN input makes the result UNKNOWN",
    "a debt figure is never read out of filing prose and placed into arithmetic",
)
"""Each prohibition states itself, the house rule D4-E7R left behind: a prohibition whose negation
lives only in the name of the constant holding it trips this project's own leakage detector."""


# -------------------------------------------------------------------------------------------------
# D. The cash contract
# -------------------------------------------------------------------------------------------------

#: Cash for an enterprise value: unrestricted cash and cash equivalents, as the filer reports them.
#:
#: One tag, no summation, and the single-element tuple is the contract rather than an accident. The
#: measured reason it is one tag: `FIELD_SPECS["cash"]` carries
#: `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` as its second priority, and that
#: tag is a different measure, not a fallback for the first. COLL at 2026-06-30 reports 129,467k
#: under the unrestricted tag and 150,377k under the restricted-inclusive one, the 20,910k difference
#: being restricted cash it is not free to apply against debt. A fallback that silently moves between
#: the two would overstate cash, understate enterprise value, and do so only for the filers that
#: happen not to tag the unrestricted line.
#:
#: Measured availability: the unrestricted tag is reported at a fresh period end by 10 of the 10
#: issuers D4 contacted, so this costs no coverage on the measured corpus.
CASH_FOR_EV_TAGS: tuple[str, ...] = ("CashAndCashEquivalentsAtCarryingValue",)

#: Cash-like tags deliberately outside `CASH_FOR_EV`, each with the reason it is out.
#:
#: These are not summed in and are not substituted when `CASH_FOR_EV_TAGS` is absent. An absent cash
#: tag is MISSING, which is the same rule the debt side follows.
CASH_EXCLUDED_FROM_EV: tuple[tuple[str, str], ...] = (
    ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
     "includes restricted cash; COLL 150,377k against 129,467k unrestricted at 2026-06-30"),
    ("RestrictedCash", "restricted by definition; not available to retire debt"),
    ("RestrictedCashCurrent", "same, and reported separately by COLL (19,850k), VRRM and IDCC"),
    ("RestrictedCashAndCashEquivalents", "same"),
    ("ShortTermInvestments",
     "a security, not cash. Material where it exists - IDCC 496,821k at 2026-06-30 - and whether a "
     "net-debt definition includes it is a choice that differs between data providers. Excluding it "
     "raises enterprise value, which is the conservative direction, and the choice is recorded here "
     "rather than made silently"),
    ("OtherShortTermInvestments", "same; FG 545,000k at 2026-06-30"),
    ("MarketableSecuritiesCurrent", "same"),
    ("AvailableForSaleSecuritiesDebtSecuritiesCurrent",
     "same; COLL 157,341k, and its own period end is 2026-03-31 while cash is 2026-06-30"),
    ("CashAndDueFromBanks",
     "a bank's operating cash, and netting it against a bank's borrowings is not a defined "
     "enterprise value - deposits are funding, not debt. FULT reports it (325,259k at 2026-06-30) "
     "and reports no CashAndCashEquivalentsAtCarryingValue at all, so FULT's cash is MISSING here. "
     "That is the intended answer: a bank needs a valuation contract of its own, which this version "
     "does not have"),
)

CASH_FOR_EV_DEFINITION = (
    "CASH_FOR_EV = the reported balance of CashAndCashEquivalentsAtCarryingValue at the valuation "
    "balance-sheet date, within the instant staleness bound. Restricted cash, short-term "
    "investments and marketable securities are excluded by name, and no excluded tag is ever "
    "substituted for the included one. When the included tag is absent the result is MISSING."
)


# -------------------------------------------------------------------------------------------------
# E/F. Debt tags, in slots, with a declared hierarchy
# -------------------------------------------------------------------------------------------------

class DebtSlot(StrEnum):
    REPORTED_TOTAL = "REPORTED_TOTAL"
    """A balance that the filer itself states is the whole of its borrowings."""
    CURRENT = "CURRENT"
    """Borrowings due within a year: short-term borrowings, drawn revolver, current maturities."""
    NONCURRENT = "NONCURRENT"
    """Borrowings due beyond a year."""


#: A tag whose value IS the filer's total borrowings, so no addition is needed or allowed.
#:
#: Two tags, and the exclusions matter more than the inclusions:
#:
#:   `DebtInstrumentCarryingAmount` is out. It is a note disclosure about one instrument, not a
#:   balance-sheet total, and it is why this list is measured rather than guessed: AEYE reports it as
#:   16,787k at 2026-06-30 against `LongTermDebt` of 16,418k - the difference being unamortised
#:   issuance cost - so admitting it would have made AEYE's debt AMBIGUOUS and lost a correct answer.
#:
#:   `LongTermDebtAndCapitalLeaseObligations` is out because it bundles leases into the total, and
#:   `EV_DEBT_SCOPE` is borrowings only. A bundled total cannot be reduced to the scope this version
#:   measures, so a filer whose only total is that tag resolves through the component path or not at
#:   all. WBD reports it - 30,530,000k at 2026-06-30 alongside `LongTermDebt` 32,023,000k and
#:   `LongTermDebtAndCapitalLeaseObligationsCurrent` 1,493,000k, the first being exactly the sum of
#:   the other two - and that arithmetic is why it is excluded rather than preferred: WBD uses the
#:   two tag families as one measure, so whether its 32,023,000k contains its 683,000k of finance
#:   lease liability is not stated anywhere in the filing. See `LEASE_BUNDLED_DEBT_TAGS`, which
#:   records the presence of such a tag on the chosen balance sheet instead of resolving the question.
REPORTED_TOTAL_DEBT_TAGS: tuple[str, ...] = (
    "DebtLongtermAndShorttermCombinedAmount",
    "LongTermDebt",
)

#: Current borrowings, with `DebtCurrent` first because it is the us-gaap element for the whole of
#: current debt rather than one part of it.
#:
#: `LongTermDebtAndFinanceLeaseObligationsCurrent` is absent, in `LEASE_BUNDLED_DEBT_TAGS` instead,
#: because it bundles finance leases and this version's scope is borrowings. It is
#: `FIELD_SPECS["total_debt"]`'s first priority, which is how a current, lease-inclusive figure came
#: to be the canonical answer for "total debt".
CURRENT_DEBT_TAGS: tuple[str, ...] = (
    "DebtCurrent",
    "LongTermDebtCurrent",
    "ShortTermBorrowings",
    "OtherShortTermBorrowings",
    "LinesOfCreditCurrent",
    "LineOfCredit",
    "NotesPayableCurrent",
    "LoansPayableCurrent",
    "NotesAndLoansPayableCurrent",
    "ConvertibleDebtCurrent",
    "ConvertibleNotesPayableCurrent",
    "CommercialPaper",
    "SecuredDebtCurrent",
    "UnsecuredDebtCurrent",
    "OtherLoansPayableCurrent",
    "BankOverdrafts",
)

#: Noncurrent borrowings. `ConvertibleSubordinatedDebtNoncurrent` is here because the audit's residual
#: scan found it unslotted on CHRS at 227,220k, which is what that scan is for.
NONCURRENT_DEBT_TAGS: tuple[str, ...] = (
    "LongTermDebtNoncurrent",
    "LongTermLineOfCredit",
    "LongTermLoansPayable",
    "LongTermNotesPayable",
    "ConvertibleLongTermNotesPayable",
    "ConvertibleDebtNoncurrent",
    "ConvertibleSubordinatedDebtNoncurrent",
    "SecuredLongTermDebt",
    "UnsecuredLongTermDebt",
    "SeniorLongTermNotes",
    "SeniorNotes",
    "OtherLongTermDebtNoncurrent",
    "NotesPayableRelatedPartiesNoncurrent",
    "LoansPayableToBankNoncurrent",
    "LongTermLoansFromBank",
)

DEBT_SLOT_TAGS: Mapping[DebtSlot, tuple[str, ...]] = {
    DebtSlot.REPORTED_TOTAL: REPORTED_TOTAL_DEBT_TAGS,
    DebtSlot.CURRENT: CURRENT_DEBT_TAGS,
    DebtSlot.NONCURRENT: NONCURRENT_DEBT_TAGS,
}

#: Tags that name debt and are not a balance of borrowings. Listed because each was observed in the
#: stored companyfacts of the measured corpus and each would corrupt a composition if admitted.
DEBT_TAGS_THAT_ARE_NOT_A_BALANCE: tuple[tuple[str, str], ...] = (
    ("DebtInstrumentCarryingAmount", "one instrument's carrying amount; AEYE 16,787k vs total 16,418k"),
    ("DebtInstrumentFaceAmount", "principal at issue, not the balance outstanding"),
    ("DebtInstrumentFairValue", "a fair-value disclosure; COLL 458,334k against a carrying total"),
    ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
     "a maturity schedule row. Reads like current debt and is a future payment"),
    ("LineOfCreditFacilityMaximumBorrowingCapacity", "capacity, not drawings. SPSC reports 1,000k "
                                                     "capacity and no drawn balance"),
    ("LineOfCreditFacilityAmountOutstanding",
     "outstanding under one facility, which is a facility disclosure rather than the balance-sheet "
     "line. SPSC's last value is 0 at 2013-12-31, which is why 'no canonical tag' and 'zero debt' "
     "are different answers"),
    ("UnamortizedDebtIssuanceExpense", "a contra-liability, already inside the carrying amount"),
    ("ProceedsFromLinesOfCredit", "a cash-flow movement, not a balance"),
)

#: Lease liabilities, out of this version's debt scope and never summed into it.
LEASE_LIABILITY_TAGS_OUT_OF_SCOPE: tuple[str, ...] = (
    "FinanceLeaseLiability", "FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent",
    "CapitalLeaseObligations", "CapitalLeaseObligationsCurrent", "CapitalLeaseObligationsNoncurrent",
    "OperatingLeaseLiability", "OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent",
)

#: Tags that state borrowings and lease obligations as one number. Never in a slot, and recorded as
#: evidence when one is present on the balance sheet a debt figure came from.
#:
#: They exist because `EV_DEBT_SCOPE` is a declaration about what this version measures and not a
#: property the filings guarantee. `LongTermDebtAndFinanceLeaseObligationsCurrent` is
#: `FIELD_SPECS["total_debt"]`'s first priority, which is how a current, lease-inclusive figure became
#: the canonical answer to "total debt"; and WBD shows that the question survives excluding those tags,
#: because its `LongTermDebt` is numerically identical to its lease-bundled pair. So the presence of
#: one of these at the chosen period end is attached to the result as
#: `DebtResolution.lease_bundled_tags_at_same_end`, and a consumer comparing two issuers' debt can see
#: that one of them carries an unresolved lease question of a stated size.
LEASE_BUNDLED_DEBT_TAGS: tuple[str, ...] = (
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    "LongTermDebtAndFinanceLeaseObligation",
)

LEASE_TREATMENT = "DEFERRED"
"""Lease liabilities are outside `EV_DEBT_SCOPE` in v1, and the decision to include them is deferred
rather than made. The grounds for deferring are measured rather than stylistic.

There is no prior Strategy H intent to follow: the only lease liability anywhere in H0 or H-V2 is the
tag name `LongTermDebtAndFinanceLeaseObligationsCurrent`, sitting first in `FIELD_SPECS["total_debt"]`
priority, which is an artefact of tag ordering rather than a decision about leases.

And the tag availability settles the version-1 question. A finance lease liability is reported at a
fresh period end by 1 of the 10 measured issuers (FRPT, 28,979k at 2026-06-30). The other nine either
never tag it or last tagged it as zero years ago - AEYE 0 at 2025-06-30, VRRM 0 at 2019-12-31, IDCC 0
at 2025-12-31, DORM 0 at 2020-12-26, and COLL, TG, CRK and SPSC not at all. Under this module's own
rule that an absent component is UNKNOWN rather than zero, requiring a finance lease component would
refuse nine of ten issuers in order to add a figure that is zero or untagged in every one of them.

Measured cost of the exclusion, stated as exposure rather than as reassurance: of the issuers whose
debt does compose, none reports a fresh non-zero finance lease liability, so this version's debt
figures would be unchanged by the opposite decision. That is a fact about this corpus and not a
general argument, which is why the treatment is DEFERRED and not CLOSED."""

EV_DEBT_SCOPE = (
    "BORROWINGS_ONLY: short-term borrowings, drawn credit facilities, current and noncurrent "
    "long-term debt, convertible debt and notes payable, as carried on the balance sheet. Finance "
    "and operating lease liabilities are excluded, and a tag that bundles leases into a debt total "
    "is excluded rather than apportioned. Pension and asset-retirement obligations are not "
    "borrowings and are not in any slot; the residual scan in the audit is what establishes that no "
    "borrowing balance was left out rather than asserting it."
)


# -------------------------------------------------------------------------------------------------
# The field registry
# -------------------------------------------------------------------------------------------------

CASH_FOR_EV_FIELD = "cash_for_ev"
DEBT_LEASE_BUNDLED_FIELD = "debt_lease_bundled"
"""Extracted so the evidence can be looked at, and in no slot so it can never be summed."""
DEBT_SLOT_FIELDS: Mapping[DebtSlot, str] = {
    DebtSlot.REPORTED_TOTAL: "debt_reported_total",
    DebtSlot.CURRENT: "debt_current",
    DebtSlot.NONCURRENT: "debt_noncurrent",
}

#: `VALUATION_FIELD_SPECS` plus the four capital-structure fields, for the same reason P0.1 added a
#: registry instead of widening `FIELD_SPECS`: `len(FIELD_SPECS)` is D1's `total_field_count` and its
#: resolved count feeds the eligibility floor, so a new canonical field would move historical D1/D2
#: outcomes that §21 forbids touching. One parser, three registries, each a strict superset of the
#: last.
CAPITAL_STRUCTURE_FIELD_SPECS: dict[str, FieldSpec] = {
    **VALUATION_FIELD_SPECS,
    CASH_FOR_EV_FIELD: FieldSpec(CASH_FOR_EV_TAGS, ("USD",), "instant"),
    DEBT_SLOT_FIELDS[DebtSlot.REPORTED_TOTAL]: FieldSpec(REPORTED_TOTAL_DEBT_TAGS, ("USD",), "instant"),
    DEBT_SLOT_FIELDS[DebtSlot.CURRENT]: FieldSpec(CURRENT_DEBT_TAGS, ("USD",), "instant"),
    DEBT_SLOT_FIELDS[DebtSlot.NONCURRENT]: FieldSpec(NONCURRENT_DEBT_TAGS, ("USD",), "instant"),
    DEBT_LEASE_BUNDLED_FIELD: FieldSpec(LEASE_BUNDLED_DEBT_TAGS, ("USD",), "instant"),
}

CAPITAL_STRUCTURE_ONLY_FIELDS: frozenset[str] = (
    frozenset(CAPITAL_STRUCTURE_FIELD_SPECS) - frozenset(VALUATION_FIELD_SPECS))

assert set(VALUATION_FIELD_SPECS) < set(CAPITAL_STRUCTURE_FIELD_SPECS)
assert all(CAPITAL_STRUCTURE_FIELD_SPECS[name] is spec
           for name, spec in VALUATION_FIELD_SPECS.items()), (
    "the inherited specs must be shared objects, not copies that can drift"
)


# -------------------------------------------------------------------------------------------------
# C. Statuses
# -------------------------------------------------------------------------------------------------

class InstantStatus(StrEnum):
    """One slot's outcome at one balance-sheet date."""

    OK = "OK"
    MISSING = "MISSING"
    """No tag of this slot reports a value at this period end, known by the decision time."""
    STALE = "STALE"
    """A value exists and its period end is older than the staleness bound."""
    AMBIGUOUS_TAGS = "AMBIGUOUS_TAGS"
    """More than one tag of this slot reports a different value at this period end.

    The whole defect this module exists to repair is a resolver choosing one of those by priority, so
    this is a refusal and not a tie-break. Measured case: COLL at 2026-06-30 reports
    `LongTermLoansPayable` 797,824k and `ConvertibleLongTermNotesPayable` 238,733k, both noncurrent.
    Their sum may well be COLL's noncurrent debt; no part of the filing says that those two are the
    complete set, and adding them because they are the two present is the same completeness
    assumption that `fundamental_fields` refuses for D&A components."""


class DebtStatus(StrEnum):
    OK = "OK"
    MISSING = "MISSING"
    """No slot reports anything at this period end. Not zero debt - no evidence either way."""
    STALE = "STALE"
    INCOMPLETE_COMPONENTS = "INCOMPLETE_COMPONENTS"
    """One of current and noncurrent resolves and the other does not, with no reported total.

    Reported separately from MISSING because the asymmetry is the finding: FRPT reports
    `ConvertibleDebtNoncurrent` 398,443k at 2026-06-30 and no current borrowing tag at that date,
    and CRK reports `LongTermDebtNoncurrent` 3,098,770k and no current tag. Both probably carry no
    current debt. Companyfacts does not say so, and the difference between 'the filer reported zero'
    and 'the filer reported nothing' is the difference between a debt figure and a guess."""
    AMBIGUOUS_TAGS = "AMBIGUOUS_TAGS"


class DebtMethod(StrEnum):
    REPORTED_TOTAL = "REPORTED_TOTAL"
    """One tag the filer states is its whole borrowings, used as reported."""
    CURRENT_PLUS_NONCURRENT = "CURRENT_PLUS_NONCURRENT"
    """The current slot plus the noncurrent slot, at one period end, each a single tag."""


SHORT_TERM_PLUS_LONG_TERM_IS_NOT_A_THIRD_METHOD = (
    "The brief's third candidate method, SHORT_TERM_PLUS_LONG_TERM, is not defined here, because the "
    "tags it would use - ShortTermBorrowings, LinesOfCreditCurrent, CommercialPaper - are current "
    "borrowings and already fill the CURRENT slot. A separate method name would assert that the "
    "filer distinguished 'short-term borrowings' from 'the current portion of long-term debt' as two "
    "additive things, and when a filer does report both at one date this module reports "
    "AMBIGUOUS_TAGS rather than adding them. Measured: TG reports ShortTermBorrowings 0 and "
    "LongTermDebtNoncurrent 46,000k at 2026-06-30, which composes as CURRENT_PLUS_NONCURRENT with an "
    "explicit zero current component - the one shape in which a zero is admissible."
)


class CapitalStructureStatus(StrEnum):
    """The composite outcome. Leg failures are mapped onto these names, which are the brief's §18."""

    OK = "OK"
    MISSING_CASH = "MISSING_CASH"
    MISSING_DEBT = "MISSING_DEBT"
    STALE_CASH = "STALE_CASH"
    STALE_DEBT = "STALE_DEBT"
    INCOMPLETE_DEBT_COMPONENTS = "INCOMPLETE_DEBT_COMPONENTS"
    AMBIGUOUS_DEBT_TAGS = "AMBIGUOUS_DEBT_TAGS"
    AMBIGUOUS_CASH_TAGS = "AMBIGUOUS_CASH_TAGS"
    COMPONENT_DATE_MISMATCH = "COMPONENT_DATE_MISMATCH"
    """Cash and debt resolved at two different balance-sheet dates. Never netted."""
    MULTI_CLASS_UNKNOWN = "MULTI_CLASS_UNKNOWN"
    """The share-class determination the market cap needs is absent or negative."""
    MARKET_CAP_UNKNOWN = "MARKET_CAP_UNKNOWN"
    """Market cap is UNKNOWN for a reason other than share class: shares or price."""
    UNKNOWN = "UNKNOWN"


_DEBT_STATUS_TO_COMPOSITE = {
    DebtStatus.MISSING: CapitalStructureStatus.MISSING_DEBT,
    DebtStatus.STALE: CapitalStructureStatus.STALE_DEBT,
    DebtStatus.INCOMPLETE_COMPONENTS: CapitalStructureStatus.INCOMPLETE_DEBT_COMPONENTS,
    DebtStatus.AMBIGUOUS_TAGS: CapitalStructureStatus.AMBIGUOUS_DEBT_TAGS,
}
_CASH_STATUS_TO_COMPOSITE = {
    InstantStatus.MISSING: CapitalStructureStatus.MISSING_CASH,
    InstantStatus.STALE: CapitalStructureStatus.STALE_CASH,
    InstantStatus.AMBIGUOUS_TAGS: CapitalStructureStatus.AMBIGUOUS_CASH_TAGS,
}
_MARKET_CAP_STATUS_TO_COMPOSITE = {
    MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED: CapitalStructureStatus.MULTI_CLASS_UNKNOWN,
    MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES: CapitalStructureStatus.MULTI_CLASS_UNKNOWN,
    MarketCapStatus.UNKNOWN_SHARES: CapitalStructureStatus.MARKET_CAP_UNKNOWN,
    MarketCapStatus.UNKNOWN_PRICE: CapitalStructureStatus.MARKET_CAP_UNKNOWN,
}


# -------------------------------------------------------------------------------------------------
# L. Provenance records
# -------------------------------------------------------------------------------------------------

def fact_id(fact: CanonicalFact) -> str:
    """A fact's identity in companyfacts, the same tuple `ttm.TtmComponent.fact_id` uses.

    Companyfacts has no row id, so identity is accession plus tag plus unit plus period - exactly
    what `resolve_fact` narrows on, which means two facts sharing this string are the duplicate rows
    the resolver already collapses.
    """
    return (f"{fact.accession}:{fact.tag}:{fact.unit}:"
            f"{fact.start.isoformat() if fact.start else 'instant'}:{fact.end.isoformat()}")


def _fact_record(fact: CanonicalFact) -> dict:
    return {
        "fact_id": fact_id(fact),
        "tag": fact.tag,
        "unit": fact.unit,
        "value": fact.value,
        "end": fact.end.isoformat(),
        "form": fact.form,
        "accession": fact.accession,
        "acceptance_time": fact.accepted_at.isoformat(),
    }


@dataclass(frozen=True)
class InstantResolution:
    """One slot at one balance-sheet date, or a named refusal."""

    field: str
    status: InstantStatus
    reason: str
    fact: CanonicalFact | None = None
    period_end: date | None = None
    age_days: int | None = None
    candidates: tuple[CanonicalFact, ...] = ()
    """Every tag of the slot that resolved at this period end. One element on an OK result; more than
    one only when they disagreed, which is why the result is AMBIGUOUS_TAGS."""

    @property
    def ok(self) -> bool:
        return self.status is InstantStatus.OK and self.fact is not None

    @property
    def value(self) -> float | None:
        return None if self.fact is None else self.fact.value

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "status": self.status.value,
            "reason": self.reason,
            "value": self.value,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "age_days": self.age_days,
            "fact": None if self.fact is None else _fact_record(self.fact),
            "candidate_tags": sorted({f.tag for f in self.candidates}),
        }


@dataclass(frozen=True)
class DebtResolution:
    """A composed total debt, or a named refusal, with every component fact preserved."""

    status: DebtStatus
    reason: str
    value: float | None = None
    unit: str | None = None
    method: DebtMethod | None = None
    period_end: date | None = None
    age_days: int | None = None
    components: tuple[tuple[DebtSlot, CanonicalFact], ...] = ()
    slots: Mapping[DebtSlot, InstantResolution] = ()  # type: ignore[assignment]
    reported_total_minus_components: float | None = None
    """When a reported total is used AND both components also resolve at the same date, the
    arithmetic difference between the total and the sum of its parts. Recorded, not gated: it is a
    cross-check on the hierarchy claim, and AEYE's is exactly 0.0 (16,418k against 850k + 15,568k)."""
    lease_bundled_tags_at_same_end: tuple[tuple[str, float], ...] = ()
    """Lease-bundled debt tags the filer reports on this same balance sheet, with their values.

    Non-empty means the scope of the figure above is not established by the filing: see
    `LEASE_BUNDLED_DEBT_TAGS`. Recorded rather than gated, because a reported total that may or may
    not contain a lease liability is still that filer's total debt, and refusing it would discard a
    real number over a question the filing does not answer either way."""

    @property
    def ok(self) -> bool:
        return self.status is DebtStatus.OK and self.value is not None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "value": self.value,
            "unit": self.unit,
            "construction_method": self.method.value if self.method else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "age_days": self.age_days,
            "scope": EV_DEBT_SCOPE,
            "lease_treatment": LEASE_TREATMENT,
            "reported_total_minus_components": self.reported_total_minus_components,
            "lease_bundled_tags_at_same_end": [{"tag": tag, "value": value}
                                               for tag, value in self.lease_bundled_tags_at_same_end],
            "components": [{"slot": slot.value, **_fact_record(fact)}
                           for slot, fact in self.components],
            "slots": {slot.value: res.to_dict() for slot, res in dict(self.slots).items()},
        }


@dataclass(frozen=True)
class NetDebtResolution:
    status: CapitalStructureStatus
    reason: str
    value: float | None = None
    unit: str | None = None
    period_end: date | None = None
    cash: InstantResolution | None = None
    debt: DebtResolution | None = None

    @property
    def ok(self) -> bool:
        return self.status is CapitalStructureStatus.OK and self.value is not None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "net_debt": self.value,
            "unit": self.unit,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "cash": None if self.cash is None else self.cash.to_dict(),
            "debt": None if self.debt is None else self.debt.to_dict(),
        }


@dataclass(frozen=True)
class EnterpriseValueResolution:
    status: CapitalStructureStatus
    reason: str
    decision_time: datetime
    decision_date: date
    value: float | None = None
    market_cap: MarketCapResolution | None = None
    net_debt: NetDebtResolution | None = None

    @property
    def ok(self) -> bool:
        return self.status is CapitalStructureStatus.OK and self.value is not None

    @property
    def components_sum(self) -> float | None:
        """`market_cap + total_debt - cash`, the §15 identity's other ordering.

        Equal to `value` as a definition - `value` is `market_cap + net_debt` and net debt is
        `total_debt - cash` - and computed here in the other association order so a test can assert
        the two agree to double precision rather than the module asserting it about itself."""
        if self.market_cap is None or self.market_cap.value is None:
            return None
        if self.net_debt is None or self.net_debt.debt is None or self.net_debt.cash is None:
            return None
        debt, cash = self.net_debt.debt.value, self.net_debt.cash.value
        if debt is None or cash is None:
            return None
        return self.market_cap.value + debt - cash

    def to_dict(self) -> dict:
        """The §17 provenance record."""
        mc = self.market_cap
        return {
            "contract_version": CAPITAL_STRUCTURE_CONTRACT_VERSION,
            "status": self.status.value,
            "reason": self.reason,
            "decision_time": self.decision_time.isoformat(),
            "decision_date": self.decision_date.isoformat(),
            "enterprise_value": self.value,
            "market_cap": None if mc is None else mc.value,
            "market_cap_provenance": None if mc is None else {
                "status": mc.status.value,
                "reason": mc.reason,
                "shares_fact": (None if mc.shares is None or mc.shares.fact is None
                                else _fact_record(mc.shares.fact)),
                "shares_reason": None if mc.shares is None else mc.shares.reason,
            },
            "net_debt": None if self.net_debt is None else self.net_debt.to_dict(),
            "staleness_bound_days": MAX_INSTANT_STALENESS_DAYS,
        }


# -------------------------------------------------------------------------------------------------
# Slot resolution
# -------------------------------------------------------------------------------------------------

def _one_tag_spec(tag: str) -> FieldSpec:
    return FieldSpec((tag,), ("USD",), "instant")


def _resolve_slot(rows: Sequence[CanonicalFact], field: str, tags: Sequence[str],
                  decision_time: datetime, decision_date: date, period_end: date,
                  max_staleness_days: int) -> InstantResolution:
    """Resolve one slot at one period end, refusing rather than choosing between disagreeing tags.

    Each candidate tag goes through `resolve_fact` on its own, with a one-tag spec and the rows
    filtered to that tag, so the PIT rules are inherited exactly: a fact is admissible only when
    `accepted_at <= decision_time`, an amendment accepted before the decision time supersedes the
    original, exact duplicate rows collapse, and conflicting rows sharing the winning provenance stay
    AMBIGUOUS. What is not inherited is tag priority as a silent tie-break, which is the defect.
    """
    age = (decision_date - period_end).days
    if age > max_staleness_days:
        return InstantResolution(field, InstantStatus.STALE,
                                 f"the period end {period_end} is {age} days before the decision "
                                 f"date, over the {max_staleness_days}-day bound",
                                 period_end=period_end, age_days=age)
    resolved: list[CanonicalFact] = []
    for tag in tags:
        tagged = [f for f in rows if f.tag == tag]
        if not tagged:
            continue
        outcome = resolve_fact(tagged, field, decision_time, report_end=period_end,
                               duration_family=DurationFamily.INSTANT,
                               specs={field: _one_tag_spec(tag)})
        if outcome.status is FactStatus.AMBIGUOUS:
            return InstantResolution(field, InstantStatus.AMBIGUOUS_TAGS,
                                     f"{tag} at {period_end}: {outcome.reason}",
                                     period_end=period_end, age_days=age)
        if outcome.status is FactStatus.OK and outcome.fact is not None:
            resolved.append(outcome.fact)
    if not resolved:
        return InstantResolution(field, InstantStatus.MISSING,
                                 f"no tag of this slot reports a value at {period_end}",
                                 period_end=period_end, age_days=age)
    values = {fact.value for fact in resolved}
    if len(values) > 1:
        detail = ", ".join(f"{f.tag}={f.value:.0f}"
                           for f in sorted(resolved, key=lambda f: tags.index(f.tag)))
        return InstantResolution(
            field, InstantStatus.AMBIGUOUS_TAGS,
            f"{len(values)} different values at {period_end} ({detail}); whether they overlap or "
            "add is not stated by the filing",
            period_end=period_end, age_days=age, candidates=tuple(resolved))
    # One value, possibly under synonymous tags. The highest-priority tag carries it, deterministically.
    chosen = min(resolved, key=lambda f: (tags.index(f.tag), f.accession))
    return InstantResolution(field, InstantStatus.OK, "one value at this balance-sheet date",
                             fact=chosen, period_end=period_end, age_days=age,
                             candidates=tuple(resolved))


def _instant_ends(rows: Sequence[CanonicalFact], decision_time: datetime, decision_date: date,
                  max_staleness_days: int) -> tuple[list[date], date | None]:
    """Instant period ends known at `decision_time`, newest first, plus the newest excluded as stale.

    Ends after the decision date are not candidates at all: a balance sheet dated into the future of
    the valuation instant is not a staleness question.
    """
    ends = {f.end for f in rows if f.start is None and f.accepted_at <= decision_time
            and f.end <= decision_date}
    oldest = decision_date - timedelta(days=max_staleness_days)
    live = sorted((end for end in ends if end >= oldest), reverse=True)
    stale = max((end for end in ends if end < oldest), default=None)
    return live, stale


def resolve_cash_for_ev(
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    decision_date: date,
    *,
    period_end: date | None = None,
    max_staleness_days: int = MAX_INSTANT_STALENESS_DAYS,
) -> InstantResolution:
    """Unrestricted cash and cash equivalents at the newest fresh balance-sheet date, or a refusal."""
    rows = [f for f in facts if f.field == CASH_FOR_EV_FIELD]
    if period_end is not None:
        return _resolve_slot(rows, CASH_FOR_EV_FIELD, CASH_FOR_EV_TAGS, decision_time,
                             decision_date, period_end, max_staleness_days)
    live, stale = _instant_ends(rows, decision_time, decision_date, max_staleness_days)
    first: InstantResolution | None = None
    for end in live:
        outcome = _resolve_slot(rows, CASH_FOR_EV_FIELD, CASH_FOR_EV_TAGS, decision_time,
                               decision_date, end, max_staleness_days)
        if outcome.ok:
            return outcome
        first = first or outcome
    if first is not None:
        return first
    if stale is not None:
        age = (decision_date - stale).days
        return InstantResolution(CASH_FOR_EV_FIELD, InstantStatus.STALE,
                                 f"the newest cash balance ends {stale}, {age} days before the "
                                 f"decision date, over the {max_staleness_days}-day bound",
                                 period_end=stale, age_days=age)
    return InstantResolution(CASH_FOR_EV_FIELD, InstantStatus.MISSING,
                             f"no {CASH_FOR_EV_TAGS[0]} fact is known at the decision time")


TAG_PRIORITY_IS_A_PREFERENCE_FOR: frozenset[str] = frozenset({"equity", "assets", "cash"})
"""Instant fields whose `FieldSpec` tag order is a preference between whole definitions of one line
item, so inheriting `resolve_fact`'s priority is correct and refusing on disagreement is not.

The distinction between these and the debt slots is the whole of this module's debt argument, so it is
worth stating in both directions. `equity`'s two tags are `StockholdersEquity` and
`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`: an issuer with a minority
interest reports both, with different values, and neither is part of the other in a way that would
make them additive - they are book value excluding and including non-controlling interests, and
price-to-book wants the first, which is why it is first. `debt_noncurrent`'s tags are
`LongTermLoansPayable` and `ConvertibleLongTermNotesPayable`, which COLL reports together at
2026-06-30, and there the question is whether 797,824k and 238,733k overlap or add. Priority answers
the first kind of question and quietly mis-answers the second.

Measured: applying the debt slots' refusal rule to `equity` turned 10 of 10 into 8 of 10 by refusing
the two issuers that report both equity definitions - a loss of coverage with no gain in correctness.
"""


def resolve_valuation_instant(
    facts: Iterable[CanonicalFact],
    field: str,
    decision_time: datetime,
    decision_date: date,
    *,
    specs: Mapping[str, FieldSpec] | None = None,
    period_end: date | None = None,
    max_staleness_days: int = MAX_INSTANT_STALENESS_DAYS,
) -> InstantResolution:
    """The freshness contract for a single-line instant field - `equity` and `assets` in practice.

    D5-D0 measured 10 of 10 coverage for both and no staleness bound on either, so a price-to-book
    built on them would inherit exactly the defect the debt side had. What this adds to `resolve_fact`
    is the bound and nothing else: selection stays `resolve_fact`'s, because for these fields tag
    priority is a preference between definitions rather than a tie-break between parts. See
    `TAG_PRIORITY_IS_A_PREFERENCE_FOR`.
    """
    registry = CAPITAL_STRUCTURE_FIELD_SPECS if specs is None else specs
    if field not in registry:
        raise KeyError(field)
    spec = registry[field]
    if spec.period_type != "instant":
        raise ValueError(f"{field} is not an instant field")
    if field not in TAG_PRIORITY_IS_A_PREFERENCE_FOR:
        raise ValueError(f"{field} is not a single-line instant field; a composition resolves it")
    rows = [f for f in facts if f.field == field]
    outcome = resolve_fact(rows, field, decision_time, report_end=period_end,
                           duration_family=DurationFamily.INSTANT, specs=registry)
    if outcome.status is FactStatus.AMBIGUOUS:
        return InstantResolution(field, InstantStatus.AMBIGUOUS_TAGS, outcome.reason,
                                 period_end=period_end)
    if outcome.status is not FactStatus.OK or outcome.fact is None:
        return InstantResolution(field, InstantStatus.MISSING, outcome.reason,
                                 period_end=period_end)
    fact = outcome.fact
    age = (decision_date - fact.end).days
    if age > max_staleness_days:
        return InstantResolution(field, InstantStatus.STALE,
                                 f"the newest {field} balance ends {fact.end}, {age} days before "
                                 f"the decision date, over the {max_staleness_days}-day bound",
                                 period_end=fact.end, age_days=age)
    if fact.end > decision_date:
        return InstantResolution(field, InstantStatus.MISSING,
                                 f"the newest {field} balance ends {fact.end}, after the decision "
                                 f"date {decision_date}",
                                 period_end=fact.end, age_days=age)
    return InstantResolution(field, InstantStatus.OK, "resolved and within the staleness bound",
                             fact=fact, period_end=fact.end, age_days=age, candidates=(fact,))


# -------------------------------------------------------------------------------------------------
# F/G. Debt composition
# -------------------------------------------------------------------------------------------------

def _compose_at(rows: Sequence[CanonicalFact], decision_time: datetime, decision_date: date,
                period_end: date, max_staleness_days: int) -> DebtResolution:
    """Compose total debt at one period end. Reported total first, components only when it is absent.

    The ordering is the double-counting prevention, and it is an ordering rather than a check because
    a check would need to know whether a particular filer's total includes its current portion. AEYE
    at 2026-06-30 reports `LongTermDebt` 16,418k, `LongTermDebtCurrent` 850k and
    `LongTermDebtNoncurrent` 15,568k: the total is the sum of the other two, so adding all three
    would report 32,836k, double the truth, with every component a real reported fact. The hierarchy
    is declared instead - a REPORTED_TOTAL tag subsumes the CURRENT and NONCURRENT slots - so the
    components are never reached when a total exists, and the difference is recorded as a cross-check.
    """
    slots = {slot: _resolve_slot(rows, DEBT_SLOT_FIELDS[slot], DEBT_SLOT_TAGS[slot], decision_time,
                                 decision_date, period_end, max_staleness_days)
             for slot in DebtSlot}
    age = (decision_date - period_end).days
    bundled = tuple(sorted({(f.tag, f.value) for f in rows
                            if f.field == DEBT_LEASE_BUNDLED_FIELD and f.end == period_end
                            and f.accepted_at <= decision_time}))
    if any(res.status is InstantStatus.STALE for res in slots.values()):
        return DebtResolution(DebtStatus.STALE,
                              f"the balance-sheet date {period_end} is {age} days before the "
                              f"decision date, over the {max_staleness_days}-day bound",
                              period_end=period_end, age_days=age, slots=slots)

    total = slots[DebtSlot.REPORTED_TOTAL]
    if total.status is InstantStatus.AMBIGUOUS_TAGS:
        return DebtResolution(DebtStatus.AMBIGUOUS_TAGS, f"reported total: {total.reason}",
                              period_end=period_end, age_days=age, slots=slots)
    if total.ok:
        assert total.fact is not None
        current, noncurrent = slots[DebtSlot.CURRENT], slots[DebtSlot.NONCURRENT]
        difference = (total.fact.value - (current.fact.value + noncurrent.fact.value)
                      if current.ok and noncurrent.ok else None)
        return DebtResolution(
            DebtStatus.OK,
            f"{total.fact.tag} as reported at {period_end}; the components it contains are not added",
            value=total.fact.value, unit=total.fact.unit, method=DebtMethod.REPORTED_TOTAL,
            period_end=period_end, age_days=age,
            components=((DebtSlot.REPORTED_TOTAL, total.fact),), slots=slots,
            reported_total_minus_components=difference, lease_bundled_tags_at_same_end=bundled)

    current, noncurrent = slots[DebtSlot.CURRENT], slots[DebtSlot.NONCURRENT]
    for slot, res in ((DebtSlot.CURRENT, current), (DebtSlot.NONCURRENT, noncurrent)):
        if res.status is InstantStatus.AMBIGUOUS_TAGS:
            return DebtResolution(DebtStatus.AMBIGUOUS_TAGS, f"{slot.value.lower()}: {res.reason}",
                                  period_end=period_end, age_days=age, slots=slots)
    if current.ok and noncurrent.ok:
        assert current.fact is not None and noncurrent.fact is not None
        if current.fact.unit != noncurrent.fact.unit:
            return DebtResolution(DebtStatus.AMBIGUOUS_TAGS,
                                  f"the components are reported in {current.fact.unit} and "
                                  f"{noncurrent.fact.unit}",
                                  period_end=period_end, age_days=age, slots=slots)
        return DebtResolution(
            DebtStatus.OK,
            f"{current.fact.tag} plus {noncurrent.fact.tag} at {period_end}; no tag reports a total",
            value=current.fact.value + noncurrent.fact.value, unit=current.fact.unit,
            method=DebtMethod.CURRENT_PLUS_NONCURRENT, period_end=period_end, age_days=age,
            components=((DebtSlot.CURRENT, current.fact), (DebtSlot.NONCURRENT, noncurrent.fact)),
            slots=slots, lease_bundled_tags_at_same_end=bundled)
    if current.ok or noncurrent.ok:
        present = "current" if current.ok else "noncurrent"
        absent = "noncurrent" if current.ok else "current"
        return DebtResolution(
            DebtStatus.INCOMPLETE_COMPONENTS,
            f"{present} borrowings resolve at {period_end} and {absent} borrowings do not, and no "
            f"tag reports a total; an absent component is not zero",
            period_end=period_end, age_days=age, slots=slots)
    return DebtResolution(DebtStatus.MISSING,
                          f"no borrowing balance of any slot is reported at {period_end}; this is "
                          "an absence of evidence and not evidence of no debt",
                          period_end=period_end, age_days=age, slots=slots)


def compose_total_debt(
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    decision_date: date,
    *,
    period_end: date | None = None,
    max_staleness_days: int = MAX_INSTANT_STALENESS_DAYS,
) -> DebtResolution:
    """Total borrowings at the newest balance-sheet date where a composition closes, or a refusal.

    Left unpinned, period ends are walked newest-first within the staleness bound. That search is the
    same shape as P0.1's and carries the same hazard - taking the first end that happens to work can
    reach back years - so it carries the same guard: ends older than `max_staleness_days` are not
    candidates, and when the only ends available are older than that the result is STALE and names
    the date, rather than MISSING.
    """
    fields = set(DEBT_SLOT_FIELDS.values()) | {DEBT_LEASE_BUNDLED_FIELD}
    rows = [f for f in facts if f.field in fields]
    if period_end is not None:
        return _compose_at(rows, decision_time, decision_date, period_end, max_staleness_days)
    live, stale = _instant_ends([f for f in rows if f.field != DEBT_LEASE_BUNDLED_FIELD],
                                decision_time, decision_date, max_staleness_days)
    first: DebtResolution | None = None
    for end in live:
        outcome = _compose_at(rows, decision_time, decision_date, end, max_staleness_days)
        if outcome.ok:
            return outcome
        first = first or outcome
    if first is not None:
        return first
    if stale is not None:
        age = (decision_date - stale).days
        return DebtResolution(DebtStatus.STALE,
                              f"the newest borrowing balance ends {stale}, {age} days before the "
                              f"decision date, over the {max_staleness_days}-day bound",
                              period_end=stale, age_days=age)
    return DebtResolution(DebtStatus.MISSING,
                          "no borrowing balance of any slot is known at the decision time; this is "
                          "an absence of evidence and not evidence of no debt")


# -------------------------------------------------------------------------------------------------
# J. Net debt
# -------------------------------------------------------------------------------------------------

def net_debt_from(debt: DebtResolution, cash: InstantResolution) -> NetDebtResolution:
    """Net debt = total debt - cash, code-owned, and only from one balance sheet.

    A negative result is a net cash position and is returned as a negative number: an issuer holding
    more cash than borrowings has negative net debt, and clamping it at zero would overstate its
    enterprise value.
    """
    if debt.status is not DebtStatus.OK:
        return NetDebtResolution(_DEBT_STATUS_TO_COMPOSITE.get(debt.status,
                                                              CapitalStructureStatus.UNKNOWN),
                                 f"debt is {debt.status.value}: {debt.reason}",
                                 cash=cash, debt=debt)
    if cash.status is not InstantStatus.OK:
        return NetDebtResolution(_CASH_STATUS_TO_COMPOSITE.get(cash.status,
                                                              CapitalStructureStatus.UNKNOWN),
                                 f"cash is {cash.status.value}: {cash.reason}",
                                 cash=cash, debt=debt)
    assert debt.value is not None and cash.fact is not None
    if debt.period_end != cash.period_end:
        return NetDebtResolution(
            CapitalStructureStatus.COMPONENT_DATE_MISMATCH,
            f"debt is dated {debt.period_end} and cash {cash.period_end}; a difference of two "
            "balance sheets is not a net debt",
            cash=cash, debt=debt)
    if debt.unit != cash.fact.unit:
        return NetDebtResolution(CapitalStructureStatus.UNKNOWN,
                                 f"debt is {debt.unit} and cash is {cash.fact.unit}",
                                 cash=cash, debt=debt)
    return NetDebtResolution(CapitalStructureStatus.OK,
                             f"total debt less cash, both at {debt.period_end}",
                             value=debt.value - cash.fact.value, unit=debt.unit,
                             period_end=debt.period_end, cash=cash, debt=debt)


def resolve_net_debt(
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    decision_date: date,
    *,
    max_staleness_days: int = MAX_INSTANT_STALENESS_DAYS,
) -> NetDebtResolution:
    """Net debt at the newest balance-sheet date where cash and debt BOTH resolve.

    The shared date is chosen first, for both legs at once, rather than each leg resolving to its own
    newest date and the difference being taken anyway. D5-D0 measured 3 of 10 issuers whose cash
    period end did not match their debt period end, and DORM is the live reminder that the dates are
    not interchangeable even when they are close: its balance sheet is dated 2026-06-27 and most of
    its peers' 2026-06-30.
    """
    rows = list(facts)
    relevant = [f for f in rows
                if f.field == CASH_FOR_EV_FIELD or f.field in set(DEBT_SLOT_FIELDS.values())]
    # The lease-bundled evidence field is deliberately not a candidate-date source: it must never make
    # a balance-sheet date eligible that no slot or cash tag reports on.
    live, stale = _instant_ends(relevant, decision_time, decision_date, max_staleness_days)
    first: NetDebtResolution | None = None
    for end in live:
        cash = resolve_cash_for_ev(rows, decision_time, decision_date, period_end=end,
                                   max_staleness_days=max_staleness_days)
        debt = compose_total_debt(rows, decision_time, decision_date, period_end=end,
                                  max_staleness_days=max_staleness_days)
        outcome = net_debt_from(debt, cash)
        if outcome.ok:
            return outcome
        first = first or outcome
    if first is not None:
        return first
    if stale is not None:
        age = (decision_date - stale).days
        return NetDebtResolution(CapitalStructureStatus.STALE_DEBT,
                                 f"the newest balance sheet ends {stale}, {age} days before the "
                                 f"decision date, over the {max_staleness_days}-day bound")
    return NetDebtResolution(CapitalStructureStatus.MISSING_DEBT,
                             "no balance-sheet instant is known at the decision time")


# -------------------------------------------------------------------------------------------------
# K. Enterprise value
# -------------------------------------------------------------------------------------------------

def enterprise_value(
    market_cap: MarketCapResolution,
    net_debt: NetDebtResolution,
    decision_time: datetime,
    decision_date: date,
) -> EnterpriseValueResolution:
    """EV = market cap + net debt = market cap + total debt - cash, code-owned.

    Every input must be valid. The market-cap gate runs first, because it is the input most likely to
    be UNKNOWN for a structural reason - P0 wired the share-class determination as a required
    argument with no default and this repository has no detector to supply it - and because a
    valuation that is NOT_READY should name the gate that stopped it rather than the next one down.
    """
    if not market_cap.valuation_ready:
        return EnterpriseValueResolution(
            _MARKET_CAP_STATUS_TO_COMPOSITE.get(market_cap.status, CapitalStructureStatus.UNKNOWN),
            f"market cap is {market_cap.status.value}: {market_cap.reason}",
            decision_time, decision_date, market_cap=market_cap, net_debt=net_debt)
    if not net_debt.ok:
        return EnterpriseValueResolution(
            net_debt.status, f"net debt is {net_debt.status.value}: {net_debt.reason}",
            decision_time, decision_date, market_cap=market_cap, net_debt=net_debt)
    assert market_cap.value is not None and net_debt.value is not None
    return EnterpriseValueResolution(
        CapitalStructureStatus.OK,
        f"market cap at {decision_date} plus net debt at {net_debt.period_end}",
        decision_time, decision_date, value=market_cap.value + net_debt.value,
        market_cap=market_cap, net_debt=net_debt)


def resolve_capital_structure(
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    decision_date: date,
    market_cap: MarketCapResolution,
    *,
    max_staleness_days: int = MAX_INSTANT_STALENESS_DAYS,
) -> EnterpriseValueResolution:
    """The whole instant side of a valuation in one call: cash, debt, net debt, market cap, EV.

    `market_cap` is supplied rather than computed, because it needs a price panel and a market
    calendar that this module has no business knowing about, and because P0 already owns that path.
    """
    net = resolve_net_debt(facts, decision_time, decision_date,
                           max_staleness_days=max_staleness_days)
    return enterprise_value(market_cap, net, decision_time, decision_date)


# -------------------------------------------------------------------------------------------------
# N. Method feasibility - a verdict about inputs, never a multiple
# -------------------------------------------------------------------------------------------------

NO_MULTIPLE_IS_COMPUTED_HERE = (
    "This step reports whether the inputs to a method exist, and computes no ratio. EV/Sales means "
    "'an enterprise value and a TTM revenue both resolved over compatible periods', not a number. "
    "The ratios are D5-D1's, and D5-D1 is a separate authorisation."
)

#: Which TTM field each EV-based method needs as its denominator. EBITDA is absent because it is not
#: a TTM field: it is operating income plus D&A over one identical period, which `ttm.ebitda_feasible`
#: answers and which this module asks rather than reimplements.
EV_METHOD_DENOMINATORS: Mapping[str, str] = {
    "EV/Sales": "revenue",
    "EV/EBIT": "operating_income",
    "EV/FCF": "free_cash_flow",
}


@dataclass(frozen=True)
class MethodFeasibility:
    """Whether one method's inputs exist. Three booleans, no value, and no judgement about the method."""

    method: str
    numerator_ready: bool
    denominator_ready: bool
    reason: str

    @property
    def feasible(self) -> bool:
        return self.numerator_ready and self.denominator_ready

    def to_dict(self) -> dict:
        return {"method": self.method, "numerator_ready": self.numerator_ready,
                "denominator_ready": self.denominator_ready, "feasible": self.feasible,
                "reason": self.reason}


def method_feasibility(
    ev: EnterpriseValueResolution,
    ttm: Mapping[str, TtmResult],
    *,
    equity: InstantResolution | None = None,
) -> dict[str, MethodFeasibility]:
    """Input availability for the EV-based methods, plus P/E and P/B, which need market cap alone.

    Separating numerator from denominator readiness is the point. When the numerator is blocked for
    one structural reason the denominators still differ from each other, and a single `feasible`
    boolean would report every method as blocked and hide which denominators this program has
    actually earned.
    """
    market_ready = ev.market_cap is not None and ev.market_cap.valuation_ready
    out: dict[str, MethodFeasibility] = {}
    for method, field in EV_METHOD_DENOMINATORS.items():
        leg = ttm.get(field)
        ready = leg is not None and leg.ok
        out[method] = MethodFeasibility(
            method, ev.ok, ready,
            f"enterprise_value={ev.status.value}; {field}="
            f"{'OK' if ready else (leg.status.value if leg else 'ABSENT')}")
    oi, da = ttm.get("operating_income"), ttm.get("depreciation_amortization")
    ebitda_ready = oi is not None and da is not None and ebitda_feasible(oi, da)
    out["EV/EBITDA"] = MethodFeasibility(
        "EV/EBITDA", ev.ok, ebitda_ready,
        f"enterprise_value={ev.status.value}; operating income and D&A over one identical TTM "
        f"period={'yes' if ebitda_ready else 'no'}")
    eps = ttm.get("eps_diluted")
    out["P/E"] = MethodFeasibility(
        "P/E", market_ready, eps is not None and eps.ok,
        f"market_cap={'OK' if market_ready else (ev.market_cap.status.value if ev.market_cap else 'ABSENT')}"
        f"; eps_diluted={'OK' if eps is not None and eps.ok else (eps.status.value if eps else 'ABSENT')}")
    out["P/B"] = MethodFeasibility(
        "P/B", market_ready, equity is not None and equity.ok,
        f"market_cap={'OK' if market_ready else (ev.market_cap.status.value if ev.market_cap else 'ABSENT')}"
        f"; equity={'OK' if equity is not None and equity.ok else (equity.status.value if equity else 'ABSENT')}")
    fcf = ttm.get("free_cash_flow")
    out["P/FCF"] = MethodFeasibility(
        "P/FCF", market_ready, fcf is not None and fcf.ok,
        f"market_cap={'OK' if market_ready else (ev.market_cap.status.value if ev.market_cap else 'ABSENT')}"
        f"; free_cash_flow={'OK' if fcf is not None and fcf.ok else (fcf.status.value if fcf else 'ABSENT')}")
    return out


# -------------------------------------------------------------------------------------------------
# What this step does not do, recorded for whatever comes next
# -------------------------------------------------------------------------------------------------

SHARE_CLASS_DETERMINATION_IS_STILL_ABSENT = (
    "Every enterprise value is UNKNOWN today, and the gate is the share-class determination rather "
    "than anything on the balance sheet. P0 made the determination a required argument with no "
    "default and added no detector; P1 measured whether the stored data could supply one and found "
    "that it cannot. SEC companyfacts carries no class signal at all: it excludes dimensioned facts, "
    "so a multi-class filer's per-class cover-page counts are simply absent, and the undimensioned "
    "`dei:EntityCommonStockSharesOutstanding` is a single number for all ten measured issuers. The "
    "multiple values that `us-gaap:CommonStockSharesOutstanding` carries at some period ends are "
    "split restatements - DORM 18,078,261 and 36,156,522 at 2011-12-31 is one count and twice it - "
    "not classes. The local Polygon reference-ticker store does carry a validated signal, one CIK "
    "with more than one active common-stock ticker, which identifies GOOG/GOOGL, FOX/FOXA, "
    "NWS/NWSA, LEN/LEN.B, BRK.A/BRK.B, HEI/HEI.A and UHAL/UHAL.B correctly and finds all ten "
    "measured issuers single-tickered. Two things stop P1 from wiring it: the only full snapshot is "
    "dated 2025-09-12, 381 days before the decision date and far outside this module's own staleness "
    "bound, and the signal sees listed classes only, so an unlisted second class would read as "
    "single-class - which is the permissive direction of error. Supplying the determination is "
    "therefore a step of its own, with its own freshness and completeness contract."
)

DEBT_COMPOSITION_RESIDUAL_RISK = (
    "A composition is complete only over the tags it knows. The audit's residual scan is what tests "
    "that claim: at each issuer's chosen balance-sheet date it lists every us-gaap USD instant tag "
    "whose name mentions debt, borrowings, credit lines, notes or leases and which is in no slot and "
    "nonzero. Over 22 issuers - the ten D4 contacted and the twelve frozen for D5-D1 - it returns "
    "asset-retirement obligations, revenue performance obligations, purchase obligations, supplier "
    "finance programmes and lease payment schedules, and no borrowing balance. That is evidence the "
    "slot lists are complete over this corpus, and it is not a proof about an issuer outside it, "
    "which is why the scan is part of the audit rather than a one-off."
)
