"""Keep the 33-day C1 window between ticks instead of downloading it again every 20 seconds.

The full refresh (`MarketData.fetch` + `build_grid`) reads about 150 public pages and decodes
roughly 47,000 bars of each series on every tick, then builds a new grid next to the old one.
Nothing in that window older than a few hours changes between two ticks, so the work and, more to
the point, the transient memory it costs are almost entirely repeated. In a 200 MB service the
repeated allocation is what the allocator keeps afterwards.

This module produces the *same grid* the full refresh would have produced at the same instant,
with only the recent part fetched again. It does not reimplement the contract's arithmetic: every
row it emits comes out of `build_grid`, called on a short span, and the result is spliced into the
rows the previous tick already computed. The reasoning that makes the splice exact:

* **A grid row is causal and local.** Row `t` reads the bar at `t`, the 5m boundary
  `floor((t + 1m) / 5m) * 5m` and the one before it (the single carry), and the newest
  open-interest record known by `t + 1m`. So a row depends only on data in `[t - 15m, t]`, plus
  the as-of open interest.
* **The suffix is rebuilt.** The last `TAIL_REFETCH_MS` of every series is fetched again on every
  tick, and the rows from there on (and from any repaired gap on) are rebuilt by `build_grid` on a
  span that starts `SPLICE_PAD_MS` earlier, so their 5m carries and as-of joins see the same inputs.
* **The head is rebuilt.** When the window start moves forward, the first rows of a full refresh
  lose the bars before `start`, the spot before `start - 5m` and the open interest before
  `start - 2h`. Those rows are rebuilt the same way from exactly those inputs.
* **Gaps are fetched again, every tick,** as the full refresh would: a minute without a perpetual
  bar, a minute without a spot close, a hole in the 5m open-interest series. If a venue fills one
  late, the rows from there on are rebuilt.
* **Everything else is unchanged by construction,** under one assumption: a finished candle older
  than `TAIL_REFETCH_MS` is not revised by the venue. The full refresh would reflect such a
  revision and this does not; `audit_chunk` exists so that assumption is checked continuously
  rather than trusted, one slice of the window per tick.

When any of that cannot be honoured - first run, a start moving backwards, a long pause, more gaps
than `MAX_REPAIR_RANGES` - it falls back to the full refresh, which is the old behaviour.
"""
from __future__ import annotations

import bisect
from array import array
from typing import Any, Iterable, Sequence

from .contract import FIVE_MIN_MS, MINUTE_MS, OI_KNOWN_DELAY_MS
from .grid import Bar, Grid, build_grid

NAN = float("nan")
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
# How far back each tick reads every series again. Bars are final at their close, but a venue can
# publish the newest ones late and open interest arrives some minutes after its stamp; three hours
# covers both with a wide margin and is still a single page of each series.
TAIL_REFETCH_MS = 3 * HOUR_MS
# Rows rebuilt before the first row that has to change, so the rebuilt span's own head (missing
# its previous 5m boundary) is discarded rather than spliced in.
SPLICE_PAD_MS = 30 * MINUTE_MS
# Rows at the window start that a full refresh computes without the data before `start`.
HEAD_ROWS_MS = 15 * MINUTE_MS
# The spot window reaches back one 5m interval, the open interest two hours (see MarketData.fetch).
SPOT_LEAD_MS = FIVE_MIN_MS
OI_LEAD_MS = 2 * HOUR_MS
FUNDING_LEAD_MS = DAY_MS
# Beyond these the incremental path declines and the tick does a full refresh.
MAX_REPAIR_RANGES = 8
MAX_ADVANCE_MS = 6 * HOUR_MS
# The rotating audit re-reads this much of the older window per tick (one page of each series).
AUDIT_SPAN_MS = 1000 * MINUTE_MS

GRID_SERIES = ("opens", "highs", "lows", "closes", "s1", "oi", "perp_5m", "spot_5m", "oi_stamp")


