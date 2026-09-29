"""Instrument spec and risk tiers. Every number here came from a Bybit PUBLIC response."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class InstrumentSpec:
    """D1 measured `/v5/market/instruments-info` values for BTCUSDT linear perpetual."""
    symbol: str = "BTCUSDT"
    contract_type: str = "LinearPerpetual"
    settle_coin: str = "USDT"
    tick_size: Decimal = Decimal("0.10")
    qty_step: Decimal = Decimal("0.001")
    min_order_qty: Decimal = Decimal("0.001")
    max_order_qty: Decimal = Decimal("1500.000")
    max_mkt_order_qty: Decimal = Decimal("150.000")
    min_notional_value: Decimal = Decimal("5")
    min_leverage: Decimal = Decimal("1")
    max_leverage: Decimal = Decimal("150.00")
    leverage_step: Decimal = Decimal("0.01")
    funding_interval_minutes: int = 480
    source: str = "D1 CRYPTO_D1_PREFLIGHT_FINDINGS_V1.md section 1"


@dataclass(frozen=True)
class RiskTier:
    risk_id: int
    lower_exclusive: Decimal
    upper_inclusive: Decimal
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    max_leverage: Decimal
    mm_deduction: Decimal


class RiskTierTable:
    """Ordered risk tiers with the maintenance-margin formula D3 verified against the data.

    `MM(notional) = notional * maintenance_margin_rate - mm_deduction` is continuous across
    every one of the 34 tier boundaries (verified exactly, in Decimal). That continuity is
    what makes the deduction column meaningful, and `verify_continuity` keeps it a test.
    """

    def __init__(self, tiers: Sequence[RiskTier], *, source: str, source_sha256: str) -> None:
        if not tiers:
            raise ValueError("risk tier table is empty")
        self.tiers = tuple(tiers)
        self.source = source
        self.source_sha256 = source_sha256

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source: str, source_sha256: str) -> "RiskTierTable":
        if payload.get("retCode") != 0:
            raise ValueError(f"risk-limit payload is an error: {payload.get('retCode')}")
        rows = payload.get("result", {}).get("list")
        if not isinstance(rows, list) or not rows:
            raise ValueError("risk-limit payload has no tier list")
        tiers: list[RiskTier] = []
        lower = Decimal(0)
        for row in rows:
            upper = Decimal(row["riskLimitValue"])
            # tier 1 sends mmDeduction as an empty string; it is 0, not missing.
            deduction = Decimal(row["mmDeduction"]) if row.get("mmDeduction") else Decimal(0)
            tiers.append(RiskTier(
                risk_id=int(row["id"]), lower_exclusive=lower, upper_inclusive=upper,
                initial_margin_rate=Decimal(row["initialMargin"]),
                maintenance_margin_rate=Decimal(row["maintenanceMargin"]),
                max_leverage=Decimal(row["maxLeverage"]), mm_deduction=deduction))
            lower = upper
        return cls(tiers, source=source, source_sha256=source_sha256)

    @classmethod
    def from_file(cls, path: Path) -> "RiskTierTable":
        import hashlib
        raw = path.read_bytes()
        return cls.from_payload(json.loads(raw), source=path.as_posix(),
                                source_sha256=hashlib.sha256(raw).hexdigest())

    def tier_for_notional(self, notional: Decimal) -> RiskTier:
        """`(lower, upper]`: the boundary value belongs to the lower tier."""
        if notional < 0:
            raise ValueError("notional must not be negative")
        for tier in self.tiers:
            if notional <= tier.upper_inclusive:
                return tier
        raise ValueError(f"notional {notional} exceeds the highest risk limit {self.tiers[-1].upper_inclusive}")

    def maintenance_margin(self, notional: Decimal) -> Decimal:
        tier = self.tier_for_notional(notional)
        return notional * tier.maintenance_margin_rate - tier.mm_deduction

    def verify_continuity(self) -> list[dict[str, str]]:
        """Return the boundaries where MM is discontinuous. Empty means the formula holds."""
        broken: list[dict[str, str]] = []
        for lower, upper in zip(self.tiers, self.tiers[1:]):
            at = lower.upper_inclusive
            gap = (upper.mm_deduction - lower.mm_deduction) - at * (
                upper.maintenance_margin_rate - lower.maintenance_margin_rate)
            if gap != 0:
                broken.append({"boundary_notional": str(at), "from_tier": str(lower.risk_id),
                               "to_tier": str(upper.risk_id), "discontinuity": str(gap)})
        return broken
