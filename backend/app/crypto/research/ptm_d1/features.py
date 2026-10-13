"""Price/volatility features for PTM-D1 (contract section 2). Boundary t = close of minute t.

Everything is built from completed UTC days (no look-ahead) or from close[t]. NaN marks undefined.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ptm_r1 import data, v3
from ..ptm_r1.r2_train import run_starts
from ..ptm_r1.states import NONE

DAY = 1440
V_DAYS = 7
VR_DAYS = 30
DISP_DAYS = 3
Z_REGIME = 1.0
PB_DEEP = 1.0


@dataclass(frozen=True)
class Features:
    st: dict[str, np.ndarray]      # M, H1, H4, D (V3 arrays)
    V: np.ndarray                  # volatility unit (fraction of price), NaN undefined
    z3: np.ndarray                 # 3-day displacement in V units
    PR: np.ndarray                 # int8 price regime +1/-1/0, NONE undefined
    VR: np.ndarray                 # int8 1 HIGH, 0 LOW, NONE undefined
    pbz: np.ndarray                # pullback depth in V units within the D episode (NaN undefined)
    d_start: np.ndarray            # start index of the current D run
    episode: np.ndarray            # D run id
    fund: np.ndarray               # latest settled funding rate (NaN before the first)
    basis: np.ndarray              # mark/index - 1

    @property
    def thesis_age(self) -> np.ndarray:
        return np.arange(len(self.V)) - self.d_start + 1


def day_arrays(px: data.Prices) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(px.open) // DAY * DAY
    h = px.high[:n].reshape(-1, DAY).max(axis=1)
    lo = px.low[:n].reshape(-1, DAY).min(axis=1)
    c = px.close[:n].reshape(-1, DAY)[:, -1]
    return h, lo, c


def vol_unit_by_day(tr: np.ndarray, days: int = V_DAYS) -> np.ndarray:
    """Vday[k] = median TR of completed days k-days .. k-1 (k = number of completed days)."""
    out = np.full(len(tr) + 1, np.nan)
    for k in range(days, len(tr) + 1):
        out[k] = np.median(tr[k - days:k])
    return out


def vol_regime_by_day(vday: np.ndarray, days: int = VR_DAYS) -> np.ndarray:
    out = np.full(len(vday), NONE, dtype=np.int8)
    for k in range(len(vday)):
        win = vday[k - days:k] if k >= days else np.array([])
        if len(win) == days and not np.isnan(win).any() and not np.isnan(vday[k]):
            out[k] = 1 if vday[k] > np.median(win) else 0
    return out


def pullback_z(px: data.Prices, d: np.ndarray, V: np.ndarray) -> np.ndarray:
    """Within each D run: running extreme of high (D=+1) / low (D=-1) since the run start."""
    n = len(d)
    out = np.full(n, np.nan)
    edges = np.flatnonzero(d[1:] != d[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [n]))
    for s, e in zip(starts, ends):
        dirn = d[s]
        if dirn == 1:
            ext = np.maximum.accumulate(px.high[s:e])
            out[s:e] = (ext - px.close[s:e]) / px.close[s:e] / V[s:e]
        elif dirn == -1:
            ext = np.minimum.accumulate(px.low[s:e])
            out[s:e] = (px.close[s:e] - ext) / px.close[s:e] / V[s:e]
    return out


def build(vol, px: data.Prices, funding: list[tuple[int, float]], basis: np.ndarray) -> Features:
    st = v3.states_v3(vol)
    n = len(px.open)
    h, lo, c = day_arrays(px)
    tr = (h - lo) / c
    vday = vol_unit_by_day(tr)
    vrday = vol_regime_by_day(vday)
    k = (np.arange(n) + 1) // DAY                        # completed days at boundary t
    V = vday[k]
    VR = vrday[k]
    z3 = np.full(n, np.nan)
    ok = (k >= DISP_DAYS) & ~np.isnan(V)
    z3[ok] = (px.close[ok] / c[k[ok] - DISP_DAYS] - 1.0) / V[ok]
    PR = np.full(n, NONE, dtype=np.int8)
    PR[ok] = 0
    PR[ok & (z3 >= Z_REGIME)] = 1
    PR[ok & (z3 <= -Z_REGIME)] = -1
    d = st["D"]
    pbz = pullback_z(px, d, V)
    fund = np.full(n, np.nan)
    if funding:
        f_idx = np.array([(t - data.TRAIN_START_MS) // data.MINUTE_MS for t, _ in funding])
        f_rate = np.array([r for _, r in funding])
        pos = np.searchsorted(f_idx, np.arange(n), side="right") - 1
        fund[pos >= 0] = f_rate[pos[pos >= 0]]
    return Features(st=st, V=V, z3=z3, PR=PR, VR=VR, pbz=pbz, d_start=run_starts(d),
                    episode=v3.thesis_episode_ids(d), fund=fund, basis=basis)


def defined(f: Features) -> np.ndarray:
    st = f.st
    return ((st["D"] != NONE) & (st["H4"] != NONE) & (st["H1"] != NONE) & (st["M"] != NONE)
            & ~np.isnan(f.V) & ~np.isnan(f.z3) & (f.VR != NONE))


def no_trade(f: Features) -> np.ndarray:
    """Contract section 10: undefined, D even, or PR conflicting with D."""
    d = f.st["D"]
    conflict = (f.PR != NONE) & (f.PR == -d) & (d != 0)
    return ~defined(f) | (d == 0) | conflict
