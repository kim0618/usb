"""FastAPI V1 router. Domain rules remain in existing services."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.simulation import broker_projection, position_projection
from app.services.simulation_runtime import get_active_sim_broker
from app.api.schemas import HumanDecisionRequest, KillSwitchRequest, ReasonRequest, RecoveryRequest, ResearchImportRequest
from app.api.service import APIQueryService, decimal_string
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ResearchError
from app.market.calendar import MarketCalendar
from app.market.factory import build_kiwoom_provider
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, GPTSource, HumanDecisionRecord
from app.models.runtime import RuntimeFailureRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.strategy import StrategyStateRecord
from app.monitoring.domain import FailureCode, MismatchType, ReconciliationMismatch, ReconciliationResult
from app.monitoring.service import RuntimeHealthService
from app.repositories.research import ResearchRepository
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.prompt import ResearchPromptService
from app.research.versions import DETAIL_PROMPT_VERSION, EVIDENCE_VERSION, GPT_SCHEMA_VERSION, TOP8_PROMPT_VERSION
from app.research.adoption import ADOPTION_FILTER_VERSION
from app.scanner.config import ScannerConfig
from app.services.research import GPTImportService, HumanDecisionService
from app.services.market_context import MarketContextService
from app.risk.config import RiskConfig
from app.execution.config import ExecutionConfig
from app.strategy.config import SHADOW_VARIANT_VERSION, STRATEGY_VERSION
from app.monitoring.config import OPERATIONS_VERSION

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]


def query_service(db: Session) -> APIQueryService:
    return APIQueryService(db, get_settings())


def require(value: Any, message: str = "Resource not found") -> Any:
    if value is None: raise LookupError(message)
    return value


@router.get("/market/status", tags=["Market"])
async def market_status() -> dict[str, Any]:
    return display_market_status(datetime.now(timezone.utc), get_settings())


def display_market_status(as_of: datetime, settings: Settings) -> dict[str, Any]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    tz = ZoneInfo(settings.market_timezone); now = as_of.astimezone(tz)
    calendar = MarketCalendar(settings.market_timezone); window = calendar.session(now.date())
    if window is None or now.timetz().replace(tzinfo=None) < time(4) or now.timetz().replace(tzinfo=None) >= time(20):
        session = "CLOSED"
    elif now < window.market_open: session = "PREMARKET"
    elif now < window.market_close: session = "REGULAR"
    else: session = "POSTMARKET"
    return {"current_time": now, "market_timezone": settings.market_timezone, "trading_date": now.date(),
            "session": session, "is_trading_day": window is not None,
            "open_at": None if window is None else window.market_open,
            "close_at": None if window is None else window.market_close,
            "early_close": False if window is None else window.is_early_close}


@lru_cache(maxsize=1)
def market_context_service() -> MarketContextService:
    settings = get_settings()
    provider = build_kiwoom_provider(settings) if settings.market_data_provider == "kiwoom" else None
    return MarketContextService(provider, clock=lambda: datetime.now(timezone.utc))


@router.get("/scanner/latest", tags=["Scanner"])
async def scanner_latest(db: DB, trading_date: date | None = None, score_version: str | None = None) -> dict[str, Any]:
    service = query_service(db); return service.scanner_run(require(service.latest_run(trading_date, score_version), "No completed scanner run"))


@router.get("/scanner/runs", tags=["Scanner"])
async def scanner_runs(db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20, trading_date: date | None = None) -> list[dict[str, Any]]:
    from app.api.service import run_dict
    stmt = select(ScannerRun)
    if trading_date is not None: stmt = stmt.where(ScannerRun.trading_date == trading_date)
    rows = db.scalars(stmt.order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(limit))
    return [run_dict(row) for row in rows]


@router.get("/scanner/runs/{run_id}", tags=["Scanner"])
async def scanner_run(run_id: int, db: DB) -> dict[str, Any]:
    return query_service(db).scanner_run(require(db.get(ScannerRun, run_id), "Scanner run not found"))


def selected_run(db: Session, scanner_run_id: int | None, trading_date: date | None) -> ScannerRun:
    if scanner_run_id is not None: return require(db.get(ScannerRun, scanner_run_id), "Scanner run not found")
    return require(query_service(db).latest_run(trading_date), "No completed scanner run")


@router.get("/research/prompt", tags=["Research"])
async def research_prompt(db: DB, scanner_run_id: int | None = None, trading_date: date | None = None) -> dict[str, Any]:
    run = selected_run(db, scanner_run_id, trading_date)
    prompt = ResearchPromptService(ScannerSnapshotRepository(db)).generate_top_for_run(run)
    return {"scanner_run_id": run.id, "trading_date": run.trading_date, "prompt_version": TOP8_PROMPT_VERSION, "prompt": prompt}


@router.get("/research/prompt/{symbol}", tags=["Research"])
async def research_detail_prompt(symbol: str, db: DB, scanner_run_id: int | None = None) -> dict[str, Any]:
    run = selected_run(db, scanner_run_id, None)
    candidate = db.scalar(select(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id, ScannerCandidate.symbol == symbol.upper()))
    candidate = require(candidate, "Scanner candidate not found")
    prompt = ResearchPromptService(ScannerSnapshotRepository(db)).generate_detail(run, candidate)
    return {"scanner_run_id": run.id, "symbol": candidate.symbol, "prompt_version": DETAIL_PROMPT_VERSION, "prompt": prompt}


@router.post("/research/import", tags=["Research"], status_code=201)
async def research_import(body: ResearchImportRequest, db: DB) -> dict[str, Any]:
    analysis = GPTImportService(ResearchRepository(db), ScannerSnapshotRepository(db)).import_json(body.raw_json)
    count = int(db.scalar(select(func.count()).select_from(GPTCandidateAnalysis).where(GPTCandidateAnalysis.gpt_analysis_id == analysis.id)) or 0)
    return {"analysis_id": analysis.id, "scanner_run_id": analysis.scanner_run_id, "analysis_at": analysis.analysis_at,
            "provider": analysis.provider, "model": analysis.model, "imported_at": analysis.imported_at, "candidate_count": count}


@router.get("/research/latest", tags=["Research"])
async def research_latest(db: DB, scanner_run_id: int | None = None) -> dict[str, Any]:
    stmt = select(GPTAnalysis).where(GPTAnalysis.status == "IMPORTED")
    if scanner_run_id is not None: stmt = stmt.where(GPTAnalysis.scanner_run_id == scanner_run_id)
    row = require(db.scalar(stmt.order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc()).limit(1)), "Research analysis not found")
    result = query_service(db).analysis(row)
    settings = get_settings()
    if ui_review_mock_active(settings):
        # The seed dataset is a deterministic historical fixture (trading_date 2025-10-31); the frontend's
        # "진입 대기" list only shows when research.trading_date matches today, so mirror /market/status's
        # own now.date() here to make review data pass that same freshness check.
        tz = ZoneInfo(settings.market_timezone)
        result["analysis"]["trading_date"] = datetime.now(timezone.utc).astimezone(tz).date()
    return result


@router.get("/research/adoption", tags=["Research"])
async def research_adoption(db: DB, scanner_run_id: int | None = None) -> dict[str, Any]:
    stmt = select(GPTAnalysis).where(GPTAnalysis.status == "IMPORTED")
    if scanner_run_id is not None:
        stmt = stmt.where(GPTAnalysis.scanner_run_id == scanner_run_id)
    row = require(db.scalar(stmt.order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc()).limit(1)), "Research analysis not found")
    return query_service(db).adoption(row)


@router.get("/research/{analysis_id}/candidates/{symbol}", tags=["Research"])
async def research_candidate(analysis_id: int, symbol: str, db: DB) -> dict[str, Any]:
    analysis = require(db.get(GPTAnalysis, analysis_id), "Research analysis not found")
    row = require(db.scalar(select(GPTCandidateAnalysis).where(GPTCandidateAnalysis.gpt_analysis_id == analysis_id, GPTCandidateAnalysis.symbol == symbol.upper())), "Research candidate not found")
    decision = db.scalar(select(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == analysis_id, HumanDecisionRecord.symbol == row.symbol))
    sources = list(db.scalars(select(GPTSource).where(GPTSource.gpt_candidate_analysis_id == row.id).order_by(GPTSource.id)))
    result = query_service(db).research_candidate(row, decision)
    result.update(market_context_service().compose(
        row.symbol, result.get("previous_close"), trading_date=analysis.trading_date,
    ))
    result["sources"] = [{"claim": s.claim, "title": s.title, "url": s.url, "source_type": s.source_type,
                          "domain": s.source_domain, "published_at": s.published_at} for s in sources]
    return result


@router.put("/research/{analysis_id}/decisions/{symbol}", tags=["Research"])
async def decide(analysis_id: int, symbol: str, body: HumanDecisionRequest, db: DB) -> dict[str, Any]:
    record = HumanDecisionService(ResearchRepository(db)).decide(analysis_id, symbol, body.decision, note=body.note)
    approved = ResearchRepository(db).count_approvals(analysis_id)
    return {**query_service(db).decision(record), "approved_count": approved}


def strategy_dict(row: StrategyStateRecord) -> dict[str, Any]:
    return {"symbol": row.symbol, "trading_date": row.trading_date, "book": row.book, "variant": row.variant,
            "phase": row.phase, "entry_price": decimal_string(row.entry_price), "initial_stop": decimal_string(row.initial_stop),
            "active_stop": decimal_string(row.active_stop), "highest_price": decimal_string(row.highest_price),
            "add_count": row.add_count, "holding_day": row.holding_day, "overnight": row.overnight}


def ui_review_mock_active(settings: Settings) -> bool:
    """Gate for every UI-review-only mock below: only a local ui_review sqlite file in
    development/test, mirroring seed_ui.py's own safety check. Never reachable from a real DB."""
    if settings.app_env.strip().lower() not in {"development", "test"}:
        return False
    return "ui_review" in settings.resolved_database_url.lower()


