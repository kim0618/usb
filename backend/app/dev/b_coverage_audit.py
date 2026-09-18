"""B coverage by role: actual scope sessions vs RVOL warmup/lookback sessions (read-only, no API).

    PYTHONPATH=backend .venv/bin/python -m app.dev.b_coverage_audit [--kinds b_minute,b_per_symbol_daily]

The fetch range of a symbol is [first S(D) - 20, last S(D)]. A session in that range is SCOPE when the
symbol is in S(D) that day (B_FETCH_UNIVERSE_Q1 membership, recomputed PIT) and WARMUP otherwise
(RVOL lookback before the first scope day, or a gap between scope days that RVOL still reads).
Each required (symbol, session) is AVAILABLE (a COMPLETE ledger answered it: legacy, Common Raw or
local staging), UNAVAILABLE (``unavailable_rolling_window.json``) or PENDING (still fetchable).
PENDING sessions older than the current window are counted apart: they are lost but not yet logged.
Ledgers marked INVALID (``invalid_ledgers.json``) do not count as AVAILABLE.

The first SCOPE loss writes ``CRITICAL_SCOPE_LOSS.json`` next to the ledger (never overwritten; the
collector is not stopped). Output: ``data/runtime/common_hist/v2/b_coverage_audit.json``.
"""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from app.backtest.historical_store import b_universe
from app.backtest.historical_store.coverage import existing_sessions
from app.backtest.workspace.discovery import resolve_workspace_root
from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
from app.dev.historical_v2 import GRID, OUT, STAGING, UNAVAILABLE, window_start
from app.market.calendar import MarketCalendar

RAW_KIND = {"b_minute": "minute", "b_per_symbol_daily": "per_symbol_daily"}
INVALID = OUT / "invalid_ledgers.json"


def invalid_sessions(sessions: list[date]) -> dict[tuple[str, str], set[date]]:
    """(raw kind, symbol) -> sessions of ledgers marked INVALID by validate_historical_v2."""
    out: dict[tuple[str, str], set[date]] = {}
    for path in (json.loads(INVALID.read_text()) if INVALID.is_file() else {}):
        ledger = json.loads(Path(path).read_text())
        start, end = date.fromisoformat(ledger["start"]), date.fromisoformat(ledger["end"])
        out.setdefault((ledger["kind"], ledger["symbol"]), set()).update(s for s in sessions if start <= s <= end)
    return out


def compute(root: Path, kinds: list[str]) -> dict:
    """The audit document (no file written)."""
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, *GRID)
    snapshots = quarter_snapshot_dates(calendar, date(2024, 9, 16), GRID[1])
    universe, member, names, _ = b_universe.build(root, sessions, snapshots)
    col = {t: j for j, t in enumerate(names)}
    ranges = {r["symbol"]: (date.fromisoformat(r["start"]), date.fromisoformat(r["end"])) for r in universe["symbols"]}
    symbols = sorted(ranges)
    unavailable = json.loads(UNAVAILABLE.read_text()) if UNAVAILABLE.is_file() else {}
    invalid = invalid_sessions(sessions)
    oldest = window_start()
    report = {"universe_digest": universe["digest"], "window_start": oldest.isoformat(), "kinds": {}}
    critical = []
    for kind in kinds:
        raw = RAW_KIND[kind]
        legacy, common = existing_sessions(root, raw, sessions, symbols)
        staged = existing_sessions(root, raw, sessions, symbols, raw_root=STAGING)[1] if kind == "b_minute" \
            else {s: set() for s in symbols}
        lost_book = {s: {date.fromisoformat(d) for d in v} for s, v in unavailable.get(kind, {}).items()}
        count = {role: {"required": 0, "available": 0, "unavailable": 0, "pending": 0, "pending_outside_window": 0}
                 for role in ("scope", "warmup")}
        scope_lost: list[tuple[str, str]] = []
        for symbol in symbols:
            lo, hi = ranges[symbol]
            have = (legacy[symbol] | common[symbol] | staged[symbol]) - invalid.get((raw, symbol), set())
            lost = lost_book.get(symbol, set())
            j = col[symbol]
            for i, day in enumerate(sessions):
                if not lo <= day <= hi:
                    continue
                role = "scope" if member[i, j] else "warmup"
                c = count[role]
                c["required"] += 1
                if day in have:
                    c["available"] += 1
                elif day in lost:
                    c["unavailable"] += 1
                    if role == "scope":
                        scope_lost.append((symbol, day.isoformat()))
                else:
                    c["pending"] += 1
                    c["pending_outside_window"] += day < oldest
        row = {f"b_{role}_{k}_sessions": v for role, c in count.items() for k, v in c.items()}
        row["scope_sessions_lost_sample"] = scope_lost[:20]
        row["scope_coverage_of_required"] = round(count["scope"]["available"] / count["scope"]["required"], 6)
        row["warmup_coverage_of_required"] = round(count["warmup"]["available"] / count["warmup"]["required"], 6)
        row["fetchable_missing_sessions"] = sum(c["pending"] - c["pending_outside_window"] for c in count.values())
        report["kinds"][kind] = row
        if scope_lost:
            critical.append({"kind": kind, "count": len(scope_lost), "first": scope_lost[:50]})
    report["first_scope_session"] = universe["first_scope_session"]
    report["scope_loss"] = sum(r["b_scope_unavailable_sessions"] for r in report["kinds"].values())
    report["critical"] = critical
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kinds", default="b_minute,b_per_symbol_daily")
    parser.add_argument("--workspace-root", type=Path, default=None)
    args = parser.parse_args()
    report = compute(resolve_workspace_root(args.workspace_root), args.kinds.split(","))
    critical = report.pop("critical")
    report["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if critical:
        flag = OUT / "CRITICAL_SCOPE_LOSS.json"
        if not flag.exists():
            flag.write_text(json.dumps({"first_detected": report["at"], "losses": critical}, indent=1) + "\n")
        report["critical_warning"] = "SCOPE_SESSION_LOST > 0"
    (OUT / "b_coverage_audit.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
