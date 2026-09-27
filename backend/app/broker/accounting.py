"""The one PnL contract a SimBroker trade is accounted by.

SimBroker, the durable services that persist its trades, and every replay read their
money through these functions, so a cost cannot be charged in one place and charged
again in another.

Cost classification (``execution_v0``)
--------------------------------------
============  ==============  ==========================================================
spread        PRICE_EMBEDDED  ``execution_price`` moves ``SimFill.fill_price`` by it
slippage      PRICE_EMBEDDED  the same; both are inside every notional, gross PnL and cash
commission    CASH_SEPARATE   charged to cash beside the notional; subtracted exactly once
fx_cost       CASH_SEPARATE   the same
============  ==============  ==========================================================

``SimFill.spread_cost``, ``SimFill.slippage_cost``, ``SimFill.total_cost`` and
``TradeResult.total_cost`` are ANALYTICS_ONLY: what execution cost, in money, for
reporting. They are never subtracted from a PnL, because the price-embedded part of them
already has been.

Contract
--------
- ``gross_pnl``: exit notional minus entry notional, both at execution (fill) prices.
  While a trade is open it is the sold quantity's ``(fill_price - average_price) * qty``.
- ``cash_charges``: every CASH_SEPARATE cost of every fill of the lifecycle.
- ``net_pnl = gross_pnl - cash_charges``. For a closed trade this is the cash the account
  holds after the close minus the cash it held before the entry.
- ``gross_r = gross_pnl / planned_initial_risk``; ``net_r = net_pnl / planned_initial_risk``.
- An open trade's ``net_pnl`` is realised only: its paid charges and its sold quantity's
  gross. The held quantity's ``unrealized_pnl`` is the mark against the average execution
  price, so ``equity - starting cash = sum(net_pnl) + sum(unrealized_pnl)`` over every
  lifecycle, and ``cash - starting cash = sum(net_pnl)`` once nothing is held.
"""

from decimal import Decimal

from app.broker.domain import SimFill
from app.execution.domain import OrderSide

#: The largest disagreement a reconciliation of Decimal money accepts. Quantities are
#: unrounded quotients, so cash summed fill by fill and a PnL summed by lifecycle can round
#: apart in the 28th significant digit; a cost charged twice or dropped is cents at least.
RECONCILIATION_TOLERANCE = Decimal("0.000001")


def fill_cash_charges(fill: SimFill) -> Decimal:
    """The CASH_SEPARATE costs one fill paid beside its notional."""
    return fill.commission + fill.fx_cost


def fill_cash_flow(fill: SimFill) -> Decimal:
    """The signed cash one fill moved: a BUY pays notional and charges, a SELL receives
    its notional less charges. This is the amount SimBroker adds to its cash."""
    notional = fill.fill_price * fill.quantity
    if fill.side is OrderSide.BUY:
        return -(notional + fill.commission + fill.fx_cost)
    return notional - fill.commission - fill.fx_cost


def net_pnl(gross_pnl: Decimal, cash_charges: Decimal) -> Decimal:
    """Gross PnL at execution prices less the cash charges; nothing price-embedded again."""
    return gross_pnl - cash_charges


def unrealized_pnl(quantity: Decimal, average_price: Decimal, mark: Decimal) -> Decimal:
    """The held quantity's PnL against the average execution price it was bought at."""
    return (mark - average_price) * quantity


def reconciles(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= RECONCILIATION_TOLERANCE
