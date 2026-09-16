"""Local CLI for the NON-TRADING Massive historical data source spike.

Default: one symbol (AAPL), 1-minute aggregates, for the most recent XNYS session whose
extended hours have fully ended (20:00 ET). ``--symbols`` and ``--sessions`` widen this
to a bounded validation run (at most 3 symbols x 5 sessions): every symbol is checked
over the same completed sessions and a summary with mechanical gates is printed. An
in-progress session is never requested. There is no database, Parquet store, Kiwoom
call, or order path here.

MASSIVE_API_KEY is read from the repository-root ``.env`` through ``Settings``. When it
is missing or blank the run stops with ``NOT RUN: MISSING_API_KEY`` before any HTTP
request. Output is counts, ET timestamps, and verdicts only: never the key, a request
header, a request URL, or a response payload. ``--save-raw`` also writes the sanitized
response bodies under the git-ignored ``data/runtime/massive_spike/``.

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_massive_spike
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_massive_spike --symbols AAPL AMD ORCL --sessions 5
"""

import argparse
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import fmean
import time
from typing import Any

import httpx

from app.core.config import PROJECT_ROOT, Settings
from app.dev.observe_entry_drift import not_run
from app.integrations.massive.client import (
    AggregateFetch, MassiveAggregatesClient, MassiveConfigurationError, MassiveError,
    RequestAccounting, build_massive_client,
)
from app.integrations.massive.minute_bars import (
    BAR_INTERVAL, ET, SessionPart, latest_completed_session, latest_completed_sessions, request_range,
)
from app.integrations.massive.validation import SpikeValidation, validate
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.symbols import normalize_symbol


DEFAULT_SYMBOL = "AAPL"
MAX_SYMBOLS = 3
MAX_SESSIONS = 5
MARKET_TIMEZONE = "America/New_York"
RAW_DIR = PROJECT_ROOT / "data" / "runtime" / "massive_spike"
MISSING_MINUTE_SAMPLE = 10
VENUE = "NOT_IN_AGGREGATES_PAYLOAD"


@dataclass(frozen=True)
class SessionOutcome:
    symbol: str
    window: TradingSessionWindow
    accounting: RequestAccounting
    elapsed: float
    validation: SpikeValidation | None
    failure_code: str | None = None
    failure_reason: str | None = None


