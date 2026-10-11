"""STRUCTURE for the BTC context strip, computed outside the API process.

The 31b6f2d API held the whole anchored 1m series as kline dicts (about 38 MB for 30 days) and
re-ran the R1 engine over all of it once a minute, inside the process that answers the screen.
This module moves that work into a short-lived worker and changes nothing about what is computed:

* **Same engine, same book.** The first build is `ContextEngine.run()` - R1's own loop over the
  whole anchored series, the frozen thetas, the same 30-day day-floored anchor.
* **Forward steps are R1's loop body.** R1 is a single forward pass whose state lives in its
  objects (swings, level book, retest pool, range, session VWAP, audit). `IncrementalEngine.step_to`
  adopts a 1m series that extends the one already processed and runs exactly the per-minute body
  of `Engine.run` for the new minutes only. Publication (`snapshot`) reads the book and does not
  change it, so where the publish window sits does not move any level.
* **Prefix-checked.** A forward step refuses a series whose first minutes differ in any field from
  the ones already processed, and refuses any gap. `tests/crypto/test_btc_context_ui_v1_structure.py`
  compares the forward-stepped engine with a full R1 re-run at every published minute.
* **Checkpoint.** The engine is pickled with a fingerprint of every source file that defines it;
  any change to that code, the symbol or the anchor discards the checkpoint and rebuilds from the
  venue, which is exactly what the old process did at every start.

What leaves this module for the API is a small JSON (`snapshot_payload`): the newest closed minute
and its nearest R1 resistance and support. Written to a temporary file, flushed, fsynced and
renamed over the old one, so a reader sees the previous file or the new one and never a part.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from ..market_context_v1 import contract as C
from ..market_context_v1 import structure as STR
from ..market_context_v1 import venue as VN
from ..research.market_context_r0.structure import ContextEngine, prev_session_levels
from ..research.price_structure_r0.data import Bars, aggregate, wilder_atr
from ..research.price_structure_r1.engine import FIVE_MS, HOUR_MS, MINUTE_MS, EngineConfig

SYMBOL = "BTCUSDT"
SNAPSHOT_NAME = "structure_snapshot.json"
CHECKPOINT_NAME = "structure_engine.pickle"
#: Rows kept on the engine between steps. Only the newest is published; a few more keep the
#: rows list bounded without hiding what the last step produced.
KEEP_ROWS = 10
#: The same anchor rule as the 31b6f2d API: 30 days back, floored to a UTC day.
ANCHOR_DAYS = 30
#: The same published window as the 31b6f2d API, for the first build.
PUBLISH_WINDOW_MS = 10 * 60_000
#: Kline fields, in the order the arrays are kept.
FIELDS = ("open", "high", "low", "close", "volume", "turnover")

_HERE = Path(__file__).resolve()
_BACKEND_APP = _HERE.parents[2]
#: Every source file whose code decides a level. A checkpoint written by any other version of
#: them is not reused.
FINGERPRINT_FILES = (
    "crypto/btc_context_ui_v1/structure_state.py",
    "crypto/market_context_v1/structure.py",
    "crypto/market_context_v1/contract.py",
    "crypto/research/market_context_r0/structure.py",
    "crypto/research/price_structure_r0/data.py",
    "crypto/research/price_structure_r0/swings.py",
    "crypto/research/price_structure_r0/vwap.py",
    "crypto/research/price_structure_r1/engine.py",
    "crypto/research/price_structure_r1/events.py",
    "crypto/research/price_structure_r1/levels.py",
)


class StructureStateError(RuntimeError):
    """A series or checkpoint this module will not build on."""


def code_fingerprint() -> str:
    digest = hashlib.sha256()
    for rel in FINGERPRINT_FILES:
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update((_BACKEND_APP / rel).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------- series


def rows_to_arrays(rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    """Kline rows as the arrays `STR.build_bars` would make of them (same `float()` of each field)."""
    out = {"ts": np.asarray([int(row["timestamp_ms"]) for row in rows], dtype=np.int64)}
    for key in FIELDS:
        out[key] = np.asarray([float(row[key]) for row in rows], dtype=float)
    return out


def concat_arrays(head: dict[str, np.ndarray], tail: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {key: np.concatenate([head[key], tail[key]]) for key in head}


def bars_from_arrays(arrays: dict[str, np.ndarray]) -> dict[str, Bars]:
    """`STR.build_bars` from arrays instead of dicts: the same checks, the same construction."""
    ts = arrays["ts"]
    if len(ts) < 2:
        raise VN.VenueError(f"SERIES_HAS_TOO_FEW_BARS:{len(ts)}")
    if not np.array_equal(ts, np.arange(ts[0], ts[-1] + C.MINUTE_MS, C.MINUTE_MS)):
        raise VN.VenueError("SERIES_GRID_HAS_A_GAP")
    o, h, l = arrays["open"], arrays["high"], arrays["low"]
    c, v, q = arrays["close"], arrays["volume"], arrays["turnover"]
    b1 = Bars("1m", C.MINUTE_MS, ts, o, h, l, c, v, q, wilder_atr(h, l, c))
    return {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}


def arrays_of(b1: Bars) -> dict[str, np.ndarray]:
    return {"ts": b1.ts, "open": b1.open, "high": b1.high, "low": b1.low, "close": b1.close,
            "volume": b1.volume, "turnover": b1.turnover}


def fetch_arrays(symbol: str, start_ms: int, end_ms: int, *, opener: Any = None) -> dict[str, np.ndarray]:
    """`[start_ms, end_ms)` of closed 1m bars, fetched a UTC day at a time so that no more than one
    day of kline dicts is alive at once."""
    parts: list[dict[str, np.ndarray]] = []
    cursor = start_ms
    while cursor < end_ms:
        stop = min(end_ms, cursor - cursor % C.DAY_MS + C.DAY_MS)
        rows = VN.klines(symbol, cursor, stop, opener=opener)
        if rows:
            parts.append(rows_to_arrays(rows))
        cursor = stop
    if not parts:
        return rows_to_arrays([])
    out = parts[0]
    for part in parts[1:]:
        out = concat_arrays(out, part)
    return out


# --------------------------------------------------------------------------- engine


class IncrementalEngine(ContextEngine):
    """`ContextEngine` that can be carried forward one closed minute at a time."""

    def __init__(self, bars: dict[str, Bars], cfg: EngineConfig, *, anchor_ms: int,
                 symbol: str = SYMBOL) -> None:
        ContextEngine.__init__(self, bars, cfg)
        self.anchor_ms = anchor_ms
        self.symbol = symbol
        self.processed = 0

    @classmethod
    def build(cls, bars: dict[str, Bars], *, anchor_ms: int, symbol: str = SYMBOL,
              publish_window_ms: int = PUBLISH_WINDOW_MS) -> "IncrementalEngine":
        """The authoritative state: R1's own `run()` over the whole series."""
        b1 = bars["1m"]
        last_open = int(b1.ts[-1])
        publish_to = last_open + MINUTE_MS
        publish_from = max(int(b1.ts[0]) + MINUTE_MS, publish_to - publish_window_ms)
        engine = cls(bars, EngineConfig(C.THETA_1H, C.THETA_5M, publish_from, publish_to),
                     anchor_ms=anchor_ms, symbol=symbol)
        engine.run()
        engine.processed = len(b1)
        engine.trim()
        return engine

    @property
    def last_open_ms(self) -> int:
        return int(self.b1.ts[self.processed - 1])

    def trim(self) -> None:
        del self.rows[:-KEEP_ROWS]

    def _check_prefix(self, bars: dict[str, Bars]) -> None:
        n = self.processed
        new = bars["1m"]
        if len(new) < n:
            raise StructureStateError("SERIES_SHORTER_THAN_PROCESSED")
        for name in ("ts", "open", "high", "low", "close", "volume", "turnover", "atr"):
            if not np.array_equal(getattr(new, name)[:n], getattr(self.b1, name)[:n],
                                  equal_nan=name == "atr"):
                raise StructureStateError(f"SERIES_PREFIX_DIFFERS:{name}")
        for tf, old in (("5m", self.b5), ("1h", self.bh)):
            m = len(old)
            fresh = bars[tf]
            if len(fresh) < m or not np.array_equal(fresh.ts[:m], old.ts[:m]) \
                    or not np.array_equal(fresh.atr[:m], old.atr[:m], equal_nan=True):
                raise StructureStateError(f"SERIES_PREFIX_DIFFERS:{tf}")

    def step_to(self, bars: dict[str, Bars]) -> int:
        """Adopt `bars` (the processed series plus newer closed minutes) and run R1's per-minute
        body over the new minutes. Returns how many minutes were stepped."""
        self._check_prefix(bars)
        start, stop = self.processed, len(bars["1m"])
        if stop == start:
            return 0
        self.b1, self.b5, self.bh = bars["1m"], bars["5m"], bars["1h"]
        self._prev_sessions = prev_session_levels(self.b1)
        self._session_day = None
        self._session_cache = None
        self.cfg.publish_from_ms = int(self.b1.ts[start]) + MINUTE_MS
        self.cfg.publish_to_ms = int(self.b1.ts[stop - 1]) + MINUTE_MS
        for i in range(start, stop):
            self._minute(i)
        self.processed = stop
        self.trim()
        return stop - start

    def _minute(self, i: int) -> None:
        """The body of `Engine.run`'s loop for minute `i`, statement for statement, without the
        run-wide statistics counters (they feed R1's study report, not the book)."""
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
            return
        snap = self.snapshot(i, at_ms)
        self._audit_snapshot(snap, at_ms)


