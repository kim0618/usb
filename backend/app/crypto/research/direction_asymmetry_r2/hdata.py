"""TRAIN + VALIDATION + HOLDOUT readers for the R2 final test (contract CONTRACT_DIRECTION_R2_FINAL_HOLDOUT_V1.md).

Range 2021-02-01T00:00Z .. 2026-09-01T00:00Z (exclusive). Guard: monthly files after 2026-08, daily metrics
after 2026-08-31, and any row at or after END_MS raise HoldoutGuard (September 2026 has no local Binance funding). Every file opened is
logged with its sha256.
"""
from __future__ import annotations

import datetime as dt
import re
import zipfile
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD
from ..positioning_r0 import data as MD

START_MS = PD.TRAIN_START_MS
VAL_START_MS = PD.TRAIN_END_MS                 # 2023-07-01
VAL_END_MS = 1_788_220_800_000                 # 2026-09-01T00:00Z exclusive (name kept for shared code)
HOLDOUT_START_MS = PD.VALIDATION_END_MS         # 2025-04-01
N_MIN = (VAL_END_MS - START_MS) // 60_000
N5 = (VAL_END_MS - START_MS) // 300_000
MONTHS = tuple(f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13) if (2021, 2) <= (y, m) <= (2026, 8))
FIRST_DAY, LAST_DAY = dt.date(2021, 2, 1), dt.date(2026, 8, 31)
_MONTH = re.compile(r"-(\d{4}-\d{2})\.zip$")
_DAY = re.compile(r"-(\d{4}-\d{2}-\d{2})\.zip$")


class HoldoutGuard(RuntimeError):
    pass


def _open_month(path: Path, log: PD.ReadLog) -> list[str]:
    m = _MONTH.search(path.name)
    if not m or m.group(1) not in MONTHS:
        raise HoldoutGuard(f"{path.name} is outside the R2 window")
    log.add(path, "train_val")
    with zipfile.ZipFile(path) as z:
        return z.read(z.namelist()[0]).decode().splitlines()


def _idx(t: int) -> int:
    if t < START_MS or t >= VAL_END_MS:
        raise HoldoutGuard(f"row {t} outside the R2 window")
    return (t - START_MS) // 60_000


def load_klines(log: PD.ReadLog) -> tuple[PD.Volumes, PD.Prices]:
    o, h, lo, c = (np.full(N_MIN, np.nan) for _ in range(4))
    quote = np.zeros(N_MIN, dtype=np.int64)
    taker = np.zeros(N_MIN, dtype=np.int64)
    valid = np.zeros(N_MIN, dtype=bool)
    for month in MONTHS:
        for line in _open_month(PD.MONTHLY / f"BTCUSDT-1m-{month}.zip", log):
            if not line or line.startswith("open_time"):
                continue
            col = line.split(",")
            i = _idx(int(col[0]))
            o[i], h[i], lo[i], c[i] = float(col[1]), float(col[2]), float(col[3]), float(col[4])
            q, b = PD.fixed(col[7]), PD.fixed(col[10])
            if 0 <= b <= q:
                quote[i], taker[i], valid[i] = q, b, True
    if np.isnan(o).any():
        raise ValueError(f"missing price minutes: {int(np.isnan(o).sum())}")
    ts = START_MS + np.arange(N_MIN, dtype=np.int64) * 60_000
    return PD.Volumes(ts=ts, quote=quote, taker=taker, valid=valid), PD.Prices(ts=ts, open=o, high=h, low=lo, close=c)


def load_funding(log: PD.ReadLog) -> list[tuple[int, float]]:
    out = []
    for month in MONTHS:
        for line in _open_month(PD.FUNDING / f"BTCUSDT-fundingRate-{month}.zip", log):
            if not line or line.startswith("calc_time"):
                continue
            t, _, r = line.split(",")
            _idx(int(t))
            out.append((int(t), float(r)))
    return sorted(out)


def load_basis(log: PD.ReadLog) -> np.ndarray:
    closes = {}
    for name, folder in (("mark", PD.MARK), ("index", PD.INDEX)):
        arr = np.full(N_MIN, np.nan)
        for month in MONTHS:
            for line in _open_month(folder / f"BTCUSDT-1m-{month}.zip", log):
                if not line or line.startswith("open_time"):
                    continue
                col = line.split(",")
                arr[_idx(int(col[0]))] = float(col[4])
        closes[name] = arr
    return closes["mark"] / closes["index"] - 1.0


def load_metrics(log: PD.ReadLog) -> MD.Metrics:
    arr = {f: np.full(N5, np.nan) for f in MD.FIELDS}
    present = np.zeros(N5, dtype=bool)
    seen: dict[int, str] = {}
    conflicts: set[int] = set()
    d = FIRST_DAY
    while d <= LAST_DAY:
        p = MD.METRICS / f"BTCUSDT-metrics-{d.isoformat()}.zip"
        d += dt.timedelta(days=1)
        if not p.exists():
            continue
        m = _DAY.search(p.name)
        day = dt.date.fromisoformat(m.group(1))
        if not (FIRST_DAY <= day <= LAST_DAY):
            raise HoldoutGuard(f"{p.name} is outside the R2 window")
        log.add(p, "train_val")
        with zipfile.ZipFile(p) as z:
            lines = z.read(z.namelist()[0]).decode().splitlines()
        for line in lines[1:]:
            if not line:
                continue
            col = line.split(",")
            t = MD._ms(col[0])
            if t < START_MS or t >= VAL_END_MS:
                raise HoldoutGuard(f"metrics row {col[0]} outside the R2 window")
            if (t - START_MS) % 300_000:
                continue
            body = ",".join(col[2:])
            if t in seen:
                if seen[t] != body:
                    conflicts.add(t)
                continue
            seen[t] = body
    for t, body in seen.items():
        if t in conflicts:
            continue
        i = (t - START_MS) // 300_000
        for f, s in zip(MD.FIELDS, body.split(",")):
            arr[f][i] = MD._val(s)
        present[i] = True
    return MD.Metrics(arr=arr, present=present, quality={"rows_unique": int(present.sum()), "expected": int(N5),
                                                         "conflicting_timestamps": len(conflicts)})
