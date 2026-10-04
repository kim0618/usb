"""H-V2-D6: the projections that read stored D3 / D4 artifacts, and the pilot's own integrity.

Two kinds of test live here. The projection tests are hermetic - they build the stored-record shape by
hand, including the shapes that only exist because a real run produced them, so the mapping from
artifact to `D3Evidence` / `D4Evidence` is asserted without needing `data/runtime/`. The artifact tests
read the real run and SKIP when it is absent, following the pattern D4.1's artifact suite established:
a fresh checkout has no run to audit and a test that fails for that reason is noise.

The one that matters most is `test_frpt_and_spsc_project_as_refused_not_as_unknown`. FRPT and SPSC are
the two Tier B issuers whose D4 contract declined every attempt, and the whole of §10 turns on their
projection being an absence rather than a reading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.decision.d6_contract import (
    Confidence,
    D3Provenance,
    D4Provenance,
    Eligibility,
    ExpectationGap,
)
from app.dev.run_strategy_h_v2_d6 import (
    D4_RUNS,
    D4_UNIVERSE,
    METHOD_JUDGEMENTS,
    RUNTIME_ROOT,
    _claims_are_sourced,
    _latest_record,
    load_legs,
    project_d3,
    project_d4,
    project_d5,
)
from app.dev.run_strategy_h_v2_d5_d2 import (
    METHOD_JUDGEMENTS as D5_D2_JUDGEMENTS,
    PILOT_ISSUERS as D5_D2_PILOT_ISSUERS,
)


# -------------------------------------------------------------------------------------------------
# The sample and the judgement table
# -------------------------------------------------------------------------------------------------

def test_the_universe_is_d4s_thirteen_and_is_disjoint_from_d5_d2s_seven():
    """The point of the step: a sample where D4 output exists, which D5-D2's sample was not."""
    assert len(D4_UNIVERSE) == 13
    assert len(set(D4_UNIVERSE)) == 13
    assert set(D4_UNIVERSE).isdisjoint(set(D5_D2_PILOT_ISSUERS))


def test_every_issuer_has_a_declared_judgement_and_d5_d2s_table_is_untouched():
    """A missing judgement is a KeyError at run time, so it is asserted here instead."""
    assert set(METHOD_JUDGEMENTS) == set(D4_UNIVERSE)
    for ticker, judgement in METHOD_JUDGEMENTS.items():
        assert judgement.ticker == ticker
        assert judgement.business
        assert judgement.valuation_risk

    assert set(D5_D2_JUDGEMENTS) == set(D5_D2_PILOT_ISSUERS)
    assert set(METHOD_JUDGEMENTS).isdisjoint(set(D5_D2_JUDGEMENTS))


def test_a_declared_preference_is_ordered_and_never_names_a_method_it_calls_unsuitable():
    """The selection cannot have been steered by a fair value if the table contradicts itself."""
    for ticker, judgement in METHOD_JUDGEMENTS.items():
        assert isinstance(judgement.primary_preference, tuple), ticker
        assert isinstance(judgement.secondary_preference, tuple), ticker
        for method in judgement.primary_preference + judgement.secondary_preference:
            assert method not in judgement.unsuitable, f"{ticker} prefers a method it calls unsuitable"
        for reason in judgement.unsuitable.values():
            assert len(reason) > 80, f"{ticker}: a NOT_SUITABLE assertion needs an argument"


def test_an_issuer_with_no_preference_declares_why_in_its_valuation_risk():
    """An empty preference order is an abstention, and an abstention must carry its reason."""
    for ticker, judgement in METHOD_JUDGEMENTS.items():
        if judgement.primary_preference:
            continue
        assert judgement.valuation_risk, ticker


# -------------------------------------------------------------------------------------------------
# Projections, built by hand
# -------------------------------------------------------------------------------------------------

def _d3_record(**overrides) -> dict:
    out = {
        "research_id": "D3.3-X-20260930T000000.000000+0000",
        "business_model": {"revenue_drivers": [
            {"text": "a driver", "source_id": "SEC:1:2", "claim_type": "FACT"}]},
        "fundamental_change": [
            {"text": "revenue rose", "source_id": "SEC:1:2"},
            {"text": "margin rose", "claims": [{"source_id": "SEC:1:3"}]},
        ],
        "growth_durability": {"state": "MIXED"},
        "future_business": [{"name": "a line", "stage": "STORY",
                             "current_revenue_evidence": False, "order_backlog_evidence": False,
                             "customer_evidence": False, "capacity_evidence": False,
                             "margin_evidence": False}],
        "competitive_position": [{"text": "a position"}],
        "catalyst_candidates": [{"type": "EARNINGS", "materiality_candidate": "HIGH",
                                 "timing_confidence": "LOW"}],
        "risks": [{"text": "a risk"}],
        "invalidation_candidates": [{"text": "an invalidation"}],
        "unknown_fields": ["earnings_date"],
        "evidence_conflicts": [],
        "research_completeness": "PARTIAL",
    }
    out.update(overrides)
    return {"final_output": out, "final_output_checksum": "abc123"}


