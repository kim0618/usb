"""H-V2 NO-AI production evaluation under the frozen H_D3D4_DETERMINISTIC_PRODUCTION_V1.

* The contract file is checksum-verified and the D6 input backtrace is pinned to D6's own source.
* D3 (H_D3_DETERMINISTIC_V1) is a schema-valid record built from code-owned evidence only, with
  abstention wherever the contract leaves the reading to AI; it is refused (never written with zero
  catalysts) when the next periodic report cannot be derived.
* D4 (H_D4_DETERMINISTIC_V1) is UNKNOWN/UNKNOWN on every frozen fixture, with the C4 ceiling and the
  frozen precondition function; G1-G3 are recorded as reason codes.
* Unchanged D6 on these records: WATCH, or REJECT when D5 says fully priced / no catalyst in horizon;
  APPROVE unreachable (declared before running).
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
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.decision.d6_contract import D3Provenance, D4Provenance, Eligibility, decide
from app.dev.run_strategy_h_v2_d6 import project_d3, project_d4, project_d5
from app.strategies.h_auto import deterministic as DT
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


def _package(states=None):
    from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
    body = json.loads(FG_PACKAGE.read_text())
    for metric, state in (states or {}).items():
        body["evidence_bundle"]["fundamental_changes"][metric]["state"] = state
    return AIResearchInputV1.model_validate(body)


def _submissions(category="Accelerated filer", periodic=None):
    periodic = periodic if periodic is not None else [
        ("10-Q", "2026-08-06", "2026-06-30", "0001934850-26-000085"),
        ("10-Q", "2026-05-08", "2026-03-31", "0001934850-26-000057"),
        ("10-K", "2026-02-26", "2025-12-31", "0001934850-26-000026"),
        ("10-Q", "2025-11-07", "2025-09-30", "0001934850-25-000072")]
    rows = [("8-K", "2026-08-05", "2026-08-05", "0001934850-26-000081")] + list(periodic)
    return {"category": category, "fiscalYearEnd": "1231", "filings": {"recent": {
        "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows], "reportDate": [r[2] for r in rows],
        "accessionNumber": [r[3] for r in rows], "acceptanceDateTime": [r[1] + "T20:00:00.000Z" for r in rows]}}}


def _d3(states=None, *, decision=date(2026, 9, 16), submissions=None):
    return DT.d3_from(_package(states), cik="0001934850", decision_date=decision,
                      submissions_doc=_submissions() if submissions is None else submissions, run_id="T",
                      now=datetime(2026, 9, 28, 6, tzinfo=timezone.utc))


def _d3_for(ticker="FG"):
    prep = {"identity": T.IN.Identity(ticker, T.CIK.get(ticker, "1"), None, "XNYS", None),
            "package": T.FakePackage(T.CIK.get(ticker, "1"), [T.FG_8K]), "triggers_chunked": [T.FG_8K],
            "package_meta": {"package_sha256": "p"}}
    return {"research_id": f"D3D-{ticker}", "final_output": None, "final_output_checksum": None}


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
    rules = {r["key"]: r["now"] for r in DT.D6_INPUT_BACKTRACE if r["leg"] == "D3"}
    for key in ("business_model", "fundamental_change", "future_business", "catalyst_candidates", "risks",
                "invalidation_candidates", "unknown_fields", "materiality_candidate"):
        assert rules[key].startswith("R3."), key


# == the frozen contract ============================================================================

def test_contract_is_frozen_and_declares_its_rules_before_running():
    body = DT.contract()
    assert body["declaration"]["contract_id"] == "H_D3D4_DETERMINISTIC_PRODUCTION_V1"
    assert body["declaration"]["new_numeric_thresholds"] == 0
    assert set(body["d3"]["rules"]) >= {"R3.1_business_model", "R3.2_fundamental_change", "R3.5_catalyst"}
    assert set(body["d4"]["rules"]) >= {"G1_POSITIVE_WIDE_POSITIVE", "G2_NEGATIVE_WIDE_NEGATIVE", "G3_NEUTRAL", "G4_UNKNOWN"}
    assert "unreachable" in body["expected_effect_on_d6_declared_before_running"]["APPROVE"]


def test_statutory_deadlines_and_cadence():
    assert DT.filer_category({"category": "Large accelerated filer"}) == "LARGE_ACCELERATED"
    assert DT.filer_category({"category": "Non-accelerated filer<br>Smaller reporting company"}) == "OTHER"
    assert DT.filer_category({}) == "OTHER"
    cutoff = datetime(2026, 9, 28, tzinfo=timezone.utc)
    nxt = DT.next_periodic_report(_submissions(), cutoff=cutoff, decision_date=date(2026, 9, 16))
    assert (nxt["form"], nxt["period_end"], nxt["deadline"], nxt["within_horizon"]) == ("10-Q", "2026-09-30", "2026-11-09", True)
    tenk = DT.next_periodic_report(_submissions("Large accelerated filer", periodic=[
        ("10-Q", "2025-11-07", "2025-09-30", "a1"), ("10-Q", "2025-08-06", "2025-06-30", "a2"),
        ("10-Q", "2025-05-08", "2025-03-31", "a3"), ("10-K", "2025-02-26", "2024-12-31", "a4")]),
        cutoff=cutoff, decision_date=date(2026, 1, 10))
    assert (tenk["form"], tenk["period_end"], tenk["deadline"]) == ("10-K", "2025-12-31", "2026-03-02")   # 03-01 is a Sunday
    assert tenk["within_horizon"] is True
    few = DT.next_periodic_report(_submissions(periodic=[("10-Q", "2026-08-06", "2026-06-30", "a")]), cutoff=cutoff,
                                  decision_date=date(2026, 9, 16))
    assert few["state"] == "UNDERIVABLE"
    late = DT.next_periodic_report(_submissions(), cutoff=cutoff, decision_date=date(2026, 7, 1))
    assert late["within_horizon"] is False                                  # deadline 11-09 > 10-01


# == D3 ============================================================================================

@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
def test_d3_is_schema_valid_code_owned_and_abstains_where_the_contract_says():
    record = _d3()
    out = record["final_output"]
    assert record["final_status"] == "OK" and record["model_calls"] == 0 and out is not None
    assert out["business_model"]["revenue_drivers"][0]["evidence_id"].startswith("SEC:0001934850:0001934850-26-000026:CHUNK:")
    states = {c["metric"]: c["code_owned_state"] for c in out["fundamental_change"]}
    assert states == {"revenue": "DETERIORATING", "eps_diluted": "PROFIT_TO_LOSS", "shares_outstanding": "STABLE"}
    assert [c["type"] for c in out["catalyst_candidates"]] == ["earnings"]
    assert out["catalyst_candidates"][0]["materiality_candidate"] == "UNKNOWN"
    assert out["growth_durability"]["state"] == "UNKNOWN" and out["future_business"] == []
    assert out["invalidation_candidates"] == [] and out["research_completeness"] == "PARTIAL"
    assert {r["category"] for r in out["risks"]} == {"EVIDENCE_GAP"}
    projected = project_d3("FG", record)
    assert projected.provenance is D3Provenance.VALID and projected.business_model_populated
    assert projected.fundamental_change_anchored and not projected.blocking_unknown_fields


@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
@pytest.mark.parametrize("label,states,expect", [
    ("clear growth durable (improving everywhere)", {m: "IMPROVING" for m in ("revenue", "operating_income", "operating_margin", "free_cash_flow")},
     {"growth_durability": "UNKNOWN"}),
    ("clear deteriorating", {"revenue": "DETERIORATING", "operating_income": "INFLECTION_NEGATIVE"}, {"growth_durability": "UNKNOWN"}),
])
def test_d3_fixtures_durability_is_abstained_by_contract(label, states, expect):
    out = _d3(states)["final_output"]
    got = {c["metric"]: c["code_owned_state"] for c in out["fundamental_change"]}
    assert got["revenue"] == states["revenue"], label            # metrics without a filing source stay out (R3.2)
    assert out["growth_durability"]["state"] == expect["growth_durability"]       # R3.3: no frozen rule (E)


@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
def test_d3_future_business_is_never_staged_and_insufficient_evidence_is_refused_not_rejected():
    assert _d3()["final_output"]["future_business"] == []                     # R3.4 (E)
    refused = _d3(submissions=_submissions(periodic=[("10-Q", "2026-08-06", "2026-06-30", "0001934850-26-000085")]))
    assert refused["final_output"] is None and refused["refusal"] == "R3.5_CATALYST_UNDERIVABLE"
    assert project_d3("FG", refused).provenance is D3Provenance.REFUSED_NO_FINAL_OUTPUT
    empty = _d3({m: "UNKNOWN" for m in ("revenue", "eps_diluted", "shares_outstanding")})["final_output"]
    assert empty["fundamental_change"] == [] and "fundamental_change" in empty["unknown_fields"]


# == D6 regression on deterministic records (D6 unchanged) ============================================

@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
def test_d6_on_deterministic_records_watch_reject_and_never_approve():
    d3 = _d3()
    d4 = DT.d4_from_bundle(_bundle(), d3, analysis_id="A")
    watch = decide(project_d3("FG", d3), project_d4("FG", d4), project_d5(T.valuation_row("FG")))
    assert watch.decision.value == "WATCH" and watch.reject_fired == ()
    assert set(watch.approve_blockers) == {"future_business_or_catalyst_meaningful", "expectation_gap_permits_approve",
                                           "risks_with_an_invalidation_condition"}
    priced = dict(T.valuation_row("FG"), target_prices=dict(T.valuation_row("FG")["target_prices"], upside_to_TP2=-0.1))
    assert decide(project_d3("FG", d3), project_d4("FG", d4), project_d5(priced)).decision.value == "REJECT"
    late = _d3(decision=date(2026, 7, 1))
    out = decide(project_d3("FG", late), project_d4("FG", DT.d4_from_bundle(_bundle(), late, analysis_id="A")),
                 project_d5(T.valuation_row("FG")))
    assert out.decision.value == "REJECT" and out.reject_fired == ("no_catalyst_identifiable_in_the_thesis_horizon",)


# == D4 ============================================================================================

@pytest.mark.parametrize("label,bundle", [
    ("positive-looking: raised guidance, +12% reaction", _bundle()),
    ("negative-looking: -30% reaction", _bundle(price_reaction=[{"event_kind": "8-K 2.02", "return_1d": {"value": -0.3}}])),
    ("neutral-looking: flat reaction", _bundle(price_reaction=[{"event_kind": "8-K 2.02", "return_1d": {"value": 0.0}}])),
    ("no evidence at all", _bundle(price_reaction=[], guidance={"status": "NOT_FOUND", "excerpts": []})),
])
def test_d4_is_unknown_on_every_frozen_fixture(label, bundle):
    record = DT.d4_from_bundle(bundle, _d3_for(), analysis_id="A")
    out = record["final_output"]
    assert (out["expectation_gap"], out["expectation_gap_confidence"]) == ("UNKNOWN", "UNKNOWN"), label
    assert out["d6_approve_precondition"] == "BLOCKED" and out["priced_in_assessment"]["state"] == "UNKNOWN"
    assert out["confidence_ceiling"] == "MEDIUM" and "C4_NO_CONSENSUS_CONFIDENCE_CEILING" in out["applied_contract_rules"]
    assert ("G4_C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN" in out["reason_codes"]) is (not bundle["price_reaction"])
    assert {"G1_POSITIVE_INADMISSIBLE_C1_NO_CODE_OWNED_NON_PRICE_ITEM", "G2_NEGATIVE_NOT_PRODUCED_REFLECTED_MAGNITUDE_UNFROZEN"} <= set(out["reason_codes"])
    projected = project_d4("FG", record)
    assert projected.provenance is D4Provenance.EVALUATED and projected.precondition_disagreement is None
    assert not projected.gap_permits_approve


def test_d4_confidence_ceiling_is_the_frozen_c4_function():
    available = _bundle(consensus={"status": "FOUND", "excerpts": []})
    assert DT.d4_from_bundle(available, _d3_for(), analysis_id="A")["final_output"]["confidence_ceiling"] == "HIGH"


@pytest.mark.skipif(not BR_C.exists(), reason="stored D4 bundles not on this host")
@pytest.mark.parametrize("ticker", ["AEYE", "COLL", "FG", "VRRM"])
def test_d4_replays_stored_bundles(ticker):
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


# == the deterministic stages on the cohort and a new candidate ======================================

def _stages(*, refused=False):
    """D3 = H_D3_DETERMINISTIC_V1 on the stored FG package (real generator); D4 = the real D4 rules on a
    fixture bundle (the production D4 also needs a price panel, exercised by the real-data run)."""
    def d3(prep, *, run_id, work):
        subs = _submissions(periodic=[("10-Q", "2026-08-06", "2026-06-30", "x")]) if refused else _submissions()
        return _d3(submissions=subs)

    def d4(prep, d3_record, *, run_id, work):
        return DT.d4_from_bundle(_bundle(), d3_record, analysis_id=f"D4D-{prep['identity'].ticker}")
    return {"d3": d3, "d4": d4}


@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
@pytest.mark.parametrize("decision", ["WATCH", "REJECT"])
def test_cohort_member_reevaluated_without_ai_gets_a_d6_decision_and_enters_nothing(store, decision):
    T.seed({"FG": decision})
    HP.launch(at=T.PAPER_AT)
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    stages = _stages()
    out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=stages["d3"], d4=stages["d4"]))
    result = out["results"][0]
    assert result["status"] == Q.COMPLETE and result["ai_calls"] == 0
    assert result["final_decision"] == "WATCH" and result["decision_transition"] == f"{decision}->WATCH"
    T.store_bars("2026-10-14", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert not [e for e in HP.events() if e.get("ticker") == "FG" and e["event"] in ("PENDING_ENTRY", "BUY")]


@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
def test_refused_d3_leaves_the_decision_and_the_ledger_untouched(store):
    T.seed({"FG": "WATCH"})
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    ledger = len(ST.ledger())
    stages = _stages(refused=True)
    result = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=stages["d3"], d4=stages["d4"]))["results"][0]
    assert result["final_decision"] == "NOT_DECISION_ELIGIBLE" and result["ledger_written"] is False
    assert len(ST.ledger()) == ledger and T.DD.runtime_rows("FG") == []
    assert T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))["created"] == []


@pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
def test_new_candidate_evaluated_without_ai_is_admitted_with_its_decision_and_no_entry(store):
    T.seed({"FG": "WATCH"})
    HP.launch(at=T.PAPER_AT)
    T.SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "x", "candidates": [
        {"ticker": "NEWCO", "cik": T.CIK["NEWCO"], "e3_priority": "P1_HIGH", "intake_order": 1}]})
    T.refresh_queue({})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    stages = _stages()
    out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=stages["d3"], d4=stages["d4"]))
    assert out["results"][0]["decision_transition"] == "NONE->WATCH"
    assert ST.latest_per_ticker()["NEWCO"]["decision"] == "WATCH"
    T.store_bars("2026-10-14", {"NEWCO": (10, 10, 10, 10), "FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert not [e for e in HP.events() if e.get("ticker") == "NEWCO" and e["event"] in ("PENDING_ENTRY", "BUY")]


def test_status_view_shows_evaluation_not_ai_budget(store):
    T.seed({"FG": "WATCH"})
    body = ORC.status()
    assert body["mode"] == "DETERMINISTIC_NO_AI" and body["ai_calls"] == 0
    assert "budget" not in body and "authorized" not in body
    assert {"latest_universe", "latest_price_session", "sec_refresh_at", "evaluation_last_run_at",
            "next_auto_run_at"} <= set(body["timestamps"])
    assert "latest_evaluation" in body and body["contract"]["production_model"] == "NONE"
    assert datetime.fromisoformat(body["timestamps"]["next_auto_run_at"]) > datetime(2026, 1, 1, tzinfo=timezone.utc)
