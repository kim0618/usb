"""Build the deterministic, local-only database used for browser UI review."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.database import create_db_engine
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.models.research import GPTCandidateAnalysis, GPTSource, HumanDecisionRecord
from app.models.runtime import RuntimeFailureRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.monitoring.domain import FailureCode, FailureSeverity, ReconciliationResult
from app.monitoring.service import RuntimeHealthService
from app.replay_smoke import ReplaySmokeRunner, SyntheticReplayDataset
from app.repositories.research import ResearchRepository
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.domain import HumanDecision
from app.research.versions import GPT_SCHEMA_VERSION, TOP8_PROMPT_VERSION
from app.scanner.scanner import QuantScanner
from app.services.research import GPTImportService, HumanDecisionService
from app.services.scanner import ScannerService


DEFAULT_DB_PATH = PROJECT_ROOT / "data/runtime/usb_ui_review.sqlite3"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH}"
SEED_PROVIDER = "USB_UI_REVIEW_SEED"
SEED_TIME = datetime(2025, 11, 28, 21, 30, tzinfo=timezone.utc)
ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})


@dataclass(frozen=True)
class SeedSummary:
    database: Path
    candidate_pool: int
    top8: int
    research: int
    approve: int
    reject: int
    undecided: int
    sources: int
    orders: int
    fills: int
    trades: int
    shadow_variants: int
    failures: int
    already_seeded: bool = False


def sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url.endswith(":memory:"):
        raise ValueError("UI Review seed requires a file-backed SQLite database URL")
    raw = Path(database_url.removeprefix(prefix))
    return raw if raw.is_absolute() else (PROJECT_ROOT / raw).resolve()


def validate_target(settings: Settings) -> Path:
    environment = settings.app_env.strip().lower()
    if environment not in ALLOWED_ENVIRONMENTS:
        raise ValueError(
            f"UI Review seed is blocked in APP_ENV={settings.app_env!r}; "
            "only development/test are allowed"
        )
    target = sqlite_path(settings.resolved_database_url).resolve()
    if "ui_review" not in target.stem.lower():
        raise ValueError(
            "Refusing to modify a non-UI-review database; the SQLite filename must contain 'ui_review'"
        )
    return target


def migrate(database_url: str) -> None:
    """Use the existing Alembic history; no schema logic is duplicated here."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


def seed_database(settings: Settings, *, reset: bool = False, run_migrations: bool = True) -> SeedSummary:
    target = validate_target(settings)
    if reset and target.exists():
        target.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{target}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
    if run_migrations:
        migrate(settings.resolved_database_url)
    engine = create_db_engine(settings.resolved_database_url)
    try:
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        with sessions() as session:
            existing = session.scalar(select(ScannerRun).where(ScannerRun.provider == SEED_PROVIDER))
            if existing is not None:
                return _summary(session, target, already_seeded=True)
            _seed_scanner_and_research(session)
            _seed_execution(session)
            _seed_shadow(session)
            _seed_runtime(session)
            return _summary(session, target)
    finally:
        engine.dispose()


