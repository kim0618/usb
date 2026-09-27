"""F0 first execution: ``PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_f0 [--workers N]``.

A second invocation with identical inputs writes ``runs/<run_id>/repeat-N`` and compares artifacts.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

from app.backtest.strategy_f0_regular_after import run as R

SKIP = {"manifest.json"}


def compare(first: Path, repeat: Path) -> dict:
    out = {}
    for f in sorted(first.iterdir()):
        if f.is_file() and f.name not in SKIP:
            other = repeat / f.name
            out[f.name] = other.is_file() and hashlib.sha256(f.read_bytes()).digest() == hashlib.sha256(other.read_bytes()).digest()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    log = lambda m: print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)
    try:
        out = R.execute(workers=args.workers, log=log)
    except R.PitFailure as exc:
        print(str(exc)); sys.exit(3)
    if out.name.startswith("repeat-"):
        result = compare(out.parent, out)
        (out / "reproduction.json").write_text(json.dumps({"identical": all(result.values()), "files": result},
                                                          indent=1, sort_keys=True) + "\n")
        log(f"REPRODUCTION identical={all(result.values())}")
    log(f"output {out}")


if __name__ == "__main__":
    main()
