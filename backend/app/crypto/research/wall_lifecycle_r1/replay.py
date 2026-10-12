"""Streaming replay of one Context Collector session into level episodes, V2 bin episodes, the
per-second mid / flow path and the per-second display set. One pass, envelope order, no record
read twice, nothing computed from a later record (every instant is the sample it was seen at).

Units (CONTRACT_WALL_LIFECYCLE_R1 section 3):
* level episode   = one `wall_r0` OPENED row passing the lm-wall.v2 open floors, to its ENDED /
                    UNKNOWN row; interaction with the 1 s mid is tracked while the row rests;
* V2 bin episode  = one `wall_v2` OPEN to its CLOSE, with every notional observation;
* display set     = the V2 bins open at a second with current notional >= 500k, nearest first,
                    at most six per side: what the V3 screen lists.
"""
from __future__ import annotations

import gzip
import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import contract as K

ASK, BID = "ASK", "BID"


# --------------------------------------------------------------------------- journal stream

def _lines(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as handle:
        pending = b""
        for line in handle:
            if not line.endswith(b"\n"):
                pending = line      # a torn trailing line is absent, not data
                continue
            yield json.loads(line)
        del pending


def _kind_stream(root: Path, kind: str, session8: str | None) -> Iterator[tuple[int, int, dict]]:
    directory = root / kind
    if not directory.exists():
        return
    order = {"wall_v2": 0, "wall_r0": 1, "context": 2}[kind]
    for path in sorted(directory.iterdir()):
        if not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")
                or path.name.endswith(".jsonl.open")):
            continue
        if session8 is not None and f"-{session8}-" not in path.name:
            continue
        for record in _lines(path):
            yield (record["seq"], order, record)


def stream(root: Path, session8: str | None = None) -> Iterator[dict[str, Any]]:
    """Records of the three kinds in envelope (`seq`) order, one session at a time."""
    merged = heapq.merge(_kind_stream(root, "wall_v2", session8),
                         _kind_stream(root, "wall_r0", session8),
                         _kind_stream(root, "context", session8), key=lambda item: item[:2])
    for _seq, _order, record in merged:
        yield record


# --------------------------------------------------------------------------- episodes

def price_bin(price: float) -> tuple[float, float]:
    low = math.floor(price / K.BIN_WIDTH_USDT) * K.BIN_WIDTH_USDT
    return low, low + K.BIN_WIDTH_USDT


def near_far(side: str, low: float, high: float) -> tuple[float, float]:
    return (low, high) if side == ASK else (high, low)


@dataclass
class Level:
    """A level episode (R1 primary unit)."""

    episode_id: str
    side: str
    price: float
    notional: float
    multiple: float
    first_seen_ms: int
    open_ms: int
    open_seq: int
    bin_low: float
    bin_high: float
    coverage: str | None
    continuity: str
    display_qualified: bool
    end_ms: int | None = None
    end_event: str | None = None
    persistence_ms: int | None = None
    t_approach: int | None = None
    t_touch: int | None = None
    t_reach_far: int | None = None
    t_cross: int | None = None
    touches: int = 0
    rank_evicted_v2_ms: int | None = None
    last_sample_ms: int | None = None
    last_mid: float | None = None
    last_known: tuple[float | None, float | None] = (None, None)
    gap_at_end: bool = False
    was_inside: bool = False
    end_class: str | None = None

    def observe(self, sample_ms: int, mid: float, known_low: float | None,
                known_high: float | None, gap: bool) -> None:
        near, far = near_far(self.side, self.bin_low, self.bin_high)
        if self.side == ASK:
            dist_bp = (near - mid) / mid * 1e4
            inside = mid >= near
            reached_far = mid >= far
            crossed = mid > far
        else:
            dist_bp = (mid - near) / mid * 1e4
            inside = mid <= near
            reached_far = mid <= far
            crossed = mid < far
        if self.t_approach is None and dist_bp <= K.APPROACH_BP:
            self.t_approach = sample_ms
        if inside and not self.was_inside:
            self.touches += 1
            if self.t_touch is None:
                self.t_touch = sample_ms
        self.was_inside = inside
        if reached_far and self.t_reach_far is None:
            self.t_reach_far = sample_ms
        if crossed and self.t_cross is None:
            self.t_cross = sample_ms
        self.last_sample_ms, self.last_mid, self.last_known = sample_ms, mid, (known_low, known_high)
        self.gap_at_end = gap

    def close(self, end_ms: int, event: str, persistence_ms: int | None) -> None:
        self.end_ms, self.end_event, self.persistence_ms = end_ms, event, persistence_ms
        self.end_class = self._classify()

    def _classify(self) -> str:
        if self.end_event != "ENDED" or self.last_mid is None or self.gap_at_end:
            return K.END_UNKNOWN
        if self.t_reach_far is not None:
            return K.END_CONSUMED
        low, high = self.last_known
        near, _far = near_far(self.side, self.bin_low, self.bin_high)
        out = (low is not None and self.price < low) or (high is not None and self.price > high) \
            or abs(near - self.last_mid) / self.last_mid > K.CANDIDATE_BAND
        if out and self.t_touch is None:
            return K.END_OUT_OF_COVERAGE
        return K.END_TOUCHED_PULLED if self.t_touch is not None else K.END_CANCEL_LIKE

    @property
    def survival_ms(self) -> int | None:
        if self.t_touch is None or self.end_ms is None:
            return None
        return max(0, self.end_ms - self.t_touch)

    def row(self) -> dict[str, Any]:
        return {"episode_id": self.episode_id, "side": self.side, "price": self.price,
                "notional": self.notional, "multiple": self.multiple,
                "first_seen_ms": self.first_seen_ms, "open_ms": self.open_ms,
                "bin_low": self.bin_low, "bin_high": self.bin_high, "coverage": self.coverage,
                "continuity": self.continuity, "display_qualified": self.display_qualified,
                "end_ms": self.end_ms, "end_event": self.end_event,
                "persistence_ms": self.persistence_ms, "end_class": self.end_class,
                "t_approach": self.t_approach, "t_touch": self.t_touch,
                "t_reach_far": self.t_reach_far, "t_cross": self.t_cross, "touches": self.touches,
                "survival_ms": self.survival_ms, "rank_evicted_v2_ms": self.rank_evicted_v2_ms}


