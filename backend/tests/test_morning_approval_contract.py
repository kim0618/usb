"""The pre-live source under the morning-review contract: analysis session D, entry D+1.

This is Strategy A's pre-live operating contract and the one the production record is made of.
It is now reachable only by declining the live candidate authority explicitly
(``A_MOVER_LIVE_ENTRY_AUTHORITY=false``), which is what every test here runs under; the live
source under the same date contract is ``tests/test_mover_next_session_authority.py``.
The trade-value scanner ranks after the close, so its ``trading_date`` is the session that has
just finished: ``usb-morning-scan.timer`` fires at 07:00 Asia/Seoul, which is 18:01 ET of
session D, and the run it writes is stamped D. The operator reads it through the Korean day,
imports the GPT analysis and records APPROVE/REJECT, and the entry runtime trades those
approvals on the *next* XNYS session - ``analysis_session_date`` is
``previous_trading_day(entry session)``. Production's own rows show the whole window being
used: analyses 1..22 were every one of them imported on the day after their run's
``trading_date``, between 00:04 and 13:06 UTC.

Two things make this worth a file of its own rather than a line in the existing authority test.

**A live mover run of the same date is present.** From 2026-10-05 the live scan also writes a
run, stamped with the session it trades and completed at 09:27 ET, so ``scanner_runs`` holds
two rows for one date and the question "which run is current" has two answers. Every fixture
here seeds both, in production's completion order, and the tests drive the deployed HTTP
endpoints rather than the services behind them.

**Market qualification is not a second approval.** The user-visible contract is "approve in the
morning, enter that night", and the night half is already in the deployed path: every approved
candidate still travels ``EntryLifecycleService.evaluate`` -> ``StrategyV0Engine`` ->
``RiskEngine``, so the same-day gap mask, the premarket volume ratio and the opening-range
breakout are evaluated against *that session's* market at the open. A morning approval is an
admission to be evaluated, never an entry, and the last test here proves that through to a
position.

Nothing here places a real order: the only broker is ``SimBroker`` and the only market data is
a list of in-process bars.
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
from app.repositories.scanner import ScannerCandidateData, ScannerSnapshotRepository
from app.repositories.simulation import SimulationStateRepository
from app.research import current_run as CR
from app.research.versions import GPT_SCHEMA_VERSION, TOP8_PROMPT_VERSION
from app.services.entry_management_runtime import analysis_session_date
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import paper_adapter as PA

ET = ZoneInfo("America/New_York")
#: One APPROVE, one REJECT, one left undecided.
SYMBOLS = ("AAA", "BBB", "CCC")
#: The session the morning scanner ranks, and the session its approvals are traded on.
ANALYSIS = date(2026, 9, 14)
ENTRY = date(2026, 9, 15)
PREVIOUS = date(2026, 9, 11)
#: Production's own instants. The live cut of the analysis session, then the 07:00 KST scan,
#: then the review, then the next session's open.
LIVE_CUT_DONE = datetime(2026, 9, 14, 9, 27, tzinfo=ET)
MORNING_SCAN_DONE = datetime(2026, 9, 14, 18, 1, tzinfo=ET)      # 07:01 KST on 09-15
REVIEW_AT = datetime(2026, 9, 15, 3, 0, tzinfo=ET)               # 16:00 KST on 09-15
OPEN = datetime(2026, 9, 15, 9, 30, tzinfo=ET)
CAPITAL = Decimal("10000")

#: The **explicit opt-out**: the live scan runs and is recorded, and the operator has declined
#: the live candidate authority, so entry stays on the pre-live trade-value source. Unset, the
#: authority now follows the scan flag, and the live source is reviewed on the same morning
#: schedule - ``tests/test_mover_next_session_authority.py`` is that branch. This file keeps
#: the pre-live branch honest: it must still work, and it must still be the source it names.
MORNING_AUTHORITY = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "false"}


@pytest.fixture(autouse=True)
def morning_authority(monkeypatch: pytest.MonkeyPatch):
    for name, value in MORNING_AUTHORITY.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def api(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'morning.sqlite3'}")
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
            initial_cash=CAPITAL, cash=CAPITAL, created_at=MORNING_SCAN_DONE)
        session.commit()
    yield app, sessions
    engine.dispose()


def candidate_rows(symbols: tuple[str, ...], at: datetime) -> list[ScannerCandidateData]:
    return [ScannerCandidateData(
        symbol=symbol, rank=rank, is_top8=True, score=float(90 - rank),
        observed_at=at, available_at=at,
        score_components={"exchange": "NASDAQ", "latest_close": 100.0,
                          "latest_volume": 1_000_000,
                          "raw": {"rvol": 3.0, "relative_strength": 0.05, "momentum": 0.04,
                                  "dollar_volume": 100_000_000.0},
                          "normalized": {}, "weighted_contributions": {}})
        for rank, symbol in enumerate(symbols, start=1)]


def seed_run(sessions, *, score_version: str, provider: str, completed_at: datetime,
             trading_date: date, symbols: tuple[str, ...] = SYMBOLS) -> int:
    with sessions() as session:
        repository = ScannerSnapshotRepository(session)
        run = repository.create_run(
            trading_date=trading_date, started_at=completed_at - timedelta(minutes=2),
            provider=provider, score_version=score_version, status="RUNNING",
            universe_count=1000, excluded_count=900, candidate_count=len(symbols),
            top8_count=len(symbols))
        repository.add_candidates(run.id, candidate_rows(symbols, completed_at))
        repository.complete_run(run.id, completed_at=completed_at, status="COMPLETED")
        session.commit()
        return run.id


@pytest.fixture
def both_runs(api):
    """Production's shape on 2026-10-06: a live run of the session, then the 07:00 KST scan.

    Both carry ``trading_date = ANALYSIS``, which is the case the predecessor loader has to
    resolve: the live run of session D and the morning run of session D are the same date.
    """
    _, sessions = api
    live = seed_run(sessions, score_version=LC.RUN_SCORE_VERSION, provider=LC.RUN_PROVIDER,
                    completed_at=LIVE_CUT_DONE, trading_date=ANALYSIS)
    morning = seed_run(sessions, score_version="quant_v0", provider="KIWOOM_REAL",
                       completed_at=MORNING_SCAN_DONE, trading_date=ANALYSIS)
    return live, morning


async def request(app, method: str, path: str, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def gpt_payload(scanner_run_id: int, trading_date: date = ANALYSIS,
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


async def import_analysis(app, scanner_run_id: int, **kwargs):
    return await request(app, "POST", "/api/v1/research/import",
                         json={"raw_json": gpt_payload(scanner_run_id, **kwargs)})


# --- 1. the date mapping ---------------------------------------------------------------------

def test_the_entry_session_is_the_next_xnys_session_over_weekends_and_holidays():
    calendar = MarketCalendar()
    # analysis trading_date -> the entry session that consumes it
    expected = {date(2026, 9, 14): date(2026, 9, 15),     # Monday -> Tuesday
                date(2026, 9, 18): date(2026, 9, 21),     # Friday -> Monday
                date(2026, 10, 9): date(2026, 10, 12),    # Friday -> Monday
                date(2026, 11, 25): date(2026, 11, 27),   # Wednesday -> Thanksgiving skipped
                date(2026, 12, 24): date(2026, 12, 28),   # Christmas Day and the weekend
                date(2026, 12, 31): date(2027, 1, 4)}     # New Year's Day and the weekend
    for analysis, entry in expected.items():
        assert calendar.next_trading_day(analysis) == entry, analysis
        # The runtime reads the same mapping in the other direction; it must round-trip, or an
        # entry session would look for an analysis no scan was ever stamped with.
        assert analysis_session_date(calendar, entry) == analysis, entry


def test_the_board_derives_the_same_entry_session_from_either_source(both_runs, api):
    """One rule for both: a run of session D is consumed on ``next_trading_day(D)``.

    The live run used to be read as its own entry session, which is what put the review
    inside the entry window. Both rows here carry ``trading_date = ANALYSIS`` and both are
    reviewed for ``ENTRY``.
    """
    _, sessions = api
    live, morning = both_runs
    calendar = MarketCalendar()
    with sessions() as session:
        assert CR.entry_session_for(session.get(ScannerRun, morning), calendar) == ENTRY
        assert CR.entry_session_for(session.get(ScannerRun, live), calendar) == ENTRY


# --- 2. which run the morning review is pointed at -------------------------------------------

@pytest.mark.asyncio
async def test_the_review_resolves_the_morning_run_not_the_live_run(api, both_runs):
    app, _ = api
    live, morning = both_runs
    body = (await request(app, "GET", "/api/v1/scanner/latest")).json()
    assert body["run"]["id"] == morning and body["run"]["id"] != live
    assert body["run"]["score_version"] == "quant_v0"


@pytest.mark.asyncio
async def test_the_live_run_stays_reachable_by_asking_for_its_score_version(api, both_runs):
    app, _ = api
    live, _ = both_runs
    body = (await request(app, "GET",
                          f"/api/v1/scanner/latest?score_version={LC.RUN_SCORE_VERSION}")).json()
    assert body["run"]["id"] == live


@pytest.mark.asyncio
async def test_a_live_run_alone_is_reported_as_no_run_rather_than_resolved(api):
    """No cross-source fallback in this direction either: it would be the same silence."""
    app, sessions = api
    assert seed_run(sessions, score_version=LC.RUN_SCORE_VERSION, provider=LC.RUN_PROVIDER,
                    completed_at=LIVE_CUT_DONE, trading_date=ANALYSIS)
    assert (await request(app, "GET", "/api/v1/scanner/latest")).status_code == 404
    current = (await request(app, "GET", "/api/v1/research/current")).json()
    assert current["status"] == "NO_SCANNER_RUN" and current["scanner_run_id"] is None


@pytest.mark.asyncio
async def test_the_prompt_is_rendered_for_the_morning_run(api, both_runs):
    app, _ = api
    live, morning = both_runs
    body = (await request(app, "GET", "/api/v1/research/prompt")).json()
    assert body["scanner_run_id"] == morning
    assert f"scanner_run_id: {morning}" in body["prompt"]
    assert f"scanner_run_id: {live}" not in body["prompt"]


@pytest.mark.asyncio
async def test_an_import_aimed_at_the_live_run_is_refused_and_writes_nothing(api, both_runs):
    """The guard is symmetric: under this contract the live run is the unreadable one."""
    app, sessions = api
    live, morning = both_runs
    response = await import_analysis(app, live)
    assert response.status_code >= 400
    assert str(live) in response.json()["error"]["message"]
    with sessions() as session:
        assert session.scalars(select(GPTAnalysis)).all() == []
        assert session.get(ScannerRun, live).active_gpt_analysis_id is None
        assert session.get(ScannerRun, morning).active_gpt_analysis_id is None


# --- 3. the morning review, then that night's entry session ----------------------------------

async def approve_reject_and_abstain(app) -> int:
    """The Korean-day review: import on the morning run, APPROVE one, REJECT one, skip one."""
    prompt = (await request(app, "GET", "/api/v1/research/prompt")).json()
    response = await import_analysis(app, prompt["scanner_run_id"])
    assert response.status_code == 201, response.text
    analysis_id = response.json()["analysis_id"]
    for symbol, decision in (("AAA", "APPROVE"), ("BBB", "REJECT")):
        decided = await request(app, "PUT",
                                f"/api/v1/research/{analysis_id}/decisions/{symbol}",
                                json={"decision": decision, "note": None})
        assert decided.status_code == 200, decided.text
    return analysis_id


@pytest.mark.asyncio
async def test_the_import_activates_the_morning_run(api, both_runs):
    app, sessions = api
    live, morning = both_runs
    analysis_id = await approve_reject_and_abstain(app)
    with sessions() as session:
        assert session.get(GPTAnalysis, analysis_id).scanner_run_id == morning
        assert session.get(ScannerRun, morning).active_gpt_analysis_id == analysis_id
        assert session.get(ScannerRun, live).active_gpt_analysis_id is None
        decisions = {row.symbol: row.decision for row in session.scalars(
            select(HumanDecisionRecord)
            .where(HumanDecisionRecord.gpt_analysis_id == analysis_id))}
    assert decisions == {"AAA": "APPROVE", "BBB": "REJECT"}


@pytest.mark.asyncio
async def test_the_next_entry_sessions_loader_returns_the_one_approval(api, both_runs):
    """The contract's core: the morning's APPROVE is what that night's entry session reads."""
    app, sessions = api
    _, morning = both_runs
    await approve_reject_and_abstain(app)
    runtime = SimulationRuntimeContext(SimBroker(CAPITAL), 1, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=MORNING_AUTHORITY)
    assert type(lifecycle) is not PA.MoverLiveEntryLifecycleService
    assert lifecycle.analysis_session_date(ENTRY) == ANALYSIS
    approved = lifecycle.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in approved] == ["AAA"]
    # Resolved from the morning run even though the live run carries the same trading_date.
    assert approved[0].scanner_run_id == morning
    assert approved[0].exchange == "NASDAQ"
    # An older session's analysis is never a fallback, so the session after it reads nothing.
    assert lifecycle.approved_candidates_for_entry_session(
        MarketCalendar().next_trading_day(ENTRY)) == ()


