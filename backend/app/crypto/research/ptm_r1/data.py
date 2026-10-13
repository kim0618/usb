"""Volume-only readers for PTM-R1 Stage 0.

Only three columns of a Binance kline row are ever parsed: open_time (0), quote_volume (7) and
taker_buy_quote_volume (10). Prices are never parsed, so no code path here can produce a return.
Amounts are exact integers at 1e-5 USDT (the archive's precision), so window sums and the
BUY/SELL/EVEN comparison are exact, as in production (which uses Decimal).

Every file opened goes through `_open_zip`, which refuses anything outside TRAIN except the
parity files (contract section 1, lineage exception) and records what was read.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
ARCHIVE = ROOT / "data/runtime/crypto/d5_2/archive/binance/futures/um"
MONTHLY = ARCHIVE / "monthly/klines/BTCUSDT/1m"
DAILY = ARCHIVE / "daily/klines/BTCUSDT/1m"
REST_COPY = (ROOT / "data/research/crypto/market_context_r0/raw"
             / "binance_kline_1m_1786492800000_1791100800000.jsonl")

MINUTE_MS = 60_000
SCALE_DIGITS = 5
TRAIN_START_MS = 1_612_137_600_000       # 2021-02-01T00:00Z
TRAIN_END_MS = 1_688_169_600_000         # 2023-07-01T00:00Z exclusive
VALIDATION_END_MS = 1_743_465_600_000    # 2025-04-01T00:00Z exclusive
HOLDOUT_END_MS = 1_791_100_800_000       # 2026-10-04T08:00Z exclusive
TRAIN_MONTHS = tuple(f"{y}-{m:02d}" for y in (2021, 2022, 2023) for m in range(1, 13)
                     if (y, m) >= (2021, 2) and (y, m) <= (2023, 6))
PARITY_DAYS = tuple(f"2026-09-{d:02d}" for d in range(1, 23))

_NAME = re.compile(r"BTCUSDT-1m-(\d{4}-\d{2})(?:-(\d{2}))?\.zip$")


class HoldoutGuard(RuntimeError):
    """A read outside TRAIN that the contract does not allow."""


@dataclass
class ReadLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, path: Path, role: str) -> None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        name = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        self.entries.append({"file": name, "role": role, "sha256": digest})


def fixed(text: str) -> int:
    """'123.45' -> 12345000 (1e-5 units). More than 5 decimals is a data problem."""
    whole, _, frac = text.strip().partition(".")
    if len(frac) > SCALE_DIGITS:
        raise ValueError(f"more than {SCALE_DIGITS} decimals: {text!r}")
    return int(whole) * 10 ** SCALE_DIGITS + int(frac.ljust(SCALE_DIGITS, "0") or 0)


def _open_zip(path: Path, role: str, log: ReadLog) -> list[str]:
    m = _NAME.search(path.name)
    if not m:
        raise HoldoutGuard(f"unexpected file {path.name}")
    month, day = m.group(1), m.group(2)
    if role == "train":
        if day is not None or month not in TRAIN_MONTHS:
            raise HoldoutGuard(f"{path.name} is outside TRAIN")
    elif role == "parity":
        if day is None or f"{month}-{day}" not in PARITY_DAYS:
            raise HoldoutGuard(f"{path.name} is not a parity file")
    else:
        raise HoldoutGuard(f"unknown role {role}")
    log.add(path, role)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise ValueError(f"{path.name}: expected one member")
        return z.read(names[0]).decode().splitlines()


def volume_rows(lines: list[str]) -> list[tuple[int, str, str]]:
    """(open_ms, quote, taker_buy_quote) as text; header skipped; price columns untouched."""
    out = []
    for line in lines:
        if not line or line.startswith("open_time"):
            continue
        cols = line.split(",")
        if len(cols) != 12:
            raise ValueError(f"expected 12 columns, got {len(cols)}")
        out.append((int(cols[0]), cols[7], cols[10]))
    return out


@dataclass(frozen=True)
class Volumes:
    """Contiguous 1m grid. `valid` is False for an unreadable minute or taker > quote."""

    ts: np.ndarray        # int64 open_ms
    quote: np.ndarray     # int64, 1e-5 USDT
    taker: np.ndarray     # int64, 1e-5 USDT
    valid: np.ndarray     # bool


def to_volumes(rows: list[tuple[int, str, str]], start_ms: int, end_ms: int) -> Volumes:
    n = (end_ms - start_ms) // MINUTE_MS
    ts = start_ms + np.arange(n, dtype=np.int64) * MINUTE_MS
    quote = np.zeros(n, dtype=np.int64)
    taker = np.zeros(n, dtype=np.int64)
    valid = np.zeros(n, dtype=bool)
    seen = np.zeros(n, dtype=bool)
    for open_ms, q, b in rows:
        if open_ms < start_ms or open_ms >= end_ms:
            raise HoldoutGuard(f"row {open_ms} outside [{start_ms}, {end_ms})")
        i = (open_ms - start_ms) // MINUTE_MS
        if seen[i]:
            raise ValueError(f"duplicate minute {open_ms}")
        seen[i] = True
        try:
            qi, bi = fixed(q), fixed(b)
        except ValueError:
            continue
        if qi < 0 or bi < 0 or bi > qi:
            continue
        quote[i], taker[i], valid[i] = qi, bi, True
    return Volumes(ts=ts, quote=quote, taker=taker, valid=valid & seen)


def load_train(log: ReadLog, months: tuple[str, ...] = TRAIN_MONTHS) -> tuple[Volumes, dict]:
    rows: list[tuple[int, str, str]] = []
    for month in months:
        rows.extend(volume_rows(_open_zip(MONTHLY / f"BTCUSDT-1m-{month}.zip", "train", log)))
    vol = to_volumes(rows, TRAIN_START_MS, TRAIN_END_MS)
    present = len(rows)
    coverage = {
        "start_ms": TRAIN_START_MS, "end_ms_exclusive": TRAIN_END_MS,
        "expected_minutes": int(len(vol.ts)), "rows_read": present,
        "missing_minutes": int(len(vol.ts) - present),
        "invalid_minutes": int((~vol.valid).sum()),
        "files": len(months),
    }
    return vol, coverage


def parity(log: ReadLog) -> dict:
    """Archive daily files vs the REST copy on the two volume fields, as exact integers."""
    arch: dict[int, tuple[int, int]] = {}
    for day in PARITY_DAYS:
        for open_ms, q, b in volume_rows(_open_zip(DAILY / f"BTCUSDT-1m-{day}.zip", "parity", log)):
            arch[open_ms] = (fixed(q), fixed(b))
    log.add(REST_COPY, "parity")
    overlap = diffs = 0
    with open(REST_COPY) as fh:
        for line in fh:
            r = json.loads(line)
            t = r["timestamp_ms"]
            if t in arch:
                overlap += 1
                if arch[t] != (fixed(r["turnover"]), fixed(r["taker_buy_quote"])):
                    diffs += 1
    return {"overlap_minutes": overlap, "differences": diffs,
            "fields": ["quote_volume", "taker_buy_quote_volume"],
            "pass": overlap == 31_680 and diffs == 0}


# --------------------------------------------------------------------------- R1 study: prices, funding

FUNDING = ARCHIVE / "monthly/fundingRate/BTCUSDT"
_FUND_NAME = re.compile(r"BTCUSDT-fundingRate-(\d{4}-\d{2})\.zip$")


@dataclass(frozen=True)
class Prices:
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray


def load_train_prices(log: ReadLog, months: tuple[str, ...] = TRAIN_MONTHS) -> Prices:
    """OHLC of the TRAIN monthly files only (same guard as the volume reader)."""
    n = (TRAIN_END_MS - TRAIN_START_MS) // MINUTE_MS
    o, h, lo, c = (np.full(n, np.nan) for _ in range(4))
    for month in months:
        for line in _open_zip(MONTHLY / f"BTCUSDT-1m-{month}.zip", "train", log):
            if not line or line.startswith("open_time"):
                continue
            cols = line.split(",")
            t = int(cols[0])
            if t < TRAIN_START_MS or t >= TRAIN_END_MS:
                raise HoldoutGuard(f"row {t} outside TRAIN")
            i = (t - TRAIN_START_MS) // MINUTE_MS
            o[i], h[i], lo[i], c[i] = float(cols[1]), float(cols[2]), float(cols[3]), float(cols[4])
    if np.isnan(o).any():
        raise ValueError("missing TRAIN price minutes")
    ts = TRAIN_START_MS + np.arange(n, dtype=np.int64) * MINUTE_MS
    return Prices(ts=ts, open=o, high=h, low=lo, close=c)


def load_train_funding(log: ReadLog, months: tuple[str, ...] = TRAIN_MONTHS) -> list[tuple[int, float]]:
    """(calc_time_ms, rate) for TRAIN months only."""
    out = []
    for month in months:
        path = FUNDING / f"BTCUSDT-fundingRate-{month}.zip"
        m = _FUND_NAME.search(path.name)
        if not m or m.group(1) not in TRAIN_MONTHS:
            raise HoldoutGuard(f"{path.name} is outside TRAIN")
        log.add(path, "train_funding")
        with zipfile.ZipFile(path) as z:
            lines = z.read(z.namelist()[0]).decode().splitlines()
        for line in lines:
            if not line or line.startswith("calc_time"):
                continue
            t, _, rate = line.split(",")
            t = int(t)
            if t < TRAIN_START_MS or t >= TRAIN_END_MS:
                raise HoldoutGuard(f"funding {t} outside TRAIN")
            out.append((t, float(rate)))
    out.sort()
    return out


MARK = ARCHIVE / "monthly/markPriceKlines/BTCUSDT/1m"
INDEX = ARCHIVE / "monthly/indexPriceKlines/BTCUSDT/1m"


def load_train_basis(log: ReadLog, months: tuple[str, ...] = TRAIN_MONTHS) -> np.ndarray:
    """mark close / index close - 1 per TRAIN minute (NaN where either is missing)."""
    n = (TRAIN_END_MS - TRAIN_START_MS) // MINUTE_MS
    closes = {}
    for name, folder in (("mark", MARK), ("index", INDEX)):
        arr = np.full(n, np.nan)
        for month in months:
            for line in _open_zip(folder / f"BTCUSDT-1m-{month}.zip", "train", log):
                if not line or line.startswith("open_time"):
                    continue
                cols = line.split(",")
                t = int(cols[0])
                if t < TRAIN_START_MS or t >= TRAIN_END_MS:
                    raise HoldoutGuard(f"row {t} outside TRAIN")
                arr[(t - TRAIN_START_MS) // MINUTE_MS] = float(cols[4])
        closes[name] = arr
    return closes["mark"] / closes["index"] - 1.0
