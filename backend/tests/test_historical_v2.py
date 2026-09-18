"""USB-HIST-V2 planning: B fetch universe (PIT), ranged planning, page caps, daily reference fetch.

No network (httpx.MockTransport) and no real workspace (tmp_path only).
"""

from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json

import httpx
import numpy as np
from pydantic import SecretStr
import pytest

from app.backtest.historical_store import b_universe, reference_fetch, requirements
from app.backtest.historical_store.raw_fetch import minute_page_cap, plan_requests
from app.dev.historical_v2 import rows_for_trades, window_start
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import MassiveAggregatesClient, long_range_page_cap
from app.integrations.massive.minute_bars import ET
from app.strategy_b.config import ScopeConfig

KEY = "test-key-value-123"


def _weekdays(start: date, n: int) -> list[date]:
    out, cursor = [], start
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _panel(n: int):
    sessions = _weekdays(date(2024, 9, 17), n)
    names = ["LIQ", "THIN", "PENNY", "NOREF"]
    close = np.tile(np.array([10.0, 10.0, 0.5, 10.0]), (n, 1))
    dollars = np.tile(np.array([5e6, 1e4, 5e6, 5e6]), (n, 1))
    return sessions, names, close, dollars


def test_scope_uses_only_d_minus_1_data_and_snapshots_dated_before_d():
    sessions, names, close, dollars = _panel(40)
    refs = {sessions[10]: {"LIQ", "THIN", "PENNY"}}
    member = b_universe.scope_membership(names, close, dollars, sessions, refs, ScopeConfig())
    assert not member[:11].any()  # the snapshot dated on session 10 is first usable on session 11
    assert member[11:, 0].all() and not member[:, 1:].any()  # thin, penny and unreferenced never in scope
    changed = dollars.copy()
    changed[25:, 0] = 0.0  # a collapse from session 25 on is visible from session 26 only
    later = b_universe.scope_membership(names, close, changed, sessions, refs, ScopeConfig())
    assert (later[:26] == member[:26]).all()


def test_scope_needs_min_history_sessions():
    sessions, names, close, dollars = _panel(30)
    dollars[:, 0] = np.nan
    dollars[20:, 0] = 5e6
    refs = {sessions[0]: {"LIQ"}}
    member = b_universe.scope_membership(names, close, dollars, sessions, refs, ScopeConfig())
    assert np.flatnonzero(member[:, 0])[0] == 25  # five bars (20..24) before D


def test_fetch_range_is_contiguous_with_rvol_warmup_and_clipped_to_grid():
    sessions = _weekdays(date(2024, 9, 17), 60)
    member = np.zeros((60, 2), dtype=bool)
    member[[5, 40, 41], 0] = True
    member[[30, 50], 1] = True
    rows = {r["symbol"]: r for r in b_universe.fetch_ranges(["EARLY", "GAPPY"], member, sessions, 20)}
    assert rows["EARLY"]["start"] == sessions[0].isoformat() and rows["EARLY"]["warmup_clipped"]
    assert rows["EARLY"]["sessions"] == 42 and rows["EARLY"]["scope_sessions"] == 3
    assert rows["GAPPY"]["start"] == sessions[10].isoformat() and rows["GAPPY"]["end"] == sessions[50].isoformat()
    assert rows["GAPPY"]["sessions"] == 41 and not rows["GAPPY"]["warmup_clipped"]


def test_ranged_plan_skips_existing_and_keeps_one_request_per_run():
    sessions = _weekdays(date(2024, 9, 17), 120)
    required = {"AAA": (sessions[10], sessions[99]), "BBB": (sessions[0], sessions[119])}
    existing = {"AAA": set(sessions[50:60]), "BBB": set(sessions)}
    plan = plan_requests("minute", sessions, existing, ["AAA", "BBB"], required=required, chunk=len(sessions))
    assert [(r.symbol, r.start, r.end) for r in plan] == [("AAA", sessions[10], sessions[49]),
                                                          ("AAA", sessions[60], sessions[99])]
    assert len(plan_requests("minute", sessions, {}, ["AAA"])) == 3  # default chunk is unchanged


