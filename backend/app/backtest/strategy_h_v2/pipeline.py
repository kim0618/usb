"""H-V2 D1 pipeline: wires Universe -> E1 -> E2 -> E3 -> Candidate Evidence Stub for one security.

Pure functions only; no file I/O and no network access. `app.dev.run_strategy_h_v2_d1` supplies
real repository data (reference snapshot, local daily bars, local SEC companyfacts/submissions)
and writes run artifacts under `data/runtime/strategy_h_v2/d1/{run_id}/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

from app.backtest.strategy_h0.facts import FIELD_SPECS, CanonicalFact, canonical_coverage, resolve_fact
from app.backtest.strategy_h_v2 import change_detection as cd
from app.backtest.strategy_h_v2.eligibility import (
    EligibilityResult,
    EligibilityStatus,
    FundamentalsCoverage,
    MarketSnapshot,
    evaluate_eligibility,
)
from app.backtest.strategy_h_v2.evidence_bundle import CandidateEvidenceStub
from app.backtest.strategy_h_v2.research_priority import PriorityInputs, PriorityState, compute_priority
from app.backtest.strategy_h_v2.universe import UniverseRow

SCHEMA_VERSION = "h_v2_pipeline_v1"

BALANCE_SHEET_FIELDS = ("cash", "total_debt", "assets", "equity")
FLOW_SNAPSHOT_FIELDS = ("revenue", "operating_income", "net_income", "eps_diluted")


@dataclass(frozen=True)
class CandidateResult:
    ticker: str
    eligibility: EligibilityResult
    change_evidence: tuple[cd.ChangeEvidence, ...]
    priority: PriorityState | None
    evidence: CandidateEvidenceStub


def _snapshot(facts: Sequence[CanonicalFact], field_name: str, cutoff: datetime) -> dict[str, Any]:
    resolution = resolve_fact(facts, field_name, cutoff)
    if resolution.status.value != "OK" or resolution.fact is None:
        return {"status": resolution.status.value, "value": None}
    return {
        "status": "OK",
        "value": resolution.fact.value,
        "end": resolution.fact.end.isoformat(),
        "accession": resolution.fact.accession,
        "accepted_at": resolution.fact.accepted_at.isoformat(),
    }


def _change_dict(evidence: cd.ChangeEvidence | None) -> dict[str, Any]:
    if evidence is None:
        return {"state": cd.ChangeState.UNKNOWN.value, "confidence": cd.Confidence.UNKNOWN.value}
    return {
        "state": evidence.state.value,
        "confidence": evidence.confidence.value,
        "current_value": evidence.current_value,
        "points": [
            {
                "end": p.end, "value": p.value, "status": p.status,
                "current_accession": p.current_accession, "prior_accession": p.prior_accession,
            }
            for p in evidence.points
        ],
        "data_cutoff": evidence.data_cutoff,
    }


def _market_dict(market: MarketSnapshot | None) -> dict[str, Any]:
    if market is None:
        return {"latest_close": None, "trailing_sessions": 0, "trailing_avg_dollar_volume": None}
    return {
        "latest_close": market.latest_close,
        "trailing_sessions": market.trailing_sessions,
        "trailing_avg_dollar_volume": market.trailing_avg_dollar_volume,
    }


def assemble_candidate(
    row: UniverseRow,
    *,
    run_id: str,
    generated_at: datetime,
    data_cutoff: datetime,
    facts: Sequence[CanonicalFact] | None,
    split_dates: Sequence[date],
    market: MarketSnapshot | None,
    days_since_latest_filing: int | None,
    price_context: dict[str, Any],
) -> CandidateResult:
    if data_cutoff.tzinfo is None or generated_at.tzinfo is None:
        raise ValueError("generated_at/data_cutoff must be timezone-aware")
    decision = data_cutoff.date()
    facts = list(facts or [])

    dilution_ratio_value, dilution_material, dilution_evidence = cd.dilution_ratio(
        facts, data_cutoff, decision, split_dates=split_dates,
    )
    coverage = canonical_coverage(facts, data_cutoff)
    resolved_count = sum(1 for status in coverage.values() if status == "OK")
    equity_resolution = resolve_fact(facts, "equity", data_cutoff) if facts else None
    negative_equity = (
        equity_resolution.fact.value < 0
        if equity_resolution is not None and equity_resolution.status.value == "OK"
        else None
    )
    fundamentals = FundamentalsCoverage(
        resolved_field_count=resolved_count,
        total_field_count=len(FIELD_SPECS),
        negative_equity=negative_equity,
        material_dilution=dilution_material,
    )
    eligibility = evaluate_eligibility(row, market, fundamentals)

    change_evidence: tuple[cd.ChangeEvidence, ...] = ()
    priority: PriorityState | None = None
    if eligibility.status == EligibilityStatus.ELIGIBLE:
        change_evidence = (
            cd.growth_trend(facts, "revenue", data_cutoff, decision),
            cd.growth_trend(facts, "operating_income", data_cutoff, decision),
            cd.growth_trend(facts, "eps_diluted", data_cutoff, decision),
            cd.fcf_trend(facts, data_cutoff, decision),
            cd.operating_margin_trend(facts, data_cutoff, decision),
            cd.balance_sheet_trend(facts, "cash", data_cutoff, decision),
            cd.balance_sheet_trend(facts, "total_debt", data_cutoff, decision),
            dilution_evidence,
        )
        priority = compute_priority(PriorityInputs(
            change_evidence=change_evidence,
            days_since_latest_filing=days_since_latest_filing,
            resolved_field_count=resolved_count,
            total_field_count=len(FIELD_SPECS),
        ))

    by_metric = {ce.metric: ce for ce in change_evidence}
    unknown_fields = sorted({
        *(reason.value for reason in eligibility.reasons if eligibility.status == EligibilityStatus.UNKNOWN),
        *(metric for metric, ce in by_metric.items() if ce.state == cd.ChangeState.UNKNOWN),
    })

    evidence = CandidateEvidenceStub(
        run_id=run_id,
        generated_at=generated_at,
        data_cutoff=data_cutoff,
        ticker=row.ticker,
        identity={
            "ticker": row.ticker, "cik": row.cik, "security_id": row.security_id,
            "exchange": row.exchange, "security_type_status": row.security_type_status.value,
            "snapshot_date": row.snapshot_date,
        },
        market=_market_dict(market),
        fundamentals={field_name: _snapshot(facts, field_name, data_cutoff) for field_name in FLOW_SNAPSHOT_FIELDS},
        fundamental_changes={metric: _change_dict(ce) for metric, ce in by_metric.items()
                              if metric not in ("cash", "total_debt")},
        balance_sheet={
            **{field_name: _snapshot(facts, field_name, data_cutoff) for field_name in BALANCE_SHEET_FIELDS},
            "cash_trend": _change_dict(by_metric.get("cash")),
            "total_debt_trend": _change_dict(by_metric.get("total_debt")),
        },
        cashflow={
            "operating_cash_flow": _snapshot(facts, "operating_cash_flow", data_cutoff),
            "capex": _snapshot(facts, "capex", data_cutoff),
            "free_cash_flow_trend": _change_dict(by_metric.get("free_cash_flow")),
        },
        price_context=price_context,
        earnings={
            "status": "UNKNOWN",
            "note": "no PIT earnings-calendar/consensus source is proven available in this repository (H0 §K)",
        },
        eligibility={"status": eligibility.status.value, "reasons": [r.value for r in eligibility.reasons]},
        research_priority={"state": priority.value if priority is not None else None},
        data_quality={
            "resolved_canonical_fields": resolved_count,
            "total_canonical_fields": len(FIELD_SPECS),
            "canonical_field_coverage": coverage,
        },
        unknown_fields=unknown_fields,
    )
    return CandidateResult(row.ticker, eligibility, change_evidence, priority, evidence)
