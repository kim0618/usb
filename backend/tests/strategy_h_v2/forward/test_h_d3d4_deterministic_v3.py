"""H_D3D4_DETERMINISTIC_PRODUCTION_V3: structured guidance, priced-in, catalyst and invalidation states,
the D4 gap table and the D6 APPROVE path. Fixtures are synthetic (or the stored FG package with synthetic
states); expected outcomes are the ones the frozen contract declared before any replay
(``fixtures_declared_before_running``)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.backtest.strategy_h_v2.decision.d6_contract import decide
from app.dev.run_strategy_h_v2_d6 import project_d3, project_d4, project_d5
from app.strategies.h_auto import deterministic_v3 as V3
from app.strategies.h_auto import guidance_parser_v2 as G
from app.strategies.h_auto import no_ai_guard as NG
from app.strategies.h_auto import orchestrator as ORC
from app.strategies.h_auto import queue as Q
from app.strategies.h_forward import research_queue as RQ
from app.strategies.h_paper import service as HP
from tests.strategy_h_v2.forward import test_h_autonomous_pipeline as T
from tests.strategy_h_v2.forward import test_h_no_ai_runtime as N

store = T.store
UTC = timezone.utc
needs_fg = pytest.mark.skipif(not N.FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")
MAY, AUG = datetime(2026, 5, 7, 20, tzinfo=UTC), datetime(2026, 8, 5, 20, tzinfo=UTC)


def _pkg(docs, cutoff=datetime(2026, 10, 1, tzinfo=UTC)):
    chunks, manifest = [], []
    for sid, published, form, text in docs:
        chunks.append(SimpleNamespace(evidence_id=f"{sid}:CHUNK:0", source_id=sid, chunk_index=0, section=None,
                                      text=text, published_at=published, available_at=published, pit_eligible=True))
        manifest.append(SimpleNamespace(source_id=sid, accession=sid.split(":")[2], title=f"{form} - accession x"))
    return SimpleNamespace(chunks=chunks, source_manifest=manifest, evidence_bundle=SimpleNamespace(data_cutoff=cutoff))


def _rows(text, fye="1231"):
    return G.guidance_evidence(_pkg([("SEC:1:A-1", MAY, "8-K", text)]), fiscal_year_end=fye)["transcriptions"]


# == guidance parser V2 ==============================================================================

def test_sentence_period_after_range_and_coordination():
    rows = _rows("Financial Outlook\nAudioEye expects revenue of between $10.85M and $11.05M for the third quarter "
                 "of 2026 and between $43.5M and $44.0M for the full year 2026.")
    assert [(r["metric_key"], r["period"], r["low"]["raw"], r["high"]["raw"]) for r in rows] == [
        ("revenue", "Q3-2026", "$10.85M", "$11.05M"), ("revenue", "FY2026", "$43.5M", "$44.0M")]


def test_bullets_inherit_the_heading_period():
    rows = _rows("2027 Outlook\n•\nTotal Revenue of $945 million to $965 million\n"
                 "• Adj. EBITDA of $360 million to $370 million\n| ● | Adjusted EPS of $1.11 to $1.17 |")
    assert [(r["template"], r["metric_key"], r["period"], r["unit"]) for r in rows] == [
        ("S2_bullet", "total revenue", "FY2027", "USD"), ("S2_bullet", "adjusted ebitda", "FY2027", "USD"),
        ("S2_bullet", "adjusted eps", "FY2027", "USD_PER_SHARE")]


def test_table_columns_low_high_and_ambiguity():
    cols = _rows("| | Third Quarter 2026 | Full Year 2026 |\n| Financial Outlook |\n"
                 "| Revenue | $10.85M – $11.05M | $43.5M – $44.0M |")
    assert [(r["period"], r["low"]["raw"]) for r in cols] == [("Q3-2026", "$10.85M"), ("FY2026", "$43.5M")]
    lowhigh = _rows("| 2026 Guidance | Low | High |\n| Revenue ($ millions) | $945 | $965 |")
    assert [(r["period"], r["kind"], r["low"]["raw_value"], r["high"]["raw_value"]) for r in lowhigh] == [
        ("FY2026", "RANGE", 945.0, 965.0)]
    ambiguous = _rows("Outlook\n| Revenue | $10 – $11 | $40 – $44 |")
    assert {r["period"] for r in ambiguous} == {G.PERIOD_UNKNOWN} and all("AMBIGUOUS_COLUMN" in r["notes"] for r in ambiguous)


def test_heading_label_value_and_subheading_period():
    rows = _rows("FY2027 Outlook\nRevenue\n$1.4 billion - $1.5 billion\nAdjusted EBITDA\n$300 million - $320 million")
    assert [(r["metric_key"], r["period"], r["low"]["normalized"], r["high"]["normalized"]) for r in rows] == [
        ("revenue", "FY2027", 1.4e9, 1.5e9), ("adjusted ebitda", "FY2027", 3.0e8, 3.2e8)]
    sub = _rows("Guidance\nFiscal 2027\n• Revenue of $1.4 billion to $1.5 billion")
    assert [r["period"] for r in sub] == ["FY2027"]
    two = _rows("Third Quarter and Full Year 2026 Outlook\n• Revenue of $1.4 billion to $1.5 billion")
    assert [r["period"] for r in two] == [G.PERIOD_UNKNOWN]


def test_scope_never_reaches_outside_its_section():
    rows = _rows("2026 Outlook\n• Revenue of $1.4 billion to $1.5 billion\nThe board declared a dividend.\n"
                 "• Adjusted EBITDA of $300 million to $320 million\n| Revenue | $1,200 | $1,100 |")
    assert [r["metric_key"] for r in rows] == ["revenue"]


def test_bare_year_is_the_fiscal_year_only_for_a_december_year_end():
    text = "2026 Outlook\n• Revenue of $1.4 billion to $1.5 billion"
    assert _rows(text, "1231")[0]["period"] == "FY2026" and _rows(text, "0630")[0]["period"] == "YEAR2026"


def test_structured_pairs_give_code_owned_directions_and_conflicts():
    pkg = _pkg([("SEC:1:A-1", MAY, "8-K", "2026 Outlook\n• Revenue of $1.40 billion to $1.50 billion\n"
                                       "• Adjusted EBITDA of $300 million to $320 million"),
                ("SEC:1:A-2", AUG, "8-K", "2026 Outlook\n• Revenue of $1.45 billion to $1.55 billion\n"
                                       "• Adj. EBITDA of $280 million to $300 million")])
    ev = G.guidance_evidence(pkg, fiscal_year_end="1231")
    assert {a["metric_key"]: a["state"] for a in ev["assessments"]} == {"revenue": "RAISED", "adjusted ebitda": "LOWERED"}
    assert ev["overall_state"] == "MIXED" and ev["paired"] == 2 and ev["non_price_items"] == 2
    conflict = _pkg([("SEC:1:A-2", AUG, "8-K", "2026 Outlook\n• Revenue of $1.45 billion to $1.55 billion"),
                     ("SEC:1:A-2:EX-99.1", AUG, "8-K", "2026 Outlook\n• Revenue of $1.5 billion to $1.6 billion")])
    assert G.guidance_evidence(conflict, fiscal_year_end="1231")["conflicts"] == 1


def test_raw_numbers_are_kept():
    for r in _rows("2026 Outlook\n• Revenue of $1,400 million to $1.5 billion"):
        assert r["low"]["raw"] == "$1,400 million" and r["low"]["raw_value"] == 1400.0
        assert r["high"]["raw"] == "$1.5 billion" and r["high"]["normalized"] == 1.5e9


# == priced-in / catalyst / invalidation =============================================================

def _row(up1, up2, confidence="MEDIUM", status="VALUED"):
    row = T.valuation_row("FG")
    return dict(row, status=status, confidence={"confidence": confidence, "drivers": []},
                target_prices=dict(row["target_prices"], upside_to_TP1=up1, upside_to_TP2=up2))


@pytest.mark.parametrize("up1,up2,state", [(0.2, 0.5, "LIKELY_NOT_PRICED"), (0.0, 0.3, "PARTIALLY_PRICED"),
                                           (-0.1, 0.0, "LIKELY_PRICED"), (-0.2, -0.1, "LIKELY_PRICED")])
def test_priced_in_reads_the_two_d6_boundaries(up1, up2, state):
    out = V3.priced_in(_row(up1, up2))
    assert out["state"] == state and out["confidence"] == "MEDIUM" and out["evidence_ids"] and out["limitations"]


def test_priced_in_unknown_without_a_valued_row():
    assert V3.priced_in(None)["state"] == "UNKNOWN"
    assert V3.priced_in(_row(0.2, 0.5, status="REFUSED"))["state"] == "UNKNOWN"
    assert V3.priced_in(_row(None, 0.5))["state"] == "UNKNOWN"


def test_catalyst_state_from_the_statutory_next_report():
    derived = {"state": "DERIVED", "form": "10-Q", "deadline": "2026-11-09", "within_horizon": True}
    assert V3.catalyst_state(derived)["state"] == "MATERIAL"
    assert V3.catalyst_state(derived | {"within_horizon": False})["state"] == "UNKNOWN"
    assert V3.catalyst_state({"state": "UNDERIVABLE"})["state"] == "UNKNOWN"


def test_invalidation_active_and_triggered_with_reason_and_evidence():
    states = {"revenue": {"state": "IMPROVING", "source_id": "SEC:1:A"}, "eps_diluted": {"state": "STABLE", "source_id": "SEC:1:A"}}
    guidance = {"assessments": [{"metric_key": "revenue", "period": "FY2026", "unit": "USD", "state": "RAISED",
                                 "current_evidence_ids": ["SEC:1:B:CHUNK:0"]}]}
    first = V3.invalidation(states, guidance, None)
    assert first["state"] == "ACTIVE" and [c["id"] for c in first["conditions"]] == [
        "INV:FUNDAMENTAL:revenue", "INV:GUIDANCE:revenue|FY2026|USD"]
    later = V3.invalidation({"revenue": {"state": "DETERIORATING", "source_id": "SEC:1:C"}},
                            {"assessments": [{"metric_key": "revenue", "period": "FY2026", "unit": "USD", "state": "LOWERED",
                                              "current_evidence_ids": ["SEC:1:D:CHUNK:0"]}]}, {"conditions": first["conditions"]})
    assert later["state"] == "TRIGGERED"
    assert [(t["reason_code"], t["evidence_id"]) for t in later["triggered"]] == [
        ("FUNDAMENTAL_REVERSED:revenue:DETERIORATING", "SEC:1:C"), ("GUIDANCE_LOWERED:revenue|FY2026|USD", "SEC:1:D:CHUNK:0")]
    assert V3.invalidation({}, {"assessments": []}, None)["state"] == "UNKNOWN"


# == D3 / D4 on the stored FG package with synthetic guidance =======================================

IMPROVING = {m: "IMPROVING" for m in ("revenue", "operating_income", "eps_diluted", "free_cash_flow")}


def _with_guidance(states, first, second):
    pkg = N._package(states)
    extra = [pkg.chunks[0].model_copy(update={"evidence_id": f"{sid}:CHUNK:0", "source_id": sid, "section": None,
                                              "chunk_index": 0, "published_at": when, "available_at": when, "text": text})
             for sid, when, text in (("SEC:0001934850:G-1", MAY, first), ("SEC:0001934850:G-2", AUG, second))]
    return pkg.model_copy(update={"chunks": list(pkg.chunks) + extra})


RAISED = ("2026 Outlook\n• Revenue of $1.40 billion to $1.50 billion",
          "2026 Outlook\n• Revenue of $1.50 billion to $1.60 billion")
LOWERED = ("2026 Outlook\n• Revenue of $1.50 billion to $1.60 billion",
           "2026 Outlook\n• Revenue of $1.40 billion to $1.50 billion")


def _d3(pkg, decision=date(2026, 9, 16), previous=None):
    subs = N._submissions() | {"fiscalYearEnd": "1231"}
    return V3.d3_from(pkg, cik="0001934850", decision_date=decision, submissions_doc=subs, run_id="T",
                      previous=previous, now=datetime(2026, 9, 28, 6, tzinfo=UTC))


def _d4(pkg, d3, row):
    return V3.d4_from(N._bundle(), d3, package=pkg, row=row, fiscal_year_end="1231", analysis_id="A")


@needs_fg
def test_fixture_a_positive():
    pkg = _with_guidance(IMPROVING, *RAISED)
    d3 = _d3(pkg)
    out = _d4(pkg, d3, _row(0.2, 0.5))["final_output"]
    assert (out["expectation_gap"], out["expectation_gap_confidence"]) == ("POSITIVE", "MEDIUM")
    assert out["d6_approve_precondition"] == "SATISFIED" and out["priced_in_assessment"]["state"] == "LIKELY_NOT_PRICED"
    assert out["reason_codes"][0] == "P1_GUIDANCE_RAISED" and out["evidence_ids"]


@needs_fg
def test_fixture_b_neutral_when_priced_and_fixture_d_lowered():
    pkg = _with_guidance(IMPROVING, *RAISED)
    out = _d4(pkg, _d3(pkg), _row(-0.2, -0.05))["final_output"]
    assert out["expectation_gap"] == "NEUTRAL" and "N_PRICED_IN_LIKELY_PRICED" in out["reason_codes"]
    low = _with_guidance(IMPROVING, *LOWERED)
    out = _d4(low, _d3(low), _row(0.2, 0.5))["final_output"]
    assert out["expectation_gap"] == "NEUTRAL" and "N_GUIDANCE_LOWERED" in out["reason_codes"]


@needs_fg
def test_fixture_c_unknown_and_low_confidence_positive():
    pkg = N._package(IMPROVING)
    out = _d4(pkg, _d3(pkg), _row(0.2, 0.5))["final_output"]
    assert (out["expectation_gap"], out["expectation_gap_confidence"]) == ("UNKNOWN", "UNKNOWN")
    assert out["reason_codes"][0] == "U2_NO_COMPARABLE_GUIDANCE_PAIR_C1"
    raised = _with_guidance(IMPROVING, *RAISED)
    low = _d4(raised, _d3(raised), _row(0.2, 0.5, confidence="LOW"))["final_output"]
    assert (low["expectation_gap"], low["expectation_gap_confidence"]) == ("POSITIVE", "LOW")
    assert not project_d4("FG", {"final_output": low, "final_output_checksum": "x"}).gap_permits_approve
    unresolved = _d4(raised, _d3(raised), None)["final_output"]
    assert unresolved["expectation_gap"] == "UNKNOWN" and unresolved["reason_codes"][0].startswith("U4_")


@needs_fg
def test_d3_v3_writes_catalyst_invalidation_and_risks_without_ai_text():
    pkg = _with_guidance(IMPROVING, *RAISED)
    d3 = _d3(pkg)
    out = d3["final_output"]
    assert d3["final_status"] == "OK" and d3["catalyst_state"]["state"] == "MATERIAL"
    assert [(c["materiality_candidate"], c["timing_confidence"]) for c in out["catalyst_candidates"]] == [("HIGH", "MEDIUM")]
    assert out["invalidation_candidates"] and all(c["description"].startswith("INV:") for c in out["invalidation_candidates"])
    assert out["risks"] and "invalidation_candidates" not in out["unknown_fields"]
    projected = project_d3("FG", d3)
    assert projected.catalyst_material_and_timed and projected.invalidation_count > 0 and projected.risk_count > 0


@needs_fg
def test_deterministic_pipeline_alone_reaches_d6_approve():
    pkg = _with_guidance(IMPROVING, *RAISED)
    d3 = _d3(pkg)
    row = _row(0.2, 0.5)
    record = decide(project_d3("FG", d3), project_d4("FG", _d4(pkg, d3, row)), project_d5(row))
    assert record.decision.value == "APPROVE" and record.approve_blockers == ()


@needs_fg
def test_d6_approve_reaches_paper_pending_entry(store):
    pkg = _with_guidance(IMPROVING, *RAISED)

    def d3_fn(prep, *, run_id, work):
        return _d3(pkg)

    def d4_fn(prep, d3, *, run_id, work):
        return _d4(pkg, d3, T.valuation_row(prep["identity"].ticker))

    T.seed({"FG": "WATCH"})
    HP.launch(at=T.PAPER_AT)
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    with NG.forbid_ai() as guard:
        out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=d3_fn, d4=d4_fn))
    assert guard.blocked == []
    result = out["results"][0]
    assert result["status"] == Q.COMPLETE and result["decision_transition"] == "WATCH->APPROVE" and result["ai_calls"] == 0
    assert result["generators"]["d4_gap"] == "POSITIVE"
    for session in ("2026-10-13", "2026-10-14"):
        T.store_bars(session, {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert "PENDING_ENTRY" in [e["event"] for e in HP.events() if e.get("ticker") == "FG"]


@needs_fg
def test_previous_thesis_reversal_is_triggered_and_counted_as_risk():
    first = _d3(_with_guidance(IMPROVING, *RAISED))
    worse = _with_guidance({"revenue": "DETERIORATING"}, *LOWERED)
    later = _d3(worse, previous=first["thesis_states"])
    assert later["invalidation"]["state"] == "TRIGGERED"
    assert any(r["description"].startswith("Invalidation triggered:") for r in later["final_output"]["risks"])


def test_v3_contract_is_frozen_with_zero_new_thresholds():
    body = V3.contract()
    assert body["declaration"]["new_numeric_thresholds"] == 0
    assert body["declaration"]["supersedes"]["sha256"] == "65c631cada055594c344270e8b8e4990e0cf31235cedf2240137e0028fc4738a"
    assert body["d4_gap_v3"]["negative"].startswith("not produced: LARGE_RERATING_NEGATIVE DEFERRED")


@needs_fg
def test_v3_generators_make_no_model_call():
    pkg = _with_guidance(IMPROVING, *RAISED)
    with NG.forbid_ai() as guard:
        _d4(pkg, _d3(pkg), _row(0.2, 0.5))
    assert guard.blocked == []


def test_class_d_fix_d2_a_year_is_never_a_guidance_value():
    """WEN 10-Q 2026-05-08 / 2026-08-07 (replay): 'we anticipate continued labor and commodity inflation
    throughout the remainder of 2026' is not a guidance value of 2026."""
    text = ("Inflationary pressures directly impacted our results, and we anticipate continued labor and commodity "
            "inflation throughout the remainder of 2026.")
    assert _rows(text) == []
