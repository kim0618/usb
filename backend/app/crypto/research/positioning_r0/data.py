"""Binance USD-M BTCUSDT metrics reader, TRAIN only (contract sections 1 and 2).

Only daily files dated inside TRAIN are opened; a row stamped at or after the TRAIN end raises. Rows with the
same create_time and identical payload are deduplicated; a different payload is a DATA_PROBLEM (excluded).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD

ROOT = PD.ROOT
METRICS = PD.ARCHIVE / "daily/metrics/BTCUSDT"
STEP_MS = 300_000
N5 = (PD.TRAIN_END_MS - PD.TRAIN_START_MS) // STEP_MS
FIELDS = ("oi", "oi_value", "ta", "tp", "g", "taker_ratio")
HEADER = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
          "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")
_NAME = re.compile(r"^BTCUSDT-metrics-(\d{4}-\d{2}-\d{2})\.zip$")
TRAIN_FIRST_DAY = dt.date(2021, 2, 1)
TRAIN_LAST_DAY = dt.date(2023, 6, 30)


class HoldoutGuard(RuntimeError):
    pass


def train_days() -> list[dt.date]:
    n = (TRAIN_LAST_DAY - TRAIN_FIRST_DAY).days + 1
    return [TRAIN_FIRST_DAY + dt.timedelta(days=i) for i in range(n)]


def _ms(stamp: str) -> int:
    return int(dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _val(s: str) -> float:
    s = s.strip().strip('"')
    if s == "":
        return np.nan
    v = float(s)
    return v if v > 0 else np.nan


@dataclass
class Metrics:
    arr: dict[str, np.ndarray]                 # each float64 [N5], NaN = missing / undefined
    present: np.ndarray                        # bool [N5]
    quality: dict = field(default_factory=dict)


def open_day(path: Path, log: PD.ReadLog) -> list[str]:
    m = _NAME.match(path.name)
    if not m:
        raise HoldoutGuard(f"unexpected file {path.name}")
    day = dt.date.fromisoformat(m.group(1))
    if not (TRAIN_FIRST_DAY <= day <= TRAIN_LAST_DAY):
        raise HoldoutGuard(f"{path.name} is outside TRAIN")
    log.add(path, "train")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise ValueError(f"{path.name}: expected one member")
        return z.read(names[0]).decode().splitlines()


def parse(lines_by_day: list[list[str]]) -> Metrics:
    arr = {f: np.full(N5, np.nan) for f in FIELDS}
    present = np.zeros(N5, dtype=bool)
    payload: dict[int, str] = {}
    dup_same = dup_conflict = off_grid = 0
    conflicts: set[int] = set()
    for lines in lines_by_day:
        if not lines or lines[0].strip() != HEADER:
            raise ValueError("unexpected metrics header")
        for line in lines[1:]:
            if not line:
                continue
            c = line.split(",")
            if len(c) != 8 or c[1] != "BTCUSDT":
                raise ValueError(f"bad row {line!r}")
            t = _ms(c[0])
            if t < PD.TRAIN_START_MS:
                raise HoldoutGuard(f"row {c[0]} before TRAIN")
            if t >= PD.TRAIN_END_MS:
                raise HoldoutGuard(f"row {c[0]} at or after TRAIN end")
            if (t - PD.TRAIN_START_MS) % STEP_MS:
                off_grid += 1
                continue
            body = ",".join(c[2:])
            if t in payload:
                if payload[t] == body:
                    dup_same += 1
                else:
                    dup_conflict += 1
                    conflicts.add(t)
                continue
            payload[t] = body
    for t, body in payload.items():
        if t in conflicts:
            continue
        i = (t - PD.TRAIN_START_MS) // STEP_MS
        v = body.split(",")
        for f, s in zip(FIELDS, v):
            arr[f][i] = _val(s)
        present[i] = True
    missing = int((~present).sum())
    gaps = _gaps(present)
    quality = {"rows_unique": int(present.sum()), "expected_slots": int(N5), "missing_slots": missing,
               "duplicate_identical": dup_same, "duplicate_conflicting_rows": dup_conflict,
               "conflicting_timestamps_excluded": len(conflicts), "off_grid_rows": off_grid,
               "gaps": gaps,
               "undefined_by_field": {f: int((present & np.isnan(arr[f])).sum()) for f in FIELDS}}
    return Metrics(arr=arr, present=present, quality=quality)


def _gaps(present: np.ndarray) -> dict:
    miss = ~present
    edges = np.flatnonzero(np.diff(np.concatenate(([0], miss.astype(np.int8), [0]))))
    runs = edges[1::2] - edges[0::2]
    starts = edges[0::2]
    longest = sorted(zip(runs.tolist(), starts.tolist()), reverse=True)[:8]
    return {"count": int(len(runs)), "max_slots": int(runs.max()) if len(runs) else 0,
            "longest": [{"slots": r, "start_utc": dt.datetime.fromtimestamp(
                (PD.TRAIN_START_MS + s * STEP_MS) / 1000, dt.timezone.utc).isoformat()} for r, s in longest]}


def load_train(log: PD.ReadLog) -> Metrics:
    days = []
    for d in train_days():
        p = METRICS / f"BTCUSDT-metrics-{d.isoformat()}.zip"
        if not p.exists():
            days.append([HEADER])
            continue
        days.append(open_day(p, log))
    return parse(days)
