"""A-MOVER-LIVE-V1 under the restored contract: mover scan D, review D, entry D+1.

This is the contract the file exists to pin, end to end and in the operator's own order:

    the live mover scan ranks session D's premarket at the 09:15 ET cut and writes a run
    stamped ``trading_date = D``
        -> through the Korean day that follows, the UI renders that run's prompt, the returned
           GPT analysis is imported against it and a human records APPROVE / REJECT
            -> on ``next_trading_day(D)`` the entry runtime resolves that run's APPROVEs and
               evaluates them against *that* session's market

The regression it replaces was not a date-arithmetic bug. ``MoverLiveEntryLifecycleService``
overrode ``analysis_session_date`` to the identity, which is arithmetically true of a cut taken
at 09:15 ET of D and operationally false of the workflow: candidates did not exist until 09:27
ET and the entry deadline is 10:30 ET, so the whole research-and-approve step had to happen
between 22:27 and 23:30 KST. Removing the override puts the live source on the rule the
trade-value scanner always had, which is the rule that leaves the Korean day free. The run's
own ``trading_date`` is untouched - it still records the premarket session it observed - and
only the session that consumes it moves.

What this file deliberately does **not** test is any entry, risk or exit rule. APPROVE is an
admission to be evaluated, never a buy: the gap mask, the premarket volume ratio and the
opening-range breakout are read from the entry session's own bars by the unmodified
``EntryLifecycleService.evaluate`` -> ``StrategyV0Engine`` -> ``RiskEngine`` path, and the last
test here follows one APPROVE through it to a position so that the claim is demonstrated
rather than asserted.

Nothing here places a real order, and nothing here reaches a network: the only broker is
``SimBroker``, the only market data is a list of in-process bars, and the only database is a
fresh SQLite file per test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.entry_board import entry_board
from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine, get_db
from app.execution.config import ExecutionConfig
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.models.analytics import PaperEntryEvaluation
from app.models.research import GPTAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerRun
from app.models.strategy import StrategyStateRecord
from app.repositories.scanner import ScannerCandidateData, ScannerSnapshotRepository
from app.repositories.simulation import SimulationStateRepository
from app.research import current_run as CR
from app.research.versions import GPT_SCHEMA_VERSION, TOP8_PROMPT_VERSION
from app.services.entry_management_runtime import analysis_session_date
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.config import StrategyConfig
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import paper_adapter as PA

ET = ZoneInfo("America/New_York")
CALENDAR = MarketCalendar("America/New_York")

#: One APPROVE, one REJECT, one left undecided - section 14's three cases in three symbols.
SYMBOLS = ("AAA", "BBB", "CCC")
#: The analysis session: a Thursday, so the entry session is an ordinary next weekday and the
#: weekend and holiday crossings are left to the mapping test rather than smuggled in here.
ANALYSIS = date(2026, 9, 17)
ENTRY = date(2026, 9, 18)
#: The session the entry session gaps from, which is the analysis session itself.
PREVIOUS_CLOSE_AT = datetime(2026, 9, 17, 15, 59, tzinfo=ET)
#: Production's own instants. The 09:15 ET cut completes at 09:27 ET; the operator reviews
#: through the Korean day after it; the entry session opens the next morning in New York.
A_CUT_DONE = datetime(2026, 9, 17, 9, 27, tzinfo=ET)        # 22:27 KST on 09-17
REVIEW_AT = datetime(2026, 9, 18, 3, 0, tzinfo=ET)          # 16:00 KST on 09-18
OPEN = datetime(2026, 9, 18, 9, 30, tzinfo=ET)
CLOSE = datetime(2026, 9, 18, 16, 5, tzinfo=ET)
CAPITAL = Decimal("10000")

#: The restored configuration, and the default once the scan is on: the live scan runs and the
#: entry runtime resolves its candidates. ``A_MOVER_LIVE_ENTRY_AUTHORITY`` is left unset here
#: on purpose - an operator who deploys the date fix and sets no new variable must get this.
LIVE_AUTHORITY = {CFG.ENV_FLAG: "true"}


@pytest.fixture(autouse=True)
def live_authority(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CFG.ENV_FLAG, "true")
    monkeypatch.delenv(CFG.ENV_ENTRY_AUTHORITY_FLAG, raising=False)


@pytest.fixture
def api(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'next_session.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    async def override():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    with sessions() as session:
        SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CAPITAL, cash=CAPITAL, created_at=A_CUT_DONE)
        session.commit()
    yield app, sessions
    engine.dispose()


# --- seeding: a mover run of the analysis session, and optionally a legacy one --------------

def candidate_rows(symbols: tuple[str, ...], at: datetime) -> list[ScannerCandidateData]:
    return [ScannerCandidateData(
        symbol=symbol, rank=rank, is_top8=True, score=float(90 - rank),
        observed_at=at, available_at=at,
        score_components={"exchange": "NASDAQ", "latest_close": 100.0,
                          "latest_volume": 1_000_000,
                          "baseline_mode": "MIXED_BOOTSTRAP", "baseline_session_count": 20,
                          "kiwoom_session_count": 6, "massive_session_count": 14,
                          "raw": {"rvol": 3.0, "relative_strength": 0.05, "momentum": 0.04,
                                  "dollar_volume": 100_000_000.0},
                          "normalized": {}, "weighted_contributions": {}})
        for rank, symbol in enumerate(symbols, start=1)]


def seed_run(sessions, *, trading_date: date = ANALYSIS, completed_at: datetime = A_CUT_DONE,
             score_version: str = LC.RUN_SCORE_VERSION, provider: str = LC.RUN_PROVIDER,
             symbols: tuple[str, ...] = SYMBOLS) -> int:
    with sessions() as session:
        repository = ScannerSnapshotRepository(session)
        run = repository.create_run(
            trading_date=trading_date, started_at=completed_at - timedelta(minutes=12),
            provider=provider, score_version=score_version, status="RUNNING",
            universe_count=5015, excluded_count=4980, candidate_count=len(symbols),
            top8_count=len(symbols))
        repository.add_candidates(run.id, candidate_rows(symbols, completed_at))
        repository.complete_run(run.id, completed_at=completed_at, status="COMPLETED")
        session.commit()
        return run.id


@pytest.fixture
def mover_run(api) -> int:
    """Section 14 steps 1 and 2: one mover run of session D, carrying its TOP8."""
    _, sessions = api
    return seed_run(sessions)


async def request(app, method: str, path: str, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def gpt_payload(scanner_run_id: int, *, trading_date: date = ANALYSIS,
                symbols: tuple[str, ...] = SYMBOLS) -> str:
    return json.dumps({
        "schema_version": GPT_SCHEMA_VERSION, "prompt_version": TOP8_PROMPT_VERSION,
        "provider": "OpenAI", "model": "GPT-5.6 Sol",
        "analysis_at": REVIEW_AT.astimezone(timezone.utc).isoformat(),
        "trading_date": trading_date.isoformat(), "scanner_run_id": scanner_run_id,
        "candidates": [{
            "ticker": symbol, "gpt_rank": rank, "overall_score": 80 - rank,
            "catalyst_score": 70, "fundamental_score": 60, "momentum_score": 75,
            "risk_score": 65, "catalyst_duration": "ONE_TO_TWO_DAYS", "stop_profile": "NORMAL",
            "trailing_profile": "WIDE", "overnight_suitability": "MEDIUM",
            "company_summary": "회사 요약", "catalyst_summary": "촉매 요약",
            "risk_summary": "위험 요약", "invalidation_summary": "무효화 조건",
            "unknown_fields": [], "sources": [{
                "claim": "catalyst", "url": "https://www.sec.gov/filing", "type": "SEC",
                "title": "Filing", "published_at": None}]}
            for rank, symbol in enumerate(symbols, start=1)]}, ensure_ascii=False)


async def review(app) -> int:
    """Section 14 steps 3-8, through the endpoints the UI calls and in the UI's order.

    The prompt is rendered for whatever run the resolver says is current, and its id is what
    the imported JSON echoes back, so this function is also the binding test: if the resolver
    pointed at another run, the import would attach the analysis to that one.
    """
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    imported = await request(app, "POST", "/api/v1/research/import",
                             json={"raw_json": gpt_payload(prompt["scanner_run_id"])})
    assert imported.status_code == 201, imported.text
    analysis_id = imported.json()["analysis_id"]
    for symbol, decision in (("AAA", "APPROVE"), ("BBB", "REJECT")):
        decided = await request(app, "PUT",
                                f"/api/v1/research/{analysis_id}/decisions/{symbol}",
                                json={"decision": decision, "note": None})
        assert decided.status_code == 200, decided.text
    return analysis_id


# --- section 10. the date mapping, on the real XNYS calendar --------------------------------

#: Every mapping is the exchange calendar's answer, listed so a holiday rule change shows up
#: here as a failure rather than as a silently shifted entry session.
MAPPINGS = {
    date(2026, 9, 14): date(2026, 9, 15),      # Mon -> Tue
    date(2026, 9, 15): date(2026, 9, 16),      # Tue -> Wed
    date(2026, 9, 16): date(2026, 9, 17),      # Wed -> Thu
    date(2026, 9, 17): date(2026, 9, 18),      # Thu -> Fri
    date(2026, 9, 18): date(2026, 9, 21),      # Fri -> Mon, across the weekend
    date(2026, 10, 9): date(2026, 10, 12),     # the production case in the brief
    date(2026, 9, 4): date(2026, 9, 8),        # Fri -> Tue, Labor Day Monday skipped
    date(2026, 1, 15): date(2026, 1, 16),      # Thu -> Fri, before the MLK long weekend
    date(2026, 2, 13): date(2026, 2, 17),      # Fri -> Tue, Presidents Day skipped
    date(2026, 4, 2): date(2026, 4, 6),        # Thu -> Mon, Good Friday skipped
    date(2026, 5, 22): date(2026, 5, 26),      # Fri -> Tue, Memorial Day skipped
    date(2026, 6, 18): date(2026, 6, 22),      # Thu -> Mon, Juneteenth Friday skipped
    date(2026, 7, 2): date(2026, 7, 6),        # Thu -> Mon, Independence Day observed
    date(2026, 11, 25): date(2026, 11, 27),    # Wed -> Fri, Thanksgiving skipped
    date(2026, 12, 24): date(2026, 12, 28),    # Thu -> Mon, Christmas and the weekend
    date(2026, 12, 31): date(2027, 1, 4),      # Thu -> Mon, across the year boundary
    date(2026, 3, 6): date(2026, 3, 9),        # Fri -> Mon, DST begins on the Sunday
    date(2026, 10, 30): date(2026, 11, 2),     # Fri -> Mon, DST ends on the Sunday
}


@pytest.mark.parametrize("analysis,entry", sorted(MAPPINGS.items()))
def test_the_entry_session_is_the_next_xnys_session_and_round_trips(analysis: date,
                                                                   entry: date):
    """Both directions, because the two ends of the chain use opposite ones.

    ``entry_session_for`` maps a run forward for the board and the runtime maps an entry
    session back to the run it must read. If the two disagreed for even one date, an entry
    session would look for an analysis no scan was ever stamped with, and the symptom would be
    a silent zero-candidate session rather than an error.
    """
    assert CALENDAR.is_trading_day(analysis), analysis
    assert CALENDAR.is_trading_day(entry), entry
    assert CALENDAR.next_trading_day(analysis) == entry
    assert analysis_session_date(CALENDAR, entry) == analysis


def test_the_board_and_the_runtime_agree_for_every_session_of_the_year():
    """Not a sample: every XNYS session of 2026 round-trips, so no calendar hole is untested."""
    day, checked = date(2026, 1, 2), 0
    while day < date(2026, 12, 31):
        if CALENDAR.is_trading_day(day):
            assert analysis_session_date(CALENDAR, CALENDAR.next_trading_day(day)) == day, day
            checked += 1
        day += timedelta(days=1)
    assert checked > 240, checked


@pytest.mark.parametrize("analysis,entry", [(date(2026, 3, 6), date(2026, 3, 9)),
                                            (date(2026, 10, 30), date(2026, 11, 2))])
def test_a_dst_boundary_between_the_two_sessions_does_not_move_either(analysis: date,
                                                                     entry: date):
    """The clock changes between the sessions; both still open at 09:30 in local time.

    This is what makes the mapping safe to state in dates rather than in hours: the offset
    moves underneath it, and the exchange calendar's session boundaries move with it.
    """
    before, after = CALENDAR.session(analysis), CALENDAR.session(entry)
    assert before is not None and after is not None
    assert before.market_open.hour == after.market_open.hour == 9
    assert before.market_open.minute == after.market_open.minute == 30
    assert before.market_open.utcoffset() != after.market_open.utcoffset()


def test_the_mover_runs_own_date_is_never_rewritten(api, mover_run):
    """The authority mapping moved; the record did not. The run still names what it observed."""
    _, sessions = api
    with sessions() as session:
        run = session.get(ScannerRun, mover_run)
        assert run.trading_date == ANALYSIS
        assert run.score_version == LC.RUN_SCORE_VERSION
        assert run.provider == LC.RUN_PROVIDER
        assert CR.is_live_run(run) is True
        assert CR.entry_session_for(run, CALENDAR) == ENTRY


# --- section 6. the UI resolves the run whose approvals the next session will read ----------

@pytest.mark.asyncio
async def test_the_resolver_serves_the_mover_run_through_the_whole_review_day(api, mover_run):
    app, _ = api
    body = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert body["run"]["id"] == mover_run
    assert body["run"]["score_version"] == LC.RUN_SCORE_VERSION
    assert len(body["top8"]) == len(SYMBOLS)


@pytest.mark.asyncio
async def test_the_resolver_prefers_the_newest_mover_run_over_an_older_one(api, mover_run):
    """Two completed mover runs: the review is pointed at the one with the later session."""
    app, sessions = api
    older = seed_run(sessions, trading_date=date(2026, 9, 16),
                     completed_at=A_CUT_DONE - timedelta(days=1))
    body = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert body["run"]["id"] == mover_run and body["run"]["id"] != older
    with sessions() as session:
        assert CR.entry_session_for(session.get(ScannerRun, older), CALENDAR) == ANALYSIS


@pytest.mark.asyncio
async def test_a_legacy_run_is_not_served_and_is_not_imported_against(api, mover_run):
    """Section 4's first prohibition, at both ends: the UI and the import guard.

    The legacy run completes at 18:01 ET of the same session, which is *after* the 09:27 ET
    mover cut, so an unfiltered "newest completed run" query would answer with it for the whole
    Korean review day. That is the exact row confusion that emptied 2026-10-05..10-09.
    """
    app, sessions = api
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=datetime(2026, 9, 17, 18, 1, tzinfo=ET),
                      symbols=("LLL", "MMM"))
    served = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert served["run"]["id"] == mover_run
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    assert prompt["scanner_run_id"] == mover_run
    assert f"scanner_run_id: {legacy}" not in prompt["prompt"]
    refused = await request(app, "POST", "/api/v1/research/import",
                            json={"raw_json": gpt_payload(legacy,
                                                          symbols=("LLL", "MMM"))})
    assert refused.status_code >= 400
    assert str(legacy) in refused.json()["error"]["message"]
    with sessions() as session:
        assert session.scalars(select(GPTAnalysis)).all() == []
        assert session.get(ScannerRun, legacy).active_gpt_analysis_id is None


@pytest.mark.asyncio
async def test_the_board_names_the_analysis_session_and_the_entry_session(api, mover_run):
    """Section 6's display contract: both dates, so the operator can read which is which."""
    app, sessions = api
    await review(app)
    with sessions() as session:
        board = entry_board(session, calendar=CALENDAR, as_of=REVIEW_AT,
                            fill_delay_bars=ExecutionConfig().fill_delay_bars)
    assert board["scanner_run_id"] == mover_run
    assert board["analysis_session_date"] == ANALYSIS
    assert board["entry_session_date"] == ENTRY
    assert board["status"] == "READY"
    assert [item["symbol"] for item in board["candidates"]] == ["AAA"]
    # The thresholds the board publishes are the deployed ones, unchanged by this stage.
    config = StrategyConfig()
    assert board["thresholds"] == {"strategy_version": config.version,
                                   "premarket_gap_min_pct": str(config.premarket_gap_min_pct),
                                   "premarket_gap_max_pct": str(config.premarket_gap_max_pct),
                                   "premarket_volume_ratio_min":
                                       str(config.premarket_volume_ratio_min)}