def _counter(counter: Counter[Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in sorted(counter.items(), key=str)) or "none"


def _ts(value: datetime | None) -> str:
    return "none" if value is None else value.astimezone(ET).isoformat()


def _yes(flag: bool) -> str:
    return "YES" if flag else "NO"


def _by_hour(minutes: Iterable[datetime]) -> str:
    return _counter(Counter(f"{minute.astimezone(ET):%H}" for minute in minutes))


def _session_line(window: TradingSessionWindow) -> str:
    return (f"session_open_et={_ts(window.market_open)} session_close_et={_ts(window.market_close)} "
            f"early_close={window.is_early_close}")


def accounting_lines(accounting: RequestAccounting, elapsed: float) -> list[str]:
    return [
        f"http_requests={accounting.http_requests}",
        f"pagination_pages={accounting.pages}",
        f"elapsed_seconds={elapsed:.2f}",
        f"status_codes={_counter(accounting.status_codes)}",
        f"transport_failures={_counter(accounting.transport_failures)}",
        f"provider_status={_counter(accounting.provider_statuses)}",
        f"provider_results_count={accounting.results_count}",
    ]


def validation_lines(result: SpikeValidation) -> list[str]:
    window, rows = result.window, result.session_rows
    sample = ",".join(f"{minute:%H:%M}" for minute in
                      result.missing_regular_minutes[:MISSING_MINUTE_SAMPLE]) or "none"
    return [
        f"total_rows={result.total_rows}",
        f"first_timestamp_et={_ts(result.first_et)}",
        f"last_timestamp_et={_ts(result.last_et)}",
        f"premarket_rows={rows[SessionPart.PREMARKET]}",
        f"regular_rows={rows[SessionPart.REGULAR]}",
        f"postmarket_rows={rows[SessionPart.POSTMARKET]}",
        f"outside_window_rows={rows[SessionPart.OUTSIDE]}",
        f"first_premarket_timestamp_et={_ts(result.first_premarket_et)}",
        f"regular_open_row_{window.market_open:%H%M}={_yes(result.regular_open_row)}",
        f"last_regular_row_{result.last_regular_minute:%H%M}={_yes(result.last_regular_row)}",
        f"rows_at_or_after_close_{window.market_close:%H%M}={result.rows_at_or_after_close}",
        f"duplicate_timestamps={result.duplicate_timestamps}",
        f"non_monotonic_timestamps={result.non_monotonic_timestamps}",
        f"ohlc_invariant_violations={result.ohlc_violations}",
        f"null_ohlcv_rows={result.null_ohlcv_rows}",
        f"negative_volume_rows={result.negative_volume_rows}",
        f"zero_volume_rows={result.zero_volume_rows}",
        f"unaligned_timestamps={result.unaligned_timestamps}",
        f"off_date_rows={result.off_date_rows}",
        f"regular_missing_minutes={len(result.missing_regular_minutes)}",
        f"regular_missing_minute_sample_et={sample}",
        f"premarket_missing_minutes={len(result.premarket_missing_minutes)} "
        f"by_hour_et={_by_hour(result.premarket_missing_minutes)}",
        f"postmarket_missing_minutes={len(result.postmarket_missing_minutes)} "
        f"by_hour_et={_by_hour(result.postmarket_missing_minutes)}",
        f"premarket_data_present={_yes(result.premarket_data_present)} "
        f"window=04:00-{window.market_open:%H:%M}_ET",
        f"premarket_ohlc_present={_yes(result.premarket_ohlc_present)} rows={result.premarket_ohlc_rows}",
        f"premarket_volume_present={_yes(result.premarket_volume_present)} "
        f"rows={result.premarket_volume_rows} total={result.premarket_volume_total:.0f}",
        f"premarket_volume_total={result.premarket_volume_total:.0f}",
        "timestamp_contract=bar_start (Massive docs: t is the start of the aggregate window)",
        f"replay_available_boundary_candidate=bar_start+{int(BAR_INTERVAL.total_seconds() // 60)}min "
        "NOT_ADOPTED publication_latency=UNKNOWN",
        f"data_quality={result.verdict}",
    ]


def run_session(client: MassiveAggregatesClient, symbol: str, window: TradingSessionWindow,
                timer: Callable[[], float]) -> tuple[SessionOutcome, AggregateFetch | None]:
    """One symbol x one session through the shared client (and its one rate limiter)."""
    client.accounting = RequestAccounting()
    start, end = request_range(window)
    started = timer()
    try:
        fetch = client.minute_aggregates(symbol, start, end)
    except MassiveError as exc:
        return SessionOutcome(symbol, window, client.accounting, timer() - started, None,
                              exc.code, str(exc)), None
    return SessionOutcome(symbol, window, client.accounting, timer() - started,
                          validate(fetch.bars, window)), fetch


def _gate(outcomes: Sequence[SessionOutcome], passes: Callable[[SpikeValidation], bool]) -> str:
    """FAIL on any fetched session that fails; INCOMPLETE when a session was not fetched."""
    fetched = [outcome.validation for outcome in outcomes if outcome.validation is not None]
    if not fetched or not all(passes(result) for result in fetched):
        return "FAIL"
    return "PASS" if len(fetched) == len(outcomes) else "INCOMPLETE"


def _mean(values: Sequence[float]) -> str:
    return f"{fmean(values):.1f}" if values else "n/a"


def summary_lines(outcomes: Sequence[SessionOutcome], symbols: Sequence[str], elapsed: float) -> list[str]:
    fetched = [outcome.validation for outcome in outcomes if outcome.validation is not None]
    failed = [outcome for outcome in outcomes if outcome.validation is None]

    def count(predicate: Callable[[SpikeValidation], bool]) -> int:
        return sum(1 for result in fetched if predicate(result))

    status_codes: Counter[int] = Counter()
    for outcome in outcomes:
        status_codes.update(outcome.accounting.status_codes)
    lines = [
        "=== SUMMARY",
        f"total_symbol_sessions={len(outcomes)}",
        f"successful_sessions={len(fetched)}",
        f"failed_sessions={len(failed)}",
        f"clean_sessions={count(lambda result: result.verdict == 'CLEAN')}",
        f"anomaly_sessions={count(lambda result: result.verdict.startswith('ANOMALIES'))}",
        f"no_data_sessions={count(lambda result: result.verdict == 'NO_DATA')}",
        f"sessions_with_premarket={count(lambda result: result.premarket_data_present)}",
        f"sessions_with_premarket_ohlc={count(lambda result: result.premarket_ohlc_present)}",
        f"sessions_with_premarket_volume={count(lambda result: result.premarket_volume_present)}",
        f"sessions_with_0930={count(lambda result: result.regular_open_row)}",
        f"sessions_with_1559={count(lambda result: result.last_regular_row)}",
        # Complete = every calendar regular minute present (390 on a full XNYS day).
        f"sessions_with_390_regular_rows={count(lambda result: result.complete_regular)}",
        f"sessions_with_regular_missing_minutes={count(lambda result: bool(result.missing_regular_minutes))}",
        f"http_requests_total={sum(outcome.accounting.http_requests for outcome in outcomes)}",
        f"pagination_pages_total={sum(outcome.accounting.pages for outcome in outcomes)}",
        f"elapsed_seconds_total={elapsed:.2f}",
        f"status_codes_total={_counter(status_codes)}",
        f"failure_codes={_counter(Counter(outcome.failure_code for outcome in failed))}",
    ]
    for symbol in symbols:
        mine = [outcome for outcome in outcomes if outcome.symbol == symbol]
        results = [outcome.validation for outcome in mine if outcome.validation is not None]
        errors = sum(1 for outcome in mine
                     if outcome.validation is None or outcome.validation.verdict != "CLEAN")
        lines.append(
            f"symbol_summary={symbol} sessions={len(mine)} failed={len(mine) - len(results)} "
            f"error_sessions={errors} "
            f"avg_total_rows={_mean([result.total_rows for result in results])} "
            f"avg_premarket_rows={_mean([result.session_rows[SessionPart.PREMARKET] for result in results])} "
            f"avg_regular_rows={_mean([result.session_rows[SessionPart.REGULAR] for result in results])} "
            f"avg_postmarket_rows={_mean([result.session_rows[SessionPart.POSTMARKET] for result in results])} "
            f"avg_premarket_volume={_mean([result.premarket_volume_total for result in results])} "
            f"min_premarket_rows={min((result.session_rows[SessionPart.PREMARKET] for result in results), default='n/a')} "
            f"min_total_rows={min((result.total_rows for result in results), default='n/a')}")
    lines += [
        f"gate_G1_api_ok={'PASS' if outcomes and not failed else 'FAIL'}",
        f"gate_G2_premarket_ohlc_volume="
        f"{_gate(outcomes, lambda result: result.premarket_ohlc_present and result.premarket_volume_present)}",
        f"gate_G3_0930_1559={_gate(outcomes, lambda result: result.regular_open_row and result.last_regular_row)}",
        f"gate_G5_no_timestamp_ohlc_duplicate_errors={_gate(outcomes, lambda result: not result.anomalies())}",
        "gate_G4_G6=judged by the operator (venue is not in the aggregates payload; "
        "rate-limit realism is a projection)",
    ]
    return lines


def save_raw(fetch: AggregateFetch, symbol: str, window: TradingSessionWindow,
             now: datetime, raw_dir: Path) -> Path:
    """Sanitized response bodies only: no key, header, or request URL is ever written."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    fetched = now.astimezone(timezone.utc)
    path = raw_dir / f"{symbol}_{window.session_date}_{fetched:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps({
        "symbol": symbol,
        "target_trading_date": window.session_date.isoformat(),
        "fetched_at_utc": fetched.isoformat(),
        "pages": list(fetch.pages),
    }, indent=2), encoding="utf-8")
    return path


def _single(client: MassiveAggregatesClient, symbol: str, window: TradingSessionWindow, now: datetime,
            *, save: bool, timer: Callable[[], float], raw_dir: Path) -> int:
    start, end = request_range(window)
    print(f"symbol={symbol} timespan=1/minute adjusted=false sort=asc")
    print(f"target_trading_date={window.session_date}")
    print(_session_line(window))
    print(f"request_range_et={_ts(start)}..{_ts(end)} completion_rule=extended_hours_ended_20:00_ET")
    outcome, fetch = run_session(client, symbol, window, timer)
    for line in accounting_lines(outcome.accounting, outcome.elapsed):
        print(line)
    if outcome.validation is None or fetch is None:
        print(f"FAILED code={outcome.failure_code} reason={outcome.failure_reason}")
        return 1
    for line in validation_lines(outcome.validation):
        print(line)
    if save:
        print(f"raw_saved={save_raw(fetch, symbol, window, now, raw_dir)}")
    return 0


def _multi(client: MassiveAggregatesClient, symbols: Sequence[str],
           windows: Sequence[TradingSessionWindow], now: datetime,
           *, save: bool, timer: Callable[[], float], raw_dir: Path) -> int:
    print(f"symbols={' '.join(symbols)} sessions={len(windows)} timespan=1/minute adjusted=false sort=asc")
    print("target_trading_dates=" + ",".join(str(window.session_date) for window in windows))
    print("completion_rule=extended_hours_ended_20:00_ET")
    outcomes: list[SessionOutcome] = []
    started = timer()
    for symbol in symbols:
        for window in windows:
            outcome, fetch = run_session(client, symbol, window, timer)
            outcomes.append(outcome)
            print(f"=== symbol={symbol} trading_date={window.session_date}")
            print(f"venue={VENUE}")
            print(_session_line(window))
            for line in accounting_lines(outcome.accounting, outcome.elapsed):
                print(line)
            if outcome.validation is None or fetch is None:
                print(f"FAILED code={outcome.failure_code} reason={outcome.failure_reason}")
                continue
            for line in validation_lines(outcome.validation):
                print(line)
            if save:
                print(f"raw_saved={save_raw(fetch, symbol, window, now, raw_dir)}")
    for line in summary_lines(outcomes, symbols, timer() - started):
        print(line)
    return 0 if all(outcome.validation is not None for outcome in outcomes) else 1


def main(argv: Sequence[str] | None = None, *,
         settings_factory: Callable[[], Settings] = Settings,
         http: httpx.Client | None = None,
         sleeper: Callable[[float], None] = time.sleep,
         clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
         timer: Callable[[], float] = time.monotonic,
         raw_dir: Path = RAW_DIR) -> int:
    parser = argparse.ArgumentParser(description="NON-TRADING Massive historical data source spike")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--symbol", help=f"one symbol (default {DEFAULT_SYMBOL})")
    target.add_argument("--symbols", nargs="+", metavar="SYMBOL",
                        help=f"validation only: 1-{MAX_SYMBOLS} symbols over the same --sessions")
    parser.add_argument("--sessions", type=int, default=1,
                        help=f"most recent completed XNYS sessions, 1-{MAX_SESSIONS} (default 1)")
    parser.add_argument("--save-raw", action="store_true",
                        help="also write sanitized response bodies under data/runtime/massive_spike/ "
                             "(git-ignored); never a key, header, or request URL")
    args = parser.parse_args(argv)
    if not 1 <= args.sessions <= MAX_SESSIONS:
        parser.error(f"--sessions must be 1-{MAX_SESSIONS}; bulk collection is out of scope")
    raw_symbols = args.symbols or [args.symbol or DEFAULT_SYMBOL]
    if len(raw_symbols) > MAX_SYMBOLS:
        parser.error(f"--symbols takes at most {MAX_SYMBOLS} symbols; bulk collection is out of scope")
    symbols: list[str] = []
    for raw in raw_symbols:
        try:
            symbols.append(normalize_symbol(raw))
        except ValueError as exc:
            raise not_run(f"symbol {raw!r} is invalid: {exc}") from exc
    if len(set(symbols)) != len(symbols):
        parser.error("--symbols must not repeat a symbol")
    try:
        client = build_massive_client(settings_factory(), http=http, sleeper=sleeper)
    except MassiveConfigurationError as exc:
        print("http_requests=0")
        raise not_run(f"{exc.code}: {exc}") from None

    now = clock()
    calendar = MarketCalendar(MARKET_TIMEZONE)
    if args.symbols is None and args.sessions == 1:
        return _single(client, symbols[0], latest_completed_session(calendar, now), now,
                       save=args.save_raw, timer=timer, raw_dir=raw_dir)
    return _multi(client, symbols, latest_completed_sessions(calendar, now, args.sessions), now,
                  save=args.save_raw, timer=timer, raw_dir=raw_dir)


if __name__ == "__main__":
    raise SystemExit(main())
