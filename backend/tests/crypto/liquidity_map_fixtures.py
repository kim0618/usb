"""Journals for the Liquidity Map preview tests.

`written_journal` drives the **real** collector into the **real** store, so the reader under test
faces the actual file names, the actual `.jsonl.open` tail and the actual record envelopes rather
than a reader-friendly imitation. That is the point: most of the ways to read this journal wrongly
are ways of mis-handling what the writer really does.

`handwritten_journal` exists for the cases the real writer cannot be steered into on demand - the
verification matrix in `wallstate`, where a test needs an exact `storage_stats` count beside an
exact set of wall transitions.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.crypto.market_structure_v0.collector import REFRESH_COVERAGE_EDGE, Collector
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store, canonical_line

from tests.crypto.ms_v0_fixtures import agg_trade, depth_frame

S = 1_000_000_000
MID = Decimal("84750")
TICK = Decimal("10")


def wall_snapshot(*, last_update_id: int = 1000, levels: int = 13,
                  big_bid_offset: int = 4, big_ask_offset: int = 6,
                  big_size: str = "40") -> dict[str, Any]:
    """A snapshot with one oversized level per side, so a wall candidate actually qualifies.

    Every other level is 1 BTC, so the oversized level is 40x the neighbour mean - well past the
    contract's 3x - and the neighbourhood has the 3 occupied levels on each side the rule needs.
    """
    bids = [[str(MID - TICK * i), big_size if i == big_bid_offset else "1"]
            for i in range(levels)]
    asks = [[str(MID + TICK * (i + 1)), big_size if i == big_ask_offset else "1"]
            for i in range(levels)]
    return {"lastUpdateId": last_update_id, "bids": bids, "asks": asks, "E": 1_000, "T": 1_000}


def written_journal(root: Path, *, samples: int = 4, trades: int = 3, stats: bool = True,
                    seal: bool = True, end_session: bool = True,
                    snapshot_payload: dict[str, Any] | None = None,
                    soft_refresh_after: int | None = None) -> dict[str, Any]:
    """A complete session written by the collector itself. Returns what the test needs to assert.

    `soft_refresh_after` installs a voluntary coverage-edge refresh once that many samples have
    been taken, which is what a continuity test needs: a real generation change, classified by
    the collector itself rather than by a handwritten state file.
    """
    session = Session()
    store = Store.open(root, session.session_id, started_ns=session.started_ns)
    collector = Collector(store=store, session=session)
    # Real clocks, not synthetic ones. The store's flush and rotation timers compare against the
    # `mono_ns` of the records it is handed, so a journal written on a 10-second fake monotonic
    # base never flushes and the reader finds an empty directory - which looks like a reader bug.
    base_ms, base_ns = int(time.time() * 1000), time.monotonic_ns()

    collector.write_session_record(config={"root": str(root)})
    collector.on_depth_connect("d1", receive_ms=base_ms, mono_ns=base_ns)
    collector.on_trade_connect("t1", receive_ms=base_ms, mono_ns=base_ns)
    collector.mark_snapshot_requested(receive_ms=base_ms + 10, mono_ns=base_ns + 10_000_000)
    collector.on_snapshot(snapshot_payload or wall_snapshot(), receive_ms=base_ms + 50,
                          mono_ns=base_ns + 50_000_000, request_ms=base_ms + 10,
                          request_mono_ns=base_ns + 10_000_000)
    for index in range(trades):
        collector.on_trade_frame(
            agg_trade(trade_id=10 + index, qty="0.5", buyer_is_maker=index % 2 == 1,
                      event_ms=base_ms + 100 + index),
            receive_ms=base_ms + 100 + index, mono_ns=base_ns + (100 + index) * 1_000_000)

    # The first delta after a snapshot must straddle `lastUpdateId` (`U <= lastUpdateId <= u`),
    # which is the futures rule and not spot's `lastUpdateId + 1`. Getting this wrong here would
    # leave every sample UNSYNCED and the fixture would quietly test the empty case.
    last_u = 1000
    first_delta = True
    sample_ms = base_ms
    for index in range(samples):
        # A delta each second keeps the book fresh; without it `check_staleness` would invalidate
        # the book after 2 s and every later sample would be UNSYNCED.
        sample_ms = base_ms + 1_000 * (index + 1)
        sample_ns = base_ns + 1_000_000_000 * (index + 1)
        if first_delta:
            snapshot_id = collector.depth.snapshot_update_id or 1000
            frame = depth_frame(first=snapshot_id - 5, last=snapshot_id + 5,
                                previous=snapshot_id - 10,
                                bids=[[str(MID - TICK * 11), "1"]], event_ms=sample_ms - 10)
            last_u = snapshot_id + 5
            first_delta = False
        else:
            frame = depth_frame(first=last_u + 1, last=last_u + 10, previous=last_u,
                                bids=[[str(MID - TICK * 11), "1"]], event_ms=sample_ms - 10)
            last_u += 10
        collector.on_depth_frame(frame, receive_ms=sample_ms - 10,
                                 mono_ns=sample_ns - 10_000_000)
        collector.sample(at_ns=sample_ns, at_ms=sample_ms)
        if soft_refresh_after is not None and index + 1 == soft_refresh_after:
            # Requested and staged between two samples, exactly as the runner does it. The
            # snapshot carries the live chain's own id, which is the case where the staged book
            # reaches the swap invariant as soon as the buffer is replayed.
            collector._want_refresh(REFRESH_COVERAGE_EDGE, at_ns=sample_ns + 100_000_000,
                                    at_ms=sample_ms + 100)
            collector.mark_snapshot_requested(receive_ms=sample_ms + 100,
                                              mono_ns=sample_ns + 100_000_000)
            payload = dict(snapshot_payload or wall_snapshot())
            payload["lastUpdateId"] = collector.depth.last_update_id
            collector.on_snapshot(payload,
                                  receive_ms=sample_ms + 200,
                                  mono_ns=sample_ns + 200_000_000,
                                  request_ms=sample_ms + 100,
                                  request_mono_ns=sample_ns + 100_000_000)
            assert collector.refreshes_applied == 1, "the staged refresh must have swapped in"

    if stats:
        collector.emit_stats(at_ns=base_ns + 1_000_000_000 * (samples + 1),
                             at_ms=base_ms + 1_000 * (samples + 1))
    if end_session:
        collector.finish("test", at_ns=base_ns + 1_000_000_000 * (samples + 2),
                         at_ms=base_ms + 1_000 * (samples + 2))
    if seal:
        store.close()
    else:
        store.tick(base_ns + 1_000_000_000 * (samples + 3), base_ms + 1_000 * (samples + 3))
        if store.lock is not None:
            store.lock.release()
            store.lock = None
    return {"session_id": session.session_id, "root": root, "last_sample_ms": sample_ms,
            "samples": samples}


# --------------------------------------------------------------------------- handwritten


class Handwriter:
    """Writes exact envelopes into exact files. For the verification matrix only."""

    def __init__(self, root: Path, session_id: str = "aaaaaaaa-0000-0000-0000-000000000000") -> None:
        self.root = root
        self.session_id = session_id
        self.seq = 0

    def _append(self, kind: str, path_seq: int, payload: dict[str, Any], *, receive_ms: int,
                open_file: bool = False) -> int:
        self.seq += 1
        record = {"version": "btc-ms.v0.1", "collector_version": "btc-ms.collector.0.1",
                  "exchange": "binance_usdm", "symbol": "BTCUSDT",
                  "session_id": self.session_id, "seq": self.seq, "kind": kind,
                  "receive_ms": receive_ms, "mono_ns": receive_ms * 1_000_000,
                  "connection_id": None, "payload": payload}
        directory = self.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".jsonl.open" if open_file else ".jsonl"
        name = f"{kind}-20261004-{self.session_id[:8]}-{path_seq:05d}{suffix}"
        with open(directory / name, "ab") as handle:
            handle.write(canonical_line(record))
        return self.seq

    def session_start(self, *, started_ms: int = 1_000) -> None:
        self._append("session", 1, {"event": "START", "session_id": self.session_id,
                                    "started_ms": started_ms, "contract": {"contract_version":
                                                                           "btc-ms.v0.1"}},
                     receive_ms=started_ms)

    def session_end(self, *, ended_ms: int = 9_000) -> None:
        self._append("session", 1, {"event": "END", "session_id": self.session_id,
                                    "ended_ms": ended_ms, "reason": "test"}, receive_ms=ended_ms)

    def wall(self, *, side: str, price: str, event: str, status: str, receive_ms: int,
             path_seq: int = 1, notional: str = "1000000", multiple: str = "9",
             first_seen_ms: int | None = None, open_file: bool = False,
             persistence_ms: int = 60_000, samples: int = 60) -> int:
        """One wall row. `persistence_ms` defaults to a candidate that has rested a minute.

        A real OPENED row carries 0 and 1 sample forever, which the frozen `lm-wall.v2`
        persistence floor rejects - correctly. Tests about reading the journal want a candidate
        that *does* qualify, so the default here is a rested one and the rejection case is asked
        for explicitly.
        """
        qty = str(Decimal(notional) / Decimal(price))
        return self._append("wall", path_seq, {
            "side": side, "price": price, "bin": price, "bin_rule": "EXACT_PRICE",
            "qty": qty, "current_size": qty, "notional": notional,
            "local_average": "1", "multiple": multiple, "max_size": qty, "min_size": qty,
            "max_multiple": multiple, "neighbours": 10,
            "first_seen_ms": receive_ms if first_seen_ms is None else first_seen_ms,
            "last_seen_ms": receive_ms, "persistence_ms": persistence_ms, "samples": samples,
            "status": status, "event": event, "coverage": "PARTIAL", "generation": 1,
            "persistence_is_sampled_span": True, "order_identity_proven": False},
            receive_ms=receive_ms, open_file=open_file)

    def stats(self, *, active: int, receive_ms: int) -> int:
        return self._append("storage_stats", 1, {
            "walls": {"active": active, "opened": active, "ended": 0, "interrupted": 0},
            "total_bytes": 1, "total_rows": 1}, receive_ms=receive_ms)

    def derived(self, *, receive_ms: int, mid: str = "84750", state: str = "SYNCED",
                fresh: bool = True, age_ms: int = 100, bands: list[dict[str, Any]] | None = None,
                flow_coverage: str = "COMPLETE", trade_connected: bool = True,
                trade_age_ms: int | None = 200, sample_index: int = 1) -> int:
        return self._append("derived", 1, {
            "sample_index": sample_index,
            "book": {"state": state, "generation": 1, "mid": mid, "best_bid": "84749.9",
                     "best_ask": "84750.1", "source_u": 10, "snapshot_update_id": 1,
                     "known_low": "84620", "known_high": "84880", "age_ms": age_ms,
                     "fresh": fresh, "event_ms": receive_ms - 10, "receive_ms": receive_ms - 5,
                     "lag_ms": 10, "lag_state": "COMPLETE", "levels": 1900,
                     "last_invalidation": None,
                     "bands": bands if bands is not None else default_bands()},
            "flow": {label: flow_window(label, flow_coverage) for label in ("5s", "15s", "60s")},
            "trade_stream": {"connected": trade_connected, "age_ms": trade_age_ms,
                             "last_receive_ms": receive_ms - 200},
            "coverage_note": "test"}, receive_ms=receive_ms)


def band_side(coverage: str, *, qty: str = "100", notional: str = "8475000") -> dict[str, Any]:
    canonical = coverage == "COMPLETE"
    observed = coverage in ("COMPLETE", "PARTIAL")
    return {"coverage": coverage, "qty": qty if canonical else None,
            "notional": notional if canonical else None,
            "observed_qty": qty if observed else None,
            "observed_notional": notional if observed else None,
            "observed_is_lower_bound": coverage == "PARTIAL",
            "levels": 700 if observed else None,
            "price_low": "84700" if observed else None,
            "price_high": "84800" if observed else None}


def default_bands() -> list[dict[str, Any]]:
    """The measured shape: +-0.1% COMPLETE, the three wider bands PARTIAL."""
    rows = []
    for label, coverage in (("0.1", "COMPLETE"), ("0.25", "PARTIAL"), ("0.5", "PARTIAL"),
                            ("1", "PARTIAL")):
        complete = coverage == "COMPLETE"
        rows.append({"band_pct": label, "bid": band_side(coverage), "ask": band_side(coverage),
                     "imbalance_btc": "0.01" if complete else None,
                     "imbalance_usdt": "0.011" if complete else None})
    return rows


def unknown_bands() -> list[dict[str, Any]]:
    return [{"band_pct": label, "bid": band_side("UNKNOWN"), "ask": band_side("UNKNOWN"),
             "imbalance_btc": None, "imbalance_usdt": None}
            for label in ("0.1", "0.25", "0.5", "1")]


def flow_window(label: str, coverage: str) -> dict[str, Any]:
    canonical = coverage == "COMPLETE"
    observed = coverage in ("COMPLETE", "PARTIAL")
    return {"coverage": coverage, "coverage_reason": None if canonical else "WARMUP",
            "coverage_age_ms": 5_000, "trades": 4 if observed else None,
            "buy_btc": "1.5" if canonical else None, "sell_btc": "0.5" if canonical else None,
            "buy_usdt": "127125" if canonical else None,
            "sell_usdt": "42375" if canonical else None,
            "net_btc": "1" if canonical else None, "net_usdt": "84750" if canonical else None,
            "observed_buy_btc": "1.5" if observed else None,
            "observed_sell_btc": "0.5" if observed else None,
            "observed_buy_usdt": "127125" if observed else None,
            "observed_sell_usdt": "42375" if observed else None,
            "observed_is_lower_bound": coverage == "PARTIAL",
            "imbalance_btc": "0.5" if canonical else None,
            "imbalance_usdt": "0.5" if canonical else None}


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().split(b"\n") if line]
