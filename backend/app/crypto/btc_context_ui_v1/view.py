"""What the BTC order screen is shown, distilled from layers that already exist. Pure functions.

Each section reduces one source layer to a handful of short figures plus one boolean, `live`.
The rules, all of which are restatements rather than new judgements:

* A value is published only while its source layer reads LIVE or PARTIAL. STALE, UNKNOWN and
  UNAVAILABLE publish `None` (the screen shows "--"): an hour-old flow window next to an order
  ticket is worse than a dash. `live` is true only for LIVE, and is all the state the screen gets.
* STRUCTURE is the R1 book's nearest resistance and support distance, as absolute percentages.
* LIQUIDITY is the collector's own wall state per side: OK (a wall that passes the existing V2
  wall rule is resting on that side) reads STRONG, NONE reads NORMAL. Nothing else is a value.
* FLOW is the collector's 60 s aggressive buy and sell notional, as a share of their sum.
* DERIVATIVES are the venue's figures: open interest value, the sign of its 1 h change on closed
  5 m buckets, the last funding rate and the mark/index premium.

There is no threshold in this module. The only arithmetic is a share of a sum, an absolute value,
a sign and rounding for display.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

SYMBOL = "BTCUSDT"

LIVE = "LIVE"
PARTIAL = "PARTIAL"
#: Source states whose values may be shown. Everything else publishes `None`.
SHOWN = (LIVE, PARTIAL)

#: The collector's flow window the screen shows. One of the windows it already publishes.
FLOW_WINDOW = "60s"
#: The open interest change window the direction is read from. One of the windows Market
#: Context V1 already publishes, differenced on the venue's closed 5 m buckets.
OI_WINDOW = "1h"

#: Collector wall states, and the words the screen uses for them.
WALL_WORDS = {"OK": "STRONG", "NONE": "NORMAL"}

UP, DOWN, FLAT = "UP", "DOWN", "FLAT"


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _fixed(value: Decimal | None, places: int) -> str | None:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    out = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if out == 0:
        out = abs(out)          # never "-0.00"
    return format(out, "f")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- sections

def structure_section(layer: dict[str, Any] | None) -> dict[str, Any]:
    layer = _dict(layer)
    state = layer.get("state")
    out: dict[str, Any] = {"live": state == LIVE, "resistance_pct": None, "support_pct": None}
    if state not in SHOWN:
        return out
    levels = _dict(layer.get("levels"))
    for key, side in (("resistance_pct", "resistance"), ("support_pct", "support")):
        level = _dict(levels.get(side))
        distance = _dec(level.get("distance_pct")) if level.get("available", True) else None
        out[key] = _fixed(None if distance is None else abs(distance), 2)
    return out


def liquidity_section(layer: dict[str, Any] | None) -> dict[str, Any]:
    layer = _dict(layer)
    state = layer.get("state")
    out: dict[str, Any] = {"live": state == LIVE, "ask": None, "bid": None}
    if state not in SHOWN:
        return out
    sides = _dict(layer.get("sides"))
    for key, side in (("ask", "ASK"), ("bid", "BID")):
        out[key] = WALL_WORDS.get(_dict(sides.get(side)).get("wall_state"))
    return out


def flow_section(layer: dict[str, Any] | None) -> dict[str, Any]:
    layer = _dict(layer)
    window = _dict(_dict(layer.get("windows")).get(FLOW_WINDOW))
    out: dict[str, Any] = {"live": layer.get("state") == LIVE and window.get("state") == LIVE,
                           "window": FLOW_WINDOW, "buy_pct": None, "sell_pct": None}
    if layer.get("state") not in SHOWN or window.get("state") not in SHOWN:
        return out
    buy, sell = _dec(window.get("buy_usdt")), _dec(window.get("sell_usdt"))
    if buy is None or sell is None or buy < 0 or sell < 0 or buy + sell == 0:
        return out
    buy_pct = int((buy / (buy + sell) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    out["buy_pct"] = buy_pct
    out["sell_pct"] = 100 - buy_pct
    return out


def derivatives_section(layer: dict[str, Any] | None) -> dict[str, Any]:
    layer = _dict(layer)
    state = layer.get("state")
    out: dict[str, Any] = {"live": state == LIVE, "oi_direction": None, "oi_change_pct": None,
                           "oi_window": OI_WINDOW, "oi_value_usdt": None, "funding_pct": None,
                           "basis_pct": None}
    if state not in SHOWN:
        return out
    change = _dict(layer.get("open_interest_change"))
    if change.get("available"):
        window = _dict(_dict(change.get("windows")).get(OI_WINDOW))
        pct = _dec(window.get("base_pct")) if window.get("available") else None
        if pct is not None:
            out["oi_direction"] = UP if pct > 0 else DOWN if pct < 0 else FLAT
            out["oi_change_pct"] = _fixed(pct, 2)
        out["oi_value_usdt"] = _fixed(_dec(change.get("newest_sum_open_interest_value")), 0)
    funding = _dict(layer.get("funding"))
    if funding.get("available"):
        out["funding_pct"] = _fixed(_dec(funding.get("last_rate_pct")), 4)
    basis = _dict(layer.get("basis"))
    if basis.get("available"):
        out["basis_pct"] = _fixed(_dec(basis.get("premium_pct")), 3)
    return out


def display(*, collector_view: dict[str, Any] | None, structure_layer: dict[str, Any] | None,
            derivatives_layer: dict[str, Any] | None, now_ms: int) -> dict[str, Any]:
    """The whole payload the screen polls. Nothing else is served."""
    collector_view = _dict(collector_view)
    return {
        "symbol": SYMBOL,
        "server_time_ms": now_ms,
        "structure": structure_section(structure_layer),
        "liquidity": liquidity_section(collector_view.get("liquidity")),
        "flow": flow_section(collector_view.get("flow")),
        "derivatives": derivatives_section(derivatives_layer),
    }


__all__ = ["display", "structure_section", "liquidity_section", "flow_section",
           "derivatives_section", "SYMBOL", "FLOW_WINDOW", "OI_WINDOW", "WALL_WORDS"]
