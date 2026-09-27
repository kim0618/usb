"""Point-in-time reference mapping and historical market-cap helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("time must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MappingRecord:
    ticker: str
    cik: str
    valid_from: datetime
    valid_to: datetime | None
    known_at: datetime


def ticker_to_cik_at(records: Iterable[MappingRecord], ticker: str, decision_time: datetime) -> str | None:
    cutoff = _utc(decision_time)
    matches = [r for r in records if r.ticker == ticker and _utc(r.known_at) <= cutoff
               and _utc(r.valid_from) <= cutoff
               and (r.valid_to is None or cutoff < _utc(r.valid_to))]
    if not matches:
        return None
    ciks = {r.cik for r in matches}
    if len(ciks) != 1:
        raise ValueError(f"ambiguous PIT mapping for {ticker}")
    return next(iter(ciks))


def market_cap_at(close: float, shares_outstanding: float, *, close_known_at: datetime,
                  shares_known_at: datetime, decision_time: datetime) -> float | None:
    cutoff = _utc(decision_time)
    if close < 0 or shares_outstanding < 0:
        raise ValueError("close and shares must be non-negative")
    if _utc(close_known_at) > cutoff or _utc(shares_known_at) > cutoff:
        return None
    return close * shares_outstanding