@dataclass
class Bin:
    """A V2 bin episode (R1 secondary unit): notional observations at and after OPEN."""

    episode_id: str
    side: str
    bin_low: float
    bin_high: float
    open_ms: int
    price: float
    continuity: str | None
    notionals: list[tuple[int, float]] = field(default_factory=list)
    close_ms: int | None = None
    close_reason: str | None = None
    last_notional: float | None = None
    collector_end: dict[str, Any] | None = None
    t_approach: int | None = None
    t_touch: int | None = None

    @property
    def current_notional(self) -> float:
        return self.notionals[-1][1]

    def row(self) -> dict[str, Any]:
        first = self.notionals[0][1]
        values = [n for _t, n in self.notionals] + ([self.last_notional] if self.last_notional else [])
        return {"episode_id": self.episode_id, "side": self.side, "bin_low": self.bin_low,
                "open_ms": self.open_ms, "close_ms": self.close_ms, "close_reason": self.close_reason,
                "continuity": self.continuity, "first_notional": first,
                "last_notional": self.last_notional, "max_notional": max(values),
                "min_notional": min(values), "observations": len(values),
                "ratio_last_first": (self.last_notional / first) if self.last_notional and first else None,
                "ratio_max_first": max(values) / first if first else None,
                "ratio_min_first": min(values) / first if first else None,
                "t_approach": self.t_approach, "t_touch": self.t_touch,
                "collector_end": self.collector_end}


@dataclass
class Path:
    """The 1 s path: parallel lists, one entry per context sample."""

    sample_ms: list[int] = field(default_factory=list)
    mid: list[float] = field(default_factory=list)
    buy60: list[float] = field(default_factory=list)
    sell60: list[float] = field(default_factory=list)
    flow_live: list[bool] = field(default_factory=list)
    gap_before: list[bool] = field(default_factory=list)
    #: display set per second: ((bin_low, notional), ...) nearest first, per side.
    display_ask: list[tuple] = field(default_factory=list)
    display_bid: list[tuple] = field(default_factory=list)