def ui_review_mock_account() -> dict[str, Any] | None:
    """Synthetic account/positions for browser UI review only. SimBroker state is process-local
    and never persisted, so this fills the gap the same way seed_ui.py fills the DB. Uses symbols
    the seed left UNDECIDED (S11, S14) so the seeded APPROVE symbols (S04, S09) stay free to show
    up in the "진입 대기" waiting-to-enter list instead of looking already-entered."""
    settings = get_settings()
    if not ui_review_mock_active(settings):
        return None
    return {
        "account": {"currency": "USD", "equity": "55628.00", "cash": "42000.00", "invested_notional": "13596.00",
                     "unrealized_pnl": "32.00", "realized_pnl": "145.30", "today_pnl": "177.30"},
        "open_positions": [
            {"symbol": "S11", "currency": "USD", "invested_notional": "6276.00", "quantity": "120",
             "average_price": "52.30", "mark_price": "54.10", "market_value": "6492.00",
             "unrealized_pnl": "216.00", "return_pct": "3.44", "active_stop": "50.80", "phase": "POSITION_OPEN"},
            {"symbol": "S14", "currency": "USD", "invested_notional": "7320.00", "quantity": "80",
             "average_price": "91.50", "mark_price": "89.20", "market_value": "7136.00",
             "unrealized_pnl": "-184.00", "return_pct": "-2.51", "active_stop": "88.00", "phase": "PYRAMID_ADDED"},
        ],
        "open_orders": [],
    }


