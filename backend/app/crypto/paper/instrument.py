"""Instrument spec and risk tiers. Every number here came from a Bybit PUBLIC response."""
from __future__ import annotations

import hashlib
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

    @classmethod
    def from_instruments_info(cls, payload: dict[str, Any], symbol: str,
                              *, source: str) -> "InstrumentSpec":
        """Build a spec out of a stored `/v5/market/instruments-info` response.

        The defaults above are BTCUSDT's, measured once and written into this file. A second and
        third instrument could not be added the same way: ETHUSDT's qty step is 0.01 and
        SOLUSDT's is 0.1, their tick sizes differ by two orders of magnitude, and a *plausible*
        number in any one of those fields is an order the exchange refuses or, worse, one it
        accepts at a size nobody intended. So the other symbols are parsed from a saved raw
        response whose sha256 is recorded with the run, and every field below is required - a
        response missing one raises rather than falling back to BTC's value, because inheriting
        BTC's 0.001 step on SOL is exactly the failure this rule prevents.
        """
        rows = payload.get("result", {}).get("list") or []
        row = next((item for item in rows if item.get("symbol") == symbol), None)
        if row is None:
            raise ValueError(f"instruments-info carries no {symbol} row")
        lot = row.get("lotSizeFilter") or {}
        price = row.get("priceFilter") or {}
        leverage = row.get("leverageFilter") or {}

        def need(holder: dict[str, Any], key: str, where: str) -> Decimal:
            value = holder.get(key)
            if value in (None, ""):
                raise ValueError(f"{symbol} instruments-info is missing {where}.{key}")
            return Decimal(str(value))

        interval = row.get("fundingInterval")
        if interval in (None, ""):
            raise ValueError(f"{symbol} instruments-info is missing fundingInterval")
        return cls(
            symbol=symbol,
            contract_type=str(row.get("contractType") or ""),
            settle_coin=str(row.get("settleCoin") or ""),
            tick_size=need(price, "tickSize", "priceFilter"),
            qty_step=need(lot, "qtyStep", "lotSizeFilter"),
            min_order_qty=need(lot, "minOrderQty", "lotSizeFilter"),
            max_order_qty=need(lot, "maxOrderQty", "lotSizeFilter"),
            max_mkt_order_qty=need(lot, "maxMktOrderQty", "lotSizeFilter"),
            min_notional_value=need(lot, "minNotionalValue", "lotSizeFilter"),
            min_leverage=need(leverage, "minLeverage", "leverageFilter"),
            max_leverage=need(leverage, "maxLeverage", "leverageFilter"),
            leverage_step=need(leverage, "leverageStep", "leverageFilter"),
            funding_interval_minutes=int(interval),
            source=source,
        )

    @classmethod
    def from_file(cls, path: Path, symbol: str) -> "InstrumentSpec":
        """Parse a saved response and record which file, and which bytes, it came from."""
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        return cls.from_instruments_info(
            json.loads(raw), symbol,
            source=f"bybit GET /v5/market/instruments-info {path} sha256 {digest}")


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
