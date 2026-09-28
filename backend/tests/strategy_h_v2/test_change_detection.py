from __future__ import annotations

import dataclasses
from datetime import date

import pytest
from conftest import mkfact, utc

from app.backtest.strategy_h_v2.change_detection import (
    ChangeEvidence,
    ChangeState,
    Confidence,
    balance_sheet_trend,
    classify_trend,
    dilution_ratio,
    fcf_trend,
    growth_trend,
    operating_margin_trend,
)

CUTOFF = utc(2026, 9, 28)


def _yearly_revenue(values: dict[int, float]) -> list:
    return [
        mkfact("revenue", v, date(y, 3, 31), start=date(y, 1, 1), form="10-Q", fiscal_period="Q1",
               accession=f"ACC-REV-{y}")
        for y, v in values.items()
    ]


def test_revenue_accelerating():
    values = {2022: 100.0, 2023: 102.0, 2024: 108.12, 2025: 121.09, 2026: 146.52}
    evidence = growth_trend(_yearly_revenue(values), "revenue", CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.ACCELERATING
    assert evidence.confidence == Confidence.HIGH
    assert len(evidence.points) == 4


def test_revenue_decelerating():
    values = {2022: 100.0, 2023: 140.0, 2024: 182.0, 2025: 214.76, 2026: 225.5}
    evidence = growth_trend(_yearly_revenue(values), "revenue", CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.DECELERATING
    assert evidence.confidence == Confidence.HIGH


def test_classify_trend_stable_within_epsilon():
    state, confidence = classify_trend([0.10, 0.105, 0.101, 0.108], allow_acceleration=True)
    assert state == ChangeState.STABLE


def test_classify_trend_insufficient_points_is_unknown():
    state, confidence = classify_trend([0.10], allow_acceleration=True)
    assert state == ChangeState.UNKNOWN
    assert confidence == Confidence.UNKNOWN


def test_classify_trend_inflection_positive():
    state, confidence = classify_trend([-0.10, -0.02, 0.05], allow_acceleration=True)
    assert state == ChangeState.INFLECTION_POSITIVE


def test_classify_trend_inflection_negative():
    state, confidence = classify_trend([0.10, 0.02, -0.05], allow_acceleration=True)
    assert state == ChangeState.INFLECTION_NEGATIVE


def test_operating_margin_improving():
    facts = []
    margins = {2022: (1000.0, 80.0), 2023: (1000.0, 90.0), 2024: (1000.0, 110.0), 2025: (1000.0, 140.0)}
    for y, (rev, oi) in margins.items():
        facts.append(mkfact("revenue", rev, date(y, 12, 31), start=date(y, 1, 1), form="10-K",
                             fiscal_period="FY", accession=f"ACC-FYREV-{y}"))
        facts.append(mkfact("operating_income", oi, date(y, 12, 31), start=date(y, 1, 1), form="10-K",
                             fiscal_period="FY", accession=f"ACC-FYOI-{y}"))
    evidence = operating_margin_trend(facts, CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.IMPROVING
    assert evidence.current_value == 0.14


def test_operating_margin_deteriorating():
    facts = []
    margins = {2022: (1000.0, 140.0), 2023: (1000.0, 120.0), 2024: (1000.0, 90.0), 2025: (1000.0, 70.0)}
    for y, (rev, oi) in margins.items():
        facts.append(mkfact("revenue", rev, date(y, 12, 31), start=date(y, 1, 1), form="10-K",
                             fiscal_period="FY", accession=f"ACC-FYREV-{y}"))
        facts.append(mkfact("operating_income", oi, date(y, 12, 31), start=date(y, 1, 1), form="10-K",
                             fiscal_period="FY", accession=f"ACC-FYOI-{y}"))
    evidence = operating_margin_trend(facts, CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.DETERIORATING


def test_eps_loss_to_profit_preserved_as_own_state():
    facts = [
        mkfact("eps_diluted", -0.50, date(2025, 3, 31), start=date(2025, 1, 1), accession="ACC-EPS-2025"),
        mkfact("eps_diluted", 0.80, date(2026, 3, 31), start=date(2026, 1, 1), accession="ACC-EPS-2026"),
    ]
    evidence = growth_trend(facts, "eps_diluted", CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.LOSS_TO_PROFIT


def test_eps_profit_to_loss_preserved_as_own_state():
    facts = [
        mkfact("eps_diluted", 0.80, date(2025, 3, 31), start=date(2025, 1, 1), accession="ACC-EPS-2025"),
        mkfact("eps_diluted", -0.30, date(2026, 3, 31), start=date(2026, 1, 1), accession="ACC-EPS-2026"),
    ]
    evidence = growth_trend(facts, "eps_diluted", CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.PROFIT_TO_LOSS


def test_fcf_negative_to_positive_transition():
    facts = [
        mkfact("operating_cash_flow", 10.0, date(2025, 3, 31), start=date(2025, 1, 1), accession="OCF-2025"),
        mkfact("capex", 30.0, date(2025, 3, 31), start=date(2025, 1, 1), accession="CAPEX-2025"),
        mkfact("operating_cash_flow", 50.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="OCF-2026"),
        mkfact("capex", 20.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="CAPEX-2026"),
    ]
    # prior FCF = 10-30 = -20 (nonpositive); current FCF = 50-20 = 30 (positive) -> LOSS_TO_PROFIT
    evidence = fcf_trend(facts, CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.LOSS_TO_PROFIT


def test_fcf_ytd_quarter_mismatch_is_rejected_not_guessed():
    facts = [
        # OCF tagged as Q2 YTD (Jan-Jun, ~181 days)
        mkfact("operating_cash_flow", 40.0, date(2026, 6, 30), start=date(2026, 1, 1),
               fiscal_period="Q2", accession="OCF-YTD-2026"),
        # capex tagged as discrete Q2 only (Apr-Jun, ~90 days) - not a YTD duration
        mkfact("capex", 15.0, date(2026, 6, 30), start=date(2026, 4, 1),
               fiscal_period="Q2", accession="CAPEX-Q-2026"),
    ]
    evidence = fcf_trend(facts, CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.UNKNOWN
    assert evidence.points == ()


def test_total_debt_unknown_when_no_facts():
    evidence = balance_sheet_trend([], "total_debt", CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.UNKNOWN
    assert evidence.confidence == Confidence.UNKNOWN


def test_total_debt_increasing_direction_only_no_value_judgment():
    facts = [
        mkfact("total_debt", 100.0, date(2024, 12, 31), start=None, form="10-K", fiscal_period="FY",
               accession="DEBT-2024"),
        mkfact("total_debt", 150.0, date(2025, 12, 31), start=None, form="10-K", fiscal_period="FY",
               accession="DEBT-2025"),
        mkfact("total_debt", 220.0, date(2026, 6, 30), start=None, form="10-Q", fiscal_period="Q2",
               accession="DEBT-2026"),
    ]
    evidence = balance_sheet_trend(facts, "total_debt", CUTOFF, date(2026, 9, 1))
    assert evidence.state == ChangeState.INCREASING
    # Code never labels a balance-sheet direction IMPROVING/DETERIORATING - that is an AI judgment.
    assert evidence.state not in (ChangeState.IMPROVING, ChangeState.DETERIORATING)


def test_dilution_flagged_when_no_matching_split():
    facts = [
        mkfact("shares_outstanding", 100_000_000.0, date(2025, 6, 30), start=None,
               fiscal_period=None, accession="SH-2025"),
        mkfact("shares_outstanding", 130_000_000.0, date(2026, 6, 30), start=None,
               fiscal_period=None, accession="SH-2026"),
    ]
    ratio, material, evidence = dilution_ratio(facts, CUTOFF, date(2026, 9, 1), split_dates=())
    assert material is True
    assert ratio > 0.10


def test_split_explained_increase_is_not_flagged_as_dilution():
    facts = [
        mkfact("shares_outstanding", 100_000_000.0, date(2025, 6, 30), start=None,
               fiscal_period=None, accession="SH-2025"),
        mkfact("shares_outstanding", 400_000_000.0, date(2026, 6, 30), start=None,
               fiscal_period=None, accession="SH-2026"),
    ]
    ratio, material, evidence = dilution_ratio(
        facts, CUTOFF, date(2026, 9, 1), split_dates=[date(2026, 1, 15)],
    )
    assert material is False


def test_pit_cutoff_excludes_facts_accepted_after_cutoff():
    early_cutoff = utc(2025, 6, 1)
    facts = [
        mkfact("revenue", 90.0, date(2024, 3, 31), start=date(2024, 1, 1), accession="ACC-PRIOR",
               accepted_at=utc(2024, 4, 20)),
        mkfact("revenue", 100.0, date(2025, 3, 31), start=date(2025, 1, 1), accession="ACC-A",
               accepted_at=utc(2025, 4, 20)),
        # Known only after `early_cutoff`; must not be usable as of that cutoff even though its
        # report period (`decision`) is otherwise within range.
        mkfact("revenue", 999.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="ACC-B",
               accepted_at=utc(2026, 4, 20)),
    ]
    evidence = growth_trend(facts, "revenue", early_cutoff, date(2026, 9, 1))
    assert evidence.points, "expected the 2025-vs-2024 pair to be usable as of early_cutoff"
    assert all(p.end != "2026-03-31" for p in evidence.points)
    assert evidence.current_value == pytest.approx(100.0 / 90.0 - 1)


def test_change_state_is_not_a_decision():
    """Structural invariant: nothing in the change-evidence schema encodes a decision."""
    field_names = {f.name for f in dataclasses.fields(ChangeEvidence)}
    forbidden = {"decision", "approve", "buy", "invest", "recommendation"}
    assert field_names.isdisjoint(forbidden)
    for state in ChangeState:
        assert state.value not in {"APPROVE", "BUY", "INVEST", "REJECT"}
