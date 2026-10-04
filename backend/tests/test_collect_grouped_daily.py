"""The nightly grouped daily refresh: what it collects, what it refuses, what it reuses.

Every test is offline. The provider is a fake whose bodies are shaped like the real ones, so the
contract under test is the one A reads: a file at ``data/runtime/strategy_c/raw/grouped/<D>.json.gz``
that ``mover_scanner_v1.daily.grouped_path`` finds and ``load_panel`` can parse.
"""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.backtest.mover_scanner_v1 import daily as D
from app.backtest.strategy_c_selection import raw_fetch as RF
from app.dev import collect_grouped_daily as G
from app.market.calendar import MarketCalendar

ET = ZoneInfo("America/New_York")
# A Sunday, so "the previous trading day" is unambiguous and crosses a weekend.
SUNDAY = datetime(2026, 10, 4, 1, 0, tzinfo=ET)
FRIDAY = date(2026, 10, 2)
MONDAY = date(2026, 10, 5)


class FakeAccounting:
    def __init__(self) -> None:
        self.http_requests = 0


class FakeClient:
    """Answers grouped daily from a dict of prepared bodies; counts every call."""

    def __init__(self, bodies: dict[date, object]) -> None:
        self.bodies = bodies
        self.accounting = FakeAccounting()
        self.asked: list[date] = []

    def grouped_daily(self, session: date):
        self.accounting.http_requests += 1
        self.asked.append(session)
        body = self.bodies.get(session)
        if body is None:
            raise AssertionError(f"the run asked for an unprepared session {session}")
        return body


def body(session: date, *, tickers=("SPY", "AAPL"), adjusted=False, duplicate=False):
    rows = [{"T": symbol, "c": 100.0 + index, "v": 1_000.0 + index, "o": 99.0, "h": 101.0,
             "l": 98.0, "n": 10, "t": 0, "vw": 100.0} for index, symbol in enumerate(tickers)]
    if duplicate:
        rows.append(dict(rows[0]))
    return {"adjusted": adjusted, "status": "OK", "queryCount": len(rows),
            "resultsCount": len(rows), "results": rows, "request_id": "r"}


def write_valid(repo, session: date, **kwargs) -> None:
    RF._write_json_gz(RF.grouped_path(repo / G.RAW_ROOT, session),
                      {"format": RF.RAW_FORMAT_VERSION, "session": session.isoformat(),
                       "body": body(session, **kwargs)})


def seed_grid(repo, calendar: MarketCalendar, session: date, *, skip=()) -> list[date]:
    """Fill every prior session A reads for ``session``, so a test can hole it deliberately."""
    start = session - timedelta(days=D.DAILY_LOOKBACK_DAYS + 10)
    from app.backtest.collector.range import sessions_between
    grid = [window.session_date for window in sessions_between(calendar, start, session)]
    for day in grid:
        if day < session and day not in skip:
            write_valid(repo, day)
    return grid


@pytest.fixture
def calendar() -> MarketCalendar:
    return MarketCalendar("America/New_York")


# -- the plan -----------------------------------------------------------------------------------

def test_target_is_the_previous_xnys_session_in_et(tmp_path, calendar):
    the_plan = G.plan(tmp_path, calendar, now=SUNDAY)
    assert the_plan.target == FRIDAY
    assert the_plan.next_session == MONDAY


def test_a_session_that_has_not_closed_is_refused(tmp_path, calendar):
    with pytest.raises(G.CollectionFailure) as error:
        G.plan(tmp_path, calendar, now=SUNDAY, session=MONDAY)
    assert "is not published yet" in str(error.value)


def test_the_plan_is_exactly_the_holes_in_a_s_own_grid(tmp_path, calendar):
    grid = seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    the_plan = G.plan(tmp_path, calendar, now=SUNDAY)
    assert the_plan.grid == tuple(grid)
    assert the_plan.next_session == grid[-1] == MONDAY
    assert the_plan.missing == (FRIDAY,)
    # The session's own file is never required: it is published only after it closes.
    assert MONDAY not in the_plan.required


def test_the_plan_is_capped_so_a_long_outage_is_never_a_bulk_pull(tmp_path, calendar):
    the_plan = G.plan(tmp_path, calendar, now=SUNDAY, max_sessions=3)
    assert len(the_plan.missing) > 3
    assert len(the_plan.planned) == 3
    assert the_plan.capped is True


# -- collection ---------------------------------------------------------------------------------

