from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.crypto.historical import KNOWN_EARLIEST_MS, collect_series, fetch_risk_limit
from app.crypto.models import SERIES, parse_risk_tiers
from app.crypto.quality import analyze_kline, overlap_window
from app.crypto.realtime import run_realtime
from app.crypto.rest import BybitPublicClient


def utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise argparse.ArgumentTypeError("timestamp must include timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bybit PUBLIC-only BTCUSDT D2 collector")
    parser.add_argument("command", choices=("risk", "history", "realtime", "quality", "overlap"))
    parser.add_argument("--root", type=Path, default=Path("data/runtime/crypto/BTCUSDT"))
    parser.add_argument("--dataset", choices=tuple(SERIES))
    parser.add_argument("--start", type=utc_ms)
    parser.add_argument("--end", type=utc_ms)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    if args.command == "risk":
        with BybitPublicClient() as client: payload = fetch_risk_limit(client)
        tiers = parse_risk_tiers(payload)
        print(json.dumps({"schema": sorted(payload["result"]["list"][0]), "tiers": [{"riskId": t.risk_id, "lowerExclusive": str(t.lower_exclusive), "riskLimitValue": str(t.upper_inclusive), "initialMargin": str(t.initial_margin), "maintenanceMargin": str(t.maintenance_margin), "maxLeverage": str(t.max_leverage)} for t in tiers]}, indent=2))
    elif args.command == "history":
        if not args.dataset or args.end is None: parser.error("history requires --dataset and --end")
        start = args.start if args.start is not None else KNOWN_EARLIEST_MS[args.dataset]
        with BybitPublicClient() as client: result = collect_series(client, dataset=args.dataset, start_ms=start, end_ms=args.end, root=args.root)
        print(result)
    elif args.command == "realtime":
        print(asyncio.run(run_realtime(args.root, duration_seconds=args.duration)))
    elif args.command == "quality":
        if args.path is None: parser.error("quality requires --path")
        report = analyze_kline(args.path)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report["research_window_candidates"] | report["stream_integrity"], indent=2))
    else:
        manifests = [json.loads(path.read_text()) for path in sorted((args.root / "manifest").glob("*.json"))]
        window = overlap_window(manifests)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"manifests": manifests, "overlap": window}, indent=2, sort_keys=True) + "\n")
        print(json.dumps(window, indent=2))


if __name__ == "__main__": main()
