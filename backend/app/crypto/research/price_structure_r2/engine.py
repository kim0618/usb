"""The R2 streaming engine.

Contract: ``docs/crypto/price_structure_r2/CONTRACT_R2.md``.

The main loop, the frames, the swings, the VWAP, the calibration, the touch scan, the breakout and
sweep machines and the retest pool are R1's and are inherited. Two of R1's frame steps are restated
here rather than wrapped, because the three new families have to enter at declared points inside
them (contract section 3.6) and not before or after the whole step. Every line that is not an
insertion is R1's, and contract section 2.1 is what proves it: with the three families switched off
this engine must reproduce R1's metric payload byte for byte.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..price_structure_r0.data import Bars
from ..price_structure_r1.engine import Engine as EngineR1
from ..price_structure_r1.engine import EngineConfig as EngineConfigR1
from ..price_structure_r1.levels import EXPIRY_5M_CONFIRMED_MS
from .events import EventEngine
from .levels import TOKENS, Candidate, LevelBook

HOUR_MS = 3_600_000
FIVE_MS = 300_000
MINUTE_MS = 60_000
DAY_MS = 86_400_000
BARS_PER_DAY_1M = DAY_MS // MINUTE_MS


@dataclass
class EngineConfig(EngineConfigR1):
    """R1's config plus the three family switches of contract section 8."""
    enable_d: bool = False      # S1, previous session high and low
    enable_range: bool = False  # S2, frozen range high and low
    enable_flip: bool = False   # S3, S-R flip level

    @property
    def families(self) -> tuple:
        return tuple(n for n, on in (("S1", self.enable_d), ("S2", self.enable_range),
                                     ("S3", self.enable_flip)) if on)

    @property
    def variant(self) -> str:
        f = self.families
        return "SWING_ONLY" if not f else "SWING_" + "_".join(f)


