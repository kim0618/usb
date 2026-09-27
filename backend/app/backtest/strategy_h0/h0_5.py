"""Frozen H0.5 historical-universe and PIT market-cap rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Iterable, Sequence

from app.backtest.strategy_h0.facts import CanonicalFact

MAX_SHARES_STALENESS_DAYS = 135
ELIGIBLE_EXCHANGES = frozenset({"XNYS", "XNAS", "XASE"})
SHARES_TAG = "EntityCommonStockSharesOutstanding"


class Lane(StrEnum):
    MICRO = "MICRO"
    H_SMALL = "H-SMALL"
    H_MID = "H-MID"
    H_UPPER_MID = "H-UPPER-MID"
    H_LARGE = "H-LARGE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SecurityInterval:
    security_id: str
    company_id: str
    ticker: str
    effective_from: date
    effective_to: date | None
    exchange: str
    security_type: str
    market: str
    locale: str
    known_at: datetime


@dataclass(frozen=True)
class SharesResolution:
    fact: CanonicalFact | None
    reason: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def security_at(intervals: Iterable[SecurityInterval], security_id: str, decision_date: date,
                decision_time: datetime) -> SecurityInterval | None:
    cutoff = _utc(decision_time)
    rows = [row for row in intervals if row.security_id == security_id
            and _utc(row.known_at) <= cutoff
            and row.effective_from <= decision_date
            and (row.effective_to is None or decision_date < row.effective_to)]
    if not rows:
        return None
    identities = {(r.company_id, r.ticker, r.effective_from, r.effective_to) for r in rows}
    if len(identities) != 1:
        raise ValueError(f"ambiguous security state for {security_id}")
    return rows[0]


def is_eligible_common_stock(row: SecurityInterval | None) -> bool:
    return bool(row and row.security_id and row.company_id and row.security_type == "CS"
                and row.market == "stocks" and row.locale == "us"
                and row.exchange in ELIGIBLE_EXCHANGES)


def first_available_session(accepted_at: datetime, regular_opens: Sequence[datetime]) -> date | None:
    accepted = _utc(accepted_at)
    for opening in sorted(_utc(value) for value in regular_opens):
        if accepted <= opening:
            return opening.date()
    return None


def resolve_pit_shares(facts: Iterable[CanonicalFact], decision_date: date,
                       regular_opens: Sequence[datetime], *, multiple_share_classes: bool = False,
                       split_dates: Iterable[date] = ()) -> SharesResolution:
    if multiple_share_classes:
        return SharesResolution(None, "unallocated multiple share classes")
    candidates: list[CanonicalFact] = []
    for fact in facts:
        if (fact.field != "shares_outstanding" or fact.tag != SHARES_TAG
                or fact.unit != "shares" or fact.start is not None or fact.end > decision_date):
            continue
        available = first_available_session(fact.accepted_at, regular_opens)
        if available is None or available > decision_date:
            continue
        if (decision_date - fact.end).days > MAX_SHARES_STALENESS_DAYS:
            continue
        if any(fact.end < split <= decision_date for split in split_dates):
            continue
        candidates.append(fact)
    if not candidates:
        return SharesResolution(None, "no non-stale split-consistent shares known by T")
    latest_end = max(fact.end for fact in candidates)
    candidates = [fact for fact in candidates if fact.end == latest_end]
    latest_acceptance = max(fact.accepted_at for fact in candidates)
    candidates = [fact for fact in candidates if fact.accepted_at == latest_acceptance]
    values = {fact.value for fact in candidates}
    if len(values) != 1:
        return SharesResolution(None, "ambiguous shares facts")
    return SharesResolution(max(candidates, key=lambda fact: fact.accession), "PIT shares resolved")


def historical_market_cap(unadjusted_close: float | None,
                          shares: SharesResolution) -> float | None:
    if unadjusted_close is None or shares.fact is None:
        return None
    if unadjusted_close < 0 or shares.fact.value < 0:
        raise ValueError("price and shares must be non-negative")
    return unadjusted_close * shares.fact.value


def market_cap_lane(value: float | None) -> Lane:
    if value is None:
        return Lane.UNKNOWN
    if value < 500_000_000:
        return Lane.MICRO
    if value < 2_000_000_000:
        return Lane.H_SMALL
    if value < 10_000_000_000:
        return Lane.H_MID
    if value < 50_000_000_000:
        return Lane.H_UPPER_MID
    return Lane.H_LARGE