@pytest.mark.asyncio
async def test_the_board_is_ready_across_the_whole_korean_review_day(api, mover_run):
    """The operating window the override had removed, measured in the operator's own clock.

    Under the same-session mapping the board reported ``SCANNER_RUN_OUTDATED`` from midnight
    ET of the review day onward, because the entry session it named had already closed. These
    four instants are 09:00, 14:00, 18:00 and 22:00 KST of the review day.
    """
    app, sessions = api
    await review(app)
    kst = ZoneInfo("Asia/Seoul")
    for hour in (9, 14, 18, 22):
        as_of = datetime(2026, 9, 18, hour, 0, tzinfo=kst)
        with sessions() as session:
            board = entry_board(session, calendar=CALENDAR, as_of=as_of,
                                fill_delay_bars=ExecutionConfig().fill_delay_bars)
        assert board["status"] == "READY", as_of
        assert board["entry_session_date"] == ENTRY, as_of


@pytest.mark.asyncio
async def test_the_board_hands_over_to_the_next_run_at_the_next_cut(api, mover_run):
    """The consequence of section 6, written down: the board is a *review* board.

    At 09:27 ET of the entry session the next mover run completes, and from that instant the
    resolver points at it, because section 6 defines the current run as the most recent
    completed one and its intended entry session. So during the 09:45-10:30 ET entry window
    the board shows the session *after* the one the runtime is trading, while the runtime goes
    on reading the earlier run through ``previous_trading_day``. The two ends are independent
    by design - the runtime never consults the resolver - but an operator watching the board at
    22:45 KST is watching tomorrow's candidates, not tonight's fills.
    """
    app, sessions = api
    await review(app)
    next_cut = datetime(2026, 9, 18, 9, 27, tzinfo=ET)
    next_run = seed_run(sessions, trading_date=ENTRY, completed_at=next_cut,
                        symbols=("DDD", "EEE"))
    with sessions() as session:
        board = entry_board(session, calendar=CALENDAR, as_of=OPEN + timedelta(minutes=20),
                            fill_delay_bars=ExecutionConfig().fill_delay_bars)
    assert board["scanner_run_id"] == next_run
    assert board["analysis_session_date"] == ENTRY
    assert board["entry_session_date"] == CALENDAR.next_trading_day(ENTRY)
    # It has no analysis yet, so it is reported as such rather than listing anything.
    assert board["status"] == "NO_ACTIVE_ANALYSIS"
    # Meanwhile the runtime still reads the earlier run for the session it is trading.
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    approved = lifecycle.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in approved] == ["AAA"]
    assert approved[0].scanner_run_id == mover_run


