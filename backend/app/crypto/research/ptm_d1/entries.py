"""PTM-D1 entry families (contract section 5). Onsets are boundary indices; entry is open[t+1]."""
from __future__ import annotations

import numpy as np

from ..ptm_r1.states import NONE
from . import features as F

FAMILIES = ("E1", "E2", "E3", "E4", "E5")
NAMES = {"E1": "TREND_ALIGN", "E2": "PULLBACK_REALIGN", "E3": "DEEP_PULLBACK_RETURN",
         "E4": "FRESH_TURN", "E5": "VOL_EXPANSION_ALIGN"}
CENSOR_MIN = 7200


def _onset(mask: np.ndarray) -> np.ndarray:
    return mask & ~np.concatenate(([False], mask[:-1]))


def _since_run_start(flag: np.ndarray, d_start: np.ndarray) -> np.ndarray:
    """True at t when `flag` was True at some boundary in [d_start[t], t)  (strictly before t)."""
    n = len(flag)
    c = np.concatenate(([0], np.cumsum(flag)))
    return (c[np.arange(n)] - c[d_start]) > 0


def onsets(f: F.Features, fam: str) -> list[tuple[int, int]]:
    """(t, dir) for every onset of the family, NO-TRADE applied at t, sorted."""
    st = f.st
    m, h1, h4, d = st["M"], st["H1"], st["H4"], st["D"]
    tradable = ~F.no_trade(f)
    out: list[tuple[int, int]] = []
    for dirn in (1, -1):
        big = d == dirn
        if fam == "E1":
            sig = big & (f.PR == dirn) & (h4 == dirn) & _onset(h1 == dirn)
        elif fam == "E2":
            was_opp = _since_run_start(big & (h4 == -dirn), f.d_start)
            sig = big & _onset(h4 == dirn) & was_opp
        elif fam == "E3":
            deep = _since_run_start(big & (f.pbz >= F.PB_DEEP), f.d_start)
            sig = big & deep & _onset(h1 == dirn)
        elif fam == "E4":
            prev = np.concatenate(([NONE], d[:-1]))
            sig = big & (prev == -dirn) & (h4 == dirn)
        elif fam == "E5":
            full = big & (h4 == dirn) & (h1 == dirn) & (m == dirn)
            sig = (f.VR == 1) & (f.PR == dirn) & _onset(full)
        else:
            raise ValueError(fam)
        for t in np.flatnonzero(sig & tradable):
            out.append((int(t), dirn))
    out.sort()
    return out


def one_position_count(ons: list[tuple[int, int]], f: F.Features, last: int,
                       hold: int = 2880) -> int:
    """State-only upper bound used in the census: onsets at least `hold` apart."""
    kept, prev = 0, None
    for t, _ in ons:
        if t + 1 + hold > last:
            break
        if prev is None or t - prev >= hold:
            kept += 1
            prev = t
    return kept
