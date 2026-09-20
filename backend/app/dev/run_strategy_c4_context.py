"""C4-P1 context stage: build the event tables once and report what companyfacts are still needed."""

import argparse
import json
from pathlib import Path
import sys
import time

from app.backtest.strategy_c4_analog import run as c4_run
from app.backtest.strategy_c4_analog.rules import load_rules

OUT = Path("data/runtime/strategy_c4/coverage")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    def log(message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    rules = load_rules()
    inputs = c4_run.load_inputs(log)
    if args.rebuild or not c4_run.context_cache_path("event_tables.npz").exists():
        tables, periodic = c4_run.build_event_tables(inputs, rules, log)
        c4_run.save_event_tables(tables, periodic)
    else:
        tables, periodic = c4_run.load_event_tables(inputs.cik_names)
    targets = c4_run.xbrl_targets(periodic)
    report = {"ciks_covered": int(tables.covered.sum()),
              "ciks_with_periodic_events": len(periodic),
              "xbrl_documents_stored": len(c4_run.stores.stored_xbrl_ciks(c4_run.stores.StoreRoots())),
              "xbrl_documents_missing": len(targets)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "xbrl_targets.json").write_text(json.dumps(targets), encoding="utf-8")
    (OUT / "coverage_stage2.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    log(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