# --- sections 7 and 8. the import and the decision belong to that run ----------------------

@pytest.mark.asyncio
async def test_the_import_attaches_to_the_mover_run_and_activates_it(api, mover_run):
    app, sessions = api
    analysis_id = await review(app)
    with sessions() as session:
        analysis = session.get(GPTAnalysis, analysis_id)
        assert analysis.scanner_run_id == mover_run
        assert analysis.trading_date == ANALYSIS
        run = session.get(ScannerRun, mover_run)
        assert run.active_gpt_analysis_id == analysis_id
        decisions = {row.symbol: row.decision for row in session.scalars(
            select(HumanDecisionRecord)
            .where(HumanDecisionRecord.gpt_analysis_id == analysis_id))}
    # One APPROVE, one REJECT, and no row at all for the third: abstention is not a decision.
    assert decisions == {"AAA": "APPROVE", "BBB": "REJECT"}


@pytest.mark.asyncio
async def test_no_decision_is_recorded_without_the_operator_asking(api, mover_run):
    """Section 4: no automatic APPROVE. Importing an analysis decides nothing by itself."""
    app, sessions = api
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    imported = await request(app, "POST", "/api/v1/research/import",
                             json={"raw_json": gpt_payload(prompt["scanner_run_id"])})
    assert imported.status_code == 201
    with sessions() as session:
        assert session.scalars(select(HumanDecisionRecord)).all() == []
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert lifecycle.approved_candidates_for_entry_session(ENTRY) == ()


