"""C-4 internal screen CLI."""

import argparse
from pathlib import Path
import sys
import time

from app.backtest.strategy_c4_analog.run import RUNS, execute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RUNS)
    parser.add_argument("--rebuild-context", action="store_true")
    parser.add_argument("--smoke", action="store_true",
                        help="run every pre-result check and stop before the returns")
    args = parser.parse_args()

    def log(message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    summary = execute(args.out, rebuild_context=args.rebuild_context,
                      stop_after_gate=args.smoke, log=log)
    print(summary.get("decision"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
