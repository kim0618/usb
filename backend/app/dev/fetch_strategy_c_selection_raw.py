"""Fetch the raw Massive inputs of the C-M selection study into a local cache.

    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_strategy_c_selection_raw --plan-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_strategy_c_selection_raw

Local only, serial, resumable (a cached file costs zero requests). Default cache:
``data/runtime/strategy_c/raw`` (git-ignored). Run it only when no other process is using
the same Massive key, or accept that both slow down.
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

from app.backtest.strategy_c_selection.raw_fetch import fetch_plan, grouped_path, tickers_path
from app.core.config import get_settings
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import MassiveAggregatesClient, MassiveConfigurationError
from app.market.calendar import MarketCalendar

DEFAULT_ROOT = Path("data/runtime/strategy_c/raw")


def sessions_between(calendar: MarketCalendar, start: date, end: date) -> list[date]:
    days, cursor = [], start
    while cursor <= end:
        if calendar.is_trading_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def quarter_snapshot_dates(calendar: MarketCalendar, start: date, end: date) -> list[date]:
    """First XNYS session of every calendar quarter that starts inside ``(start, end]``."""
    dates = []
    year, month = start.year, ((start.month - 1) // 3 + 1) * 3 + 1
    while True:
        if month > 12:
            year, month = year + 1, month - 12
        first = date(year, month, 1)
        if first > end:
            return dates
        while not calendar.is_trading_day(first):
            first += timedelta(days=1)
        dates.append(first)
        month += 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 16))
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="default: T-1 ET")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--spacing-seconds", type=float, default=13.0,
                        help="seconds between HTTP attempts (Basic allows 5/min; 12.0 sits on the edge)")
    args = parser.parse_args()
    calendar = MarketCalendar()
    end = args.end or calendar.previous_trading_day(date.today())
    sessions = sessions_between(calendar, args.start, end)
    snapshots = quarter_snapshot_dates(calendar, args.start, end)
    missing_grouped = [s for s in sessions if not grouped_path(args.root, s).exists()]
    missing_tickers = [d for d in snapshots if not tickers_path(args.root, d).exists()]
    print(f"range={sessions[0]}..{sessions[-1]} sessions={len(sessions)} missing_grouped={len(missing_grouped)}")
    print(f"ticker_snapshots={[d.isoformat() for d in snapshots]} missing={len(missing_tickers)}")
    if args.plan_only:
        return
    key = get_settings().massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise MassiveConfigurationError("MISSING_API_KEY", "MASSIVE_API_KEY is not configured")
    client = MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / args.spacing_seconds))
    fetch_plan(client, args.root, sessions=sessions, ticker_dates=snapshots,
               split_range=(args.start, end), log=lambda m: print(m, flush=True))
    print(f"done http_total={client.accounting.http_requests} statuses={dict(client.accounting.status_codes)} "
          f"retries={client.accounting.retries}", flush=True)


if __name__ == "__main__":
    main()
