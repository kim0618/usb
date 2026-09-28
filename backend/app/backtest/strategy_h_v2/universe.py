"""H-V2 D1 Universe Engine.

Builds the current, live candidate universe from a dated reference snapshot. This is explicitly
not the survivorship-safe historical universe H0.5 audited and left INCONCLUSIVE: D1 runs a
current research pipeline, not a historical backtest, so today's dated snapshot is a legitimate
input here even though it would not be legitimate as a historical decision-date universe
(`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md` §6/§C). This module must never be reused to
select a *historical* backtest universe.

Reuses H0.5's eligible-exchange set (`app.backtest.strategy_h0.h0_5.ELIGIBLE_EXCHANGES`) rather
than redefining it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from app.backtest.strategy_h0.h0_5 import ELIGIBLE_EXCHANGES

SCHEMA_VERSION = "h_v2_universe_v1"


class SecurityTypeStatus(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    NOT_COMMON_STOCK = "NOT_COMMON_STOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class UniverseRow:
    ticker: str
    cik: str | None
    security_id: str | None
    exchange: str | None
    exchange_supported: bool
    security_type_status: SecurityTypeStatus
    market: str | None
    locale: str | None
    snapshot_date: str
    known_at: datetime

    def __post_init__(self) -> None:
        if self.known_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")


def classify_security_type(raw_type: Any, market: Any, locale: Any) -> SecurityTypeStatus:
    """Classify a reference row's security type without guessing a missing field.

    A row that came from a snapshot already queried for `type=CS` will normally classify as
    COMMON_STOCK; a row from a broader source (or with a field the snapshot did not populate)
    must not be silently assumed common stock.
    """
    if raw_type is None or market is None or locale is None:
        return SecurityTypeStatus.UNKNOWN
    if raw_type == "CS" and market == "stocks" and locale == "us":
        return SecurityTypeStatus.COMMON_STOCK
    return SecurityTypeStatus.NOT_COMMON_STOCK


def security_id_of(row: Mapping[str, Any]) -> str | None:
    """H0.5 identity rule: share_class_figi, else composite_figi, else UNKNOWN (None here)."""
    return row.get("share_class_figi") or row.get("composite_figi") or None


def build_universe(
    reference_rows: list[Mapping[str, Any]],
    *,
    snapshot_date: str,
    known_at: datetime,
) -> list[UniverseRow]:
    """Materialize identity rows from a dated reference snapshot.

    This performs no exclusion. Eligibility (whether a row may enter AI research) is a separate
    decision made by `eligibility.evaluate_eligibility`, not by this function.
    """
    if known_at.tzinfo is None:
        raise ValueError("known_at must be timezone-aware")
    out: list[UniverseRow] = []
    for row in reference_rows:
        ticker = row.get("ticker")
        if not ticker:
            continue
        exchange = row.get("primary_exchange") or None
        out.append(
            UniverseRow(
                ticker=str(ticker),
                cik=str(row["cik"]) if row.get("cik") else None,
                security_id=security_id_of(row),
                exchange=exchange,
                exchange_supported=exchange in ELIGIBLE_EXCHANGES,
                security_type_status=classify_security_type(
                    row.get("type"), row.get("market"), row.get("locale")
                ),
                market=row.get("market"),
                locale=row.get("locale"),
                snapshot_date=snapshot_date,
                known_at=known_at.astimezone(timezone.utc),
            )
        )
    return out
