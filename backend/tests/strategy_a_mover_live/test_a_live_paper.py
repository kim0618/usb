"""Paper candidate injection: the official path, APPROVE only, and the flag-off identity.

Section S tests covered here: 1 (A live off leaves A's behaviour unchanged), 17 (an APPROVED
candidate is injected), 18 (a REJECTED one is not) and 19 (no decision is not).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.repositories.simulation import SimulationStateRepository
from app.services import entry_management_runtime as EMR
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.lifecycle import OvernightSuitability, TrailingProfile
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import paper_adapter as PA
from app.market.calendar import MarketCalendar
from app.research import current_run as CR
from tests.strategy_a_mover_live.fixtures import ET, SESSION

NOW = datetime(2026, 9, 15, 13, 16, tzinfo=timezone.utc)
CAPITAL = Decimal("10000")
#: ``SESSION`` is the session the scan observed, so it is the *analysis* session; the entry
#: session that consumes it is the next XNYS one. Keeping both names in the tests is the
#: point: a run and the session that trades it are different dates.
ENTRY = MarketCalendar().next_trading_day(SESSION)
#: Both switches: the live scan runs *and* entry resolves candidates from it. Declining the
#: authority explicitly keeps the pre-live source, which
#: ``tests/test_morning_approval_contract.py`` covers.
ON = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "true"}
#: The explicit opt-out. An unset authority flag now follows the scan flag.
OFF = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "false"}


@pytest.fixture
def durable(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'paper.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        account_id = SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CAPITAL, cash=CAPITAL, created_at=NOW).id
        session.commit()
    yield SimulationRuntimeContext(SimBroker(CAPITAL), account_id, factory), factory
    engine.dispose()


def seed(factory, *, decisions: dict[str, str | None], trading_date: date = SESSION,
         score_version: str = LC.RUN_SCORE_VERSION,
         provider: str = LC.RUN_PROVIDER, completed_at: datetime = NOW) -> int:
    """One run, one analysis, and one decision per symbol. ``None`` means no decision row."""
    with factory() as session:
        run = ScannerRun(trading_date=trading_date, started_at=NOW, completed_at=completed_at,
                         status="COMPLETED", provider=provider, score_version=score_version,
                         universe_count=1000, excluded_count=900, candidate_count=35,
                         top8_count=len(decisions))
        session.add(run)
        session.flush()
        analysis = GPTAnalysis(scanner_run_id=run.id, trading_date=trading_date, provider="gpt",
                               model="m", prompt_version="1", schema_version="1",
                               evidence_version="1", status="IMPORTED", raw_json="{}",
                               payload_hash=f"hash-{score_version}-{trading_date}",
                               analysis_at=NOW)
        session.add(analysis)
        session.flush()
        for rank, (symbol, verdict) in enumerate(sorted(decisions.items()), start=1):
            candidate = ScannerCandidate(
                scanner_run_id=run.id, symbol=symbol, rank=rank, is_top8=True, score=1.0,
                score_components_json={"exchange": "NASDAQ",
                                       "scanner_version": LC.LIVE_VERSION},
                observed_at=NOW, available_at=NOW)
            session.add(candidate)
            session.flush()
            session.add(GPTCandidateAnalysis(
                gpt_analysis_id=analysis.id, scanner_candidate_id=candidate.id, symbol=symbol,
                gpt_rank=rank, overall_score=1, catalyst_score=1, fundamental_score=1,
                momentum_score=1, risk_score=1, evidence_confidence=1, catalyst_duration="D",
                stop_profile="TIGHT", trailing_profile=TrailingProfile.WIDE.value,
                overnight_suitability=OvernightSuitability.MEDIUM.value, company_summary="",
                catalyst_summary="", risk_summary="", invalidation_summary="",
                unknown_fields_json=[]))
            if verdict is not None:
                session.add(HumanDecisionRecord(
                    gpt_analysis_id=analysis.id, scanner_candidate_id=candidate.id,
                    symbol=symbol, decision=verdict, decided_at=NOW))
        run.active_gpt_analysis_id = analysis.id
        session.commit()
        return run.id


# -- the official path is not bypassed -------------------------------------------------------

def test_the_live_service_inherits_the_deployed_evaluation_path():
    assert PA.MoverLiveEntryLifecycleService.evaluate is EMR.EntryLifecycleService.evaluate
    overridden = {name for name in vars(PA.MoverLiveEntryLifecycleService)
                  if not name.startswith("__")}
    assert overridden == {"candidate_source", "scanner_version",
                          "approved_candidates", "session_metadata"}
    assert issubclass(PA.MoverLiveEntryLifecycleService, EMR.EntryLifecycleService)
    # The date rule is inherited, not overridden: that is the restored contract.
    assert (PA.MoverLiveEntryLifecycleService.analysis_session_date
            is EMR.EntryLifecycleService.analysis_session_date)


def test_entry_management_runtime_is_not_modified_by_the_adapter(durable):
    runtime, _ = durable
    built = PA.build_runtime(runtime, lambda: None, environ=ON)
    assert isinstance(built, EMR.EntryManagementRuntime)
    assert isinstance(built._lifecycle, PA.MoverLiveEntryLifecycleService)  # noqa: SLF001


# -- flag off: A is unchanged (section S test 1) ---------------------------------------------

@pytest.mark.parametrize("environ", [{}, OFF], ids=["nothing-set", "declined"])
def test_with_the_authority_off_the_pre_live_lifecycle_and_the_same_date_rule_are_used(
        durable, environ):
    runtime, _ = durable
    service = PA.lifecycle_for(runtime, environ=environ)
    assert type(service) is EMR.EntryLifecycleService
    assert service.analysis_session_date(ENTRY) == SESSION


@pytest.mark.parametrize("environ", [{}, OFF], ids=["nothing-set", "declined"])
def test_with_the_authority_off_a_legacy_run_wins_the_analysis_session(durable, environ):
    """Both sources are keyed by the analysis session now, so this is where they meet.

    The pre-live loader (``entry_management_runtime.load_approved_candidates``) is **not**
    source-filtered - it takes the newest COMPLETED run of the analysis session, whatever
    stamped it - and that is unchanged from HEAD. In production the trade-value run completes
    at 18:01 ET and the live cut at 09:27 ET of the same session, so the legacy row wins on
    ``completed_at`` and the declined branch reads what it names. This pins that ordering.
    """
    runtime, factory = durable
    legacy = seed(factory, decisions={"LLL": "APPROVE"}, score_version="quant_v0",
                  provider="KIWOOM_REAL", completed_at=NOW + timedelta(hours=9))
    seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=environ)
    approved = service.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in approved] == ["LLL"]
    assert approved[0].scanner_run_id == legacy


@pytest.mark.parametrize("environ", [{}, OFF], ids=["nothing-set", "declined"])
def test_with_the_authority_off_a_lone_live_run_is_held_off_by_the_import_guard(durable,
                                                                               environ):
    """The one case where the unfiltered pre-live loader would reach a live run.

    With no legacy run on the analysis session at all, the newest COMPLETED run of that date
    *is* the live one, and the unfiltered loader resolves it. What stops that in production is
    the enforcement point one step earlier: ``router.require_current_run_target`` refuses an
    import aimed at a run the bound source does not own, so a live run under a declined
    authority never acquires an ``active_gpt_analysis_id`` and the loader stops at
    ``resolve``. This test seeds the analysis directly, which is how it reaches past the guard,
    and is here so the dependency on the guard is written down rather than assumed.
    """
    runtime, factory = durable
    run_id = seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=environ)
    with factory() as session:
        run = session.get(ScannerRun, run_id)
        assert CR.is_live_run(run) is True
        assert CR.run_is_authoritative(run, environ) is False
        # Without the directly-seeded activation there is nothing to resolve.
        run.active_gpt_analysis_id = None
        session.commit()
    assert service.approved_candidates_for_entry_session(ENTRY) == ()


def test_with_the_authority_on_the_analysis_session_is_the_entry_sessions_predecessor(durable):
    runtime, _ = durable
    service = PA.lifecycle_for(runtime, environ=ON)
    assert isinstance(service, PA.MoverLiveEntryLifecycleService)
    assert service.analysis_session_date(ENTRY) == SESSION
    assert service.analysis_session_date(ENTRY) != ENTRY


# -- APPROVE only (section S tests 17, 18, 19) -----------------------------------------------

def test_an_approved_candidate_is_injected(durable):
    runtime, factory = durable
    run_id = seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=ON)
    candidates = service.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in candidates] == ["AAA"]
    assert candidates[0].scanner_run_id == run_id
    assert candidates[0].trailing_profile is TrailingProfile.WIDE


def test_a_rejected_candidate_is_not_injected(durable):
    runtime, factory = durable
    seed(factory, decisions={"AAA": "APPROVE", "BBB": "REJECT"})
    service = PA.lifecycle_for(runtime, environ=ON)
    assert [item.symbol for item in
            service.approved_candidates_for_entry_session(ENTRY)] == ["AAA"]


def test_a_candidate_with_no_decision_is_not_injected(durable):
    runtime, factory = durable
    seed(factory, decisions={"AAA": "APPROVE", "CCC": None})
    service = PA.lifecycle_for(runtime, environ=ON)
    assert [item.symbol for item in
            service.approved_candidates_for_entry_session(ENTRY)] == ["AAA"]


def test_nothing_is_injected_without_a_human_decision_at_all(durable):
    runtime, factory = durable
    seed(factory, decisions={"AAA": None, "BBB": None})
    service = PA.lifecycle_for(runtime, environ=ON)
    assert service.approved_candidates_for_entry_session(ENTRY) == ()


def test_candidates_arrive_in_gpt_rank_order(durable):
    runtime, factory = durable
    seed(factory, decisions={"AAA": "APPROVE", "BBB": "APPROVE", "CCC": "APPROVE"})
    runtime_service = PA.lifecycle_for(runtime, environ=ON)
    ranks = [item.rank for item in runtime_service.approved_candidates_for_entry_session(ENTRY)]
    assert ranks == sorted(ranks)


# -- the legacy boundary (section Q) ---------------------------------------------------------

def test_a_legacy_run_on_the_analysis_session_is_not_read_as_live(durable):
    runtime, factory = durable
    seed(factory, decisions={"LLL": "APPROVE"}, score_version="quant_v0",
         provider="KIWOOM_REAL")
    service = PA.lifecycle_for(runtime, environ=ON)
    assert service.approved_candidates_for_entry_session(ENTRY) == ()


def test_the_live_run_is_chosen_even_when_a_legacy_run_shares_the_date(durable):
    runtime, factory = durable
    seed(factory, decisions={"LLL": "APPROVE"}, score_version="quant_v0",
         provider="KIWOOM_REAL")
    seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=ON)
    assert [item.symbol for item in
            service.approved_candidates_for_entry_session(ENTRY)] == ["AAA"]


def test_session_metadata_records_the_source_and_the_scanner_version(durable):
    runtime, factory = durable
    run_id = seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=ON)
    body = service.session_metadata(ENTRY)
    assert body["candidate_source"] == "A_MOVER_LIVE_V1"
    assert body["scanner_version"] == "A-MOVER-LIVE-V1"
    assert body["scanner_checksum"] == LC.current().scanner_checksum
    # Resolved through the predecessor rule: the run is stamped with the analysis session.
    assert body["scanner_run_id"] == run_id
    assert body["entry_session_date"] == ENTRY.isoformat()
    assert body["analysis_session_date"] == SESSION.isoformat()
    assert body["analysis_session_rule"] == "previous_trading_day"


def test_session_metadata_reports_no_run_for_a_session_whose_predecessor_has_none(durable):
    """The entry session after the one this run serves has no run of its own, and says so."""
    runtime, factory = durable
    seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=ON)
    body = service.session_metadata(MarketCalendar().next_trading_day(ENTRY))
    assert body["scanner_run_id"] is None
    assert body["baseline_mode"] == "UNKNOWN"


def test_the_evaluation_key_follows_the_live_analysis_session(durable):
    runtime, factory = durable
    seed(factory, decisions={"AAA": "APPROVE"})
    service = PA.lifecycle_for(runtime, environ=ON)
    candidate = service.approved_candidates_for_entry_session(ENTRY)[0]
    key = service.evaluation_key(candidate, ENTRY)
    assert key.trading_date == ENTRY
    assert key.analysis_trading_date == SESSION
