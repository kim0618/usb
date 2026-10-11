"""Layer PRICE: the R1 curated level book, read on a live, forward-only anchored series.

Nothing about the curation happens here. The R1 engine, the R1 level book, the R1 retest pool, the
R0 swings/VWAP/ATR modules and the thetas frozen by R0's held-out calibration are imported and
called; the per-minute display projection is Market Context R0's `ContextEngine`, which was written
for exactly this and already carries the previous-session context block outside the level book.

What this module adds is the two things a live panel needs and a study does not:

**A fixed anchor.** The engine is a single pass over a bar series, so the panel re-runs it as
minutes close. The series start is chosen once and never moved. A rolling window would delete
history off the back, which changes level lifecycles and identities - repaint under another name.
With a fixed anchor every run is a prefix extension of the same series, and R1's prefix-truncation
invariance (6/6 in R1, re-verified in Market Context R0) means the minutes already published do not
move. `prefix_check` makes that a measurement rather than a claim.

**The last closed minute only.** A 1m bar is knowable at its close and not one instant earlier, so
the newest published minute trails real time by up to a minute. The payload says so and carries the
age, instead of smoothing it over with a partial bar.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..research.market_context_r0.structure import ContextEngine
from ..research.price_structure_r0.data import Bars, aggregate, wilder_atr
from ..research.price_structure_r1.engine import EngineConfig
from . import contract as C
from . import venue as VN


def floor_day(ms: int) -> int:
    return ms - ms % C.DAY_MS


def last_closed_open_ms(now_ms: int) -> int:
    """Open time of the newest bar whose close has already happened."""
    return (now_ms - now_ms % C.MINUTE_MS) - C.MINUTE_MS


def default_anchor_ms(now_ms: int, anchor_days: int = C.ANCHOR_DAYS) -> int:
    """`anchor_days` back, floored to a UTC day.

    Day alignment is not cosmetic: `aggregate` refuses a series that does not start on the
    boundary of the timeframe it is folding to, and the previous-session context block is keyed on
    UTC day starts.
    """
    return floor_day(now_ms - anchor_days * C.DAY_MS)


def build_bars(rows: list[dict[str, Any]]) -> dict[str, Bars]:
    """`Bars` for 1m/5m/1h out of kline rows, using R0's own construction.

    The gap check is R0's and is kept strict. A hole in the 1m grid would silently shift every
    higher-timeframe fold, so a short series is an error the panel shows rather than a series it
    quietly repairs.
    """
    if len(rows) < 2:
        raise VN.VenueError(f"SERIES_HAS_TOO_FEW_BARS:{len(rows)}")
    ts = np.asarray([row["timestamp_ms"] for row in rows], dtype=np.int64)
    if not np.array_equal(ts, np.arange(ts[0], ts[-1] + C.MINUTE_MS, C.MINUTE_MS)):
        raise VN.VenueError("SERIES_GRID_HAS_A_GAP")
    column = lambda key: np.asarray([float(row[key]) for row in rows], dtype=float)
    o, h, l = column("open"), column("high"), column("low")
    c, v, q = column("close"), column("volume"), column("turnover")
    b1 = Bars("1m", C.MINUTE_MS, ts, o, h, l, c, v, q, wilder_atr(h, l, c))
    return {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}


def run_engine(bars: dict[str, Bars], publish_from_ms: int, publish_to_ms: int) -> ContextEngine:
    """R1's engine with the frozen thetas, publishing the requested window."""
    engine = ContextEngine(bars, EngineConfig(C.THETA_1H, C.THETA_5M,
                                              publish_from_ms, publish_to_ms))
    engine.run()
    return engine


@dataclass
class StructureRun:
    """One completed pass: the published rows and everything needed to audit them."""

    anchor_ms: int
    series_first_ms: int
    series_last_open_ms: int
    series_rows: int
    publish_from_ms: int
    publish_to_ms: int
    rows: list[dict[str, Any]]
    computed_at_ms: int
    recompute_ms: int
    repaint_violations: int
    vanished_levels: int
    fetch_ms: int = 0
    pages_fetched: int = 0

    @property
    def latest(self) -> dict[str, Any] | None:
        return self.rows[-1] if self.rows else None

    def row_at(self, info_ts: int) -> dict[str, Any] | None:
        for row in reversed(self.rows):
            if row["info_ts"] == info_ts:
                return row
        return None