def test_minute_page_cap_keeps_v1_caps_and_bounds_two_year_ranges_by_sessions():
    start = datetime(2025, 9, 15, 4, tzinfo=ET)
    end = datetime(2025, 11, 25, 20, tzinfo=ET)
    assert minute_page_cap(start, end, 50) == long_range_page_cap(start, end) == 4
    two_years = minute_page_cap(datetime(2024, 9, 17, 4, tzinfo=ET), datetime(2026, 9, 16, 20, tzinfo=ET), 501)
    assert two_years == 11
    with pytest.raises(ValueError):
        minute_page_cap(datetime(2020, 1, 2, 4, tzinfo=ET), datetime(2026, 9, 16, 20, tzinfo=ET), 1700)


def test_window_start_is_the_et_date_two_years_back_with_margin():
    assert window_start(datetime(2026, 9, 18, 2, 16, tzinfo=timezone.utc)) == date(2024, 9, 17)  # 22:16 ET
    assert window_start(datetime(2026, 9, 18, 3, 45, tzinfo=timezone.utc)) == date(2024, 9, 18)  # 23:45 ET
    assert window_start(datetime(2028, 2, 29, 12, tzinfo=timezone.utc)) == date(2026, 2, 28)


def test_rows_for_trades_is_bounded_by_the_extended_session():
    assert rows_for_trades(0) == 64 and rows_for_trades(10**9) <= 960
    assert rows_for_trades(20_000) == 366


def _client(handler, capture):
    return MassiveAggregatesClient(SecretStr(KEY), http=httpx.Client(transport=httpx.MockTransport(handler)),
                                   limiter=RequestRateLimiter(1000.0, sleeper=lambda s: None),
                                   sleeper=lambda s: None, page_observer=capture.append)


def test_reference_day_stores_provider_bytes_per_page_and_ledger_last(tmp_path):
    calls = []
    pages = [{"status": "OK", "results": [{"ticker": "AAA", "type": "CS"}],
              "next_url": "https://api.massive.com/v3/reference/tickers?cursor=abc"},
             {"status": "OK", "results": [{"ticker": "BBB", "type": "CS"}]}]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = json.dumps(pages[len(calls) - 1]).encode()
        return httpx.Response(200, content=body)

    capture: list[bytes] = []
    client = _client(handler, capture)
    day = date(2025, 3, 3)
    outcome = reference_fetch.fetch_reference_day(client, capture, tmp_path, day, secret=KEY, log=lambda m: None,
                                                  sleeper=lambda s: None, now=lambda: "t")
    assert outcome == "ok pages=2 rows=2" and "date=2025-03-03" in calls[0]
    ledger = json.loads(reference_fetch.ledger_path(tmp_path, day).read_text())
    for number, page in enumerate(ledger["pages"]):
        raw = gzip.decompress((reference_fetch.ledger_path(tmp_path, day).parent / page["file"]).read_bytes())
        assert raw == json.dumps(pages[number]).encode()
        assert page["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    again = reference_fetch.fetch_reference_day(client, capture, tmp_path, day, secret=KEY, log=lambda m: None,
                                                sleeper=lambda s: None, now=lambda: "t")
    assert again == "cached" and len(calls) == 2


def test_reference_existing_dates_count_quarterly_seed_and_complete_ledgers(tmp_path):
    quarterly = tmp_path / reference_fetch.QUARTERLY_REFERENCE_DIR / "CS_2025-01-02.json.gz"
    quarterly.parent.mkdir(parents=True)
    quarterly.write_bytes(b"x")
    done = reference_fetch.ledger_path(tmp_path, date(2025, 1, 3))
    done.parent.mkdir(parents=True)
    done.write_text(json.dumps({"status": "COMPLETE"}))
    have = reference_fetch.existing_dates(tmp_path, [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)])
    assert have == {date(2025, 1, 2), date(2025, 1, 3)}