def test_a_collected_session_lands_where_a_reads_it(tmp_path, calendar):
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    client = FakeClient({FRIDAY: body(FRIDAY)})
    payload = G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, ledger=False)
    assert payload["status"] == "OK"
    assert payload["collected"] == [FRIDAY.isoformat()]
    assert payload["a_next_session_readable"] is True
    assert payload["prior_missing_after"] == []
    found = D.grouped_path(tmp_path, FRIDAY)
    assert found is not None and found == tmp_path / G.RAW_ROOT / "grouped" / "2026-10-02.json.gz"
    assert {row["T"] for row in D.grouped_rows(found)} == {"SPY", "AAPL"}
    assert client.asked == [FRIDAY]


def test_nothing_to_do_costs_zero_requests(tmp_path, calendar):
    seed_grid(tmp_path, calendar, MONDAY)
    client = FakeClient({})
    payload = G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, ledger=False)
    assert payload["status"] == "NO_WORK"
    assert client.accounting.http_requests == 0


def test_a_rerun_reuses_the_file_and_never_rewrites_it(tmp_path, calendar):
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    client = FakeClient({FRIDAY: body(FRIDAY)})
    G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, ledger=False)
    path = tmp_path / G.RAW_ROOT / "grouped" / "2026-10-02.json.gz"
    before = path.read_bytes()
    again = G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, ledger=False)
    assert again["status"] == "NO_WORK"
    assert client.accounting.http_requests == 1
    assert path.read_bytes() == before


def test_plan_only_writes_nothing_and_makes_no_request(tmp_path, calendar):
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    client = FakeClient({})
    payload = G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, dry_run=True, ledger=False)
    assert payload["status"] == "DRY_RUN"
    assert [item["status"] for item in payload["results"]] == ["WOULD_COLLECT"]
    assert D.grouped_path(tmp_path, FRIDAY) is None
    assert client.accounting.http_requests == 0


# -- refusals -----------------------------------------------------------------------------------

def test_a_provider_stub_is_quarantined_so_a_fails_closed(tmp_path, calendar):
    """``fetch_grouped`` writes an error stub on a plan refusal; A must not read it as a session."""
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    from app.integrations.massive.client import MassiveError

    class Refusing(FakeClient):
        def grouped_daily(self, session):
            self.accounting.http_requests += 1
            raise MassiveError("PLAN_TIMEFRAME_NOT_INCLUDED", "plan does not include this timeframe")

    payload = G.run(tmp_path, now=SUNDAY, client_factory=lambda: Refusing({}), ledger=False)
    assert payload["status"] == "INCOMPLETE"
    assert payload["prior_missing_after"] == [FRIDAY.isoformat()]
    assert D.grouped_path(tmp_path, FRIDAY) is None
    kept = list((tmp_path / G.RAW_ROOT / "quarantine" / "grouped").glob("2026-10-02*"))
    assert len(kept) == 1
    assert "PROVIDER_STUB" in json.dumps(payload["results"])


def test_an_invalid_file_already_on_disk_is_quarantined_not_trusted(tmp_path, calendar):
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    RF._write_json_gz(RF.grouped_path(tmp_path / G.RAW_ROOT, FRIDAY),
                      {"format": RF.RAW_FORMAT_VERSION, "session": FRIDAY.isoformat(),
                       "body": body(FRIDAY, tickers=("AAPL",))})  # no benchmark row
    client = FakeClient({FRIDAY: body(FRIDAY)})
    payload = G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, ledger=False)
    statuses = [item["status"] for item in payload["results"]]
    assert statuses == ["QUARANTINED", "COLLECTED"]
    assert "NO_SPY" in json.dumps(payload["results"])
    assert payload["status"] == "OK"
    assert payload["plan"]["invalid"] == [FRIDAY.isoformat()]


def test_an_older_defect_is_only_found_when_revalidation_is_asked_for(tmp_path, calendar):
    """A nightly run owns the target session; sweeping the whole window is a deliberate ask."""
    older = date(2026, 10, 1)
    seed_grid(tmp_path, calendar, MONDAY)
    write_valid(tmp_path, older, tickers=("AAPL",))  # a prior session with no benchmark row
    client = FakeClient({older: body(older)})
    quiet = G.run(tmp_path, now=SUNDAY, client_factory=lambda: client, ledger=False)
    assert quiet["status"] == "NO_WORK" and client.accounting.http_requests == 0
    swept = G.run(tmp_path, now=SUNDAY, revalidate=True, client_factory=lambda: client, ledger=False)
    assert swept["plan"]["invalid"] == [older.isoformat()]
    assert swept["collected"] == [older.isoformat()]