class Series:
    """The anchored kline series, extended forward and never trimmed.

    The row list is the series. `extend` asks the venue only for the minutes it does not have, so
    the steady-state cost of a minute is one small page; the expensive call is the first one.
    """

    def __init__(self, symbol: str, *, anchor_ms: int) -> None:
        self.symbol = symbol
        self.anchor_ms = anchor_ms
        self.rows: list[dict[str, Any]] = []
        self.fetches = 0

    @property
    def last_open_ms(self) -> int | None:
        return None if not self.rows else int(self.rows[-1]["timestamp_ms"])

    def extend(self, now_ms: int, *, opener: Any = None) -> int:
        """Fetch every closed minute this series is missing. Returns how many were added."""
        target = last_closed_open_ms(now_ms)
        if target < self.anchor_ms:
            raise VN.VenueError("ANCHOR_IS_IN_THE_FUTURE")
        have = self.last_open_ms
        start = self.anchor_ms if have is None else have + C.MINUTE_MS
        if start > target:
            return 0
        fetched = VN.klines(self.symbol, start, target + C.MINUTE_MS, opener=opener)
        self.fetches += 1
        if not fetched:
            return 0
        if have is not None and int(fetched[0]["timestamp_ms"]) != have + C.MINUTE_MS:
            raise VN.VenueError("SERIES_TAIL_DOES_NOT_JOIN")
        self.rows.extend(fetched)
        return len(fetched)

    def run(self, *, publish_window_ms: int = C.PUBLISH_WINDOW_MS,
            now_ms: int | None = None) -> StructureRun:
        """Re-run the engine over the whole anchored series and keep the published window."""
        started = time.monotonic()
        bars = build_bars(self.rows)
        last_open = int(self.rows[-1]["timestamp_ms"])
        publish_to = last_open + C.MINUTE_MS
        publish_from = max(int(self.rows[0]["timestamp_ms"]) + C.MINUTE_MS,
                           publish_to - publish_window_ms)
        engine = run_engine(bars, publish_from, publish_to)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return StructureRun(
            anchor_ms=self.anchor_ms, series_first_ms=int(self.rows[0]["timestamp_ms"]),
            series_last_open_ms=last_open, series_rows=len(self.rows),
            publish_from_ms=publish_from, publish_to_ms=publish_to, rows=engine.rows,
            computed_at_ms=int(time.time() * 1000) if now_ms is None else now_ms,
            recompute_ms=elapsed_ms, repaint_violations=len(engine.audit.violations),
            vanished_levels=len(engine.audit.vanished))


def prefix_check(rows: list[dict[str, Any]], *, cut: int,
                 publish_window_ms: int = C.PUBLISH_WINDOW_MS) -> dict[str, Any]:
    """Run the series twice - truncated and whole - and compare the minutes they share.

    This is the property that makes a fixed anchor worth the cost: if the overlap is identical then
    extending the series forward cannot move a line that is already on the screen. Reported with
    the count of compared minutes, because an empty overlap would pass vacuously.
    """
    if cut <= 0 or cut >= len(rows):
        raise ValueError("cut must be inside the series")
    short = Series("", anchor_ms=int(rows[0]["timestamp_ms"]))
    short.rows = list(rows[:-cut])
    whole = Series("", anchor_ms=int(rows[0]["timestamp_ms"]))
    whole.rows = list(rows)
    a = short.run(publish_window_ms=publish_window_ms)
    b = whole.run(publish_window_ms=publish_window_ms)
    index = {row["info_ts"]: row for row in b.rows}
    compared = 0
    differences: list[dict[str, Any]] = []
    for row in a.rows:
        other = index.get(row["info_ts"])
        if other is None:
            continue
        compared += 1
        if row != other:
            differing = sorted(key for key in set(row) | set(other)
                               if row.get(key) != other.get(key))
            differences.append({"info_ts": row["info_ts"], "fields": differing})
    return {"cut_minutes": cut, "compared_minutes": compared,
            "identical": not differences, "differences": differences[:5],
            "truncated_rows": len(a.rows), "whole_rows": len(b.rows)}


# --------------------------------------------------------------------------- display projection


def _level(row: dict[str, Any], tag: str, side: str) -> dict[str, Any]:
    """One nearest level, as the panel renders it. `None` price means the book has none."""
    price = row.get(f"{tag}_price")
    return {
        "side": side,
        "price": price,
        "distance_pct": row.get(f"{tag}_distance_pct"),
        "lifecycle": row.get(f"{tag}_lifecycle"),
        "origin_kind": row.get(f"{tag}_origin"),
        "strength": row.get(f"{tag}_strength"),
        "touches": row.get(f"{tag}_touches"),
        "confirm_tf": row.get(f"{tag}_confirm_tf"),
        "sources": row.get(f"{tag}_sources"),
        "level_id": row.get(f"{tag}_id"),
        "available": price is not None,
        "unavailable_reason": None if price is not None else "NO_PUBLISHED_LEVEL_ON_THIS_SIDE",
    }


