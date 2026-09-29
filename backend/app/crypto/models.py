from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_string(value: Any) -> str:
    """Validate a provider number without ever routing through binary float."""
    if isinstance(value, float):
        value = str(value)
    if not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"not a decimal value: {value!r}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return "0" if parsed == 0 else str(value)


@dataclass(frozen=True)
class SeriesSpec:
    name: str
    endpoint: str
    interval_ms: int
    limit: int
    params: dict[str, str]
    timestamp_index: int | None = 0
    timestamp_field: str | None = None


SERIES: dict[str, SeriesSpec] = {
    "kline_1m": SeriesSpec("kline_1m", "/v5/market/kline", 60_000, 1000, {"category": "linear", "symbol": "BTCUSDT", "interval": "1"}),
    "funding": SeriesSpec("funding", "/v5/market/funding/history", 8 * 60 * 60_000, 200, {"category": "linear", "symbol": "BTCUSDT"}, None, "fundingRateTimestamp"),
    "open_interest_5m": SeriesSpec("open_interest_5m", "/v5/market/open-interest", 5 * 60_000, 200, {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min"}, None, "timestamp"),
    "mark_1m": SeriesSpec("mark_1m", "/v5/market/mark-price-kline", 60_000, 1000, {"category": "linear", "symbol": "BTCUSDT", "interval": "1"}),
    "index_1m": SeriesSpec("index_1m", "/v5/market/index-price-kline", 60_000, 1000, {"category": "linear", "symbol": "BTCUSDT", "interval": "1"}),
}


def row_timestamp(spec: SeriesSpec, row: Any) -> int:
    value = row[spec.timestamp_index] if spec.timestamp_index is not None else row[spec.timestamp_field]  # type: ignore[index]
    return int(value)


def normalize_row(spec: SeriesSpec, row: Any) -> dict[str, str | int]:
    ts = row_timestamp(spec, row)
    if spec.name == "kline_1m":
        keys = ("timestamp_ms", "open", "high", "low", "close", "volume", "turnover")
        values: list[str | int] = [ts, *(decimal_string(v) for v in row[1:7])]
    elif spec.name in {"mark_1m", "index_1m"}:
        keys = ("timestamp_ms", "open", "high", "low", "close")
        values = [ts, *(decimal_string(v) for v in row[1:5])]
    elif spec.name == "funding":
        keys = ("timestamp_ms", "funding_rate")
        values = [ts, decimal_string(row["fundingRate"])]
    else:
        keys = ("timestamp_ms", "open_interest")
        values = [ts, decimal_string(row["openInterest"])]
    return dict(zip(keys, values, strict=True))


@dataclass(frozen=True)
class RiskTier:
    risk_id: int
    lower_exclusive: Decimal
    upper_inclusive: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    max_leverage: Decimal
    mm_deduction: Decimal | None


def parse_risk_tiers(payload: dict[str, Any]) -> list[RiskTier]:
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit error: {payload.get('retCode')} {payload.get('retMsg')}")
    rows = payload.get("result", {}).get("list")
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing risk tier list")
    tiers: list[RiskTier] = []
    lower = Decimal("0")
    for row in rows:
        tier = RiskTier(
            risk_id=int(row["id"]), lower_exclusive=lower,
            upper_inclusive=Decimal(row["riskLimitValue"]),
            initial_margin=Decimal(row["initialMargin"]),
            maintenance_margin=Decimal(row["maintenanceMargin"]),
            max_leverage=Decimal(row["maxLeverage"]),
            mm_deduction=Decimal(row["mmDeduction"]) if row.get("mmDeduction") else None,
        )
        tiers.append(tier)
        lower = tier.upper_inclusive
    validate_risk_tiers(tiers)
    return tiers


def validate_risk_tiers(tiers: list[RiskTier]) -> None:
    for index, tier in enumerate(tiers):
        if tier.risk_id != index + 1 or tier.upper_inclusive <= tier.lower_exclusive:
            raise ValueError("risk tiers are not contiguous and ascending")
        if tier.initial_margin < tier.maintenance_margin:
            raise ValueError("initial margin is below maintenance margin")
        if index and (tier.initial_margin < tiers[index - 1].initial_margin or tier.maintenance_margin < tiers[index - 1].maintenance_margin or tier.max_leverage > tiers[index - 1].max_leverage):
            raise ValueError("risk tier margins/leverage are not monotonic")
        # Published leverage is rounded; permit the API's four-decimal IM rounding.
        if abs(tier.initial_margin * tier.max_leverage - Decimal("1")) > Decimal("0.011"):
            raise ValueError("initial margin/max leverage mismatch")
