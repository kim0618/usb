"""Event quality raw features from one filing's own XBRL facts. No text, no score, no model.

The only magnitude SEC serves as structured data for this universe is the one a periodic report
tags itself (`EQM_V0_DATA_FEASIBILITY_V1.md` §2-§3): an 8-K carries no facts at all, so a contract
or a press release has no machine-readable size. A 10-Q/10-K, on the other hand, tags the current
period **and its prior-year comparative in the same accession**, which is what lets this module
compute a year-on-year growth rate whose every input was public at that accession's acceptance
time. Facts from any other accession are never read: that rule is what makes the PIT claim
checkable rather than assumed, and it is also what stops a later 10-Q from back-filling the size
of an earlier 8-K 2.02.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Priority order. The first tag that yields a comparable pair wins, so a filer that reports both
# the ASC 606 revenue tag and the legacy one is read the same way on every date.
REVENUE_TAGS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
)
NET_INCOME_TAGS: tuple[str, ...] = ("NetIncomeLoss", "ProfitLoss")
GROSS_PROFIT_TAGS: tuple[str, ...] = ("GrossProfit",)
EPS_TAGS: tuple[str, ...] = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                             "EarningsPerShareBasic")
SHARES_TAG = "EntityCommonStockSharesOutstanding"

# Reporting periods a filer may tag, as day counts: quarter, half year, three quarters, year.
PERIOD_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (80, 100, "Q"), (170, 195, "H"), (260, 285, "3Q"), (350, 380, "Y"),
)
YOY_GAP_DAYS = (355, 375)

OK = "OK"
NO_FACTS = "NO_FACTS"
NO_REVENUE = "NO_REVENUE"
NO_COMPARATIVE = "NO_COMPARATIVE"
ZERO_BASE = "ZERO_BASE"
NEGATIVE_BASE = "NEGATIVE_BASE"


@dataclass(frozen=True)
class Fact:
    tag: str
    taxonomy: str
    unit: str
    start: date | None
    end: date | None
    value: float

    @property
    def days(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days


@dataclass(frozen=True)
class QualityFacts:
    """One accession's raw quality features. `status` says why a growth rate is absent."""

    accession: str
    status: str
    revenue_tag: str | None = None
    period_label: str | None = None
    period_days: int | None = None
    period_end: date | None = None
    revenue: float | None = None
    revenue_prior_year: float | None = None
    revenue_growth_yoy: float | None = None
    net_income: float | None = None
    net_income_prior_year: float | None = None
    gross_profit: float | None = None
    eps: float | None = None
    shares_outstanding: float | None = None
    facts_in_accession: int = 0
    tags_seen: tuple[str, ...] = field(default_factory=tuple)


def facts_of_accession(document: Mapping[str, Any], accession: str) -> list[Fact]:
    """Every fact the companyfacts document attributes to this one accession."""
    out: list[Fact] = []
    for taxonomy, tags in (document.get("facts") or {}).items():
        for tag, body in (tags or {}).items():
            for unit, rows in ((body or {}).get("units") or {}).items():
                for row in rows or ():
                    if str(row.get("accn")) != accession:
                        continue
                    value = row.get("val")
                    if value is None:
                        continue
                    out.append(Fact(tag=str(tag), taxonomy=str(taxonomy), unit=str(unit),
                                    start=_day(row.get("start")), end=_day(row.get("end")),
                                    value=float(value)))
    return out


def extract(facts: Sequence[Fact], accession: str) -> QualityFacts:
    """Raw features of one accession. Only facts carried by that accession are read."""
    if not facts:
        return QualityFacts(accession=accession, status=NO_FACTS)
    tags_seen = tuple(sorted({f.tag for f in facts}))
    base = QualityFacts(accession=accession, status=NO_REVENUE,
                        facts_in_accession=len(facts), tags_seen=tags_seen,
                        shares_outstanding=_latest_value(facts, (SHARES_TAG,), unit="shares"))
    pair = _revenue_pair(facts)
    if pair is None:
        usd_revenue = [f for f in facts if f.tag in REVENUE_TAGS and f.unit == "USD"]
        return _replace(base, status=NO_REVENUE if not usd_revenue else NO_COMPARATIVE)
    tag, label, current, prior = pair
    row = _replace(
        base, revenue_tag=tag, period_label=label, period_days=current.days,
        period_end=current.end, revenue=current.value, revenue_prior_year=prior.value,
        net_income=_period_value(facts, NET_INCOME_TAGS, current),
        net_income_prior_year=_period_value(facts, NET_INCOME_TAGS, prior),
        gross_profit=_period_value(facts, GROSS_PROFIT_TAGS, current),
        eps=_period_value(facts, EPS_TAGS, current, unit="USD/shares"),
    )
    if prior.value == 0:
        return _replace(row, status=ZERO_BASE)
    if prior.value < 0:
        return _replace(row, status=NEGATIVE_BASE)
    return _replace(row, status=OK, revenue_growth_yoy=(current.value / prior.value) - 1.0)


def _revenue_pair(facts: Sequence[Fact]) -> tuple[str, str, Fact, Fact] | None:
    """(tag, period label, current, prior-year) for the newest period with a comparative."""
    for tag in REVENUE_TAGS:
        candidates = [f for f in facts if f.tag == tag and f.unit == "USD" and f.days is not None]
        if not candidates:
            continue
        for low, high, label in PERIOD_BUCKETS:
            in_bucket = [f for f in candidates if low <= (f.days or -1) <= high]
            if not in_bucket:
                continue
            current = max(in_bucket, key=lambda f: (f.end or date.min, f.value))
            prior = _prior_year(in_bucket, current)
            if prior is not None:
                return tag, label, current, prior
    return None


def _prior_year(facts: Iterable[Fact], current: Fact) -> Fact | None:
    low, high = YOY_GAP_DAYS
    matches = [f for f in facts if f.end is not None and current.end is not None
               and low <= (current.end - f.end).days <= high]
    return max(matches, key=lambda f: f.end or date.min) if matches else None


def _period_value(facts: Sequence[Fact], tags: Sequence[str], period: Fact,
                  *, unit: str = "USD") -> float | None:
    """A same-period value under the first tag that has one, so periods never get mixed."""
    for tag in tags:
        for fact in facts:
            if (fact.tag == tag and fact.unit == unit and fact.end == period.end
                    and fact.start == period.start):
                return fact.value
    return None


def _latest_value(facts: Sequence[Fact], tags: Sequence[str], *, unit: str) -> float | None:
    for tag in tags:
        matches = [f for f in facts if f.tag == tag and f.unit == unit]
        if matches:
            return max(matches, key=lambda f: (f.end or date.min)).value
    return None


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _replace(row: QualityFacts, **changes: Any) -> QualityFacts:
    from dataclasses import replace
    return replace(row, **changes)
