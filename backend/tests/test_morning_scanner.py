import fcntl
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, create_db_engine
from app.core.config import Settings
from app.dev.run_morning_scanner import (
    DEFAULT_LIMIT,
    LOGGER,
    _database_job_lock,
    _log_completed_outcome,
    _paper_database_url,
)
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.research import GPTAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.simulation import (
    SimulationAccountRecord,
    SimulationPositionRecord,
    SimulationTradeRecord,
)
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.prompt import ResearchPromptService
from app.scanner.scanner import QuantScanner
from app.services.morning_scanner import (
    MorningJobResult,
    MorningJobStatus,
    MorningScanExecution,
    MorningScannerService,
)
from app.services.scanner import ScannerService
from tests.scanner_fixtures import SCAN_AS_OF, TRADING_DATE, scanner_providers

ET = ZoneInfo("America/New_York")


def _database(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'morning.sqlite3'}")
    Base.metadata.create_all(engine)
    return engine


def _successful_executor(session: Session, symbols: list[str], calls: list[date]):
    market, reference = scanner_providers(symbols)
    scanner = QuantScanner(market, reference)

    def execute(target: date, now: datetime) -> MorningScanExecution:
        calls.append(target)
        run_id, result = ScannerService(
            scanner, ScannerSnapshotRepository(session), provider_name="KIWOOM_REAL"
        ).run(symbols, trading_date=target, scan_as_of=now)
        return MorningScanExecution(run_id, result, {"MU": 1}, 0)

    return execute


