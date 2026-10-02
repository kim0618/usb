"""The backend loop: evaluate every closed minute, keep the shadow ledger, survive a restart.

Deliberately not in the browser. The 4 h result of a signal that fired at 13:35 has to exist
whether or not a tab was open at 17:35, so the evaluation, the ledger and the settlement all run
here and the screen only reads what this produced.

On the evaluation cadence. The brief for this work said every five minutes; the contract's
decision grid is every minute, and the contract is the authority. Two of the three conditions
move on the 1m grid - the open-interest change and the 24 h volatility are recomputed each minute,
and only the spot basis is pinned to 5m boundaries - so a five-minute cadence would place triggers
up to four minutes after the bar the study would have decided on and would silently drop events
shorter than one cadence. This loop therefore evaluates each closed 1m bar, which is also what
made the historical parity gate reproducible. The tick interval is only how often it looks for
newly closed bars; it is not the decision grid.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any

from . import c1x as c1x_rule
from .contract import (
    C1X_CONTRACT_DOC, C1X_CONTRACT_SHA256, C1X_ID, C1X_MAX_HOLD_MIN, C1X_MEANING, C1X_RESEARCH,
    C1X_SCHEMA_VERSION, CONTRACT_DOC, CONTRACT_SHA256, COST_SCENARIO, DIRECTION,
    DIRECTION_CONTRACT, E0_BENCHMARK_ID, E0_HORIZON_MIN, MINUTE_MS, OBSERVATION_HORIZONS_MIN,
    OFFICIAL_HORIZON_MIN, OVERLAP_POLICY, RESEARCH_CELL, RESEARCH_OOS, RESEARCH_VERDICT,
    SCHEMA_VERSION, STRATEGY, TARGET_SYMBOL, TARGET_VENUE,
)
from .engine import C1Engine, warmup_bars
from .grid import Grid, build_grid
from .models import Attribution, C1xEvent, ShadowTrade, Signal, clean_json
from .shadow import open_shadow, settle, summarize
from .sources import MarketData
from .store import DEFAULT_ROOT, C1Store

# 30 whole UTC days for the bucket window plus a day of slack for the volatility window, rounded
# up to whole days so the bucket window always lands on a day boundary.
RETAIN_DAYS = 33
DAY_MS = 86_400_000
TICK_SECONDS = 20.0
# A signal still holding needs its bars kept; 4 h is the official horizon and 8 h the longest
# observation, so a day of margin covers every open record.
HOLD_MARGIN_MS = 2 * DAY_MS


class C1Runtime:
    """Owns the grid, the engine, the ledger and the clock.

    `fixture_mode` exists for the preview screen: it loads signals from a file instead of a venue
    so a reviewer can see a LONG marker and a settled 4 h result without waiting for the market to
    produce one. It changes where signals come from and nothing about what a signal is, and
    production never sets it - `C1_FIXTURE` has to be pointed at a file by hand.
    """

    def __init__(self, root: Path | str = DEFAULT_ROOT, market: MarketData | None = None,
                 *, clock=time.time) -> None:
        self.store = C1Store(root)
        self.market = market
        self.engine = C1Engine()
        self.clock = clock
        self.grid: Grid | None = None
        self.funding: list[tuple[int, float]] = []
        self.signals: dict[str, Signal] = {}
        self.trades: dict[str, ShadowTrade] = {}
        self.c1x: dict[str, C1xEvent] = {}
        self.error: str | None = None
        self.ready = False
        self.last_tick_ms: int | None = None
        self.last_decided_at_ms: int | None = None
        self.bars_seen = 0
        self._pump: asyncio.Task | None = None

    # ------------------------------------------------------------------ setup
    def _market(self) -> MarketData:
        if self.market is None:
            self.market = MarketData()
        return self.market

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _window(self) -> tuple[int, int]:
        """The span of bars to hold: the warm-up the contract needs, extended back far enough to
        settle anything still open from before a restart."""
        end = self.now_ms() - self.now_ms() % MINUTE_MS
        start = end - RETAIN_DAYS * DAY_MS
        open_entries = [trade.entry_at_ms for trade in self.trades.values()
                        if trade.status not in ("SETTLED", "UNUSABLE_ENTRY")]
        if open_entries:
            start = min(start, min(open_entries) - HOLD_MARGIN_MS)
        return start, end

    def bootstrap(self) -> None:
        """Load what was written before, fetch the window, settle the backlog, then catch up.

        Order matters. The ledger is read first so the cursor and the armed flag come from disk
        rather than from a fresh engine, which is what stops a restart mid-event from emitting a
        second signal for the event already running.
        """
        self.signals = self.store.signals()
        self.trades = self.store.shadow()
        self.c1x = self.store.c1x()
        cursor = self.store.cursor()
        self.last_decided_at_ms = cursor.get("last_decided_at_ms")
        self.engine.resume_from(self.last_decided_at_ms, bool(cursor.get("armed", True)))
        self.refresh_grid()
        self.settle_open()
        self.catch_up()
        self.refresh_c1x()
        self.ready = self.grid is not None and len(self.grid) >= warmup_bars()

    def refresh_grid(self) -> None:
        start, end = self._window()
        data = self._market().fetch(start, end)
        self.grid = build_grid(data["bars"], data["spot"], data["open_interest"],
                               start_ms=data["start_ms"], end_ms=data["end_ms"] - MINUTE_MS)
        self.funding = list(data["funding"])
        self.bars_seen = len(self.grid)
        self.engine.cutoffs.clear()

    # ------------------------------------------------------------------ work
    def catch_up(self) -> None:
        """Judge every closed bar the cursor has not judged yet, in order."""
        grid = self.grid
        if grid is None or len(grid) < warmup_bars():
            return
        first = warmup_bars() - 1
        if self.last_decided_at_ms is not None:
            resume = grid.index_of(self.last_decided_at_ms - MINUTE_MS) + 1
            first = max(first, resume)
        fresh: list[Signal] = []
        for _, _, signal in self.engine.walk(grid, start_index=first, stop_index=len(grid)):
            if signal is None:
                continue
            if signal.signal_id in self.signals:
                continue                      # replay of a bar already judged; not a new event
            self.signals[signal.signal_id] = signal
            self.trades[signal.signal_id] = open_shadow(signal, grid, self.funding)
            fresh.append(signal)
        self.last_decided_at_ms = self.engine.last_decided_at_ms
        if fresh:
            self.store.append_signals(fresh)
            self.store.append_shadow(self.trades[signal.signal_id] for signal in fresh)
        self.settle_open()
        self.refresh_c1x()
        self.store.write_cursor(last_decided_at_ms=self.last_decided_at_ms,
                                armed=self.engine.armed,
                                extra={"bars": len(grid), "signals": len(self.signals)})

    def settle_open(self) -> None:
        """Advance every unfinished shadow as far as the candles allow.

        An overdue exit is priced from the historical bar at its planned instant, not from the
        current price: a signal whose 4 h ended while the process was down still has one correct
        exit, and it is in the candles.
        """
        grid = self.grid
        if grid is None:
            return
        changed: list[ShadowTrade] = []
        for signal_id, trade in self.trades.items():
            if trade.status == "SETTLED":
                continue
            signal = self.signals.get(signal_id)
            if signal is None:
                continue
            if grid.index_of(trade.entry_at_ms) < 0:
                trade.status = "UNSETTLEABLE_OUTSIDE_RETAINED_WINDOW"
                changed.append(trade)
                continue
            before = trade.to_json()
            settle(trade, signal, grid, self.funding)
            if trade.status == "SETTLED":
                trade.settled_from = "BYBIT_PUBLIC_KLINE"
            if trade.to_json() != before:
                changed.append(trade)
                signal.state = "COMPLETED" if trade.status == "SETTLED" else "ACTIVE"
        if changed:
            self.store.append_shadow(changed)
            self.store.append_signals(
                [self.signals[trade.signal_id] for trade in changed
                 if trade.signal_id in self.signals])

    def refresh_c1x(self) -> None:
        """Recompute the premium-normalization diagnostic for every signal that has not fired one.

        Three rules this method exists to hold:

        * **At most one per signal.** The event id is derived from the signal id, so a replay
          cannot make a second one.
        * **A published trigger is never recomputed.** Once a signal's diagnostic is TRIGGERED it
          is read from the ledger and left alone, so no later candle, and no restart, can move a
          timestamp that has already been shown. Only the E0 pairing is allowed to change after
          that, because the benchmark finishes later by construction.
        * **It is a diagnostic.** Nothing in this path touches an order, a position or a guard.
        """
        grid = self.grid
        if grid is None:
            return
        changed: list[C1xEvent] = []
        for signal_id, signal in self.signals.items():
            held = self.c1x.get(signal_id)
            if held is not None and held.status == "TRIGGERED":
                event = held
            else:
                event = c1x_rule.evaluate(signal, grid, self.engine.cutoffs.get, self.funding)
            before = None if held is None else held.to_json()
            c1x_rule.pair_with_e0(event, self.trades.get(signal_id))
            self.c1x[signal_id] = event
            if event.to_json() != before:
                changed.append(event)
        if changed:
            self.store.append_c1x(changed)

    def tick(self) -> None:
        try:
            self.refresh_grid()
            self.catch_up()
            self.ready = self.grid is not None and len(self.grid) >= warmup_bars()
            self.error = None
        except Exception as exc:                      # one bad fetch must not kill the loop
            self.error = f"{type(exc).__name__}: {exc}"
        self.last_tick_ms = self.now_ms()

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.tick)
            await asyncio.sleep(TICK_SECONDS)

    async def start(self) -> None:
        await asyncio.to_thread(self.bootstrap)
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        if self.market is not None:
            await asyncio.to_thread(self.market.close)

    # ------------------------------------------------------------------ views
    def display_sequences(self) -> dict[str, int]:
        """Human numbering derived from the durable signal ledger, never used as identity."""
        ordered = sorted(self.signals.values(), key=lambda row: (row.triggered_at_ms, row.signal_id))
        return {signal.signal_id: index for index, signal in enumerate(ordered, start=1)}

    def active(self) -> list[dict[str, Any]]:
        """Signals whose official 4 h has not finished, newest first."""
        rows = []
        sequences = self.display_sequences()
        for signal in self.signals.values():
            trade = self.trades.get(signal.signal_id)
            if trade is not None and trade.status == "SETTLED":
                continue
            diagnostic = self.c1x.get(signal.signal_id)
            rows.append({"signal": {**signal.to_json(),
                                     "display_seq": sequences[signal.signal_id]},
                         "shadow": trade.to_json() if trade else None,
                         "c1x": ({**diagnostic.to_json(),
                                   "display_seq": sequences[signal.signal_id]}
                                  if diagnostic else None)})
        rows.sort(key=lambda row: row["signal"]["triggered_at_ms"], reverse=True)
        return rows

    def markers(self, from_ms: int | None = None, to_ms: int | None = None,
                limit: int = 500) -> list[dict[str, Any]]:
        """What the chart draws: one row per signal, with its result when it has one.

        Timeframe is not a parameter. There is one C1 event series and each chart timeframe
        renders it; recomputing the signal per timeframe would invent events the contract never
        produced.
        """
        rows = []
        sequences = self.display_sequences()
        for signal in self.signals.values():
            at = signal.triggered_at_ms
            if from_ms is not None and at < from_ms:
                continue
            if to_ms is not None and at > to_ms:
                continue
            trade = self.trades.get(signal.signal_id)
            diagnostic = self.c1x.get(signal.signal_id)
            # `clean_json` turns NaN into null. A signal that fired on the newest closed bar has
            # no entry price until the next bar opens, and `json.dumps` would write a bare `NaN`
            # token that the browser's JSON.parse rejects - taking the whole marker response, and
            # with it the chart's signals, down over one unfilled field.
            rows.append(clean_json({
                "signal_id": signal.signal_id, "strategy": signal.strategy,
                "display_seq": sequences[signal.signal_id],
                "direction": signal.direction, "triggered_at_ms": at,
                "signal_bar_ms": signal.signal_bar_ms, "signal_price": signal.signal_price,
                "planned_exit_at_ms": signal.planned_exit_at_ms,
                "horizon_min": signal.horizon_min, "state": signal.state,
                "status": trade.status if trade else "NO_SHADOW",
                "net_return": trade.net_return if trade else None,
                "entry_price": trade.entry_price if trade else None,
                "exit_price": trade.exit_price if trade else None,
                # The diagnostic rides on the same row so the chart draws both marks from one
                # series. It is never a second signal: `is_exit` is false and stays false.
                "c1x": ({**diagnostic.to_json(),
                           "display_seq": sequences[signal.signal_id]}
                          if diagnostic else None),
            }))
        rows.sort(key=lambda row: row["triggered_at_ms"], reverse=True)
        return rows[:limit]

    def snapshot(self) -> dict[str, Any]:
        grid = self.grid
        evaluation = self.engine.last_evaluation
        return {
            "schema_version": SCHEMA_VERSION, "strategy": STRATEGY,
            "direction_contract": DIRECTION_CONTRACT, "direction": DIRECTION,
            "contract": {"document": CONTRACT_DOC, "sha256": CONTRACT_SHA256,
                         "research_cell": RESEARCH_CELL, "research_verdict": RESEARCH_VERDICT,
                         "research_oos": RESEARCH_OOS, "cost_scenario": COST_SCENARIO,
                         "official_horizon_min": OFFICIAL_HORIZON_MIN,
                         "observation_horizons_min": list(OBSERVATION_HORIZONS_MIN),
                         "overlap_policy": OVERLAP_POLICY},
            "venue": {"target": TARGET_VENUE, "symbol": TARGET_SYMBOL},
            "ready": self.ready, "error": self.error,
            "places_orders": False, "mutates_account": False,
            "last_tick_ms": self.last_tick_ms,
            "last_decided_at_ms": self.last_decided_at_ms,
            "armed": self.engine.armed,
            "bars": len(grid) if grid else 0,
            "warmup_bars_required": warmup_bars(),
            "last_bar": {
                "bar_ms": evaluation.bar_ms, "state": evaluation.state,
                "eligible": evaluation.eligible,
                "missing": list(evaluation.completeness.missing),
                "features": evaluation.features.to_json(),
            } if evaluation else None,
            "active": self.active(),
            "shadow_summary": summarize(self.trades.values()),
            "signals_total": len(self.signals),
            "c1x": self.c1x_summary(),
        }

    def c1x_summary(self) -> dict[str, Any]:
        """What the forward sample looks like so far, and the research it is being read against.

        Every rate here is reported with its denominator because the denominator is the point:
        this layer starts at zero events and C1 fires on 0.7% of bars, so for a long while the
        honest answer to "is C1x any good" is "not enough signals yet". Nothing in this method
        draws a conclusion.
        """
        events = list(self.c1x.values())
        triggered = [row for row in events if row.status == "TRIGGERED"]
        priced = [row for row in triggered if row.net_if_exited is not None]
        paired = [row for row in priced if row.delta_net is not None]
        holdings = sorted(row.holding_minutes for row in priced if row.holding_minutes is not None)
        body: dict[str, Any] = {
            "id": C1X_ID, "meaning": C1X_MEANING, "is_exit": False,
            "benchmark": E0_BENCHMARK_ID, "benchmark_horizon_min": E0_HORIZON_MIN,
            "max_hold_min": C1X_MAX_HOLD_MIN,
            "contract": {"document": C1X_CONTRACT_DOC, "sha256": C1X_CONTRACT_SHA256,
                         "schema_version": C1X_SCHEMA_VERSION},
            "research": C1X_RESEARCH,
            "c1_signals": len(self.signals),
            "c1x_triggered": len(triggered),
            "c1x_censored": sum(1 for row in events if row.censored_by_max_hold),
            "c1x_pending": sum(1 for row in events
                               if row.status in ("NOT_TRIGGERED", "CONFIRM_1")),
            "paired_with_e0": len(paired),
            "forward_sample_sufficient": False,
        }
        if self.signals:
            body["trigger_rate"] = len(triggered) / len(self.signals)
        if holdings:
            body["holding_minutes_median"] = holdings[len(holdings) // 2]
        if priced:
            body["c1x_net_mean_bp"] = sum(r.net_if_exited or 0 for r in priced) / len(priced) * 10_000
        if paired:
            deltas = [row.delta_net or 0.0 for row in paired]
            e0 = [row.e0_net or 0.0 for row in paired]
            body["e0_net_mean_bp"] = sum(e0) / len(e0) * 10_000
            body["paired_delta_mean_bp"] = sum(deltas) / len(deltas) * 10_000
            body["paired_delta_positive"] = sum(1 for d in deltas if d > 0)
        # A number of pairs this small cannot separate C1x from E0; E2 needed 1,603 events to get
        # a confidence interval that still crossed zero. The flag exists so no screen and no later
        # reader mistakes an early tally for a finding.
        body["forward_sample_sufficient"] = len(paired) >= C1X_RESEARCH["events"]
        return body

    # ------------------------------------------------------------- attribution
    def attributable(self) -> list[str]:
        """Signals an operator could plausibly be acting on: the ones still holding."""
        return [row["signal"]["signal_id"] for row in self.active()]

    def attribute(self, *, signal_id: str, account: str, trade_ref: str, note: str = "") -> Attribution:
        """Record that the operator says this trade of theirs came from this signal.

        It refuses an unknown signal id and it never infers a link. Nothing else in the system
        writes to this file, so a C1-tagged performance figure can only ever be built from
        statements the operator made.
        """
        if signal_id not in self.signals:
            raise KeyError(signal_id)
        attribution = Attribution(signal_id=signal_id, account=account, trade_ref=trade_ref,
                                  declared_at_ms=self.now_ms(), note=note)
        self.store.append_attribution(attribution)
        return attribution

    def attributions(self) -> list[dict[str, Any]]:
        return [row.to_json() for row in self.store.attributions()]
