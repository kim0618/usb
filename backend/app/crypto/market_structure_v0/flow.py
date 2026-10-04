"""Rolling aggressive-flow windows, with coverage that tracks interruptions.

Windows are 5, 15 and 60 seconds over the half-open interval `(now - window, now]`, measured on
the **local monotonic receipt clock**. That choice is deliberate and it has a cost worth stating:
a window built on receipt time answers "what had this collector seen by then", which is the
question a live reader of these metrics is really asking, but it is not identical to a window
built on exchange event time. The raw `E`/`T` of every trade is retained, so an exchange-time
reconstruction stays possible offline; what cannot be reconstructed later is what we knew at the
time, so that is what is sampled.

Coverage follows the same three-state vocabulary as the depth bands:

* `COMPLETE` - the trade stream was connected, fresh and uninterrupted for the entire window.
* `PARTIAL` - the window reaches back past the start of continuous coverage (session warmup) or
  overlaps an interruption (reconnect, id jump, stale period). Canonical totals are null and the
  observed totals are exposed as what was actually seen in the window.
* `UNKNOWN` - the stream is disconnected or stale right now. Everything is null.

A COMPLETE window with no trades in it reports zero volume, because that zero was observed. Its
normalized imbalance is still null, because the denominator is zero and a ratio of nothing is not
zero. Warmup is `max(windows)` = 60 s by construction, so a restart genuinely starts over: the
5 s window becomes COMPLETE again after 5 s of coverage, the 60 s window after 60 s, and nothing
is counted across a session boundary.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .contract import (COMPLETE, FLOW_MAX_RECORDS, FLOW_WINDOWS_S, PARTIAL, RATIO_DECIMALS,
                       TRADE_STALE_MS, UNKNOWN)
from .envelope import decimal_out, ratio_out
from .trades import BUY, Trade

#: Reasons continuous coverage restarted. Recorded so a PARTIAL window can be explained.
WARMUP = "WARMUP"
INTERRUPTION_RECONNECT = "RECONNECT"
INTERRUPTION_ID_JUMP = "ID_JUMP"
INTERRUPTION_STALE = "STALE"
INTERRUPTION_OVERFLOW = "FLOW_OVERFLOW"
INTERRUPTION_OUT_OF_ORDER = "OUT_OF_ORDER_EVENT_TIME"


@dataclass
class FlowWindows:
    """Bounded tape of recent trades plus the continuous-coverage mark."""

    windows_s: tuple[int, ...] = FLOW_WINDOWS_S
    max_records: int = FLOW_MAX_RECORDS
    #: (mono_ns, aggressor, qty, notional)
    tape: deque[tuple[int, str, Decimal, Decimal]] = field(default_factory=deque)
    #: Monotonic time from which coverage has been continuous. None means no coverage at all.
    coverage_since_ns: int | None = None
    coverage_reason: str = WARMUP
    overflows: int = 0
    interruptions: int = 0
    dropped_records: int = 0

    # ------------------------------------------------------------------ coverage marks

    def start_coverage(self, at_ns: int, reason: str = WARMUP) -> None:
        """Begin (or restart) continuous coverage at `at_ns`."""
        self.coverage_since_ns = at_ns
        self.coverage_reason = reason

    def interrupt(self, at_ns: int, reason: str) -> None:
        """Coverage is broken as of now. Retained trades stay: they were still observed."""
        self.interruptions += 1
        self.coverage_since_ns = at_ns
        self.coverage_reason = reason

    def lose_coverage(self, reason: str) -> None:
        """No coverage at all until a connection comes back."""
        self.interruptions += 1
        self.coverage_since_ns = None
        self.coverage_reason = reason

    # ------------------------------------------------------------------ tape

    def add(self, trade: Trade) -> None:
        self.tape.append((trade.mono_ns, trade.aggressor, trade.qty, trade.notional))
        if len(self.tape) > self.max_records:
            # The ceiling is a safety bound, not a working size. Reaching it means something is
            # badly wrong upstream, so coverage is invalidated rather than quietly trimmed.
            while len(self.tape) > self.max_records:
                self.tape.popleft()
                self.dropped_records += 1
            self.overflows += 1
            self.interrupt(trade.mono_ns, INTERRUPTION_OVERFLOW)

    def prune(self, at_ns: int) -> None:
        """Drop everything older than the longest window. Called once per sample."""
        horizon = at_ns - max(self.windows_s) * 1_000_000_000
        while self.tape and self.tape[0][0] <= horizon:
            self.tape.popleft()

    # ------------------------------------------------------------------ view

    def view(self, *, at_ns: int, connected: bool, age_ms: int | None,
             stale_ms: int = TRADE_STALE_MS) -> dict[str, Any]:
        """One payload per window, keyed by `"5s"`, `"15s"`, `"60s"`."""
        self.prune(at_ns)
        # A stream that has never delivered a trade is not stale, it is warming up; silence only
        # becomes staleness once something has arrived to be silent after.
        stale = age_ms is not None and age_ms > stale_ms
        usable = connected and not stale and self.coverage_since_ns is not None

        out: dict[str, Any] = {}
        for window in self.windows_s:
            cutoff = at_ns - window * 1_000_000_000
            buy_qty = sell_qty = buy_notional = sell_notional = Decimal(0)
            trades = 0
            for mono_ns, aggressor, qty, notional in self.tape:
                if mono_ns <= cutoff:
                    continue
                trades += 1
                if aggressor == BUY:
                    buy_qty += qty
                    buy_notional += notional
                else:
                    sell_qty += qty
                    sell_notional += notional

            if not usable:
                coverage = UNKNOWN
            elif self.coverage_since_ns > cutoff:  # type: ignore[operator]
                coverage = PARTIAL
            else:
                coverage = COMPLETE

            out[f"{window}s"] = _window_view(
                coverage=coverage, buy_qty=buy_qty, sell_qty=sell_qty,
                buy_notional=buy_notional, sell_notional=sell_notional, trades=trades,
                coverage_age_ms=None if self.coverage_since_ns is None
                else max(0, (at_ns - self.coverage_since_ns) // 1_000_000),
                reason=self.coverage_reason)
        return out

    def counters(self) -> dict[str, int | str | None]:
        return {
            "retained_records": len(self.tape),
            "interruptions": self.interruptions,
            "overflows": self.overflows,
            "dropped_records": self.dropped_records,
            "coverage_reason": self.coverage_reason,
            "has_coverage": self.coverage_since_ns is not None,
        }


def _window_view(*, coverage: str, buy_qty: Decimal, sell_qty: Decimal, buy_notional: Decimal,
                 sell_notional: Decimal, trades: int, coverage_age_ms: int | None,
                 reason: str) -> dict[str, Any]:
    canonical = coverage == COMPLETE
    observed = coverage in (COMPLETE, PARTIAL)

    def value(amount: Decimal) -> str | None:
        return decimal_out(amount) if canonical else None

    def seen(amount: Decimal) -> str | None:
        return decimal_out(amount) if observed else None

    return {
        "coverage": coverage,
        "coverage_reason": reason if coverage != COMPLETE else None,
        "coverage_age_ms": coverage_age_ms,
        "trades": trades if observed else None,
        "buy_btc": value(buy_qty),
        "sell_btc": value(sell_qty),
        "buy_usdt": value(buy_notional),
        "sell_usdt": value(sell_notional),
        "net_btc": value(buy_qty - sell_qty),
        "net_usdt": value(buy_notional - sell_notional),
        "observed_buy_btc": seen(buy_qty),
        "observed_sell_btc": seen(sell_qty),
        "observed_buy_usdt": seen(buy_notional),
        "observed_sell_usdt": seen(sell_notional),
        "observed_is_lower_bound": coverage == PARTIAL,
        # Imbalance is a ratio of two observed sides, so it needs COMPLETE and a live denominator.
        "imbalance_btc": ratio_out(buy_qty - sell_qty, buy_qty + sell_qty, RATIO_DECIMALS)
        if canonical else None,
        "imbalance_usdt": ratio_out(buy_notional - sell_notional, buy_notional + sell_notional,
                                    RATIO_DECIMALS) if canonical else None,
    }


__all__ = ["FlowWindows", "WARMUP", "INTERRUPTION_RECONNECT", "INTERRUPTION_ID_JUMP",
           "INTERRUPTION_STALE", "INTERRUPTION_OVERFLOW", "INTERRUPTION_OUT_OF_ORDER"]
