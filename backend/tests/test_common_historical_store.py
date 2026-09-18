"""Common Historical Store: planning, provider-byte raw fetch, C freeze promotion, snapshot freeze.

No network (httpx.MockTransport) and no real workspace (tmp_path only).
"""

from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

import httpx
from pydantic import SecretStr
import pytest

from app.backtest.historical_store import c_freeze, session_audit
from app.backtest.historical_store.raw_fetch import (
    CHUNK_SESSIONS, RawRequest, contiguous_runs, fetch_request, ordered, plan_requests,
)
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import MassiveAggregatesClient, MassiveError
from app.integrations.massive.minute_bars import ET
from app.market.calendar import MarketCalendar

KEY = "test-key-value-123"


def _weekdays(start: date, n: int) -> list[date]:
    out, cursor = [], start
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _client(handler, capture: list[bytes]) -> MassiveAggregatesClient:
    return MassiveAggregatesClient(SecretStr(KEY), http=httpx.Client(transport=httpx.MockTransport(handler)),
                                   limiter=RequestRateLimiter(1000.0, sleeper=lambda s: None),
                                   sleeper=lambda s: None, page_observer=capture.append)


def _fetch(client, capture, root, request):
    return fetch_request(client, capture, root, request, secret=KEY, log=lambda m: None,
                         sleeper=lambda s: None, now=lambda: "2026-09-18T00:00:00+00:00")


def test_existing_sessions_are_never_planned_and_minute_runs_are_chunked():
    sessions = _weekdays(date(2024, 9, 16), 130)
    existing = {"AAA": set(sessions[60:100]), "BBB": set(sessions)}
    plan = plan_requests("minute", sessions, existing, ["AAA", "BBB"])
    covered = {s for r in plan for s in sessions if r.start <= s <= r.end}
    assert covered.isdisjoint(existing["AAA"]) and all(r.symbol == "AAA" for r in plan)
    assert sum(r.sessions for r in plan) == 90
    assert max(r.sessions for r in plan) == CHUNK_SESSIONS
    assert [r.start for r in ordered(plan)] == sorted(r.start for r in plan)
    daily = plan_requests("per_symbol_daily", sessions, existing, ["AAA"])
    assert [(r.start, r.end) for r in daily] == [(sessions[0], sessions[59]), (sessions[100], sessions[129])]


def test_contiguous_runs_follow_the_session_grid_not_the_calendar():
    sessions = _weekdays(date(2026, 9, 4), 4)  # Fri, Mon, Tue, Wed
    assert contiguous_runs(sessions, [sessions[0], sessions[1], sessions[3]]) == [sessions[:2], [sessions[3]]]


def test_raw_fetch_keeps_provider_bytes_and_a_second_run_costs_nothing(tmp_path):
    start_ms = int(datetime(2026, 9, 8, 9, 30, tzinfo=ET).timestamp() * 1000)
    body = (b'{"ticker":"AAA","adjusted":false,"status":"OK","resultsCount":2,"results":['
            b'{"v":10.5,"vw":1.25,"o":1.2,"c":1.3,"h":1.4,"l":1.1,"t":%d,"n":3},'
            b'{"v":5,"vw":1.3,"o":1.3,"c":1.3,"h":1.3,"l":1.3,"t":%d,"n":1}]}' % (start_ms, start_ms + 60_000))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body)

    capture: list[bytes] = []
    client = _client(handler, capture)
    request = RawRequest("minute", "AAA", date(2026, 9, 8), date(2026, 9, 8), 1)
    assert _fetch(client, capture, tmp_path, request).startswith("ok pages=1 rows=2")
    assert calls[0].url.params["adjusted"] == "false"
    stored = request.page_path(tmp_path, 1).read_bytes()
    assert gzip.decompress(stored) == body  # byte-identical, not re-serialized
    ledger = json.loads(request.ledger_path(tmp_path).read_text())
    assert ledger["status"] == "COMPLETE" and ledger["pages"][0]["raw_sha256"] == hashlib.sha256(body).hexdigest()
    assert _fetch(client, capture, tmp_path, request) == "cached" and len(calls) == 1


