"""Run the pre-registered C-E0 event split of the frozen C-M0 candidates. No API calls.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_c_e0

Needs the SEC event raw store (`app.dev.fetch_strategy_c_e0_events`) and the frozen baseline run.
Refuses to start unless both declaration files still hash to their declared checksums and the C
raw digest equals the baseline's. Writes `data/runtime/strategy_c/e0/runs/<run_id>/`.
"""

import argparse
from datetime import date
from pathlib import Path

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_c_e0.rules import load_declaration
from app.backtest.strategy_c_e0.run import E0Inputs, execute
from app.backtest.strategy_c_selection.rules import load_rules
from app.dev.fetch_strategy_c_e0_events import BASELINE, C_RAW
from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
from app.market.calendar import MarketCalendar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--raw", type=Path, default=C_RAW)
    parser.add_argument("--events", type=Path, default=sec_store.DEFAULT_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--out", type=Path, default=Path("data/runtime/strategy_c/e0/runs"))
    parser.add_argument("--snapshot-id", default="USB-HIST-V1")
    args = parser.parse_args()
    calendar = MarketCalendar()
    declaration = load_declaration()
    inputs = E0Inputs(args.raw, args.events, args.baseline,
                      tuple(sessions_between(calendar, args.start, args.end)),
                      tuple(quarter_snapshot_dates(calendar, args.start, args.end)),
                      (args.start, args.end))
    execute(inputs, load_rules(), declaration, args.out, snapshot_id=args.snapshot_id,
            log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
