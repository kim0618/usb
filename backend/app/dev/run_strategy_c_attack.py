"""Run the pre-registered C-ATTACK-V0 right-tail harvest test. No API calls.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_c_attack
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_c_attack --engineering-only

Needs the C raw cache (or the USB-HIST-V1 common view) and, for the gate, the frozen C-E0 status
table `candidate_status.parquet` of run ce01-234478e8f7ff27570e13. Without that table the run
stops as BLOCKED_M_ONLY_SOURCE before a single trade is simulated. `--engineering-only` runs the
simulator PIT/determinism checks on all C-M0 candidates and writes no economics.
"""

import argparse
from datetime import date
from pathlib import Path

from app.backtest.strategy_c_attack.rules import load_rules
from app.backtest.strategy_c_attack.run import AttackInputs, execute
from app.backtest.strategy_c_selection.rules import load_rules as load_c_rules
from app.dev.fetch_strategy_c_e0_events import C_RAW
from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
from app.market.calendar import MarketCalendar

STATUS = Path("data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13/candidate_status.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--raw", type=Path, default=C_RAW)
    parser.add_argument("--status", type=Path, default=STATUS)
    parser.add_argument("--status-source", choices=("A", "B"), default="A",
                        help="A: byte copy of the C-E0 run table; B: recomputed by strategy_c_e0 over an EDGAR store")
    parser.add_argument("--out", type=Path, default=Path("data/runtime/strategy_c_attack/runs"))
    parser.add_argument("--engineering-only", action="store_true")
    args = parser.parse_args()
    calendar = MarketCalendar()
    inputs = AttackInputs(args.raw, tuple(sessions_between(calendar, args.start, args.end)),
                          tuple(quarter_snapshot_dates(calendar, args.start, args.end)),
                          (args.start, args.end), args.status, args.status_source)
    execute(inputs, load_rules(), load_c_rules(), args.out, engineering_only=args.engineering_only,
            log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
