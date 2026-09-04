"""Read-only application service: query and serialize stored domain snapshots."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, GPTSource, HumanDecisionRecord
from app.models.runtime import RuntimeFailureRecord, RuntimeStateRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.strategy import StrategyStateRecord
from app.research.adoption import ADOPTION_FILTER_VERSION, AdoptionInput, evaluate, recommendation_key


def decimal_string(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def run_dict(run: ScannerRun) -> dict[str, Any]:
    return {"id": run.id, "trading_date": run.trading_date, "started_at": run.started_at,
            "completed_at": run.completed_at, "status": run.status, "provider": run.provider,
            "score_version": run.score_version, "universe_count": run.universe_count,
            "excluded_count": run.excluded_count, "candidate_count": run.candidate_count,
            "top8_count": run.top8_count}


def candidate_dict(row: ScannerCandidate) -> dict[str, Any]:
    parts = row.score_components_json if isinstance(row.score_components_json, dict) else {}
    return {"candidate_id": row.id, "rank": row.rank, "symbol": row.symbol,
            "company_name": parts.get("company_name"), "quant_score": row.score,
            "exchange": parts.get("exchange"), "industry": parts.get("industry"),
            "previous_open": parts.get("previous_open"), "previous_high": parts.get("previous_high"),
            "previous_low": parts.get("previous_low"), "previous_return_pct": parts.get("previous_return_pct"),
            "latest_close": parts.get("latest_close"), "latest_volume": parts.get("latest_volume"),
            "market_cap": parts.get("market_cap"), "raw_metrics": parts.get("raw", {}),
            "normalized_metrics": parts.get("normalized", {}),
            "contributions": parts.get("weighted_contributions", {}), "is_top8": row.is_top8}


class APIQueryService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session, self.settings = session, settings

    def latest_run(self, trading_date: date | None = None, score_version: str | None = None) -> ScannerRun | None:
        stmt = select(ScannerRun).where(ScannerRun.status == "COMPLETED", ScannerRun.completed_at.is_not(None))
        if trading_date is not None: stmt = stmt.where(ScannerRun.trading_date == trading_date)
        if score_version is not None: stmt = stmt.where(ScannerRun.score_version == score_version)
        return self.session.scalar(stmt.order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1))

    def scanner_run(self, run: ScannerRun) -> dict[str, Any]:
        rows = list(self.session.scalars(select(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id)
                    .order_by(ScannerCandidate.rank.is_(None), ScannerCandidate.rank, ScannerCandidate.symbol)))
        values = [candidate_dict(row) for row in rows]
        return {"run": run_dict(run), "candidate_pool_count": len(values),
                "candidates": values, "top8": [item for item in values if item["is_top8"]]}

    def analysis(self, row: GPTAnalysis) -> dict[str, Any]:
        decisions = {d.symbol: d for d in self.session.scalars(select(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == row.id))}
        candidates = list(self.session.scalars(select(GPTCandidateAnalysis).where(GPTCandidateAnalysis.gpt_analysis_id == row.id).order_by(GPTCandidateAnalysis.gpt_rank, GPTCandidateAnalysis.symbol)))
        return {"analysis": {"id": row.id, "scanner_run_id": row.scanner_run_id, "trading_date": row.trading_date,
                "analysis_at": row.analysis_at, "imported_at": row.imported_at, "provider": row.provider,
                "model": row.model, "prompt_version": row.prompt_version, "schema_version": row.schema_version,
                "evidence_version": row.evidence_version, "status": row.status},
                "candidates": [self.research_candidate(item, decisions.get(item.symbol)) for item in candidates]}

    def adoption(self, row: GPTAnalysis) -> dict[str, Any]:
        decisions = {d.symbol: d for d in self.session.scalars(select(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == row.id))}
        candidates = list(self.session.scalars(select(GPTCandidateAnalysis).where(GPTCandidateAnalysis.gpt_analysis_id == row.id).order_by(GPTCandidateAnalysis.gpt_rank, GPTCandidateAnalysis.symbol)))
        source_ids = set(self.session.scalars(select(GPTSource.gpt_candidate_analysis_id).where(GPTSource.gpt_candidate_analysis_id.in_([item.id for item in candidates]), GPTSource.claim == "catalyst"))) if candidates else set()
        items: list[dict[str, Any]] = []
        for candidate in candidates:
            base = self.research_candidate(candidate, decisions.get(candidate.symbol))
            quant = self.session.get(ScannerCandidate, candidate.scanner_candidate_id)
            snapshot = quant.score_components_json if quant is not None and isinstance(quant.score_components_json, dict) else {}
            raw = snapshot.get("raw") or {}
            rule_input = AdoptionInput(symbol=candidate.symbol, quant_rank=base["quant_rank"], gpt_rank=candidate.gpt_rank,
                quant_score=base["quant_score"], overall_score=candidate.overall_score, catalyst_score=candidate.catalyst_score,
                momentum_score=candidate.momentum_score, risk_score=candidate.risk_score,
                evidence_score=candidate.evidence_confidence, fundamental_score=candidate.fundamental_score,
                rvol=raw.get("rvol"), relative_strength=raw.get("relative_strength"),
                unknown_fields=tuple(candidate.unknown_fields_json), catalyst_duration=candidate.catalyst_duration,
                has_catalyst_source=candidate.id in source_ids, pool_size=len(candidates))
            items.append({**base, "evidence_score": candidate.evidence_confidence,
                "relative_strength": raw.get("relative_strength"), "momentum": raw.get("momentum"),
                **evaluate(rule_input), "analysis_id": row.id, "scanner_run_id": row.scanner_run_id})
        items.sort(key=lambda item: recommendation_key(item["classification"], item["gpt_rank"], item["quant_rank"], item["symbol"]))
        for position, item in enumerate(items, start=1): item["recommendation_rank"] = position
        counts = {"adoption_candidate": 0, "review_required": 0, "excluded": 0}
        for item in items: counts[item["classification"].lower()] += 1
        return {"filter_version": ADOPTION_FILTER_VERSION, "analysis_id": row.id,
                "scanner_run_id": row.scanner_run_id, "trading_date": row.trading_date,
                "counts": counts, "items": items}

    def research_candidate(self, item: GPTCandidateAnalysis, decision: HumanDecisionRecord | None = None) -> dict[str, Any]:
        quant = self.session.get(ScannerCandidate, item.scanner_candidate_id)
        snapshot = {} if quant is None or not isinstance(quant.score_components_json, dict) else quant.score_components_json
        return {"scanner_candidate_id": item.scanner_candidate_id, "symbol": item.symbol,
                "quant_rank": None if quant is None else quant.rank, "gpt_rank": item.gpt_rank,
                "quant_score": None if quant is None else quant.score, "overall_score": item.overall_score,
                "catalyst_score": item.catalyst_score, "fundamental_score": item.fundamental_score,
                "momentum_score": item.momentum_score, "risk_score": item.risk_score,
                "evidence_confidence": item.evidence_confidence, "catalyst_duration": item.catalyst_duration,
                "stop_profile": item.stop_profile, "trailing_profile": item.trailing_profile,
                "overnight_suitability": item.overnight_suitability, "company_summary": item.company_summary,
                "catalyst_summary": item.catalyst_summary, "risk_summary": item.risk_summary,
                "invalidation_summary": item.invalidation_summary, "unknown_fields": item.unknown_fields_json,
                "company_name": snapshot.get("company_name"), "exchange": snapshot.get("exchange"),
                "industry": snapshot.get("industry"), "market_cap": snapshot.get("market_cap"),
                "previous_open": snapshot.get("previous_open"), "previous_high": snapshot.get("previous_high"),
                "previous_low": snapshot.get("previous_low"), "previous_close": snapshot.get("latest_close"),
                "previous_return_pct": snapshot.get("previous_return_pct"),
                "previous_volume": snapshot.get("latest_volume"),
                "rvol": (snapshot.get("raw") or {}).get("rvol"),
                "human_decision": None if decision is None else self.decision(decision)}

    @staticmethod
    def decision(row: HumanDecisionRecord) -> dict[str, Any]:
        return {"symbol": row.symbol, "decision": row.decision, "note": row.note, "decided_at": row.decided_at}

    @staticmethod
    def failure(row: RuntimeFailureRecord) -> dict[str, Any]:
        return {"id": row.id, "code": row.failure_code, "severity": row.severity, "component": row.component,
                "message": row.message, "occurred_at": row.occurred_at, "resolved": row.resolved,
                "resolved_at": row.resolved_at}

    def runtime(self, now: datetime) -> dict[str, Any]:
        row = self.session.get(RuntimeStateRecord, 1)
        last = self.session.scalar(select(RuntimeFailureRecord).order_by(RuntimeFailureRecord.occurred_at.desc(), RuntimeFailureRecord.id.desc()).limit(1))
        unresolved = int(self.session.scalar(select(func.count()).select_from(RuntimeFailureRecord).where(RuntimeFailureRecord.resolved.is_(False))) or 0)
        open_orders = int(self.session.scalar(select(func.count()).select_from(ExecutionOrderRecord).where(ExecutionOrderRecord.status.in_(("PENDING", "PARTIALLY_FILLED")))) or 0)
        phases = ("POSITION_OPEN", "PYRAMID_ADDED", "OVERNIGHT_REVIEW", "OVERNIGHT_HELD", "DAY2_ACTIVE", "EXIT_SIGNALLED")
        open_positions = int(self.session.scalar(select(func.count()).select_from(StrategyStateRecord).where(StrategyStateRecord.book == "ACTUAL", StrategyStateRecord.phase.in_(phases))) or 0)
        return {"mode": "NORMAL" if row is None else row.mode, "healthy": (row is None or row.mode == "NORMAL") and unresolved == 0,
                "last_heartbeat": None if row is None else row.last_heartbeat_at,
                "last_market_data": None if row is None else row.last_market_data_at,
                "last_execution": None if row is None else row.last_execution_at,
                "last_reconciliation": None if row is None else row.last_reconciliation_at,
                "unresolved_failure_count": unresolved, "last_failure": None if last is None else self.failure(last),
                "open_positions_count": open_positions, "open_orders_count": open_orders}

    def replay_report(self) -> dict[str, Any]:
        path = self.settings.resolved_data_dir / "runtime" / "replay_smoke_report.json"
        if not path.is_file(): return {"available": False, "generated_at": None, "summary": {}, "variants": [], "invariants": None, "determinism": None}
        try: payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {"available": False, "generated_at": None, "summary": {}, "variants": [], "invariants": None, "determinism": None}
        return {"available": True, "generated_at": payload.get("generated_at"),
                "summary": payload.get("summary", payload.get("totals", {})),
                "variants": payload.get("variants", payload.get("variant_summaries", [])),
                "invariants": payload.get("invariants"), "determinism": payload.get("determinism")}
