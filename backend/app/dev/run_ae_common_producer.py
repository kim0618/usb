"""A/E common premarket producer: A official via snapshot_0915, E official paper via snapshot_0924.

    PYTHONPATH=backend python -m app.dev.run_ae_common_producer run \
        --universe <E universe dir> --a-repo <A/E operations tree> \
        --snapshots <snapshot root> --out <run root> --rvol-store <E RVOL sqlite>

One app key, two chart lanes (usa06011 minute, usa06010 tick), one process for both strategies'
acquisition (``app.ae_common.producer``). It writes two sealed snapshots under
``<snapshots>/<session>/`` - ``snapshot_0915`` for A's consumer and ``snapshot_0924`` for E's
decision - and its own run record under ``<out>/<session>/``. With ``--paper`` an authorized E
decision runs through E-RUNTIME-R1's paper stage on the V2 official book (SimBroker, exact 09:34
close); no real order path exists. It never writes the A database: A's consumer does.

Startup refuses unless STRATEGY_E_MAX_ENABLED is false, RT2_DRY_RUN=true, NO_ORDER_MODE=true,
BROKER_PROVIDER=simulation and KIWOOM_MODE=market_data_only (E-RT2's own safety checks).
"""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import time
import traceback


def now() -> datetime:
    from app.strategy_e_max_rt.finalizer import ET
    return datetime.now(ET)


def log(text: str) -> None:
    print(f"[{now().strftime('%H:%M:%S')} ET] {text}", flush=True)


def _e_artifact(universe: Path, session, calendar):
    from app.strategy_e_max_rt import universe_build as UB
    path = universe / f"universe_{session.isoformat()}.json" if universe.is_dir() else universe
    if not path.exists():
        return None, f"UNIVERSE_NOT_AVAILABLE: {path.name} missing"
    art = json.loads(path.read_text(encoding="utf-8"))
    usable, why = UB.d_minus_1_identity(art, session, calendar)
    return (art, None) if usable else (None, f"UNIVERSE_NOT_AVAILABLE: {why}")


#: An operator's per-strategy hold, next to ACTIVE_FROM. ``HOLD_E`` keeps E decision-only (no book
#: write) while A runs; A's hold is ``HOLD_A`` on the A consumer unit.
HOLD_E = "HOLD_E"