def test_plan_window_refusal_is_recorded_and_other_errors_leave_no_ledger(tmp_path):
    capture: list[bytes] = []
    refused = _client(lambda r: httpx.Response(403, text="Your plan doesn't include this data timeframe."), capture)
    request = RawRequest("per_symbol_daily", "AAA", date(2024, 9, 16), date(2024, 9, 16), 1)
    assert _fetch(refused, capture, tmp_path, request) == "PLAN_TIMEFRAME_NOT_INCLUDED"
    assert json.loads(request.ledger_path(tmp_path).read_text())["status"] == "NOT_AVAILABLE"
    broken = _client(lambda r: httpx.Response(500), capture)
    other = RawRequest("per_symbol_daily", "BBB", date(2024, 9, 16), date(2024, 9, 16), 1)
    with pytest.raises(MassiveError):
        _fetch(broken, capture, tmp_path, other)
    assert not other.ledger_path(tmp_path).exists()


def test_adjusted_true_or_a_key_in_the_body_is_refused(tmp_path):
    capture: list[bytes] = []
    request = RawRequest("per_symbol_daily", "AAA", date(2026, 9, 8), date(2026, 9, 8), 1)
    adjusted = _client(lambda r: httpx.Response(200, json={"status": "OK", "adjusted": True, "results": []}), capture)
    with pytest.raises(MassiveError):
        _fetch(adjusted, capture, tmp_path, request)
    leaky = _client(lambda r: httpx.Response(200, json={"status": "OK", "adjusted": False, "results": [],
                                                        "next_url": f"x?apiKey={KEY}"}), capture)
    with pytest.raises(MassiveError):
        _fetch(leaky, capture, tmp_path, request)
    assert not request.ledger_path(tmp_path).exists()


def _gz(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)


def test_c_freeze_promotion_copies_bytes_and_flags_a_conflict(tmp_path):
    raw, workspace = tmp_path / "raw", tmp_path / "1_US-B"
    _gz(raw / "grouped/2024-09-16.json.gz", {"format": "f", "session": "2024-09-16", "error": "NOT_AUTHORIZED"})
    _gz(raw / "grouped/2024-09-17.json.gz", {"format": "f", "session": "2024-09-17",
                                             "body": {"adjusted": False, "results": [{"T": "AAA"}]}})
    _gz(raw / "tickers/CS_2024-10-01.json.gz", {"format": "f", "as_of": "2024-10-01", "pages": 1, "results": []})
    _gz(raw / "splits/splits_2024-09-16_2026-09-16.json.gz",
        {"format": "f", "start": "2024-09-16", "end": "2026-09-16", "pages": 1, "results": []})
    freeze = c_freeze.build_freeze(raw, c_raw_digest="x")
    assert freeze["grouped_usable_sessions"] == 1 and freeze["not_available"] == ["grouped/2024-09-16.json.gz"]
    before = {p: p.read_bytes() for p in raw.rglob("*.gz")}
    assert c_freeze.promote(freeze, raw, workspace, log=lambda m: None)["verified"]
    assert (workspace / "market_data/raw/massive/grouped_daily/2024/2024-09-17.json.gz").read_bytes() \
        == before[raw / "grouped/2024-09-17.json.gz"]
    assert {p: p.read_bytes() for p in raw.rglob("*.gz")} == before  # source untouched
    assert c_freeze.promote(freeze, raw, workspace, log=lambda m: None)["outcomes"] == {"present": 4}
    (workspace / "market_data/raw/massive/splits/splits_2024-09-16_2026-09-16.json.gz").write_bytes(b"tampered")
    result = c_freeze.promote(freeze, raw, workspace, log=lambda m: None)
    assert not result["verified"] and result["failures"][0][1].startswith("CONFLICT")


def test_session_audit_counts_parts_and_flags_without_filling():
    calendar = MarketCalendar()
    day = date(2026, 9, 8)
    window = calendar.session(day)
    stamps = [int((window.market_open + timedelta(minutes=i)).timestamp() * 1000) for i in range(389)]
    stamps.append(int(datetime(2026, 9, 8, 8, 0, tzinfo=ET).timestamp() * 1000))
    parts = session_audit._parts_from_ms(stamps, calendar)
    rows = session_audit.audit_rows("AAA", "COMMON_RAW", parts, [day, date(2026, 9, 9)], calendar,
                                    grouped_trades={day: 50_000.0}, split_days={date(2026, 9, 9)})
    first, second = rows
    assert (first["regular_rows"], first["premarket_rows"], first["regular_complete"]) == (389, 1, False)
    assert first["api_loss_suspect"] and not first["empty_session"]
    assert second["empty_session"] and second["corporate_action_suspect"] and not second["api_loss_suspect"]