# --- section 9. the entry session resolves its predecessor's mover run ----------------------

@pytest.mark.asyncio
async def test_the_entry_session_reads_its_predecessors_mover_run(api, mover_run):
    """The contract's core assertion, and the one the override broke."""
    app, sessions = api
    await review(app)
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert isinstance(lifecycle, PA.MoverLiveEntryLifecycleService)
    assert lifecycle.analysis_session_date(ENTRY) == ANALYSIS
    approved = lifecycle.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in approved] == ["AAA"]
    assert approved[0].scanner_run_id == mover_run
    assert approved[0].exchange == "NASDAQ"
    metadata = lifecycle.session_metadata(ENTRY)
    assert metadata["entry_session_date"] == ENTRY.isoformat()
    assert metadata["analysis_session_date"] == ANALYSIS.isoformat()
    assert metadata["analysis_session_rule"] == "previous_trading_day"
    assert metadata["scanner_run_id"] == mover_run
    assert metadata["baseline_mode"] == "MIXED_BOOTSTRAP"


@pytest.mark.asyncio
async def test_the_analysis_session_itself_resolves_nothing(api, mover_run):
    """The old mapping's session is now empty, which is the regression stated as a test.

    Entering on D would need a mover run of ``previous_trading_day(D)``, and the fixture has
    none, so the session the override used to trade resolves zero candidates.
    """
    app, sessions = api
    await review(app)
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert lifecycle.approved_candidates_for_entry_session(ANALYSIS) == ()
    # And the session after the entry session is not served by it either: an older analysis is
    # never carried forward, so a day with no scan of its own trades nothing.
    assert lifecycle.approved_candidates_for_entry_session(
        CALENDAR.next_trading_day(ENTRY)) == ()


