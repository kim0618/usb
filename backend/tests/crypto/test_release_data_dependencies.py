from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from app.crypto.historical import KNOWN_EARLIEST_MS, CoverageError, collect_series
from app.crypto.models import decimal_string
from app.crypto.rest import BybitPublicClient
from app.crypto.storage import JsonlDatasetWriter, sha256_file

def response(rows: list, cursor: str = "") -> dict:
    return {"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}}

class FakeClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = iter(pages); self.telemetry = type("T", (), {"requests": 0, "retries": 0, "rate_limits": 0})()
    def get(self, *_args, **_kwargs): self.telemetry.requests += 1; return next(self.pages)

def kline(ts: int, volume: str = "1") -> list[str]: return [str(ts), "10", "12", "9", "11", volume, "10"]

def test_decimal_parsing_is_exact_and_rejects_non_finite() -> None:
    assert Decimal(decimal_string("0.100000000000000001")) == Decimal("0.100000000000000001")
    with pytest.raises(ValueError): decimal_string("NaN")
    assert KNOWN_EARLIEST_MS["mark_1m"] == 1_585_125_660_000
    assert KNOWN_EARLIEST_MS["index_1m"] == 1_585_123_020_000

def test_cursor_pagination_duplicate_safe_manifest_and_checksum(tmp_path: Path) -> None:
    start = 1_585_132_560_000
    client = FakeClient([response([kline(start + 60_000), kline(start)], "next"), response([kline(start + 120_000), kline(start + 60_000)])])
    result = collect_series(client, dataset="kline_1m", start_ms=start, end_ms=start + 180_000, root=tmp_path)
    assert result.rows == 3 and result.duplicates_ignored == 1
    assert sha256_file(result.path) == result.checksum
    manifest = json.loads(next((tmp_path / "manifest").glob("*.json")).read_text())
    assert manifest["row_count"] == 3 and ".partial" not in manifest["relative_path"]

def test_resume_and_no_silent_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"; writer = JsonlDatasetWriter(path)
    writer.append([{"timestamp_ms": 1}, {"timestamp_ms": 2}])
    resumed = JsonlDatasetWriter(path); assert resumed.resume_state() == ({1, 2}, 2)
    resumed.finalize()
    with pytest.raises(FileExistsError): JsonlDatasetWriter(path)

def test_oi_silent_truncation_detected(tmp_path: Path) -> None:
    start = 1_596_527_400_000
    client = FakeClient([response([{"timestamp": str(start + 900_000), "openInterest": "1"}])])
    with pytest.raises(CoverageError, match="silent truncation"):
        collect_series(client, dataset="open_interest_5m", start_ms=start, end_ms=start + 1_200_000, root=tmp_path)

def test_retry_and_rate_limit_telemetry() -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls; calls += 1
        return httpx.Response(429 if calls == 1 else 200, request=request, json={"retCode": 0, "result": {}})
    client = BybitPublicClient(requests_per_second=1_000_000, max_retries=1, transport=httpx.MockTransport(handler), sleeper=lambda _: None)
    assert client.get("/x", {})["retCode"] == 0
    assert client.telemetry.retries == 1 and client.telemetry.rate_limits == 1

def test_resume_truncates_a_torn_trailing_line(tmp_path: Path) -> None:
    path = tmp_path / "torn.jsonl"
    writer = JsonlDatasetWriter(path)
    writer.append([{"timestamp_ms": 1}, {"timestamp_ms": 2}])
    with writer.partial_path.open("ab") as stream:
        stream.write(b'{"timestamp_ms": 3')  # a hard crash mid-write leaves no newline

    resumed = JsonlDatasetWriter(path)
    assert resumed.resume_state() == ({1, 2}, 2)
    resumed.append([{"timestamp_ms": 3}])
    rows, _ = resumed.finalize()
    assert rows == 3
    assert [json.loads(line)["timestamp_ms"] for line in path.open()] == [1, 2, 3]
