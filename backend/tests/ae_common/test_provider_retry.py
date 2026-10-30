"""E finalization's bounded retry of Kiwoom's bare generic rejection (return_code 7, HTTP 200, no
subcode), the per-symbol provider telemetry, and the immutable 2026-10-29 replay."""

from collections import Counter
from datetime import date, datetime, time
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.ae_common import e_shadow as ES, producer as P, provider_events as PEV, snapshot as SS
from app.core import provider_errors as PE
from app.core.exceptions import MarketDataError
from app.strategy_e_max_rt import finalizer as FZ
from tests.ae_common.helpers import LimitedClient, at
from tests.ae_common.test_producer import morning
from tests.test_kiwoom_stage10a import client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
E_DEADLINE = at(time(9, 29, 45))
FAILING = ("P03", "P07", "EONLY1")


def code7(symbol: str = "TEST") -> MarketDataError:
    """What ``KiwoomMarketDataClient.request`` raises for ``{"return_code": 7, "return_msg": "..."}``."""
    return MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom market-data query was rejected", provider="KIWOOM",
                           error_category=PE.TRANSIENT_PROVIDER_ERROR, provider_code="7", http_status=200,
                           provider_return_code="7", provider_subcode=None,
                           sanitized_return_msg="fixture rejection", requested_exchange="ND")


def kiwoom(handler, **kwargs):
    def routed(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "fixture", "expires_dt": "20990101000000", "return_code": 0})
        return handler(request)
    return client(httpx.Client(transport=httpx.MockTransport(routed)), **kwargs)


# -- the client boundary -----------------------------------------------------------------------------

@pytest.mark.parametrize("answer,retryable", [
    ({"return_code": 7, "return_msg": "provider busy"}, True),
    ({"return_code": 7}, True),
    ({"return_code": 7, "return_msg": "[1903:종목 정보가 없습니다]"}, False),          # mapping
    ({"return_code": 7, "return_msg": "[2001:other subcode]"}, False),                 # a subcode: not generic
    ({"return_code": 7, "return_msg": "종목코드 오류"}, False),                         # INVALID_SYMBOL
    ({"return_code": 7, "error_code": "MARKET_DATA_UNAVAILABLE"}, False),             # explicit refusal
    ({"return_code": 5, "return_msg": "other"}, False),
])
def test_only_the_bare_generic_rejection_is_retryable(answer, retryable):
    c = kiwoom(lambda request: httpx.Response(200, json=answer), max_retries=0)
    with pytest.raises(MarketDataError) as failure:
        c.request("usa06011", "/api/us/chart", {"stex_tp": "NY", "stk_cd": "GS"})
    error = failure.value
    assert PE.generic_rejection(error) is retryable
    assert error.http_status == 200 and error.provider_return_code == str(answer["return_code"])
    assert error.requested_exchange == "NY"
    assert c.request_counts["/api/us/chart"] == 1             # the client itself never re-sends a rejection


@pytest.mark.parametrize("status", [429, 500, 503, 400])
def test_http_failures_are_never_generic_rejections(status):
    c = kiwoom(lambda request: httpx.Response(status), max_retries=0)
    with pytest.raises(MarketDataError) as failure:
        c.request("usa06011", "/api/us/chart", {"stex_tp": "ND", "stk_cd": "TEST"})
    assert not PE.generic_rejection(failure.value) and failure.value.http_status == status


@pytest.mark.parametrize("mode,attempts", [("429", 2), ("500", 2), ("remote_protocol", 2), ("timeout", 2),
                                           ("code7", 1)])
def test_transport_retry_is_unchanged_and_does_not_absorb_rejections(mode, attempts):
    sent = []
    def handler(request):
        sent.append(1)
        if len(sent) == 1:
            if mode == "remote_protocol": raise httpx.RemoteProtocolError("reset", request=request)
            if mode == "timeout": raise httpx.ReadTimeout("slow", request=request)
            if mode == "code7": return httpx.Response(200, json={"return_code": 7, "return_msg": "busy"})
            return httpx.Response(int(mode))
        return httpx.Response(200, json={"return_code": 0, "result_list": []})
    c = kiwoom(handler, max_retries=1)
    if mode == "code7":
        with pytest.raises(MarketDataError):
            c.request("usa06011", "/api/us/chart", {"stex_tp": "ND", "stk_cd": "TEST"})
    else:
        c.request("usa06011", "/api/us/chart", {"stex_tp": "ND", "stk_cd": "TEST"})
    assert len(sent) == attempts


