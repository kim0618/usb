"""Position size from the risk budget, and the volatility stop it is derived from.

Leverage and size are separate things here, as the contract insists. Leverage is fixed at 1x and
does not enter the size at all: it sets the liquidation distance (none, at 1x) and nothing else.
Size comes from the risk budget divided by the stop distance, so a wider stop buys a smaller
position rather than a larger loss.

SAFE MAX is a ceiling, never the size itself. D6-B never calls the live sizing probe; the caller
supplies a ceiling or supplies none.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN

from .contract import Contract
from .model import SizingResult

FEASIBLE = "FEASIBLE"
STOP_DISTANCE_UNKNOWN = "STOP_DISTANCE_UNKNOWN"
ENTRY_PRICE_UNKNOWN = "ENTRY_PRICE_UNKNOWN"
EQUITY_UNKNOWN = "EQUITY_UNKNOWN"
QTY_BELOW_MIN = "QTY_BELOW_MIN"
NOTIONAL_BELOW_MIN = "NOTIONAL_BELOW_MIN"


def _dec(value: float) -> Decimal:
    """float -> Decimal through str, the conversion the crypto feed already uses."""
    return Decimal(str(value))


def stop_distance(contract: Contract, rv24h: float) -> float | None:
    """clip(2.5 * f_rv24h * sqrt(240), 0.01, 0.10), with every constant bound to the contract."""
    if rv24h is None or not math.isfinite(rv24h) or rv24h < 0:
        return None
    stop = contract.stop
    sigma_horizon = rv24h * math.sqrt(stop["horizon_bars"])
    raw = stop["sigma_multiplier"] * sigma_horizon
    return min(max(raw, stop["dist_min"]), stop["dist_max"])


def stop_price(contract: Contract, entry_price: float, rv24h: float) -> float | None:
    distance = stop_distance(contract, rv24h)
    if distance is None or entry_price is None or not math.isfinite(entry_price):
        return None
    return float(_dec(entry_price) * (Decimal(1) - _dec(distance)))


def floor_to_step(qty: Decimal, step: Decimal) -> Decimal:
    return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step


def size(contract: Contract, *, equity: float | None, entry_price: float | None,
         rv24h: float, safe_max_qty: float | None = None) -> SizingResult:
    """Risk-budget sizing. Every intermediate is reported so a cap is visible as a cap."""
    leverage = contract.leverage
    distance = stop_distance(contract, rv24h)
    empty = SizingResult(stop_distance=float("nan"), stop_price=None, theoretical_notional=0.0,
                         risk_limited_notional=0.0, safe_max_cap_qty=safe_max_qty,
                         final_qty=0.0, final_notional=0.0, leverage=leverage,
                         feasible=False, reason=STOP_DISTANCE_UNKNOWN)
    if distance is None or distance <= 0:
        return empty
    if equity is None or not math.isfinite(equity) or equity <= 0:
        return SizingResult(**{**empty.__dict__, "stop_distance": distance,
                               "reason": EQUITY_UNKNOWN})
    if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
        return SizingResult(**{**empty.__dict__, "stop_distance": distance,
                               "reason": ENTRY_PRICE_UNKNOWN})

    risk_budget = _dec(contract.sizing["risk_budget"])
    cap_multiple = _dec(contract.sizing["notional_cap_over_equity"])
    equity_d, price_d = _dec(equity), _dec(entry_price)

    theoretical = equity_d * risk_budget / _dec(distance)
    risk_limited = min(theoretical, equity_d * cap_multiple)

    step = _dec(contract.qty_step)
    qty = floor_to_step(risk_limited / price_d, step)
    if safe_max_qty is not None:
        qty = min(qty, floor_to_step(_dec(safe_max_qty), step))
    notional = qty * price_d

    reason = FEASIBLE
    if qty < _dec(contract.min_order_qty):
        reason = QTY_BELOW_MIN
    elif notional < _dec(contract.min_notional_usdt):
        reason = NOTIONAL_BELOW_MIN

    return SizingResult(
        stop_distance=distance,
        stop_price=float(price_d * (Decimal(1) - _dec(distance))),
        theoretical_notional=float(theoretical),
        risk_limited_notional=float(risk_limited),
        safe_max_cap_qty=safe_max_qty,
        final_qty=float(qty),
        final_notional=float(notional),
        leverage=leverage,
        feasible=reason == FEASIBLE,
        reason=reason,
    )
