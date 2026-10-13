"""The R2 level book: five source tokens, one merge zone, one expiry rule.

Contract: ``docs/crypto/price_structure_r2/CONTRACT_R2.md`` sections 3, 4, 5, 6, 10.

R1's book knew two sources, ``5m`` and ``1h``, and keyed four of its rules on the literal test
``"1h" in sources``. R2 adds three more tokens and replaces that test with ``major``, which is the
whole of the generalisation. Everything else is R1's: the merge discards the candidate and never
recomputes the incumbent's price, the 5m lifecycle is untouched, the state graph is unchanged, and a
drawn line never moves.

With no new family enabled this module must behave exactly as R1's does. That is not an aspiration:
contract section 2.1 makes it a blocker and ``replay`` checks it byte for byte.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..price_structure_r0.levels import (  # carried over unchanged, contract section 2
    DAY_MS,
    FAR_EXPIRY_ATR_1H,
    FAR_EXPIRY_UNTOUCHED_MS,
    MERGE_TOL_ATR,
)
from ..price_structure_r0.swings import Swing
from ..price_structure_r1.levels import (  # carried over unchanged, contract section 2
    ALLOWED_TRANSITIONS,
    EXPIRY_1H_MS,
    EXPIRY_5M_CONFIRMED_MS,
    PROVISIONAL_TTL_MS,
    WEAKEN_AFTER_MS,
)
from ..price_structure_r1.levels import Level as LevelR1
from ..price_structure_r1.levels import LevelBook as LevelBookR1

INF = float("inf")

# Contract section 3.1 and 3.2.
TOKENS = ("5m", "1h", "D", "RANGE", "FLIP")
MAJOR = frozenset({"1h", "D", "RANGE", "FLIP"})
NEW_TOKENS = frozenset({"D", "RANGE", "FLIP"})


def frame_of(token: str) -> str:
    """Contract section 3.3: the frame of a source. Every new family is an hourly scale object."""
    return "5m" if token == "5m" else "1h"


def is_major(sources) -> bool:
    return bool(MAJOR & set(sources))


@dataclass(frozen=True)
class Candidate:
    """What is offered to the book. A swing is one of these; so is every new family's object."""
    source: str          # one of TOKENS
    price: float
    kind: str            # origin_kind
    pivot_at: int
    confirmed_at: int
    day_expiry: int | None = None    # token D, contract section 4
    range_episode: int | None = None  # token RANGE, contract section 5
    flip_at: int | None = None        # token FLIP, contract section 6

    @property
    def frame(self) -> str:
        return frame_of(self.source)

    @classmethod
    def from_swing(cls, sw: Swing) -> "Candidate":
        return cls(source=sw.tf, price=sw.price, kind=sw.kind, pivot_at=sw.pivot_at,
                   confirmed_at=sw.confirmed_at)


@dataclass
class Level(LevelR1):
    """R1's level plus the three per token expiry anchors of contract section 10.

    Each anchor is written once, when its token is added, and never revised.
    """
    day_expiry: int | None = None
    range_episode: int | None = None
    flip_at: int | None = None

    # -- derived, contract section 3.2 -------------------------------------------
    @property
    def major(self) -> bool:
        return is_major(self.sources)

    @property
    def provenance(self) -> str:
        if len(self.sources) > 1:
            return "MULTI"
        return next(iter(self.sources))

    @property
    def confirm_tf(self) -> str:
        return "1h" if self.major else "5m"

    def strength(self, now_ms: int) -> int:
        recent = 1 if (self.last_touch_at is not None and now_ms - self.last_touch_at <= DAY_MS) else 0
        weak = 1 if self.state == "WEAKENED" else 0
        return self.touches + (2 if self.major else 0) + recent - weak

    def view(self, close: float, atr_5m: float, now_ms: int) -> dict:
        d = LevelR1.view(self, close, atr_5m, now_ms)
        d["major"] = self.major
        return d


