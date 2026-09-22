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


def run(universe_path: Path, out_root: Path) -> int:
    from app.core.config import get_settings
    from app.integrations.kiwoom.auth import KiwoomAuthClient
    from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
    from app.market.calendar import MarketCalendar
    from app.strategy_e_max_rt import finalizer as FZ

    checks = safety_checks()
    cal = MarketCalendar("America/New_York")
    session = now().date()
    window = cal.session(session)
    art = json.loads(universe_path.read_text(encoding="utf-8"))
    if window is None or art["session"] != session.isoformat():
        raise SystemExit(f"{session}: not a session or universe artifact is for {art['session']}")
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
    for _ in range(2):
        limits = KiwoomRateLimits()
        limits.chart = RequestRateLimiter(4.9)
        clients.append(FZ.ChartLaneClient(base_url=s.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=1))
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

    while now() < at(FZ.PREMARKET_START) + timedelta(seconds=5):
        time.sleep(5)
    report["rolling_started_at"] = now().isoformat()
    cycles, refresh_errors = 0, 0
    order = [spy] + [caches[x] for x in shard_a + shard_b]
    while now() < at(FZ.REFRESH_B_AT):
        cycles += 1
        for cache in order:
            if now() >= at(FZ.REFRESH_B_AT):
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

    statuses = {}
    for sym, c in caches.items():
        ok = c.finalized_at is not None and c.contiguous and c.finalized_at <= deadline and c.error is None
        pm = [m for m in c.bars if 4 * 60 <= m <= 9 * 60 + 24]
        statuses[sym] = "STALE" if not ok else ("SPARSE_NO_PREMARKET" if not pm else "FEATURE_COMPLETE")
    for sym in unmapped:
        statuses[sym] = "STALE"
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
    from app.strategy_e_v1_1 import universe as U
    from app.strategy_e_v1_1.universe import DecisionFrame
    timings = {}
    t = time.monotonic()
    spy_pre = _premarket_block(spy)
    spy_ret = spy_pre["pm_last_price"] / art["spy_close_d_minus_1"] - 1 if spy_pre else float("nan")
    rows = {}
    for sym, c in caches.items():
        if statuses[sym] != "FEATURE_COMPLETE":
            continue
        pre = _premarket_block(c)
        if pre["pm_bars"] < U.MIN_PREMARKET_BARS or pre["pm_dollar_volume"] < U.MIN_PREMARKET_DOLLAR_VOLUME:
            continue
        close = art["close_d_minus_1"][sym]
        gap = pre["pm_last_price"] / close - 1
        span = pre["pm_high"] - pre["pm_low"]
        rows[sym] = {"premarket_gap": gap, "premarket_rvol": float("nan"),
                     "position_in_premarket_range": (pre["pm_last_price"] - pre["pm_low"]) / span if span > 0 else float("nan"),
                     "return_0900_0925": pre["return_0900_0925"], "return_last30m": pre["return_0855_0925"],
                     "relative_strength_vs_spy": gap - spy_ret, "premarket_dollar_volume": pre["pm_dollar_volume"],
                     "previous_day_dollar_volume": art["dollar_volume_d_minus_1"][sym], "close_price": close,
                     "spy_premarket_return": spy_ret, "pm_bars": pre["pm_bars"]}
    symbols = tuple(sorted(rows))
    frame = DecisionFrame(session, symbols, {n: np.array([rows[x][n] for x in symbols], dtype=float)
                                             for n in SEALED_FEATURES}, {})
    timings["feature_build_s"] = time.monotonic() - t
    t = time.monotonic()
    decision = DEC.decide_from_frame(frame, source_digest=art["digest"], source="KIWOOM_NATIVE_RT2_DRYRUN", decided_at=now())
    timings["h5_r1_top3_breadth_seal_s"] = time.monotonic() - t
    timings["total_decision_s"] = timings["feature_build_s"] + timings["h5_r1_top3_breadth_seal_s"]
    report.update(status="COMPLETE", capacity=capacity, cutoff_audit=cutoff_audit, decision_compute={
        **timings, "premarket_rows": len(symbols), "h5_candidates": decision.h5_count,
        "rvol": "NaN by design: no same-source denominator yet (next step: Kiwoom RVOL denominator audit)",
        "top3_diagnostic": list(decision.selected)},
        orders={"order_request_count": sum(c.order_request_count for c in clients),
                "execution_orders": 0, "fills": 0, "simbroker_used": False})
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
    r.add_argument("--universe", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build-universe":
        build_universe(args.session, args.out)
        return 0
    return run(args.universe, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
