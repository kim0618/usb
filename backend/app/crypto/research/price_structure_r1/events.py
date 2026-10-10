"""R1 events: the breakout and sweep machines of R0, with a retest machine per level.

Contract: ``docs/crypto/price_structure_r1/CONTRACT_R1.md`` section 5.

The breakout rule, the sweep rule, the buffer, the cooldown, the side bookkeeping and the retest
geometry are all R0's and are imported rather than restated. What changes is scope: a retest machine
is attached to its own level instead of to one global slot, so ``SUPERSEDED`` leaves the state space.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..price_structure_r0.events import (  # carried over unchanged, contract section 2
    BUFFER_ATR,
    BUFFER_PCT,
    FAILED_BREAK_HOLD_5M_BARS,
    REBREAK_COOLDOWN_BARS,
    RETEST_WINDOW_BARS,
    Breakout,
    RangeEdge,
    Sweep,
    buffer,
)
from .levels import Level

# Contract section 5. SUPERSEDED is deliberately absent.
TERMINAL = {"RETESTED", "HELD", "FAILED", "EXPIRED"}
RESOLVED = {"RETESTED", "HELD", "FAILED"}
ALLOWED_TRANSITIONS = {
    ("BROKEN", "RETEST_PENDING"),
    ("BROKEN", "HELD"),
    ("BROKEN", "FAILED"),
    ("BROKEN", "EXPIRED"),
    ("RETEST_PENDING", "RETESTED"),
    ("RETEST_PENDING", "FAILED"),
    ("RETEST_PENDING", "EXPIRED"),
}


@dataclass
class Retest:
    """One machine per confirmed breakout. All of them live at once."""
    breakout: Breakout
    deadline_ms: int
    state: str = "BROKEN"
    reason: str | None = None
    above_run: int = 0
    below_run: int = 0
    entered_pending_at: int | None = None
    terminated_at: int | None = None
    window_elapsed: bool = False

    @property
    def level_key(self):
        return self.breakout.level_id

    def view(self, now_ms: int) -> dict:
        return {"state": self.state, "breakout_id": self.breakout.id,
                "level_id": self.breakout.level_id,
                "level_price": round(self.breakout.level_price, 2),
                "direction": self.breakout.direction, "tf": self.breakout.tf,
                "confirmed_at": self.breakout.confirmed_at, "deadline_ms": self.deadline_ms,
                "reason": self.reason,
                "minutes_left": max(0, (self.deadline_ms - now_ms) // 60_000)}


class EventEngine:
    def __init__(self) -> None:
        self._bo_id = 1
        self._sw_id = 1
        self.breakouts: list[Breakout] = []
        self.sweeps: list[Sweep] = []
        self.retests: list[Retest] = []
        self.open_by_level: dict[object, Retest] = {}
        self.last_breakout: Breakout | None = None
        self.cooldown_suppressed = 0
        self.last_failed_break: tuple[str, int] | None = None
        self.max_concurrency = 0
        self.transitions: list[dict] = []
        self._pending_sweeps: dict[tuple, dict] = {}

    # -- breakouts ---------------------------------------------------------------
    def scan_breakouts(self, targets: list, tf: str, close_prev: float, close_now: float,
                       atr_tf: float, at_ms: int, bar_idx: int) -> list[Breakout]:
        """Identical to R0 apart from the target list the engine hands in."""
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
            self.last_breakout = bo
            fired.append(bo)
        return fired

    # -- retest ------------------------------------------------------------------
    def _terminate(self, r: Retest, state: str, at_ms: int, reason: str) -> None:
        if (r.state, state) not in ALLOWED_TRANSITIONS:
            raise AssertionError(f"illegal retest transition {r.state} -> {state}")
        self.transitions.append({"at": at_ms, "breakout_id": r.breakout.id,
                                 "from": r.state, "to": state, "reason": reason})
        r.state, r.terminated_at, r.reason = state, at_ms, reason
        if self.open_by_level.get(r.level_key) is r:
            del self.open_by_level[r.level_key]

    def _advance(self, r: Retest, state: str, at_ms: int, reason: str) -> None:
        if (r.state, state) not in ALLOWED_TRANSITIONS:
            raise AssertionError(f"illegal retest transition {r.state} -> {state}")
        self.transitions.append({"at": at_ms, "breakout_id": r.breakout.id,
                                 "from": r.state, "to": state, "reason": reason})
        r.state = state

    def open_retest(self, bo: Breakout, at_ms: int, window_tf: str) -> Retest:
        """At most one non terminal machine per level. A re-break displaces only its own level."""
        prev = self.open_by_level.get(bo.level_id)
        if prev is not None and prev.state not in TERMINAL:
            self._terminate(prev, "EXPIRED", at_ms, "RELEVEL")
        minutes = RETEST_WINDOW_BARS * (60 if window_tf == "1h" else 5)
        r = Retest(bo, at_ms + minutes * 60_000)
        self.retests.append(r)
        self.open_by_level[bo.level_id] = r
        self.max_concurrency = max(self.max_concurrency, len(self.open_by_level))
        return r

    def level_expired(self, level_id, at_ms: int) -> None:
        r = self.open_by_level.get(level_id)
        if r is not None and r.state not in TERMINAL:
            self._terminate(r, "EXPIRED", at_ms, "LEVEL_EXPIRED")

    def step_retests(self, close_5m: float, low_5m: float, high_5m: float, at_ms: int) -> None:
        """Every open machine is stepped on every 5m close. The comparisons are R0's."""
        for r in list(self.open_by_level.values()):
            if r.state in TERMINAL:
                continue
            if at_ms <= r.breakout.confirmed_at:
                # The machine opens at confirmation and is evaluated on the bars that follow it.
                continue
            p, b = r.breakout.level_price, r.breakout.buffer
            up = r.breakout.direction == "UP"
            if at_ms >= r.deadline_ms:
                r.window_elapsed = True
                if r.state == "BROKEN":
                    self._terminate(r, "HELD", at_ms, "WINDOW_ELAPSED_UNTOUCHED")
                else:
                    self._terminate(r, "EXPIRED", at_ms, "UNDECIDED")
                continue
            # failure is checked from confirmation onward, in the direction opposite the breakout
            if up:
                r.below_run = r.below_run + 1 if close_5m < p - b else 0
                if r.below_run >= 2:
                    self._terminate(r, "FAILED", at_ms, "TWO_CLOSES_BACK_THROUGH")
                    self.last_failed_break = ("FAILED_BREAK_UP", at_ms)
                    continue
            else:
                r.above_run = r.above_run + 1 if close_5m > p + b else 0
                if r.above_run >= 2:
                    self._terminate(r, "FAILED", at_ms, "TWO_CLOSES_BACK_THROUGH")
                    self.last_failed_break = ("FAILED_BREAK_DOWN", at_ms)
                    continue
            if r.state == "BROKEN":
                touched = low_5m <= p + b if up else high_5m >= p - b
                if touched:
                    r.entered_pending_at = at_ms
                    self._advance(r, "RETEST_PENDING", at_ms, "PRICE_RETURNED")
                    # Reset only the hold counter. The failure run is "two consecutive closes" and a
                    # touch must not break that chain, otherwise a bar that already closed through
                    # the level is silently forgiven. (R0's fixed bug, carried over fixed.)
                    if up:
                        r.above_run = 0
                    else:
                        r.below_run = 0
                continue
            if r.state == "RETEST_PENDING":
                if up:
                    r.above_run = r.above_run + 1 if close_5m > p + b else 0
                    if r.above_run >= 2:
                        self._terminate(r, "RETESTED", at_ms, "TWO_CLOSES_HELD")
                else:
                    r.below_run = r.below_run + 1 if close_5m < p - b else 0
                    if r.below_run >= 2:
                        self._terminate(r, "RETESTED", at_ms, "TWO_CLOSES_HELD")

    def latest_retest(self) -> Retest | None:
        """Contract section 5: the scalar is the machine of the most recent confirmed breakout."""
        if self.last_breakout is None:
            return None
        for r in reversed(self.retests):
            if r.breakout.id == self.last_breakout.id:
                return r
        return None

    def open_views(self, close: float, now_ms: int, cap: int = 5) -> list[dict]:
        live = [r for r in self.open_by_level.values() if r.state not in TERMINAL]
        live.sort(key=lambda r: (abs(r.breakout.level_price - close), r.breakout.id))
        return [r.view(now_ms) for r in live[:cap]]

    def failed_break_state(self, now_ms: int) -> str:
        if self.last_failed_break is None:
            return "NONE"
        kind, at = self.last_failed_break
        return kind if now_ms - at <= FAILED_BREAK_HOLD_5M_BARS * 5 * 60_000 else "NONE"

    # -- sweeps -------------------------------------------------------------------
    def scan_sweeps(self, targets: list, high: float, low: float, close: float, atr_5m: float,
                    at_ms: int, bar_idx: int, broke_now: set) -> list[Sweep]:
        """Identical to R0. Only the target list the engine hands in is narrower."""
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
