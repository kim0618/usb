"""H-V2-D5-P0.1 §13: the valuation layer's fundamental field registry, which adds D&A.

D5-D0 recorded `depreciation_amortization` as `NOT_CANONICAL`: every one of the ten issuers D4
contacted reports a unified D&A tag, and `FIELD_SPECS` had no entry, so `resolve_fact` could not see
it. This module canonicalises it - for the valuation layer only.

Why not simply widen `FIELD_SPECS`. `len(FIELD_SPECS)` is D1's `total_field_count`; the count of
fields that resolve OK out of it feeds `eligibility.MIN_RESOLVED_CANONICAL_FIELDS` and E3's research
priority, and the whole dict is written into every D2.1 package as `canonical_field_coverage`. A
thirteenth canonical field would therefore move historical D1/D2 outcomes for borderline securities
without anybody asking for it, and §21 forbids modifying historical H results. So `FIELD_SPECS`
stays exactly as it was, `extract_companyfacts`/`resolve_fact` take an optional `specs` registry,
and the valuation layer passes this superset. One parser, two registries.

The D0-time record in `d5_d0_contract.VALUATION_FIELDS` is deliberately left saying `NOT_CANONICAL`,
the same way P0 left D0's measured 0-of-10 coverage figures in place: it states what was true when
D0 measured it, and this file is the repair rather than a rewrite of the finding.
"""

from __future__ import annotations

from app.backtest.strategy_h0.facts import FIELD_SPECS, FieldSpec

#: Tags that are a *complete* D&A measure on their own, in resolution priority order.
#:
#: The order is not cosmetic, because these tags do not agree. Measured on the stored companyfacts:
#:
#:   FRPT, six months to 2026-06-30:  DepreciationDepletionAndAmortization  47,859,000
#:                                    DepreciationAndAmortization           49,954,000
#:   DORM, fiscal 2025:               DepreciationAndAmortization           33,600,000
#:                                    DepreciationAmortizationAndAccretionNet 55,732,000
#:
#: `DepreciationDepletionAndAmortization` leads because it is the us-gaap element for the cash-flow
#: statement's depreciation, depletion and amortisation line - the D&A an EBITDA add-back means.
#: `DepreciationAmortizationAndAccretionNet` is last because accretion is not amortisation: DORM's
#: 55.7m against 33.6m is the size of that difference, so this tag is a last resort rather than a
#: synonym. It is still here because for AEYE and DORM it is the only unified tag carrying current
#: periods at all (AEYE's `DepreciationAndAmortization` stops in 2018; DORM's
#: `DepreciationDepletionAndAmortization` stops in 2014), and `resolve_fact` reaches tag priority
#: only *after* narrowing to the latest period end, so a tag with no recent data cannot win.
DEPRECIATION_AMORTIZATION_TAGS: tuple[str, ...] = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
)

#: Tags that are a *component* of D&A, and are therefore never summed into it.
#:
#: Summing `Depreciation` and `AmortizationOfIntangibleAssets` would be the `total_debt` composition
#: defect in a new costume: the sum is complete only if those two are the only components, and no
#: filing states that they are. The same filers also report `AmortizationOfDeferredCharges`,
#: `AmortizationOfFinancingCosts`, `FinanceLeaseRightOfUseAssetAmortization`,
#: `OperatingLeaseRightOfUseAssetAmortizationExpense` and `CapitalizedComputerSoftwareAmortization`,
#: some of which belong in D&A and some of which do not - and a unified tag, where it exists, already
#: includes the right ones. So a filer reporting only components resolves MISSING, not a sum.
#:
#: Measured cost of that refusal: one issuer of ten. COLL's unified
#: `DepreciationDepletionAndAmortization` stops in 2020 and it now reports only `Depreciation`
#: (2,275,000 for the six months to 2026-06-30) and `AmortizationOfIntangibleAssets` (118,426,000 for
#: the same six months), alongside `AmortizationOfFinancingCostsAndDiscounts`,
#: `CostOfGoodsAndServicesSoldAmortization` and `AccretionAmortizationOfDiscountsAndPremiumsInvestments`.
#: Their sum may well be COLL's D&A; no part of the filing says so, and the one unified tag COLL did
#: once use reported 589,000 for the nine months to 2020-09-30 - depreciation-scale, not
#: depreciation-plus-amortisation-scale - so for this filer even the unified tag was narrower than its
#: name. Guessing which subset is complete is the `total_debt` defect, and this refuses instead.
DEPRECIATION_AMORTIZATION_COMPONENT_TAGS_NOT_SUMMED: tuple[str, ...] = (
    "Depreciation",
    "AmortizationOfIntangibleAssets",
    "AmortizationOfDeferredCharges",
    "AmortizationOfFinancingCosts",
    "AmortizationOfFinancingCostsAndDiscounts",
    "AmortizationOfDebtDiscountPremium",
    "FinanceLeaseRightOfUseAssetAmortization",
    "OperatingLeaseRightOfUseAssetAmortizationExpense",
    "CapitalizedComputerSoftwareAmortization",
    "CostOfGoodsAndServicesSoldAmortization",
    "CostOfGoodsSoldDepreciation",
    "CostOfGoodsSoldDepreciationAndAmortization",
    "DepreciationAndAmortizationDiscontinuedOperations",
    "OtherDepreciationAndAmortization",
)

DEPRECIATION_AMORTIZATION_SUMMATION_RULE = (
    "A unified D&A tag is used as reported and is never added to another tag. When a filer reports "
    "only separate components, D&A resolves MISSING rather than being summed, because the filing "
    "does not state that the reported components are the complete set - the same reason "
    "`total_debt` is BLOCKED_COMPOSITION rather than a sum of the debt tags it happens to carry. If "
    "a later step needs a summed D&A it must first establish component completeness from the filing "
    "itself; this step does not, so it refuses."
)

#: `FIELD_SPECS` plus D&A. The 12 canonical fields are re-exported unchanged, by reference to the
#: same `FieldSpec` objects, so the two registries cannot drift on anything they share.
VALUATION_FIELD_SPECS: dict[str, FieldSpec] = {
    **FIELD_SPECS,
    "depreciation_amortization": FieldSpec(
        DEPRECIATION_AMORTIZATION_TAGS, ("USD",), "duration"),
}

VALUATION_ONLY_FIELDS: frozenset[str] = frozenset(VALUATION_FIELD_SPECS) - frozenset(FIELD_SPECS)
"""What this registry adds. Asserted non-empty and disjoint from `FIELD_SPECS` by test."""

assert set(FIELD_SPECS) < set(VALUATION_FIELD_SPECS), "the valuation registry must be a superset"
assert all(VALUATION_FIELD_SPECS[name] is spec for name, spec in FIELD_SPECS.items()), (
    "the 12 canonical specs must be shared objects, not copies that can drift"
)
