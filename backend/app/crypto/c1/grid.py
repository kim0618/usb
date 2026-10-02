"""The 1m decision grid the C1 conditions are read off.

One row per UTC minute, gap-free. Row i describes the Bybit bar that opens at `start_ms + i*60s`
and closes a minute later, and carries only what was already knowable at that close:

  s1   the spot basis of the last 5m boundary that had ended by then (contract section 2),
  oi   the most recent Bybit 5m open-interest record, counted as known one full interval after
       its own stamp.

A minute the venue never published stays in the grid as a hole rather than being dropped, because
every C1 input is positional: the volatility window is the last 1,440 rows and the open-interest
change is the row 60 back. Silently closing a gap would move both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .contract import FIVE_MIN_MS, MINUTE_MS, OI_KNOWN_DELAY_MS
from .features import (NAN, carry_one, five_min_boundary, five_min_closes, is_nan, log_returns,
                       spot_basis)


@dataclass(frozen=True)
class Bar:
    """One finalized 1m candle. `confirmed` is false for the minute still forming, which never
    enters the grid: the study decides at a bar's close."""
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    confirmed: bool = True


@dataclass
class Grid:
    start_ms: int
    opens: list[float] = field(default_factory=list)
    highs: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)
    s1: list[float] = field(default_factory=list)
    oi: list[float] = field(default_factory=list)
    perp_5m: list[float] = field(default_factory=list)
    spot_5m: list[float] = field(default_factory=list)
    # Stamp of the open-interest record each row is reading, so a caller can tell a fresh value
    # from one the as-of join has been carrying.
    oi_stamp: list[int] = field(default_factory=list)
    _returns: list[float] | None = field(default=None, repr=False, compare=False)

    def __len__(self) -> int:
        return len(self.closes)

    def ts(self, index: int) -> int:
        return self.start_ms + index * MINUTE_MS

    def index_of(self, ts_ms: int) -> int:
        return (ts_ms - self.start_ms) // MINUTE_MS

    def utc_day(self, index: int) -> int:
        return self.ts(index) // 86_400_000

    def has_bar(self, index: int) -> bool:
        return 0 <= index < len(self) and not is_nan(self.closes[index])

    def returns(self) -> list[float]:
        """1m log returns of the Bybit close, computed once and kept. A gap in the grid leaves two
        NaN returns - the one into the hole and the one out of it - which is what makes a missing
        minute suppress the volatility regime for the next 24 h rather than distort it."""
        if self._returns is None:
            self._returns = log_returns(self.closes)
        return self._returns


def build_grid(bars: Sequence[Bar], spot_bars: Sequence[Bar] | Sequence[tuple[int, float]],
               oi_records: Sequence[tuple[int, float]], *,
               start_ms: int | None = None, end_ms: int | None = None) -> Grid:
    """Assemble the grid from the three public series.

    `bars` are Bybit BTCUSDT perpetual 1m candles, `spot_bars` Binance BTCUSDT spot 1m candles
    (either Bar objects or (open_time, close) pairs) and `oi_records` Bybit 5m open interest as
    (stamp, value). Unconfirmed bars are ignored on both venues.
    """
    perp = {bar.ts_ms: bar for bar in bars if bar.confirmed}
    if not perp:
        return Grid(start_ms=start_ms or 0)
    spot: dict[int, float] = {}
    for row in spot_bars:
        if isinstance(row, Bar):
            if row.confirmed:
                spot[row.ts_ms] = row.close
        else:
            spot[int(row[0])] = float(row[1])

    first = min(perp) if start_ms is None else start_ms
    last = max(perp) if end_ms is None else end_ms
    first -= first % MINUTE_MS
    last -= last % MINUTE_MS
    count = (last - first) // MINUTE_MS + 1
    grid = Grid(start_ms=first)

    # The 5m boundary series, carried at most one bar, then turned into S1 - prices first and the
    # logarithm after, which is the order the study uses.
    boundary_first = five_min_boundary(first) - FIVE_MIN_MS
    boundary_last = five_min_boundary(last)
    boundaries = list(range(boundary_first, boundary_last + FIVE_MIN_MS, FIVE_MIN_MS))
    perp_by_boundary = five_min_closes((ts, bar.close) for ts, bar in perp.items())
    spot_by_boundary = five_min_closes(spot.items())
    perp_5m = carry_one([perp_by_boundary.get(boundary, NAN) for boundary in boundaries])
    spot_5m = carry_one([spot_by_boundary.get(boundary, NAN) for boundary in boundaries])
    basis = {boundary: spot_basis(perp_5m[position], spot_5m[position])
             for position, boundary in enumerate(boundaries)}
    perp_at = {boundary: perp_5m[position] for position, boundary in enumerate(boundaries)}
    spot_at = {boundary: spot_5m[position] for position, boundary in enumerate(boundaries)}

    oi_sorted = sorted((int(stamp) + OI_KNOWN_DELAY_MS, float(value)) for stamp, value in oi_records)

    cursor = 0
    latest_oi = NAN
    latest_stamp = -1
    for index in range(count):
        ts = first + index * MINUTE_MS
        bar = perp.get(ts)
        grid.opens.append(bar.open if bar else NAN)
        grid.highs.append(bar.high if bar else NAN)
        grid.lows.append(bar.low if bar else NAN)
        grid.closes.append(bar.close if bar else NAN)
        boundary = five_min_boundary(ts)
        grid.s1.append(basis.get(boundary, NAN))
        grid.perp_5m.append(perp_at.get(boundary, NAN))
        grid.spot_5m.append(spot_at.get(boundary, NAN))
        close_ms = ts + MINUTE_MS
        while cursor < len(oi_sorted) and oi_sorted[cursor][0] <= close_ms:
            latest_stamp, latest_oi = oi_sorted[cursor]
            cursor += 1
        grid.oi.append(latest_oi)
        # Back to the venue's own stamp: `oi_sorted` holds the instant the record became usable.
        grid.oi_stamp.append(-1 if latest_stamp < 0 else latest_stamp - OI_KNOWN_DELAY_MS)
    return grid


def bucket_window(grid: Grid, index: int) -> list[float] | None:
    """The S1 rows of the 30 whole UTC days before the day `index` falls in.

    Returns None when the grid does not reach back far enough, which is a different answer from
    "the window is too sparse to bucket": one means wait, the other means no bucket today.
    """
    from .contract import DAY_BARS, NORM_DAYS

    day_start_ms = (grid.ts(index) // 86_400_000) * 86_400_000
    window_start = grid.index_of(day_start_ms - NORM_DAYS * 86_400_000)
    window_stop = grid.index_of(day_start_ms)
    if window_start < 0 or window_stop > len(grid):
        return None
    if window_stop - window_start != NORM_DAYS * DAY_BARS:
        return None
    return grid.s1[window_start:window_stop]
