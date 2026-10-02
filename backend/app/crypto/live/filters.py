"""Binance symbol filters, and the quantity rules they impose.

The paper engine has its own instrument spec, measured from Bybit. Those numbers must not be
used to size a Binance order even when they happen to agree today: the two exchanges publish
their own `stepSize` and `minNotional` and either can change one. So a LIVE order's quantity is
validated only against a live `exchangeInfo` response, and `SymbolFilters` records when that
response was read so a stale table is visible rather than silent.

The reject codes are deliberately the same vocabulary the paper engine uses
(`QTY_BELOW_MINIMUM`, `QTY_OFF_GRID`, `NOTIONAL_BELOW_MINIMUM`, `QTY_ABOVE_MARKET_MAXIMUM`), so
the terminal's existing Korean labels apply without a second translation table. The *numbers*
behind them are Binance's.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from .credentials import SUPPORTED_SYMBOL

FILTER_PRICE = "PRICE_FILTER"
FILTER_LOT_SIZE = "LOT_SIZE"
FILTER_MARKET_LOT_SIZE = "MARKET_LOT_SIZE"
FILTER_MIN_NOTIONAL = "MIN_NOTIONAL"
FILTER_NOTIONAL = "NOTIONAL"


class QuantityRejected(ValueError):
    """A size Binance would refuse. Carries the engine-style code the terminal already labels."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    status: str
    contract_type: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    price_tick: Decimal
    price_precision: int
    quantity_precision: int
    qty_step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    market_qty_step: Decimal
    market_min_qty: Decimal
    market_max_qty: Decimal
    min_notional: Decimal
    max_notional: Decimal | None
    fetched_at_ms: int

    TRADING = "TRADING"

    @property
    def tradable(self) -> bool:
        return self.status == self.TRADING

    # ------------------------------------------------------------------ parsing

    @classmethod
    def from_exchange_info(cls, payload: dict[str, Any], symbol: str = SUPPORTED_SYMBOL,
                           *, fetched_at_ms: int | None = None) -> "SymbolFilters":
        rows: Iterable[dict[str, Any]] = payload.get("symbols") or []
        row = next((item for item in rows if item.get("symbol") == symbol), None)
        if row is None:
            raise ValueError(f"exchangeInfo carries no {symbol} symbol")
        filters = {item.get("filterType"): item for item in row.get("filters") or []}

        def required(filter_type: str, key: str) -> Decimal:
            item = filters.get(filter_type)
            if item is None or item.get(key) in (None, ""):
                # A missing filter is not defaulted. Binance publishes these for BTCUSDT, and
                # inventing a step size is exactly the mistake this module exists to prevent.
                raise ValueError(f"{symbol} exchangeInfo is missing {filter_type}.{key}")
            return Decimal(str(item[key]))

        lot_step = required(FILTER_LOT_SIZE, "stepSize")
        market_step = (Decimal(str(filters[FILTER_MARKET_LOT_SIZE]["stepSize"]))
                       if FILTER_MARKET_LOT_SIZE in filters
                       and filters[FILTER_MARKET_LOT_SIZE].get("stepSize") not in (None, "", "0")
                       else lot_step)
        market_min = (Decimal(str(filters[FILTER_MARKET_LOT_SIZE]["minQty"]))
                      if FILTER_MARKET_LOT_SIZE in filters else required(FILTER_LOT_SIZE, "minQty"))
        market_max = (Decimal(str(filters[FILTER_MARKET_LOT_SIZE]["maxQty"]))
                      if FILTER_MARKET_LOT_SIZE in filters else required(FILTER_LOT_SIZE, "maxQty"))
        notional_filter = filters.get(FILTER_NOTIONAL) or filters.get(FILTER_MIN_NOTIONAL) or {}
        notional_raw = notional_filter.get(
            "minNotional", notional_filter.get("notional"))
        if notional_raw in (None, ""):
            raise ValueError(f"{symbol} exchangeInfo is missing {FILTER_MIN_NOTIONAL}")

        return cls(
            symbol=symbol,
            status=str(row.get("status") or ""),
            contract_type=str(row.get("contractType") or ""),
            base_asset=str(row.get("baseAsset") or ""),
            quote_asset=str(row.get("quoteAsset") or ""),
            margin_asset=str(row.get("marginAsset") or ""),
            price_tick=required(FILTER_PRICE, "tickSize"),
            price_precision=int(row["pricePrecision"]),
            quantity_precision=int(row["quantityPrecision"]),
            qty_step=lot_step,
            min_qty=required(FILTER_LOT_SIZE, "minQty"),
            max_qty=required(FILTER_LOT_SIZE, "maxQty"),
            market_qty_step=market_step,
            market_min_qty=market_min,
            market_max_qty=market_max,
            min_notional=Decimal(str(notional_raw)),
            max_notional=(Decimal(str(notional_filter["maxNotional"]))
                          if notional_filter.get("maxNotional") not in (None, "", "0") else None),
            fetched_at_ms=fetched_at_ms if fetched_at_ms is not None else int(time.time() * 1000),
        )

    # ------------------------------------------------------------------ quantity rules

    def quantize(self, qty: Decimal) -> Decimal:
        """Floor to the market step. Floored, never rounded: rounding up can turn a size the
        operator can afford into one they cannot."""
        step = self.market_qty_step
        if step <= 0:
            raise ValueError("market step size must be positive")
        return (qty / step).to_integral_value(rounding="ROUND_DOWN") * step

    def validate_market_qty(self, qty: Decimal, *, reference_price: Decimal) -> None:
        """Every rule a MARKET order must satisfy, in the order that gives the clearest refusal.

        `reference_price` is the price the notional is measured at - the side's best book price
        for an entry preview, so the check matches what the order will actually be worth.
        """
        if qty <= 0:
            raise QuantityRejected("QTY_NOT_POSITIVE", f"quantity must be positive: {qty}")
        if not self.tradable:
            raise QuantityRejected("SYMBOL_NOT_TRADING",
                                   f"{self.symbol} status is {self.status!r}, not {self.TRADING}")
        # Bounds before the grid: a size under the minimum is usually also off the grid, and
        # "below the minimum" is the refusal the operator can act on.
        if qty < self.market_min_qty:
            raise QuantityRejected("QTY_BELOW_MINIMUM",
                                   f"quantity {qty} is below the market minimum {self.market_min_qty}")
        if qty > self.market_max_qty:
            raise QuantityRejected("QTY_ABOVE_MARKET_MAXIMUM",
                                   f"quantity {qty} exceeds the market maximum {self.market_max_qty}")
        step = self.market_qty_step
        if (qty % step) != 0:
            raise QuantityRejected("QTY_OFF_GRID",
                                   f"quantity {qty} is not a multiple of the step {step}")
        if -qty.as_tuple().exponent > self.quantity_precision:
            raise QuantityRejected("QTY_OFF_GRID",
                                   f"quantity {qty} exceeds quantityPrecision {self.quantity_precision}")
        notional = qty * reference_price
        if notional < self.min_notional:
            raise QuantityRejected("NOTIONAL_BELOW_MINIMUM",
                                   f"notional {notional} is below the minimum {self.min_notional}")
        if self.max_notional is not None and notional > self.max_notional:
            raise QuantityRejected("NOTIONAL_ABOVE_MAXIMUM",
                                   f"notional {notional} exceeds the maximum {self.max_notional}")

    def qty_from_notional(self, notional: Decimal, *, reference_price: Decimal) -> Decimal:
        """Coins for a USDT amount, floored to the step, exactly as the paper order route floors
        its own notional. Binance's step, not the paper engine's."""
        if reference_price <= 0:
            raise QuantityRejected("NO_QUOTE", "no reference price for sizing")
        return self.quantize(notional / reference_price)

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "status": self.status, "contract_type": self.contract_type,
                "base_asset": self.base_asset, "quote_asset": self.quote_asset,
                "margin_asset": self.margin_asset,
                "price_tick": self.price_tick, "price_precision": self.price_precision,
                "quantity_precision": self.quantity_precision,
                "qty_step": self.qty_step, "min_qty": self.min_qty, "max_qty": self.max_qty,
                "market_qty_step": self.market_qty_step, "market_min_qty": self.market_min_qty,
                "market_max_qty": self.market_max_qty, "min_notional": self.min_notional,
                "max_notional": self.max_notional,
                "fetched_at_ms": self.fetched_at_ms, "source": "binance GET /fapi/v1/exchangeInfo"}
