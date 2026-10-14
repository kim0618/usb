"""Create a PAPER run config, or print what is missing.

Every provenance field is a required argument. There is no default fee and no default FX rate:
the project holds no exchange account, so both are assumptions and an assumption has to be
written down by whoever makes it.
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path("data/runtime/crypto/paper")

import sys as _sys
_sys.path.insert(0, "backend")
from app.crypto.paper.config import DEFAULT_STARTING_CAPITAL_KRW  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a crypto PAPER run config")
    parser.add_argument("--run-id", default=None, help="default: paper-<UTC timestamp>")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--starting-capital-krw",
                        default=str(DEFAULT_STARTING_CAPITAL_KRW),
                        help=f"default: {DEFAULT_STARTING_CAPITAL_KRW}")
    parser.add_argument("--fx-krw-per-usdt", required=True)
    parser.add_argument("--fx-source", required=True)
    parser.add_argument("--fx-asof-utc", required=True)
    parser.add_argument("--fee-version", required=True)
    parser.add_argument("--fee-taker-rate", required=True)
    parser.add_argument("--fee-maker-rate", required=True)
    parser.add_argument("--fee-source", required=True)
    parser.add_argument("--fee-effective-date", required=True)
    parser.add_argument("--fee-basis", required=True,
                        choices=("ASSUMED_PUBLIC_NON_VIP", "OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED"))
    parser.add_argument("--slippage-model", required=True, choices=("NONE", "FIXED_BPS"))
    parser.add_argument("--slippage-bps", required=True)
    parser.add_argument("--leverage", required=True)
    parser.add_argument("--risk-limit-path", default=None,
                        help="default: the newest captured /v5/market/risk-limit response")
    args = parser.parse_args()

    risk_limit = args.risk_limit_path
    if risk_limit is None:
        captured = sorted(glob.glob("data/runtime/crypto/BTCUSDT/reference/risk_limit_*.json"))
        if not captured:
            parser.error("no captured risk-limit response; pass --risk-limit-path")
        risk_limit = captured[-1]

    run_id = args.run_id or f"paper-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    config = {
        "run_id": run_id,
        "starting_capital_krw": args.starting_capital_krw,
        "fx_krw_per_usdt": args.fx_krw_per_usdt,
        "fx_source": args.fx_source,
        "fx_asof_utc": args.fx_asof_utc,
        "fee_version": args.fee_version,
        "fee_taker_rate": args.fee_taker_rate,
        "fee_maker_rate": args.fee_maker_rate,
        "fee_source": args.fee_source,
        "fee_effective_date": args.fee_effective_date,
        "fee_basis": args.fee_basis,
        "slippage_model": args.slippage_model,
        "slippage_bps": args.slippage_bps,
        "leverage": args.leverage,
        "risk_limit_path": risk_limit,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    args.root.mkdir(parents=True, exist_ok=True)
    target = args.root / "run_config.json"
    target.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")

    # Fail loudly here rather than at terminal startup.
    import sys
    sys.path.insert(0, "backend")
    from app.crypto.terminal.api import load_run_config
    loaded, tiers = load_run_config(target)
    print(json.dumps({
        "run_config": target.as_posix(), "run_id": loaded.run_id,
        "starting_capital_usdt": str(loaded.starting_capital_usdt),
        "risk_tiers": len(tiers.tiers),
        "risk_limit_sha256": tiers.source_sha256,
        "maintenance_margin_continuity_breaks": tiers.verify_continuity(),
    }, indent=2))


if __name__ == "__main__":
    main()
