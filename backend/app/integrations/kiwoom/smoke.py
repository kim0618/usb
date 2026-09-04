"""Explicit opt-in, read-only Kiwoom live smoke check.

The output is deliberately structural: credentials, tokens, and raw provider
payloads are never printed or persisted.
"""

import argparse
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import MarketDataError
from app.market.factory import build_kiwoom_provider
from app.integrations.kiwoom.mapping import map_daily_bar, map_metadata, map_minute_bar
from app.scanner.config import ScannerConfig


def _present(value: object) -> str:
    return "PASS" if value not in (None, "") else "MISSING"


def _error(label: str, error: MarketDataError) -> None:
    print(f"{label}: FAIL — {error.code}: {error}")


def _safety_gate(settings: Any) -> bool:
    expected = (
        settings.kiwoom_mode == "market_data_only"
        and settings.market_data_provider == "kiwoom"
        and settings.broker_provider == "simulation"
    )
    print(f"KIWOOM_MODE: {settings.kiwoom_mode}")
    print(f"MARKET_DATA_PROVIDER: {settings.market_data_provider}")
    print(f"BROKER_PROVIDER: {settings.broker_provider}")
    print(f"Safety gate: {'PASS' if expected else 'FAIL'}")
    return expected


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    result = body.get("result_list", [])
    return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []


def _map_daily_rows(symbol: str, rows: list[dict[str, Any]], received_at: datetime) -> list[Any]:
    mapped = []
    for row in rows:
        try:
            mapped.append(map_daily_bar(symbol, row, received_at))
        except MarketDataError as error:
            if error.code != "FUTURE_DATA":
                raise
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiwoom market-data-only smoke")
    parser.add_argument("symbol", nargs="?", default="AAPL")
    parser.add_argument("--exchange", choices=("NA", "ND", "NY"), default=None)
    args = parser.parse_args()
    settings = get_settings()
    print(f"KIWOOM: {settings.kiwoom_env.upper()} MARKET DATA / ORDERING DISABLED")
    print(f"KIWOOM_APP_KEY: {'SET' if settings.kiwoom_app_key else 'NOT SET'}")
    print(f"KIWOOM_APP_SECRET: {'SET' if settings.kiwoom_app_secret else 'NOT SET'}")
    print(f"RUN_KIWOOM_LIVE_SMOKE: {'ENABLED' if settings.run_kiwoom_live_smoke else 'DISABLED'}")
    if not settings.has_kiwoom_credentials:
        print("LIVE SMOKE: NOT RUN — credentials not configured")
        return 0
    if not settings.run_kiwoom_live_smoke:
        print("LIVE SMOKE: NOT RUN — RUN_KIWOOM_LIVE_SMOKE is not enabled")
        return 0
    if not _safety_gate(settings):
        print("LIVE SMOKE: NOT RUN — safety gate rejected configuration")
        return 0
    provider = build_kiwoom_provider(settings)
    symbol = args.symbol.strip().upper()
    if args.exchange:
        provider._exchanges[symbol] = args.exchange  # smoke-only explicit input
    exchange = provider._exchange(symbol)
    client = provider.client
    try:
        token = client._auth.access_token()
        assert token and client._auth.expires_at is not None
        cached = client._auth.access_token() == token
        print("OAuth: PASS")
        print("expires_at: parsed")
        print(f"token cache: {'PASS' if cached else 'FAIL'}")

        try:
            metadata = client.metadata(symbol, exchange)
            print(f"Metadata: {'PASS' if metadata.get('stk_cd') else 'PARTIAL'}")
            print(f"metadata company_name: {_present(metadata.get('stk_enm') or metadata.get('stk_nm'))}")
            print(f"metadata exchange: {_present(metadata.get('stex_tp'))}")
            print(f"metadata market_cap field: {_present(metadata.get('mac'))} — unit UNCONFIRMED")
        except MarketDataError as error:
            _error("Metadata", error)
            print("Metadata result: PARTIAL — optional capability")

        quote = client.quote(symbol, exchange)
        canonical_metadata = map_metadata(symbol, quote, datetime.now(timezone.utc))
        quote_ok = bool(quote.get("stk_cd") and canonical_metadata.symbol == symbol)
        print(f"Quote: {'PASS' if quote_ok else 'FAIL'}")
        print("Quote canonical mapping: PASS" if quote_ok else "Quote canonical mapping: FAIL")

        daily_body = {"stex_tp": exchange, "stk_cd": symbol, "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        first = client.request("usa06012", "/api/us/chart", daily_body)
        daily_rows = _rows(first.body)
        second_rows: list[dict[str, Any]] = []
        if first.continuation and first.next_key:
            second = client.request("usa06012", "/api/us/chart", daily_body, continuation=first)
            second_rows = _rows(second.body)
        received_at = datetime.now(timezone.utc)
        first_mapped = _map_daily_rows(symbol, daily_rows, received_at)
        mapped_daily = first_mapped + _map_daily_rows(symbol, second_rows, received_at)
        required = ScannerConfig().required_history
        unique_dates = {bar.trading_date for bar in mapped_daily}
        if len({bar.trading_date for bar in first_mapped}) >= required:
            depth = "SINGLE CALL SUFFICIENT"
        elif first.continuation:
            depth = "PAGINATION REQUIRED"
        else:
            depth = "INSUFFICIENT / SECOND SOURCE REQUIRED"
        print(f"Daily: {'PASS' if mapped_daily else 'FAIL'} — first_rows={len(daily_rows)}, sampled_total={len(unique_dates)}")
        print(f"Daily depth: {depth} (required={required})")
        print(f"Pagination: {'OBSERVED' if first.continuation else 'NOT OBSERVED'}")
        print("Adjusted price semantics: UNCONFIRMED (request option upd_stkpc_tp=1)")

        try:
            minute_body = {"stex_tp": exchange, "stk_cd": symbol, "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}
            minute_page = client.request("usa06011", "/api/us/chart", minute_body)
            mapped_minute = []
            for row in _rows(minute_page.body):
                try:
                    mapped_minute.append(map_minute_bar(symbol, row, received_at))
                except MarketDataError as error:
                    if error.code != "FUTURE_DATA":
                        raise
            print(f"Minute: {'PASS' if mapped_minute else 'PARTIAL'}")
        except MarketDataError as error:
            _error("Minute", error)
            print("Minute result: PARTIAL — optional capability")
        print("Session: DERIVED from ET timestamp; calendar validation required")
        print("WebSocket: NOT RUN — optional implementation deferred")
    except MarketDataError as error:
        _error("Live smoke", error)
        print(f"Kiwoom order requests executed = {client.order_request_count}")
        return 1
    print(f"Kiwoom order requests executed = {client.order_request_count}")
    if client.order_request_count != 0:
        print("CRITICAL FAIL — order request observed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
