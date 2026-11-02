"""Strategy I paper timer: runs on grouped-daily's days, after it, on America/New_York time (no systemd needed)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.backtest.collector.range import previous_trading_day
from app.market.calendar import MarketCalendar

REPO = Path(__file__).resolve().parents[2]
I_SYSTEMD = REPO / "docs/operations/i_paper_deploy_prep_v2/systemd"
ET = ZoneInfo("America/New_York")


def on_calendar(path: Path) -> str:
    lines = [ln.split("=", 1)[1] for ln in path.read_text().splitlines() if ln.startswith("OnCalendar=")]
    assert len(lines) == 1
    return lines[0]


def test_i_timer_runs_monday_to_friday_at_0215_new_york():
    assert on_calendar(I_SYSTEMD / "usb-i-paper.timer") == "Mon..Fri 02:15 America/New_York"


def test_i_timer_shares_grouped_dailys_days_and_runs_after_it():
    grouped = on_calendar(REPO / "deploy/systemd/usb-grouped-daily.timer")
    assert grouped == "Mon..Fri 00:40 America/New_York"
    days, at, tz = on_calendar(I_SYSTEMD / "usb-i-paper.timer").split(" ")
    g_days, g_at, g_tz = grouped.split(" ")
    assert (days, tz) == (g_days, g_tz) and at > g_at


def test_every_i_as_of_is_the_session_grouped_daily_stored_that_morning():
    """Both take the previous XNYS session of the same ET date, so the file I needs is the one just written.
    Covers both DST transitions and the 2026 Thanksgiving / Christmas holidays."""
    cal = MarketCalendar()
    day = date(2026, 10, 26)
    while day <= date(2027, 3, 31):
        if day.weekday() < 5:
            grouped_at = datetime(day.year, day.month, day.day, 0, 40, tzinfo=ET)
            i_at = datetime(day.year, day.month, day.day, 2, 15, tzinfo=ET)
            assert previous_trading_day(cal, i_at) == previous_trading_day(cal, grouped_at), day
        day += timedelta(days=1)


def test_service_guard_is_unchanged():
    text = (I_SYSTEMD / "usb-i-paper.service").read_text()
    assert ("ExecCondition=/root/usb/.venv/bin/python -m app.strategies.i_paper.guard "
            "--store /root/usb_runtime/strategy_i_paper/store \\\n  --min-available-mib 350 --systemd-condition") in text
