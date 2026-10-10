"""Builders for Production Context Collector V1 tests.

The context step reads the root through the Liquidity Map viewer's own path, which reads the
session stream from disk. So unlike the V0 tests, the sink here records envelopes **and** writes
them through to the store, and `flush` pushes the buffered writers to disk the way the runner's
once-a-second tick does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.crypto.context_collector_v1.collector import ContextCollector
from app.crypto.context_collector_v1.context import ContextEngine
from app.crypto.context_collector_v1.store import ContextStore
from app.crypto.market_structure_v0.collector import Collector
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store

from tests.crypto.liquidity_map_fixtures import wall_snapshot
from tests.crypto.ms_v0_fixtures import S, agg_trade, depth_frame

BASE_MS = 1_791_600_000_000


class TeeSink:
    """Records every envelope and writes it to the store, like the runner's persist worker."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.records: list[dict[str, Any]] = []

    def __call__(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        self.store.write(record, now_ns=record["mono_ns"], now_ms=record["receive_ms"])

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [record["payload"] for record in self.records if record["kind"] == kind]

    def kinds(self) -> list[str]:
        return [record["kind"] for record in self.records]


def flush(store: Store) -> None:
    for writer in store.writers.values():
        writer._flush(force=True)


def context_collector(tmp_path, *, shadow_bytes: bool = False, name: str = "ctx",
                      session: Session | None = None) -> tuple[ContextCollector, TeeSink]:
    session = session or Session(started_ms=BASE_MS, started_ns=0)
    root = tmp_path / name
    store = ContextStore.open(root, session.session_id, started_ns=session.started_ns,
                              shadow_bytes=shadow_bytes)
    sink = TeeSink(store)
    instance = ContextCollector(store=store, session=session, sink=sink,
                                engine=ContextEngine(root=root))
    return instance, sink


def research_collector(tmp_path, *, session: Session) -> tuple[Collector, TeeSink]:
    store = Store.open(tmp_path / "research", session.session_id, started_ns=session.started_ns)
    sink = TeeSink(store)
    return Collector(store=store, session=session, sink=sink), sink


@dataclass
class Script:
    """A deterministic live-like session: one depth frame and some trades every second.

    `ms(i)` / `ns(i)` are the wall and monotonic clocks of second `i`. Depth frames chain on `pu`
    from the snapshot's id, so the book stays SYNCED and fresh unless a step breaks it on purpose.
    """

    collectors: list[Any]
    next_update: int = 1100
    trade_id: int = 1
    second: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def ms(i: float) -> int:
        return BASE_MS + int(i * 1000)

    @staticmethod
    def ns(i: float) -> int:
        return int(i * S)

    def each(self, method: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [getattr(c, method)(*args, **kwargs) for c in self.collectors]

    def start(self, *, snapshot_payload: dict[str, Any] | None = None) -> None:
        for c in self.collectors:
            c.write_session_record(config={"test": True})
            flush(c.store)
        self.each("on_depth_connect", "d1", receive_ms=self.ms(0), mono_ns=self.ns(0))
        self.each("on_trade_connect", "t1", receive_ms=self.ms(0), mono_ns=self.ns(0))
        self.each("mark_snapshot_requested", receive_ms=self.ms(0), mono_ns=self.ns(0))
        payload = snapshot_payload or wall_snapshot()
        self.each("on_snapshot", payload, receive_ms=self.ms(0.1), mono_ns=self.ns(0.1),
                  request_ms=self.ms(0), request_mono_ns=self.ns(0))
        self.each("on_depth_frame", depth_frame(first=900, last=self.next_update, previous=850),
                  receive_ms=self.ms(0.2), mono_ns=self.ns(0.2))

    def depth(self, i: float, *, bids: list[list[str]] | None = None,
              asks: list[list[str]] | None = None) -> None:
        first = self.next_update + 1
        last = first + 99
        self.each("on_depth_frame", depth_frame(first=first, last=last,
                                                previous=self.next_update, bids=bids, asks=asks),
                  receive_ms=self.ms(i), mono_ns=self.ns(i))
        self.next_update = last

    def trade(self, i: float, *, qty: str = "1", price: str = "84750",
              sell: bool = False, trade_id: int | None = None) -> None:
        tid = self.trade_id if trade_id is None else trade_id
        self.each("on_trade_frame", agg_trade(trade_id=tid, qty=qty, price=price,
                                              buyer_is_maker=sell),
                  receive_ms=self.ms(i), mono_ns=self.ns(i))
        if trade_id is None:
            self.trade_id += 1

    def sample(self, i: float) -> list[dict[str, Any]]:
        out = self.each("sample", at_ns=self.ns(i), at_ms=self.ms(i))
        for c in self.collectors:
            flush(c.store)
        return out

    def run(self, seconds: int, *, trades_per_second: int = 1, sell_every: int = 3) -> None:
        """Advance `seconds` whole seconds: a frame, trades, then the sample, per second."""
        for _ in range(seconds):
            self.second += 1
            i = self.second
            self.depth(i - 0.5)
            for k in range(trades_per_second):
                self.trade(i - 0.4 + k * 0.01, sell=(self.trade_id % sell_every == 0))
            self.sample(i)


def latest(collector: ContextCollector) -> dict[str, Any]:
    assert collector.engine is not None and collector.engine.last_latest is not None
    return collector.engine.last_latest
