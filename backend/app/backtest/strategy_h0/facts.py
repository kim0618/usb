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
        "EntityCommonStockSharesOutstanding", "CommonStocksIncludingAdditionalPaidInCapitalMember",
    ), ("shares",), "instant"),
}

ALLOWED_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A"})


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
    def amended(self) -> bool:
        return self.form.endswith("/A")


@dataclass(frozen=True)
class Resolution:
    status: FactStatus
    fact: CanonicalFact | None
    reason: str


def _date(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("knowledge time must be timezone-aware")
    return value.astimezone(timezone.utc)


def extract_companyfacts(
    document: Mapping[str, Any],
    accepted_by_accession: Mapping[str, datetime],
) -> list[CanonicalFact]:
    """Flatten supported SEC facts, rejecting facts without verified acceptance time."""
    output: list[CanonicalFact] = []
    facts = document.get("facts") or {}
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, Mapping):
            continue
        for field, spec in FIELD_SPECS.items():
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


def resolve_fact(
    facts: Iterable[CanonicalFact],
    field: str,
    decision_time: datetime,
    *,
    report_end: date | None = None,
) -> Resolution:
    """Resolve one field from versions known at ``decision_time``.

    Latest report period wins, then canonical tag priority, then acceptance time and accession.
    Exact duplicate rows are collapsed. Conflicting values with an otherwise identical winning
    provenance remain ambiguous instead of being selected arbitrarily.
    """
    if field not in FIELD_SPECS:
        raise KeyError(field)
    cutoff = _utc(decision_time)
    spec = FIELD_SPECS[field]
    eligible = [f for f in facts if f.field == field and f.accepted_at <= cutoff]
    if report_end is not None:
        eligible = [f for f in eligible if f.end == report_end]
    if not eligible:
        return Resolution(FactStatus.MISSING, None, "no fact known by decision time")
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
        return Resolution(FactStatus.AMBIGUOUS, None, "conflicting facts share winning provenance")
    fact = next(iter(unique.values()))
    return Resolution(FactStatus.OK, fact, "deterministic PIT resolution")


def canonical_coverage(facts: Iterable[CanonicalFact], decision_time: datetime) -> dict[str, str]:
    values = list(facts)
    return {field: resolve_fact(values, field, decision_time).status.value for field in FIELD_SPECS}
