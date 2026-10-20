"""The post-session audit of a common-producer paper session (A official, E official paper). Read-only.

    PYTHONPATH=backend python -m app.dev.verify_ae_shadow --session 2026-10-19 \
        --producer-out <run root> --consumer-out <A consumer run root> --snapshots <snapshot root> \
        [--database <paper sqlite>] [--e-book <paper_state_v2/OFFICIAL_KIWOOM_PAPER>]

Reads the producer record, the A consumer record, the sealed snapshots and (``mode=ro``) A's
``scanner_runs`` row, and writes ``<producer-out>/<session>/live_gate.json``. Nothing else is
written and nothing is called.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

#: 429 responses the gate tolerates. Production ran 0 at 4.9/s per lane.
TOLERATED_429 = 0


def _load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _scanner_run(database: Path | None, session: date, run_id: Any) -> dict[str, Any]:
    if database is None or not database.is_file():
        return {"checked": False, "reason": "no database path"}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, status, provider, score_version, universe_count, candidate_count, top8_count "
            "FROM scanner_runs WHERE trading_date=? AND score_version='a_mover_live_v1' ORDER BY id",
            (session.isoformat(),)).fetchall()
    finally:
        connection.close()
    runs = [dict(zip(("id", "status", "provider", "score_version", "universe_count", "candidate_count",
                      "top8_count"), row)) for row in rows]
    return {"checked": True, "runs": runs, "consumer_run_present": any(r["id"] == run_id for r in runs),
            "completed": any(r["id"] == run_id and r["status"] == "COMPLETED" for r in runs)}


def _e_book(e_book: Path | None, session: date) -> tuple[dict | None, dict | None]:
    if e_book is None:
        return None, None
    base = e_book / "STRATEGY_E_MAX_V1"
    return _load(base / "book.json"), _load(base / "sessions" / f"{session.isoformat()}.json")


def execution_checks(state: dict[str, Any] | None, book: dict[str, Any] | None, session: date,
                     decision: dict[str, Any] | None, authorized: bool | None) -> dict[str, dict[str, Any]]:
    """E's SimBroker execution against the frozen rule: 09:30 open in, exact 09:34 close out or
    UNRESOLVED_EXIT, never the 09:35 open; the book chain; the executed decision is the sealed one."""
    out: dict[str, dict[str, Any]] = {}
    day = session.isoformat()
    if state is None or book is None:
        out["E_book_session_recorded"] = {"pass": False, "detail": "no book or no session state"}
        return out
    out["E_book_session_recorded"] = {"pass": day in book.get("sessions", {}), "phase": state.get("phase")}
    out["E_book_version"] = {"pass": book.get("execution_version") == "e_cost_v2_exact_0934_close"
                             and state.get("execution_version") == "e_cost_v2_exact_0934_close",
                             "book": book.get("execution_version"), "initial_equity": book.get("initial_equity")}
    executed = (state.get("decision") or {})
    out["E_executed_decision_is_sealed_decision"] = {
        "pass": decision is not None and executed.get("selected") == decision.get("selected")
        and executed.get("digest") == decision.get("digest"),
        "selected": executed.get("selected"), "digest": executed.get("digest")}
    entries, exits = state.get("entries", {}), state.get("exits", {})
    if authorized is False:
        out["E_unauthorized_not_traded"] = {"pass": state.get("phase") == "NO_DECISION" and not entries,
                                            "no_decision": state.get("no_decision")}
        return out
    bad_entries = [s for s, e in entries.items() if e.get("status") == "FILLED" and not (
        str((e.get("fill_bar") or {}).get("timestamp", "")).startswith(f"{day}T09:30")
        and e.get("raw_market_price") is not None
        and float(e["raw_market_price"]) == float((e.get("fill_bar") or {}).get("open", "nan")))]
    out["E_entry_0930_open"] = {"pass": not bad_entries, "filled": sum(e.get("status") == "FILLED" for e in entries.values()),
                                "invalid": {s: e.get("reason") for s, e in entries.items() if e.get("status") != "FILLED"},
                                "violations": bad_entries}
    bad_exits, unresolved = [], []
    for symbol, exit_ in exits.items():
        if exit_.get("status") == "UNRESOLVED_EXIT":
            ok = exit_.get("reason") == "NO_EXACT_0934_BAR" and exit_.get("fill_price_field") is None
            (unresolved if ok else bad_exits).append(symbol)
            continue
        bar = exit_.get("fill_bar") or {}
        ok = (str(bar.get("timestamp", "")).startswith(f"{day}T09:34") and exit_.get("fill_price_field") == "close"
              and exit_.get("exit_flag") == "EXACT_0934_CLOSE"
              and float(exit_.get("raw_market_price", "nan")) == float(bar.get("close", "nan")))
        if not ok:
            bad_exits.append(symbol)
    filled = [s for s, e in entries.items() if e.get("status") == "FILLED"]
    out["E_exit_exact_0934_close"] = {"pass": not bad_exits and set(filled) <= set(exits), "exits": len(exits),
                                      "unresolved_exit": unresolved, "violations": bad_exits,
                                      "open_without_exit": sorted(set(filled) - set(exits))}
    sessions = book.get("sessions", {})
    days = sorted(sessions)
    previous = sessions[days[days.index(day) - 1]]["equity_after"] if day in days and days.index(day) > 0 \
        else book.get("initial_equity")
    current = sessions.get(day, {})
    try:
        from decimal import Decimal
        chained = Decimal(current["equity_after"]) == Decimal(previous) + Decimal(current["sim_realized_pnl"])
    except Exception:
        chained = False
    out["E_book_chain"] = {"pass": chained, "previous_equity": previous, "session": current,
                           "summary": state.get("summary")}
    return out


def gate(session: date, producer_out: Path, consumer_out: Path, snapshots: Path,
         database: Path | None = None, e_book: Path | None = None) -> dict[str, Any]:
    from app.ae_common import snapshot as SS
    day = session.isoformat()
    producer = _load(producer_out / day / "producer.json") or {}
    e_shadow = _load(producer_out / day / "e_shadow.json") or {}
    a_parity = _load(producer_out / day / "a_parity.json") or {}
    consumer = _load(consumer_out / day / "a_consumer.json") or {}
    snap_dir = snapshots / day
    checks: dict[str, dict[str, Any]] = {}

    def check(name: str, passed: bool | None, **detail: Any) -> None:
        checks[name] = {"pass": passed, **detail}

    # -- A ------------------------------------------------------------------------------------
    sweep = producer.get("a_sweep") or {}
    phases = {p["phase"]: p for lim in producer.get("limiters", []) for p in lim.get("phases", [])}
    sweep_phases = [p for lim in producer.get("limiters", []) for p in lim.get("phases", []) if p["phase"] == "a_sweep"]
    check("A_no_request_at_or_after_0925", bool(sweep_phases) and all(p["last_sent_before_deadline"] for p in sweep_phases),
          phases=sweep_phases)
    coverage = consumer.get("coverage") or {}
    check("A_full_coverage", bool(coverage.get("complete")), coverage=coverage,
          sweep={k: sweep.get(k) for k in ("reached", "usable", "stale", "failed", "interrupted", "not_reached",
                                          "tick_lane_steals", "t1")})
    comparison = (a_parity.get("comparison") or {})
    check("A_snapshot_0915_parity", bool(comparison.get("exact")) and a_parity.get("bars_at_or_after_cut_in_file") == 0,
          comparison=comparison)
    legacy = None
    if SS.exists(snap_dir, SS.A_PARITY):
        legacy = SS.load(snap_dir, SS.A_PARITY, fmt=SS.FORMAT_A_PARITY, session=session).body["legacy_row_digests"]
    fed = ((consumer.get("a_mover_live") or {}).get("snapshot_row_digests"))
    fed_cmp = SS.compare_digests(legacy, fed) if legacy is not None and fed is not None else None
    check("A_consumer_fed_identical_snapshots", bool(fed_cmp and fed_cmp["exact"]), comparison=fed_cmp)
    a_live = consumer.get("a_mover_live") or {}
    handoff = a_live.get("handoff") or {}
    scan = a_live.get("scan") or {}
    run = _scanner_run(database, session, handoff.get("run_id"))
    check("A_selected_candidate_result", bool(fed_cmp and fed_cmp["exact"]) and scan.get("status") not in
          ("SCANNER_NOT_RUN", "DATA_UNAVAILABLE", "CONTRACT_DRIFT", "ATTACH_FAILED"),
          candidates=scan.get("candidates"), candidate_symbols=[c.get("symbol") for c in a_live.get("candidates") or []],
          note="identical snapshots into the same deterministic scan; the candidates are those of the legacy path")
    check("A_official_continuity", bool(handoff.get("persisted")) and bool(run.get("completed")),
          handoff=handoff, scanner_run=run, consumer_status=consumer.get("status"),
          kiwoom_requests=consumer.get("kiwoom_requests"))

    # -- E ------------------------------------------------------------------------------------
    fin = producer.get("e_finalization") or {}
    from_snapshot = e_shadow.get("from_snapshot") or {}
    egate = from_snapshot.get("gate") or {}
    check("E_T0_at_0925", fin.get("T0_delay_s") is not None and fin["T0_delay_s"] < 1.0, T0=fin.get("T0"),
          delay_s=fin.get("T0_delay_s"))
    check("E_T1_before_0930", fin.get("margin_to_0930_s") is not None and fin["margin_to_0930_s"] > 0,
          T1=fin.get("T1"), margin_s=fin.get("margin_to_0930_s"))
    check("E_seal_before_0930", egate.get("seal_margin_s") is not None and egate["seal_margin_s"] > 0,
          seal_at=egate.get("seal_at"), margin_s=egate.get("seal_margin_s"))
    check("E_unexpected_stale_zero", egate.get("unexpected_stale") == 0, unexpected=egate.get("unexpected_stale"),
          examples=egate.get("unexpected_stale_examples"), documented=egate.get("documented_exceptions"))
    check("E_cutoff_violation_zero", fin.get("future_cutoff_violation_count") == 0,
          violations=fin.get("future_cutoff_violation_count"))
    check("E_snapshot_0924_parity", bool((e_shadow.get("parity") or {}).get("exact")), parity=e_shadow.get("parity"))
    decision = from_snapshot.get("decision") or {}
    check("E_h5_r1_top3_b2", bool((e_shadow.get("parity") or {}).get("exact")),
          h5_count=decision.get("h5_count"), candidates=decision.get("candidates"),
          r1_order=decision.get("r1_order"), selected=decision.get("selected"),
          high_breadth=decision.get("high_breadth"), final_exposure=decision.get("final_exposure"),
          note="the snapshot decision equals the live-cache decision field by field (parity)")
    check("E_no_real_orders", e_shadow.get("real_orders") == 0 and producer.get("real_order_requests") == 0,
          sim_entries=e_shadow.get("sim_entries"), entry_exit_requests=producer.get("entry_exit_requests"))
    book, state = _e_book(e_book, session)
    if e_book is not None and (e_shadow.get("paper_runtime") or {}).get("reason") != "E_PAPER_NOT_ENABLED":
        for name, body in execution_checks(state, book, session, decision or None, egate.get("authorized")).items():
            check(name, body.pop("pass"), **body)

    # -- shared -------------------------------------------------------------------------------
    clients = producer.get("clients") or {}
    status_429 = sum(c.get("status_429", 0) for c in clients.values())
    lanes = producer.get("lanes") or {}
    refused = sum(l.get("refused", 0) for l in producer.get("limiters", []))
    logical = sum(l.get("calls", 0) for l in lanes.values()) - refused
    attempts = sum(c.get("http_attempts", 0) for c in clients.values())
    check("S_single_app_key", producer.get("single_app_key") is True)
    check("S_duplicate_rolling_zero", producer.get("rolling_order_unique") is True,
          rolling=producer.get("rolling"))
    check("S_429", status_429 <= TOLERATED_429, status_429=status_429,
          statuses={k: c.get("http_statuses") for k, c in clients.items()},
          events={k: [e for e in c.get("events") or [] if e.get("status") == "429"] for k, c in clients.items()})
    resources = producer.get("resources") or {}
    check("S_resources_recorded", bool(resources.get("samples")), producer=resources,
          a_consumer_peak_rss_mb=consumer.get("peak_rss_mb"))
    check("S_a_and_e_do_not_block", fin.get("T0_delay_s") is not None and fin["T0_delay_s"] < 1.0,
          note="E's T0 is read off the clock; the A consumer is another process")
    calls = {"rolling": (producer.get("rolling") or {}).get("minute_calls"),
             "a_sweep": sweep.get("calls"), "e_finalization": fin.get("calls"),
             "listing_codes": producer.get("listing_codes"), "logical_chart_requests": logical,
             "http_attempts": attempts, "retries_estimate": attempts - logical, "deadline_refusals": refused,
             "per_phase": phases}
    failed = sorted(k for k, v in checks.items() if v["pass"] is False)
    verdicts = {}
    for group, prefix in (("A", "A_"), ("E", "E_"), ("COMMON", "S_")):
        bad = [k for k in failed if k.startswith(prefix)]
        verdicts[group] = {"verdict": "PASS" if not bad else "FAIL", "failed": bad}
    verdict = "PASS" if not failed else "FAIL"
    return {"session": day, "verdict": verdict, "verdicts": verdicts, "failed": failed, "checks": checks,
            "calls": calls, "generated_at": datetime.now().astimezone().isoformat()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="common producer shadow cutover live gate")
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--producer-out", type=Path, required=True)
    parser.add_argument("--consumer-out", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--e-book", type=Path)
    args = parser.parse_args(argv)
    body = gate(args.session, args.producer_out, args.consumer_out, args.snapshots, args.database, args.e_book)
    out = args.producer_out / args.session.isoformat() / "live_gate.json"
    out.write_text(json.dumps(body, indent=1, default=str) + "\n", encoding="utf-8")
    print(" ".join(f"{g}={v['verdict']}" for g, v in body["verdicts"].items()), f"failed={body['failed']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
