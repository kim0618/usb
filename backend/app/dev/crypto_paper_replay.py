"""Replay a D2 historical window through the paper engine.

Commands come from a JSON file so a replay is reproducible and reviewable: there is no rule,
no signal and no generator here. D4 replays decisions someone wrote down; it does not make any.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

DEFAULT_HISTORICAL = Path("data/runtime/crypto/BTCUSDT/historical")


def utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def newest(directory: Path) -> Path:
    files = sorted(directory.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no dataset in {directory}")
    return files[-1]


def main() -> None:
    import sys
    sys.path.insert(0, "backend")
    from dataclasses import replace
    from app.crypto.paper.analytics import build_trades, reconcile, summarize
    from app.crypto.paper.fees import describe, load_scenarios
    from app.crypto.paper.historical import HistoricalTapeBuilder, as_tape_records
    from app.crypto.paper.replay import MODES, ACCELERATED, ReplayDriver, write_tape
    from app.crypto.terminal.api import load_run_config

    parser = argparse.ArgumentParser(description="Replay a historical window through the paper engine")
    parser.add_argument("--run-config", type=Path, default=Path("data/runtime/crypto/paper/run_config.json"))
    parser.add_argument("--historical-root", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--start", type=utc_ms)
    parser.add_argument("--end", type=utc_ms)
    parser.add_argument("--commands", type=Path,
                        help="JSON list of engine commands; START is added if absent")
    parser.add_argument("--mode", choices=MODES, default=ACCELERATED)
    parser.add_argument("--speed", default="1")
    parser.add_argument("--out", type=Path, default=Path("data/runtime/crypto/paper/replays"))
    parser.add_argument("--label", default=None)
    parser.add_argument("--fee-scenario", default=None,
                        help="fee sensitivity scenario name; default uses the run config's own fees")
    parser.add_argument("--fee-verification",
                        type=Path, default=Path("data/runtime/crypto/reference/fee_source_verification_v1.json"),
                        help="captured Bybit fee page record the scenarios are built from")
    parser.add_argument("--list-fee-scenarios", action="store_true")
    args = parser.parse_args()

    if args.list_fee_scenarios:
        print(json.dumps(describe(load_scenarios(args.fee_verification)), indent=2))
        return
    missing = [name for name, value in (("--start", args.start), ("--end", args.end),
                                        ("--commands", args.commands)) if value is None]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")

    config, tiers = load_run_config(args.run_config)
    fee_note: dict | None = None
    if args.fee_scenario:
        scenarios = load_scenarios(args.fee_verification)
        if args.fee_scenario not in scenarios:
            parser.error(f"unknown fee scenario {args.fee_scenario}; "
                         f"available: {', '.join(scenarios)}")
        scenario = scenarios[args.fee_scenario]
        # A scenario replaces the fee schedule and nothing else, so two runs differ only by
        # the number under test. The config is frozen, hence a rebuilt copy rather than a poke.
        config = replace(config, fees=replace(
            config.fees, taker_rate=scenario.taker_rate, maker_rate=scenario.maker_rate,
            version=scenario.version, source=scenario.source,
            effective_date=scenario.effective_date, basis=scenario.basis))
        fee_note = scenario.view()
    research_floor = utc_ms("2021-02-01T00:00:00Z")
    if args.start < research_floor:
        parser.error("Research Window V1 starts at 2021-02-01T00:00:00Z; see "
                     "docs/crypto/CRYPTO_RESEARCH_WINDOW_V1.md")

    def optional_series(name: str) -> Path | None:
        try:
            return newest(args.historical_root / name)
        except FileNotFoundError:
            return None   # a missing series is absent from the tape, never filled in

    builder = HistoricalTapeBuilder(
        kline_path=newest(args.historical_root / "kline_1m"),
        mark_path=newest(args.historical_root / "mark_1m"),
        funding_path=optional_series("funding"),
        index_path=optional_series("index_1m"),
        open_interest_path=optional_series("open_interest_5m"))
    market = builder.build(start_ms=args.start, end_ms=args.end)
    if not market:
        parser.error("the window produced no market records")

    commands = json.loads(args.commands.read_text())
    if not any(item.get("command") == "START" for item in commands):
        commands = [{"command": "START", "ts_ms": args.start}] + commands
    records = as_tape_records(market, commands)

    label = args.label or f"replay-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = args.out / label
    run_dir.mkdir(parents=True, exist_ok=True)
    write_tape(records, run_dir / "input.jsonl")

    driver = ReplayDriver(config=config, tiers=tiers, records=records, mode=args.mode,
                          speed=Decimal(args.speed), ledger_path=run_dir / "ledger.jsonl")
    driver.run()

    account = driver.engine.account
    summary = summarize(driver.engine.ledger.events,
                        starting_capital=account.starting_capital_usdt)
    summary["source"] = "HISTORICAL_REPLAY"
    summary["synthetic_book"] = builder.source_note()["book"]
    summary["data_sources"] = builder.source_note()
    summary["fees_applied"] = {
        "taker_rate": str(config.fees.taker_rate), "maker_rate": str(config.fees.maker_rate),
        "version": config.fees.version, "basis": config.fees.basis,
        "scenario": fee_note,
    }
    summary["window"] = {"start_ms": args.start, "end_ms": args.end,
                         "market_records": len(market), "tape_records": len(records)}
    summary["reconciliation"] = reconcile(
        driver.engine.ledger.events, starting_capital=account.starting_capital_usdt,
        account_realized=account.realized_pnl, account_fees=account.cumulative_fees,
        account_funding=account.cumulative_funding_paid)
    (run_dir / "performance.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (run_dir / "trades.json").write_text(json.dumps(
        [trade.view() for trade in build_trades(driver.engine.ledger.events)],
        indent=2, sort_keys=True) + "\n")

    account.assert_invariants(driver.engine.quote.mark_price)
    print(json.dumps({
        "run_dir": run_dir.as_posix(), "mode": args.mode, "progress": driver.progress,
        "trades": summary["trades"], "net_pnl": summary["net_pnl"],
        "ending_equity": summary["ending_equity"],
        "reconciled": all(value for key, value in summary["reconciliation"].items()
                          if "match" in key),
        "source": summary["source"],
    }, indent=2))


if __name__ == "__main__":
    main()
