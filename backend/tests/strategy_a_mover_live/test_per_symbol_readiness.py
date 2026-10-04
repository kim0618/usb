"""Per-symbol admission, durable exclusion audit, and forward re-admission."""
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import select

from app.models.scanner import ScannerUniverseInput, ScannerRun
from app.services.mover_scanner_source import DataUnavailable, MoverDataUnavailableError
from app.strategy_a_mover_live import baseline as B, features as F, scanner as S, gpt_handoff as G
from app.strategy_a_mover_live import universe as U
from tests.strategy_a_mover_live.fixtures import SESSION, daily_panel
from tests.strategy_a_mover_live.test_a_live_scanner import baseline_for, snapshot_for, OBSERVED, ON
from tests.strategy_a_mover_live.test_a_live_mixed_baseline import database, store, prior, CAL


def incomplete(symbol, count=19):
    b = baseline_for(symbol, 50_000)
    return replace(b, status=B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS,
                   used_sessions=b.used_sessions[:count], volumes=b.volumes[:count],
                   providers=b.providers[:count], median_volume=None)


def union(symbols):
    return U.UnionUniverse(session=SESSION, a_symbols=tuple(symbols), e_symbols=(),
                           union=tuple(symbols), reference_as_of=date(2026, 7, 1),
                           reference_checksum="offline-reference", reference_active_rows=len(symbols),
                           pruned_no_daily_baseline=0, pruned_split_session=0, e_artifact=None)


def test_actual_scale_partial_universe_is_ready():
    base = baseline_for("READY", 50_000)
    ready = [f"R{i:04}" for i in range(3343)]
    missing = [f"M{i:04}" for i in range(1672)]
    baselines = {s: replace(base, symbol=s) for s in ready}
    audit = F.baseline_readiness(SESSION, ready + missing, baselines)
    assert audit["status"] == "READY"
    assert (audit["universe_total"], audit["baseline_ready_count"],
            audit["baseline_insufficient_count"], audit["scanner_eligible_count"]) == (5015, 3343, 1672, 3343)


def test_ready_participates_insufficient_excluded_and_persisted(tmp_path, monkeypatch, database):
    symbols = ("AAA", "BBB", "CCC")
    snapshots = {s: snapshot_for(s, gap=.05, volume=500_000) for s in symbols}
    baselines = {"AAA": baseline_for("AAA", 50_000), "BBB": incomplete("BBB")}
    monkeypatch.setattr(F, "live_daily_panel", lambda *args, **kwargs: daily_panel(symbols, SESSION))
    panels = F.assemble(tmp_path, SESSION, snapshots, baselines, union(symbols))
    assert panels.symbols == ("AAA",)
    live = S.run(F.scan_input(panels), observed_at=OBSERVED, environ=ON)
    assert live.scan.evaluated == 1 and live.candidate_count == 1
    handoff = G.persist(database, live, now=OBSERVED)
    run = database.get(ScannerRun, handoff.run_id)
    assert run.universe_count == 3 and run.excluded_count == 2
    rows = list(database.scalars(select(ScannerUniverseInput).order_by(ScannerUniverseInput.position)))
    assert [(r.symbol, r.exclusion_reason) for r in rows] == [
        ("AAA", None), ("BBB", "BASELINE_INSUFFICIENT"), ("CCC", "BASELINE_INSUFFICIENT")]
    assert panels.readiness["symbols"][1]["available_session_count"] == 19
    assert G.persist(database, live, now=OBSERVED).reused
    assert len(list(database.scalars(select(ScannerUniverseInput)))) == 3


def test_zero_ready_blocks_without_a_zero_denominator(tmp_path):
    with pytest.raises(MoverDataUnavailableError) as error:
        F.assemble(tmp_path, SESSION, {"AAA": snapshot_for("AAA", gap=.05, volume=500_000)},
                   {"AAA": incomplete("AAA")}, union(("AAA",)))
    assert error.value.reason == DataUnavailable.PREMARKET_BASELINE_TOO_SHORT


def test_missing_snapshot_is_not_a_baseline_failure():
    audit = F.baseline_readiness(SESSION, ("AAA",), {"AAA": baseline_for("AAA", 50_000)}, snapshots={})
    assert audit["baseline_ready_count"] == 1 and audit["scanner_eligible_count"] == 0
    assert audit["symbols"][0]["exclusion_reason"] == "NO_FINALIZED_SNAPSHOT"


def test_19_to_20_forward_is_admitted_next_session(database):
    for day in prior(19):
        store(database, day, 50_000, B.BaselineProvider.MASSIVE)
    database.commit()
    before = B.load_baseline(database, "AAA", "ND", SESSION)
    assert F.baseline_readiness(SESSION, ("AAA",), {"AAA": before})["status"] == "BLOCKED"
    snapshot = snapshot_for("AAA", gap=.05, volume=500_000)
    B.record_forward_observations(database, {"AAA": snapshot}, {"AAA": "ND"}, collected_at=OBSERVED)
    same = B.load_baseline(database, "AAA", "ND", SESSION)
    assert same.baseline_session_count == 19  # own numerator is still excluded
    next_day = CAL.next_trading_day(SESSION)
    after = B.load_baseline(database, "AAA", "ND", next_day)
    audit = F.baseline_readiness(next_day, ("AAA",), {"AAA": after})
    assert audit["status"] == "READY" and after.baseline_session_count == 20
    assert (after.kiwoom_session_count, after.massive_session_count) == (1, 19)


