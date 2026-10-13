"""Flow states, setups and invalidation timing (contract sections 3 to 7). No prices.

Index convention: position `t` is the boundary at the close of minute `t`; a state at `t` uses
minutes `t-N+1 .. t` only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import Volumes

NONE = np.int8(9)                     # undefined state; BUY=+1, SELL=-1, EVEN=0
HORIZONS = (("M", 10), ("H1", 60), ("H4", 240), ("D", 1440))
MAX_HOLD_MIN = 48 * 60
FAMILIES = ("F1", "F2", "F3", "UNCLASSIFIED_EVEN")
INVALIDATIONS = ("I1", "I2", "I3", "I4")


def lean(vol: Volumes, minutes: int) -> np.ndarray:
    """Production `horizon_view` lean on every boundary, exactly (integer sums)."""
    net = 2 * vol.taker - vol.quote                     # buy - sell per minute
    total = vol.quote
    csum_net = np.concatenate(([0], np.cumsum(net)))
    csum_tot = np.concatenate(([0], np.cumsum(total)))
    csum_bad = np.concatenate(([0], np.cumsum(~vol.valid)))
    if csum_tot[-1] >= 2 ** 62 or np.abs(csum_net).max() >= 2 ** 62:
        raise OverflowError("window sums leave int64 headroom")
    n = len(net)
    out = np.full(n, NONE, dtype=np.int8)
    if n < minutes:
        return out
    hi = np.arange(minutes, n + 1)
    lo = hi - minutes
    w_net = csum_net[hi] - csum_net[lo]
    w_tot = csum_tot[hi] - csum_tot[lo]
    w_bad = csum_bad[hi] - csum_bad[lo]
    s = np.sign(w_net).astype(np.int8)
    s[(w_bad > 0) | (w_tot == 0)] = NONE
    out[minutes - 1:] = s
    return out


def all_states(vol: Volumes) -> dict[str, np.ndarray]:
    return {name: lean(vol, minutes) for name, minutes in HORIZONS}


def big_direction(st: dict[str, np.ndarray]) -> np.ndarray:
    """+1/-1 when D == H4 and non-zero, else 0 (NO_TRADE)."""
    d, h4 = st["D"], st["H4"]
    ok = (d == h4) & ((d == 1) | (d == -1))
    return np.where(ok, d, 0).astype(np.int8)


def regime_ids(bigdir: np.ndarray) -> np.ndarray:
    change = np.concatenate(([True], bigdir[1:] != bigdir[:-1]))
    return np.cumsum(change)


def _prefix(mask: np.ndarray) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(mask)))


@dataclass(frozen=True)
class Setup:
    t: int
    direction: int
    family: str


def detect_setups(st: dict[str, np.ndarray]) -> list[Setup]:
    m, h1, h4, d = st["M"], st["H1"], st["H4"], st["D"]
    defined = (m != NONE) & (h1 != NONE) & (h4 != NONE) & (d != NONE)
    if not defined.any():
        return []
    first = int(np.argmax(defined))
    out: list[Setup] = []
    for dirn in (1, -1):
        full = (m == dirn) & (h1 == dirn) & (h4 == dirn) & (d == dirn)
        big_off = _prefix((d != dirn) | (h4 != dirn))
        h1_off = _prefix(h1 != dirn)
        h1_opp = _prefix(h1 == -dirn)
        m_opp = _prefix(m == -dirn)
        onset = np.flatnonzero(full & ~np.concatenate(([False], full[:-1])))
        full_idx = np.flatnonzero(full)
        for t in onset:
            if t < first:
                continue
            k = np.searchsorted(full_idx, t) - 1          # last full minute before t
            g0 = max(first, int(full_idx[k]) + 1) if k >= 0 else first
            g1 = int(t)                                    # G = [g0, g1)
            if g1 <= g0:
                fam = "F1"
            elif big_off[g1] - big_off[g0] > 0:
                fam = "F1"
            elif h1_off[g1] - h1_off[g0] > 0:
                fam = "F3" if h1_opp[g1] - h1_opp[g0] > 0 else "UNCLASSIFIED_EVEN"
            else:
                fam = "F2" if m_opp[g1] - m_opp[g0] > 0 else "UNCLASSIFIED_EVEN"
            out.append(Setup(t=int(t), direction=dirn, family=fam))
    out.sort(key=lambda s: (s.t, s.direction))
    return out


def invalidation_masks(st: dict[str, np.ndarray], dirn: int) -> dict[str, np.ndarray]:
    h1, h4, d = st["H1"], st["H4"], st["D"]
    return {
        "I1": h1 == -dirn,
        "I2": h4 == -dirn,
        "I3": (h1 == -dirn) & (h4 == -dirn),
        "I4": ~((d == dirn) & (h4 == dirn)),
    }


def next_true(mask: np.ndarray) -> np.ndarray:
    """next_true[i] = smallest j >= i with mask[j], or len(mask) if none."""
    n = len(mask)
    idx = np.where(mask, np.arange(n), n)
    return np.minimum.accumulate(idx[::-1])[::-1]


def dedup_primary(setups: list[Setup], regime: np.ndarray) -> list[bool]:
    """Same family + direction + regime and < 48 h after the last accepted one -> duplicate."""
    last: dict[tuple[str, int], int] = {}
    keep = []
    for s in setups:
        key = (s.family, s.direction)
        prev = last.get(key)
        dup = prev is not None and regime[prev] == regime[s.t] and s.t - prev < MAX_HOLD_MIN
        keep.append(not dup)
        if not dup:
            last[key] = s.t
    return keep


def dedup_one_position(setups: list[Setup]) -> list[bool]:
    """Across everything: skip a setup within 48 h of the last accepted one."""
    last = None
    keep = []
    for s in setups:
        ok = last is None or s.t - last >= MAX_HOLD_MIN
        keep.append(ok)
        if ok:
            last = s.t
    return keep


# --------------------------------------------------------------------------- V2 (contract V2)

DAY_MIN, H4_MIN, H1_MIN = 1440, 240, 60


def block_lean(vol: Volumes, block_minutes: int) -> np.ndarray:
    """V2-A: lean of the most recent completed UTC-aligned block at every boundary.

    Boundary t closes minute t (end = ts[t] + 1 min). The latest completed block ends at
    floor(end / B) * B. The series must start on a block edge (TRAIN starts at a UTC midnight).
    """
    b_ms = block_minutes * 60_000
    if int(vol.ts[0]) % b_ms:
        raise ValueError("series must start on a block boundary")
    n = len(vol.ts)
    nb = n // block_minutes                              # complete blocks in the series
    m = nb * block_minutes
    net = (2 * vol.taker[:m] - vol.quote[:m]).reshape(nb, block_minutes).sum(axis=1)
    tot = vol.quote[:m].reshape(nb, block_minutes).sum(axis=1)
    bad = (~vol.valid[:m]).reshape(nb, block_minutes).any(axis=1)
    blk = np.sign(net).astype(np.int8)
    blk[bad | (tot == 0)] = NONE
    # boundary t -> number of completed blocks = (t + 1) // B ; latest completed = that - 1
    done = (np.arange(n) + 1) // block_minutes
    out = np.full(n, NONE, dtype=np.int8)
    has = done >= 1
    out[has] = blk[done[has] - 1]
    return out


def states_v2a(vol: Volumes) -> dict[str, np.ndarray]:
    return {"M": lean(vol, 10), "H1": block_lean(vol, H1_MIN), "H4": block_lean(vol, H4_MIN),
            "D": block_lean(vol, DAY_MIN)}


def persistent(raw: np.ndarray, hold: int) -> np.ndarray:
    """V2-B: confirm a non-zero raw value once it has held `hold` consecutive minutes; keep the
    confirmed value until the opposite value is confirmed. NONE before the first confirmation."""
    n = len(raw)
    out = np.full(n, NONE, dtype=np.int8)
    cur = NONE
    run_val, run_len = None, 0
    for t in range(n):
        v = raw[t]
        if v == run_val:
            run_len += 1
        else:
            run_val, run_len = v, 1
        if (v == 1 or v == -1) and run_len >= hold:
            cur = v
        out[t] = cur
    return out


def states_v2b(vol: Volumes) -> dict[str, np.ndarray]:
    return {"M": lean(vol, 10), "H1": lean(vol, 60),
            "H4": persistent(lean(vol, 240), H1_MIN), "D": persistent(lean(vol, 1440), H4_MIN)}


def dedup_family_48h(setups: list[Setup]) -> list[bool]:
    """Per family, LONG and SHORT merged: skip a setup within 48 h of the family's last accepted."""
    last: dict[str, int] = {}
    keep = []
    for s in setups:
        prev = last.get(s.family)
        ok = prev is None or s.t - prev >= MAX_HOLD_MIN
        keep.append(ok)
        if ok:
            last[s.family] = s.t
    return keep


def run_lengths(s: np.ndarray, values=(1, -1)) -> np.ndarray:
    """Lengths (minutes) of maximal runs whose value is in `values`; runs touching either end of
    the series are dropped (their true length is unknown)."""
    if len(s) == 0:
        return np.zeros(0, dtype=np.int64)
    edges = np.flatnonzero(s[1:] != s[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(s)]))
    keep = np.isin(s[starts], values) & (starts > 0) & (ends < len(s))
    return (ends - starts)[keep]