class LevelBook(LevelBookR1):
    """R1's book with the five token lattice. The R1 constructor and ``publish`` are inherited."""

    def __init__(self, require_touch_to_publish: bool = True,
                 expiry_5m_ms: int = EXPIRY_5M_CONFIRMED_MS) -> None:
        super().__init__(require_touch_to_publish=require_touch_to_publish,
                         expiry_5m_ms=expiry_5m_ms)
        self.episode_ended_at: dict[int, int] = {}   # range episode id -> end instant
        self.merges_by_source: dict[str, int] = {}
        self.creates_by_source: dict[str, int] = {}
        self.merge_detail: list[dict] = []
        self.flip_tokens_added: int = 0
        self.flip_levels_created: int = 0
        self.last_offer_level_id: int | None = None

    # -- merge zone, contract section 3.3 ------------------------------------------
    def _zone_tol(self, cand_frame: str, inc_tf: str, atr_1h: float, atr_5m: float) -> float:
        """0.25 * ATR_1h only when both sides of the pair are hourly scale. Widens nothing.

        The incumbent's side of the pair is its immutable originating ``tf``, which is the field R1
        used, so a book holding only ``5m`` and ``1h`` tokens gets exactly R1's tolerances.
        """
        if cand_frame == "1h" and inc_tf == "1h":
            return MERGE_TOL_ATR * atr_1h
        return MERGE_TOL_ATR * atr_5m

    # -- creation, contract sections 3.3 and 3.4 ------------------------------------
    def offer(self, cand, close: float, atr_1h: float, atr_5m: float, now_ms: int,
              bar_5m: int | None = None) -> Level | None:
        c = cand if isinstance(cand, Candidate) else Candidate.from_swing(cand)
        near = [l for l in self.alive
                if abs(l.price - c.price) <= self._zone_tol(c.frame, l.tf, atr_1h, atr_5m)]
        if near:
            inc = min(near, key=lambda l: (abs(l.price - c.price), l.id))
            cross = c.source not in inc.sources
            inc.touches += 1
            inc.merges += 1
            self.merges_by_source[c.source] = self.merges_by_source.get(c.source, 0) + 1
            if cross:
                inc.sources.add(c.source)
                self._adopt_anchor(inc, c)
                self.cross_tf_merges += 1
                self.log.append({"event": "LEVEL_SOURCE_ADDED", "at": c.confirmed_at,
                                 "id": inc.id, "added": c.source,
                                 "sources": sorted(inc.sources), "price": inc.price})
                if c.source in MAJOR and inc.state == "PROVISIONAL":
                    # A major source promotes: the level leaves the 5m lifecycle at once.
                    self.promotions += 1
                    self._transition(inc, "CONFIRMED", c.confirmed_at,
                                     f"PROMOTED_BY_{c.source}_SOURCE")
            else:
                self.same_tf_merges += 1
            self.merged_into.append({"at": c.confirmed_at, "incumbent": inc.id,
                                     "candidate_tf": c.source, "candidate_price": c.price,
                                     "delta": c.price - inc.price, "cross_tf": cross})
            self.merge_detail.append({"at": c.confirmed_at, "source": c.source,
                                      "incumbent_id": inc.id, "incumbent_tf": inc.tf,
                                      "incumbent_sources": sorted(inc.sources),
                                      "delta": round(c.price - inc.price, 2),
                                      "new_token": cross})
            self.log.append({"event": "CANDIDATE_MERGED", "at": c.confirmed_at,
                             "incumbent": inc.id, "candidate_tf": c.source,
                             "candidate_price": c.price, "cross_tf": cross})
            self.last_offer_level_id = inc.id
            return None
        born = ("CONFIRMED" if c.source in MAJOR or not self.require_touch_to_publish
                else "PROVISIONAL")
        lvl = Level(id=self._next_id, tf=c.frame, price=c.price, origin_kind=c.kind,
                    pivot_at=c.pivot_at, created_at=c.confirmed_at,
                    side="UP" if close > c.price else "DOWN",
                    sources={c.source}, state=born, created_bar_5m=bar_5m)
        self._adopt_anchor(lvl, c)
        self._next_id += 1
        self.levels[lvl.id] = lvl
        self.creates_by_source[c.source] = self.creates_by_source.get(c.source, 0) + 1
        if c.source == "FLIP":
            self.flip_levels_created += 1
        self.log.append({"event": "LEVEL_CREATED", "at": lvl.created_at, "id": lvl.id,
                         "tf": lvl.tf, "price": lvl.price, "origin_kind": lvl.origin_kind,
                         "side": lvl.side, "lifecycle": lvl.state, "source": c.source})
        self.last_offer_level_id = lvl.id
        return lvl

    @staticmethod
    def _adopt_anchor(l: Level, c: Candidate) -> None:
        """Write the token's expiry anchor once. Contract section 10: never revised."""
        if c.source == "D" and l.day_expiry is None:
            l.day_expiry = c.day_expiry
        elif c.source == "RANGE" and l.range_episode is None:
            l.range_episode = c.range_episode
        elif c.source == "FLIP" and l.flip_at is None:
            l.flip_at = c.flip_at

    # -- the flip token, contract section 6 -----------------------------------------
    def add_flip(self, l: Level, at_ms: int) -> bool:
        """Claimed on a RETESTED termination, not on the break. Append only like any token."""
        if "FLIP" in l.sources or not l.alive:
            return False
        l.sources.add("FLIP")
        l.flip_at = at_ms
        l.flips += 1
        self.flip_tokens_added += 1
        self.log.append({"event": "LEVEL_SOURCE_ADDED", "at": at_ms, "id": l.id, "added": "FLIP",
                         "sources": sorted(l.sources), "price": l.price})
        if l.state == "PROVISIONAL":
            # R1 promotes PROVISIONAL on a major source and says nothing about WEAKENED. R2 keeps
            # that exactly, so no new token behaves differently from the 1h token it generalises.
            self.promotions += 1
            self._transition(l, "CONFIRMED", at_ms, "PROMOTED_BY_FLIP_SOURCE")
        return True

    # -- the range episode, contract section 5 ---------------------------------------
    def end_range_episode(self, episode: int, at_ms: int) -> None:
        self.episode_ended_at.setdefault(episode, at_ms)

    # -- lifetime, contract section 3.4 -----------------------------------------------
    def _token_expiry_instant(self, l: Level, token: str, now_ms: int) -> float:
        """The instant at which this token is expired, or +inf while it is not.

        A single source level reproduces R1's own condition exactly, including R1's use of
        ``>`` rather than ``>=`` for the age rules.
        """
        if token == "5m":
            if l.state == "PROVISIONAL":
                return l.created_at + PROVISIONAL_TTL_MS
            return max(l.created_at, l.last_touch_at or 0) + self.expiry_5m_ms
        if token == "1h":
            return max(l.created_at, l.last_touch_at or 0) + EXPIRY_1H_MS
        if token == "D":
            return (l.day_expiry - 1) if l.day_expiry is not None else INF
        if token == "RANGE":
            end = self.episode_ended_at.get(l.range_episode)
            return (end - 1) if end is not None else INF
        if token == "FLIP":
            return max(l.flip_at or 0, l.last_touch_at or 0) + EXPIRY_1H_MS
        raise AssertionError(f"unknown source token {token}")

    _TOKEN_REASON = {"5m": "AGE_24H", "1h": "AGE_10D", "D": "DAY_SUPERSEDED",
                     "RANGE": "RANGE_ENDED", "FLIP": "FLIP_AGE_10D"}

    def _token_reason(self, l: Level, token: str) -> str:
        if token == "5m":
            if l.state == "PROVISIONAL":
                return "PROVISIONAL_UNTOUCHED"
            return ("AGE_24H" if self.expiry_5m_ms == EXPIRY_5M_CONFIRMED_MS
                    else "AGE_5M_LIFETIME")
        return self._TOKEN_REASON[token]

    def weaken_scan(self, now_ms: int) -> list[int]:
        """R1's scan with ``"1h" in sources`` generalised to ``major`` (contract section 3.2)."""
        out = []
        for l in self.alive:
            if l.state != "CONFIRMED" or l.major:
                continue
            anchor = l.last_touch_at if l.last_touch_at is not None else l.created_at
            if now_ms - anchor > WEAKEN_AFTER_MS:
                self._transition(l, "WEAKENED", now_ms, "NO_TOUCH_12H")
                out.append(l.id)
        return out

    def expire_scan(self, close: float, atr_1h: float, now_ms: int) -> list[int]:
        """A level expires when EVERY token in ``sources`` is expired. The max rule."""
        gone = []
        for l in self.alive:
            instants = {t: self._token_expiry_instant(l, t, now_ms) for t in l.sources}
            governing = max(instants, key=lambda t: instants[t])
            reason = None
            if now_ms > instants[governing]:
                reason = self._token_reason(l, governing)
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

    # -- reporting -------------------------------------------------------------------
    def census_by_source(self) -> dict:
        out = {}
        for t in TOKENS:
            held = [l for l in self.levels.values() if t in l.sources]
            out[t] = {"levels_holding_token": len(held),
                      "created_by_this_source": self.creates_by_source.get(t, 0),
                      "merged_into_existing": self.merges_by_source.get(t, 0),
                      "token_added_on_merge": sum(1 for m in self.merge_detail
                                                  if m["source"] == t and m["new_token"]),
                      "merged_into_5m_incumbent": sum(1 for m in self.merge_detail
                                                      if m["source"] == t
                                                      and m["incumbent_tf"] == "5m"),
                      "merged_into_1h_incumbent": sum(1 for m in self.merge_detail
                                                      if m["source"] == t
                                                      and m["incumbent_tf"] == "1h")}
        return out
