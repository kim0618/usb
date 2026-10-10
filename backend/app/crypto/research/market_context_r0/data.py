"""Bars, taker flow and the venue cross check for Market Context R0.

The bar semantics are R0's and are imported rather than restated: a bar of any timeframe is known
at its close, higher timeframes fold 1m on UTC aligned boundaries, and a partial bar is never
exposed. What this module adds is the per minute aggressive side split that Binance publishes with
every kline (``taker_buy_base``), which is the only Layer C signal that exists for the whole
calendar window rather than for the three and a half hours the journal covers.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..price_structure_r0.data import Bars, aggregate, wilder_atr

ROOT = Path(__file__).resolve().parents[5]
RAW = ROOT / "data/research/crypto/market_context_r0/raw"
BYBIT_RAW = ROOT / "data/research/crypto/price_structure_r0/raw"

MINUTE_MS = 60_000
WARMUP_START_MS = 1_786_492_800_000  # 2026-08-12T00:00:00Z, same as R0
REPLAY_START_MS = 1_788_220_800_000  # 2026-09-01T00:00:00Z, same as R0
SERIES_END_MS = 1_791_100_800_000  # 2026-10-04T08:00:00Z exclusive


@dataclass(frozen=True)
class TakerFlow:
    """Per 1m aggressive side split, aligned to ``Bars`` rows of the 1m grid."""

    ts: np.ndarray
    buy_base: np.ndarray
    sell_base: np.ndarray
    buy_quote: np.ndarray
    sell_quote: np.ndarray
    trades: np.ndarray

    def imbalance_base(self, i: int) -> float:
        """(buy - sell) / (buy + sell) on base volume, NaN on an empty minute."""
        tot = self.buy_base[i] + self.sell_base[i]
        if tot <= 0:
            return float("nan")
        return float((self.buy_base[i] - self.sell_base[i]) / tot)


def _rows() -> list[dict]:
    files = sorted(RAW.glob("binance_kline_1m_*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"expected exactly one raw 1m file, found {len(files)}")
    with open(files[0]) as fh:
        return [json.loads(line) for line in fh]


def load_1m() -> Bars:
    rows = _rows()
    ts = np.asarray([r["timestamp_ms"] for r in rows], dtype=np.int64)
    if not np.array_equal(ts, np.arange(ts[0], ts[-1] + MINUTE_MS, MINUTE_MS)):
        raise RuntimeError("1m grid has a gap")
    f = lambda k: np.asarray([float(r[k]) for r in rows], dtype=float)
    o, h, l, c, v, q = f("open"), f("high"), f("low"), f("close"), f("volume"), f("turnover")
    return Bars("1m", MINUTE_MS, ts, o, h, l, c, v, q, wilder_atr(h, l, c))


def load_flow() -> TakerFlow:
    rows = _rows()
    ts = np.asarray([r["timestamp_ms"] for r in rows], dtype=np.int64)
    f = lambda k: np.asarray([float(r[k]) for r in rows], dtype=float)
    vol, quote = f("volume"), f("turnover")
    buy_b, buy_q = f("taker_buy_base"), f("taker_buy_quote")
    sell_b, sell_q = vol - buy_b, quote - buy_q
    if float(np.min(sell_b)) < -1e-6 or float(np.min(sell_q)) < -1e-3:
        raise RuntimeError("taker buy volume exceeds total volume")
    return TakerFlow(ts, buy_b, np.maximum(sell_b, 0.0), buy_q, np.maximum(sell_q, 0.0),
                     f("trades"))


def load() -> dict[str, Bars]:
    b1 = load_1m()
    return {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}


def provenance() -> dict:
    raw = sorted(RAW.glob("binance_kline_1m_*.jsonl"))[0]
    man = json.loads(raw.with_suffix(".manifest.json").read_text())
    return {"raw_file": raw.name, "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "manifest": man}


def venue_basis() -> dict:
    """Compare the Binance series with the frozen Bybit series minute by minute.

    This is the number that decides whether the structure layer could have been left on Bybit. It
    is reported, not acted on: the study reads one venue either way.
    """
    bybit_files = sorted(BYBIT_RAW.glob("kline_1m_*.jsonl"))
    if len(bybit_files) != 1:
        raise RuntimeError("expected exactly one frozen Bybit 1m file")
    bybit: dict[int, dict] = {}
    with open(bybit_files[0]) as fh:
        for line in fh:
            r = json.loads(line)
            bybit[r["timestamp_ms"]] = r
    diffs: list[float] = []
    rel: list[float] = []
    compared = 0
    for r in _rows():
        t = r["timestamp_ms"]
        b = bybit.get(t)
        if b is None:
            continue
        compared += 1
        d = float(r["close"]) - float(b["close"])
        diffs.append(d)
        rel.append(abs(d) / float(b["close"]))
    if not diffs:
        raise RuntimeError("no overlapping minutes between venues")
    a = np.asarray(diffs)
    rl = np.asarray(rel)
    return {"overlap_minutes": compared,
            "close_diff_usdt": {"mean": round(float(a.mean()), 4),
                                "median": round(float(np.median(a)), 4),
                                "p95_abs": round(float(np.percentile(np.abs(a), 95)), 4),
                                "max_abs": round(float(np.abs(a).max()), 4)},
            "close_diff_pct_of_price": {"median": round(float(np.median(rl)) * 100, 6),
                                        "p95": round(float(np.percentile(rl, 95)) * 100, 6),
                                        "max": round(float(rl.max()) * 100, 6)},
            "bybit_series_last_ms": max(bybit), "binance_series_last_ms": max(
                r["timestamp_ms"] for r in _rows())}