@pytest.mark.asyncio
async def test_there_is_no_fallback_onto_the_legacy_source(api):
    """Section 4 and section 9: a legacy run with approvals is not a substitute for a scan.

    The legacy run here is stamped with the analysis session and carries a live APPROVE chain
    of its own. With the mover authority bound, the entry session reads none of it and trades
    nothing, which is the refusal the brief asks for rather than a quiet substitution.
    """
    app, sessions = api
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=datetime(2026, 9, 17, 18, 1, tzinfo=ET),
                      symbols=("LLL", "MMM"))
    # Activate it directly: the HTTP guard would refuse this import, which is the point - even
    # a chain that exists in the database is not reachable from the mover authority.
    with sessions() as session:
        run = session.get(ScannerRun, legacy)
        candidates = ScannerSnapshotRepository(session).get_top8(legacy)
        analysis = GPTAnalysis(scanner_run_id=legacy, trading_date=ANALYSIS, provider="gpt",
                               model="m", prompt_version="1", schema_version="1",
                               evidence_version="1", status="IMPORTED", raw_json="{}",
                               payload_hash="legacy-chain", analysis_at=REVIEW_AT)
        session.add(analysis)
        session.flush()
        session.add(HumanDecisionRecord(gpt_analysis_id=analysis.id,
                                        scanner_candidate_id=candidates[0].id,
                                        symbol=candidates[0].symbol, decision="APPROVE",
                                        decided_at=REVIEW_AT))
        run.active_gpt_analysis_id = analysis.id
        session.commit()
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert lifecycle.approved_candidates_for_entry_session(ENTRY) == ()
    with sessions() as session:
        # The reason is named: there is no mover run of the analysis session to resolve.
        assert PA.live_run_for(session, ANALYSIS) is None
    # The UI is refused in the same words rather than shown the legacy row.
    assert (await request(app, "GET", "/api/v1/scanner/latest")).status_code == 404


