"""Current ScannerRun authority: no previous-run fallback for GPT, adoption, or entry board."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.api.entry_board import entry_board
from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.market.calendar import MarketCalendar
from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.models.strategy import PremarketDiagnosticRecord, StrategyStateRecord
from app.research.authority import CurrentAuthorityStatus, ResearchAuthorityService

CALENDAR = MarketCalendar("America/New_York")
PREVIOUS_SESSION = date(2026, 9, 11)   # Friday: Analysis #6 era
CURRENT_SESSION = date(2026, 9, 14)    # Monday: the new run, not yet analysed
ENTRY_SESSION = date(2026, 9, 15)      # Tuesday: consumes the 09/14 run
AS_OF = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)   # 10:00 ET on the entry session


@pytest.fixture
def api(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'authority.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    async def override():  # type: ignore[no-untyped-def]
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    return app, sessions


async def get(app, path: str):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


def add_run(db, trading_date: date, symbols: list[str]) -> ScannerRun:  # type: ignore[no-untyped-def]
    completed = datetime.combine(trading_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=23)
    run = ScannerRun(trading_date=trading_date, started_at=completed, completed_at=completed,
                     status="COMPLETED", provider="FAKE", score_version="quant_v0",
                     candidate_count=len(symbols), top8_count=len(symbols))
    db.add(run); db.flush()
    db.add_all([ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=index, is_top8=True, score=1.0,
                                 score_components_json={"exchange": "NASDAQ"}, observed_at=completed,
                                 available_at=completed)
                for index, symbol in enumerate(symbols, start=1)])
    db.flush()
    return run


def add_analysis(db, run: ScannerRun, ranks: dict[str, int], approve: tuple[str, ...] = (),  # type: ignore[no-untyped-def]
                 reject: tuple[str, ...] = ()) -> GPTAnalysis:
    at = run.completed_at + timedelta(hours=1)
    analysis = GPTAnalysis(scanner_run_id=run.id, trading_date=run.trading_date, provider="GPT", model="m",
                           prompt_version="p", schema_version="s", evidence_version="e", analysis_at=at,
                           imported_at=at, status="IMPORTED", raw_json="{}", payload_hash=f"{run.id:064d}")
    db.add(analysis); db.flush()
    run.active_gpt_analysis_id = analysis.id
    quant = {row.symbol: row for row in db.query(ScannerCandidate).filter_by(scanner_run_id=run.id)}
    for symbol, rank in ranks.items():
        db.add(GPTCandidateAnalysis(
            gpt_analysis_id=analysis.id, scanner_candidate_id=quant[symbol].id, symbol=symbol, gpt_rank=rank,
            overall_score=80, catalyst_score=80, fundamental_score=80, momentum_score=80, risk_score=60,
            evidence_confidence=60, catalyst_duration="ONE_TO_TWO_DAYS", stop_profile="NORMAL",
            trailing_profile="NORMAL", overnight_suitability="MEDIUM", company_summary="c",
            catalyst_summary="c", risk_summary="r", invalidation_summary="i", unknown_fields_json=[]))
    for decision, symbols in (("APPROVE", approve), ("REJECT", reject)):
        for symbol in symbols:
            db.add(HumanDecisionRecord(gpt_analysis_id=analysis.id, scanner_candidate_id=quant[symbol].id,
                                       symbol=symbol, decision=decision, note=None, decided_at=at))
    db.flush()
    return analysis


def add_state(db, run: ScannerRun, symbol: str, phase: str, reason: str | None = None,  # type: ignore[no-untyped-def]
              **values: object) -> StrategyStateRecord:
    candidate = db.query(ScannerCandidate).filter_by(scanner_run_id=run.id, symbol=symbol).one()
    row = StrategyStateRecord(symbol=symbol, trading_date=values.pop("trading_date", ENTRY_SESSION),
                              scanner_candidate_id=values.pop("scanner_candidate_id", candidate.id),
                              phase=phase, phase_reason=reason, strategy_version="strategy_v0",
                              trailing_profile="NORMAL", overnight_suitability="MEDIUM", **values)
    db.add(row); db.flush()
    return row


def previous_run_with_analysis_six(db) -> ScannerRun:  # type: ignore[no-untyped-def]
    run = add_run(db, PREVIOUS_SESSION, ["SPCX", "ORCL", "AMD", "AAPL", "META"])
    add_analysis(db, run, {"SPCX": 1, "ORCL": 2, "AMD": 3, "AAPL": 4, "META": 5},
                 approve=("SPCX", "ORCL", "AMD", "AAPL", "META"))
    return run


def board(db, **kwargs):  # type: ignore[no-untyped-def]
    return entry_board(db, calendar=CALENDAR, as_of=kwargs.pop("as_of", AS_OF), fill_delay_bars=1, **kwargs)


# --- 1/2. new run without analysis: no GPT fallback --------------------------------

@pytest.mark.asyncio
async def test_new_run_without_analysis_has_no_current_gpt_analysis(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    with sessions() as db:
        previous_run_with_analysis_six(db)
        current = add_run(db, CURRENT_SESSION, ["GOOGL", "META"]); db.commit(); current_id = current.id
        assert ResearchAuthorityService(db).resolve() is None
        assert ResearchAuthorityService(db).current().status is CurrentAuthorityStatus.NO_ACTIVE_ANALYSIS

    assert (await get(app, "/api/v1/research/latest")).status_code == 404
    assert (await get(app, "/api/v1/research/adoption")).status_code == 404
    authority = (await get(app, "/api/v1/research/current")).json()
    assert authority == {"status": "NO_ACTIVE_ANALYSIS", "scanner_run_id": current_id,
                         "trading_date": "2026-09-14", "completed_at": authority["completed_at"],
                         "active_analysis_id": None, "analysis_at": None, "approved_count": 0}
    research = (await get(app, "/api/v1/dashboard")).json()["research"]
    assert (research["active_analysis_id"], research["scanner_run_id"], research["approved_count"]) == (None, None, 0)


@pytest.mark.asyncio
async def test_previous_run_active_analysis_is_history_only(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    with sessions() as db:
        previous = previous_run_with_analysis_six(db); add_run(db, CURRENT_SESSION, ["GOOGL"]); db.commit()
        previous_id, previous_analysis = previous.id, previous.active_gpt_analysis_id

    history = (await get(app, "/api/v1/research/history")).json()
    assert [(row["id"], row["scanner_run_id"], row["is_active"]) for row in history] == [
        (previous_analysis, previous_id, True)]
    assert history[0]["approved_symbols"] == ["AAPL", "AMD", "META", "ORCL", "SPCX"]
    # An explicit run id still reads that run's own analysis: history stays reachable.
    explicit = (await get(app, f"/api/v1/research/latest?scanner_run_id={previous_id}")).json()
    assert explicit["analysis"]["id"] == previous_analysis


def test_no_completed_run_is_its_own_state(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        assert ResearchAuthorityService(db).current().status is CurrentAuthorityStatus.NO_SCANNER_RUN
        result = board(db)
    assert (result["status"], result["candidates"], result["scanner_run_id"]) == ("NO_SCANNER_RUN", [], None)


# --- 3. analysis without decisions ---------------------------------------------------

@pytest.mark.asyncio
async def test_analysis_without_decision_has_no_adoption_or_entry_candidates(api) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    with sessions() as db:
        previous_run_with_analysis_six(db)
        current = add_run(db, CURRENT_SESSION, ["GOOGL", "META"])
        analysis = add_analysis(db, current, {"GOOGL": 1, "META": 2}); db.commit(); analysis_id = analysis.id
        result = board(db)
    assert (result["status"], result["analysis_id"], result["candidates"]) == ("NO_APPROVALS", analysis_id, [])
    assert (await get(app, "/api/v1/research/current")).json()["status"] == "NO_APPROVALS"
    adoption = (await get(app, "/api/v1/research/adoption")).json()
    assert adoption["analysis_id"] == analysis_id
    assert [item["human_decision"] for item in adoption["items"]] == [None, None]


def test_reject_only_decisions_are_not_entry_candidates(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        current = add_run(db, CURRENT_SESSION, ["GOOGL", "META"])
        add_analysis(db, current, {"GOOGL": 1, "META": 2}, reject=("GOOGL",)); db.commit()
        assert board(db)["status"] == "NO_APPROVALS"


# --- 4. a new ScannerRun retires the previous run's entry candidates -----------------

def test_new_run_removes_previous_trading_candidates(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        previous_run_with_analysis_six(db); db.commit()
        before = board(db, as_of=datetime(2026, 9, 14, 14, tzinfo=timezone.utc))
        assert [row["symbol"] for row in before["candidates"]] == ["SPCX", "ORCL", "AMD", "AAPL", "META"]
        add_run(db, CURRENT_SESSION, ["GOOGL", "META"]); db.commit()
        after = board(db)
    assert (after["status"], after["candidates"], after["analysis_id"]) == ("NO_ACTIVE_ANALYSIS", [], None)
    assert (after["analysis_session_date"], after["entry_session_date"]) == (CURRENT_SESSION, ENTRY_SESSION)


def test_outdated_run_is_not_shown_as_today(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        previous_run_with_analysis_six(db); db.commit()
        # 09/15 with only the 09/11 run: its entry session (09/14) has already passed.
        result = board(db)
        review = board(db, allow_outdated=True)
    assert (result["status"], result["candidates"]) == ("SCANNER_RUN_OUTDATED", [])
    assert result["entry_session_date"] == date(2026, 9, 14)
    assert review["status"] == "READY"


# --- 5. current analysis + decisions ---------------------------------------------------

def test_current_candidates_follow_runtime_order_and_persisted_state(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        previous = previous_run_with_analysis_six(db)
        current = add_run(db, CURRENT_SESSION, ["GOOGL", "META", "AMD", "AAPL", "MSFT"])
        analysis = add_analysis(db, current, {"MSFT": 5, "AAPL": 4, "AMD": 3, "META": 2, "GOOGL": 1},
                                approve=("GOOGL", "META", "AMD", "AAPL", "MSFT"))
        meta = add_state(db, current, "META", "PREMARKET_REJECTED", "GAP_TOO_LOW")
        db.add(PremarketDiagnosticRecord(strategy_state_id=meta.id, minute_bars_count=300, premarket_bars_count=120,
                                         previous_close=Decimal("150.27"), reference_price=Decimal("151.85"),
                                         gap_pct=Decimal("0.0105"), premarket_volume=Decimal("100"),
                                         historical_average_daily_volume=Decimal("10000"),
                                         volume_ratio=Decimal("0.01")))
        add_state(db, current, "AMD", "OPENING_RANGE_BUILDING")
        add_state(db, current, "AAPL", "POSITION_OPEN", entry_price=Decimal("230.10"),
                  initial_stop=Decimal("228.00"), active_stop=Decimal("228.00"),
                  last_market_as_of=datetime(2026, 9, 15, 13, 50, tzinfo=timezone.utc))
        signal_at = datetime(2026, 9, 15, 13, 47, 30, tzinfo=timezone.utc)
        add_state(db, current, "MSFT", "ENTRY_SIGNALLED", entry_price=Decimal("500.5"),
                  initial_stop=Decimal("495"), active_stop=Decimal("495"), last_market_as_of=signal_at)
        # A previous session's row and the previous run's candidates never leak in.
        add_state(db, previous, "SPCX", "POSITION_OPEN", trading_date=CURRENT_SESSION)
        db.commit()
        result = board(db)

    assert (result["status"], result["analysis_id"]) == ("READY", analysis.id)
    assert result["thresholds"]["premarket_gap_min_pct"] == "0.02"
    assert result["thresholds"]["premarket_volume_ratio_min"] == "0.05"
    rows = {row["symbol"]: row for row in result["candidates"]}
    assert [(row["rank"], row["symbol"]) for row in result["candidates"]] == [
        (1, "GOOGL"), (2, "META"), (3, "AMD"), (4, "AAPL"), (5, "MSFT")]
    assert rows["GOOGL"]["state"] is None and rows["GOOGL"]["premarket"] is None
    assert rows["META"]["state"]["phase_reason"] == "GAP_TOO_LOW"
    assert rows["META"]["premarket"]["previous_close"] == "150.27"
    assert rows["META"]["premarket"]["gap_pct"] == "0.0105"
    assert rows["AMD"]["state"]["phase"] == "OPENING_RANGE_BUILDING"
    assert rows["AAPL"]["state"]["entry_price"] == "230.10"
    assert rows["AAPL"]["state"]["signal_at"] is None
    assert rows["MSFT"]["state"]["signal_at"] == signal_at
    assert rows["MSFT"]["state"]["intended_execution_bar_at"] == datetime(2026, 9, 15, 13, 48, tzinfo=timezone.utc)


def test_rank_order_does_not_move_with_state(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        current = add_run(db, CURRENT_SESSION, ["AAA", "BBB", "CCC"])
        add_analysis(db, current, {"AAA": 1, "BBB": 2, "CCC": 3}, approve=("AAA", "BBB", "CCC"))
        db.commit()
        order = [row["symbol"] for row in board(db)["candidates"]]
        add_state(db, current, "AAA", "PREMARKET_REJECTED", "GAP_TOO_LOW")
        add_state(db, current, "CCC", "POSITION_OPEN", entry_price=Decimal("10"))
        db.commit()
        assert [row["symbol"] for row in board(db)["candidates"]] == order == ["AAA", "BBB", "CCC"]


def test_state_owned_by_another_candidate_is_a_conflict(api) -> None:  # type: ignore[no-untyped-def]
    _, sessions = api
    with sessions() as db:
        current = add_run(db, CURRENT_SESSION, ["AAA"])
        add_analysis(db, current, {"AAA": 1}, approve=("AAA",))
        add_state(db, current, "AAA", "POSITION_OPEN", scanner_candidate_id=None)
        db.commit()
        row = board(db)["candidates"][0]
    assert (row["state_conflict"], row["state"], row["premarket"]) == (True, None, None)


@pytest.mark.asyncio
async def test_entry_board_endpoint_is_read_only_and_never_fetches_market_data(api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app, sessions = api
    monkeypatch.setattr("app.market.factory.build_kiwoom_provider", lambda *_a, **_k: pytest.fail(
        "the entry board must not construct a market data provider"))
    today = datetime.now(timezone.utc).astimezone(CALENDAR.timezone).date()
    analysis_date = CALENDAR.previous_trading_day(CALENDAR.next_trading_day(today))
    with sessions() as db:
        run = add_run(db, analysis_date, ["AAA", "BBB"])
        add_analysis(db, run, {"AAA": 2, "BBB": 1}, approve=("AAA", "BBB")); db.commit()
    response = await get(app, "/api/v1/trading/entry-board")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "READY"
    assert [row["symbol"] for row in payload["candidates"]] == ["BBB", "AAA"]
    with sessions() as db:
        assert db.query(StrategyStateRecord).count() == 0
        assert db.query(HumanDecisionRecord).count() == 2
