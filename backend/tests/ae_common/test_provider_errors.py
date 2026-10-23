"""Provider taxonomy, authoritative mapping recovery and immutable 10/22 replay."""

from datetime import date, datetime, time
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.ae_common import e_shadow as ES, snapshot as SS
from app.ae_common.deadline import DeadlineReached
from app.core.exceptions import MarketDataError
from app.core import provider_errors as PE
from app.dev.run_e_rt2_dryrun import execution_gate
from app.integrations.kiwoom.client import KiwoomPage
from app.strategy_e_max_rt import availability as AV, finalizer as FZ
from tests.ae_common.helpers import at, SESSION, minute_rows
from tests.test_kiwoom_stage10a import client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def gate(statuses, documented=()):
    return execution_gate(decided_at=at(time(9, 29)), open_et=at(time(9, 30)), statuses=statuses,
                          documented=set(documented), rvol_unknown=[], unreachable=set(), pending=[])


def cache_error(error, repeats=2):
    cache = FZ.SymbolCache("TEST", "ND", "TEST")
    cache.final_attempted = True
    for _ in range(repeats):
        cache.record_error(error)
    return cache


@pytest.mark.parametrize("error", [
    MarketDataError("PROVIDER_TIMEOUT", "private raw token=secret"),
    MarketDataError("MARKET_DATA_UNAVAILABLE", "HTTP 500", error_category=PE.TRANSIENT_PROVIDER_ERROR),
    httpx.ConnectError("connection reset"),
    DeadlineReached(),
    MarketDataError("PAGINATION_INCOMPLETE", "missing pages"),
    MarketDataError("UNSUPPORTED_EXCHANGE", "unmapped"),
    MarketDataError("INVALID_SYMBOL", "1903", error_category=PE.MAPPING_ERROR),
    MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom market-data query was rejected"),
])
def test_unreceived_data_remains_unexpected_stale_and_blocks(error, tmp_path):
    cache = cache_error(error)
    body = SS.body_0924(SESSION, {"TEST": cache}, FZ.SymbolCache("SPY", "NA"),
                        universe=["TEST"], unmapped=[], universe_digest="fixture", finalization={})
    SS.seal(tmp_path, SS.SNAPSHOT_0924, body, sealed_at=at(time(9, 29)))
    sealed = SS.load(tmp_path, SS.SNAPSHOT_0924, fmt=SS.FORMAT_0924, session=SESSION)
    loaded, _ = SS.e_caches_from(sealed.body)
    assert loaded["TEST"].error == type(error).__name__
    assert loaded["TEST"].error_detail == cache.error_detail
    assert "secret" not in json.dumps(sealed.body)
    state = AV.classify(AV.outcome_from_cache(loaded["TEST"], deadline=at(time(9, 29, 45))))
    result = gate({"TEST": state})
    assert state == AV.STALE
    assert result["unexpected_stale"] == 1 and not result["authorized"]
    assert result["status"] == "DECISION_INCOMPLETE_UNIVERSE"


def refusal():
    return MarketDataError("INVALID_SYMBOL", "private provider text", provider="KIWOOM",
                           error_category=PE.SOURCE_REFUSAL, provider_code="fixture-explicit-refusal")


def test_only_repeated_explicit_current_session_refusal_is_expected_unavailable():
    cache = cache_error(refusal())
    state = AV.classify(AV.outcome_from_cache(cache, deadline=at(time(9, 29, 45))))
    assert state == AV.MARKET_DATA_UNAVAILABLE and gate({"TEST": state})["authorized"]
    assert AV.h5_status(state, None) == AV.H5_UNKNOWN and not AV.executable(state)
    for change in ("single", "served", "no_final_attempt", "transient_after_refusal"):
        c = cache_error(refusal(), repeats=1 if change == "single" else 2)
        if change == "served": c.bars[540] = [10., 11., 9., 10., 100.]
        if change == "no_final_attempt": c.final_attempted = False
        if change == "transient_after_refusal": c.record_error(TimeoutError())
        assert AV.classify(AV.outcome_from_cache(c, deadline=at(time(9, 29, 45)))) == AV.STALE


def test_unmapped_symbol_keeps_existing_known_unavailable_contract_and_ps_remains_documented():
    assert ES.statuses_of({}, ["UNMAPPED"], at(time(9, 29, 45))) == {"UNMAPPED": AV.MARKET_DATA_UNAVAILABLE}
    assert gate({"PS": AV.STALE}, documented={"PS"})["authorized"]
    assert AV.reclassify(AV.STALE, "MarketDataError") == AV.STALE
    assert AV.reclassify(AV.STALE, "MarketDataError:PROVIDER_TIMEOUT") == AV.STALE


