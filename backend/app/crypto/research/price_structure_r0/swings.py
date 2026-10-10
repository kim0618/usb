"""Confirmed, non repainting swings and the structure they imply (contract sections 5, 6, 7)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .data import Bars

THETA_LADDER = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0)


@dataclass(frozen=True)
class Swing:
    tf: str
    kind: str  # SWING_HIGH | SWING_LOW
    price: float
    pivot_at: int  # open timestamp of the bar that set the extreme
    confirmed_at: int  # close timestamp of the bar that confirmed the retracement
    confirm_close: float
    label: str  # HH | LH | HL | LL | UNLABELED (1h frame) or NA (5m frame)

    def key(self) -> tuple:
        return (self.tf, self.kind, self.pivot_at, self.confirmed_at)

    def immutable(self) -> dict:
        return {"tf": self.tf, "kind": self.kind, "price": self.price, "pivot_at": self.pivot_at,
                "confirmed_at": self.confirmed_at, "label": self.label}


class ZigZag:
    """Close based ATR zigzag. A swing is published at the bar that confirms it and never changes."""

    def __init__(self, tf: str, theta: float, label_frame: bool) -> None:
        self.tf, self.theta, self.label_frame = tf, theta, label_frame
        self.mode = "UNDECIDED"
        self.hi_idx: int | None = None
        self.hi_px = -np.inf
        self.lo_idx: int | None = None
        self.lo_px = np.inf
        self.prev_high_price: float | None = None
        self.prev_low_price: float | None = None
        self.confirmed: list[Swing] = []

    # -- labelling ---------------------------------------------------------------
    def _label(self, kind: str, price: float) -> str:
        if not self.label_frame:
            return "NA"
        if kind == "SWING_HIGH":
            if self.prev_high_price is None:
                return "UNLABELED"
            return "HH" if price >= self.prev_high_price else "LH"
        if self.prev_low_price is None:
            return "UNLABELED"
        return "HL" if price >= self.prev_low_price else "LL"

    def _emit(self, kind: str, price: float, pivot_at: int, b: Bars, t: int) -> Swing:
        sw = Swing(self.tf, kind, float(price), int(pivot_at), b.close_ms(t), float(b.close[t]),
                   self._label(kind, price))
        if kind == "SWING_HIGH":
            self.prev_high_price = float(price)
        else:
            self.prev_low_price = float(price)
        self.confirmed.append(sw)
        return sw

    # -- streaming ---------------------------------------------------------------
    def on_bar(self, b: Bars, t: int) -> list[Swing]:
        atr = b.atr[t]
        if not np.isfinite(atr):
            return []
        if self.hi_idx is None:
            self.hi_idx, self.hi_px = t, float(b.high[t])
            self.lo_idx, self.lo_px = t, float(b.low[t])
            return []
        if b.high[t] > self.hi_px:
            self.hi_idx, self.hi_px = t, float(b.high[t])
        if b.low[t] < self.lo_px:
            self.lo_idx, self.lo_px = t, float(b.low[t])
        need = self.theta * float(atr)
        c = float(b.close[t])
        out: list[Swing] = []
        if self.mode == "UNDECIDED":
            up_excess = self.hi_px - c  # excess for confirming a swing high
            dn_excess = c - self.lo_px
            hi_ok, lo_ok = up_excess >= need, dn_excess >= need
            if hi_ok and lo_ok:
                hi_ok, lo_ok = (True, False) if up_excess >= dn_excess else (False, True)
            if hi_ok:
                out.append(self._emit("SWING_HIGH", self.hi_px, b.ts[self.hi_idx], b, t))
                self._flip_to_low(b, t)
            elif lo_ok:
                out.append(self._emit("SWING_LOW", self.lo_px, b.ts[self.lo_idx], b, t))
                self._flip_to_high(b, t)
            return out
        if self.mode == "SEEK_HIGH":
            if self.hi_px - c >= need:
                out.append(self._emit("SWING_HIGH", self.hi_px, b.ts[self.hi_idx], b, t))
                self._flip_to_low(b, t)
        else:
            if c - self.lo_px >= need:
                out.append(self._emit("SWING_LOW", self.lo_px, b.ts[self.lo_idx], b, t))
                self._flip_to_high(b, t)
        return out

    def _flip_to_low(self, b: Bars, t: int) -> None:
        a = self.hi_idx if self.hi_idx is not None else t
        seg = b.low[a:t + 1]
        off = int(np.argmin(seg))
        self.mode, self.lo_idx, self.lo_px = "SEEK_LOW", a + off, float(seg[off])
        # The running high is reseeded from the candidate low; _flip_to_high recomputes it anyway.
        self.hi_idx, self.hi_px = self.lo_idx, float(b.high[self.lo_idx])

    def _flip_to_high(self, b: Bars, t: int) -> None:
        a = self.lo_idx if self.lo_idx is not None else t
        seg = b.high[a:t + 1]
        off = int(np.argmax(seg))
        self.mode, self.hi_idx, self.hi_px = "SEEK_HIGH", a + off, float(seg[off])
        self.lo_idx, self.lo_px = self.hi_idx, float(b.low[self.hi_idx])


def trend_structure(zz: ZigZag) -> tuple[str, str, str]:
    """(trend_structure, last_high_label, last_low_label) from the confirmed 1h swing sequence."""
    hi = next((s.label for s in reversed(zz.confirmed) if s.kind == "SWING_HIGH"), "NONE")
    lo = next((s.label for s in reversed(zz.confirmed) if s.kind == "SWING_LOW"), "NONE")
    if hi == "HH" and lo == "HL":
        return "BULLISH", hi, lo
    if hi == "LH" and lo == "LL":
        return "BEARISH", hi, lo
    return "NEUTRAL", hi, lo


RANGE_WIDTH_ATR = 6.0
RANGE_EDGE_FRAC = 0.35
RANGE_BROKEN_HOLD_BARS_1H = 24


@dataclass
class RangeState:
    state: str = "NO_RANGE"  # NO_RANGE | IN_RANGE | RANGE_BROKEN_UP | RANGE_BROKEN_DOWN
    high: float | None = None
    low: float | None = None
    frozen_at: int | None = None
    broken_at_1h_index: int | None = None
    log: list[dict] = field(default_factory=list)

    def evaluate_1h(self, zz: ZigZag, b: Bars, t: int) -> None:
        """Called after new 1h swings are confirmed, and on every 1h close for the broken timer."""
        if self.state in ("RANGE_BROKEN_UP", "RANGE_BROKEN_DOWN"):
            if self.broken_at_1h_index is not None and t - self.broken_at_1h_index >= RANGE_BROKEN_HOLD_BARS_1H:
                self.log.append({"event": "RANGE_CLEARED", "at": b.close_ms(t), "from": self.state})
                self.state, self.high, self.low = "NO_RANGE", None, None
                self.frozen_at = self.broken_at_1h_index = None
            return
        last4 = zz.confirmed[-4:]
        if len(last4) < 4:
            return
        highs = [s.price for s in last4 if s.kind == "SWING_HIGH"]
        lows = [s.price for s in last4 if s.kind == "SWING_LOW"]
        if len(highs) < 2 or len(lows) < 2:
            return
        H, L = max(highs), min(lows)
        W = H - L
        atr = float(b.atr[t])
        ok = (W > 0 and W <= RANGE_WIDTH_ATR * atr
              and all(H - h <= RANGE_EDGE_FRAC * W for h in highs)
              and all(l - L <= RANGE_EDGE_FRAC * W for l in lows))
        if self.state == "IN_RANGE":
            if not ok:
                self.log.append({"event": "RANGE_LOST", "at": b.close_ms(t),
                                 "high": self.high, "low": self.low})
                self.state, self.high, self.low, self.frozen_at = "NO_RANGE", None, None, None
            return
        if ok:
            self.state, self.high, self.low, self.frozen_at = "IN_RANGE", H, L, b.close_ms(t)
            self.log.append({"event": "RANGE_FROZEN", "at": self.frozen_at, "high": H, "low": L,
                             "width": W, "atr_1h": atr})

    def on_edge_break(self, direction: str, at_ms: int, t_1h: int) -> None:
        self.state = "RANGE_BROKEN_UP" if direction == "UP" else "RANGE_BROKEN_DOWN"
        self.broken_at_1h_index = t_1h
        self.log.append({"event": self.state, "at": at_ms, "high": self.high, "low": self.low})
