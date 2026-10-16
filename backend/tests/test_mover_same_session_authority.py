"""A-MOVER-LIVE-V1 same-session contract: mover scan D 09:15 ET -> entry evaluation on D.

The mover research contract is "scan at 09:15 ET of the trade session; entry, risk and exit
unchanged". This file pins it end to end, in the operator's own order:

    the live mover scan ranks session D's premarket at the 09:15 ET cut and writes a run
    stamped ``trading_date = D`` (complete about 09:27 ET)
        -> the UI renders that run's prompt, the returned GPT analysis is imported against it
           and a human records APPROVE / REJECT before the 10:30 ET entry deadline
            -> on the same session D the entry runtime resolves that run's APPROVEs and the
               unmodified Strategy V0 evaluates them: premarket gate, OR15, entry from 09:45

A-MOVER-NEXT-SESSION-AUTHORITY-V1 (4b0e7ba) consumed a run of D on ``next_trading_day(D)``.
That moved the strategy to a session whose gap had already been spent and is reverted. What it
also fixed - one current run, of the runtime's own source, for the UI, the import and the
board - is kept and re-tested here. The legacy trade-value source keeps its own next-session
rule, because it ranks after the close.

APPROVE is an admission to be evaluated, never a buy. Nothing here places a real order or
reaches a network: the broker is ``SimBroker``, the market data is a list of in-process bars and
the database is a fresh SQLite file per test.
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

#: One APPROVE, one REJECT, one left undecided.
SYMBOLS = ("AAA", "BBB", "CCC")
#: The session the scan observes and trades: a Thursday.
SESSION = date(2026, 9, 17)
ANALYSIS = SESSION
#: The session the gap is measured from.
PREVIOUS = date(2026, 9, 16)
PREVIOUS_CLOSE_AT = datetime(2026, 9, 16, 15, 59, tzinfo=ET)
#: Production's own instants on D: cut complete 09:27 ET, review before the deadline.
A_CUT_DONE = datetime(2026, 9, 17, 9, 27, tzinfo=ET)        # 22:27 KST
REVIEW_AT = datetime(2026, 9, 17, 9, 40, tzinfo=ET)         # 22:40 KST, after the open
OPEN = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
CLOSE = datetime(2026, 9, 17, 16, 5, tzinfo=ET)
CAPITAL = Decimal("10000")

#: The deployed configuration: the live scan runs and entry resolves its candidates.
#: ``A_MOVER_LIVE_ENTRY_AUTHORITY`` is left unset on purpose - unset follows the scan flag.
LIVE_AUTHORITY = {CFG.ENV_FLAG: "true"}


@pytest.fixture(autouse=True)
def live_authority(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CFG.ENV_FLAG, "true")
    monkeypatch.delenv(CFG.ENV_ENTRY_AUTHORITY_FLAG, raising=False)


@pytest.fixture
def api(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'same_session.sqlite3'}")
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


# --- the date rule, on the real XNYS calendar -------------------------------------------------

def test_a_live_run_is_traded_on_its_own_session_for_every_session_of_the_year(api):
    """Not a sample: every XNYS session of 2026, both ends of the chain.

    ``entry_session_for`` maps a run forward for the board and the API; the runtime maps an
    entry session back to the run it reads. For a live run both are the identity, and for a
    legacy run both are the next/previous session pair the trade-value scanner always had.
    """
    _, sessions = api
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    live = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    day, checked = date(2026, 1, 2), 0
    while day < date(2026, 12, 31):
        if CALENDAR.is_trading_day(day):
            live_run = ScannerRun(trading_date=day, score_version=LC.RUN_SCORE_VERSION,
                                  provider=LC.RUN_PROVIDER)
            legacy_run = ScannerRun(trading_date=day, score_version="quant_v0",
                                    provider="KIWOOM_REAL")
            assert CR.entry_session_for(live_run, CALENDAR) == day, day
            assert live.analysis_session_date(day) == day, day
            entry = CR.entry_session_for(legacy_run, CALENDAR)
            assert entry == CALENDAR.next_trading_day(day), day
            assert analysis_session_date(CALENDAR, entry) == day, day
            checked += 1
        day += timedelta(days=1)
    assert checked > 240, checked


def test_the_mover_runs_own_date_is_never_rewritten(api, mover_run):
    _, sessions = api
    with sessions() as session:
        run = session.get(ScannerRun, mover_run)
        assert run.trading_date == SESSION
        assert CR.is_live_run(run) is True
        assert CR.entry_session_for(run, CALENDAR) == SESSION


def test_the_declared_rule_is_the_same_session():
    assert CFG.LiveSwitch.current(LIVE_AUTHORITY).declaration()["entry_session_rule"] == \
        "run.trading_date (same session)"
    assert PA.MoverLiveEntryLifecycleService.ANALYSIS_SESSION_RULE == "same_session"


# --- the UI and the import resolve the mover run of D ----------------------------------------

@pytest.mark.asyncio
async def test_the_resolver_serves_the_mover_run(api, mover_run):
    app, _ = api
    body = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert body["run"]["id"] == mover_run
    assert body["run"]["score_version"] == LC.RUN_SCORE_VERSION


@pytest.mark.asyncio
async def test_research_current_names_d_as_both_analysis_and_entry_session(api, mover_run):
    """The value the adoption screen renders; no screen derives an entry date of its own."""
    app, _ = api
    await review(app)
    body = (await request(app, "GET", "/api/v1/research/current")).json()
    assert body["scanner_run_id"] == mover_run
    assert body["trading_date"] == SESSION.isoformat()
    assert body["analysis_session_date"] == SESSION.isoformat()
    assert body["entry_session_date"] == SESSION.isoformat()


@pytest.mark.asyncio
async def test_an_explicit_legacy_authority_still_maps_d_to_the_next_session(
        api, monkeypatch: pytest.MonkeyPatch):
    """Declining the live authority binds the trade-value source, which keeps D -> D+1."""
    monkeypatch.setenv(CFG.ENV_ENTRY_AUTHORITY_FLAG, "false")
    app, sessions = api
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=datetime(2026, 9, 17, 18, 1, tzinfo=ET),
                      symbols=("LLL", "MMM"))
    body = (await request(app, "GET", "/api/v1/research/current")).json()
    assert body["scanner_run_id"] == legacy
    assert body["analysis_session_date"] == SESSION.isoformat()
    assert body["entry_session_date"] == date(2026, 9, 18).isoformat()


@pytest.mark.asyncio
async def test_a_legacy_run_is_not_served_and_is_not_imported_against(api, mover_run):
    """The legacy run completes at 18:01 ET, after the mover cut; it is never the current run."""
    app, sessions = api
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=datetime(2026, 9, 17, 18, 1, tzinfo=ET),
                      symbols=("LLL", "MMM"))
    served = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert served["run"]["id"] == mover_run
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    assert prompt["scanner_run_id"] == mover_run
    refused = await request(app, "POST", "/api/v1/research/import",
                            json={"raw_json": gpt_payload(legacy, symbols=("LLL", "MMM"))})
    assert refused.status_code >= 400
    with sessions() as session:
        assert session.scalars(select(GPTAnalysis)).all() == []
        assert session.get(ScannerRun, legacy).active_gpt_analysis_id is None


@pytest.mark.asyncio
async def test_the_board_names_d_as_both_sessions_and_is_ready_in_the_entry_window(
        api, mover_run):
    app, sessions = api
    await review(app)
    for as_of in (REVIEW_AT, OPEN + timedelta(minutes=16), datetime(2026, 9, 17, 10, 30, tzinfo=ET)):
        with sessions() as session:
            board = entry_board(session, calendar=CALENDAR, as_of=as_of,
                                fill_delay_bars=ExecutionConfig().fill_delay_bars)
        assert board["scanner_run_id"] == mover_run, as_of
        assert board["analysis_session_date"] == SESSION, as_of
        assert board["entry_session_date"] == SESSION, as_of
        assert board["status"] == "READY", as_of
        assert [item["symbol"] for item in board["candidates"]] == ["AAA"], as_of
    config = StrategyConfig()
    assert board["thresholds"]["premarket_gap_min_pct"] == str(config.premarket_gap_min_pct)


@pytest.mark.asyncio
async def test_the_board_reports_the_run_outdated_once_its_session_is_over(api, mover_run):
    """Once session D is over in New York the run is history, not a candidate."""
    app, sessions = api
    await review(app)
    next_morning = datetime(2026, 9, 18, 13, 30, tzinfo=ZoneInfo("Asia/Seoul"))  # 00:30 ET 09-18
    with sessions() as session:
        board = entry_board(session, calendar=CALENDAR, as_of=next_morning,
                            fill_delay_bars=ExecutionConfig().fill_delay_bars)
    assert board["status"] == "SCANNER_RUN_OUTDATED"
    assert board["entry_session_date"] == SESSION


# --- the import and the decision belong to the mover run of D --------------------------------

@pytest.mark.asyncio
async def test_the_import_attaches_to_the_mover_run_and_activates_it(api, mover_run):
    app, sessions = api
    analysis_id = await review(app)
    with sessions() as session:
        analysis = session.get(GPTAnalysis, analysis_id)
        assert analysis.scanner_run_id == mover_run
        assert analysis.trading_date == SESSION
        assert session.get(ScannerRun, mover_run).active_gpt_analysis_id == analysis_id
        decisions = {row.symbol: row.decision for row in session.scalars(
            select(HumanDecisionRecord)
            .where(HumanDecisionRecord.gpt_analysis_id == analysis_id))}
    assert decisions == {"AAA": "APPROVE", "BBB": "REJECT"}


@pytest.mark.asyncio
async def test_no_decision_is_recorded_without_the_operator_asking(api, mover_run):
    app, sessions = api
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    imported = await request(app, "POST", "/api/v1/research/import",
                             json={"raw_json": gpt_payload(prompt["scanner_run_id"])})
    assert imported.status_code == 201
    with sessions() as session:
        assert session.scalars(select(HumanDecisionRecord)).all() == []
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert lifecycle.approved_candidates_for_entry_session(SESSION) == ()


# --- the entry runtime reads the mover run of its own session --------------------------------

@pytest.mark.asyncio
async def test_the_entry_session_reads_its_own_mover_run(api, mover_run):
    app, sessions = api
    await review(app)
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert isinstance(lifecycle, PA.MoverLiveEntryLifecycleService)
    assert lifecycle.analysis_session_date(SESSION) == SESSION
    approved = lifecycle.approved_candidates_for_entry_session(SESSION)
    assert [item.symbol for item in approved] == ["AAA"]
    assert approved[0].scanner_run_id == mover_run
    assert approved[0].exchange == "NASDAQ"
    metadata = lifecycle.session_metadata(SESSION)
    assert metadata["entry_session_date"] == SESSION.isoformat()
    assert metadata["analysis_session_date"] == SESSION.isoformat()
    assert metadata["analysis_session_rule"] == "same_session"
    assert metadata["scanner_run_id"] == mover_run


@pytest.mark.asyncio
async def test_the_next_session_does_not_inherit_the_run(api, mover_run):
    """No carry-forward: D+1 has no scan of its own, so it trades nothing from D's run."""
    app, sessions = api
    await review(app)
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_AUTHORITY)
    assert lifecycle.approved_candidates_for_entry_session(CALENDAR.next_trading_day(SESSION)) == ()
    assert lifecycle.approved_candidates_for_entry_session(PREVIOUS) == ()