def test_sanitized_message_drops_tokens_and_account_numbers():
    text = "토큰 eyJhbGciOiJIUzI1NiJ9.payload.signature 계좌 12345678 [1700:유량=5, API ID=usa20100]"
    clean = PE.sanitize_message(text)
    assert "eyJ" not in clean and "12345678" not in clean and "[1700:" in clean and "usa20100" in clean
    assert PE.sanitize_message("x" * 1000).__len__() <= PE.MAX_MESSAGE and PE.sanitize_message("  ") is None


# -- the producer's finalization -----------------------------------------------------------------------

def run(tmp_path, monkeypatch, rejections: dict[str, int], *, before_failure=None):
    """The morning with ``symbol -> n``: the first ``n`` finalization requests for it are rejected."""
    original = LimitedClient.request
    seen, sent = Counter(), []
    def request(self, api_id, path, body, continuation=None):
        symbol = body["stk_cd"]
        if self.clock() >= at(time(9, 25)):
            self.limiter.acquire()                               # every request passes the limiter
            sent.append((self.clock(), symbol))
            seen[symbol] += 1
            if seen[symbol] <= rejections.get(symbol, 0):
                if before_failure:
                    before_failure(self.clock, symbol, seen[symbol])
                self.clock.tick(self.seconds)
                raise code7(symbol)
            self.clock.tick(self.seconds)
            from app.integrations.kiwoom.client import KiwoomPage
            return KiwoomPage(body={"result_list": list(self.pages.get(symbol, []))}, continuation=False, next_key=None)
        return original(self, api_id, path, body, continuation)
    monkeypatch.setattr(LimitedClient, "request", request)
    result, clients, limiters, _, clock = morning(tmp_path)
    return result, seen, sent, limiters


def decision_view(result):
    out = result.e_shadow["from_snapshot"]
    gate = {k: v for k, v in out["gate"].items() if k not in ("seal_at", "seal_margin_s")}   # instants
    return ({f: SS.canonical(out["decision"][f]) for f in ES.DECISION_FIELDS},
            out["statuses_digest"], out["feature_rows_digest"], gate)


def test_a_first_rejection_is_recovered_and_the_decision_is_exactly_the_clean_one(tmp_path, monkeypatch):
    clean, *_ = run(tmp_path / "clean", monkeypatch, {})
    result, seen, sent, limiters = run(tmp_path / "retry", monkeypatch, {s: 1 for s in FAILING})
    assert decision_view(result) == decision_view(clean)
    out = result.e_shadow["from_snapshot"]
    assert out["unexpected_stale_count"] == 0 and out["gate"]["unexpected_stale"] == 0
    assert all(seen[s] == 2 for s in FAILING)
    retry = result.report["e_finalization"]["provider_retry"]
    assert retry["recovered"] == sorted(FAILING) and retry["retry_requests"] == 3 and not retry["exhausted"]
    snap = SS.load(tmp_path / "retry" / "snap", SS.SNAPSHOT_0924).body["symbols"]
    assert all(snap[s]["error"] is None and snap[s]["finalized_at"] for s in FAILING)
    tel = result.e_shadow["provider_telemetry"]
    assert tel["finalization_error_count"] == 3 and tel["generic_code7_count"] == 3
    assert tel["generic_code7_recovered_count"] == 3 and tel["generic_code7_unrecovered_count"] == 0
    assert {e["retry_outcome"] for e in tel["events"]} == {PEV.RETRIED}
    assert all(e["result"] == PEV.RECOVERED and e["attempt"] == 2 for e in tel["examples"])
    assert all(sent_at < E_DEADLINE for sent_at, _ in sent)
    assert all(p["last_sent_before_deadline"] for l in limiters for p in l.declaration()["phases"])


def test_retries_wait_for_the_lane_first_pass(tmp_path, monkeypatch):
    result, seen, sent, _ = run(tmp_path, monkeypatch, {s: 1 for s in FAILING})
    first = {}
    for moment, symbol in sent:
        first.setdefault(symbol, moment)
    second = {s: [m for m, x in sent if x == s][1] for s in FAILING}
    # every symbol's first read happens before any retry: a retry never delays an unread symbol
    assert max(first.values()) <= min(second.values())
    assert all((second[s] - first[s]).total_seconds() >= P.E_RETRY_SPACING_S for s in FAILING)


