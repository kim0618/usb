"""The static XNYS table behind MarketCalendar: special dates, range edges and exchange_calendars parity.

The parity tests rebuild the facade the backend used before the table (``exchange_calendars`` read
through pandas) and compare every public answer day by day. They need the library, which stays a
development dependency for the generator, and are skipped where it is absent.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from app.market import calendar as C
from app.market.calendar import CalendarRangeError, MarketCalendar

BACKEND = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
TIMEZONES = ("America/New_York", "Asia/Seoul", "UTC")
#: The window the deployed backend answered before the table: exchange_calendars' default bounds
#: (20 years back, 1 year ahead) on the day the production process last started.
DEPLOYED_WINDOW = (date(2006, 10, 27), date(2027, 10, 27))
TABLE_RANGE = (date(2000, 1, 1), date(2035, 12, 31))
METHODS = ("is_trading_day", "is_holiday", "session", "regular_market_open", "regular_market_close",
           "is_early_close", "next_trading_day", "previous_trading_day")


def ny(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), NY)


# -- fixed special dates (expected values read from exchange_calendars 4.13.2) ---------------------

CLOSED = [
    (date(2026, 10, 24), "Saturday"), (date(2026, 10, 25), "Sunday"),
    (date(2026, 4, 3), "Good Friday"), (date(2026, 6, 19), "Juneteenth"),
    (date(2026, 11, 26), "Thanksgiving"), (date(2026, 12, 25), "Christmas"),
    (date(2027, 1, 1), "New Year"), (date(2018, 12, 5), "G.H.W. Bush mourning"),
    (date(2025, 1, 9), "Carter mourning"), (date(2012, 10, 29), "Hurricane Sandy"),
]


@pytest.mark.parametrize("day,why", CLOSED)
def test_closed_days(day, why) -> None:
    cal = MarketCalendar("America/New_York")
    assert not cal.is_trading_day(day) and cal.session(day) is None
    assert cal.is_holiday(day) is (day.weekday() < 5)


def test_juneteenth_is_a_session_before_2022() -> None:
    assert MarketCalendar("America/New_York").is_trading_day(date(2021, 6, 18))


@pytest.mark.parametrize("day,close", [(date(2026, 11, 27), (13, 0)), (date(2026, 12, 24), (13, 0)),
                                       (date(2025, 7, 3), (13, 0)), (date(2024, 11, 29), (13, 0))])
def test_early_closes(day, close) -> None:
    session = MarketCalendar("America/New_York").session(day)
    assert session is not None and session.is_early_close
    assert session.market_open == ny(day, 9, 30) and session.market_close == ny(day, *close)


@pytest.mark.parametrize("day,open_utc", [
    (date(2026, 3, 6), (14, 30)), (date(2026, 3, 9), (13, 30)),    # DST starts 2026-03-08
    (date(2026, 10, 30), (13, 30)), (date(2026, 11, 2), (14, 30)),  # DST ends 2026-11-01
])
def test_dst_shifts_utc_not_new_york_time(day, open_utc) -> None:
    session = MarketCalendar("UTC").session(day)
    assert session is not None and not session.is_early_close
    assert (session.market_open.hour, session.market_open.minute) == open_utc
    assert session.market_open.astimezone(NY).time() == time(9, 30)
    assert session.market_close.astimezone(NY).time() == time(16, 0)


def test_seoul_view_of_a_session() -> None:
    session = MarketCalendar("Asia/Seoul").session(date(2026, 11, 27))
    assert session.market_open.isoformat() == "2026-11-27T23:30:00+09:00"
    assert session.market_close.isoformat() == "2026-11-28T03:00:00+09:00"
    assert str(session.market_open.tzinfo) == "Asia/Seoul"


@pytest.mark.parametrize("day,nxt,prev", [
    (date(2026, 12, 31), date(2027, 1, 4), date(2026, 12, 30)),
    (date(2027, 1, 1), date(2027, 1, 4), date(2026, 12, 31)),
    (date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 25)),
    (date(2026, 4, 3), date(2026, 4, 6), date(2026, 4, 2)),
    (date(2026, 10, 24), date(2026, 10, 26), date(2026, 10, 23)),
])
def test_next_and_previous_skip_closed_days(day, nxt, prev) -> None:
    cal = MarketCalendar("America/New_York")
    assert cal.next_trading_day(day) == nxt and cal.previous_trading_day(day) == prev


def test_holding_day_number_counts_sessions_inclusively() -> None:
    cal = MarketCalendar("America/New_York")
    assert cal.holding_day_number(date(2026, 11, 25), date(2026, 11, 25)) == 1
    assert cal.holding_day_number(date(2026, 11, 25), date(2026, 11, 30)) == 3
    with pytest.raises(ValueError, match="ordered XNYS sessions"):
        cal.holding_day_number(date(2026, 11, 25), date(2026, 11, 26))


def test_naive_midnight_datetime_keeps_its_previous_answers() -> None:
    # The library facade compared a found session date with the argument; a datetime never equals
    # a date, so next/previous returned the session day itself. The table keeps that answer.
    cal = MarketCalendar("America/New_York")
    assert cal.next_trading_day(datetime(2026, 10, 27)) == date(2026, 10, 27)
    assert cal.previous_trading_day(datetime(2026, 10, 27)) == date(2026, 10, 27)
    assert cal.next_trading_day(datetime(2026, 10, 24)) == date(2026, 10, 26)
    with pytest.raises(ValueError):
        cal.is_trading_day(datetime(2026, 10, 27, 10, 0))
    with pytest.raises(ValueError):
        cal.session(datetime(2026, 10, 27, tzinfo=UTC))


def test_outside_the_table_fails_instead_of_guessing() -> None:
    cal = MarketCalendar("America/New_York")
    for call in (lambda: cal.is_trading_day(date(1999, 12, 31)), lambda: cal.session(date(2036, 1, 2)),
                 lambda: cal.next_trading_day(date(2035, 12, 31)),
                 lambda: cal.previous_trading_day(date(2000, 1, 3))):
        with pytest.raises(CalendarRangeError):
            call()


def test_table_matches_its_metadata() -> None:
    meta = json.loads(C.META_PATH.read_text(encoding="utf-8"))
    body = C.TABLE_PATH.read_bytes()
    assert hashlib.sha256(body).hexdigest() == meta["sha256"]
    assert body.decode("ascii").splitlines()[0] == C.TABLE_HEADER
    assert (meta["range_start"], meta["range_end"]) == tuple(d.isoformat() for d in TABLE_RANGE)
    assert len(C.session_table().days) == meta["row_count"] == len(body.splitlines()) - 1
    assert meta["first_session"] == "2000-01-03" and meta["last_session"] == "2035-12-31"


# -- exchange_calendars parity (the previous implementation, rebuilt here as the reference) --------

class _LibraryCalendar:
    """The pre-table ``MarketCalendar`` body, verbatim apart from the injected library calendar."""

    def __init__(self, xcal, pd, timezone_name: str) -> None:
        self._calendar, self._pd, self.timezone = xcal, pd, ZoneInfo(timezone_name)

    def _label(self, day):
        return self._pd.Timestamp(day)

    def is_trading_day(self, day):
        return bool(self._calendar.is_session(self._label(day)))

    def is_holiday(self, day):
        return day.weekday() < 5 and not self.is_trading_day(day)

    def session(self, day):
        label = self._label(day)
        if not self._calendar.is_session(label):
            return None
        market_open = self._calendar.session_open(label).to_pydatetime().astimezone(self.timezone)
        market_close = self._calendar.session_close(label).to_pydatetime().astimezone(self.timezone)
        market_close_ny = self._calendar.session_close(label).to_pydatetime().astimezone(NY)
        return C.TradingSessionWindow(session_date=day, market_open=market_open, market_close=market_close,
                                      is_early_close=market_close_ny.timetz().replace(tzinfo=None) < time(16, 0))

    def regular_market_open(self, day):
        session = self.session(day)
        return session.market_open if session else None

    def regular_market_close(self, day):
        session = self.session(day)
        return session.market_close if session else None

    def is_early_close(self, day):
        session = self.session(day)
        return bool(session and session.is_early_close)

    def next_trading_day(self, day):
        label = self._calendar.date_to_session(self._label(day), direction="next")
        if label.date() == day:
            label = self._calendar.next_session(label)
        return label.date()

    def previous_trading_day(self, day):
        label = self._calendar.date_to_session(self._label(day), direction="previous")
        if label.date() == day:
            label = self._calendar.previous_session(label)
        return label.date()

    def holding_day_number(self, entry_day, current_day):
        if current_day < entry_day or not self.is_trading_day(entry_day) or not self.is_trading_day(current_day):
            raise ValueError("holding dates must be ordered XNYS sessions")
        return len(self._calendar.sessions_in_range(self._label(entry_day), self._label(current_day)))


def _answer(cal, method: str, day):
    try:
        value = getattr(cal, method)(day)
    except Exception:  # an edge refusal is an answer too; compared by kind below
        return ("RAISES",)
    if isinstance(value, C.TradingSessionWindow):
        return (value.session_date, value.market_open.isoformat(), value.market_close.isoformat(),
                str(value.market_open.tzinfo), str(value.market_close.tzinfo), value.is_early_close)
    if isinstance(value, datetime):
        return (value.isoformat(), str(value.tzinfo))
    return (type(value).__name__, value)


def _diffs(reference, table, first: date, last: date) -> list:
    diffs, day = [], first
    while day <= last:
        for argument in (day, datetime.combine(day, time(0))):
            for method in METHODS:
                expected, actual = _answer(reference, method, argument), _answer(table, method, argument)
                if expected != actual:
                    diffs.append((argument, method, expected, actual))
        day += timedelta(days=1)
    return diffs


def _holding_diffs(reference, table, first: date, last: date, pairs: int = 5000) -> list:
    sessions = [date.fromordinal(o) for o in C.session_table().days if first.toordinal() <= o <= last.toordinal()]
    rng, diffs = random.Random(20261027), []
    for _ in range(pairs):
        a, b = sorted(rng.sample(sessions, 2))
        if reference.holding_day_number(a, b) != table.holding_day_number(a, b):
            diffs.append((a, b))
    return diffs


@pytest.fixture(scope="module")
def xcals_modules():
    xcals = pytest.importorskip("exchange_calendars")
    pd = pytest.importorskip("pandas")
    return xcals, pd


@pytest.mark.parametrize("tz", TIMEZONES)
def test_parity_over_the_deployed_window(xcals_modules, tz) -> None:
    xcals, pd = xcals_modules
    xcal = xcals.get_calendar("XNYS", start=DEPLOYED_WINDOW[0].isoformat(), end=DEPLOYED_WINDOW[1].isoformat())
    reference, table = _LibraryCalendar(xcal, pd, tz), MarketCalendar(tz)
    first, last = xcal.first_session.date(), xcal.last_session.date()
    # The library's own first and last sessions refuse previous/next; inside them every answer agrees.
    inner = _diffs(reference, table, first + timedelta(days=1), last - timedelta(days=1))
    assert inner == []
    assert _holding_diffs(reference, table, first, last) == []


@pytest.mark.parametrize("tz", ("America/New_York", "Asia/Seoul"))
def test_parity_over_the_whole_table(xcals_modules, tz) -> None:
    xcals, pd = xcals_modules
    xcal = xcals.get_calendar("XNYS", start=TABLE_RANGE[0].isoformat(), end=TABLE_RANGE[1].isoformat())
    reference, table = _LibraryCalendar(xcal, pd, tz), MarketCalendar(tz)
    assert _diffs(reference, table, TABLE_RANGE[0] + timedelta(days=3), TABLE_RANGE[1] - timedelta(days=1)) == []
    assert _holding_diffs(reference, table, *TABLE_RANGE) == []


def test_table_regenerates_byte_for_byte(xcals_modules) -> None:
    from app.dev import generate_xnys_sessions as G

    assert G.render(G.build_rows()) == C.TABLE_PATH.read_bytes()


# -- fail-fast on a corrupt table or meta.json ----------------------------------------------------

def _write_table(tmp_path, monkeypatch, edit_rows=None, edit_meta=None, resign: bool = True):
    """Copy the table into ``tmp_path``, apply the edits, and point the loader at the copy.

    ``resign`` re-records sha256 and row_count after a row edit, so the check under test is the one
    that fires and not the sha256 in front of it.
    """
    meta = json.loads(C.META_PATH.read_text(encoding="utf-8"))
    lines = C.TABLE_PATH.read_bytes().decode("ascii").splitlines()
    if edit_rows:
        lines = [lines[0]] + edit_rows(lines[1:])
    body = ("\n".join(lines) + "\n").encode("ascii")
    if edit_rows and resign:
        meta.update(sha256=hashlib.sha256(body).hexdigest(), row_count=len(lines) - 1)
    if edit_meta:
        edit_meta(meta)
    (tmp_path / "xnys_sessions.csv").write_bytes(body)
    (tmp_path / "xnys_sessions.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(C, "TABLE_PATH", tmp_path / "xnys_sessions.csv")
    monkeypatch.setattr(C, "META_PATH", tmp_path / "xnys_sessions.meta.json")


def _flip_one_byte(rows):
    # 2000-01-04's close 947019600 -> 947019601: one digit, still a valid row.
    return [rows[0], rows[1].replace("947019600", "947019601")] + rows[2:]


CORRUPTIONS = {
    "csv_byte_changed": (dict(edit_rows=_flip_one_byte, resign=False), "sha256"),
    "meta_sha_changed": (dict(edit_meta=lambda m: m.update(sha256="0" * 64)), "sha256"),
    "meta_row_count_plus_one": (dict(edit_meta=lambda m: m.update(row_count=m["row_count"] + 1)), "meta records"),
    "meta_range_end_extended": (dict(edit_meta=lambda m: m.update(range_end="2037-12-31")), "not covered"),
    "csv_duplicate_session": (dict(edit_rows=lambda r: r[:100] + [r[99]] + r[100:]), "repeats session"),
    "csv_sessions_swapped": (dict(edit_rows=lambda r: r[:100] + [r[101], r[100]] + r[102:]), "goes back to"),
    # Beyond the six required cases: the other fields the loader now holds to the rows.
    "meta_range_start_extended": (dict(edit_meta=lambda m: m.update(range_start="1999-01-01")), "not covered"),
    "meta_range_end_before_last_session": (dict(edit_meta=lambda m: m.update(range_end="2035-12-30")),
                                           "not covered"),
    "meta_last_session_moved": (dict(edit_meta=lambda m: m.update(last_session="2035-12-28")), "first/last"),
    "meta_first_session_moved": (dict(edit_meta=lambda m: m.update(first_session="2000-01-04")), "first/last"),
    "csv_last_row_dropped": (dict(edit_rows=lambda r: r[:-1]), "first/last"),
    "meta_key_missing": (dict(edit_meta=lambda m: m.pop("range_end")), "missing \\[.range_end.\\]"),
    "meta_other_generator": (dict(edit_meta=lambda m: m.update(generator_version="xnys-sessions-v0")),
                             "generator .xnys-sessions-v0."),
    "row_missing_field": (dict(edit_rows=lambda r: [r[0].rsplit(",", 1)[0]] + r[1:]), "malformed"),
    "row_bad_date": (dict(edit_rows=lambda r: [r[0].replace("2000-01-03", "2000-13-03")] + r[1:]), "malformed"),
    "row_early_flag_out_of_range": (dict(edit_rows=lambda r: [r[0][:-1] + "2"] + r[1:]), "row contract"),
    "row_close_before_open": (dict(edit_rows=lambda r: ["2000-01-03,946933200,946909800,0"] + r[1:]),
                              "row contract"),
}


@pytest.mark.parametrize("name", CORRUPTIONS)
def test_corrupt_table_fails_on_load(tmp_path, monkeypatch, name) -> None:
    edits, message = CORRUPTIONS[name]
    _write_table(tmp_path, monkeypatch, **edits)
    with pytest.raises(C.CalendarTableError, match=message):
        C._load_table()


def test_unchanged_copy_loads_the_same_table(tmp_path, monkeypatch) -> None:
    # Control for the corruptions: the same copy path with no edit loads exactly the shipped table.
    shipped = C._load_table()
    _write_table(tmp_path, monkeypatch, edit_rows=lambda r: r)
    copied = C._load_table()
    assert (copied.range_start, copied.range_end, copied.days, copied.opens, copied.closes, copied.early) == (
        shipped.range_start, shipped.range_end, shipped.days, shipped.opens, shipped.closes, shipped.early)


#: Importing app.market.calendar already loads the table (app.market pulls in the kiwoom mapping), so
#: the probe swaps the paths in the moment the module body has run, before anything reads them.
STARTUP_PROBE = r"""
import importlib.abc, importlib.machinery, sys
from pathlib import Path