@pytest.mark.asyncio
async def test_the_entry_board_lists_the_morning_analysis_against_that_nights_session(
        api, both_runs):
    app, sessions = api
    _, morning = both_runs
    await approve_reject_and_abstain(app)
    with sessions() as session:
        # Read at the review moment: 16:00 KST, before the entry session has opened.
        board = entry_board(session, calendar=MarketCalendar(), as_of=REVIEW_AT,
                            fill_delay_bars=ExecutionConfig().fill_delay_bars)
    assert board["scanner_run_id"] == morning
    assert board["analysis_session_date"] == ANALYSIS
    assert board["entry_session_date"] == ENTRY
    assert board["status"] == "READY"
    assert [item["symbol"] for item in board["candidates"]] == ["AAA"]


# --- 4. the configuration boundary -----------------------------------------------------------

def test_the_entry_authority_flag_alone_does_not_bind_entry_to_a_scan_that_is_off():
    asked = {CFG.ENV_FLAG: "", CFG.ENV_ENTRY_AUTHORITY_FLAG: "true"}
    assert CFG.entry_authority(asked) is False
    assert CFG.entry_authority_misconfigured(asked) is True
    # The opt-out this file runs under: declined explicitly, so it is not a misconfiguration.
    assert CFG.entry_authority(MORNING_AUTHORITY) is False
    assert CFG.entry_authority_misconfigured(MORNING_AUTHORITY) is False
    both = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "true"}
    assert CFG.entry_authority(both) is True
    assert CFG.entry_authority_misconfigured(both) is False


