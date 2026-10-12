"""What the BTC order screen is shown, distilled from layers that already exist. Pure functions.

Each section reduces one source layer to a handful of short figures plus one boolean, `live`.
The rules, all of which are restatements rather than new judgements:

* A value is published only while its source layer reads LIVE or PARTIAL. STALE, UNKNOWN and
  UNAVAILABLE publish `None` (the screen shows "--"): an hour-old flow window next to an order
  ticket is worse than a dash. `live` is true only for LIVE, and is all the state the screen gets.
* STRUCTURE is the R1 book's nearest resistance and support: their price, and their distance as
  an absolute percentage.
* LIQUIDITY is the collector's own wall state per side: OK (a wall that passes the existing V2
  wall rule is resting on that side) reads STRONG, NONE reads NORMAL. Nothing else is a value.
  While a side reads OK its wall price is the collector's own `nearest_wall` on that side - the
  wall it already picked, not a second selection made here.
* FLOW is the collector's 60 s aggressive buy and sell notional, as a share of their sum.
* DERIVATIVES are the venue's figures: open interest value, the sign of its 1 h change on closed
  5 m buckets, the last funding rate and the mark/index premium.
* WALLS (V3) are the collector's own published walls per side - its display set, at most
  `DISPLAY_WALL_LIMIT` per side, nearest first - with the figures it already carries: price,
  notional, multiple, observed persistence and continuity. Published only while that side reads
  OK, exactly as the nearest wall above. `size_rank` is the order of the published notionals
  within the side; it is a sort, not a judgement. Recent ends are the collector's own classified
  disappearances (`flow.vanished.recent`), restated.
* ENTRY (V3) is the Manual Trade Snapshot Recorder's EVENT snapshot of the open episode's first
  fill (`entry_context`), restated in the units the current sections use: funding as a percent,
  basis as a percent, the 60 s flow as shares. A layer the recorder marked other than OK is `None`.

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
#: The one wall state under which a side has a wall to place on the chart.
WALL_OK = "OK"
#: Prices are BTCUSDT's: tick 0.10.
PRICE_PLACES = 1
#: The collector's classified wall ends the screen is handed, newest first.
RECENT_ENDS_LIMIT = 6

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
    out: dict[str, Any] = {"live": state == LIVE, "resistance_pct": None, "support_pct": None,
                           "resistance_price": None, "support_price": None}
    if state not in SHOWN:
        return out
    levels = _dict(layer.get("levels"))
    for side in ("resistance", "support"):
        level = _dict(levels.get(side))
        available = level.get("available", True)
        distance = _dec(level.get("distance_pct")) if available else None
        price = _dec(level.get("price")) if available else None
        out[f"{side}_pct"] = _fixed(None if distance is None else abs(distance), 2)
        out[f"{side}_price"] = _fixed(price if price is not None and price > 0 else None,
                                      PRICE_PLACES)
    return out


def liquidity_section(layer: dict[str, Any] | None) -> dict[str, Any]:
    layer = _dict(layer)
    state = layer.get("state")
    out: dict[str, Any] = {"live": state == LIVE, "ask": None, "bid": None,
                           "ask_wall_price": None, "bid_wall_price": None}
    if state not in SHOWN:
        return out
    sides = _dict(layer.get("sides"))
    for key, side in (("ask", "ASK"), ("bid", "BID")):
        held = _dict(sides.get(side))
        out[key] = WALL_WORDS.get(held.get("wall_state"))
        if held.get("wall_state") == WALL_OK:
            price = _dec(_dict(held.get("nearest_wall")).get("price"))
            out[f"{key}_wall_price"] = _fixed(price if price is not None and price > 0 else None,
                                              PRICE_PLACES)
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


def _price(value: Any) -> str | None:
    price = _dec(value)
    return _fixed(price if price is not None and price > 0 else None, PRICE_PLACES)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _wall_row(wall: dict[str, Any], rank: int) -> dict[str, Any] | None:
    price = _price(wall.get("price"))
    if price is None:
        return None
    notional = _dec(wall.get("notional_usdt"))
    multiple = _dec(wall.get("multiple"))
    return {"rank": rank, "price": price,
            "notional_usdt": _fixed(notional, 0),
            "multiple": _fixed(multiple, 1),
            "age_ms": _int(wall.get("persistence_ms")),
            "own_age_ms": _int(wall.get("own_persistence_ms")),
            "continuity": wall.get("continuity_status"),
            "first_seen_ms": _int(wall.get("first_seen_ms"))}


def walls_section(liquidity_layer: dict[str, Any] | None,
                  flow_layer: dict[str, Any] | None) -> dict[str, Any]:
    layer = _dict(liquidity_layer)
    state = layer.get("state")
    out: dict[str, Any] = {"live": state == LIVE, "mid": None, "ask": [], "bid": [],
                           "recent_ends": []}
    if state not in SHOWN:
        return out
    out["mid"] = _price(_dict(layer.get("book")).get("mid"))
    sides = _dict(layer.get("sides"))
    for key, side in (("ask", "ASK"), ("bid", "BID")):
        held = _dict(sides.get(side))
        if held.get("wall_state") != WALL_OK:
            continue
        rows = [row for row in (_wall_row(_dict(w), i + 1)
                                for i, w in enumerate(held.get("walls") or [])) if row]
        by_size = sorted(rows, key=lambda r: (-(_dec(r["notional_usdt"]) or Decimal(0)), r["rank"]))
        for position, row in enumerate(by_size):
            row["size_rank"] = position + 1
        out[key] = rows
    vanished = _dict(_dict(flow_layer).get("vanished"))
    ends = []
    for item in (vanished.get("recent") or [])[:RECENT_ENDS_LIMIT]:
        item = _dict(item)
        price = _price(item.get("price"))
        if price is None or item.get("side") not in ("ASK", "BID"):
            continue
        touched = item.get("mid_path_touched_bin")
        # The fact the collector classified on, not its class name: whether the price path
        # reached the wall's bin before it went (the screen must not say "consumed").
        ends.append({"side": item.get("side"), "price": price,
                     "price_reached": touched if isinstance(touched, bool) else None,
                     "notional_usdt": _fixed(_dec(item.get("notional_usdt")), 0),
                     "observed_at_ms": _int(item.get("observed_at_ms"))})
    out["recent_ends"] = ends
    return out


def _share(buy: Any, sell: Any) -> tuple[int | None, int | None]:
    b, s = _dec(buy), _dec(sell)
    if b is None or s is None or b < 0 or s < 0 or b + s == 0:
        return None, None
    buy_pct = int((b / (b + s) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return buy_pct, 100 - buy_pct


def entry_section(entry: dict[str, Any] | None) -> dict[str, Any]:
    """The recorder's entry snapshot, restated. Not available is `{"available": False}` only: the
    reason stays in the API process (`entry_context`), the screen shows dashes either way."""
    entry = _dict(entry)
    if not entry.get("available"):
        return {"available": False}
    event, snap = _dict(entry.get("event")), _dict(entry.get("snapshot"))
    status = _dict(snap.get("layer_status"))
    market_ok = _dict(status.get("market")).get("status") in ("OK", "PARTIAL")
    collector_ok = _dict(status.get("collector")).get("status") == "OK"
    market = _dict(snap.get("market")) if market_ok else {}
    collector = _dict(snap.get("collector")) if collector_ok else {}
    window = _dict(_dict(collector.get("flow")).get(FLOW_WINDOW))
    buy_pct, sell_pct = (_share(window.get("buy_usdt"), window.get("sell_usdt"))
                         if window.get("state") in SHOWN else (None, None))
    funding = _dec(market.get("funding_rate_last"))
    basis = _dec(market.get("basis_bp"))
    walls = _dict(collector.get("liquidity"))

    def wall(side: str) -> str | None:
        held = _dict(walls.get(side))
        return _price(held.get("price")) if held.get("wall_state") == WALL_OK else None

    return {
        "available": True,
        "episode_id": event.get("episode_id"),
        # +1 long, -1 short: the sign the screen checks against the account's own position.
        "direction_sign": {"LONG": 1, "SHORT": -1}.get(str(event.get("direction"))),
        "entry_ms": _int(event.get("first_fill_ms")),
        "entry_price": _price(event.get("vwap")),
        "market_recorded": market_ok,
        "collector_recorded": collector_ok,
        "flow": {"window": FLOW_WINDOW, "buy_pct": buy_pct, "sell_pct": sell_pct},
        "oi_value_usdt": _fixed(_dec(market.get("oi_last_value_usdt")), 0),
        "oi_change_pct": _fixed(_dec(market.get("oi_change_1h_pct")), 2),
        "funding_pct": _fixed(None if funding is None else funding * 100, 4),
        "basis_pct": _fixed(None if basis is None else basis / 100, 3),
        "ask_wall_price": wall("ASK"),
        "bid_wall_price": wall("BID"),
        "mid": _price(_dict(collector.get("book")).get("mid")),
    }


def display(*, collector_view: dict[str, Any] | None, structure_layer: dict[str, Any] | None,
            derivatives_layer: dict[str, Any] | None, now_ms: int,
            entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """The whole payload the screen polls. Nothing else is served."""
    collector_view = _dict(collector_view)
    return {
        "symbol": SYMBOL,
        "server_time_ms": now_ms,
        "structure": structure_section(structure_layer),
        "liquidity": liquidity_section(collector_view.get("liquidity")),
        "flow": flow_section(collector_view.get("flow")),
        "derivatives": derivatives_section(derivatives_layer),
        "walls": walls_section(collector_view.get("liquidity"), collector_view.get("flow")),
        "entry": entry_section(entry),
    }


__all__ = ["display", "structure_section", "liquidity_section", "flow_section",
           "derivatives_section", "walls_section", "entry_section", "SYMBOL", "FLOW_WINDOW", "OI_WINDOW", "WALL_WORDS"]