class RedirectTable(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name != "app.market.calendar":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        run = spec.loader.exec_module
        def exec_module(module):
            run(module)
            module.TABLE_PATH, module.META_PATH = Path(sys.argv[1]), Path(sys.argv[2])
        spec.loader.exec_module = exec_module
        return spec

sys.meta_path.insert(0, RedirectTable())
import app.main
import app.market.calendar as C
assert C.TABLE_PATH == Path(sys.argv[1]), C.TABLE_PATH
print("STARTED", C._table is not None)
"""


@pytest.mark.parametrize("name", ["csv_byte_changed", "meta_range_end_extended", "csv_sessions_swapped", None])
def test_backend_import_fails_on_a_corrupt_table(tmp_path, monkeypatch, name) -> None:
    # The table loads while app.main imports (kiwoom mapping builds US_CALENDAR), so a corrupt table
    # stops the backend before it serves anything. ``None`` is the unchanged copy, which starts.
    if name is None:
        _write_table(tmp_path, monkeypatch, edit_rows=lambda r: r)
    else:
        _write_table(tmp_path, monkeypatch, **CORRUPTIONS[name][0])
    database = "sqlite:///%s" % (tmp_path / "probe.sqlite3")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STRATEGY_E_MAX", "USB_STRATEGY_I"))}
    env.update(PYTHONPATH=str(BACKEND), A_PAPER_RUNTIME_ENABLED="false", DATABASE_URL=database,
               PAPER_DATABASE_URL=database)
    done = subprocess.run([sys.executable, "-c", STARTUP_PROBE, str(C.TABLE_PATH), str(C.META_PATH)],
                          cwd=BACKEND.parent, env=env, capture_output=True, text=True, timeout=300)
    if name is None:
        assert done.returncode == 0 and "STARTED True" in done.stdout, done.stderr[-2000:]
    else:
        assert done.returncode != 0 and "STARTED" not in done.stdout
        assert "app.market.calendar.CalendarTableError: XNYS session" in done.stderr, done.stderr[-2000:]


def test_expiry_warning_starts_a_year_before_the_table_ends(caplog) -> None:
    table = C.session_table()
    with caplog.at_level("WARNING", logger=C.__name__):
        assert C.warn_if_expiring(table, date(2026, 10, 27)) is False
        assert C.warn_if_expiring(table, date(2034, 12, 30)) is False
        assert C.warn_if_expiring(table, date(2034, 12, 31)) is True
    assert len(caplog.records) == 1 and "2035-12-31 (365 days left)" in caplog.messages[0]
