"""The provider mix as a persisted fact: on the run, on the entry session, and never on legacy.

Section O asks for ``baseline_mode``, ``kiwoom_session_count`` and ``massive_session_count`` on
the candidate and session records. The point of testing it separately from the arithmetic is
that a denominator made of two providers is only honest if a row can be read back years later
and still say which halves it was: a run that merely *computed* a mix and did not store one
would be indistinguishable from twenty Kiwoom sessions.

Also covered: an entry session with no stamp reads UNKNOWN rather than zero sessions, a restart
reuses the run and its stamp instead of writing a second one, and a legacy ``quant_v0`` row is
not given a baseline mode it never had.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine
from app.market.calendar import MarketCalendar
from app.models.scanner import ScannerCandidate, ScannerRun
from app.repositories.simulation import SimulationStateRepository
from app.services.candidate_source import LEGACY_SCORE_VERSION
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import gpt_handoff as GPT
from app.strategy_a_mover_live import paper_adapter as PA
from app.strategy_a_mover_live import scanner as SCAN
from tests.strategy_a_mover_live.fixtures import SESSION

#: ``SESSION`` is the session the scan observed; the entry session that consumes its approvals
#: is the next XNYS one, so that is what ``session_metadata`` is asked about.
ENTRY = MarketCalendar().next_trading_day(SESSION)
from tests.strategy_a_mover_live.test_a_live_scanner import baseline_for, panel_input

CAL = MarketCalendar("America/New_York")
OBSERVED = datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc)
#: Both switches: the live scan runs *and* entry resolves candidates from it. The scan
#: flag alone keeps the deployed predecessor contract, which
#: ``tests/test_morning_approval_contract.py`` covers.
ON = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "true"}
STAMP_KEYS = ("baseline_mode", "baseline_session_count", "kiwoom_session_count",
              "massive_session_count")


CAPITAL = Decimal("10000")


@pytest.fixture
def factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'record.sqlite3'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def mixed_scan(kiwoom: int, massive: int, count: int = 60) -> SCAN.LiveScan:
    """A live scan whose denominator really is ``kiwoom``/``massive`` sessions per symbol."""
    scan_input = panel_input(count)
    baselines = {}
    for index, symbol in enumerate(scan_input.symbols):
        item = baseline_for(symbol, 50_000.0)
        providers = tuple([B.BaselineProvider.MASSIVE] * massive
                          + [B.BaselineProvider.KIWOOM] * kiwoom)
        baselines[symbol] = B.AMoverBaseline(
            item.status, symbol, "ND", SESSION, item.used_sessions, (), item.volumes,
            item.median_volume, item.rule, providers)
    import dataclasses
    scan_input = dataclasses.replace(scan_input,
                                     baseline_provider_mix=B.provider_mix(baselines))
    return SCAN.run(scan_input, observed_at=OBSERVED, environ=ON)


# -- the run's own metadata ------------------------------------------------------------------

@pytest.mark.parametrize("kiwoom,massive,mode", [
    (0, 20, "MASSIVE_BOOTSTRAP"), (6, 14, "MIXED_BOOTSTRAP"), (20, 0, "KIWOOM_NATIVE")])
def test_the_scan_metadata_states_the_mode_and_both_counts(kiwoom, massive, mode):
    live = mixed_scan(kiwoom, massive)
    body = live.metadata()
    assert body["baseline_mode"] == mode
    assert body["baseline_session_count"] == B.BASELINE_SESSIONS
    assert body["baseline_provider_mix"]["per_symbol_kiwoom_sessions"]["min"] == kiwoom
    assert body["baseline_provider_contract"] == LC.BASELINE_PROVIDER_CONTRACT


def test_a_source_that_reports_no_mix_is_unknown_rather_than_zero_sessions():
    import dataclasses
    live = mixed_scan(10, 10)
    bare = dataclasses.replace(live, baseline_provider_mix=None)
    assert bare.baseline_stamp["baseline_mode"] == "UNKNOWN"
    assert bare.baseline_stamp["kiwoom_session_count"] is None
    assert bare.baseline_stamp["massive_session_count"] is None


# -- the candidate rows -----------------------------------------------------------------------

def test_every_candidate_row_carries_the_mix(factory):
    live = mixed_scan(6, 14)
    rows = SCAN.candidate_rows(live)
    assert rows
    for row in rows:
        for name in STAMP_KEYS:
            assert name in row.score_components
        assert row.score_components["baseline_mode"] == "MIXED_BOOTSTRAP"
        # section I's units: sessions out of twenty, not a sum over the scanned universe
        assert row.score_components["kiwoom_session_count"] == 6
        assert row.score_components["massive_session_count"] == 14
        assert row.score_components["baseline_session_count"] == B.BASELINE_SESSIONS
        assert row.score_components["baseline_provider_contract"] == \
            LC.BASELINE_PROVIDER_CONTRACT


def test_the_handoff_payload_carries_the_mix_per_candidate():
    live = mixed_scan(0, 20)
    payload = SCAN.candidate_payload(live)
    assert payload["baseline_mode"] == "MASSIVE_BOOTSTRAP"
    for entry in payload["candidates"]:
        assert entry["baseline_mode"] == "MASSIVE_BOOTSTRAP"


def test_the_persisted_run_can_be_read_back_as_a_mixed_denominator(factory):
    live = mixed_scan(6, 14)
    with factory() as session:
        result = GPT.persist(session, live, now=OBSERVED)
        assert result.candidate_count > 0
        run = session.get(ScannerRun, result.run_id)
        stamp = PA.baseline_stamp_of(session, run)
    assert stamp["baseline_mode"] == "MIXED_BOOTSTRAP"
    assert stamp["baseline_session_count"] == B.BASELINE_SESSIONS


def test_a_restart_reuses_the_run_and_its_stamp_rather_than_writing_a_second(factory):
    live = mixed_scan(6, 14)
    with factory() as session:
        first = GPT.persist(session, live, now=OBSERVED)
        second = GPT.persist(session, live, now=OBSERVED)
        assert second.reused is True
        assert second.run_id == first.run_id
        runs = list(session.scalars(select(ScannerRun)))
        assert len(runs) == 1
        run = session.get(ScannerRun, first.run_id)
        assert PA.baseline_stamp_of(session, run)["baseline_mode"] == "MIXED_BOOTSTRAP"


# -- the entry session's record ---------------------------------------------------------------

def entry_service(factory):
    """The deployed service with the live lifecycle, over a real simulation runtime."""
    with factory() as session:
        account_id = SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CAPITAL, cash=CAPITAL, created_at=OBSERVED).id
        session.commit()
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), account_id, factory)
    service = PA.lifecycle_for(runtime, calendar=CAL, environ=ON)
    assert isinstance(service, PA.MoverLiveEntryLifecycleService)
    return service


def test_session_metadata_reports_the_mode_the_morning_actually_divided_by(factory):
    live = mixed_scan(6, 14)
    with factory() as session:
        GPT.persist(session, live, now=OBSERVED)
    body = entry_service(factory).session_metadata(ENTRY)
    assert body["baseline_mode"] == "MIXED_BOOTSTRAP"
    assert body["kiwoom_session_count"] == 6
    assert body["massive_session_count"] == 14
    assert body["baseline_version"] == LC.BASELINE_VERSION
    assert body["baseline_provider_contract"] == LC.BASELINE_PROVIDER_CONTRACT


def test_an_entry_session_with_no_live_run_reports_unknown_not_zero(factory):
    body = entry_service(factory).session_metadata(ENTRY)
    assert body["scanner_run_id"] is None
    assert body["baseline_mode"] == "UNKNOWN"
    assert body["kiwoom_session_count"] is None
    assert body["massive_session_count"] is None


def test_a_run_that_admitted_nobody_reports_unknown_not_zero(factory):
    with factory() as session:
        run = ScannerRun(trading_date=SESSION, started_at=OBSERVED, completed_at=OBSERVED,
                         status="COMPLETED", provider=LC.RUN_PROVIDER,
                         score_version=LC.RUN_SCORE_VERSION, universe_count=10,
                         excluded_count=10, candidate_count=0, top8_count=0)
        session.add(run)
        session.commit()
    body = entry_service(factory).session_metadata(ENTRY)
    assert body["scanner_run_id"] is not None
    assert body["baseline_mode"] == "UNKNOWN"


# -- no legacy row acquires a baseline mode ---------------------------------------------------

def test_a_legacy_run_is_not_read_as_a_live_denominator(factory):
    with factory() as session:
        run = ScannerRun(trading_date=SESSION, started_at=OBSERVED, completed_at=OBSERVED,
                         status="COMPLETED", provider="KIWOOM", score_version=LEGACY_SCORE_VERSION,
                         universe_count=10, excluded_count=2, candidate_count=8, top8_count=8)
        session.add(run)
        session.flush()
        session.add(ScannerCandidate(scanner_run_id=run.id, symbol="AAA", rank=1, is_top8=True,
                                     score=1.0, score_components_json={"exchange": "NASDAQ"},
                                     observed_at=OBSERVED, available_at=OBSERVED))
        session.commit()
        legacy_id = run.id
    body = entry_service(factory).session_metadata(ENTRY)
    # the live resolver is source-filtered, so the legacy run is simply not this session's run
    assert body["scanner_run_id"] != legacy_id
    assert body["scanner_run_id"] is None
    assert body["baseline_mode"] == "UNKNOWN"
    with factory() as session:
        row = session.scalar(select(ScannerCandidate))
        assert "baseline_mode" not in dict(row.score_components_json)


def test_the_live_run_identity_is_still_distinct_from_legacy_and_research():
    from app.backtest.mover_scanner_v1 import contract as K
    assert LC.RUN_SCORE_VERSION not in (LEGACY_SCORE_VERSION, K.RUN_SCORE_VERSION)


# -- the prompt text does not change (section N) ----------------------------------------------

def test_the_baseline_stamp_does_not_reach_the_gpt_prompt(factory):
    """The mix is a record, not a research input: adding it must not alter the prompt text.

    ``ResearchPromptService`` reads named keys out of ``score_components``, so a new key is
    inert by construction - but "by construction" is worth asserting, because the prompt is the
    one artifact this stage promised not to touch.
    """
    live = mixed_scan(6, 14)
    with factory() as session:
        result = GPT.persist(session, live, now=OBSERVED)
        prompt = result.prompt
    assert prompt
    for name in STAMP_KEYS:
        assert name not in prompt
    assert "MIXED_BOOTSTRAP" not in prompt
    assert LC.BASELINE_PROVIDER_CONTRACT not in prompt


def test_the_prompt_is_byte_identical_with_and_without_the_mix(factory, tmp_path):
    """Two runs that differ only in the denominator's provenance render the same prompt."""
    import dataclasses
    from sqlalchemy.orm import sessionmaker as maker
    from app.core.database import Base as B2, create_db_engine as engine_for

    prompts = []
    for mix in (B.provider_mix({}), None):
        other = engine_for(f"sqlite:///{tmp_path / f'prompt{len(prompts)}.sqlite3'}")
        B2.metadata.create_all(other)
        local = maker(bind=other, expire_on_commit=False)
        live = dataclasses.replace(mixed_scan(6, 14), baseline_provider_mix=mix)
        with local() as session:
            prompts.append(GPT.persist(session, live, now=OBSERVED).prompt)
        other.dispose()
    assert prompts[0] == prompts[1]
