"""Run the pre-registered EQM-V0 event quality split of the frozen C-M0 candidates. No API calls.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_eqm_v0

Needs the C raw store, the C-E0 SEC event store, the C-E0 baseline runs and the EQM-V0 XBRL store
plus its feature table (`app.dev.run_strategy_eqm_v0_features`). Refuses to start unless the
declaration still hashes to `0cd09ba2…` and the XBRL store still hashes to its declared digest.
Writes `data/runtime/strategy_eqm/v0/runs/<run_id>/`.
"""

import argparse
from datetime import date
from pathlib import Path

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_c_selection.rules import load_rules
from app.backtest.strategy_eqm_v0 import xbrl_store
from app.backtest.strategy_eqm_v0.rules import load_declaration
from app.backtest.strategy_eqm_v0.run import EqmInputs, execute
from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
from app.market.calendar import MarketCalendar

C_RAW = Path("data/runtime/strategy_c/raw")
BASELINE = Path("data/runtime/strategy_c/runs/cmsel1-855b6a0ce64e3698fc74")
C_E0_RUN = Path("data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--raw", type=Path, default=C_RAW)
    parser.add_argument("--events", type=Path, default=sec_store.DEFAULT_ROOT)
    parser.add_argument("--facts", type=Path, default=xbrl_store.DEFAULT_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--c-e0-run", type=Path, default=C_E0_RUN)
    parser.add_argument("--features", type=Path, required=True,
                        help="the quality feature directory, named after the XBRL store digest")
    parser.add_argument("--out", type=Path, default=Path("data/runtime/strategy_eqm/v0/runs"))
    parser.add_argument("--snapshot-id", default="USB-HIST-V1")
    args = parser.parse_args()
    calendar = MarketCalendar()
    inputs = EqmInputs(args.raw, args.events, args.facts, args.baseline, args.c_e0_run, args.features,
                       tuple(sessions_between(calendar, args.start, args.end)),
                       tuple(quarter_snapshot_dates(calendar, args.start, args.end)),
                       (args.start, args.end))
    execute(inputs, load_rules(), load_declaration(), args.out, snapshot_id=args.snapshot_id,
            log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
