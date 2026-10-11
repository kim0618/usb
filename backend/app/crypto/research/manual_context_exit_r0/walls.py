"""Wall and flow studies inside the Context Collector window C1J (contract R0.1 section 4).

Vocabulary is the collector's own. A V2 wall's life is read from its `wall_v2` OPEN / CHANGE /
CLOSE events. How it ended is the collector's own `vanished` record when it published one
(`CONSUMED_CANDIDATE` / `CANCEL_LIKE` / `UNKNOWN`, MC V1 VanishTracker semantics); a CLOSE the
collector did not publish as vanished is `NOT_PUBLISHED` (its own contract publishes only
TRUE_ENDED and UNKNOWN, so these are rank-evicted or out-of-coverage closes). Nothing is
re-classified here.

Independence: thousands of walls overlap in time, so the unit counted against the gates is the
60-minute block (contract section 8 uses 60-minute blocks for C1J), not the wall.
"""
from __future__ import annotations

import glob
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import stats as ST

BLOCK_MS = 3_600_000
APPROACH_BP = 5.0
PERSIST_AFTER_TOUCH_MS = 60_000
OUTCOME_MIN = (5, 15, 30, 60)


def _rows(root: Path, kind: str):
    for f in sorted(glob.glob(str(root / kind / "*"))):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue      # a torn last line of an open segment


@dataclass
class Context:
    t: np.ndarray            # sample receive ms
    mid: np.ndarray
    synced: np.ndarray       # book SYNCED
    generation: np.ndarray
    session: np.ndarray
    buy60: np.ndarray        # flow 60 s buy share 0..1, NaN unless LIVE/PARTIAL
    vanished: list = field(default_factory=list)

    def index_at(self, ms: int) -> int:
        """Last sample at or before ms (-1 if none)."""
        return int(np.searchsorted(self.t, ms, side="right")) - 1

    def mid_at(self, ms: int) -> float:
        i = self.index_at(ms)
        return float(self.mid[i]) if i >= 0 else float("nan")

    def mid_after(self, ms: int, tolerance_ms: int = 5_000) -> float:
        """First sample at or after ms, if within tolerance (no sample = unknown, not stale)."""
        i = int(np.searchsorted(self.t, ms, side="left"))
        if i >= len(self.t) or self.t[i] - ms > tolerance_ms:
            return float("nan")
        return float(self.mid[i])


def load_context(root: Path) -> Context:
    t, mid, sy, gen, ses, buy, van = [], [], [], [], [], [], []
    sessions: dict[str, int] = {}
    seen = set()
    for r in _rows(root, "context"):
        p = r.get("payload") or {}
        book = p.get("book") or {}
        try:
            m = float(book.get("mid")) if book.get("mid") is not None else np.nan
        except (TypeError, ValueError):
            m = np.nan
        t.append(int(r["receive_ms"]))
        mid.append(m)
        sy.append(book.get("state") == "SYNCED")
        gen.append(book.get("generation") if isinstance(book.get("generation"), int) else -1)
        ses.append(sessions.setdefault(r.get("session_id") or "", len(sessions)))
        w = ((p.get("flow") or {}).get("windows") or {}).get("60s") or {}
        b, s_ = w.get("buy_usdt"), w.get("sell_usdt")
        if w.get("state") in ("LIVE", "PARTIAL") and b is not None and s_ is not None \
                and float(b) + float(s_) > 0:
            buy.append(float(b) / (float(b) + float(s_)))
        else:
            buy.append(np.nan)
        for v in p.get("vanished") or []:
            key = (v.get("side"), v.get("bin_low"), v.get("last_seen_ms"))
            if key in seen:
                continue
            seen.add(key)
            van.append(v)
    order = np.argsort(np.asarray(t), kind="stable")
    arr = lambda x, dt=float: np.asarray(x, dtype=dt)[order]
    return Context(arr(t, np.int64), arr(mid), arr(sy, bool), arr(gen, np.int64), arr(ses, np.int64),
                   arr(buy), van)


@dataclass
class Wall:
    side: str
    bin_low: float
    bin_high: float
    price: float
    open_ms: int
    close_ms: int | None
    open_notional: float
    last_notional: float
    max_notional: float
    close_reason: str | None
    end_state: str = "OPEN_AT_END"
    first_touch_ms: int | None = None
    approaches: int = 0