def test_a_file_dated_for_another_session_is_never_accepted(tmp_path, calendar):
    RF._write_json_gz(RF.grouped_path(tmp_path / G.RAW_ROOT, FRIDAY),
                      {"format": RF.RAW_FORMAT_VERSION, "session": "2026-10-01",
                       "body": body(FRIDAY)})
    checked = G.validate(RF.grouped_path(tmp_path / G.RAW_ROOT, FRIDAY), FRIDAY)
    assert checked.ok is False
    assert any("SESSION_IDENTITY_MISMATCH" in problem for problem in checked.problems)


@pytest.mark.parametrize("kwargs,expected", [
    ({"adjusted": True}, "NOT_UNADJUSTED"),
    ({"duplicate": True}, "DUPLICATE_TICKERS"),
    ({"tickers": ()}, "ZERO_ROWS"),
    ({"tickers": ("AAPL",)}, "NO_SPY"),
])
def test_validation_names_each_defect(tmp_path, kwargs, expected):
    write_valid(tmp_path, FRIDAY, **kwargs)
    checked = G.validate(RF.grouped_path(tmp_path / G.RAW_ROOT, FRIDAY), FRIDAY)
    assert checked.ok is False
    assert any(expected in problem for problem in checked.problems)


def test_a_valid_file_reports_its_rows_benchmark_and_checksum(tmp_path):
    write_valid(tmp_path, FRIDAY)
    checked = G.validate(RF.grouped_path(tmp_path / G.RAW_ROOT, FRIDAY), FRIDAY)
    assert checked.ok and checked.problems == ()
    assert checked.rows == 2 and checked.duplicates == 0 and checked.benchmark is True
    assert len(checked.sha256) == 64 and checked.bytes > 0


# -- mirroring and the ledger -------------------------------------------------------------------

def test_the_mirror_shares_the_inode_rather_than_copying_bytes(tmp_path, calendar):
    repo, other = tmp_path / "primary", tmp_path / "secondary"
    seed_grid(repo, calendar, MONDAY, skip=(FRIDAY,))
    client = FakeClient({FRIDAY: body(FRIDAY)})
    G.run(repo, now=SUNDAY, mirrors=(other,), client_factory=lambda: client, ledger=False)
    source = repo / G.RAW_ROOT / "grouped" / "2026-10-02.json.gz"
    target = other / G.RAW_ROOT / "grouped" / "2026-10-02.json.gz"
    assert target.is_file()
    assert source.stat().st_ino == target.stat().st_ino
    assert D.grouped_path(other, FRIDAY) == target


def test_a_differing_mirror_file_is_left_alone_and_reported(tmp_path, calendar):
    repo, other = tmp_path / "primary", tmp_path / "secondary"
    seed_grid(repo, calendar, MONDAY, skip=(FRIDAY,))
    write_valid(other, FRIDAY, tickers=("SPY", "MSFT"))
    kept = (other / G.RAW_ROOT / "grouped" / "2026-10-02.json.gz").read_bytes()
    client = FakeClient({FRIDAY: body(FRIDAY)})
    payload = G.run(repo, now=SUNDAY, mirrors=(other,), client_factory=lambda: client, ledger=False)
    assert "MIRROR_CONFLICT_LEFT_ALONE" in json.dumps(payload["results"])
    assert (other / G.RAW_ROOT / "grouped" / "2026-10-02.json.gz").read_bytes() == kept


def test_the_run_leaves_durable_evidence_even_when_it_fails(tmp_path, calendar):
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    from app.integrations.massive.client import MassiveError

    class Broken(FakeClient):
        def grouped_daily(self, session):
            self.accounting.http_requests += 1
            raise MassiveError("PLAN_TIMEFRAME_NOT_INCLUDED", "plan timeframe")

    payload = G.run(tmp_path, now=SUNDAY, client_factory=lambda: Broken({}))
    ledger = tmp_path / G.LEDGER_DIR
    assert json.loads((ledger / "runs.jsonl").read_text().strip())["status"] == "INCOMPLETE"
    assert json.loads(Path(payload["ledger"]).read_text())["status"] == "INCOMPLETE"
    assert Path(payload["ledger"]).parent == ledger


def test_the_cli_exit_code_separates_a_ready_night_from_a_broken_one(tmp_path, calendar, monkeypatch):
    seed_grid(tmp_path, calendar, MONDAY, skip=(FRIDAY,))
    monkeypatch.setattr(G, "datetime", _FixedDatetime)
    monkeypatch.setattr(G, "_client_factory", lambda spacing: (lambda: FakeClient({FRIDAY: body(FRIDAY)})))
    assert G.main(["--repo", str(tmp_path)]) == 0
    assert G.main(["--repo", str(tmp_path), "--session", MONDAY.isoformat()]) == 2


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D102 - a clock, pinned to the Sunday the tests reason about
        return SUNDAY
