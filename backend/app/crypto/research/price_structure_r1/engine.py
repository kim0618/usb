"""The R1 streaming engine.

Contract: ``docs/crypto/price_structure_r1/CONTRACT_R1.md``.

Same single pass over the 1m grid as R0, same frame order at a shared instant (1h, then 5m, then the
1m publication), same swings, same VWAP, same calibration. The level book and the retest pool are the
R1 ones. The audit is R0's plus the two monotonicity rules of contract section 7.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..price_structure_r0.data import Bars
from ..price_structure_r0.swings import RangeState, Swing, ZigZag, trend_structure
from ..price_structure_r0.vwap import SessionVwap
from .events import ALLOWED_TRANSITIONS as RETEST_TRANSITIONS
from .events import EventEngine, RangeEdge
from .levels import ALLOWED_TRANSITIONS as LEVEL_TRANSITIONS
from .levels import EXPIRY_5M_CONFIRMED_MS, LevelBook

HOUR_MS = 3_600_000
FIVE_MS = 300_000
MINUTE_MS = 60_000


@dataclass
class Audit:
    """Repaint audit, metric M1. Immutable fields are the closed list on each object class."""
    registry: dict = field(default_factory=dict)
    violations: list[dict] = field(default_factory=list)
    vanished: list[dict] = field(default_factory=list)
    prev_active: set = field(default_factory=set)
    prev_sources: dict = field(default_factory=dict)
    prev_state: dict = field(default_factory=dict)
    prev_retest_state: dict = field(default_factory=dict)
    counts: dict = field(default_factory=lambda: {"swings": 0, "breakouts": 0, "sweeps": 0,
                                                  "retests": 0})

    def check(self, kind: str, obj, at_ms: int) -> None:
        key = (kind, obj.id if hasattr(obj, "id") else obj.key())
        imm = obj.immutable()
        seen = self.registry.get(key)
        if seen is None:
            self.registry[key] = imm
            return
        if seen != imm:
            self.violations.append({"at": at_ms, "object": str(key), "first": seen, "now": imm})

    def check_sources_append_only(self, lvl, at_ms: int) -> None:
        prev = self.prev_sources.get(lvl.id)
        now = set(lvl.sources)
        if prev is not None and not prev <= now:
            self.violations.append({"at": at_ms, "object": f"level:{lvl.id}:sources",
                                    "first": sorted(prev), "now": sorted(now)})
        self.prev_sources[lvl.id] = now

    def check_level_transition(self, lvl, at_ms: int) -> None:
        prev = self.prev_state.get(lvl.id)
        if prev is not None and prev != lvl.state and (prev, lvl.state) not in LEVEL_TRANSITIONS:
            self.violations.append({"at": at_ms, "object": f"level:{lvl.id}:state",
                                    "first": prev, "now": lvl.state})
        self.prev_state[lvl.id] = lvl.state

    def check_retest_transition(self, r, at_ms: int) -> None:
        prev = self.prev_retest_state.get(r.breakout.id)
        if prev is not None and prev != r.state and (prev, r.state) not in RETEST_TRANSITIONS:
            self.violations.append({"at": at_ms, "object": f"retest:{r.breakout.id}:state",
                                    "first": prev, "now": r.state})
        self.prev_retest_state[r.breakout.id] = r.state

    def monotonic(self, name: str, n: int, at_ms: int) -> None:
        if n < self.counts[name]:
            self.violations.append({"at": at_ms, "object": name,
                                    "first": self.counts[name], "now": n})
        self.counts[name] = max(self.counts[name], n)


@dataclass
class EngineConfig:
    theta_1h: float
    theta_5m: float
    publish_from_ms: int
    publish_to_ms: int
    # Frozen contract defaults. Switchable for the post verdict attribution only.
    require_touch_to_publish: bool = True
    expiry_5m_ms: int = EXPIRY_5M_CONFIRMED_MS


class Engine:
    def __init__(self, bars: dict[str, Bars], cfg: EngineConfig) -> None:
        self.b1, self.b5, self.bh = bars["1m"], bars["5m"], bars["1h"]
        self.cfg = cfg
        self.zz_h = ZigZag("1h", cfg.theta_1h, label_frame=True)
        self.zz_5 = ZigZag("5m", cfg.theta_5m, label_frame=False)
        self.book = LevelBook(require_touch_to_publish=cfg.require_touch_to_publish,
                              expiry_5m_ms=cfg.expiry_5m_ms)
        self.ev = EventEngine()
        self.rng = RangeState()
        self.vwap = SessionVwap()
        self.edges: list[RangeEdge] = []
        self.audit = Audit()
        self.last_touch_bar: dict[int, int] = {}
        self.new_swings_log: list[Swing] = []
        self.hourly_snapshots: list[dict] = []
        self.counters = {"snapshots": 0, "active": [], "book": [], "provisional": [],
                         "published": [], "trend": {}, "range": {}, "joint": {}, "vwap_pos": {},
                         "retests_open": []}
        self.events_log: list[dict] = []
        self._t0 = int(self.b1.ts[0])

    # -- frame steps --------------------------------------------------------------
    def _on_1h_close(self, h: int, at_ms: int) -> None:
        atr_h = float(self.bh.atr[h])
        for sw in self.zz_h.on_bar(self.bh, h):
            self.new_swings_log.append(sw)
            self.events_log.append({"kind": "SWING", **sw.immutable()})
            atr5 = self._atr5_at(at_ms)
            self.book.offer(sw, float(self.bh.close[h]), atr_h, atr5, at_ms)
        self.rng.evaluate_1h(self.zz_h, self.bh, h)
        self._sync_edges(at_ms)
        self._broke_this_ms = set()
        if h >= 1 and np.isfinite(atr_h):
            tgt = [l for l in self.book.publishable if l.confirm_tf == "1h"]
            for bo in self.ev.scan_breakouts(tgt, "1h", float(self.bh.close[h - 1]),
                                             float(self.bh.close[h]), atr_h, at_ms, h):
                self._broke_this_ms.add((bo.level_id, bo.direction))
                self.ev.open_retest(bo, at_ms, "1h")
                self.events_log.append({"kind": "BREAKOUT", **bo.immutable()})

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
        for lid in self.book.weaken_scan(at_ms):
            self.events_log.append({"kind": "LEVEL_WEAKENED", "level_id": lid, "at": at_ms})
        for lid in self.book.expire_scan(cl, atr_h, at_ms):
            self.events_log.append({"kind": "LEVEL_EXPIRED", "level_id": lid, "at": at_ms})
            self.ev.level_expired(lid, at_ms)

    def _sync_edges(self, at_ms: int) -> None:
        if self.rng.state == "IN_RANGE" and self.rng.high is not None:
            have = {e.id for e in self.edges}
            if "RANGE_HIGH" not in have:
                c = self._last_close_at(at_ms)
                self.edges = [RangeEdge("RANGE_HIGH", self.rng.high,
                                        "UP" if c > self.rng.high else "DOWN"),
                              RangeEdge("RANGE_LOW", self.rng.low,
                                        "UP" if c > self.rng.low else "DOWN")]
        else:
            self.edges = []

    # -- helpers ------------------------------------------------------------------
    def _h_index_at(self, at_ms: int) -> int:
        return max(0, (at_ms - HOUR_MS - self._t0) // HOUR_MS)

    def _atr_h_at(self, at_ms: int) -> float:
        i = (at_ms - HOUR_MS - self._t0) // HOUR_MS
        i = min(max(i, 0), len(self.bh) - 1)
        a = float(self.bh.atr[i])
        return a if np.isfinite(a) else float("nan")

    def _atr5_at(self, at_ms: int) -> float:
        i = (at_ms - FIVE_MS - self._t0) // FIVE_MS
        i = min(max(i, 0), len(self.b5) - 1)
        a = float(self.b5.atr[i])
        return a if np.isfinite(a) else float("nan")

    def _last_close_at(self, at_ms: int) -> float:
        i = (at_ms - MINUTE_MS - self._t0) // MINUTE_MS
        return float(self.b1.close[min(max(i, 0), len(self.b1) - 1)])

    # -- publication ---------------------------------------------------------------
    def snapshot(self, i: int, at_ms: int) -> dict:
        cl = float(self.b1.close[i])
        atr5, atr_h = self._atr5_at(at_ms), self._atr_h_at(at_ms)
        pub = self.book.publish(cl, atr5, at_ms)
        trend, hi_lab, lo_lab = trend_structure(self.zz_h)
        z, pos = self.vwap.position(cl)
        r = self.ev.latest_retest()
        bo = self.ev.breakouts[-1] if self.ev.breakouts else None
        sw = self.ev.sweeps[-1] if self.ev.sweeps else None
        open_views = self.ev.open_views(cl, at_ms)
        return {"ts": int(self.b1.ts[i]), "info_ts": at_ms, "close": round(cl, 2),
                "trend_structure": trend, "last_high_label": hi_lab, "last_low_label": lo_lab,
                "range_state": self.rng.state,
                "range_high": round(self.rng.high, 2) if self.rng.high else None,
                "range_low": round(self.rng.low, 2) if self.rng.low else None,
                "nearest_resistance": pub["nearest_resistance"],
                "nearest_support": pub["nearest_support"],
                "last_breakout": bo.view(at_ms) if bo else None,
                "retest_state": r.state if r else "NONE",
                "retest": r.view(at_ms) if r else None,
                "retests_open": len([x for x in self.ev.open_by_level.values()]),
                "retests": open_views,
                "failed_break_state": self.ev.failed_break_state(at_ms),
                "last_sweep": sw.view(at_ms) if sw else None,
                "vwap": round(self.vwap.vwap, 2) if self.vwap.vwap == self.vwap.vwap else None,
                "vwap_z": round(z, 4), "vwap_position": pos,
                "active_level_count": pub["active_level_count"],
                "book_level_count": pub["book_level_count"],
                "provisional_count": pub["provisional_count"],
                "published_levels": pub["published_levels"],
                "atr_1h": round(atr_h, 2) if atr_h == atr_h else None,
                "atr_5m": round(atr5, 2) if atr5 == atr5 else None}

    def _audit_snapshot(self, snap: dict, at_ms: int) -> None:
        a = self.audit
        act = {l.id for l in self.book.publishable}
        for l in self.book.alive:
            a.check("level", l, at_ms)
            a.check_sources_append_only(l, at_ms)
            a.check_level_transition(l, at_ms)
        gone = a.prev_active - act
        for lid in gone:
            lv = self.book.levels.get(lid)
            # Leaving the published set is only legitimate as a logged EXPIRED. R1 has no path from
            # CONFIRMED or WEAKENED back to PROVISIONAL, so nothing else can remove a line.
            if lv is None or lv.state != "EXPIRED" or lv.expired_at is None:
                a.vanished.append({"at": at_ms, "level_id": lid,
                                   "state": None if lv is None else lv.state})
        a.prev_active = act
        if self.ev.breakouts:
            a.check("breakout", self.ev.breakouts[-1], at_ms)
        if self.ev.sweeps:
            a.check("sweep", self.ev.sweeps[-1], at_ms)
        for r in self.ev.open_by_level.values():
            a.check("breakout", r.breakout, at_ms)
            a.check_retest_transition(r, at_ms)
        a.monotonic("swings", len(self.zz_h.confirmed) + len(self.zz_5.confirmed), at_ms)
        a.monotonic("breakouts", len(self.ev.breakouts), at_ms)
        a.monotonic("sweeps", len(self.ev.sweeps), at_ms)
        a.monotonic("retests", len(self.ev.retests), at_ms)

    def final_audit(self) -> None:
        a = self.audit
        for l in self.book.levels.values():
            a.check("level", l, -1)
            a.check_sources_append_only(l, -1)
        for bo in self.ev.breakouts:
            a.check("breakout", bo, -1)
        for sw in self.ev.sweeps:
            a.check("sweep", sw, -1)
        for s in self.zz_h.confirmed + self.zz_5.confirmed:
            a.check("swing", s, -1)
        for t in self.book.transitions:
            if (t["from"], t["to"]) not in LEVEL_TRANSITIONS:
                a.violations.append({"at": t["at"], "object": f'level:{t["id"]}:transition',
                                     "first": t["from"], "now": t["to"]})
        for t in self.ev.transitions:
            if (t["from"], t["to"]) not in RETEST_TRANSITIONS:
                a.violations.append({"at": t["at"],
                                     "object": f'retest:{t["breakout_id"]}:transition',
                                     "first": t["from"], "now": t["to"]})

    # -- main loop -----------------------------------------------------------------
    def run(self) -> None:
        c = self.counters
        for i in range(len(self.b1)):
            ts = int(self.b1.ts[i])
            at_ms = ts + MINUTE_MS
            if at_ms % HOUR_MS == 0:
                h = (at_ms - HOUR_MS - self._t0) // HOUR_MS
                if 0 <= h < len(self.bh):
                    self._on_1h_close(h, at_ms)
            if at_ms % FIVE_MS == 0:
                k = (at_ms - FIVE_MS - self._t0) // FIVE_MS
                if 0 <= k < len(self.b5):
                    self._on_5m_close(k, at_ms)
            self.vwap.on_1m(ts, float(self.b1.high[i]), float(self.b1.low[i]),
                            float(self.b1.close[i]), float(self.b1.volume[i]),
                            float(self.b1.turnover[i]))
            if not (self.cfg.publish_from_ms <= at_ms <= self.cfg.publish_to_ms):
                continue
            snap = self.snapshot(i, at_ms)
            self._audit_snapshot(snap, at_ms)
            c["snapshots"] += 1
            c["active"].append(snap["active_level_count"])
            c["book"].append(snap["book_level_count"])
            c["provisional"].append(snap["provisional_count"])
            c["published"].append(len(snap["published_levels"]))
            c["retests_open"].append(snap["retests_open"])
            c["trend"][snap["trend_structure"]] = c["trend"].get(snap["trend_structure"], 0) + 1
            c["range"][snap["range_state"]] = c["range"].get(snap["range_state"], 0) + 1
            jk = f'{snap["trend_structure"]}|{snap["range_state"]}'
            c["joint"][jk] = c["joint"].get(jk, 0) + 1
            c["vwap_pos"][snap["vwap_position"]] = c["vwap_pos"].get(snap["vwap_position"], 0) + 1
            if at_ms % HOUR_MS == 0:
                self.hourly_snapshots.append(snap)
        self.final_audit()