def _seed_scanner_and_research(session: Session) -> None:
    dataset = SyntheticReplayDataset.build(trading_day_count=1)
    trading_date = dataset.scan_days[0]
    scan_as_of = ReplaySmokeRunner(dataset).calendar.regular_market_close(trading_date)
    assert scan_as_of is not None
    run_id, result = ScannerService(
        QuantScanner(dataset.market_data, dataset.metadata),
        ScannerSnapshotRepository(session),
        provider_name=SEED_PROVIDER,
        clock=lambda: SEED_TIME,
    ).run(dataset.universe, trading_date=trading_date, scan_as_of=scan_as_of + timedelta(minutes=2))
    assert result.candidate_count >= 20 and len(result.top8) == 8
    payload = _research_payload(run_id, trading_date, [item.symbol for item in result.top8])
    analysis = GPTImportService(
        ResearchRepository(session), ScannerSnapshotRepository(session), clock=lambda: SEED_TIME
    ).import_json(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    decision_service = HumanDecisionService(ResearchRepository(session), clock=lambda: SEED_TIME)
    symbols = [item.symbol for item in result.top8]
    for symbol in symbols[:2]:
        decision_service.decide(analysis.id, symbol, HumanDecision.APPROVE, note="UI review approval")
    for symbol in symbols[2:4]:
        decision_service.decide(analysis.id, symbol, HumanDecision.REJECT, note="UI review rejection")


def _research_payload(run_id: int, trading_date: date, symbols: list[str]) -> dict[str, Any]:
    # Deliberately reverse adjacent Quant ranks so the two judgments are visibly independent.
    gpt_order = [1, 0, 3, 2, 5, 4, 7, 6]
    profiles = ["TIGHT", "NORMAL", "WIDE", "UNKNOWN"] * 2
    overnight = ["HIGH", "MEDIUM", "LOW", "UNKNOWN"] * 2
    candidates = []
    for quant_index, symbol in enumerate(symbols):
        source_sets = (
            [{"claim": "catalyst", "url": f"https://ir.example.com/{symbol}", "type": "IR", "title": "Synthetic IR release", "published_at": "2025-11-28T13:00:00Z"},
             {"claim": "guidance", "url": f"https://news.example.net/{symbol}", "type": "NEWS", "title": "Synthetic news coverage", "published_at": "2025-11-28T14:00:00Z"}],
            [{"claim": "catalyst", "url": f"https://news.example.net/{symbol}", "type": "NEWS", "title": "Synthetic catalyst report", "published_at": "2025-11-28T14:00:00Z"}],
            [{"claim": "background", "url": f"https://other.example.org/{symbol}", "type": "OTHER", "title": "Synthetic background", "published_at": None}],
        )[quant_index % 3]
        candidates.append({
            "ticker": symbol, "gpt_rank": gpt_order.index(quant_index) + 1,
            "overall_score": 91 - quant_index * 4, "catalyst_score": 94 - quant_index * 5,
            "fundamental_score": 82 - quant_index * 2, "momentum_score": 90 - quant_index * 3,
            "risk_score": 76 - quant_index * 3, "catalyst_duration": ["INTRADAY", "ONE_TO_TWO_DAYS", "MULTI_DAY", "UNKNOWN"][quant_index % 4],
            "stop_profile": profiles[quant_index], "trailing_profile": profiles[quant_index],
            "overnight_suitability": overnight[quant_index],
            "company_summary": f"{symbol} synthetic company profile for UI review.",
            "catalyst_summary": f"{symbol} has a deterministic synthetic catalyst scenario.",
            "risk_summary": f"{symbol} risk scenario {quant_index + 1}; not real trading data.",
            "invalidation_summary": "The review thesis is invalidated if the synthetic catalyst fails.",
            "unknown_fields": ["supplier_concentration"] if quant_index in {2, 6} else [],
            "sources": source_sets,
        })
    return {"schema_version": GPT_SCHEMA_VERSION, "prompt_version": TOP8_PROMPT_VERSION,
            "provider": "synthetic-fixture", "model": "offline-ui-review-v0",
            "analysis_at": SEED_TIME.isoformat(), "trading_date": trading_date.isoformat(),
            "scanner_run_id": run_id, "candidates": candidates}


def _seed_execution(session: Session) -> None:
    statuses = ("FILLED", "FILLED", "FILLED", "FILLED", "PARTIALLY_FILLED", "CANCELLED", "REJECTED", "FILLED")
    for index, status in enumerate(statuses, start=1):
        order_id = f"UI-ORDER-{index:02d}"
        requested = Decimal(str(5 + index))
        filled = requested if status == "FILLED" else (Decimal("3") if status == "PARTIALLY_FILLED" else Decimal("0"))
        submitted = SEED_TIME + timedelta(minutes=index)
        session.add(ExecutionOrderRecord(
            id=order_id, broker_type="SIM", symbol=f"S{index + 2:02d}",
            side="BUY" if index % 2 else "SELL", requested_quantity=requested,
            filled_quantity=filled, status=status,
            rejection_reason="NO_NEXT_BAR" if status == "REJECTED" else None,
            reference_price=Decimal("50") + index, submitted_at=submitted,
            completed_at=submitted + timedelta(minutes=1) if status in {"FILLED", "CANCELLED", "REJECTED"} else None,
            execution_version="execution_v0"))
        if filled:
            session.flush()
            price = Decimal("50.10") + index
            session.add(ExecutionFillRecord(
                id=f"UI-FILL-{index:02d}", order_id=order_id, quantity=filled,
                raw_market_price=price, fill_price=price + Decimal("0.02"),
                spread_cost=Decimal("0.06"), slippage_cost=Decimal("0.08"),
                commission=Decimal("0.03"), fx_cost=Decimal("0"), total_cost=Decimal("0.17"),
                filled_at=submitted + timedelta(minutes=1)))
    session.commit()


def _seed_shadow(session: Session) -> None:
    replay = ReplaySmokeRunner(SyntheticReplayDataset.build(trading_day_count=3)).run()
    for index, item in enumerate(replay.shadow_results):
        entry_at = datetime.combine(item.trading_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=15)
        closed = item.status == "CLOSED"
        session.add(ShadowTradeRecord(
            id=f"UI-SHADOW-{index:04d}", symbol=item.symbol, variant=item.variant,
            variant_version=replay.shadow_variant_version, is_control=item.variant == "C",
            initial_planned_risk=Decimal("100"), entry_at=entry_at if closed else None,
            exit_at=entry_at + timedelta(minutes=item.holding_minutes) if closed else None,
            average_entry_price=Decimal("50") if closed else None,
            average_exit_price=(Decimal("50") + item.net_pnl / Decimal("10")) if closed else None,
            gross_pnl=item.gross_pnl, net_pnl=item.net_pnl, gross_r=item.gross_r,
            net_r=item.net_r, total_cost=item.total_cost,
            ambiguous_bar_count=item.ambiguous_bar_count, status=item.status,
            premarket_passed=item.status != "NO_TRADE", opening_passed=closed,
            entry_signalled=closed, entry_filled=closed,
            no_trade_reason=item.exit_reason if item.status == "NO_TRADE" else None,
            exit_reason=item.exit_reason, gate_reached="EXIT" if closed else "PREMARKET",
            holding_days=2 if item.overnight else (1 if closed else 0)))
    session.commit()


def _seed_runtime(session: Session) -> None:
    service = RuntimeHealthService(session)
    service.bootstrap(SEED_TIME)
    service.record_heartbeat(SEED_TIME)
    service.record_market_data(SEED_TIME - timedelta(seconds=30), SEED_TIME)
    service.record_execution(SEED_TIME - timedelta(minutes=2), SEED_TIME)
    service.apply_reconciliation(ReconciliationResult(True, (), SEED_TIME))
    cases = (
        (FailureCode.MARKET_DATA_INVALID, FailureSeverity.WARNING, "market_data"),
        (FailureCode.EXECUTION_TIMEOUT, FailureSeverity.ERROR, "execution"),
        (FailureCode.HEARTBEAT_MISSED, FailureSeverity.WARNING, "watchdog"),
        (FailureCode.EXECUTION_REJECTED, FailureSeverity.ERROR, "broker"),
    )
    events = []
    for index, (code, severity, component) in enumerate(cases):
        events.append(service.report_failure(code=code, severity=severity, component=component,
            message=f"Resolved synthetic {code.value.lower()} example" if index < 2 else f"Synthetic {code.value.lower()} example",
            occurred_at=SEED_TIME - timedelta(hours=4 - index)))
    for event in events[:2]:
        assert event.id is not None
        service.resolve_failure(event.id, SEED_TIME - timedelta(minutes=30))


def _summary(session: Session, target: Path, *, already_seeded: bool = False) -> SeedSummary:
    run = session.scalar(select(ScannerRun).where(ScannerRun.provider == SEED_PROVIDER))
    analysis_id = None
    if run is not None:
        from app.models.research import GPTAnalysis
        analysis_id = session.scalar(select(GPTAnalysis.id).where(GPTAnalysis.scanner_run_id == run.id))
    decisions = [] if analysis_id is None else list(session.scalars(select(HumanDecisionRecord).where(HumanDecisionRecord.gpt_analysis_id == analysis_id)))
    return SeedSummary(
        target,
        0 if run is None else int(session.scalar(select(func.count()).select_from(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id)) or 0),
        0 if run is None else int(session.scalar(select(func.count()).select_from(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id, ScannerCandidate.is_top8.is_(True))) or 0),
        0 if analysis_id is None else int(session.scalar(select(func.count()).select_from(GPTCandidateAnalysis).where(GPTCandidateAnalysis.gpt_analysis_id == analysis_id)) or 0),
        sum(d.decision == "APPROVE" for d in decisions), sum(d.decision == "REJECT" for d in decisions),
        8 - len(decisions), int(session.scalar(select(func.count()).select_from(GPTSource)) or 0),
        int(session.scalar(select(func.count()).select_from(ExecutionOrderRecord)) or 0),
        int(session.scalar(select(func.count()).select_from(ExecutionFillRecord)) or 0),
        int(session.scalar(select(func.count()).select_from(ShadowTradeRecord).where(ShadowTradeRecord.status == "CLOSED")) or 0),
        len(set(session.scalars(select(ShadowTradeRecord.variant)))),
        int(session.scalar(select(func.count()).select_from(RuntimeFailureRecord)) or 0), already_seeded)


def _print_summary(summary: SeedSummary) -> None:
    state = "already present; no changes made" if summary.already_seeded else "completed"
    print(f"USB UI Review Seed {state}.")
    print(f"Database: {summary.database}")
    print(f"Scanner: Candidate Pool {summary.candidate_pool}, TOP8 {summary.top8}")
    print(f"Research: Candidates {summary.research}, APPROVE {summary.approve}, REJECT {summary.reject}, UNDECIDED {summary.undecided}, Sources {summary.sources}")
    print(f"Trading: Orders {summary.orders}, Fills {summary.fills}, Closed Trades {summary.trades}, Open Positions unavailable (process-local SimBroker)")
    print(f"Shadow: A-E {summary.shadow_variants == 5}")
    print(f"Runtime: Mode NORMAL, Failures {summary.failures}")
    print(f"Start backend with: DATABASE_URL=sqlite:///{summary.database} PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000")
    print("Open frontend: http://localhost:3000")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="recreate only a filename containing ui_review")
    args = parser.parse_args()
    settings = Settings(database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    try:
        _print_summary(seed_database(settings, reset=args.reset))
    except Exception as exc:
        parser.exit(1, f"UI Review seed failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
