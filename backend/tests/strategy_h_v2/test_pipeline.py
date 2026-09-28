from __future__ import annotations

from datetime import date

from conftest import mkfact, utc

from app.backtest.strategy_h_v2.change_detection import ChangeState
from app.backtest.strategy_h_v2.eligibility import EligibilityStatus, MarketSnapshot
from app.backtest.strategy_h_v2.evidence_bundle import NotResearched
from app.backtest.strategy_h_v2.pipeline import assemble_candidate
from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow

GENERATED_AT = utc(2026, 9, 28, 12, 0)
CUTOFF = utc(2026, 9, 1)

ROW = UniverseRow(
    ticker="ACME", cik="0000000001", security_id="BBG-TEST", exchange="XNAS",
    exchange_supported=True, security_type_status=SecurityTypeStatus.COMMON_STOCK,
    market="stocks", locale="us", snapshot_date="2026-08-31", known_at=GENERATED_AT,
)
GOOD_MARKET = MarketSnapshot(latest_close=40.0, trailing_sessions=200, trailing_avg_dollar_volume=20_000_000.0)


def _rich_facts():
    facts = []
    revenue = {2023: 100.0, 2024: 106.0, 2025: 114.0, 2026: 124.0}
    for y, v in revenue.items():
        facts.append(mkfact("revenue", v, date(y, 3, 31), start=date(y, 1, 1),
                             accession=f"REV-{y}"))
    facts += [
        mkfact("net_income", 8.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="NI-2026"),
        mkfact("operating_cash_flow", 12.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="OCF-2026"),
        mkfact("capex", 4.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="CAPEX-2026"),
        mkfact("cash", 50.0, date(2026, 6, 30), start=None, form="10-Q", fiscal_period="Q2", accession="CASH-2026"),
        mkfact("total_debt", 30.0, date(2026, 6, 30), start=None, form="10-Q", fiscal_period="Q2", accession="DEBT-2026"),
        mkfact("assets", 200.0, date(2026, 6, 30), start=None, form="10-Q", fiscal_period="Q2", accession="ASSETS-2026"),
        mkfact("equity", 90.0, date(2026, 6, 30), start=None, form="10-Q", fiscal_period="Q2", accession="EQ-2026"),
        mkfact("operating_income", 10.0, date(2026, 3, 31), start=date(2026, 1, 1), accession="OI-2026"),
        mkfact("eps_diluted", 0.5, date(2026, 3, 31), start=date(2026, 1, 1), accession="EPS-2026"),
        mkfact("shares_outstanding", 100_000_000.0, date(2026, 6, 30), start=None,
               fiscal_period=None, accession="SH-2026"),
    ]
    return facts


def test_eligible_candidate_gets_change_evidence_and_priority():
    result = assemble_candidate(
        ROW, run_id="RUN-1", generated_at=GENERATED_AT, data_cutoff=CUTOFF,
        facts=_rich_facts(), facts_fetched=True, split_dates=(), market=GOOD_MARKET,
        days_since_latest_filing=5, price_context={},
    )
    assert result.eligibility.status == EligibilityStatus.ELIGIBLE
    assert len(result.change_evidence) > 0
    assert result.priority is not None
    assert result.evidence.eligibility["status"] == "ELIGIBLE"
    assert result.evidence.future_business == NotResearched.NOT_RESEARCHED


def test_ineligible_candidate_skips_e2_and_e3():
    row = UniverseRow(**{**ROW.__dict__, "cik": None})
    result = assemble_candidate(
        row, run_id="RUN-1", generated_at=GENERATED_AT, data_cutoff=CUTOFF,
        facts=[], facts_fetched=False, split_dates=(), market=GOOD_MARKET,
        days_since_latest_filing=None, price_context={},
    )
    assert result.eligibility.status == EligibilityStatus.INELIGIBLE
    assert result.change_evidence == ()
    assert result.priority is None
    assert result.evidence.research_priority == {"state": None}


def test_data_not_ready_candidate_skips_e2_and_e3_but_is_not_ineligible():
    """The D1.1 fix, exercised end-to-end: a security with no local fundamentals at all must come
    back DATA_NOT_READY, not INELIGIBLE, and still gets no wasted E2/E3 effort."""
    result = assemble_candidate(
        ROW, run_id="RUN-1", generated_at=GENERATED_AT, data_cutoff=CUTOFF,
        facts=[], facts_fetched=False, split_dates=(), market=GOOD_MARKET,
        days_since_latest_filing=None, price_context={},
    )
    assert result.eligibility.status == EligibilityStatus.DATA_NOT_READY
    assert result.eligibility.status != EligibilityStatus.INELIGIBLE
    assert result.change_evidence == ()
    assert result.priority is None
    assert result.evidence.eligibility["status"] == "DATA_NOT_READY"


def test_evidence_stub_is_reproducible_for_same_inputs():
    kwargs = dict(
        row=ROW, run_id="RUN-1", generated_at=GENERATED_AT, data_cutoff=CUTOFF,
        facts=_rich_facts(), facts_fetched=True, split_dates=(), market=GOOD_MARKET,
        days_since_latest_filing=5, price_context={"return_1m": 0.03},
    )
    first = assemble_candidate(**kwargs)
    second = assemble_candidate(**kwargs)
    assert first.evidence.model_dump() == second.evidence.model_dump()


def test_evidence_stub_is_reproducible_across_a_fresh_rerun():
    """Rerun idempotence at the acquisition-consumer boundary: calling the pipeline twice on
    identical cached facts (as a rerun after an acquisition pass would) must not change the
    result, even though a new companyfacts fetch happened in between conceptually."""
    kwargs = dict(
        row=ROW, run_id="RUN-2", generated_at=GENERATED_AT, data_cutoff=CUTOFF,
        facts=_rich_facts(), facts_fetched=True, split_dates=(), market=GOOD_MARKET,
        days_since_latest_filing=5, price_context={},
    )
    before = assemble_candidate(**kwargs)
    after = assemble_candidate(**{**kwargs, "facts": _rich_facts()})
    assert before.evidence.model_dump() == after.evidence.model_dump()


def test_unknown_fields_include_unresolvable_change_metrics():
    result = assemble_candidate(
        ROW, run_id="RUN-1", generated_at=GENERATED_AT, data_cutoff=CUTOFF,
        facts=_rich_facts(), facts_fetched=True, split_dates=(), market=GOOD_MARKET,
        days_since_latest_filing=5, price_context={},
    )
    # total_debt only has one instant fact in the fixture -> UNKNOWN trend, must be surfaced.
    assert "total_debt" in result.evidence.unknown_fields
