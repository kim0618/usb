"""Builders for Market Structure V0 tests.

The snapshot builder deliberately reproduces the *measured* shape of a live `limit=1000` read:
outer bounds about +-0.15% from mid, which is why only the +-0.1% band can be COMPLETE. Tests that
want a band to be COMPLETE have to widen the bounds on purpose, which keeps the limitation
visible instead of letting a generous fixture hide it.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store

S = 1_000_000_000

#: Matches the live probe of 2026-10-04.
MID = Decimal("84750")
TICK = Decimal("10")


def snapshot(*, last_update_id: int = 1000, levels: int = 13, size: str = "1",
             event_ms: int | None = 1_000, mid: Decimal = MID,
             tick: Decimal = TICK) -> dict[str, Any]:
    """A depth snapshot centred on `mid`, `levels` deep on each side."""
    bids = [[str(mid - tick * i), size] for i in range(levels)]
    asks = [[str(mid + tick * (i + 1)), size] for i in range(levels)]
    payload: dict[str, Any] = {"lastUpdateId": last_update_id, "bids": bids, "asks": asks}
    if event_ms is not None:
        payload["E"] = event_ms
        payload["T"] = event_ms
    return payload


def wide_snapshot(*, last_update_id: int = 1000, size: str = "1") -> dict[str, Any]:
    """Bounds past +-1% of mid, so every band can reach COMPLETE."""
    return snapshot(last_update_id=last_update_id, levels=120, size=size)


def depth_frame(*, first: int, last: int, previous: int | None, bids: list[list[str]] | None = None,
                asks: list[list[str]] | None = None, event_ms: int = 1_001) -> dict[str, Any]:
    message: dict[str, Any] = {
        "e": "depthUpdate", "E": event_ms, "T": event_ms, "s": "BTCUSDT", "ps": "BTCUSDT",
        "U": first, "u": last, "b": bids or [], "a": asks or [],
    }
    if previous is not None:
        message["pu"] = previous
    return message


def agg_trade(*, trade_id: int, price: str = "84750", qty: str = "1", buyer_is_maker: bool = False,
              first: int | None = None, last: int | None = None,
              event_ms: int = 1_000) -> dict[str, Any]:
    """An `aggTrade` frame. `buyer_is_maker=True` is an aggressive SELL."""
    return {
        "e": "aggTrade", "E": event_ms, "T": event_ms - 150, "s": "BTCUSDT",
        "a": trade_id, "p": price, "q": qty, "nq": qty,
        "f": trade_id if first is None else first,
        "l": trade_id if last is None else last,
        "m": buyer_is_maker, "st": "MARKET",
    }


def synced_book(*, snapshot_payload: dict[str, Any] | None = None, receive_ms: int = 5_000,
                mono_ns: int = 0) -> B.DepthBook:
    """A book that has a snapshot installed and nothing else."""
    book = B.DepthBook()
    book.apply_snapshot(snapshot_payload or snapshot(), receive_ms, mono_ns)
    return book


class RecordingSink:
    """Captures envelopes so a test can assert the exact record sequence a fault produced."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def __call__(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def kinds(self) -> list[str]:
        return [record["kind"] for record in self.records]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [record["payload"] for record in self.records if record["kind"] == kind]

    def telemetry(self, event: str | None = None) -> list[dict[str, Any]]:
        rows = self.of("telemetry")
        return rows if event is None else [row for row in rows if row["event"] == event]

    def last(self, kind: str) -> dict[str, Any]:
        return self.of(kind)[-1]


def collector(tmp_path, **kwargs):
    """A `Collector` writing to `tmp_path`, with a recording sink attached."""
    from app.crypto.market_structure_v0.collector import Collector
    session = Session()
    store = Store.open(tmp_path / "data", session.session_id, started_ns=session.started_ns)
    sink = RecordingSink()
    instance = Collector(store=store, session=session, sink=sink, **kwargs)
    return instance, sink
