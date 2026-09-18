"""B HYBRID-S view smoke over USB-HIST-V2 raw (read-only, in memory, no API, no workspace write).

    PYTHONPATH=backend .venv/bin/python -m app.dev.smoke_b_hybrid_s_v2 --symbols AAPL,XYZ,...

For each symbol: every COMPLETE minute ledger in the local staging (or Common Raw) is parsed into
``SourceRow`` exactly as sent and passed through B's structural view (``dataset._validated_table``,
the same function ``materialize`` uses). The smoke requires raw rows == view rows (nothing dropped,
nothing synthesized) and view sessions == sessions with raw bars. Then every session with an
official per-symbol daily bar is audited with ``validate_sparse_session`` (post-session only, an
audit, never a feature) and the verdicts are counted. PIT: no bar may be dated after the grid end.
"""

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import gzip
import json
from pathlib import Path

from app.backtest.historical_store.raw_fetch import DAILY_DIR, MINUTE_DIR
from app.backtest.strategy_b.dataset import SourceRow, _validated_table, boundaries_for
from app.backtest.workspace.discovery import resolve_workspace_root
from app.dev.historical_v2 import GRID, OUT, STAGING
from app.integrations.massive.minute_bars import ET
from app.market.calendar import MarketCalendar
from app.strategy_b.config import SparseValidationConfig
from app.strategy_b.models import MomentumBar, OfficialDailyBar, Session
from app.strategy_b.sparse_session import validate_sparse_session
from app.strategy_b.split_adjustment import SplitRecord


def _bars(folder: Path) -> tuple[list[dict], list[dict]]:
    ledgers, bars = [], []
    for path in sorted(folder.glob("*.request.json")) if folder.is_dir() else ():
        ledger = json.loads(path.read_text())
        if ledger.get("status") != "COMPLETE":
            continue
        ledgers.append(ledger)
        for page in ledger["pages"]:
            bars.extend(json.loads(gzip.decompress((folder / page["file"]).read_bytes())).get("results") or ())
    return ledgers, sorted(bars, key=lambda b: b["t"])


def _splits(root: Path) -> dict[str, list[SplitRecord]]:
    body = json.loads(gzip.decompress(next((root / "market_data/raw/massive/splits").glob("*.json.gz")).read_bytes()))
    rows = body.get("results") if isinstance(body, dict) else body
    rows = rows if rows is not None else body.get("body", {}).get("results", [])
    out: dict[str, list[SplitRecord]] = {}
    for r in rows:
        try:
            out.setdefault(r["ticker"], []).append(
                SplitRecord(date.fromisoformat(r["execution_date"]), float(r["split_from"]), float(r["split_to"])))
        except (KeyError, ValueError):
            continue
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--workspace-root", type=Path, default=None)
    args = parser.parse_args()
    root = resolve_workspace_root(args.workspace_root)
    calendar = MarketCalendar()
    config = SparseValidationConfig()
    splits = _splits(root)
    results = {}
    for symbol in args.symbols.split(","):
        folder = STAGING / MINUTE_DIR / symbol
        if not folder.is_dir():
            folder = root / MINUTE_DIR / symbol
        ledgers, raw = _bars(folder)
        if not ledgers:
            results[symbol] = {"status": "NO_DATA"}
            continue
        start = min(date.fromisoformat(x["start"]) for x in ledgers)
        end = max(date.fromisoformat(x["end"]) for x in ledgers)
        rows = [SourceRow(datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc), b.get("o"), b.get("h"), b.get("l"),
                          b.get("c"), b.get("v"), b.get("vw"), b.get("n")) for b in raw]
        table, dropped, days, premarket = _validated_table(symbol, rows, start=start, end=end, calendar=calendar)
        raw_days = {datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).astimezone(ET).date() for b in raw}
        _, daily_raw = _bars(root / DAILY_DIR / symbol)
        daily = {datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).astimezone(ET).date(): b for b in daily_raw}
        by_day: dict[date, list[MomentumBar]] = {}
        et = table.column("timestamp_et").to_pylist()
        cols = {c: table.column(c).to_pylist() for c in ("open", "high", "low", "close", "volume", "session",
                                                           "vwap", "transactions")}
        for i, ts in enumerate(et):
            if cols["session"][i] != "REGULAR":
                continue
            local = ts.astimezone(ET)
            by_day.setdefault(local.date(), []).append(MomentumBar(
                local, cols["open"][i], cols["high"][i], cols["low"][i], cols["close"][i], cols["volume"][i],
                Session.REGULAR, vwap=cols["vwap"][i], transactions=cols["transactions"][i]))
        verdicts: Counter = Counter()
        for day in sorted(d for d in daily if start <= d <= end):
            bar = daily[day]
            if bar["v"] <= 0:
                continue
            validation = validate_sparse_session(
                by_day.get(day, []), OfficialDailyBar(day, bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]),
                boundaries_for(calendar, day), splits=splits.get(symbol, ()), config=config)
            verdicts[validation.verdict.value] += 1
        results[symbol] = {
            "range": [str(start), str(end)], "raw_rows": len(raw), "view_rows": table.num_rows, "dropped": dropped,
            "raw_sessions": len(raw_days), "view_sessions": len(days), "premarket_sessions": len(premarket),
            "rows_equal": len(raw) == table.num_rows and dropped == 0, "sessions_equal": raw_days == days,
            "pit_no_bar_after_grid": max(raw_days) <= GRID[1] if raw_days else True,
            "splits_in_range": [str(s.execution_date) for s in splits.get(symbol, ()) if start <= s.execution_date <= end],
            "daily_sessions": sum(1 for d in daily if start <= d <= end), "verdicts": dict(verdicts)}
    ok = all(r.get("rows_equal") and r.get("sessions_equal") and r.get("pit_no_bar_after_grid")
             for r in results.values() if r.get("status") != "NO_DATA")
    out = {"smoke": "B_HYBRID_S_V2", "verdict": "PASS" if ok else "FAIL", "symbols": results,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (OUT / "smoke_hybrid_s.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
