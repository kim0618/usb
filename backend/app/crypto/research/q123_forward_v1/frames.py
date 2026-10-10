"""The two frames R0's T1 code reads, built from forward data.

* The minute frame is R1's engine (through R0's `ContextEngine`) over public Binance 1m klines
  anchored at contract section 3's fixed series start. Bars are built with R0's own primitives.
* The second frame is the Context Collector journal, read through the collector's reader, mapped
  into R0's `SecondSample` with the adapter that proved R0 parity (V1.1 report section K).

Nothing here reads an outcome. Both frames can be truncated at a cut so the forward-only property
can be tested by construction rather than asserted.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...context_collector_v1 import reader as CR
from ..market_context_r0 import layers as R0L
from ..market_context_r0.structure import ContextEngine
from ..price_structure_r0.data import Bars, aggregate, wilder_atr
from ..price_structure_r1.engine import EngineConfig
from . import contract as K

MINUTE_MS = 60_000
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
PAGE = 1500


# ----------------------------------------------------------------------------- klines
def _page(start_ms: int, end_ms: int) -> list[list]:
    query = (f"{KLINES_URL}?symbol=BTCUSDT&interval=1m&startTime={start_ms}"
             f"&endTime={end_ms - 1}&limit={PAGE}")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(query, timeout=25) as handle:
                return json.load(handle)
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch_klines(out_dir: Path, end_ms: int, start_ms: int = K.SERIES_START_MS) -> Path:
    """Closed 1m bars in `[start_ms, end_ms)`, public endpoint, one file with a manifest."""
    end_ms -= end_ms % MINUTE_MS
    rows: dict[int, list] = {}
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + PAGE * MINUTE_MS, end_ms)
        for r in _page(cursor, chunk_end):
            ts = int(r[0])
            if start_ms <= ts < end_ms and ts + MINUTE_MS <= int(r[6]) + 1:
                rows[ts] = r
        cursor = chunk_end
        time.sleep(0.12)
    want = list(range(start_ms, end_ms, MINUTE_MS))
    missing = [t for t in want if t not in rows]
    if missing:
        raise RuntimeError(f"{len(missing)} minutes missing, first {missing[0]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"binance_kline_1m_{start_ms}_{end_ms}.jsonl"
    with open(path, "w") as handle:
        for t in want:
            r = rows[t]
            handle.write(json.dumps({"timestamp_ms": t, "open": r[1], "high": r[2], "low": r[3],
                                     "close": r[4], "volume": r[5], "turnover": r[7],
                                     "trades": r[8], "taker_buy_base": r[9],
                                     "taker_buy_quote": r[10]}, sort_keys=True) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".manifest.json").write_text(json.dumps(
        {"source": "binance usdm /fapi/v1/klines symbol=BTCUSDT interval=1m (public)",
         "rows": len(want), "first_timestamp_ms": want[0], "last_timestamp_ms": want[-1],
         "sha256": digest}, indent=2, sort_keys=True) + "\n")
    return path


def load_kline_rows(path: Path, *, closed_by_ms: int | None = None) -> list[dict]:
    """Rows of a kline file, optionally only bars whose close is at or before `closed_by_ms`."""
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if closed_by_ms is not None:
        rows = [r for r in rows if r["timestamp_ms"] + MINUTE_MS <= closed_by_ms]
    return rows


def bars_from_rows(rows: list[dict]) -> dict[str, Bars]:
    """R0 `data.load` semantics on an arbitrary row set (gap check, ATR, 5m and 1h folding)."""
    ts = np.asarray([r["timestamp_ms"] for r in rows], dtype=np.int64)
    if ts.size == 0 or ts[0] != K.SERIES_START_MS:
        raise RuntimeError("kline series must start at the contract's SERIES_START")
    if not np.array_equal(ts, np.arange(ts[0], ts[-1] + MINUTE_MS, MINUTE_MS)):
        raise RuntimeError("1m grid has a gap")
    f = lambda k: np.asarray([float(r[k]) for r in rows], dtype=float)  # noqa: E731
    o, h, l, c, v, q = f("open"), f("high"), f("low"), f("close"), f("volume"), f("turnover")
    b1 = Bars("1m", MINUTE_MS, ts, o, h, l, c, v, q, wilder_atr(h, l, c))
    return {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}


def minute_frame(rows: list[dict]) -> R0L.MinuteFrame:
    """R1 snapshots for every minute from PUBLISH_FROM to the last closed bar."""
    bars = bars_from_rows(rows)
    b1 = bars["1m"]
    last_close = int(b1.ts[-1]) + MINUTE_MS
    engine = ContextEngine(bars, EngineConfig(K.THETAS[0], K.THETAS[1], K.PUBLISH_FROM_MS,
                                              last_close))
    engine.run()
    nan = np.full(len(b1.ts), np.nan)
    return R0L.MinuteFrame(rows=engine.rows, ts=b1.ts, high=b1.high, low=b1.low, close=b1.close,
                           imb_1m=nan, imb_3m=nan,
                           index_of={int(t): i for i, t in enumerate(b1.ts)})


# ----------------------------------------------------------------------------- collector journal
@dataclass
class SecondFrameX:
    """R0's `SecondFrame` plus the per-sample fields the forward gate and diagnostics need."""

    frame: R0L.SecondFrame
    generation: list = field(default_factory=list)
    session_id: list = field(default_factory=list)
    absorption: list = field(default_factory=list)
    liquidity_state: list = field(default_factory=list)
    flow_state: list = field(default_factory=list)


