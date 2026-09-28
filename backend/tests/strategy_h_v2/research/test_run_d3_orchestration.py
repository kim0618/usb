"""Tests for the D3 pilot orchestration (`app.dev.run_strategy_h_v2_d3`), using a fake
`call_opus` - no real model calls, no network, no cost.
"""

from __future__ import annotations

import json

from helpers import utc

import app.dev.run_strategy_h_v2_d3 as d3
from app.backtest.strategy_h_v2.evidence.bundle import CandidateSource, CollectionDepth, EvidenceBundleV2, EvidenceCompleteness
from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1, CandidateMaterializationRecord

NOW = utc(2026, 9, 28)

VALID_CONTENT = {
    "business_model": {}, "fundamental_change": [], "growth_durability":
        {"state": "UNKNOWN", "rationale": [], "evidence_checklist": {}},
    "future_business": [], "competitive_position": [], "management_execution": [],
    "catalyst_candidates": [], "why_now_candidate": {"summary": "worth checking", "reasons": []},
    "risks": [], "invalidation_candidates": [], "open_questions": [], "unknown_fields": [],
    "sources": [], "research_completeness": "INSUFFICIENT_EVIDENCE",
}


def _package() -> AIResearchInputV1:
    bundle = EvidenceBundleV2(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={"cik": "1"}, market={}, fundamentals={}, fundamental_changes={}, balance_sheet={},
        cashflow={}, price_context={}, earnings={}, filings=[], company_releases=[],
        investor_materials=[], material_events=[], source_manifest=[], data_quality={},
        unknown_fields=[],
    )
    return AIResearchInputV1(
        run_id="RUN-1", generated_at=NOW, evidence_bundle=bundle, chunks=[],
        materialization=CandidateMaterializationRecord(
            ticker="ACME", candidate_id="ACME", content_readiness="FULL", documents=[],
        ),
        source_manifest=[],
    )


def _ok_response(content: dict, cost: float = 0.5) -> dict:
    return {
        "result": json.dumps(content), "total_cost_usd": cost, "is_error": False,
        "modelUsage": {d3.MODEL: {"canonicalModel": d3.MODEL}},
    }


def test_successful_first_attempt_writes_ledger_and_reports_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(d3, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(d3, "call_opus", lambda system, user: _ok_response(VALID_CONTENT))
    result = d3.research_one(_package(), "ACME")
    assert result["status"] == "OK"
    assert result["repair_attempts"] == 0
    assert (tmp_path / "ledger" / "ACME" / "V1.json").exists()


def test_bounded_repair_succeeds_on_second_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(d3, "OUTPUT_ROOT", tmp_path)
    calls = {"n": 0}

    def fake_call(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"result": "not valid json", "total_cost_usd": 0.3, "is_error": False,
                     "modelUsage": {d3.MODEL: {"canonicalModel": d3.MODEL}}}
        return _ok_response(VALID_CONTENT, cost=0.2)

    monkeypatch.setattr(d3, "call_opus", fake_call)
    result = d3.research_one(_package(), "ACME")
    assert result["status"] == "OK"
    assert result["repair_attempts"] == 1
    assert calls["n"] == 2


def test_repair_is_bounded_and_fails_soft_not_infinite(tmp_path, monkeypatch):
    monkeypatch.setattr(d3, "OUTPUT_ROOT", tmp_path)
    calls = {"n": 0}

    def always_broken(system, user):
        calls["n"] += 1
        return {"result": "still not valid json", "total_cost_usd": 0.1, "is_error": False,
                 "modelUsage": {d3.MODEL: {"canonicalModel": d3.MODEL}}}

    monkeypatch.setattr(d3, "call_opus", always_broken)
    result = d3.research_one(_package(), "ACME")
    assert result["status"] == "SCHEMA_VALIDATION_FAILED"
    # 1 initial attempt + MAX_REPAIR_ATTEMPTS repairs, never more
    assert calls["n"] == 1 + d3.MAX_REPAIR_ATTEMPTS
    assert not (tmp_path / "ledger" / "ACME" / "V1.json").exists()


def test_model_call_failure_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(d3, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(d3, "call_opus", lambda system, user: {
        "result": "API Error: overloaded", "total_cost_usd": 0, "is_error": True,
    })
    result = d3.research_one(_package(), "ACME")
    assert result["status"] == "MODEL_CALL_FAILED"


def test_model_and_prompt_version_persisted_in_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(d3, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(d3, "call_opus", lambda system, user: _ok_response(VALID_CONTENT))
    d3.research_one(_package(), "ACME")
    from app.backtest.strategy_h_v2.research.ledger import read_research_output
    output = read_research_output(tmp_path, "ACME", 1)
    assert output.model == d3.MODEL
    assert output.prompt_version == d3.PROMPT_VERSION


def test_pick_pilot_tickers_is_deterministic_not_hand_picked(tmp_path, monkeypatch):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    for ticker, depth in [("ZETA", "CORE"), ("ALPHA", "FULL"), ("BETA", "FULL"), ("GAMMA", "CORE")]:
        (packages_dir / f"{ticker}.json").write_text(json.dumps({
            "evidence_bundle": {"collection_depth": depth},
        }))
    monkeypatch.setattr(d3, "PACKAGES_DIR", packages_dir)
    tickers = d3.pick_pilot_tickers(1, 1)
    assert tickers == ["ALPHA", "GAMMA"]  # alphabetically first of each depth, not curated