def unavailable_view(symbol: str, reason: str) -> dict[str, Any]:
    """What PRICE returns for an instrument the curation was never calibrated on."""
    return {"state": C.UNAVAILABLE, "reasons": [reason], "symbol": symbol,
            "series": None, "row": None, "levels": None, "events": None, "range": None,
            "prev_session": None, "note": C.STRUCTURE_SERIES_NOTE}


def error_view(symbol: str, reason: str) -> dict[str, Any]:
    """What PRICE returns when the layer exists but could not be read."""
    view = unavailable_view(symbol, reason)
    view["state"] = C.UNKNOWN
    return view


def structure_view(run: StructureRun | None, *, symbol: str, now_ms: int,
                   reasons: list[str] | None = None) -> dict[str, Any]:
    """The PRICE layer payload for one poll."""
    if symbol != C.BTC:
        return unavailable_view(symbol, C.REASON_NOT_CALIBRATED)
    if run is None or run.latest is None:
        return error_view(symbol, (reasons or ["NO_SERIES_YET"])[0])
    row = run.latest
    bar_close_ms = int(row["info_ts"])
    age_ms = max(0, now_ms - bar_close_ms)
    state = C.LIVE if age_ms <= C.MINUTE_MS + C.STRUCTURE_GRACE_MS else C.STALE
    why: list[str] = list(reasons or [])
    if state == C.STALE:
        why.append("LAST_CLOSED_BAR_OLDER_THAN_GRACE")
    if run.repaint_violations or run.vanished_levels:
        # Not downgraded to UNKNOWN: the figures are still what the engine published. But a
        # repaint is the one thing this layer promises never to do, so it is on the screen.
        why.append("REPAINT_AUDIT_NOT_CLEAN")
    return {
        "state": state,
        "reasons": why,
        "symbol": symbol,
        "series": {
            "anchor_ms": run.anchor_ms,
            "series_first_ms": run.series_first_ms,
            "series_last_open_ms": run.series_last_open_ms,
            "series_rows": run.series_rows,
            "publish_from_ms": run.publish_from_ms,
            "publish_to_ms": run.publish_to_ms,
            "published_minutes": len(run.rows),
            "computed_at_ms": run.computed_at_ms,
            "recompute_ms": run.recompute_ms,
            "fetch_ms": run.fetch_ms,
            "anchor_rule": "FIXED_AT_PROCESS_START_EXTENDED_FORWARD_ONLY",
            "theta_1h": C.THETA_1H,
            "theta_5m": C.THETA_5M,
            "repaint_violations": run.repaint_violations,
            "vanished_levels": run.vanished_levels,
            "venue": "binance_usdm",
        },
        "row": {
            "bar_open_ms": int(row["ts"]),
            "bar_close_ms": bar_close_ms,
            "bar_age_ms": age_ms,
            "close": row["close"],
            "trend_structure": row["trend_structure"],
            "last_high_label": row["last_high_label"],
            "last_low_label": row["last_low_label"],
            "active_level_count": row["active_level_count"],
            "published_prices": row["published_prices"],
            "atr_5m": row["atr_5m"],
            "atr_1h": row["atr_1h"],
            "vwap_z": row["vwap_z"],
            "vwap_position": row["vwap_position"],
        },
        "levels": {
            "resistance": _level(row, "res", "RESISTANCE"),
            "support": _level(row, "sup", "SUPPORT"),
        },
        "events": {
            "breakout_state": ("NONE" if row["last_breakout_direction"] is None
                               else row["last_breakout_direction"]),
            "breakout_level_id": row["last_breakout_level_id"],
            "breakout_at_ms": row["last_breakout_at"],
            "breakout_age_min": row["last_breakout_age_min"],
            "breakout_confirm_tf": row["last_breakout_confirm_tf"],
            "retest_state": row["retest_state"],
            "retest_direction": row["retest_direction"],
            "retest_level_id": row["retest_level_id"],
            "failed_break_state": row["failed_break_state"],
            "sweep_direction": row["last_sweep_direction"],
            "sweep_at_ms": row["last_sweep_at"],
            "sweep_age_min": row["last_sweep_age_min"],
            "sweep_level_id": row["last_sweep_level_id"],
        },
        "range": {
            "state": row["range_state"],
            "high": row["range_high"],
            "low": row["range_low"],
            "position": row["range_position"],
        },
        "prev_session": {
            "high": row["prev_session_high"],
            "low": row["prev_session_low"],
            "high_distance_pct": row["prev_session_high_distance_pct"],
            "low_distance_pct": row["prev_session_low_distance_pct"],
            "in_level_book": False,
            "note": C.PREV_SESSION_NOTE,
        },
        "note": C.STRUCTURE_SERIES_NOTE,
    }


__all__ = ["Series", "StructureRun", "build_bars", "run_engine", "prefix_check", "floor_day",
           "last_closed_open_ms", "default_anchor_ms", "structure_view", "unavailable_view",
           "error_view"]
