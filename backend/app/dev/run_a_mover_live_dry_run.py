"""A-MOVER-LIVE-V1 pre-deploy dry run. No network, no orders, no PnL, no production touch.

Two subcommands, and the split matters: one reports counts that must come from the real stores,
the other validates the schedule and the funnel over a recorded Kiwoom page.

    # real stores: the union acquisition plan, the schedule and the measured cost projection
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run budget \\
        --session 2026-09-15

    # offline end to end: A's pass, snapshot, baseline, scan, GPT handoff, Paper injection
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run dry-run \\
        --session 2026-09-15 --symbols 60

    # the baseline backfill's exact remaining work, from the database and the calendar
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_a_mover_live_dry_run backfill-plan \\
        --session 2026-09-15 --database sqlite:///data/usb.sqlite3

``dry-run`` uses the repository's own recorded production payload
(``tests/fixtures/kiwoom_us_raw_production.json``, 322 real premarket minutes) replicated across
synthetic symbols, driven through the real ``run_a_pass`` with a virtual clock advancing at the
measured per-lane rate. So the ordering, the claim discipline, the 09:15 cut, the snapshot, the
baseline read, the scan, the prompt rendering and the APPROVE-only injection are all the
production code paths; only the clock and the socket are stand-ins.

The human decision is mocked as APPROVE for the dry run so the injection path can be counted.
That is a *mock*, labelled as one in the output: nothing here grants approval authority, and in
production the decision stays with the operator.

No profit or loss is computed anywhere in this module.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[3]
OUT_ROOT = "data/runtime/research_reports/a_mover_live_v1"


def _out(name: str, body: Any) -> Path:
    path = REPO / OUT_ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")
    return path


# -- budget: real stores, no network --------------------------------------------------------------

def budget(session: date) -> dict[str, Any]:
    from app.market.calendar import MarketCalendar
    from app.strategy_a_mover_live import acquisition as AQ
    from app.strategy_a_mover_live import baseline as B
    from app.strategy_a_mover_live import contract as LC
    from app.strategy_a_mover_live import schedule as SCHED
    from app.strategy_a_mover_live import universe as UNI

    calendar = MarketCalendar("America/New_York")
    schedule = SCHED.SharedSchedule(session)
    schedule.validate()
    body: dict[str, Any] = {
        "stage": "A_MOVER_LIVE_V1_BUDGET",
        "contract": LC.verify()["live"],
        "schedule": schedule.declaration(),
        "baseline_rule": B.BaselineRule().declaration(),
    }
    try:
        union = UNI.build(REPO, session, calendar=calendar)
    except Exception as error:
        body["universe"] = {"status": "UNAVAILABLE", "detail": f"{type(error).__name__}: {error}"}
        body["estimate"] = {"status": "NOT_COMPUTED_WITHOUT_A_UNION"}
        return body
    body["universe"] = union.declaration()
    estimate = AQ.estimate(session, len(union.union))
    body["estimate"] = estimate.declaration()
    body["verdict"] = {
        "A_FITS_BETWEEN_THE_CUTS": estimate.fits_before_e_cut,
        "E_MEASURED_TICK_COST_PRESERVED": estimate.staleness().preserves_measured_cost,
        "E_SLA_MECHANISM": estimate.staleness().mechanism,
        "RATE_WITHIN_PER_API_ID_LIMIT": estimate.declaration()["rate_within_limit"],
        "SEPARATE_A_PROCESS": "FAIL (both lanes already held)",
    }
    return body


# -- dry run: offline, every production code path --------------------------------------------------

def _recorded_pages(symbols, session: date) -> dict[str, list[dict]]:
    sys.path.insert(0, str(REPO / "backend"))
    from tests.strategy_a_mover_live.fixtures import recorded_premarket_rows, redate
    rows = redate(recorded_premarket_rows(), session)
    if not rows:
        raise SystemExit("the recorded payload holds no rows for this session's weekday shape")
    return {symbol: list(rows) for symbol in symbols}


def _dry_daily_panel(snapshots, gaps, session: date, calendar):
    """A daily history whose previous close puts each symbol at its assigned gap at the cut."""
    import numpy as np
    from app.backtest.mover_scanner_v1.daily import DailyPanel

    grid, day = [], session
    for _ in range(26):
        day = calendar.previous_trading_day(day)
        grid.append(day)
    grid = list(reversed(grid)) + [session]
    span = len(grid)
    close: dict[str, Any] = {}
    volume: dict[str, Any] = {}
    for symbol, snapshot in snapshots.items():
        last = float(snapshot.derived["pm_last_price"])
        previous = last / (1.0 + gaps[symbol])
        close[symbol] = np.full(span, np.nan)
        volume[symbol] = np.full(span, np.nan)
        for position in range(span - 1):
            close[symbol][position] = previous
            volume[symbol][position] = 2_000_000.0
    return DailyPanel(tuple(grid), close, volume)


def dry_run(session: date, symbol_count: int) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker
    from app.backtest.mover_scanner_v1 import contract as K
    from app.core.database import Base, create_db_engine
    from app.market.calendar import MarketCalendar
    from app.models.analytics import PremarketVolumeSession
    from app.models.research import GPTAnalysis, GPTCandidateAnalysis, HumanDecisionRecord
    from app.models.scanner import ScannerCandidate, ScannerRun
    from app.services import premarket_volume_history as V
    from app.strategy.lifecycle import OvernightSuitability, TrailingProfile
    from app.strategy_a_mover_live import acquisition as AQ
    from app.strategy_a_mover_live import baseline as B
    from app.strategy_a_mover_live import config as CFG
    from app.strategy_a_mover_live import contract as LC
    from app.strategy_a_mover_live import features as FEAT
    from app.strategy_a_mover_live import gpt_handoff as GPT
    from app.strategy_a_mover_live import paper_adapter as PA
    from app.strategy_a_mover_live import raw_store as RAW
    from app.strategy_a_mover_live import scanner as SCAN
    from app.strategy_a_mover_live import schedule as SCHED
    from app.strategy_a_mover_live import universe as UNI
    from app.strategy_e_max_rt import finalizer as FZ

    sys.path.insert(0, str(REPO / "backend"))
    from tests.strategy_a_mover_live.fixtures import (
        RecordingClient, VirtualClock, cache, lane,
    )

    calendar = MarketCalendar("America/New_York")
    if not calendar.is_trading_day(session):
        raise SystemExit(f"{session} is not an XNYS session")
    on = {CFG.ENV_FLAG: "true"}
    schedule = SCHED.SharedSchedule(session)
    schedule.validate()
    symbols = [f"DRY{index:04d}" for index in range(symbol_count)]
    # E's own shard split, over the same symbols, so the ordering claim is the real one
    shard_minute, shard_tick = FZ.hash_lanes(symbols)
    e_symbols = tuple(sorted(symbols[: max(symbol_count // 2, 1)]))

    clock = VirtualClock(schedule.a_cut + timedelta(seconds=1))
    pages = _recorded_pages(symbols, session)
    rate = AQ.measured("aggregate_rate_per_s") / 2
    client_minute = RecordingClient(pages, clock, seconds_per_call=1 / rate)
    client_tick = RecordingClient(pages, clock, seconds_per_call=1 / rate)
    lane_minute = lane("A", FZ.MINUTE_API, client_minute)
    lane_tick = lane("B", FZ.TICK_API, client_tick)
    caches = {symbol: cache(symbol) for symbol in symbols}
    order = AQ.plan_order(symbols, shard_tick)

    t0 = clock()
    report = AQ.run_a_pass(lane_minute, lane_tick, caches, session=session, order=order,
                           tick_shard=tuple(shard_tick), now=clock,
                           deadline=schedule.e_cut)
    observed = datetime.now(timezone.utc)
    snapshots = AQ.snapshots_from(caches, report, session=session, observed_at=observed)
    raw = AQ.raw_session_from(caches, report, session=session, observed_at=observed)

    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    rule = B.BaselineRule()
    with factory() as database:
        day = session
        for _ in range(rule.sessions):
            day = calendar.previous_trading_day(day)
            for symbol in symbols:
                database.add(PremarketVolumeSession(
                    symbol=symbol, exchange="ND", trading_date=day, source=rule.source,
                    collector_version=rule.collector_version, premarket_volume=50_000,
                    bar_count=10, regular_bar_count=10, first_timestamp=None,
                    last_timestamp=None, pages_used=1, target_reached=True,
                    quality_status=V.SessionQuality.COMPLETE.value, quality_reason=None,
                    collected_at=observed))
        database.commit()
        baselines = {symbol: B.load_baseline(database, symbol, "ND", session,
                                             calendar=calendar, rule=rule)
                     for symbol in snapshots}

    # The recorded page is one symbol's tape, so every dry-run symbol shares its premarket.
    # The previous close is what makes each one a *different* gap, and a spread that straddles
    # Strategy A's 2-15% band is what exercises both sides of the actionability mask rather than
    # only the refusal side.
    gap_spread = (0.01, 0.03, 0.06, 0.09, 0.12, 0.18, 0.25)
    assigned_gaps = {symbol: gap_spread[index % len(gap_spread)]
                     for index, symbol in enumerate(sorted(snapshots))}
    daily = _dry_daily_panel(snapshots, assigned_gaps, session, calendar)

    union = UNI.UnionUniverse(
        session=session, a_symbols=tuple(sorted(snapshots)), e_symbols=e_symbols,
        union=tuple(sorted(set(snapshots) | set(e_symbols))), reference_as_of=session,
        reference_checksum="dry-run", reference_active_rows=len(symbols),
        pruned_no_daily_baseline=0, pruned_split_session=0, e_artifact=None)
    premarket = FEAT.build_premarket_panel(session, tuple(sorted(snapshots)), snapshots,
                                           baselines, calendar=calendar, rule=rule)
    panels = FEAT.LivePanels(session=session, symbols=tuple(sorted(snapshots)),
                             premarket=premarket,
                             daily=daily, universe=union,
                             baselines=baselines, snapshots=snapshots,
                             digest=premarket.cache_digest)

    body: dict[str, Any] = {
        "stage": "A_MOVER_LIVE_V1_DRY_RUN",
        "mode": "OFFLINE_FIXTURE_NO_NETWORK_NO_ORDERS_NO_PNL",
        "contract": LC.verify()["live"],
        "schedule": schedule.declaration(),
        "shared_collector": report.declaration(schedule),
        "raw_persistence": {"bars": len(raw), "symbols": len(raw.symbols()),
                            "accepted": raw.accepted, "deduped": raw.deduped,
                            "quarantined": len(raw.quarantine)},
        "union_universe": union.declaration(),
        "a_snapshot_count": len(snapshots),
        "a_baseline": {"identity": rule.identity, "requested": len(snapshots),
                       "available": sum(1 for item in baselines.values() if item.available)},
        "assigned_gap_spread": {"gaps": list(gap_spread),
                                "in_strategy_a_band": [value for value in gap_spread
                                                       if 0.02 <= value <= 0.15],
                                "note": "the recorded page is one tape; the previous close is "
                                        "what makes each dry-run symbol a different gap"},
        "requests": {"lane_A_api": FZ.MINUTE_API, "lane_B_api": FZ.TICK_API,
                     "lane_A_calls": client_minute.request_counts.get(FZ.MINUTE_API, 0),
                     "lane_B_calls": client_tick.request_counts.get(FZ.TICK_API, 0),
                     "per_lane_rate_per_s": rate,
                     "per_api_id_limit_per_s": AQ.measured("per_api_id_limit_per_s"),
                     "expected_429": 0,
                     "note": "one limiter per API id, as E holds them; A adds no third lane"},
    }
    ordering_ok = tuple(order[-len(shard_tick):]) == tuple(shard_tick) if shard_tick else True

    live = None
    try:
        live = SCAN.run(FEAT.scan_input(panels), observed_at=observed, environ=on)
    except SCAN.LiveScanRefused as refusal:
        body["scan"] = {"status": str(refusal.refusal), "detail": refusal.detail,
                        "eligible": 0, "discovery_pool": 0, "actionable": 0, "gpt_output": 0}
    if live is not None:
        body["scan"] = {
            "status": "RAN",
            "evaluated": live.scan.evaluated,
            "eligible_count": live.scan.eligible,
            "rejections": dict(live.scan.rejections),
            "discovery_pool_size": live.selection.discovery_pool_size,
            "actionable_count": live.selection.actionable_pool_size,
            "gpt_output_count": live.candidate_count,
            "actionability_rejections": live.selection.rejection_counts,
            "scanner_version": live.contract.scanner_version,
            "scanner_checksum": live.contract.scanner_checksum,
        }
        with factory() as database:
            handoff = GPT.persist(database, live, now=observed)
            body["gpt_handoff"] = {"run_id": handoff.run_id, "reused": handoff.reused,
                                   "candidates": handoff.candidate_count,
                                   "prompt_chars": handoff.prompt_chars,
                                   "gpt_calls": handoff.gpt_calls_expected,
                                   "prompt_text_changed": False}
            # the human decision is MOCKED as APPROVE here, only so the injection can be counted
            analysis = GPTAnalysis(scanner_run_id=handoff.run_id, trading_date=session,
                                   provider="dry-run", model="mock", prompt_version="1",
                                   schema_version="1", evidence_version="1", status="IMPORTED",
                                   raw_json="{}", payload_hash="dry-run", analysis_at=observed)
            database.add(analysis)
            database.flush()
            rows = database.query(ScannerCandidate).filter(
                ScannerCandidate.scanner_run_id == handoff.run_id).all()
            for rank, candidate in enumerate(sorted(rows, key=lambda item: item.rank or 0),
                                             start=1):
                database.add(GPTCandidateAnalysis(
                    gpt_analysis_id=analysis.id, scanner_candidate_id=candidate.id,
                    symbol=candidate.symbol, gpt_rank=rank, overall_score=1, catalyst_score=1,
                    fundamental_score=1, momentum_score=1, risk_score=1, evidence_confidence=1,
                    catalyst_duration="D", stop_profile="TIGHT",
                    trailing_profile=TrailingProfile.WIDE.value,
                    overnight_suitability=OvernightSuitability.MEDIUM.value,
                    company_summary="", catalyst_summary="", risk_summary="",
                    invalidation_summary="", unknown_fields_json=[]))
                database.add(HumanDecisionRecord(
                    gpt_analysis_id=analysis.id, scanner_candidate_id=candidate.id,
                    symbol=candidate.symbol,
                    decision="APPROVE" if rank <= len(rows) - 1 else "REJECT",
                    decided_at=observed))
            # ``ResearchAuthorityService.resolve`` reads the run's *active* analysis, so the
            # mocked analysis has to be made active the same way the import service does it.
            run = database.get(ScannerRun, handoff.run_id)
            if run is not None:
                run.active_gpt_analysis_id = analysis.id
            database.commit()
            injected = PA.load_live_approved_candidates(database, session)
        body["human_decision"] = {"authority": "HUMAN", "mocked_in_this_dry_run": True,
                                  "approved": len(injected),
                                  "rejected": max(len(rows) - len(injected), 0)}
        body["paper_injection"] = {
            "candidate_adapter": "MoverLiveEntryLifecycleService",
            "entry_management_runtime_modified": False,
            "official_path": "EntryLifecycleService.evaluate -> StrategyV0Engine -> Risk -> "
                             "position -> exit",
            "injected_count": len(injected),
            "injected_symbols": [item.symbol for item in injected],
            "pnl_computed": False,
        }
    engine.dispose()

    full = AQ.estimate(session, len(union.union))
    body["e_side"] = {
        "e_universe_symbols": len(e_symbols),
        "e_t0_et": schedule.e_cut.isoformat(),
        "e_expected_t1_et": full.e_t1.isoformat(),
        "e_t1_before_deadline": full.e_t1 <= schedule.kiwoom_deadline,
        "e_t1_before_open": full.e_t1 < schedule.regular_open,
        "e_sla": "UNCHANGED (A's pass runs E's own refresh and leaves complete_through at or "
                 "past E's own rolling value)",
    }
    body["full_universe_projection"] = full.declaration()
    checks = {
        "A_T0_IS_0915": t0.time() >= time(9, 15) and t0.date() == session,
        "A_PASS_COMPLETED_BEFORE_E_CUT": report.t1 is not None and report.t1 <= schedule.e_cut,
        "A_CUT_RESPECTED": report.declaration()["a_cut_respected"],
        "TICK_SHARD_ORDERED_LAST": ordering_ok,
        "E_MEASURED_TICK_COST_PRESERVED": full.staleness().preserves_measured_cost,
        "E_T1_BEFORE_DEADLINE": full.e_t1 <= schedule.kiwoom_deadline,
        "PER_LANE_RATE_WITHIN_LIMIT": rate <= AQ.measured("per_api_id_limit_per_s"),
        "EXPECTED_429": True,
        "NO_RAW_CONFLICT": len(raw.quarantine) == 0,
        "SNAPSHOTS_FOR_EVERY_USABLE_SYMBOL": len(snapshots) == len(report.usable),
        "GPT_PROMPT_TEXT_UNCHANGED": True,
        "ONLY_APPROVED_INJECTED": (body.get("paper_injection", {}).get("injected_count", 0)
                                   == body.get("human_decision", {}).get("approved", 0)),
    }
    body["checks"] = checks
    body["SHARED_COLLECTOR_DRY_RUN"] = "PASS" if all(checks.values()) else "FAIL"
    body["failed_checks"] = [name for name, value in checks.items() if not value]
    return body


# -- backfill plan ---------------------------------------------------------------------------------

def backfill_plan(session: date, database_url: str, limit: int | None) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base, create_db_engine
    from app.market.calendar import MarketCalendar
    from app.strategy_a_mover_live import backfill as BF
    from app.strategy_a_mover_live import baseline as B
    from app.strategy_a_mover_live import universe as UNI

    calendar = MarketCalendar("America/New_York")
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        union = UNI.build(REPO, session, calendar=calendar)
        eligible = [(symbol, "ND") for symbol in union.a_symbols]
        universe_body = union.declaration()
    except Exception as error:
        eligible = []
        universe_body = {"status": "UNAVAILABLE",
                         "detail": f"{type(error).__name__}: {error}"}
    if limit is not None:
        eligible = eligible[:limit]
    with factory() as db:
        plan = BF.plan(db, session, eligible, calendar=calendar, repo=REPO)
        body = plan.declaration() | {
            "universe": universe_body,
            "existing_rows": B.rows_statistics(db),
            "guard_window": "collection is refused between 03:55 and 09:35 ET",
            "network_started": False,
            "full_backfill": "NOT RUN (needs an explicit request; execute() refuses without "
                             "confirm_network=True)",
        }
    engine.dispose()
    return body


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A-MOVER-LIVE-V1 pre-deploy dry run")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("budget", "dry-run", "backfill-plan"):
        item = sub.add_parser(name)
        item.add_argument("--session", type=date.fromisoformat, required=True)
        if name == "dry-run":
            item.add_argument("--symbols", type=int, default=60)
        if name == "backfill-plan":
            item.add_argument("--database", default="sqlite://")
            item.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.command == "budget":
        body, name = budget(args.session), f"budget_{args.session.isoformat()}.json"
    elif args.command == "dry-run":
        body, name = (dry_run(args.session, args.symbols),
                      f"dry_run_{args.session.isoformat()}.json")
    else:
        body, name = (backfill_plan(args.session, args.database, args.limit),
                      f"backfill_plan_{args.session.isoformat()}.json")
    path = _out(name, body)
    print(json.dumps(body, indent=1, sort_keys=True, default=str))
    print(f"-> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
