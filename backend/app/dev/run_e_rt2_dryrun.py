"""E-RT2 live dry run: single-app-key, full canonical universe, 09:24-cutoff finalization by 09:30.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_e_rt2_dryrun [--minute-share 1300]

Read-only Kiwoom market data (usa10099, usa06011, usa06010); no order, no account TR, no E enable,
no return computed. Kiwoom calls stop at 09:29:45 ET, before Strategy A's first call at the open.
Writes data/runtime/strategy_e_max/rt/dryrun/<session>.json.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import gzip
import json
import statistics
import threading
import time
from pathlib import Path

import numpy as np

from app.backtest.strategy_c_selection.panel import SplitEvent
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e1_premarket.premarket import DECISION_LAST_BAR, premarket_block
from app.backtest.workspace.discovery import resolve_workspace_root
from app.core.config import get_settings
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward import forward_daily as FD, storage as ST
from app.strategy_e_max_rt import decision as DEC, finalizer as FZ
from app.strategy_e_v1_1 import universe as U
from app.strategy_e_v1_1.universe import DecisionFrame

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "data/runtime/strategy_e_max/rt/dryrun"
LANE_RATE = 4.9


def log(text):
    print(f"[{datetime.now(FZ.ET).strftime('%H:%M:%S')} ET] {text}", flush=True)


def now():
    return datetime.now(FZ.ET)


def wait_until(t):
    while now() < t:
        time.sleep(min(30.0, max(0.05, (t - now()).total_seconds())))


def lane_clients(settings, auth):
    out = []
    for _ in range(2):
        limits = KiwoomRateLimits()
        limits.chart = RequestRateLimiter(LANE_RATE)
        out.append(FZ.ChartLaneClient(base_url=settings.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=1))
    return out


def splits_through(root: Path, day: date) -> tuple[SplitEvent, ...]:
    body = json.loads(gzip.decompress(ST.splits_asof_path(root, day).read_bytes()))
    return tuple(SplitEvent(r["ticker"], date.fromisoformat(r["execution_date"]), float(r["split_from"]),
                            float(r["split_to"])) for r in body["results"] if r["split_from"] != r["split_to"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minute-share", type=int, default=1300)
    args = parser.parse_args(argv)
    cal = MarketCalendar("America/New_York")
    session = now().date()
    if cal.session(session) is None or now().time() >= FZ.REFRESH_B_AT:
        raise SystemExit(f"{session} is not a session or it is past 09:20:40 ET")
    prev = cal.previous_trading_day(session)
    root = resolve_workspace_root(None)
    report = {"session": session.isoformat(), "mode": "DRY_RUN_NO_ORDERS", "single_app_key": True,
              "lanes": {"minute": FZ.MINUTE_API, "tick": FZ.TICK_API}, "lane_rate_per_s": LANE_RATE}

    base = FD.load_base(root)
    universe, missing = FD.eligible_universe(base, root, session, cal, forward_splits=splits_through(root, prev))
    if universe is None:
        raise SystemExit(f"canonical universe inputs missing: {missing}")
    report["universe"] = {"rows": len(universe), "provisional": "split executions on the session itself are "
                          "not in the stored list (splits_asof_" + prev.isoformat() + " used)"}
    grouped = json.loads(gzip.decompress(ST.grouped_path(root, prev).read_bytes()))["body"]["results"]
    dv = {r["T"]: float(r["c"]) * float(r["v"]) for r in grouped if r.get("c") and r.get("v")}
    close = {r["T"]: float(r["c"]) for r in grouped if r.get("c")}

    settings = get_settings()
    auth = KiwoomAuthClient(base_url=settings.kiwoom_base_url, app_key=settings.kiwoom_app_key.get_secret_value(),
                            app_secret=settings.kiwoom_app_secret.get_secret_value(), limiter=KiwoomRateLimits().auth)
    minute_client, tick_client = lane_clients(settings, auth)
    exch = {}
    for ex in ("ND", "NY", "NA"):
        for r in minute_client._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list", max_pages=50):  # noqa: SLF001
            exch.setdefault(str(r["stk_cd"]).strip(), ex)
    mapped = [s for s in universe if FZ.kiwoom_code(s, exch) in exch]
    unmapped = [s for s in universe if FZ.kiwoom_code(s, exch) not in exch]
    report["kiwoom_mapping"] = {"mapped": len(mapped), "unmapped": len(unmapped), "unmapped_symbols": unmapped[:50],
                                "share_class_rule": "X.Y -> X + lower(Y)",
                                "share_class_mapped": [s for s in mapped if FZ.kiwoom_code(s, exch) != s]}
    order = sorted(mapped, key=lambda s: (-dv.get(s, 0.0), s))
    group_a, group_b = FZ.split_lanes(order, args.minute_share)
    caches = {s: FZ.SymbolCache(s, exch[FZ.kiwoom_code(s, exch)], FZ.kiwoom_code(s, exch)) for s in order}
    spy = FZ.SymbolCache("SPY", exch.get("SPY", "NA"))
    minute_lane, tick_lane = FZ.Lane("minute", FZ.MINUTE_API, minute_client), FZ.Lane("tick", FZ.TICK_API, tick_client)
    log(f"universe {len(universe)} mapped {len(mapped)} unmapped {len(unmapped)}; minute lane {len(group_a)}, tick lane {len(group_b)}")

    at = lambda t: datetime.combine(session, t, tzinfo=FZ.ET)  # noqa: E731
    wait_until(at(FZ.PREMARKET_START) + timedelta(seconds=5))
    cycles, errors = 0, 0
    while now() < at(FZ.REFRESH_B_AT):
        cycles += 1
        for cache in [spy] + [caches[s] for s in order]:
            if now() >= at(FZ.REFRESH_B_AT):
                break
            try:
                FZ.refresh(minute_lane, cache, session, now)
            except Exception as error:  # a failed refresh leaves the cache as it was
                errors += 1
                if errors <= 5:
                    log(f"refresh error {cache.symbol}: {error}")
        log(f"cycle {cycles} done; minute-lane calls {minute_lane.calls} errors {errors}")
    for cache in [spy] + [caches[s] for s in group_b]:          # the tick group last, in finalization order
        if now() >= at(FZ.FINALIZE_AT):
            break
        try:
            FZ.refresh(minute_lane, cache, session, now)
        except Exception:
            errors += 1
    report["rolling"] = {"cycles": cycles, "minute_lane_calls": minute_lane.calls, "refresh_errors": errors}

    wait_until(at(FZ.FINALIZE_AT))
    t0 = now()
    deadline = at(FZ.DEADLINE)
    fin_errors = {"minute": 0, "tick": 0}

    def run(lane, group, fn, key):
        for s in group:
            if now() >= deadline:
                return
            try:
                fn(lane, caches[s], session, now)
            except Exception:
                fin_errors[key] += 1
    threads = [threading.Thread(target=run, args=(minute_lane, group_a, FZ.finalize_minute, "minute")),
               threading.Thread(target=run, args=(tick_lane, group_b, FZ.finalize_ticks, "tick"))]
    try:
        FZ.finalize_minute(minute_lane, spy, session, now)
    except Exception:
        fin_errors["minute"] += 1
    [t.start() for t in threads]
    [t.join() for t in threads]
    t1 = max((c.finalized_at for c in caches.values() if c.finalized_at), default=None)
    calls_stopped_at = now()
    audit = FZ.audit(caches, deadline)
    tick_pages = [c.calls for s, c in caches.items() if s in set(group_b) and c.data_source]
    report["finalization"] = {
        "T0": t0.isoformat(), "T1": t1.isoformat() if t1 else None,
        "T1_minus_T0_s": (t1 - t0).total_seconds() if t1 else None, "T1_before_0930": bool(t1 and t1.time() < FZ.dtime(9, 30)),
        "kiwoom_calls_stopped_at": calls_stopped_at.isoformat(), "errors": fin_errors,
        "minute_lane": {"calls": minute_lane.calls, "errors": minute_lane.errors,
                        "latency_p50": statistics.median(minute_lane.latencies) if minute_lane.latencies else None,
                        "latency_max": max(minute_lane.latencies) if minute_lane.latencies else None},
        "tick_lane": {"calls": tick_lane.calls, "errors": tick_lane.errors,
                      "latency_p50": statistics.median(tick_lane.latencies) if tick_lane.latencies else None,
                      "latency_max": max(tick_lane.latencies) if tick_lane.latencies else None},
        "tick_calls_per_symbol_incl_refresh": {"median": statistics.median(tick_pages) if tick_pages else None,
                                               "max": max(tick_pages) if tick_pages else None},
        "audit": {**audit, "unmapped_stale": len(unmapped), "total_stale": audit["stale"] + len(unmapped)}}
    log(f"T0 {t0.time()} T1 {t1.time() if t1 else None} stale {audit['stale']} unmapped {len(unmapped)}")

    # features from the finalized bars (no return is computed)
    started = time.monotonic()
    spy_pre = _block(spy)
    spy_close = close.get("SPY", float("nan"))
    spy_ret = spy_pre["pm_last_price"] / spy_close - 1 if spy_pre and spy_close == spy_close else float("nan")
    rows, complete = {}, 0
    for s, c in caches.items():
        pre = _block(c)
        if pre is None or pre["pm_bars"] < U.MIN_PREMARKET_BARS or pre["pm_dollar_volume"] < U.MIN_PREMARKET_DOLLAR_VOLUME:
            continue
        gap = pre["pm_last_price"] / close[s] - 1
        span = pre["pm_high"] - pre["pm_low"]
        rows[s] = {"premarket_gap": gap, "premarket_rvol": float("nan"),
                   "position_in_premarket_range": (pre["pm_last_price"] - pre["pm_low"]) / span if span > 0 else float("nan"),
                   "return_0900_0925": pre["return_0900_0925"], "return_last30m": pre["return_0855_0925"],
                   "relative_strength_vs_spy": gap - spy_ret, "premarket_dollar_volume": pre["pm_dollar_volume"],
                   "previous_day_dollar_volume": dv.get(s, float("nan")), "close_price": close[s],
                   "spy_premarket_return": spy_ret, "pm_bars": pre["pm_bars"]}
        complete += all(np.isfinite(rows[s][k]) for k in ("premarket_gap", "position_in_premarket_range"))
    symbols = tuple(sorted(rows))
    frame = DecisionFrame(session, symbols, {n: np.array([rows[s][n] for s in symbols], dtype=float) for n in SEALED_FEATURES}, {})
    decision = DEC.decide_from_frame(frame, source_digest="dryrun", source="KIWOOM_NATIVE_DRYRUN", decided_at=now())
    report["decision_compute"] = {
        "seconds_incl_feature_build": round(time.monotonic() - started, 3), "premarket_rows": len(symbols),
        "rows_with_gap_and_range": complete,
        "rvol": "NOT COMPUTED: no same-source (Kiwoom) 20-session denominator exists; E-RT1 volume-semantics finding stands",
        "h5_candidates_without_rvol": decision.h5_count}
    report["orders_sent"] = 0
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{session.isoformat()}.json"
    path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    log(f"written {path}")
    return 0


def _block(cache):
    items = sorted((m, b) for m, b in cache.bars.items() if 4 * 60 <= m <= 9 * 60 + 24)
    if not items:
        return None
    a = np.array([[m, *b, float("nan")] for m, b in items], dtype=float)
    return premarket_block(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], DECISION_LAST_BAR)


if __name__ == "__main__":
    raise SystemExit(main())
