"""Manual entry point (no timer, no deployment). Orders: none.

    PYTHONPATH=backend .venv/bin/python -m app.strategies.i_paper.run --store DIR --launch 2026-10-30   # once
    PYTHONPATH=backend .venv/bin/python -m app.strategies.i_paper.run --store DIR --as-of 2026-11-03
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import store as ST
from . import view as V


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--runtime", default="data/runtime")
    ap.add_argument("--sic-store", default=None)
    ap.add_argument("--launch", default=None, help="launch signal (last XNYS session of a month); writes launch.json once")
    ap.add_argument("--as-of", default=None)
    a = ap.parse_args(argv)
    store = Path(a.store)
    if a.launch:
        print(json.dumps(ST.launch(store, date.fromisoformat(a.launch), stores=V.Stores(Path(a.runtime))), indent=1)); return 0
    rec = ST.run(store, date.fromisoformat(a.as_of),
                 ST.frozen_compute(V.Stores(Path(a.runtime)), Path(a.sic_store) if a.sic_store else None, store=store))
    print(json.dumps({k: rec[k] for k in ("as_of", "appended", "events", "pending")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
