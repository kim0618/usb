"""One poll of the whole panel: the five layers side by side, and nothing above them.

The assembly rule that matters is a negative one. There is no function here that reads two layers
and returns a third value - no joint state, no agreement count, no roll-up header. `rollup` is in
the payload as `{"exists": false}` with the measurement that decided it, because an absent key is
indistinguishable from a key nobody got round to adding, while an explicit false is a statement a
test can hold onto.

The other decision worth naming is the cadence split. The structure layer is a full pass over a
multi-week bar series, so it is recomputed only when a new minute closes - roughly 200 ms once a
minute on a 30 day series - while the book and flow layers are re-read every poll at the collector's
own one-second cadence. Keeping them on one clock would either make the panel a second stale or
make it burn a CPU second per second for figures that cannot have changed.
"""
from __future__ import annotations

import time
from typing import Any

from . import auxiliary as AUX
from . import contract as C
from . import derivatives as DRV
from . import flow as FLW
from . import journal as JRN
from . import liquidity as LIQ
from . import structure as STR
from . import venue as VN
from . import PREVIEW_VERSION

#: Section 3, as data. The screen reads this rather than deciding per layer, so a symbol can
#: never be supported by one code path and unsupported by another.
SYMBOL_SUPPORT: dict[str, dict[str, Any]] = {
    C.BTC: {layer: {"supported": True, "reason": None} for layer in C.LAYERS},
    "ETHUSDT": {
        "PRICE": {"supported": False, "reason": C.REASON_NOT_CALIBRATED},
        "LIQUIDITY": {"supported": False, "reason": C.REASON_COLLECTOR_BTC_ONLY},
        "FLOW": {"supported": False, "reason": C.REASON_COLLECTOR_BTC_ONLY},
        "DERIVATIVES": {"supported": True, "reason": None},
        "AUXILIARY": {"supported": False, "reason": C.REASON_RESEARCH_BTC_ONLY},
    },
    "SOLUSDT": {
        "PRICE": {"supported": False, "reason": C.REASON_NOT_CALIBRATED},
        "LIQUIDITY": {"supported": False, "reason": C.REASON_COLLECTOR_BTC_ONLY},
        "FLOW": {"supported": False, "reason": C.REASON_COLLECTOR_BTC_ONLY},
        "DERIVATIVES": {"supported": True, "reason": None},
        "AUXILIARY": {"supported": False, "reason": C.REASON_RESEARCH_BTC_ONLY},
    },
}

#: Derivatives are polled from the venue, so they get their own floor. The figures move on the
#: endpoint's own 5 m bucket and the mark price updates about once a second; 5 s is well inside
#: both and keeps the request budget of a panel left open all day negligible.
DERIVATIVES_MIN_INTERVAL_MS = 5_000