def _nan(value: float) -> bool:
    return value != value


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, stop in sorted(ranges):
        if start >= stop:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def _gaps(present: Sequence[float], base_ms: int, lo_ms: int, hi_ms: int) -> list[tuple[int, int]]:
    """Runs of NaN in a per-minute array, as [from, to) instants, restricted to [lo, hi)."""
    out: list[tuple[int, int]] = []
    first = max(0, (lo_ms - base_ms) // MINUTE_MS)
    last = min(len(present), (hi_ms - base_ms) // MINUTE_MS)
    run = None
    for index in range(first, last):
        if _nan(present[index]):
            if run is None:
                run = index
        elif run is not None:
            out.append((base_ms + run * MINUTE_MS, base_ms + index * MINUTE_MS))
            run = None
    if run is not None:
        out.append((base_ms + run * MINUTE_MS, base_ms + last * MINUTE_MS))
    return out


class C1Window:
    """The raw series behind the current grid, and the grid itself.

    Perpetual OHLC is not stored twice: the grid's own `opens/highs/lows/closes` *are* the raw
    bars (NaN where the venue published none). Spot closes are one float per minute and open
    interest is two parallel lists, a few hundred kilobytes between them.
    """

    def __init__(self) -> None:
        self.grid: Grid | None = None
        self.funding: list[tuple[int, float]] = []
        self.start_ms = 0
        self.end_ms = 0
        self.spot_base_ms = 0
        self.spot = array("d")
        self.oi_stamps: list[int] = []
        self.oi_values: list[float] = []
        self.audit_cursor_ms: int | None = None
        # Counters, not decisions. They say how the window was kept, for the runtime's log.
        self.full_refreshes = 0
        self.incremental_refreshes = 0
        self.fallback_reasons: dict[str, int] = {}
        self.audit_mismatches = 0
        self.last_mode = ""

    # ------------------------------------------------------------------ full
    def full(self, market: Any, start_ms: int, end_ms: int, reason: str = "") -> Grid:
        """The old refresh, unchanged, and then remember its inputs."""
        data = market.fetch(start_ms, end_ms)
        grid = build_grid(data["bars"], data["spot"], data["open_interest"],
                          start_ms=data["start_ms"], end_ms=data["end_ms"] - MINUTE_MS)
        self.start_ms, self.end_ms = data["start_ms"], data["end_ms"]
        self.spot_base_ms = self.start_ms - SPOT_LEAD_MS
        self.spot = array("d", [NAN]) * ((self.end_ms - self.spot_base_ms) // MINUTE_MS)
        for ts, close in data["spot"]:
            self._set_spot(int(ts), float(close))
        oi = sorted((int(ts), float(value)) for ts, value in data["open_interest"])
        self.oi_stamps = [ts for ts, _ in oi]
        self.oi_values = [value for _, value in oi]
        self.funding = list(data["funding"])
        self.grid = grid
        self.full_refreshes += 1
        if reason and reason != "INITIAL":
            self.fallback_reasons[reason] = self.fallback_reasons.get(reason, 0) + 1
        self.last_mode = f"FULL:{reason or 'INITIAL'}"
        return grid

    def _set_spot(self, ts: int, close: float) -> None:
        index = (ts - self.spot_base_ms) // MINUTE_MS
        if 0 <= index < len(self.spot):
            self.spot[index] = close

    # ----------------------------------------------------------- incremental
    def refresh(self, market: Any, start_ms: int, end_ms: int) -> Grid:
        start_ms -= start_ms % MINUTE_MS
        end_ms -= end_ms % MINUTE_MS
        reason = self._decline(start_ms, end_ms, market)
        if reason:
            return self.full(market, start_ms, end_ms, reason)

        tail_ms = max(start_ms, self.end_ms - TAIL_REFETCH_MS)
        perp_ranges = [(tail_ms, end_ms)]
        spot_ranges = [(max(start_ms - SPOT_LEAD_MS, tail_ms - SPOT_LEAD_MS), end_ms)]
        oi_ranges = [(max(start_ms - OI_LEAD_MS, tail_ms - OI_LEAD_MS), end_ms)]
        grid = self.grid
        assert grid is not None
        perp_ranges += _gaps(grid.closes, grid.start_ms, start_ms, tail_ms)
        spot_ranges += _gaps(self.spot, self.spot_base_ms, start_ms - SPOT_LEAD_MS,
                             spot_ranges[0][0])
        oi_ranges += self._oi_gaps(start_ms - OI_LEAD_MS, oi_ranges[0][0])
        perp_ranges, spot_ranges, oi_ranges = (_merge_ranges(perp_ranges),
                                               _merge_ranges(spot_ranges),
                                               _merge_ranges(oi_ranges))
        if len(perp_ranges) + len(spot_ranges) + len(oi_ranges) - 3 > MAX_REPAIR_RANGES:
            return self.full(market, start_ms, end_ms, "TOO_MANY_GAPS")

        # Fetch everything first so a failed page leaves the window exactly as it was.
        perp_pages = [(lo, hi, market.perp_bars(lo, hi)) for lo, hi in perp_ranges]
        spot_pages = [(lo, hi, market.spot_closes(lo, hi)) for lo, hi in spot_ranges]
        oi_pages = [(lo, hi, market.open_interest(lo, hi)) for lo, hi in oi_ranges]
        funding = market.funding_rates(start_ms - FUNDING_LEAD_MS, end_ms + FUNDING_LEAD_MS)

        try:
            if any((int(ts) - start_ms) % MINUTE_MS for _, _, closes in spot_pages
                   for ts, _ in closes):
                # A close off the minute grid would be dropped here and kept by a full refresh.
                raise _Decline("SPOT_OFF_GRID")
            rows = self._slide_rows(grid, start_ms, end_ms)
            changed_ms = end_ms
            for lo, hi, bars in perp_pages:
                changed_ms = min(changed_ms, self._write_perp(rows, start_ms, lo, hi, bars))
            self._slide_spot(start_ms, end_ms)
            for lo, hi, closes in spot_pages:
                changed_ms = min(changed_ms, self._write_spot(lo, hi, closes))
            self._evict_oi(start_ms - OI_LEAD_MS)
            for lo, hi, records in oi_pages:
                changed_ms = min(changed_ms, self._write_oi(lo, hi, records))
            rebuild_from = min(changed_ms, tail_ms)
            rebuild_from -= (rebuild_from - start_ms) % MINUTE_MS
            self.grid = self._assemble(rows, start_ms, end_ms, rebuild_from)
        except _Decline as exc:
            return self.full(market, start_ms, end_ms, str(exc))
        except Exception:
            # The raw series may be half updated; the next tick must not build on them.
            self.grid = None
            raise
        self.start_ms, self.end_ms = start_ms, end_ms
        self.funding = list(funding)
        self.incremental_refreshes += 1
        self.last_mode = "INCREMENTAL"
        return self.grid

    def _decline(self, start_ms: int, end_ms: int, market: Any = None) -> str:
        if market is not None and not all(hasattr(market, name) for name in (
                "perp_bars", "spot_closes", "open_interest", "funding_rates")):
            return "MARKET_WITHOUT_SLICES"
        if self.grid is None or not len(self.grid):
            return "NO_WINDOW" if self.full_refreshes else "INITIAL"
        if start_ms < self.start_ms:
            return "START_MOVED_BACK"
        if end_ms < self.end_ms:
            return "END_MOVED_BACK"
        if end_ms - self.end_ms > MAX_ADVANCE_MS:
            return "LONG_PAUSE"
        if end_ms - start_ms < 2 * (TAIL_REFETCH_MS + SPLICE_PAD_MS):
            return "SHORT_WINDOW"
        return ""

    # ----------------------------------------------------------------- perp
    def _slide_rows(self, grid: Grid, start_ms: int, end_ms: int) -> dict[str, list]:
        """The previous grid's rows re-based on the new window: the head dropped, new minutes
        appended as holes. New lists, so the grid the API may be reading is never mutated."""
        drop = (start_ms - grid.start_ms) // MINUTE_MS
        extra = (end_ms - self.end_ms) // MINUTE_MS
        rows: dict[str, list] = {}
        for name in GRID_SERIES:
            kept = getattr(grid, name)[drop:]
            filler = -1 if name == "oi_stamp" else NAN
            rows[name] = kept + [filler] * extra
        return rows

    @staticmethod
    def _write_perp(rows: dict[str, list], start_ms: int, lo: int, hi: int,
                    bars: Sequence[Bar]) -> int:
        """Overwrite [lo, hi) with what the venue returned now; a minute it no longer returns is a
        hole, as it would be in a full refresh. Returns the first instant whose value changed."""
        size = len(rows["closes"])
        first = max(0, (lo - start_ms) // MINUTE_MS)
        last = min(size, (hi - start_ms) // MINUTE_MS)
        fresh: dict[int, Bar] = {}
        for bar in bars:
            if bar.confirmed and lo <= bar.ts_ms < hi:
                fresh[bar.ts_ms] = bar
        changed = None
        for index in range(first, last):
            ts = start_ms + index * MINUTE_MS
            bar = fresh.get(ts)
            new = (bar.open, bar.high, bar.low, bar.close) if bar else (NAN, NAN, NAN, NAN)
            for name, value in zip(("opens", "highs", "lows", "closes"), new):
                old = rows[name][index]
                if not (old == value or (_nan(old) and _nan(value))):
                    rows[name][index] = value
                    if changed is None:
                        changed = ts
        return changed if changed is not None else hi

    # ----------------------------------------------------------------- spot
    def _slide_spot(self, start_ms: int, end_ms: int) -> None:
        base = start_ms - SPOT_LEAD_MS
        drop = (base - self.spot_base_ms) // MINUTE_MS
        size = (end_ms - base) // MINUTE_MS
        kept = self.spot[drop:]
        if len(kept) < size:
            kept.extend([NAN] * (size - len(kept)))
        self.spot = kept[:size]
        self.spot_base_ms = base

    def _write_spot(self, lo: int, hi: int, closes: Iterable[tuple[int, float]]) -> int:
        fresh = {int(ts): float(close) for ts, close in closes if lo <= int(ts) < hi}
        changed = None
        first = max(0, (lo - self.spot_base_ms) // MINUTE_MS)
        last = min(len(self.spot), (hi - self.spot_base_ms) // MINUTE_MS)
        for index in range(first, last):
            ts = self.spot_base_ms + index * MINUTE_MS
            value = fresh.get(ts, NAN)
            old = self.spot[index]
            if not (old == value or (_nan(old) and _nan(value))):
                self.spot[index] = value
                if changed is None:
                    changed = ts
        return changed if changed is not None else hi

    # ---------------------------------------------------------- open interest
    def _oi_gaps(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """Where the 5m open-interest series is missing a record inside [lo, hi)."""
        out: list[tuple[int, int]] = []
        previous = lo - FIVE_MIN_MS
        start = bisect.bisect_left(self.oi_stamps, lo)
        for stamp in self.oi_stamps[start:]:
            if stamp >= hi:
                break
            if stamp - previous > FIVE_MIN_MS:
                out.append((previous + 1, stamp))
            previous = stamp
        if hi - previous > FIVE_MIN_MS:
            out.append((previous + 1, hi))
        return out

    def _evict_oi(self, keep_from: int) -> None:
        cut = bisect.bisect_left(self.oi_stamps, keep_from)
        if cut:
            del self.oi_stamps[:cut]
            del self.oi_values[:cut]

    def _write_oi(self, lo: int, hi: int, records: Iterable[tuple[int, float]]) -> int:
        fresh = sorted((int(ts), float(value)) for ts, value in records if lo <= int(ts) < hi)
        left = bisect.bisect_left(self.oi_stamps, lo)
        right = bisect.bisect_left(self.oi_stamps, hi)
        old = dict(zip(self.oi_stamps[left:right], self.oi_values[left:right]))
        new = dict(fresh)
        differing = [ts for ts in old.keys() | new.keys() if old.get(ts) != new.get(ts)]
        if not differing:
            return hi
        changed = min(differing)
        self.oi_stamps[left:right] = [ts for ts, _ in fresh]
        self.oi_values[left:right] = [value for _, value in fresh]
        # A record counts from one interval after its stamp; the row that first reads it closes
        # then, so it opened a minute earlier.
        return changed + OI_KNOWN_DELAY_MS - MINUTE_MS

    # ------------------------------------------------------------- assembly
    def _bars(self, rows: dict[str, list], start_ms: int, lo: int, hi: int) -> list[Bar]:
        out = []
        first = max(0, (lo - start_ms) // MINUTE_MS)
        last = min(len(rows["closes"]), (hi - start_ms) // MINUTE_MS)
        opens, highs, lows, closes = rows["opens"], rows["highs"], rows["lows"], rows["closes"]
        for index in range(first, last):
            if not _nan(closes[index]):
                out.append(Bar(ts_ms=start_ms + index * MINUTE_MS, open=opens[index],
                               high=highs[index], low=lows[index], close=closes[index]))
        return out

    def _spot_pairs(self, lo: int, hi: int) -> list[tuple[int, float]]:
        first = max(0, (lo - self.spot_base_ms) // MINUTE_MS)
        last = min(len(self.spot), (hi - self.spot_base_ms) // MINUTE_MS)
        return [(self.spot_base_ms + index * MINUTE_MS, self.spot[index])
                for index in range(first, last) if not _nan(self.spot[index])]

    def _oi_pairs(self, from_ms: int, hi: int) -> list[tuple[int, float]]:
        """Records the rows from `from_ms` on can read: everything known after it, plus the one
        record that was already current then, which is all an as-of join looks at."""
        known_by = from_ms + MINUTE_MS
        index = bisect.bisect_right(self.oi_stamps, known_by - OI_KNOWN_DELAY_MS)
        index = max(0, index - 1)
        right = bisect.bisect_left(self.oi_stamps, hi)
        return list(zip(self.oi_stamps[index:right], self.oi_values[index:right]))

    def _span(self, rows: dict[str, list], window_start: int, lo: int, last_row: int) -> Grid:
        """`build_grid` over rows [lo, last_row] with exactly the inputs a full refresh of the
        window starting at `window_start` would hand it for those rows."""
        spot_from = max(window_start - SPOT_LEAD_MS, lo - SPOT_LEAD_MS - FIVE_MIN_MS)
        if lo == window_start:
            oi = list(zip(self.oi_stamps, self.oi_values))
        else:
            oi = self._oi_pairs(lo, last_row + MINUTE_MS)
        return build_grid(self._bars(rows, window_start, lo, last_row + MINUTE_MS),
                          self._spot_pairs(spot_from, last_row + MINUTE_MS),
                          [(ts, value) for ts, value in oi if ts < last_row + MINUTE_MS],
                          start_ms=lo, end_ms=last_row)

    def _assemble(self, rows: dict[str, list], start_ms: int, end_ms: int,
                  rebuild_from_ms: int) -> Grid:
        last_row = end_ms - MINUTE_MS
        size = len(rows["closes"])
        # Head: the rows a full refresh computes without anything before the window start.
        head_end = start_ms + HEAD_ROWS_MS
        if self.oi_stamps:
            head_end = max(head_end, self.oi_stamps[0] + OI_KNOWN_DELAY_MS)
        else:
            head_end = end_ms
        tail_lo = max(start_ms, rebuild_from_ms - SPLICE_PAD_MS)
        tail_lo -= (tail_lo - start_ms) % FIVE_MIN_MS
        if head_end >= tail_lo:
            return self._span(rows, start_ms, start_ms, last_row)
        head = self._span(rows, start_ms, start_ms, head_end)
        tail = self._span(rows, start_ms, tail_lo, last_row)
        head_rows = (head_end - start_ms) // MINUTE_MS + 1
        cut = (rebuild_from_ms - start_ms) // MINUTE_MS
        tail_skip = (rebuild_from_ms - tail_lo) // MINUTE_MS
        grid = Grid(start_ms=start_ms)
        for name in GRID_SERIES:
            body = rows[name]
            spliced = getattr(head, name)[:head_rows] + body[head_rows:cut] \
                + getattr(tail, name)[tail_skip:]
            if len(spliced) != size:
                raise AssertionError(f"C1 window splice produced {len(spliced)} rows for {name}, "
                                     f"expected {size}")
            setattr(grid, name, spliced)
        return grid

    # ---------------------------------------------------------------- audit
    def audit_chunk(self, market: Any) -> dict[str, Any] | None:
        """Re-read one older slice of every series and compare it with what the window holds.

        The incremental refresh assumes data older than the refetched tail is never revised by the
        venue. This checks that assumption instead of trusting it: one `AUDIT_SPAN_MS` slice per
        call (one page of each series), cycling through the window. A difference drops the window,
        so the next tick is a full refresh - exactly what the old code would have done every tick.
        It never changes a signal on its own.
        """
        grid = self.grid
        if grid is None:
            return None
        lo_limit = self.start_ms
        hi_limit = self.end_ms - TAIL_REFETCH_MS
        if hi_limit - lo_limit < AUDIT_SPAN_MS:
            return None
        cursor = self.audit_cursor_ms
        if cursor is None or cursor < lo_limit or cursor >= hi_limit:
            cursor = lo_limit
        hi = min(hi_limit, cursor + AUDIT_SPAN_MS)
        bars = {bar.ts_ms: bar for bar in market.perp_bars(cursor, hi) if bar.confirmed}
        spot = {int(ts): float(close) for ts, close in market.spot_closes(cursor, hi)}
        oi = {int(ts): float(value) for ts, value in market.open_interest(cursor, hi)}
        if self.grid is not grid:
            return None
        mismatched = 0
        for index in range((cursor - grid.start_ms) // MINUTE_MS, (hi - grid.start_ms) // MINUTE_MS):
            ts = grid.start_ms + index * MINUTE_MS
            bar = bars.get(ts)
            held = (grid.opens[index], grid.highs[index], grid.lows[index], grid.closes[index])
            new = (bar.open, bar.high, bar.low, bar.close) if bar else (NAN, NAN, NAN, NAN)
            if any(not (a == b or (_nan(a) and _nan(b))) for a, b in zip(held, new)):
                mismatched += 1
            spot_index = (ts - self.spot_base_ms) // MINUTE_MS
            a, b = self.spot[spot_index], spot.get(ts, NAN)
            if not (a == b or (_nan(a) and _nan(b))):
                mismatched += 1
        left = bisect.bisect_left(self.oi_stamps, cursor)
        right = bisect.bisect_left(self.oi_stamps, hi)
        if dict(zip(self.oi_stamps[left:right], self.oi_values[left:right])) != oi:
            mismatched += 1
        self.audit_cursor_ms = hi
        if mismatched:
            self.audit_mismatches += mismatched
            self.grid = None
        return {"from_ms": cursor, "to_ms": hi, "mismatched": mismatched}

    def stats(self) -> dict[str, Any]:
        return {"mode": self.last_mode, "full": self.full_refreshes,
                "incremental": self.incremental_refreshes,
                "fallbacks": dict(self.fallback_reasons),
                "audit_mismatched": self.audit_mismatches,
                "spot_minutes": len(self.spot), "oi_records": len(self.oi_stamps)}


class _Decline(Exception):
    """The incremental path cannot reproduce the full refresh for this tick."""


