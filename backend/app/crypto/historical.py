from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .models import SERIES, SeriesSpec, normalize_row, row_timestamp
from .rest import BybitPublicClient
from .storage import JsonlDatasetWriter, manifest_entry, write_manifest


KNOWN_EARLIEST_MS = {
    "kline_1m": 1_585_132_560_000,
    "funding": 1_585_152_000_000,
    "open_interest_5m": 1_596_527_400_000,
    "mark_1m": 1_585_125_660_000,  # 2020-03-25T08:41:00Z
    "index_1m": 1_585_123_020_000,  # 2020-03-25T07:57:00Z
}


class CoverageError(RuntimeError): pass


@dataclass(frozen=True)
class CollectionResult:
    dataset: str
    path: Path
    rows: int
    first_timestamp_ms: int | None
    last_timestamp_ms: int | None
    checksum: str
    duplicates_ignored: int
    missing_intervals: int


def _page_rows(payload: dict[str, Any]) -> list[Any]:
    rows = payload.get("result", {}).get("list")
    if not isinstance(rows, list):
        raise CoverageError("response result.list is absent")
    return rows


def iter_window(client: BybitPublicClient, spec: SeriesSpec, start_ms: int, end_ms: int) -> Iterator[Any]:
    """Fetch a bounded window and exhaust any provider cursor before advancing."""
    cursor = ""
    cursors: set[str] = set()
    while True:
        params: dict[str, Any] = {**spec.params, "startTime": start_ms, "endTime": end_ms, "limit": spec.limit}
        if cursor:
            params["cursor"] = cursor
        payload = client.get(spec.endpoint, params)
        for row in _page_rows(payload):
            yield row
        next_cursor = str(payload.get("result", {}).get("nextPageCursor", ""))
        if not next_cursor:
            return
        if next_cursor in cursors:
            raise CoverageError("repeated pagination cursor")
        cursors.add(next_cursor); cursor = next_cursor


def collect_series(client: BybitPublicClient, *, dataset: str, start_ms: int, end_ms: int, root: Path, strict_start: bool = True) -> CollectionResult:
    if dataset not in SERIES: raise ValueError(f"unknown dataset: {dataset}")
    if start_ms > end_ms: raise ValueError("start must not exceed end")
    spec = SERIES[dataset]
    target = root / "historical" / dataset / f"{start_ms}_{end_ms}.jsonl"
    writer = JsonlDatasetWriter(target)
    seen, resume_ts = writer.resume_state()
    duplicates = 0
    window_span = spec.interval_ms * spec.limit
    cursor_start = max(start_ms, (resume_ts + spec.interval_ms) if resume_ts is not None else start_ms)
    actual_first = min(seen) if seen else None
    actual_last = max(seen) if seen else None
    while cursor_start <= end_ms:
        window_end = min(end_ms, cursor_start + window_span - 1)
        normalized: list[dict[str, Any]] = []
        raw_rows = list(iter_window(client, spec, cursor_start, window_end))
        raw_rows.sort(key=lambda row: row_timestamp(spec, row))
        for raw in raw_rows:
            ts = row_timestamp(spec, raw)
            if not cursor_start <= ts <= window_end:
                raise CoverageError(f"provider returned out-of-window timestamp {ts}")
            if ts in seen:
                duplicates += 1; continue
            seen.add(ts); normalized.append(normalize_row(spec, raw))
            actual_first = ts if actual_first is None else min(actual_first, ts)
            actual_last = ts if actual_last is None else max(actual_last, ts)
        writer.append(normalized)
        cursor_start = window_end + 1
    earliest = KNOWN_EARLIEST_MS[dataset]
    expected_start = max(start_ms, earliest)
    if strict_start and actual_first is not None and actual_first > expected_start + spec.interval_ms:
        raise CoverageError(f"silent truncation: requested/known start {expected_start}, first returned {actual_first}")
    ordered = sorted(seen)
    missing = sum(max(0, (b - a) // spec.interval_ms - 1) for a, b in zip(ordered, ordered[1:]))
    row_count, checksum = writer.finalize()
    relative = target.relative_to(root).as_posix()
    entry = manifest_entry(dataset=dataset, requested_start_ms=start_ms, requested_end_ms=end_ms, actual_start_ms=actual_first, actual_end_ms=actual_last, relative_path=relative, row_count=row_count, checksum=checksum, requests=client.telemetry.requests, retries=client.telemetry.retries, rate_limits=client.telemetry.rate_limits)
    write_manifest(root, entry)
    return CollectionResult(dataset, target, row_count, actual_first, actual_last, checksum, duplicates, missing)


def fetch_risk_limit(client: BybitPublicClient) -> dict[str, Any]:
    return client.get("/v5/market/risk-limit", {"category": "linear", "symbol": "BTCUSDT"})
