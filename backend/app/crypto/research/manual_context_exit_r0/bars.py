"""Bars per chart timeframe (contract section 2), and the derivatives series (section 1, D1).

Every bar is known at its close. Higher timeframes are UTC aligned aggregations of the frozen D2
1m file (K1, Bybit linear); 15 s bars are built from Bybit's public trade archives (K15), the same
venue (contract R0.1 section 1). A 15 s interval without a trade is a flat bar at the previous close
with volume 0.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.crypto.research.price_structure_r0.data import wilder_atr

ROOT = Path(__file__).resolve().parents[5]
HIST = ROOT / "data/runtime/crypto/BTCUSDT/historical"
K1_PATH = HIST / "kline_1m/1585132560000_1790127239999.jsonl"
FUNDING_PATH = HIST / "funding/1585152000000_1790127239999.jsonl"
INDEX_PATH = HIST / "index_1m/1585123020000_1790127239999.jsonl"
OI_PATH = HIST / "open_interest_5m/1596527400000_1790127239999.jsonl"
TRADES_DIR = HIST / "bybit_trades_daily"
CACHE = ROOT / "data/runtime/crypto/manual_context_exit_r0/cache"

MINUTE_MS = 60_000
DAY_MS = 86_400_000
K1_END_MS = 1_790_121_600_000          # 2026-09-23T00:00:00Z, exclusive
K15_FIRST_DAY_MS = 1_788_912_000_000   # 2026-09-09T00:00:00Z
K15_DAYS = 14
WARMUP_BARS = 1_440

#: Minutes per bar for the timeframes built from K1.
TF_MINUTES = {"1m": 1, "10m": 10, "1h": 60, "4h": 240, "1d": 1_440}
#: Contract section 2 study spans: days before K1_END_MS, or None for all of K1.
STUDY_DAYS = {"1m": 180, "10m": 730, "1h": None, "4h": None, "1d": None}
TIMEFRAMES = ("15s", "1m", "10m", "1h", "4h", "1d")
STEP_MS = {"15s": 15_000, **{tf: m * MINUTE_MS for tf, m in TF_MINUTES.items()}}


@dataclass(frozen=True)
class Series:
    """OHLCV of one timeframe on its real clock. Row i opens at ts[i] and is known at ts[i]+step."""
    tf: str
    step_ms: int
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def atr(self) -> np.ndarray:
        return wilder_atr(self.high, self.low, self.close)

    def slice(self, a: int, b: int) -> "Series":
        s = slice(a, b)
        return Series(self.tf, self.step_ms, self.ts[s], self.open[s], self.high[s], self.low[s],
                      self.close[s], self.volume[s], self.turnover[s])


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(line: str, key: str) -> str:
    i = line.index(f'"{key}":') + len(key) + 3
    j = line.find(",", i)
    k = line.find("}", i)
    end = k if j < 0 else min(j, k)
    return line[i:end].strip('"')


def _load_jsonl(path: Path, keys: tuple[str, ...], name: str) -> dict[str, np.ndarray]:
    """Parse a D2 jsonl once and cache it as npz, keyed by the file's sha256."""
    sha = file_sha256(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{name}_{sha[:16]}.npz"
    if cached.exists():
        with np.load(cached) as z:
            return {k: z[k] for k in z.files}
    cols: dict[str, list] = {k: [] for k in keys}
    with open(path) as fh:
        for line in fh:
            cols["timestamp_ms"].append(int(_num(line, "timestamp_ms")))
            for k in keys[1:]:
                cols[k].append(float(_num(line, k)))
    out = {k: np.asarray(v, dtype=np.int64 if k == "timestamp_ms" else float) for k, v in cols.items()}
    np.savez(cached, **out)
    return out


def load_k1() -> Series:
    raw = _load_jsonl(K1_PATH, ("timestamp_ms", "open", "high", "low", "close", "volume", "turnover"),
                      "k1")
    keep = raw["timestamp_ms"] < K1_END_MS
    ts = raw["timestamp_ms"][keep]
    if not np.array_equal(np.diff(ts), np.full(len(ts) - 1, MINUTE_MS)):
        raise RuntimeError("K1 grid has a gap")
    g = lambda k: raw[k][keep]
    return Series("1m", MINUTE_MS, ts, g("open"), g("high"), g("low"), g("close"), g("volume"),
                  g("turnover"))


def aggregate(b1: Series, tf: str) -> Series:
    """UTC aligned complete groups of 1m bars. The partial group at either end is dropped."""
    m = TF_MINUTES[tf]
    if m == 1:
        return b1
    step = m * MINUTE_MS
    first = int(np.argmax(b1.ts % step == 0))
    usable = ((len(b1) - first) // m) * m
    sl = slice(first, first + usable)
    g = lambda a: a[sl].reshape(-1, m)
    return Series(tf, step, b1.ts[sl][::m], g(b1.open)[:, 0], g(b1.high).max(axis=1),
                  g(b1.low).min(axis=1), g(b1.close)[:, -1], g(b1.volume).sum(axis=1),
                  g(b1.turnover).sum(axis=1))


def k15_days() -> list[Path]:
    days = []
    for d in range(K15_DAYS):
        day = dt.datetime.fromtimestamp((K15_FIRST_DAY_MS + d * DAY_MS) / 1000, dt.timezone.utc)
        days.append(TRADES_DIR / f"BTCUSDT{day:%Y-%m-%d}.csv.gz")
    return days


def bars_from_trades(t_ms: np.ndarray, price: np.ndarray, qty: np.ndarray, start_ms: int,
                     end_ms: int, step_ms: int) -> Series:
    """OHLCV per `step_ms` bucket over [start, end). Empty buckets are flat at the previous close."""
    n = (end_ms - start_ms) // step_ms
    idx = (t_ms - start_ms) // step_ms
    ok = (idx >= 0) & (idx < n)
    idx, price, qty = idx[ok], price[ok], qty[ok]
    o = np.full(n, np.nan); h = np.full(n, np.nan); l = np.full(n, np.nan); c = np.full(n, np.nan)
    v = np.zeros(n); q = np.zeros(n)
    if len(idx):
        # Trades are in time order, so the first/last occurrence of a bucket is its open/close.
        first = np.r_[True, idx[1:] != idx[:-1]]
        last = np.r_[idx[1:] != idx[:-1], True]
        o[idx[first]] = price[first]
        c[idx[last]] = price[last]
        h_s = np.full(n, -np.inf); l_s = np.full(n, np.inf)
        np.maximum.at(h_s, idx, price)
        np.minimum.at(l_s, idx, price)
        has = np.isfinite(h_s)
        h[has] = h_s[has]; l[has] = l_s[has]
        np.add.at(v, idx, qty)
        np.add.at(q, idx, qty * price)
    prev = np.nan
    for i in range(n):
        if np.isnan(c[i]):
            if np.isnan(prev):
                continue
            o[i] = h[i] = l[i] = c[i] = prev
        prev = c[i]
    keep = ~np.isnan(c)
    ts = start_ms + np.arange(n, dtype=np.int64) * step_ms
    first_ok = int(np.argmax(keep))
    s = slice(first_ok, n)
    return Series("15s", step_ms, ts[s], o[s], h[s], l[s], c[s], v[s], q[s])


def load_15s(*, include_rpi: bool = True) -> Series:
    """15 s bars from Bybit public trades. `include_rpi` keeps RPI-flagged trades (the archive's
    last column); which choice reproduces Bybit's own klines is checked in the tests."""
    sha = hashlib.sha256("".join(file_sha256(p) for p in k15_days()).encode()).hexdigest()[:16]
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"k15_bybit_{sha}.npz"
    if not cached.exists():
        ts_l, px_l, qt_l, rpi_l = [], [], [], []
        for path in k15_days():
            with gzip.open(path, "rt") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                it, ip, iq = header.index("timestamp"), header.index("price"), header.index("size")
                ir = header.index("RPI") if "RPI" in header else None
                for row in reader:
                    # Seconds with 4 decimals; rounded to the millisecond, never truncated.
                    ts_l.append(int(round(float(row[it]) * 1000)))
                    px_l.append(float(row[ip])); qt_l.append(float(row[iq]))
                    rpi_l.append(row[ir] == "1" if ir is not None else False)
        t = np.asarray(ts_l, dtype=np.int64)
        order = np.argsort(t, kind="stable")
        np.savez(cached, t=t[order], p=np.asarray(px_l)[order], q=np.asarray(qt_l)[order],
                 r=np.asarray(rpi_l)[order])
    with np.load(cached) as z:
        t, p, q, r = z["t"], z["p"], z["q"], z["r"]
    if not include_rpi:
        t, p, q = t[~r], p[~r], q[~r]
    return bars_from_trades(t, p, q, K15_FIRST_DAY_MS, K15_FIRST_DAY_MS + K15_DAYS * DAY_MS, 15_000)


def series(tf: str, k1: Series | None = None) -> Series:
    """The study series of a timeframe, including its warm-up bars."""
    if tf == "15s":
        return load_15s()
    k1 = k1 if k1 is not None else load_k1()
    s = aggregate(k1, tf)
    days = STUDY_DAYS[tf]
    if days is None:
        return s
    start_ms = K1_END_MS - days * DAY_MS
    first = int(np.searchsorted(s.ts, start_ms)) - WARMUP_BARS
    return s.slice(max(first, 0), len(s))


# --------------------------------------------------------------------------- derivatives (D1)

@dataclass(frozen=True)
class Derivatives:
    funding_ts: np.ndarray
    funding_rate: np.ndarray
    index_ts: np.ndarray
    index_close: np.ndarray
    oi_ts: np.ndarray
    oi: np.ndarray

    def funding_between(self, a_ms: int, b_ms: int) -> float:
        """Sum of funding rates with a_ms < t < b_ms (strictly inside a hold)."""
        i = np.searchsorted(self.funding_ts, a_ms, side="right")
        j = np.searchsorted(self.funding_ts, b_ms, side="left")
        return float(self.funding_rate[i:j].sum())

    def last_funding(self, at_ms: int) -> float:
        i = np.searchsorted(self.funding_ts, at_ms, side="right") - 1
        return float(self.funding_rate[i]) if i >= 0 else float("nan")

    def index_at(self, at_ms: int) -> float:
        """Close of the last index minute known at `at_ms` (minute t is known at t + 60 s)."""
        i = np.searchsorted(self.index_ts, at_ms - MINUTE_MS, side="right") - 1
        return float(self.index_close[i]) if i >= 0 else float("nan")

    def oi_at(self, at_ms: int) -> float:
        """Last 5m open interest value known at `at_ms` (the value stamped t is used from t + 5m)."""
        i = np.searchsorted(self.oi_ts, at_ms - 5 * MINUTE_MS, side="right") - 1
        return float(self.oi[i]) if i >= 0 else float("nan")


def load_derivatives() -> Derivatives:
    f = _load_jsonl(FUNDING_PATH, ("timestamp_ms", "funding_rate"), "funding")
    x = _load_jsonl(INDEX_PATH, ("timestamp_ms", "close"), "index")
    o = _load_jsonl(OI_PATH, ("timestamp_ms", "open_interest"), "oi")
    return Derivatives(f["timestamp_ms"], f["funding_rate"], x["timestamp_ms"], x["close"],
                       o["timestamp_ms"], o["open_interest"])
