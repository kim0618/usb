"""E-RT2 live capacity dry run: one app key, full canonical universe, 09:24-cutoff finalization by 09:30.

    # local: build the canonical-universe artifact from the US-B common store (Drive)
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_e_rt2_dryrun build-universe --session 2026-09-22 --out u.json
    # server worker (systemd): read-only Kiwoom REST lanes usa06011 + usa06010
    PYTHONPATH=backend python -m app.dev.run_e_rt2_dryrun run --universe u.json --out <run dir>

CAPACITY DRY RUN ONLY: no order, no SimBroker, no E enable, no return; no WebSocket is opened.
Startup refuses unless STRATEGY_E_MAX_ENABLED is false, RT2_DRY_RUN=true, NO_ORDER_MODE=true,
BROKER_PROVIDER=simulation and KIWOOM_MODE=market_data_only. Kiwoom calls stop at 09:29:45 ET,
before Strategy A's first Kiwoom call at the 09:30 open. One run per session (fcntl lock + marker);
a start after 09:25 ET records RESTARTED_AFTER_CUTOFF and makes no decision.

E-RUNTIME-R1 (2026-10-16):

* nothing of Strategy A runs in this process. A's mover cut has its own worker
  (``app.dev.run_a_mover_live``); the two share the app key only through the fixed ET windows of
  ``app.integrations.kiwoom.lane_share``, so E's finalization starts at 09:25:00 whatever A does;
* the paper stage runs only when the decision is authorized: sealed before 09:30:00, every
  canonical symbol classified (an unexpected STALE blocks; a documented source refusal does not),
  no unexpected RVOL gap and a complete RVOL bootstrap. Otherwise the session is recorded as
  NO_DECISION with its reason and nothing is executed - a partial universe never trades;
* books are ``paper_state_v2/<OFFICIAL_KIWOOM_PAPER|SHADOW_VALIDATION>`` under execution
  ``e_cost_v2_exact_0934_close`` (exit at the exact 09:34 close). V0/V1/PROVISIONAL books are
  historical evidence and are never opened for writing.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import threading
import time

import numpy as np

from app.strategy_e_max_rt import availability as AV
from app.strategy_e_max_rt.rvol_store import RvolStore

RVOL_STORE_DEFAULT = "data/runtime/strategy_e_max/rvol/kiwoom_premarket.sqlite3"


def now():
    from app.strategy_e_max_rt.finalizer import ET
    return datetime.now(ET)


def log(text):
    print(f"[{now().strftime('%H:%M:%S')} ET] {text}", flush=True)


# -- local: canonical universe artifact -------------------------------------------------------------

def build_universe(session: date, out: Path) -> None:
    from app.backtest.strategy_c_selection.panel import SplitEvent
    from app.backtest.workspace.discovery import resolve_workspace_root
    from app.market.calendar import MarketCalendar
    from app.strategy_e_max_forward import forward_daily as FD, storage as ST
    cal = MarketCalendar("America/New_York")
    prev = cal.previous_trading_day(session)
    root = resolve_workspace_root(None)
    splits_path = ST.splits_asof_path(root, prev)
    body = json.loads(gzip.decompress(splits_path.read_bytes()))
    events = tuple(SplitEvent(r["ticker"], date.fromisoformat(r["execution_date"]), float(r["split_from"]),
                              float(r["split_to"])) for r in body["results"] if r["split_from"] != r["split_to"])
    universe, missing = FD.eligible_universe(FD.load_base(root), root, session, cal, forward_splits=events)
    if universe is None:
        raise SystemExit(f"inputs missing: {missing}")
    grouped_path = ST.grouped_path(root, prev)
    grouped = json.loads(gzip.decompress(grouped_path.read_bytes()))["body"]["results"]
    rows = {r["T"]: r for r in grouped}
    payload = {"format": "e-rt2-canonical-universe-v1", "session": session.isoformat(), "d_minus_1": prev.isoformat(),
               "rule": "E0 daily eligibility via app.strategy_e_v1_1.universe.daily_eligibility (forward_daily)",
               "inputs": {"grouped_daily_d_minus_1_sha256": ST.sha256_file(grouped_path),
                          "splits_asof_d_minus_1_sha256": ST.sha256_file(splits_path),
                          "note": "split executions on the session itself are not in this list (provisional)"},
               "symbols": list(universe),
               "close_d_minus_1": {s: float(rows[s]["c"]) for s in universe},
               "dollar_volume_d_minus_1": {s: float(rows[s]["c"]) * float(rows[s]["v"]) for s in universe},
               "spy_close_d_minus_1": float(rows["SPY"]["c"])}
    payload["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    out.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    print(f"universe {len(universe)} digest {payload['digest']} -> {out}")


# -- server worker ----------------------------------------------------------------------------------

def safety_checks() -> dict:
    from app.core.config import get_settings
    s = get_settings()
    checks = {"STRATEGY_E_MAX_ENABLED_false": os.environ.get("STRATEGY_E_MAX_ENABLED", "false").strip().lower()
              in {"", "0", "false", "no"},
              "RT2_DRY_RUN_true": os.environ.get("RT2_DRY_RUN") == "true",
              "NO_ORDER_MODE_true": os.environ.get("NO_ORDER_MODE") == "true",
              "broker_provider_simulation": s.broker_provider == "simulation",
              "kiwoom_mode_market_data_only": s.kiwoom_mode == "market_data_only",
              "kiwoom_credentials_present": bool(s.has_kiwoom_credentials)}
    if not all(checks.values()):
        raise SystemExit(f"E-RT2 safety check failed: {checks}")
    return checks


def a_health() -> dict:
    active = subprocess.run(["systemctl", "is-active", "usb-backend"], capture_output=True, text=True).stdout.strip()
    try:
        import httpx
        health = httpx.get("http://127.0.0.1:8000/health", timeout=5).json().get("status")
    except Exception as error:     # the check never fails the worker
        health = f"unreachable: {type(error).__name__}"
    return {"at": now().isoformat(), "usb_backend": active, "health": health}


def _premarket_block(cache):
    from app.backtest.strategy_e1_premarket.premarket import DECISION_LAST_BAR, premarket_block
    items = sorted((m, b) for m, b in cache.bars.items() if 4 * 60 <= m <= 9 * 60 + 24)
    if not items:
        return None
    a = np.array([[m, *b, float("nan")] for m, b in items], dtype=float)
    return premarket_block(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], DECISION_LAST_BAR)


#: Root of the ACCOUNTING_V1 / E cost contract books (see app.strategy_e_max_rt.cost). Read-only now.
PAPER_STATE_V1 = "paper_state_v1"
#: E-RUNTIME-R1 books: exact 09:34 close exit, new initial equity, never merged with V1.
PAPER_STATE_V2 = "paper_state_v2"
BOOK_OFFICIAL = "OFFICIAL_KIWOOM_PAPER"
BOOK_SHADOW = "SHADOW_VALIDATION"
RUNTIME_VERSION = "E-RUNTIME-R1"
#: Source refusals the RVOL store has recorded for good; such a symbol is a documented exception,
#: classified MARKET_DATA_UNAVAILABLE-like, and never blocks the session (PS since 2026-09-23).
DOCUMENTED_REFUSALS = ("MARKET_DATA_UNAVAILABLE", "INVALID_SYMBOL")


def execution_gate(*, decided_at: datetime, open_et: datetime, statuses: dict, documented: set,
                   rvol_unknown: list, unreachable: set, pending: list) -> dict:
    """Whether this session's decision may be executed, and every reason it may not.

    A decision is still computed and recorded when it is blocked; it is just never traded.
    """
    unexpected_stale = sorted(s for s, v in statuses.items() if v == AV.STALE and s not in documented)
    unexpected_rvol = sorted(s for s in rvol_unknown if s not in unreachable)
    reasons = []
    if decided_at >= open_et:
        reasons.append("DECISION_AFTER_0930")
    if unexpected_stale:
        reasons.append("DECISION_INCOMPLETE_UNIVERSE")
    if unexpected_rvol:
        reasons.append("RVOL_UNEXPECTED_MISSING")
    if pending:
        reasons.append("RVOL_BOOTSTRAP_INCOMPLETE")
    return {"authorized": not reasons, "reasons": reasons, "status": reasons[0] if reasons else "AUTHORIZED",
            "seal_at": decided_at.isoformat(), "seal_margin_s": (open_et - decided_at).total_seconds(),
            "unexpected_stale": len(unexpected_stale), "unexpected_stale_examples": unexpected_stale[:20],
            "documented_exceptions": sorted(s for s, v in statuses.items() if v == AV.STALE and s in documented),
            "unexpected_rvol_missing": len(unexpected_rvol), "unexpected_rvol_examples": unexpected_rvol[:20],
            "rvol_pending": len(pending), "rvol_pending_examples": pending[:20]}


def _paper_stage(decision, session, record, out_root: Path, cal, now, at, log, equity: str,
                 provider_factory=None, interval: float = 10.0, book: str = BOOK_SHADOW,
                 gate: dict | None = None) -> dict:
    """Frozen entry/exit through the common RT0 engine: SimBroker only, E's own virtual book.

    The 09:25 decision is seeded into the engine state instead of being re-derived, because the
    full-universe cutoff finishes seconds before 09:30. Provisional and official evidence keep
    separate books, so a provisional session is never summed into the official record.
    """
    from datetime import time as dtime
    from decimal import Decimal
    from app.core.config import get_settings
    from app.market.factory import build_kiwoom_provider
    from app.strategy_e_max_rt import config as CFG, engine as ENG, paper as PAPER
    from app.strategy_e_max_rt import cost as COST
    # V2 books only. ``paper_state`` (V0) and ``paper_state_v1`` (V1, incl. PROVISIONAL) are
    # historical evidence: this runner never opens them for writing again.
    if book not in (BOOK_OFFICIAL, BOOK_SHADOW):
        raise ValueError(f"unknown E book {book}")
    state_dir = out_root / PAPER_STATE_V2 / book
    config = CFG.RuntimeConfig(enabled=True, state_dir=state_dir, initial_equity=Decimal(equity))
    engine_store = ENG.Store(state_dir, CFG.STRATEGY_ID)
    execution = COST.official_execution(COST.EXECUTION_VERSION_V2)
    if gate is not None and not gate["authorized"]:
        # recorded, not traded: the book shows the session and why it did nothing
        ledger = engine_store.load_book(config.initial_equity, execution)
        if ledger.get("execution_version") != execution.version:
            raise RuntimeError("E V2 book opened under another execution version")
        state = engine_store.load_session(session) or {
            "strategy_id": CFG.STRATEGY_ID, "session": session.isoformat(), "phase": ENG.Phase.NO_DECISION,
            "equity_at_open": ledger["equity"], "decision": decision.to_json(), "entries": {}, "exits": {},
            "keys": [], "events": [], "mode": "SIMULATION_VIRTUAL_ONLY", "execution_version": execution.version,
            "no_decision": {"status": gate["status"], "reasons": gate["reasons"]},
            "runtime_version": RUNTIME_VERSION, **COST.record(execution.version)}
        state["events"].append({"at": now().isoformat(), "event": f"NOT_AUTHORIZED {','.join(gate['reasons'])}"})
        engine_store.save_session(state)
        if session.isoformat() not in ledger["sessions"]:
            ledger["sessions"][session.isoformat()] = {"phase": ENG.Phase.NO_DECISION, "reason": gate["status"],
                                                       "sim_realized_pnl": "0", "sim_gross_pnl": "0",
                                                       "sim_cost": "0", "equity_after": ledger["equity"]}
            engine_store.save_book(ledger)
        log(f"paper: NOT AUTHORIZED ({','.join(gate['reasons'])}); book={state_dir} unchanged")
        return {"ran": False, "phase": ENG.Phase.NO_DECISION, "book": book, "book_dir": str(state_dir),
                "reason": gate["status"], "reasons": gate["reasons"], "entries": 0, "fills": 0, "exits": 0,
                "summary": None, "mode": "SIMULATION_VIRTUAL_ONLY", "real_orders": 0,
                "execution_version": execution.version}
    factory = provider_factory or (lambda: build_kiwoom_provider(get_settings()))
    provider = factory()
    engine = ENG.Engine(config, lambda: provider, PAPER.FixedDecision(decision), cal, engine_store,
                        execution=execution)
    book = engine_store.load_book(config.initial_equity, execution)
    state = engine_store.load_session(session) or engine.new_state(session, Decimal(book["equity"]))
    if ENG.Phase(state["phase"]) is ENG.Phase.WAITING:
        state["decision"] = decision.to_json()
        state["phase"] = ENG.Phase.DECIDED
        state["events"].append({"at": now().isoformat(), "event": "DECISION_SEEDED_FROM_0925_CUTOFF"})
        engine_store.save_session(state)
    log(f"paper: book={state_dir} equity={book['equity']} selected={list(decision.selected)}")
    deadline = at(dtime(9, 50))
    while now() < deadline:
        state = engine.tick(now())
        if ENG.Phase(state["phase"]) in ENG.TERMINAL:
            break
        time.sleep(interval)
    entries = state.get("entries", {})
    exits = state.get("exits", {})
    return {"ran": True, "phase": state["phase"], "book": book, "book_dir": str(state_dir),
            "execution_version": execution.version,
            "source_refusals": dict(getattr(provider, "failures", {})),
            "evidence_status": record["paper_evidence_status"],
            "entries": len(entries), "fills": sum(1 for e in entries.values() if e.get("status") == "FILLED"),
            "exits": len(exits), "summary": state.get("summary"),
            "mode": "SIMULATION_VIRTUAL_ONLY", "live_margin_approved": CFG.LIVE_MARGIN_APPROVED,
            "real_orders": 0}


def _feature_rows(caches: dict, spy, statuses: dict, art: dict, denominators: dict) -> tuple[dict, list]:
    """The sealed feature rows of every eligible symbol, and the ones without an RVOL denominator."""
    from app.strategy_e_v1_1 import universe as U
    spy_pre = _premarket_block(spy)
    spy_ret = spy_pre["pm_last_price"] / art["spy_close_d_minus_1"] - 1 if spy_pre else float("nan")
    rows, rvol_unknown = {}, []
    for sym, c in caches.items():
        if statuses[sym] != AV.FEATURE_COMPLETE:
            continue
        pre = _premarket_block(c)
        if pre["pm_bars"] < U.MIN_PREMARKET_BARS or pre["pm_dollar_volume"] < U.MIN_PREMARKET_DOLLAR_VOLUME:
            continue
        close = art["close_d_minus_1"][sym]
        gap = pre["pm_last_price"] / close - 1
        span = pre["pm_high"] - pre["pm_low"]
        denominator = denominators.get(sym)
        if denominator is None:                      # E-RT3: no same-source history -> H5 is UNKNOWN
            rvol_unknown.append(sym)
        rows[sym] = {"premarket_gap": gap,
                     "premarket_rvol": (pre["pm_dollar_volume"] / denominator) if denominator else float("nan"),
                     "position_in_premarket_range": (pre["pm_last_price"] - pre["pm_low"]) / span if span > 0 else float("nan"),
                     "return_0900_0925": pre["return_0900_0925"], "return_last30m": pre["return_0855_0925"],
                     "relative_strength_vs_spy": gap - spy_ret, "premarket_dollar_volume": pre["pm_dollar_volume"],
                     "previous_day_dollar_volume": art["dollar_volume_d_minus_1"][sym], "close_price": close,
                     "spy_premarket_return": spy_ret, "pm_bars": pre["pm_bars"]}
    return rows, rvol_unknown


def _persist_inputs(run_dir: Path, session: date, caches: dict, spy, statuses: dict, rows: dict) -> None:
    """What an independent recomputation needs (R1).

    ``premarket_raw.json.gz``: every symbol's merged premarket bars (<= 09:24, as the cutoff kept
    them) and its availability status; ``feature_frame.csv``: the eligible rows' sealed features.
    Neither is read back by this runner, and both are written after the seal (they are the inputs
    the seal already used). A failure here is recorded, never fatal for the session.
    """
    try:
        body = {"session": session.isoformat(), "cutoff_last_minute": 9 * 60 + 24,
                "spy": [[m, *b] for m, b in sorted(spy.bars.items()) if m <= 9 * 60 + 24],
                "symbols": {sym: {"status": statuses.get(sym),
                                  "bars": [[m, *b] for m, b in sorted(c.bars.items()) if m <= 9 * 60 + 24]}
                            for sym, c in caches.items()}}
        (run_dir / "premarket_raw.json.gz").write_bytes(gzip.compress(json.dumps(body).encode(), 6))
        names = sorted({k for r in rows.values() for k in r})
        with (run_dir / "feature_frame.csv").open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["symbol", *names])
            for sym in sorted(rows):
                writer.writerow([sym, *[repr(float(rows[sym][n])) for n in names]])
    except Exception as error:                                      # evidence only
        log(f"INPUT_PERSIST_FAILED {type(error).__name__}: {error}")


def run(universe_path: Path, out_root: Path, *, paper: bool = False,
        rvol_store_path: Path | None = None, equity: str = "10000", book: str = BOOK_SHADOW) -> int:
    from app.core.config import get_settings
    from app.integrations.kiwoom.auth import KiwoomAuthClient
    from app.integrations.kiwoom.rate_limit import KiwoomRateLimits
    from app.market.calendar import MarketCalendar
    from app.integrations.kiwoom import lane_share as SHARE
    from app.strategy_e_max_rt import finalizer as FZ

    checks = safety_checks()
    cal = MarketCalendar("America/New_York")
    session = now().date()
    window = cal.session(session)
    if universe_path.is_dir():                       # a directory of daily artifacts
        universe_path = universe_path / f"universe_{session.isoformat()}.json"
    if window is None:
        log(f"{session}: not a trading session")
        return 0
    if not universe_path.exists():
        log(f"UNIVERSE_NOT_AVAILABLE for {session}: {universe_path} is missing; no decision (fail closed)")
        run_dir = out_root / session.isoformat()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(
            {"session": session.isoformat(), "status": "NO_DECISION",
             "reason": "UNIVERSE_NOT_AVAILABLE",
             "detail": "the canonical D-1 eligible universe artifact for this session was not staged; "
                       "the run refuses to decide on a stale universe because that would change the "
                       "B2 denominator", "at": now().isoformat()}, indent=1) + "\n")
        return 0
    art = json.loads(universe_path.read_text(encoding="utf-8"))
    from app.strategy_e_max_rt import universe_build as UB
    usable, why = UB.d_minus_1_identity(art, session, cal)
    if not usable:                                   # a stale universe is never reused
        log(f"UNIVERSE_NOT_AVAILABLE for {session}: {why}")
        run_dir = out_root / session.isoformat()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(
            {"session": session.isoformat(), "status": "NO_DECISION", "reason": "UNIVERSE_NOT_AVAILABLE",
             "detail": why, "artifact": universe_path.name, "at": now().isoformat()}, indent=1) + "\n")
        return 0
    run_dir = out_root / session.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = open(run_dir / "run.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another E-RT2 worker holds this session's lock")
    if (run_dir / "run.json").exists():
        log("run.json exists for this session; nothing to do")
        return 0
    at = lambda t: datetime.combine(session, t, tzinfo=FZ.ET)  # noqa: E731
    report = {"session": session.isoformat(), "mode": "E_RT2_CAPACITY_DRY_RUN_NO_ORDERS", "single_app_key": True,
              "runtime_version": RUNTIME_VERSION, "a_attached": False,
              "safety": checks, "universe_digest": art["digest"], "lanes": {"A": FZ.MINUTE_API, "B": FZ.TICK_API},
              "websocket_opened": False, "calendar": {"open_et": window.market_open.isoformat()},
              "started_at": now().isoformat(), "a_health": [a_health()]}
    if now() >= at(FZ.FINALIZE_AT):
        report["status"] = "RESTARTED_AFTER_CUTOFF"
        (run_dir / "run.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
        log("started after 09:25 ET: no decision")
        return 0

    s = get_settings()
    auth = KiwoomAuthClient(base_url=s.kiwoom_base_url, app_key=s.kiwoom_app_key.get_secret_value(),
                            app_secret=s.kiwoom_app_secret.get_secret_value(), limiter=KiwoomRateLimits().auth)
    clients = []
    # the minute lane and the tick lane follow E's side of the fixed A/E clock split (lane_share)
    for windows in (SHARE.E_MINUTE, SHARE.E_TICK):
        limits = KiwoomRateLimits()
        limits.chart = SHARE.ScheduledRateLimiter(session, windows, clock=now)
        clients.append(FZ.ChartLaneClient(base_url=s.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=1))
    report["lane_share"] = {"minute": [[a.isoformat(), b.isoformat(), r] for a, b, r in SHARE.E_MINUTE],
                            "tick": [[a.isoformat(), b.isoformat(), r] for a, b, r in SHARE.E_TICK]}
    lane_a, lane_b = FZ.Lane("A", FZ.MINUTE_API, clients[0]), FZ.Lane("B", FZ.TICK_API, clients[1])
    exch: dict[str, str] = {}
    for ex in ("ND", "NY", "NA"):
        for r in clients[0]._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list", max_pages=50):  # noqa: SLF001
            exch.setdefault(str(r["stk_cd"]).strip(), ex)
    report["kiwoom_auth"] = "OK"
    universe = art["symbols"]
    codes = {sym: FZ.kiwoom_code(sym, exch) for sym in universe}
    unmapped = [sym for sym in universe if codes[sym] not in exch]
    report["universe"] = {"rows": len(universe), "mapped": len(universe) - len(unmapped), "unmapped": unmapped,
                          "share_class_codes": {k: v for k, v in codes.items() if k != v}}
    shard_a, shard_b = FZ.hash_lanes([sym for sym in universe if sym not in unmapped])
    caches = {sym: FZ.SymbolCache(sym, exch[codes[sym]], codes[sym]) for sym in universe if sym not in unmapped}
    spy = FZ.SymbolCache("SPY", exch.get("SPY", "NA"))
    report["partition"] = {"method": "sha256(symbol) parity; the minute lane takes the tick shard from its end when "
                                     "its own shard is done", "shard_A": len(shard_a), "shard_B": len(shard_b)}
    log(f"universe {len(universe)} mapped {len(caches)} unmapped {len(unmapped)} shards A {len(shard_a)} B {len(shard_b)}")
    timeline = {}
    while now() < at(FZ.PREMARKET_START) + timedelta(seconds=5):
        time.sleep(5)
    report["rolling_started_at"] = now().isoformat()
    cycles, refresh_errors = 0, 0
    order = [spy] + [caches[x] for x in shard_a + shard_b]
    # E rolls until A's cut window opens, leaves the minute lane to A until its own tick-shard
    # refresh at 09:20:40, and never waits for anything A does.
    rolling_deadline = at(SHARE.A_CUT)
    while now() < rolling_deadline:
        cycles += 1
        for cache in order:
            if now() >= rolling_deadline:
                break
            try:
                FZ.refresh(lane_a, cache, session, now)
            except Exception as error:
                refresh_errors += 1
                cache.error = type(error).__name__
            else:
                cache.error = None
        log(f"cycle {cycles}: lane A calls {lane_a.calls} errors {refresh_errors}")
        if cycles == 1:
            report["a_health"].append(a_health())
    report["a_health"].append(a_health())
    timeline["rolling_end"] = now().isoformat()
    while now() < at(FZ.REFRESH_B_AT):
        time.sleep(0.2)
    timeline["tick_refresh_start"] = now().isoformat()
    for cache in [caches[x] for x in shard_b]:                 # tick shard last, in its finalization order
        if now() >= at(FZ.FINALIZE_AT):
            break
        try:
            FZ.refresh(lane_a, cache, session, now)
        except Exception:
            refresh_errors += 1
    incomplete_start = [x for x, c in caches.items() if c.complete_through is None]
    report["rolling"] = {"cycles": cycles, "lane_A_calls": lane_a.calls, "errors": refresh_errors,
                         "incomplete_start_symbols": len(incomplete_start),
                         "incomplete_start_examples": incomplete_start[:20]}

    timeline["tick_refresh_end"] = now().isoformat()
    while now() < at(FZ.FINALIZE_AT):
        time.sleep(0.05)
    t0, deadline = now(), at(FZ.DEADLINE)
    taken, guard = set(), threading.Lock()

    def claim(symbol: str) -> bool:
        with guard:
            if symbol in taken:
                return False
            taken.add(symbol)
            return True

    def worker_a() -> None:
        for sym in ["SPY"] + shard_a + list(reversed(shard_b)):      # own shard, then the end of B
            if now() >= deadline:
                return
            cache = spy if sym == "SPY" else caches[sym]
            if not claim(sym):
                continue
            try:
                FZ.finalize_minute(lane_a, cache, session, now)
                cache.error = None
            except Exception as error:
                cache.error = type(error).__name__

    def worker_b() -> None:
        for sym in shard_b:
            if now() >= deadline:
                return
            if not claim(sym):
                continue
            try:
                FZ.finalize_ticks(lane_b, caches[sym], session, now)
                caches[sym].error = None
            except Exception as error:
                caches[sym].error = type(error).__name__
    before = {"A": lane_a.calls, "B": lane_b.calls}
    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    [t.start() for t in threads]
    report["a_health"].append(a_health())
    [t.join() for t in threads]
    stopped = now()
    finalized = [c.finalized_at for c in caches.values() if c.finalized_at]
    t1 = max(finalized) if finalized else None

    statuses = {}                                    # E-RT2.1 contract: a refusal is its own state
    for sym, c in caches.items():
        statuses[sym] = AV.classify(AV.outcome_from_cache(c, deadline=deadline))
    for sym in unmapped:
        statuses[sym] = AV.MARKET_DATA_UNAVAILABLE   # in Kiwoom's listing walk, no chart code to ask with
    violations = sum(1 for c in caches.values() if any(m > 9 * 60 + 24 for m in c.bars))

    def lat(lane):
        v = sorted(lane.latencies)
        return {"p50": v[len(v) // 2], "p95": v[int(len(v) * 0.95)], "max": v[-1]} if v else None
    fin_b = [c for c in caches.values() if c.data_source == "KIWOOM_" + FZ.TICK_API]
    pages = sorted(c.final_pages for c in fin_b)
    window_s = max((stopped - t0).total_seconds(), 1e-9)
    lane_stats = {
        "A": {"api": FZ.MINUTE_API, "total_calls": lane_a.calls, "finalization_calls": lane_a.calls - before["A"],
              "errors": lane_a.errors, "attempts_incl_retries": sum(clients[0].request_counts.values()),
              "latency_s": lat(lane_a)},
        "B": {"api": FZ.TICK_API, "total_calls": lane_b.calls, "finalization_calls": lane_b.calls - before["B"],
              "errors": lane_b.errors, "attempts_incl_retries": sum(clients[1].request_counts.values()),
              "latency_s": lat(lane_b)},
        "finalization_window_s": window_s,
        "combined_finalization_calls_per_s": ((lane_a.calls - before["A"]) + (lane_b.calls - before["B"])) / window_s}
    tick_pagination = {"symbols": len(fin_b), "1_page": sum(p == 1 for p in pages), "2_pages": sum(p == 2 for p in pages),
                       "3plus_pages": sum(p >= 3 for p in pages), "max_pages": pages[-1] if pages else None,
                       "avg_pages": statistics.mean(pages) if pages else None,
                       "p95_pages": pages[int(len(pages) * 0.95)] if pages else None, "total_calls": sum(pages),
                       "per_symbol": {c.symbol: {"pages": c.final_pages, "ticks_read": c.ticks_read,
                                                 "last_relevant_tick": c.last_relevant_tick} for c in fin_b}}
    counts = {k: sum(v == k for v in statuses.values()) for k in ("FEATURE_COMPLETE", "SPARSE_NO_PREMARKET", "STALE")}
    capacity = {"T0": t0.isoformat(), "T1": t1.isoformat() if t1 else None,
                "elapsed_s": (t1 - t0).total_seconds() if t1 else None,
                "T1_before_0930": bool(t1 and t1 < at(FZ.dtime(9, 30))), "T1_before_092945": bool(t1 and t1 <= deadline),
                "seconds_before_0930": (at(FZ.dtime(9, 30)) - t1).total_seconds() if t1 else None,
                "kiwoom_calls_stopped_at": stopped.isoformat(), "status_counts": counts,
                "finalized_by_lane": {"A": sum(c.data_source == "KIWOOM_" + FZ.MINUTE_API for c in caches.values()),
                                      "B": len(fin_b)},
                "tick_shard_taken_by_A": sum(1 for x in shard_b if caches[x].data_source == "KIWOOM_" + FZ.MINUTE_API)}
    cutoff_audit = {"future_cutoff_violation_count": violations, "rule": "only bars / ticks stamped <= 09:24 ET are merged"}

    from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
    from app.strategy_e_max_rt import decision as DEC
    from app.strategy_e_v1_1.universe import DecisionFrame
    timings = {}
    t = time.monotonic()
    store = RvolStore(rvol_store_path or Path(os.environ.get("E_RVOL_STORE", RVOL_STORE_DEFAULT)))
    denominators = store.preload(list(caches), session)
    rows, rvol_unknown = _feature_rows(caches, spy, statuses, art, denominators)
    symbols = tuple(sorted(rows))
    h5_by_symbol = {}
    frame = DecisionFrame(session, symbols, {n: np.array([rows[x][n] for x in symbols], dtype=float)
                                             for n in SEALED_FEATURES}, {})
    timings["feature_build_s"] = time.monotonic() - t
    t = time.monotonic()
    seal_at = now()
    decision = DEC.decide_from_frame(frame, source_digest=art["digest"], source="KIWOOM_NATIVE_RT2_DRYRUN", decided_at=seal_at)
    timeline.update(T0=t0.isoformat(), T1=t1.isoformat() if t1 else None, kiwoom_stopped=stopped.isoformat(),
                    features_done=(seal_at.isoformat()), seal=seal_at.isoformat())
    timings["h5_r1_top3_breadth_seal_s"] = time.monotonic() - t
    timings["total_decision_s"] = timings["feature_build_s"] + timings["h5_r1_top3_breadth_seal_s"]
    # after the seal on purpose: writing the evidence must never cost the decision its margin
    _persist_inputs(run_dir, session, caches, spy, statuses, rows)
    from app.strategy_e_max_rt import paper as PAPER
    for sym in rvol_unknown:                        # eligible row without a same-source denominator
        statuses[sym] = AV.RVOL_HISTORY_INSUFFICIENT
    for sym in caches:
        h5_by_symbol[sym] = AV.h5_status(statuses[sym], sym in decision.candidates)
    availability = AV.diagnostics(statuses, h5_by_symbol, decision.universe_rows, decision.candidates) | {
        "rvol_denominator_source": "KIWOOM_NATIVE store (E-RT3); Massive is never mixed in",
        "rvol_ready_rows": len(symbols) - len(rvol_unknown),
        "rvol_missing_rows": len(rvol_unknown),
        "rvol_missing_examples": sorted(rvol_unknown)[:10],
        "h5_unknown_due_to_rvol": len(rvol_unknown),
        "enable_blocked_while_rvol_missing": bool(rvol_unknown)}
    (run_dir / "availability.json").write_text(json.dumps(availability, indent=1) + "\n")
    record, paper_summary = {}, {"ran": False, "reason": "paper stage not requested"}
    try:                                    # the capacity evidence is written even if this fails
        staged_counts = {s: store.staged_count(s, session) for s in symbols}
        coverage = store.coverage(list(caches), session)
        # "complete" means nothing is left that collection could fix. A symbol whose Kiwoom history
        # is exhausted, or that the source refuses, is a recorded exception and never becomes
        # collectable, so it must not hold the evidence grade at PROVISIONAL forever.
        unreachable = store.unreachable_symbols()
        pending = store.bootstrap_pending(list(caches), session)
        bootstrap_complete = not pending
        coverage = coverage | {"unreachable": len(unreachable), "pending_collection": len(pending),
                               "pending_examples": pending[:10]}
        documented = {sym for sym, st in store.collection_state().items()
                      if st.get("last_error") in DOCUMENTED_REFUSALS}
        gate = execution_gate(decided_at=seal_at, open_et=at(FZ.dtime(9, 30)), statuses=statuses,
                              documented=documented, rvol_unknown=rvol_unknown, unreachable=unreachable,
                              pending=pending)
        timeline_margins = {"T1_margin_s": (at(FZ.dtime(9, 30)) - t1).total_seconds() if t1 else None,
                            "seal_margin_s": gate["seal_margin_s"],
                            "T0_delay_s": (t0 - at(FZ.FINALIZE_AT)).total_seconds()}
        record = PAPER.session_record(
            session, states=statuses, h5_by_symbol=h5_by_symbol, eligible_rows=decision.universe_rows,
            candidates=decision.candidates, selected=decision.selected,
            rvol_ready_rows=len(symbols) - len(rvol_unknown), rvol_missing_rows=len(rvol_unknown),
            bootstrap_complete=bootstrap_complete, coverage=coverage, decision_digest=decision.digest,
            extra={"universe_digest": art["digest"], "paper": bool(paper), "runtime_version": RUNTIME_VERSION,
                   "book": book if paper else None, "execution_gate": gate,
                   "timeline": timeline | timeline_margins})
        (run_dir / "session_record.json").write_text(json.dumps(record, indent=1) + "\n")
        PAPER.write_evidence(run_dir / "rvol_evidence.csv", PAPER.evidence_rows(
            session, features=rows, denominators=denominators, staged_counts=staged_counts,
            states=statuses))
        log(f"session record: status={record['paper_evidence_status']} rvol_ready={record['rvol_ready']} "
            f"rvol_missing={record['rvol_missing']} h5_true={record['h5_true']} selected={record['selected']}")
        if paper:
            # the entry and exit ask Kiwoom with the same code and exchange the cutoff used
            entry_client = FZ.ChartLaneClient(base_url=s.kiwoom_base_url, auth=auth,
                                              rate_limits=KiwoomRateLimits(), max_retries=1)
            paper_summary = _paper_stage(
                decision, session, record, out_root, cal, now, at, log, equity,
                provider_factory=lambda: PAPER.ListingMinuteProvider(entry_client, codes, exch),
                book=book, gate=gate)
        record["paper_runtime"] = paper_summary
        (run_dir / "session_record.json").write_text(json.dumps(record, indent=1, default=str) + "\n")
    except Exception as error:
        log(f"SESSION_RECORD_FAILED {type(error).__name__}: {error}")
        record = {"session": session.isoformat(), "error": f"{type(error).__name__}: {error}",
                  "paper_evidence_status": "PROVISIONAL_RVOL_BOOTSTRAP"}
        (run_dir / "session_record.json").write_text(json.dumps(record, indent=1, default=str) + "\n")
    report.update(status="COMPLETE", capacity=capacity, cutoff_audit=cutoff_audit,
        availability=availability, decision_compute={
        **timings, "premarket_rows": len(symbols), "h5_candidates": decision.h5_count,
        "rvol": f"{len(symbols) - len(rvol_unknown)}/{len(symbols)} rows had a Kiwoom-native denominator",
        "top3_diagnostic": list(decision.selected)},
        session_record=record,
        orders={"order_request_count": sum(c.order_request_count for c in clients),
                "execution_orders": paper_summary.get("entries", 0), "fills": paper_summary.get("fills", 0),
                "simbroker_used": bool(paper_summary.get("ran")), "real_orders": 0,
                "mode": "SIMULATION_VIRTUAL_ONLY"})
    report["a_health"].append(a_health())
    report["finished_at"] = now().isoformat()
    for name, body in (("capacity.json", capacity), ("lane_stats.json", lane_stats),
                       ("tick_pagination.json", tick_pagination), ("cutoff_audit.json", cutoff_audit)):
        (run_dir / name).write_text(json.dumps(body, indent=1, default=str) + "\n")
    with (run_dir / "symbol_status.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "kiwoom_code", "lane", "last_complete_minute", "finalized_at", "final_pages", "status", "error"])
        for sym in universe:
            c = caches.get(sym)
            last = max((m for m in c.bars if m <= 9 * 60 + 24), default=None) if c else None
            w.writerow([sym, codes[sym], (c.data_source or "") if c else "",
                        f"{last // 60:02d}:{last % 60:02d}" if last is not None else "",
                        c.finalized_at.isoformat() if c and c.finalized_at else "", c.final_pages if c else 0,
                        statuses[sym], (c.error or "") if c else "UNMAPPED"])
    (run_dir / "run.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    log(f"T0 {t0.time()} T1 {t1.time() if t1 else None} status {counts} violations {violations}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E-RT2 capacity dry run")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build-universe")
    b.add_argument("--session", type=date.fromisoformat, required=True)
    b.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--universe", type=Path, required=True, help="artifact file or a directory of them")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--paper", action="store_true", help="run the frozen entry/exit as simulation-only paper")
    r.add_argument("--rvol-store", type=Path)
    r.add_argument("--equity", default="10000")
    r.add_argument("--book", choices=("shadow", "official"), default="shadow",
                   help="E-RUNTIME-R1 V2 book: shadow validation (default) or the official paper book")
    args = parser.parse_args(argv)
    if args.command == "build-universe":
        build_universe(args.session, args.out)
        return 0
    return run(args.universe, args.out, paper=args.paper, rvol_store_path=args.rvol_store,
               equity=args.equity, book=BOOK_OFFICIAL if args.book == "official" else BOOK_SHADOW)


if __name__ == "__main__":
    raise SystemExit(main())
