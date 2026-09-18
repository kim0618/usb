"""USB-HIST-V2 raw validation (read-only, no API): every COMPLETE ledger of a kind is re-read.

    PYTHONPATH=backend .venv/bin/python -m app.dev.validate_historical_v2 minute|daily [--symbols A,B]

Per ledger: page sha256 (file and raw body) against the ledger, gzip integrity, ``adjusted=false``,
timestamps strictly increasing across pages (no duplicate bar, no overlapping page), every bar
inside the requested range (and 04:00-20:00 ET for minute), OHLC ordering and non-negative volume,
row and session counts equal to the ledger. Silent truncation / pagination gaps: a session inside
the clipped request where grouped daily shows trades but the ticker aggregate has no bar. Ledgers
that are not COMPLETE and sessions in ``unavailable_rolling_window.json`` are counted separately.

A ledger that fails is recorded INVALID in ``invalid_ledgers.json`` (merged, never dropped by this
tool): nothing is overwritten or refetched, coverage audits treat its sessions as not available and
the V2 freeze refuses while the list is not empty. Clearing an entry is a deliberate manual step.
"""

import argparse
from collections import Counter
from datetime import date, datetime, time, timezone
import gzip
import hashlib
import json
from pathlib import Path

from app.backtest.historical_store.b_universe import grouped_matrix
from app.backtest.historical_store.raw_fetch import DAILY_DIR, MINUTE_DIR
from app.backtest.workspace.discovery import resolve_workspace_root
from app.dev.fetch_strategy_c_selection_raw import sessions_between
from app.dev.historical_v2 import GRID, OUT, STAGING
from app.integrations.massive.minute_bars import ET
from app.market.calendar import MarketCalendar


def check_ledger(folder: Path, ledger: dict, *, minute: bool) -> tuple[list[str], dict[str, int]]:
    """(problems, sessions -> bar count) for one COMPLETE ledger."""
    problems: list[str] = []
    per_day: Counter = Counter()
    start, end = date.fromisoformat(ledger["start"]), date.fromisoformat(ledger["end"])
    lo = datetime.combine(start, time(4, 0) if minute else time(0, 0), tzinfo=ET).timestamp() * 1000
    hi = datetime.combine(end, time(20, 0) if minute else time(23, 59), tzinfo=ET).timestamp() * 1000
    last, rows = None, 0
    for page in ledger["pages"]:
        packed = (folder / page["file"]).read_bytes()
        if hashlib.sha256(packed).hexdigest() != page["file_sha256"]:
            problems.append(f"{page['file']} file sha256 mismatch")
        raw = gzip.decompress(packed)
        if hashlib.sha256(raw).hexdigest() != page["raw_sha256"]:
            problems.append(f"{page['file']} raw sha256 mismatch")
        body = json.loads(raw)
        if body.get("adjusted") is not False:
            problems.append(f"{page['file']} adjusted is not false")
        for bar in body.get("results") or ():
            t = bar["t"]
            if last is not None and t <= last:
                problems.append(f"{page['file']} t={t} not after {last} (duplicate/overlap/order)")
            last = t
            if not lo <= t < hi:
                problems.append(f"{page['file']} t={t} outside the request")
            if not (bar["l"] <= min(bar["o"], bar["c"]) and max(bar["o"], bar["c"]) <= bar["h"]) or bar["v"] < 0:
                problems.append(f"{page['file']} t={t} invalid OHLCV")
            per_day[datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(ET).date().isoformat()] += 1
            rows += 1
    if rows != ledger["rows"]:
        problems.append(f"rows {rows} != ledger {ledger['rows']}")
    if len(per_day) != ledger.get("sessions_with_bars", len(per_day)):
        problems.append(f"sessions {len(per_day)} != ledger {ledger['sessions_with_bars']}")
    return problems[:20], dict(per_day)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("minute", "daily"))
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--workspace-root", type=Path, default=None)
    args = parser.parse_args()
    root = resolve_workspace_root(args.workspace_root)
    minute = args.kind == "minute"
    base = (STAGING / MINUTE_DIR) if minute else (root / DAILY_DIR)
    universe = {r["symbol"]: r for r in json.loads((OUT / "b_fetch_universe_q1.json").read_text())["symbols"]}
    wanted = set(args.symbols.split(",")) if args.symbols else None
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, *GRID)
    names, _, _, trades = grouped_matrix(root, sessions)
    col = {t: j for j, t in enumerate(names)}
    index = {s.isoformat(): i for i, s in enumerate(sessions)}
    report = Counter()
    problems: dict[str, list[str]] = {}
    gaps: dict[str, list[str]] = {}
    invalid_paths: dict[str, Path] = {}
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else ():
        symbol = folder.name
        if symbol not in universe or (wanted and symbol not in wanted):
            continue
        for path in sorted(folder.glob("*.request.json")):
            ledger = json.loads(path.read_text())
            if ledger.get("plan_kind") is None and not minute and ledger.get("collected_at", "") < "2026-09-18":
                continue  # A/V1 per-symbol daily, validated by V1
            if ledger.get("status") != "COMPLETE":
                report[f"ledger_{ledger.get('status')}"] += 1
                continue
            report["ledgers"] += 1
            report["rows"] += ledger["rows"]
            found, per_day = check_ledger(folder, ledger, minute=minute)
            if found:
                problems[path.name] = found
                invalid_paths[path.name] = path
            span = [s for s in sessions if ledger["start"] <= s.isoformat() <= ledger["end"]]
            report["symbol_sessions"] += len(span)
            report["sessions_with_bars"] += len(per_day)
            j = col.get(symbol)
            missing = [s.isoformat() for s in span if s.isoformat() not in per_day and j is not None
                       and trades[index[s.isoformat()], j] == trades[index[s.isoformat()], j]
                       and trades[index[s.isoformat()], j] > 0]
            if missing:
                gaps[symbol] = missing
                report["grouped_traded_but_no_bar"] += len(missing)
            report["symbols_" + ("clipped" if ledger.get("unavailable_rolling_window") else "full")] += 1
    unavailable = json.loads((OUT / "unavailable_rolling_window.json").read_text()) \
        if (OUT / "unavailable_rolling_window.json").is_file() else {}
    kind_key = "b_minute" if minute else "b_per_symbol_daily"
    report["unavailable_rolling_window_sessions"] = sum(len(v) for v in unavailable.get(kind_key, {}).values())
    out = {"kind": args.kind, "root": str(base), "summary": dict(report), "problem_ledgers": len(problems),
           "problems": dict(list(problems.items())[:50]), "truncation_suspects": dict(list(gaps.items())[:50]),
           "truncation_suspect_symbols": len(gaps), "verdict": "PASS" if not problems else "FAIL",
           "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if problems:
        book_path = OUT / "invalid_ledgers.json"
        book = json.loads(book_path.read_text()) if book_path.is_file() else {}
        for name, found in problems.items():
            book[str(invalid_paths[name])] = {"kind": args.kind, "problems": found,
                                              "detected_at": out["validated_at"]}
        book_path.write_text(json.dumps(book, indent=1, sort_keys=True) + "\n")
    (OUT / f"validation_{args.kind}.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k not in {"problems", "truncation_suspects"}}, indent=1))


if __name__ == "__main__":
    main()
