"""Run the pre-registered C-V2A/C-V2B decomposition from the local C raw cache. No API calls.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_c_v2

Rebuilds the frozen C-M V1 study, refuses to continue unless it reproduces the baseline run,
then writes ``data/runtime/strategy_c/v2/runs/<run_id>/``.
"""

import argparse
from datetime import date
from pathlib import Path

from app.backtest.strategy_c_selection.rules import load_rules
from app.backtest.strategy_c_selection.run import RunInputs
from app.backtest.strategy_c_v2.rules import load_v2_rules
from app.backtest.strategy_c_v2.run import execute
from app.dev.fetch_strategy_c_selection_raw import DEFAULT_ROOT, quarter_snapshot_dates, sessions_between
from app.market.calendar import MarketCalendar

BASELINE = Path("data/runtime/strategy_c/runs/cmsel1-855b6a0ce64e3698fc74")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--raw", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--out", type=Path, default=Path("data/runtime/strategy_c/v2/runs"))
    args = parser.parse_args()
    calendar = MarketCalendar()
    v2, v2_checksum = load_v2_rules()
    inputs = RunInputs(args.raw, tuple(sessions_between(calendar, args.start, args.end)),
                       tuple(quarter_snapshot_dates(calendar, args.start, args.end)), (args.start, args.end))
    execute(inputs, load_rules(), v2, v2_checksum, args.baseline, args.out, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
