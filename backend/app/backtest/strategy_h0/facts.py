"""PIT-safe interpretation of SEC companyfacts for Strategy H0.

Companyfacts supplies fact values and accession numbers, but not a trustworthy intraday
knowledge time for each fact.  The caller must join every accession to SEC submissions metadata.
Facts without that join remain unavailable rather than falling back to ``filed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence


class FactStatus(StrEnum):
    OK = "OK"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class FieldSpec:
    tags: tuple[str, ...]
    units: tuple[str, ...]
    period_type: str
    sign: str = "reported"


FIELD_SPECS: dict[str, FieldSpec] = {
    "revenue": FieldSpec((
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues", "SalesRevenueNet", "RevenuesNetOfInterestExpense",
    ), ("USD",), "duration"),
    "gross_profit": FieldSpec(("GrossProfit",), ("USD",), "duration"),
    "operating_income": FieldSpec(("OperatingIncomeLoss",), ("USD",), "duration"),
    "net_income": FieldSpec(("NetIncomeLoss", "ProfitLoss"), ("USD",), "duration"),
    "eps_diluted": FieldSpec(("EarningsPerShareDiluted",), ("USD/shares",), "duration"),
    "operating_cash_flow": FieldSpec(
        ("NetCashProvidedByUsedInOperatingActivities",), ("USD",), "duration"
    ),
    "capex": FieldSpec((
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ), ("USD",), "duration", "cash_outflow_positive"),
    "cash": FieldSpec((
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ), ("USD",), "instant"),
    "total_debt": FieldSpec((
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebt",
    ), ("USD",), "instant"),
    "assets": FieldSpec(("Assets",), ("USD",), "instant"),
    "equity": FieldSpec((
        "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ), ("USD",), "instant"),
    "shares_outstanding": FieldSpec((
        "EntityCommonStockSharesOutstanding",
    ), ("shares",), "instant"),
}

ALLOWED_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A"})


# ---------------------------------------------------------------------------------------------
# Duration families (H-V2-D5-P0)
# ---------------------------------------------------------------------------------------------
#
# A 10-Q reports the same line item twice from one accession: once for the discrete quarter and
# once year to date.  Both rows carry the same tag, the same `end` and the same `fp`, so neither
# tag priority nor fiscal-period metadata separates them - only the span does.  Measured on the ten
# issuers D4 contacted, that collision is what made revenue, operating income, net income and
# diluted EPS resolve AMBIGUOUS 0-for-10.  A 10-K collides the same way: it carries the full year
# and the discrete fourth quarter under one `end` (1,732 such rows in the same sample).
#
# The classification below is code-owned and total.  It never guesses: a span that matches no
# window is OTHER_DURATION, and metadata that contradicts itself is UNKNOWN.  Neither is ever
# substituted for a requested family.


class DurationFamily(StrEnum):
    INSTANT = "INSTANT"
    """No start: a balance-sheet instant."""
    QUARTER = "QUARTER"
    """A discrete ~3-month period, including the fourth quarter reported inside a 10-K."""
    YTD_Q1 = "YTD_Q1"
    """Request-only. The first quarter and the first-quarter year to date are the same span of the
    same fiscal year, so no stored fact is classified YTD_Q1 - it is classified QUARTER. A YTD_Q1
    request is satisfied by that identity (`fiscal_period == "Q1"`), which is an equality rather
    than a fallback."""
    YTD_Q2 = "YTD_Q2"
    """~6 months accumulated from the fiscal year start."""
    YTD_Q3 = "YTD_Q3"
    """~9 months accumulated from the fiscal year start."""
    FY = "FY"
    """A full fiscal year."""
    OTHER_DURATION = "OTHER_DURATION"
    """Start and end are coherent, and the span matches no recognised window - a fiscal transition
    stub, a multi-year span, an odd restatement period. Classified, and deliberately unusable."""
    UNKNOWN = "UNKNOWN"
    """Cannot be classified: no end, a non-positive span, or form/fiscal-period metadata that
    contradict each other (an `fp` of Q2 on a 10-K, say)."""


REQUEST_ONLY_FAMILIES: frozenset[DurationFamily] = frozenset({DurationFamily.YTD_Q1})
"""Families `classify_duration` never returns, but `resolve_fact` accepts as a request."""

#: Day-count windows, adopted from the frozen H-PV2/H-PV3 comparable-period primitives
#: (`h_pv2.period_family`, `h_pv3.DISCRETE`/`YTD`) so the repository holds one set of duration
#: tolerances rather than several that disagree.  Nine-month is closed one day below annual so the
#: windows are strictly disjoint: a span belongs to at most one, and a span in none is
#: OTHER_DURATION rather than the nearest neighbour.
QUARTER_SPAN_DAYS = (70, 110)
SEMI_SPAN_DAYS = (160, 200)
NINE_MONTH_SPAN_DAYS = (250, 299)
ANNUAL_SPAN_DAYS = (300, 400)

#: How far apart two facts must be to be each other's prior-year comparable, and how closely their
#: own spans must agree.  Adopted verbatim from frozen `h_pv2.comparable_pair`, which is the only
#: validated prior-comparable rule in this repository: `345 <= (current.end - prior.end).days <= 385`
#: and `abs(duration_current - duration_prior) <= 15 if FY else 7`.  They live here for the same
#: reason the span windows do - P0 found three modules defining duration tolerances that disagreed,
#: and a second definition of "one year apart" would recreate exactly that drift.  `h_pv2.py` keeps
#: its inline literals and stays frozen; a test asserts the numbers still agree.
YEAR_APART_DAYS = (345, 385)
COMPARABLE_DURATION_TOLERANCE_DAYS = 7
COMPARABLE_FY_DURATION_TOLERANCE_DAYS = 15

_ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A"})
_QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})
_QUARTER_PERIODS = frozenset({"Q1", "Q2", "Q3", "Q4"})


def _within(span: int, window: tuple[int, int]) -> bool:
    return window[0] <= span <= window[1]


def classify_duration(start: date | None, end: date | None, form: str,
                      fiscal_period: str | None) -> DurationFamily:
    """Classify one fact's period from its own stored metadata. Pure and deterministic.

    `fiscal_period` is required to assert a year-to-date family, because QUARTER and FY are
    determined by the span alone while YTD_Q2/YTD_Q3 additionally assert *which* quarter the figure
    accumulates to - a claim only the fiscal-period metadata supports. Without it a six- or
    nine-month span is OTHER_DURATION, not a year to date.
    """
    if end is None:
        return DurationFamily.UNKNOWN
    if start is None:
        return DurationFamily.INSTANT
    span = (end - start).days
    if span <= 0:
        return DurationFamily.UNKNOWN

    if fiscal_period is None:
        if _within(span, QUARTER_SPAN_DAYS):
            return DurationFamily.QUARTER
        if _within(span, ANNUAL_SPAN_DAYS) and form in _ANNUAL_FORMS:
            return DurationFamily.FY
        return DurationFamily.OTHER_DURATION

    if fiscal_period == "FY":
        if form not in _ANNUAL_FORMS:
            return DurationFamily.UNKNOWN
        if _within(span, ANNUAL_SPAN_DAYS):
            return DurationFamily.FY
        # A 10-K also carries its own discrete fourth quarter under the fiscal-year `end`.
        if _within(span, QUARTER_SPAN_DAYS):
            return DurationFamily.QUARTER
        return DurationFamily.OTHER_DURATION

    if fiscal_period in _QUARTER_PERIODS:
        if form not in _QUARTERLY_FORMS:
            return DurationFamily.UNKNOWN
        if _within(span, QUARTER_SPAN_DAYS):
            return DurationFamily.QUARTER
        if fiscal_period == "Q2" and _within(span, SEMI_SPAN_DAYS):
            return DurationFamily.YTD_Q2
        if fiscal_period == "Q3" and _within(span, NINE_MONTH_SPAN_DAYS):
            return DurationFamily.YTD_Q3
        return DurationFamily.OTHER_DURATION

    return DurationFamily.UNKNOWN


@dataclass(frozen=True)
class CanonicalFact:
    field: str
    taxonomy: str
    tag: str
    unit: str
    value: float
    start: date | None
    end: date
    filed: date
    accepted_at: datetime
    accession: str
    form: str
    fiscal_year: int | None
    fiscal_period: str | None
    frame: str | None

    @property
    def period_type(self) -> str:
        return "duration" if self.start is not None else "instant"

    @property
    def duration_days(self) -> int | None:
        return None if self.start is None else (self.end - self.start).days

    @property
    def duration_family(self) -> DurationFamily:
        """Derived, not stored, so every existing `CanonicalFact(...)` construction keeps working
        and no persisted fact can carry a family that disagrees with its own start/end."""
        return classify_duration(self.start, self.end, self.form, self.fiscal_period)

    @property
    def amended(self) -> bool:
        return self.form.endswith("/A")


@dataclass(frozen=True)
class Resolution:
    status: FactStatus
    fact: CanonicalFact | None
    reason: str
    requested_duration_family: DurationFamily | None = None
    """What the caller asked for. `None` means the caller did not narrow by period."""

    @property
    def duration_family(self) -> DurationFamily | None:
        """The resolved fact's own family. `None` when nothing resolved - a status, not a period."""
        return None if self.fact is None else self.fact.duration_family