def load_walls(root: Path) -> list[Wall]:
    """V2 wall instances from OPEN / CHANGE / CLOSE, keyed by (side, bin_low)."""
    live: dict[tuple, Wall] = {}
    done: list[Wall] = []
    for r in _rows(root, "wall_v2"):
        p = r.get("payload") or {}
        key = (p.get("side"), p.get("bin_low"))
        ms = int(r["receive_ms"])
        ev = p.get("event")
        if ev == "OPEN":
            if key in live:                       # re-open without a close: close the old one
                w = live.pop(key)
                w.close_ms, w.close_reason = ms, "REOPENED"
                done.append(w)
            n = float(p.get("notional_usdt") or "nan")
            live[key] = Wall(p["side"], float(p["bin_low"]), float(p["bin_high"]),
                             float(p.get("price") or "nan"), ms, None, n, n, n, None)
        elif ev == "CHANGE" and key in live:
            w = live[key]
            if p.get("notional_usdt") is not None:
                n = float(p["notional_usdt"])
                w.last_notional = n
                w.max_notional = max(w.max_notional, n)
            if p.get("price") is not None:
                w.price = float(p["price"])
        elif ev == "CLOSE" and key in live:
            w = live.pop(key)
            w.close_ms, w.close_reason = ms, p.get("reason")
            done.append(w)
    return done + list(live.values())


def attach_end_states(walls: list[Wall], ctx: Context) -> None:
    """The collector's own vanish classification, matched to the CLOSE it explains."""
    index: dict[tuple, list] = {}
    for v in ctx.vanished:
        try:
            index.setdefault((v["side"], float(v["bin_low"])), []).append(v)
        except (KeyError, TypeError, ValueError):
            continue
    for lst in index.values():
        lst.sort(key=lambda v: v.get("observed_at_ms") or 0)
    for w in walls:
        if w.close_ms is None:
            continue
        cands = index.get((w.side, w.bin_low), [])
        best = None
        for v in cands:
            obs = v.get("observed_at_ms")
            if obs is not None and abs(int(obs) - w.close_ms) <= 2_000:
                best = v
                break
        w.end_state = best.get("state") if best else "NOT_PUBLISHED"


def attach_touches(walls: list[Wall], ctx: Context) -> None:
    for w in walls:
        a = int(np.searchsorted(ctx.t, w.open_ms, side="left"))
        b = int(np.searchsorted(ctx.t, w.close_ms if w.close_ms else ctx.t[-1], side="right"))
        mids = ctx.mid[a:b]
        if not len(mids):
            continue
        if w.side == "ASK":
            dist = (w.bin_low - mids) / mids * 1e4
            inside = mids >= w.bin_low
        else:
            dist = (mids - w.bin_high) / mids * 1e4
            inside = mids <= w.bin_high
        near = dist <= APPROACH_BP
        entering = near & ~np.r_[False, near[:-1]]
        w.approaches = int(entering.sum())
        if inside.any():
            w.first_touch_ms = int(ctx.t[a + int(np.argmax(inside))])


def lifecycle_table(walls: list[Wall]) -> dict:
    out: dict = {}
    for side in ("ASK", "BID"):
        ws = [w for w in walls if w.side == side and w.close_ms is not None]
        by = {}
        for st in sorted({w.end_state for w in ws}):
            g = [w for w in ws if w.end_state == st]
            pers = np.array([(w.close_ms - w.open_ms) / 1000 for w in g], float)
            size = np.array([w.last_notional / w.open_notional for w in g
                             if w.open_notional and np.isfinite(w.open_notional)], float)
            by[st] = {"walls": len(g), "median_persistence_s": ST.median(pers),
                      "p90_persistence_s": ST.pct(pers, 90),
                      "median_size_end_over_open": ST.median(size),
                      "touched_share": ST.rate(np.array([w.first_touch_ms is not None for w in g])),
                      "median_approaches": ST.median(np.array([w.approaches for w in g], float)),
                      "median_post_touch_persistence_s": ST.median(np.array(
                          [(w.close_ms - w.first_touch_ms) / 1000 for w in g if w.first_touch_ms], float))}
        out[side] = {"closed_walls": len(ws), "by_end_state": by}
    return out


def _outcomes(ctx: Context, at_ms: int, sign: float) -> dict:
    m0 = ctx.mid_at(at_ms)
    out = {}
    for mins in OUTCOME_MIN:
        m1 = ctx.mid_after(at_ms + mins * 60_000)
        out[f"m{mins}"] = sign * (m1 - m0) / m0 * 1e4 if np.isfinite(m0) and np.isfinite(m1) else np.nan
    return out