@dataclass
class Replay:
    session_id: str | None = None
    path: Path = field(default_factory=Path)
    levels: list[Level] = field(default_factory=list)
    bins: list[Bin] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def replay(root: Path, session8: str | None = None, *, cut_ms: int | None = None) -> Replay:
    """One pass over the journal. `cut_ms` stops at the first sample after it (prefix runs)."""
    out = Replay()
    resting: dict[tuple[str, float], Level] = {}
    pending_r0: list[dict[str, Any]] = []
    open_bins: dict[tuple[str, float], Bin] = {}
    recently_closed: dict[tuple[str, float], Bin] = {}
    last_sample_ms: int | None = None
    session8_now: str | None = None
    for record in stream(root, session8):
        sid = record["session_id"]
        if sid != out.session_id:
            if out.session_id is not None:
                out.count("session_changes")
                for level in resting.values():
                    level.close(last_sample_ms or level.open_ms, "SESSION_END", None)
                    out.levels.append(level)
                resting.clear()
                open_bins.clear()
                pending_r0.clear()
            out.session_id = sid
            session8_now = sid[:8]
        kind = record["kind"]
        payload = record["payload"]
        if kind == "wall_v2":
            _wall_v2(out, payload, open_bins, resting, session8_now or "", recently_closed)
            continue
        if kind == "wall_r0":
            pending_r0.append((record["receive_ms"], payload))
            continue
        sample_ms = payload.get("sample_ms")
        if sample_ms is None:
            out.count("context_without_sample")
            continue
        if cut_ms is not None and sample_ms > cut_ms:
            break
        book = payload.get("book") or {}
        mid = _f(book.get("mid"))
        gap = last_sample_ms is not None and sample_ms - last_sample_ms > K.PATH_GAP_MS
        if gap:
            out.count("gaps")
        window = ((payload.get("flow") or {}).get("windows") or {}).get("60s") or {}
        buy, sell = _f(window.get("buy_usdt")), _f(window.get("sell_usdt"))
        live = window.get("state") in ("LIVE", "PARTIAL") and buy is not None and sell is not None
        path = out.path
        path.sample_ms.append(sample_ms)
        path.mid.append(mid if mid is not None else float("nan"))
        path.buy60.append(buy if live else float("nan"))
        path.sell60.append(sell if live else float("nan"))
        path.flow_live.append(bool(live))
        path.gap_before.append(gap)
        ask_set, bid_set = display_set(open_bins)
        path.display_ask.append(ask_set)
        path.display_bid.append(bid_set)
        if mid is not None:
            known = (_f(book.get("known_low")), _f(book.get("known_high")))
            for level in resting.values():
                level.observe(sample_ms, mid, known[0], known[1], gap)
            for key, abin in open_bins.items():
                _bin_observe(abin, sample_ms, mid)
        # V0 rows the sequence placed before this sample's derived record (R0's merge rule) are
        # applied after the sample's mid has been observed: a level that ENDED in this second
        # saw the mid that ended it (a consumed wall is classified on that mid).
        derived_seq = payload.get("v0_derived_seq")
        still = []
        for receive_ms, row in pending_r0:
            if derived_seq is not None and row["v0_seq"] < derived_seq:
                _wall_r0(out, receive_ms, row, resting, open_bins, session8_now or "")
            else:
                still.append((receive_ms, row))
        pending_r0 = still
        # the collector's own classification of display walls that vanished this second
        for item in (payload.get("vanished") or []) + (payload.get("display_exits") or []):
            key = (item.get("side"), _f(item.get("bin_low")))
            abin = recently_closed.get(key)
            if abin is not None and abs((abin.close_ms or 0) - sample_ms) <= 2_000 \
                    and abin.collector_end is None:
                abin.collector_end = {"end_class": item.get("end_class"),
                                      "state": item.get("state") or item.get("mc_v1_raw_state"),
                                      "touched": item.get("mid_path_touched_bin"),
                                      "reason": item.get("end_reason") or item.get("reason")}
        last_sample_ms = sample_ms
    for level in resting.values():
        level.close(last_sample_ms or level.open_ms, "OPEN_AT_CUT", None)
        out.levels.append(level)
        out.count("levels_open_at_end")
    out.counters["samples"] = len(out.path.sample_ms)
    out.counters["levels"] = len(out.levels)
    out.counters["bins"] = len(out.bins)
    return out


def display_set(open_bins: dict[tuple[str, float], Bin]) -> tuple[tuple, tuple]:
    asks = sorted(((b.bin_low, b.current_notional) for (s, _l), b in open_bins.items()
                   if s == ASK and b.current_notional >= K.DISPLAY_MIN_NOTIONAL_USDT))
    bids = sorted(((b.bin_low, b.current_notional) for (s, _l), b in open_bins.items()
                   if s == BID and b.current_notional >= K.DISPLAY_MIN_NOTIONAL_USDT), reverse=True)
    return tuple(asks[:K.DISPLAY_WALL_LIMIT]), tuple(bids[:K.DISPLAY_WALL_LIMIT])