def test_requirements_document_is_valid_and_content_addressed():
    doc = requirements.document()
    allowed = {"REQUIRED", "OPTIONAL", "PLANNED", "NOT_REQUIRED", "UNAVAILABLE"}
    for row in doc["matrix"].values():
        assert {row[s] for s in "ABCD"} <= allowed
    assert doc["digest"] == requirements.document()["digest"]
    assert doc["matrix"]["minute_regular"]["B"] == "REQUIRED" and doc["matrix"]["grouped_daily"]["A"] == "NOT_REQUIRED"


def _minute_body(day: date, minutes: list[int], next_url: str | None = None) -> bytes:
    base = int(datetime.combine(day, datetime.min.time().replace(hour=9, minute=30), tzinfo=ET).timestamp() * 1000)
    bars = [{"v": 1, "vw": 1.0, "o": 1.0, "c": 1.0, "h": 1.0, "l": 1.0, "t": base + m * 60_000, "n": 1}
            for m in minutes]
    body = {"ticker": "AAA", "adjusted": False, "status": "OK", "resultsCount": len(bars), "results": bars}
    if next_url:
        body["next_url"] = next_url
    return json.dumps(body).encode()


def _fetch_minute(tmp_path, pages, request, extra=None):
    from app.backtest.historical_store.raw_fetch import fetch_request
    capture: list[bytes] = []
    queue = list(pages)
    client = _client(lambda r: httpx.Response(200, content=queue.pop(0)), capture)
    return fetch_request(client, capture, tmp_path, request, secret=KEY, log=lambda m: None,
                         sleeper=lambda s: None, now=lambda: "t", extra=extra)


def test_minute_ledger_keeps_requested_and_clipped_range_and_actual_sessions(tmp_path):
    from app.backtest.historical_store.raw_fetch import RawRequest
    request = RawRequest("minute", "AAA", date(2026, 9, 8), date(2026, 9, 9), 2)
    extra = {"requested_start": "2026-09-04", "clipped_request_start": "2026-09-08",
             "unavailable_rolling_window": ["2026-09-04"], "coverage_status": "COMPLETE_CLIPPED"}
    outcome = _fetch_minute(tmp_path, [_minute_body(date(2026, 9, 9), [0, 1])], request, extra)
    assert outcome.startswith("ok pages=1 rows=2 sessions_with_bars=1/2")
    ledger = json.loads(request.ledger_path(tmp_path).read_text())
    assert ledger["requested_start"] == "2026-09-04" and ledger["start"] == "2026-09-08"
    assert ledger["unavailable_rolling_window"] == ["2026-09-04"] and ledger["adjusted"] is False
    assert ledger["first_session"] == ledger["last_session"] == "2026-09-09"
    assert ledger["pagination_pages"] == 1 and ledger["file_bytes"] == ledger["pages"][0]["file_bytes"]


def test_minute_fetch_refuses_overlapping_pages_and_bars_outside_the_request(tmp_path):
    from app.backtest.historical_store.raw_fetch import RawRequest
    from app.integrations.massive.client import MassiveError
    nxt = "https://api.massive.com/v2/aggs/ticker/AAA/range/1/minute/1/2?cursor=x"
    request = RawRequest("minute", "AAA", date(2026, 9, 8), date(2026, 9, 8), 1)
    with pytest.raises(MassiveError) as overlap:
        _fetch_minute(tmp_path, [_minute_body(date(2026, 9, 8), [0, 1], nxt),
                                 _minute_body(date(2026, 9, 8), [1, 2])], request)
    assert overlap.value.code in {"PAGE_OVERLAP", "DUPLICATE_PAGE", "OVERLAPPING_PAGE"}
    with pytest.raises(MassiveError):
        _fetch_minute(tmp_path, [_minute_body(date(2026, 9, 10), [0])], request)
    assert not request.ledger_path(tmp_path).exists()  # neither counts as covered


