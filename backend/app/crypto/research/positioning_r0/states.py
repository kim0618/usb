"""Hourly decision grid, positioning states, divergences and candidate flags (contract sections 3 and 5)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1.states import NONE, lean
from . import data as MD

HOUR_MIN = 60
OI_WINDOWS = {"O1": 1, "O2": 12, "O3": 48, "O4": 288}       # in 5 m slots
CANDIDATES = ("P1", "P2", "P3", "P4")
NAMES = {"P1": "NEW_POSITIONS_WITH_MOVE", "P2": "TOP_VS_CROWD", "P3": "FLOW_WITHOUT_OI",
         "P4": "TOP_ACCOUNT_VS_POSITION"}
REGIMES = ("A", "B", "C", "D")


def ratio_state(x: np.ndarray) -> np.ndarray:
    out = np.full(x.shape, NONE, dtype=np.int8)
    ok = ~np.isnan(x)
    out[ok & (x > 1)] = 1
    out[ok & (x < 1)] = -1
    out[ok & (x == 1)] = 0
    return out


def oi_change(oi: np.ndarray, k: int) -> np.ndarray:
    out = np.full(oi.shape, np.nan)
    out[k:] = oi[k:] / oi[:-k] - 1.0
    return out


@dataclass
class Hourly:
    h: np.ndarray            # hour number i (boundary H = TRAIN_START + i h), i >= 1
    j5: np.ndarray           # metrics slot used (stamped H - 5 min)
    mclose: np.ndarray       # minute index whose close is H
    O: dict                  # O1..O4 at H
    G: np.ndarray
    TA: np.ndarray
    TP: np.ndarray           # int8 ratio states
    price: np.ndarray        # int8 sign of 1 h return, NONE undefined
    ret1h: np.ndarray
    flow: np.ndarray         # int8 rolling 60 m lean
    oi_dir: np.ndarray       # int8 sign(O2), NONE undefined
    regime: np.ndarray       # 0..3 = A..D, -1 undefined
    raw: dict                # raw ratio values at H

    @property
    def n(self) -> int:
        return len(self.h)


def build_hourly(m: MD.Metrics, close: np.ndarray, vol) -> Hourly:
    n_min = len(close)
    nh = n_min // HOUR_MIN
    h = np.arange(1, nh)                                       # H must have a full previous hour
    j5 = h * 12 - 1
    mclose = h * HOUR_MIN - 1
    oi = m.arr["oi"]
    O = {k: oi_change(oi, w)[j5] for k, w in OI_WINDOWS.items()}
    st = {k: ratio_state(m.arr[f])[j5] for k, f in (("G", "g"), ("TA", "ta"), ("TP", "tp"))}
    ret = close[mclose] / close[mclose - HOUR_MIN] - 1.0
    price = np.where(np.isnan(ret), NONE, np.sign(ret)).astype(np.int8)
    flow = lean(vol, 60)[mclose]
    o2 = O["O2"]
    oi_dir = np.where(np.isnan(o2), NONE, np.sign(o2)).astype(np.int8)
    regime = np.full(len(h), -1, dtype=np.int8)
    regime[(price == 1) & (oi_dir == 1)] = 0
    regime[(price == 1) & (oi_dir == -1)] = 1
    regime[(price == -1) & (oi_dir == 1)] = 2
    regime[(price == -1) & (oi_dir == -1)] = 3
    raw = {k: m.arr[f][j5] for k, f in (("G", "g"), ("TA", "ta"), ("TP", "tp"), ("taker_ratio", "taker_ratio"),
                                        ("oi", "oi"), ("oi_value", "oi_value"))}
    return Hourly(h=h, j5=j5, mclose=mclose, O=O, G=st["G"], TA=st["TA"], TP=st["TP"], price=price, ret1h=ret,
                  flow=flow, oi_dir=oi_dir, regime=regime, raw=raw)


def conditions(hr: Hourly, cand: str) -> tuple[np.ndarray, np.ndarray]:
    """(long_condition, short_condition) per hour; undefined inputs give False."""
    if cand == "P1":
        return hr.regime == 0, hr.regime == 2
    if cand == "P2":
        return (hr.TP == 1) & (hr.G == -1), (hr.TP == -1) & (hr.G == 1)
    if cand == "P3":
        down = hr.oi_dir == -1
        return (hr.flow == -1) & down, (hr.flow == 1) & down
    if cand == "P4":
        return (hr.TP == 1) & (hr.TA == -1), (hr.TP == -1) & (hr.TA == 1)
    raise ValueError(cand)


def onsets(hr: Hourly, cand: str) -> list[tuple[int, int]]:
    """(row index into the hourly arrays, direction), sorted by time."""
    out = []
    for cond, dirn in zip(conditions(hr, cand), (1, -1)):
        prev = np.concatenate(([False], cond[:-1]))
        for k in np.flatnonzero(cond & ~prev):
            out.append((int(k), dirn))
    out.sort()
    return out


def dedup_4h(ev: list[tuple[int, int]]) -> list[tuple[int, int]]:
    last: dict[int, int] = {}
    out = []
    for k, d in ev:
        if d in last and k - last[d] < 4:
            continue
        last[d] = k
        out.append((k, d))
    return out


def overlap_48h(ev: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, last = [], None
    for k, d in ev:
        if last is None or k - last >= 48:
            out.append((k, d))
            last = k
    return out


def entry_minute(hr: Hourly, k: int) -> int:
    """Open of the 1 m bar starting at H + 1 min."""
    return int(hr.h[k]) * HOUR_MIN + 1
