"""Exchange message -> liq.v1 rows (contract sections 3-5). Pure functions, no I/O."""
from __future__ import annotations

from typing import Any

from . import COLLECTOR_VERSION, SCHEMA_VERSION

SYMBOL = "BTCUSDT"
BYBIT_SYMBOL = "BTCUSDT"
BINANCE_SYMBOL = "BTCUSDT"
OKX_INST = "BTC-USDT-SWAP"


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _row(*, exchange, source, event_ts, receive_ts, raw_symbol, side, side_rule, raw_side, price, price_kind,
         qty, key, is_aggregated, is_partial=None, bankruptcy_price=None) -> dict[str, Any]:
    notional = price * qty if price is not None and qty is not None else None
    return {"event_ts_ms": event_ts, "receive_ts_ms": receive_ts, "exchange": exchange, "symbol": SYMBOL,
            "raw_symbol": raw_symbol, "instrument_type": "linear_perpetual", "liquidated_side": side,
            "side_rule": side_rule, "raw_side": raw_side, "price": price, "price_kind": price_kind, "qty": qty,
            "notional_usd": notional, "source": source, "source_event_id": None, "dedupe_key": key,
            "is_aggregated": is_aggregated, "is_partial": is_partial, "bankruptcy_price": bankruptcy_price,
            "mark_price": None, "index_price": None, "collector_version": COLLECTOR_VERSION,
            "schema_version": SCHEMA_VERSION}


def bybit(msg: dict, receive_ts: int) -> list[dict]:
    """allLiquidation.BTCUSDT. S = position side: Buy -> a LONG was liquidated (official doc)."""
    if msg.get("topic") != f"allLiquidation.{BYBIT_SYMBOL}":
        return []
    out = []
    for d in msg.get("data") or []:
        if d.get("s") != BYBIT_SYMBOL:
            continue
        s = d.get("S")
        side = {"Buy": "LONG", "Sell": "SHORT"}.get(s)
        p, v = _f(d.get("p")), _f(d.get("v"))
        out.append(_row(exchange="bybit", source="bybit_ws", event_ts=int(d["T"]), receive_ts=receive_ts,
                        raw_symbol=d.get("s"), side=side, side_rule="bybit_S_position_side" if side else "unknown",
                        raw_side=s, price=p, price_kind="bankruptcy", qty=v,
                        key=f"{d.get('T')}|{s}|{d.get('p')}|{d.get('v')}", is_aggregated=False, bankruptcy_price=p))
    return out


def binance(msg: dict, receive_ts: int) -> list[dict]:
    """forceOrder: S is the liquidation ORDER side; SELL closes a long -> LONG liquidated. Aggregated stream."""
    data = msg.get("data", msg)
    if not isinstance(data, dict) or data.get("e") != "forceOrder":
        return []
    o = data.get("o") or {}
    if o.get("s") != BINANCE_SYMBOL:
        return []
    s = o.get("S")
    side = {"SELL": "LONG", "BUY": "SHORT"}.get(s)
    ap = _f(o.get("ap"))
    price, kind = (ap, "avg_fill") if ap and ap > 0 else (_f(o.get("p")), "order_price")
    return [_row(exchange="binance", source="binance_ws", event_ts=int(o["T"]), receive_ts=receive_ts,
                 raw_symbol=o.get("s"), side=side, side_rule="binance_order_side" if side else "unknown", raw_side=s,
                 price=price, price_kind=kind, qty=_f(o.get("z")),
                 key=f"{o.get('T')}|{s}|{o.get('ap')}|{o.get('z')}|{o.get('X')}", is_aggregated=True,
                 is_partial=(o.get("X") == "PARTIALLY_FILLED") if o.get("X") else None)]


def okx_side(pos_side: str | None, order_side: str | None) -> tuple[str | None, str]:
    if pos_side == "long":
        return "LONG", "okx_posSide"
    if pos_side == "short":
        return "SHORT", "okx_posSide"
    if pos_side == "net":
        side = {"sell": "LONG", "buy": "SHORT"}.get(order_side)
        return side, "okx_net_order_side" if side else "unknown"
    return None, "unknown"


def okx_details(details: list[dict], inst_id: str, ct_val: float | None, source: str, receive_ts: int) -> list[dict]:
    out = []
    if inst_id != OKX_INST:
        return out
    for d in details:
        side, rule = okx_side(d.get("posSide"), d.get("side"))
        sz = _f(d.get("sz"))
        qty = sz * ct_val if sz is not None and ct_val is not None else None
        out.append(_row(exchange="okx", source=source, event_ts=int(d["ts"]), receive_ts=receive_ts, raw_symbol=inst_id,
                        side=side, side_rule=rule, raw_side=f"{d.get('posSide')}/{d.get('side')}",
                        price=_f(d.get("bkPx")), price_kind="okx_bkPx", qty=qty,
                        key=f"{d.get('ts')}|{d.get('posSide')}|{d.get('side')}|{d.get('bkPx')}|{d.get('sz')}",
                        is_aggregated=False))
    return out


def okx_ws(msg: dict, ct_val: float | None, receive_ts: int) -> list[dict]:
    if (msg.get("arg") or {}).get("channel") != "liquidation-orders":
        return []
    out = []
    for inst in msg.get("data") or []:
        out += okx_details(inst.get("details") or [], inst.get("instId"), ct_val, "okx_ws", receive_ts)
    return out


def okx_rest(payload: dict, ct_val: float | None, receive_ts: int) -> list[dict]:
    out = []
    for inst in payload.get("data") or []:
        out += okx_details(inst.get("details") or [], inst.get("instId"), ct_val, "okx_rest", receive_ts)
    return out


class Deduper:
    """Contract section 5: drop keys seen in EARLIER messages; keep same-key rows within one message."""

    def __init__(self, keep_ms: int = 48 * 3_600_000):
        self.keep_ms = keep_ms
        self.seen: dict[str, int] = {}
        self.duplicates = 0
        self.intra_message_same_key = 0

    def load(self, key: str, event_ts: int) -> None:
        self.seen[key] = event_ts

    def filter(self, rows: list[dict]) -> list[dict]:
        out, this_msg = [], set()
        for r in rows:
            k = r["dedupe_key"]
            if k in self.seen and k not in this_msg:
                self.duplicates += 1
                continue
            if k in this_msg:
                self.intra_message_same_key += 1
            this_msg.add(k)
            out.append(r)
        for r in out:
            self.seen[r["dedupe_key"]] = r["event_ts_ms"]
        return out

    def prune(self, now_ms: int) -> None:
        cut = now_ms - self.keep_ms
        self.seen = {k: t for k, t in self.seen.items() if t >= cut}
