"""Offline runner: a handful of timestamps in, decision JSON out.

This exists to show the engine works end to end on real D2 data and to produce the golden
snapshots. It computes no forward return, no PnL, no trade sequence and no performance figure of
any kind, and it never places or simulates an order.

    PYTHONPATH=backend python -m app.crypto.research.d6.runner --ts 2024-03-05T12:00:00Z
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from . import data as D
from . import decision as DEC
from .contract import load as load_contract
from .model import HISTORICAL, StrategyState

#: 1441 closes for f_rv24h, plus the 30 day bucket window, plus the day the decision sits in.
LOOKBACK_BARS = 31 * 1440 + 1441

DEFAULT_EQUITY_USDT = 7440.48  # D4 run_config starting capital; a size input, not a result


def parse_ts(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp() * 1000)


def format_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decide_at(grid, contract, ts_ms: int, *, equity: float = DEFAULT_EQUITY_USDT,
              state: StrategyState | None = None) -> DEC.Decision:
    index = D.index_of(grid, ts_ms)
    window = D.window_at(grid, index, LOOKBACK_BARS)
    market = D.market_at(grid, index)
    state = state or StrategyState(equity=equity)
    if state.equity is None:
        state = StrategyState(**{**state.__dict__, "equity": equity})
    return DEC.decide(contract, window, market, state, mode=HISTORICAL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FDN-V1 offline decision runner (no PnL)")
    parser.add_argument("--ts", action="append", required=True,
                        help="UTC bar open time, e.g. 2024-03-05T12:00:00Z (repeatable)")
    parser.add_argument("--equity", type=float, default=DEFAULT_EQUITY_USDT)
    parser.add_argument("--out", default=None, help="write a JSON array here")
    args = parser.parse_args(argv)

    contract = load_contract()
    grid = D.load_inputs()

    rows = []
    for text in args.ts:
        decision = decide_at(grid, contract, parse_ts(text), equity=args.equity)
        rows.append(decision.as_dict())
        print(f"{format_ts(decision.timestamp_ms)}  {decision.decision:4s}  "
              f"score={decision.long_score:3d}  {','.join(decision.reason_codes) or 'ENTRY'}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(rows, fh, sort_keys=True, indent=1)
        print(f"wrote {len(rows)} decisions to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
