"""Massive historical collector V1: range rules, validation, storage, manifest, guards.

No real key and no network in any test but the opt-in live smoke at the end. A small
in-memory router serves a handful of XNYS sessions in pages, and every workspace is a
fake Google Drive mount under ``tmp_path``, so the same assertions hold on the home PC
and on the office PC.
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pyarrow.parquet as pq
import pytest
from pydantic import SecretStr

from app.backtest.collector.collector import COLLECTOR_VERSION, collect, manifest_key
from app.backtest.collector.dataset import (
    DATASET_SCHEMA_VERSION, SCHEMA, PrecisionAudit, audit_precision,
)
from app.backtest.collector.environment import assert_local_environment
from app.backtest.collector.errors import (
    IncompleteRange, PartitionConflict, PrecisionLoss, ProductionEnvironment, RangeTooLarge,
    RegularMinutesMissing, SameDayRequest, SessionsMissing,
)
from app.backtest.collector.range import plan_range, plan_years, previous_trading_day
from app.backtest.collector import storage
from app.backtest.collector.storage import relative_partition_path, relative_symbol_directory
from app.backtest.workspace.discovery import WORKSPACE_DIR_NAME
from app.backtest.workspace.errors import WriterLockHeld
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import acquire as acquire_lock
from app.backtest.workspace.manifest import (
    MANIFEST_SCHEMA_VERSION, entry_files, find_entry, get_entry, manifest_connection, open_manifest,
    plan_entry, verify_entry,
)
from app.backtest.workspace.operations import initialize
from app.backtest.workspace.safe_write import PARTIAL_SUFFIX, sha256_file
from app.backtest.workspace.state import UNCOMMITTED, CurrentState
from app.core.config import PROJECT_ROOT, Settings
from app.dev import collect_historical_massive as cli
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import (
    MassiveAggregatesClient, MassiveError, build_massive_client,
)
from app.integrations.massive.minute_bars import EPOCH
from app.market.calendar import MarketCalendar, TradingSessionWindow


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SENTINEL = "unit-test-massive-key-not-a-credential"
CALENDAR = MarketCalendar("America/New_York")
# 21:41 ET on 2026-09-15. The previous trading day, and so the newest collectable
# session, is 2026-09-14: Stocks Basic does not publish the session of the current date.
RUN_AT = datetime(2026, 9, 16, 1, 41, tzinfo=UTC)
TODAY_ET = date(2026, 9, 15)
LAST_SESSION = date(2026, 9, 14)
RANGE_START = date(2026, 9, 8)
SESSION_DATES = (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11),
                 LAST_SESSION)
PREMARKET_MINUTES = 6  # 04:00-04:05 ET
POSTMARKET_MINUTES = 3
NEXT_BASE = "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/1/2"
SYMBOL = "AAPL"
# An early-close range: 2025-11-28 closes at 13:00 ET.
EARLY_CLOSE_DAY = date(2025, 11, 28)
EARLY_RUN_AT = datetime(2025, 12, 2, 2, 0, tzinfo=UTC)  # 21:00 ET on 2025-12-01


def ms(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(milliseconds=1)


def at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET)


def row(moment: datetime, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"t": ms(moment), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5,
                                 "v": 1000.5, "vw": 100.2, "n": 12}
    values.update(overrides)
    return values


def minutes(start: datetime, count: int) -> list[datetime]:
    return [start + timedelta(minutes=n) for n in range(count)]


def window_of(day: date) -> TradingSessionWindow:
    window = CALENDAR.session(day)
    assert window is not None
    return window


def session_rows(day: date, *, skip: tuple[datetime, ...] = ()) -> list[dict[str, object]]:
    window = window_of(day)
    regular = (window.market_close - window.market_open) // timedelta(minutes=1)
    moments = (minutes(at(day, 4, 0), PREMARKET_MINUTES)
               + minutes(window.market_open, regular)
               + minutes(window.market_close, POSTMARKET_MINUTES))
    return [row(moment) for moment in moments if moment not in skip]


def range_rows(days: Sequence[date] = SESSION_DATES, *,
               skip: tuple[datetime, ...] = (),
               drop_days: tuple[date, ...] = ()) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in days:
        if day not in drop_days:
            rows.extend(session_rows(day, skip=skip))
    return rows


def body(rows: Sequence[object], **extra: object) -> dict[str, object]:
    return {"ticker": SYMBOL, "status": "OK", "adjusted": False, "queryCount": len(rows),
            "resultsCount": len(rows), "results": list(rows), "request_id": "req-test", **extra}


class Router:
    """Serves ``rows`` in ``page_size`` slices keyed by a ``cursor=pageN`` query parameter."""

    def __init__(self, rows: Sequence[dict[str, object]], *, page_size: int = 50_000,
                 next_url_for: Callable[[int], str] | None = None,
                 overrides: dict[int, httpx.Response] | None = None) -> None:
        self.rows = list(rows)
        self.page_size = page_size
        self.next_url_for = next_url_for or (lambda page: f"{NEXT_BASE}?cursor=page{page}")
        self.overrides = overrides or {}
        self.requests: list[httpx.Request] = []

    def page_of(self, request: httpx.Request) -> int:
        cursor = request.url.params.get("cursor")
        return int(cursor.removeprefix("page")) if cursor else 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = self.page_of(request)
        if page in self.overrides:
            return self.overrides[page]
        chunk = self.rows[page * self.page_size:(page + 1) * self.page_size]
        extra: dict[str, object] = {}
        if (page + 1) * self.page_size < len(self.rows):
            extra["next_url"] = self.next_url_for(page + 1)
        return httpx.Response(200, json=body(chunk, **extra))

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


class DeadRouter(Router):
    """Any request is a test failure: used where the collector must make none."""

    def __init__(self) -> None:
        super().__init__([])

    def __call__(self, request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
        raise AssertionError("the collector made an HTTP request when it must not have")


def unlimited() -> RequestRateLimiter:
    return RequestRateLimiter(1_000_000.0, clock=lambda: 0.0, sleeper=lambda _: None)


def client_factory(router: Router) -> Callable[..., MassiveAggregatesClient]:
    def factory(settings: Settings, *, page_observer: Callable[[bytes], None] | None = None,
                **_: object) -> MassiveAggregatesClient:
        return MassiveAggregatesClient(SecretStr(SENTINEL), http=router.http(),
                                       limiter=unlimited(), sleeper=lambda _: None,
                                       page_observer=page_observer)
    return factory


def keyed_settings() -> Settings:
    return Settings(_env_file=None, massive_api_key=SENTINEL)


def mount_workspace(base: Path) -> Path:
    root = base / "mnt" / "g" / "내 드라이브" / WORKSPACE_DIR_NAME
    root.mkdir(parents=True)
    return root.resolve()


def seed_state() -> CurrentState:
    return CurrentState(stage="MASSIVE_AAPL_1_YEAR_FEASIBILITY", status="PASS_WITH_LIMITATIONS",
                        provider="massive", source_commit=UNCOMMITTED,
                        last_completed_at=RUN_AT.isoformat(timespec="seconds"),
                        next_stage="MASSIVE_HISTORICAL_COLLECTOR_V1", notes="")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = mount_workspace(tmp_path)
    initialize(root, seed_state=seed_state(), now=RUN_AT)
    return Workspace(root)


def planned_range(*, start: date = RANGE_START, end: date = LAST_SESSION,
                  now: datetime = RUN_AT):
    return plan_range(CALENDAR, start=start, end=end, now=now)


def run_collect(workspace: Workspace, router: Router, *, now: datetime = RUN_AT,
                collection_range=None, **kwargs: object):
    return collect(workspace, symbol=SYMBOL,
                   collection_range=collection_range or planned_range(now=now),
                   settings=keyed_settings(), now=now,
                   client_factory=client_factory(router), **kwargs)  # type: ignore[arg-type]


def partition(workspace: Workspace, year: int) -> Path:
    return workspace.root / relative_partition_path("massive", SYMBOL, year)


def leftovers(workspace: Workspace) -> list[Path]:
    return sorted(workspace.root.rglob(f"*{PARTIAL_SUFFIX}"))


def entry_status(workspace: Workspace, collection_range=None) -> str:
    with manifest_connection(workspace, create=False) as connection:
        found = find_entry(connection, manifest_key(SYMBOL, collection_range or planned_range()))
        return "absent" if found is None else str(found["status"])


# --- 1. the T-1 end session --------------------------------------------------------

def test_effective_end_is_the_previous_trading_day_and_never_today() -> None:
    assert previous_trading_day(CALENDAR, RUN_AT) == LAST_SESSION
    planned = plan_range(CALENDAR, start=RANGE_START, end=TODAY_ET + timedelta(days=5), now=RUN_AT)
    assert planned.effective_end == LAST_SESSION
    assert planned.requested_end == TODAY_ET + timedelta(days=5)
    assert planned.end_clamped is True
    assert planned.last_collectable_session == LAST_SESSION
    assert [window.session_date for window in planned.sessions] == list(SESSION_DATES)
    assert planned.expected_sessions == 5


def test_years_option_ends_at_the_previous_trading_day() -> None:
    planned = plan_years(CALENDAR, years=1, now=RUN_AT)
    assert planned.effective_end == LAST_SESSION
    assert planned.effective_start == date(2025, 9, 15)
    assert planned.expected_sessions == 251
    assert planned.years == (2025, 2026)


# --- 2. a same-day request is refused ----------------------------------------------

def test_a_request_for_today_only_is_refused_before_any_http_call() -> None:
    with pytest.raises(SameDayRequest) as refused:
        plan_range(CALENDAR, start=TODAY_ET, end=TODAY_ET, now=RUN_AT)
    assert "2026-09-14" in str(refused.value)


def test_collect_refuses_a_range_that_reaches_today(workspace: Workspace) -> None:
    stale = planned_range()
    # The same range, judged a day earlier: its end is then the current ET date.
    earlier = RUN_AT - timedelta(days=1)
    with pytest.raises(SameDayRequest):
        run_collect(workspace, DeadRouter(), now=earlier, collection_range=stale)
    assert not list(workspace.root.rglob("*.parquet"))


# --- 3. the last expected session must be present ----------------------------------

def test_a_silently_truncated_range_fails_closed_and_writes_nothing(workspace: Workspace) -> None:
    router = Router(range_rows(drop_days=(LAST_SESSION,)))
    with pytest.raises(IncompleteRange) as failed:
        run_collect(workspace, router)
    assert failed.value.code == "COLLECTOR_INCOMPLETE_RANGE"
    assert str(LAST_SESSION) in failed.value.reason
    assert not list(workspace.root.rglob("*.parquet"))
    assert leftovers(workspace) == []
    assert entry_status(workspace) == "FAILED"


def test_a_missing_middle_session_fails_closed(workspace: Workspace) -> None:
    router = Router(range_rows(drop_days=(date(2026, 9, 10),)))
    with pytest.raises(SessionsMissing):
        run_collect(workspace, router)
    assert not list(workspace.root.rglob("*.parquet"))


# --- 4. long range over several pages ----------------------------------------------

def test_a_multi_page_range_is_collected_whole(workspace: Workspace) -> None:
    rows = range_rows()
    router = Router(rows, page_size=1000)
    outcome = run_collect(workspace, router)
    assert outcome.pages == 2
    assert outcome.http_requests == outcome.pages
    assert outcome.row_count == len(rows)
    assert outcome.validation is not None
    assert outcome.validation.coverage.sessions_present == 5
    assert outcome.manifest_status == "COMPLETE"
    assert partition(workspace, 2026).is_file()


# --- 5, 6. pagination loop, duplicate and overlapping pages ------------------------

def test_a_repeating_next_url_is_refused(workspace: Workspace) -> None:
    rows = range_rows()
    repeat = httpx.Response(200, json=body(rows[1000:], next_url=f"{NEXT_BASE}?cursor=page1"))
    router = Router(rows, page_size=1000, overrides={1: repeat})
    with pytest.raises(MassiveError) as failed:
        run_collect(workspace, router)
    assert failed.value.code == "PAGINATION_LOOP"
    assert not list(workspace.root.rglob("*.parquet"))
    assert leftovers(workspace) == []


def test_an_overlapping_page_is_refused(workspace: Workspace) -> None:
    rows = range_rows()
    repeat = httpx.Response(200, json=body(rows[:1000]))
    router = Router(rows, page_size=1000, overrides={1: repeat})
    with pytest.raises(MassiveError) as failed:
        run_collect(workspace, router)
    assert failed.value.code == "DUPLICATE_PAGE"
    assert not list(workspace.root.rglob("*.parquet"))


# --- 7, 8. regular minutes must be complete; premarket minutes need not be ---------

def test_one_missing_regular_minute_fails_the_collection(workspace: Workspace) -> None:
    missing = at(date(2026, 9, 10), 11, 17)
    router = Router(range_rows(skip=(missing,)))
    with pytest.raises(RegularMinutesMissing) as failed:
        run_collect(workspace, router)
    assert "2026-09-10" in failed.value.reason
    assert not list(workspace.root.rglob("*.parquet"))
    assert entry_status(workspace) == "FAILED"


def test_a_missing_premarket_minute_is_kept_as_a_gap_not_a_failure(workspace: Workspace) -> None:
    missing = at(date(2026, 9, 10), 4, 3)
    rows = range_rows(skip=(missing,))
    outcome = run_collect(workspace, Router(rows))
    assert outcome.manifest_status == "COMPLETE"
    assert outcome.row_count == len(rows)
    table = pq.read_table(partition(workspace, 2026))
    stamps = {value.as_py() for value in table.column("timestamp_et")}
    assert missing not in stamps  # no synthetic bar was invented
    assert outcome.validation is not None
    assert outcome.validation.coverage.sessions_with_premarket == 5


# --- 9. early close -----------------------------------------------------------------

def test_an_early_close_session_is_measured_against_the_calendar_close(workspace: Workspace) -> None:
    days = (date(2025, 11, 26), EARLY_CLOSE_DAY)
    planned = plan_range(CALENDAR, start=days[0], end=EARLY_CLOSE_DAY, now=EARLY_RUN_AT)
    assert [window.session_date for window in planned.sessions] == list(days)
    outcome = run_collect(workspace, Router(range_rows(days)), now=EARLY_RUN_AT,
                          collection_range=planned)
    coverage = outcome.validation.coverage  # type: ignore[union-attr]
    assert coverage.early_close_sessions == (EARLY_CLOSE_DAY,)
    assert coverage.early_close_sessions_valid == (EARLY_CLOSE_DAY,)
    assert coverage.sessions_with_complete_regular_minutes == 2
    early = [report for report in outcome.validation.reports  # type: ignore[union-attr]
             if report.window.session_date == EARLY_CLOSE_DAY][0]
    assert early.expected_regular_minutes == 210
    assert early.regular_row_count == 210


# --- 10, 11, 12. numbers and the Parquet round trip --------------------------------

def test_fractional_volume_and_provider_price_precision_survive_the_round_trip(
        workspace: Workspace) -> None:
    moment = at(date(2026, 9, 10), 10, 0)
    rows = range_rows()
    for item in rows:
        if item["t"] == ms(moment):
            item.update({"o": 332.795944, "h": 332.8, "l": 332.7, "c": 332.795945,
                         "v": 1100077.342805, "vw": 318.9714, "n": 30458})
    outcome = run_collect(workspace, Router(rows))
    assert outcome.precision.max_decimal_places == 6
    assert outcome.precision.max_significant_digits == 13
    assert outcome.precision.violations == []
    table = pq.read_table(partition(workspace, 2026))
    index = [value.as_py() for value in table.column("timestamp_et")].index(moment)
    assert table.column("open")[index].as_py() == 332.795944
    assert table.column("close")[index].as_py() == 332.795945
    assert table.column("volume")[index].as_py() == 1100077.342805
    assert table.column("vwap")[index].as_py() == 318.9714
    assert table.column("volume")[0].as_py() == 1000.5  # never cast to an integer


def test_the_stored_table_matches_the_declared_schema_and_session_labels(
        workspace: Workspace) -> None:
    run_collect(workspace, Router(range_rows()))
    file = pq.ParquetFile(partition(workspace, 2026))
    assert file.schema_arrow == SCHEMA
    assert file.schema_arrow.metadata[b"dataset_schema_version"] == b"1"
    table = file.read()
    assert set(table.column("provider").to_pylist()) == {"massive"}
    assert set(table.column("symbol").to_pylist()) == {SYMBOL}
    labels = table.column("session").to_pylist()
    assert set(labels) == {"PREMARKET", "REGULAR", "POSTMARKET"}
    assert labels.count("PREMARKET") == PREMARKET_MINUTES * len(SESSION_DATES)
    assert labels.count("POSTMARKET") == POSTMARKET_MINUTES * len(SESSION_DATES)
    utc = table.column("timestamp_utc").to_pylist()
    local = table.column("timestamp_et").to_pylist()
    assert utc == sorted(utc)
    assert all(a == b for a, b in zip(utc, local))
    assert table.column("trading_date")[0].as_py() == SESSION_DATES[0]


def test_a_number_that_would_not_survive_float64_stops_the_collection() -> None:
    audit = PrecisionAudit()
    with pytest.raises(PrecisionLoss):
        audit_precision(b'{"results":[{"o":1.2345678901234567891,"v":1}]}', audit)


# --- 13. deterministic checksum ----------------------------------------------------

def test_the_same_rows_produce_the_same_file_checksum(workspace: Workspace, tmp_path: Path) -> None:
    first = run_collect(workspace, Router(range_rows()))
    other_root = mount_workspace(tmp_path / "second")
    initialize(other_root, seed_state=seed_state(), now=RUN_AT)
    second = run_collect(Workspace(other_root), Router(range_rows()))
    assert [item.checksum for item in first.partitions] == [item.checksum for item in second.partitions]
    assert first.checksum == second.checksum
    assert sha256_file(partition(workspace, 2026)) == first.partitions[0].checksum


# --- 14, 15. a failed write leaves the store as it was -----------------------------

def test_a_failure_while_writing_leaves_no_partial_and_no_file(workspace: Workspace,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "FLUSH_ROWS", 400)
    calls = {"n": 0}
    original = pq.ParquetWriter.write_table

    def explode(self, table, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("simulated disk failure")
        return original(self, table, **kwargs)

    monkeypatch.setattr(pq.ParquetWriter, "write_table", explode)
    with pytest.raises(OSError):
        run_collect(workspace, Router(range_rows(), page_size=1000))
    assert not list(workspace.root.rglob("*.parquet"))
    assert leftovers(workspace) == []
    assert entry_status(workspace) == "FAILED"


def test_a_failed_recollection_does_not_touch_the_existing_complete_file(
        workspace: Workspace) -> None:
    first = run_collect(workspace, Router(range_rows()))
    before = partition(workspace, 2026).read_bytes()
    truncated = Router(range_rows(drop_days=(LAST_SESSION,)))
    with pytest.raises(IncompleteRange):
        run_collect(workspace, truncated, refresh=True)
    assert partition(workspace, 2026).read_bytes() == before
    assert leftovers(workspace) == []
    with manifest_connection(workspace, create=False) as connection:
        row = find_entry(connection, manifest_key(SYMBOL, planned_range()))
        assert str(row["status"]) == "COMPLETE"  # the finished dataset keeps its standing
        assert str(row["checksum"]) == first.checksum
        assert verify_entry(connection, int(row["id"]), workspace=workspace)[0] is True


# --- 16, 17. idempotency and the cache hit ------------------------------------------

def test_a_second_run_of_the_same_range_makes_no_request(workspace: Workspace) -> None:
    first = run_collect(workspace, Router(range_rows()))
    assert first.cache_hit is False
    dead = DeadRouter()
    second = run_collect(workspace, dead)
    assert second.cache_hit is True
    assert second.http_requests == 0
    assert dead.requests == []
    assert second.checksum == first.checksum
    assert [item.relative_path for item in second.partitions] == list(first.relative_paths)


def test_the_manifest_holds_one_entry_per_range_with_the_collection_metadata(
        workspace: Workspace) -> None:
    outcome = run_collect(workspace, Router(range_rows()))
    with manifest_connection(workspace, create=False) as connection:
        key = manifest_key(SYMBOL, planned_range())
        assert plan_entry(connection, key, collector_version=COLLECTOR_VERSION) == outcome.entry_id
        row = find_entry(connection, key)
        assert str(row["status"]) == "COMPLETE"
        assert str(row["provider"]) == "massive"
        assert str(row["timeframe"]) == "1minute"
        assert str(row["data_kind"]) == "minute_bars"
        assert str(row["start_date"]) == str(RANGE_START)
        assert str(row["end_date"]) == str(LAST_SESSION)
        assert str(row["collector_version"]) == COLLECTOR_VERSION
        assert int(row["schema_version"]) == DATASET_SCHEMA_VERSION
        assert int(row["expected_sessions"]) == 5
        assert int(row["actual_sessions"]) == 5
        assert int(row["regular_complete_sessions"]) == 5
        assert int(row["premarket_sessions"]) == 5
        assert int(row["source_request_count"]) == outcome.http_requests
        assert int(row["row_count"]) == outcome.row_count
        assert str(row["relative_path"]) == relative_symbol_directory("massive", SYMBOL)
        files = entry_files(connection, outcome.entry_id)
        assert [item.relative_path for item in files] == list(outcome.relative_paths)
        assert verify_entry(connection, outcome.entry_id, workspace=workspace)[0] is True


def test_a_manifest_written_under_schema_1_migrates_in_place(workspace: Workspace) -> None:
    connection = open_manifest(workspace.manifest_path)
    connection.execute("DROP TABLE collector_entry_files")
    for column in ("expected_sessions", "actual_sessions", "regular_complete_sessions",
                   "premarket_sessions", "source_request_count", "schema_version", "file_count",
                   "byte_size", "listing_state"):
        connection.execute(f"ALTER TABLE collector_entries DROP COLUMN {column}")
    connection.execute("UPDATE manifest_meta SET value = '1' WHERE key = 'manifest_schema_version'")
    connection.close()
    outcome = run_collect(workspace, Router(range_rows()))
    with manifest_connection(workspace, create=False) as connection:
        version = connection.execute(
            "SELECT value FROM manifest_meta WHERE key = 'manifest_schema_version'").fetchone()
        assert int(version["value"]) == MANIFEST_SCHEMA_VERSION == 3
        assert entry_files(connection, outcome.entry_id)


def test_a_partition_owned_by_another_entry_is_not_overwritten(workspace: Workspace) -> None:
    run_collect(workspace, Router(range_rows()))
    shorter = plan_range(CALENDAR, start=date(2026, 9, 9), end=LAST_SESSION, now=RUN_AT)
    with pytest.raises(PartitionConflict):
        run_collect(workspace, Router(range_rows(SESSION_DATES[1:])), collection_range=shorter)
    overwritten = run_collect(workspace, Router(range_rows(SESSION_DATES[1:])),
                              collection_range=shorter, overwrite_partitions=True)
    assert overwritten.manifest_status == "COMPLETE"


def test_an_overwrite_demotes_the_entry_whose_file_it_replaced(workspace: Workspace) -> None:
    first = run_collect(workspace, Router(range_rows()))
    shorter = plan_range(CALENDAR, start=date(2026, 9, 9), end=LAST_SESSION, now=RUN_AT)
    overwritten = run_collect(workspace, Router(range_rows(SESSION_DATES[1:])),
                              collection_range=shorter, overwrite_partitions=True)
    assert overwritten.displaced_entries == (first.entry_id,)
    with manifest_connection(workspace, create=False) as connection:
        assert str(get_entry(connection, first.entry_id)["status"]) == "FAILED"
        assert str(get_entry(connection, overwritten.entry_id)["status"]) == "COMPLETE"
        complete = [row for row in connection.execute(
            "SELECT id FROM collector_entries WHERE symbol = ? AND status = 'COMPLETE'",
            (SYMBOL,)).fetchall()]
        assert [int(row["id"]) for row in complete] == [overwritten.entry_id]
    # A plain re-run of the new range is still a cache hit: the demotion touched no file.
    again = run_collect(workspace, DeadRouter(), collection_range=shorter)
    assert again.cache_hit and again.displaced_entries == ()


def test_a_range_beyond_the_page_ceiling_is_refused_before_any_request(workspace: Workspace) -> None:
    long_range = plan_years(CALENDAR, years=3, now=RUN_AT)
    with pytest.raises(RangeTooLarge) as refused:
        run_collect(workspace, DeadRouter(), collection_range=long_range)
    assert refused.value.code == "COLLECTOR_RANGE_TOO_LARGE"


# --- 18. no secret reaches the output ----------------------------------------------

def test_no_key_reaches_stdout_a_raw_page_or_an_error(workspace: Workspace,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    leaky = f"{NEXT_BASE}?cursor=page{{page}}&apiKey={SENTINEL}"
    router = Router(range_rows(), page_size=1000,
                    next_url_for=lambda page: leaky.format(page=page))
    exit_code = cli.main(["--symbol", SYMBOL, "--start", str(RANGE_START), "--end",
                          str(LAST_SESSION), "--workspace-root", str(workspace.root),
                          "--save-raw"],
                         settings_factory=keyed_settings, clock=lambda: RUN_AT,
                         client_factory=client_factory(router))
    printed = capsys.readouterr().out
    assert exit_code == 0
    assert SENTINEL not in printed
    assert "VERDICT=COMPLETE" in printed
    raw_files = sorted((workspace.root / "market_data/raw/massive" / SYMBOL).glob("*.json"))
    assert raw_files
    for path in raw_files:
        text = path.read_text(encoding="utf-8")
        assert SENTINEL not in text
        assert "apiKey=REDACTED" in text or "apiKey" not in text


def test_the_client_never_prints_its_key() -> None:
    client = MassiveAggregatesClient(SecretStr(SENTINEL), http=Router([]).http())
    assert SENTINEL not in repr(client)


# --- 19. a missing workspace stops the run -----------------------------------------

def test_a_missing_workspace_stops_the_run_before_any_request(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from app.backtest.workspace import discovery

    monkeypatch.setattr(discovery, "DEFAULT_MOUNT_BASES", (tmp_path / "empty",))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--symbol", SYMBOL, "--years", "1"], settings_factory=keyed_settings,
                 clock=lambda: RUN_AT, client_factory=client_factory(DeadRouter()))
    assert "NOT RUN" in str(stopped.value)
    assert "1_US-B" in str(stopped.value)


# --- 20. the writer lock ------------------------------------------------------------

def test_a_held_writer_lock_blocks_the_collection(workspace: Workspace) -> None:
    acquire_lock(workspace, purpose="backtest_run", now=datetime.now(UTC))
    with pytest.raises(WriterLockHeld):
        run_collect(workspace, DeadRouter())
    assert not list(workspace.root.rglob("*.parquet"))


# --- 21. the collector refuses to run in production ---------------------------------

@pytest.mark.parametrize("root", [Path("/root/usb"), Path("/srv/usb"), Path("/opt/usb")])
def test_a_production_path_is_refused(root: Path) -> None:
    with pytest.raises(ProductionEnvironment) as refused:
        assert_local_environment(settings=keyed_settings(), project_root=root, environ={})
    assert refused.value.code == "COLLECTOR_PRODUCTION_ENVIRONMENT"


def test_a_production_app_env_and_a_service_manager_are_refused() -> None:
    with pytest.raises(ProductionEnvironment):
        assert_local_environment(settings=Settings(_env_file=None, app_env="production"),
                                 project_root=PROJECT_ROOT, environ={})
    with pytest.raises(ProductionEnvironment):
        assert_local_environment(settings=keyed_settings(), project_root=PROJECT_ROOT,
                                 environ={"INVOCATION_ID": "systemd-unit"})


def test_a_development_checkout_passes_the_environment_check() -> None:
    report = assert_local_environment(settings=keyed_settings(), project_root=PROJECT_ROOT,
                                      environ={})
    assert any("repository_root=" in line for line in report.checks)


# --- the Basic plan timeframe refusal is typed, not a credential failure -------------

def test_a_same_day_403_is_classified_as_a_plan_timeframe_limit(workspace: Workspace) -> None:
    refusal = httpx.Response(403, json={
        "status": "NOT_AUTHORIZED",
        "message": "Your plan doesn't include this data timeframe. Please upgrade your plan."})
    router = Router(range_rows(), overrides={0: refusal})
    with pytest.raises(MassiveError) as failed:
        run_collect(workspace, router)
    assert failed.value.code == "PLAN_TIMEFRAME_NOT_INCLUDED"
    assert "timeframe" in str(failed.value)


def test_an_ordinary_403_stays_a_credential_failure(workspace: Workspace) -> None:
    router = Router(range_rows(), overrides={0: httpx.Response(403, json={"status": "NOT_AUTHORIZED"})})
    with pytest.raises(MassiveError) as failed:
        run_collect(workspace, router)
    assert failed.value.code == "NOT_AUTHORIZED"


# --- off-date rows and corrupt bars fail the collection -----------------------------

def test_a_row_on_a_non_session_date_fails_the_collection(workspace: Workspace) -> None:
    rows = range_rows() + [row(at(date(2026, 9, 12), 10, 0))]  # a Saturday
    rows.sort(key=lambda item: item["t"])  # type: ignore[arg-type,return-value]
    with pytest.raises(Exception) as failed:
        run_collect(workspace, Router(rows))
    assert "off_date_rows" in str(failed.value) or "DATA_QUALITY" in str(failed.value)
    assert not list(workspace.root.rglob("*.parquet"))


def test_a_broken_ohlc_bar_fails_the_collection(workspace: Workspace) -> None:
    rows = range_rows()
    rows[100] = dict(rows[100], h=1.0, l=500.0)
    with pytest.raises(Exception) as failed:
        run_collect(workspace, Router(rows))
    assert "ohlc" in str(failed.value).lower()
    assert not list(workspace.root.rglob("*.parquet"))


# --- 22. the opt-in live smoke ------------------------------------------------------

@pytest.mark.skipif(os.environ.get("RUN_MASSIVE_HISTORICAL_SMOKE") != "1",
                    reason="set RUN_MASSIVE_HISTORICAL_SMOKE=1 to collect AAPL for real")
def test_aapl_one_year_live_smoke() -> None:  # pragma: no cover - opt-in, hits the provider
    """One real AAPL year into the real workspace. Local, manual, and never in CI."""
    from app.backtest.workspace.discovery import resolve_workspace_root

    now = datetime.now(UTC)
    workspace = Workspace(resolve_workspace_root())
    planned = plan_years(CALENDAR, years=1, now=now)
    outcome = collect(workspace, symbol=SYMBOL, collection_range=planned,
                      settings=Settings(), now=now, client_factory=build_massive_client)
    assert outcome.manifest_status == "COMPLETE"
    assert outcome.validation is None or outcome.validation.complete
    assert outcome.row_count > 100_000
