"""Binance spot and USD-M perpetual 1m klines from the D5.2 archive, aligned to the D2 grid.

Two directional axes that D2 alone cannot see: Bybit perp against Binance perp (who moved
first), and Binance spot against Binance perp (whether the move is being led by leverage or by
cash). D5.2's standalone cross-exchange strategy failed, so these enter as directional context
only, in a feature family that is reported separately and never silently merged into the primary
result.

Only what the archive already holds is read. Nothing is downloaded.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import numpy as np

from app.crypto.research import dataset

ARCHIVE = Path("data/runtime/crypto/d5_2/archive/binance")
CACHE = Path("data/runtime/crypto/btc_p2/external_grid_v1.npz")

#: Longest forward fill tolerated before the build refuses. A genuine venue outage of a few
#: hours is survivable; anything larger is a defect, not a gap.
MAX_STALENESS_MINUTES = 360

SOURCES = {
    "um": ("futures/um/monthly/klines/BTCUSDT/1m", "futures/um/daily/klines/BTCUSDT/1m"),
    "spot": ("spot/monthly/klines/BTCUSDT/1m", "spot/daily/klines/BTCUSDT/1m"),
}


class ExternalDataError(RuntimeError):
    """The archive cannot cover the research window."""


def available() -> bool:
    return all((ARCHIVE / monthly).exists() for monthly, _ in SOURCES.values())


#: Anything at or above this is microseconds. Binance switched spot klines to microsecond
#: timestamps from 2025-01 while USD-M futures stayed in milliseconds, and reading the new files
#: as milliseconds lands them in the year 58000: `searchsorted` then forward-fills the last 2024
#: price across two years of grid, which looks like a working series and is not one.
MICROSECOND_FLOOR = 10 ** 15


def _to_milliseconds(value: int) -> int:
    return value // 1000 if value >= MICROSECOND_FLOOR else value


def _read_zip(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Open timestamps and closes from one Binance kline zip.

    Binance added a header row to some months; a line whose first field is not an integer is
    skipped rather than crashing the load.
    """
    stamps, closes = [], []
    with zipfile.ZipFile(path) as archive:
        name = archive.namelist()[0]
        with archive.open(name) as handle:
            for row in csv.reader(io.TextIOWrapper(handle, encoding="utf-8")):
                if not row:
                    continue
                try:
                    stamp = _to_milliseconds(int(row[0]))
                    close = float(row[4])
                except (ValueError, IndexError):
                    continue
                stamps.append(stamp)
                closes.append(close)
    return np.array(stamps, dtype=np.int64), np.array(closes, dtype=np.float64)


def _load_source(monthly: str, daily: str) -> tuple[np.ndarray, np.ndarray]:
    files = sorted((ARCHIVE / monthly).glob("*.zip"))
    files += sorted((ARCHIVE / daily).glob("*.zip")) if (ARCHIVE / daily).exists() else []
    if not files:
        raise ExternalDataError(f"no zips under {monthly}")
    merged: dict[int, float] = {}
    for path in files:
        stamps, closes = _read_zip(path)
        merged.update(zip(stamps.tolist(), closes.tolist()))
    keys = np.array(sorted(merged), dtype=np.int64)
    return keys, np.array([merged[int(k)] for k in keys], dtype=np.float64)


def build(rebuild: bool = False) -> dict[str, np.ndarray]:
    """Binance closes on the D2 research grid, forward-filled from the last known minute.

    A missing Binance minute is filled from the previous one rather than interpolated: carrying
    the last observed price forward is what a live consumer would have had, while interpolation
    would use a price from after the decision.
    """
    if CACHE.exists() and not rebuild:
        with np.load(CACHE) as store:
            return {key: store[key] for key in store.files}

    grid_ts = np.arange(dataset.RESEARCH_START_MS, dataset.RESEARCH_END_MS, dataset.MINUTE_MS,
                        dtype=np.int64)
    out: dict[str, np.ndarray] = {"ts": grid_ts}
    for label, (monthly, daily) in SOURCES.items():
        stamps, closes = _load_source(monthly, daily)
        position = np.searchsorted(stamps, grid_ts, side="right") - 1
        if (position < 0).any():
            raise ExternalDataError(f"{label}: no observation before the research window starts")
        out[f"{label}_close"] = closes[position]
        age = (grid_ts - stamps[position]) // dataset.MINUTE_MS
        out[f"{label}_age_minutes"] = age
        # A silent unit change already produced a two-year forward fill once. Refusing here
        # means the next one stops the build instead of becoming a feature.
        if int(age.max()) > MAX_STALENESS_MINUTES:
            raise ExternalDataError(
                f"{label}: forward fill reaches {int(age.max())} minutes, above the "
                f"{MAX_STALENESS_MINUTES} minute limit; the archive has a hole or a unit change")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, **out)
    return out


def coverage(grid: dict[str, np.ndarray]) -> dict[str, object]:
    return {
        label: {
            "rows": int(len(grid[f"{label}_close"])),
            "stale_over_5m": int((grid[f"{label}_age_minutes"] > 5).sum()),
            "max_age_minutes": int(grid[f"{label}_age_minutes"].max()),
        }
        for label in SOURCES
    }