@router.get("/trading", tags=["Trading"])
async def trading(db: DB) -> dict[str, Any]:
    states = list(db.scalars(select(StrategyStateRecord).order_by(StrategyStateRecord.updated_at.desc(), StrategyStateRecord.id.desc())))
    broker = get_active_sim_broker()
    if broker is not None:
        return {"broker_mode": "SIMULATION", "availability": "AVAILABLE", **broker_projection(broker),
                "strategy_states": [strategy_dict(row) for row in states]}
    mock = ui_review_mock_account()
    if mock is not None:
        return {"broker_mode": "SIMULATION", "availability": "SIMULATED_UI_REVIEW", **mock,
                "strategy_states": [strategy_dict(row) for row in states]}
    return {"broker_mode": "SIMULATION", "availability": "NO_ACTIVE_SIM_BROKER", "account": None,
            "open_positions": [], "open_orders": [], "strategy_states": [strategy_dict(row) for row in states]}


@router.get("/trading/positions/{symbol}", tags=["Trading"])
async def position(symbol: str, db: DB) -> dict[str, Any]:
    broker = get_active_sim_broker()
    if broker is not None:
        return position_projection(broker, require(broker.get_position(symbol), "Active position is unavailable"))
    row = db.scalar(select(StrategyStateRecord).where(StrategyStateRecord.symbol == symbol.upper(), StrategyStateRecord.book == "ACTUAL").order_by(StrategyStateRecord.updated_at.desc()).limit(1))
    return strategy_dict(require(row, "Active position is unavailable"))


