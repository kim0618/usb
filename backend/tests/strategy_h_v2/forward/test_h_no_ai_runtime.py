"""H-V2 PRODUCTION NO-AI RUNTIME REPAIR: production evaluation makes no AI call and invents no rule.

* D6 input backtrace is pinned to the projection functions' own source.
* The code-owned D3 has no interpretation, so D6 projects it as refused and the decision stands
  (never an empty interpretation that would fire a REJECT trigger).
* The code-owned D4 is UNKNOWN/UNKNOWN on every bundle, with the frozen C4 ceiling and the frozen
  D6 precondition function, including on bundles where a model once said NEUTRAL.
* ``no_ai_guard`` blocks a Claude subprocess, an LLM client import, an LLM host and the research
  runners' model entry points, and the executor fails such an item without a ledger write.
* WATCH and REJECT cohort members re-evaluated with the production stages enter nothing.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.decision.d6_contract import D3Provenance, D4Provenance, Eligibility, decide
from app.dev.run_strategy_h_v2_d6 import project_d3, project_d4, project_d5
from app.strategies.h_auto import deterministic as DT
from app.strategies.h_auto import executor as EX
from app.strategies.h_auto import no_ai_guard as NG
from app.strategies.h_auto import orchestrator as ORC
from app.strategies.h_auto import queue as Q
from app.strategies.h_forward import research_queue as RQ
from app.strategies.h_forward import store as ST
from app.strategies.h_paper import service as HP
from tests.strategy_h_v2.forward import test_h_autonomous_pipeline as T

REPO = Path(__file__).resolve().parents[4]
FG_PACKAGE = REPO / "data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages/FG.json"
BR_C = REPO / "data/runtime/strategy_h_v2/d4_br_c/analyses/D4_BR_C-20261001T005758Z"
store = T.store


def _bundle(**over):
    body = {"consensus": {"status": "SOURCE_NOT_AVAILABLE", "excerpts": []},
            "estimate_revisions": {"status": "SOURCE_NOT_AVAILABLE", "excerpts": []},
            "guidance": {"status": "FOUND", "excerpts": [{"text": "we raise our outlook"}]},
            "earnings_history": {"status": "FOUND", "excerpts": []},
            "management_expectation_signals": {"status": "FOUND", "excerpts": []},
            "price_reaction": [{"event_kind": "8-K 2.02", "return_1d": {"value": 0.12}}]}
    body.update(over)
    return body


def _d3_for(ticker="FG"):
    prep = {"identity": T.IN.Identity(ticker, T.CIK.get(ticker, "1"), None, "XNYS", None),
            "package": T.FakePackage(T.CIK.get(ticker, "1"), [T.FG_8K]), "triggers_chunked": [T.FG_8K],
            "package_meta": {"package_sha256": "p"}}
    return DT.d3_code_owned(prep, run_id="R")


# == D6 input backtrace ============================================================================

def _keys(fn_names):
    src = (REPO / "backend/app/dev/run_strategy_h_v2_d6.py").read_text()
    keys = set()
    for fn in ast.parse(src).body:
        if isinstance(fn, ast.FunctionDef) and fn.name in fn_names:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get" and node.args \
                        and isinstance(node.args[0], ast.Constant):
                    keys.add(node.args[0].value)
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                    keys.add(node.slice.value)
    return keys


def test_d6_input_backtrace_covers_every_key_the_projections_read():
    table = {(r["leg"], r["key"]) for r in DT.D6_INPUT_BACKTRACE}
    assert {("D3", k) for k in _keys({"project_d3", "_claims_are_sourced"})} <= table
    assert {("D4", k) for k in _keys({"project_d4"})} <= table
    ai_only = {r["key"] for r in DT.D6_INPUT_BACKTRACE if r["leg"] == "D3" and r["now"] == "NOT_PRODUCED"}
    assert {"business_model", "future_business", "catalyst_candidates", "risks", "invalidation_candidates"} <= ai_only


# == D3 ============================================================================================

def test_d3_code_owned_has_no_interpretation_and_records_why():
    record = _d3_for()
    assert record["final_output"] is None and record["model_calls"] == 0 and record["generator"] == DT.D3_GENERATOR
    assert set(DT.D3_NOT_PRODUCED) <= set(record["not_produced"])
    assert record["code_owned"]["growth_durability_state"] == "UNKNOWN"
    assert record["code_owned"]["future_business_state"] == "UNKNOWN"
    assert project_d3("FG", record).provenance is D3Provenance.REFUSED_NO_FINAL_OUTPUT


@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
def test_d3_code_owned_reads_e2_states_with_sec_sources_from_a_real_package():
    package = json.loads(FG_PACKAGE.read_text())
    changes = {c["metric"]: c for c in DT.code_owned_fundamental_changes(package["evidence_bundle"], "0001934850")}
    assert changes["revenue"]["code_owned_state"] == "DETERIORATING"
    assert changes["revenue"]["source_id"] == "SEC:0001934850:0001934850-26-000085"
    assert changes["operating_income"]["code_owned_state"] == "UNKNOWN" and changes["operating_income"]["source_id"] is None


def test_d3_insufficient_evidence_is_unknown_never_an_empty_interpretation():
    record = _d3_for()
    record["code_owned"]["fundamental_changes"] = []
    d4 = DT.d4_from_bundle(_bundle(), record, analysis_id="A")
    out = decide(project_d3("FG", record), project_d4("FG", d4), project_d5(T.valuation_row("FG")))
    assert out.decision is None and out.eligibility.eligibility is Eligibility.NOT_DECISION_ELIGIBLE
    # what an empty interpretation would have done: REJECT from missing evidence
    empty = {"final_output": {"research_id": "x", "business_model": {}, "fundamental_change": []},
             "final_output_checksum": "c"}
    wrong = decide(project_d3("FG", empty), project_d4("FG", d4), project_d5(T.valuation_row("FG")))
    assert wrong.decision is not None and wrong.decision.value == "REJECT"


# == D4 ============================================================================================

@pytest.mark.parametrize("label,bundle", [
    ("positive-looking: raised guidance, +12% reaction", _bundle()),
    ("negative-looking: -30% reaction", _bundle(price_reaction=[{"event_kind": "8-K 2.02", "return_1d": {"value": -0.3}}])),
    ("neutral-looking: flat reaction", _bundle(price_reaction=[{"event_kind": "8-K 2.02", "return_1d": {"value": 0.0}}])),
    ("no evidence at all", _bundle(price_reaction=[], guidance={"status": "NOT_FOUND", "excerpts": []})),
])
def test_d4_code_owned_is_unknown_on_every_frozen_fixture(label, bundle):
    record = DT.d4_from_bundle(bundle, _d3_for(), analysis_id="A")
    out = record["final_output"]
    assert (out["expectation_gap"], out["expectation_gap_confidence"]) == ("UNKNOWN", "UNKNOWN"), label
    assert out["d6_approve_precondition"] == "BLOCKED" and out["priced_in_assessment"]["state"] == "UNKNOWN"
    assert out["confidence_ceiling"] == "MEDIUM" and "C4_NO_CONSENSUS_CONFIDENCE_CEILING" in out["applied_contract_rules"]
    assert ("C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN" in out["reason_codes"]) is (not bundle["price_reaction"])
    projected = project_d4("FG", record)
    assert projected.provenance is D4Provenance.EVALUATED and projected.precondition_disagreement is None
    assert not projected.gap_permits_approve


def test_d4_confidence_ceiling_is_the_frozen_c4_function():
    available = _bundle(consensus={"status": "FOUND", "excerpts": []})
    assert DT.d4_from_bundle(available, _d3_for(), analysis_id="A")["final_output"]["confidence_ceiling"] == "HIGH"


@pytest.mark.skipif(not BR_C.exists(), reason="stored D4 bundles not on this host")
@pytest.mark.parametrize("ticker", ["AEYE", "COLL", "FG", "VRRM"])
def test_d4_code_owned_replays_stored_bundles(ticker):
    bundle = json.loads((BR_C / ticker / "expectation_evidence.json").read_text())
    out = DT.d4_from_bundle(bundle, _d3_for(ticker), analysis_id="A")["final_output"]
    assert out["expectation_gap"] == "UNKNOWN" and out["confidence_ceiling"] == "MEDIUM"
    assert out["code_owned_expectation_evidence"]["price_reaction_events"] > 0


# == AI guard ======================================================================================

def test_guard_blocks_claude_subprocess_llm_imports_hosts_and_runner_entry_points(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/usr/bin/claude")
    with NG.forbid_ai() as guard:
        assert "CLAUDE_CODE_EXECPATH" not in os.environ
        with pytest.raises(NG.ProductionAICallBlocked):
            subprocess.run(["/usr/bin/claude", "-p"], input="x", capture_output=True)
        with pytest.raises(NG.ProductionAICallBlocked):
            subprocess.run("claude -p hi", shell=False)
        with pytest.raises(NG.ProductionAICallBlocked):
            __import__("anthropic")
        with pytest.raises(NG.ProductionAICallBlocked):
            socket.getaddrinfo("api.anthropic.com", 443)
        with pytest.raises(NG.ProductionAICallBlocked):
            socket.getaddrinfo("api.openai.com", 443)
        from app.dev import run_strategy_h_v2_d3_3 as D33
        from app.dev import run_strategy_h_v2_d4_2 as D42
        for fn in (D33.call_opus, D42.call_opus):
            with pytest.raises(NG.ProductionAICallBlocked):
                fn("s", "u")
        assert subprocess.run(["true"]).returncode == 0           # ordinary subprocesses still work
    assert len(guard.blocked) == 7
    assert os.environ["CLAUDE_CODE_EXECPATH"] == "/usr/bin/claude"
    assert subprocess.Popen is not None and D33.call_opus.__name__ == "call_opus"


def test_an_item_that_reaches_for_a_model_fails_and_writes_nothing(store):
    T.seed({"FG": "WATCH"})
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    before = len(ST.ledger())

    def sneaky_d3(prep, *, run_id, work):
        from app.dev.run_strategy_h_v2_d3_3 import call_opus
        return call_opus("system", "user")

    out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=sneaky_d3))
    item = Q.fold()["REFRESH_DUE:FG:1"]
    assert item["status"] == Q.FAILED and item["status_reason"] == "PRODUCTION_AI_CALL_BLOCKED"
    assert out["results"][0]["ai_calls_blocked"] and len(ST.ledger()) == before


def test_production_package_has_no_model_wiring():
    root = REPO / "backend/app/strategies/h_auto"
    assert not (root / "providers.py").exists() and not (root / "budget.py").exists()
    for path in root.glob("*.py"):
        if path.name == "no_ai_guard.py":
            continue
        text = path.read_text()
        tree = ast.parse(text)
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert "app.dev.run_strategy_h_v2_d3_3" not in modules, path.name
        assert not {m for m in modules if m.split(".")[0] in ("anthropic", "openai", "subprocess")}, path.name
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
                {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert not names & {"analyze_one_v2", "research_one_v3", "call_opus", "live_call"}, path.name
    units = REPO / "deploy/systemd"
    assert not (units / "usb-h-research.service").exists()
    for unit in units.glob("usb-h-*"):
        assert "CLAUDE_CODE_EXECPATH" not in unit.read_text() and "claude" not in unit.read_text().lower(), unit.name


# == the production stages on the cohort: decisions stand, nothing enters ===========================

def _prod_like_stages():
    """Production D3 (real) and D4 (the real code path on a fixture bundle; the real one needs a panel)."""
    def d4(prep, d3, *, run_id, work):
        return DT.d4_from_bundle(_bundle(), d3, analysis_id=f"D4C-{prep['identity'].ticker}")
    return {"d3": EX._d3, "d4": d4}


@pytest.mark.parametrize("decision", ["WATCH", "REJECT"])
def test_watch_or_reject_member_reevaluated_without_ai_keeps_its_decision_and_enters_nothing(store, decision):
    T.seed({"FG": decision})
    HP.launch(at=T.PAPER_AT)
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    ledger = len(ST.ledger())
    stages = _prod_like_stages()
    out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=stages["d3"], d4=stages["d4"]))
    result = out["results"][0]
    assert result["status"] == Q.COMPLETE and result["ledger_written"] is False
    assert result["final_decision"] == "NOT_DECISION_ELIGIBLE"
    assert result["decision_transition"] == f"{decision}->NOT_DECISION_ELIGIBLE" and result["ai_calls"] == 0
    assert len(ST.ledger()) == ledger and ST.latest_per_ticker()["FG"]["decision"] == decision
    # not marked as read by a thesis, and not queued again for the same filing
    assert T.DD.runtime_rows("FG") == [] and T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))["created"] == []
    T.store_bars("2026-10-14", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert not [e for e in HP.events() if e.get("ticker") == "FG" and e["event"] in ("PENDING_ENTRY", "BUY")]


def test_new_candidate_evaluated_without_ai_is_not_admitted(store):
    T.seed({"FG": "WATCH"})
    T.SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "x", "candidates": [
        {"ticker": "NEWCO", "cik": T.CIK["NEWCO"], "e3_priority": "P1_HIGH", "intake_order": 1}]})
    T.refresh_queue({})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    stages = _prod_like_stages()
    out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=stages["d3"], d4=stages["d4"]))
    assert out["results"][0]["decision_transition"] == "None->NOT_DECISION_ELIGIBLE"
    assert "NEWCO" not in ST.latest_per_ticker()


def test_status_view_shows_evaluation_not_ai_budget(store):
    T.seed({"FG": "WATCH"})
    body = ORC.status()
    assert body["mode"] == "DETERMINISTIC_NO_AI" and body["ai_calls"] == 0
    assert "budget" not in body and "authorized" not in body
    assert {"latest_universe", "latest_price_session", "sec_refresh_at", "evaluation_last_run_at",
            "next_auto_run_at"} <= set(body["timestamps"])
    assert "latest_evaluation" in body and body["contract"]["production_model"] == "NONE"
    assert datetime.fromisoformat(body["timestamps"]["next_auto_run_at"]) > datetime(2026, 1, 1, tzinfo=timezone.utc)