class Engine(EngineR1):
    def __init__(self, bars: dict[str, Bars], cfg: EngineConfig) -> None:
        super().__init__(bars, cfg)
        # Same construction arguments, the R2 classes. Nothing has been consumed yet.
        self.book = LevelBook(require_touch_to_publish=cfg.require_touch_to_publish,
                              expiry_5m_ms=cfg.expiry_5m_ms)
        self.ev = EventEngine()
        self._rng_log_seen = 0
        self._episode = 0
        self._episode_open: int | None = None
        self.range_level_ids: dict[str, int] = {}
        self.day_objects: list[dict] = []
        self.range_objects: list[dict] = []
        self.flip_claims: list[dict] = []
        self.range_edge_breaks_migrated = 0
        self.counters["by_source"] = {t: [] for t in TOKENS}

    # -- publication -----------------------------------------------------------------
    def snapshot(self, i: int, at_ms: int) -> dict:
        """R1's snapshot plus the per token publishable census, which M2 does not break out."""
        s = super().snapshot(i, at_ms)
        held = {t: 0 for t in TOKENS}
        major = 0
        for l in self.book.publishable:
            for t in l.sources:
                held[t] += 1
            if l.major:
                major += 1
        for t in TOKENS:
            self.counters["by_source"][t].append(held[t])
        s["publishable_by_source"] = held
        s["publishable_major"] = major
        return s

    # -- S1, previous session high and low, contract section 4 ----------------------
    def _emit_day_objects(self, at_ms: int, close: float, atr_h: float, atr5: float) -> None:
        """The UTC day [at_ms - 24h, at_ms) is complete. Both prices come from closed 1m bars."""
        i1 = (at_ms - self._t0) // MINUTE_MS
        i0 = i1 - BARS_PER_DAY_1M
        if i0 < 0 or i1 > len(self.b1) or i1 <= i0:
            return  # a partial session is not a session
        hi = float(self.b1.high[i0:i1].max())
        lo = float(self.b1.low[i0:i1].min())
        for kind, px in (("PREV_DAY_HIGH", hi), ("PREV_DAY_LOW", lo)):
            cand = Candidate(source="D", price=px, kind=kind, pivot_at=at_ms - DAY_MS,
                             confirmed_at=at_ms, day_expiry=at_ms + DAY_MS)
            lvl = self.book.offer(cand, close, atr_h, atr5, at_ms)
            self.day_objects.append({"at": at_ms, "kind": kind, "price": round(px, 2),
                                     "created": lvl is not None,
                                     "level_id": self.book.last_offer_level_id})
            self.events_log.append({"kind": "DAY_OBJECT", "at": at_ms, "origin_kind": kind,
                                    "price": round(px, 2), "created": lvl is not None})

    # -- S2, frozen range high and low, contract section 5 ---------------------------
    def _consume_range_log(self, close: float, atr_h: float, atr5: float) -> None:
        fresh = self.rng.log[self._rng_log_seen:]
        self._rng_log_seen = len(self.rng.log)
        if not self.cfg.enable_range:
            return
        for e in fresh:
            if e["event"] == "RANGE_FROZEN":
                self._episode += 1
                self._episode_open = self._episode
                self.range_level_ids = {}
                for kind, px in (("RANGE_HIGH", e["high"]), ("RANGE_LOW", e["low"])):
                    cand = Candidate(source="RANGE", price=float(px), kind=kind,
                                     pivot_at=e["at"], confirmed_at=e["at"],
                                     range_episode=self._episode)
                    lvl = self.book.offer(cand, close, atr_h, atr5, e["at"])
                    self.range_level_ids[kind] = self.book.last_offer_level_id
                    self.range_objects.append({"at": e["at"], "episode": self._episode,
                                               "kind": kind, "price": round(float(px), 2),
                                               "created": lvl is not None,
                                               "level_id": self.book.last_offer_level_id})
                    self.events_log.append({"kind": "RANGE_OBJECT", "at": e["at"],
                                            "origin_kind": kind, "price": round(float(px), 2),
                                            "episode": self._episode,
                                            "created": lvl is not None})
            elif e["event"] in ("RANGE_LOST", "RANGE_CLEARED"):
                if self._episode_open is not None:
                    self.book.end_range_episode(self._episode_open, e["at"])
                self._episode_open = None
                self.range_level_ids = {}

    def _sync_edges(self, at_ms: int) -> None:
        """Contract section 5: with S2 on, the boundary price lives in the book, not beside it."""
        if self.cfg.enable_range:
            self.edges = []
            return
        super()._sync_edges(at_ms)

    # -- S3, the S-R flip level, contract section 6 ----------------------------------
    def _promote_flips(self, close: float, atr_h: float, atr5: float) -> None:
        for bo, at in self.ev.drain_flips():
            if not self.cfg.enable_flip:
                continue
            if bo.source == "LEVEL":
                lvl = self.book.levels.get(bo.level_id)
                if lvl is not None and self.book.add_flip(lvl, at):
                    self.flip_claims.append({"at": at, "mode": "TOKEN_ADDED", "level_id": lvl.id,
                                             "price": round(lvl.price, 2),
                                             "sources": sorted(lvl.sources),
                                             "breakout_id": bo.id,
                                             "direction": bo.direction})
                    self.events_log.append({"kind": "FLIP_CLAIMED", "at": at, "level_id": lvl.id,
                                            "mode": "TOKEN_ADDED"})
            else:
                kind = f"FLIP_FROM_{bo.level_id}"
                cand = Candidate(source="FLIP", price=bo.level_price, kind=kind,
                                 pivot_at=bo.confirmed_at, confirmed_at=at, flip_at=at)
                lvl = self.book.offer(cand, close, atr_h, atr5, at)
                mode = "LEVEL_CREATED" if lvl is not None else "TOKEN_ADDED_ON_MERGE"
                self.flip_claims.append({"at": at, "mode": mode,
                                         "level_id": self.book.last_offer_level_id,
                                         "price": round(bo.level_price, 2),
                                         "origin_kind": kind, "breakout_id": bo.id,
                                         "direction": bo.direction})
                self.events_log.append({"kind": "FLIP_CLAIMED", "at": at, "mode": mode,
                                        "level_id": self.book.last_offer_level_id})

    # -- frame steps, R1's with the insertions of contract section 3.6 ----------------
    def _on_1h_close(self, h: int, at_ms: int) -> None:
        atr_h = float(self.bh.atr[h])
        close_h = float(self.bh.close[h])
        for sw in self.zz_h.on_bar(self.bh, h):
            self.new_swings_log.append(sw)
            self.events_log.append({"kind": "SWING", **sw.immutable()})
            atr5 = self._atr5_at(at_ms)
            self.book.offer(sw, close_h, atr_h, atr5, at_ms)
        self.rng.evaluate_1h(self.zz_h, self.bh, h)
        # insertion: the range episode objects of contract section 5
        self._consume_range_log(close_h, atr_h, self._atr5_at(at_ms))
        self._sync_edges(at_ms)
        # insertion: the day objects of contract section 4
        if self.cfg.enable_d and at_ms % DAY_MS == 0:
            self._emit_day_objects(at_ms, close_h, atr_h, self._atr5_at(at_ms))
        self._broke_this_ms = set()
        if h >= 1 and np.isfinite(atr_h):
            tgt = [l for l in self.book.publishable if l.confirm_tf == "1h"]
            for bo in self.ev.scan_breakouts(tgt, "1h", float(self.bh.close[h - 1]),
                                             close_h, atr_h, at_ms, h):
                self._broke_this_ms.add((bo.level_id, bo.direction))
                self.ev.open_retest(bo, at_ms, "1h")
                self.events_log.append({"kind": "BREAKOUT", **bo.immutable()})
                # insertion: contract section 5, the range episode feedback R0 drove from an edge
                if (self.cfg.enable_range and bo.source == "LEVEL"
                        and self.rng.state == "IN_RANGE"
                        and bo.level_id in self.range_level_ids.values()):
                    self.range_edge_breaks_migrated += 1
                    self.rng.on_edge_break(bo.direction, at_ms, h)
                    self._sync_edges(at_ms)

    def _on_5m_close(self, k: int, at_ms: int) -> None:
        atr5 = float(self.b5.atr[k])
        if not np.isfinite(atr5):
            return
        atr_h = self._atr_h_at(at_ms)
        for sw in self.zz_5.on_bar(self.b5, k):
            self.new_swings_log.append(sw)
            self.events_log.append({"kind": "SWING", **sw.immutable()})
            self.book.offer(sw, float(self.b5.close[k]), atr_h, atr5, at_ms, bar_5m=k)
        hi, lo, cl = float(self.b5.high[k]), float(self.b5.low[k]), float(self.b5.close[k])
        for lid in self.book.touch_scan(hi, lo, atr5, at_ms, k, self.last_touch_bar):
            self.events_log.append({"kind": "TOUCH", "level_id": lid, "at": at_ms})
        broke_now: set = set(getattr(self, "_broke_this_ms", set()))
        self._broke_this_ms = set()
        if k >= 1:
            tgt = ([l for l in self.book.publishable if l.confirm_tf == "5m"] + self.edges)
            for bo in self.ev.scan_breakouts(tgt, "5m", float(self.b5.close[k - 1]), cl,
                                             atr5, at_ms, k):
                broke_now.add((bo.level_id, bo.direction))
                self.ev.open_retest(bo, at_ms, "5m")
                self.events_log.append({"kind": "BREAKOUT", **bo.immutable()})
                if bo.source == "RANGE_EDGE" and self.rng.state == "IN_RANGE":
                    self.rng.on_edge_break(bo.direction, at_ms, self._h_index_at(at_ms))
                    self._sync_edges(at_ms)
        sweep_tgt = list(self.book.publishable) + self.edges
        for sw in self.ev.scan_sweeps(sweep_tgt, hi, lo, cl, atr5, at_ms, k, broke_now):
            self.events_log.append({"kind": "SWEEP", **sw.immutable()})
            lvl = self.book.levels.get(sw.level_id) if isinstance(sw.level_id, int) else None
            if lvl is not None and lvl.publishable:
                self.book.note_touch(lvl, at_ms)
                self.last_touch_bar[lvl.id] = k
        self.ev.step_retests(cl, lo, hi, at_ms)
        # insertion: contract section 6, the flip is claimed on a RETESTED termination
        self._promote_flips(cl, atr_h, atr5)
        for lid in self.book.weaken_scan(at_ms):
            self.events_log.append({"kind": "LEVEL_WEAKENED", "level_id": lid, "at": at_ms})
        for lid in self.book.expire_scan(cl, atr_h, at_ms):
            self.events_log.append({"kind": "LEVEL_EXPIRED", "level_id": lid, "at": at_ms})
            self.ev.level_expired(lid, at_ms)
