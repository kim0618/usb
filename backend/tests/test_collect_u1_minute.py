"""The U1 collector's planning helpers: resumed minute plans and the per-symbol daily kind.

No network: only the parser, the Common Raw coverage read and the request planner are exercised,
over ledgers written into a temporary workspace.
"""

from datetime import date
import json
from pathlib import Path

from app.backtest.historical_store.raw_fetch import (
    DAILY_DIR, MINUTE_DIR, ordered, plan_requests,
)
from app.dev.collect_u1_minute import build_parser, drive_coverage

GRID = [date(2024, 9, 25), date(2024, 9, 26), date(2024, 9, 27), date(2024, 9, 30)]


def ledger(root: Path, base: str, symbol: str, start: date, end: date, status: str = "COMPLETE") -> None:
    folder = root / base / symbol
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{symbol}_{start}_{end}.request.json").write_text(json.dumps(
        {"status": status, "start": start.isoformat(), "end": end.isoformat()}))


def test_the_default_kind_is_minute_and_daily_is_explicit() -> None:
    assert build_parser().parse_args([]).kind == "minute"
    assert build_parser().parse_args(["--kind", "daily"]).kind == "daily"


def test_coverage_reads_only_the_requested_tape(tmp_path: Path) -> None:
    ledger(tmp_path, MINUTE_DIR, "AAA", GRID[0], GRID[1])
    ledger(tmp_path, DAILY_DIR, "AAA", GRID[0], GRID[-1])
    assert drive_coverage(tmp_path, ["AAA"], GRID) == {"AAA": {GRID[0], GRID[1]}}
    assert drive_coverage(tmp_path, ["AAA"], GRID, base=DAILY_DIR) == {"AAA": set(GRID)}


def test_an_incomplete_ledger_covers_nothing(tmp_path: Path) -> None:
    ledger(tmp_path, DAILY_DIR, "AAA", GRID[0], GRID[-1], status="FAILED")
    assert drive_coverage(tmp_path, ["AAA"], GRID, base=DAILY_DIR) == {}


def test_a_resumed_minute_plan_requests_only_what_is_missing(tmp_path: Path) -> None:
    # The urgent oldest slice is already on disk: the plan starts after it, and the
    # earliest session counts as held (the collector's guard reads exactly this).
    ledger(tmp_path, MINUTE_DIR, "AAA", GRID[0], GRID[1])
    existing = drive_coverage(tmp_path, ["AAA"], GRID)
    planned = ordered(plan_requests("minute", GRID, existing, ["AAA"]))
    assert [(item.start, item.end) for item in planned] == [(GRID[2], GRID[3])]
    assert GRID[0] in existing["AAA"]


def test_a_clipped_daily_ledger_does_not_claim_the_lost_sessions(tmp_path: Path) -> None:
    # A request clipped by the rolling window records the sessions it was sent for; the
    # sessions it could not get are never counted as held.
    ledger(tmp_path, DAILY_DIR, "AAA", GRID[2], GRID[3])
    existing = drive_coverage(tmp_path, ["AAA"], GRID, base=DAILY_DIR)
    assert existing == {"AAA": {GRID[2], GRID[3]}}
    planned = plan_requests("per_symbol_daily", GRID, existing, ["AAA"])
    assert [(item.kind, item.start, item.end) for item in planned] == [
        ("per_symbol_daily", GRID[0], GRID[1])]


def test_a_request_straddling_the_window_floor_is_clipped_to_dates() -> None:
    from app.backtest.historical_store.raw_fetch import RawRequest
    from app.dev.collect_u1_minute import clip_to_window
    from app.dev.run_massive_spike import MARKET_TIMEZONE
    from app.market.calendar import MarketCalendar
    calendar = MarketCalendar(MARKET_TIMEZONE)
    request = RawRequest("per_symbol_daily", "AAA", date(2024, 9, 17), date(2024, 9, 25), 7)
    sent, lost = clip_to_window(calendar, request, date(2024, 9, 21))
    assert lost == [date(2024, 9, 17), date(2024, 9, 18), date(2024, 9, 19), date(2024, 9, 20)]
    assert sent is not None and (sent.start, sent.end, sent.sessions) == (date(2024, 9, 23), date(2024, 9, 25), 3)


def test_a_request_wholly_before_the_floor_is_lost_and_one_inside_is_untouched() -> None:
    from app.backtest.historical_store.raw_fetch import RawRequest
    from app.dev.collect_u1_minute import clip_to_window
    from app.dev.run_massive_spike import MARKET_TIMEZONE
    from app.market.calendar import MarketCalendar
    calendar = MarketCalendar(MARKET_TIMEZONE)
    gone = RawRequest("per_symbol_daily", "AAA", date(2024, 9, 17), date(2024, 9, 17), 1)
    assert clip_to_window(calendar, gone, date(2024, 9, 21)) == (None, [date(2024, 9, 17)])
    inside = RawRequest("minute", "AAA", date(2024, 12, 5), date(2025, 2, 19), 50)
    assert clip_to_window(calendar, inside, date(2024, 9, 21)) == (inside, [])
