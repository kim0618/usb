"""Explicit opt-in entry point for a bounded real-market/simulation cycle.

The command deliberately owns no Kiwoom execution client. It validates the
operator DB/research handoff and canonical Kiwoom minute data, then reports
whether the frozen strategy is allowed to evaluate at the current ET session.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import REAL_MARKET_DATABASE_URL, Settings, get_settings
from app.core.database import create_db_engine
from app.market.factory import build_kiwoom_provider
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.strategy.session_policy import SessionPolicy

DEFAULT_DB = REAL_MARKET_DATABASE_URL.removeprefix("sqlite:///")


@dataclass(frozen=True)
class OperatorReadiness:
    scanner_run_id: int | None
    trading_date: object | None
    top8: tuple[str, ...]
    analysis_id: int | None
    research_candidates: int
    approved: int
    rejected: int
    undecided: int

    @property
    def ready(self) -> bool:
        return (self.scanner_run_id is not None and len(self.top8) == 8
                and self.analysis_id is not None and self.research_candidates == 8
                and 1 <= self.approved <= 2)


def inspect_readiness(session: Session) -> OperatorReadiness:
    """Read persisted operator truth without creating research or decisions."""
    run = session.scalar(select(ScannerRun).where(
        ScannerRun.status == "COMPLETED", ScannerRun.provider == "KIWOOM_REAL"
    ).order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1))
    if run is None:
        return OperatorReadiness(None, None, (), None, 0, 0, 0, 0)
    top8 = tuple(session.scalars(select(ScannerCandidate.symbol).where(
        ScannerCandidate.scanner_run_id == run.id, ScannerCandidate.is_top8.is_(True)
    ).order_by(ScannerCandidate.rank)))
    analysis = session.scalar(select(GPTAnalysis).where(
        GPTAnalysis.scanner_run_id == run.id, GPTAnalysis.status == "IMPORTED"
    ).order_by(GPTAnalysis.analysis_at.desc(), GPTAnalysis.id.desc()).limit(1))
    if analysis is None:
        return OperatorReadiness(run.id, run.trading_date, top8, None, 0, 0, 0, 0)
    research_count = int(session.scalar(select(func.count()).select_from(GPTCandidateAnalysis).where(
        GPTCandidateAnalysis.gpt_analysis_id == analysis.id)) or 0)
    decisions = list(session.scalars(select(HumanDecisionRecord.decision).where(
        HumanDecisionRecord.gpt_analysis_id == analysis.id)))
    approved, rejected = decisions.count("APPROVE"), decisions.count("REJECT")
    return OperatorReadiness(run.id, run.trading_date, top8, analysis.id, research_count,
                             approved, rejected, max(0, research_count - len(decisions)))


def print_readiness(readiness: OperatorReadiness, settings: Settings, now: datetime) -> None:
    print(f"Real scanner run: {readiness.scanner_run_id or 'NO'}")
    print(f"Trading date: {readiness.trading_date or 'N/A'}")
    print(f"TOP8 ({len(readiness.top8)}): {', '.join(readiness.top8) or 'NONE'}")
    print(f"Research imported: {'YES' if readiness.analysis_id else 'NO'}")
    print(f"Research candidates: {readiness.research_candidates}")
    print(f"Decisions: approved={readiness.approved}, rejected={readiness.rejected}, undecided={readiness.undecided}")
    print(f"Session: {SessionPolicy().session_at(now) or 'CLOSED'}")
    print(f"Market provider: {settings.market_data_provider.upper()}")
    print(f"Broker provider: {settings.broker_provider.upper()}")
    print(f"Kiwoom mode: {settings.kiwoom_mode.upper()}")
    print(f"Operator ready: {'YES' if readiness.ready else 'NO'}")


def safety_gate(settings: Settings) -> None:
    if not settings.run_kiwoom_real_scanner or not settings.run_real_market_simulation:
        raise SystemExit("NOT RUN: both real-scanner and real-market-simulation opt-ins are required")
    if (settings.market_data_provider, settings.broker_provider, settings.kiwoom_mode) != (
        "kiwoom", "simulation", "market_data_only"
    ):
        raise SystemExit("NOT RUN: kiwoom/simulation/market_data_only safety gate failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Kiwoom-real + Simulation cycle check")
    parser.add_argument("--database", help=f"SQLite path (default: runtime profile, otherwise {DEFAULT_DB})")
    parser.add_argument("--symbol", help="Optional approved symbol; never creates a decision")
    parser.add_argument("--check-ready", action="store_true", help="Read-only; does not call Kiwoom")
    args = parser.parse_args()
    settings = get_settings()
    safety_gate(settings)
    now = datetime.now(timezone.utc)
    policy = SessionPolicy()
    database = args.database or (
        settings.resolved_database_url.removeprefix("sqlite:///")
        if settings.runtime_profile == "real_market_operator"
        else DEFAULT_DB
    )
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[3] / db_path
    engine = create_db_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        readiness = inspect_readiness(session)
        print_readiness(readiness, settings, now)
        if args.check_ready:
            print("Kiwoom order requests executed = 0")
            return 0
        if readiness.analysis_id is None:
            print("Kiwoom order requests executed = 0")
            raise SystemExit("NOT RUN: imported GPT research is required")
        approvals = list(session.scalars(select(HumanDecisionRecord).where(
            HumanDecisionRecord.gpt_analysis_id == readiness.analysis_id,
            HumanDecisionRecord.decision == "APPROVE").order_by(HumanDecisionRecord.decided_at)))
    if len(approvals) > 2:
        print("Kiwoom order requests executed = 0")
        raise SystemExit("NOT RUN: human approval invariant exceeded")
    symbols = [item.symbol for item in approvals]
    if args.symbol:
        wanted = args.symbol.strip().upper()
        symbols = [symbol for symbol in symbols if symbol == wanted]
    if not symbols:
        print("Kiwoom order requests executed = 0")
        print("NO-OP: zero human approvals")
        return 0
    provider = build_kiwoom_provider(settings)
    try:
        bars = provider.get_minute_bars(symbols, start=now.replace(hour=0, minute=0, second=0, microsecond=0), end=now)
        print("Market: KIWOOM_REAL")
        print("Broker: SIMULATION")
        print("Account: SIMULATION")
        print("Kiwoom account: NOT USED")
        print(f"Approved symbols: {', '.join(symbols)}")
        print(f"Canonical minute bars: {len(bars)}")
        print(f"Session: {policy.session_at(now)}")
        print(f"New-entry evaluation allowed: {policy.permissions_at(now).new_entry}")
        print("APPROVE does not force BUY; Strategy/Risk must independently signal and approve")
    finally:
        print(f"Kiwoom order requests executed = {provider.client.order_request_count}")
    return 0 if provider.client.order_request_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