def test_a_symbol_rejected_three_times_stays_stale_and_blocks(tmp_path, monkeypatch):
    clean, *_ = run(tmp_path / "clean", monkeypatch, {})
    result, seen, sent, _ = run(tmp_path / "persist", monkeypatch, {"P03": 99})
    assert seen["P03"] == P.E_MAX_ATTEMPTS == 3
    out = result.e_shadow["from_snapshot"]
    assert out["unexpected_stale_count"] == 1 and out["unexpected_stale_examples"][0]["symbol"] == "P03"
    assert not out["gate"]["authorized"] and "DECISION_INCOMPLETE_UNIVERSE" in out["gate"]["reasons"]
    detail = result.e_shadow["session_record"]["provider_errors"]["P03"]
    assert detail["error_category"] == PE.TRANSIENT_PROVIDER_ERROR and detail["provider_return_code"] == "7"
    assert detail["attempts"] == 3 and detail["max_attempts"] == 3 and detail["retry_outcome"] == PEV.EXHAUSTED
    snap = SS.load(tmp_path / "persist" / "snap", SS.SNAPSHOT_0924).body["symbols"]["P03"]
    assert snap["attempts"] == 3 and snap["finalized_at"] is None
    assert result.report["e_finalization"]["provider_retry"]["exhausted"] == ["P03"]
    assert result.e_shadow["sim_entries"] == 0 and result.e_shadow["real_orders"] == 0
    tel = result.e_shadow["provider_telemetry"]
    assert [e["attempt"] for e in tel["events"]] == [1, 2, 3]
    assert [e["retry_outcome"] for e in tel["events"]] == [PEV.RETRIED, PEV.RETRIED, PEV.EXHAUSTED]
    assert tel["generic_code7_unrecovered_count"] == 1
    assert clean.e_shadow["from_snapshot"]["unexpected_stale_count"] == 0


def test_the_deadline_stops_a_retry_and_keeps_every_completed_symbol(tmp_path, monkeypatch):
    clean, *_ = run(tmp_path / "clean", monkeypatch, {})
    def late(clock, symbol, n):                     # the rejection arrives 0.5 s before 09:29:45
        if symbol == "P03":
            clock.tick(max(0.0, (E_DEADLINE - clock()).total_seconds() - 0.7))
    result, seen, sent, limiters = run(tmp_path / "late", monkeypatch, {"P03": 1}, before_failure=late)
    assert seen["P03"] == 1
    assert all(sent_at < E_DEADLINE for sent_at, _ in sent)
    assert all(p["last_sent_before_deadline"] for l in limiters for p in l.declaration()["phases"])
    detail = result.e_shadow["session_record"]["provider_errors"]["P03"]
    assert detail["retry_outcome"] == PEV.DEADLINE_BEFORE_RETRY and detail["attempts"] == 1
    out = result.e_shadow["from_snapshot"]
    assert "P03" in [e["symbol"] for e in out["unexpected_stale_examples"]]
    assert not out["gate"]["authorized"]
    snap = SS.load(tmp_path / "late" / "snap", SS.SNAPSHOT_0924).body["symbols"]
    clean_snap = SS.load(tmp_path / "clean" / "snap", SS.SNAPSHOT_0924).body["symbols"]
    for symbol, entry in snap.items():
        if entry["finalized_at"] and symbol != "P03":
            assert entry["bars"] == clean_snap[symbol]["bars"] and entry["error"] is None
    tel = result.e_shadow["provider_telemetry"]
    assert tel["events"][0]["retry_outcome"] == PEV.DEADLINE_BEFORE_RETRY


