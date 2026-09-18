"""Coverage per strategy and the USB-HIST-V2 freeze (no API calls).

    PYTHONPATH=backend .venv/bin/python -m app.dev.build_historical_snapshot_v2 --dry-run
    PYTHONPATH=backend .venv/bin/python -m app.dev.build_historical_snapshot_v2 [--accept-missing b_minute]

V2 is defined by STRATEGY_REQUIREMENTS_V2: A/B/C/D requirements, the B fetch universe, and what the
store holds against them. A required kind with MISSING sessions refuses the freeze unless it is
named in ``--accept-missing`` (then it is written as a limitation). Sessions that left the Basic
rolling window before they were fetched are UNAVAILABLE, not MISSING. USB-HIST-V1 is never
touched; V2 lists V1's files as members alongside the new ones.

B minute lives in the local staging root, not on Drive. Its pages and ledgers are members with
``storage_tier = LOCAL_STAGING`` and a ``local_staging/`` path prefix (relative to the staging root),
verified against their ledger sha256 like every Drive member; nothing is recorded as moved to Drive.
"""

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from app.backtest.historical_store import session_audit
from app.backtest.historical_store.coverage import existing_sessions, legacy_entries, minute_universe_v1
from app.backtest.historical_store.raw_fetch import DAILY_DIR, MINUTE_DIR, read_ledger
from app.backtest.historical_store.reference_fetch import DAILY_REFERENCE_DIR
from app.backtest.historical_store.snapshot import (
    SNAPSHOT_DIR, build, canonical, common_raw_members, reference_daily_members,
)
from app.backtest.workspace.discovery import resolve_workspace_root
from app.dev import b_coverage_audit
from app.dev.build_historical_snapshot import FREEZE_PATH, REUSE_MATRIX, grouped_index
from app.dev.fetch_strategy_c_selection_raw import sessions_between
from app.dev.historical_v2 import A_SETTLEMENT, GRID, PRIORITY, STAGING, UNAVAILABLE, build_plan, window_start
from app.market.calendar import MarketCalendar

V1 = "USB-HIST-V1"
STAGING_PREFIX = "local_staging/"


def raw_totals(base: Path) -> dict:
    """Symbols, answered sessions, sessions with bars and rows over the COMPLETE ledgers under ``base``."""
    out = {"symbols": 0, "ledgers": 0, "requested_sessions": 0, "sessions_with_bars": 0, "rows": 0}
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else ():
        ledgers = [x for x in map(read_ledger, folder.glob("*.request.json")) if x and x.get("status") == "COMPLETE"]
        out["symbols"] += bool(ledgers)
        for x in ledgers:
            out["ledgers"] += 1
            out["requested_sessions"] += x.get("requested_sessions", 0)
            out["sessions_with_bars"] += x.get("sessions_with_bars", 0)
            out["rows"] += x.get("rows", 0)
    return out


def staging_members() -> list[dict]:
    """B minute pages + ledgers in the local staging, sha256-verified, tagged LOCAL_STAGING."""
    files, _ = common_raw_members(STAGING, MINUTE_DIR, "MINUTE_RAW")
    return [{**f, "path": STAGING_PREFIX + f["path"], "storage_tier": "LOCAL_STAGING"} for f in files]


def split_missing(plan: dict, oldest: date) -> dict:
    """MISSING vs UNAVAILABLE (older than the current window) per kind, in sessions or dates."""
    out = {}
    for kind in PRIORITY:
        items = plan["plans"][kind]
        if kind == "reference_daily":
            out[kind] = {"missing": len(items), "unavailable": 0}
            continue
        unavailable = sum(len([d for d in sessions_between(MarketCalendar(), r.start, r.end) if d < oldest])
                          for r in items)
        out[kind] = {"missing": sum(r.sessions for r in items) - unavailable, "unavailable": unavailable}
    return out


def evaluation_ranges(plan: dict, sessions: list[date], settlement_present: bool) -> dict:
    at = lambda i: sessions[i].isoformat()  # noqa: E731
    ranges = plan["requirements"]["ranges"]
    a_end = 500 if settlement_present else 499
    return {
        "raw_data_range": {"grid": [at(0), at(500)], "a_settlement_minute": A_SETTLEMENT.isoformat(),
                           "reference_as_of": ["2024-09-16", at(499)]},
        "A": {"data": [at(0), A_SETTLEMENT.isoformat() if settlement_present else at(500)],
              "warmup_start": at(0), "eval": [at(ranges["A"]["eval_start_index"]), at(a_end)],
              "forward_end": A_SETTLEMENT.isoformat() if settlement_present else at(500),
              "limitation": ranges["A"]["pre_grid_needed"]},
        "B": {"data": [at(0), at(500)], "warmup_start": at(0),
              "eval": [at(ranges["B"]["eval_start_index"]), at(500)],
              "eval_full_warmup": [at(ranges["B"]["eval_start_full_warmup_index"]), at(500)],
              "forward_end": at(500), "limitation": ranges["B"]["pre_grid_needed"]},
        "C": {"data": [at(0), at(500)], "warmup_start": at(0),
              "eval": [at(ranges["C"]["eval_start_index"]), at(ranges["C"]["eval_end_index"])],
              "eval_secondary": [at(ranges["C"]["eval_secondary_start_index"]), at(ranges["C"]["eval_end_index"])],
              "forward_end": at(500)},
        "D": {"data": [at(0), at(500)], "warmup_start": at(0),
              "eval": [at(ranges["D"]["eval_start_index"]), at(ranges["D"]["eval_end_index"])],
              "forward_end": at(500)},
    }


