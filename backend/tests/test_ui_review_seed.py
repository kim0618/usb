"""Stage 9.7 deterministic UI-review dataset and safety guards."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base, create_db_engine, get_db
from app.dev.seed_ui import seed_database, validate_target
from app.main import create_app
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.models.research import GPTCandidateAnalysis, GPTSource, HumanDecisionRecord
from app.models.runtime import RuntimeFailureRecord
from app.models.scanner import ScannerCandidate, ScannerRun


def _settings(path: Path, *, app_env: str = "test") -> Settings:
    return Settings(app_env=app_env, database_url=f"sqlite:///{path}")


def _seed(path: Path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return seed_database(_settings(path), run_migrations=False)


def _snapshot(path: Path) -> dict:  # type: ignore[type-arg]
    engine = create_db_engine(f"sqlite:///{path}")
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        run = db.scalar(select(ScannerRun))
        assert run is not None
        scanner = [(r.symbol, r.rank, r.score, r.score_components_json) for r in db.scalars(
            select(ScannerCandidate).order_by(ScannerCandidate.rank))]
        research_rows = list(db.scalars(
            select(GPTCandidateAnalysis).order_by(GPTCandidateAnalysis.gpt_rank)))
        research = [(r.symbol, r.gpt_rank, db.get(ScannerCandidate, r.scanner_candidate_id).rank,
                     r.evidence_confidence, r.trailing_profile, r.overnight_suitability,
                     r.unknown_fields_json) for r in research_rows]
        decisions = sorted((r.symbol, r.decision) for r in db.scalars(select(HumanDecisionRecord)))
        sources = sorted((r.source_type, r.source_domain, r.claim) for r in db.scalars(select(GPTSource)))
        orders = [(r.id, r.symbol, r.side, str(r.requested_quantity), str(r.filled_quantity),
                   r.status, str(r.reference_price)) for r in db.scalars(
            select(ExecutionOrderRecord).order_by(ExecutionOrderRecord.id))]
        fills = [(r.id, r.order_id, str(r.quantity), str(r.fill_price), str(r.total_cost))
                 for r in db.scalars(select(ExecutionFillRecord).order_by(ExecutionFillRecord.id))]
        shadow = [(r.symbol, r.variant, r.status, str(r.net_pnl), str(r.net_r),
                   r.ambiguous_bar_count, r.holding_days) for r in db.scalars(
            select(ShadowTradeRecord).order_by(ShadowTradeRecord.id))]
        failures = [(r.failure_code, r.severity, r.component, r.resolved)
                    for r in db.scalars(select(RuntimeFailureRecord).order_by(RuntimeFailureRecord.id))]
    engine.dispose()
    return {"scanner": scanner, "research": research, "decisions": decisions,
            "sources": sources, "orders": orders, "fills": fills, "shadow": shadow,
            "failures": failures}


def test_dedicated_target_and_environment_guards(tmp_path: Path) -> None:
    target = tmp_path / "custom_ui_review.sqlite3"
    assert validate_target(_settings(target)) == target
    assert validate_target(_settings(target, app_env="development")) == target
    with pytest.raises(ValueError, match="blocked"):
        validate_target(_settings(target, app_env="production"))
    with pytest.raises(ValueError, match="non-UI-review"):
        validate_target(_settings(tmp_path / "usb.sqlite3"))


def test_seed_populates_required_coverage_and_second_run_is_safe(tmp_path: Path) -> None:
    target = tmp_path / "coverage_ui_review.sqlite3"
    first = _seed(target)
    second = seed_database(_settings(target), run_migrations=False)
    assert (first.candidate_pool, first.top8, first.research) == (21, 8, 8)
    assert (first.approve, first.reject, first.undecided) == (0, 0, 8)
    assert first.sources >= 8
    assert first.orders >= 6 and first.fills >= 4 and first.trades >= 6
    assert first.shadow_variants == 5 and first.failures == 4
    assert second.already_seeded is True
    assert first.__dict__ | {"already_seeded": True} == second.__dict__


def test_seed_is_deterministic_and_varied(tmp_path: Path) -> None:
    left = tmp_path / "left_ui_review.sqlite3"
    right = tmp_path / "right_ui_review.sqlite3"
    _seed(left); _seed(right)
    assert _snapshot(left) == _snapshot(right)
    snapshot = _snapshot(left)
    assert any(row[1] != row[2] for row in snapshot["research"])
    assert len({row[3] for row in snapshot["research"]}) >= 3
    assert snapshot["decisions"] == []
    assert {row[0] for row in snapshot["sources"]} >= {"IR", "NEWS", "OTHER"}


@pytest.mark.asyncio
async def test_seeded_api_responses_are_non_empty(tmp_path: Path) -> None:
    target = tmp_path / "api_ui_review.sqlite3"
    summary = _seed(target)
    engine = create_db_engine(f"sqlite:///{target}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    async def override():  # type: ignore[no-untyped-def]
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    paths = ("/api/v1/dashboard", "/api/v1/scanner/latest", "/api/v1/research/latest",
             "/api/v1/trading", "/api/v1/trading/orders", "/api/v1/trading/fills",
             "/api/v1/trading/trades", "/api/v1/shadow/summary", "/api/v1/shadow/trades",
             "/api/v1/runtime", "/api/v1/runtime/failures", "/api/v1/settings",
             "/api/v1/capabilities")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = {path: await client.get(path) for path in paths}
    assert all(response.status_code == 200 for response in responses.values())
    assert len(responses["/api/v1/scanner/latest"].json()["top8"]) == 8
    research_candidates = responses["/api/v1/research/latest"].json()["candidates"]
    assert len(research_candidates) == 8
    assert all(item["human_decision"] is None for item in research_candidates)
    assert len(responses["/api/v1/trading/orders"].json()) == summary.orders
    assert len(responses["/api/v1/trading/fills"].json()) == summary.fills
    assert responses["/api/v1/trading/trades"].json()
    assert all(item["candidate_paths"] > 0 and item["trades"] > 0 and item["no_trade"] > 0
               for item in responses["/api/v1/shadow/summary"].json()["variants"])
    assert len(responses["/api/v1/runtime/failures"].json()) == summary.failures
    dashboard = responses["/api/v1/dashboard"].json()
    assert dashboard["scanner"]["top8_count"] == 8
    assert dashboard["research"]["approved_count"] == 0
    assert dashboard["shadow"]["recent_result_count"] > 0
    assert dashboard["runtime"]["last_failure"] is not None
    assert responses["/api/v1/trading"].json()["open_positions"] == []
    engine.dispose()


def test_runtime_artifacts_are_git_ignored() -> None:
    patterns = (Path(__file__).parents[2] / ".gitignore").read_text(encoding="utf-8")
    for expected in ("*.sqlite", "*.sqlite3", "*-wal", "*-shm", "data/runtime/*", "logs/*"):
        assert expected in patterns
