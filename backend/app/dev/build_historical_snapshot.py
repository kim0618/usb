"""Build the Session Audit, the 2-year Coverage Matrix and freeze USB-HIST-V1 (no API calls).

    PYTHONPATH=backend .venv/bin/python -m app.dev.build_historical_snapshot --dry-run
    PYTHONPATH=backend .venv/bin/python -m app.dev.build_historical_snapshot

Reads the Common Raw store, the C raw freeze and the legacy manifest (read-only). ``--dry-run``
prints the coverage and writes nothing to the workspace.
"""

import argparse
from collections import Counter
from datetime import date
import gzip
import json
from pathlib import Path

from app.backtest.historical_store import session_audit
from app.backtest.historical_store.coverage import existing_sessions, legacy_entries, minute_universe_v1
from app.backtest.historical_store.raw_fetch import MINUTE_DIR, read_ledger
from app.backtest.historical_store.snapshot import build
from app.backtest.workspace.discovery import resolve_workspace_root
from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
from app.market.calendar import MarketCalendar

FREEZE_PATH = Path("data/runtime/common_hist/STRATEGY_C_RAW_FREEZE_V1.json")
B_SCOPE_PATH = Path("data/runtime/common_hist/b_scope_estimate.json")

REUSE_MATRIX = [
    {"data": "C grouped daily 2024-09-16..2026-09-16 (502 files)", "source": "local strategy_c/raw",
     "decision": "COPY", "target": "market_data/raw/massive/grouped_daily/", "note": "501 usable + 1 NOT_AVAILABLE"},
    {"data": "C CS reference snapshots (8 quarterly)", "source": "local strategy_c/raw", "decision": "COPY",
     "target": "market_data/raw/massive/reference_tickers/"},
    {"data": "C market-wide splits 2024-09-16..2026-09-16", "source": "local strategy_c/raw", "decision": "COPY",
     "target": "market_data/raw/massive/splits/"},
    {"data": "A minute_bars COMPLETE (30 symbols + SPY/SPCX 21 sessions)", "source": "Drive legacy",
     "decision": "REFERENCE_ONLY", "note": "Legacy STRICT; counted as existing coverage, never re-fetched or moved"},
    {"data": "A daily_bars COMPLETE (101 symbols, 2025-08-11..2026-09-15)", "source": "Drive legacy",
     "decision": "REFERENCE_ONLY", "note": "MASSIVE_TICKER_AGGREGATE authority; existing per-symbol daily coverage"},
    {"data": "A minute/daily FAILED manifest entries (96)", "source": "Drive manifest", "decision": "DO_NOT_USE",
     "note": "no files on disk (orphans 0); rows stay as history"},
    {"data": "RU v2 reference/grouped 2025-08-08..2025-09-12 (25 files)", "source": "Drive research_universe",
     "decision": "REFERENCE_ONLY", "note": "results identical to C grouped on all 25 dates (duplicate); RU v2 artifact, kept"},
    {"data": "RU v2 reference/tickers CS_2025-09-12, details 160", "source": "Drive research_universe",
     "decision": "REFERENCE_ONLY", "note": "RU v2 selection evidence; not a quarterly common snapshot"},
    {"data": "RU v2 eligibility_ledger (local)", "source": "local research_universe_v2", "decision": "REFERENCE_ONLY"},
    {"data": "massive_feasibility AAPL 2025-09-15..2026-09-15 scratch", "source": "local", "decision": "DO_NOT_USE",
     "note": "superseded by legacy AAPL COMPLETE; storage cleanup candidate"},
    {"data": "massive_spike / daily_basis probe JSON", "source": "local", "decision": "DO_NOT_USE",
     "note": "probe evidence only"},
    {"data": "B sparse_minute_bars smoke (14 symbols x 6 sessions)", "source": "session scratch 2026-09-17",
     "decision": "DO_NOT_USE", "note": "scratch no longer exists; nothing to reuse or dedupe"},
    {"data": "per-symbol daily vs grouped daily", "source": "both", "decision": "CONFLICT",
     "note": "values differ (see authority contract); kept as two authorities, never merged"},
]