# --------------------------------------------------------------------------- output


def snapshot_payload(engine: IncrementalEngine, *, written_ms: int) -> dict[str, Any]:
    """The file the API reads. Only what the strip needs, plus enough to audit it."""
    row = engine.rows[-1]

    def side(tag: str) -> dict[str, Any]:
        return {"price": row.get(f"{tag}_price"), "distance_pct": row.get(f"{tag}_distance_pct"),
                "level_id": row.get(f"{tag}_id")}

    return {
        "symbol": engine.symbol,
        "bar_open_ms": int(row["ts"]),
        "bar_close_ms": int(row["info_ts"]),
        "close": row["close"],
        "resistance": side("res"),
        "support": side("sup"),
        "anchor_ms": engine.anchor_ms,
        "series_rows": engine.processed,
        "repaint_violations": len(engine.audit.violations),
        "vanished_levels": len(engine.audit.vanished),
        "written_ms": written_ms,
    }


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Temporary file in the same directory, flushed and fsynced, renamed over `path`, then the
    directory fsynced. A reader opens either the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def save_checkpoint(path: Path, engine: IncrementalEngine) -> None:
    blob = pickle.dumps({"fingerprint": code_fingerprint(), "symbol": engine.symbol,
                         "anchor_ms": engine.anchor_ms, "engine": engine},
                        protocol=pickle.HIGHEST_PROTOCOL)
    atomic_write_bytes(path, blob)


def load_checkpoint(path: Path, *, symbol: str = SYMBOL) -> IncrementalEngine | None:
    """The saved engine, or `None` if there is none or it was written by other code. The file is
    this worker's own, in a root-only directory; it is never read from anywhere else."""
    try:
        with open(path, "rb") as handle:
            blob = pickle.load(handle)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not isinstance(blob, dict) or blob.get("fingerprint") != code_fingerprint() \
            or blob.get("symbol") != symbol:
        return None
    engine = blob.get("engine")
    if not isinstance(engine, IncrementalEngine) or engine.anchor_ms != blob.get("anchor_ms") \
            or engine.processed < 2:
        return None
    return engine


__all__ = ["IncrementalEngine", "StructureStateError", "rows_to_arrays", "bars_from_arrays",
           "concat_arrays", "arrays_of", "fetch_arrays", "snapshot_payload", "write_snapshot",
           "save_checkpoint", "load_checkpoint", "atomic_write_bytes", "code_fingerprint",
           "SNAPSHOT_NAME", "CHECKPOINT_NAME", "ANCHOR_DAYS", "SYMBOL"]
