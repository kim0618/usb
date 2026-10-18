"""The live gate of the one-session common-producer shadow cutover. Read-only, after the session.

    PYTHONPATH=backend python -m app.dev.verify_ae_shadow --session 2026-10-19 \
        --producer-out <run root> --consumer-out <A consumer run root> --snapshots <snapshot root> \
        [--database <paper sqlite>]

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


def gate(session: date, producer_out: Path, consumer_out: Path, snapshots: Path,
         database: Path | None = None) -> dict[str, Any]:
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
    check("E_no_execution", e_shadow.get("orders") == 0 and e_shadow.get("simbroker_used") is False
          and e_shadow.get("book_written") is False and producer.get("orders") == 0)

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
          statuses={k: c.get("http_statuses") for k, c in clients.items()})
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
    verdict = ("COMMON PRODUCER SHADOW PASS" if not failed else "COMMON PRODUCER SHADOW FAIL")
    return {"session": day, "verdict": verdict, "failed": failed, "checks": checks, "calls": calls,
            "generated_at": datetime.now().astimezone().isoformat()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="common producer shadow cutover live gate")
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--producer-out", type=Path, required=True)
    parser.add_argument("--consumer-out", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args(argv)
    body = gate(args.session, args.producer_out, args.consumer_out, args.snapshots, args.database)
    out = args.producer_out / args.session.isoformat() / "live_gate.json"
    out.write_text(json.dumps(body, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"{body['verdict']} failed={body['failed']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