@pytest.mark.parametrize("mode", ["mapping", "500", "timeout", "connection", "rate_limit", "refusal", "unknown_rejection"])
def test_real_client_boundary_preserves_provider_semantics(mode):
    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "fixture", "expires_dt": "20990101000000", "return_code": 0})
        if mode == "timeout": raise httpx.ReadTimeout("private raw text", request=request)
        if mode == "connection": raise httpx.ConnectError("private raw text", request=request)
        if mode in {"500", "rate_limit"}: return httpx.Response(500 if mode == "500" else 429)
        if mode == "mapping": return httpx.Response(200, json={"return_code": 7, "return_msg": "[1903:종목 정보가 없습니다]"})
        if mode == "refusal": return httpx.Response(200, json={"return_code": 7, "error_code": "MARKET_DATA_UNAVAILABLE"})
        return httpx.Response(200, json={"return_code": 7, "return_msg": "provider busy"})
    c = client(httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)
    with pytest.raises(MarketDataError) as failure:
        c.request("usa06011", "/api/us/chart", {"stex_tp": "ND", "stk_cd": "TEST"})
    detail = PE.classify(failure.value)
    expected = PE.MAPPING_ERROR if mode == "mapping" else PE.SOURCE_REFUSAL if mode == "refusal" else PE.TRANSIENT_PROVIDER_ERROR
    assert detail.error_category == expected
    assert detail.provider == "KIWOOM" and detail.error_class == "MarketDataError"
    if mode == "mapping": assert detail.provider_code == "1903" and detail.error_code == "INVALID_SYMBOL"
    assert c.order_request_count == 0


@pytest.mark.parametrize("listing_mode", ["correct", "conflict", "absent", "truncated", "deadline", "http500"])
def test_mapping_recovery_requires_a_complete_unambiguous_authority(listing_mode):
    calls = []
    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "fixture", "expires_dt": "20990101000000", "return_code": 0})
        body = json.loads(request.content); api = request.headers["api-id"]
        calls.append((api, body["stex_tp"]))
        if api == "usa10099":
            if listing_mode == "deadline": raise DeadlineReached()
            if listing_mode == "http500": return httpx.Response(500)
            matches = body["stex_tp"] == "NA" or (listing_mode == "conflict" and body["stex_tp"] == "ND")
            rows = [{"stk_cd": "CECO"}] if matches and listing_mode != "absent" else []
            return httpx.Response(200, json={"return_code": 0, "list": rows},
                                  headers={"cont-yn": "Y" if listing_mode == "truncated" else "N", "next-key": "next"})
        if body["stex_tp"] == "NA":
            return httpx.Response(200, json={"return_code": 0, "result_list": minute_rows(SESSION, 564)})
        return httpx.Response(200, json={"return_code": 7, "return_msg": "[1903:종목 정보가 없습니다]"})
    c = client(httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0, recover_chart_mapping=True)
    cache = FZ.SymbolCache("CECO", "ND", "CECO", complete_through=550)
    cache.bars[550] = [999., 999., 999., 999., 999.]
    lane = FZ.Lane("A", FZ.MINUTE_API, c)
    if listing_mode == "correct":
        FZ.refresh(lane, cache, SESSION, lambda: at(time(9, 25)))
        assert cache.exchange == "NA" and cache.complete_through == 564
        assert cache.bars[550] != [999.] * 5
        assert len([api for api, _ in calls if api == "usa10099"]) == 3
    else:
        with pytest.raises((MarketDataError, DeadlineReached)):
            FZ.refresh(lane, cache, SESSION, lambda: at(time(9, 25)))
        assert cache.exchange == "ND" and not cache.finalized_at
    assert c.order_request_count == 0


def replay_inputs():
    raw = (FIXTURES / "strategy_e_20261022_snapshot_0924.json.gz").read_bytes()
    ctx = json.loads(gzip.decompress((FIXTURES / "strategy_e_20261022_context.json.gz").read_bytes()))
    assert hashlib.sha256(raw).hexdigest() == ctx["snapshot_sha256"]
    return json.loads(gzip.decompress(raw)), ctx


def replay(body, ctx):
    caches, spy = SS.e_caches_from(body)
    original = ctx["original"]
    store = SimpleNamespace(preload=lambda *_: ctx["denominators"], unreachable_symbols=lambda: set(),
                            bootstrap_pending=lambda *_: [], coverage=lambda *_: {},
                            collection_state=lambda: {s: {"last_error": "MARKET_DATA_UNAVAILABLE"} for s in ctx["documented"]})
    return ES.decide(caches, spy, ctx["universe"], unmapped=body["unmapped"], session=date(2026, 10, 22),
                     deadline=datetime(2026, 10, 22, 9, 29, 45, tzinfo=FZ.ET), store=store,
                     decided_at=datetime.fromisoformat(original["decision"]["decided_at"]),
                     source=original["decision"]["source"])


