"""Timeframe structure (contract section 2): the unmodified R1 engine on each timeframe's own bars.

Time scaling: a timeframe's bars are handed to R1 as its "1m" series on a synthetic one-minute
clock, so R1's internal 5m and 1h layers become 5x and 60x the timeframe. 1m runs natively. The
published nearest resistance / support at a bar's close is whatever R1's own `Engine.snapshot`
returns there; this module only records it, so parity with R1 holds by construction (and is tested).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.crypto.market_context_v1 import contract as MC
from app.crypto.research.price_structure_r0.data import Bars, aggregate as r0_aggregate, wilder_atr
from app.crypto.research.price_structure_r1.engine import Engine, EngineConfig

from .bars import MINUTE_MS, WARMUP_BARS, Series

#: Synthetic clock origin for time-scaled runs: 2020-09-13T00:00Z, aligned to the hour and the day,
#: so R1's 5m / 1h aggregations and its UTC-day logic see whole groups.
SYN_T0_MS = 1_599_955_200_000


@dataclass
class StructureRun:
    tf: str
    ts: np.ndarray            # real bar open, ms
    known_ms: np.ndarray      # real time the row is known (bar close)
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    atr: np.ndarray           # ATR(14) of this timeframe's bars, known at the bar's close
    res: np.ndarray           # nearest published resistance at the close, NaN if none
    sup: np.ndarray
    res_id: np.ndarray        # R1 level id, -1 if none
    sup_id: np.ndarray
    published: np.ndarray     # bool: row is inside the publish window (after warm-up)
    levels: list              # every published level price at the close (R1's published set)
    violations: int
    vanished: int

    def __len__(self) -> int:
        return len(self.ts)


class _Recorder(Engine):
    """R1's engine, recording the nearest published levels of every snapshot it takes."""

    def __init__(self, bars: dict[str, Bars], cfg: EngineConfig) -> None:
        Engine.__init__(self, bars, cfg)
        n = len(bars["1m"])
        self.rec_res = np.full(n, np.nan)
        self.rec_sup = np.full(n, np.nan)
        self.rec_res_id = np.full(n, -1, dtype=np.int64)
        self.rec_sup_id = np.full(n, -1, dtype=np.int64)
        self.rec_pub = np.zeros(n, dtype=bool)
        self.rec_levels: list = [()] * n

    def snapshot(self, i: int, at_ms: int) -> dict:
        snap = Engine.snapshot(self, i, at_ms)
        nr, ns = snap["nearest_resistance"], snap["nearest_support"]
        if nr:
            self.rec_res[i] = nr["price"]
            self.rec_res_id[i] = nr["id"]
        if ns:
            self.rec_sup[i] = ns["price"]
            self.rec_sup_id[i] = ns["id"]
        self.rec_pub[i] = True
        self.rec_levels[i] = tuple(l["price"] for l in snap["published_levels"])
        # Keep run-wide memory flat on long series: R1 keeps one full snapshot per (synthetic) hour.
        self.hourly_snapshots.clear()
        return snap


def engine_bars(s: Series) -> dict[str, Bars]:
    """The series as R1's input. 1m keeps its own clock; any other timeframe gets the synthetic one."""
    n = len(s)
    if s.tf == "1m":
        ts = s.ts.astype(np.int64)
        if int(ts[0]) % 3_600_000:
            raise RuntimeError("1m series must start on an hour")
    else:
        ts = SYN_T0_MS + np.arange(n, dtype=np.int64) * MINUTE_MS
    b1 = Bars("1m", MINUTE_MS, ts, s.open, s.high, s.low, s.close, s.volume, s.turnover,
              wilder_atr(s.high, s.low, s.close))
    return {"1m": b1, "5m": r0_aggregate(b1, "5m", 5), "1h": r0_aggregate(b1, "1h", 60)}


def run(s: Series, *, warmup: int = WARMUP_BARS) -> StructureRun:
    bars = engine_bars(s)
    syn = bars["1m"].ts
    cfg = EngineConfig(MC.THETA_1H, MC.THETA_5M, int(syn[min(warmup, len(syn) - 1)]) + MINUTE_MS,
                       int(syn[-1]) + MINUTE_MS)
    eng = _Recorder(bars, cfg)
    eng.run()
    return StructureRun(
        tf=s.tf, ts=s.ts.copy(), known_ms=s.ts + s.step_ms, close=s.close.copy(),
        high=s.high.copy(), low=s.low.copy(), atr=wilder_atr(s.high, s.low, s.close),
        res=eng.rec_res, sup=eng.rec_sup, res_id=eng.rec_res_id, sup_id=eng.rec_sup_id,
        published=eng.rec_pub, levels=eng.rec_levels, violations=len(eng.audit.violations),
        vanished=len(eng.audit.vanished))
