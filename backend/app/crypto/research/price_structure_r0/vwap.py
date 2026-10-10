"""Session anchored VWAP and relative position (contract section 12).

``vwap`` is the exact traded VWAP, sum(turnover) / sum(volume), because turnover is quote volume.
``sigma`` is the volume weighted dispersion of the typical price, exactly as the contract writes it:
sqrt(max(0, E_v[tp^2] - E_v[tp]^2)). The two use different price definitions on purpose.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DAY_MS = 86_400_000


@dataclass
class SessionVwap:
    day: int = -1
    v: float = 0.0
    q: float = 0.0
    vp: float = 0.0
    vp2: float = 0.0
    vwap: float = float("nan")
    sigma: float = float("nan")

    def on_1m(self, ts: int, high: float, low: float, close: float, volume: float,
              turnover: float) -> None:
        day = ts // DAY_MS
        if day != self.day:
            self.day, self.v, self.q, self.vp, self.vp2 = day, 0.0, 0.0, 0.0, 0.0
        tp = (high + low + close) / 3.0
        self.v += volume
        self.q += turnover
        self.vp += volume * tp
        self.vp2 += volume * tp * tp
        if self.v > 0:
            self.vwap = self.q / self.v
            mean_tp = self.vp / self.v
            self.sigma = math.sqrt(max(0.0, self.vp2 / self.v - mean_tp * mean_tp))
        else:
            self.vwap = self.sigma = float("nan")

    def position(self, close: float) -> tuple[float, str]:
        if not (self.vwap == self.vwap) or not (self.sigma == self.sigma) or self.sigma <= 0:
            return 0.0, "AT"
        z = (close - self.vwap) / self.sigma
        if z >= 2:
            return z, "ABOVE_UPPER"
        if z > 0:
            return z, "ABOVE"
        if z == 0:
            return z, "AT"
        if z > -2:
            return z, "BELOW"
        return z, "BELOW_LOWER"
