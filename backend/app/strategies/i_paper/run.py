"""Manual entry point (no timer, no deployment). Orders: none.

    PYTHONPATH=backend .venv/bin/python -m app.strategies.i_paper.run --store DIR --launch 2026-10-30   # once
    PYTHONPATH=backend .venv/bin/python -m app.strategies.i_paper.run --store DIR --as-of 2026-11-03
    ... --as-of auto --history DIR   # timer form: the last XNYS session before today (ET)
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from . import store as ST
from . import view as V


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--runtime", default="data/runtime")
    ap.add_argument("--sic-store", default=None)
    ap.add_argument("--launch", default=None, help="launch signal (last XNYS session of a month); writes launch.json once")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD, or 'auto' = previous XNYS session in ET")
    ap.add_argument("--history", action="append", default=[], help="I-owned read-only grouped history dir (repeatable)")
    a = ap.parse_args(argv)
    store = Path(a.store)
    stores = V.Stores(Path(a.runtime), history=tuple(Path(h) for h in a.history))
    if a.launch:
        print(json.dumps(ST.launch(store, date.fromisoformat(a.launch), stores=stores), indent=1)); return 0
    if a.as_of == "auto":
        from app.backtest.collector.range import previous_trading_day
        from app.market.calendar import MarketCalendar
        as_of = previous_trading_day(MarketCalendar(), datetime.now(timezone.utc))
    else:
        as_of = date.fromisoformat(a.as_of)
    rec = ST.run(store, as_of, ST.frozen_compute(stores, Path(a.sic_store) if a.sic_store else None, store=store))
    print(json.dumps({k: rec[k] for k in ("as_of", "appended", "events", "pending")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