def test_project_d3_reads_only_what_the_contract_asks_about():
    d3 = project_d3("X", _d3_record())

    assert d3.provenance is D3Provenance.VALID
    assert d3.business_model_populated is True
    assert d3.fundamental_change_count == 2
    assert d3.fundamental_change_anchored is True
    assert d3.future_business_stages == ("STORY",)
    assert d3.future_business_evidence_present is False
    assert d3.catalyst_count == 1
    assert d3.catalyst_material_and_timed is False, "HIGH materiality at LOW timing is not both"
    assert d3.risk_count == 1 and d3.invalidation_count == 1
    assert d3.research_output_checksum == "abc123"


def test_a_future_business_entry_with_one_section_j_evidence_kind_counts():
    """D0 §J: any ONE of the six evidence kinds makes a claim REAL BUSINESS evidence."""
    record = _d3_record(future_business=[{"stage": "EARLY_EVIDENCE", "capacity_evidence": True}])

    assert project_d3("X", record).future_business_evidence_present is True


def test_an_unanchored_fundamental_change_entry_fails_the_anchoring_flag():
    """D0 §I: 'margin improved' with no named driver and no citation is incomplete, not accepted."""
    record = _d3_record(fundamental_change=[{"text": "margin improved"}])

    assert project_d3("X", record).fundamental_change_anchored is False
    assert _claims_are_sourced([{"text": "margin improved"}]) is False
    assert _claims_are_sourced([{"a": {"b": [{"source_id": "SEC:1:2"}]}}]) is True


def test_a_missing_d3_record_and_a_refused_one_are_different_states():
    assert project_d3("X", None).provenance is D3Provenance.NOT_EVALUATED
    assert project_d3("X", {"final_output": None}).provenance \
        is D3Provenance.REFUSED_NO_FINAL_OUTPUT


def test_project_d4_reads_the_gap_the_confidence_and_d4s_own_precondition():
    record = {
        "analysis_id": "X-a1", "final_output_checksum": "def456",
        "final_output": {"analysis_id": "X-a1", "expectation_gap": "NEUTRAL",
                         "expectation_gap_confidence": "LOW", "confidence_ceiling": "MEDIUM",
                         "d6_approve_precondition": "BLOCKED", "conflicts": [{}, {}],
                         "unknown_fields": ["a", "b", "c"]},
        "terminal_failure_codes": [],
    }
    d4 = project_d4("X", record)

    assert d4.provenance is D4Provenance.EVALUATED
    assert d4.gap is ExpectationGap.NEUTRAL
    assert d4.gap_confidence is Confidence.LOW
    assert d4.d6_approve_precondition == "BLOCKED"
    assert d4.gap_permits_approve is False
    assert d4.precondition_disagreement is None
    assert d4.conflict_count == 2


def test_a_d4_record_with_no_final_output_projects_as_refused_with_its_failure_codes():
    record = {"analysis_id": "X-a1", "final_output": None,
              "initial_failure_codes": ["SCHEMA"]}
    d4 = project_d4("X", record)

    assert d4.provenance is D4Provenance.REFUSED_NO_FINAL_OUTPUT
    assert d4.terminal_failure_codes == ("SCHEMA",)
    assert d4.gap is None, "a refusal carries no gap"
    assert d4.gap_permits_approve is False


def test_project_d5_carries_the_published_targets_and_adds_none():
    row = {
        "ticker": "X", "status": "VALUED", "primary_method": "EV/EBIT",
        "secondary_method": "EV/Sales", "contract_window": "RECENT_6M", "current_price": 124.96,
        "confidence": {"confidence": "MEDIUM", "drivers": ["a driver"]},
        "reconciliation": {"status": "CORROBORATES"},
        "target_prices": {"TP1": 150.5, "TP2": 180.25, "BEAR_ANCHOR": 110.0,
                          "upside_to_TP1": 0.2043854033290653, "upside_to_TP2": 0.44246158770006405,
                          "downside_to_bear": -0.11971830985915491},
        "valuation_risk": ["a risk"],
    }
    d5 = project_d5(row)

    assert d5.tp1 == 150.5 and d5.tp2 == 180.25
    assert d5.upside_to_tp1 == 0.2043854033290653
    assert d5.ready is True and d5.base_case_supports_upside is True and d5.fully_priced is False
    assert d5.confidence_drivers == ("a driver",)


def test_project_d5_on_a_not_ready_row_carries_no_target():
    row = {"ticker": "X", "status": "VALUATION_NOT_READY",
           "confidence": {"confidence": "NOT_READY", "drivers": ["no suitable method"]},
           "target_prices": None, "fair_value": None}
    d5 = project_d5(row)

    assert d5.tp1 is None and d5.tp2 is None
    assert d5.ready is False
    assert d5.base_case_supports_upside is False and d5.fully_priced is False