def minute_audit(root: Path, sessions: list[date], calendar: MarketCalendar) -> tuple[list[dict], dict]:
    """Session Audit over every symbol that has minute data (legacy STRICT, Common Raw, local staging)."""
    folder = root / MINUTE_DIR
    common_symbols = {p.name for p in folder.iterdir() if p.is_dir()} if folder.is_dir() else set()
    staged = STAGING / MINUTE_DIR
    staged_symbols = {p.name for p in staged.iterdir() if p.is_dir()} if staged.is_dir() else set()
    legacy_dirs = {s: p for s, _, _, p, _ in legacy_entries(root, "minute")}
    symbols = sorted(common_symbols | staged_symbols | set(legacy_dirs))
    trades, _ = grouped_index(root, [s for s in sessions if s <= GRID[1]], set(symbols))
    legacy_m, common_m = existing_sessions(root, "minute", sessions, symbols)
    _, staged_m = existing_sessions(root, "minute", sessions, symbols, raw_root=STAGING)
    rows = []
    for symbol in symbols:
        parts_legacy = session_audit.legacy_parts(root, legacy_dirs[symbol]) if symbol in legacy_dirs else {}
        parts_common = session_audit.common_raw_parts(root, symbol, calendar)
        parts_staged = session_audit.common_raw_parts(STAGING, symbol, calendar) if symbol in staged_symbols else {}
        for source, days, parts in (("LEGACY_STRICT", legacy_m[symbol], parts_legacy),
                                    ("COMMON_RAW", common_m[symbol] - legacy_m[symbol], parts_common),
                                    ("LOCAL_STAGING", staged_m[symbol] - common_m[symbol] - legacy_m[symbol],
                                     parts_staged)):
            rows += session_audit.audit_rows(symbol, source, parts, sorted(days), calendar,
                                             grouped_trades=trades[symbol], split_days=set())
    summary = {"symbols": len(symbols), "symbol_sessions": len(rows),
               "with_bars": sum(not r["empty_session"] for r in rows),
               "regular_complete": sum(r["regular_complete"] for r in rows)}
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--snapshot-id", default="USB-HIST-V2")
    parser.add_argument("--accept-missing", nargs="*", default=[], choices=PRIORITY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = resolve_workspace_root(args.workspace_root)
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, *GRID)
    plan = build_plan(root)
    oldest = window_start()
    missing = split_missing(plan, oldest)
    settlement_present = plan["kinds"]["a_settlement_minute"]["missing_symbol_sessions"] == 0
    v1 = json.loads((root / SNAPSHOT_DIR / V1 / "coverage.json").read_text())
    coverage = {
        "grid": [sessions[0].isoformat(), sessions[-1].isoformat(), len(sessions)],
        "window_start_at_build": oldest.isoformat(),
        "grouped_daily": v1["grouped_daily"], "reference_tickers": v1["reference_tickers"], "splits": v1["splits"],
        "a_minute_universe_v1": {"minute": v1["minute"], "per_symbol_daily": v1["per_symbol_daily"]},
        "kinds": {kind: {**{k: plan["kinds"][kind][k] for k in plan["kinds"][kind] if k != "capacity_gate"},
                         **missing[kind]} for kind in PRIORITY},
        "strategies": {
            "A": {"minute": "V1 30 symbols 100% answered; STRICT regular-complete 14,808 / 14,898 traded",
                  "per_symbol_daily": "V1 30 symbols 100%", "settlement_2026_09_17": settlement_present},
            "B": {kind: missing[kind] for kind in ("b_per_symbol_daily", "b_minute", "reference_daily")},
            "C": {"grouped": "501/501", "reference_quarterly": "8/8", "splits": "3,328 records", "missing": 0},
            "D": {"grouped": "501/501", "reference_quarterly": "8/8", "splits": "3,328 records", "missing": 0},
        },
    }
    blocking = [k for k in PRIORITY if missing[k]["missing"] and k not in args.accept_missing]
    limitations = [f"{k}: {missing[k]['missing']} MISSING accepted as a limitation" for k in args.accept_missing] + [
        f"{k}: {missing[k]['unavailable']} sessions left the Basic window before fetch (UNAVAILABLE)"
        for k in PRIORITY if missing[k]["unavailable"]] + [
        "No data before 2024-09-17 (Basic rolling window): A/B warmup for the first 20-26 sessions is UNAVAILABLE.",
        "B reference cadence: quarterly snapshots + daily D-1 snapshots (the 8 quarterly dates keep the C-seed form without list_date).",
        "Historical quotes/spread and halt feed: UNAVAILABLE on Basic.",
        "SEC filings (C-E): outside this store."]
    ranges = evaluation_ranges(plan, sessions, settlement_present)
    print(json.dumps({"coverage_kinds": coverage["kinds"], "blocking": blocking, "evaluation_ranges": ranges},
                     indent=1), flush=True)
    invalid_book = UNAVAILABLE.with_name("invalid_ledgers.json")
    invalid = json.loads(invalid_book.read_text()) if invalid_book.is_file() else {}
    if invalid:
        print(json.dumps({"invalid_ledgers": len(invalid)}), flush=True)
    b_cov = b_coverage_audit.compute(root, ["b_minute", "b_per_symbol_daily"])
    if b_cov.pop("critical"):
        limitations.append(f"B: {b_cov['scope_loss']} SCOPE sessions left the window before fetch (see b_coverage)")
    print(json.dumps({"b_coverage": b_cov}, indent=1), flush=True)
    if args.dry_run:
        return
    if invalid:
        raise SystemExit(f"refused: {len(invalid)} ledgers are INVALID (invalid_ledgers.json)")
    if blocking:
        raise SystemExit(f"refused: required kinds still MISSING {blocking}; fetch them or --accept-missing")
    audit_sessions = sessions + ([A_SETTLEMENT] if settlement_present else [])
    audit_rows, audit_summary = minute_audit(root, audit_sessions, calendar)
    freeze = json.loads(FREEZE_PATH.read_text())
    universe = minute_universe_v1()
    universe["b_fetch_universe"] = {"id": plan["b_universe"]["universe_id"], "digest": plan["b_universe"]["digest"],
                                    "symbols": plan["b_universe"]["unique_symbols"]}
    coverage["minute"] = {**v1["minute"], "symbol_sessions_with_bars": audit_summary["with_bars"],
                          "session_audit": audit_summary}
    extra_fields = {
        "raw_data_range": ranges["raw_data_range"], "evaluation_ranges": {k: ranges[k] for k in "ABCD"},
        "strategy_requirements_digest": plan["requirements"]["digest"],
        "b_fetch_universe_digest": plan["b_universe"]["digest"],
        "reference_daily_dates": plan["kinds"]["reference_daily"]["existing_dates"],
        "limitations": limitations, "supersedes": V1,
        "provider": "Massive Stocks Basic",
        "storage": {"DRIVE_COMMON": {"root": str(root), "holds": "Common Raw minute/per-symbol daily (A, B daily), "
                                     "grouped, reference quarterly + daily, splits, C freeze"},
                    "DRIVE_LEGACY_REFERENCE": {"root": str(root), "holds": "A legacy STRICT parquet (collector manifest), "
                                               "reference only, unchanged"},
                    "LOCAL_STAGING": {"root": str(STAGING), "path_prefix": STAGING_PREFIX, "holds": "B minute"}},
        "b_coverage": b_cov,
        "totals": {"minute_drive": raw_totals(root / MINUTE_DIR), "minute_local_staging": raw_totals(STAGING / MINUTE_DIR),
                   "per_symbol_daily_drive": raw_totals(root / DAILY_DIR),
                   "reference_daily_dates": plan["kinds"]["reference_daily"]["existing_dates"],
                   "grouped_sessions": v1["grouped_daily"], "splits": v1["splits"]},
    }
    snapshot = build(root, snapshot_id=args.snapshot_id, sessions=sessions, freeze=freeze, universe=universe,
                     audit_rows=audit_rows, coverage=coverage,
                     reuse_matrix=REUSE_MATRIX + [{"data": "USB-HIST-V1 members", "decision": "REFERENCE_ONLY",
                                                   "note": "V2 lists the same paths; nothing copied"}],
                     extra_files=reference_daily_members(root, DAILY_REFERENCE_DIR) + staging_members(),
                     extra_documents={"strategy_requirements.json": plan["requirements"],
                                      "b_fetch_universe.json": plan["b_universe"],
                                      "evaluation_ranges.json": ranges, "limitations.json": limitations,
                                      "unavailable_rolling_window.json":
                                          json.loads(UNAVAILABLE.read_text()) if UNAVAILABLE.is_file() else {}},
                     extra_fields=extra_fields)
    print(json.dumps(snapshot, indent=1))
    assert sha256(canonical(coverage)).hexdigest() == snapshot["coverage_digest"]


if __name__ == "__main__":
    main()
