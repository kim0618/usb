"""H-V2 AUTONOMOUS PIPELINE V1: queue, dedup, budget, retry, executor, transitions and paper, end to end.

Every test runs on a temporary forward store (``STRATEGY_H_FORWARD_DIR``). No test calls a model:
the model is a fake ``call_fn`` and the expensive stages are the executor's injectable seams. D6 is
the real ``decide`` and the paper book is the real engine, so the dry runs below exercise the
frozen decision and execution rules on synthetic evidence. No price here is a real forward price.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.strategies import registry as REG
from app.strategies.h_auto import budget as BU
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import dedup as DD
from app.strategies.h_auto import executor as EX
from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import intake as INT
from app.strategies.h_auto import orchestrator as ORC
from app.strategies.h_auto import providers as PV
from app.strategies.h_auto import queue as Q
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import contract as FC
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import research_queue as RQ
from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import transitions as TR
from app.strategies.h_paper import service as HP

H = REG.STRATEGY_H_V2
D5 = FC.required_d5_contract()
LAUNCH_D7 = "2026-10-04T08:34:30+00:00"
PAPER_AT = "2026-10-13T12:00:00+00:00"
RUN_AT = datetime(2026, 10, 13, 21, 30, tzinfo=timezone.utc)        # after the 10-13 close
CIK = {"FG": "0001934850", "VRRM": "0001682745", "AEYE": "0001362190", "NEWCO": "0000000777"}
FG_8K, VRRM_8K, AEYE_8K = "0001934850-26-000095", "0001193125-26-412497", "0001104659-26-108940"
OK_MODEL = {"total_cost_usd": 1.3, "is_error": False, "result": "{}",
            "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}}}


# -- store fixtures ---------------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(ST.ROOT_ENV, str(tmp_path / "d7"))
    return tmp_path / "d7"


def seed(decisions: dict[str, str]) -> None:
    ST.append_snapshots([{"record": "LAUNCH", "strategy_id": H, "ticker": t, "cik": CIK.get(t, "0000000001"),
                          "security_id": f"BBG{t}", "exchange": "XNYS", "d5_contract": D5, "decision": d,
                          "thesis_version": f"T1:{t}", "cohort_tag": FC.INITIAL_COHORT,
                          "launch_baseline_session": "2026-10-02", "decision_session": "2026-09-16",
                          "launch_timestamp": LAUNCH_D7, "valuation": {"tp1": 12.0, "tp2": 15.0, "bear": 8.0}}
                         for t, d in sorted(decisions.items())])
    ST.append_ledger([{"strategy_id": H, "ticker": t, "thesis_version": f"T1:{t}", "decision_time": LAUNCH_D7,
                       "record": "LAUNCH_STATE", "decision": d, "previous_decision": None, "cause": "LAUNCH",
                       "cohort_tag": FC.INITIAL_COHORT, "tp1": 12.0, "tp2": 15.0, "bear": 8.0, "d5_contract": D5}
                      for t, d in sorted(decisions.items())])


def refresh_queue(filings: dict[str, list[dict]]) -> None:
    SR.atomic_write_json(ST.path("refresh_queue.json"), {
        "generated_at": "2026-10-13T05:12:51+00:00", "cache_as_of": "2026-10-13",
        "issuers": [{"ticker": t, "cik": CIK[t], "state": "REFRESH_DUE", "new_material_filings": f}
                    for t, f in filings.items()],
        "refresh_due": sorted(filings)})


def caches(items: dict[str, dict[str, str]]) -> dict[str, SR.Cache]:
    return {t: SR.Cache(rows=[{"accessionNumber": a, "items": i} for a, i in rows.items()], path="x",
                        fetched_on="2026-10-13") for t, rows in items.items()}


INDEX = {"issuers": {"AEYE": {"thesis_version": "T1:AEYE", "chunked_accessions": [AEYE_8K], "data_cutoff": "2026-09-28"},
                     "FG": {"thesis_version": "T1:FG", "chunked_accessions": ["0001934850-26-000081"]},
                     "VRRM": {"thesis_version": "T1:VRRM", "chunked_accessions": ["0001682745-26-000010"]}}}
CACHES = caches({"FG": {FG_8K: "2.02,7.01"}, "VRRM": {VRRM_8K: "5.02,7.01,9.01"},
                 "AEYE": {AEYE_8K: "1.01,2.03,9.01"}})


def sync(plan: dict) -> dict:
    """Queue sync at a fixed time before RUN_AT, so no test depends on the wall clock."""
    return Q.sync(plan, now="2026-10-13T06:00:00+00:00")


def production_like_queue() -> dict:
    refresh_queue({"AEYE": [{"form": "8-K", "filingDate": "2026-09-18", "accession": AEYE_8K}],
                   "FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}],
                   "VRRM": [{"form": "8-K", "filingDate": "2026-10-02", "accession": VRRM_8K}]})
    return RQ.build(caches=CACHES, index=INDEX)


# -- fake stages ------------------------------------------------------------------------------------

class FakePackage:
    def __init__(self, cik: str, accessions):
        self.body = {"evidence_bundle": {"data_cutoff": RUN_AT.isoformat(),
                                         "filings": [{"source_id": f"SEC:{cik}:{a}"} for a in accessions]},
                     "chunks": [{"source_id": f"SEC:{cik}:{a}:EX-99.1"} for a in accessions]}

    def model_dump_json(self, **_):
        return json.dumps(self.body)


def fake_prepare(item, deps, *, work, run_id):
    if item["ticker"] not in EX._judgements(deps):
        raise IN.InputsNotReady("D5_METHOD_JUDGEMENT_REQUIRED", item["ticker"])
    accessions = list(item.get("source_accessions") or []) or ["0000000777-26-000001"]
    cik = CIK.get(item["ticker"], "0000000001")
    path = work / "packages" / f"{item['ticker']}.json"
    return {"identity": IN.Identity(item["ticker"], cik, None, "XNYS", None), "session": date(2026, 10, 13),
            "panel": None, "coverage": {"sources": {"h_d7": 1}},
            "sec": SimpleNamespace(submissions_fetched_at=RUN_AT), "package": FakePackage(cik, accessions),
            "package_meta": {"package_sha256": "pkg"}, "package_path": path, "cutoff": RUN_AT,
            "triggers_chunked": accessions}


def d3_record(ticker: str) -> dict:
    out = {"research_id": f"R-{ticker}", "research_completeness": "COMPLETE",
           "business_model": {"revenue_drivers": ["contracts"]},
           "fundamental_change": [{"claim": "margin", "source_id": "SEC:x:y"}],
           "growth_durability": {"state": "DURABLE"},
           "future_business": [{"stage": "REVENUE", "current_revenue_evidence": True}],
           "catalyst_candidates": [{"materiality_candidate": "HIGH", "timing_confidence": "MEDIUM"}],
           "competitive_position": [{"x": 1}], "risks": [{"r": 1}],
           "invalidation_candidates": [{"i": 1}], "unknown_fields": [], "evidence_conflicts": []}
    return {"final_status": "OK", "model_mismatch": False, "final_output": out,
            "final_output_checksum": f"c3-{ticker}"}


def make_d3(*, status="OK"):
    def d3(prep, model, *, run_id, work):
        model.stage = "d3"
        model("s", "u")
        record = d3_record(prep["identity"].ticker)
        record["final_status"] = status
        if status != "OK":
            record["final_output"] = None
        return record
    return d3


def make_d4(gap="POSITIVE", confidence="MEDIUM"):
    def d4(prep, d3, model, *, run_id, work):
        model.stage = "d4"
        model("s", "u")
        permitted = gap in ("POSITIVE", "WIDE_POSITIVE") and confidence in ("HIGH", "MEDIUM")
        return {"final_status": "OK", "model_mismatch": False, "analysis_id": "A1",
                "research_input_id": d3["final_output"]["research_id"],
                "research_output_checksum": d3["final_output_checksum"],
                "final_output_checksum": f"c4-{gap}",
                "final_output": {"analysis_id": "A1", "expectation_gap": gap,
                                 "expectation_gap_confidence": confidence,
                                 "d6_approve_precondition": "PERMITTED" if permitted else "BLOCKED",
                                 "conflicts": [], "unknown_fields": []}}
    return d4


def valuation_row(ticker: str) -> dict:
    return {"ticker": ticker, "status": "VALUED", "confidence": {"confidence": "MEDIUM", "drivers": []},
            "primary_method": "P/E", "secondary_method": None, "contract_window": "FULL_2Y",
            "current_price": 10.0, "reconciliation": {"status": "AGREE"}, "valuation_risk": [],
            "target_prices": {"TP1": 12.0, "TP2": 15.0, "BEAR_ANCHOR": 8.0, "upside_to_TP1": 0.2,
                              "upside_to_TP2": 0.5, "downside_to_bear": -0.2},
            "fair_value": {"bear": {}}}


def real_d6(prep, d3, d4, deps):
    from app.backtest.strategy_h_v2.decision.d6_contract import decide
    from app.dev.run_strategy_h_v2_d6 import project_d3, project_d4, project_d5
    t = prep["identity"].ticker
    row = valuation_row(t)
    return row, decide(project_d3(t, d3), project_d4(t, d4), project_d5(row))


def deps(*, gap="POSITIVE", call=None, d3=None, judgements=("FG", "VRRM", "AEYE", "NEWCO"), now=RUN_AT):
    return EX.Deps(call_fn=call or (lambda s, u: dict(OK_MODEL)), sec_client_factory=lambda: None,
                   judgements={t: object() for t in judgements}, now=lambda: now,
                   prepare_fn=fake_prepare, d3_fn=d3 or make_d3(), d4_fn=make_d4(gap), d5d6_fn=real_d6)


def authorize(cap=24.0, scope="REFRESH_AND_NEW_CANDIDATE"):
    return BU.authorize(by="test", cumulative_cap_usd=cap, scope=scope, at="2026-01-01T00:00:00+00:00")


def store_bars(session: str, rows: dict[str, tuple]) -> None:
    PR.write_session(session, {t: {"open": o, "high": h, "low": l, "close": c, "volume": 1}
                               for t, (o, h, l, c) in rows.items()}, fetched_at="2026-10-23T05:00:00+00:00")


# == contract ======================================================================================

def test_contract_is_frozen_with_budget_model_and_d5():
    state = AC.state()
    assert state["contract_id"] == "H_V2_AUTONOMOUS_PIPELINE_V1"
    assert (state["daily_hard_budget_usd"], state["per_candidate_reservation_usd"],
            state["max_research_candidates_per_day"]) == (24.0, 12.0, 2)
    assert state["model"] == "claude-opus-5-5" and AC.canonical_model() == "claude-opus-5-5"
    assert AC.contract()["model"]["fallback_model"] == "FORBIDDEN"
    assert state["d5_contract"] == "D5_D2R_V1"
    assert state["first_batch"]["tickers"] == ["FG", "VRRM"]


# == dedup, queue, priority ========================================================================

def test_fg_p0_then_vrrm_p1_and_aeye_already_in_thesis(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    plan = production_like_queue()
    by = {r["ticker"]: r for r in plan["rows"]}
    assert plan["queued"] == ["FG", "VRRM"]
    assert by["FG"]["priority_class"] == "P0" and by["VRRM"]["priority_class"] == "P1"
    assert by["AEYE"]["research_status"] == RQ.EVIDENCE_ALREADY_IN_THESIS
    assert by["AEYE"]["source_event"][0]["dedup_rule"] == "CHUNKED_IN_THESIS_PACKAGE"
    sync(plan)
    items = Q.fold()
    assert items["REFRESH_DUE:AEYE:1"]["status"] == Q.EVIDENCE_ALREADY_IN_THESIS
    assert [i["ticker"] for i in Q.ordered(now="2099-01-01")] == ["FG", "VRRM"]


def test_item_7_01_alone_is_not_promoted_to_p0(store):
    seed({"VRRM": "WATCH"})
    refresh_queue({"VRRM": [{"form": "8-K", "filingDate": "2026-10-02", "accession": "A-1"}]})
    plan = RQ.build(caches=caches({"VRRM": {"A-1": "7.01"}}), index=INDEX)
    assert plan["rows"][0]["priority_class"] == "P1"


def test_runtime_thesis_evidence_dedups_a_completed_refresh(store):
    seed({"FG": "WATCH"})
    DD.record(ticker="FG", cik=CIK["FG"], thesis_version="T2:x", queue_id="REFRESH_DUE:FG:1",
              package=FakePackage(CIK["FG"], [FG_8K]).body, submissions_visible_through="2026-10-13",
              package_sha256="p")
    assert DD.classify("FG", {"accession": FG_8K})["rule"] == "CHUNKED_IN_THESIS_PACKAGE"
    assert DD.classify("FG", {"accession": "OTHER", "filingDate": "2026-10-10"})["rule"] == \
        "FILED_BEFORE_THESIS_SUBMISSIONS_DATE"
    assert DD.classify("FG", {"accession": "LATER", "filingDate": "2026-10-20"})["state"] == DD.NEW_EVIDENCE
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    plan = RQ.build(caches=CACHES, index=INDEX)
    assert plan["queued"] == [] and plan["rows"][0]["research_status"] == RQ.EVIDENCE_ALREADY_IN_THESIS


def test_queue_sync_is_idempotent_and_never_reopens_terminal(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    plan = production_like_queue()
    first = sync(plan)
    again = sync(plan)
    assert len(first["created"]) == 3 and again["created"] == [] and again["updated"] == []
    item = Q.fold()["REFRESH_DUE:VRRM:1"]
    Q.transition(item, "FINISHED", Q.FAILED, status_reason="D4_SCHEMA_VALIDATION_FAILED")
    assert sync(plan)["created"] == []                      # same evidence: not retried for a result
    with pytest.raises(Q.QueueIntegrityError):
        Q.transition(Q.fold()["REFRESH_DUE:VRRM:1"], "X", Q.QUEUED)
    refresh_queue({"VRRM": [{"form": "8-K", "filingDate": "2026-10-20", "accession": "NEW-ACC"}]})
    newer = RQ.build(caches=caches({"VRRM": {"NEW-ACC": "5.02"}}), index=INDEX)
    assert sync(newer)["created"] == ["REFRESH_DUE:VRRM:2"]


def test_new_candidate_rows_are_queued_after_refreshes(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "2026-10-13T06:00:00+00:00", "candidates": [
        {"ticker": "NEWB", "cik": "0000000888", "e3_priority": "P2_MEDIUM", "intake_order": 2},
        {"ticker": "NEWCO", "cik": CIK["NEWCO"], "e3_priority": "P1_HIGH", "intake_order": 1},
        {"ticker": "FG", "cik": CIK["FG"], "e3_priority": "P1_HIGH", "intake_order": 3}]})
    plan = production_like_queue()
    order = [(r["ticker"], r["priority_class"]) for r in plan["rows"] if r["research_status"] == RQ.QUEUED]
    assert order == [("FG", "P0"), ("VRRM", "P1"), ("NEWCO", "P2"), ("NEWB", "P3")]


# == budget ========================================================================================

def test_no_authorization_means_no_model_call(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    sync(production_like_queue())
    calls = []
    out = ORC.research_step(now=RUN_AT, deps=deps(call=lambda s, u: calls.append(1) or dict(OK_MODEL)))
    assert out["state"] == BU.NOT_AUTHORIZED and calls == []
    assert Q.counts()[Q.QUEUED] == 2 and BU.ledger() == []


def test_daily_hard_cap_and_max_per_day_defer_never_drop(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "x", "candidates": [
        {"ticker": "NEWCO", "cik": CIK["NEWCO"], "e3_priority": "P1_HIGH", "intake_order": 1}]})
    sync(production_like_queue())
    authorize(cap=100.0)
    out = ORC.research_step(now=RUN_AT, deps=deps(gap="NEUTRAL"))
    status = {r["queue_id"]: r["status"] for r in out["results"]}
    assert status["REFRESH_DUE:FG:1"] == Q.COMPLETE and status["REFRESH_DUE:VRRM:1"] == Q.COMPLETE
    assert status["NEW_CANDIDATE:NEWCO:1"] == Q.BUDGET_DEFERRED
    assert Q.fold()["NEW_CANDIDATE:NEWCO:1"]["status_reason"] == "MAX_RESEARCH_CANDIDATES_PER_DAY"
    summary = BU.summary(BU.et_day(RUN_AT))
    assert summary["started_today"] == 2 and summary["committed_today_usd"] <= 24.0


def test_operator_cumulative_cap_binds_before_the_reservation_would_exceed_it(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    sync(production_like_queue())
    authorize(cap=12.0)
    out = ORC.research_step(now=RUN_AT, deps=deps(gap="NEUTRAL"))
    assert [r["status"] for r in out["results"]] == [Q.COMPLETE, Q.BUDGET_DEFERRED]
    assert out["results"][1]["why"] == "OPERATOR_CUMULATIVE_CAP"


def test_unsettled_reservation_keeps_counting(store):
    authorize()
    day = BU.et_day(RUN_AT)
    BU.reserve(queue_id="Q1", day=day, run_id="R")
    BU.reserve(queue_id="Q2", day=day, run_id="R")
    assert BU.check(day=day, reason="REFRESH_DUE")["admitted"] is False


def test_metered_model_refuses_wrong_canonical_model_and_never_falls_back():
    wrong = dict(OK_MODEL, modelUsage={"claude-opus-5-5": {"canonicalModel": "claude-sonnet-5-5"}})
    model = PV.MeteredModel(call=lambda s, u: wrong, ceiling_usd=12.0)
    with pytest.raises(PV.ModelContractViolation):
        model("s", "u")
    assert model.calls == 1 and model.spent_usd == pytest.approx(1.3)
    absent = PV.MeteredModel(call=lambda s, u: {"total_cost_usd": 0.5, "is_error": False}, ceiling_usd=12.0)
    with pytest.raises(PV.ModelContractViolation):
        absent("s", "u")


def test_metered_model_charges_a_dead_call_its_cap_and_stops_at_the_reservation():
    model = PV.MeteredModel(call=lambda s, u: (_ for _ in ()).throw(TimeoutError()), ceiling_usd=12.0)
    with pytest.raises(PV.ModelProcessError):
        model("s", "u")
    assert model.spent_usd == 2.0
    capped = PV.MeteredModel(call=lambda s, u: dict(OK_MODEL, total_cost_usd=2.0), ceiling_usd=4.0)
    capped("s", "u"), capped("s", "u")
    with pytest.raises(PV.BudgetCeilingReached):
        capped("s", "u")


def test_canonical_mismatch_fails_the_item_and_writes_no_ledger_row(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    sync(production_like_queue())
    authorize()
    before = len(ST.ledger())
    wrong = lambda s, u: dict(OK_MODEL, modelUsage={"claude-opus-5-5": {"canonicalModel": "other"}})
    out = ORC.research_step(now=RUN_AT, deps=deps(call=wrong))
    assert {r["status"] for r in out["results"]} == {Q.FAILED}
    assert Q.fold()["REFRESH_DUE:FG:1"]["status_reason"] == "MODEL_CONTRACT_VIOLATION"
    assert len(ST.ledger()) == before


# == retry =========================================================================================

def test_infra_retry_is_capped_and_waits_for_the_next_run(store):
    seed({"FG": "WATCH"})
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize(cap=100.0)
    dead = lambda s, u: (_ for _ in ()).throw(RuntimeError("cli died"))
    ORC.research_step(now=RUN_AT, deps=deps(call=dead))
    item = Q.fold()["REFRESH_DUE:FG:1"]
    assert item["status"] == Q.QUEUED and item["attempt_count"] == 1
    assert item["eligible_after"] > RUN_AT.isoformat()
    assert ORC.research_step(now=RUN_AT, deps=deps(call=dead))["results"] == []      # same run day: waits
    later = datetime(2026, 10, 14, 21, 30, tzinfo=timezone.utc)
    ORC.research_step(now=later, deps=deps(call=dead, now=later))
    item = Q.fold()["REFRESH_DUE:FG:1"]
    assert item["status"] == Q.FAILED and item["status_reason"].startswith("QUEUE_ATTEMPTS_EXHAUSTED")
    assert item["attempt_count"] == AC.max_queue_attempts()


def test_schema_failure_is_terminal_without_queue_retry(store):
    seed({"FG": "WATCH"})
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize()
    ORC.research_step(now=RUN_AT, deps=deps(d3=make_d3(status="SCHEMA_VALIDATION_FAILED")))
    item = Q.fold()["REFRESH_DUE:FG:1"]
    assert item["status"] == Q.FAILED and item["status_reason"] == "D3_SCHEMA_VALIDATION_FAILED"
    assert ST.latest_per_ticker()["FG"]["decision"] == "WATCH"


def test_interrupted_running_item_is_recovered(store):
    seed({"FG": "WATCH"})
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    Q.transition(Q.fold()["REFRESH_DUE:FG:1"], "STARTED", Q.RUNNING, attempt_count=1)
    assert Q.recover_interrupted() == ["REFRESH_DUE:FG:1"]
    assert Q.fold()["REFRESH_DUE:FG:1"]["status"] == Q.QUEUED


def test_new_candidate_without_method_judgement_is_data_not_ready_without_a_call(store):
    seed({"FG": "WATCH"})
    SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "x", "candidates": [
        {"ticker": "NOJUDGE", "cik": "0000000999", "e3_priority": "P1_HIGH", "intake_order": 1}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize()
    calls = []
    ORC.research_step(now=RUN_AT, deps=deps(call=lambda s, u: calls.append(1) or dict(OK_MODEL)))
    item = Q.fold()["NEW_CANDIDATE:NOJUDGE:1"]
    assert item["status"] == Q.DATA_NOT_READY and item["status_reason"] == "D5_METHOD_JUDGEMENT_REQUIRED"
    assert calls == [] and BU.ledger() == []


# == freshness =====================================================================================

def test_stale_universe_fails_closed_and_leaves_intake_untouched(store, tmp_path):
    ref = tmp_path / "ref"
    ref.mkdir()
    import gzip
    (ref / "CS_2024-10-25.json.gz").write_bytes(gzip.compress(json.dumps({"results": [
        {"ticker": "A", "type": "CS", "active": True, "cik": "1"}]}).encode()))
    SR.atomic_write_json(ST.path(INT.NEW_CANDIDATES), {"candidates": [], "marker": "before"})
    gate = INT.freshness(RUN_AT, reference_dirs=[ref], grouped=tmp_path / "none", mirror=tmp_path / "none",
                         facts_store=tmp_path / "none")
    assert gate["state"] == INT.DATA_NOT_READY
    assert any(r.startswith("REFERENCE_SNAPSHOT_STALE") for r in gate["reasons"])
    assert "COMPANYFACTS_STORE_MISSING" in gate["reasons"]
    assert any(r.startswith("PRICE_HISTORY_INSUFFICIENT") for r in gate["reasons"])
    assert SR.read_json(ST.path(INT.NEW_CANDIDATES))["marker"] == "before"


def test_intake_writes_ordered_candidates_up_to_the_open_item_cap(store):
    from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow
    seed({"FG": "WATCH"})
    now = RUN_AT
    rows = [UniverseRow(ticker=t, cik=c, security_id=None, exchange="XNYS", exchange_supported=True,
                        security_type_status=SecurityTypeStatus.COMMON_STOCK, market="stocks", locale="us",
                        snapshot_date="2026-10-12", known_at=now) for t, c in (("FG", CIK["FG"]), ("ZZZ", "5"))]
    candidates = [{"ticker": "B", "e3_priority": "P2_MEDIUM", "days_since_latest_filing": 1},
                  {"ticker": "A", "e3_priority": "P1_HIGH", "days_since_latest_filing": 30}]
    body = INT.write_candidates(sorted(candidates, key=lambda c: (c["e3_priority"] != "P1_HIGH", c["ticker"])),
                                open_new_items=9, now=now, run_id="R")
    assert [c["ticker"] for c in body["candidates"]] == ["A"] and body["held_back_by_open_item_cap"] == 1
    assert rows[0].ticker == "FG"


# == end to end ====================================================================================

def test_refresh_watch_to_approve_then_paper_entry_tp1_tp2(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    HP.launch(at=PAPER_AT)
    sync(production_like_queue())
    authorize()
    out = ORC.research_step(now=RUN_AT, deps=deps(gap="POSITIVE"))
    fg = next(r for r in out["results"] if r["queue_id"] == "REFRESH_DUE:FG:1")
    assert fg["status"] == Q.COMPLETE and fg["decision_transition"] == "WATCH->APPROVE" and fg["ledger_written"]
    row = ST.latest_per_ticker()["FG"]
    assert row["cause"] == TR.MATERIAL_REFRESH and row["d5_contract"] == D5 and row["thesis_version"].startswith("T2:")
    item = Q.fold()["REFRESH_DUE:FG:1"]
    assert item["d5_version"] == "D5_D2R_V1" and item["final_decision"] == "APPROVE" and item["cost_usd"] > 0
    # the completed evidence is now in the thesis: tomorrow's plan does not queue FG again
    assert "FG" not in production_like_queue()["queued"]
    for s, bars in (("2026-10-13", (10, 10, 10, 10)), ("2026-10-14", (10, 10.5, 9.9, 10.2)),
                    ("2026-10-15", (10.5, 12.5, 10.4, 12.2)), ("2026-10-16", (12.5, 15.5, 12.4, 15.2))):
        store_bars(s, {"FG": bars, "VRRM": (3.5, 3.5, 3.5, 3.5), "SPY": (700, 700, 700, 700)})
    run = HP.run()
    assert run["paper_run"] == "OK"
    events = [e["event"] for e in HP.events() if e.get("ticker") == "FG"]
    assert events.index("PENDING_ENTRY") < events.index("BUY")
    buy = next(e for e in HP.events() if e["event"] == "BUY" and e["ticker"] == "FG")
    assert buy["entry_session"] == "2026-10-14"                 # next regular-session open after the decision
    assert any(e.startswith("SELL") and "TP1" in e for e in events) or "SELL_TP1" in {
        e.get("trade_action") for e in HP.events() if e.get("ticker") == "FG"}
    assert HP.account()["open_positions"] == 0                 # TP2 closed the remainder


def test_refresh_watch_to_reject_takes_no_paper_action(store):
    seed({"FG": "WATCH"})
    HP.launch(at=PAPER_AT)
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize()
    out = ORC.research_step(now=RUN_AT, deps=deps(gap="NEGATIVE"))
    assert out["results"][0]["decision_transition"] == "WATCH->REJECT"
    store_bars("2026-10-14", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert not [e for e in HP.events() if e.get("ticker") == "FG" and e["event"] in ("BUY", "PENDING_ENTRY")]


def test_watch_unchanged_is_recorded_and_never_enters(store):
    seed({"FG": "WATCH"})
    HP.launch(at=PAPER_AT)
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize()
    out = ORC.research_step(now=RUN_AT, deps=deps(gap="NEUTRAL"))
    assert out["results"][0]["decision_transition"] == "WATCH->WATCH"
    store_bars("2026-10-14", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert not [e for e in HP.events() if e.get("ticker") == "FG" and e["event"] == "BUY"]


def test_approve_downgrade_closes_the_position(store):
    seed({"FG": "WATCH"})
    HP.launch(at=PAPER_AT)
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": FG_8K}]})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize(cap=100.0)
    ORC.research_step(now=RUN_AT, deps=deps(gap="POSITIVE"))
    store_bars("2026-10-13", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    store_bars("2026-10-14", {"FG": (10, 10.5, 9.9, 10.2), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert HP.account()["open_positions"] == 1
    later = datetime(2026, 10, 14, 21, 30, tzinfo=timezone.utc)
    refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-14", "accession": "FG-NEXT"}]})
    sync(RQ.build(caches=caches({"FG": {"FG-NEXT": "2.02"}}), index=INDEX))
    out = ORC.research_step(now=later, deps=deps(gap="NEUTRAL", now=later))
    assert out["results"][0]["decision_transition"] == "APPROVE->WATCH"
    store_bars("2026-10-15", {"FG": (10.1, 10.3, 10.0, 10.1), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert HP.account()["open_positions"] == 0
    assert any(e["event"] == "EXIT_PENDING" and e["exit_reason"] == "DECISION_DOWNGRADE_WATCH" for e in HP.events())


def test_new_candidate_admission_approve_enters_and_exits(store):
    seed({"FG": "WATCH"})
    HP.launch(at=PAPER_AT)
    SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "x", "candidates": [
        {"ticker": "NEWCO", "cik": CIK["NEWCO"], "e3_priority": "P1_HIGH", "intake_order": 1}]})
    refresh_queue({})
    sync(RQ.build(caches=CACHES, index=INDEX))
    authorize()
    out = ORC.research_step(now=RUN_AT, deps=deps(gap="POSITIVE"))
    assert out["results"][0]["decision_transition"] == "NONE->APPROVE"
    admitted = ST.latest_per_ticker()["NEWCO"]
    assert admitted["record"] == "ADMISSION" and admitted["cohort_tag"] == FC.NEW_CANDIDATE
    with pytest.raises(TR.TransitionRefused):
        TR.admit("NEWCO", cik=CIK["NEWCO"], decision="APPROVE", thesis_version="T9", decision_time=RUN_AT.isoformat(),
                 effective_session="2026-10-13", d5_contract=D5, valuation={}, checksums={}, evidence={})
    for s, bars in (("2026-10-13", (10, 10, 10, 10)), ("2026-10-14", (10, 10.4, 9.9, 10.2)),
                    ("2026-10-15", (10.5, 12.5, 10.4, 12.2)),
                    ("2026-10-16", (12.5, 15.5, 12.4, 15.2))):
        store_bars(s, {"NEWCO": bars, "FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    kinds = [e["event"] for e in HP.events() if e.get("ticker") == "NEWCO"]
    assert "PENDING_ENTRY" in kinds and "BUY" in kinds
    assert HP.account()["open_positions"] == 0 and HP.account()["realized_pnl"] not in (None, "0")
    # admitted issuers join the SEC refresh cohort
    from app.dev.run_h_v2_d7 import refresh_cohort
    assert "NEWCO" in {r["ticker"] for r in refresh_cohort()}


# == daily job: order, failure domains, isolation ==================================================

@pytest.fixture
def daily(store, monkeypatch):
    from app.dev import run_h_v2_d7 as D7
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    HP.launch(at=PAPER_AT)
    production_like_queue()
    monkeypatch.setattr(D7, "collect_prices", lambda **_: {"latest_session": PR.latest_session()})
    monkeypatch.setattr(D7, "_material_refresh", lambda: {"refresh_run": "OK", "research_refresh_due": ["FG"],
                                                          "cache_as_of": "2026-10-13"})
    monkeypatch.setattr(INT, "freshness", lambda now=None, **_: {"state": INT.DATA_NOT_READY,
                                                                  "reasons": ["REFERENCE_SNAPSHOT_STALE"], "checks": {}})
    store_bars("2026-10-13", {"FG": (10, 10, 10, 10), "VRRM": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    return D7


@pytest.fixture
def planner(monkeypatch):
    original = RQ.build
    monkeypatch.setattr(RQ, "build", lambda **_: original(caches=CACHES, index=INDEX))


def test_daily_order_and_ai_failure_isolation(daily, planner, monkeypatch):
    D7 = daily
    body = D7.update()
    assert list(body["steps"]) == ["prices", "sec_refresh", "intake", "queue", "paper"]
    assert body["research"]["state"] == "SEPARATE_UNIT"
    assert body["integrity"] == "PASS" and body["paper"]["paper_run"] == "OK" and body["autonomy_hard_failure"] is None
    assert body["intake"]["new_candidate_scan"] == INT.DATA_NOT_READY
    assert D7.main(["update"]) == 0

    def boom(**_):
        raise RuntimeError("model provider down")
    monkeypatch.setattr(ORC, "research_step", boom)
    research = D7.auto_research()
    assert list(research["steps"]) == ["queue", "research", "paper"]
    assert research["research"]["state"] == "ERROR" and research["paper"]["paper_run"] == "OK"
    assert D7.main(["auto-research"]) == 0
    status = AS.read_json(AS.RUN_STATUS)
    assert status["last_auto_run_finished_at"] and status["last_research_run_at"]
    assert status["last_research_unit_finished_at"]


def test_ai_failure_does_not_stop_an_existing_position_exit(daily, planner, monkeypatch):
    D7 = daily
    ST.append_ledger([{"strategy_id": H, "ticker": "VRRM", "thesis_version": "T2:V", "decision_time": "2026-10-13T20:30:00+00:00",
                       "record": "REEVALUATION", "decision": "APPROVE", "previous_decision": "WATCH",
                       "cause": TR.MATERIAL_REFRESH, "tp1": 12.0, "tp2": 15.0, "bear": None,
                       "bear_refusal": "NEGATIVE_IMPLIED_EQUITY", "d5_contract": D5},
                      {"strategy_id": H, "ticker": "VRRM", "thesis_version": "T3:V", "decision_time": "2026-10-14T20:30:00+00:00",
                       "record": "REEVALUATION", "decision": "WATCH", "previous_decision": "APPROVE",
                       "cause": TR.MATERIAL_REFRESH, "tp1": 12.0, "tp2": 15.0, "bear": None, "d5_contract": D5}])
    for s in ("2026-10-14", "2026-10-15"):
        store_bars(s, {"VRRM": (10, 10.2, 9.9, 10), "FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    monkeypatch.setattr(ORC, "research_step", lambda **_: (_ for _ in ()).throw(RuntimeError("down")))
    body = D7.auto_research()
    assert body["research"]["state"] == "ERROR"
    assert HP.account()["open_positions"] == 0
    assert any(e["event"] == "EXIT_PENDING" and e["ticker"] == "VRRM" for e in HP.events())


def test_queue_corruption_is_a_hard_failure_but_paper_still_runs(daily, planner):
    D7 = daily
    AS.append_jsonl(AS.QUEUE_EVENTS, [{"queue_id": "X:1", "event": "UPDATED", "status": "QUEUED", "at": "t"}])
    body = D7.update()
    assert body["autonomy_hard_failure"].startswith("QUEUE_INTEGRITY")
    assert body["paper"]["paper_run"] == "OK"
    assert D7.main(["update"]) == D7.EXIT_AUTONOMY_INTEGRITY_FAILURE
    assert D7.main(["auto-research"]) == D7.EXIT_AUTONOMY_INTEGRITY_FAILURE


def test_autonomous_files_stay_inside_h_store(store):
    seed({"FG": "WATCH", "VRRM": "WATCH", "AEYE": "REJECT"})
    sync(production_like_queue())
    authorize()
    ORC.research_step(now=RUN_AT, deps=deps(gap="NEUTRAL"))
    assert AS.root().is_relative_to(ST.root())
    written = {p.relative_to(store).parts[0] for p in store.rglob("*") if p.is_file()}
    assert written <= {"auto", "launch_snapshot.jsonl", "forward_ledger.jsonl", "refresh_queue.json"}


def test_a_and_e_modules_are_not_imported_by_the_autonomous_package():
    import ast
    root = Path(AS.__file__).parent
    imported = set()
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not [m for m in imported if m.startswith(("app.strategies.official", "app.strategies.paper_gate",
                                                     "app.strategy_e", "app.strategy.", "app.services"))]


# == Massive future-date noise =====================================================================

def test_price_requests_stop_at_the_last_publishable_session(store, monkeypatch):
    from app.dev import run_h_v2_d7 as D7
    monday_0110 = datetime(2026, 10, 12, 5, 10, tzinfo=timezone.utc)     # 01:10 ET Monday
    tuesday_0110 = datetime(2026, 10, 13, 5, 10, tzinfo=timezone.utc)
    assert D7.publishable_session(monday_0110) == "2026-10-09"
    assert D7.publishable_session(tuesday_0110) == "2026-10-12"
    seed({"FG": "WATCH"})
    asked = []

    class Client:
        def grouped_daily(self, day):
            asked.append(day.isoformat())
            return {"results": [{"T": "FG", "o": 1, "c": 1, "h": 1, "l": 1, "v": 1}]}

    import app.integrations.massive.client as MC
    monkeypatch.setattr(MC, "build_massive_client", lambda _s: Client())
    monkeypatch.setattr(D7, "publishable_session", lambda now=None: "2026-10-12")
    report = D7.collect_prices(start="2026-10-08")
    assert asked == ["2026-10-09", "2026-10-12"] and report["request_upper_bound"] == "2026-10-12"
