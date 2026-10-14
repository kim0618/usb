from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


def _month_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).strftime("%Y-%m")


class _MonthAccumulator:
    """Per-month counters kept bounded so a multi-million-row file never lands in RAM."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.seen: set[int] = set()
        self.rows = 0
        self.zero_volume = 0
        self.invalid_ohlc = 0
        self.volume_sum = Decimal(0)
        self.volume_min: Decimal | None = None
        self.volume_max: Decimal | None = None
        self.first_ms: int | None = None
        self.last_ms: int | None = None

    def add(self, row: dict[str, Any]) -> None:
        ts = int(row["timestamp_ms"])
        self.rows += 1
        self.seen.add(ts)
        self.first_ms = ts if self.first_ms is None else min(self.first_ms, ts)
        self.last_ms = ts if self.last_ms is None else max(self.last_ms, ts)
        volume = Decimal(row["volume"])
        self.volume_sum += volume
        self.volume_min = volume if self.volume_min is None else min(self.volume_min, volume)
        self.volume_max = volume if self.volume_max is None else max(self.volume_max, volume)
        if volume == 0:
            self.zero_volume += 1
        low, high = Decimal(row["low"]), Decimal(row["high"])
        if not (low <= Decimal(row["open"]) <= high and low <= Decimal(row["close"]) <= high and low <= high):
            self.invalid_ohlc += 1

    def summary(self) -> dict[str, Any]:
        year, month = map(int, self.key.split("-"))
        days = calendar.monthrange(year, month)[1]
        first = datetime.fromtimestamp(self.first_ms / 1000, timezone.utc)  # type: ignore[arg-type]
        last = datetime.fromtimestamp(self.last_ms / 1000, timezone.utc)  # type: ignore[arg-type]
        complete_month = first.day == 1 and first.hour == 0 and first.minute == 0 and last.day == days and last.hour == 23 and last.minute == 59
        expected = days * 1440 if complete_month else (self.last_ms - self.first_ms) // 60_000 + 1  # type: ignore[operator]
        unique = len(self.seen)
        return {
            "complete_month": complete_month,
            "first_ms": self.first_ms,
            "last_ms": self.last_ms,
            "bars_expected": expected,
            "bars_actual": self.rows,
            "missing_count": max(0, expected - unique),
            "duplicate_count": self.rows - unique,
            "zero_volume_count": self.zero_volume,
            "zero_volume_ratio": str(round(Decimal(self.zero_volume) / Decimal(self.rows), 6)) if self.rows else "0",
            "invalid_ohlc": self.invalid_ohlc,
            "volume_min": str(self.volume_min),
            "volume_max": str(self.volume_max),
            "volume_sum": str(self.volume_sum),
        }


def _iter_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def analyze_kline(path: Path, *, zero_volume_threshold: str = "0.001", sustained_months: int = 3) -> dict[str, Any]:
    """Stream a 1m kline dataset and report per-month quality plus research-window evidence."""
    report: dict[str, Any] = {}
    current: _MonthAccumulator | None = None
    previous_ts: int | None = None
    out_of_order = 0
    revisited_months: list[str] = []
    for row in _iter_rows(path):
        ts = int(row["timestamp_ms"])
        if previous_ts is not None and ts < previous_ts:
            out_of_order += 1
        previous_ts = ts
        key = _month_key(ts)
        if current is None or current.key != key:
            if current is not None:
                report[current.key] = current.summary()
            if key in report:
                revisited_months.append(key)
            current = _MonthAccumulator(key)
        current.add(row)
    if current is not None:
        report[current.key] = current.summary()

    threshold = Decimal(zero_volume_threshold)
    strict: str | None = None
    sustained: str | None = None
    run = 0
    for key in sorted(report):
        item = report[key]
        clean = item["missing_count"] == 0 and item["duplicate_count"] == 0 and item["invalid_ohlc"] == 0
        if strict is None and item["complete_month"] and clean and item["zero_volume_count"] == 0:
            strict = key
        if item["complete_month"] and clean and Decimal(item["zero_volume_ratio"]) <= threshold:
            run += 1
            if sustained is None and run >= sustained_months:
                sustained = sorted(report)[sorted(report).index(key) - sustained_months + 1]
        else:
            run = 0
    return {
        "months": report,
        "stream_integrity": {"out_of_order_rows": out_of_order, "revisited_months": revisited_months},
        "research_window_candidates": {
            "strict_zero_volume_free": strict,
            "sustained_low_zero_volume": sustained,
            "sustained_rule": f"first of {sustained_months} consecutive complete months with zero_volume_ratio <= {zero_volume_threshold}, no missing/duplicate/invalid bars",
        },
        "candidate_rule": "mechanical only; recommendation is not an automatic confirmation of the research window",
    }


def overlap_window(manifests: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Common coverage across datasets; None when any dataset cannot overlap the rest."""
    usable = [m for m in manifests if m.get("actual_start_ms") is not None and m.get("actual_end_ms") is not None]
    if not usable:
        return None
    start = max(m["actual_start_ms"] for m in usable)
    end = min(m["actual_end_ms"] for m in usable)
    if start > end:
        return None
    return {
        "start_ms": start,
        "end_ms": end,
        "start_utc": datetime.fromtimestamp(start / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "end_utc": datetime.fromtimestamp(end / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "binding_start_dataset": max(usable, key=lambda m: m["actual_start_ms"])["dataset"],
        "binding_end_dataset": min(usable, key=lambda m: m["actual_end_ms"])["dataset"],
        "datasets": sorted(m["dataset"] for m in usable),
    }