def end_state_outcomes(walls: list[Wall], ctx: Context) -> dict:
    """Q3: mid move after a wall's end, signed toward the wall's side (up for ASK, down for BID),
    by the collector's end state. n_eff = distinct 60-minute blocks."""
    out: dict = {}
    for side in ("ASK", "BID"):
        sign = 1.0 if side == "ASK" else -1.0
        cells = {}
        for st in ("CONSUMED_CANDIDATE", "CANCEL_LIKE", "NOT_PUBLISHED", "UNKNOWN"):
            g = [w for w in walls if w.side == side and w.end_state == st and w.close_ms]
            rows = [(w.close_ms // BLOCK_MS, _outcomes(ctx, w.close_ms, sign)) for w in g]
            blocks = np.array([b for b, _ in rows])
            cell = {"walls": len(g), "n_eff_blocks": int(len(set(blocks.tolist()))),
                    "gate": ST.gate(int(len(set(blocks.tolist()))))}
            for mins in OUTCOME_MIN:
                v = np.array([o[f"m{mins}"] for _, o in rows], float)
                cell[f"m{mins}_toward_wall_bp"] = (ST.mean_ci(v, blocks, f"walls|{side}|{st}|{mins}")
                                                   if len(v) else None)
            cells[st] = cell
        out[side] = cells
    return out


def flow_into_wall(ctx: Context, w: Wall, ms: int) -> bool | None:
    """True when the 60 s flow's larger side pushes into the wall (buy into an ASK wall, sell into
    a BID wall); None when there is no flow reading or the shares are exactly even."""
    i = ctx.index_at(ms)
    if i < 0 or not np.isfinite(ctx.buy60[i]) or ctx.buy60[i] == 0.5:
        return None
    return bool(ctx.buy60[i] > 0.5) if w.side == "ASK" else bool(ctx.buy60[i] < 0.5)


def interaction_classes(walls: list[Wall], ctx: Context) -> dict:
    """I-A..I-D of contract section 4 (ASK; BID mirrored), at the classification instant."""
    rows = []
    for w in walls:
        ask = w.side == "ASK"
        if w.first_touch_ms is not None:
            fw = flow_into_wall(ctx, w, w.first_touch_ms)
            if fw is None:
                continue
            end = w.close_ms if w.close_ms else int(ctx.t[-1])
            persisted = end - w.first_touch_ms >= PERSIST_AFTER_TOUCH_MS
            a = int(np.searchsorted(ctx.t, w.first_touch_ms, side="left"))
            b = int(np.searchsorted(ctx.t, w.first_touch_ms + PERSIST_AFTER_TOUCH_MS, side="right"))
            mids = ctx.mid[a:b]
            beyond = bool((mids > w.bin_high).any() if ask else (mids < w.bin_low).any())
            away = -1.0 if ask else 1.0               # away from the wall
            if fw and persisted and not beyond:
                rows.append(("I-A", w.side, w.first_touch_ms + PERSIST_AFTER_TOUCH_MS, away))
            elif (not fw) and persisted:
                rows.append(("I-D", w.side, w.first_touch_ms + PERSIST_AFTER_TOUCH_MS, away))
            if fw and w.end_state == "CONSUMED_CANDIDATE" and w.close_ms:
                c = int(np.searchsorted(ctx.t, w.close_ms, side="left"))
                after = ctx.mid[c:c + 60]
                if ((after > w.bin_high).any() if ask else (after < w.bin_low).any()):
                    rows.append(("I-B", w.side, w.close_ms, -away))
        elif w.end_state == "CANCEL_LIKE" and w.close_ms:
            rows.append(("I-C", w.side, w.close_ms, 1.0 if ask else -1.0))
    out: dict = {}
    for cls in ("I-A", "I-B", "I-C", "I-D"):
        for side in ("ASK", "BID"):
            g = [r for r in rows if r[0] == cls and r[1] == side]
            blocks = np.array([r[2] // BLOCK_MS for r in g])
            neff = int(len(set(blocks.tolist())))
            cell = {"events": len(g), "n_eff_blocks": neff, "gate": ST.gate(neff)}
            for mins in OUTCOME_MIN:
                v = np.array([_outcomes(ctx, r[2], r[3])[f"m{mins}"] for r in g], float)
                cell[f"m{mins}_bp"] = ST.mean_ci(v, blocks, f"inter|{cls}|{side}|{mins}") if len(v) else None
                if cls == "I-C" and len(v):
                    cell[f"m{mins}_abs_bp"] = ST.median(np.abs(v))
            out[f"{cls}|{side}"] = cell
    return out


def open_walls_at(walls: list[Wall], side: str, ms: int) -> list[Wall]:
    return [w for w in walls if w.side == side and w.open_ms <= ms
            and (w.close_ms is None or w.close_ms > ms)]
