"""TRAIN-only reader for HOT-EDGE R0: 5 m klines and funding of every USDT perpetual on one time grid.

Guard: only monthly files whose month lies in TRAIN are opened; every file opened is logged with its sha256.
Grid: TRAIN 2021-02-01T00:00Z .. 2023-07-01T00:00Z exclusive at 5 m = 253,440 bars. Prices are float32
(relative precision 6e-8, enough for bp-level returns); missing bars are NaN.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
ARCHIVE = ROOT / "data/runtime/crypto/hot_edge_r0/archive/binance/futures/um/monthly"
KLINES = ARCHIVE / "klines"
FUNDING = ARCHIVE / "fundingRate"

BAR_MS = 300_000
TRAIN_START_MS = 1_612_137_600_000       # 2021-02-01T00:00Z
TRAIN_END_MS = 1_688_169_600_000         # 2023-07-01T00:00Z exclusive
N_BARS = (TRAIN_END_MS - TRAIN_START_MS) // BAR_MS
TRAIN_MONTHS = tuple(f"{y}-{m:02d}" for y in (2021, 2022, 2023) for m in range(1, 13)
                     if (2021, 2) <= (y, m) <= (2023, 6))
_K = re.compile(r"^([A-Z0-9]+USDT)-5m-(\d{4}-\d{2})\.zip$")
_F = re.compile(r"^([A-Z0-9]+USDT)-fundingRate-(\d{4}-\d{2})\.zip$")


class HoldoutGuard(RuntimeError):
    pass


@dataclass
class ReadLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, path: Path) -> None:
        self.entries.append({"file": str(path.relative_to(ROOT)),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})


def _lines(path: Path, pattern: re.Pattern, log: ReadLog) -> list[str]:
    m = pattern.match(path.name)
    if not m or m.group(2) not in TRAIN_MONTHS:
        raise HoldoutGuard(f"{path.name} is outside TRAIN")
    log.add(path)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise ValueError(f"{path.name}: expected one member")
        return z.read(names[0]).decode().splitlines()


def train_symbols() -> list[str]:
    """Symbols (ASCII USDT perps) with at least one TRAIN monthly 5 m file on disk."""
    out = []
    for d in sorted(KLINES.iterdir()):
        if not d.name.endswith("USDT") or not _K.match(f"{d.name}-5m-2021-02.zip"):
            continue
        if any((d / "5m" / f"{d.name}-5m-{m}.zip").exists() for m in TRAIN_MONTHS):
            out.append(d.name)
    return out


@dataclass
class Grid:
    symbols: list[str]
    open: np.ndarray      # float32 [n_sym, N_BARS], NaN missing
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    qv: np.ndarray        # quote volume, float32
    funding: dict[str, tuple[np.ndarray, np.ndarray]]   # symbol -> (bar index of settlement, rate)

    @property
    def ts(self) -> np.ndarray:
        return TRAIN_START_MS + np.arange(N_BARS, dtype=np.int64) * BAR_MS


def load_symbol(sym: str, log: ReadLog, grid: Grid, i: int) -> int:
    rows = 0
    for month in TRAIN_MONTHS:
        path = KLINES / sym / "5m" / f"{sym}-5m-{month}.zip"
        if not path.exists():
            continue
        for line in _lines(path, _K, log):
            if not line or line.startswith("open_time"):
                continue
            c = line.split(",")
            t = int(c[0])
            if t < TRAIN_START_MS or t >= TRAIN_END_MS:
                raise HoldoutGuard(f"{sym} bar {t} outside TRAIN")
            j = (t - TRAIN_START_MS) // BAR_MS
            grid.open[i, j], grid.high[i, j], grid.low[i, j], grid.close[i, j] = \
                float(c[1]), float(c[2]), float(c[3]), float(c[4])
            grid.qv[i, j] = float(c[7])
            rows += 1
    idx, rate = [], []
    for month in TRAIN_MONTHS:
        path = FUNDING / sym / f"{sym}-fundingRate-{month}.zip"
        if not path.exists():
            continue
        for line in _lines(path, _F, log):
            if not line or line.startswith("calc_time"):
                continue
            t, _, r = line.split(",")
            t = int(t)
            if t < TRAIN_START_MS or t >= TRAIN_END_MS:
                raise HoldoutGuard(f"{sym} funding {t} outside TRAIN")
            idx.append((t - TRAIN_START_MS) // BAR_MS)
            rate.append(float(r))
    order = np.argsort(idx)
    grid.funding[sym] = (np.asarray(idx, dtype=np.int64)[order], np.asarray(rate, dtype=float)[order])
    return rows


def load_train(log: ReadLog, symbols: list[str] | None = None) -> tuple[Grid, dict]:
    syms = symbols if symbols is not None else train_symbols()
    n = len(syms)
    mk = lambda: np.full((n, N_BARS), np.nan, dtype=np.float32)   # noqa: E731
    grid = Grid(symbols=syms, open=mk(), high=mk(), low=mk(), close=mk(), qv=mk(), funding={})
    rows = {s: load_symbol(s, log, grid, i) for i, s in enumerate(syms)}
    first = {}
    for i, s in enumerate(syms):
        ok = np.flatnonzero(~np.isnan(grid.close[i]))
        first[s] = int(ok[0]) if len(ok) else None
    cov = {"symbols": n, "bars_per_symbol": N_BARS, "rows_total": int(sum(rows.values())),
           "symbols_with_data": sum(1 for v in first.values() if v is not None),
           "first_bar_index": first}
    return grid, cov