def _bin_observe(abin: Bin, sample_ms: int, mid: float) -> None:
    near, _far = near_far(abin.side, abin.bin_low, abin.bin_high)
    if abin.side == ASK:
        dist_bp, inside = (near - mid) / mid * 1e4, mid >= near
    else:
        dist_bp, inside = (mid - near) / mid * 1e4, mid <= near
    if abin.t_approach is None and dist_bp <= K.APPROACH_BP:
        abin.t_approach = sample_ms
    if abin.t_touch is None and inside:
        abin.t_touch = sample_ms


def _wall_v2(out: Replay, payload: dict[str, Any], open_bins: dict, resting: dict, session8: str,
             recently_closed: dict) -> None:
    side, low = payload.get("side"), _f(payload.get("bin_low"))
    if side not in (ASK, BID) or low is None:
        return
    key = (side, low)
    event = payload.get("event")
    sample_ms = payload.get("sample_ms")
    if event == "OPEN":
        notional = _f(payload.get("notional_usdt")) or 0.0
        abin = Bin(episode_id=f"v2:{session8}:{side}:{low:g}:{payload.get('sample_index')}",
                   side=side, bin_low=low, bin_high=low + K.BIN_WIDTH_USDT, open_ms=sample_ms,
                   price=_f(payload.get("price")) or low, continuity=payload.get("continuity_status"),
                   notionals=[(sample_ms, notional)])
        open_bins[key] = abin
        out.bins.append(abin)
    elif event == "CHANGE":
        abin = open_bins.get(key)
        notional = _f(payload.get("notional_usdt"))
        if abin is not None and notional is not None:
            abin.notionals.append((sample_ms, notional))
    elif event == "CLOSE":
        abin = open_bins.pop(key, None)
        if abin is not None:
            recently_closed[key] = abin
            abin.close_ms, abin.close_reason = sample_ms, payload.get("reason")
            abin.last_notional = _f(payload.get("last_notional_usdt"))
            if payload.get("reason") == "NOT_SELECTED":
                for level in resting.values():
                    if level.side == side and level.bin_low == low and level.rank_evicted_v2_ms is None:
                        level.rank_evicted_v2_ms = sample_ms


def _wall_r0(out: Replay, receive_ms: int, row: dict[str, Any], resting: dict, open_bins: dict,
             session8: str) -> None:
    side, price = row.get("side"), _f(row.get("price"))
    if side not in (ASK, BID) or price is None:
        return
    key = (side, price)
    event, status = row.get("event"), row.get("status")
    is_resting = status == "ACTIVE" or (status not in ("ENDED", "INTERRUPTED", "UNKNOWN")
                                        and event in ("OPENED", "UPDATED"))
    if is_resting:
        if key in resting:
            out.count("r0_reopen_while_resting")
            resting[key].close(receive_ms, "REPLACED", None)
            out.levels.append(resting.pop(key))
        notional, multiple = _f(row.get("notional")) or 0.0, _f(row.get("multiple")) or 0.0
        if notional < K.MIN_NOTIONAL_USDT or multiple < K.MIN_MULTIPLE:
            out.count("r0_below_floors")
            return
        low, high = price_bin(price)
        abin = open_bins.get((side, low))
        level = Level(episode_id=f"r0:{session8}:{side}:{price:g}:{row.get('v0_seq')}", side=side,
                      price=price, notional=notional, multiple=multiple,
                      first_seen_ms=int(row.get("first_seen_ms") or receive_ms), open_ms=receive_ms,
                      open_seq=int(row.get("v0_seq") or 0), bin_low=low, bin_high=high,
                      coverage=row.get("coverage"),
                      continuity=(abin.continuity or "NONE") if abin else "NONE",
                      display_qualified=notional >= K.DISPLAY_MIN_NOTIONAL_USDT)
        resting[key] = level
        out.count("levels_opened")
    else:
        level = resting.pop(key, None)
        if level is None:
            out.count("r0_end_without_open")
            return
        level.close(receive_ms, "ENDED" if event == "ENDED" else "UNKNOWN",
                    int(row["persistence_ms"]) if row.get("persistence_ms") is not None else None)
        out.levels.append(level)


__all__ = ["replay", "stream", "Replay", "Level", "Bin", "Path", "display_set", "price_bin",
           "near_far", "ASK", "BID"]
