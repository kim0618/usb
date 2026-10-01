from __future__ import annotations

from app.crypto.terminal.chart_history import BybitChartHistory, SPECS, aggregate_rows


def row(ts, open_, high, low, close, volume):
    return {"start_ms": ts, "open": str(open_), "high": str(high), "low": str(low),
            "close": str(close), "volume": str(volume)}


def test_target_timeframes_use_native_intervals_except_10m():
    assert {key: spec.source_interval for key, spec in SPECS.items()} == {
        "1m": "1", "10m": "5", "1h": "60", "4h": "240", "1d": "D"}


def test_aggregate_ohlcv_uses_utc_bucket_boundary_and_deduplicates_timestamp():
    rows = [
        row(600_000, 10, 12, 9, 11, 2),
        row(900_000, 11, 15, 8, 14, 3),
        row(900_000, 11, 16, 7, 13, 4),  # later duplicate wins
        row(1_200_000, 20, 21, 19, 20, 5),
    ]
    result = aggregate_rows(rows, 600_000, now_ms=1_800_000)
    assert result == [
        {"start_ms": 600_000, "open": "10", "high": "16", "low": "7", "close": "13",
         "volume": "6", "confirmed": True},
        {"start_ms": 1_200_000, "open": "20", "high": "21", "low": "19", "close": "20",
         "volume": "5", "confirmed": True},
    ]


def test_history_is_ordered_paged_strictly_before_cursor_and_cached():
    source = [[str(ts), str(ts), str(ts + 2), str(ts - 2), str(ts + 1), "1"]
              for ts in range(0, 3_600_000, 300_000)]
    calls = []

    def request(_path, params):
        calls.append(dict(params))
        end = int(params.get("end", 10**20))
        eligible = [item for item in source if int(item[0]) <= end]
        return {"retCode": 0, "result": {"list": list(reversed(eligible[-int(params["limit"]):]))}}

    history = BybitChartHistory(request=request, clock=lambda: 1.0)
    first = history.get("10m", 3, 3_600_000)
    assert [item["start_ms"] for item in first["bars"]] == [1_800_000, 2_400_000, 3_000_000]
    assert all(item["start_ms"] < 3_600_000 for item in first["bars"])
    assert first["source_interval"] == "5"
    count = len(calls)
    assert history.get("10m", 3, 3_600_000) is first
    assert len(calls) == count


def test_empty_range_and_validation():
    history = BybitChartHistory(request=lambda _path, _params: {
        "retCode": 0, "result": {"list": []}})
    assert history.get("1d", 20, 1)["bars"] == []
    assert history.get("1d", 20, 1)["has_more"] is False
    try:
        history.get("3m", 20)
    except ValueError as exc:
        assert "unsupported timeframe" in str(exc)
    else:
        raise AssertionError("unsupported timeframe must fail")


def test_bucket_widths_are_fixed_per_timeframe():
    assert {key: spec.bucket_ms for key, spec in SPECS.items()} == {
        "1m": 60_000, "10m": 600_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    assert {key: spec.source_bars_per_bucket for key, spec in SPECS.items()} == {
        "1m": 1, "10m": 2, "1h": 1, "4h": 1, "1d": 1}


def test_daily_bucket_folds_on_the_utc_day_not_the_kst_day():
    """Bybit stamps its own daily bar at 00:00 UTC, so the fold has to agree with the venue.

    09:00 KST on 2026-10-01 is 00:00 UTC the same day: a KST-aligned fold would put these two
    rows in different buckets, and the daily candle would stop matching the exchange's.
    """
    day = 1_790_812_800_000  # 2026-10-01T00:00:00Z
    result = aggregate_rows([row(day, 10, 12, 9, 11, 2),
                             row(day + 23 * 3_600_000, 11, 14, 8, 13, 3)],
                            86_400_000, now_ms=day + 86_400_000)
    assert [item["start_ms"] for item in result] == [day]
    assert result[0]["high"] == "14" and result[0]["low"] == "8" and result[0]["volume"] == "5"


def test_only_a_closed_bucket_is_confirmed():
    closed, open_ = aggregate_rows([row(0, 1, 2, 1, 2, 1), row(600_000, 2, 3, 2, 3, 1)],
                                   600_000, now_ms=900_000)
    assert closed["confirmed"] is True
    assert open_["confirmed"] is False