# --- sections 12 and 14. the controlled run, through the gate to a paper position ----------

def bar(symbol: str, at: datetime, close: float,
        session: MarketSession = MarketSession.REGULAR, *,
        open_: float | None = None) -> MinuteBar:
    opened = close if open_ is None else open_
    return MinuteBar(symbol=symbol, timestamp=at, open=opened, high=max(close, opened) + .2,
                     low=min(close, opened) - .2, close=close,
                     volume=100_000 if session is MarketSession.PREMARKET else 20_000,
                     session=session, observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


class Provider:
    """The *entry* session's own market: the previous close, a gap, a range, a breakout.

    Every bar is dated to the entry session except the previous regular close, which is the
    analysis session's final minute. That is the whole point of the contract being tested: the
    approval came from the analysis session's premarket and the qualification is measured
    against the market of the session that trades it.
    """

    def __init__(self, symbol: str = "AAA") -> None:
        self.minutes = [bar(symbol, PREVIOUS_CLOSE_AT, 100.0),
                        bar(symbol, datetime(2026, 9, 18, 8, 0, tzinfo=ET), 105.0,
                            MarketSession.PREMARKET)]
        self.minutes += [bar(symbol, OPEN + timedelta(minutes=index), 100.0)
                         for index in range(15)]
        self.minutes += [bar(symbol, OPEN + timedelta(minutes=15), 102.0),
                         bar(symbol, OPEN + timedelta(minutes=16), 102.1),
                         bar(symbol, OPEN + timedelta(minutes=17), 102.2, open_=102.0)]

    def get_minute_bars(self, symbols, start=None, end=None, session=None):
        return [item for item in self.minutes if item.symbol in set(symbols)
                and (start is None or item.timestamp >= start)
                and (end is None or item.timestamp <= end)
                and (session is None or item.session is session)]

    def get_daily_bars(self, symbols, start=None, end=None):
        at = datetime(2026, 9, 17, 16, 0, tzinfo=ET)
        return [DailyBar(symbol=symbol, trading_date=ANALYSIS, open=100, high=101, low=99,
                         close=100, volume=1_000_000, observed_at=at,
                         available_at=at + timedelta(minutes=1)) for symbol in symbols]


@pytest.mark.asyncio
async def test_the_approval_is_qualified_by_the_entry_sessions_market_and_then_traded(
        api, mover_run):
    """Section 14 end to end, and section 15's claim demonstrated: APPROVE is not BUY.

    One APPROVE travels the unmodified deployed path - premarket gap mask, premarket volume
    ratio, 15-minute opening range, breakout - against the *entry* session's bars, and opens a
    position on ``SimBroker``. The REJECT and the undecided symbol never reach it, so no state
    row exists for either.
    """
    app, sessions = api
    await review(app)
    broker = SimBroker(CAPITAL)
    with sessions() as session:
        account_id = SimulationStateRepository(session).get_account(
            broker_type="SIM", account_key="operator").id
    runtime = SimulationRuntimeContext(broker, account_id, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    provider = Provider()
    candidates = lifecycle.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in candidates] == ["AAA"]
    for minute in (1, 16, 18):
        for candidate in candidates:
            lifecycle.evaluate(candidate, provider, as_of=OPEN + timedelta(minutes=minute))
    lifecycle.finalize_session(ENTRY, now=CLOSE)
    with sessions() as session:
        rows = session.scalars(select(PaperEntryEvaluation)).all()
        states = session.scalars(select(StrategyStateRecord)).all()
    assert [row.symbol for row in rows] == ["AAA"]
    assert rows[0].scanner_run_id == mover_run
    assert rows[0].trading_date == ENTRY
    assert rows[0].analysis_trading_date == ANALYSIS
    assert rows[0].final_status == "TRADED"
    assert rows[0].last_phase == "POSITION_OPEN"
    # Only the approved symbol has a session at all, and only on the entry session's date.
    assert {row.symbol for row in states} == {"AAA"}
    assert {row.trading_date for row in states} == {ENTRY}
    # The fill is the simulation broker's own: no real order exists anywhere in this test,
    # which has no broker credentials, no network and no provider but the list of bars above.
    assert type(broker) is SimBroker
    assert [position.symbol for position in broker.get_positions()] == ["AAA"]


@pytest.mark.asyncio
async def test_no_row_is_written_for_a_session_before_the_entry_session(api, mover_run):
    """Section 4 and 12: nothing is backfilled. The review writes no evaluation at all."""
    app, sessions = api
    await review(app)
    with sessions() as session:
        assert session.scalars(select(PaperEntryEvaluation)).all() == []
        assert session.scalars(select(StrategyStateRecord)).all() == []
