"""Bars and ATR for Price Structure R0.

Contract: ``docs/crypto/price_structure_r0/CONTRACT_V1.md`` sections 3 and 4.

A bar of any timeframe is known at its close and not one instant earlier. Higher timeframes are
aggregated from 1m on UTC aligned boundaries and a partial bar is never exposed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
RAW = ROOT / "data/research/crypto/price_structure_r0/raw"
D2_KLINE = ROOT / "data/runtime/crypto/BTCUSDT/historical/kline_1m"

MINUTE_MS = 60_000
WARMUP_START_MS = 1_786_492_800_000  # 2026-08-12T00:00:00Z
REPLAY_START_MS = 1_788_220_800_000  # 2026-09-01T00:00:00Z
REPLAY_END_MS = 1_791_072_000_000  # 2026-10-04T00:00:00Z exclusive
ATR_PERIOD = 14


@dataclass(frozen=True)
class Bars:
    """OHLCV for one timeframe. Row i is the bar that opens at ts[i] and closes at ts[i]+step_ms."""
    tf: str
    step_ms: int
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    atr: np.ndarray

    def __len__(self) -> int:
        return len(self.ts)

    def close_ms(self, i: int) -> int:
        return int(self.ts[i]) + self.step_ms


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = ATR_PERIOD) -> np.ndarray:
    """Wilder ATR on completed bars. NaN until `period` true ranges exist."""
    n = len(close)
    tr = np.full(n, np.nan)
    if n > 1:
        prev = close[:-1]
        tr[1:] = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - prev), np.abs(low[1:] - prev)))
    atr = np.full(n, np.nan)
    if n > period:
        atr[period] = float(np.mean(tr[1:period + 1]))
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def load_1m() -> Bars:
    files = sorted(RAW.glob("kline_1m_*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"expected exactly one raw 1m file, found {len(files)}")
    ts, o, h, l, c, v, q = [], [], [], [], [], [], []
    with open(files[0]) as fh:
        for line in fh:
            r = json.loads(line)
            ts.append(r["timestamp_ms"]); o.append(float(r["open"])); h.append(float(r["high"]))
            l.append(float(r["low"])); c.append(float(r["close"])); v.append(float(r["volume"]))
            q.append(float(r["turnover"]))
    ts = np.asarray(ts, dtype=np.int64)
    if not np.array_equal(ts, np.arange(ts[0], ts[-1] + MINUTE_MS, MINUTE_MS)):
        raise RuntimeError("1m grid has a gap")
    arrs = [np.asarray(a, dtype=float) for a in (o, h, l, c, v, q)]
    return Bars("1m", MINUTE_MS, ts, *arrs, wilder_atr(arrs[1], arrs[2], arrs[3]))


def aggregate(b1: Bars, tf: str, factor: int) -> Bars:
    """Fold `factor` consecutive 1m bars into one. Requires UTC aligned, complete groups."""
    step = MINUTE_MS * factor
    if int(b1.ts[0]) % step != 0:
        raise RuntimeError(f"{tf}: 1m series does not start on a {tf} boundary")
    usable = (len(b1) // factor) * factor
    g = lambda a: a[:usable].reshape(-1, factor)
    ts = b1.ts[:usable:factor]
    o = g(b1.open)[:, 0]
    h = g(b1.high).max(axis=1)
    l = g(b1.low).min(axis=1)
    c = g(b1.close)[:, -1]
    v = g(b1.volume).sum(axis=1)
    q = g(b1.turnover).sum(axis=1)
    return Bars(tf, step, ts, o, h, l, c, v, q, wilder_atr(h, l, c))


def load() -> dict[str, Bars]:
    b1 = load_1m()
    return {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}


def provenance() -> dict:
    raw = sorted(RAW.glob("kline_1m_*.jsonl"))[0]
    man = json.loads(raw.with_suffix(".manifest.json").read_text())
    return {"raw_file": raw.name, "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "manifest": man}


def crosscheck_d2() -> dict:
    """Compare the overlap with the frozen D2 history field by field (contract section 3)."""
    raw = sorted(RAW.glob("kline_1m_*.jsonl"))[0]
    fresh = {}
    with open(raw) as fh:
        for line in fh:
            r = json.loads(line)
            fresh[r["timestamp_ms"]] = r
    lo = min(fresh)
    d2files = sorted(D2_KLINE.glob("*.jsonl"))
    if len(d2files) != 1:
        raise RuntimeError("expected exactly one D2 kline file")
    compared = 0
    mismatches: list[dict] = []
    with open(d2files[0]) as fh:
        for line in fh:
            if '"timestamp_ms":17' not in line:
                continue
            r = json.loads(line)
            t = r["timestamp_ms"]
            if t < lo or t not in fresh:
                continue
            compared += 1
            for f in ("open", "high", "low", "close", "volume", "turnover"):
                if float(r[f]) != float(fresh[t][f]):
                    mismatches.append({"timestamp_ms": t, "field": f,
                                       "d2": r[f], "fresh": fresh[t][f]})
    return {"overlap_rows_compared": compared, "mismatches": len(mismatches),
            "first_mismatches": mismatches[:5]}