# -------------------------------------------------------------------------------------------------
# The real artifacts
# -------------------------------------------------------------------------------------------------

def _d4_dirs() -> list[Path]:
    return [RUNTIME_ROOT / d4 for _tier, d4, _d3 in D4_RUNS if (RUNTIME_ROOT / d4).is_dir()]


HAS_D4_RUNS = pytest.mark.skipif(not _d4_dirs(), reason="no D4 run artifacts present")


@HAS_D4_RUNS
def test_the_authoritative_runs_cover_the_whole_universe():
    legs = load_legs()

    assert set(legs) == set(D4_UNIVERSE)
    for ticker, leg in legs.items():
        assert leg.d4_tier is not None, ticker


@HAS_D4_RUNS
def test_frpt_and_spsc_project_as_refused_not_as_unknown():
    """§10's load-bearing case: D4 ran three times on each and its contract declined every time."""
    legs = load_legs()

    for ticker in ("FRPT", "SPSC"):
        d4 = legs[ticker].d4
        assert d4.provenance is D4Provenance.REFUSED_NO_FINAL_OUTPUT, ticker
        assert d4.gap is None, f"{ticker} must carry no gap at all"
        assert d4.gap_confidence is None, ticker
        assert d4.gap_permits_approve is False, ticker


@HAS_D4_RUNS
def test_no_issuer_in_the_universe_carries_a_positive_expectation_gap():
    """Measured, not assumed. If this ever fails the pilot's zero-APPROVE finding has changed."""
    legs = load_legs()
    gaps = {t: (None if legs[t].d4.gap is None else legs[t].d4.gap.value) for t in D4_UNIVERSE}

    assert not {g for g in gaps.values()} & {"POSITIVE", "WIDE_POSITIVE"}, gaps
    assert not {g for g in gaps.values()} & {"NEGATIVE", "WIDE_NEGATIVE"}, gaps


@HAS_D4_RUNS
def test_every_evaluated_d4_agrees_with_d0_section_l_as_d6_evaluates_it():
    """Two implementations of the frozen rule over the whole universe, compared rather than trusted."""
    legs = load_legs()
    disagreements = [legs[t].d4.precondition_disagreement for t in D4_UNIVERSE
                     if legs[t].d4.precondition_disagreement]

    assert disagreements == []


@HAS_D4_RUNS
def test_the_latest_record_picker_returns_one_record_per_issuer():
    for _tier, d4, _d3 in D4_RUNS:
        directory = RUNTIME_ROOT / d4
        if not directory.is_dir():
            continue
        for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
            record = _latest_record(directory, folder.name)
            assert record is not None, folder
            assert record.get("ticker") == folder.name


@HAS_D4_RUNS
def test_d3_is_valid_for_every_issuer_d4_evaluated():
    """D4 cannot have graded an issuer whose research leg produced nothing."""
    legs = load_legs()

    for ticker, leg in legs.items():
        if leg.d4.provenance is not D4Provenance.EVALUATED:
            continue
        assert leg.d3.provenance is D3Provenance.VALID, ticker
        assert leg.d3.research_id, ticker


def _d6_reports() -> list[Path]:
    directory = Path("data/runtime/strategy_h_v2/d6")
    return sorted(directory.glob("D6-*.json")) if directory.is_dir() else []


HAS_D6_RUN = pytest.mark.skipif(not _d6_reports(), reason="no D6 run present")


@HAS_D6_RUN
def test_the_stored_d6_report_has_no_defect_in_either_layer():
    report = json.loads(_d6_reports()[-1].read_text())

    for name, offenders in report["d5_defects"].items():
        assert offenders == [], f"D5 {name}: {offenders}"
    for name, offenders in report["d6_defects"].items():
        assert offenders == [], f"D6 {name}: {offenders}"
    assert report["d4_precondition_disagreements"] == []
    assert report["model_calls"] == 0 and report["cost_usd"] == 0.0


@HAS_D6_RUN
def test_the_stored_report_decides_exactly_the_eligible_issuers():
    report = json.loads(_d6_reports()[-1].read_text())
    eligible = set(report["decision_eligible"])

    for record in report["decisions"]:
        decided = record["decision"] is not None
        assert decided is (record["ticker"] in eligible), record["ticker"]
        if decided:
            assert record["eligibility"]["eligibility"] == Eligibility.DECISION_ELIGIBLE.value

    assert sum(report["decision_counts"].values()) == len(eligible)


@HAS_D6_RUN
def test_the_stored_report_reads_no_session_after_the_decision_session():
    """No forward return, asserted against the panel the run actually built."""
    report = json.loads(_d6_reports()[-1].read_text())
    session = report["decision_session"]

    for cover in report["coverage"]:
        if cover.get("status") != "OK":
            continue
        assert cover["last_session"] <= session, cover["ticker"]
