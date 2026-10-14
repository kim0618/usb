"""H_D3D4_DETERMINISTIC_PRODUCTION_V2: R3.1 business evidence, guidance transcription, D4 V2, and the
synthetic D6 APPROVE path. Every fixture here is synthetic or a stored D2.1 package; none is fitted to a
historical decision or a forward return. The expected outcomes are the ones the frozen contract
declared before any replay (``expected_effect_on_d6_declared_before_running``)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.backtest.strategy_h_v2.decision.d6_contract import D3Provenance, Eligibility, decide
from app.dev.run_strategy_h_v2_d6 import project_d3, project_d4, project_d5
from app.strategies.h_auto import business_evidence as BE
from app.strategies.h_auto import deterministic_v2 as V2
from app.strategies.h_auto import guidance_parser as GP
from app.strategies.h_auto import no_ai_guard as NG
from app.strategies.h_auto import orchestrator as ORC
from app.strategies.h_auto import queue as Q
from app.strategies.h_forward import research_queue as RQ
from app.strategies.h_paper import service as HP
from tests.strategy_h_v2.forward import test_h_autonomous_pipeline as T
from tests.strategy_h_v2.forward import test_h_no_ai_runtime as N

store = T.store
UTC = timezone.utc
FG_PACKAGE = N.FG_PACKAGE
needs_fg = pytest.mark.skipif(not FG_PACKAGE.exists(), reason="stored D2.1 package not on this host")


# == fakes ===========================================================================================

def _chunk(source_id, index, text, *, section=None, published=datetime(2026, 2, 26, 21, tzinfo=UTC)):
    return SimpleNamespace(evidence_id=f"{source_id}:CHUNK:{index}", source_id=source_id, chunk_index=index,
                           section=section, text=text, published_at=published, available_at=published,
                           pit_eligible=True)


def _tenk_package(texts, *, form="10-K", sections=None):
    sid = "SEC:0000000001:0000000001-26-000001"
    chunks = [_chunk(sid, i, t, section=(sections or {}).get(i)) for i, t in enumerate(texts)]
    source = SimpleNamespace(source_id=sid, accession="0000000001-26-000001",
                             title=f"{form} - accession 0000000001-26-000001",
                             published_at=datetime(2026, 2, 26, 21, tzinfo=UTC))
    return SimpleNamespace(chunks=chunks, source_manifest=[source], materialization=SimpleNamespace(
        documents=[SimpleNamespace(role="LATEST_10K", chunk_count=len(chunks), source_id=sid)]))


ITEM1 = ("PART I\nITEM 1. Business.\nAcme designs and sells widgets to industrial customers.\n"
         "ITEM 1A. Risk Factors\nOur business is subject to risks.\n")
TOC = "Table of Contents\n| Item 1. | Business | 3 |\n| Item 1A. | Risk Factors | 10 |\n"


# == R3.1 V2 =========================================================================================

def test_a_explicit_business_chunk_is_priority_one():
    pkg = _tenk_package(["intro", "Item 1. Business\nAcme makes widgets."], sections={1: "BUSINESS"})
    ev = BE.business_evidence(pkg)
    assert ev["state"] == "FOUND" and ev["source_priority"] == BE.P1
    assert ev["cited_evidence_id"].endswith(":CHUNK:1") and ev["form"] == "10-K"


def test_b_missing_label_with_a_valid_item_1_uses_the_fallback_with_provenance():
    pkg = _tenk_package([TOC, "cover page text", ITEM1])
    ev = BE.business_evidence(pkg)
    assert ev["state"] == "FOUND" and ev["source_priority"] == BE.P2
    assert ev["heading"] == "ITEM 1. Business." and ev["terminator"] == "ITEM 1A. Risk Factors"
    assert ev["cited_evidence_id"].endswith(":CHUNK:2")       # the body, not the table of contents
    text = "\n".join(c.text for c in pkg.chunks)
    span = text[ev["start_offset"]:ev["end_offset"]]
    assert span.startswith("ITEM 1. Business.") and "Risk Factors" not in span
    import hashlib
    assert ev["sha256"] == hashlib.sha256(span.encode()).hexdigest()
    assert {"accession", "form", "filing_date", "source_id", "extraction_method"} <= set(ev)
    assert ev["extraction_method"] == BE.EXTRACTION_METHOD and ev["filing_date"] == "2026-02-26"


@pytest.mark.parametrize("heading", ["ITEM 1", "Item 1.", "ITEM 1. BUSINESS", "Item 1 — Business",
                                     "Item 1: Business", "Item\xa01.\xa0\xa0\xa0BUSINESS.",
                                     "ITEM 1 -DESCRIPTION OF BUSINESS", "Items 1 and 2. Business and Properties"])
def test_b_sec_heading_variants(heading):
    body = f"{heading}\nBusiness\n" if heading in ("ITEM 1", "Item 1.") else f"{heading}\n"
    pkg = _tenk_package([body + "We operate stores.\nItem 1A. Risk Factors\nrisk text"])
    assert BE.business_evidence(pkg)["source_priority"] == BE.P2, heading


def test_c_item_1_absent_is_not_ready():
    ev = BE.business_evidence(_tenk_package(["Management's discussion\nRevenue rose.\n"]))
    assert ev["state"] == BE.NOT_READY and ev["reason"] == "NO_ITEM_1_HEADING"


def test_d_risk_factors_only_is_never_business_support():
    ev = BE.business_evidence(_tenk_package(["ITEM 1A. Risk Factors\nWe face competition.\nITEM 2. Properties\n"]))
    assert ev["state"] == BE.NOT_READY


def test_e_malformed_filing_is_fail_neutral():
    no_end = BE.business_evidence(_tenk_package(["ITEM 1. Business\nAcme makes widgets.\n"]))
    toc_only = BE.business_evidence(_tenk_package(["Item 1. Business\nItem 1A. Risk Factors\n"]))
    assert no_end["reason"] == "ITEM_1_HEADING_WITHOUT_TERMINATOR"
    assert toc_only["reason"] == "ITEM_1_HEADING_WITHOUT_BODY"
    assert BE.business_evidence(SimpleNamespace(chunks=[], source_manifest=[], materialization=SimpleNamespace(
        documents=[])))["reason"] == "NO_LATEST_10K_CHUNKS"


def test_f_amendment_follows_the_d2_selection_and_never_reaches_outside_the_package():
    part3 = BE.business_evidence(_tenk_package(["This Amendment No. 1 amends Part III.\nItem 10. Directors\n"],
                                               form="10-K/A"))
    assert part3["state"] == BE.NOT_READY and part3["reason"] == "AMENDMENT_WITHOUT_ITEM_1"
    full = BE.business_evidence(_tenk_package([ITEM1], form="10-K/A"))
    assert full["source_priority"] == BE.P2 and full["form"] == "10-K/A"


def _strip_business_label(states=None):
    pkg = N._package(states)
    return pkg.model_copy(update={"chunks": [c.model_copy(update={"section": None}) if c.section == "BUSINESS" else c
                                             for c in pkg.chunks]})


def _v2(pkg, *, decision=date(2026, 9, 16), submissions=None):
    return V2.d3_from(pkg, cik="0001934850", decision_date=decision,
                      submissions_doc=N._submissions() if submissions is None else submissions, run_id="T",
                      now=datetime(2026, 9, 28, 6, tzinfo=UTC))


@needs_fg
def test_d3_v2_explicit_chunk_matches_v1_and_the_fallback_recovers_an_unlabelled_10k():
    v1, v2 = N._d3(), _v2(N._package())
    assert v2["final_status"] == "OK" and v2["business_evidence"]["source_priority"] == BE.P1
    strip = lambda o: {k: v for k, v in o.items() if k not in ("research_id", "prompt_version")}   # noqa: E731
    assert strip(v1["final_output"]) == strip(v2["final_output"])
    unlabelled = _v2(_strip_business_label())
    assert unlabelled["final_status"] == "OK" and unlabelled["business_evidence"]["source_priority"] == BE.P2
    driver = unlabelled["final_output"]["business_model"]["revenue_drivers"][0]
    assert driver["evidence_id"] == unlabelled["business_evidence"]["cited_evidence_id"]
    assert "business_model" not in unlabelled["final_output"]["unknown_fields"]
    v1_unlabelled = N.DT.d3_from(_strip_business_label(), cik="0001934850", decision_date=date(2026, 9, 16),
                                 submissions_doc=N._submissions(), run_id="T")
    assert v1_unlabelled["final_output"]["business_model"]["revenue_drivers"] == []       # the V1 defect


@needs_fg
def test_business_not_ready_is_an_abstention_in_d6_never_a_reject():
    pkg = N._package()
    tenk = BE.latest_10k(pkg).source_id
    gone = pkg.model_copy(update={"chunks": [c.model_copy(update={"section": None, "text": "Item 10. Directors"})
                                             if c.source_id == tenk else c for c in pkg.chunks]})
    d3 = _v2(gone)
    assert d3["final_output"] is None and d3["refusal"] == V2.BUSINESS_NOT_READY_REFUSAL
    d4 = V2.d4_from_bundle(N._bundle(), d3, package=gone, analysis_id="A")
    out = decide(project_d3("FG", d3), project_d4("FG", d4), project_d5(T.valuation_row("FG")))
    assert project_d3("FG", d3).provenance is D3Provenance.REFUSED_NO_FINAL_OUTPUT
    # D6 still lists its triggers as diagnostics on an ineligible record, but none of them decides: no REJECT.
    assert out.decision is None and out.eligibility.eligibility is Eligibility.NOT_DECISION_ELIGIBLE


# == guidance transcription ==========================================================================

def _gpkg(docs, cutoff=datetime(2026, 10, 1, tzinfo=UTC)):
    """docs: [(source_id, published datetime, form, text)]"""
    chunks, manifest = [], []
    for sid, published, form, text in docs:
        chunks.append(_chunk(sid, 0, text, published=published))
        manifest.append(SimpleNamespace(source_id=sid, accession=sid.split(":")[2], title=f"{form} - accession x"))
    return SimpleNamespace(chunks=chunks, source_manifest=manifest,
                           evidence_bundle=SimpleNamespace(data_cutoff=cutoff))


MAY, AUG, AUG_LATER = (datetime(2026, 5, 7, 20, tzinfo=UTC), datetime(2026, 8, 5, 20, tzinfo=UTC),
                       datetime(2026, 8, 20, 20, tzinfo=UTC))


def _states(*docs):
    return {(a["metric_key"], a["period"], a["unit"]): (a["state"], a["reasons"])
            for a in GP.guidance_evidence(_gpkg(docs))["assessments"]}


def test_range_vs_range_and_single_vs_single_directions_are_code_arithmetic():
    out = _states(("SEC:1:A-1", MAY, "8-K", "The Company expects fiscal 2026 revenue of $1.40 billion to $1.50 billion."),
                  ("SEC:1:A-2", AUG, "8-K", "The Company now expects fiscal 2026 revenue of $1.45 billion to $1.55 billion. "
                                            "For fiscal 2026, the Company expects adjusted EBITDA to be approximately $200 million."),
                  ("SEC:1:A-0", datetime(2026, 2, 1, tzinfo=UTC), "8-K",
                   "For fiscal 2026, the Company expects adjusted EBITDA to be approximately $210 million."))
    assert out[("revenue", "FY2026", "USD")][0] == "RAISED"
    assert out[("adjusted ebitda", "FY2026", "USD")][0] == "LOWERED"


def test_million_billion_normalisation_keeps_raw_values():
    ev = GP.guidance_evidence(_gpkg([
        ("SEC:1:A-1", MAY, "8-K", "We expect fiscal 2026 revenue of $1.4 billion to $1.5 billion."),
        ("SEC:1:A-2", AUG, "8-K", "We expect fiscal 2026 revenue of $1,400 million to $1,500 million.")]))
    assert ev["assessments"][0]["state"] == "MAINTAINED"
    rows = ev["transcriptions"]
    assert [(r["low"]["raw"], r["low"]["raw_value"], r["low"]["scale_word"]) for r in rows] == [
        ("$1.4 billion", 1.4, "billion"), ("$1,400 million", 1400.0, "million")]
    assert {r["low"]["normalized"] for r in rows} == {1.4e9}


def test_percent_and_bps_are_separate_families():
    out = _states(("SEC:1:A-1", MAY, "8-K", "We now expect FY2026 net sales growth of 7% to 9%. "
                                            "We expect operating margin expansion of 50 to 100 basis points in fiscal 2026."),
                  ("SEC:1:A-2", AUG, "8-K", "We now expect FY2026 net sales growth of 3% to 5%. "
                                            "We expect operating margin expansion of 75 to 125 bps in fiscal 2026."))
    assert out[("net sales growth", "FY2026", "PERCENT")][0] == "LOWERED"
    assert out[("operating margin expansion", "FY2026", "BPS")][0] == "RAISED"
    bps = [r for r in GP.guidance_evidence(_gpkg([("SEC:1:A-1", MAY, "8-K",
           "We expect operating margin expansion of 50 to 100 basis points in fiscal 2026.")]))["transcriptions"]][0]
    assert (bps["low"]["raw_value"], bps["low"]["normalized"], bps["high"]["normalized"]) == (50.0, 0.5, 1.0)


def test_one_sided_is_unknown_current_only_and_previous_only():
    only = _states(("SEC:1:A-1", MAY, "8-K", "We expect fiscal 2026 revenue of $1.4 billion to $1.5 billion."))
    assert only[("revenue", "FY2026", "USD")] == ("UNKNOWN", ["ONE_SIDED"])
    old_only = _states(("SEC:1:A-1", MAY, "8-K", "We expect fiscal 2025 revenue of $1.2 billion to $1.3 billion."),
                       ("SEC:1:A-2", AUG, "8-K", "Our quarterly dividend was $0.10 per share."))
    assert old_only[("revenue", "FY2025", "USD")] == ("UNKNOWN", ["ONE_SIDED"])


def test_period_and_metric_mismatch_are_never_compared():
    out = _states(("SEC:1:A-1", MAY, "8-K", "We expect fiscal 2026 revenue of $1.4 billion to $1.5 billion."),
                  ("SEC:1:A-2", AUG, "8-K", "We expect fiscal 2027 revenue of $1.6 billion to $1.7 billion. "
                                            "We expect fiscal 2026 adjusted EBITDA of $300 million to $320 million."))
    assert {k: v[0] for k, v in out.items()} == {("revenue", "FY2026", "USD"): "UNKNOWN",
                                                 ("revenue", "FY2027", "USD"): "UNKNOWN",
                                                 ("adjusted ebitda", "FY2026", "USD"): "UNKNOWN"}


def test_unknown_period_and_incomplete_bound_are_recorded_and_not_compared():
    ev = GP.guidance_evidence(_gpkg([
        ("SEC:1:A-1", MAY, "8-K", "We expect full year revenue of $1.4 billion to $1.5 billion."),
        ("SEC:1:A-2", AUG, "8-K", "We expect revenue of at least $1.2 billion for fiscal 2026.")]))
    assert ev["period_unknown"] == 1 and ev["incomplete_or_malformed"] == 1
    assert [(a["state"], a["reasons"]) for a in ev["assessments"]] == [
        ("UNKNOWN", ["CURRENT_INCOMPLETE_BOUND", "NO_COMPLETE_BOUNDS"])]
    assert ev["non_price_items"] == 0


def test_conflicting_ranges_at_one_publication_are_a_conflict():
    out = _states(("SEC:1:A-1", MAY, "8-K", "We expect fiscal 2026 revenue of $1.4 billion to $1.5 billion."),
                  ("SEC:1:A-2", AUG, "8-K", "We expect fiscal 2026 revenue of $1.5 billion to $1.6 billion."),
                  ("SEC:1:A-2:EX-99.1", AUG, "8-K", "We expect fiscal 2026 revenue of $1.45 billion to $1.6 billion."))
    assert out[("revenue", "FY2026", "USD")] == ("UNKNOWN", [GP.CONFLICT])


def test_a_later_amendment_is_the_later_side_by_publication_time():
    out = _states(("SEC:1:A-1", AUG, "8-K", "We expect fiscal 2026 revenue of $1.4 billion to $1.5 billion."),
                  ("SEC:1:A-3", AUG_LATER, "8-K/A", "We expect fiscal 2026 revenue of $1.3 billion to $1.45 billion."))
    assert out[("revenue", "FY2026", "USD")][0] == "LOWERED"


def test_in_sentence_previous_range_and_mixed_bounds():
    out = _states(("SEC:1:A-2", AUG, "8-K", "The Company raises its full year 2026 diluted EPS guidance to $7.93 to "
                                            "$8.23, up from the prior guidance of $7.57 to $7.97. The Company updated its "
                                            "fiscal 2026 revenue guidance from $1.40-$1.50 billion to $1.42-$1.48 billion."))
    assert out[("diluted eps", "FY2026", "USD_PER_SHARE")][0] == "RAISED"
    assert out[("revenue", "FY2026", "USD")][0] == "MIXED"


def test_transcription_never_mutates_the_source_numbers():
    text = ("The Company expects third quarter 2026 revenue of $154 million to $158 million. Fiscal 2026 diluted EPS "
            "guidance of $(0.05) to $0.02. Guidance for fiscal 2026 capital expenditures is $180 billion to $190 billion.")
    rows = GP.guidance_evidence(_gpkg([("SEC:1:A-1", MAY, "8-K", text)]))["transcriptions"]
    assert len(rows) == 3
    for r in rows:
        for side in ("low", "high"):
            v = r[side]
            assert v["raw"] in r["source_text"] and v["raw"] in text
            assert f"{abs(v['raw_value']):g}" in v["raw"].replace(",", "")
    assert [(r["period"], r["unit"], r["low"]["raw_value"], r["high"]["raw_value"]) for r in rows] == [
        ("Q3-2026", "USD", 154.0, 158.0), ("FY2026", "USD_PER_SHARE", -0.05, 0.02), ("FY2026", "USD", 180.0, 190.0)]


def test_tables_and_unlocated_sentences_are_not_transcribed():
    ev = GP.guidance_evidence(_gpkg([("SEC:1:A-1", MAY, "8-K", "| Fiscal 2026 outlook | Revenue | $1.4 - $1.5 billion |\n"
                                                                "Revenue was $1.3 billion for fiscal 2025.")]))
    assert ev["table_lines_with_guidance_terms"] == 1 and ev["transcriptions"] == []


# == D4 V2 ===========================================================================================

def _guidance_raised_package(states=None):
    pkg = N._package(states)
    extra = [
        pkg.chunks[0].model_copy(update={"evidence_id": "SEC:0001934850:G-1:CHUNK:0", "source_id": "SEC:0001934850:G-1",
                                         "section": None, "chunk_index": 0, "published_at": MAY, "available_at": MAY,
                                         "text": "We expect fiscal 2026 revenue of $1.40 billion to $1.50 billion."}),
        pkg.chunks[0].model_copy(update={"evidence_id": "SEC:0001934850:G-2:CHUNK:0", "source_id": "SEC:0001934850:G-2",
                                         "section": None, "chunk_index": 0, "published_at": AUG, "available_at": AUG,
                                         "text": "We now expect fiscal 2026 revenue of $1.50 billion to $1.60 billion."})]
    return pkg.model_copy(update={"chunks": list(pkg.chunks) + extra})


@needs_fg
def test_clear_positive_fixture_stays_unknown_because_no_frozen_positive_reading_exists():
    """Contract fixture 'clear_positive': every code-owned state improving, guidance RAISED by code arithmetic,
    a price reaction. D0 §L POSITIVE also needs the priced-in conjunct and its confidence (AI-owned, D4 §N/§Q),
    so the expected state declared before running is UNKNOWN."""
    improving = {m: "IMPROVING" for m in ("revenue", "operating_income", "eps_diluted", "free_cash_flow")}
    pkg = _guidance_raised_package(improving)
    d3 = _v2(pkg)
    record = V2.d4_from_bundle(N._bundle(), d3, package=pkg, analysis_id="A")
    out = record["final_output"]
    assert out["code_owned_expectation_evidence"]["non_price_expectation_items_code_owned"] >= 1
    assert out["reason_codes"][0].startswith("C1_SATISFIED_BY_")
    assert (out["expectation_gap"], out["expectation_gap_confidence"]) == ("UNKNOWN", "UNKNOWN")
    assert "G1_V2_POSITIVE_NOT_PRODUCED_PRICED_IN_CONJUNCT_AND_CONFIDENCE_AI_OWNED" in out["reason_codes"]
    assert not project_d4("FG", record).gap_permits_approve


@needs_fg
def test_clear_neutral_fixture_stays_unknown_and_negative_is_deferred():
    pkg = N._package()
    flat = N._bundle(price_reaction=[{"event_kind": "8-K 2.02", "return_1d": {"value": 0.0}}])
    out = V2.d4_from_bundle(flat, _v2(pkg), package=pkg, analysis_id="A")["final_output"]
    assert out["expectation_gap"] == "UNKNOWN"
    assert {"G3_V2_NEUTRAL_REASONABLE_REFLECTION_UNFROZEN",
            "G2_V2_LARGE_RERATING_NEGATIVE_DEFERRED_OPERATOR_DECISION_C"} <= set(out["reason_codes"])


@needs_fg
def test_clear_unknown_fixture_records_c6_when_there_is_no_expectation_evidence():
    pkg = N._package()
    none = N._bundle(price_reaction=[], guidance={"status": "NOT_FOUND", "excerpts": []})
    out = V2.d4_from_bundle(none, _v2(pkg), package=pkg, analysis_id="A")["final_output"]
    assert out["expectation_gap"] == "UNKNOWN" and "G4_C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN" in out["reason_codes"]
    assert out["d6_approve_precondition"] == "BLOCKED" and out["confidence_ceiling"] == "MEDIUM"


def test_v2_contract_is_frozen_and_declares_its_effect_before_running():
    body = V2.contract()
    assert body["declaration"]["new_numeric_thresholds"] == 0
    assert body["declaration"]["supersedes"]["sha256"] == N.DT.CHECKSUM.read_text().strip()
    assert "NOT reachable" in body["expected_effect_on_d6_declared_before_running"]["APPROVE"]
    assert body["d4"]["rules"]["G2_V2_NEGATIVE"]["large_rerating"].startswith("LARGE_RERATING_NEGATIVE = DEFERRED")


# == synthetic D6 APPROVE (D6 and paper unchanged) ===================================================

def _synthetic_approve_d3(prep, *, run_id, work):
    """Deterministic D3 V2 on the stored FG package (business + fundamental change are code-owned), plus the
    three D0 §N1 fields no frozen code rule produces, set synthetically: a material and timed catalyst and an
    invalidation condition. Synthetic evidence for a structural test, never a reading of real data."""
    d3 = _v2(N._package({"revenue": "IMPROVING"}))
    out = json.loads(json.dumps(d3["final_output"]))
    out["catalyst_candidates"][0].update(materiality_candidate="HIGH", timing_confidence="HIGH")
    out["invalidation_candidates"] = [{"condition": "SYNTHETIC: next 10-Q revenue state not IMPROVING",
                                       "sources": [out["catalyst_candidates"][0]["sources"][0]]}]
    return d3 | {"final_output": out, "final_output_checksum": "synthetic-approve-d3"}


def _synthetic_positive_d4(prep, d3, *, run_id, work):
    return {"final_status": "OK", "analysis_id": "SYN-D4", "research_input_id": d3["final_output"]["research_id"],
            "research_output_checksum": d3["final_output_checksum"], "final_output_checksum": "synthetic-positive-d4",
            "final_output": {"analysis_id": "SYN-D4", "expectation_gap": "POSITIVE",
                             "expectation_gap_confidence": "MEDIUM", "d6_approve_precondition": "SATISFIED",
                             "conflicts": [], "unknown_fields": []}}


@needs_fg
def test_synthetic_d6_approve_reaches_paper_pending_entry(store):
    T.seed({"FG": "WATCH"})
    HP.launch(at=T.PAPER_AT)
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    with NG.forbid_ai() as guard:
        out = ORC.evaluation_step(now=T.RUN_AT, deps=T.deps(d3=_synthetic_approve_d3, d4=_synthetic_positive_d4))
    assert guard.blocked == []
    result = out["results"][0]
    assert result["status"] == Q.COMPLETE and result["final_decision"] == "APPROVE"
    assert result["decision_transition"] == "WATCH->APPROVE" and result["ai_calls"] == 0
    T.store_bars("2026-10-13", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    T.store_bars("2026-10-14", {"FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    assert "PENDING_ENTRY" in [e["event"] for e in HP.events() if e.get("ticker") == "FG"]


@needs_fg
def test_the_deterministic_pipeline_alone_cannot_approve_the_same_issuer():
    d3 = _v2(N._package({"revenue": "IMPROVING"}))
    pkg = _guidance_raised_package({"revenue": "IMPROVING"})
    d4 = V2.d4_from_bundle(N._bundle(), d3, package=pkg, analysis_id="A")
    out = decide(project_d3("FG", d3), project_d4("FG", d4), project_d5(T.valuation_row("FG")))
    assert out.decision.value == "WATCH"
    assert set(out.approve_blockers) == {"future_business_or_catalyst_meaningful", "expectation_gap_permits_approve",
                                         "risks_with_an_invalidation_condition"}


@needs_fg
def test_v2_generators_make_no_model_call():
    with NG.forbid_ai() as guard:
        d3 = _v2(_strip_business_label())
        V2.d4_from_bundle(N._bundle(), d3, package=_strip_business_label(), analysis_id="A")
    assert guard.blocked == []


def test_class_d_fix_a_connector_is_never_part_of_the_metric_label():
    """IDCC 2026-07-30 EX-99.1 (replay): the metric is not named in the sentence, so nothing is transcribed
    instead of a metric called 'guidance to'."""
    text = ("we have raised the full year 2026 guidance to between $775 million and $845 million, an increase of "
            "$85 million at the midpoint.")
    assert GP.guidance_evidence(_gpkg([("SEC:1:A-1", AUG, "8-K", text)]))["transcriptions"] == []