@pytest.mark.parametrize("defect", ["volume", "median", "duplicate_session", "wrong_session"])
def test_integrity_failure_is_not_admitted(defect):
    base = baseline_for("AAA", 50_000)
    changes = {"volume": {"volumes": (float("nan"),) * 20},
               "median": {"median_volume": float("nan")},
               "duplicate_session": {"used_sessions": (base.used_sessions[0],) * 20},
               "wrong_session": {"entry_session_date": date(2026, 9, 16)}}
    bad = replace(base, **changes[defect])
    assert F.baseline_readiness(SESSION, ("AAA",), {"AAA": bad})["status"] == "BLOCKED"


def test_production_runner_to_import_human_and_durable_paper(tmp_path, monkeypatch):
    """Controlled offline dependencies; no fabricated scan/approval in an operating DB."""
    import gzip
    import json
    from datetime import datetime, time, timedelta
    from decimal import Decimal
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from app.core import database as DB, config as C
    from app.dev import run_e_rt2_dryrun as worker
    from app.integrations.kiwoom import auth
    from app.strategy_e_max_rt import finalizer as E, universe_build as UB
    from app.strategy_a_mover_live import integration as I, paper_adapter as PA
    from app.services import mover_scanner_source as M
    from app.broker.sim import SimBroker
    from app.market.fake import FakeMarketDataProvider
    from app.models.analytics import PaperEntryEvaluation
    from app.models.scanner import ScannerCandidate
    from app.repositories.scanner import ScannerSnapshotRepository
    from app.repositories.research import ResearchRepository
    from app.repositories.simulation import SimulationStateRepository
    from app.services.research import GPTImportService, HumanDecisionService
    from app.services.simulation_runtime import SimulationRuntimeContext
    from app.research.prompt import ResearchPromptService
    from tests.strategy_a_mover_live.fixtures import VirtualClock, RecordingClient, minute_rows, ET
    from tests.strategy_a_mover_live.test_a_live_scanner import write_grouped
    from tests.test_research_stage4 import payload

    engine = DB.create_db_engine(f"sqlite:///{tmp_path / 'controlled-paper.sqlite3'}")
    DB.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(DB, "SessionLocal", factory)
    monkeypatch.setattr(M, "LIVE_PREMARKET_FEEDS", {})
    symbols = [f"F{i:03}" for i in range(45)] + ["ONLY19", "MISSING"]
    reference = tmp_path / "data/runtime/strategy_c/raw/tickers/CS_2026-07-01.json.gz"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(gzip.compress(json.dumps({"results": [
        {"ticker": s, "active": True, "composite_figi": s} for s in symbols]}).encode()))
    splits = tmp_path / "data/runtime/strategy_c/raw/splits/splits_fixture.json.gz"
    splits.parent.mkdir(parents=True)
    splits.write_bytes(gzip.compress(b'{"results":[]}'))
    for day in prior(60):
        write_grouped(tmp_path, day, [{"T": s, "c": 10., "v": 5_000_000} for s in symbols + ["SPY"]])
    with factory() as db:
        for symbol in symbols[:-1]:
            for day in prior(19 if symbol == "ONLY19" else 20):
                store(db, day, 50_000, B.BaselineProvider.MASSIVE, symbol=symbol)
        account = SimulationStateRepository(db).create_account(
            broker_type="SIM", account_key="controlled-offline", base_currency="USD",
            initial_cash=Decimal("10000"), cash=Decimal("10000"), created_at=OBSERVED)
        db.commit()
        account_id = account.id
    clock = VirtualClock(datetime.combine(SESSION, time(9, 14, 59), tzinfo=ET))
    pages = {}
    for index, symbol in enumerate(symbols + ["SPY"]):
        rows = []
        for minute in range(240, 565):
            price = 10 + (.3 + index * .002) * (minute - 240) / 314
            rows += minute_rows(SESSION, [minute], price=price, volume=2000 + index * 10)
        pages[symbol] = list(reversed(rows))

    class Client(RecordingClient):
        order_request_count = 0
        def __init__(self, **kwargs):
            super().__init__(pages, clock=clock, seconds_per_call=.25)
        def _collect(self, *args, **kwargs):
            return [{"stk_cd": s} for s in pages] if args[2]["stex_tp"] == "ND" else []

    secret = SimpleNamespace(get_secret_value=lambda: "offline-fixture")
    monkeypatch.setattr(C, "get_settings", lambda: SimpleNamespace(
        broker_provider="simulation", kiwoom_mode="market_data_only", has_kiwoom_credentials=True,
        kiwoom_base_url="https://offline.invalid", kiwoom_app_key=secret, kiwoom_app_secret=secret))
    monkeypatch.setattr(auth, "KiwoomAuthClient", lambda **kwargs: object())
    monkeypatch.setattr(E, "ChartLaneClient", Client)
    monkeypatch.setattr(worker, "now", clock)
    monkeypatch.setattr(worker.time, "sleep", clock.tick)
    monkeypatch.setattr(worker, "a_health", lambda: {"mode": "controlled-offline"})
    original = I.attach_isolated
    def attach(**kwargs):
        assert kwargs["session_factory"] is factory
        return original(**(kwargs | {"repo": tmp_path}))
    monkeypatch.setattr(I, "attach_isolated", attach)
    for key, value in {"A_MOVER_LIVE_ENABLED": "true", "STRATEGY_E_MAX_ENABLED": "false",
                       "RT2_DRY_RUN": "true", "NO_ORDER_MODE": "true"}.items():
        monkeypatch.setenv(key, value)
    artifact = {"format": UB.ARTIFACT_FORMAT, "target_session": str(SESSION),
                "asof_session": str(CAL.previous_trading_day(SESSION)), "symbols": [symbols[0]],
                "close_d_minus_1": {symbols[0]: 10},
                "dollar_volume_d_minus_1": {symbols[0]: 50_000_000}, "spy_close_d_minus_1": 10}
    artifact["digest"] = UB.digest_of(artifact)
    path = tmp_path / "universe.json"
    path.write_text(json.dumps(artifact))
    assert worker.run(path, tmp_path / "out", paper=False, rvol_store_path=tmp_path / "rvol.sqlite3") == 0
    report = json.loads((tmp_path / "out" / str(SESSION) / "run.json").read_text())
    assert report["status"] == "COMPLETE"
    a = report["a_mover_live"]
    assert a["baseline_readiness"]["baseline_ready_count"] == 45
    assert a["baseline_readiness"]["baseline_insufficient_count"] == 2
    assert a["scan"]["discovery_pool_size"] == 35 and a["scan"]["handoff_size"] == 8
    assert len(a["candidates"]) == 8
    assert all(c["scanner_version"] == "A-MOVER-LIVE-V1" for c in a["candidates"])
    with factory() as db:
        run = db.get(ScannerRun, a["handoff"]["run_id"])
        repository = ScannerSnapshotRepository(db)
        candidates = repository.get_top8(run.id)
        prompt = ResearchPromptService(repository).generate_top_for_run(run)
        assert prompt and len(candidates) == 8
        assert a["handoff"]["prompt_chars"] == len(prompt)
        data = payload(run.id, 8)
        data.update(trading_date=str(SESSION), analysis_at=(OBSERVED + timedelta(minutes=1)).isoformat())
        for entry, candidate in zip(data["candidates"], candidates, strict=True):
            entry["ticker"] = candidate.symbol
        research = ResearchRepository(db)
        analysis = GPTImportService(research, repository, clock=lambda: OBSERVED + timedelta(minutes=2)).import_json(json.dumps(data))
        human = HumanDecisionService(research)
        human.decide(analysis.id, candidates[0].symbol, "APPROVE", decided_at=OBSERVED + timedelta(minutes=2))
        human.decide(analysis.id, candidates[1].symbol, "REJECT", decided_at=OBSERVED + timedelta(minutes=2))
        approved = PA.load_live_approved_candidates(db, SESSION)
        assert [c.symbol for c in approved] == [candidates[0].symbol]
        assert all(c.score_components_json["baseline_readiness"]["baseline_insufficient_count"] == 2 for c in candidates)
        assert db.scalar(select(ScannerUniverseInput).where(ScannerUniverseInput.symbol == "ONLY19")).exclusion_reason == "BASELINE_INSUFFICIENT"
    runtime = SimulationRuntimeContext(SimBroker(Decimal("10000")), account_id, factory)
    service = PA.lifecycle_for(runtime, environ=ON)
    injected = service.approved_candidates_for_entry_session(SESSION)
    assert len(injected) == 1
    service.evaluate(injected[0], FakeMarketDataProvider(), as_of=datetime.combine(SESSION, time(9, 31), tzinfo=ET))
    with factory() as db:
        records = list(db.scalars(select(PaperEntryEvaluation)))
        assert len(records) == 1 and records[0].symbol == injected[0].symbol
        assert len(list(db.scalars(select(ScannerCandidate)))) == 8
    assert not runtime.broker.get_positions()
    print({"mode": "CONTROLLED_OFFLINE", "runner": report["status"],
           "baseline_ready": 45, "baseline_insufficient": 2,
           "TOP35": a["scan"]["discovery_pool_size"],
           "actionable": a["scan"]["actionable_pool_size"], "TOP8": len(candidates),
           "GPT_import": "PASS", "Human_APPROVE_only": "PASS",
           "Paper_evaluations_persisted": len(records), "real_orders": 0})
    engine.dispose()
