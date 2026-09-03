"""Explicit opt-in, read-only Kiwoom live smoke check."""

import argparse

from app.core.config import get_settings
from app.market.factory import build_kiwoom_provider


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiwoom market-data-only smoke")
    parser.add_argument("symbol")
    parser.add_argument("--exchange", choices=("NA", "ND", "NY"), default=None)
    args = parser.parse_args()
    settings = get_settings()
    print(f"KIWOOM: {settings.kiwoom_env.upper()} MARKET DATA / ORDERING DISABLED")
    print("BROKER: SIMULATION")
    if not settings.run_kiwoom_live_smoke:
        print("LIVE SMOKE: NOT RUN — RUN_KIWOOM_LIVE_SMOKE is not enabled")
        return 0
    if not settings.has_kiwoom_credentials:
        print("LIVE SMOKE: NOT RUN — credentials not configured")
        return 0
    provider = build_kiwoom_provider(settings)
    if args.exchange:
        provider._exchanges[args.symbol.strip().upper()] = args.exchange  # smoke-only explicit input
    token = provider.client._auth.access_token()
    assert token
    print("Token: PASS")
    quote = provider.client.quote(args.symbol.upper(), provider._exchange(args.symbol.upper()))
    print("Quote: PASS" if quote.get("stk_cd") else "Quote: FAIL")
    daily = provider.get_daily_bars([args.symbol])
    print("Daily: PASS" if daily else "Daily: FAIL")
    print("Actual order: NOT EXECUTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