@pytest.mark.parametrize("error", [
    MarketDataError("INVALID_SYMBOL", "1903", provider="KIWOOM", error_category=PE.MAPPING_ERROR, provider_code="1903",
                    http_status=200, provider_return_code="7", provider_subcode="1903"),
    MarketDataError("MARKET_DATA_UNAVAILABLE", "refused", provider="KIWOOM", error_category=PE.SOURCE_REFUSAL,
                    http_status=200, provider_return_code="7"),
    MarketDataError("MARKET_DATA_UNAVAILABLE", "HTTP 400", http_status=400),
    MarketDataError("RATE_LIMITED", "429", provider="KIWOOM", error_category=PE.TRANSIENT_PROVIDER_ERROR,
                    provider_code="429", http_status=429),
])
def test_mapping_refusal_and_http_failures_are_attempted_once_in_finalization(tmp_path, monkeypatch, error):
    original = LimitedClient.request
    seen = Counter()
    def request(self, api_id, path, body, continuation=None):
        if body["stk_cd"] == "P03" and self.clock() >= at(time(9, 25)):
            self.limiter.acquire()
            seen["P03"] += 1
            self.clock.tick(.2)
            raise error
        return original(self, api_id, path, body, continuation)
    monkeypatch.setattr(LimitedClient, "request", request)
    result, *_ = morning(tmp_path)
    assert seen["P03"] == 1
    detail = result.e_shadow["session_record"]["provider_errors"]["P03"]
    assert detail["retry_outcome"] == PEV.NOT_RETRYABLE and detail["attempts"] == 1
    assert result.report["e_finalization"]["provider_retry"]["retry_requests"] == 0


# -- telemetry ------------------------------------------------------------------------------------------

def test_rolling_and_sweep_errors_are_recorded_per_symbol(tmp_path, monkeypatch):
    original = LimitedClient.request
    def request(self, api_id, path, body, continuation=None):
        if body["stk_cd"] == "P20" and self.clock() < at(time(9, 25)):
            self.limiter.acquire()
            self.clock.tick(.2)
            raise code7("P20")
        return original(self, api_id, path, body, continuation)
    monkeypatch.setattr(LimitedClient, "request", request)
    result, *_ = morning(tmp_path)
    tel = result.report["provider_telemetry"]
    rolling = [e for e in tel["events"] if e["phase"] == PEV.ROLLING]
    sweep = [e for e in tel["events"] if e["phase"] == PEV.A_SWEEP]
    assert rolling and all(e["symbol"] == "P20" and e["retry_outcome"] == PEV.NEXT_CYCLE for e in rolling)
    assert len(sweep) == 1 and sweep[0]["retry_outcome"] == PEV.NOT_RETRIED
    event = rolling[0]
    assert set(event) == {"timestamp", "symbol", "phase", "api_id", "actual_lane", "requested_exchange",
                          "resolved_exchange", "attempt", "max_attempts", "http_status", "provider_return_code",
                          "provider_subcode", "sanitized_return_msg", "error_class", "error_code",
                          "error_category", "retryable", "retry_outcome"}
    assert event["api_id"] == FZ.MINUTE_API and event["actual_lane"] == "A" and event["http_status"] == 200
    assert tel["rolling_error_count"] == len(rolling) and tel["a_sweep_error_count"] == 1
    assert tel["provider_error_unrecovered_count"] == 2           # (P20, rolling) and (P20, a_sweep)
    json.dumps(result.report, default=str)


def test_detailed_events_are_capped_and_the_overflow_is_counted():
    clock = lambda: at(time(5))                                    # noqa: E731
    events = PEV.ProviderEvents(clock, max_events={PEV.ROLLING: 3})
    lane = FZ.Lane("A", FZ.MINUTE_API, None)
    for i in range(10):
        events.record(f"S{i}", PEV.ROLLING, lane, code7(), attempt=1, max_attempts=1, requested_exchange="ND",
                      resolved_exchange="ND", retry_outcome=PEV.NEXT_CYCLE)
    events.succeeded("S9", PEV.ROLLING)
    summary = events.summary({"A": {"status_429": 2, "status_5xx": 1, "transport_errors": 4}})
    assert summary["detailed_event_count"] == 3 and summary["dropped_event_count"] == 7
    assert summary["provider_error_count"] == 10 and summary["generic_code7_count"] == 10
    assert summary["provider_error_recovered_count"] == 1 and summary["provider_error_unrecovered_count"] == 9
    assert (summary["http_429_count"], summary["http_5xx_count"], summary["transport_error_count"]) == (2, 1, 4)
    text = json.dumps(summary)
    assert "authorization" not in text.lower() and "bearer" not in text.lower()
    assert sum(PEV.MAX_EVENTS.values()) == 500