@router.get("/trading/orders", tags=["Trading"])
async def orders(db: DB, status: str | None = None, symbol: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    stmt = select(ExecutionOrderRecord)
    if status: stmt = stmt.where(ExecutionOrderRecord.status == status.upper())
    if symbol: stmt = stmt.where(ExecutionOrderRecord.symbol == symbol.upper())
    rows = db.scalars(stmt.order_by(ExecutionOrderRecord.submitted_at.desc(), ExecutionOrderRecord.id.desc()).limit(limit))
    return [{"order_id": r.id, "broker_type": r.broker_type, "symbol": r.symbol, "side": r.side,
             "requested_quantity": str(r.requested_quantity), "filled_quantity": str(r.filled_quantity),
             "status": r.status, "rejection_reason": r.rejection_reason, "reference_price": str(r.reference_price),
             "submitted_at": r.submitted_at, "completed_at": r.completed_at, "execution_version": r.execution_version} for r in rows]


@router.get("/trading/fills", tags=["Trading"])
async def fills(db: DB, symbol: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    stmt = select(ExecutionFillRecord, ExecutionOrderRecord.symbol).join(ExecutionOrderRecord, ExecutionOrderRecord.id == ExecutionFillRecord.order_id)
    if symbol: stmt = stmt.where(ExecutionOrderRecord.symbol == symbol.upper())
    rows = db.execute(stmt.order_by(ExecutionFillRecord.filled_at.desc(), ExecutionFillRecord.id.desc()).limit(limit))
    return [{"fill_id": r.id, "order_id": r.order_id, "symbol": sym, "fill_price": str(r.fill_price), "quantity": str(r.quantity),
             "spread_cost": str(r.spread_cost), "slippage_cost": str(r.slippage_cost), "commission": str(r.commission),
             "fx_cost": str(r.fx_cost), "total_cost": str(r.total_cost), "filled_at": r.filled_at} for r, sym in rows]


def shadow_dict(r: ShadowTradeRecord) -> dict[str, Any]:
    return {"id": r.id, "symbol": r.symbol, "variant": r.variant, "control": r.is_control, "status": r.status,
            "entry": decimal_string(r.average_entry_price), "exit": decimal_string(r.average_exit_price),
            "gross_pnl": str(r.gross_pnl), "net_pnl": str(r.net_pnl), "gross_r": str(r.gross_r), "net_r": str(r.net_r),
            "total_cost": str(r.total_cost), "exit_reason": r.exit_reason, "holding_duration": r.holding_days,
            "ambiguous_count": r.ambiguous_bar_count, "source": "SIMULATION"}


@router.get("/trading/trades", tags=["Trading"])
async def trades(db: DB, symbol: str | None = None, status: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    return await shadow_trades(db, None, symbol, None, status, limit)


@router.get("/shadow/trades", tags=["Shadow"])
async def shadow_trades(db: DB, variant: str | None = None, symbol: str | None = None, date: date | None = None,
                  status: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    stmt = select(ShadowTradeRecord)
    if variant: stmt = stmt.where(ShadowTradeRecord.variant == variant.upper())
    if symbol: stmt = stmt.where(ShadowTradeRecord.symbol == symbol.upper())
    if status: stmt = stmt.where(ShadowTradeRecord.status == status.upper())
    if date: stmt = stmt.where(func.date(ShadowTradeRecord.entry_at) == date.isoformat())
    rows = db.scalars(stmt.order_by(ShadowTradeRecord.exit_at.desc(), ShadowTradeRecord.entry_at.desc(), ShadowTradeRecord.id.desc()).limit(limit))
    return [shadow_dict(row) for row in rows]


def shadow_performance_date(
    row: ShadowTradeRecord, trading_dates: dict[int, date], market_timezone: str
) -> date | None:
    """Return the date on which a shadow path contributes to performance."""
    if row.status == "CLOSED":
        return None if row.exit_at is None else row.exit_at.astimezone(ZoneInfo(market_timezone)).date()
    return trading_dates.get(row.scanner_run_id) if row.scanner_run_id is not None else None


@router.get("/shadow/summary", tags=["Shadow"])
async def shadow_summary(
    db: DB, start_date: date | None = None, end_date: date | None = None
) -> dict[str, Any]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    rows = list(db.scalars(select(ShadowTradeRecord).order_by(ShadowTradeRecord.variant, ShadowTradeRecord.id)))
    if start_date is not None or end_date is not None:
        run_ids = {row.scanner_run_id for row in rows if row.scanner_run_id is not None}
        trading_dates = {
            run.id: run.trading_date
            for run in db.scalars(select(ScannerRun).where(ScannerRun.id.in_(run_ids)))
        } if run_ids else {}
        market_timezone = get_settings().market_timezone
        rows = [
            row for row in rows
            if (performance_date := shadow_performance_date(row, trading_dates, market_timezone)) is not None
            and (start_date is None or performance_date >= start_date)
            and (end_date is None or performance_date <= end_date)
        ]
    variants = []
    for variant in "ABCDE":
        items = [r for r in rows if r.variant == variant]; closed = [r for r in items if r.status == "CLOSED"]
        net = sum((r.net_pnl for r in items), Decimal("0")); net_r = sum((r.net_r for r in items), Decimal("0"))
        variants.append({"variant": variant, "control": variant == "C", "candidate_paths": len(items), "trades": len(closed),
            "no_trade": sum(r.status == "NO_TRADE" for r in items), "wins": sum(r.net_pnl > 0 for r in closed),
            "losses": sum(r.net_pnl < 0 for r in closed), "net_pnl": str(net), "net_r": str(net_r),
            "avg_net_r": str(net_r / len(closed)) if closed else "0", "total_cost": str(sum((r.total_cost for r in items), Decimal("0"))),
            "ambiguity": sum(r.ambiguous_bar_count for r in items), "overnight": sum(r.holding_days > 1 for r in items),
            "pyramid": None, "source": "SIMULATION"})
    return {"source": "SIMULATION", "variants": variants}


@router.get("/replay-smoke/latest", tags=["Shadow"])
async def replay_smoke(db: DB) -> dict[str, Any]: return query_service(db).replay_report()


@router.get("/runtime", tags=["Runtime"])
async def runtime(db: DB) -> dict[str, Any]: return query_service(db).runtime(datetime.now(timezone.utc))


@router.get("/runtime/failures", tags=["Runtime"])
async def failures(db: DB, resolved: bool | None = None, severity: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    stmt = select(RuntimeFailureRecord)
    if resolved is not None: stmt = stmt.where(RuntimeFailureRecord.resolved == resolved)
    if severity: stmt = stmt.where(RuntimeFailureRecord.severity == severity.upper())
    rows = db.scalars(stmt.order_by(RuntimeFailureRecord.occurred_at.desc(), RuntimeFailureRecord.id.desc()).limit(limit))
    return [query_service(db).failure(row) for row in rows]


@router.post("/runtime/failures/{failure_id}/resolve", tags=["Runtime"])
async def resolve_failure(failure_id: int, db: DB) -> dict[str, Any]:
    service = RuntimeHealthService(db); event = service.resolve_failure(failure_id, datetime.now(timezone.utc))
    return {"failure": query_service(db).failure(require(db.get(RuntimeFailureRecord, event.id))), "runtime_mode": service.health(datetime.now(timezone.utc)).mode.value}


@router.post("/runtime/safe-mode", tags=["Runtime"])
async def safe_mode(body: ReasonRequest, db: DB) -> dict[str, Any]:
    service = RuntimeHealthService(db); now = datetime.now(timezone.utc); previous = service.health(now).mode.value
    service.enter_safe_mode(now, body.reason); return {"previous_mode": previous, "mode": service.health(now).mode.value, "reason": body.reason, "changed_at": now}


@router.post("/runtime/halt", tags=["Runtime"])
async def halt(body: ReasonRequest, db: DB) -> dict[str, Any]:
    service = RuntimeHealthService(db); now = datetime.now(timezone.utc); previous = service.health(now).mode.value
    service.halt(now, message=body.reason); return {"previous_mode": previous, "mode": "HALTED", "reason": body.reason, "changed_at": now}


def persisted_reconciliation(db: Session, now: datetime) -> ReconciliationResult:
    active = int(db.scalar(select(func.count()).select_from(StrategyStateRecord).where(StrategyStateRecord.book == "ACTUAL", StrategyStateRecord.phase.in_(("POSITION_OPEN", "PYRAMID_ADDED", "OVERNIGHT_REVIEW", "OVERNIGHT_HELD", "DAY2_ACTIVE", "EXIT_SIGNALLED")))) or 0)
    mismatches = () if active == 0 else (ReconciliationMismatch(
        MismatchType.NONTERMINAL_STATE_WITHOUT_POSITION, "*",
        f"{active} active persisted strategy state(s) but no active broker runtime"),)
    return ReconciliationResult(active == 0, mismatches, now)


@router.post("/runtime/reconcile", tags=["Runtime"])
async def reconcile(db: DB) -> dict[str, Any]:
    now = datetime.now(timezone.utc); result = persisted_reconciliation(db, now); RuntimeHealthService(db).apply_reconciliation(result)
    return {"matched": result.matched, "checked_at": now,
            "mismatches": [{"type": item.mismatch_type.value, "symbol": item.symbol, "details": item.details} for item in result.mismatches]}


@router.post("/runtime/recover", tags=["Runtime"])
async def recover(body: RecoveryRequest, db: DB) -> dict[str, Any]:
    now = datetime.now(timezone.utc); state = RuntimeHealthService(db).recover_to_normal(acknowledged=body.acknowledged,
        reconciliation=persisted_reconciliation(db, now), recovered_at=now)
    return {"mode": state.mode.value, "healthy": state.healthy, "recovered_at": now}


@router.post("/runtime/kill-switch", tags=["Runtime"])
async def kill_switch(body: KillSwitchRequest, db: DB) -> dict[str, Any]:
    if not body.confirm: raise ValueError("kill switch confirmation is required")
    now = datetime.now(timezone.utc); service = RuntimeHealthService(db)
    service.halt(now, code=FailureCode.KILL_SWITCH_ACTIVATED, message=body.reason)
    return {"mode": "HALTED", "positions_before": 0, "liquidation_orders": [], "successful_liquidations": 0, "failed_liquidations": 0}


@router.get("/settings", tags=["Settings"])
async def settings_api() -> dict[str, Any]:
    settings = get_settings()
    return {"app_environment": settings.app_env, "scanner_version": ScannerConfig().score_version,
            "research_prompt_version": TOP8_PROMPT_VERSION, "research_schema_version": GPT_SCHEMA_VERSION,
            "evidence_version": EVIDENCE_VERSION, "risk_version": RiskConfig().version,
            "execution_version": ExecutionConfig().version, "strategy_version": STRATEGY_VERSION,
            "shadow_variant_version": SHADOW_VARIANT_VERSION, "operations_version": OPERATIONS_VERSION,
            "adoption_filter_version": ADOPTION_FILTER_VERSION,
            "broker_mode": "SIMULATION"}


@router.get("/capabilities", tags=["Settings"])
async def capabilities() -> dict[str, Any]:
    return {"market_data": {"fake": True, "replay": True, "kiwoom": False},
            "broker": {"simulation": True, "paper": False, "live": False},
            "features": {"scanner": True, "research": True, "shadow": True, "runtime_safety": True}}


@router.get("/dashboard", tags=["Dashboard"])
async def dashboard(db: DB) -> dict[str, Any]:
    now = datetime.now(timezone.utc); market = await market_status(); service = query_service(db); run = service.latest_run(); rt = service.runtime(now)
    analysis = db.scalar(select(GPTAnalysis).where(GPTAnalysis.status == "IMPORTED").order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc()).limit(1))
    approved = 0 if analysis is None else int(db.scalar(select(func.count()).select_from(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == analysis.id, HumanDecisionRecord.decision == "APPROVE")) or 0)
    shadow_count = int(db.scalar(select(func.count()).select_from(ShadowTradeRecord)) or 0)
    return {"system_time": now, "market": {"trading_date": market["trading_date"], "session": market["session"],
            "is_trading_day": market["is_trading_day"], "market_open": market["open_at"], "market_close": market["close_at"]},
            "runtime": {"mode": rt["mode"], "healthy": rt["healthy"], "last_heartbeat_at": rt["last_heartbeat"],
            "unresolved_failure_count": rt["unresolved_failure_count"], "last_failure": rt["last_failure"]},
            "scanner": {"latest_run_id": None if run is None else run.id, "trading_date": None if run is None else run.trading_date,
            "completed_at": None if run is None else run.completed_at, "candidate_count": 0 if run is None else run.candidate_count,
            "top8_count": 0 if run is None else run.top8_count},
            "research": {"latest_analysis_id": None if analysis is None else analysis.id, "analysis_at": None if analysis is None else analysis.analysis_at, "approved_count": approved},
            "trading": {"broker_mode": "SIMULATION", "open_positions_count": rt["open_positions_count"], "open_orders_count": rt["open_orders_count"]},
            "shadow": {"recent_result_count": shadow_count}}
