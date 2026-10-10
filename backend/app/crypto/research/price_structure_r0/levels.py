"""The support and resistance level book (contract section 8, with section 17.1 side bookkeeping)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .swings import Swing

MERGE_TOL_ATR = 0.25
TOL_TOUCH_ATR = 0.15
TOL_ROLE_ATR = 0.15
TOUCH_COOLDOWN_5M_BARS = 6
EXPIRY_1H_MS = 10 * 24 * 3_600_000
EXPIRY_5M_MS = 48 * 3_600_000
FAR_EXPIRY_ATR_1H = 10.0
FAR_EXPIRY_UNTOUCHED_MS = 24 * 3_600_000
MAX_PUBLISHED = 10
MAX_PER_SIDE = 5
DAY_MS = 24 * 3_600_000


@dataclass
class Level:
    id: int
    tf: str
    price: float
    origin_kind: str
    pivot_at: int
    created_at: int
    side: str  # UP when the last confirmed close stood above the level, else DOWN
    touches: int = 1
    last_touch_at: int | None = None
    breaks: int = 0
    last_break: dict | None = None
    flips: int = 0
    state: str = "ACTIVE"
    last_breakout_bar: dict = field(default_factory=dict)  # direction -> frame bar index
    expired_at: int | None = None

    IMMUTABLE = ("id", "tf", "price", "origin_kind", "pivot_at", "created_at")

    def immutable(self) -> dict:
        return {k: getattr(self, k) for k in self.IMMUTABLE}

    def strength(self, now_ms: int) -> int:
        recent = 1 if (self.last_touch_at is not None and now_ms - self.last_touch_at <= DAY_MS) else 0
        return self.touches + (2 if self.tf == "1h" else 0) + recent

    def role(self, close: float, atr_5m: float) -> str:
        tol = TOL_ROLE_ATR * atr_5m
        if self.price > close + tol:
            return "RESISTANCE"
        if self.price < close - tol:
            return "SUPPORT"
        return "AT_PRICE"

    def view(self, close: float, atr_5m: float, now_ms: int) -> dict:
        return {"id": self.id, "tf": self.tf, "price": round(self.price, 2),
                "origin_kind": self.origin_kind, "role": self.role(close, atr_5m),
                "touches": self.touches, "flips": self.flips, "breaks": self.breaks,
                "strength": self.strength(now_ms), "created_at": self.created_at,
                "pivot_at": self.pivot_at, "last_touch_at": self.last_touch_at,
                "distance_pct": round((self.price - close) / close * 100.0, 4)}


class LevelBook:
    def __init__(self) -> None:
        self._next_id = 1
        self.levels: dict[int, Level] = {}
        self.log: list[dict] = []
        self.merged_into: list[dict] = []
        self.suppressed_by_1h: int = 0

    @property
    def active(self) -> list[Level]:
        return [l for l in self.levels.values() if l.state == "ACTIVE"]

    # -- creation ----------------------------------------------------------------
    def offer(self, sw: Swing, close: float, atr_1h: float, atr_5m: float, now_ms: int) -> Level | None:
        atr_own = atr_1h if sw.tf == "1h" else atr_5m
        tol = MERGE_TOL_ATR * atr_own
        if sw.tf == "5m":
            near_1h = [l for l in self.active
                       if l.tf == "1h" and abs(l.price - sw.price) <= MERGE_TOL_ATR * atr_5m]
            if near_1h:
                self.suppressed_by_1h += 1
                self.log.append({"event": "CANDIDATE_SUPPRESSED_BY_1H", "at": sw.confirmed_at,
                                 "price": sw.price, "incumbent": near_1h[0].id})
                return None
        same = [l for l in self.active if l.tf == sw.tf and abs(l.price - sw.price) <= tol]
        if same:
            inc = min(same, key=lambda l: abs(l.price - sw.price))
            inc.touches += 1
            self.merged_into.append({"at": sw.confirmed_at, "incumbent": inc.id,
                                     "candidate_price": sw.price, "delta": sw.price - inc.price})
            self.log.append({"event": "CANDIDATE_MERGED", "at": sw.confirmed_at,
                             "incumbent": inc.id, "candidate_price": sw.price})
            return None
        lvl = Level(id=self._next_id, tf=sw.tf, price=sw.price, origin_kind=sw.kind,
                    pivot_at=sw.pivot_at, created_at=sw.confirmed_at,
                    side="UP" if close > sw.price else "DOWN")
        self._next_id += 1
        self.levels[lvl.id] = lvl
        self.log.append({"event": "LEVEL_CREATED", "at": lvl.created_at, "id": lvl.id,
                         "tf": lvl.tf, "price": lvl.price, "origin_kind": lvl.origin_kind,
                         "side": lvl.side})
        return lvl

    # -- maintenance on 5m closes -------------------------------------------------
    def touch_scan(self, high: float, low: float, atr_5m: float, now_ms: int, bar_5m: int,
                   last_touch_bar: dict[int, int]) -> list[int]:
        tol = TOL_TOUCH_ATR * atr_5m
        hit = []
        for l in self.active:
            if low <= l.price + tol and high >= l.price - tol:
                prev = last_touch_bar.get(l.id)
                if prev is not None and bar_5m - prev < TOUCH_COOLDOWN_5M_BARS:
                    continue
                l.touches += 1
                l.last_touch_at = now_ms
                last_touch_bar[l.id] = bar_5m
                hit.append(l.id)
        return hit

    def expire_scan(self, close: float, atr_1h: float, now_ms: int) -> list[int]:
        gone = []
        for l in self.active:
            anchor = max(l.created_at, l.last_touch_at or 0)
            age = now_ms - anchor
            reason = None
            if l.tf == "1h" and age > EXPIRY_1H_MS:
                reason = "AGE_10D"
            elif l.tf == "5m" and age > EXPIRY_5M_MS:
                reason = "AGE_48H"
            elif (abs(close - l.price) > FAR_EXPIRY_ATR_1H * atr_1h
                  and (l.last_touch_at is None or now_ms - l.last_touch_at > FAR_EXPIRY_UNTOUCHED_MS)
                  and now_ms - l.created_at > FAR_EXPIRY_UNTOUCHED_MS):
                reason = "FAR_AND_UNTOUCHED"
            if reason:
                l.state, l.expired_at = "EXPIRED", now_ms
                gone.append(l.id)
                self.log.append({"event": "LEVEL_EXPIRED", "at": now_ms, "id": l.id,
                                 "reason": reason, "price": l.price, "tf": l.tf})
        return gone

    # -- publication --------------------------------------------------------------
    def publish(self, close: float, atr_5m: float, now_ms: int) -> dict:
        act = self.active
        above = sorted([l for l in act if l.price > close], key=lambda l: l.price)
        below = sorted([l for l in act if l.price < close], key=lambda l: -l.price)
        nr = above[0] if above else None
        ns = below[0] if below else None
        rank = lambda ls: sorted(ls, key=lambda l: (-l.strength(now_ms), abs(l.price - close)))
        sel = rank(above)[:MAX_PER_SIDE] + rank(below)[:MAX_PER_SIDE]
        sel = sorted(sel, key=lambda l: l.price)[:MAX_PUBLISHED]
        return {"nearest_resistance": nr.view(close, atr_5m, now_ms) if nr else None,
                "nearest_support": ns.view(close, atr_5m, now_ms) if ns else None,
                "published_levels": [l.view(close, atr_5m, now_ms) for l in sel],
                "active_level_count": len(act)}