class Panel:
    """Everything the panel keeps between polls, and nothing it does not need to.

    One `Series` and one `VanishTracker` per symbol that has them, held in per-symbol dictionaries
    rather than behind a symbol argument on a shared object. That is the shape the multi-symbol
    terminal arrived at after a per-symbol stream flag was read from shared state and the symbols
    silently stole each other's updates: with a dictionary of whole objects there is no path that
    reads one symbol's state while answering about another.
    """

    def __init__(self, *, anchor_days: int = C.ANCHOR_DAYS,
                 journal_root: Any = None,
                 publish_window_ms: int = C.PUBLISH_WINDOW_MS) -> None:
        self.anchor_days = anchor_days
        self.publish_window_ms = publish_window_ms
        self.started_ms = int(time.time() * 1000)
        self.anchor_ms = STR.default_anchor_ms(self.started_ms, anchor_days)
        self.series: dict[str, STR.Series] = {}
        self.runs: dict[str, STR.StructureRun] = {}
        self.structure_errors: dict[str, str] = {}
        self.trackers: dict[str, FLW.VanishTracker] = {}
        self.derivatives: dict[str, DRV.DerivativesRead] = {}
        self.derivatives_errors: dict[str, str] = {}
        self.journal = JRN.ForwardJournal(
            JRN.journal_root() if journal_root is None else journal_root)
        self.polls = 0
        self.structure_recomputes = 0

    # -- structure --------------------------------------------------------------------------
    def _tracker(self, symbol: str) -> FLW.VanishTracker:
        if symbol not in self.trackers:
            self.trackers[symbol] = FLW.VanishTracker()
        return self.trackers[symbol]

    def refresh_structure(self, symbol: str, now_ms: int, *, opener: Any = None) -> None:
        """Extend the anchored series and re-run R1, but only when a minute has closed."""
        if symbol != C.BTC:
            return
        series = self.series.get(symbol)
        if series is None:
            series = STR.Series(symbol, anchor_ms=self.anchor_ms)
            self.series[symbol] = series
        run = self.runs.get(symbol)
        if run is not None and series.last_open_ms == STR.last_closed_open_ms(now_ms):
            return
        try:
            started = time.monotonic()
            added = series.extend(now_ms, opener=opener)
            fetch_ms = int((time.monotonic() - started) * 1000)
            if added == 0 and run is not None:
                return
            fresh = series.run(publish_window_ms=self.publish_window_ms, now_ms=now_ms)
            fresh.fetch_ms = fetch_ms
            fresh.pages_fetched = series.fetches
            self.runs[symbol] = fresh
            self.structure_errors.pop(symbol, None)
            self.structure_recomputes += 1
        except VN.VenueError as caught:
            # The previous run stays on screen with its own age beside it. A panel that blanks
            # the structure layer on one failed fetch throws away a reading that is still true
            # about the minute it was computed for.
            self.structure_errors[symbol] = str(caught)
        except (RuntimeError, ValueError) as caught:
            self.structure_errors[symbol] = f"SERIES_UNUSABLE:{type(caught).__name__}"

    # -- derivatives ------------------------------------------------------------------------
    def refresh_derivatives(self, symbol: str, now_ms: int, *, opener: Any = None) -> None:
        previous = self.derivatives.get(symbol)
        if previous is not None and now_ms - previous.read_at_ms < DERIVATIVES_MIN_INTERVAL_MS:
            return
        self.derivatives[symbol] = DRV.read(symbol, now_ms, opener=opener)

    # -- one poll ---------------------------------------------------------------------------
    def poll(self, symbol: str, *, now_ms: int | None = None,
             min_notional_usdt: str | None = None,
             liquidity_snapshot: dict[str, Any] | None = None,
             opener: Any = None, write_journal: bool = True) -> dict[str, Any]:
        """Assemble the payload for one symbol."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        started = time.monotonic()
        self.polls += 1

        self.refresh_structure(symbol, now_ms, opener=opener)
        self.refresh_derivatives(symbol, now_ms, opener=opener)

        snapshot = liquidity_snapshot
        liquidity_reason = None
        if snapshot is None and symbol == C.BTC:
            try:
                snapshot = LIQ.read(min_notional_usdt=min_notional_usdt)
            except Exception as caught:                   # a viewer fault must not blank the page
                liquidity_reason = f"VIEWER_RAISED:{type(caught).__name__}"
                snapshot = None

        price_layer = STR.structure_view(
            self.runs.get(symbol), symbol=symbol, now_ms=now_ms,
            reasons=([self.structure_errors[symbol]] if symbol in self.structure_errors
                     else None))
        liquidity_layer = LIQ.liquidity_view(snapshot, symbol=symbol, reason=liquidity_reason)
        flow_layer = FLW.flow_view(snapshot, liquidity_layer, symbol=symbol,
                                   tracker=self._tracker(symbol), reason=liquidity_reason)
        derivatives_layer = DRV.derivatives_view(self.derivatives.get(symbol), symbol=symbol,
                                                 now_ms=now_ms)
        auxiliary_layer = AUX.auxiliary_view(symbol=symbol, now_ms=now_ms)

        layers = {"PRICE": price_layer, "LIQUIDITY": liquidity_layer, "FLOW": flow_layer,
                  "DERIVATIVES": derivatives_layer, "AUXILIARY": auxiliary_layer}

        payload: dict[str, Any] = {
            "panel_version": PREVIEW_VERSION,
            "mode": "READ_ONLY_CONTEXT_PANEL",
            "contract": C.contract_identity(),
            "server_time_ms": now_ms,
            "symbol": symbol,
            "supported_symbols": list(C.SUPPORTED_SYMBOLS),
            "symbol_support": SYMBOL_SUPPORT.get(symbol, {}),
            "layer_order": list(C.LAYERS),
            "layer_states": {name: layer.get("state") for name, layer in layers.items()},
            "layers": layers,
            "rollup": {
                "exists": False,
                "reason": "NO_JOINT_READING_IS_PUBLISHED",
                "measurement": ("Market Context R0: A_NO_CONTEXT_EDGE. T1 NOT_MEASURABLE "
                                "(796/799 resistance approaches OUTSIDE_JOURNAL), T3 best effect "
                                "+4.83bp against an 11.25bp round trip, T2's significant cells "
                                "carried the wrong sign."),
                "note": C.NO_ROLLUP_NOTE,
            },
            "freshness_bounds": C.freshness_bounds(),
            "notes": C.notes_payload(),
            "missing_value": C.MISSING,
            "panel": {
                "started_ms": self.started_ms,
                "anchor_ms": self.anchor_ms,
                "anchor_days": self.anchor_days,
                "polls": self.polls,
                "structure_recomputes": self.structure_recomputes,
            },
        }

        line = JRN.record(payload, written_at_ms=now_ms)
        wrote = self.journal.append(line, written_at_ms=now_ms) if write_journal else False
        payload["journal"] = {**self.journal.view(now_ms=now_ms),
                              "wrote_this_poll": wrote}
        payload["cost"] = {"elapsed_ms": int((time.monotonic() - started) * 1000),
                           "structure_recompute_ms": (price_layer.get("series") or {})
                           .get("recompute_ms") if price_layer.get("series") else None}
        return payload


__all__ = ["Panel", "SYMBOL_SUPPORT", "DERIVATIVES_MIN_INTERVAL_MS"]
