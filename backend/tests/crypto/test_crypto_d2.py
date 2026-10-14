from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.crypto.historical import KNOWN_EARLIEST_MS, CoverageError, collect_series
from app.crypto.models import SERIES, decimal_string, parse_risk_tiers
from app.crypto.orderbook import BookGap, LocalOrderBook
from app.crypto.realtime import RealtimeProcessor
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


def test_risk_tier_parser_and_monotonicity() -> None:
    payload = response([{"id": 1, "riskLimitValue": "300000", "initialMargin": "0.0066", "maintenanceMargin": "0.0033", "maxLeverage": "150", "mmDeduction": ""}, {"id": 2, "riskLimitValue": "2000000", "initialMargin": "0.01", "maintenanceMargin": "0.005", "maxLeverage": "100", "mmDeduction": "510"}])
    tiers = parse_risk_tiers(payload); assert tiers[1].lower_exclusive == Decimal("300000")
    payload["result"]["list"][1]["maintenanceMargin"] = "0.001"
    with pytest.raises(ValueError, match="monotonic"): parse_risk_tiers(payload)


def test_orderbook_snapshot_delta_features_and_gap() -> None:
    book = LocalOrderBook(); book.apply("snapshot", {"u": 10, "seq": 20, "b": [["100", "2"]], "a": [["102", "1"]]})
    book.apply("delta", {"u": 11, "seq": 21, "b": [["99", "3"]], "a": []})
    features = book.features(1, 2)
    assert features["mid"] == "101" and features["microprice"] == "101.3333333333333333333333333"
    with pytest.raises(BookGap): book.apply("delta", {"u": 13, "seq": 22, "b": [], "a": []})
    assert not book.ready