@pytest.mark.parametrize("score_version,provider,is_live", [
    (LC.RUN_SCORE_VERSION, LC.RUN_PROVIDER, True),
    # The live filter is two predicates, so a row carrying only one of them is not a live run.
    (LC.RUN_SCORE_VERSION, "KIWOOM_REAL", False),
    ("quant_v0", LC.RUN_PROVIDER, False),
    ("quant_v0", "KIWOOM_REAL", False),
    ("mover_v1.2", "MASSIVE", False),
])
def test_the_two_source_filters_partition_every_row(api, score_version, provider, is_live):
    """No row may be invisible to both resolvers, or an operator could not reach it at all."""
    _, sessions = api
    run_id = seed_run(sessions, score_version=score_version, provider=provider,
                      completed_at=MORNING_SCAN_DONE, trading_date=ANALYSIS)
    with sessions() as session:
        assert CR.is_live_run(session.get(ScannerRun, run_id)) is is_live
        live_authority = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "true"}
        resolved_live = CR.current_run(session, environ=live_authority)
        resolved_morning = CR.current_run(session, environ=MORNING_AUTHORITY)
    # Exactly one of the two resolvers sees it, whichever way it is stamped.
    assert (resolved_live is not None) is is_live
    assert (resolved_morning is not None) is (not is_live)