def test_trading_day_persists_candidates_and_prepares_same_run_prompt(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    calls: list[date] = []
    with Session(engine) as session:
        account = SimulationAccountRecord(
            account_key="operator",
            broker_type="SIM",
            base_currency="USD",
            initial_cash=Decimal("7428.92"),
            cash=Decimal("7428.92"),
            state_version=0,
            created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        session.add(account)
        session.commit()
        repository = ScannerSnapshotRepository(session)
        result = MorningScannerService(
            repository, _successful_executor(session, ["AAA", "BBB"], calls)
        ).run(SCAN_AS_OF)
        assert result.status is MorningJobStatus.COMPLETED
        assert result.target_trading_date == TRADING_DATE
        assert result.mapping_issues == {"MU": 1}
        assert result.order_request_count == 0
        assert calls == [TRADING_DATE]
        run = session.get(ScannerRun, result.scanner_run_id)
        assert run is not None and run.trading_date == TRADING_DATE
        assert session.scalar(select(func.count()).select_from(ScannerCandidate)) == 2
        prompt = ResearchPromptService(repository).generate_top_for_run(run)
        assert f"scanner_run_id: {run.id}" in prompt
        assert result.prompt_chars == len(prompt)
        assert session.scalar(select(func.count()).select_from(GPTAnalysis)) == 0
        assert session.scalar(select(func.count()).select_from(HumanDecisionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionOrderRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionFillRecord)) == 0
        assert session.scalar(select(func.count()).select_from(SimulationPositionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(SimulationTradeRecord)) == 0
        session.refresh(account)
        assert account.cash == Decimal("7428.92")
        assert account.state_version == 0
    engine.dispose()


def test_same_target_rerun_reuses_durable_run_without_scanning(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    calls: list[date] = []
    with Session(engine) as session:
        service = MorningScannerService(
            ScannerSnapshotRepository(session),
            _successful_executor(session, ["AAA"], calls),
        )
        first = service.run(SCAN_AS_OF)
        second = service.run(SCAN_AS_OF)
        assert first.status is MorningJobStatus.COMPLETED
        assert second.status is MorningJobStatus.REUSED
        assert second.scanner_run_id == first.scanner_run_id
        assert calls == [TRADING_DATE]
        assert session.scalar(select(func.count()).select_from(ScannerRun)) == 1
    engine.dispose()


@pytest.mark.parametrize(
    "now,target",
    [
        (datetime(2024, 6, 29, 18, tzinfo=ET), date(2024, 6, 29)),
        (datetime(2024, 7, 4, 18, tzinfo=ET), date(2024, 7, 4)),
    ],
)
def test_weekend_and_holiday_skip_without_business_mutation(
    tmp_path: Path, now: datetime, target: date
) -> None:
    engine = _database(tmp_path)
    with Session(engine) as session:
        def forbidden(_target: date, _now: datetime) -> MorningScanExecution:
            raise AssertionError("provider must not run")

        outcome = MorningScannerService(
            ScannerSnapshotRepository(session), forbidden
        ).run(now)
        assert outcome.status is MorningJobStatus.SKIPPED
        assert outcome.target_trading_date == target
        assert session.scalar(select(func.count()).select_from(ScannerRun)) == 0
        assert session.scalar(select(func.count()).select_from(GPTAnalysis)) == 0
    engine.dispose()


def test_scanner_failure_does_not_create_run_or_prompt(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    with Session(engine) as session:
        def fail(_target: date, _now: datetime) -> MorningScanExecution:
            raise RuntimeError("scanner unavailable")

        with pytest.raises(RuntimeError, match="scanner unavailable"):
            MorningScannerService(ScannerSnapshotRepository(session), fail).run(SCAN_AS_OF)
        assert session.scalar(select(func.count()).select_from(ScannerRun)) == 0
        assert session.scalar(select(func.count()).select_from(GPTAnalysis)) == 0
    engine.dispose()


def test_prompt_failure_is_visible_after_durable_scanner_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    calls: list[date] = []
    with Session(engine) as session:
        service = MorningScannerService(
            ScannerSnapshotRepository(session),
            _successful_executor(session, ["AAA"], calls),
        )
        monkeypatch.setattr(
            service,
            "_prepare_prompt",
            lambda _run: (_ for _ in ()).throw(RuntimeError("prompt failed")),
        )
        with pytest.raises(RuntimeError, match="prompt failed"):
            service.run(SCAN_AS_OF)
        assert session.scalar(select(func.count()).select_from(ScannerRun)) == 1
        assert session.scalar(select(func.count()).select_from(GPTAnalysis)) == 0
    engine.dispose()


def test_production_limit_and_explicit_paper_database_identity(tmp_path: Path) -> None:
    paper_path = tmp_path / "paper.sqlite3"
    settings = Settings(
        runtime_profile="real_market_operator",
        market_data_provider="kiwoom",
        broker_provider="simulation",
        kiwoom_mode="market_data_only",
        paper_database_url=f"sqlite:///{paper_path}",
    )
    assert DEFAULT_LIMIT == 10
    assert _paper_database_url(settings) == f"sqlite:///{paper_path}"
    with pytest.raises(SystemExit, match="explicit PAPER_DATABASE_URL"):
        _paper_database_url(
            Settings(
                runtime_profile="real_market_operator",
                market_data_provider="kiwoom",
                broker_provider="simulation",
                kiwoom_mode="market_data_only",
                paper_database_url=None,
            )
        )


def _try_acquire_without_blocking(lock_path: Path) -> str:
    """Probe the lock through a second open file description, as a rival process would.

    ``flock`` conflicts between distinct open file descriptions rather than between
    processes, so this reproduces the cross-invocation contest without a subprocess.
    """
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return "ACQUIRED"
    except BlockingIOError:
        return "BLOCKED"
    finally:
        os.close(descriptor)


def test_database_job_lock_serializes_rival_invocations(tmp_path: Path) -> None:
    database = tmp_path / "paper.sqlite3"
    lock_path = database.with_suffix(database.suffix + ".morning.lock")
    with _database_job_lock(f"sqlite:///{database}"):
        assert lock_path.exists()
        assert _try_acquire_without_blocking(lock_path) == "BLOCKED"
    assert _try_acquire_without_blocking(lock_path) == "ACQUIRED"


def test_database_job_lock_is_per_database(tmp_path: Path) -> None:
    first = tmp_path / "paper.sqlite3"
    second = tmp_path / "other.sqlite3"
    with _database_job_lock(f"sqlite:///{first}"):
        other_lock = second.with_suffix(second.suffix + ".morning.lock")
        with _database_job_lock(f"sqlite:///{second}"):
            assert other_lock.exists()


def test_database_job_lock_passes_through_non_sqlite_url(tmp_path: Path) -> None:
    with _database_job_lock("postgresql://localhost/usb"):
        pass
    assert list(tmp_path.iterdir()) == []


def test_mapping_and_latest_bar_diagnostics_are_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = MorningJobResult(
        status=MorningJobStatus.COMPLETED,
        target_trading_date=TRADING_DATE,
        scanner_run_id=7,
        prompt_chars=123,
        mapping_issues={"MU": 2},
        latest_bar_missing_symbols=("MU",),
        order_request_count=0,
    )
    info_calls: list[tuple[object, ...]] = []
    warning_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(LOGGER, "info", lambda *args: info_calls.append(args))
    monkeypatch.setattr(LOGGER, "warning", lambda *args: warning_calls.append(args))
    _log_completed_outcome(outcome)
    assert warning_calls == [
        (
            "Scanner mapping issues target_trading_date=%s symbol=%s count=%d",
            TRADING_DATE,
            "MU",
            2,
        )
    ]
    assert (
        "Scanner latest-bar-missing target_trading_date=%s count=%d symbols=%s",
        TRADING_DATE,
        1,
        "MU",
    ) in info_calls