def run(universe: Path, a_repo: Path, snapshot_root: Path, out_root: Path, rvol_store: Path, *,
        paper: bool = False, e_book_root: Path | None = None, equity: str = "10000",
        control_dir: Path | None = None) -> int:
    from app.ae_common import lanes as LN
    from app.ae_common import producer as P
    from app.ae_common import resources as RS
    from app.core.config import get_settings
    from app.dev import run_e_rt2_dryrun as RT2
    from app.integrations.kiwoom.auth import KiwoomAuthClient
    from app.integrations.kiwoom.rate_limit import KiwoomRateLimits
    from app.market.calendar import MarketCalendar
    from app.strategy_a_mover_live import universe as UNI

    checks = RT2.safety_checks()
    calendar = MarketCalendar("America/New_York")
    session = now().date()
    if calendar.session(session) is None:
        log(f"{session}: not a trading session")
        return 0
    run_dir = out_root / session.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = open(run_dir / "producer.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another common producer holds this session's lock")
    record_path = run_dir / "producer.json"
    if record_path.exists():
        log("producer.json exists for this session; nothing to do")
        return 0
    clock = P.Clock(now=now, sleep=time.sleep, session=session)
    record = {"session": session.isoformat(), "mode": "AE_COMMON_PRODUCER_SHADOW_NO_ORDERS",
              "single_app_key": True, "safety": checks, "pid": os.getpid(), "started_at": now().isoformat(),
              "a_health": [RT2.a_health()], "real_order_requests": 0}
    if now() >= clock.at(P.A_CUT):
        record["status"] = "RESTARTED_AFTER_A_CUT"           # neither cut can be served honestly
        P.write_json(record_path, record)
        log("started after 09:15 ET: no snapshot (A and E fail closed)")
        return 0
    sampler = RS.Sampler(clock=now, interval=30.0)
    sampler.start()
    try:
        art, refusal = _e_artifact(universe, session, calendar)
        try:                                   # A's data authority failing must not stop E's morning
            union = UNI.build(a_repo, session, calendar=calendar)
            a_symbols, a_checksum = tuple(union.a_symbols), union.checksum
        except Exception as error:
            a_symbols, a_checksum = (), ""
            record["a_universe_refusal"] = f"{type(error).__name__}: {error}"
            log(f"A universe unavailable ({record['a_universe_refusal']}); A consumer will fail closed")
        settings = get_settings()
        auth = KiwoomAuthClient(base_url=settings.kiwoom_base_url,
                                app_key=settings.kiwoom_app_key.get_secret_value(),
                                app_secret=settings.kiwoom_app_secret.get_secret_value(),
                                limiter=KiwoomRateLimits().auth)
        lane_minute, lane_tick, clients, limiters = LN.build(base_url=settings.kiwoom_base_url, auth=auth, clock=now)
        exchanges: dict[str, str] = {}
        for ex in ("ND", "NY", "NA"):
            for row in clients[0]._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list",  # noqa: SLF001
                                           max_pages=50):
                exchanges.setdefault(str(row["stk_cd"]).strip(), ex)
        record["kiwoom_auth"] = "OK"
        record["listing_codes"] = len(exchanges)
        documented, documented_error = P.documented_refusals(rvol_store)
        if documented_error:
            record["documented_refusals_error"] = documented_error      # none excluded: stricter, not looser
        inputs = P.Inputs(session=session, exchanges=exchanges, a_symbols=a_symbols,
                          a_union_checksum=a_checksum, e_art=art, e_refusal=refusal,
                          documented_refusals=documented)
        snapshot_dir = snapshot_root / session.isoformat()
        held = control_dir is not None and (control_dir / HOLD_E).exists()
        e_paper = None
        entry_clients: list = []
        if paper and not held and e_book_root is not None and art is not None:
            from app.strategy_e_max_rt import finalizer as FZ
            from app.strategy_e_max_rt import paper as PAPER
            codes = {sym: FZ.kiwoom_code(sym, exchanges) for sym in art["symbols"]}

            def provider_factory():
                # its own client and limiter (3/s), the same app key: the producer's lanes are closed
                client = FZ.ChartLaneClient(base_url=settings.kiwoom_base_url, auth=auth,
                                            rate_limits=KiwoomRateLimits(), max_retries=1)
                entry_clients.append(client)
                return PAPER.ListingMinuteProvider(client, codes, exchanges)
            e_paper = P.EPaper(book_root=e_book_root, provider_factory=provider_factory, calendar=calendar,
                               equity=equity)
        record["e_paper"] = ("ON_OFFICIAL_V2" if e_paper else "HELD_BY_OPERATOR" if held
                             else "OFF" if not paper else "NO_UNIVERSE")

        def on_phase(name: str) -> None:
            sampler.dense(2.0 if name in ("a_sweep", "e_finalization") else 30.0)
            if name in ("a_sweep", "e_finalization", "closed"):
                record["a_health"].append(RT2.a_health())
        morning = P.run_session(inputs, lane_minute=lane_minute, lane_tick=lane_tick, limiters=limiters,
                                clock=clock, snapshot_dir=snapshot_dir, rvol_store=rvol_store, log=log,
                                on_phase=on_phase, e_paper=e_paper)
        record.update(morning.report)
        record["clients"] = {lane.name: client.stats() for lane, client in zip((lane_minute, lane_tick), clients)}
        record["real_order_requests"] = sum(c.order_request_count for c in [*clients, *entry_clients])
        record["entry_exit_requests"] = sum(sum(c.request_counts.values()) for c in entry_clients)
        record["status"] = "COMPLETE"
        P.write_json(run_dir / "e_shadow.json", morning.e_shadow)
        P.write_json(run_dir / "a_parity.json", morning.a_parity)
    except Exception as error:
        record["status"] = "PRODUCER_FAILED"
        record["error"] = f"{type(error).__name__}: {error}"
        record["traceback"] = traceback.format_exc()[-6000:]
        log(f"PRODUCER_FAILED {record['error']}")
    finally:
        sampler.stop()
        record["resources"] = sampler.summary()
        record["finished_at"] = now().isoformat()
        P.write_json(run_dir / "resources.json", {"summary": record["resources"], "samples": sampler.samples})
        P.write_json(record_path, record)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A/E common premarket producer (shadow cutover)")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--universe", type=Path, required=True, help="E universe artifact or directory")
    r.add_argument("--a-repo", type=Path, required=True, help="A/E operations tree A's universe is read from")
    r.add_argument("--snapshots", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--rvol-store", type=Path, required=True)
    r.add_argument("--paper", action="store_true", help="E official paper on the V2 book (SimBroker only)")
    r.add_argument("--e-book-root", type=Path, help="E run root holding paper_state_v2/")
    r.add_argument("--equity", default="10000")
    r.add_argument("--control-dir", type=Path, help="directory of operator hold files (HOLD_E)")
    args = parser.parse_args(argv)
    return run(args.universe, args.a_repo, args.snapshots, args.out, args.rvol_store, paper=args.paper,
               e_book_root=args.e_book_root, equity=args.equity, control_dir=args.control_dir)


if __name__ == "__main__":
    raise SystemExit(main())
