"""PTM-D1 trade simulator: event-driven exits (contract sections 6, 7), level fills as in R1.

A trade is (e, dir): entry at open[e]. Exits:
- level stops (catastrophic / fixed) fill at the level or the bar open if already beyond (adverse first);
- boundary events fire at the close of minute b and fill at open[b+1];
- path-dependent levels (adverse expansion, volatility trail) fill like level stops.
The earliest exit wins; a boundary event at open[k] precedes any intrabar fill in bar k.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..ptm_r1 import r1_train as R
from ..ptm_r1.states import NONE
from . import features as F

CENSOR_MIN = 7200
MMR = R.MMR
ARM_V = 1.0            # protection arming: MFE >= 1 V
TRAIL_V = 1.0          # P3 retrace from MFE
ATT_V = 0.5            # X4 recovery attempt / expansion size in V
X_RULES = ("X1", "X2", "X3", "X4", "X5")
P_RULES = ("P0", "P1", "P2", "P3", "P4")


def s_cat(lev: int) -> float:
    return 0.40 * (1.0 / lev - MMR)


@dataclass
class Ctx:
    f: F.Features
    mk: R.Market
    rel: dict[int, dict[str, np.ndarray]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        st = self.f.st
        for dirn in (1, -1):
            self.rel[dirn] = {
                "D_opp": st["D"] == -dirn, "H4_opp": st["H4"] == -dirn, "H4_agree": st["H4"] == dirn,
                "H1_opp": st["H1"] == -dirn, "H1_agree": st["H1"] == dirn, "M_opp": st["M"] == -dirn,
                "PR_opp": (self.f.PR != NONE) & (self.f.PR == -dirn),
            }

    @property
    def last(self) -> int:
        return self.mk.last


@dataclass
class Path:
    e: int
    dir: int
    end: int                 # censor bar (exclusive end of the window; exit at open[end] at the latest)
    p0: float
    r_open: np.ndarray       # bars e .. end-1 relative to p0 (signed by dir)
    r_close: np.ndarray
    r_adv: np.ndarray        # <= 0 side: dir*(adverse extreme/p0 - 1), negative when adverse
    r_fav: np.ndarray
    mfe_run: np.ndarray      # running max of r_fav (including the bar)
    V: float                 # volatility unit at the entry boundary

    @property
    def n(self) -> int:
        return self.end - self.e

    def b(self, k: int) -> int:
        """Boundary index of bar k (close of minute e+k)."""
        return self.e + k


def make_path(ctx: Ctx, e: int, dirn: int, censor: int = CENSOR_MIN) -> Path:
    px = ctx.mk.px
    end = min(e + censor, ctx.last)
    p0 = px.open[e]
    sl = slice(e, end)
    r_open = dirn * (px.open[sl] / p0 - 1.0)
    r_close = dirn * (px.close[sl] / p0 - 1.0)
    adv = px.low[sl] if dirn == 1 else px.high[sl]
    fav = px.high[sl] if dirn == 1 else px.low[sl]
    r_adv = dirn * (adv / p0 - 1.0)
    r_fav = dirn * (fav / p0 - 1.0)
    return Path(e=e, dir=dirn, end=end, p0=p0, r_open=r_open, r_close=r_close, r_adv=r_adv,
                r_fav=r_fav, mfe_run=np.maximum.accumulate(np.maximum(r_fav, 0.0)),
                V=float(ctx.f.V[e - 1]))


# --------------------------------------------------------------------------- exits (bar index k, fill)


def level_hit(p: Path, level: float) -> tuple[int, float] | None:
    hit = np.flatnonzero(p.r_adv <= -level)
    if len(hit) == 0:
        return None
    k = int(hit[0])
    return k, min(float(p.r_open[k]), -level)


def first_event(mask_from_e: np.ndarray) -> int | None:
    """mask over boundaries e..end-2 (an event at the last boundary cannot fill before the censor)."""
    idx = np.flatnonzero(mask_from_e[:-1]) if len(mask_from_e) > 1 else np.zeros(0, dtype=int)
    return int(idx[0]) + 1 if len(idx) else None          # exit bar k = boundary offset + 1


def _completion(p: Path, block: int) -> np.ndarray:
    return (np.arange(p.e, p.end) % block) == block - 1


def _window_count(flag: np.ndarray, width: int) -> np.ndarray:
    """count of flag over (k-width, k] for each k (bars before the window start are not counted)."""
    c = np.concatenate(([0], np.cumsum(flag)))
    idx = np.arange(len(flag)) + 1
    return c[idx] - c[np.maximum(idx - width, 0)]


def x_mask(ctx: Ctx, p: Path, rule: str) -> np.ndarray:
    """Boundary event mask (bars e..end-1) for a loss-exit rule. X1 is always part of X3..X5."""
    r = ctx.rel[p.dir]
    sl = slice(p.e, p.end)
    d_opp = r["D_opp"][sl]
    loss = p.r_close < 0
    if rule == "X1":
        return d_opp
    if rule == "X2":
        return d_opp & r["H4_opp"][sl] & _completion(p, 1440)
    if rule == "X3":
        h4c = _completion(p, 240)
        h4_opp = r["H4_opp"][sl]
        prev_opp = np.concatenate((np.zeros(240, dtype=bool), h4_opp[:-240])) if p.n > 240 else np.zeros(p.n, dtype=bool)
        # the previous completed block is the value 240 minutes earlier; it must lie inside the trade
        inside = np.arange(p.n) >= 240
        realign = _window_count(r["H1_agree"][sl] & _completion(p, 60), 240) > 0
        return d_opp | (loss & h4c & h4_opp & prev_opp & inside & ~realign)
    if rule == "X5":
        return d_opp | (loss & r["PR_opp"][sl])
    raise ValueError(rule)


def x4_expansion(p: Path) -> tuple[int, float] | None:
    """Adverse expansion after a failed recovery attempt (contract X4). Returns (k, fill)."""
    v = p.V * ATT_V
    k0 = 0
    n = p.n
    while k0 < n:
        seg_adv = p.r_adv[k0:]
        seg_fav = p.r_fav[k0:]
        runmin = np.minimum.accumulate(seg_adv)
        back = np.flatnonzero(seg_fav >= 0.0)                       # price back at entry -> reset
        prev_min = np.concatenate(([0.0], runmin[:-1]))
        att = np.flatnonzero((seg_fav - prev_min >= v) & (prev_min < 0) & (seg_fav < 0.0))
        if len(att) == 0:
            return None
        k1 = int(att[0])
        if len(back) and back[0] <= k1:
            k0 = k0 + int(back[0]) + 1
            continue
        A = -float(prev_min[k1])
        lvl = A + v
        exp_ = np.flatnonzero(seg_adv[k1 + 1:] <= -lvl)
        later_back = back[back > k1]
        if len(exp_) == 0:
            return None
        k2 = k1 + 1 + int(exp_[0])
        if len(later_back) and later_back[0] <= k2:
            k0 = k0 + int(later_back[0]) + 1
            continue
        k = k0 + k2
        return k, min(float(p.r_open[k]), -lvl)
    return None


def p_exit(ctx: Ctx, p: Path, rule: str) -> tuple[int, float] | None:
    """Profit protection exit (k, fill) or None. Armed once mfe_run >= ARM_V * V."""
    if rule == "P0":
        return None
    arm = p.mfe_run >= ARM_V * p.V
    if not arm.any():
        return None
    a0 = int(np.argmax(arm))
    r = ctx.rel[p.dir]
    sl = slice(p.e, p.end)
    if rule == "P3":
        prev_mfe = np.concatenate(([0.0], p.mfe_run[:-1]))
        armed_prev = prev_mfe >= ARM_V * p.V
        hit = np.flatnonzero(armed_prev & (p.r_adv <= prev_mfe - TRAIL_V * p.V))
        if len(hit) == 0:
            return None
        k = int(hit[0])
        return k, min(float(p.r_open[k]), float(prev_mfe[k] - TRAIL_V * p.V))
    h4c = np.flatnonzero(_completion(p, 240))
    h1c = np.flatnonzero(_completion(p, 60))
    h4_opp, h1_opp, h1_agree = r["H4_opp"][sl], r["H1_opp"][sl], r["H1_agree"][sl]
    if rule == "P1":
        for b2 in h4c[(h4c >= a0) & (h4c < p.n - 1)]:
            if not h4_opp[b2]:
                continue
            cand = h1c[(h1c >= a0) & (h1c < b2)]
            cand = cand[h1_opp[cand]]
            if len(cand) == 0:
                continue
            b1 = int(cand[-1])
            if p.mfe_run[b2] <= p.mfe_run[b1]:
                return int(b2) + 1, float(p.r_open[b2 + 1])
        return None
    if rule == "P2":
        for b in h4c[(h4c >= a0 + 480) & (h4c < p.n - 1)]:
            if h4_opp[b] and p.mfe_run[b] <= p.mfe_run[b - 480]:
                return int(b) + 1, float(p.r_open[b + 1])
        return None
    if rule == "P4":
        state, b_r = 0, -1                     # 0 wait H4 opp, 1 wait H1 realign, 2 wait H1 opp again
        pts = np.unique(np.concatenate((h4c, h1c)))
        for b in pts[(pts >= a0) & (pts < p.n - 1)]:
            if state == 0:
                if h4_opp[b]:
                    state = 1
            elif state == 1:
                if h1_agree[b]:
                    state, b_r = 2, int(b)
            else:
                if h1_opp[b]:
                    if p.mfe_run[b] <= p.mfe_run[b_r]:
                        return int(b) + 1, float(p.r_open[b + 1])
                    state = 1                  # new MFE since realignment: start over from H1 realign
        return None
    raise ValueError(rule)


# --------------------------------------------------------------------------- one trade


def simulate(ctx: Ctx, p: Path, x_rule: str | None, p_rule: str, stops: list[tuple[str, float]],
             fee_side: float = R.FEE_SIDE, spread_rt: float = R.BASE_RT) -> dict:
    """Earliest of: level stops, X rule, X4 expansion, protection, censor."""
    cands: list[tuple[int, float, str]] = []
    for name, lvl in sorted(stops, key=lambda s: s[1]):
        h = level_hit(p, lvl)
        if h:
            cands.append((h[0], h[1], name))
    if x_rule is not None:
        base = "X1" if x_rule == "X4" else x_rule
        k = first_event(x_mask(ctx, p, base))
        if k is not None:
            thesis = bool(ctx.rel[p.dir]["D_opp"][p.e + k - 1])
            cands.append((k, float(p.r_open[k]), "THESIS" if thesis else "LOSS_EXIT"))
        if x_rule == "X4":
            h = x4_expansion(p)
            if h:
                cands.append((h[0], h[1], "LOSS_EXIT"))
    h = p_exit(ctx, p, p_rule)
    if h:
        cands.append((h[0], h[1], "PROTECT"))
    if cands:
        # earliest bar; at the same bar an open fill (event) precedes an intrabar fill
        cands.sort(key=lambda c: (c[0], 0 if c[1] == p.r_open[c[0]] else 1))
        k, gross, kind = cands[0]
        exit_bar = p.e + k
        hold = k
    else:
        k, exit_bar, kind = p.n, p.end, "CENSOR"
        gross = float(p.dir * (ctx.mk.px.open[p.end] / p.p0 - 1.0))
        hold = p.n
    mfe = max(0.0, float(p.r_fav[:k].max()) if k > 0 else 0.0, gross)
    mae = max(0.0, float(-p.r_adv[:k].min()) if k > 0 else 0.0, -gross)
    ratio = 1.0 + p.dir * gross
    fee = fee_side * (1.0 + ratio)
    mk = ctx.mk
    a, b = np.searchsorted(mk.fund_idx, [p.e, exit_bar + 1])
    funding = float(np.sum(p.dir * mk.fund_rate[a:b] * mk.px.open[mk.fund_idx[a:b]] / p.p0))
    net = gross - fee - spread_rt - funding
    gb = mfe - gross
    return {"e": p.e, "dir": p.dir, "exit_bar": int(exit_bar), "kind": kind, "gross": gross,
            "fee": fee, "funding": funding, "net": net, "mfe": mfe, "mae": mae, "hold": int(hold),
            "giveback": gb, "giveback_ratio": (gb / mfe) if mfe > 0 else None,
            "mfe_v": mfe / p.V, "mae_v": mae / p.V,
            "recovered": bool(k > 0 and (p.r_adv[:k] < 0).any()
                              and (p.r_fav[int(np.argmax(p.r_adv[:k] < 0)):k] >= 0).any())}
