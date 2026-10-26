"""The HOT slot: one persistent HOT symbol beside BTC, replaced only by a guarded scan.

What it holds is a fact about the terminal, not about a browser: the symbol, when it was
selected and the scan figures that put it there. It is kept on disk beside the paper run, so a
reload, a route change or a restart all come back to the same HOT symbol, and it changes only
when the operator presses 코인탐색 and the replacement passes the lock on both ends.

The lock is per symbol. BTC never takes part (it is the main instrument, never the HOT one). The
current HOT symbol blocks a replacement while it has a position, a resting order or an order in
flight on any account this terminal drives - the paper engine, and the Binance account when a
key is configured. A source that cannot answer blocks too.

Exposure sources are registered from outside. The Binance one is registered by
`terminal.live_routes`, the one module allowed to reach the account, so nothing in this package
imports the LIVE code.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from .lock import (ALLOWED, BLOCKED, HOT_STATE_CHANGED_DURING_SCAN, HOT_STATE_UNKNOWN,
                   ExposureSource, InFlightRegistry, LockDecision, SymbolExposure, scan_permission)
from .scanner import ScanFailed, ScanResult

SLOT_VERSION = 1
HOT_SCAN_IN_PROGRESS = "HOT_SCAN_IN_PROGRESS"
HOT_UNCHANGED = "HOT_UNCHANGED"
HOT_REPLACED = "HOT_REPLACED"
HOT_SELECTED = "HOT_SELECTED"


@dataclass(frozen=True)
class HotSlot:
    symbol: str
    selected_at_ms: int
    scanned_at_ms: int
    metrics: dict[str, Any]
    top: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "selected_at_ms": self.selected_at_ms,
                "scanned_at_ms": self.scanned_at_ms, "metrics": self.metrics, "top": self.top}


class SlotStore:
    """One JSON file, replaced atomically. A file that does not parse is read as no HOT."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def load(self) -> HotSlot | None:
        if self.path is None or not self.path.exists():
            return None
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
            if body.get("version") != SLOT_VERSION or not isinstance(body.get("symbol"), str):
                return None
            return HotSlot(symbol=body["symbol"], selected_at_ms=int(body["selected_at_ms"]),
                           scanned_at_ms=int(body["scanned_at_ms"]),
                           metrics=dict(body.get("metrics") or {}), top=list(body.get("top") or []))
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save(self, slot: HotSlot) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": SLOT_VERSION, **slot.as_dict()},
                                  ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class CombinedExposure:
    """Every registered source's exposure on one symbol, added up. Unknown if any is unknown."""

    def __init__(self, sources: list[ExposureSource]) -> None:
        self.sources = sources

    def exposure(self, symbol: str) -> SymbolExposure:
        position = Decimal(0)
        orders = 0
        in_flight = 0
        for source in self.sources:
            part = source.exposure(symbol)       # a raise propagates: scan_permission blocks
            if part.symbol != symbol or None in (part.position_amt, part.open_orders, part.in_flight):
                return SymbolExposure(symbol, None, None, None)
            position += abs(part.position_amt)
            orders += part.open_orders
            in_flight += part.in_flight
        return SymbolExposure(symbol, position, orders, in_flight)


@dataclass
class ScanOutcome:
    code: str
    decision: LockDecision
    previous: str | None
    slot: HotSlot | None
    result: ScanResult | None = None
    prepare_error: str | None = None

    @property
    def changed(self) -> bool:
        return self.code in (HOT_REPLACED, HOT_SELECTED)


