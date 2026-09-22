"""E-MAX-F0: forward-shadow preflight and session readiness (read-only; fetches nothing).

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f0 preflight
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f0 readiness

``readiness`` writes ``data/runtime/strategy_e_max/forward/readiness/readiness_<today ET>.json``.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from app.backtest.strategy_e1_forward import layout
from app.backtest.workspace.discovery import resolve_workspace_root
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward import readiness as RD, rules as F
from app.strategy_e_v1_1 import context

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "data/runtime/strategy_e_max/forward/readiness"


def preflight() -> dict:
    rules = F.load_rules()
    return {"rules": F.RULES_CANONICAL_SHA256, "provenance": F.provenance_closure(rules)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-F0 forward shadow preflight / readiness")
    parser.add_argument("command", choices=("preflight", "readiness"))
    parser.add_argument("--workspace-root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        pre = preflight()
    except Exception as error:
        print(f"E-MAX-F0 BLOCKED — UPSTREAM INTEGRITY: {error}", file=sys.stderr)
        return 2
    if not pre["provenance"]["checks"]["pass"]:
        print(f"E-MAX-F0 BLOCKED — UPSTREAM INTEGRITY: {pre['provenance']}", file=sys.stderr)
        return 2
    if args.command == "preflight":
        print(json.dumps(pre, indent=1))
        return 0
    calendar = MarketCalendar("America/New_York")
    today = datetime.now(context.ET).date()
    root = resolve_workspace_root(args.workspace_root)
    inventory, notes = RD.probe(root, calendar=calendar)
    sessions = RD.forward_sessions(date_after(today), calendar)
    rows = RD.matrix(sessions, inventory, today_et=today, calendar=calendar)
    payload = {"today_et": today.isoformat(), "forward_holdout_start": layout.FORWARD_HOLDOUT_START.isoformat(),
               "preflight": pre, "inventory": notes, "sessions": rows, "summary": RD.summary(rows)}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"readiness_{today.isoformat()}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "inventory": notes}, indent=1))
    for row in rows:
        print(row["session"], row["state"], ",".join(row["reasons"]))
    print(f"written {path}")
    return 0


def date_after(day):
    from datetime import timedelta
    return day + timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
