"""The R1 level book: symmetric cross timeframe merge and the 5m level lifecycle.

Contract: ``docs/crypto/price_structure_r1/CONTRACT_R1.md`` sections 3, 4, 6, 7.

Two things change against R0 and nothing else does. A candidate of either frame that lands in the
same structural zone as an alive level merges into that level's identity whatever the order of
arrival, carrying its frame into ``sources`` rather than creating a second line. And a level sourced
from 5m alone has a lifecycle: it is invisible and inert until price comes back and touches it, it
weakens when nothing trades at it, and it dies sooner than R0's flat 48 hours.

The incumbent's ``price``, ``id``, ``tf``, ``pivot_at`` and ``created_at`` are never recomputed, so a
drawn line still never moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..price_structure_r0.levels import (  # carried over unchanged, contract section 2
    DAY_MS,
    FAR_EXPIRY_ATR_1H,
    FAR_EXPIRY_UNTOUCHED_MS,
    MAX_PER_SIDE,
    MAX_PUBLISHED,
    MERGE_TOL_ATR,
    TOL_ROLE_ATR,
    TOL_TOUCH_ATR,
    TOUCH_COOLDOWN_5M_BARS,
)
from ..price_structure_r0.swings import Swing

EXPIRY_1H_MS = 10 * 24 * 3_600_000  # carried from R0

# Contract section 4: single pre-registered values, taken from R0's frozen 48h 5m lifetime quartered
# and halved. Not ladders, not swept, before or after any result.
PROVISIONAL_TTL_MS = 12 * 3_600_000
WEAKEN_AFTER_MS = 12 * 3_600_000
EXPIRY_5M_CONFIRMED_MS = 24 * 3_600_000

PUBLISHABLE = ("CONFIRMED", "WEAKENED")
# Contract section 7.2. Any transition outside this graph is an M1 violation.
ALLOWED_TRANSITIONS = {
    ("PROVISIONAL", "CONFIRMED"),
    ("PROVISIONAL", "EXPIRED"),
    ("CONFIRMED", "WEAKENED"),
    ("CONFIRMED", "EXPIRED"),
    ("WEAKENED", "CONFIRMED"),
    ("WEAKENED", "EXPIRED"),
}


@dataclass
class Level:
    id: int
    tf: str  # the ORIGINATING frame, immutable forever, even after a cross frame merge
    price: float
    origin_kind: str
    pivot_at: int
    created_at: int
    side: str  # UP when the last confirmed close stood above the level, else DOWN
    sources: set = field(default_factory=set)  # append only over {"5m", "1h"}
    touches: int = 1
    last_touch_at: int | None = None
    breaks: int = 0
    last_break: dict | None = None
    flips: int = 0
    state: str = "PROVISIONAL"
    last_breakout_bar: dict = field(default_factory=dict)  # direction -> frame bar index
    expired_at: int | None = None
    created_bar_5m: int | None = None  # a touch on the creation bar does not confirm
    merges: int = 0

    IMMUTABLE = ("id", "tf", "price", "origin_kind", "pivot_at", "created_at")

    def immutable(self) -> dict:
        return {k: getattr(self, k) for k in self.IMMUTABLE}

    # -- derived provenance, contract section 3 ----------------------------------
    @property
    def provenance(self) -> str:
        if len(self.sources) > 1:
            return "MULTI_TF"
        return next(iter(self.sources))

    @property
    def confirm_tf(self) -> str:
        """One identity, one confirming frame: the slowest frame in `sources`."""
        return "1h" if "1h" in self.sources else "5m"

    @property
    def publishable(self) -> bool:
        return self.state in PUBLISHABLE

    @property
    def alive(self) -> bool:
        return self.state != "EXPIRED"

    def strength(self, now_ms: int) -> int:
        recent = 1 if (self.last_touch_at is not None and now_ms - self.last_touch_at <= DAY_MS) else 0
        weak = 1 if self.state == "WEAKENED" else 0
        return self.touches + (2 if "1h" in self.sources else 0) + recent - weak

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
                "provenance": self.provenance, "lifecycle": self.state,
                "sources": sorted(self.sources), "confirm_tf": self.confirm_tf,
                "touches": self.touches, "flips": self.flips, "breaks": self.breaks,
                "merges": self.merges,
                "strength": self.strength(now_ms), "created_at": self.created_at,
                "pivot_at": self.pivot_at, "last_touch_at": self.last_touch_at,
                "distance_pct": round((self.price - close) / close * 100.0, 4)}


class LevelBook:
    def __init__(self, require_touch_to_publish: bool = True,
                 expiry_5m_ms: int = EXPIRY_5M_CONFIRMED_MS) -> None:
        # Both default to the frozen contract. They are switchable for the post verdict attribution
        # of RESULTS section 6 only, which does not feed the verdict.
        self.require_touch_to_publish = require_touch_to_publish
        self.expiry_5m_ms = expiry_5m_ms
        self._next_id = 1
        self.levels: dict[int, Level] = {}
        self.log: list[dict] = []
        self.merged_into: list[dict] = []
        self.cross_tf_merges: int = 0
        self.same_tf_merges: int = 0
        self.promotions: int = 0
        self.transitions: list[dict] = []

    @property
    def alive(self) -> list[Level]:
        return [l for l in self.levels.values() if l.alive]

    @property
    def publishable(self) -> list[Level]:
        return [l for l in self.levels.values() if l.publishable]

    # backwards compatible name so the shared metric code can read either book
    @property
    def active(self) -> list[Level]:
        return self.publishable

    # -- lifecycle ----------------------------------------------------------------
    def _transition(self, l: Level, to: str, at_ms: int, reason: str) -> None:
        frm = l.state
        if frm == to:
            return
        l.state = to
        self.transitions.append({"at": at_ms, "id": l.id, "from": frm, "to": to, "reason": reason})
        self.log.append({"event": f"LEVEL_{to}", "at": at_ms, "id": l.id, "from": frm,
                         "reason": reason, "price": l.price, "provenance": l.provenance})

    # -- creation ----------------------------------------------------------------
    def _zone_tol(self, cand_tf: str, inc_tf: str, atr_1h: float, atr_5m: float) -> float:
        """Symmetric: the tolerance is a property of the pair, not of who arrived first."""
        if cand_tf == inc_tf:
            return MERGE_TOL_ATR * (atr_1h if cand_tf == "1h" else atr_5m)
        return MERGE_TOL_ATR * atr_5m  # R0's own cross frame tolerance, not widened

    def offer(self, sw: Swing, close: float, atr_1h: float, atr_5m: float, now_ms: int,
              bar_5m: int | None = None) -> Level | None:
        near = [l for l in self.alive
                if abs(l.price - sw.price) <= self._zone_tol(sw.tf, l.tf, atr_1h, atr_5m)]
        if near:
            inc = min(near, key=lambda l: (abs(l.price - sw.price), l.id))
            cross = sw.tf not in inc.sources
            inc.touches += 1
            inc.merges += 1
            if cross:
                inc.sources.add(sw.tf)
                self.cross_tf_merges += 1
                self.log.append({"event": "LEVEL_SOURCE_ADDED", "at": sw.confirmed_at,
                                 "id": inc.id, "added": sw.tf,
                                 "sources": sorted(inc.sources), "price": inc.price})
                if sw.tf == "1h" and inc.state == "PROVISIONAL":
                    # A 1h source promotes: the level leaves the 5m lifecycle at once.
                    self.promotions += 1
                    self._transition(inc, "CONFIRMED", sw.confirmed_at, "PROMOTED_BY_1H_SOURCE")
            else:
                self.same_tf_merges += 1
            self.merged_into.append({"at": sw.confirmed_at, "incumbent": inc.id,
                                     "candidate_tf": sw.tf, "candidate_price": sw.price,
                                     "delta": sw.price - inc.price, "cross_tf": cross})
            self.log.append({"event": "CANDIDATE_MERGED", "at": sw.confirmed_at,
                             "incumbent": inc.id, "candidate_tf": sw.tf,
                             "candidate_price": sw.price, "cross_tf": cross})
            return None
        lvl = Level(id=self._next_id, tf=sw.tf, price=sw.price, origin_kind=sw.kind,
                    pivot_at=sw.pivot_at, created_at=sw.confirmed_at,
                    side="UP" if close > sw.price else "DOWN",
                    sources={sw.tf},
                    state=("CONFIRMED" if sw.tf == "1h" or not self.require_touch_to_publish
                           else "PROVISIONAL"),
                    created_bar_5m=bar_5m)
        self._next_id += 1
        self.levels[lvl.id] = lvl
        self.log.append({"event": "LEVEL_CREATED", "at": lvl.created_at, "id": lvl.id,
                         "tf": lvl.tf, "price": lvl.price, "origin_kind": lvl.origin_kind,
                         "side": lvl.side, "lifecycle": lvl.state})
        return lvl

    # -- maintenance on 5m closes -------------------------------------------------
    def touch_scan(self, high: float, low: float, atr_5m: float, now_ms: int, bar_5m: int,
                   last_touch_bar: dict[int, int]) -> list[int]:
        """Touch definition is R0's. PROVISIONAL levels are scanned: a touch is how they confirm."""
        tol = TOL_TOUCH_ATR * atr_5m
        hit = []
        for l in self.alive:
            if low <= l.price + tol and high >= l.price - tol:
                prev = last_touch_bar.get(l.id)
                if prev is not None and bar_5m - prev < TOUCH_COOLDOWN_5M_BARS:
                    continue
                if l.state == "PROVISIONAL" and l.created_bar_5m is not None \
                        and bar_5m <= l.created_bar_5m:
                    continue  # a touch on the creation bar does not confirm
                l.touches += 1
                l.last_touch_at = now_ms
                last_touch_bar[l.id] = bar_5m
                hit.append(l.id)
                if l.state == "PROVISIONAL":
                    self._transition(l, "CONFIRMED", now_ms, "FIRST_TOUCH")
                elif l.state == "WEAKENED":
                    self._transition(l, "CONFIRMED", now_ms, "RETOUCHED")
        return hit

    def note_touch(self, l: Level, now_ms: int) -> None:
        """A sweep counts as a touch (R0 section 11). It can also re-confirm a WEAKENED level."""
        l.touches += 1
        l.last_touch_at = now_ms
        if l.state == "WEAKENED":
            self._transition(l, "CONFIRMED", now_ms, "RETOUCHED")

    def weaken_scan(self, now_ms: int) -> list[int]:
        out = []
        for l in self.alive:
            if l.state != "CONFIRMED" or "1h" in l.sources:
                continue
            anchor = l.last_touch_at if l.last_touch_at is not None else l.created_at
            if now_ms - anchor > WEAKEN_AFTER_MS:
                self._transition(l, "WEAKENED", now_ms, "NO_TOUCH_12H")
                out.append(l.id)
        return out

    def expire_scan(self, close: float, atr_1h: float, now_ms: int) -> list[int]:
        gone = []
        for l in self.alive:
            anchor = max(l.created_at, l.last_touch_at or 0)
            age = now_ms - anchor
            reason = None
            if "1h" in l.sources:
                if age > EXPIRY_1H_MS:
                    reason = "AGE_10D"
            elif l.state == "PROVISIONAL":
                if now_ms - l.created_at > PROVISIONAL_TTL_MS:
                    reason = "PROVISIONAL_UNTOUCHED"
            elif age > self.expiry_5m_ms:
                # Contract section 4.1 names this reason AGE_24H at the frozen lifetime.
                reason = ("AGE_24H" if self.expiry_5m_ms == EXPIRY_5M_CONFIRMED_MS
                          else "AGE_5M_LIFETIME")
            if reason is None and (abs(close - l.price) > FAR_EXPIRY_ATR_1H * atr_1h
                                  and (l.last_touch_at is None
                                       or now_ms - l.last_touch_at > FAR_EXPIRY_UNTOUCHED_MS)
                                  and now_ms - l.created_at > FAR_EXPIRY_UNTOUCHED_MS):
                reason = "FAR_AND_UNTOUCHED"
            if reason:
                l.expired_at = now_ms
                self._transition(l, "EXPIRED", now_ms, reason)
                gone.append(l.id)
        return gone

    # -- publication --------------------------------------------------------------
    def publish(self, close: float, atr_5m: float, now_ms: int) -> dict:
        act = self.publishable
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
                "active_level_count": len(act),
                "book_level_count": len(self.alive),
                "provisional_count": sum(1 for l in self.alive if l.state == "PROVISIONAL")}