# -- the immutable 2026-10-29 replay ---------------------------------------------------------------------

TEN = ("AKAM", "ARLO", "EBC", "GS", "LH", "MGY", "OII", "PRMB", "TSLA", "VFC")


def replay_1029():
    raw = (FIXTURES / "strategy_e_20261029_snapshot_0924.json.gz").read_bytes()
    ctx = json.loads(gzip.decompress((FIXTURES / "strategy_e_20261029_context.json.gz").read_bytes()))
    assert hashlib.sha256(raw).hexdigest() == ctx["snapshot_sha256"]
    return json.loads(gzip.decompress(raw)), ctx


def decide_1029(body, ctx):
    caches, spy = SS.e_caches_from(body)
    store = SimpleNamespace(preload=lambda *_: ctx["denominators"], unreachable_symbols=lambda: set(),
                            bootstrap_pending=lambda *_: [], coverage=lambda *_: {},
                            collection_state=lambda: {s: {"last_error": "MARKET_DATA_UNAVAILABLE"}
                                                      for s in ctx["documented"]})
    original = ctx["original"]["decision"]
    return ES.decide(caches, spy, ctx["universe"], unmapped=body["unmapped"], session=date(2026, 10, 29),
                     deadline=datetime(2026, 10, 29, 9, 29, 45, tzinfo=FZ.ET), store=store,
                     decided_at=datetime.fromisoformat(original["decided_at"]), source=original["source"])


def test_1029_replay_reproduces_production_exactly():
    body, ctx = replay_1029()
    out, original = decide_1029(body, ctx), ctx["original"]
    assert out["gate"] == original["gate"] and out["gate"]["status"] == "DECISION_INCOMPLETE_UNIVERSE"
    assert out["statuses_digest"] == original["statuses_digest"]
    assert out["feature_rows_digest"] == original["feature_rows_digest"]
    for field in ES.DECISION_FIELDS:
        assert SS.canonical(out["decision"][field]) == SS.canonical(original["decision"][field]), field
    assert sorted(out["gate"]["unexpected_stale_examples"]) == list(TEN)
    for symbol in TEN:
        entry = body["symbols"][symbol]
        assert entry["provider_code"] == "7" and entry["error_category"] == PE.TRANSIENT_PROVIDER_ERROR
        assert entry["finalized_at"] is None


def test_1029_symbols_recovered_on_a_retry_authorize_the_same_top3():
    """Case A: each of the ten answered on its second attempt. Their finalized bars are the 09:24
    cache production held (the real retry answer was never received); E's code is unchanged."""
    body, ctx = replay_1029()
    before = decide_1029(body, ctx)
    for symbol in TEN:
        entry = body["symbols"][symbol]
        for key in [*PE.FIELDS, *PE.RETRY_FIELDS, "error"]:
            entry.pop(key, None)
        entry.update(error=None, contiguous=True, finalized_at="2026-10-29T09:29:30-04:00",
                     data_source="KIWOOM_" + FZ.MINUTE_API, final_pages=1)
    after = decide_1029(body, ctx)
    assert after["gate"]["authorized"] and after["gate"]["status"] == "AUTHORIZED"
    assert after["gate"]["unexpected_stale"] == 0 and after["unexpected_stale_count"] == 0
    assert after["decision"]["selected"] == before["decision"]["selected"] == ["TPR", "INSM", "MAA"]
    assert after["decision"]["candidates"] == before["decision"]["candidates"]
    assert after["decision"]["r1_order"] == before["decision"]["r1_order"]
    assert after["status_counts"]["STALE"] == 1                     # PS, documented
    for symbol in ("TPR", "INSM", "MAA"):
        assert after["decision"]["features"][symbol] == before["decision"]["features"][symbol]


def test_1029_symbols_rejected_three_times_still_block():
    body, ctx = replay_1029()
    for symbol in TEN:
        body["symbols"][symbol].update(attempts=3, max_attempts=3, retry_outcome=PEV.EXHAUSTED)
    out = decide_1029(body, ctx)
    assert out["gate"] == ctx["original"]["gate"] and not out["gate"]["authorized"]
    assert out["_record"]["provider_errors"]["TSLA"]["attempts"] == 3