def grouped_index(root: Path, sessions: list[date], symbols: set[str]) -> tuple[dict, dict]:
    trades: dict[str, dict[date, float]] = {s: {} for s in symbols}
    present: dict[str, set[date]] = {s: set() for s in symbols}
    for session in sessions:
        path = root / "market_data/raw/massive/grouped_daily" / str(session.year) / f"{session.isoformat()}.json.gz"
        body = json.loads(gzip.decompress(path.read_bytes()))["body"]
        for row in body["results"]:
            if row["T"] in symbols:
                present[row["T"]].add(session)
                trades[row["T"]][session] = row.get("n")
    return trades, present


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 17))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--snapshot-id", default="USB-HIST-V1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = resolve_workspace_root(args.workspace_root)
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, args.start, args.end)
    freeze = json.loads(FREEZE_PATH.read_text())
    universe = minute_universe_v1()
    symbols = universe["symbols"]
    trades, present = grouped_index(root, sessions, set(symbols))
    splits = json.loads(gzip.decompress(next((root / "market_data/raw/massive/splits").glob("*.json.gz"))
                                        .read_bytes()))["results"]
    split_days: dict[str, set[date]] = {}
    for row in splits:
        split_days.setdefault(row["ticker"], set()).add(date.fromisoformat(row["execution_date"]))

    legacy_minute_dirs = {s: p for s, _, _, p, _ in legacy_entries(root, "minute")}
    legacy_m, common_m = existing_sessions(root, "minute", sessions, symbols)
    legacy_d, common_d = existing_sessions(root, "per_symbol_daily", sessions, symbols)
    audit, per_symbol = [], {}
    for symbol in symbols:
        legacy_parts = session_audit.legacy_parts(root, legacy_minute_dirs[symbol]) if symbol in legacy_minute_dirs else {}
        common_parts = session_audit.common_raw_parts(root, symbol, calendar)
        for source, days, parts in (("LEGACY_STRICT", legacy_m[symbol], legacy_parts),
                                    ("COMMON_RAW", common_m[symbol] - legacy_m[symbol], common_parts)):
            audit += session_audit.audit_rows(symbol, source, parts, sorted(days), calendar,
                                              grouped_trades=trades[symbol], split_days=split_days.get(symbol, set()))
        missing = [s for s in sessions if s not in legacy_m[symbol] | common_m[symbol]]
        per_symbol[symbol] = {"traded_sessions": len(present[symbol]), "missing_minute_sessions": len(missing),
                              "missing_daily_sessions": len([s for s in sessions if s not in legacy_d[symbol] | common_d[symbol]])}
    legacy_rows = 0
    for symbol, start, end, relative, rows in legacy_entries(root, "minute"):
        if symbol in symbols and start >= sessions[0] and end <= sessions[-1]:
            legacy_rows += rows
    minute_ledgers = [read_ledger(p) for p in sorted((root / MINUTE_DIR).rglob("*.request.json"))]
    status = Counter(l["status"] for l in minute_ledgers)
    with_bars = [r for r in audit if not r["empty_session"]]
    traded = sum(len(present[s]) for s in symbols)
    answered_m = sum(len(legacy_m[s] | common_m[s]) for s in symbols)
    answered_d = sum(len(legacy_d[s] | common_d[s]) for s in symbols)
    required = len(sessions) * len(symbols)
    ref_dates = quarter_snapshot_dates(calendar, date(2024, 9, 16), args.end)
    pct = lambda a, b: round(100.0 * a / b, 3) if b else None  # noqa: E731
    coverage = {
        "window": [sessions[0].isoformat(), sessions[-1].isoformat()], "sessions": len(sessions),
        "grouped_daily": {"required": len(sessions), "existing": freeze["grouped_usable_sessions"],
                          "missing": len(sessions) - freeze["grouped_usable_sessions"],
                          "coverage_pct": pct(freeze["grouped_usable_sessions"], len(sessions)),
                          "outside_window": freeze["not_available"]},
        "reference_tickers": {"required": len(ref_dates), "existing": len(ref_dates),
                              "missing": 0, "coverage_pct": 100.0, "dates": [d.isoformat() for d in ref_dates],
                              "cadence": "quarterly first session; between snapshots PIT uses the latest earlier one"},
        "splits": {"required": 1, "existing": 1, "missing": 0, "coverage_pct": 100.0, "records": len(splits),
                   "range": "2024-09-16..2026-09-16"},
        "per_symbol_daily": {"universe": universe["universe_id"], "required": required, "existing": answered_d,
                             "missing": required - answered_d, "coverage_pct": pct(answered_d, required),
                             "legacy": sum(map(len, legacy_d.values())), "common_raw": sum(map(len, common_d.values()))},
        "minute": {"universe": universe["universe_id"], "required": required, "existing": answered_m,
                   "missing": required - answered_m, "coverage_pct": pct(answered_m, required),
                   "traded_symbol_sessions": traded,
                   "symbol_sessions_with_bars": len(with_bars),
                   "coverage_of_traded_pct": pct(len(with_bars), traded),
                   "regular_complete": sum(r["regular_complete"] for r in audit),
                   "regular_incomplete_with_bars": sum((not r["regular_complete"]) and not r["empty_session"] for r in audit),
                   "api_loss_suspect": sum(r["api_loss_suspect"] for r in audit),
                   "empty_sessions": sum(r["empty_session"] for r in audit),
                   "corporate_action_suspect": sum(r["corporate_action_suspect"] for r in audit),
                   "legacy_sessions": sum(map(len, legacy_m.values())),
                   "common_raw_sessions": sum(map(len, common_m.values())),
                   "legacy_rows_in_window": legacy_rows,
                   "common_raw_rows": sum(l.get("rows", 0) for l in minute_ledgers),
                   "common_raw_ledgers": dict(status)},
        "per_symbol": per_symbol,
        "b_scope_estimate": json.loads(B_SCOPE_PATH.read_text()) if B_SCOPE_PATH.exists() else None,
    }
    print(json.dumps({k: v for k, v in coverage.items() if k not in {"per_symbol"}}, indent=1))
    if args.dry_run:
        return
    snapshot = build(root, snapshot_id=args.snapshot_id, sessions=sessions, freeze=freeze, universe=universe,
                     audit_rows=audit, coverage=coverage, reuse_matrix=REUSE_MATRIX)
    print(json.dumps(snapshot, indent=1))


if __name__ == "__main__":
    main()