def test_1022_exact_replay_mapping_error_cannot_authorize_missing_bars():
    body, ctx = replay_inputs()
    before = replay(body, ctx)
    for sym in ("CECO", "IBOC"):
        assert ctx["a_errors"][sym]["error"] == "SOURCE_REFUSAL:INVALID_SYMBOL"
        assert body["symbols"][sym]["error"] == "MarketDataError"
        assert body["symbols"][sym]["bars"] == []
        body["symbols"][sym].update(PE.classify(MarketDataError("INVALID_SYMBOL", "sanitized", provider="KIWOOM",
                                                                               error_category=PE.MAPPING_ERROR,
                                                                               provider_code="1903")).to_json())
    after = replay(body, ctx)
    assert before["gate"] == ctx["original"]["gate"]
    assert before["gate"]["unexpected_stale"] == after["gate"]["unexpected_stale"] == 2
    assert after["gate"]["status"] == "DECISION_INCOMPLETE_UNIVERSE" and not after["gate"]["authorized"]
    for field in ES.DECISION_FIELDS:
        assert SS.canonical(before["decision"][field]) == SS.canonical(ctx["original"]["decision"][field]), field
        assert SS.canonical(after["decision"][field]) == SS.canonical(before["decision"][field]), field
    assert after["decision"]["selected"] == ["DAR", "TRV", "VRTX"]
    assert after["source_refusal_count"] == 1  # PS, existing documented refusal
    assert {e["error_category"] for e in after["unexpected_stale_examples"]} == {PE.MAPPING_ERROR}
    assert after["_record"]["unexpected_stale_count"] == 2
    # The actual probe disproves the structural-refusal premise, on both lanes twice.
    for symbol in ("CECO", "IBOC"):
        rows = [r for r in ctx["provider_probe"]["charts"] if r["symbol"] == symbol]
        assert len(rows) == 12
        assert all(r["return_code"] == 0 and r["bars"] == 100 for r in rows if r["exchange"] == "NA")
        assert all(r["return_code"] == 7 for r in rows if r["exchange"] in {"ND", "NY"})


@pytest.mark.parametrize("error,expected", [
    (refusal(), AV.MARKET_DATA_UNAVAILABLE),
    (MarketDataError("INVALID_SYMBOL", "1903", provider="KIWOOM", error_category=PE.MAPPING_ERROR, provider_code="1903"), AV.STALE),
    (MarketDataError("PROVIDER_TIMEOUT", "timeout"), AV.STALE),
    (MarketDataError("MARKET_DATA_UNAVAILABLE", "500", error_category=PE.TRANSIENT_PROVIDER_ERROR), AV.STALE),
    (httpx.ConnectError("reset"), AV.STALE),
    (MarketDataError("PAGINATION_INCOMPLETE", "deadline"), AV.STALE),
])
def test_common_producer_catches_and_serializes_the_same_a_and_e_semantics(tmp_path, monkeypatch, error, expected):
    from tests.ae_common.helpers import LimitedClient
    from tests.ae_common.test_producer import morning
    original = LimitedClient.request
    def request(self, api_id, path, body, continuation=None):
        if body["stk_cd"] == "P00":
            self.clock.tick(.2)
            raise error
        return original(self, api_id, path, body, continuation)
    monkeypatch.setattr(LimitedClient, "request", request)
    result, *_ = morning(tmp_path)
    a = SS.load(tmp_path / "snap", SS.SNAPSHOT_0915).body["symbols"]["P00"]
    e = SS.load(tmp_path / "snap", SS.SNAPSHOT_0924).body["symbols"]["P00"]
    assert {k: a[k] for k in PE.FIELDS} == {k: e[k] for k in PE.FIELDS}
    assert e["error"] == type(error).__name__ and e["final_attempted"]
    assert a["status"] == (SS.MARKET_DATA_UNAVAILABLE if expected == AV.MARKET_DATA_UNAVAILABLE else SS.FAILED)
    out = result.e_shadow["from_snapshot"]
    assert out["unexpected_stale_count"] == (expected == AV.STALE)
    assert out["source_refusal_count"] == (expected == AV.MARKET_DATA_UNAVAILABLE)
    assert result.e_shadow["session_record"]["provider_errors"]["P00"]["error_code"] == PE.classify(error).error_code
    assert result.e_shadow["parity"]["exact"]