def test_unavailable_ledger_only_grows_and_priority_puts_windowed_minute_first(tmp_path, monkeypatch):
    from app.dev import historical_v2
    monkeypatch.setattr(historical_v2, "UNAVAILABLE", tmp_path / "u.json")
    historical_v2._record_unavailable("b_minute", "AAA", [date(2024, 9, 17)])
    historical_v2._record_unavailable("b_minute", "AAA", [date(2024, 9, 18)])
    assert json.loads((tmp_path / "u.json").read_text()) == {"b_minute": {"AAA": ["2024-09-17", "2024-09-18"]}}
    assert historical_v2.PRIORITY[:2] == ("b_minute", "b_per_symbol_daily")
    assert historical_v2.PRIORITY[-1] == "reference_daily"
    assert historical_v2.kind_root("b_minute", tmp_path) == historical_v2.STAGING
    assert historical_v2.kind_root("b_per_symbol_daily", tmp_path) == tmp_path


def test_fetch_retries_a_transient_error_on_the_same_item_and_never_retries_auth(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.backtest.historical_store.raw_fetch import RawRequest
    from app.dev import historical_v2
    from app.integrations.massive.client import MassiveError
    monkeypatch.setattr(historical_v2, "OUT", tmp_path / "out")
    monkeypatch.setattr(historical_v2, "UNAVAILABLE", tmp_path / "out" / "u.json")
    monkeypatch.setattr(historical_v2, "STAGING", tmp_path / "staging")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(historical_v2, "get_settings", lambda: SimpleNamespace(massive_api_key=SecretStr(KEY)))
    monkeypatch.setattr(historical_v2, "_locked_batches", lambda ws, items, log: iter(items))
    monkeypatch.setattr(historical_v2, "window_start", lambda: date(2024, 1, 2))
    sleeps: list[float] = []
    monkeypatch.setattr(historical_v2.time, "sleep", sleeps.append)
    script = {"AAA": [MassiveError("NETWORK_ERROR", "x"), MassiveError("PROVIDER_ERROR", "y"), "ok"],
              "BBB": [MassiveError("NOT_AUTHORIZED", "z")], "CCC": ["ok"]}
    calls: list[str] = []

    def fake_fetch(client, capture, target, request, **kwargs):
        calls.append(request.symbol)
        step = script[request.symbol].pop(0)
        if isinstance(step, Exception):
            raise step
        request.ledger_path(target).parent.mkdir(parents=True, exist_ok=True)
        request.ledger_path(target).write_text(json.dumps({"status": "COMPLETE", "rows": 1, "file_bytes": 1}))
        return "ok pages=1"

    monkeypatch.setattr(historical_v2, "fetch_request", fake_fetch)
    day = date(2025, 3, 3)
    plan = {"kinds": {k: {"capacity_gate": "PASS" if k == "b_minute" else "BLOCKED", "missing_symbol_sessions": 3,
                          "http_calls": 3} for k in historical_v2.PRIORITY},
            "plans": {"b_minute": [RawRequest("minute", s, day, day, 1) for s in ("AAA", "BBB", "CCC")]}}
    code = historical_v2.fetch(tmp_path, plan, spacing=0.001, max_requests=None)
    assert calls == ["AAA", "AAA", "AAA", "BBB"]  # retried twice, then fail-closed on auth, CCC never sent
    assert sleeps[:2] == [60, 120] and code == 1
    progress = json.loads((tmp_path / "out" / "fetch_progress.json").read_text())
    assert progress["kinds"]["b_minute"]["done"] == 1 and progress["kinds"]["b_minute"]["transient_retries"] == 2
