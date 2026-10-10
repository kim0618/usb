"""Breakout, retest, failed break and sweep machines (contract sections 9, 10, 11, 17)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .levels import Level

BUFFER_PCT = 0.0010
BUFFER_ATR = 0.25
REBREAK_COOLDOWN_BARS = 24
RETEST_WINDOW_BARS = 48
FAILED_BREAK_HOLD_5M_BARS = 48
TERMINAL = {"RETEST_HELD", "RETEST_FAILED", "NO_RETEST", "RETEST_INCONCLUSIVE", "SUPERSEDED"}


def buffer(price: float, atr_tf: float) -> float:
    return max(BUFFER_PCT * price, BUFFER_ATR * atr_tf)


@dataclass
class RangeEdge:
    """A frozen range boundary. Section 8 keeps it out of the level book; section 9 lets it break."""
    id: str  # RANGE_HIGH | RANGE_LOW
    price: float
    side: str
    tf: str = "5m"
    broken_bar: int | None = None


@dataclass
class Breakout:
    id: int
    level_id: int | str
    level_price: float
    tf: str
    direction: str  # UP | DOWN
    confirmed_at: int
    confirm_close: float
    buffer: float
    source: str  # LEVEL | RANGE_EDGE

    IMMUTABLE = ("id", "level_id", "level_price", "tf", "direction", "confirmed_at",
                 "confirm_close", "buffer", "source")

    def immutable(self) -> dict:
        return {k: getattr(self, k) for k in self.IMMUTABLE}

    def view(self, now_ms: int) -> dict:
        d = self.immutable()
        d["level_price"] = round(d["level_price"], 2)
        d["buffer"] = round(d["buffer"], 2)
        d["confirm_close"] = round(d["confirm_close"], 2)
        d["age_minutes"] = (now_ms - self.confirmed_at) // 60_000
        return d


@dataclass
class Retest:
    breakout: Breakout
    deadline_ms: int
    state: str = "AWAITING_RETEST"
    above_run: int = 0
    below_run: int = 0
    entered_touched_at: int | None = None
    terminated_at: int | None = None
    window_elapsed: bool = False

    def view(self, now_ms: int) -> dict:
        return {"state": self.state, "breakout_id": self.breakout.id,
                "level_id": self.breakout.level_id,
                "level_price": round(self.breakout.level_price, 2),
                "direction": self.breakout.direction, "tf": self.breakout.tf,
                "confirmed_at": self.breakout.confirmed_at, "deadline_ms": self.deadline_ms,
                "minutes_left": max(0, (self.deadline_ms - now_ms) // 60_000)}


@dataclass
class Sweep:
    id: int
    level_id: int | str
    level_price: float
    direction: str  # UP sweep pierces from below and closes back under the level
    wick_at: int
    confirmed_at: int
    extreme: float

    IMMUTABLE = ("id", "level_id", "level_price", "direction", "wick_at", "confirmed_at", "extreme")

    def immutable(self) -> dict:
        return {k: getattr(self, k) for k in self.IMMUTABLE}

    def view(self, now_ms: int) -> dict:
        d = self.immutable()
        d["level_price"] = round(d["level_price"], 2)
        d["extreme"] = round(d["extreme"], 2)
        d["age_minutes"] = (now_ms - self.confirmed_at) // 60_000
        return d


class EventEngine:
    def __init__(self) -> None:
        self._bo_id = 1
        self._sw_id = 1
        self.breakouts: list[Breakout] = []
        self.sweeps: list[Sweep] = []
        self.retests: list[Retest] = []
        self.active_retest: Retest | None = None
        self.cooldown_suppressed = 0
        self.last_failed_break: tuple[str, int] | None = None  # (direction, confirmed_at)
        self._pending_sweeps: dict[tuple, dict] = {}

    # -- breakouts ---------------------------------------------------------------
    def scan_breakouts(self, targets: list, tf: str, close_prev: float, close_now: float,
                       atr_tf: float, at_ms: int, bar_idx: int) -> list[Breakout]:
        fired: list[Breakout] = []
        for obj in targets:
            p = obj.price
            b = buffer(p, atr_tf)
            direction = None
            if obj.side == "DOWN" and close_prev > p + b and close_now > p + b:
                direction = "UP"
            elif obj.side == "UP" and close_prev < p - b and close_now < p - b:
                direction = "DOWN"
            if direction is None:
                continue
            obj.side = "UP" if direction == "UP" else "DOWN"
            if isinstance(obj, Level):
                prev_bar = obj.last_breakout_bar.get(direction)
                if prev_bar is not None and bar_idx - prev_bar < REBREAK_COOLDOWN_BARS:
                    self.cooldown_suppressed += 1
                    continue
                obj.last_breakout_bar[direction] = bar_idx
                obj.breaks += 1
                obj.last_break = {"direction": direction, "at": at_ms}
                src, lid = "LEVEL", obj.id
            else:
                if obj.broken_bar is not None and bar_idx - obj.broken_bar < REBREAK_COOLDOWN_BARS:
                    self.cooldown_suppressed += 1
                    continue
                obj.broken_bar = bar_idx
                src, lid = "RANGE_EDGE", obj.id
            bo = Breakout(self._bo_id, lid, p, tf, direction, at_ms, close_now, b, src)
            self._bo_id += 1
            self.breakouts.append(bo)
            fired.append(bo)
        return fired

    def open_retest(self, bo: Breakout, at_ms: int) -> None:
        minutes = RETEST_WINDOW_BARS * (60 if bo.tf == "1h" else 5)
        if self.active_retest is not None and self.active_retest.state not in TERMINAL:
            self.active_retest.state = "SUPERSEDED"
            self.active_retest.terminated_at = at_ms
        r = Retest(bo, at_ms + minutes * 60_000)
        self.retests.append(r)
        self.active_retest = r

    def step_retest(self, close_5m: float, low_5m: float, high_5m: float, at_ms: int) -> None:
        r = self.active_retest
        if r is None or r.state in TERMINAL:
            return
        if at_ms <= r.breakout.confirmed_at:
            # The machine opens at confirmation and is evaluated on the bars that follow it.
            return
        p, b, up = r.breakout.level_price, r.breakout.buffer, r.breakout.direction == "UP"
        if at_ms >= r.deadline_ms:
            r.window_elapsed = True
            r.state = "NO_RETEST" if r.state == "AWAITING_RETEST" else "RETEST_INCONCLUSIVE"
            r.terminated_at = at_ms
            return
        # failure is checked from confirmation onward, in the direction opposite the breakout
        if up:
            r.below_run = r.below_run + 1 if close_5m < p - b else 0
            if r.below_run >= 2:
                r.state, r.terminated_at = "RETEST_FAILED", at_ms
                self.last_failed_break = ("FAILED_BREAK_UP", at_ms)
                return
        else:
            r.above_run = r.above_run + 1 if close_5m > p + b else 0
            if r.above_run >= 2:
                r.state, r.terminated_at = "RETEST_FAILED", at_ms
                self.last_failed_break = ("FAILED_BREAK_DOWN", at_ms)
                return
        if r.state == "AWAITING_RETEST":
            touched = low_5m <= p + b if up else high_5m >= p - b
            if touched:
                r.state, r.entered_touched_at = "RETEST_TOUCHED", at_ms
                # Reset only the hold counter. The failure run is "two consecutive closes" and a
                # touch must not break that chain, otherwise a bar that already closed through the
                # level is silently forgiven.
                if up:
                    r.above_run = 0
                else:
                    r.below_run = 0
            return
        if r.state == "RETEST_TOUCHED":
            if up:
                r.above_run = r.above_run + 1 if close_5m > p + b else 0
                if r.above_run >= 2:
                    r.state, r.terminated_at = "RETEST_HELD", at_ms
            else:
                r.below_run = r.below_run + 1 if close_5m < p - b else 0
                if r.below_run >= 2:
                    r.state, r.terminated_at = "RETEST_HELD", at_ms

    def failed_break_state(self, now_ms: int) -> str:
        if self.last_failed_break is None:
            return "NONE"
        kind, at = self.last_failed_break
        return kind if now_ms - at <= FAILED_BREAK_HOLD_5M_BARS * 5 * 60_000 else "NONE"

    # -- sweeps -------------------------------------------------------------------
    def scan_sweeps(self, targets: list, high: float, low: float, close: float, atr_5m: float,
                    at_ms: int, bar_idx: int, broke_now: set) -> list[Sweep]:
        out: list[Sweep] = []
        for key, pend in list(self._pending_sweeps.items()):
            if pend["bar"] != bar_idx - 1:
                del self._pending_sweeps[key]
                continue
            lid, direction = key
            p = pend["price"]
            ok = close <= p if direction == "UP" else close >= p
            if ok and (lid, direction) not in broke_now:
                sw = Sweep(self._sw_id, lid, p, direction, pend["wick_at"], at_ms, pend["extreme"])
                self._sw_id += 1
                self.sweeps.append(sw)
                out.append(sw)
            del self._pending_sweeps[key]
        for obj in targets:
            p = obj.price
            b = buffer(p, atr_5m)
            lid = obj.id
            if obj.side == "DOWN" and high > p + b and close <= p and (lid, "UP") not in broke_now:
                self._pending_sweeps[(lid, "UP")] = {"bar": bar_idx, "price": p, "wick_at": at_ms,
                                                     "extreme": high}
            if obj.side == "UP" and low < p - b and close >= p and (lid, "DOWN") not in broke_now:
                self._pending_sweeps[(lid, "DOWN")] = {"bar": bar_idx, "price": p, "wick_at": at_ms,
                                                       "extreme": low}
        return out
