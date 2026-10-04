"""Persistence, the unchanged prompt, idempotency and the version boundary.

Section S tests covered here: 13 (8 candidates), 14 (5 candidates), 15 (1 candidate),
16 (0 candidates -> no GPT call), 20 (duplicate session idempotency), 21 (restart
idempotency), 22 (the scanner version is persisted) and 23 (legacy rows are untouched).
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine
from app.models.scanner import ScannerCandidate, ScannerRun
from app.research.prompt import ResearchPromptService
from app.research.versions import TOP8_PROMPT_VERSION
from app.repositories.scanner import ScannerSnapshotRepository
from app.services.candidate_source import CandidateSource, candidate_source_of
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import gpt_handoff as GPT
from app.strategy_a_mover_live import paper_adapter as PA
from app.strategy_a_mover_live import scanner as SCAN
from tests.strategy_a_mover_live.fixtures import SESSION
from tests.strategy_a_mover_live.test_a_live_scanner import ON, OBSERVED, panel_input

NOW = datetime(2026, 9, 15, 13, 16, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'handoff.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def live_scan(count: int):
    return SCAN.run(panel_input(count), observed_at=OBSERVED, environ=ON)


def legacy_run(session, trading_date: date = SESSION) -> ScannerRun:
    """A deployed trade-value run for the same date, to prove it is left alone."""
    run = ScannerRun(trading_date=trading_date, started_at=NOW, completed_at=NOW,
                     status="COMPLETED", provider="KIWOOM_REAL", score_version="quant_v0",
                     universe_count=100, excluded_count=92, candidate_count=8, top8_count=8)
    session.add(run)
    session.flush()
    for rank in range(1, 9):
        session.add(ScannerCandidate(
            scanner_run_id=run.id, symbol=f"L{rank}", rank=rank, is_top8=True, score=1.0,
            score_components_json={"latest_close": 10.0, "latest_volume": 1.0,
                                   "market_cap": None, "raw": {}, "normalized": {},
                                   "weighted_contributions": {}},
            observed_at=NOW, available_at=NOW))
    session.commit()
    return run


# -- candidate counts (section S tests 13, 14, 15) -------------------------------------------

@pytest.mark.parametrize("universe,expected", [(60, 8), (5, 5), (1, 1)])
def test_the_prompt_renders_the_candidate_count_it_is_given(database, universe, expected):
    live = live_scan(universe)
    assert live.candidate_count == expected
    result = GPT.persist(database, live, now=NOW)
    assert result.candidate_count == expected
    assert result.gpt_called
    assert f"exactly the same {expected} symbols" in result.prompt
    assert f"unique contiguous ranks 1..{expected}" in result.prompt
    assert TOP8_PROMPT_VERSION in result.prompt


def test_the_prompt_text_is_the_deployed_renderers_own(database):
    live = live_scan(60)
    result = GPT.persist(database, live, now=NOW)
    run = database.get(ScannerRun, result.run_id)
    deployed = ResearchPromptService(ScannerSnapshotRepository(database)).generate_top_for_run(run)
    assert result.prompt == deployed


# -- zero candidates means zero GPT calls (section S test 16) --------------------------------

def test_zero_candidates_persists_the_run_and_calls_nothing(database):
    live = live_scan(60)
    empty = replace(live, selection=replace(live.selection, handoff=()))
    result = GPT.persist(database, empty, now=NOW)
    assert result.candidate_count == 0
    assert result.prompt is None
    assert result.prompt_chars == 0
    assert result.gpt_calls_expected == 0
    assert not result.gpt_called
    run = database.get(ScannerRun, result.run_id)
    assert run.status == "COMPLETED" and run.top8_count == 0
    assert ScannerSnapshotRepository(database).get_top8(run.id) == []


def test_every_refusal_name_is_recordable():
    assert str(CFG.Refusal.NO_CANDIDATES) == "NO_CANDIDATES"
    assert str(CFG.Refusal.SCANNER_NOT_RUN) == "SCANNER_NOT_RUN"
    assert str(CFG.Refusal.DATA_UNAVAILABLE) == "DATA_UNAVAILABLE"


# -- idempotency (section S tests 20, 21) ----------------------------------------------------

def test_a_second_handoff_for_the_same_session_reuses_the_run(database):
    live = live_scan(60)
    first = GPT.persist(database, live, now=NOW)
    second = GPT.persist(database, live, now=NOW + timedelta(minutes=1))
    assert second.reused and second.run_id == first.run_id
    assert not first.reused
    runs = database.scalars(select(ScannerRun).where(
        ScannerRun.score_version == LC.RUN_SCORE_VERSION)).all()
    assert len(runs) == 1
    candidates = ScannerSnapshotRepository(database).get_top8(first.run_id)
    assert len(candidates) == 8                       # not doubled


def test_a_restart_finds_the_completed_run_instead_of_writing_another(database):
    live = live_scan(60)
    first = GPT.persist(database, live, now=NOW)
    database.expire_all()                             # a fresh process's view of the same rows
    found = GPT.existing_run(database, SESSION)
    assert found is not None and found.id == first.run_id
    restarted = GPT.persist(database, live_scan(60), now=NOW + timedelta(hours=1))
    assert restarted.reused and restarted.run_id == first.run_id
    assert restarted.prompt == first.prompt


def test_the_run_is_found_only_under_its_own_source(database):
    live = live_scan(60)
    GPT.persist(database, live, now=NOW)
    repository = ScannerSnapshotRepository(database)
    assert repository.get_latest_completed_run(SESSION, score_version="quant_v0") is None
    assert repository.get_latest_completed_run(
        SESSION, score_version=LC.RUN_SCORE_VERSION, provider=LC.RUN_PROVIDER) is not None


# -- the version boundary (section S tests 22, 23) -------------------------------------------

def test_the_scanner_version_is_persisted_on_the_run_and_on_every_candidate(database):
    live = live_scan(60)
    result = GPT.persist(database, live, now=NOW)
    run = database.get(ScannerRun, result.run_id)
    assert run.score_version == LC.RUN_SCORE_VERSION == "a_mover_live_v1"
    assert run.provider == LC.RUN_PROVIDER
    assert PA.source_of(run) == "A_MOVER_LIVE_V1"
    for candidate in ScannerSnapshotRepository(database).get_top8(run.id):
        body = candidate.score_components_json
        assert body["scanner_version"] == "A-MOVER-LIVE-V1"
        assert body["scanner_checksum"] == LC.current().scanner_checksum
        assert body["research_parent_version"] == "a-mover-scanner-v1.2"
        assert body["collector_version"] == LC.COLLECTOR_VERSION
        assert body["provider_contract"]["same_day_premarket_bars"] == "KIWOOM"


def test_a_legacy_run_for_the_same_date_is_untouched(database):
    legacy = legacy_run(database)
    before = (legacy.score_version, legacy.provider, legacy.top8_count, legacy.status,
              legacy.candidate_count)
    legacy_candidate_ids = [item.id for item in
                            ScannerSnapshotRepository(database).get_top8(legacy.id)]
    GPT.persist(database, live_scan(60), now=NOW)
    database.expire_all()
    again = database.get(ScannerRun, legacy.id)
    assert (again.score_version, again.provider, again.top8_count, again.status,
            again.candidate_count) == before
    assert [item.id for item in ScannerSnapshotRepository(database).get_top8(legacy.id)] \
        == legacy_candidate_ids
    assert candidate_source_of(again) is CandidateSource.LEGACY_TRANSACTION_AMOUNT
    assert PA.source_of(again) == "LEGACY_TRANSACTION_AMOUNT"


def test_both_sources_coexist_for_one_date_and_are_partitionable(database):
    legacy = legacy_run(database)
    result = GPT.persist(database, live_scan(60), now=NOW)
    runs = list(database.scalars(select(ScannerRun).order_by(ScannerRun.id)))
    parted = PA.partition_runs(runs)
    assert parted["LEGACY_TRANSACTION_AMOUNT"] == [legacy.id]
    assert parted["A_MOVER_LIVE_V1"] == [result.run_id]


def test_the_candidate_metadata_block_names_every_required_field():
    live = live_scan(60)
    rows = GPT.candidate_metadata(live)
    assert len(rows) == 8
    for row in rows:
        for name in ("symbol", "live_scanner_rank", "discovery_output_rank",
                     "discovery_pool_rank", "score", "score_components", "scanner_version",
                     "scanner_checksum", "provider_contract"):
            assert name in row, name