@pytest.mark.asyncio
async def test_there_is_no_fallback_onto_the_legacy_source(api):
    """A legacy run of D with its own APPROVE chain is not a substitute for a mover scan."""
    app, sessions = api
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=datetime(2026, 9, 16, 18, 1, tzinfo=ET),
                      trading_date=PREVIOUS, symbols=("LLL", "MMM"))
    with sessions() as session:
        run = session.get(ScannerRun, legacy)
        candidates = ScannerSnapshotRepository(session).get_top8(legacy)
        analysis = GPTAnalysis(scanner_run_id=legacy, trading_date=PREVIOUS, provider="gpt",
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
    assert lifecycle.approved_candidates_for_entry_session(SESSION) == ()
    with sessions() as session:
        assert PA.live_run_for(session, SESSION) is None
    assert (await request(app, "GET", "/api/v1/scanner/latest")).status_code == 404


# --- the controlled run: 09:15 cut, review, same-session gate, OR15, SimBroker ---------------

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
    """Session D's own market: the previous close, D's premarket gap, D's range and breakout."""

    def __init__(self, symbol: str = "AAA") -> None:
        self.minutes = [bar(symbol, PREVIOUS_CLOSE_AT, 100.0),
                        bar(symbol, datetime(2026, 9, 17, 8, 0, tzinfo=ET), 105.0,
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
        at = datetime(2026, 9, 16, 16, 0, tzinfo=ET)
        return [DailyBar(symbol=symbol, trading_date=PREVIOUS, open=100, high=101, low=99,
                         close=100, volume=1_000_000, observed_at=at,
                         available_at=at + timedelta(minutes=1)) for symbol in symbols]


@pytest.mark.asyncio
async def test_a_same_session_approval_is_qualified_and_traded_on_d(api, mover_run):
    """End to end: run D 09:27 ET -> import + APPROVE 09:40 ET -> D 09:46+ OR15 -> SimBroker.

    The approval arrives after the open and before the deadline, which is when a same-session
    review happens; the runtime reloads candidates each tick, so it is evaluated from then on.
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
    candidates = lifecycle.approved_candidates_for_entry_session(SESSION)
    assert [item.symbol for item in candidates] == ["AAA"]
    for minute in (11, 16, 18):
        for candidate in candidates:
            lifecycle.evaluate(candidate, provider, as_of=OPEN + timedelta(minutes=minute))
    lifecycle.finalize_session(SESSION, now=CLOSE)
    with sessions() as session:
        rows = session.scalars(select(PaperEntryEvaluation)).all()
        states = session.scalars(select(StrategyStateRecord)).all()
    assert [row.symbol for row in rows] == ["AAA"]
    assert rows[0].scanner_run_id == mover_run
    assert rows[0].trading_date == SESSION
    assert rows[0].analysis_trading_date == SESSION
    assert rows[0].final_status == "TRADED"
    assert rows[0].last_phase == "POSITION_OPEN"
    assert {row.symbol for row in states} == {"AAA"}
    assert {row.trading_date for row in states} == {SESSION}
    assert type(broker) is SimBroker
    assert [position.symbol for position in broker.get_positions()] == ["AAA"]


@pytest.mark.asyncio
async def test_the_review_alone_writes_no_evaluation(api, mover_run):
    app, sessions = api
    await review(app)
    with sessions() as session:
        assert session.scalars(select(PaperEntryEvaluation)).all() == []
        assert session.scalars(select(StrategyStateRecord)).all() == []