@dataclass
class HotSlotController:
    store: SlotStore = field(default_factory=SlotStore)
    sources: list[ExposureSource] = field(default_factory=list)
    #: Called with the new symbol before it is committed: validate it and measure what its
    #: paper run needs. Raising refuses the replacement; returning a string records a non-fatal
    #: note (a paper margin table that could not be measured, say).
    prepare: Callable[[str, Mapping[str, Any]], str | None] | None = None
    #: Called inside the commit, before the new slot is visible to any reader: admits the new
    #: symbol so the first request for it cannot arrive before it is permitted.
    on_commit: list[Callable[[str], Any]] = field(default_factory=list)
    #: Called after a replacement is committed, with the symbol that left the slot.
    on_retire: list[Callable[[str], Any]] = field(default_factory=list)
    clock_ms: Callable[[], int] = field(default_factory=lambda: lambda: int(time.time() * 1000))
    #: The symbol being replaced, set from just before the re-check until the commit is done.
    #: An order route registers itself in flight *first* and then refuses if this names its
    #: symbol; the scan sets this *first* and then re-checks in-flight counts. Whichever runs
    #: second sees the other, so an order cannot land on the old HOT symbol unseen.
    replacing: str | None = None
    _slot: HotSlot | None = None
    _loaded: bool = False
    _scan_lock: threading.Lock = field(default_factory=threading.Lock)
    _state_lock: threading.Lock = field(default_factory=threading.Lock)
    paper_in_flight: InFlightRegistry = field(default_factory=InFlightRegistry)
    #: Set by `live_routes` when a Binance key is configured: the account's own leverage
    #: brackets for a symbol, used for a paper margin table only when Bybit does not list it.
    bracket_provider: Callable[[str], list[Mapping[str, Any]] | None] | None = None

    # ------------------------------------------------------------------ state
    def configure(self, path: Path | None) -> HotSlot | None:
        with self._state_lock:
            self.store = SlotStore(path)
            self._slot = self.store.load()
            self._loaded = True
            return self._slot

    def current(self) -> HotSlot | None:
        with self._state_lock:
            if not self._loaded:
                self._slot = self.store.load()
                self._loaded = True
            return self._slot

    def add_source(self, source: ExposureSource) -> None:
        if source not in self.sources:
            self.sources.append(source)

    def lock(self) -> LockDecision:
        slot = self.current()
        return scan_permission(slot.symbol if slot else None, CombinedExposure(list(self.sources)))

    @property
    def scanning(self) -> bool:
        return self._scan_lock.locked()

    # ------------------------------------------------------------------ scan
    def scan_and_replace(self, scan: Callable[[], ScanResult]) -> ScanOutcome:
        """Check, scan, re-check, commit. Blocking; run it on a worker thread.

        One at a time: a second call while one runs gets HOT_SCAN_IN_PROGRESS immediately rather
        than a second scan.
        """
        if not self._scan_lock.acquire(blocking=False):
            return ScanOutcome(HOT_SCAN_IN_PROGRESS,
                               LockDecision(BLOCKED, HOT_STATE_UNKNOWN, None), None, self.current())
        try:
            slot = self.current()
            previous = slot.symbol if slot else None
            source = CombinedExposure(list(self.sources))
            before = scan_permission(previous, source)
            if not before.allowed:
                return ScanOutcome(before.code, before, previous, slot)
            result = scan()                     # ScanFailed propagates; nothing was changed
            top = result.top[0]
            note = None
            if top.symbol != previous and self.prepare is not None:
                note = self.prepare(top.symbol, top.as_dict())
            self.replacing = previous
            after = scan_permission(previous, source)
            if not after.allowed:
                changed = LockDecision(BLOCKED, HOT_STATE_CHANGED_DURING_SCAN, previous)
                return ScanOutcome(HOT_STATE_CHANGED_DURING_SCAN, changed, previous, slot, result)
            now = self.clock_ms()
            same = top.symbol == previous
            new = HotSlot(symbol=top.symbol,
                          selected_at_ms=slot.selected_at_ms if same and slot else now,
                          scanned_at_ms=result.scanned_at_ms, metrics=top.as_dict(),
                          top=[c.as_dict() for c in result.top])
            with self._state_lock:
                for hook in list(self.on_commit):
                    hook(new.symbol)
                self.store.save(new)
                self._slot = new
            code = HOT_UNCHANGED if same else (HOT_REPLACED if previous else HOT_SELECTED)
            outcome = ScanOutcome(code, after, previous, new, result, prepare_error=note)
        finally:
            self.replacing = None
            self._scan_lock.release()
        if outcome.changed and previous:
            for hook in list(self.on_retire):
                try:
                    hook(previous)
                except Exception:
                    pass     # a release that fails costs a resource, never the new HOT symbol
        return outcome


#: The process's one controller. `server.py`'s lifespan points it at its file; `live_routes`
#: adds the Binance exposure source; the paper exposure source is added by `hot_routes`.
controller = HotSlotController()

__all__ = ["CombinedExposure", "HotSlot", "HotSlotController", "ScanOutcome", "SlotStore",
           "controller", "HOT_REPLACED", "HOT_SELECTED", "HOT_UNCHANGED", "HOT_SCAN_IN_PROGRESS",
           "ScanFailed", "ALLOWED"]