def test_trade_execid_kline_confirm_and_one_second_feature(tmp_path: Path) -> None:
    p = RealtimeProcessor(tmp_path)
    trade = {"topic": "publicTrade.BTCUSDT", "ts": 1000, "data": [{"i": "x", "p": "1"}, {"i": "x", "p": "1"}]}
    p.process(trade, 1002); assert p.telemetry.duplicate_trades == 1
    p.process({"topic": "kline.1.BTCUSDT", "ts": 1000, "data": [{"start": 1, "confirm": False}, {"start": 2, "confirm": True}]}, 1002)
    p.process({"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 1000, "data": {"u": 1, "seq": 1, "b": [["1", "1"]], "a": [["2", "1"]]}}, 1002)
    p.process({"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 1500, "data": {"u": 2, "seq": 2, "b": [], "a": []}}, 1502)
    assert sum(1 for _ in p.klines.partial_path.open()) == 1
    assert sum(1 for _ in p.features.partial_path.open()) == 1


def test_retry_and_rate_limit_telemetry() -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls; calls += 1
        return httpx.Response(429 if calls == 1 else 200, request=request, json={"retCode": 0, "result": {}})
    client = BybitPublicClient(requests_per_second=1_000_000, max_retries=1, transport=httpx.MockTransport(handler), sleeper=lambda _: None)
    assert client.get("/x", {})["retCode"] == 0
    assert client.telemetry.retries == 1 and client.telemetry.rate_limits == 1


class FlakySocket:
    """Drops the first connection mid-stream so the reconnect path is exercised for real."""

    def __init__(self, script: list[list[str] | type[Exception]]) -> None:
        self.script = script; self.connections = 0; self.sent: list[str] = []

    def __call__(self) -> "FlakySocket":
        self._frames = iter(self.script[self.connections]) if self.connections < len(self.script) else iter([])
        self.connections += 1
        return self

    async def __aenter__(self) -> "FlakySocket": return self
    async def __aexit__(self, *_: object) -> None: return None
    async def send(self, payload: str) -> None: self.sent.append(payload)

    async def recv(self) -> str:
        frame = next(self._frames, None)
        if frame is None:
            raise ConnectionResetError("stream closed")
        return frame


def test_reconnect_resubscribes_and_resets_local_book(tmp_path: Path) -> None:
    import asyncio

    from app.crypto.realtime import TOPICS, run_realtime

    snapshot = json.dumps({"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 1000,
                           "data": {"u": 1, "seq": 1, "b": [["1", "1"]], "a": [["2", "1"]]}})
    stale_delta = json.dumps({"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 3000,
                              "data": {"u": 2, "seq": 2, "b": [], "a": []}})
    socket = FlakySocket([[snapshot], [stale_delta, snapshot]])
    telemetry = asyncio.run(run_realtime(tmp_path, duration_seconds=0.6, connector=socket,
                                         backoff=lambda _attempt: 0.05))
    assert socket.connections >= 2 and telemetry.reconnects >= 1
    manifests = {json.loads(path.read_text())["dataset"]: json.loads(path.read_text())
                 for path in (tmp_path / "manifest").glob("*_session_*.json")}
    assert set(manifests) == {"realtime_public_trade", "orderbook_1s", "finalized_kline", "ticker_state"}
    book = manifests["orderbook_1s"]
    assert book["provider"] == "bybit" and book["symbol"] == "BTCUSDT" and book["status"] == "COMPLETE"
    assert book["telemetry"]["reconnects"] == telemetry.reconnects and ".partial" not in book["relative_path"]
    assert book["checksum"] and book["session_end_ms"] >= book["session_start_ms"]
    subscribes = [json.loads(frame) for frame in socket.sent]
    assert sum(1 for f in subscribes if f["op"] == "subscribe" and f["args"] == list(TOPICS)) == socket.connections
    # The delta that arrives before the post-reconnect snapshot must be rejected, not silently applied.
    assert telemetry.book_gaps >= 1
    # A gap is only recoverable by resubscribing, so the run loop must have asked for a fresh snapshot.
    assert telemetry.book_resyncs >= 1
    resync = [json.loads(frame) for frame in socket.sent if json.loads(frame)["args"] == ["orderbook.50.BTCUSDT"]]
    assert [f["op"] for f in resync[:2]] == ["unsubscribe", "subscribe"]


def test_quality_report_streams_months_and_flags_zero_volume(tmp_path: Path) -> None:
    from app.crypto.quality import analyze_kline, overlap_window

    path = tmp_path / "kline.jsonl"
    base = 1_609_459_200_000  # 2021-01-01T00:00:00Z
    rows = [{"timestamp_ms": base + i * 60_000, "open": "10", "high": "12", "low": "9", "close": "11",
             "volume": "0" if i < 2 else "1", "turnover": "10"} for i in range(5)]
    rows.append({"timestamp_ms": base + 10 * 60_000, "open": "10", "high": "9", "low": "12",
                 "close": "11", "volume": "1", "turnover": "10"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = analyze_kline(path)
    january = report["months"]["2021-01"]
    assert january["zero_volume_count"] == 2 and january["invalid_ohlc"] == 1
    assert january["bars_actual"] == 6 and january["missing_count"] == 5 and january["complete_month"] is False
    assert report["research_window_candidates"]["strict_zero_volume_free"] is None
    assert report["stream_integrity"]["out_of_order_rows"] == 0

    window = overlap_window([
        {"dataset": "kline_1m", "actual_start_ms": 100, "actual_end_ms": 900},
        {"dataset": "open_interest_5m", "actual_start_ms": 300, "actual_end_ms": 800},
    ])
    assert window is not None
    assert window["start_ms"] == 300 and window["binding_start_dataset"] == "open_interest_5m"
    assert window["binding_end_dataset"] == "open_interest_5m"


def test_realtime_session_id_is_shared_by_every_writer(tmp_path: Path) -> None:
    from app.crypto.realtime import RealtimeProcessor

    p = RealtimeProcessor(tmp_path, session_id=1234567890)
    suffixes = {writer.final_path.stem.rsplit("_", 1)[1] for writer in (p.trades, p.features, p.klines, p.tickers)}
    assert suffixes == {"1234567890"}


def test_ticker_delta_merges_into_snapshot_state(tmp_path: Path) -> None:
    from app.crypto.realtime import RealtimeProcessor

    p = RealtimeProcessor(tmp_path, session_id=1)
    # A delta arriving before any snapshot has nothing to merge into and must not be written.
    p.process({"topic": "tickers.BTCUSDT", "type": "delta", "ts": 900, "data": {"markPrice": "1"}}, 901)
    assert p.telemetry.ticker_deltas_without_snapshot == 1
    assert not p.tickers.partial_path.exists()

    snapshot = {"symbol": "BTCUSDT", "markPrice": "100", "indexPrice": "101", "fundingRate": "0.0001",
                "nextFundingTime": "1790150400000", "openInterest": "60000", "volume24h": "7", "turnover24h": "8"}
    p.process({"topic": "tickers.BTCUSDT", "type": "snapshot", "ts": 1000, "data": snapshot}, 1001)
    p.process({"topic": "tickers.BTCUSDT", "type": "delta", "ts": 2000, "data": {"markPrice": "105"}}, 2001)
    rows = [json.loads(line) for line in p.tickers.partial_path.open()]
    assert len(rows) == 2
    # The delta carried markPrice only; everything else must survive from the snapshot, not go null.
    assert rows[1]["markPrice"] == "105" and rows[1]["indexPrice"] == "101"
    assert rows[1]["openInterest"] == "60000" and rows[1]["fundingRate"] == "0.0001"
    assert rows[1]["changed_fields"] == ["markPrice"] and rows[1]["message_type"] == "delta"
    assert rows[1]["schema_version"] == "crypto.ticker.v2"

    # A reconnect invalidates the merged state, so the next delta must not resurrect stale prices.
    p.on_reconnect()
    p.process({"topic": "tickers.BTCUSDT", "type": "delta", "ts": 3000, "data": {"markPrice": "110"}}, 3001)
    assert p.telemetry.ticker_deltas_without_snapshot == 2
    assert sum(1 for _ in p.tickers.partial_path.open()) == 2


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