def test_running_the_live_scan_now_binds_the_entry_authority_to_it():
    """The scan flag alone binds entry to the live source, because that no longer moves the
    review: the live run of session D is consumed on ``next_trading_day(D)`` exactly as the
    trade-value run of session D is. Declining is a false word, not an omission, so deploying
    the date fix cannot leave the authority on the pre-live source by forgetfulness."""
    scan_only = {CFG.ENV_FLAG: "true"}
    assert CFG.enabled(scan_only) is True
    assert CFG.entry_authority(scan_only) is True
    assert CR.live_source_active(scan_only) is True
    assert CFG.entry_authority_misconfigured(scan_only) is False
    # An unparsed value is named rather than guessed at, and declines.
    typo = {CFG.ENV_FLAG: "true", CFG.ENV_ENTRY_AUTHORITY_FLAG: "ture"}
    assert CFG.entry_authority(typo) is False
    assert CFG.entry_authority_unparsed(typo) == "ture"
    assert CFG.entry_authority_unparsed(MORNING_AUTHORITY) is None


# --- 5. the night half: the approval is evaluated against that session's market ---------------

def bar(symbol: str, at: datetime, close: float, session: MarketSession = MarketSession.REGULAR,
        *, open_: float | None = None) -> MinuteBar:
    opened = close if open_ is None else open_
    return MinuteBar(symbol=symbol, timestamp=at, open=opened, high=max(close, opened) + .2,
                     low=min(close, opened) - .2, close=close,
                     volume=100_000 if session is MarketSession.PREMARKET else 20_000,
                     session=session, observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


class Provider:
    """The entry session's own market: a gap that passes, an opening range, a breakout."""

    def __init__(self, symbol: str = "AAA") -> None:
        self.minutes = [bar(symbol, datetime(2026, 9, 14, 15, 59, tzinfo=ET), 100.0),
                        bar(symbol, datetime(2026, 9, 15, 8, 0, tzinfo=ET), 105.0,
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
        at = datetime(2026, 9, 11, 16, 0, tzinfo=ET)
        return [DailyBar(symbol=symbol, trading_date=PREVIOUS, open=100, high=101, low=99,
                         close=100, volume=1_000_000, observed_at=at,
                         available_at=at + timedelta(minutes=1)) for symbol in symbols]


@pytest.mark.asyncio
async def test_the_morning_approval_is_qualified_by_that_nights_market_and_then_traded(
        api, both_runs):
    """One APPROVE, qualified at the open by the deployed gate, through to a position.

    No second approval exists anywhere in this path: the gap mask, the premarket volume ratio
    and the opening-range breakout are read from the entry session's own bars, which is the
    runtime market qualification the contract asks for.
    """
    app, sessions = api
    _, morning = both_runs
    await approve_reject_and_abstain(app)
    broker = SimBroker(CAPITAL)
    with sessions() as session:
        account_id = SimulationStateRepository(session).get_account(
            broker_type="SIM", account_key="operator").id
    runtime = SimulationRuntimeContext(broker, account_id, sessions)
    lifecycle = PA.lifecycle_for(runtime, environ=MORNING_AUTHORITY)
    provider = Provider()
    candidates = lifecycle.approved_candidates_for_entry_session(ENTRY)
    assert [item.symbol for item in candidates] == ["AAA"]
    for minute in (1, 16, 18):
        for candidate in candidates:
            lifecycle.evaluate(candidate, provider, as_of=OPEN + timedelta(minutes=minute))
    lifecycle.finalize_session(ENTRY, now=datetime(2026, 9, 15, 16, 5, tzinfo=ET))
    with sessions() as session:
        rows = session.scalars(select(PaperEntryEvaluation)).all()
    assert [row.symbol for row in rows] == ["AAA"]
    assert rows[0].scanner_run_id == morning
    assert rows[0].trading_date == ENTRY
    assert rows[0].analysis_trading_date == ANALYSIS
    assert rows[0].final_status == "TRADED"
    assert rows[0].last_phase == "POSITION_OPEN"
    # The fill is the simulation broker's own: no real order exists anywhere in this test,
    # which has no broker credentials, no network and no provider but the list of bars above.
    assert type(broker) is SimBroker
    assert [position.symbol for position in broker.get_positions()] == ["AAA"]