def _sample(second: CR.Second) -> R0L.SecondSample:
    """The adapter of the V1.1 parity proof, verbatim in meaning."""
    c = second.context
    book = c.get("book") or {}
    d01 = next((d for d in (c.get("depth") or []) if d.get("band_pct") == "0.1"), {})
    f60 = ((c.get("flow") or {}).get("windows") or {}).get("60s") or {}
    mid = None if book.get("mid") is None else float(book["mid"])
    cov = d01.get("bid_coverage") or ""
    return R0L.SecondSample(
        ms=second.sample_ms, session8=second.session_id[:8], mid=mid,
        ok=(book.get("state") == "SYNCED" and cov == "COMPLETE" and mid is not None),
        flow60_imb=None if f60.get("imbalance_usdt") is None else float(f60["imbalance_usdt"]),
        flow60_complete=f60.get("coverage") == "COMPLETE",
        band_bid=float(d01["bid_notional"]) if cov == "COMPLETE" else None,
        band_ask=float(d01["ask_notional"]) if d01.get("ask_coverage") == "COMPLETE" else None,
        ask_bins={w["bin_low"]: w for w in second.walls_r0.get("ASK", [])},
        bid_bins={w["bin_low"]: w for w in second.walls_r0.get("BID", [])})


def second_frame(roots: list[Path], *, cut_ms: int | None = None) -> SecondFrameX:
    """Every sample of every collector session under `roots`, ordered by time, optionally cut."""
    seconds: list[CR.Second] = []
    for root in roots:
        seconds.extend(s for s in CR.seconds(Path(root))
                       if cut_ms is None or s.sample_ms <= cut_ms)
    seconds.sort(key=lambda s: (s.sample_ms, s.session_id, s.seq))
    samples = [_sample(s) for s in seconds]
    session_index: dict[str, int] = {}
    for s in seconds:
        session_index.setdefault(s.session_id, len(session_index))
    frame = R0L.SecondFrame(
        samples=samples,
        mids=np.asarray([x.mid if x.mid is not None else np.nan for x in samples]),
        ms=np.asarray([x.ms for x in samples], dtype=np.int64),
        session_of=np.asarray([session_index[s.session_id] for s in seconds], dtype=np.int32))
    ctx = [s.context for s in seconds]
    return SecondFrameX(
        frame=frame,
        generation=[(c.get("book") or {}).get("generation") for c in ctx],
        session_id=[s.session_id for s in seconds],
        absorption=[((c.get("absorption") or {}).get("state")) for c in ctx],
        liquidity_state=[(c.get("liquidity") or {}).get("state") for c in ctx],
        flow_state=[(c.get("flow") or {}).get("state") for c in ctx])
