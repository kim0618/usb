"""Liquidation message normalization across Bybit, Binance and OKX (D5.2 schema `deriv_liquidation`).

The three venues publish different sides:
  Bybit allLiquidation  `S`      = side of the LIQUIDATED POSITION (Buy = a long was liquidated) per Bybit docs;
                                    verified empirically in D5.2 capture_v2 against mark moves.
  Binance forceOrder    `S`      = side of the liquidation ORDER (SELL closes a long -> LONG liquidated).
  OKX liquidation-orders `posSide` = liquidated position (long/short); `side` = order side.
Everything is mapped to `liquidated_side` in {LONG, SHORT}. Unknown stays None, never guessed.
OKX `sz` is in contracts (BTC-USDT-SWAP ctVal 0.01 BTC).
"""
from __future__ import annotations

import json
from typing import Any, Iterator

OKX_CT_VAL = {"BTC-USDT-SWAP": 0.01}
VENUE_SYMBOL = {"bybit": "BTCUSDT", "binance": "BTCUSDT", "okx": "BTC-USDT-SWAP"}


def _row(venue, symbol, ts, side, price, qty, raw_side, recv_ms, aggregated):
    notional = price * qty if price is not None and qty is not None else None
    return {"venue": venue, "symbol": symbol, "event_ts_ms": ts, "liquidated_side": side, "price": price,
            "qty_base": qty, "notional_quote": notional, "raw_side": raw_side, "recv_ms": recv_ms,
            "is_aggregated": aggregated}


def bybit(msg: dict, recv_ms: int | None = None) -> Iterator[dict[str, Any]]:
    if not str(msg.get("topic", "")).startswith("allLiquidation."):
        return
    for d in msg.get("data", []):
        side = {"Buy": "LONG", "Sell": "SHORT"}.get(d.get("S"))
        yield _row("bybit", d.get("s"), int(d["T"]), side, float(d["p"]), float(d["v"]), d.get("S"), recv_ms, False)


def binance(msg: dict, recv_ms: int | None = None) -> Iterator[dict[str, Any]]:
    data = msg.get("data", msg)
    if data.get("e") != "forceOrder":
        return
    o = data["o"]
    side = {"SELL": "LONG", "BUY": "SHORT"}.get(o.get("S"))
    price = float(o["ap"]) if float(o.get("ap") or 0) > 0 else float(o["p"])
    # Binance pushes at most one liquidation per symbol per second (documented snapshot): aggregated
    yield _row("binance", o.get("s"), int(o["T"]), side, price, float(o["z"] or o["q"]), o.get("S"), recv_ms, True)


def okx(msg: dict, recv_ms: int | None = None) -> Iterator[dict[str, Any]]:
    if msg.get("arg", {}).get("channel") != "liquidation-orders":
        return
    for inst in msg.get("data", []):
        iid = inst.get("instId")
        ct = OKX_CT_VAL.get(iid)
        for d in inst.get("details", []):
            side = {"long": "LONG", "short": "SHORT"}.get(d.get("posSide"))
            qty = float(d["sz"]) * ct if ct is not None else None
            yield _row("okx", iid, int(d["ts"]), side, float(d["bkPx"]), qty, d.get("posSide"), recv_ms, False)


PARSERS = {"bybit": bybit, "binance": binance, "okx": okx}


def parse_capture_line(venue: str, line: str) -> list[dict[str, Any]]:
    rec = json.loads(line)
    if "raw" not in rec or rec["raw"] in ("pong",):
        return []
    try:
        msg = json.loads(rec["raw"])
    except json.JSONDecodeError:
        return []
    return list(PARSERS[venue](msg, rec.get("recv_ms")))


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exact duplicate events (venue, symbol, ts, side, price, qty) collapse to one."""
    seen, out = set(), []
    for r in rows:
        k = (r["venue"], r["symbol"], r["event_ts_ms"], r["liquidated_side"], r["price"], r["qty_base"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out
