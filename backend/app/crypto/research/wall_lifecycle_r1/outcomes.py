"""Outcomes on the 1 s mid path (CONTRACT_WALL_LIFECYCLE_R1 section 5) and the external series
(ATR, OI, funding, basis), all read at or before an instant. numpy arrays, bisect lookups."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import contract as K
from .replay import ASK, Path

MIN_MS = 60_000


@dataclass
class Series:
    """The path as arrays plus the external series."""

    t: np.ndarray            # sample_ms
    mid: np.ndarray
    buy60: np.ndarray
    sell60: np.ndarray
    gap_before: np.ndarray   # bool
    kline_close_ms: list[int]
    atr14: list[float]       # ATR14 usable from kline_close_ms[i]
    oi_t: list[int]          # bucket timestamp + 5 min (usable from)
    oi_value: list[float]
    oi_change_1h: list[float | None]
    funding_t: list[int]
    funding_rate: list[float]
    basis_t: list[int]       # premium bar close ms
    basis_bp: list[float]

    @classmethod
    def build(cls, path: Path, klines: list, oi: list, funding: list, premium: list) -> "Series":
        t = np.asarray(path.sample_ms, dtype=np.int64)
        mid = np.asarray(path.mid, dtype=np.float64)
        closes, atr = [], []
        trs: list[float] = []
        prev = None
        for row in klines:
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
            trs.append(tr)
            prev = c
            if len(trs) >= K.ATR_BARS:
                closes.append(int(row[6]) + 1 if len(row) > 6 else int(row[0]) + MIN_MS)
                atr.append(float(np.mean(trs[-K.ATR_BARS:])))
        oi_t, oi_v, oi_ch = [], [], []
        for i, row in enumerate(oi):
            ts = int(row["timestamp"])
            value = float(row["sumOpenInterestValue"])
            oi_t.append(ts + 300_000)
            oi_v.append(value)
            back = [r for r in oi[:i] if int(r["timestamp"]) <= ts - 3_600_000]
            oi_ch.append((value / float(back[-1]["sumOpenInterestValue"]) - 1) * 100 if back else None)
        f_t = [int(r["fundingTime"]) for r in funding]
        f_r = [float(r["fundingRate"]) for r in funding]
        b_t = [int(row[6]) + 1 if len(row) > 6 else int(row[0]) + MIN_MS for row in premium]
        b_v = [float(row[4]) * 1e4 for row in premium]   # premium index (fraction) -> bp
        return cls(t=t, mid=mid, buy60=np.asarray(path.buy60), sell60=np.asarray(path.sell60),
                   gap_before=np.asarray(path.gap_before, dtype=bool), kline_close_ms=closes,
                   atr14=atr, oi_t=oi_t, oi_value=oi_v, oi_change_1h=oi_ch, funding_t=f_t,
                   funding_rate=f_r, basis_t=b_t, basis_bp=b_v)

    # ---------------------------------------------------------------- lookups at or before t

    def index_at(self, ms: int) -> int | None:
        """Index of the last sample at or before `ms`, or None."""
        i = int(np.searchsorted(self.t, ms, side="right")) - 1
        return i if i >= 0 else None

    def mid_at(self, ms: int) -> float | None:
        i = self.index_at(ms)
        if i is None or math.isnan(self.mid[i]):
            return None
        return float(self.mid[i])

    def flow_side(self, ms: int) -> str | None:
        """BUY / SELL by the larger 60 s share, sign only; None without a reading or at 50/50."""
        i = self.index_at(ms)
        if i is None:
            return None
        b, s = self.buy60[i], self.sell60[i]
        if math.isnan(b) or math.isnan(s) or b == s:
            return None
        return "BUY" if b > s else "SELL"

    def atr_at(self, ms: int) -> float | None:
        i = bisect.bisect_right(self.kline_close_ms, ms) - 1
        return self.atr14[i] if i >= 0 else None

    def oi_at(self, ms: int) -> tuple[float | None, float | None]:
        i = bisect.bisect_right(self.oi_t, ms) - 1
        return (self.oi_value[i], self.oi_change_1h[i]) if i >= 0 else (None, None)

    def funding_at(self, ms: int) -> float | None:
        i = bisect.bisect_right(self.funding_t, ms) - 1
        return self.funding_rate[i] if i >= 0 else None

    def basis_at(self, ms: int) -> float | None:
        i = bisect.bisect_right(self.basis_t, ms) - 1
        return self.basis_bp[i] if i >= 0 else None

    def funding_inside(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        """Funding settlements strictly inside (start, end)."""
        lo = bisect.bisect_right(self.funding_t, start_ms)
        hi = bisect.bisect_left(self.funding_t, end_ms)
        return [(self.funding_t[i], self.funding_rate[i]) for i in range(lo, hi)]

    def range_before(self, ms: int, window_ms: int) -> float | None:
        lo = int(np.searchsorted(self.t, ms - window_ms, side="left"))
        hi = int(np.searchsorted(self.t, ms, side="right"))
        seg = self.mid[lo:hi]
        seg = seg[~np.isnan(seg)]
        return float(seg.max() - seg.min()) if seg.size else None

    # ---------------------------------------------------------------- windows

    def window(self, start_ms: int, horizon_ms: int) -> tuple[int, int] | None:
        """[i0, i1] sample indices covering (start, start + horizon], or None when the window runs
        past the data or holds a gap wider than WINDOW_GAP_MS."""
        i0 = self.index_at(start_ms)
        if i0 is None:
            return None
        end = start_ms + horizon_ms
        i1 = int(np.searchsorted(self.t, end, side="right")) - 1
        if i1 <= i0 or self.t[-1] < end:
            return None
        steps = np.diff(self.t[i0:i1 + 1])
        if steps.size and steps.max() > K.WINDOW_GAP_MS:
            return None
        return i0, i1

    def return_bp(self, start_ms: int, horizon_ms: int, sign: int) -> float | None:
        w = self.window(start_ms, horizon_ms)
        if w is None:
            return None
        a, b = self.mid[w[0]], self.mid[w[1]]
        if math.isnan(a) or math.isnan(b):
            return None
        return sign * (b - a) / a * 1e4

    def excursions(self, start_ms: int, horizon_ms: int, sign: int) -> tuple[float, float] | None:
        """(MFE, MAE) in bp in direction `sign` over the window."""
        w = self.window(start_ms, horizon_ms)
        if w is None:
            return None
        a = self.mid[w[0]]
        seg = self.mid[w[0]:w[1] + 1]
        seg = seg[~np.isnan(seg)]
        if math.isnan(a) or not seg.size:
            return None
        moves = sign * (seg - a) / a * 1e4
        return float(moves.max()), float(-moves.min())

    def first_reach(self, start_ms: int, horizon_ms: int, level: float, direction: int,
                    inclusive: bool = True) -> int | None:
        """First sample ms in (start, start+horizon] where the mid reaches `level` moving in
        `direction` (+1: mid >= level, -1: mid <= level). None if never or the window is unusable."""
        w = self.window(start_ms, horizon_ms)
        if w is None:
            return None
        seg = self.mid[w[0] + 1:w[1] + 1]
        if direction > 0:
            hit = seg >= level if inclusive else seg > level
        else:
            hit = seg <= level if inclusive else seg < level
        idx = np.flatnonzero(hit)
        return int(self.t[w[0] + 1 + idx[0]]) if idx.size else None

    def first_flow_flip(self, start_ms: int, horizon_ms: int, against: str) -> int | None:
        """First sample ms after start where the 60 s flow side equals `against`."""
        w = self.window(start_ms, horizon_ms)
        if w is None:
            return None
        b = self.buy60[w[0] + 1:w[1] + 1]
        s = self.sell60[w[0] + 1:w[1] + 1]
        side = np.where(b > s, 1, np.where(s > b, -1, 0))
        want = 1 if against == "BUY" else -1
        idx = np.flatnonzero(side == want)
        return int(self.t[w[0] + 1 + idx[0]]) if idx.size else None


def rejection_sign(side: str) -> int:
    """+1 when 'away from the wall' is up (BID), -1 when it is down (ASK)."""
    return -1 if side == ASK else 1


def classify_reaction(series: Series, side: str, t_c: int, near: float, far: float) -> dict[str, Any]:
    """Continuation (far edge crossed within 15 min) vs reversal (>= MEANINGFUL_BP away from the
    near edge before that) vs neither."""
    h = K.PRIMARY_H_MIN * MIN_MS
    sign = rejection_sign(side)
    cont = series.first_reach(t_c, h, far, -sign, inclusive=False)
    target = near * (1 + sign * K.MEANINGFUL_BP / 1e4)
    rev = series.first_reach(t_c, h, target, sign)
    if cont is not None and (rev is None or cont < rev):
        return {"reaction": "CONTINUATION", "t": cont}
    if rev is not None:
        return {"reaction": "REVERSAL", "t": rev}
    if series.window(t_c, h) is None:
        return {"reaction": None, "t": None}
    return {"reaction": "NEITHER", "t": None}


__all__ = ["Series", "rejection_sign", "classify_reaction", "MIN_MS"]