def _date(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("knowledge time must be timezone-aware")
    return value.astimezone(timezone.utc)


def extract_companyfacts(
    document: Mapping[str, Any],
    accepted_by_accession: Mapping[str, datetime],
    *,
    specs: Mapping[str, FieldSpec] | None = None,
) -> list[CanonicalFact]:
    """Flatten supported SEC facts, rejecting facts without verified acceptance time.

    `specs` defaults to `FIELD_SPECS`, so every pre-existing caller extracts exactly the fields it
    extracted before. A caller may pass a superset - the valuation layer passes
    `valuation.fundamental_fields.VALUATION_FIELD_SPECS`, which adds `depreciation_amortization` -
    and the extra rows arrive as ordinary `CanonicalFact`s carrying that field name. Widening
    `FIELD_SPECS` itself was rejected: `len(FIELD_SPECS)` is D1's `total_field_count` and its
    resolved-field count feeds the `MIN_RESOLVED_CANONICAL_FIELDS` eligibility floor and E3's
    priority, so one more canonical field would silently move historical D1/D2 outcomes - which
    D5-P0.1 forbids. One parser, two field registries.
    """
    output: list[CanonicalFact] = []
    facts = document.get("facts") or {}
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, Mapping):
            continue
        for field, spec in (FIELD_SPECS if specs is None else specs).items():
            for tag in spec.tags:
                concept = concepts.get(tag)
                if not isinstance(concept, Mapping):
                    continue
                for unit, rows in (concept.get("units") or {}).items():
                    if unit not in spec.units or not isinstance(rows, Sequence):
                        continue
                    for row in rows:
                        if not isinstance(row, Mapping):
                            continue
                        accession = str(row.get("accn") or "")
                        accepted = accepted_by_accession.get(accession)
                        form = str(row.get("form") or "")
                        if not accession or accepted is None or form not in ALLOWED_FORMS:
                            continue
                        start, end, filed = _date(row.get("start")), _date(row.get("end")), _date(row.get("filed"))
                        if end is None or filed is None or (start is None) != (spec.period_type == "instant"):
                            continue
                        try:
                            value = float(row["val"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        output.append(CanonicalFact(
                            field=field, taxonomy=str(taxonomy), tag=tag, unit=str(unit), value=value,
                            start=start, end=end, filed=filed, accepted_at=_utc(accepted),
                            accession=accession, form=form,
                            fiscal_year=int(row["fy"]) if row.get("fy") is not None else None,
                            fiscal_period=str(row["fp"]) if row.get("fp") is not None else None,
                            frame=str(row["frame"]) if row.get("frame") is not None else None,
                        ))
    return output


def _matches_requested_family(fact: CanonicalFact, requested: DurationFamily) -> bool:
    """Whether one fact satisfies a requested duration family.

    The only non-identity rule is YTD_Q1: a first quarter and a first-quarter year to date are the
    same span of the same fiscal year, so a QUARTER fact whose `fp` is Q1 *is* the YTD_Q1 figure.
    That is an equality between two names for one period, not a substitution of one period for
    another, and it is the sole case in which the stored family differs from the requested one.
    """
    if requested is DurationFamily.YTD_Q1:
        return (fact.duration_family is DurationFamily.QUARTER
                and fact.fiscal_period == "Q1")
    return fact.duration_family is requested


def resolve_fact(
    facts: Iterable[CanonicalFact],
    field: str,
    decision_time: datetime,
    *,
    report_end: date | None = None,
    duration_family: DurationFamily | None = None,
    specs: Mapping[str, FieldSpec] | None = None,
) -> Resolution:
    """Resolve one field from versions known at ``decision_time``.

    Latest report period wins, then canonical tag priority, then acceptance time and accession.
    Exact duplicate rows are collapsed. Conflicting values with an otherwise identical winning
    provenance remain ambiguous instead of being selected arbitrarily.

    ``duration_family`` narrows to one period family *before* any other narrowing, so a request for
    a discrete quarter cannot first pick a period end that only a year-to-date figure reports and
    then find nothing there. When the requested family is absent the result is MISSING: a fact of
    another family is never returned in its place, and no instant/duration mixing is possible
    because INSTANT is itself a family. Leaving it ``None`` preserves the pre-D5-P0 behaviour
    exactly - including reporting the quarter/year-to-date collision as AMBIGUOUS, which is honest
    rather than wrong - so existing H0/D1/D2/D3/D4 callers are unaffected until they opt in.

    ``specs`` defaults to ``FIELD_SPECS``; see `extract_companyfacts` for why the valuation layer
    passes a superset instead of this module widening the canonical registry.
    """
    registry = FIELD_SPECS if specs is None else specs
    if field not in registry:
        raise KeyError(field)
    if duration_family is DurationFamily.UNKNOWN:
        raise ValueError("UNKNOWN is a classification outcome, not a requestable duration family")
    cutoff = _utc(decision_time)
    spec = registry[field]
    eligible = [f for f in facts if f.field == field and f.accepted_at <= cutoff]
    if report_end is not None:
        eligible = [f for f in eligible if f.end == report_end]
    if duration_family is not None:
        eligible = [f for f in eligible if _matches_requested_family(f, duration_family)]
        if not eligible:
            return Resolution(FactStatus.MISSING, None,
                              f"no {duration_family.value} fact known by decision time",
                              duration_family)
    if not eligible:
        return Resolution(FactStatus.MISSING, None, "no fact known by decision time",
                          duration_family)
    latest_end = max(f.end for f in eligible)
    eligible = [f for f in eligible if f.end == latest_end]
    best_tag = min(spec.tags.index(f.tag) for f in eligible)
    eligible = [f for f in eligible if spec.tags.index(f.tag) == best_tag]
    latest_acceptance = max(f.accepted_at for f in eligible)
    eligible = [f for f in eligible if f.accepted_at == latest_acceptance]
    latest_accession = max(f.accession for f in eligible)
    eligible = [f for f in eligible if f.accession == latest_accession]
    unique = {(f.value, f.unit, f.start, f.end, f.frame): f for f in eligible}
    if len(unique) != 1:
        families = sorted({f.duration_family.value for f in eligible})
        detail = (f"conflicting duration families {families} share winning provenance"
                  if len(families) > 1 else "conflicting facts share winning provenance")
        return Resolution(FactStatus.AMBIGUOUS, None, detail, duration_family)
    fact = next(iter(unique.values()))
    return Resolution(FactStatus.OK, fact, "deterministic PIT resolution", duration_family)


def resolve_period_aligned(
    facts: Iterable[CanonicalFact],
    fields: Sequence[str],
    decision_time: datetime,
    duration_family: DurationFamily,
    *,
    specs: Mapping[str, FieldSpec] | None = None,
) -> tuple[dict[str, CanonicalFact] | None, str]:
    """Resolve several duration fields onto ONE shared period family and period end.

    `resolve_fact` guarantees the family of the fact it returns; it does not guarantee that two
    fields resolved independently describe the same period. They routinely do not. Measured on all
    ten D4-contacted issuers: cash-flow statements report no discrete second quarter, so a QUARTER
    request for `operating_cash_flow` resolves to the Q1 fact ending 2026-03-31 while `revenue`
    resolves to the Q2 fact ending 2026-06-30. Both are genuine quarters. Dividing one by the other,
    or subtracting capex of one period from cash flow of another, produces a number that is not a
    ratio of anything.

    So this walks period ends newest-first and returns the newest end at which EVERY requested field
    resolves OK in the requested family. It adds no selection rule of its own - each field is
    resolved by `resolve_fact` pinned to that end - and it never relaxes the end or the family to
    find a match.
    """
    rows = list(facts)
    wanted = list(fields)
    if not wanted:
        raise ValueError("at least one field is required")
    ends = sorted({f.end for f in rows
                   if f.field == wanted[0] and _matches_requested_family(f, duration_family)},
                  reverse=True)
    for end in ends:
        resolved = {name: resolve_fact(rows, name, decision_time, report_end=end,
                                       duration_family=duration_family, specs=specs)
                    for name in wanted}
        if all(r.status is FactStatus.OK and r.fact is not None for r in resolved.values()):
            return {name: r.fact for name, r in resolved.items()}, "aligned"  # type: ignore[misc]
    return None, f"no period end where every field resolves as {duration_family.value}"


def canonical_coverage(facts: Iterable[CanonicalFact], decision_time: datetime) -> dict[str, str]:
    values = list(facts)
    return {field: resolve_fact(values, field, decision_time).status.value for field in FIELD_SPECS}
