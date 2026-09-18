"""Run the C-M selection-only study (C-P3/C-P4) from the local raw cache. No API calls.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_c_selection

Reads ``data/runtime/strategy_c/raw`` (filled by ``fetch_strategy_c_selection_raw``) and the
pre-registered rules; refuses to run if the rules file no longer matches its declared checksum.
Writes ``data/runtime/strategy_c/runs/<run_id>/``.
"""

import argparse
from datetime import date
from pathlib import Path

from app.backtest.strategy_c_selection.rules import load_rules
from app.backtest.strategy_c_selection.run import RunInputs, execute
from app.dev.fetch_strategy_c_selection_raw import DEFAULT_ROOT, quarter_snapshot_dates, sessions_between
from app.market.calendar import MarketCalendar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--raw", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=Path("data/runtime/strategy_c/runs"))
    args = parser.parse_args()
    calendar = MarketCalendar()
    rules = load_rules()
    inputs = RunInputs(args.raw, tuple(sessions_between(calendar, args.start, args.end)),
                       tuple(quarter_snapshot_dates(calendar, args.start, args.end)), (args.start, args.end))
    execute(inputs, rules, args.out, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
