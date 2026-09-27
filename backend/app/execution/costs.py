"""The one execution cost model Risk sizing and SimBroker fills both read.

An entry is sized against the price the broker will actually charge, so Risk and
the broker must not each restate the bps arithmetic: both call these functions on
the same ``ExecutionConfig``.
"""

from decimal import Decimal

from app.execution.config import ExecutionConfig, RoundTripCostConfig
from app.execution.domain import OrderSide

BPS = Decimal("10000")


def commission_bps(config: ExecutionConfig, side: OrderSide) -> Decimal:
    """The cash-charge rate one fill pays. Per leg under ``execution_v0``; under a round-trip
    contract the whole total is paid by the entry (BUY) fill and the exit pays none."""
    if isinstance(config, RoundTripCostConfig):
        return config.round_trip_cost_bps if side is OrderSide.BUY else Decimal("0")
    return config.commission_bps


def execution_price(raw: Decimal, side: OrderSide, config: ExecutionConfig) -> Decimal:
    """Per-share execution price: the raw price moved adversely by spread and slippage."""
    direction = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
    return raw + direction * raw * (config.default_spread_bps + config.default_slippage_bps) / BPS


def buy_effective_price(raw: Decimal, config: ExecutionConfig) -> Decimal:
    """Per-share cash a BUY costs: its execution price plus commission and FX cost."""
    return (execution_price(raw, OrderSide.BUY, config)
            + raw * (commission_bps(config, OrderSide.BUY) + config.fx_cost_bps) / BPS)
