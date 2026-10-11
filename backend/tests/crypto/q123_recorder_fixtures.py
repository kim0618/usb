"""Synthetic Context Collector roots and a stubbed structure layer, for the Q123 recorder tests.

The journal writer produces the collector's own file shapes: `<kind>-YYYYMMDD-<s8>-NNNNN` segments,
sealed ones as `.jsonl` or `.jsonl.gz`, the newest as `.jsonl.open` (optionally with a torn last
line), `context` and `wall_r0` records sharing one envelope `seq` per session and R0's V0 merge
key (`v0_seq` on wall rows, `v0_derived_seq` on samples).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.crypto.research.market_context_r0 import layers as R0L  # noqa: E402
from app.crypto.research.q123_forward_v1 import contract as K  # noqa: E402

MIN = 60_000
T0 = K.PUBLISH_FROM_MS + 3 * 3_600_000      # minute open of the first approach
LEVEL = 100_000.0


def sid(ch: str) -> str:
    return (ch * 8) + "-0000-4000-8000-" + ("0" * 12)


class Journal:
    """Writes one collector session. `rotate` = records per segment per kind."""

    def __init__(self, root: Path, session_id: str, *, rotate: int = 400, gz: bool = True):
        self.root = Path(root)
        self.session_id = session_id
        self.s8 = session_id[:8]
        self.rotate = rotate
        self.gz = gz
        self.seq = 0
        self.v0 = 0
        self.lines: dict[str, list[bytes]] = {"context": [], "wall_r0": []}
        self.seg: dict[str, int] = {"context": 1, "wall_r0": 1}
        self.sealed: dict[str, list[int]] = {"context": [], "wall_r0": []}
        self.first_ms: int | None = None

    def _name(self, kind: str, n: int, ms: int) -> str:
        day = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d")
        return f"{kind}-{day}-{self.s8}-{n:05d}"

    def _env(self, kind: str, ms: int, payload: dict) -> bytes:
        self.seq += 1
        rec = {"kind": kind, "seq": self.seq, "session_id": self.session_id, "receive_ms": ms,
               "payload": payload}
        return (json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode()

    def _add(self, kind: str, ms: int, line: bytes) -> None:
        if self.first_ms is None:
            self.first_ms = ms
        self.lines[kind].append(line)
        if len(self.lines[kind]) >= self.rotate:
            self._seal(kind, ms)

    def _seal(self, kind: str, ms: int) -> None:
        d = self.root / kind
        d.mkdir(parents=True, exist_ok=True)
        n = self.seg[kind]
        data = b"".join(self.lines[kind])
        base = d / self._name(kind, n, self.first_ms or ms)
        open_path = base.with_name(base.name + ".jsonl.open")
        if open_path.exists():
            open_path.unlink()
        if self.gz and n % 2 == 1:
            (base.with_name(base.name + ".jsonl.gz")).write_bytes(gzip.compress(data))
        else:
            (base.with_name(base.name + ".jsonl")).write_bytes(data)
        self.sealed[kind].append(n)
        self.lines[kind] = []
        self.seg[kind] = n + 1

    def wall(self, ms: int, *, side="ASK", price=100_050.0, event="OPENED", status="ACTIVE",
             notional="900000", multiple="9", first_seen_ms=None, generation=1,
             coverage="COMPLETE") -> None:
        self.v0 += 1
        payload = {"v0_seq": self.v0, "event": event, "status": status, "side": side,
                   "price": f"{price:.1f}", "qty": "9", "notional": notional,
                   "multiple": multiple, "first_seen_ms": first_seen_ms or ms - 120_000,
                   "persistence_ms": 0, "coverage": coverage, "generation": generation,
                   "local_average": "100000", "neighbours": 4}
        self._add("wall_r0", ms, self._env("wall_r0", ms, payload))

    def sample(self, ms: int, mid: float, *, flow=0.5, gen=1, synced=True, flow_ok=True,
               band=(2e6, 1e6)) -> None:
        self.v0 += 1
        payload = {
            "sample_ms": ms, "v0_derived_seq": self.v0,
            "book": {"state": "SYNCED" if synced else "SYNCING", "mid": f"{mid:.2f}",
                     "generation": gen, "known_low": f"{mid - 400:.1f}",
                     "known_high": f"{mid + 400:.1f}"},
            "depth": [{"band_pct": "0.1", "bid_coverage": "COMPLETE",
                       "ask_coverage": "COMPLETE", "bid_notional": str(band[0]),
                       "ask_notional": str(band[1])}],
            "flow": {"state": "LIVE", "windows": {"60s": {
                "imbalance_usdt": flow, "coverage": "COMPLETE" if flow_ok else "PARTIAL"}}},
            "liquidity": {"state": "LIVE"}, "absorption": {"state": "NONE"},
            "collector": {"state": "LIVE" if synced else "SYNCING"}}
        self._add("context", ms, self._env("context", ms, payload))

    def flush(self, *, torn: bool = False, close: bool = False) -> None:
        """Write the live `.open` files (or seal everything when the session is closed)."""
        for kind in ("context", "wall_r0"):
            d = self.root / kind
            d.mkdir(parents=True, exist_ok=True)
            if close:
                if self.lines[kind]:
                    self._seal(kind, self.first_ms or 0)
                continue
            path = d / (self._name(kind, self.seg[kind], self.first_ms or 0) + ".jsonl.open")
            data = b"".join(self.lines[kind])
            if torn and kind == "context":
                data += b'{"kind":"context","seq":99999'
            path.write_bytes(data)


def session(root: Path, ch: str, start_ms: int, seconds: int, *, mid=LEVEL * 0.999,
            mids=None, flow=0.5, walls=((100_050.0, "ASK"),), rotate=400, torn=False,
            close=False, gen=None, gz=True) -> Journal:
    """One sample a second from `start_ms`; walls opened before the first sample."""
    j = Journal(root, sid(ch), rotate=rotate, gz=gz)
    for price, side in walls:
        j.wall(start_ms - 500, side=side, price=price, first_seen_ms=start_ms - 300_000)
    for k in range(seconds):
        m = mids[k] if mids is not None else mid
        j.sample(start_ms + k * 1000, m, flow=flow if not callable(flow) else flow(k),
                 gen=1 if gen is None else gen(k))
    j.flush(torn=torn, close=close)
    return j


# ----------------------------------------------------------------------------- structure stub
def minute_rows(approaches: list[tuple[int, str, int, float]]) -> list[dict]:
    """Rows on the 1m grid; `approaches` = (minute ts, side, level_id, level price)."""
    rows = []
    for ts, side, lid, price in approaches:
        r = {"ts": ts, "res_price": None, "res_distance_pct": None, "res_id": None,
             "sup_price": None, "sup_distance_pct": None, "sup_id": None,
             "trend_structure": "RANGE", "range_state": "IN_RANGE", "retest_state": "NONE",
             "failed_break_state": "NONE", "atr_5m": 10.0, "active_level_count": 9}
        tag = "res" if side == "RES" else "sup"
        r.update({f"{tag}_price": price, f"{tag}_distance_pct": 0.1 if side == "RES" else -0.1,
                  f"{tag}_id": lid})
        rows.append(r)
    return rows


class FakeBook:
    """Stands in for `KlineBook`: the structure layer is stubbed, so there are no bars."""

    def __init__(self, cache=None, fixed=None):
        self.version = 1
        self.last_error = None
        self.last_open = int(os.environ.get("Q123_TEST_LAST_OPEN", T0 + 600 * MIN))

    def load(self):
        pass

    def poll(self, now_ms):
        return False


def install_stub(rc_module, approaches: list[tuple[int, str, int, float]], setter=setattr) -> None:
    """Replace the kline book and the R1 window with fixed rows (seeds handled like the real one)."""
    full = minute_rows(approaches)

    def window(book, row_from, row_to, seeds):
        rows = [r for r in full if row_from <= r["ts"] <= row_to]
        seed_rows = minute_rows([(ts, side, lid, price) for ts, side, lid, price in seeds])
        rows = seed_rows + rows
        ts = np.asarray(sorted({r["ts"] for r in rows}), dtype=np.int64)
        nan = np.full(len(ts), np.nan)
        return R0L.MinuteFrame(rows=rows, ts=ts, high=nan, low=nan, close=nan, imb_1m=nan,
                               imb_3m=nan, index_of={int(t): i for i, t in enumerate(ts)})

    setter(rc_module, "KlineBook", FakeBook)
    setter(rc_module, "window_minute_frame", window)
