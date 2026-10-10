"""The UI approval chain lands on the A live run the entry runtime reads.

The failure this covers is not a wrong rule, it is a wrong row. The review chain resolved "the
current run" as the newest COMPLETED run of any source, while the entry runtime resolves the
live-source run of the analysis session - ``previous_trading_day`` of the session it trades. A's live cut completes near 09:27 ET and the legacy
trade-value scanner near 18:01 ET of the same session, so for the rest of that day and the
whole next morning the newest run overall is the legacy one: the prompt carried its id, the
import attached to it, its ``active_gpt_analysis_id`` was set and its HumanDecisions recorded,
and the live run stayed empty. On 2026-10-05..10-09 the live runs 21, 23, 25, 27 and 29 all
had ``active_gpt_analysis_id = None`` while analyses 21 and 22 sat on legacy runs 24 and 26.

So every fixture here seeds **both** runs with the production completion order - live first,
legacy later - and the tests drive the deployed HTTP endpoints the UI calls, not the services
behind them, because the run binding is made by what the prompt endpoint renders and what the
import endpoint accepts.

Nothing here places a real order: the only broker is ``SimBroker`` and the only market data is
a list of in-process bars.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.entry_board import entry_board
from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine, get_db
from app.execution.config import ExecutionConfig
from app.market.calendar import MarketCalendar
from app.main import create_app
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.models.analytics import PaperEntryEvaluation
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.repositories.simulation import SimulationStateRepository
from app.repositories.scanner import ScannerCandidateData, ScannerSnapshotRepository
from app.research.versions import GPT_SCHEMA_VERSION, TOP8_PROMPT_VERSION
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import paper_adapter as PA
from tests.strategy_a_mover_live.fixtures import ET, SESSION

#: The three symbols the controlled test needs: one APPROVE, one REJECT, one with no decision.
SYMBOLS = ("AAA", "BBB", "CCC")
#: ``SESSION`` (2026-09-15, Tuesday) is the session both scanners observed, so it is the
#: analysis session; the entry session that consumes its approvals is the next XNYS one.
ENTRY = MarketCalendar().next_trading_day(SESSION)                # 2026-09-16, Wednesday
#: The regular close the entry session gaps from: the analysis session's own.
PREVIOUS = SESSION
#: Production's own completion order, in the analysis session's own wall clock.
A_CUT_DONE = datetime(2026, 9, 15, 9, 27, tzinfo=ET)
LEGACY_DONE = datetime(2026, 9, 15, 18, 1, tzinfo=ET)
#: The Korean review day between them: 16:00 KST on 09-16 is 03:00 ET of the entry session.
REVIEW_AT = datetime(2026, 9, 16, 3, 0, tzinfo=ET)
OPEN = datetime(2026, 9, 16, 9, 30, tzinfo=ET)
PREVIOUS_CLOSE_AT = datetime(2026, 9, 15, 15, 59, tzinfo=ET)
CAPITAL = Decimal("10000")


# --- the app, the database and the two runs -------------------------------------------------

#: The configuration this file describes, and now the default once the scan is on: the live
#: scan runs *and* entry resolves candidates from it. The run of session D is reviewed through
#: the Korean day that follows the 09:27 ET cut and traded on ``next_trading_day(D)``, so this
#: is the morning-review contract on the live source. The pre-live trade-value source under the
#: same date contract is covered by ``tests/test_morning_approval_contract.py``.
LIVE_ENTRY_AUTHORITY = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "true"}


@pytest.fixture(autouse=True)
def live_source_on(monkeypatch: pytest.MonkeyPatch):
    """Both switches on: the live scan runs and entry is bound to its candidates."""
    for name, value in LIVE_ENTRY_AUTHORITY.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def api(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'chain.sqlite3'}")
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


def candidate_rows(symbols: tuple[str, ...]) -> list[ScannerCandidateData]:
    return [ScannerCandidateData(
        symbol=symbol, rank=rank, is_top8=True, score=float(90 - rank),
        observed_at=A_CUT_DONE, available_at=A_CUT_DONE,
        score_components={"exchange": "NASDAQ", "scanner_version": LC.LIVE_VERSION,
                          "latest_close": 100.0, "latest_volume": 1_000_000,
                          "raw": {"rvol": 3.0, "relative_strength": 0.05, "momentum": 0.04,
                                  "dollar_volume": 100_000_000.0},
                          "normalized": {}, "weighted_contributions": {},
                          "baseline_mode": "KIWOOM_ONLY", "baseline_session_count": 20,
                          "kiwoom_session_count": 20, "massive_session_count": 0})
        for rank, symbol in enumerate(symbols, start=1)]


def seed_run(sessions, *, score_version: str, provider: str, completed_at: datetime,
             symbols: tuple[str, ...] = SYMBOLS, trading_date: date = SESSION) -> int:
    """One COMPLETED run with its Top candidates, written through the deployed repository."""
    with sessions() as session:
        repository = ScannerSnapshotRepository(session)
        run = repository.create_run(
            trading_date=trading_date, started_at=completed_at - timedelta(minutes=2),
            provider=provider, score_version=score_version, status="RUNNING",
            universe_count=1000, excluded_count=900, candidate_count=35,
            top8_count=len(symbols))
        repository.add_candidates(run.id, candidate_rows(symbols))
        repository.complete_run(run.id, completed_at=completed_at, status="COMPLETED")
        session.commit()
        return run.id


@pytest.fixture
def both_runs(api):
    """The production shape: the live run completes at 09:27 ET, the legacy run at 18:01 ET."""
    _, sessions = api
    live = seed_run(sessions, score_version=LC.RUN_SCORE_VERSION, provider=LC.RUN_PROVIDER,
                    completed_at=A_CUT_DONE)
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=LEGACY_DONE)
    return live, legacy


# --- HTTP helpers ---------------------------------------------------------------------------

async def request(app, method: str, path: str, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def gpt_payload(scanner_run_id: int, symbols: tuple[str, ...] = SYMBOLS,
                trading_date: date = SESSION) -> str:
    """The JSON shape the operator pastes back, echoing the prompt's own run id."""
    return json.dumps({
        "schema_version": GPT_SCHEMA_VERSION, "prompt_version": TOP8_PROMPT_VERSION,
        "provider": "OpenAI", "model": "GPT-5.6 Sol",
        "analysis_at": datetime(2026, 9, 15, 13, 40, tzinfo=timezone.utc).isoformat(),
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


async def import_analysis(app, scanner_run_id: int, **kwargs):
    return await request(app, "POST", "/api/v1/research/import",
                         json={"raw_json": gpt_payload(scanner_run_id, **kwargs)})


# --- 1. which run the UI shows --------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_candidate_board_shows_the_live_run_not_the_later_legacy_run(api, both_runs):
    app, _ = api
    live, legacy = both_runs
    body = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert body["run"]["id"] == live and body["run"]["id"] != legacy
    assert body["run"]["score_version"] == LC.RUN_SCORE_VERSION
    assert body["run"]["provider"] == LC.RUN_PROVIDER
    assert [item["symbol"] for item in body["top8"]] == list(SYMBOLS)


@pytest.mark.asyncio
async def test_the_legacy_run_stays_reachable_by_asking_for_its_score_version(api, both_runs):
    app, _ = api
    _, legacy = both_runs
    body = (await request(app, "GET", "/api/v1/scanner/latest?score_version=quant_v0")).json()
    assert body["run"]["id"] == legacy


@pytest.mark.asyncio
async def test_with_the_live_source_off_the_newest_run_overall_is_still_served(
        api, both_runs, monkeypatch):
    """Declining the authority is the pre-live behaviour: the legacy run is the current run.

    ``false``, not an empty value: an unset flag now follows the scan flag, so the authority
    has to be refused in words.
    """
    app, _ = api
    _, legacy = both_runs
    monkeypatch.setenv(CFG.ENV_ENTRY_AUTHORITY_FLAG, "false")
    assert (await request(app, "GET", "/api/v1/scanner/latest")).json()["run"]["id"] == legacy


@pytest.mark.asyncio
async def test_no_live_run_is_reported_as_no_run_rather_than_the_legacy_one(api):
    app, sessions = api
    legacy = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                      completed_at=LEGACY_DONE)
    assert legacy
    assert (await request(app, "GET", "/api/v1/scanner/latest")).status_code == 404
    current = (await request(app, "GET", "/api/v1/research/current")).json()
    assert current["status"] == "NO_SCANNER_RUN" and current["scanner_run_id"] is None


# --- 2 and 3. the prompt and the import carry the live run id -------------------------------

@pytest.mark.asyncio
async def test_the_prompt_is_rendered_for_the_live_run(api, both_runs):
    app, _ = api
    live, legacy = both_runs
    body = (await request(app, "GET", "/api/v1/research/prompt")).json()
    assert body["scanner_run_id"] == live
    assert f"scanner_run_id: {live}" in body["prompt"]
    assert f"scanner_run_id: {legacy}" not in body["prompt"]
    assert f'"score_version": "{LC.RUN_SCORE_VERSION}"' in body["prompt"]


@pytest.mark.asyncio
async def test_importing_the_prompts_json_activates_the_live_run(api, both_runs):
    app, sessions = api
    live, legacy = both_runs
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    response = await import_analysis(app, prompt["scanner_run_id"])
    assert response.status_code == 201
    body = response.json()
    assert body["scanner_run_id"] == live and body["activated"] is True
    assert body["active_analysis_id"] == body["analysis_id"]
    with sessions() as session:
        assert session.get(ScannerRun, live).active_gpt_analysis_id == body["analysis_id"]
        assert session.get(ScannerRun, legacy).active_gpt_analysis_id is None


@pytest.mark.asyncio
async def test_an_import_aimed_at_the_legacy_run_is_refused_and_writes_nothing(api, both_runs):
    app, sessions = api
    live, legacy = both_runs
    response = await import_analysis(app, legacy)
    assert response.status_code >= 400
    assert str(legacy) in response.json()["error"]["message"]
    with sessions() as session:
        assert session.scalars(select(GPTAnalysis)).all() == []
        assert session.get(ScannerRun, legacy).active_gpt_analysis_id is None
        assert session.get(ScannerRun, live).active_gpt_analysis_id is None


# --- 4 and 5. the analysis and the decisions belong to the live run -------------------------

async def approve_reject_and_abstain(app) -> int:
    """Import on the live run, then APPROVE one, REJECT one and leave one undecided."""
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    analysis_id = (await import_analysis(app, prompt["scanner_run_id"])).json()["analysis_id"]
    for symbol, decision in (("AAA", "APPROVE"), ("BBB", "REJECT")):
        response = await request(app, "PUT",
                                 f"/api/v1/research/{analysis_id}/decisions/{symbol}",
                                 json={"decision": decision, "note": None})
        assert response.status_code == 200, response.text
    return analysis_id


@pytest.mark.asyncio
async def test_the_decisions_are_recorded_against_the_live_runs_analysis(api, both_runs):
    app, sessions = api
    live, _ = both_runs
    analysis_id = await approve_reject_and_abstain(app)
    with sessions() as session:
        analysis = session.get(GPTAnalysis, analysis_id)
        assert analysis.scanner_run_id == live
        decisions = {row.symbol: row.decision for row in
                     session.scalars(select(HumanDecisionRecord)
                                     .where(HumanDecisionRecord.gpt_analysis_id == analysis_id))}
        assert decisions == {"AAA": "APPROVE", "BBB": "REJECT"}
        rows = session.scalars(select(GPTCandidateAnalysis).where(
            GPTCandidateAnalysis.gpt_analysis_id == analysis_id)).all()
        runs = {session.get(ScannerCandidate, row.scanner_candidate_id).scanner_run_id
                for row in rows}
        assert runs == {live}


@pytest.mark.asyncio
async def test_the_entry_runtimes_own_loader_returns_the_one_approval(api, both_runs):
    app, sessions = api
    live, _ = both_runs
    await approve_reject_and_abstain(app)
    with sessions() as session:
        approved = PA.load_live_approved_candidates(session, SESSION)
    assert [item.symbol for item in approved] == ["AAA"]
    assert approved[0].scanner_run_id == live
    assert approved[0].exchange == "NASDAQ"


@pytest.mark.asyncio
async def test_research_current_and_adoption_resolve_the_live_chain(api, both_runs):
    app, _ = api
    live, _ = both_runs
    analysis_id = await approve_reject_and_abstain(app)
    current = (await request(app, "GET", "/api/v1/research/current")).json()
    assert current["scanner_run_id"] == live and current["active_analysis_id"] == analysis_id
    assert current["status"] == "READY" and current["approved_count"] == 1
    adoption = (await request(app, "GET", "/api/v1/research/adoption")).json()
    assert adoption["scanner_run_id"] == live and adoption["analysis_id"] == analysis_id


@pytest.mark.asyncio
async def test_the_entry_board_names_both_sessions_and_is_ready_through_the_review_day(
        api, both_runs):
    app, sessions = api
    live, _ = both_runs
    await approve_reject_and_abstain(app)
    served = (await request(app, "GET", "/api/v1/trading/entry-board")).json()
    assert served["scanner_run_id"] == live
    # The run keeps the session it observed; the session that consumes it is the next one.
    assert served["analysis_session_date"] == SESSION.isoformat()
    assert served["entry_session_date"] == ENTRY.isoformat()
    # Served live, the fixture's session is long past, which the board states rather than
    # listing a candidate; the rows are read at a moment the entry session has not closed.
    assert served["status"] == "SCANNER_RUN_OUTDATED"
    # 16:00 KST of the review day. This is the moment the old same-session mapping reported
    # SCANNER_RUN_OUTDATED, because the entry session it named had already passed.
    for as_of in (REVIEW_AT, OPEN + timedelta(minutes=1)):
        with sessions() as session:
            board = entry_board(session, calendar=MarketCalendar(), as_of=as_of,
                                fill_delay_bars=ExecutionConfig().fill_delay_bars)
        assert board["status"] == "READY" and board["scanner_run_id"] == live, as_of
        assert board["analysis_session_date"] == SESSION
        assert board["entry_session_date"] == ENTRY
        assert [item["symbol"] for item in board["candidates"]] == ["AAA"]


# --- 6. the approval reaches a Paper evaluation, with no real order -------------------------

def bar(symbol: str, at: datetime, close: float, session: MarketSession = MarketSession.REGULAR,
        *, open_: float | None = None, volume: int | None = None) -> MinuteBar:
    opened = close if open_ is None else open_
    return MinuteBar(symbol=symbol, timestamp=at, open=opened, high=max(close, opened) + .2,
                     low=min(close, opened) - .2, close=close,
                     volume=(100_000 if session is MarketSession.PREMARKET else 20_000)
                     if volume is None else volume, session=session,
                     observed_at=at + timedelta(minutes=1), available_at=at + timedelta(minutes=1))


class Provider:
    """One symbol's session: a gap that passes, an opening range, then a breakout."""

    def __init__(self, symbol: str = "AAA") -> None:
        self.minutes = [bar(symbol, PREVIOUS_CLOSE_AT, 100.0),
                        bar(symbol, datetime(2026, 9, 16, 8, 0, tzinfo=ET), 105.0,
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
        at = datetime(2026, 9, 15, 16, 0, tzinfo=ET)
        return [DailyBar(symbol=symbol, trading_date=PREVIOUS, open=100, high=101, low=99,
                         close=100, volume=1_000_000, observed_at=at,
                         available_at=at + timedelta(minutes=1)) for symbol in symbols]


@pytest.mark.asyncio
async def test_the_approved_candidate_is_evaluated_on_paper_and_the_others_are_not(api,
                                                                                  both_runs):
    app, sessions = api
    live, _ = both_runs
    await approve_reject_and_abstain(app)
    broker = SimBroker(CAPITAL)
    with sessions() as session:
        account_id = SimulationStateRepository(session).get_account(
            broker_type="SIM", account_key="operator").id
    runtime = SimulationRuntimeContext(broker, account_id, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=LIVE_ENTRY_AUTHORITY)
    provider = Provider()
    candidates = lifecycle.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in candidates] == ["AAA"]
    for minute in (1, 16, 18):
        for candidate in candidates:
            lifecycle.evaluate(candidate, provider, as_of=OPEN + timedelta(minutes=minute))
    lifecycle.finalize_session(ENTRY, now=datetime(2026, 9, 16, 16, 5, tzinfo=ET))
    with sessions() as session:
        rows = session.scalars(select(PaperEntryEvaluation)).all()
        assert [row.symbol for row in rows] == ["AAA"]
        assert rows[0].scanner_run_id == live
        assert rows[0].trading_date == ENTRY
        assert rows[0].analysis_trading_date == SESSION
        # Not merely recorded: the one APPROVE travelled the deployed gate and opened a
        # paper position, so the chain is proven through to a trade and not just to a row.
        assert rows[0].final_status == "TRADED"
        assert rows[0].last_phase == "POSITION_OPEN"
    # The fill is the simulation broker's own: no real order exists anywhere in this test,
    # which has no broker credentials, no network and no provider but the list of bars above.
    assert type(broker) is SimBroker
    assert [position.symbol for position in broker.get_positions()] == ["AAA"]
