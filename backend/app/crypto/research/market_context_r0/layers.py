"""The two analysis frames: T3 at one minute over 33 days, T2 at one second over 3.61 hours.

Both frames are built once and then only read. Nothing in here looks at an outcome; the split
exists so that the episode and family code of `families.py` can be written against arrays rather
than against two different journal shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import data, journal, structure

MINUTE_MS = 60_000
RT_COST_BP = 11.25
MEANINGFUL_BP = 22.5
WALL_PROX_BPS_LO = 1.0
WALL_PROX_BPS_HI = 10.0
LEVEL_TOL_PCT = 0.25


# ----------------------------------------------------------------------------- T3, one minute
@dataclass
class MinuteFrame:
    """Layer A plus Layer C long, on the 1m grid, with the bar arrays the outcomes need."""

    rows: list[dict]
    ts: np.ndarray            # the full series grid, not only the replay part
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    imb_1m: np.ndarray
    imb_3m: np.ndarray
    index_of: dict[int, int] = field(default_factory=dict)
    dom_quantile: float = 0.0
    warmup_rows: int = 0

    def row_index(self, ts_ms: int) -> int:
        return self.index_of[ts_ms]


def _trailing_imbalance(buy: np.ndarray, sell: np.ndarray, window: int) -> np.ndarray:
    """Sum the sides over the trailing window, then divide. NaN until the window is full."""
    csb, css = np.cumsum(buy), np.cumsum(sell)
    out = np.full(len(buy), np.nan)
    for i in range(window - 1, len(buy)):
        lo = i - window
        b = csb[i] - (csb[lo] if lo >= 0 else 0.0)
        s = css[i] - (css[lo] if lo >= 0 else 0.0)
        tot = b + s
        if tot > 0:
            out[i] = (b - s) / tot
    return out


def build_minute_frame() -> MinuteFrame:
    eng = structure.run(data.REPLAY_START_MS, data.SERIES_END_MS)
    bars = data.load()["1m"]
    flow = data.load_flow()
    imb1 = _trailing_imbalance(flow.buy_base, flow.sell_base, 1)
    imb3 = _trailing_imbalance(flow.buy_base, flow.sell_base, 3)
    idx = {int(t): i for i, t in enumerate(bars.ts)}
    warm = (bars.ts >= data.WARMUP_START_MS) & (bars.ts < data.REPLAY_START_MS)
    held = imb3[warm]
    held = held[np.isfinite(held)]
    q = float(np.percentile(np.abs(held), 80))
    for r in eng.rows:
        i = idx[r["ts"]]
        r["flow_imb_1m"] = None if not np.isfinite(imb1[i]) else round(float(imb1[i]), 6)
        r["flow_imb_3m"] = None if not np.isfinite(imb3[i]) else round(float(imb3[i]), 6)
    return MinuteFrame(rows=eng.rows, ts=bars.ts, high=bars.high, low=bars.low, close=bars.close,
                       imb_1m=imb1, imb_3m=imb3, index_of=idx, dom_quantile=round(q, 6),
                       warmup_rows=int(held.size))


# ----------------------------------------------------------------------------- T2, one second
@dataclass
class SecondSample:
    ms: int
    session8: str
    mid: float | None
    ok: bool
    flow60_imb: float | None
    flow60_complete: bool
    band_bid: float | None
    band_ask: float | None
    ask_bins: dict[str, dict]
    bid_bins: dict[str, dict]


@dataclass
class SecondFrame:
    samples: list[SecondSample]
    mids: np.ndarray
    ms: np.ndarray
    session_of: np.ndarray

    def index_after(self, i: int, delta_ms: int) -> int | None:
        """The last sample at or before `ms[i] + delta_ms`, inside the same session.

        A horizon that runs past the end of a session has no value rather than a stale one.
        """
        target = self.ms[i] + delta_ms
        j = int(np.searchsorted(self.ms, target, side="right")) - 1
        if j <= i or j >= len(self.ms):
            return None
        if self.session_of[j] != self.session_of[i]:
            return None
        if self.ms[j] < target - 5_000:
            return None
        return j


def build_second_frame() -> SecondFrame:
    samples: list[SecondSample] = []
    sess_ids: list[int] = []
    seen: dict[str, int] = {}
    for s in journal.replay():
        if s.session8 not in seen:
            seen[s.session8] = len(seen)
        f60 = s.flow["60s"]
        samples.append(SecondSample(
            ms=s.ms, session8=s.session8, mid=s.mid,
            ok=(s.book_state == "SYNCED" and s.band01_coverage == "COMPLETE" and s.mid is not None),
            flow60_imb=f60["imbalance_usdt"], flow60_complete=(f60["coverage"] == "COMPLETE"),
            band_bid=s.band01_bid_notional, band_ask=s.band01_ask_notional,
            ask_bins={w["bin_low"]: w for w in s.walls_ask},
            bid_bins={w["bin_low"]: w for w in s.walls_bid}))
        sess_ids.append(seen[s.session8])
    return SecondFrame(samples=samples,
                       mids=np.asarray([s.mid if s.mid is not None else np.nan for s in samples]),
                       ms=np.asarray([s.ms for s in samples], dtype=np.int64),
                       session_of=np.asarray(sess_ids, dtype=np.int32))
